"""tests/test_critic_agent_fallback.py — CriticAgent Gemini フォールバックのユニットテスト"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests as req_lib

from tools.critic_agent import CriticAgent, _FALLBACK_RESPONSE


# ──────────────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────────────

@pytest.fixture
def agent():
    return CriticAgent()


def _valid_ollama_response(decision: str = "APPROVE") -> MagicMock:
    body = (
        f'{{"critic_decision": "{decision}", '
        f'"revised_action": "BUY", '
        f'"critique_reason": "test reason"}}'
    )
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": body}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ──────────────────────────────────────────────
# Ollama 成功時
# ──────────────────────────────────────────────

class TestOllamaSuccess:
    def test_ollama_success_returns_decision(self, agent):
        """Ollama が正常応答した場合は正しい判定を返す"""
        with patch("tools.critic_agent.requests.post",
                   return_value=_valid_ollama_response("APPROVE")):
            result = agent.evaluate_trade("AAPL", "BUY", "strong signals", "no rules")

        assert result["critic_decision"] == "APPROVE"
        assert result["revised_action"]  == "BUY"

    def test_ollama_success_does_not_call_gemini(self, agent):
        """Ollama 成功時は Gemini を呼び出さない"""
        with patch("tools.critic_agent.requests.post",
                   return_value=_valid_ollama_response()):
            with patch.object(agent, "_call_gemini") as mock_gemini:
                agent.evaluate_trade("NVDA", "BUY", "context", "rules")
                mock_gemini.assert_not_called()

    def test_llm_source_is_ollama(self, agent):
        """Ollama 成功時は llm_source='ollama'"""
        with patch("tools.critic_agent.requests.post",
                   return_value=_valid_ollama_response()):
            result = agent.evaluate_trade("MSFT", "HOLD", "context", "rules")
        assert result.get("llm_source") == "ollama"


# ──────────────────────────────────────────────
# Ollama 失敗 → Gemini フォールバック
# ──────────────────────────────────────────────

class TestGeminiFallback:
    def _gemini_raw(self, decision: str = "APPROVE") -> str:
        return (
            f'{{"critic_decision": "{decision}", '
            f'"revised_action": "BUY", '
            f'"critique_reason": "gemini fallback reason"}}'
        )

    def test_connection_error_triggers_gemini(self, agent):
        """ConnectionError → Gemini に自動切替"""
        with patch("tools.critic_agent.requests.post",
                   side_effect=req_lib.exceptions.ConnectionError("Ollama down")):
            with patch.object(agent, "_call_gemini",
                               return_value=self._gemini_raw("APPROVE")):
                result = agent.evaluate_trade("TSLA", "BUY", "context", "rules")

        assert result["critic_decision"] == "APPROVE"
        assert result.get("llm_source")  == "gemini"

    def test_timeout_triggers_gemini(self, agent):
        """Timeout → Gemini に自動切替"""
        with patch("tools.critic_agent.requests.post",
                   side_effect=req_lib.exceptions.Timeout("timeout")):
            with patch.object(agent, "_call_gemini",
                               return_value=self._gemini_raw("OVERRIDE")):
                result = agent.evaluate_trade("AMD", "BUY", "context", "rules")

        assert result["critic_decision"] == "OVERRIDE"
        assert result.get("llm_source")  == "gemini"

    def test_http_error_triggers_gemini(self, agent):
        """HTTPError → Gemini に自動切替"""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req_lib.exceptions.HTTPError("503")
        with patch("tools.critic_agent.requests.post", return_value=mock_resp):
            with patch.object(agent, "_call_gemini",
                               return_value=self._gemini_raw("APPROVE")):
                result = agent.evaluate_trade("META", "HOLD", "context", "rules")

        assert result.get("llm_source") == "gemini"

    def test_gemini_result_parsed_correctly(self, agent):
        """Gemini 応答を正しくパースする"""
        gemini_json = (
            '{"critic_decision": "OVERRIDE", '
            '"revised_action": "HOLD", '
            '"critique_reason": "RSI is too high"}'
        )
        with patch("tools.critic_agent.requests.post",
                   side_effect=req_lib.exceptions.ConnectionError()):
            with patch.object(agent, "_call_gemini", return_value=gemini_json):
                result = agent.evaluate_trade("COIN", "BUY", "ctx", "rules")

        assert result["critic_decision"] == "OVERRIDE"
        assert result["revised_action"]  == "HOLD"
        assert result["critique_reason"] == "RSI is too high"


# ──────────────────────────────────────────────
# 全バックエンド失敗 → フォールバック
# ──────────────────────────────────────────────

class TestFullFallback:
    def test_both_fail_returns_fallback(self, agent):
        """Ollama・Gemini 両方が失敗した場合は _FALLBACK_RESPONSE を返す"""
        with patch("tools.critic_agent.requests.post",
                   side_effect=req_lib.exceptions.ConnectionError()):
            with patch.object(agent, "_call_gemini", return_value=None):
                result = agent.evaluate_trade("XOM", "SELL", "ctx", "rules")

        assert result["critic_decision"] == _FALLBACK_RESPONSE["critic_decision"]
        assert result["revised_action"]  == _FALLBACK_RESPONSE["revised_action"]

    def test_fallback_response_has_required_keys(self, agent):
        """フォールバック応答は必須キーを持つ"""
        with patch("tools.critic_agent.requests.post",
                   side_effect=req_lib.exceptions.ConnectionError()):
            with patch.object(agent, "_call_gemini", return_value=None):
                result = agent.evaluate_trade("JPM", "BUY", "ctx", "rules")

        assert "critic_decision" in result
        assert "revised_action"  in result
        assert "critique_reason" in result


# ──────────────────────────────────────────────
# _call_gemini の JSON 前処理
# ──────────────────────────────────────────────

class TestCallGeminiParsing:
    def test_gemini_strips_markdown_code_block(self, agent):
        """```json ... ``` ブロックを除去してJSONのみ返す"""
        raw_with_fences = (
            "```json\n"
            '{"critic_decision": "APPROVE", "revised_action": "BUY", "critique_reason": "ok"}\n'
            "```"
        )
        # _call_gemini をラップして前処理を検証
        parsed = agent._parse_response(
            raw_with_fences.replace("```json", "").replace("```", "").strip()
        )
        assert parsed["critic_decision"] == "APPROVE"

    def test_parse_response_invalid_json_returns_fallback(self, agent):
        """不正JSON はフォールバックを返す"""
        result = agent._parse_response("not json at all {{{")
        assert result["critic_decision"] == _FALLBACK_RESPONSE["critic_decision"]

    def test_parse_response_missing_key_returns_fallback(self, agent):
        """必須キー欠損はフォールバックを返す"""
        result = agent._parse_response('{"critic_decision": "APPROVE"}')
        assert result["critic_decision"] == _FALLBACK_RESPONSE["critic_decision"]
