#!/usr/bin/env python3
"""
check_time_exit_impact.py — TIME_EXIT 有効化の影響を事前確認する（読み取り専用）

`engine.constants.MAX_HOLD_DAYS` を 0 以外にして本番 ExitAgent の TIME_EXIT を
有効化した場合、現在の保有ポジションのうちどれが「即座に売られるか」を一覧表示する。

このスクリプトは一切の書き込み・発注を行わない:
  - data/portfolio.json は読むだけ（更新しない）
  - Obsidian ログを書かない
  - Alpaca 発注を行わない
  - LLM を呼ばない
  - yfinance の価格取得のみネットワークアクセスする

使い方:
    python scripts/check_time_exit_impact.py
    python scripts/check_time_exit_impact.py --max-hold-days 15
    python scripts/check_time_exit_impact.py --no-price   # オフライン（価格取得なし）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.exit_agent import _business_days_held  # noqa: E402
from engine.constants import BACKTEST_MAX_HOLD_DAYS, MAX_HOLD_DAYS  # noqa: E402

PORTFOLIO_PATH = PROJECT_ROOT / "data" / "portfolio.json"

_W = 78  # 表示幅


def load_positions() -> list[dict]:
    """portfolio.json の positions を読み取り専用で返す。"""
    if not PORTFOLIO_PATH.exists():
        print(f"  portfolio.json が見つかりません: {PORTFOLIO_PATH}")
        return []
    try:
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            return json.load(f).get("positions", [])
    except (OSError, json.JSONDecodeError) as e:
        print(f"  portfolio.json の読み込みに失敗しました: {e}")
        return []


def fetch_price(ticker: str) -> float:
    """現在価格を取得する。失敗時は 0.0（含み損益は N/A 表示）。"""
    try:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:  # noqa: BLE001 — 表示用途なので握って続行
        print(f"  [warn] {ticker} 価格取得エラー: {e}")
    return 0.0


def build_rows(positions: list[dict], max_hold_days: int, with_price: bool) -> list[dict]:
    """各ポジションの経過営業日・含み損益・TIME_EXIT 判定を組み立てる。"""
    rows: list[dict] = []
    for pos in positions:
        ticker      = pos.get("ticker", "?")
        entry_date  = pos.get("entry_date", "")
        entry_price = float(pos.get("entry_price") or 0)

        held = _business_days_held(entry_date)
        current = fetch_price(ticker) if with_price else 0.0
        pnl_pct = (
            (current - entry_price) / entry_price * 100.0
            if current > 0 and entry_price > 0
            else None
        )

        rows.append({
            "ticker":      ticker,
            "entry_date":  entry_date or "(未設定)",
            "held_days":   held,
            "current":     current,
            "pnl_pct":     pnl_pct,
            "would_exit":  held is not None and held >= max_hold_days,
        })
    return rows


def print_table(rows: list[dict], max_hold_days: int) -> None:
    print(f"  {'ティッカー':<10} {'購入日':<12} {'経過営業日':>10} "
          f"{'現在値':>10} {'含み損益':>10}  判定")
    print("  " + "-" * (_W - 2))

    for r in rows:
        held    = "N/A" if r["held_days"] is None else str(r["held_days"])
        current = f"${r['current']:.2f}" if r["current"] > 0 else "N/A"
        pnl     = f"{r['pnl_pct']:+.2f}%" if r["pnl_pct"] is not None else "N/A"

        if r["held_days"] is None:
            verdict = "判定不能（entry_date 不正/未来日）→ HOLD"
        elif r["would_exit"]:
            verdict = f"→ TIME_EXIT で即売却（{held} >= {max_hold_days}）"
        else:
            verdict = f"継続保有（あと {max_hold_days - r['held_days']} 営業日）"

        print(f"  {r['ticker']:<10} {r['entry_date']:<12} {held:>10} "
              f"{current:>10} {pnl:>10}  {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TIME_EXIT 有効化時の影響を確認する（読み取り専用・発注なし）"
    )
    parser.add_argument(
        "--max-hold-days",
        type=int,
        default=BACKTEST_MAX_HOLD_DAYS,
        help=f"想定する最大保有日数・営業日（デフォルト: {BACKTEST_MAX_HOLD_DAYS}）",
    )
    parser.add_argument(
        "--no-price",
        action="store_true",
        help="yfinance の価格取得を行わない（オフライン確認用）",
    )
    args = parser.parse_args()

    print("=" * _W)
    print("  TIME_EXIT 影響確認（読み取り専用 — 発注・ファイル書き込みは一切行いません）")
    print("=" * _W)
    print(f"  基準日            : {date.today().isoformat()}")
    print(f"  想定 MAX_HOLD_DAYS: {args.max_hold_days} 営業日")
    print(f"  本番の現在設定    : engine.constants.MAX_HOLD_DAYS = {MAX_HOLD_DAYS} "
          f"({'有効' if MAX_HOLD_DAYS else '無効 — TIME_EXIT は発火しない'})")
    print(f"  portfolio.json    : {PORTFOLIO_PATH}")
    print()

    positions = load_positions()
    if not positions:
        print("  保有ポジションなし — 影響はありません。")
        print("=" * _W)
        return

    rows = build_rows(positions, args.max_hold_days, with_price=not args.no_price)
    print_table(rows, args.max_hold_days)

    to_exit = [r for r in rows if r["would_exit"]]
    unknown = [r for r in rows if r["held_days"] is None]

    print()
    print("=" * _W)
    print(f"  保有ポジション数              : {len(rows)} 件")
    print(f"  TIME_EXIT で即売却されるもの  : {len(to_exit)} 件"
          + (f"  ({', '.join(r['ticker'] for r in to_exit)})" if to_exit else ""))
    if unknown:
        print(f"  entry_date 判定不能           : {len(unknown)} 件"
              f"  ({', '.join(r['ticker'] for r in unknown)}) — HOLD 継続")

    realized = [r["pnl_pct"] for r in to_exit if r["pnl_pct"] is not None]
    if realized:
        print(f"  即売却分の合計含み損益（単純和）: {sum(realized):+.2f}%")
    print("=" * _W)


if __name__ == "__main__":
    main()
