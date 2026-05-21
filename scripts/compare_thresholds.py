#!/usr/bin/env python3
"""
scripts/compare_thresholds.py — 閾値比較バックテスト

STRONG_BUY_SCORE の変更（0.60 → 0.50）が
パフォーマンスに与える影響を定量的に比較する。

【注意】
このスクリプトはテクニカルスコア（RSI / MACD / SMA25）を proxy として使用します。
本番の STRONG_BUY_SCORE は 5エージェントの加重合算スコアであり、
テクニカルスコアとは直接対応しません。
方向性（閾値を下げると取引増・勝率変化）の傾向確認にご利用ください。

Usage:
    python scripts/compare_thresholds.py
    python scripts/compare_thresholds.py --start 2026-02-01 --end 2026-05-01
    python scripts/compare_thresholds.py --tickers AAPL MSFT NVDA --capital 5000
"""

from __future__ import annotations

import argparse
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

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
)

_W = 76

DEFAULT_TICKERS    = ["AAPL", "MSFT", "NVDA", "NOC", "DXCM"]
DEFAULT_THRESHOLDS = [0.60, 0.50]


def run_comparison(
    tickers: list[str],
    thresholds: list[float],
    start: datetime,
    end: datetime,
    capital: float,
) -> dict[str, dict[float, dict]]:
    results: dict[str, dict[float, dict]] = {}

    for ticker in tickers:
        print(f"\n  [{ticker}] データ取得中...")
        try:
            df = fetch_ohlcv(ticker, start, end)
        except Exception as e:
            print(f"  [{ticker}] スキップ（データ取得失敗: {e}）")
            continue

        results[ticker] = {}
        for threshold in thresholds:
            trades, equity_curve = run_simulation(
                ticker          = ticker,
                df              = df,
                sim_start       = start,
                sim_end         = end,
                initial_capital = capital,
                use_critic      = False,
                buy_threshold   = threshold,
            )
            metrics = calc_metrics(trades, equity_curve, capital)
            results[ticker][threshold] = metrics
            print(
                f"    閾値 {threshold:.2f} → "
                f"{metrics['n_trades']:2d}件  "
                f"勝率 {metrics['win_rate']:5.1f}%  "
                f"P&L {metrics['total_pnl_pct']:+.2f}%"
            )

    return results


def print_comparison_table(
    results: dict[str, dict[float, dict]],
    thresholds: list[float],
    start: datetime,
    end: datetime,
    capital: float,
) -> None:
    print()
    print("═" * _W)
    print(f"  閾値比較バックテスト  {start.date()} → {end.date()}  初期資金 ${capital:,.0f}")
    print("═" * _W)
    print(f"  {'Ticker':<6} {'閾値':>5}  {'取引数':>6}  {'勝率':>7}  {'合計P&L':>10}  {'最大DD':>8}  {'平均利益':>8}  {'平均損失':>8}")
    print("─" * _W)

    totals: dict[float, dict] = {t: {"n_trades": 0, "wins": 0, "pnl": 0.0} for t in thresholds}

    for ticker, thresh_results in results.items():
        for i, threshold in enumerate(thresholds):
            m = thresh_results.get(threshold)
            if m is None:
                continue
            prefix = f"  {ticker:<6}" if i == 0 else f"  {'':6}"
            pnl_sign = "+" if m["total_pnl_pct"] >= 0 else ""
            print(
                f"{prefix} {threshold:>5.2f}  "
                f"{m['n_trades']:>6}件  "
                f"{m['win_rate']:>6.1f}%  "
                f"{pnl_sign}{m['total_pnl_pct']:>8.2f}%  "
                f"-{m['max_drawdown_pct']:>6.2f}%  "
                f"+{m['avg_win_pct']:>6.2f}%  "
                f"{m['avg_loss_pct']:>7.2f}%"
            )
            totals[threshold]["n_trades"] += m["n_trades"]
            totals[threshold]["wins"]     += m["n_wins"]
            totals[threshold]["pnl"]      += m["total_pnl_pct"]

        print("─" * _W)

    n_tickers = len(results)
    print()
    print("  ■ 全銘柄合計")
    for threshold in thresholds:
        t = totals[threshold]
        win_rate = t["wins"] / t["n_trades"] * 100 if t["n_trades"] else 0.0
        avg_pnl  = t["pnl"] / n_tickers if n_tickers else 0.0
        pnl_sign = "+" if avg_pnl >= 0 else ""
        print(
            f"    閾値 {threshold:.2f}  取引計 {t['n_trades']:3d}件  "
            f"勝率 {win_rate:5.1f}%  "
            f"平均P&L {pnl_sign}{avg_pnl:.2f}%"
        )

    print()
    print("═" * _W)
    _print_verdict(results, thresholds)
    print()
    print("  ⚠  このバックテストはテクニカルスコア（RSI/MACD/SMA25）を proxy として使用しています。")
    print("     本番の STRONG_BUY_SCORE は 5エージェント加重合算スコアであり直接対応しません。")
    print("═" * _W)


def _print_verdict(
    results: dict[str, dict[float, dict]],
    thresholds: list[float],
) -> None:
    if len(thresholds) != 2:
        return

    t_high, t_low = thresholds[0], thresholds[1]
    trade_diff_total = 0
    pnl_diff_total   = 0.0
    win_diff_total   = 0.0
    n = 0

    for thresh_results in results.values():
        m_high = thresh_results.get(t_high)
        m_low  = thresh_results.get(t_low)
        if m_high is None or m_low is None:
            continue
        trade_diff_total += m_low["n_trades"] - m_high["n_trades"]
        pnl_diff_total   += m_low["total_pnl_pct"] - m_high["total_pnl_pct"]
        win_diff_total   += m_low["win_rate"] - m_high["win_rate"]
        n += 1

    if n == 0:
        return

    avg_trade_diff = trade_diff_total / n
    avg_pnl_diff   = pnl_diff_total / n
    avg_win_diff   = win_diff_total / n

    print(f"  ■ データに基づく判定（{t_high:.2f} → {t_low:.2f} への変更効果）")
    print()
    trade_arrow = "↑" if avg_trade_diff > 0 else ("↓" if avg_trade_diff < 0 else "→")
    win_arrow   = "↑" if avg_win_diff > 0 else ("↓" if avg_win_diff < 0 else "→")
    pnl_arrow   = "↑" if avg_pnl_diff > 0 else ("↓" if avg_pnl_diff < 0 else "→")
    print(f"    取引数の変化   : {trade_arrow} 平均 {avg_trade_diff:+.1f}件/銘柄")
    print(f"    勝率の変化     : {win_arrow} 平均 {avg_win_diff:+.1f}pt")
    print(f"    P&Lの変化      : {pnl_arrow} 平均 {avg_pnl_diff:+.2f}%pt")
    print()

    if avg_pnl_diff > 1.0 and avg_win_diff >= 0:
        verdict = "✅  利益を最大化する良い変更  — P&Lが改善し勝率も維持されています。"
    elif avg_pnl_diff > 0 and avg_win_diff < -5:
        verdict = "⚠️  要注意  — P&Lは微増ですが勝率が低下しています。取引数増加に依存した改善です。"
    elif avg_pnl_diff < -1.0 or avg_win_diff < -10:
        verdict = "🔴  ノイズを拾いすぎる危険な変更  — 勝率・P&Lともに悪化しています。0.60 に戻すことを推奨します。"
    elif abs(avg_pnl_diff) <= 1.0 and abs(avg_win_diff) <= 3:
        verdict = "➡️  中立  — 有意な差はありません。0.50 のままでも大きなリスクはありませんが、実運用で継続モニタリングを推奨します。"
    else:
        verdict = "⚠️  混合シグナル  — 銘柄によって結果が分かれています。実運用で慎重にモニタリングしてください。"

    print(f"    {verdict}")


def main() -> None:
    today         = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    default_start = (today - timedelta(days=90)).strftime("%Y-%m-%d")
    default_end   = today.strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser(description="閾値比較バックテスト")
    parser.add_argument("--tickers",    nargs="+", default=DEFAULT_TICKERS,    help="検証銘柄リスト")
    parser.add_argument("--thresholds", nargs="+", type=float, default=DEFAULT_THRESHOLDS, help="比較する閾値リスト")
    parser.add_argument("--start",      default=default_start, help="開始日 YYYY-MM-DD")
    parser.add_argument("--end",        default=default_end,   help="終了日 YYYY-MM-DD")
    parser.add_argument("--capital",    type=float, default=10000, help="初期資金（デフォルト: 10000）")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end   = datetime.strptime(args.end,   "%Y-%m-%d")

    print()
    print("═" * _W)
    print("  閾値比較バックテスト（proxy: テクニカルスコア）")
    print(f"  期間: {start.date()} → {end.date()}")
    print(f"  銘柄: {', '.join(t.upper() for t in args.tickers)}")
    print(f"  比較閾値: {' vs '.join(str(t) for t in args.thresholds)}")
    print("═" * _W)

    results = run_comparison(
        tickers    = [t.upper() for t in args.tickers],
        thresholds = args.thresholds,
        start      = start,
        end        = end,
        capital    = args.capital,
    )

    print_comparison_table(results, args.thresholds, start, end, args.capital)


if __name__ == "__main__":
    main()
