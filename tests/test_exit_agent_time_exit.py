"""tests/test_exit_agent_time_exit.py — ExitAgent TIME_EXIT（最大保有日数）テスト"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.exit_agent import ExitAgent, _business_days_held


def _business_days_ago(n: int) -> str:
    """n 営業日前（土日除外）の日付を ISO 文字列で返す。"""
    d = date.today()
    remaining = n
    while remaining > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            remaining -= 1
    return d.isoformat()


def _make_agent(current_price: float) -> ExitAgent:
    """yfinance / LLM を排した ExitAgent インスタンスを組み立てる。"""
    agent = ExitAgent.__new__(ExitAgent)
    agent.bbs     = MagicMock()
    agent._llm    = MagicMock()
    agent._ob_log = MagicMock()
    agent._fetch_price = MagicMock(return_value=current_price)  # type: ignore[method-assign]
    return agent


def _make_position(entry_date: str, entry_price: float = 100.0) -> dict:
    return {
        "ticker":          "AAPL",
        "entry_date":      entry_date,
        "entry_price":     entry_price,
        "shares":          10,
        "target_price":    None,
        "stop_loss_price": None,
        "buy_log_file":    "",
        "thesis":          "テクニカルブレイクアウト",
    }


# ──────────────────────────────────────────────
# _business_days_held
# ──────────────────────────────────────────────

class TestBusinessDaysHeld:
    def test_counts_weekdays_only(self):
        # 2026-01-05(月) → 2026-01-19(月) は 10 営業日
        assert _business_days_held("2026-01-05", today=date(2026, 1, 19)) == 10

    def test_same_day_is_zero(self):
        assert _business_days_held("2026-01-05", today=date(2026, 1, 5)) == 0

    def test_returns_none_for_empty_string(self):
        assert _business_days_held("", today=date(2026, 1, 19)) is None

    def test_returns_none_for_unparseable_date(self):
        assert _business_days_held("not-a-date", today=date(2026, 1, 19)) is None

    def test_returns_none_for_future_date(self):
        assert _business_days_held("2026-02-01", today=date(2026, 1, 19)) is None

    def test_accepts_iso_datetime_prefix(self):
        assert _business_days_held("2026-01-05T09:30:00", today=date(2026, 1, 19)) == 10


# ──────────────────────────────────────────────
# ExitAgent._evaluate — TIME_EXIT 分岐
# ──────────────────────────────────────────────

class TestEvaluateTimeExit:
    @patch("agents.exit_agent.MAX_HOLD_DAYS", 0)
    def test_disabled_when_max_hold_days_is_zero(self):
        """MAX_HOLD_DAYS=0（本番初期値）では TIME_EXIT が発火しない。"""
        agent = _make_agent(current_price=101.0)
        pos   = _make_position(_business_days_ago(30))

        decision = agent._evaluate(pos, mock_mode=True)

        assert decision["action"] == "HOLD"
        assert decision["exit_type"] == "CONTINUE"

    @patch("agents.exit_agent.MAX_HOLD_DAYS", 10)
    def test_sells_when_hold_period_exceeded(self):
        agent = _make_agent(current_price=103.21)
        pos   = _make_position(_business_days_ago(12))

        decision = agent._evaluate(pos, mock_mode=True)

        assert decision["action"] == "SELL"
        assert decision["exit_type"] == "MAX_HOLD"
        assert "最大保有日数 10 営業日" in decision["reason"]

    @patch("agents.exit_agent.MAX_HOLD_DAYS", 10)
    def test_holds_when_within_hold_period(self):
        agent = _make_agent(current_price=101.0)
        pos   = _make_position(_business_days_ago(5))

        decision = agent._evaluate(pos, mock_mode=True)

        assert decision["action"] == "HOLD"
        assert decision["exit_type"] == "CONTINUE"

    @patch("agents.exit_agent.MAX_HOLD_DAYS", 10)
    def test_stop_loss_takes_priority_over_time_exit(self):
        """保有日数超過とストップロス到達が同時なら STOP_LOSS を優先する。"""
        agent = _make_agent(current_price=88.0)
        pos   = _make_position(_business_days_ago(12))
        pos["stop_loss_price"] = 90.0

        decision = agent._evaluate(pos, mock_mode=True)

        assert decision["action"] == "SELL"
        assert decision["exit_type"] == "STOP_LOSS"

    @patch("agents.exit_agent.MAX_HOLD_DAYS", 10)
    def test_take_profit_takes_priority_over_time_exit(self):
        agent = _make_agent(current_price=120.0)
        pos   = _make_position(_business_days_ago(12))
        pos["target_price"] = 115.0

        decision = agent._evaluate(pos, mock_mode=True)

        assert decision["action"] == "SELL"
        assert decision["exit_type"] == "TAKE_PROFIT"

    @patch("agents.exit_agent.MAX_HOLD_DAYS", 10)
    def test_empty_entry_date_holds_without_raising(self):
        agent = _make_agent(current_price=101.0)
        pos   = _make_position("")

        decision = agent._evaluate(pos, mock_mode=True)

        assert decision["action"] == "HOLD"
        assert decision["exit_type"] == "CONTINUE"

    @patch("agents.exit_agent.MAX_HOLD_DAYS", 10)
    def test_missing_entry_date_key_holds_without_raising(self):
        agent = _make_agent(current_price=101.0)
        pos   = _make_position("")
        del pos["entry_date"]

        decision = agent._evaluate(pos, mock_mode=True)

        assert decision["action"] == "HOLD"
        assert decision["exit_type"] == "CONTINUE"

    @patch("agents.exit_agent.MAX_HOLD_DAYS", 10)
    def test_future_entry_date_holds_without_raising(self):
        agent = _make_agent(current_price=101.0)
        future = (date.today() + timedelta(days=30)).isoformat()
        pos    = _make_position(future)

        decision = agent._evaluate(pos, mock_mode=True)

        assert decision["action"] == "HOLD"
        assert decision["exit_type"] == "CONTINUE"

    @patch("agents.exit_agent.MAX_HOLD_DAYS", 10)
    def test_unparseable_entry_date_holds_without_raising(self):
        agent = _make_agent(current_price=101.0)
        pos   = _make_position("2026-13-45")

        decision = agent._evaluate(pos, mock_mode=True)

        assert decision["action"] == "HOLD"
        assert decision["exit_type"] == "CONTINUE"

    @patch("agents.exit_agent.MAX_HOLD_DAYS", 10)
    def test_price_unavailable_takes_priority_over_time_exit(self):
        agent = _make_agent(current_price=0.0)
        pos   = _make_position(_business_days_ago(12))

        decision = agent._evaluate(pos, mock_mode=True)

        assert decision["action"] == "HOLD"
        assert decision["exit_type"] == "PRICE_UNAVAILABLE"

    @patch("agents.exit_agent.MAX_HOLD_DAYS", 10)
    def test_time_exit_does_not_invoke_llm(self):
        """時間切れ確定ポジションでは thesis 判定の LLM を呼ばない。"""
        agent = _make_agent(current_price=101.0)
        pos   = _make_position(_business_days_ago(12))

        decision = agent._evaluate(pos, mock_mode=False)

        assert decision["exit_type"] == "MAX_HOLD"
        agent._llm.invoke.assert_not_called()


# ──────────────────────────────────────────────
# _derive_rule
# ──────────────────────────────────────────────

class TestDeriveRuleMaxHold:
    def test_max_hold_rule_text(self):
        from agents.exit_agent import _derive_rule

        rule = _derive_rule("MAX_HOLD", 3.21)
        assert "最大保有日数" in rule
        assert "+3.21%" in rule
