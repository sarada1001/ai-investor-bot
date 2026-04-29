"""
Skill: portfolio_tracker
Manages persistent portfolio state for RULE-02 and RULE-07 enforcement.

State file: portfolio_state.json (project root)
"""

import json
import datetime
from pathlib import Path

STATE_FILE = Path("portfolio_state.json")
_BUY_COOLDOWN_DAYS = 3
_MAX_DECISIONS_HISTORY = 30


def load() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"holdings": {}, "recent_decisions": [], "last_updated": None}


def save(state: dict) -> None:
    state["last_updated"] = datetime.datetime.now().isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def recent_buy_within_days(state: dict, company: str, days: int = _BUY_COOLDOWN_DAYS) -> bool:
    """Returns True if there was a BUY for this company within the last N calendar days."""
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    for d in state["recent_decisions"]:
        try:
            if (d["company"] == company
                    and d["action"] == "BUY"
                    and datetime.date.fromisoformat(d["date"]) >= cutoff):
                return True
        except (KeyError, ValueError):
            continue
    return False


def total_position_pct(state: dict) -> float:
    return sum(h.get("position_size_pct", 0) for h in state["holdings"].values())


def holding_count(state: dict) -> int:
    return len(state["holdings"])


def get_summary(state: dict) -> dict:
    return {
        "holdings": state["holdings"],
        "holding_count": holding_count(state),
        "total_position_pct": round(total_position_pct(state), 1),
        "recent_decisions_last10": state["recent_decisions"][-10:],
    }


def apply_decision(
    state: dict,
    company: str,
    ticker: str,
    action: str,
    position_size_pct: float,
    current_price: float | None = None,
) -> dict:
    today = datetime.date.today().isoformat()
    state["recent_decisions"].append({
        "date": today,
        "company": company,
        "ticker": ticker,
        "action": action,
        "position_size_pct": position_size_pct,
    })
    state["recent_decisions"] = state["recent_decisions"][-_MAX_DECISIONS_HISTORY:]

    if action == "BUY":
        state["holdings"][company] = {
            "ticker": ticker,
            "entry_date": today,
            "entry_price": current_price,
            "position_size_pct": position_size_pct,
        }
    elif action == "SELL":
        state["holdings"].pop(company, None)

    return state
