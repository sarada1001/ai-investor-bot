"""tests/test_signal_scorer.py — signal_scorer 純粋関数のユニットテスト

外部API・LLMを一切呼び出さない。LLMが必要なケースはMagicMockで注入する。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from skills.signal_scorer import (
    BUY_THRESHOLD,
    SELL_THRESHOLD,
    _adjust_weights,
    _news_signal,
    _technical_signal,
    extract_fundamental_signal,
    run,
)

pytestmark = pytest.mark.unit


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────

def _make_news(articles: list[dict]) -> dict:
    return {"articles": articles}


def _article(company: str, sentiment: str) -> dict:
    return {"company": company, "sentiment": sentiment, "headline": f"{company} news"}


def _make_technical(ticker: str, rsi=50, macd_trend="golden_cross", ma25_diff=0) -> dict:
    return {
        "tickers": {
            ticker: {
                "rsi": rsi,
                "macd": {"trend": macd_trend},
                "ma25": {"diff_pct": ma25_diff},
            }
        }
    }


def _make_fundamental(queries: list[dict] | None = None) -> dict:
    if queries is None:
        queries = []
    return {"queries": queries}


def _query(q: str = "Revenue?", a: str = "Strong results") -> dict:
    return {"query": q, "analysis": a}


# ──────────────────────────────────────────────
# _news_signal
# ──────────────────────────────────────────────

class TestNewsSignal:
    def test_no_articles_returns_zero(self):
        sig, reason = _news_signal(_make_news([]), "AAPL")
        assert sig == 0.0
        assert "ニュースなし" in reason

    def test_all_positive_returns_one(self):
        news = _make_news([
            _article("AAPL", "positive"),
            _article("AAPL", "positive"),
        ])
        sig, _ = _news_signal(news, "AAPL")
        assert sig == 1.0

    def test_all_negative_returns_minus_one(self):
        news = _make_news([
            _article("AAPL", "negative"),
            _article("AAPL", "negative"),
        ])
        sig, _ = _news_signal(news, "AAPL")
        assert sig == -1.0

    def test_mixed_returns_zero(self):
        news = _make_news([
            _article("AAPL", "positive"),
            _article("AAPL", "negative"),
        ])
        sig, _ = _news_signal(news, "AAPL")
        assert sig == pytest.approx(0.0, abs=0.01)

    def test_neutral_returns_zero(self):
        news = _make_news([_article("AAPL", "neutral")])
        sig, _ = _news_signal(news, "AAPL")
        assert sig == 0.0

    def test_filters_by_target_company(self):
        """AAPL 記事のみ抽出、TSLA 記事は無視される。"""
        news = _make_news([
            _article("AAPL", "positive"),
            _article("TSLA", "negative"),
            _article("TSLA", "negative"),
        ])
        sig, _ = _news_signal(news, "AAPL")
        assert sig == 1.0

    def test_fallback_to_all_articles_when_no_match(self):
        """対象企業の記事がない → 全記事で計算。"""
        news = _make_news([
            _article("TSLA", "positive"),
            _article("MSFT", "positive"),
        ])
        sig, _ = _news_signal(news, "AAPL")
        assert sig == 1.0

    def test_unknown_sentiment_counts_as_zero(self):
        news = _make_news([_article("AAPL", "unknown_value")])
        sig, _ = _news_signal(news, "AAPL")
        assert sig == 0.0

    def test_reason_contains_company_and_sentiment(self):
        news = _make_news([_article("AAPL", "positive")])
        _, reason = _news_signal(news, "AAPL")
        assert "AAPL" in reason
        assert "positive" in reason

    def test_return_types(self):
        news = _make_news([_article("AAPL", "positive")])
        sig, reason = _news_signal(news, "AAPL")
        assert isinstance(sig, float)
        assert isinstance(reason, str)


# ──────────────────────────────────────────────
# _technical_signal
# ──────────────────────────────────────────────

class TestTechnicalSignal:
    def test_oversold_rsi_positive_signal(self):
        """RSI < 30 → 強い買いシグナル。"""
        tech = _make_technical("AAPL", rsi=25, macd_trend="dead_cross", ma25_diff=0)
        sig, reason = _technical_signal(tech, "AAPL")
        assert "RSI売られすぎ" in reason

    def test_overbought_rsi_negative_signal(self):
        """RSI > 70 → 強い売りシグナル。"""
        tech = _make_technical("AAPL", rsi=80, macd_trend="dead_cross", ma25_diff=0)
        sig, reason = _technical_signal(tech, "AAPL")
        assert "RSI買われすぎ" in reason

    def test_golden_cross_positive(self):
        """MACDゴールデンクロス → 正のシグナル成分。"""
        tech = _make_technical("AAPL", rsi=50, macd_trend="golden_cross", ma25_diff=0)
        sig, reason = _technical_signal(tech, "AAPL")
        assert "ゴールデンクロス" in reason

    def test_dead_cross_negative(self):
        """MACDデッドクロス → 負のシグナル成分。"""
        tech = _make_technical("AAPL", rsi=50, macd_trend="dead_cross", ma25_diff=0)
        sig, reason = _technical_signal(tech, "AAPL")
        assert "デッドクロス" in reason

    def test_large_negative_ma25_diff_bullish(self):
        """25MA大幅下方乖離 (-10%) → 反発期待で正のシグナル成分。"""
        tech = _make_technical("AAPL", rsi=50, macd_trend="golden_cross", ma25_diff=-10)
        _, reason = _technical_signal(tech, "AAPL")
        assert "反発期待" in reason

    def test_large_positive_ma25_diff_bearish(self):
        """25MA大幅上方乖離 (+10%) → 過熱警戒で負のシグナル成分。"""
        tech = _make_technical("AAPL", rsi=50, macd_trend="dead_cross", ma25_diff=10)
        _, reason = _technical_signal(tech, "AAPL")
        assert "過熱警戒" in reason

    def test_missing_ticker_returns_zero(self):
        tech = {"tickers": {}}
        sig, reason = _technical_signal(tech, "AAPL")
        assert sig == 0.0
        assert "指標なし" in reason

    def test_error_flag_returns_zero(self):
        tech = {"tickers": {"AAPL": {"error": "network timeout"}}}
        sig, reason = _technical_signal(tech, "AAPL")
        assert sig == 0.0
        assert "エラー" in reason

    def test_signal_bounded_minus_one_to_one(self):
        """全指標が売り → -1.0 を超えない。"""
        tech = _make_technical("AAPL", rsi=90, macd_trend="dead_cross", ma25_diff=20)
        sig, _ = _technical_signal(tech, "AAPL")
        assert -1.0 <= sig <= 1.0

    def test_return_types(self):
        tech = _make_technical("AAPL")
        sig, reason = _technical_signal(tech, "AAPL")
        assert isinstance(sig, float)
        assert isinstance(reason, str)


# ──────────────────────────────────────────────
# extract_fundamental_signal
# ──────────────────────────────────────────────

class TestExtractFundamentalSignal:
    def _make_llm(self, content: str) -> MagicMock:
        llm = MagicMock()
        llm.invoke.return_value.content = content
        return llm

    def test_no_queries_returns_zero(self):
        llm = MagicMock()
        sig, reason = extract_fundamental_signal(_make_fundamental([]), llm)
        assert sig == 0.0
        assert "ファンダメンタルデータなし" in reason
        llm.invoke.assert_not_called()

    def test_llm_bullish_signal(self):
        llm = self._make_llm('{"signal": 1, "reason": "Strong earnings growth"}')
        fa = _make_fundamental([_query()])
        sig, reason = extract_fundamental_signal(fa, llm)
        assert sig == 1.0
        assert "Strong earnings growth" in reason

    def test_llm_bearish_signal(self):
        llm = self._make_llm('{"signal": -1, "reason": "Declining revenue"}')
        fa = _make_fundamental([_query()])
        sig, reason = extract_fundamental_signal(fa, llm)
        assert sig == -1.0

    def test_llm_neutral_signal(self):
        llm = self._make_llm('{"signal": 0, "reason": "Mixed results"}')
        fa = _make_fundamental([_query()])
        sig, _ = extract_fundamental_signal(fa, llm)
        assert sig == 0.0

    def test_llm_response_with_extra_text(self):
        """LLMが余分なテキストを付けてもJSONパースできる。"""
        llm = self._make_llm('Here is the result: {"signal": 1, "reason": "Good"}')
        fa = _make_fundamental([_query()])
        sig, _ = extract_fundamental_signal(fa, llm)
        assert sig == 1.0

    def test_llm_error_returns_zero(self):
        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("LLM unavailable")
        fa = _make_fundamental([_query()])
        sig, reason = extract_fundamental_signal(fa, llm)
        assert sig == 0.0
        assert "LLM解析エラー" in reason

    def test_signal_clamped_to_minus_one_plus_one(self):
        """LLMが範囲外の値を返しても -1/+1 にクランプ。"""
        llm = self._make_llm('{"signal": 99, "reason": "Extreme bullish"}')
        fa = _make_fundamental([_query()])
        sig, _ = extract_fundamental_signal(fa, llm)
        assert sig == 1.0


# ──────────────────────────────────────────────
# _adjust_weights
# ──────────────────────────────────────────────

class TestAdjustWeights:
    def test_default_weights_when_no_condition(self):
        weights, reason = _adjust_weights(0.0, 0.0, _make_fundamental([]))
        assert weights["news"] == pytest.approx(0.2, abs=0.01)
        assert weights["fundamental"] == pytest.approx(0.5, abs=0.01)
        assert weights["technical"] == pytest.approx(0.3, abs=0.01)
        assert "デフォルト" in reason

    def test_major_news_shifts_news_weight(self):
        """|news_signal| >= 0.8 → News 重み増。"""
        weights, reason = _adjust_weights(1.0, 0.0, _make_fundamental([]))
        assert weights["news"] == pytest.approx(0.35, abs=0.01)
        assert "重大ニュース" in reason

    def test_major_news_negative_also_triggers(self):
        weights, _ = _adjust_weights(-1.0, 0.0, _make_fundamental([]))
        assert weights["news"] == pytest.approx(0.35, abs=0.01)

    def test_earnings_period_shifts_fundamental_weight(self):
        """FA query >= 4 → Fundamental 重み増。"""
        fa = _make_fundamental([_query()] * 4)
        weights, reason = _adjust_weights(0.0, 0.0, fa)
        assert weights["fundamental"] == pytest.approx(0.55, abs=0.01)
        assert "決算期" in reason

    def test_strong_ta_shifts_technical_weight(self):
        """|tech_signal| >= 0.7 → Technical 重み増。"""
        weights, reason = _adjust_weights(0.0, 1.0, _make_fundamental([]))
        assert weights["technical"] == pytest.approx(0.45, abs=0.01)
        assert "テクニカルトレンド" in reason

    def test_major_news_takes_priority_over_earnings(self):
        """重大ニュース が決算期より優先。"""
        fa = _make_fundamental([_query()] * 5)
        weights, reason = _adjust_weights(1.0, 0.0, fa)
        assert "重大ニュース" in reason
        assert weights["news"] == pytest.approx(0.35, abs=0.01)

    def test_weights_sum_to_one(self):
        """全条件でウェイトの合計が 1.0 になること。"""
        for ns, ts, fa in [
            (0.0, 0.0, _make_fundamental([])),
            (1.0, 0.0, _make_fundamental([])),
            (0.0, 0.0, _make_fundamental([_query()] * 4)),
            (0.0, 0.9, _make_fundamental([])),
        ]:
            weights, _ = _adjust_weights(ns, ts, fa)
            assert sum(weights.values()) == pytest.approx(1.0, abs=0.001)

    def test_boundary_news_signal_exactly_08(self):
        """news_signal = 0.8 (境界値) → 重大ニュースとして扱う。"""
        weights, _ = _adjust_weights(0.8, 0.0, _make_fundamental([]))
        assert weights["news"] == pytest.approx(0.35, abs=0.01)

    def test_boundary_ta_signal_exactly_07(self):
        """tech_signal = 0.7 → Strong TA として扱う。"""
        weights, _ = _adjust_weights(0.0, 0.7, _make_fundamental([]))
        assert weights["technical"] == pytest.approx(0.45, abs=0.01)


# ──────────────────────────────────────────────
# run (public API)
# ──────────────────────────────────────────────

class TestRun:
    def _make_inputs(self, rsi=50, macd="golden_cross", ma25=0,
                     news_sentiment="neutral", fa_queries=0,
                     fundamental_signal=0.0):
        news = _make_news([_article("AAPL", news_sentiment)])
        tech = _make_technical("AAPL", rsi=rsi, macd_trend=macd, ma25_diff=ma25)
        fa = _make_fundamental([_query()] * fa_queries)
        return news, fa, tech, fundamental_signal

    def test_returns_required_keys(self):
        news, fa, tech, fs = self._make_inputs()
        result = run(news, fa, tech, "AAPL", fundamental_signal=fs)
        for key in ("action", "score", "confidence", "signals",
                    "signal_reasons", "weights", "weight_reason"):
            assert key in result, f"キーが欠落: {key}"

    def test_action_buy_when_score_exceeds_threshold(self):
        """全シグナルが強気 → BUY。"""
        news = _make_news([_article("AAPL", "positive")])
        tech = _make_technical("AAPL", rsi=25, macd_trend="golden_cross", ma25_diff=-10)
        result = run(news, _make_fundamental([]), tech, "AAPL", fundamental_signal=1.0)
        assert result["action"] == "BUY"
        assert result["score"] > BUY_THRESHOLD

    def test_action_sell_when_score_below_threshold(self):
        """全シグナルが弱気 → SELL。"""
        news = _make_news([_article("AAPL", "negative")])
        tech = _make_technical("AAPL", rsi=80, macd_trend="dead_cross", ma25_diff=10)
        result = run(news, _make_fundamental([]), tech, "AAPL", fundamental_signal=-1.0)
        assert result["action"] == "SELL"
        assert result["score"] < SELL_THRESHOLD

    def test_action_hold_when_score_in_range(self):
        """中立シグナル → HOLD。"""
        news, fa, tech, fs = self._make_inputs()
        result = run(news, fa, tech, "AAPL", fundamental_signal=0.0)
        assert result["action"] == "HOLD"

    def test_confidence_proportional_to_abs_score(self):
        news, fa, tech, fs = self._make_inputs()
        result = run(news, fa, tech, "AAPL", fundamental_signal=0.0)
        expected = min(100, int(abs(result["score"]) * 100))
        assert result["confidence"] == expected

    def test_confidence_capped_at_100(self):
        news = _make_news([_article("AAPL", "positive")])
        tech = _make_technical("AAPL", rsi=25, macd_trend="golden_cross", ma25_diff=-10)
        result = run(news, _make_fundamental([]), tech, "AAPL", fundamental_signal=1.0)
        assert result["confidence"] <= 100

    def test_no_llm_uses_zero_fundamental(self):
        """llm=None かつ fundamental_signal 未指定 → 0.0 で計算。"""
        news, fa, tech, _ = self._make_inputs()
        result = run(news, fa, tech, "AAPL", llm=None)
        assert result["signals"]["fundamental"] == 0.0

    def test_precomputed_fundamental_skips_llm(self):
        """fundamental_signal を事前注入するとLLMを呼ばない。"""
        llm = MagicMock()
        news, fa, tech, _ = self._make_inputs()
        run(news, fa, tech, "AAPL", fundamental_signal=0.5, llm=llm)
        llm.invoke.assert_not_called()

    def test_signals_dict_has_three_keys(self):
        news, fa, tech, fs = self._make_inputs()
        result = run(news, fa, tech, "AAPL", fundamental_signal=fs)
        assert set(result["signals"].keys()) == {"news", "fundamental", "technical"}

    def test_score_is_weighted_sum(self):
        """score = Σ(signal × weight)。"""
        news = _make_news([_article("AAPL", "positive")])  # news_sig = 1.0
        tech = _make_technical("AAPL", rsi=50, macd_trend="golden_cross", ma25_diff=0)
        fa = _make_fundamental([])
        result = run(news, fa, tech, "AAPL", fundamental_signal=0.0)
        w = result["weights"]
        sig = result["signals"]
        expected = round(
            sig["news"] * w["news"]
            + sig["fundamental"] * w["fundamental"]
            + sig["technical"] * w["technical"],
            4,
        )
        assert result["score"] == pytest.approx(expected, abs=0.0001)
