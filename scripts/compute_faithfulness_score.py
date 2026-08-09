#!/usr/bin/env python3
"""
scripts/compute_faithfulness_score.py — 説明信頼性スコアの集計

hold_cases.jsonl の primary_reason_agent / contributing_agents と
intervention_results.jsonl の true_cause_agents を突き合わせ、
ManagerAgent の HOLD 説明がどれだけ正確かを定量化する。

使い方:
    python scripts/compute_faithfulness_score.py
    python scripts/compute_faithfulness_score.py \\
        --hold-cases data/research/hold_cases.jsonl \\
        --intervention data/research/intervention_results.jsonl \\
        --output data/research/faithfulness_report.csv

出力指標:
    全体スコア        : 一致事例数 ÷ 全 HOLD 事例数（primary が真の原因に含まれる率）
    エージェント別スコア: 各エージェントが真の原因のとき、説明が正しく名指しできた割合
    Jaccard 係数      : contributing_agents ∩ true_cause_agents の集合一致度
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_PROJECT_ROOT   = Path(__file__).parent.parent
_RESEARCH_DIR   = _PROJECT_ROOT / "data" / "research"
_HOLD_CASES     = _RESEARCH_DIR / "hold_cases.jsonl"
_INTERVENTION   = _RESEARCH_DIR / "intervention_results.jsonl"
_OUTPUT_CSV     = _RESEARCH_DIR / "faithfulness_report.csv"

_CSV_FIELDS = [
    "case_id", "ticker", "date", "hold_type",
    "primary_reason_agent", "contributing_agents",
    "true_cause_agents",
    "primary_match",     # bool: primary が true_cause に含まれるか
    "jaccard",           # float: Jaccard 係数
    "contributing_str", "true_cause_str",
]


def _load_jsonl(path: Path) -> dict[str, dict]:
    """JSONL ファイルを case_id をキーとして読み込む。"""
    records: dict[str, dict] = {}
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                records[r["case_id"]] = r
            except (json.JSONDecodeError, KeyError):
                continue
    return records


def _jaccard(set_a: set, set_b: set) -> float:
    if not set_a and not set_b:
        return 1.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return round(inter / union, 4) if union > 0 else 0.0


def _compute_rows(hold_cases: dict, interventions: dict) -> list[dict]:
    rows: list[dict] = []
    for case_id, hc in hold_cases.items():
        iv = interventions.get(case_id)
        if iv is None:
            continue  # 介入結果がないケースはスキップ

        primary      = hc.get("primary_reason_agent", "")
        contributing = set(hc.get("contributing_agents", []))
        true_causes  = set(iv.get("true_cause_agents", []))

        primary_match = primary in true_causes if true_causes else False
        jac = _jaccard(contributing, true_causes)

        rows.append({
            "case_id":             case_id,
            "ticker":              hc.get("ticker", ""),
            "date":                hc.get("date", ""),
            "hold_type":           hc.get("hold_type", ""),
            "primary_reason_agent":primary,
            "contributing_agents": ",".join(sorted(contributing)),
            "true_cause_agents":   ",".join(sorted(true_causes)),
            "primary_match":       primary_match,
            "jaccard":             jac,
            "contributing_str":    str(sorted(contributing)),
            "true_cause_str":      str(sorted(true_causes)),
        })
    return rows


def _agent_scores(rows: list[dict]) -> dict[str, dict]:
    """エージェント別の説明精度を集計する。"""
    stats: dict[str, dict] = {}
    for row in rows:
        for agent in row["true_cause_agents"].split(","):
            agent = agent.strip()
            if not agent:
                continue
            if agent not in stats:
                stats[agent] = {"total": 0, "correct": 0}
            stats[agent]["total"] += 1
            if row["primary_match"] and row["primary_reason_agent"] == agent:
                stats[agent]["correct"] += 1
    for agent, s in stats.items():
        s["precision"] = round(s["correct"] / s["total"], 4) if s["total"] > 0 else 0.0
    return stats


def _print_summary(rows: list[dict]) -> None:
    total = len(rows)
    if total == 0:
        print("[説明信頼性スコア] 評価可能なケースが 0 件でした。")
        return

    correct_primary = sum(1 for r in rows if r["primary_match"])
    avg_jaccard     = round(sum(r["jaccard"] for r in rows) / total, 4)
    overall_score   = round(correct_primary / total, 4)

    hold_types = {}
    for r in rows:
        ht = r.get("hold_type", "unknown")
        hold_types.setdefault(ht, {"total": 0, "correct": 0})
        hold_types[ht]["total"] += 1
        if r["primary_match"]:
            hold_types[ht]["correct"] += 1

    print("\n" + "=" * 60)
    print(" ECC 説明信頼性スコア (Faithfulness Score)")
    print("=" * 60)
    print(f"  評価ケース数       : {total} 件")
    print(f"  Primary 一致数     : {correct_primary} / {total}")
    print(f"  全体スコア         : {overall_score:.4f}  ({overall_score * 100:.1f}%)")
    print(f"  平均 Jaccard 係数  : {avg_jaccard:.4f}")
    print()

    print("  ── HOLD タイプ別 ──")
    for ht, s in hold_types.items():
        prec = round(s["correct"] / s["total"], 4) if s["total"] > 0 else 0.0
        print(f"  {ht:<12}: {prec:.4f}  ({s['correct']}/{s['total']})")
    print()

    print("  ── エージェント別スコア（Primary として正確に名指しできた率）──")
    agent_stats = _agent_scores(rows)
    for agent, s in sorted(agent_stats.items(), key=lambda x: -x[1]["precision"]):
        print(f"  {agent:<20}: {s['precision']:.4f}  ({s['correct']}/{s['total']})")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ECC 説明信頼性スコア集計",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--hold-cases",   default=str(_HOLD_CASES),   metavar="FILE")
    parser.add_argument("--intervention", default=str(_INTERVENTION),  metavar="FILE")
    parser.add_argument("--output",       default=str(_OUTPUT_CSV),    metavar="FILE")
    args = parser.parse_args()

    hold_cases    = _load_jsonl(Path(args.hold_cases))
    interventions = _load_jsonl(Path(args.intervention))

    if not hold_cases:
        print(f"[ERROR] hold_cases が空または見つかりません: {args.hold_cases}")
        sys.exit(1)
    if not interventions:
        print(f"[ERROR] intervention_results が空または見つかりません: {args.intervention}")
        sys.exit(1)

    rows = _compute_rows(hold_cases, interventions)
    if not rows:
        print("[WARN] マッチする case_id が 0 件でした。hold_cases と intervention の case_id を確認してください。")
        sys.exit(1)

    _print_summary(rows)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n  CSVを保存しました: {output_path}")


if __name__ == "__main__":
    main()
