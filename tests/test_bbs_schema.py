"""tests/test_bbs_schema.py — BBSスキーマ dataclass のユニットテスト"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.bbs_schema import (
    CriticJudgment,
    ExitDecisions,
    ExitResult,
    FundamentalAnalysis,
    MacroAnalysis,
    ManagerJudgment,
    NewsAnalysis,
    NewsArticle,
    RiskAnalysis,
    SocialAnalysis,
    TechnicalAnalysis,
    is_bbs_dataclass,
)


# ──────────────────────────────────────────────
# TechnicalAnalysis
# ──────────────────────────────────────────────

class TestTechnicalAnalysis:
    def test_from_dict_all_fields(self):
        ta = TechnicalAnalysis.from_dict(
            {"trend": "positive", "trend_reason": "MACD golden cross"}
        )
        assert ta.trend        == "positive"
        assert ta.trend_reason == "MACD golden cross"

    def test_from_dict_empty_uses_defaults(self):
        ta = TechnicalAnalysis.from_dict({})
        assert ta.trend        == "neutral"
        assert ta.trend_reason == ""

    def test_from_dict_missing_trend_reason(self):
        ta = TechnicalAnalysis.from_dict({"trend": "negative"})
        assert ta.trend_reason == ""

    def test_to_dict_roundtrip(self):
        ta  = TechnicalAnalysis(trend="negative", trend_reason="Death cross")
        ta2 = TechnicalAnalysis.from_dict(ta.to_dict())
        assert ta.trend        == ta2.trend
        assert ta.trend_reason == ta2.trend_reason

    def test_to_dict_keys(self):
        d = TechnicalAnalysis(trend="neutral").to_dict()
        assert "trend"        in d
        assert "trend_reason" in d


# ──────────────────────────────────────────────
# NewsArticle / NewsAnalysis
# ──────────────────────────────────────────────

class TestNewsAnalysis:
    def test_from_dict_articles_typed(self):
        data = {
            "articles": [
                {"ticker": "AAPL", "sentiment": "positive",
                 "title": "Apple beats Q1", "reason": "EPS up"},
            ],
            "avg_sentiment_score": 1.0,
        }
        na = NewsAnalysis.from_dict(data)
        assert len(na.articles) == 1
        assert isinstance(na.articles[0], NewsArticle)
        assert na.articles[0].ticker    == "AAPL"
        assert na.articles[0].sentiment == "positive"

    def test_from_dict_empty_articles(self):
        na = NewsAnalysis.from_dict({})
        assert na.articles            == []
        assert na.avg_sentiment_score == 0.0

    def test_news_article_defaults(self):
        a = NewsArticle.from_dict({})
        assert a.ticker    == ""
        assert a.sentiment == "neutral"

    def test_news_analysis_to_dict_roundtrip(self):
        na = NewsAnalysis(
            articles=[NewsArticle(ticker="NVDA", sentiment="positive",
                                  title="AI boom", reason="Revenue up")],
            avg_sentiment_score=1.0,
        )
        d   = na.to_dict()
        na2 = NewsAnalysis.from_dict(d)
        assert len(na2.articles)             == 1
        assert na2.articles[0].ticker        == "NVDA"
        assert na2.avg_sentiment_score       == 1.0


# ──────────────────────────────────────────────
# MacroAnalysis
# ──────────────────────────────────────────────

class TestMacroAnalysis:
    def test_suspended_flag(self):
        ma = MacroAnalysis.from_dict({"suspended": True, "trend": "neutral"})
        assert ma.suspended is True

    def test_defaults(self):
        ma = MacroAnalysis.from_dict({})
        assert ma.trend     == "neutral"
        assert ma.suspended is False

    def test_to_dict_includes_suspended(self):
        d = MacroAnalysis(suspended=True).to_dict()
        assert d["suspended"] is True


# ──────────────────────────────────────────────
# SocialAnalysis
# ──────────────────────────────────────────────

class TestSocialAnalysis:
    def test_from_dict_valid(self):
        sa = SocialAnalysis.from_dict(
            {"sentiment": "POSITIVE", "hype_score": 0.85, "reason": "meme stock"}
        )
        assert sa.sentiment  == "POSITIVE"
        assert sa.hype_score == pytest.approx(0.85)

    def test_from_dict_defaults(self):
        sa = SocialAnalysis.from_dict({})
        assert sa.sentiment  == "NEUTRAL"
        assert sa.hype_score == 0.0
        assert sa.post_count == 0

    def test_to_dict_roundtrip(self):
        sa  = SocialAnalysis(sentiment="NEGATIVE", hype_score=0.2, reason="fear")
        sa2 = SocialAnalysis.from_dict(sa.to_dict())
        assert sa.sentiment  == sa2.sentiment
        assert sa.hype_score == sa2.hype_score


# ──────────────────────────────────────────────
# FundamentalAnalysis
# ──────────────────────────────────────────────

class TestFundamentalAnalysis:
    def test_defaults(self):
        fa = FundamentalAnalysis.from_dict({})
        assert fa.trend        == "neutral"
        assert fa.trend_reason == ""

    def test_from_dict(self):
        fa = FundamentalAnalysis.from_dict(
            {"trend": "positive", "trend_reason": "EPS beat"}
        )
        assert fa.trend == "positive"


# ──────────────────────────────────────────────
# RiskAnalysis
# ──────────────────────────────────────────────

class TestRiskAnalysis:
    def test_from_dict_all_fields(self):
        ra = RiskAnalysis.from_dict({
            "ticker": "AAPL", "recommended_shares": 15,
            "stop_loss_price": 175.0, "take_profit_price": 210.0,
            "current_price": 190.0, "atr": 3.5, "risk_amount": 2000.0,
        })
        assert ra.ticker             == "AAPL"
        assert ra.recommended_shares == 15
        assert ra.stop_loss_price    == pytest.approx(175.0)

    def test_defaults(self):
        ra = RiskAnalysis.from_dict({})
        assert ra.recommended_shares == 1
        assert ra.stop_loss_price    == 0.0

    def test_to_dict_keys(self):
        d = RiskAnalysis().to_dict()
        for key in ("ticker", "recommended_shares", "stop_loss_price",
                    "take_profit_price", "current_price", "atr", "reason"):
            assert key in d


# ──────────────────────────────────────────────
# ManagerJudgment
# ──────────────────────────────────────────────

class TestManagerJudgment:
    def test_from_dict_strong_buy(self):
        mj = ManagerJudgment.from_dict({
            "ticker": "NVDA", "decision": "STRONG BUY",
            "score": 0.72, "is_strong_buy": True,
        })
        assert mj.decision      == "STRONG BUY"
        assert mj.is_strong_buy is True
        assert mj.score         == pytest.approx(0.72)

    def test_defaults(self):
        mj = ManagerJudgment.from_dict({})
        assert mj.decision          == "HOLD"
        assert mj.macro_forced_hold is False
        assert mj.signals           == {}

    def test_to_dict_roundtrip(self):
        mj  = ManagerJudgment(ticker="AAPL", decision="HOLD", score=-0.1)
        mj2 = ManagerJudgment.from_dict(mj.to_dict())
        assert mj.ticker   == mj2.ticker
        assert mj.decision == mj2.decision
        assert mj.score    == mj2.score


# ──────────────────────────────────────────────
# CriticJudgment
# ──────────────────────────────────────────────

class TestCriticJudgment:
    def test_approve(self):
        cj = CriticJudgment.from_dict({
            "critic_decision": "APPROVE",
            "revised_action":  "BUY",
            "critique_reason": "Fundamentals are solid",
        })
        assert cj.critic_decision == "APPROVE"
        assert cj.revised_action  == "BUY"

    def test_defaults(self):
        cj = CriticJudgment.from_dict({})
        assert cj.critic_decision == "HOLD"
        assert cj.llm_source      == "unknown"

    def test_to_dict_has_required_keys(self):
        d = CriticJudgment(critic_decision="OVERRIDE",
                           revised_action="HOLD",
                           critique_reason="RSI > 80").to_dict()
        assert "critic_decision" in d
        assert "revised_action"  in d
        assert "critique_reason" in d
        assert "llm_source"      in d


# ──────────────────────────────────────────────
# ExitDecisions
# ──────────────────────────────────────────────

class TestExitDecisions:
    def test_from_dict_results_typed(self):
        ed = ExitDecisions.from_dict({
            "date": "2026-05-11",
            "results": [
                {"ticker": "HSY", "action": "HOLD", "exit_type": "CONTINUE",
                 "pnl_pct": 0.35, "entry_price": 186.5, "current_price": 187.15},
            ],
        })
        assert ed.date              == "2026-05-11"
        assert len(ed.results)      == 1
        assert isinstance(ed.results[0], ExitResult)
        assert ed.results[0].ticker == "HSY"
        assert ed.results[0].action == "HOLD"

    def test_from_dict_empty(self):
        ed = ExitDecisions.from_dict({})
        assert ed.date    == ""
        assert ed.results == []

    def test_exit_result_pnl(self):
        er = ExitResult.from_dict({"ticker": "COR", "pnl_pct": -3.2, "action": "SELL"})
        assert er.pnl_pct == pytest.approx(-3.2)
        assert er.action  == "SELL"


# ──────────────────────────────────────────────
# is_bbs_dataclass ユーティリティ
# ──────────────────────────────────────────────

class TestIsBBSDataclass:
    def test_returns_true_for_bbs_schema(self):
        assert is_bbs_dataclass(TechnicalAnalysis()) is True
        assert is_bbs_dataclass(CriticJudgment())    is True
        assert is_bbs_dataclass(RiskAnalysis())      is True

    def test_returns_false_for_plain_dict(self):
        assert is_bbs_dataclass({"trend": "positive"}) is False

    def test_returns_false_for_string(self):
        assert is_bbs_dataclass("hello") is False

    def test_returns_false_for_none(self):
        assert is_bbs_dataclass(None) is False


# ──────────────────────────────────────────────
# BBS.write() が dataclass を受け入れる統合テスト
# ──────────────────────────────────────────────

class TestBBSWriteAcceptsDataclass:
    def test_bbs_write_with_technical_analysis(self, tmp_path, monkeypatch):
        """BBS.write() に TechnicalAnalysis を渡すと dict として保存される"""
        import engine.bbs as bbs_mod
        monkeypatch.setattr(bbs_mod, "BBS_DIR", tmp_path / "bbs")
        (tmp_path / "bbs").mkdir()

        from engine.bbs import BBS

        bbs = BBS("test_session")
        ta  = TechnicalAnalysis(trend="positive", trend_reason="MACD golden cross")
        bbs.write("TechnicalAgent", "technical_analysis", ta)

        result = bbs.read("technical_analysis")
        assert isinstance(result, dict)
        assert result["trend"]        == "positive"
        assert result["trend_reason"] == "MACD golden cross"

    def test_bbs_write_with_critic_judgment(self, tmp_path, monkeypatch):
        """BBS.write() に CriticJudgment を渡すと dict として保存される"""
        import engine.bbs as bbs_mod
        monkeypatch.setattr(bbs_mod, "BBS_DIR", tmp_path / "bbs")
        (tmp_path / "bbs").mkdir()

        from engine.bbs import BBS

        bbs = BBS("critic_session")
        cj  = CriticJudgment(
            critic_decision = "APPROVE",
            revised_action  = "BUY",
            critique_reason = "No rules violated",
            llm_source      = "gemini",
        )
        bbs.write("CriticAgent", "critic_judgment", cj)

        result = bbs.read("critic_judgment")
        assert result["critic_decision"] == "APPROVE"
        assert result["llm_source"]      == "gemini"

    def test_bbs_write_dict_still_works(self, tmp_path, monkeypatch):
        """既存コードの bbs.write(agent, key, dict) は引き続き動作する"""
        import engine.bbs as bbs_mod
        monkeypatch.setattr(bbs_mod, "BBS_DIR", tmp_path / "bbs")
        (tmp_path / "bbs").mkdir()

        from engine.bbs import BBS

        bbs = BBS("legacy_session")
        bbs.write("TestAgent", "test_key", {"foo": "bar", "count": 42})

        result = bbs.read("test_key")
        assert result["foo"]   == "bar"
        assert result["count"] == 42
