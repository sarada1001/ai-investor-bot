#!/usr/bin/env python3
"""
scripts/universe_backtest.py — S&P500/NASDAQ100 Universe 大規模バックテスト

STRONG_BUY_SCORE = 0.60 を維持したまま銘柄プールを 100 銘柄に拡大し、
取引機会の絶対数とトータル利益のスケールを検証する。
ボトルネック分析により、スコアが閾値に届かない主因指標を特定する。

Usage:
    python scripts/universe_backtest.py
    python scripts/universe_backtest.py --start 2026-02-01 --end 2026-05-01
    python scripts/universe_backtest.py --threshold 0.60 --capital 10000
    python scripts/universe_backtest.py --no-bottleneck   # ボトルネック分析スキップ
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

from scripts.run_backtest import (  # noqa: E402
    fetch_ohlcv,
    run_simulation,
    calc_metrics,
    calc_tech_score,
)

_W = 82

# ─────────────────────────────────────────────────────────────
# 100銘柄 Universe（S&P500/NASDAQ100 セクター別・分散）
# ─────────────────────────────────────────────────────────────

UNIVERSE: dict[str, list[str]] = {
    # ── Technology (20) ──────────────────────────────────────
    "Technology": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "META",
        "AMD",  "INTC", "QCOM", "AMAT",  "LRCX",
        "ORCL", "CRM",  "ADBE", "CSCO",  "AVGO",
        "TXN",  "NOW",  "PANW", "PLTR",  "FTNT",
    ],
    # ── Healthcare (15) ──────────────────────────────────────
    "Healthcare": [
        "DXCM", "UNH",  "JNJ",  "ABT",  "LLY",
        "PFE",  "AMGN", "MDT",  "ABBV", "MRK",
        "GILD", "VRTX", "ISRG", "SYK",  "ELV",
    ],
    # ── Industrial / Defense (10) ────────────────────────────
    "Industrial": [
        "NOC", "LMT", "RTX", "GE",  "CAT",
        "BA",  "HON", "UPS", "DE",  "MMM",
    ],
    # ── Financials (12) ──────────────────────────────────────
    "Financials": [
        "JPM", "BAC", "GS",   "V",    "MA",
        "WFC", "C",   "AXP",  "BLK",  "MS",
        "SCHW","COF",
    ],
    # ── Consumer Discretionary (13) ──────────────────────────
    "Cons.Disc": [
        "AMZN", "TSLA", "HD",   "NKE",  "COST",
        "WMT",  "MCD",  "SBUX", "TGT",  "LOW",
        "BKNG", "ABNB", "DG",
    ],
    # ── Consumer Staples (8) ─────────────────────────────────
    "Cons.Staples": [
        "PG",  "KO",  "PEP", "PM",
        "MO",  "CL",  "KHC", "GIS",
    ],
    # ── Energy (6) ───────────────────────────────────────────
    "Energy": [
        "XOM", "CVX", "OXY", "COP", "SLB", "EOG",
    ],
    # ── Communication Services (7) ───────────────────────────
    "Communication": [
        "VZ", "T", "NFLX", "DIS", "CMCSA", "CHTR", "WBD",
    ],
    # ── Utilities / REIT (5) ─────────────────────────────────
    "Utilities": [
        "NEE", "AMT", "DUK", "PLD", "O",
    ],
    # ── Materials (4) ────────────────────────────────────────
    "Materials": [
        "LIN", "APD", "SHW", "FCX",
    ],
}

BASELINE_5 = {
    "label":    "5銘柄ベースライン (AAPL/MSFT/NVDA/NOC/DXCM)",
    "n_trades": 11,
    "win_rate": 45.5,
    "avg_pnl":  0.15,
}

BASELINE_39 = {
    "label":         "39銘柄ベースライン (threshold=0.60, 2026-02〜05)",
    "n_trades":      70,
    "win_rate":      42.9,
    "avg_pnl_pct":   0.335,
    "total_pnl_pct": 23.47,
}


# ─────────────────────────────────────────────────────────────
# Universe バックテスト
# ─────────────────────────────────────────────────────────────

def run_universe_backtest(
    universe: dict[str, list[str]],
    threshold: float,
    start: datetime,
    end: datetime,
    capital: float,
) -> tuple[dict[str, dict], dict[str, pd.DataFrame]]:
    """
    全銘柄のバックテストを実行し、メトリクスと取得済みDFを返す。

    Returns:
        (results, dfs)
        results: ticker → {"metrics": dict, "sector": str}
        dfs:     ticker → DataFrame（ボトルネック分析で再利用）
    """
    results: dict[str, dict]        = {}
    dfs:     dict[str, pd.DataFrame] = {}

    for sector, tickers in universe.items():
        print(f"\n  ■ {sector}")
        for ticker in tickers:
            try:
                df = fetch_ohlcv(ticker, start, end)
            except Exception as e:
                print(f"    [{ticker}] スキップ: {e}")
                continue

            dfs[ticker] = df
            trades, equity_curve = run_simulation(
                ticker          = ticker,
                df              = df,
                sim_start       = start,
                sim_end         = end,
                initial_capital = capital,
                use_critic      = False,
                buy_threshold   = threshold,
            )
            m = calc_metrics(trades, equity_curve, capital)
            results[ticker] = {"metrics": m, "sector": sector}
            print(
                f"    {ticker:<6}  取引 {m['n_trades']:2d}件  "
                f"勝率 {m['win_rate']:5.1f}%  P&L {m['total_pnl_pct']:+.2f}%"
            )

    return results, dfs


# ─────────────────────────────────────────────────────────────
# ボトルネック分析
# ─────────────────────────────────────────────────────────────

def analyze_bottleneck(
    universe: dict[str, list[str]],
    dfs: dict[str, pd.DataFrame],
    threshold: float,
    start: datetime,
    end: datetime,
) -> dict:
    """
    スコア < threshold の日に RSI / MACD / SMA25 のどれが
    負シグナルだったかを集計し、主要ボトルネック指標を特定する。

    技術スコアは離散値 {0.333, 0.567, 0.833} のみを取るため、
    threshold=0.60 では score=0.833（全指標 positive）のみがエントリー条件を満たす。
    miss 日 = score < 0.60 = 少なくとも1指標が negative な日。
    """
    total_days = 0
    miss_days  = 0
    drag: dict[str, int] = {"rsi": 0, "macd": 0, "sma": 0}

    for tickers in universe.values():
        for ticker in tickers:
            df = dfs.get(ticker)
            if df is None:
                continue

            sim_dates = [d for d in df.index if start <= d <= end]
            for date in sim_dates:
                hist = df.loc[df.index <= date, "Close"].squeeze()
                if len(hist) < 30:
                    continue
                score, details = calc_tech_score(hist)
                total_days += 1
                if score < threshold:
                    miss_days += 1
                    if details["rsi_sig"]  < 0:
                        drag["rsi"]  += 1
                    if details["macd_sig"] < 0:
                        drag["macd"] += 1
                    if details["sma_sig"]  < 0:
                        drag["sma"]  += 1

    return {
        "total_days":   total_days,
        "miss_days":    miss_days,
        "hit_days":     total_days - miss_days,
        "hit_rate_pct": round((total_days - miss_days) / total_days * 100, 1) if total_days else 0.0,
        "drag":         drag,
    }


# ─────────────────────────────────────────────────────────────
# レポート出力
# ─────────────────────────────────────────────────────────────

def print_universe_report(
    results:    dict[str, dict],
    universe:   dict[str, list[str]],
    bottleneck: dict,
    threshold:  float,
    start:      datetime,
    end:        datetime,
    capital:    float,
) -> None:
    print()
    print("═" * _W)
    print(f"  Universe 拡張バックテスト  {start.date()} → {end.date()}  閾値 {threshold:.2f}  資金 ${capital:,.0f}")
    print("═" * _W)
    print(
        f"  {'Ticker':<6}  {'セクター':<14}  {'取引':>4}  "
        f"{'勝率':>7}  {'P&L':>9}  {'最大DD':>7}  {'avg勝':>7}  {'avg敗':>7}"
    )
    print("─" * _W)

    total_trades = 0
    total_wins   = 0
    total_pnl    = 0.0

    for sector, tickers in universe.items():
        for ticker in tickers:
            if ticker not in results:
                continue
            m        = results[ticker]["metrics"]
            pnl_sign = "+" if m["total_pnl_pct"] >= 0 else ""
            print(
                f"  {ticker:<6}  {sector:<14}  {m['n_trades']:>4}件  "
                f"{m['win_rate']:>6.1f}%  "
                f"{pnl_sign}{m['total_pnl_pct']:>7.2f}%  "
                f"-{m['max_drawdown_pct']:>5.2f}%  "
                f"+{m['avg_win_pct']:>5.2f}%  "
                f"{m['avg_loss_pct']:>6.2f}%"
            )
            total_trades += m["n_trades"]
            total_wins   += m["n_wins"]
            total_pnl    += m["total_pnl_pct"]

    n                = len(results)
    overall_win_rate = total_wins / total_trades * 100 if total_trades else 0.0
    avg_pnl          = total_pnl / n if n else 0.0
    pnl_sign         = "+" if avg_pnl >= 0 else ""

    print("─" * _W)
    print(
        f"  全 {n} 銘柄  合計取引: {total_trades} 件  "
        f"勝率: {overall_win_rate:.1f}%  平均P&L: {pnl_sign}{avg_pnl:.2f}%"
    )

    # ── ベースライン比較
    n_tickers = sum(len(v) for v in universe.values())
    print()
    print(f"  ■ ベースライン比較（スケール検証）")
    print("─" * _W)
    print(f"  {'':38}  {'取引数':>6}  {'勝率':>7}  {'平均P&L':>9}")

    # 5銘柄ベースライン
    print(
        f"  {BASELINE_5['label']:<38}  {BASELINE_5['n_trades']:>6} 件  "
        f"{BASELINE_5['win_rate']:>6.1f}%  +{BASELINE_5['avg_pnl']:>7.3f}%"
    )
    # 39銘柄ベースライン
    print(
        f"  {BASELINE_39['label']:<38}  {BASELINE_39['n_trades']:>6} 件  "
        f"{BASELINE_39['win_rate']:>6.1f}%  +{BASELINE_39['avg_pnl_pct']:>7.3f}%"
    )
    # 今回の結果
    cur_label = f"{n_tickers}銘柄 Universe（今回）"
    print(
        f"  {cur_label:<38}  {total_trades:>6} 件  "
        f"{overall_win_rate:>6.1f}%  {pnl_sign}{avg_pnl:>7.3f}%"
    )
    print("─" * _W)
    trade_delta = total_trades - BASELINE_39["n_trades"]
    wr_delta    = overall_win_rate - BASELINE_39["win_rate"]
    pnl_delta   = avg_pnl - BASELINE_39["avg_pnl_pct"]
    scale_ratio = total_trades / BASELINE_39["n_trades"] if BASELINE_39["n_trades"] else 0.0
    ticker_ratio = n_tickers / 39
    print(
        f"  {'vs 39銘柄差分':<38}  "
        f"{'+' if trade_delta >= 0 else ''}{trade_delta:>5} 件  "
        f"{'+' if wr_delta >= 0 else ''}{wr_delta:>6.1f} pt  "
        f"{'+' if pnl_delta >= 0 else ''}{pnl_delta:>7.3f} pt"
    )
    print(
        f"  スケール効率: 銘柄数 {ticker_ratio:.2f}x → 取引数 {scale_ratio:.2f}x  "
        f"({'線形スケール達成' if scale_ratio >= ticker_ratio * 0.8 else '線形スケール未達'})"
    )

    # ── ボトルネック分析
    if bottleneck["total_days"] > 0:
        _print_bottleneck_section(bottleneck, threshold)

    print()
    print("  ⚠  このバックテストはテクニカルスコア（RSI/MACD/SMA25）を proxy として使用しています。")
    print("     本番の STRONG_BUY_SCORE は 5 エージェント加重合算スコアであり直接対応しません。")
    print("═" * _W)


def _print_bottleneck_section(bottleneck: dict, threshold: float) -> None:
    miss  = bottleneck["miss_days"]
    total = bottleneck["total_days"]
    hit   = bottleneck["hit_days"]

    print()
    print("  ■ スコアボトルネック分析")
    print("─" * _W)
    print(
        f"  全観測日数: {total} 日  "
        f"閾値到達 ({threshold:.2f}以上): {hit} 日 ({bottleneck['hit_rate_pct']:.1f}%)  "
        f"未到達: {miss} 日"
    )
    print()
    print("  未到達日における各指標の負シグナル頻度:")
    drag = bottleneck["drag"]
    for label, key in [
        ("RSI   (過熱/売られすぎ判定)", "rsi"),
        ("MACD  (ゴールデン/デッドクロス)", "macd"),
        ("SMA25 (トレンド方向)", "sma"),
    ]:
        count = drag[key]
        pct   = count / miss * 100 if miss else 0.0
        bar   = "█" * int(pct / 4)
        print(f"    {label:<32}  {count:5d} 日  {pct:5.1f}%  {bar}")

    worst_key   = max(drag, key=lambda k: drag[k])
    worst_label = {"rsi": "RSI", "macd": "MACD", "sma": "SMA25"}[worst_key]
    worst_pct   = drag[worst_key] / miss * 100 if miss else 0.0

    print()
    print(f"  主ボトルネック: {worst_label}（未到達日の {worst_pct:.1f}% で負シグナル）")
    print()
    _print_weight_suggestion(drag, miss)


def _print_weight_suggestion(drag: dict[str, int], miss_days: int) -> None:
    if miss_days == 0:
        return

    rsi_pct  = drag["rsi"]  / miss_days * 100
    macd_pct = drag["macd"] / miss_days * 100
    sma_pct  = drag["sma"]  / miss_days * 100

    print("  ■ 改善提案（engine/constants.py の Weights 調整候補）")
    print()

    if sma_pct >= 60:
        print("    SMA25 が最大ボトルネック（トレンド追随型指標）")
        print("    SMA は長期トレンド確認に有効ですが、反転初動では信号が遅れます。")
        print("    対策1: _calc_sma25() を EMA20 に変更（skills/technical_calc.py）")
        print("    対策2: スコア算出を均等 1/3 → SMA:1/4、RSI:3/8、MACD:3/8 に変更")
    elif macd_pct >= 60:
        print("    MACD が最大ボトルネック（モメンタム指標）")
        print("    デッドクロス状態が続く横ばい・緩やかな下落局面で多発します。")
        print("    対策1: MACD ヒストグラムに neutral ゾーン（-0.05〜+0.05）を導入")
        print("    対策2: MACD シグナルを binary（±1）→ 3段階（+1/0/-1）に変更")
    elif rsi_pct >= 60:
        print("    RSI が最大ボトルネック（過熱検知指標）")
        print("    RSI 55〜70 ゾーンに -0.3 を返す設計が過剰に保守的な可能性があります。")
        print("    対策: _rsi_to_signal() の 55-70 ゾーンを -0.3 → 0.0 (neutral) に緩和")
    else:
        rsi_r  = round(rsi_pct)
        macd_r = round(macd_pct)
        sma_r  = round(sma_pct)
        print(f"    ボトルネックが分散しています（RSI:{rsi_r}% / MACD:{macd_r}% / SMA:{sma_r}%）。")
        print("    単一指標の改善より Universe をさらに 50〜100 銘柄に拡大する方が")
        print("    絶対取引数の増加に効果的です。")

    print()
    print("    ※ いずれの変更も compare_thresholds.py で再バックテストして検証してください。")


# ─────────────────────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────────────────────

def main() -> None:
    today         = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    default_start = (today - timedelta(days=90)).strftime("%Y-%m-%d")
    default_end   = today.strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser(description="Universe拡張バックテスト + ボトルネック分析")
    parser.add_argument("--start",         default=default_start, help="開始日 YYYY-MM-DD")
    parser.add_argument("--end",           default=default_end,   help="終了日 YYYY-MM-DD")
    parser.add_argument("--threshold",     type=float, default=0.60, help="BUY 閾値（デフォルト: 0.60）")
    parser.add_argument("--capital",       type=float, default=10000, help="初期資金（デフォルト: 10000）")
    parser.add_argument("--no-bottleneck", action="store_true",   help="ボトルネック分析をスキップ")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end   = datetime.strptime(args.end,   "%Y-%m-%d")

    all_tickers = [t for tickers in UNIVERSE.values() for t in tickers]
    print()
    print("═" * _W)
    print("  Universe 拡張バックテスト（proxy: テクニカルスコア）")
    print(f"  期間: {start.date()} → {end.date()}")
    print(f"  銘柄数: {len(all_tickers)} 銘柄  閾値: {args.threshold:.2f}")
    print("═" * _W)

    # Phase 1: バックテスト（DFをキャッシュしてフェーズ2で再利用）
    results, dfs = run_universe_backtest(UNIVERSE, args.threshold, start, end, args.capital)

    # Phase 2: ボトルネック分析（キャッシュ済みDFを使用 → yfinance 追加呼び出しなし）
    if not args.no_bottleneck:
        print("\n  ボトルネック分析中（キャッシュ済みデータを使用）...")
        bottleneck = analyze_bottleneck(UNIVERSE, dfs, args.threshold, start, end)
    else:
        bottleneck = {
            "total_days": 0, "miss_days": 0, "hit_days": 0,
            "hit_rate_pct": 0.0, "drag": {"rsi": 0, "macd": 0, "sma": 0},
        }

    # Phase 3: レポート出力
    print_universe_report(results, UNIVERSE, bottleneck, args.threshold, start, end, args.capital)


if __name__ == "__main__":
    main()
