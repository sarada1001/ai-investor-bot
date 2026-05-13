"""tests/test_monitor.py — monitor.py ヘルパー関数のユニットテスト

外部ネットワーク・yfinance は一切呼び出さない。
Rich パネルビルダーが各データ形式で正常動作することを検証する。
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from io import StringIO

import monitor

pytestmark = pytest.mark.unit


def _render(renderable) -> str:
    """Rich オブジェクトをプレーンテキストにレンダリングして返す。"""
    buf = StringIO()
    con = Console(file=buf, highlight=False, markup=False, no_color=True, width=120)
    con.print(renderable)
    return buf.getvalue()


# ──────────────────────────────────────────────
# _load_json
# ──────────────────────────────────────────────

class TestLoadJson:
    def test_loads_valid_json(self, tmp_path: Path):
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        with patch.object(monitor, "_PORTFOLIO_PATH", f):
            result = monitor._load_json(f)
        assert result == {"key": "value"}

    def test_missing_file_returns_empty_dict(self, tmp_path: Path):
        f = tmp_path / "nonexistent.json"
        result = monitor._load_json(f)
        assert result == {}

    def test_corrupt_json_returns_empty_dict(self, tmp_path: Path):
        f = tmp_path / "bad.json"
        f.write_text("{corrupt json", encoding="utf-8")
        result = monitor._load_json(f)
        assert result == {}


# ──────────────────────────────────────────────
# _last_n_training
# ──────────────────────────────────────────────

class TestLastNTraining:
    def _write_records(self, path: Path, records: list[dict]) -> None:
        path.write_text(
            "\n".join(json.dumps(r) for r in records),
            encoding="utf-8",
        )

    def test_returns_last_n_records(self, tmp_path: Path):
        path = tmp_path / "training.jsonl"
        records = [{"id": i, "ticker": "AAPL"} for i in range(10)]
        self._write_records(path, records)

        with patch.object(monitor, "_TRAINING_DATA", path):
            result = monitor._last_n_training(3)

        assert len(result) == 3
        assert result[0]["id"] == 9  # 最新が先頭

    def test_missing_file_returns_empty(self, tmp_path: Path):
        with patch.object(monitor, "_TRAINING_DATA", tmp_path / "missing.jsonl"):
            result = monitor._last_n_training(5)
        assert result == []

    def test_returns_all_when_fewer_than_n(self, tmp_path: Path):
        path = tmp_path / "training.jsonl"
        self._write_records(path, [{"id": 0}, {"id": 1}])
        with patch.object(monitor, "_TRAINING_DATA", path):
            result = monitor._last_n_training(5)
        assert len(result) == 2


# ──────────────────────────────────────────────
# _portfolio_panel
# ──────────────────────────────────────────────

class TestPortfolioPanel:
    def _portfolio_json(self, positions: list[dict]) -> dict:
        return {"schema_version": "1.0", "positions": positions}

    def test_no_open_positions_shows_none_message(self, tmp_path: Path):
        path = tmp_path / "portfolio.json"
        path.write_text(
            json.dumps(self._portfolio_json([])), encoding="utf-8"
        )
        with patch.object(monitor, "_PORTFOLIO_PATH", path), \
             patch.object(monitor, "_fetch_prices", return_value={}):
            panel = monitor._portfolio_panel()
        assert "なし" in _render(panel)

    def test_open_position_renders_table(self, tmp_path: Path):
        path = tmp_path / "portfolio.json"
        pos = {
            "ticker": "AAPL", "entry_price": 100.0, "shares": 10,
            "status": "OPEN", "target_price": 110.0, "stop_loss_price": 95.0,
        }
        path.write_text(json.dumps(self._portfolio_json([pos])), encoding="utf-8")
        with patch.object(monitor, "_PORTFOLIO_PATH", path), \
             patch.object(monitor, "_fetch_prices", return_value={"AAPL": 105.0}):
            panel = monitor._portfolio_panel()
        panel_str = _render(panel)
        assert "AAPL" in panel_str

    def test_closed_position_excluded(self, tmp_path: Path):
        path = tmp_path / "portfolio.json"
        pos_closed = {
            "ticker": "TSLA", "entry_price": 200.0, "shares": 5,
            "status": "CLOSED",
        }
        pos_open = {
            "ticker": "AAPL", "entry_price": 100.0, "shares": 10,
            "status": "OPEN",
        }
        path.write_text(
            json.dumps(self._portfolio_json([pos_closed, pos_open])),
            encoding="utf-8",
        )
        with patch.object(monitor, "_PORTFOLIO_PATH", path), \
             patch.object(monitor, "_fetch_prices", return_value={"AAPL": 100.0}):
            panel = monitor._portfolio_panel()
        panel_str = _render(panel)
        assert "AAPL" in panel_str
        assert "TSLA" not in panel_str

    def test_missing_portfolio_file(self, tmp_path: Path):
        with patch.object(monitor, "_PORTFOLIO_PATH", tmp_path / "missing.json"):
            panel = monitor._portfolio_panel()
        assert panel is not None


# ──────────────────────────────────────────────
# _agent_health_panel
# ──────────────────────────────────────────────

class TestAgentHealthPanel:
    def _agent_json(self, last_evaluated: str, status: str = "ACTIVE") -> dict:
        return {
            "TestAgent": {
                "status": status,
                "win_rate": 0.6,
                "total_trades": 10,
                "last_evaluated": last_evaluated,
            }
        }

    def test_active_agent_shows_active_badge(self, tmp_path: Path):
        path = tmp_path / "agent_status.json"
        now  = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(self._agent_json(now)), encoding="utf-8")
        with patch.object(monitor, "_AGENT_STATUS_PATH", path):
            panel = monitor._agent_health_panel()
        assert "ACTIVE" in _render(panel)

    def test_stale_agent_shows_stale_badge(self, tmp_path: Path):
        path = tmp_path / "agent_status.json"
        old  = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        path.write_text(json.dumps(self._agent_json(old)), encoding="utf-8")
        with patch.object(monitor, "_AGENT_STATUS_PATH", path):
            panel = monitor._agent_health_panel()
        assert "STALE" in _render(panel)

    def test_suspended_agent_shows_suspended(self, tmp_path: Path):
        path = tmp_path / "agent_status.json"
        now  = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(self._agent_json(now, "SUSPENDED")), encoding="utf-8")
        with patch.object(monitor, "_AGENT_STATUS_PATH", path):
            panel = monitor._agent_health_panel()
        assert "SUSPENDED" in _render(panel)

    def test_missing_status_file(self, tmp_path: Path):
        with patch.object(monitor, "_AGENT_STATUS_PATH", tmp_path / "missing.json"):
            panel = monitor._agent_health_panel()
        assert panel is not None


# ──────────────────────────────────────────────
# _screener_panel
# ──────────────────────────────────────────────

class TestScreenerPanel:
    def test_no_cache_file(self, tmp_path: Path):
        with patch.object(monitor, "_INTRADAY_CACHE", tmp_path / "missing.json"):
            panel = monitor._screener_panel()
        assert "なし" in _render(panel)

    def test_renders_ticker_from_cache(self, tmp_path: Path):
        path = tmp_path / "intraday_cache.json"
        data = {
            "session_key": "2026-05-13_10_00",
            "results": [
                {"ticker": "NVDA", "score": 10, "rsi": 35.0,
                 "intraday_momentum_pct": 2.5, "intraday_vol_spike": 2.0},
            ],
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(monitor, "_INTRADAY_CACHE", path):
            panel = monitor._screener_panel()
        assert "NVDA" in _render(panel)

    def test_limits_to_8_rows(self, tmp_path: Path):
        path = tmp_path / "intraday_cache.json"
        data = {
            "session_key": "2026-05-13_10_00",
            "results": [
                {"ticker": f"T{i:02d}", "score": i, "rsi": 50.0,
                 "intraday_momentum_pct": 0.0, "intraday_vol_spike": 1.0}
                for i in range(12)
            ],
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(monitor, "_INTRADAY_CACHE", path):
            panel = monitor._screener_panel()
        panel_str = _render(panel)
        # T09 以降（9番目以降）は表示されない
        assert "T11" not in panel_str


# ──────────────────────────────────────────────
# _decisions_panel
# ──────────────────────────────────────────────

class TestDecisionsPanel:
    def test_no_training_data(self, tmp_path: Path):
        with patch.object(monitor, "_TRAINING_DATA", tmp_path / "missing.jsonl"):
            panel = monitor._decisions_panel()
        assert "なし" in _render(panel)

    def test_renders_decision_from_record(self, tmp_path: Path):
        path = tmp_path / "training.jsonl"
        record = {
            "ticker": "AAPL",
            "date":   "2026-05-13",
            "manager_chain_of_thought": {"weighted_score": 0.7, "rationale": "Strong buy"},
            "manager_output": {"decision": "STRONG BUY", "score": 0.7},
        }
        path.write_text(json.dumps(record), encoding="utf-8")
        with patch.object(monitor, "_TRAINING_DATA", path):
            panel = monitor._decisions_panel()
        assert "AAPL" in _render(panel)
