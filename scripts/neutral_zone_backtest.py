#!/usr/bin/env python3
"""
scripts/neutral_zone_backtest.py — ニュートラルゾーン A/B 比較バックテスト

MACD / SMA25 の「不感帯（neutral zone）」有無で取引機会と品質がどう変わるかを
40銘柄 Universe で比較する。

パターンA: ニュートラルゾーンなし（従来の厳格な ±1 バイナリ判定）
パターンB: ニュートラルゾーンあり（MACD ratio + SMA pct で不感帯を設定）

Usage:
    python scripts/neutral_zone_backtest.py
    python scripts/neutral_zone_backtest.py --start 2026-02-01 --end 2026-05-01
    python scripts/neutral_zone_backtest.py --macd-ratio 0.20 --sma-pct 2.0
    python scripts/neutral_zone_backtest.py --threshold 0.40 --capital 10000
"""

from __future__ import annotations

import argparse
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

SCRIPT_DIR   = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from scripts.run_backtest import (   # noqa: E402
    fetch_ohlcv,
    run_simulation,
    calc_metrics,
    MACD_NEUTRAL_RATIO,
    SMA_NEUTRAL_PCT,
    BUY_SCORE_THRESHOLD,
)
from scripts.universe_backtest import UNIVERSE  # noqa: E402

_W = 88  # 表示幅


# ─────────────────────────────────────────────────────────────
# コア: 1パターンを全 Universe で実行
# ─────────────────────────────────────────────────────────────

def run_pattern(
    start: datetime,
    end: datetime,
    initial_capital: float,
    buy_threshold: float,
    macd_neutral_ratio: float,
    sma_neutral_pct: float,
    cached_dfs: dict[str, pd.DataFrame] | None = None,
) -> tuple[dict[str, dict], dict[str, pd.DataFrame]]:
    """
    全 Universe を指定パターンで実行し結果を返す。

    Returns:
        results : ticker → metrics + trades list
        dfs     : ticker → DataFrame（次パターンで再利用）
    """
    results: dict[str, dict] = {}
    dfs: dict[str, pd.DataFrame] = cached_dfs or {}

    all_tickers = [t for tickers in UNIVERSE.values() for t in tickers]

    for ticker in all_tickers:
        try:
            if ticker not in dfs:
                dfs[ticker] = fetch_ohlcv(ticker, start, end)

            trades, equity_curve = run_simulation(
                ticker             = ticker,
                df                 = dfs[ticker],
                sim_start          = start,
                sim_end            = end,
                initial_capital    = initial_capital,
                use_critic         = False,
                buy_threshold      = buy_threshold,
                macd_neutral_ratio = macd_neutral_ratio,
                sma_neutral_pct    = sma_neutral_pct,
            )
            metrics = calc_metrics(trades, equity_curve, initial_capital)
            # avg_pnl_pct を trades から算出
            executed = [t for t in trades if t.get("critic_decision") == "APPROVE"]
            avg_pnl = (
                sum(t["pnl_pct"] for t in executed) / len(executed)
                if executed else 0.0
            )
            metrics["avg_pnl_pct"] = round(avg_pnl, 3)
            results[ticker] = metrics
        except Exception as exc:
            results[ticker] = {"error": str(exc), "n_trades": 0}

    return results, dfs


# ─────────────────────────────────────────────────────────────
# 集計
# ─────────────────────────────────────────────────────────────

def _aggregate(results: dict[str, dict]) -> dict:
    """全銘柄の合計・平均を集計する。"""
    n_trades_total = 0
    wins = 0
    pnl_list: list[float] = []
    error_tickers: list[str] = []

    for ticker, m in results.items():
        if "error" in m and m.get("n_trades", 0) == 0:
            error_tickers.append(ticker)
            continue
        n = m.get("n_trades", 0)
        n_trades_total += n
        wins += m.get("n_wins", 0)
        avg_pnl = m.get("avg_pnl_pct", 0.0) or 0.0
        if n > 0:
            pnl_list.extend([avg_pnl] * n)

    win_rate  = wins / n_trades_total * 100 if n_trades_total > 0 else 0.0
    avg_pnl   = sum(pnl_list) / len(pnl_list) if pnl_list else 0.0
    total_pnl = sum(pnl_list)

    return {
        "n_trades":      n_trades_total,
        "n_wins":        wins,
        "win_rate":      round(win_rate, 1),
        "avg_pnl_pct":   round(avg_pnl, 3),
        "total_pnl_pct": round(total_pnl, 2),
        "n_errors":      len(error_tickers),
        "error_tickers": error_tickers,
    }


# ─────────────────────────────────────────────────────────────
# レポート出力
# ─────────────────────────────────────────────────────────────

def _print_verdict(agg_a: dict, agg_b: dict) -> None:
    """ニュートラルゾーンの効果を判定して出力する。"""
    trade_delta = agg_b["n_trades"]    - agg_a["n_trades"]
    wr_delta    = agg_b["win_rate"]    - agg_a["win_rate"]
    pnl_delta   = agg_b["avg_pnl_pct"] - agg_a["avg_pnl_pct"]
    total_delta = agg_b["total_pnl_pct"] - agg_a["total_pnl_pct"]

    print("=" * _W)
    print("【総合評価】ニュートラルゾーンの効果")
    print("=" * _W)

    trade_up = trade_delta > 0
    wr_ok    = wr_delta >= -3.0    # 勝率低下が 3%以内なら許容
    pnl_ok   = pnl_delta >= -0.05  # 平均P&L低下が 0.05%以内なら許容
    total_up = total_delta > 0

    if trade_up and wr_ok and pnl_ok and total_up:
        verdict = "✅ 効果的なチューニング"
        detail  = "取引数↑ / 勝率微減以内 / 総P&L↑ の三条件を満たす"
    elif trade_up and wr_ok and not total_up:
        verdict = "⚠️  混合シグナル"
        detail  = "取引数は増えたが総P&Lが改善せず。閾値の再調整を検討"
    elif not wr_ok:
        verdict = "🔴 危険：勝率が大幅低下"
        detail  = f"勝率 {wr_delta:+.1f}% — ニュートラルゾーンが悪質シグナルを通過させている"
    elif not trade_up:
        verdict = "➡️  効果なし"
        detail  = "取引数に変化なし。パラメータ調整または他の改善策を検討"
    else:
        verdict = "⚠️  要検討"
        detail  = "一部指標が改善、一部が悪化。詳細な銘柄別分析を推奨"

    print(f"  判定 : {verdict}")
    print(f"  根拠 : {detail}")
    print()
    print(f"  取引数変化    : {agg_a['n_trades']:>4d}  →  {agg_b['n_trades']:>4d}  ({trade_delta:+d})")
    print(f"  勝率変化      : {agg_a['win_rate']:>5.1f}%  →  {agg_b['win_rate']:>5.1f}%  ({wr_delta:+.1f}%)")
    print(f"  平均P&L変化   : {agg_a['avg_pnl_pct']:>+7.3f}%  →  {agg_b['avg_pnl_pct']:>+7.3f}%  ({pnl_delta:+.3f}%)")
    print(f"  総P&L変化     : {agg_a['total_pnl_pct']:>+8.2f}%  →  {agg_b['total_pnl_pct']:>+8.2f}%  ({total_delta:+.2f}%)")
    print("=" * _W)


def print_report(
    results_a: dict[str, dict],
    results_b: dict[str, dict],
    label_a: str,
    label_b: str,
    start: datetime,
    end: datetime,
    threshold: float,
    macd_ratio: float,
    sma_pct: float,
) -> None:
    """比較レポートを標準出力に表示する。"""
    agg_a = _aggregate(results_a)
    agg_b = _aggregate(results_b)

    print()
    print("=" * _W)
    print("  ニュートラルゾーン A/B 比較バックテスト")
    print(f"  期間: {start.date()} 〜 {end.date()}")
    print(f"  閾値: {threshold:.2f}  |  MACD ratio: {macd_ratio}  |  SMA pct: {sma_pct}%")
    print(f"  Universe: {sum(len(v) for v in UNIVERSE.values())} 銘柄")
    print("=" * _W)

    # ── アグリゲート比較テーブル
    print(f"  {'':20s}  {'取引数':>7s}  {'勝率':>7s}  {'平均P&L':>9s}  {'総P&L':>9s}")
    print("-" * _W)
    for label, agg in [(label_a, agg_a), (label_b, agg_b)]:
        print(
            f"  {label:<20s}  {agg['n_trades']:>7d}  "
            f"{agg['win_rate']:>6.1f}%  {agg['avg_pnl_pct']:>+8.3f}%  "
            f"{agg['total_pnl_pct']:>+8.2f}%"
        )
    print("-" * _W)

    # ── 銘柄別差分テーブル（取引数が変化した銘柄）
    changed: list[tuple[str, dict, dict, int]] = []
    for ticker in results_a:
        ma = results_a[ticker]
        mb = results_b[ticker]
        if "error" in ma and ma.get("n_trades", 0) == 0:
            continue
        if "error" in mb and mb.get("n_trades", 0) == 0:
            continue
        delta = mb.get("n_trades", 0) - ma.get("n_trades", 0)
        if delta != 0:
            changed.append((ticker, ma, mb, delta))

    changed.sort(key=lambda x: -abs(x[3]))  # 変化量降順

    if changed:
        print()
        print(f"  銘柄別変化（取引数が変化した銘柄: {len(changed)} 銘柄）")
        print(
            f"  {'Ticker':>6s}  {'A取引':>5s}  {'B取引':>5s}  {'Δ':>4s}  "
            f"{'A勝率':>6s}  {'B勝率':>6s}  {'A avg':>7s}  {'B avg':>7s}"
        )
        print("-" * _W)
        for ticker, ma, mb, delta in changed[:25]:
            a_wr  = ma.get("win_rate", 0.0) or 0.0
            b_wr  = mb.get("win_rate", 0.0) or 0.0
            a_avg = ma.get("avg_pnl_pct", 0.0) or 0.0
            b_avg = mb.get("avg_pnl_pct", 0.0) or 0.0
            print(
                f"  {ticker:>6s}  {ma['n_trades']:>5d}  {mb['n_trades']:>5d}  "
                f"{delta:>+4d}  {a_wr:>5.1f}%  {b_wr:>5.1f}%  "
                f"{a_avg:>+6.2f}%  {b_avg:>+6.2f}%"
            )
    else:
        print()
        print("  ※ 取引数の変化なし — ニュートラルゾーンが機能していません")
        print("     MACD ratio / SMA pct のパラメータを大きくするか、")
        print("     閾値 (--threshold) を見直してください。")

    print()
    _print_verdict(agg_a, agg_b)


# ─────────────────────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ニュートラルゾーン A/B 比較バックテスト")
    parser.add_argument("--start",      default=None,
                        help="シミュレーション開始日 YYYY-MM-DD（デフォルト: 3ヶ月前）")
    parser.add_argument("--end",        default=None,
                        help="シミュレーション終了日 YYYY-MM-DD（デフォルト: 昨日）")
    parser.add_argument("--threshold",  type=float, default=BUY_SCORE_THRESHOLD,
                        help=f"テクニカルスコア BUY 閾値（デフォルト: {BUY_SCORE_THRESHOLD}）")
    parser.add_argument("--capital",    type=float, default=10_000.0,
                        help="初期資金（デフォルト: 10000）")
    parser.add_argument("--macd-ratio", type=float, default=MACD_NEUTRAL_RATIO,
                        help=f"MACD ニュートラル比率（デフォルト: {MACD_NEUTRAL_RATIO}）")
    parser.add_argument("--sma-pct",    type=float, default=SMA_NEUTRAL_PCT,
                        help=f"SMA ニュートラル帯 %%（デフォルト: {SMA_NEUTRAL_PCT}）")
    args = parser.parse_args()

    end   = datetime.strptime(args.end,   "%Y-%m-%d") if args.end   else datetime.now() - timedelta(days=1)
    start = datetime.strptime(args.start, "%Y-%m-%d") if args.start else end - timedelta(days=90)

    label_a = "A: ゾーンなし (厳格)"
    label_b = f"B: ゾーンあり ({args.macd_ratio}/{args.sma_pct}%)"

    print(f"\n[パターン A] {label_a} を実行中...")
    results_a, dfs = run_pattern(
        start              = start,
        end                = end,
        initial_capital    = args.capital,
        buy_threshold      = args.threshold,
        macd_neutral_ratio = 0.0,
        sma_neutral_pct    = 0.0,
    )

    print(f"\n[パターン B] {label_b} を実行中（キャッシュ再利用）...")
    results_b, _ = run_pattern(
        start              = start,
        end                = end,
        initial_capital    = args.capital,
        buy_threshold      = args.threshold,
        macd_neutral_ratio = args.macd_ratio,
        sma_neutral_pct    = args.sma_pct,
        cached_dfs         = dfs,
    )

    print_report(
        results_a  = results_a,
        results_b  = results_b,
        label_a    = label_a,
        label_b    = label_b,
        start      = start,
        end        = end,
        threshold  = args.threshold,
        macd_ratio = args.macd_ratio,
        sma_pct    = args.sma_pct,
    )


if __name__ == "__main__":
    main()
