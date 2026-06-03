"""tests/test_live_trading_gate.py — LiveTradingGate のユニットテスト"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import tools.live_trading_gate as ltg_mod


class TestLiveTradingGateCheck:
    """check() の判定ロジックをテストする。"""

    def test_paper_mode_always_allowed(self):
        """ペーパーモード（_IS_PAPER=True）では常に通過すること。"""
        with patch.object(ltg_mod, "_IS_PAPER", True):
            result = ltg_mod.LiveTradingGate().check()
        assert result.allowed
        assert result.is_live is False
        assert "ペーパー" in result.reason

    def test_live_mode_with_api_key_allowed(self):
        """ライブモードでAPIキーが設定されていれば通過すること。"""
        with (
            patch.object(ltg_mod, "_IS_PAPER", False),
            patch.object(ltg_mod, "_API_KEY", "ABCDEFGHIJ1234"),
            patch.object(ltg_mod, "_KEY_SUFFIX", "1234"),
        ):
            result = ltg_mod.LiveTradingGate().check()
        assert result.allowed
        assert result.is_live is True
        assert "1234" in result.reason

    def test_live_mode_without_api_key_blocked(self):
        """ライブモードでAPIキーが未設定の場合はブロックされること。"""
        with (
            patch.object(ltg_mod, "_IS_PAPER", False),
            patch.object(ltg_mod, "_API_KEY", ""),
        ):
            result = ltg_mod.LiveTradingGate().check()
        assert not result.allowed
        assert result.is_live is True
        assert "未設定" in result.reason or "API" in result.reason

    def test_live_mode_no_intent_file_required(self, tmp_path):
        """ライブモードで意思ファイルが存在しなくても通過できること（旧24h制約なし）。"""
        nonexistent = tmp_path / "live_trading_enabled.json"
        assert not nonexistent.exists()
        with (
            patch.object(ltg_mod, "_IS_PAPER", False),
            patch.object(ltg_mod, "_API_KEY", "TESTKEY9999"),
            patch.object(ltg_mod, "_KEY_SUFFIX", "9999"),
            patch.object(ltg_mod, "_INTENT_PATH", nonexistent),
        ):
            result = ltg_mod.LiveTradingGate().check()
        assert result.allowed

    def test_result_contains_key_suffix(self):
        """通過時の result に key_suffix が含まれること。"""
        with (
            patch.object(ltg_mod, "_IS_PAPER", False),
            patch.object(ltg_mod, "_API_KEY", "XYZW5678"),
            patch.object(ltg_mod, "_KEY_SUFFIX", "5678"),
        ):
            result = ltg_mod.LiveTradingGate().check()
        assert result.key_suffix == "5678"
