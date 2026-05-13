"""tests/test_exit_agent_fallback.py — ExitAgent ルールベースフォールバック (M-2) テスト"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.exit_agent import ExitAgent, _rule_based_thesis_break


# ──────────────────────────────────────────────
# _rule_based_thesis_break
# ──────────────────────────────────────────────

class TestRuleBasedThesisBreak:
    def test_returns_false_for_empty_news(self):
        assert _rule_based_thesis_break("") is False

    def test_returns_false_for_all_positive_sentiment(self):
        news = (
            "- [positive] Strong earnings beat: surpassed expectations\n"
            "- [positive] Guidance raised: company sees growth ahead"
        )
        assert _rule_based_thesis_break(news) is False

    def test_returns_false_for_mixed_sentiment_with_keyword(self):
        news = (
            "- [negative] Earnings miss: below expectations\n"
            "- [positive] Strong product pipeline: new launches expected"
        )
        assert _rule_based_thesis_break(news) is False

    def test_returns_true_for_all_negative_with_break_keyword(self):
        news = (
            "- [negative] Earnings miss: revenue well below expectations\n"
            "- [negative] Guidance cut: company lowers full-year outlook"
        )
        assert _rule_based_thesis_break(news) is True

    def test_returns_false_for_all_negative_without_break_keyword(self):
        news = (
            "- [negative] Market uncertainty weighs on sentiment\n"
            "- [negative] Analyst cautious on valuation"
        )
        assert _rule_based_thesis_break(news) is False

    def test_returns_false_for_no_parseable_lines(self):
        assert _rule_based_thesis_break("no articles here") is False

    def test_japanese_break_keyword(self):
        news = (
            "- [negative] 下方修正: 売上高が計画を大幅に下回る見込み\n"
            "- [negative] 業績悪化: 需要低迷が継続"
        )
        assert _rule_based_thesis_break(news) is True

    def test_single_negative_article_with_fraud_keyword(self):
        news = "- [negative] Investigation launched into accounting fraud at company"
        assert _rule_based_thesis_break(news) is True

    def test_neutral_sentiment_not_treated_as_negative(self):
        news = (
            "- [neutral] Earnings miss: below expectations by 2%\n"
            "- [negative] Guidance cut: lowered full-year forecast"
        )
        # neutral is not negative, so "all negative" fails → False
        assert _rule_based_thesis_break(news) is False


# ──────────────────────────────────────────────
# ExitAgent._is_thesis_broken fallback behaviour
# ──────────────────────────────────────────────

class TestIsThesisBrokenFallback:
    def _make_agent(self) -> ExitAgent:
        bbs  = MagicMock()
        llm  = MagicMock()
        ob   = MagicMock()
        agent = ExitAgent.__new__(ExitAgent)
        agent.bbs   = bbs
        agent._llm  = llm
        agent._ob_log = ob
        return agent

    def test_returns_false_when_news_empty(self):
        agent = self._make_agent()
        assert agent._is_thesis_broken("AAPL", "good thesis", "") is False
        agent._llm.invoke.assert_not_called()

    def test_llm_yes_returns_true(self):
        agent = self._make_agent()
        agent._llm.invoke.return_value.content = "YES"
        news = "- [negative] Bad earnings miss"
        assert agent._is_thesis_broken("AAPL", "expected good earnings", news) is True

    def test_llm_no_returns_false(self):
        agent = self._make_agent()
        agent._llm.invoke.return_value.content = "NO"
        news = "- [negative] Minor analyst caution"
        assert agent._is_thesis_broken("AAPL", "bullish thesis", news) is False

    def test_llm_failure_falls_back_to_rules_true(self):
        """When LLM raises, rule-based fallback runs and returns True."""
        agent = self._make_agent()
        agent._llm.invoke.side_effect = RuntimeError("Gemini unavailable")
        news = (
            "- [negative] Earnings miss: missed expectations badly\n"
            "- [negative] Guidance cut: lower than expected"
        )
        assert agent._is_thesis_broken("AAPL", "expected beat", news) is True

    def test_llm_failure_falls_back_to_rules_false(self):
        """When LLM raises but no break keywords, rule-based fallback returns False."""
        agent = self._make_agent()
        agent._llm.invoke.side_effect = RuntimeError("Gemini unavailable")
        news = (
            "- [negative] Market uncertainty persists\n"
            "- [negative] Caution on sector valuation"
        )
        assert agent._is_thesis_broken("AAPL", "bullish thesis", news) is False

    def test_llm_failure_with_mixed_sentiment_returns_false(self):
        """Rule-based fallback does not fire on mixed sentiment even with keywords."""
        agent = self._make_agent()
        agent._llm.invoke.side_effect = RuntimeError("Gemini unavailable")
        news = (
            "- [negative] Earnings miss: fell short\n"
            "- [positive] Strong forward guidance raised"
        )
        assert agent._is_thesis_broken("AAPL", "bullish thesis", news) is False
