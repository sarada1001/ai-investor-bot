#!/usr/bin/env python3
"""
scripts/run_agent_exam.py — Walk-Forward Testing System (Agent Exam)

過去N日間のS&P500高ボラティリティ銘柄に対してテクニカル事前フィルター
（RSI / MACD / ボリンジャーバンド）でエントリーシグナルを検出し、
ATRベースのSL/TPで walk-forward シミュレーションを実施する。

各エージェントの合否判定:
  - 全エージェント共通: 全仮想取引の勝率で判定
  - MacroAgent: 全仮想取引で評価し、追加で macro_ok=True 条件下の勝率を参考指標として記録

  注: 全エージェントが同一の評価プールを共有するため、各エージェント固有の貢献は測定しない。
  本 Exam はシステム全体のシグナル収益性を評価する。

合格条件: ≥20 仮想取引 AND ≥50% 勝率 → ACTIVE ; それ以外 → SUSPENDED

data/agent_status.json を更新（既存フォーマット・coaching_prompt を保持）。

Usage:
    python scripts/run_agent_exam.py               # 全エージェント検定（過去 90 日）
    python scripts/run_agent_exam.py --dry-run     # ステータス更新なし
    python scripts/run_agent_exam.py --days 60     # 検定期間変更
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────
# パス設定
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from skills.technical_calc import _calc_rsi, _calc_macd  # noqa: E402

AGENT_STATUS_PATH = PROJECT_ROOT / "data" / "agent_status.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────────────────────

EXAM_UNIVERSE: list[str] = [
    "TSLA", "NVDA", "AAPL", "META", "AMZN",
    "AMD",  "NFLX", "GOOGL", "MSFT", "JPM",
    "XOM",  "SMCI", "PLTR",  "COIN", "MU",
]

ALL_EVAL_AGENTS: list[str] = [
    "ManagerAgent",
    "TechnicalAgent",
    "NewsAgent",
    "MacroAgent",
    "SocialAgent",
    "FundamentalAgent",
]

INDICATOR_BUFFER_DAYS = 60    # MACD/SMA 計算用ウォームアップ
MAX_HOLD_DAYS         = 10    # 最大保有日数（営業日）

ATR_PERIOD      = 14
ATR_MULTIPLIER  = 2.0         # SL = entry - ATR × 2.0
TP_MULTIPLIER   = 4.0         # TP = entry + ATR × 4.0  (1:2 RR)
ATR_FALLBACK    = 0.015       # ATR 計算失敗時の価格比率

RSI_OVERSOLD        = 35.0    # RSI < 35 → 買いシグナル
BB_PERIOD           = 20      # ボリンジャーバンド期間
VIX_CALM_THRESHOLD  = 25.0    # VIX < 25 → マクロ良好（関税ショック相場対応で緩和）
MACRO_SMA_PERIOD    = 200     # SPY の 200 日移動平均

PASS_MIN_TRADES    = 20       # 合格最低取引数
PASS_MIN_WIN_RATE  = 0.50     # 合格最低勝率

_W = 72  # 表示幅


# ─────────────────────────────────────────────────────────────
# データクラス
# ─────────────────────────────────────────────────────────────

@dataclass
class VirtualTrade:
    ticker:      str
    entry_date:  str
    exit_date:   str
    entry_price: float
    exit_price:  float
    atr:         float
    sl_price:    float
    tp_price:    float
    exit_reason: str    # "TAKE_PROFIT" | "STOP_LOSS" | "TIMEOUT"
    pnl_pct:     float
    win:         bool
    macro_ok:    bool   # SPY > SMA200 かつ VIX < VIX_CALM_THRESHOLD (現在: 25)


@dataclass
class AgentExamResult:
    agent_name:     str
    total_trades:   int
    winning_trades: int
    losing_trades:  int
    win_rate:       float
    passed:         bool
    status:         str   # "ACTIVE" | "SUSPENDED"
    reason:         str
    macro_ok_rate:  float | None = None  # MacroAgent 用参考値: None=macro_ok取引なし, 0.0=全敗


# ─────────────────────────────────────────────────────────────
# データ取得
# ─────────────────────────────────────────────────────────────

def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    """MultiIndex カラムを単一レベルに平坦化する。"""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    return df


def fetch_ohlcv(ticker: str, start: datetime, end: datetime) -> pd.DataFrame | None:
    """バッファ付きで OHLCV を取得する。失敗時は None を返す。"""
    fetch_start = start - timedelta(days=INDICATOR_BUFFER_DAYS)
    try:
        df = yf.download(
            ticker,
            start=fetch_start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df.empty or len(df) < 30:
            logger.warning("%s: データ不足（%d 行）— スキップ", ticker, len(df))
            return None
        return _flatten(df)
    except Exception as e:
        logger.warning("%s: 取得失敗 — %s — スキップ", ticker, e)
        return None


def fetch_macro_data(
    start: datetime, end: datetime
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """SPY（SMA200 用に長期取得）と VIX を取得する。"""
    spy_start = start - timedelta(days=MACRO_SMA_PERIOD * 2)

    spy_df: pd.DataFrame | None = None
    vix_df: pd.DataFrame | None = None

    try:
        raw = yf.download(
            "SPY",
            start=spy_start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if not raw.empty:
            spy_df = _flatten(raw)
            logger.info("SPY 取得完了: %d 行", len(spy_df))
    except Exception as e:
        logger.warning("SPY 取得失敗: %s", e)

    try:
        raw = yf.download(
            "^VIX",
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if not raw.empty:
            vix_df = _flatten(raw)
            logger.info("VIX 取得完了: %d 行", len(vix_df))
    except Exception as e:
        logger.warning("VIX 取得失敗: %s", e)

    return spy_df, vix_df


# ─────────────────────────────────────────────────────────────
# インジケーター計算
# ─────────────────────────────────────────────────────────────

def _calc_atr(df_slice: pd.DataFrame) -> float:
    """14 日間 ATR を計算する。データ不足時はフォールバック値を返す。"""
    if len(df_slice) < ATR_PERIOD + 1:
        return float(df_slice["Close"].iloc[-1]) * ATR_FALLBACK
    high  = df_slice["High"].values
    low   = df_slice["Low"].values
    close = df_slice["Close"].values
    tr = [
        max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i]  - close[i - 1]),
        )
        for i in range(1, len(df_slice))
    ]
    return float(sum(tr[-ATR_PERIOD:]) / ATR_PERIOD)


def _calc_bb_lower(close: pd.Series) -> float:
    """ボリンジャーバンド（20日・2σ）下限を返す。"""
    lower = close.rolling(BB_PERIOD).mean() - 2.0 * close.rolling(BB_PERIOD).std()
    return float(lower.iloc[-1])


def _is_entry_signal(hist: pd.DataFrame) -> bool:
    """
    エントリーシグナル判定（いずれか 1 条件で True）:
      1. RSI(14) < RSI_OVERSOLD  （売られすぎ）
      2. MACD ヒストグラムが 負→正 にクロス（ゴールデンクロス）
      3. 終値 ≤ ボリンジャーバンド下限
    """
    close = hist["Close"].squeeze()
    if len(close) < 30:
        return False

    try:
        if _calc_rsi(close) < RSI_OVERSOLD:
            return True
    except Exception:
        pass

    try:
        curr_hist = _calc_macd(close)["histogram"]
        if curr_hist >= 0:  # プラスの場合のみ前期を確認（最適化）
            prev_hist = _calc_macd(close.iloc[:-1])["histogram"]
            if prev_hist < 0:
                return True
    except Exception:
        pass

    try:
        if float(close.iloc[-1]) <= _calc_bb_lower(close):
            return True
    except Exception:
        pass

    return False


def _is_macro_ok(
    date: pd.Timestamp,
    spy_df: pd.DataFrame | None,
    vix_df: pd.DataFrame | None,
) -> bool:
    """SPY > SMA200 かつ VIX < VIX_CALM_THRESHOLD を満たすか。データなし → True（制約なし）。"""
    spy_ok = True
    vix_ok = True

    if spy_df is not None:
        spy_hist = spy_df.loc[spy_df.index <= date, "Close"]
        if isinstance(spy_hist, pd.DataFrame):
            spy_hist = spy_hist.iloc[:, 0]
        if len(spy_hist) >= MACRO_SMA_PERIOD:
            sma200 = float(spy_hist.rolling(MACRO_SMA_PERIOD).mean().iloc[-1])
            spy_ok = float(spy_hist.iloc[-1]) > sma200

    if vix_df is not None:
        vix_hist = vix_df.loc[vix_df.index <= date, "Close"]
        if isinstance(vix_hist, pd.DataFrame):
            vix_hist = vix_hist.iloc[:, 0]
        if len(vix_hist) > 0:
            vix_ok = float(vix_hist.iloc[-1]) < VIX_CALM_THRESHOLD

    return spy_ok and vix_ok


# ─────────────────────────────────────────────────────────────
# Walk-Forward シミュレーション
# ─────────────────────────────────────────────────────────────

def _walk_forward(
    df: pd.DataFrame,
    entry_idx: int,
    sl_price: float,
    tp_price: float,
) -> tuple[str, float, str]:
    """
    エントリー翌日から MAX_HOLD_DAYS 間、High/Low で SL/TP を確認する。

    SL と TP が同日に触れた場合は SL 優先（保守的）。

    Returns:
        (exit_reason, exit_price, exit_date_str)
        exit_reason: "TAKE_PROFIT" | "STOP_LOSS" | "TIMEOUT"
    """
    for i in range(1, MAX_HOLD_DAYS + 1):
        idx = entry_idx + i
        if idx >= len(df):
            break
        row      = df.iloc[idx]
        date_str = df.index[idx].date().isoformat()
        if float(row["Low"]) <= sl_price:
            return "STOP_LOSS",   sl_price, date_str
        if float(row["High"]) >= tp_price:
            return "TAKE_PROFIT", tp_price, date_str

    last_idx   = min(entry_idx + MAX_HOLD_DAYS, len(df) - 1)
    exit_price = float(df.iloc[last_idx]["Close"])
    date_str   = df.index[last_idx].date().isoformat()
    return "TIMEOUT", exit_price, date_str


def simulate_ticker(
    ticker: str,
    df: pd.DataFrame,
    sim_start: datetime,
    spy_df: pd.DataFrame | None,
    vix_df: pd.DataFrame | None,
) -> list[VirtualTrade]:
    """1 銘柄の walk-forward 仮想トレードリストを生成する。"""
    trades: list[VirtualTrade] = []
    sim_start_ts  = pd.Timestamp(sim_start)
    skip_until_ts = pd.Timestamp("1970-01-01")  # 初回はスキップなし

    for idx, date in enumerate(df.index):
        if date < sim_start_ts:
            continue
        if date <= skip_until_ts:
            continue

        hist = df.iloc[: idx + 1]  # ルックアヘッドバイアス排除

        if not _is_entry_signal(hist):
            continue

        entry_price = float(df.iloc[idx]["Close"])
        atr         = _calc_atr(hist)
        sl_price    = entry_price - ATR_MULTIPLIER * atr
        tp_price    = entry_price + TP_MULTIPLIER  * atr

        exit_reason, exit_price, exit_date_str = _walk_forward(
            df, idx, sl_price, tp_price
        )

        pnl_pct = (exit_price - entry_price) / entry_price * 100.0
        win = (
            exit_reason == "TAKE_PROFIT"
            or (exit_reason == "TIMEOUT" and exit_price > entry_price)
        )
        macro_ok = _is_macro_ok(date, spy_df, vix_df)

        trades.append(VirtualTrade(
            ticker=ticker,
            entry_date=date.date().isoformat(),
            exit_date=exit_date_str,
            entry_price=round(entry_price, 4),
            exit_price=round(exit_price, 4),
            atr=round(atr, 4),
            sl_price=round(sl_price, 4),
            tp_price=round(tp_price, 4),
            exit_reason=exit_reason,
            pnl_pct=round(pnl_pct, 2),
            win=win,
            macro_ok=macro_ok,
        ))

        # 次のエントリーは exit_date 以降（重複ポジション回避）
        skip_until_ts = pd.Timestamp(exit_date_str)

    return trades


# ─────────────────────────────────────────────────────────────
# エージェント評価
# ─────────────────────────────────────────────────────────────

def evaluate_agent(
    agent_name: str,
    all_trades: list[VirtualTrade],
) -> AgentExamResult:
    """
    エージェント別合否判定。

    全エージェントを全仮想取引で評価する。
    MacroAgent は追加で macro_ok=True 条件下の勝率を参考指標として計算する。
    """
    total  = len(all_trades)
    wins   = sum(1 for t in all_trades if t.win)
    losses = total - wins
    rate   = wins / total if total > 0 else 0.0

    # MacroAgent 参考値: macro_ok 条件（SPY>SMA200 かつ VIX<VIX_CALM_THRESHOLD）下の勝率
    # None: macro_ok=True のトレードが存在しない（未定義）
    # 0.0 : macro_ok トレードが全て負けた（定義済みの値）
    macro_ok_rate: float | None = None
    if agent_name == "MacroAgent":
        ok_trades = [t for t in all_trades if t.macro_ok]
        if ok_trades:
            macro_ok_wins = sum(1 for t in ok_trades if t.win)
            macro_ok_rate = round(macro_ok_wins / len(ok_trades), 4)

    if total < PASS_MIN_TRADES:
        status = "SUSPENDED"
        reason = (
            f"仮想取引数不足: {total} < {PASS_MIN_TRADES}"
            " — 検定データ不足のため評価保留"
        )
        passed = False
    elif rate >= PASS_MIN_WIN_RATE:
        status = "ACTIVE"
        reason = (
            f"合格: 勝率 {rate:.1%} >= {PASS_MIN_WIN_RATE:.1%}"
            f"（{total} 取引中 {wins} 勝）"
        )
        passed = True
    else:
        status = "SUSPENDED"
        reason = (
            f"不合格: 勝率 {rate:.1%} < {PASS_MIN_WIN_RATE:.1%}"
            f"（{total} 取引中 {wins} 勝）"
        )
        passed = False

    return AgentExamResult(
        agent_name=agent_name,
        total_trades=total,
        winning_trades=wins,
        losing_trades=losses,
        win_rate=round(rate, 4),
        passed=passed,
        status=status,
        reason=reason,
        macro_ok_rate=macro_ok_rate,
    )


# ─────────────────────────────────────────────────────────────
# agent_status.json 更新
# ─────────────────────────────────────────────────────────────

def _load_status() -> dict:
    if AGENT_STATUS_PATH.exists():
        with AGENT_STATUS_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_status(status_dict: dict) -> None:
    AGENT_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AGENT_STATUS_PATH.open("w", encoding="utf-8") as f:
        json.dump(status_dict, f, ensure_ascii=False, indent=2)


def apply_exam_results(
    results: list[AgentExamResult],
    exam_period: str,
    dry_run: bool,
) -> None:
    """検定結果を agent_status.json に書き込む（coaching_prompt など既存フィールドを保持）。"""
    existing = _load_status()
    now      = datetime.now().isoformat()

    for r in results:
        prev = existing.get(r.agent_name, {})
        entry: dict = {
            "status":            r.status,
            "win_rate":          r.win_rate,
            "total_trades":      r.total_trades,
            "winning_trades":    r.winning_trades,
            "losing_trades":     r.losing_trades,
            "suspension_reason": r.reason,
            "coaching_prompt":   prev.get("coaching_prompt", ""),
            "last_evaluated":    now,
            "exam_mode":         True,
            "exam_period":       exam_period,
        }
        if r.macro_ok_rate is not None:
            entry["macro_ok_rate"] = r.macro_ok_rate
        existing[r.agent_name] = entry

    if dry_run:
        logger.info("[DRY RUN] agent_status.json 更新をスキップ")
        return

    _save_status(existing)
    logger.info("agent_status.json 更新完了 (%s)", AGENT_STATUS_PATH)


# ─────────────────────────────────────────────────────────────
# コンソール出力
# ─────────────────────────────────────────────────────────────

def _print_summary(
    results: list[AgentExamResult],
    all_trades: list[VirtualTrade],
    exam_days: int,
    dry_run: bool,
) -> None:
    total   = len(all_trades)
    tickers = len({t.ticker for t in all_trades})
    wins    = sum(1 for t in all_trades if t.win)
    rate    = wins / total * 100 if total else 0.0

    print()
    print("═" * _W)
    print(f"  Agent Exam — Walk-Forward テスト結果  (過去 {exam_days} 日間)")
    print("═" * _W)
    print(f"  検定ユニバース   : {tickers} 銘柄  /  仮想取引合計: {total} 件")
    print(f"  全体勝率         : {rate:.1f}%  ({wins} 勝 / {total - wins} 敗)")
    print("─" * _W)
    print(f"  {'エージェント':<22}  {'取引':>5}  {'勝率':>7}  {'判定':>7}  ステータス")
    print("─" * _W)

    for r in results:
        label    = "🟢 ACTIVE   " if r.status == "ACTIVE" else "🔴 SUSPENDED"
        pass_str = "✅ 合格" if r.passed else "❌ 不合格"
        print(
            f"  {r.agent_name:<22}  {r.total_trades:>5}  "
            f"{r.win_rate * 100:>6.1f}%  {pass_str}  {label}"
        )
        if r.macro_ok_rate is not None:
            print(
                f"  {'':22}  {'':5}  "
                f"  (macro_ok 参考: {r.macro_ok_rate * 100:.1f}%)"
            )

    print("═" * _W)
    note = "[DRY RUN] agent_status.json は更新されませんでした。" if dry_run else \
           "agent_status.json を更新しました。"
    print(f"  {note}")
    print()


# ─────────────────────────────────────────────────────────────
# メインロジック
# ─────────────────────────────────────────────────────────────

def run_exam(exam_days: int = 90, dry_run: bool = False) -> list[AgentExamResult]:
    """Walk-Forward テストを実行してエージェント合否を返す。"""
    today     = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    sim_end   = today
    sim_start = today - timedelta(days=exam_days)

    logger.info(
        "Walk-Forward 検定開始: %s 〜 %s  (%d 日間)",
        sim_start.date(), sim_end.date(), exam_days,
    )
    logger.info("ユニバース: %s", ", ".join(EXAM_UNIVERSE))

    # マクロデータ取得（全銘柄共通）
    logger.info("マクロデータ取得中 (SPY / ^VIX)...")
    spy_df, vix_df = fetch_macro_data(sim_start, sim_end)

    # 全銘柄の仮想トレード収集
    all_trades: list[VirtualTrade] = []

    for ticker in EXAM_UNIVERSE:
        logger.info("[%s] OHLCV 取得中...", ticker)
        df = fetch_ohlcv(ticker, sim_start, sim_end)
        if df is None:
            continue

        ticker_trades = simulate_ticker(ticker, df, sim_start, spy_df, vix_df)
        all_trades.extend(ticker_trades)

        wins_t = sum(1 for t in ticker_trades if t.win)
        rate_t = wins_t / len(ticker_trades) * 100 if ticker_trades else 0.0
        logger.info(
            "[%s] %d 件シグナル  %d 勝 / %d 敗  勝率 %.1f%%",
            ticker,
            len(ticker_trades),
            wins_t,
            len(ticker_trades) - wins_t,
            rate_t,
        )

    logger.info("全銘柄処理完了: 合計 %d 仮想トレード", len(all_trades))

    # エージェント別評価
    results = [evaluate_agent(name, all_trades) for name in ALL_EVAL_AGENTS]

    exam_period = f"{exam_days}d"
    apply_exam_results(results, exam_period, dry_run)
    _print_summary(results, all_trades, exam_days, dry_run)

    return results


# ─────────────────────────────────────────────────────────────
# CLI エントリポイント
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent Exam — Walk-Forward テストシステム"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="検定期間（日数、デフォルト: 90）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="agent_status.json を更新せずに検定のみ実行する",
    )
    args = parser.parse_args()

    run_exam(args.days, args.dry_run)


if __name__ == "__main__":
    main()
