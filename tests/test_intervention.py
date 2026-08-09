"""tests/test_intervention.py — _flip_bbs_snapshot / _compute_decision_from_signals テスト"""

from __future__ import annotations

import pytest


# ── テスト用スナップショットファクトリ ─────────────────────────────────────

def _make_snapshot(
    tech=0.5, news=0.4, macro=0.3, social=0.2, fa=0.6, liq=0.1,
    tech_trend="positive", news_signal="positive",
    macro_trend="positive", social_sentiment="POSITIVE",
    fa_trend="positive",
) -> dict:
    return {
        "technical_analysis": {
            "_score": tech, "_signal": tech_trend,
            "trend": tech_trend, "score": tech,
        },
        "news_analysis": {
            "_score": news, "_signal": news_signal,
            "avg_sentiment_score": news,
            "articles": [
                {"sentiment": news_signal, "score": news, "title": "test"},
            ],
        },
        "macro_analysis": {
            "_score": macro, "_signal": macro_trend,
            "trend": macro_trend, "score": macro,
        },
        "social_analysis": {
            "_score": social, "_signal": social_sentiment,
            "sentiment": social_sentiment, "score": social, "hype_score": 0.2,
        },
        "fundamental_analysis": {
            "_score": fa, "_signal": fa_trend,
            "trend": fa_trend, "score": fa,
        },
        "liquidity_analysis": {
            "_score": liq, "_signal": "buy_dominant",
            "score": liq, "pressure": "buy_dominant",
        },
    }


_WEIGHTS = {
    "fundamental": 0.35, "technical": 0.20, "macro": 0.15,
    "news": 0.10, "social": 0.10, "liquidity": 0.10,
}


# ── _flip_bbs_snapshot ───────────────────────────────────────────────────

class TestFlipBbsSnapshot:
    def _flip(self, snapshot, weight_key):
        from engine.research_helpers import _flip_bbs_snapshot
        return _flip_bbs_snapshot(snapshot, weight_key)

    # ── 基本的な符号反転 ──────────────────────────────────────────────

    def test_technical_score_negated(self):
        snap = _make_snapshot(tech=0.5)
        result = self._flip(snap, "technical")
        assert result["technical_analysis"]["_score"] == pytest.approx(-0.5)

    def test_macro_negative_becomes_positive(self):
        snap = _make_snapshot(macro=-0.8, macro_trend="negative")
        result = self._flip(snap, "macro")
        assert result["macro_analysis"]["_score"] == pytest.approx(0.8)
        assert result["macro_analysis"]["_signal"] == "positive"
        assert result["macro_analysis"]["trend"] == "positive"

    def test_social_positive_becomes_negative(self):
        snap = _make_snapshot(social=0.6, social_sentiment="POSITIVE")
        result = self._flip(snap, "social")
        entry = result["social_analysis"]
        assert entry["_score"] == pytest.approx(-0.6)
        assert entry["_signal"] == "NEGATIVE"
        assert entry["sentiment"] == "NEGATIVE"

    def test_news_articles_score_negated(self):
        snap = _make_snapshot(news=0.3, news_signal="positive")
        result = self._flip(snap, "news")
        entry = result["news_analysis"]
        assert entry["_score"] == pytest.approx(-0.3)
        assert entry["_signal"] == "negative"
        for art in entry.get("articles", []):
            if isinstance(art.get("score"), (int, float)):
                assert art["score"] < 0

    def test_news_article_sentiment_flipped(self):
        snap = _make_snapshot(news=0.3, news_signal="positive")
        result = self._flip(snap, "news")
        for art in result["news_analysis"].get("articles", []):
            if "sentiment" in art:
                assert art["sentiment"] in ("negative", "NEGATIVE", "neutral")

    def test_fundamental_flip(self):
        snap = _make_snapshot(fa=0.7, fa_trend="positive")
        result = self._flip(snap, "fundamental")
        assert result["fundamental_analysis"]["_score"] == pytest.approx(-0.7)
        assert result["fundamental_analysis"]["_signal"] == "negative"

    def test_liquidity_pressure_inverted(self):
        snap = _make_snapshot(liq=0.4)
        result = self._flip(snap, "liquidity")
        assert result["liquidity_analysis"]["_score"] == pytest.approx(-0.4)
        assert result["liquidity_analysis"]["pressure"] == "sell_dominant"

    # ── 中立は反転後も中立 ────────────────────────────────────────────

    def test_neutral_score_stays_zero(self):
        snap = _make_snapshot(tech=0.0, tech_trend="neutral")
        result = self._flip(snap, "technical")
        assert result["technical_analysis"]["_score"] == pytest.approx(0.0)
        assert result["technical_analysis"]["_signal"] == "neutral"

    # ── 不変性: flip(flip(x)) == x ───────────────────────────────────

    def test_double_flip_restores_original(self):
        from engine.research_helpers import _flip_bbs_snapshot
        snap = _make_snapshot(tech=0.5, tech_trend="positive")
        double_flipped = _flip_bbs_snapshot(_flip_bbs_snapshot(snap, "technical"), "technical")
        assert double_flipped["technical_analysis"]["_score"] == pytest.approx(0.5)
        assert double_flipped["technical_analysis"]["_signal"] == "positive"

    # ── 他エージェントのデータは変更されない ────────────────────────

    def test_other_agents_unchanged(self):
        snap = _make_snapshot(tech=0.5, macro=0.3)
        result = self._flip(snap, "technical")
        # macro は変更されていないはず
        assert result["macro_analysis"]["_score"] == pytest.approx(0.3)

    # ── 存在しない weight_key は何もしない ──────────────────────────

    def test_unknown_key_returns_original(self):
        snap = _make_snapshot(tech=0.5)
        result = self._flip(snap, "nonexistent_key")
        assert result["technical_analysis"]["_score"] == pytest.approx(0.5)


# ── _compute_decision_from_signals ───────────────────────────────────────

class TestComputeDecisionFromSignals:
    def _run(self, snapshot, weights=None, excluded=None):
        from engine.research_helpers import _compute_decision_from_signals
        return _compute_decision_from_signals(
            bbs_snapshot=snapshot,
            ticker="AAPL",
            weights=weights or _WEIGHTS,
            excluded_keys=excluded,
        )

    def test_all_zero_returns_hold(self):
        snap = _make_snapshot(tech=0.0, news=0.0, macro=0.0, social=0.0, fa=0.0, liq=0.0)
        result = self._run(snap)
        assert result["decision"] == "HOLD"
        assert result["score"] == pytest.approx(0.0)

    def test_all_positive_high_score_returns_strong_buy(self):
        # score = 0.8*0.35 + 0.8*0.20 + 0.8*0.15 + 0.8*0.10 + 0.8*0.10 + 0.8*0.10 = 0.8
        snap = _make_snapshot(tech=0.8, news=0.8, macro=0.8, social=0.8, fa=0.8, liq=0.8)
        result = self._run(snap)
        assert result["decision"] == "STRONG BUY"
        assert result["is_strong_buy"] is True

    def test_macro_negative_forces_hold(self):
        snap = _make_snapshot(tech=0.9, news=0.9, macro=-0.1, social=0.9, fa=0.9, liq=0.9)
        result = self._run(snap)
        assert result["decision"] == "HOLD"
        assert result["macro_forced_hold"] is True

    def test_macro_excluded_removes_forced_hold(self):
        """macro が negative でも exclusion すれば macro_forced_hold は解除される。"""
        snap = _make_snapshot(tech=0.9, news=0.9, macro=-0.1, social=0.9, fa=0.9, liq=0.9)
        result = self._run(snap, excluded=["macro"])
        assert result["macro_forced_hold"] is False

    def test_excluded_agent_signal_is_zero(self):
        snap = _make_snapshot(tech=0.8, news=0.8, macro=0.8, social=0.8, fa=0.8, liq=0.8)
        result_no_excl = self._run(snap)
        result_excl    = self._run(snap, excluded=["technical"])
        # technical 除外でスコアが下がる
        assert result_excl["score"] < result_no_excl["score"]
        assert result_excl["signals"]["technical"] == 0.0

    def test_fa_zero_prevents_strong_buy(self):
        """FA > 0 の条件を満たさないと STRONG BUY にならない。"""
        snap = _make_snapshot(tech=0.9, news=0.9, macro=0.9, social=0.9, fa=0.0, liq=0.9)
        result = self._run(snap)
        assert result["decision"] == "HOLD"

    def test_score_computation_is_weighted_sum(self):
        snap = _make_snapshot(tech=1.0, news=0.0, macro=0.0, social=0.0, fa=0.0, liq=0.0)
        result = self._run(snap)
        expected = round(1.0 * _WEIGHTS["technical"], 4)
        assert result["score"] == pytest.approx(expected)


# ── _compute_gate_from_snapshot ──────────────────────────────────────────

class TestComputeGateFromSnapshot:
    def _run(self, snapshot, excluded=None):
        from engine.research_helpers import _compute_gate_from_snapshot
        return _compute_gate_from_snapshot(snapshot, excluded_keys=excluded)

    def test_macro_negative_triggers_gate_hold(self):
        snap = _make_snapshot(tech=0.5, news=0.5, macro=-0.5)
        result = self._run(snap)
        assert result["macro_brake"] is True
        assert result["skip_fundamental"] is True

    def test_both_neutral_triggers_gate_hold(self):
        snap = _make_snapshot(tech=0.0, news=0.0, macro=0.5)
        result = self._run(snap)
        assert result["macro_brake"] is False
        assert result["skip_fundamental"] is True

    def test_all_positive_passes_gate(self):
        snap = _make_snapshot(tech=0.5, news=0.5, macro=0.5)
        result = self._run(snap)
        assert result["skip_fundamental"] is False

    def test_macro_excluded_removes_brake(self):
        snap = _make_snapshot(tech=0.5, news=0.5, macro=-0.8)
        result = self._run(snap, excluded=["macro"])
        assert result["macro_brake"] is False
