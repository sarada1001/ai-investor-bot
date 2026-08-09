"""scripts/backfill_hold_cases.py

training_data.jsonl の既存 HOLD ケースから hold_cases.jsonl を生成する。

実行:
    python scripts/backfill_hold_cases.py [--dry-run] [--out PATH]

オプション:
    --dry-run    ファイルへの書き込みを行わず、統計のみ表示する
    --out PATH   出力先 jsonl のパス（デフォルト: data/research/hold_cases.jsonl）
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.research_helpers import (
    _identify_hold_causes,
    _numeric_to_signal_str,
)

_TRAINING_FILE = _PROJECT_ROOT / "data" / "training" / "training_data.jsonl"
_DEFAULT_OUT   = _PROJECT_ROOT / "data" / "research" / "hold_cases.jsonl"


# ── スナップショット再構築 ────────────────────────────────────────────────────

def _reconstruct_snapshot(inputs: dict, cot: dict) -> dict:
    """
    training_data の inputs + manager_chain_of_thought から
    hold_cases.jsonl 互換の bbs_snapshot を再構築する。

    inputs には technical/news/macro/social の生データが含まれる。
    fundamental/liquidity は旧データには含まれないため、
    score のみを持つ最小 dict で補完する。
    """
    sigs: dict[str, float] = cot.get("signals", {})

    def _score(key: str) -> float:
        return float(sigs.get(key, 0.0))

    # ── TechnicalAgent ──
    tech = dict(inputs.get("technical_analysis") or {})
    tech["_score"]  = _score("technical")
    tech["_signal"] = tech.get("trend", "neutral")

    # ── NewsAgent（記事は最大3件） ──
    news = dict(inputs.get("news_analysis") or {})
    news["articles"] = (news.get("articles") or [])[:3]
    news_sc = _score("news")
    news["_score"]  = news_sc
    news["_signal"] = _numeric_to_signal_str(news_sc)

    # ── MacroAgent ──
    macro = dict(inputs.get("macro_analysis") or {})
    macro["_score"]  = _score("macro")
    macro["_signal"] = macro.get("trend", "neutral")

    # ── SocialAgent ──
    social = dict(inputs.get("social_analysis") or {})
    soc_sc = _score("social")
    social["_score"]  = soc_sc
    social["_signal"] = social.get("sentiment", _numeric_to_signal_str(soc_sc).upper())

    # ── FundamentalAgent（旧データには生情報なし → score のみ） ──
    fa_sc = _score("fundamental")
    fa = {
        "_score":       fa_sc,
        "_signal":      _numeric_to_signal_str(fa_sc),
        "_backfilled":  True,
    }
    fa_raw = inputs.get("fundamental_analysis")
    if fa_raw:
        fa = {**dict(fa_raw), **fa}

    # ── LiquidityAgent（旧データには存在しない → neutral で補完） ──
    liq_sc = _score("liquidity")
    liq = {
        "_score":      liq_sc,
        "_signal":     "neutral",
        "_backfilled": True,
    }
    liq_raw = inputs.get("liquidity_analysis")
    if liq_raw:
        liq = {**dict(liq_raw), **liq}

    return {
        "technical_analysis":   tech,
        "news_analysis":        news,
        "macro_analysis":       macro,
        "social_analysis":      social,
        "fundamental_analysis": fa,
        "liquidity_analysis":   liq,
    }


# ── 1件のレコードを hold_case dict に変換 ────────────────────────────────────

def _to_hold_case(record: dict) -> dict:
    cot = record.get("manager_chain_of_thought") or {}
    if isinstance(cot, str):
        try:
            cot = json.loads(cot)
        except json.JSONDecodeError:
            cot = {}

    mo     = record.get("manager_output") or {}
    inputs = record.get("inputs") or {}
    sigs   = cot.get("signals", {})
    wts    = cot.get("weights", {})

    hold_type      = "gate" if cot.get("gate_skipped") else "manager"
    macro_hold     = bool(cot.get("macro_forced_hold") or cot.get("macro_brake"))
    bbs_snapshot   = _reconstruct_snapshot(inputs, cot)

    causes = _identify_hold_causes(
        sigs=sigs,
        weights=wts,
        macro_forced_hold=macro_hold,
    )

    return {
        "case_id":              record.get("record_id", ""),
        "ticker":               record.get("ticker", ""),
        "date":                 record.get("date", ""),
        "session_id":           record.get("session_id", "unknown"),
        "hold_type":            hold_type,
        "decision":             mo.get("decision", "HOLD"),
        "score":                mo.get("score", 0.0),
        "threshold":            mo.get("threshold", cot.get("threshold", 0.6)),
        "macro_forced_hold":    macro_hold,
        "signals":              sigs,
        "weights":              wts,
        "primary_reason_agent": causes["primary"],
        "contributing_agents":  causes["contributing"],
        "explanation":          cot.get("rationale", cot.get("gate_reason", "")),
        "gate_reason":          cot.get("gate_reason"),
        "bbs_snapshot":         bbs_snapshot,
        "_backfill":            True,
    }


# ── メイン ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="書き込みを行わず統計のみ表示")
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="出力先 jsonl パス")
    args = parser.parse_args()

    if not _TRAINING_FILE.exists():
        print(f"[ERROR] 入力ファイルが見つかりません: {_TRAINING_FILE}", file=sys.stderr)
        sys.exit(1)

    # 既存 case_id を読み込んで重複スキップ
    existing_ids: set[str] = set()
    if args.out.exists():
        with args.out.open(encoding="utf-8") as f:
            for line in f:
                try:
                    existing_ids.add(json.loads(line).get("case_id", ""))
                except json.JSONDecodeError:
                    pass
        print(f"[INFO] 既存 hold_cases.jsonl: {len(existing_ids)} 件を読み込み済み（重複スキップ）")

    cases: list[dict] = []
    skipped_dup = 0
    gate_count = manager_count = 0

    with _TRAINING_FILE.open(encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            mo = record.get("manager_output") or {}
            if "HOLD" not in str(mo.get("decision", "")).upper():
                continue

            rid = record.get("record_id", "")
            if rid in existing_ids:
                skipped_dup += 1
                continue

            case = _to_hold_case(record)
            cases.append(case)
            if case["hold_type"] == "gate":
                gate_count += 1
            else:
                manager_count += 1

    print(f"[INFO] 変換: gate={gate_count}, manager={manager_count}, 重複スキップ={skipped_dup}")
    print(f"[INFO] 新規追加対象: {len(cases)} 件")

    if args.dry_run:
        if cases:
            print("\n[DRY-RUN] サンプル出力（1件目）:")
            sample = dict(cases[0])
            sample["bbs_snapshot"] = {k: {"_score": v.get("_score"), "_signal": v.get("_signal")}
                                       for k, v in sample["bbs_snapshot"].items()}
            print(json.dumps(sample, indent=2, ensure_ascii=False))
        print("[DRY-RUN] ファイルへの書き込みはスキップされました。")
        return

    if not cases:
        print("[INFO] 追加対象が0件のため終了。")
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    print(f"[OK] {len(cases)} 件を {args.out} に追記しました。")


if __name__ == "__main__":
    main()
