#!/usr/bin/env python3
"""
scripts/build_analytics_parquet.py — training_data.jsonl を3枚のParquetに変換するETL

出力:
  data/analytics/fact_decisions.parquet     — 1行=1サイクル
  data/analytics/fact_agent_signals.parquet — 1行=1エージェント (ロング形式)
  data/analytics/fact_faithfulness.parquet  — 1行=1非truncatedレコード（説明信頼性スコア）

Usage:
    python scripts/build_analytics_parquet.py
    python scripts/build_analytics_parquet.py data/training/training_data_prod.jsonl
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SCRIPT_DIR    = Path(__file__).parent
PROJECT_ROOT  = SCRIPT_DIR.parent
TRAINING_PATH = PROJECT_ROOT / "data" / "training" / "training_data.jsonl"
ANALYTICS_DIR = PROJECT_ROOT / "data" / "analytics"

# signals キー → エージェント名の対応
SIGNAL_KEY_TO_AGENT: dict[str, str] = {
    "fundamental": "FundamentalAgent",
    "technical":   "TechnicalAgent",
    "macro":       "MacroAgent",
    "news":        "NewsAgent",
    "social":      "SocialAgent",
    "liquidity":   "LiquidityAgent",
}

# ─────────────────────────────────────────────────────────────
# 説明信頼性スコア用定数
# ─────────────────────────────────────────────────────────────

_FAITHFULNESS_PATTERNS: dict[str, re.Pattern] = {
    "MacroAgent":       re.compile(r"マクロ.{0,80}?([+-]0\.\d+)",             re.DOTALL),
    "TechnicalAgent":   re.compile(r"テクニカル.{0,80}?([+-]0\.\d+)",         re.DOTALL),
    "FundamentalAgent": re.compile(r"ファンダメンタル.{0,80}?([+-]0\.\d+)",   re.DOTALL),
    "NewsAgent":        re.compile(r"ニュース.{0,80}?([+-]0\.\d+)",           re.DOTALL),
    "SocialAgent":      re.compile(r"(?:ソーシャル|SNS).{0,80}?([+-]0\.\d+)", re.DOTALL),
    "LiquidityAgent":   re.compile(r"(?:リクイディティ|流動性).{0,80}?([+-]0\.\d+)", re.DOTALL),
}

_AGENT_TO_SIGNAL_KEY: dict[str, str] = {
    "MacroAgent":       "macro",
    "TechnicalAgent":   "technical",
    "FundamentalAgent": "fundamental",
    "NewsAgent":        "news",
    "SocialAgent":      "social",
    "LiquidityAgent":   "liquidity",
}

_SCORE_TOLERANCE = 0.05
_N_AGENTS        = 6


# ─────────────────────────────────────────────────────────────
# ローダー
# ─────────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("行 %d: JSON解析失敗 — %s", i, e)
    logger.info("読み込み完了: %d 行", len(records))
    return records


# ─────────────────────────────────────────────────────────────
# fact_decisions 構築
# ─────────────────────────────────────────────────────────────

def _build_fact_decisions(records: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    for r in records:
        cot = r.get("manager_chain_of_thought") or {}
        mo  = r.get("manager_output") or {}
        _raw_rationale = cot.get("rationale") or ""

        rows.append({
            "record_id":           r.get("record_id"),
            "session_id":          r.get("session_id"),
            "date":                r.get("date"),
            "created_at":          r.get("created_at"),
            "ticker":              r.get("ticker"),
            "mock_mode":           r.get("mock_mode"),
            "hybrid_mode":         r.get("hybrid_mode"),           # 古いレコードは null
            "decision":            mo.get("decision"),
            "score":               mo.get("score"),
            "threshold":           cot.get("threshold"),
            "macro_forced_hold":   cot.get("macro_forced_hold"),
            "social_hype_penalty": cot.get("social_hype_penalty"),
            "social_hype_score":   cot.get("social_hype_score"),
            "gate_skipped":        bool(cot.get("gate_skipped") or False),
            "gate_reason":         cot.get("gate_reason"),         # null の場合もある
            "outcome_label":       r.get("outcome_label"),
            "is_closed":           r.get("outcome_label") is not None,
            "rationale":           _raw_rationale or None,
            "rationale_truncated": len(_raw_rationale) == 200,
        })

    df = pd.DataFrame(rows)
    df["date"]       = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    return df


# ─────────────────────────────────────────────────────────────
# fact_agent_signals 構築
# ─────────────────────────────────────────────────────────────

def _extract_trend(
    key: str,
    inputs: dict,
    signal_score: float | None,
    signal_reasons: dict,
) -> tuple[str | None, str | None]:
    """
    (trend, trend_reason) をフォールバック付きで抽出する。

    優先順位:
      trend:        inputs.{key}_analysis.trend
                    → news: avg_sentiment_score から派生
                    → social: sentiment フィールド
                    → その他: signal_score から派生
      trend_reason: inputs.{key}_analysis.trend_reason
                    → signal_reasons.{key}
    """
    analysis = inputs.get(f"{key}_analysis", {}) or {}
    trend        = analysis.get("trend")
    trend_reason = analysis.get("trend_reason") or signal_reasons.get(key)

    if trend is None:
        if key == "news":
            score = analysis.get("avg_sentiment_score")
            if score is not None:
                if score > 0.3:
                    trend = "positive"
                elif score < -0.3:
                    trend = "negative"
                else:
                    trend = "neutral"
            elif signal_score is not None:
                trend = "positive" if signal_score > 0 else ("negative" if signal_score < 0 else "neutral")

        elif key == "social":
            raw = analysis.get("sentiment", "")
            if raw:
                trend = raw.lower()  # "POSITIVE" → "positive"

        elif signal_score is not None:
            trend = "positive" if signal_score > 0 else ("negative" if signal_score < 0 else "neutral")

    return trend, trend_reason


def _build_fact_agent_signals(records: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    for r in records:
        record_id = r.get("record_id")
        cot       = r.get("manager_chain_of_thought") or {}
        inputs    = r.get("inputs") or {}
        signals        = cot.get("signals") or {}
        signal_reasons = cot.get("signal_reasons") or {}
        gate_skipped   = bool(cot.get("gate_skipped") or False)

        for key, score in signals.items():
            agent_name = SIGNAL_KEY_TO_AGENT.get(key, key)
            trend, trend_reason = _extract_trend(key, inputs, score, signal_reasons)

            # gate_skipped=True かつ当該 analysis が空 {} の場合にフラグを立てる
            analysis = inputs.get(f"{key}_analysis", {}) or {}
            is_skipped = gate_skipped and not analysis

            rows.append({
                "record_id":    record_id,
                "agent_name":   agent_name,
                "signal_key":   key,
                "signal_score": score,
                "trend":        trend,
                "trend_reason": trend_reason,
                "is_skipped":   is_skipped,
            })

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
# fact_faithfulness 構築
# ─────────────────────────────────────────────────────────────

def _score_rationale(
    rationale: str,
    signals: dict,
) -> tuple[float, int, str]:
    """(faithfulness_score, matched_count, details_json) を返す。分母は常に 6。"""
    matched = 0
    details = []
    for agent, pat in _FAITHFULNESS_PATTERNS.items():
        key    = _AGENT_TO_SIGNAL_KEY[agent]
        actual = signals.get(key)
        m      = pat.search(rationale)
        extracted = float(m.group(1)) if m else None
        is_match  = (
            extracted is not None
            and actual is not None
            and abs(extracted - actual) <= _SCORE_TOLERANCE
        )
        if is_match:
            matched += 1
        details.append({
            "agent":     agent,
            "extracted": extracted,
            "actual":    actual,
            "matched":   is_match,
        })
    score = round(matched / _N_AGENTS, 4)
    return score, matched, json.dumps(details, ensure_ascii=False)


def _build_fact_faithfulness(records: list[dict]) -> pd.DataFrame:
    """非truncated・非mock レコードについて説明信頼性スコアを計算する。"""
    rows: list[dict] = []
    for r in records:
        if r.get("mock_mode"):
            continue
        cot      = r.get("manager_chain_of_thought") or {}
        raw_rat  = cot.get("rationale") or ""
        if len(raw_rat) == 200:
            continue  # truncated レコードは除外
        signals  = cot.get("signals") or {}
        score, matched, details = _score_rationale(raw_rat, signals)
        rows.append({
            "record_id":          r.get("record_id"),
            "ticker":             r.get("ticker"),
            "date":               r.get("date"),
            "faithfulness_score": score,
            "matched_count":      matched,
            "total_agents":       _N_AGENTS,
            "rationale_preview":  raw_rat[:120],
            "details_json":       details,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    return df


# ─────────────────────────────────────────────────────────────
# Parquet 書き出し
# ─────────────────────────────────────────────────────────────

def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, path, compression="snappy")
    logger.info("書き出し完了: %s  (%d 行 × %d 列)", path.name, len(df), len(df.columns))


# ─────────────────────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────────────────────

def main() -> None:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else TRAINING_PATH
    if not input_path.exists():
        logger.error("training_data.jsonl が見つかりません: %s", input_path)
        sys.exit(1)

    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)

    records = _load_jsonl(input_path)

    logger.info("fact_decisions を構築中...")
    df_decisions = _build_fact_decisions(records)
    _write_parquet(df_decisions, ANALYTICS_DIR / "fact_decisions.parquet")

    logger.info("fact_agent_signals を構築中...")
    df_signals = _build_fact_agent_signals(records)
    _write_parquet(df_signals, ANALYTICS_DIR / "fact_agent_signals.parquet")

    logger.info("fact_faithfulness を構築中...")
    df_faith = _build_fact_faithfulness(records)
    _write_parquet(df_faith, ANALYTICS_DIR / "fact_faithfulness.parquet")

    # スキーマ表示
    print("\n─── fact_decisions スキーマ ───")
    print(df_decisions.dtypes.to_string())
    print(f"\n─── fact_decisions サンプル 5行 ───")
    print(df_decisions.head(5).to_string(index=False))

    print("\n─── fact_agent_signals スキーマ ───")
    print(df_signals.dtypes.to_string())
    print(f"\n─── fact_agent_signals サンプル 5行 ───")
    print(df_signals.head(5).to_string(index=False))

    print("\n─── fact_faithfulness スキーマ ───")
    print(df_faith.dtypes.to_string())
    print(f"\n─── fact_faithfulness サンプル 5行 ───")
    print(df_faith[["record_id","ticker","faithfulness_score","matched_count"]].head(5).to_string(index=False))

    print(f"\nfact_decisions      : {len(df_decisions)} 行")
    print(f"fact_agent_signals  : {len(df_signals)} 行")
    print(f"fact_faithfulness   : {len(df_faith)} 行")
    print(f"rationale_truncated : {df_decisions['rationale_truncated'].sum()} 件")
    print(f"is_closed=True      : {df_decisions['is_closed'].sum()} 件")


if __name__ == "__main__":
    main()
