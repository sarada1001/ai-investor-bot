"""tests/test_trade_guard.py — TradeGuard のユニットテスト"""

from __future__ import annotations

import pytest

from tools.trade_guard import TradeGuard


@pytest.fixture
def guard(tmp_path):
    config = tmp_path / "trade_guards.json"
    state  = tmp_path / "state.json"
    config.write_text('{"max_daily_buys": 3, "max_positions": 5, "max_position_pct": 0.20}')
    return TradeGuard(config_path=config, state_path=state)


class TestPreBuyChecks:
    def test_allows_valid_order(self, guard):
        result = guard.check_pre_buy(
            ticker="AAPL", order_value=1000.0,
            account_equity=10000.0, open_positions=2,
        )
        assert result.allowed

    def test_blocks_at_daily_limit(self, guard):
        for t in ["AAPL", "MSFT", "NVDA"]:
            guard.record_buy(t)
        result = guard.check_pre_buy(
            ticker="TSLA", order_value=1000.0,
            account_equity=10000.0, open_positions=3,
        )
        assert not result.allowed
        assert "日次" in result.reason

    def test_blocks_at_max_positions(self, guard):
        result = guard.check_pre_buy(
            ticker="AAPL", order_value=1000.0,
            account_equity=10000.0, open_positions=5,
        )
        assert not result.allowed
        assert "銘柄" in result.reason

    def test_blocks_oversized_position(self, guard):
        result = guard.check_pre_buy(
            ticker="AAPL", order_value=2500.0,
            account_equity=10000.0, open_positions=2,
        )
        assert not result.allowed
        assert "比率" in result.reason

    def test_record_buy_increments_count(self, guard):
        assert guard.daily_buy_count == 0
        guard.record_buy("AAPL")
        assert guard.daily_buy_count == 1
        guard.record_buy("MSFT")
        assert guard.daily_buy_count == 2
