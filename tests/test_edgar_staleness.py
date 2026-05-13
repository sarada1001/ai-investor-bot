"""tests/test_edgar_staleness.py — EDGAR 陳腐化チェック (H-5) のユニットテスト"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.fundamental_agent import (
    FundamentalAgent,
    _EDGAR_STALENESS_DAYS,
    _FETCH_LOG_PATH,
    _load_fetch_log,
    _save_fetch_log,
)


# ──────────────────────────────────────────────
# _load_fetch_log / _save_fetch_log
# ──────────────────────────────────────────────

class TestFetchLogIO:
    def test_load_returns_empty_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "agents.fundamental_agent._FETCH_LOG_PATH",
            tmp_path / "edgar_fetch_log.json",
        )
        # Import fresh to use patched path
        import importlib
        import agents.fundamental_agent as mod
        mod._FETCH_LOG_PATH = tmp_path / "edgar_fetch_log.json"
        assert mod._load_fetch_log() == {}

    def test_roundtrip(self, tmp_path, monkeypatch):
        log_path = tmp_path / "edgar_fetch_log.json"
        import agents.fundamental_agent as mod
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path
        try:
            data = {"AAPL": {"form": "10-Q", "fetched_at": "2024-01-01T00:00:00+00:00"}}
            mod._save_fetch_log(data)
            assert log_path.exists()
            loaded = mod._load_fetch_log()
            assert loaded == data
        finally:
            mod._FETCH_LOG_PATH = original

    def test_load_returns_empty_on_corrupt_json(self, tmp_path, monkeypatch):
        log_path = tmp_path / "bad.json"
        log_path.write_text("not json", encoding="utf-8")
        import agents.fundamental_agent as mod
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path
        try:
            assert mod._load_fetch_log() == {}
        finally:
            mod._FETCH_LOG_PATH = original


# ──────────────────────────────────────────────
# FundamentalAgent._is_edgar_stale
# ──────────────────────────────────────────────

class TestIsEdgarStale:
    def _agent(self) -> FundamentalAgent:
        agent = FundamentalAgent.__new__(FundamentalAgent)
        agent.persist_dir     = "chroma_db_saved"
        agent.collection_name = "financial_filings"
        agent.top_k           = 5
        agent._embeddings     = None
        agent._db             = None
        agent._llm            = None
        return agent

    def test_returns_false_when_no_log_entry(self, tmp_path, monkeypatch):
        import agents.fundamental_agent as mod
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = tmp_path / "edgar_fetch_log.json"
        try:
            agent = self._agent()
            assert agent._is_edgar_stale("AAPL") is False
        finally:
            mod._FETCH_LOG_PATH = original

    def test_returns_true_when_log_is_old(self, tmp_path, monkeypatch):
        import agents.fundamental_agent as mod
        log_path = tmp_path / "edgar_fetch_log.json"
        old_date = (datetime.now(timezone.utc) - timedelta(days=_EDGAR_STALENESS_DAYS + 1)).isoformat()
        log_path.write_text(json.dumps({"AAPL": {"form": "10-Q", "fetched_at": old_date}}))
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path
        try:
            agent = self._agent()
            assert agent._is_edgar_stale("AAPL") is True
        finally:
            mod._FETCH_LOG_PATH = original

    def test_returns_true_at_exact_staleness_boundary(self, tmp_path, monkeypatch):
        """Exactly _EDGAR_STALENESS_DAYS elapsed must count as stale (>= boundary)."""
        import agents.fundamental_agent as mod
        log_path = tmp_path / "edgar_fetch_log.json"
        # Use timedelta of exactly staleness_days full days
        boundary_date = (datetime.now(timezone.utc) - timedelta(days=_EDGAR_STALENESS_DAYS)).isoformat()
        log_path.write_text(json.dumps({"AAPL": {"form": "10-Q", "fetched_at": boundary_date}}))
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path
        try:
            agent = self._agent()
            # >= means exactly 90 days is stale
            assert agent._is_edgar_stale("AAPL") is True
        finally:
            mod._FETCH_LOG_PATH = original

    def test_returns_false_when_log_is_recent(self, tmp_path, monkeypatch):
        import agents.fundamental_agent as mod
        log_path = tmp_path / "edgar_fetch_log.json"
        recent = datetime.now(timezone.utc).isoformat()
        log_path.write_text(json.dumps({"NVDA": {"form": "10-Q", "fetched_at": recent}}))
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path
        try:
            agent = self._agent()
            assert agent._is_edgar_stale("NVDA") is False
        finally:
            mod._FETCH_LOG_PATH = original

    def test_ticker_case_insensitive(self, tmp_path, monkeypatch):
        import agents.fundamental_agent as mod
        log_path = tmp_path / "edgar_fetch_log.json"
        old_date = (datetime.now(timezone.utc) - timedelta(days=_EDGAR_STALENESS_DAYS + 5)).isoformat()
        log_path.write_text(json.dumps({"MSFT": {"form": "10-Q", "fetched_at": old_date}}))
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path
        try:
            agent = self._agent()
            assert agent._is_edgar_stale("msft") is True
        finally:
            mod._FETCH_LOG_PATH = original

    def test_returns_false_on_invalid_fetched_at(self, tmp_path, monkeypatch):
        import agents.fundamental_agent as mod
        log_path = tmp_path / "edgar_fetch_log.json"
        log_path.write_text(json.dumps({"TST": {"form": "10-Q", "fetched_at": "bad-date"}}))
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path
        try:
            agent = self._agent()
            assert agent._is_edgar_stale("TST") is False
        finally:
            mod._FETCH_LOG_PATH = original


# ──────────────────────────────────────────────
# FundamentalAgent._record_edgar_fetch
# ──────────────────────────────────────────────

class TestRecordEdgarFetch:
    def _agent(self) -> FundamentalAgent:
        agent = FundamentalAgent.__new__(FundamentalAgent)
        agent.persist_dir     = "chroma_db_saved"
        agent.collection_name = "financial_filings"
        agent.top_k           = 5
        agent._embeddings     = None
        agent._db             = None
        agent._llm            = None
        return agent

    def test_writes_entry_to_log(self, tmp_path, monkeypatch):
        import agents.fundamental_agent as mod
        log_path = tmp_path / "edgar_fetch_log.json"
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path
        try:
            agent = self._agent()
            agent._record_edgar_fetch("AAPL", "10-Q")
            log = json.loads(log_path.read_text())
            assert "AAPL" in log
            assert log["AAPL"]["form"] == "10-Q"
            assert "fetched_at" in log["AAPL"]
        finally:
            mod._FETCH_LOG_PATH = original

    def test_preserves_existing_entries(self, tmp_path, monkeypatch):
        import agents.fundamental_agent as mod
        log_path = tmp_path / "edgar_fetch_log.json"
        existing_ts = "2024-01-01T00:00:00+00:00"
        log_path.write_text(json.dumps({"NVDA": {"form": "10-Q", "fetched_at": existing_ts}}))
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path
        try:
            agent = self._agent()
            agent._record_edgar_fetch("AAPL", "10-Q")
            log = json.loads(log_path.read_text())
            assert "NVDA" in log   # existing entry preserved
            assert "AAPL" in log   # new entry added
        finally:
            mod._FETCH_LOG_PATH = original


# ──────────────────────────────────────────────
# analyze() staleness flow integration
# ──────────────────────────────────────────────

class TestAnalyseStalenessFlow:
    """Verify that analyze() triggers re-fetch when data is stale."""

    def _make_agent(self) -> FundamentalAgent:
        agent = FundamentalAgent.__new__(FundamentalAgent)
        agent.persist_dir     = "chroma_db_saved"
        agent.collection_name = "financial_filings"
        agent.top_k           = 5
        agent._embeddings     = None
        agent._db             = None
        agent._llm            = None
        return agent

    def test_stale_data_triggers_refetch(self, tmp_path, monkeypatch):
        """When chunks exist but data is stale, _fetch_from_edgar must be called."""
        import agents.fundamental_agent as mod
        log_path = tmp_path / "edgar_fetch_log.json"
        old_date = (datetime.now(timezone.utc) - timedelta(days=_EDGAR_STALENESS_DAYS + 1)).isoformat()
        log_path.write_text(json.dumps({"AAPL": {"form": "10-Q", "fetched_at": old_date}}))
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path

        agent = self._make_agent()

        fake_chunks = ["chunk1", "chunk2"]
        fetch_result_no_new = {"chunks_added": 0, "form": "10-Q", "error": None}
        rag_result = {
            "trend": "positive", "trend_reason": "test",
            "investment_signal": "BUY", "data_source": "RAG",
            "chunks_used": 2, "data_available": True, "fallback_used": False,
            "raw_llm_output": "",
        }

        with (
            patch.object(agent, "retrieve_chunks", return_value=fake_chunks),
            patch.object(agent, "_fetch_from_edgar", return_value=fetch_result_no_new) as mock_fetch,
            patch.object(agent, "_analyze_with_rag", return_value=rag_result),
        ):
            try:
                agent.analyze("AAPL")
            except Exception:
                pass
            mock_fetch.assert_called_once_with("AAPL")

        mod._FETCH_LOG_PATH = original

    def test_fresh_data_skips_refetch(self, tmp_path, monkeypatch):
        """When chunks exist and data is fresh, _fetch_from_edgar must NOT be called."""
        import agents.fundamental_agent as mod
        log_path = tmp_path / "edgar_fetch_log.json"
        recent = datetime.now(timezone.utc).isoformat()
        log_path.write_text(json.dumps({"AAPL": {"form": "10-Q", "fetched_at": recent}}))
        original = mod._FETCH_LOG_PATH
        mod._FETCH_LOG_PATH = log_path

        agent = self._make_agent()

        fake_chunks = ["chunk1"]
        rag_result = {
            "trend": "neutral", "trend_reason": "test",
            "investment_signal": "HOLD", "data_source": "RAG",
            "chunks_used": 1, "data_available": True, "fallback_used": False,
            "raw_llm_output": "",
        }

        with (
            patch.object(agent, "retrieve_chunks", return_value=fake_chunks),
            patch.object(agent, "_fetch_from_edgar") as mock_fetch,
            patch.object(agent, "_analyze_with_rag", return_value=rag_result),
        ):
            try:
                agent.analyze("AAPL")
            except Exception:
                pass
            mock_fetch.assert_not_called()

        mod._FETCH_LOG_PATH = original
