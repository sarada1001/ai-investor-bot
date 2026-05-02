#!/usr/bin/env python3
"""
scripts/run_ablation_test.py — アブレーション実験スクリプト

各エージェントを一つずつ除外した場合の判断変化を計測し、
data/evaluation/ablation_results.csv に記録する。

使い方:
    python scripts/run_ablation_test.py --ticker AAPL --mock
    python scripts/run_ablation_test.py --ticker AAPL --hybrid
"""

from __future__ import annotations

import argparse
import csv
import datetime
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import run_trade_cycle

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "evaluation" / "ablation_results.csv"

# アブレーション実験パターン
ABLATION_PATTERNS: list[dict] = [
    {"label": "Baseline (全エージェント)",  "exclude": []},
    {"label": "w/o SocialAgent",           "exclude": ["SocialAgent"]},
    {"label": "w/o NewsAgent",             "exclude": ["NewsAgent"]},
    {"label": "w/o TechnicalAgent",        "exclude": ["TechnicalAgent"]},
    {"label": "w/o MacroAgent",            "exclude": ["MacroAgent"]},
    {"label": "w/o FundamentalAgent",      "exclude": ["FundamentalAgent"]},
]

CSV_FIELDS = [
    "run_at", "ticker", "pattern", "excluded_agents",
    "decision", "score",
    "sig_fundamental", "sig_technical", "sig_macro", "sig_news", "sig_social",
]


def _run_pattern(
    ticker: str,
    pattern: dict,
    mock_mode: bool,
    hybrid_mode: bool,
) -> dict:
    print(f"\n  [AblationTest] {pattern['label']} 実行中...")
    try:
        result = run_trade_cycle(
            ticker=ticker,
            mock_mode=mock_mode,
            hybrid_mode=hybrid_mode,
            excluded_agents=pattern["exclude"],
            dry_run=True,
        )
    except Exception as e:
        print(f"  [AblationTest] エラー: {e}")
        result = {"decision": "ERROR", "score": 0.0, "signals": {}}

    sigs = result.get("signals", {})
    return {
        "run_at":         datetime.datetime.now().isoformat(timespec="seconds"),
        "ticker":         ticker,
        "pattern":        pattern["label"],
        "excluded_agents": ",".join(pattern["exclude"]) if pattern["exclude"] else "none",
        "decision":       result.get("decision", "N/A"),
        "score":          result.get("score", 0.0),
        "sig_fundamental": sigs.get("fundamental", 0.0),
        "sig_technical":   sigs.get("technical",   0.0),
        "sig_macro":       sigs.get("macro",       0.0),
        "sig_news":        sigs.get("news",        0.0),
        "sig_social":      sigs.get("social",      0.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="ECC アブレーション実験")
    parser.add_argument("--ticker",  default="AAPL",  help="分析銘柄")
    parser.add_argument("--mock",    action="store_true", help="モックモードで実行（APIトークン消費ゼロ）")
    parser.add_argument("--hybrid",  action="store_true", help="ハイブリッドモードで実行")
    args = parser.parse_args()

    print("=" * 64)
    print(" ECC アブレーション実験")
    print(f" 銘柄: {args.ticker}  パターン数: {len(ABLATION_PATTERNS)}")
    print(f" モード: {'mock' if args.mock else 'hybrid' if args.hybrid else 'real'}")
    print("=" * 64)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    file_exists = OUTPUT_CSV.exists()

    rows = []
    for pattern in ABLATION_PATTERNS:
        row = _run_pattern(args.ticker, pattern, args.mock, args.hybrid)
        rows.append(row)

        # 結果サマリー表示
        decision_icon = "🚀" if row["decision"] == "STRONG BUY" else "⏸"
        print(
            f"  {decision_icon} [{row['pattern']:30s}]  "
            f"決定: {row['decision']:12s}  スコア: {row['score']:+.4f}"
        )

    # CSV に追記
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists or OUTPUT_CSV.stat().st_size == 0:
            writer.writeheader()
        writer.writerows(rows)

    print(f"\n  [AblationTest] 結果を保存しました: {OUTPUT_CSV}")

    # ─── 差分レポート ───────────────────────────────────────────
    if len(rows) >= 2:
        baseline = rows[0]
        print("\n  ── 判断変化サマリー（Baseline との比較）──")
        for row in rows[1:]:
            score_diff = row["score"] - baseline["score"]
            changed = row["decision"] != baseline["decision"]
            change_flag = " ← 判断変化!" if changed else ""
            print(
                f"  {row['pattern']:30s}  "
                f"スコア差: {score_diff:+.4f}  "
                f"判断: {baseline['decision']} → {row['decision']}{change_flag}"
            )

    print(f"\n  完了。出力先: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
