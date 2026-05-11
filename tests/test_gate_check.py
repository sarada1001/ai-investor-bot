"""tests/test_gate_check.py — _gate_check / MacroAgent SUSPENDED 時の Gate ロジック"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_bbs(tech_trend: str = "positive", news_trend: str = "positive",
              macro_trend: str = "positive", macro_suspended: bool = False) -> MagicMock:
    news_data = {"articles": [{"ticker": "AAPL", "sentiment": news_trend}]}
    macro_data = {"trend": macro_trend}
    if macro_suspended:
        macro_data = {"trend": "neutral", "suspended": True,
                      "trend_reason": "MacroAgent SUSPENDED (shadow mode) — ウェイト=0"}

    def _read(key: str):
        return {
            "technical_analysis": {"trend": tech_trend},
            "news_analysis":       news_data,
            "macro_analysis":      macro_data,
        }.get(key, {})

    bbs = MagicMock()
    bbs.read.side_effect = _read
    return bbs


class TestGateCheck:
    def _run(self, bbs, ticker: str = "AAPL") -> dict:
        from engine.agent_wrappers import _gate_check
        return _gate_check(bbs, ticker)

    def test_all_positive_passes(self):
        bbs = _make_bbs("positive", "positive", "positive")
        result = self._run(bbs)
        assert not result["skip_fundamental"]
        assert not result["macro_brake"]

    def test_macro_negative_triggers_brake(self):
        bbs = _make_bbs("positive", "positive", "negative")
        result = self._run(bbs)
        assert result["macro_brake"]
        assert result["skip_fundamental"]

    def test_macro_suspended_does_not_brake(self):
        """SUSPENDED の MacroAgent が negative を shadow 実行しても Gate はブレーキしない"""
        bbs = _make_bbs("positive", "positive", "negative", macro_suspended=True)
        result = self._run(bbs)
        assert not result["macro_brake"], "SUSPENDED MacroAgent でブレーキが発動してはいけない"
        assert not result["skip_fundamental"], "SUSPENDED MacroAgent で Gate がブロックしてはいけない"
        assert result["macro_is_suspended"]
        assert result["macro_signal"] == 0.0

    def test_macro_suspended_does_not_affect_signals_flat_logic(self):
        """SUSPENDED MacroAgent でも Tech/News が両方 neutral なら Gate はブロックする"""
        bbs = _make_bbs("neutral", "neutral", "negative", macro_suspended=True)
        result = self._run(bbs)
        assert not result["macro_brake"]
        assert result["skip_fundamental"], "Tech/News 両 neutral なら SUSPENDED でも Gate ブロック"

    def test_tech_neutral_news_neutral_skips(self):
        bbs = _make_bbs("neutral", "neutral", "positive")
        result = self._run(bbs)
        assert not result["macro_brake"]
        assert result["skip_fundamental"]

    def test_tech_positive_news_neutral_passes(self):
        bbs = _make_bbs("positive", "neutral", "positive")
        result = self._run(bbs)
        assert not result["skip_fundamental"]
