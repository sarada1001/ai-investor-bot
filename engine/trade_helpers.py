"""engine/trade_helpers.py — トレードサイクル用ユーティリティ関数"""

from __future__ import annotations

import re
from pathlib import Path

from engine.constants import WEIGHTS

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _agent_to_weight_key(agent_name: str) -> str | None:
    """エージェント名をウェイトキーに変換する。"""
    mapping = {
        "technicalagent":   "technical",
        "technical":        "technical",
        "newsagent":        "news",
        "news":             "news",
        "macroagent":       "macro",
        "macro":            "macro",
        "socialagent":      "social",
        "social":           "social",
        "fundamentalagent": "fundamental",
        "fundamental":      "fundamental",
        "liquidityagent":   "liquidity",
        "liquidity":        "liquidity",
    }
    return mapping.get(agent_name.lower())


def _compute_effective_weights(excluded_keys: list[str]) -> dict[str, float]:
    """除外エージェントのウェイトを残存エージェントに比例配分して再正規化する。"""
    excluded_weight = sum(WEIGHTS[k] for k in excluded_keys if k in WEIGHTS)
    if excluded_weight >= 1.0:
        return {k: 0.0 for k in WEIGHTS}
    scale = 1.0 / (1.0 - excluded_weight)
    return {k: (0.0 if k in excluded_keys else round(WEIGHTS[k] * scale, 6)) for k in WEIGHTS}


def _fetch_past_lessons(ticker: str, max_rules: int = 5) -> str:
    """
    Obsidian ログから過去の失敗・成功教訓を抽出し、CriticAgent 用テキストを返す。
    対象: outcome=CLOSED かつ action=SELL のログ（負の損益を優先）
    """
    logs_dir = _PROJECT_ROOT / "data" / "knowledge_base" / "obsidian_logs"
    if not logs_dir.exists():
        return "（過去ログなし）"

    lessons: list[str] = []
    for log_file in sorted(logs_dir.glob(f"*_{ticker.upper()}_SELL*.md"), reverse=True):
        try:
            text = log_file.read_text(encoding="utf-8")
            if "outcome: CLOSED" not in text:
                continue
            pl_match = re.search(r"profit_loss:\s*([^\n]+)", text)
            pl_str   = pl_match.group(1).strip() if pl_match else "?"
            rule_start = text.find("## 4.")
            if rule_start != -1:
                rule_end = text.find("\n## 5.", rule_start)
                rule_sec = text[rule_start: rule_end if rule_end != -1 else rule_start + 400]
                lessons.append(f"損益 {pl_str}: {rule_sec.strip()}")
        except Exception:
            continue

    if not lessons:
        return "（対象銘柄の過去教訓なし）"
    return "\n\n".join(lessons[:max_rules])


def _fetch_wiki_context(ticker: str, max_trades: int = 5) -> str:
    """
    Wiki ティッカーページから直近 SELL 実績と関連コンセプトを抽出し、
    ManagerAgent の rationale 生成に注入するコンテキストを返す。
    """
    ticker_file = _PROJECT_ROOT / "data" / "knowledge_base" / "wiki" / "tickers" / f"{ticker.upper()}.md"
    if not ticker_file.exists():
        return ""

    text = ticker_file.read_text(encoding="utf-8")

    assessment_m = re.search(r"^assessment:\s*(\w+)", text, re.MULTILINE)
    score_m      = re.search(r"^assessment_score:\s*([\d.+\-]+)", text, re.MULTILINE)
    assessment   = assessment_m.group(1) if assessment_m else "UNKNOWN"
    score_str    = score_m.group(1)      if score_m      else "?"

    trade_match = re.search(r"## トレード履歴\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    recent_sells: list[str] = []
    if trade_match:
        seen_log: set[str] = set()
        for row in trade_match.group(1).splitlines():
            cols  = [c.strip() for c in row.split("|")]
            cells = [c for i, c in enumerate(cols) if 0 < i < len(cols) - 1]
            if len(cells) < 6 or cells[1] != "SELL":
                continue
            entry_date, result, raw_log_key = cells[0], cells[4], cells[5]
            log_key = re.sub(r"\|[^\]]*", "", raw_log_key)
            if log_key in seen_log:
                continue
            seen_log.add(log_key)
            recent_sells.append(f"{entry_date}: P&L={result}")
            if len(recent_sells) >= max_trades:
                break

    concepts_match = re.search(r"## 関連コンセプト\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    unique_concepts: list[str] = []
    if concepts_match:
        seen_cpt: set[str] = set()
        for line in concepts_match.group(1).splitlines():
            m = re.search(r"\[\[concepts/([^\]|]+)", line)
            if m:
                cname = m.group(1)
                if cname not in seen_cpt:
                    seen_cpt.add(cname)
                    unique_concepts.append(cname)

    if not recent_sells and not unique_concepts:
        return ""

    parts = [f"【{ticker} 過去実績】直近評価: {assessment} (score={score_str})"]
    if recent_sells:
        parts.append("直近SELL実績:\n" + "\n".join(f"  {s}" for s in recent_sells))
    if unique_concepts:
        parts.append("関連コンセプト: " + ", ".join(unique_concepts[:8]))
    return "\n".join(parts)
