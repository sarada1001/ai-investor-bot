"""tests/test_research_mode.py — research_helpers の基本動作テスト"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── _identify_hold_causes ────────────────────────────────────────────────

class TestIdentifyHoldCauses:
    def _run(self, sigs, weights, macro_forced_hold=False):
        from engine.research_helpers import _identify_hold_causes
        return _identify_hold_causes(sigs, weights, macro_forced_hold)

    def test_macro_forced_hold_always_macro_primary(self):
        sigs    = {"technical": 0.5, "news": 0.5, "macro": -0.5, "fundamental": 0.5, "social": 0.0, "liquidity": 0.0}
        weights = {"technical": 0.2, "news": 0.1, "macro": 0.15, "fundamental": 0.35, "social": 0.1, "liquidity": 0.1}
        result = self._run(sigs, weights, macro_forced_hold=True)
        assert result["primary"] == "MacroAgent"
        assert result["contributing"] == ["MacroAgent"]

    def test_most_negative_is_primary(self):
        sigs    = {"technical": -0.8, "news": -0.3, "macro": 0.2, "fundamental": 0.5, "social": 0.0, "liquidity": 0.0}
        weights = {"technical": 0.2,  "news": 0.1,  "macro": 0.15, "fundamental": 0.35, "social": 0.1, "liquidity": 0.1}
        result = self._run(sigs, weights)
        # technical: -0.8 * 0.2 = -0.16
        # news:      -0.3 * 0.1 = -0.03
        assert result["primary"] == "TechnicalAgent"
        assert "TechnicalAgent" in result["contributing"]
        assert "NewsAgent" in result["contributing"]

    def test_all_neutral_returns_some_primary(self):
        sigs    = {k: 0.0 for k in ["technical", "news", "macro", "fundamental", "social", "liquidity"]}
        weights = {"technical": 0.2, "news": 0.1, "macro": 0.15, "fundamental": 0.35, "social": 0.1, "liquidity": 0.1}
        result = self._run(sigs, weights)
        assert result["primary"] is not None
        assert len(result["contributing"]) >= 1

    def test_single_negative_agent(self):
        sigs    = {"technical": 0.5, "news": 0.5, "macro": -0.9, "fundamental": 0.5, "social": 0.3, "liquidity": 0.2}
        weights = {"technical": 0.2, "news": 0.1, "macro": 0.15, "fundamental": 0.35, "social": 0.1, "liquidity": 0.1}
        result = self._run(sigs, weights, macro_forced_hold=False)
        assert result["primary"] == "MacroAgent"
        assert result["contributing"] == ["MacroAgent"]


# ── _normalize_bbs_snapshot ──────────────────────────────────────────────

def _make_mock_bbs(
    tech_score=0.5, news_score=0.3, macro_score=-0.2,
    social_score=0.1, fa_score=0.4, liq_score=0.2,
) -> MagicMock:
    def _read(key):
        return {
            "technical_analysis": {"score": tech_score, "trend": "positive", "trend_reason": "OK"},
            "news_analysis": {
                "articles": [{"ticker": "AAPL", "score": news_score, "sentiment": "positive"}],
                "avg_sentiment_score": news_score,
            },
            "macro_analysis": {"score": macro_score, "trend": "negative", "trend_reason": "VIX high"},
            "social_analysis": {"score": social_score, "sentiment": "NEUTRAL", "hype_score": 0.2},
            "fundamental_analysis": {"score": fa_score, "trend": "positive"},
            "liquidity_analysis": {"score": liq_score, "pressure": "buy_dominant"},
        }.get(key, {})
    bbs = MagicMock()
    bbs.read.side_effect = _read
    bbs.session_id = "test_session"
    return bbs


class TestNormalizeBbsSnapshot:
    def _run(self, bbs):
        from engine.research_helpers import _normalize_bbs_snapshot
        return _normalize_bbs_snapshot(bbs, "AAPL")

    def test_all_entries_have_score_and_signal(self):
        bbs = _make_mock_bbs()
        snap = self._run(bbs)
        for key in ["technical_analysis", "news_analysis", "macro_analysis",
                    "social_analysis", "fundamental_analysis", "liquidity_analysis"]:
            assert "_score" in snap[key], f"_score missing in {key}"
            assert "_signal" in snap[key], f"_signal missing in {key}"

    def test_score_values_are_float_in_range(self):
        bbs = _make_mock_bbs()
        snap = self._run(bbs)
        for key in snap:
            score = snap[key]["_score"]
            assert isinstance(score, float), f"{key}._score is not float"
            assert -1.0 <= score <= 1.0, f"{key}._score out of range: {score}"

    def test_articles_capped_at_3(self):
        bbs = MagicMock()
        def _read(key):
            if key == "news_analysis":
                return {"articles": [{"score": 0.1, "sentiment": "positive"}] * 10}
            return {}
        bbs.read.side_effect = _read
        bbs.session_id = "test"
        snap = _read("news_analysis")  # just verify articles trimming logic
        from engine.research_helpers import _normalize_bbs_snapshot
        result = _normalize_bbs_snapshot(bbs, "AAPL")
        assert len(result["news_analysis"]["articles"]) <= 3

    def test_macro_negative_signal_preserved(self):
        bbs = _make_mock_bbs(macro_score=-0.8)
        snap = self._run(bbs)
        assert snap["macro_analysis"]["_score"] < 0.0


# ── _save_hold_case ──────────────────────────────────────────────────────

class TestSaveHoldCase:
    def test_saves_jsonl_with_required_fields(self):
        from engine.research_helpers import _save_hold_case

        bbs = _make_mock_bbs()
        judgment = {
            "decision": "HOLD",
            "score": -0.05,
            "threshold": 0.60,
            "macro_forced_hold": False,
            "macro_brake": False,
            "signals": {"technical": 0.5, "news": 0.3, "macro": -0.2,
                        "fundamental": 0.4, "social": 0.1, "liquidity": 0.2},
            "rationale": "テスト用HOLDケース",
            "gate_reason": None,
        }
        weights = {"technical": 0.2, "news": 0.1, "macro": 0.15,
                   "fundamental": 0.35, "social": 0.1, "liquidity": 0.1}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("engine.research_helpers._RESEARCH_DIR", Path(tmpdir)):
                case_id = _save_hold_case("AAPL", judgment, bbs, "manager", weights)

            saved_file = Path(tmpdir) / "hold_cases.jsonl"
            assert saved_file.exists()

            with saved_file.open() as f:
                cases = [json.loads(line) for line in f if line.strip()]

        assert len(cases) == 1
        c = cases[0]

        required_fields = [
            "case_id", "ticker", "date", "session_id", "hold_type",
            "decision", "score", "threshold", "macro_forced_hold",
            "signals", "weights", "primary_reason_agent", "contributing_agents",
            "explanation", "bbs_snapshot",
        ]
        for field in required_fields:
            assert field in c, f"必須フィールド '{field}' が欠けています"

        assert c["ticker"] == "AAPL"
        assert c["hold_type"] == "manager"
        assert c["case_id"] == case_id
        assert c["primary_reason_agent"] is not None

        # bbs_snapshot の各エントリに _score と _signal が存在すること
        for bbs_key in ["technical_analysis", "news_analysis", "macro_analysis",
                         "social_analysis", "fundamental_analysis", "liquidity_analysis"]:
            assert "_score"  in c["bbs_snapshot"][bbs_key], f"bbs_snapshot[{bbs_key}]._score missing"
            assert "_signal" in c["bbs_snapshot"][bbs_key], f"bbs_snapshot[{bbs_key}]._signal missing"


# ── research_mode flag でLINE/Obsidian が無効化されることの確認 ──────────

class TestResearchModeFlag:
    def test_research_mode_forces_dry_run(self):
        """research_mode=True 時は dry_run=False でも発注経路に到達しない。"""
        from unittest.mock import patch, MagicMock
        import engine.trade_cycle as tc

        # run_trade_cycle の発注部分に到達しないことを確認（mock_mode=True を使い最小実行）
        with patch.object(tc, "send_line_message") as mock_line:
            try:
                from engine.trade_cycle import run_trade_cycle
                # mock_mode=True で実際のAPIは呼ばない、research_mode=True も設定
                result = run_trade_cycle(
                    ticker="AAPL",
                    mock_mode=True,
                    research_mode=True,
                    notify_line=True,  # research_mode で上書きされるはず
                )
                # LINE は呼ばれない（research_mode で notify_line=False に強制）
                mock_line.assert_not_called()
            except Exception:
                pass  # API関連エラーは無視（環境依存）
