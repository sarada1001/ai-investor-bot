#!/usr/bin/env python3
"""
scripts/run_intervention_experiment.py — 介入実験スクリプト

hold_cases.jsonl に記録された HOLD 事例ごとに、
エージェントを 1 つずつ除外・反転させて再実行し、
「どのエージェントが HOLD の真の原因か」を特定する。

使い方:
    python scripts/run_intervention_experiment.py
    python scripts/run_intervention_experiment.py --input data/research/hold_cases.jsonl
    python scripts/run_intervention_experiment.py --case-id AAPL_20250301_143022

介入の種類:
    除外 (exclusion) : 対象エージェントのシグナルを 0.0 にして再計算
    反転 (flip)      : 対象エージェントのスコアを符号反転して再計算

出力: data/research/intervention_results.jsonl
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.constants import WEIGHTS as _DEFAULT_WEIGHTS
from engine.research_helpers import (
    _compute_decision_from_signals,
    _compute_decision_research,
    _compute_gate_from_snapshot,
    _flip_bbs_snapshot,
    _WEIGHT_KEY_TO_AGENT,
    _ALL_WEIGHT_KEYS,
)

_PROJECT_ROOT  = Path(__file__).parent.parent
_RESEARCH_DIR  = _PROJECT_ROOT / "data" / "research"
_INPUT_FILE    = _RESEARCH_DIR / "hold_cases.jsonl"
_OUTPUT_FILE   = _RESEARCH_DIR / "intervention_results.jsonl"

# 介入対象エージェント（weight_key 順）
_INTERVENTION_AGENTS: list[str] = [
    "technical", "news", "macro", "social", "fundamental",
]


# ── signals キー直接利用フォールバック ──────────────────────────────────────

def _bbs_has_scores(bbs_snapshot: dict) -> bool:
    """bbs_snapshot の少なくとも1エントリに _score フィールドがあるか判定。"""
    return any(
        isinstance(v, dict) and "_score" in v
        for v in bbs_snapshot.values()
    )


def _signals_exclusion(
    signals: dict[str, float],
    weights: dict[str, float],
    weight_key: str,
    threshold: float = 0.6,
) -> str:
    """対象エージェントの signals スコアを 0 にして加重スコアを再計算し、閾値を超えるか判定。"""
    score = sum(
        (0.0 if k == weight_key else float(signals.get(k, 0.0))) * float(weights.get(k, 0.0))
        for k in weights
    )
    return "STRONG BUY" if score > threshold else "HOLD"


def _signals_flip(
    signals: dict[str, float],
    weights: dict[str, float],
    weight_key: str,
    threshold: float = 0.6,
) -> str:
    """対象エージェントの signals スコアの符号を反転して加重スコアを再計算し、閾値を超えるか判定。"""
    flipped = {k: (-float(v) if k == weight_key else float(v)) for k, v in signals.items()}
    score = sum(flipped.get(k, 0.0) * float(weights.get(k, 0.0)) for k in weights)
    return "STRONG BUY" if score > threshold else "HOLD"


def _run_exclusion(
    bbs_snapshot: dict,
    ticker: str,
    weights: dict[str, float],
    hold_type: str,
    weight_key: str,
) -> str:
    """
    指定エージェントを除外して判断を再計算し、決定を返す。

    gate hold: Gate チェック再実行。通過時は "GATE_PASS" を返す。
    manager hold: 研究用緩和条件（fundamental必須なし）で判定。
    """
    if hold_type == "gate":
        gate = _compute_gate_from_snapshot(bbs_snapshot, excluded_keys=[weight_key])
        if not gate["skip_fundamental"]:
            return "GATE_PASS"
        return "HOLD"
    else:
        result = _compute_decision_research(
            bbs_snapshot=bbs_snapshot,
            ticker=ticker,
            weights=weights,
            excluded_keys=[weight_key],
        )
        return result["decision"]


def _run_flip(
    bbs_snapshot: dict,
    ticker: str,
    weights: dict[str, float],
    hold_type: str,
    weight_key: str,
) -> str:
    """
    指定エージェントのスコアを反転させて判断を再計算し、決定を返す。

    manager hold: 研究用緩和条件（fundamental必須なし）で判定。
    """
    flipped = _flip_bbs_snapshot(bbs_snapshot, weight_key)

    if hold_type == "gate":
        gate = _compute_gate_from_snapshot(flipped)
        if not gate["skip_fundamental"]:
            return "GATE_PASS"
        return "HOLD"
    else:
        result = _compute_decision_research(
            bbs_snapshot=flipped,
            ticker=ticker,
            weights=weights,
        )
        return result["decision"]


def _process_case(case: dict) -> dict:
    """
    1 つの HOLD 事例に対して全介入パターンを実行し、結果 dict を返す。

    manager HOLD かつ bbs_snapshot に _score/_signal フィールドがない場合は、
    signals キーの値を直接使って加重スコアを再計算し 0.6 超えを判定する
    シンプルフォールバックモードで動作する。
    """
    ticker       = case["ticker"]
    hold_type    = case.get("hold_type", "manager")
    bbs_snapshot = case.get("bbs_snapshot", {})
    signals      = case.get("signals", {})
    weights      = case.get("weights", {}) or _DEFAULT_WEIGHTS
    threshold    = float(case.get("threshold", 0.6))

    # フォールバック条件: manager HOLD かつ bbs_snapshot に _score がない
    use_signals_fallback = (
        hold_type == "manager"
        and not _bbs_has_scores(bbs_snapshot)
    )

    exclusion_results: dict[str, str] = {}
    flip_results:      dict[str, str] = {}

    for wkey in _INTERVENTION_AGENTS:
        agent_name = _WEIGHT_KEY_TO_AGENT.get(wkey, wkey)

        # Gate hold かつ fundamental: Gate はFundamental実行前で終わるため除外介入は意味なし
        if hold_type == "gate" and wkey == "fundamental":
            exclusion_results[agent_name] = "N/A_GATE_HOLD"
            flip_results[agent_name]      = "N/A_GATE_HOLD"
            continue

        if use_signals_fallback:
            exclusion_results[agent_name] = _signals_exclusion(
                signals, weights, wkey, threshold,
            )
            flip_results[agent_name] = _signals_flip(
                signals, weights, wkey, threshold,
            )
        else:
            exclusion_results[agent_name] = _run_exclusion(
                bbs_snapshot, ticker, weights, hold_type, wkey,
            )
            flip_results[agent_name] = _run_flip(
                bbs_snapshot, ticker, weights, hold_type, wkey,
            )

    # 真の原因エージェント = 除外 OR 反転で HOLD 以外になったエージェント
    _changed_decisions = {"STRONG BUY", "GATE_PASS"}
    true_cause_agents = sorted({
        agent for agent in exclusion_results
        if exclusion_results.get(agent) in _changed_decisions
        or flip_results.get(agent)      in _changed_decisions
    })

    # manager HOLDは研究用緩和条件（fundamental必須なし）を適用
    research_relaxed = hold_type == "manager" and not use_signals_fallback

    return {
        "case_id":                    case["case_id"],
        "ticker":                     ticker,
        "date":                       case.get("date", ""),
        "hold_type":                  hold_type,
        "original_score":             case.get("score", 0.0),
        "mode":                       "signals-fallback" if use_signals_fallback else "bbs-snapshot",
        "research_relaxed_condition": research_relaxed,
        "exclusion_results":          exclusion_results,
        "flip_results":               flip_results,
        "true_cause_agents":          true_cause_agents,
        "analyzed_at":                datetime.datetime.now().isoformat(timespec="seconds"),
    }


def _load_hold_cases(
    input_file: Path,
    case_id_filter: str | None = None,
) -> list[dict]:
    if not input_file.exists():
        print(f"[ERROR] 入力ファイルが見つかりません: {input_file}")
        sys.exit(1)

    cases: list[dict] = []
    with input_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[WARN] JSON パースエラー（行スキップ）: {e}")
                continue
            if case_id_filter and c.get("case_id") != case_id_filter:
                continue
            cases.append(c)

    if not cases:
        print(f"[WARN] 対象ケースが 0 件でした。")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ECC 介入実験スクリプト",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input", default=str(_INPUT_FILE), metavar="FILE",
        help="入力 hold_cases.jsonl ファイルパス",
    )
    parser.add_argument(
        "--output", default=str(_OUTPUT_FILE), metavar="FILE",
        help="出力 intervention_results.jsonl ファイルパス",
    )
    parser.add_argument(
        "--case-id", default=None, metavar="ID",
        help="特定の case_id のみ処理（省略時は全件）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="結果をファイルに書き込まず標準出力のみ",
    )
    args = parser.parse_args()

    input_file  = Path(args.input)
    output_file = Path(args.output)

    cases = _load_hold_cases(input_file, case_id_filter=args.case_id)
    print(f"\n[介入実験] 対象ケース: {len(cases)} 件")
    print(f"  介入エージェント: {_INTERVENTION_AGENTS}")
    print(f"  介入種別: 除外 (exclusion) × 反転 (flip)")
    print(f"  1 ケースあたり最大介入数: {len(_INTERVENTION_AGENTS)} × 2 = {len(_INTERVENTION_AGENTS) * 2}")
    print()

    results: list[dict] = []
    for i, case in enumerate(cases, 1):
        snap = case.get("bbs_snapshot", {})
        mode = "signals-fallback" if (
            case.get("hold_type") == "manager" and not _bbs_has_scores(snap)
        ) else "bbs-snapshot"
        print(f"  [{i}/{len(cases)}] {case['case_id']}  (hold_type={case.get('hold_type', '?')}, mode={mode})")
        result = _process_case(case)
        results.append(result)

        # 結果を表示
        cause_str = ", ".join(result["true_cause_agents"]) or "（特定不可）"
        print(f"          → 真の原因: {cause_str}")
        for agent in _INTERVENTION_AGENTS:
            aname = _WEIGHT_KEY_TO_AGENT.get(agent, agent)
            ex = result["exclusion_results"].get(aname, "-")
            fl = result["flip_results"].get(aname, "-")
            changed_ex = ex not in ("HOLD", "N/A_GATE_HOLD", "-")
            changed_fl = fl not in ("HOLD", "N/A_GATE_HOLD", "-")
            flag = "  ← 原因!" if (changed_ex or changed_fl) else ""
            print(f"          {aname:<18}: 除外={ex:<14} 反転={fl:<14}{flag}")
        print()

    if not args.dry_run and results:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open("a", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[完了] {len(results)} 件を保存しました: {output_file}")
    elif args.dry_run:
        print("[dry-run] ファイル書き込みをスキップしました。")
    else:
        print("[WARN] 出力する結果がありません。")


if __name__ == "__main__":
    main()
