"""engine/evaluation_history.py — エージェント評価履歴の追記ユーティリティ

audit_agent.py と run_agent_exam.py の両方から呼び出される共有ヘルパー。
agent_status.json の変更を data/evaluation/agent_status_history.jsonl に
ロング形式（エージェント1件 = 1行）で追記する。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH  = _PROJECT_ROOT / "data" / "evaluation" / "agent_status_history.jsonl"

# engine/constants.py の WEIGHTS と同値（循環インポート回避のため直接定義）
_BASE_WEIGHTS: dict[str, float] = {
    "fundamental": 0.35,
    "technical":   0.20,
    "macro":       0.15,
    "news":        0.10,
    "social":      0.10,
    "liquidity":   0.10,
}

# agents/audit_agent.py の AGENT_WEIGHT_MAP と同値
_AGENT_WEIGHT_KEY: dict[str, str] = {
    "TechnicalAgent":   "technical",
    "NewsAgent":        "news",
    "MacroAgent":       "macro",
    "SocialAgent":      "social",
    "FundamentalAgent": "fundamental",
}


def _compute_effective_weights(new_status: dict[str, dict]) -> dict[str, Optional[float]]:
    """
    SUSPENDED エージェントを 0.0 にして残存エージェントを再正規化した
    effective_weight を返す。ManagerAgent など WEIGHTS 未定義は None。
    """
    suspended_keys: set[str] = set()
    for agent_name, info in new_status.items():
        if info.get("status") == "SUSPENDED":
            key = _AGENT_WEIGHT_KEY.get(agent_name)
            if key:
                suspended_keys.add(key)

    suspended_weight = sum(_BASE_WEIGHTS.get(k, 0.0) for k in suspended_keys)
    remaining        = 1.0 - suspended_weight
    scale            = (1.0 / remaining) if remaining > 0.0 else 0.0

    result: dict[str, Optional[float]] = {}
    for agent_name in new_status:
        key = _AGENT_WEIGHT_KEY.get(agent_name)
        if key is None:
            result[agent_name] = None  # ManagerAgent 等
        elif key in suspended_keys:
            result[agent_name] = 0.0
        else:
            result[agent_name] = round(_BASE_WEIGHTS[key] * scale, 6)

    return result


def append_agent_status_history(
    prev_status: dict[str, dict],
    new_status:  dict[str, dict],
    source:      str,
) -> None:
    """
    評価結果を agent_status_history.jsonl に追記する。

    Args:
        prev_status: 書き込み前の agent_status.json の内容（初回は {}）
        new_status:  書き込み後の agent_status.json の内容
        source:      "audit_agent" または "run_agent_exam"
    """
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp        = datetime.now().isoformat()
        effective_weights = _compute_effective_weights(new_status)

        lines: list[str] = []
        for agent_name, new_info in new_status.items():
            prev_info   = prev_status.get(agent_name, {})
            prev_status_val = prev_info.get("status", "UNKNOWN") if prev_info else "UNKNOWN"

            record = {
                "timestamp":         timestamp,
                "source":            source,
                "agent_name":        agent_name,
                "prev_status":       prev_status_val,
                "new_status":        new_info.get("status", "UNKNOWN"),
                "win_rate":          new_info.get("win_rate"),
                "total_trades":      new_info.get("total_trades"),
                "winning_trades":    new_info.get("winning_trades"),
                "losing_trades":     new_info.get("losing_trades"),
                "effective_weight":  effective_weights.get(agent_name),
                "suspension_reason": new_info.get("suspension_reason", ""),
            }
            lines.append(json.dumps(record, ensure_ascii=False))

        with HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        logger.info(
            "agent_status_history.jsonl に %d 行追記 (source=%s)",
            len(lines), source,
        )
    except Exception as exc:
        logger.warning("evaluation history 追記失敗（既存ロジックには影響なし）: %s", exc)
