"""tests/test_liquidity_mock_isolation.py — mock_fallback の発注スコア混入防止テスト

問題背景:
  Futu OpenD 接続失敗時、MoomooFetcher は mock_fallback を返す。
  その mock score が BBS に保存され、Gate HOLD パスと Manager の
  weighted score の両方に liquidity weight=0.10 として加算されていた。
  例: AAPL mock_fallback score=+0.8048 → +0.08048 が架空データ由来で加算。

テスト対象:
  - _liquidity_trading_score: mock/mock_fallback → 0.0, live → 実スコア
  - analyze_liquidity: eligible_for_trading フィールドの正しい設定
  - Gate HOLD パス（trade_cycle.py）: mock_fallback が score に寄与しない
  - ManagerAgent（agent_wrappers.py）: mock_fallback が weighted score に寄与しない
  - LiquidityAnalysis bbs_schema: eligible_for_trading フィールドの保存・復元
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.agent_wrappers import _liquidity_trading_score
from skills.liquidity_monitor import (
    analyze_liquidity,
    _TRADING_ELIGIBLE_SOURCES,
)
from engine.bbs_schema import LiquidityAnalysis


# ──────────────────────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────────────────────

def _liq_data(
    score: float,
    data_source: str,
    eligible_for_trading: bool | None = None,
) -> dict:
    """テスト用の liquidity_analysis BBS エントリを組み立てる。"""
    base = {
        "ticker":            "AAPL",
        "score":             score,
        "data_source":       data_source,
        "verbal_annotation": "テスト用アノテーション",
        "ask_ratio":         0.4,
        "bid_ratio":         0.6,
        "net_large_inflow":  3_000_000,
        "net_small_inflow":  -50_000,
        "pressure":          "buy_dominant",
    }
    if eligible_for_trading is not None:
        base["eligible_for_trading"] = eligible_for_trading
    return base


# ──────────────────────────────────────────────────────────────────────
# _TRADING_ELIGIBLE_SOURCES 定数の確認
# ──────────────────────────────────────────────────────────────────────

class TestTradingEligibleSources:
    def test_live_is_eligible(self):
        assert "live" in _TRADING_ELIGIBLE_SOURCES

    def test_mock_is_not_eligible(self):
        assert "mock" not in _TRADING_ELIGIBLE_SOURCES

    def test_mock_fallback_is_not_eligible(self):
        assert "mock_fallback" not in _TRADING_ELIGIBLE_SOURCES

    def test_error_is_not_eligible(self):
        assert "error" not in _TRADING_ELIGIBLE_SOURCES


# ──────────────────────────────────────────────────────────────────────
# _liquidity_trading_score のユニットテスト
# ──────────────────────────────────────────────────────────────────────

class TestLiquidityTradingScore:
    """_liquidity_trading_score の全パターン。"""

    def test_live_source_returns_actual_score(self):
        data = _liq_data(score=0.75, data_source="live", eligible_for_trading=True)
        assert _liquidity_trading_score(data) == pytest.approx(0.75)

    def test_mock_fallback_returns_zero(self):
        """Futu OpenD 接続失敗 → mock_fallback score は寄与しない（要件 1）。"""
        data = _liq_data(score=0.8048, data_source="mock_fallback", eligible_for_trading=False)
        assert _liquidity_trading_score(data) == 0.0

    def test_mock_source_returns_zero(self):
        """MOOMOO_USE_MOCK=true 時の意図的モックも寄与しない。"""
        data = _liq_data(score=0.65, data_source="mock", eligible_for_trading=False)
        assert _liquidity_trading_score(data) == 0.0

    def test_error_source_returns_zero(self):
        """取得エラー時も 0.0。"""
        data = _liq_data(score=0.0, data_source="error", eligible_for_trading=False)
        assert _liquidity_trading_score(data) == 0.0

    def test_missing_eligible_flag_defaults_to_zero(self):
        """eligible_for_trading フィールドが存在しない旧データは保守的に 0.0。"""
        data = {"score": 0.5, "data_source": "mock_fallback"}
        assert _liquidity_trading_score(data) == 0.0

    def test_live_score_is_clamped_to_valid_range(self):
        """live データでも ±1.0 クランプが効くこと。"""
        data = _liq_data(score=1.5, data_source="live", eligible_for_trading=True)
        assert _liquidity_trading_score(data) == pytest.approx(1.0)

    def test_negative_live_score_is_preserved(self):
        data = _liq_data(score=-0.72, data_source="live", eligible_for_trading=True)
        assert _liquidity_trading_score(data) == pytest.approx(-0.72)

    def test_empty_dict_returns_zero(self):
        assert _liquidity_trading_score({}) == 0.0

    def test_raw_score_preserved_in_bbs_when_mock_fallback(self):
        """mock_fallback でも BBS 上の raw score は書き換えられない（デバッグ用途）。"""
        data = _liq_data(score=0.8048, data_source="mock_fallback", eligible_for_trading=False)
        trading_score = _liquidity_trading_score(data)
        assert trading_score == 0.0
        assert data["score"] == pytest.approx(0.8048)  # BBS の raw score は変更なし


# ──────────────────────────────────────────────────────────────────────
# analyze_liquidity: eligible_for_trading フィールドのテスト
# ──────────────────────────────────────────────────────────────────────

class TestAnalyzeLiquidityEligibility:
    """analyze_liquidity が eligible_for_trading を正しく設定するか。"""

    def test_mock_mode_sets_eligible_false(self):
        result = analyze_liquidity("AAPL", use_mock=True)
        assert result["eligible_for_trading"] is False

    def test_eligible_for_trading_key_present(self):
        result = analyze_liquidity("AAPL", use_mock=True)
        assert "eligible_for_trading" in result

    def test_mock_fallback_sets_eligible_false(self):
        """ライブ失敗→フォールバック時に eligible_for_trading=False になること。

        _check_opend_reachable をパッチして ConnectionRefusedError を発生させる。
        _live_order_book / _live_capital_flow の内部 try/except が捕捉して
        _had_live_failure=True にセットし、data_source が "mock_fallback" になる。
        """
        from engine.fetchers.moomoo_fetcher import MoomooFetcher
        with patch.object(MoomooFetcher, "_check_opend_reachable",
                          side_effect=ConnectionRefusedError("no OpenD")):
            result = analyze_liquidity("AAPL", use_mock=False)

        # OpenD が unreachable なのでフォールバックが起きているはず
        assert result["data_source"] == "mock_fallback"
        assert result["eligible_for_trading"] is False

    def test_error_path_sets_eligible_false(self):
        """get_order_book が例外を raise → error パスで eligible_for_trading=False。"""
        from unittest.mock import patch
        from engine.fetchers.moomoo_fetcher import MoomooFetcher
        with patch.object(MoomooFetcher, "get_order_book",
                          side_effect=RuntimeError("boom")):
            result = analyze_liquidity("AAPL", use_mock=True)
        assert result.get("eligible_for_trading") is False


# ──────────────────────────────────────────────────────────────────────
# Manager weighted score への混入防止テスト
# ──────────────────────────────────────────────────────────────────────

class TestManagerMockFallbackIsolation:
    """ManagerAgent: mock_fallback Liquidity が weighted score に寄与しない（要件 1・4）。"""

    def _make_bbs_with_liquidity(self, liq_data: dict) -> MagicMock:
        """指定した liquidity_data を返す BBS モックを作る。"""
        def _read(key: str):
            return {
                "liquidity_analysis":  liq_data,
                "news_analysis":       {"articles": []},
                "technical_analysis":  {"trend": "neutral", "score": 0.0},
                "fundamental_analysis":{"trend": "neutral", "score": 0.0},
                "macro_analysis":      {"trend": "neutral", "score": 0.0},
                "social_analysis":     {"sentiment": "NEUTRAL", "hype_score": 0.0, "score": 0.0},
            }.get(key, {})

        bbs = MagicMock()
        bbs.read.side_effect = _read
        return bbs

    def test_mock_fallback_does_not_contribute_to_weighted_score(self):
        """mock_fallback score=+0.8048 で liquidity weight=0.10 のとき寄与=0。"""
        from engine.agent_wrappers import ManagerAgent
        from engine.constants import WEIGHTS

        liq = _liq_data(score=0.8048, data_source="mock_fallback", eligible_for_trading=False)
        bbs = self._make_bbs_with_liquidity(liq)

        agent = ManagerAgent.__new__(ManagerAgent)
        agent.bbs  = bbs
        agent._llm = MagicMock()
        agent._llm.invoke.return_value = MagicMock(content="mock rationale")

        result = agent.run(
            ticker="AAPL",
            dry_run=True,
            phase_tag="TEST",
            mock_mode=True,
            effective_weights=WEIGHTS,
            excluded_keys=[],
        )

        liq_contribution = result["signals"].get("liquidity", 0.0) * WEIGHTS.get("liquidity", 0.0)
        assert liq_contribution == pytest.approx(0.0), (
            f"mock_fallback が weighted score に寄与している: "
            f"liq_sig={result['signals'].get('liquidity')}, weight={WEIGHTS.get('liquidity')}"
        )

    def test_live_source_contributes_normally(self):
        """live score=+0.75 で liquidity weight=0.10 のとき寄与=+0.075。"""
        from engine.agent_wrappers import ManagerAgent
        from engine.constants import WEIGHTS

        liq = _liq_data(score=0.75, data_source="live", eligible_for_trading=True)
        bbs = self._make_bbs_with_liquidity(liq)

        agent = ManagerAgent.__new__(ManagerAgent)
        agent.bbs  = bbs
        agent._llm = MagicMock()
        agent._llm.invoke.return_value = MagicMock(content="mock rationale")

        result = agent.run(
            ticker="AAPL",
            dry_run=True,
            phase_tag="TEST",
            mock_mode=True,
            effective_weights=WEIGHTS,
            excluded_keys=[],
        )

        liq_sig = result["signals"].get("liquidity", 0.0)
        assert liq_sig == pytest.approx(0.75), (
            f"live score が正しく反映されていない: expected 0.75, got {liq_sig}"
        )

    def test_mock_fallback_signal_is_zero_in_signals_dict(self):
        """result['signals']['liquidity'] が mock_fallback 時に 0.0 であること。"""
        from engine.agent_wrappers import ManagerAgent
        from engine.constants import WEIGHTS

        liq = _liq_data(score=0.8048, data_source="mock_fallback", eligible_for_trading=False)
        bbs = self._make_bbs_with_liquidity(liq)

        agent = ManagerAgent.__new__(ManagerAgent)
        agent.bbs  = bbs
        agent._llm = MagicMock()
        agent._llm.invoke.return_value = MagicMock(content="mock rationale")

        result = agent.run(
            ticker="AAPL",
            dry_run=True,
            phase_tag="TEST",
            mock_mode=True,
            effective_weights=WEIGHTS,
            excluded_keys=[],
        )

        assert result["signals"]["liquidity"] == pytest.approx(0.0)


# ──────────────────────────────────────────────────────────────────────
# Gate HOLD パスへの混入防止テスト
# ──────────────────────────────────────────────────────────────────────

class TestGateMockFallbackIsolation:
    """Gate HOLD パス: mock_fallback Liquidity が score に寄与しない（要件 1・4）。"""

    def _run_gate_hold_score(self, liq_data: dict) -> tuple[float, float]:
        """
        Gate HOLD パスをシミュレートして (gate_score_with_liq, expected_without_liq) を返す。

        実際の trade_cycle.run_trade_cycle() を呼ばず、
        Gate HOLD パスの score 計算式と _liquidity_trading_score() の組み合わせだけを検証する。
        """
        from engine.constants import WEIGHTS

        gate_news  = 0.0
        gate_tech  = 0.0
        gate_macro = 0.0
        sg_sig     = 0.0
        liq_sig    = _liquidity_trading_score(liq_data)

        score = round(
            gate_news  * WEIGHTS["news"]
            + gate_tech  * WEIGHTS["technical"]
            + gate_macro * WEIGHTS["macro"]
            + sg_sig     * WEIGHTS["social"]
            + liq_sig    * WEIGHTS.get("liquidity", 0.0),
            4,
        )
        return score, liq_sig

    def test_mock_fallback_does_not_contaminate_gate_score(self):
        """mock_fallback score=+0.8048 → Gate score に寄与しない。"""
        liq = _liq_data(score=0.8048, data_source="mock_fallback", eligible_for_trading=False)
        score, liq_sig = self._run_gate_hold_score(liq)
        assert liq_sig == 0.0
        assert score == pytest.approx(0.0)

    def test_live_source_contributes_to_gate_score(self):
        """live score=+0.75 → Gate score に正しく寄与する。"""
        from engine.constants import WEIGHTS
        liq = _liq_data(score=0.75, data_source="live", eligible_for_trading=True)
        score, liq_sig = self._run_gate_hold_score(liq)
        expected = round(0.75 * WEIGHTS.get("liquidity", 0.0), 4)
        assert liq_sig == pytest.approx(0.75)
        assert score == pytest.approx(expected)

    def test_gate_check_liquidity_signal_is_zero_for_mock_fallback(self):
        """_gate_check() が返す liquidity_signal が mock_fallback 時に 0.0 であること。"""
        from engine.agent_wrappers import _gate_check

        liq = _liq_data(score=0.8048, data_source="mock_fallback", eligible_for_trading=False)

        def _read(key: str):
            return {
                "technical_analysis":  {"trend": "positive"},
                "news_analysis":       {"articles": [{"ticker": "AAPL", "sentiment": "positive"}]},
                "macro_analysis":      {"trend": "positive"},
                "liquidity_analysis":  liq,
            }.get(key, {})

        bbs = MagicMock()
        bbs.read.side_effect = _read

        gate = _gate_check(bbs, "AAPL")
        assert gate["liquidity_signal"] == pytest.approx(0.0), (
            f"_gate_check() が mock_fallback のスコアを返している: {gate['liquidity_signal']}"
        )

    def test_gate_check_liquidity_signal_for_live_source(self):
        """_gate_check() が live データのスコアを正しく返すこと。"""
        from engine.agent_wrappers import _gate_check

        liq = _liq_data(score=0.75, data_source="live", eligible_for_trading=True)

        def _read(key: str):
            return {
                "technical_analysis":  {"trend": "positive"},
                "news_analysis":       {"articles": [{"ticker": "AAPL", "sentiment": "positive"}]},
                "macro_analysis":      {"trend": "positive"},
                "liquidity_analysis":  liq,
            }.get(key, {})

        bbs = MagicMock()
        bbs.read.side_effect = _read

        gate = _gate_check(bbs, "AAPL")
        assert gate["liquidity_signal"] == pytest.approx(0.75)


# ──────────────────────────────────────────────────────────────────────
# LiquidityAnalysis bbs_schema テスト
# ──────────────────────────────────────────────────────────────────────

class TestLiquidityAnalysisBBSSchema:
    """eligible_for_trading フィールドのスキーマ保存・復元テスト。"""

    def test_from_dict_with_eligible_true(self):
        data = _liq_data(score=0.75, data_source="live", eligible_for_trading=True)
        la = LiquidityAnalysis.from_dict(data)
        assert la.eligible_for_trading is True
        assert la.data_source == "live"

    def test_from_dict_with_eligible_false(self):
        data = _liq_data(score=0.8, data_source="mock_fallback", eligible_for_trading=False)
        la = LiquidityAnalysis.from_dict(data)
        assert la.eligible_for_trading is False

    def test_from_dict_backward_compat_live(self):
        """eligible_for_trading がない旧データ: data_source=live → True に推論。"""
        data = {"score": 0.5, "data_source": "live", "verbal_annotation": "test"}
        la = LiquidityAnalysis.from_dict(data)
        assert la.eligible_for_trading is True

    def test_from_dict_backward_compat_mock_fallback(self):
        """eligible_for_trading がない旧データ: data_source=mock_fallback → False に推論。"""
        data = {"score": 0.8, "data_source": "mock_fallback", "verbal_annotation": "test"}
        la = LiquidityAnalysis.from_dict(data)
        assert la.eligible_for_trading is False

    def test_to_dict_includes_eligible_for_trading(self):
        la = LiquidityAnalysis(
            score=0.75, data_source="live", eligible_for_trading=True,
        )
        d = la.to_dict()
        assert "eligible_for_trading" in d
        assert d["eligible_for_trading"] is True

    def test_dataclass_default_is_false(self):
        """デフォルト値は False (安全側フォールバック)。"""
        la = LiquidityAnalysis()
        assert la.eligible_for_trading is False

    def test_roundtrip_live(self):
        la = LiquidityAnalysis(score=0.75, data_source="live", eligible_for_trading=True)
        restored = LiquidityAnalysis.from_dict(la.to_dict())
        assert restored.eligible_for_trading is True
        assert restored.score == pytest.approx(0.75)

    def test_roundtrip_mock_fallback(self):
        la = LiquidityAnalysis(score=0.8048, data_source="mock_fallback",
                               eligible_for_trading=False)
        restored = LiquidityAnalysis.from_dict(la.to_dict())
        assert restored.eligible_for_trading is False
        assert restored.score == pytest.approx(0.8048)  # raw score 保持
