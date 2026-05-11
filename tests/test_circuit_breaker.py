"""tests/test_circuit_breaker.py — CircuitBreaker のユニットテスト"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tools.circuit_breaker import CircuitBreaker, DAILY_DRAWDOWN_LIMIT, TOTAL_DRAWDOWN_LIMIT


@pytest.fixture
def tmp_state(tmp_path):
    return tmp_path / "cb_state.json"


class TestCircuitBreakerOpen:
    def test_initial_state_is_open(self, tmp_state):
        cb = CircuitBreaker(state_path=tmp_state)
        result = cb.check(current_equity=10000.0)
        assert result.status == "OPEN"
        assert result.buy_allowed

    def test_high_water_mark_updates(self, tmp_state):
        cb = CircuitBreaker(state_path=tmp_state)
        cb.check(10000.0)
        cb.check(10500.0)
        assert cb.state["high_water_mark"] == 10500.0


class TestSoftTrip:
    def test_soft_trip_triggers_at_threshold(self, tmp_state):
        cb = CircuitBreaker(state_path=tmp_state)
        cb.check(10000.0)
        drop = 10000.0 * (1 + DAILY_DRAWDOWN_LIMIT) - 1
        result = cb.check(drop)
        assert result.status == "SOFT_TRIP"
        assert not result.buy_allowed

    def test_soft_trip_persists_within_day(self, tmp_state):
        cb = CircuitBreaker(state_path=tmp_state)
        cb.check(10000.0)
        cb.check(9400.0)
        result = cb.check(9900.0)  # 回復しても SOFT_TRIP 維持
        assert result.status == "SOFT_TRIP"

    def test_manual_reset_soft(self, tmp_state):
        cb = CircuitBreaker(state_path=tmp_state)
        cb.check(10000.0)
        cb.check(9400.0)
        cb.manual_reset(level="soft")
        assert cb.status == "OPEN"


class TestHardTrip:
    def test_hard_trip_triggers_at_threshold(self, tmp_state):
        cb = CircuitBreaker(state_path=tmp_state)
        cb.check(10000.0)
        drop = 10000.0 * (1 + TOTAL_DRAWDOWN_LIMIT) - 1
        result = cb.check(drop)
        assert result.status == "HARD_TRIP"
        assert not result.buy_allowed

    def test_hard_trip_requires_manual_reset(self, tmp_state):
        cb = CircuitBreaker(state_path=tmp_state)
        cb.check(10000.0)
        cb.check(8900.0)
        assert cb.status == "HARD_TRIP"
        cb.manual_reset(level="soft")
        assert cb.status == "HARD_TRIP"  # soft では解除されない
        cb.manual_reset(level="hard")
        assert cb.status == "OPEN"
