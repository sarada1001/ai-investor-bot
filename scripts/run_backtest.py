#!/usr/bin/env python3
"""
scripts/run_backtest.py — Time Travel バックテストエンジン

指定期間の OHLCV データを yfinance で取得し、1日ずつループして
TechnicalAgent シグナルと CriticAgent (Ollama) の介入を再現する。

Usage:
    python scripts/run_backtest.py                        # AAPL 直近30日
    python scripts/run_backtest.py --ticker NVDA --start 2026-03-01 --end 2026-04-30
    python scripts/run_backtest.py --no-critic            # CriticAgent 呼び出しなし
    python scripts/run_backtest.py --capital 20000        # 初期資金変更
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=FutureWarning)

# ─────────────────────────────────────────────────────────────
# パス設定
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from engine.constants import BACKTEST_MAX_HOLD_DAYS                    # noqa: E402
from skills.technical_calc import _calc_rsi, _calc_macd, _calc_sma25  # noqa: E402
from skills.ohlcv_cache import get_ohlcv                               # noqa: E402
from tools.critic_agent import CriticAgent                             # noqa: E402

RESULTS_DIR = PROJECT_ROOT / "data" / "backtest_results"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# バックテスト定数
# ─────────────────────────────────────────────────────────────

INDICATOR_BUFFER_DAYS = 60   # SMA25 / MACD(26) の計算に必要なバッファ日数
POSITION_SIZE_RATIO   = 0.20 # 1トレードあたり資金の20%を投入
STOP_LOSS_PCT         = 0.05 # -5% でストップロス
TAKE_PROFIT_PCT       = 0.10 # +10% で利益確定
BUY_SCORE_THRESHOLD   = 0.40 # テクニカルスコアの BUY 閾値（-1〜+1）CLI の --threshold で上書き可

# 最大保有日数（営業日）は engine.constants.BACKTEST_MAX_HOLD_DAYS を既定値とし、
# CLI の --max-hold-days で上書きする。
# ※ engine.constants.MAX_HOLD_DAYS は本番 ExitAgent 専用（初期値 0 = 無効）であり、
#    バックテストからは参照しない。

# ── ニュートラルゾーン（不感帯）定数 ──────────────────────────────────
# 0.0 にするとニュートラルゾーンなし（従来の厳格モード）
MACD_NEUTRAL_RATIO = 0.30  # histogram/|macd_line| が -0.30 以上なら neutral(0)
SMA_NEUTRAL_PCT    = 4.0   # diff_pct が -4.0% 以上（SMA を 4% 未満で下回る）なら neutral(0)

_W = 72  # 表示幅


# ─────────────────────────────────────────────────────────────
# データ取得
# ─────────────────────────────────────────────────────────────

def fetch_ohlcv(ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
    """バッファ付きで OHLCV を取得する（キャッシュ優先）。"""
    df = get_ohlcv(ticker, start=start, end=end, buffer_days=INDICATOR_BUFFER_DAYS)
    logger.info("取得完了: %s  %d 営業日  (%s 〜 %s)",
                ticker, len(df), df.index[0].date(), df.index[-1].date())
    return df


# ─────────────────────────────────────────────────────────────
# テクニカルスコア算出
# ─────────────────────────────────────────────────────────────

def _rsi_to_signal(rsi: float) -> float:
    """RSI を -1〜+1 の連続スコアに変換。"""
    if rsi < 30:
        return +1.0
    if rsi < 45:
        return +0.5
    if rsi < 55:
        return  0.0
    if rsi < 70:
        return -0.3
    return -1.0


def calc_tech_score(
    close: pd.Series,
    macd_neutral_ratio: float = 0.0,
    sma_neutral_pct:    float = 0.0,
) -> tuple[float, dict]:
    """
    RSI / MACD / SMA25 から [-1, +1] のテクニカルスコアを返す。

    Args:
        macd_neutral_ratio: histogram/|macd_line| が -ratio 以上なら neutral(0)。
                            0.0 で従来の厳格モード（binary ±1）。
        sma_neutral_pct:    diff_pct が -pct% 以上（0未満）なら neutral(0)。
                            0.0 で従来の厳格モード（binary ±1）。

    Returns:
        (score, details_dict)
    """
    rsi  = _calc_rsi(close)
    macd = _calc_macd(close)
    sma  = _calc_sma25(close)

    rsi_sig = _rsi_to_signal(rsi)

    # MACD シグナル（ニュートラルゾーン対応）
    hist      = macd["histogram"]
    macd_line = macd["macd"]
    if hist >= 0:
        macd_sig = +1.0
    elif (
        macd_neutral_ratio > 0
        and abs(macd_line) > 1e-9
        and hist / abs(macd_line) >= -macd_neutral_ratio
    ):
        macd_sig = 0.0   # 不感帯: わずかなデッドクロスは neutral
    else:
        macd_sig = -1.0

    # SMA25 シグナル（ニュートラルゾーン対応）
    diff_pct = sma["diff_pct"]
    if diff_pct >= 0:
        sma_sig = +1.0
    elif sma_neutral_pct > 0 and diff_pct >= -sma_neutral_pct:
        sma_sig = 0.0    # 不感帯: SMA を僅差で下回る場合は neutral
    else:
        sma_sig = -1.0

    # 均等ウェイトで合算
    score = round((rsi_sig + macd_sig + sma_sig) / 3, 4)
    details = {
        "rsi":       rsi,
        "rsi_sig":   rsi_sig,
        "macd_hist": hist,
        "macd_sig":  macd_sig,
        "sma25":     sma["sma25"],
        "sma_sig":   sma_sig,
        "score":     score,
    }
    return score, details


# ─────────────────────────────────────────────────────────────
# CriticAgent 呼び出しラッパー
# ─────────────────────────────────────────────────────────────

def call_critic(
    critic: CriticAgent,
    ticker: str,
    date_str: str,
    details: dict,
) -> dict:
    """
    バックテスト用 CriticAgent 呼び出し。
    retrieved_rules はバックテスト実行日時点の固定テキストを渡す。
    """
    context = (
        f"[バックテスト: {date_str}]\n"
        f"テクニカルスコア: {details['score']:+.3f}\n"
        f"RSI(14): {details['rsi']:.1f} (signal={details['rsi_sig']:+.1f})\n"
        f"MACD histogram: {details['macd_hist']:+.4f} (signal={details['macd_sig']:+.1f})\n"
        f"SMA25 乖離: {'上方' if details['sma_sig'] > 0 else '下方'}乖離 "
        f"(SMA={details['sma25']:.2f})"
    )
    rules = (
        "【バックテスト用ルール】\n"
        "- RSI > 70 の過熱状態では買いを避けること\n"
        "- MACD デッドクロス中の逆張りは高リスク\n"
        "- 全シグナルが弱気の場合は原則 OVERRIDE すること"
    )
    return critic.evaluate_trade(
        ticker          = ticker,
        manager_action  = "BUY",
        manager_context = context,
        retrieved_rules = rules,
    )


# ─────────────────────────────────────────────────────────────
# シミュレーションループ
# ─────────────────────────────────────────────────────────────

def run_simulation(
    ticker: str,
    df: pd.DataFrame,
    sim_start: datetime,
    sim_end: datetime,
    initial_capital: float,
    use_critic: bool,
    buy_threshold: float = BUY_SCORE_THRESHOLD,
    macd_neutral_ratio: float = 0.0,
    sma_neutral_pct:    float = 0.0,
    max_hold_days:      int   = BACKTEST_MAX_HOLD_DAYS,
) -> tuple[list[dict], list[float]]:
    """
    Time Travel シミュレーション。

    Returns:
        (trades, equity_curve)
        trades: 各トレードの dict リスト
        equity_curve: 日次資産額リスト
    """
    critic = CriticAgent() if use_critic else None
    capital = initial_capital
    equity_curve: list[float] = [capital]
    trades: list[dict] = []

    # シミュレーション対象日（バッファ後の営業日のみ）
    sim_dates = [d for d in df.index if sim_start <= d <= sim_end]

    position: dict | None = None  # 保有中ポジション

    for i, date in enumerate(sim_dates):
        # ── その日までのデータ（ルックアヘッドバイアス排除）
        hist = df.loc[df.index <= date, "Close"].squeeze()
        if len(hist) < 30:
            equity_curve.append(capital)
            continue

        price_today = float(df.loc[date, "Close"])
        # エントリー用スコア（ニュートラルゾーン適用）
        score_entry, details = calc_tech_score(hist, macd_neutral_ratio, sma_neutral_pct)
        # 出口用スコア（ニュートラルゾーンなし・厳格モード固定）
        score_exit, _ = calc_tech_score(hist, 0.0, 0.0)

        # ── 保有中ポジションの出口チェック
        if position is not None:
            entry_price    = position["entry_price"]
            pnl_pct        = (price_today - entry_price) / entry_price
            hold_days_done = position["hold_days"]

            exit_reason: str | None = None
            if pnl_pct <= -STOP_LOSS_PCT:
                exit_reason = "STOP_LOSS"
            elif pnl_pct >= TAKE_PROFIT_PCT:
                exit_reason = "TAKE_PROFIT"
            elif hold_days_done >= max_hold_days:
                exit_reason = "MAX_HOLD"
            elif score_exit < -0.5:
                exit_reason = "SIGNAL_REVERSAL"

            if exit_reason:
                pnl_dollar = position["qty"] * (price_today - entry_price)
                capital   += pnl_dollar
                trades.append({
                    "entry_date":   position["entry_date"],
                    "exit_date":    date.date().isoformat(),
                    "ticker":       ticker,
                    "entry_price":  entry_price,
                    "exit_price":   price_today,
                    "qty":          position["qty"],
                    "pnl_pct":      round(pnl_pct * 100, 2),
                    "pnl_dollar":   round(pnl_dollar, 2),
                    "exit_reason":  exit_reason,
                    "critic_decision": position["critic_decision"],
                    "win":          pnl_dollar > 0,
                })
                logger.info("  EXIT  %s @ $%.2f  %s  (%+.2f%%)",
                            date.date(), price_today, exit_reason, pnl_pct * 100)
                position = None
            else:
                position["hold_days"] += 1

        # ── エントリー判定（ポジションなし && エントリースコアが閾値超え）
        if position is None and score_entry >= buy_threshold:
            critic_decision = "APPROVE"
            critic_reason   = "CriticAgent 無効（--no-critic）"

            if use_critic:
                logger.info("  BUY候補 %s  entry=%+.3f exit=%+.3f → CriticAgent 評価...",
                            date.date(), score_entry, score_exit)
                result          = call_critic(critic, ticker,
                                              date.date().isoformat(), details)
                critic_decision = result.get("critic_decision", "APPROVE")
                critic_reason   = result.get("critique_reason", "")
                logger.info("  CriticAgent → %s", critic_decision)

            if critic_decision == "APPROVE":
                # 翌営業日の Open で約定
                next_idx = i + 1
                if next_idx >= len(sim_dates):
                    continue
                next_date  = sim_dates[next_idx]
                open_price = float(df.loc[next_date, "Open"]) if "Open" in df.columns else price_today

                invest_amt = capital * POSITION_SIZE_RATIO
                qty        = invest_amt / open_price

                position = {
                    "entry_date":      next_date.date().isoformat(),
                    "entry_price":     open_price,
                    "qty":             qty,
                    "hold_days":       0,
                    "critic_decision": critic_decision,
                    "critic_reason":   critic_reason,
                    "tech_score":      score_entry,
                    "details":         details,
                }
                logger.info("  BUY   %s @ $%.2f  qty=%.3f  score=%+.3f",
                            next_date.date(), open_price, qty, score_entry)
            else:
                logger.info("  OVERRIDE: %s", critic_reason[:80])
                # OVERRIDE されたトレードも記録（介入効果の測定用）
                trades.append({
                    "entry_date":      date.date().isoformat(),
                    "exit_date":       date.date().isoformat(),
                    "ticker":          ticker,
                    "entry_price":     price_today,
                    "exit_price":      price_today,
                    "qty":             0.0,
                    "pnl_pct":         0.0,
                    "pnl_dollar":      0.0,
                    "exit_reason":     "CRITIC_OVERRIDE",
                    "critic_decision": "OVERRIDE",
                    "win":             False,
                    "critic_reason":   critic_reason,
                    "override_avoided_entry_price": price_today,
                })

        equity_curve.append(capital)

    # ── シミュレーション終了時にオープンポジションを強制クローズ
    if position is not None and len(sim_dates) > 0:
        last_date  = sim_dates[-1]
        last_price = float(df.loc[last_date, "Close"])
        pnl_pct    = (last_price - position["entry_price"]) / position["entry_price"]
        pnl_dollar = position["qty"] * (last_price - position["entry_price"])
        capital   += pnl_dollar
        trades.append({
            "entry_date":      position["entry_date"],
            "exit_date":       last_date.date().isoformat(),
            "ticker":          ticker,
            "entry_price":     position["entry_price"],
            "exit_price":      last_price,
            "qty":             position["qty"],
            "pnl_pct":         round(pnl_pct * 100, 2),
            "pnl_dollar":      round(pnl_dollar, 2),
            "exit_reason":     "END_OF_PERIOD",
            "critic_decision": position["critic_decision"],
            "win":             pnl_dollar > 0,
        })
        equity_curve.append(capital)

    return trades, equity_curve


# ─────────────────────────────────────────────────────────────
# メトリクス算出
# ─────────────────────────────────────────────────────────────

def calc_metrics(
    trades: list[dict],
    equity_curve: list[float],
    initial_capital: float,
) -> dict:
    """各種パフォーマンス指標を算出する。"""
    executed     = [t for t in trades if t["critic_decision"] == "APPROVE"]
    overridden   = [t for t in trades if t["critic_decision"] == "OVERRIDE"]
    wins         = [t for t in executed if t["win"]]
    losses       = [t for t in executed if not t["win"]]

    total_pnl    = sum(t["pnl_dollar"] for t in executed)
    win_rate     = len(wins) / len(executed) * 100 if executed else 0.0

    # 最大ドローダウン
    peak = initial_capital
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak
        if dd > max_dd:
            max_dd = dd

    # OVERRIDE による回避貢献度の推定
    # → OVERRIDE 後の N 日間の実際の値動きを遡及して計算（近似）
    override_saved = 0.0
    for t in overridden:
        # OVERRIDE した日の価格でポジションを入れ "max_hold_days後" に手放した場合の損益
        # ここではログに保存した entry_price で保守的に0とする
        # （実際には run の外でデータを参照できないため 0 推定を返す）
        override_saved += 0.0  # 次セクションで後付け計算

    return {
        "initial_capital":  initial_capital,
        "final_capital":    initial_capital + total_pnl,
        "total_pnl":        round(total_pnl, 2),
        "total_pnl_pct":    round(total_pnl / initial_capital * 100, 2),
        "n_trades":         len(executed),
        "n_wins":           len(wins),
        "n_losses":         len(losses),
        "win_rate":         round(win_rate, 1),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "n_override":       len(overridden),
        "override_saved":   round(override_saved, 2),
        "avg_win_pct":      round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_pct":     round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0.0,
    }


def estimate_override_contribution(
    overridden: list[dict],
    df: pd.DataFrame,
    initial_capital: float,
    max_hold_days: int = BACKTEST_MAX_HOLD_DAYS,
) -> float:
    """
    OVERRIDE されたトレードが実際にどれだけの損失を回避したかを推定する。
    仮に BUY した場合、max_hold_days 後（または期末）の価格で決済したと仮定。
    """
    saved_total = 0.0
    for t in overridden:
        entry_date = pd.Timestamp(t["entry_date"])
        entry_price = t.get("override_avoided_entry_price", 0.0)
        if entry_price == 0:
            continue
        # entry_date 以降 max_hold_days 営業日後の終値
        future = df.loc[df.index > entry_date].head(max_hold_days)
        if future.empty:
            continue
        exit_price = float(future["Close"].iloc[-1])
        pnl_pct = (exit_price - entry_price) / entry_price
        invest = initial_capital * POSITION_SIZE_RATIO
        pnl_dollar = invest * pnl_pct
        # CriticAgent が回避することで "損失を防いだ" 額を加算
        if pnl_dollar < 0:
            saved_total += abs(pnl_dollar)
    return round(saved_total, 2)


# ─────────────────────────────────────────────────────────────
# レポート生成
# ─────────────────────────────────────────────────────────────

def _trade_table_md(trades: list[dict]) -> str:
    lines = [
        "| 日付 | 終了日 | アクション | 約定価格 | 終値 | 損益(%) | 損益($) | 終了理由 | CriticAgent |",
        "|------|--------|-----------|---------|------|---------|---------|----------|-------------|",
    ]
    for t in trades:
        action = "OVERRIDE" if t["critic_decision"] == "OVERRIDE" else "BUY→SELL"
        lines.append(
            f"| {t['entry_date']} | {t['exit_date']} | {action} "
            f"| ${t['entry_price']:.2f} | ${t['exit_price']:.2f} "
            f"| {t['pnl_pct']:+.2f}% | ${t['pnl_dollar']:+.2f} "
            f"| {t['exit_reason']} | {t['critic_decision']} |"
        )
    return "\n".join(lines)


def build_report(
    ticker: str,
    start: datetime,
    end: datetime,
    metrics: dict,
    trades: list[dict],
    use_critic: bool,
    override_contribution: float,
    max_hold_days: int = BACKTEST_MAX_HOLD_DAYS,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    pnl_sign = "+" if metrics["total_pnl"] >= 0 else ""
    executed  = [t for t in trades if t["critic_decision"] == "APPROVE"]
    overridden = [t for t in trades if t["critic_decision"] == "OVERRIDE"]

    critic_section = ""
    if use_critic:
        critic_section = f"""
## CriticAgent (Ollama) 介入分析

| 指標 | 値 |
|------|----|
| BUY シグナル発生 | {metrics['n_trades'] + metrics['n_override']} 件 |
| APPROVE（実行） | {metrics['n_trades']} 件 |
| OVERRIDE（拒否）| {metrics['n_override']} 件 |
| OVERRIDE 回避貢献（推定損失回避額） | ${override_contribution:+.2f} |

### OVERRIDE されたトレードの詳細
"""
        if overridden:
            for t in overridden:
                critic_section += (
                    f"- **{t['entry_date']}** @ ${t['entry_price']:.2f}: "
                    f"{t.get('critic_reason', '理由なし')[:120]}\n"
                )
        else:
            critic_section += "_OVERRIDE なし_\n"

    return f"""---
concept: backtest_{ticker}_{today.replace("-", "_")}
title: バックテスト結果 {ticker} ({start.date()} → {end.date()})
last_updated: {today}
ticker: {ticker}
period: {start.date()} to {end.date()}
---

# バックテスト結果: {ticker}

> 対象期間: {start.date()} → {end.date()}
> 実行日: {today}
> エンジン: Time Travel バックテスト v1.0

---

## パフォーマンスサマリー

| 指標 | 値 |
|------|----|
| 初期資金 | ${metrics['initial_capital']:,.0f} |
| 最終資金 | ${metrics['final_capital']:,.2f} |
| 合計損益 | {pnl_sign}${metrics['total_pnl']:,.2f} ({pnl_sign}{metrics['total_pnl_pct']:.2f}%) |
| 取引回数 | {metrics['n_trades']} 件 |
| 勝率 | {metrics['win_rate']:.1f}% ({metrics['n_wins']}勝 {metrics['n_losses']}敗) |
| 平均利益 | +{metrics['avg_win_pct']:.2f}% |
| 平均損失 | {metrics['avg_loss_pct']:.2f}% |
| 最大ドローダウン | -{metrics['max_drawdown_pct']:.2f}% |

## シミュレーション設定

| パラメータ | 値 |
|------------|----|
| BUY 閾値（テクニカルスコア） | {BUY_SCORE_THRESHOLD:.2f} |
| ストップロス | -{STOP_LOSS_PCT*100:.0f}% |
| 利益確定 | +{TAKE_PROFIT_PCT*100:.0f}% |
| 最大保有日数 | {max_hold_days} 営業日 |
| 1トレード投入比率 | {POSITION_SIZE_RATIO*100:.0f}% |
| CriticAgent | {'有効 (Ollama)' if use_critic else '無効'} |
{critic_section}
## 取引履歴

{_trade_table_md(trades) if trades else "_取引なし_"}

---

*このレポートは `scripts/run_backtest.py` によって自動生成されました。*
"""


# ─────────────────────────────────────────────────────────────
# コンソール表示
# ─────────────────────────────────────────────────────────────

def print_report(ticker: str, start: datetime, end: datetime, metrics: dict, use_critic: bool) -> None:
    print()
    print("═" * _W)
    print(f"  バックテスト結果  {ticker}  ({start.date()} → {end.date()})")
    print("═" * _W)
    pnl_sign = "+" if metrics["total_pnl"] >= 0 else ""
    print(f"  初期資金         : ${metrics['initial_capital']:>10,.0f}")
    print(f"  最終資金         : ${metrics['final_capital']:>10,.2f}")
    print(f"  合計損益         : {pnl_sign}${metrics['total_pnl']:,.2f}  ({pnl_sign}{metrics['total_pnl_pct']:.2f}%)")
    print(f"  取引回数         : {metrics['n_trades']} 件  ({metrics['n_wins']}勝 / {metrics['n_losses']}敗)")
    print(f"  勝率             : {metrics['win_rate']:.1f}%")
    print(f"  平均利益         : +{metrics['avg_win_pct']:.2f}%")
    print(f"  平均損失         : {metrics['avg_loss_pct']:.2f}%")
    print(f"  最大ドローダウン : -{metrics['max_drawdown_pct']:.2f}%")
    if use_critic:
        print("─" * _W)
        print(f"  CriticAgent OVERRIDE : {metrics['n_override']} 件")
    print("═" * _W)


# ─────────────────────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────────────────────

def main() -> None:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    default_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    default_end   = today.strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser(description="Time Travel バックテストエンジン")
    parser.add_argument("--ticker",    default="AAPL",         help="銘柄ティッカー（デフォルト: AAPL）")
    parser.add_argument("--start",     default=default_start,  help="開始日 YYYY-MM-DD")
    parser.add_argument("--end",       default=default_end,    help="終了日 YYYY-MM-DD")
    parser.add_argument("--capital",   type=float, default=10000, help="初期資金（デフォルト: 10000）")
    parser.add_argument("--no-critic", action="store_true",    help="CriticAgent を呼び出さない")
    parser.add_argument("--threshold", type=float, default=BUY_SCORE_THRESHOLD, help=f"BUY スコア閾値（デフォルト: {BUY_SCORE_THRESHOLD}）")
    parser.add_argument(
        "--max-hold-days", type=int, default=BACKTEST_MAX_HOLD_DAYS,
        help=f"最大保有日数・営業日（デフォルト: {BACKTEST_MAX_HOLD_DAYS}）",
    )
    args = parser.parse_args()

    ticker    = args.ticker.upper()
    sim_start = datetime.strptime(args.start, "%Y-%m-%d")
    sim_end   = datetime.strptime(args.end,   "%Y-%m-%d")
    use_critic = not args.no_critic

    logger.info("バックテスト開始: %s  %s → %s  資金=$%.0f  閾値=%.2f  CriticAgent=%s",
                ticker, sim_start.date(), sim_end.date(), args.capital, args.threshold,
                "有効" if use_critic else "無効")

    # ── データ取得
    df = fetch_ohlcv(ticker, sim_start, sim_end)

    # ── シミュレーション
    trades, equity_curve = run_simulation(
        ticker          = ticker,
        df              = df,
        sim_start       = sim_start,
        sim_end         = sim_end,
        initial_capital = args.capital,
        use_critic      = use_critic,
        buy_threshold   = args.threshold,
        max_hold_days   = args.max_hold_days,
    )

    # ── メトリクス
    metrics = calc_metrics(trades, equity_curve, args.capital)

    # ── OVERRIDE 貢献推定
    overridden = [t for t in trades if t["critic_decision"] == "OVERRIDE"]
    override_contribution = estimate_override_contribution(
        overridden, df, args.capital, max_hold_days=args.max_hold_days
    )
    metrics["override_saved"] = override_contribution

    # ── コンソール出力
    print_report(ticker, sim_start, sim_end, metrics, use_critic)

    # ── レポート保存
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report_date = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"backtest_{ticker}_{report_date}.md"
    report_md   = build_report(
        ticker                = ticker,
        start                 = sim_start,
        end                   = sim_end,
        metrics               = metrics,
        trades                = trades,
        use_critic            = use_critic,
        override_contribution = override_contribution,
        max_hold_days         = args.max_hold_days,
    )
    report_path.write_text(report_md, encoding="utf-8")
    logger.info("レポート保存: %s", report_path)
    print(f"\n  レポート → {report_path}\n")


if __name__ == "__main__":
    main()
