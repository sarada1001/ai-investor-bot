"""tests/test_alpaca_client_limit.py — 指値注文ロジックのユニットテスト"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools.alpaca_client import LIMIT_SPREAD_PCT


# ──────────────────────────────────────────────
# 指値価格計算
# ──────────────────────────────────────────────

class TestLimitPriceCalc:
    def test_buy_limit_price_is_above_current(self):
        """買い指値 = 現在価格 × (1 + LIMIT_SPREAD_PCT)"""
        current = 100.0
        limit   = round(current * (1 + LIMIT_SPREAD_PCT), 2)
        assert limit > current
        assert limit == pytest.approx(100.50, abs=0.01)

    def test_sell_limit_price_is_below_current(self):
        """売り指値 = 現在価格 × (1 - LIMIT_SPREAD_PCT)"""
        current = 100.0
        limit   = round(current * (1 - LIMIT_SPREAD_PCT), 2)
        assert limit < current
        assert limit == pytest.approx(99.50, abs=0.01)

    def test_spread_is_half_percent(self):
        assert LIMIT_SPREAD_PCT == 0.005


# ──────────────────────────────────────────────
# AlpacaClient フィクスチャ
# ──────────────────────────────────────────────

@pytest.fixture
def client():
    """AlpacaClient を TradingClient なしでインスタンス化するフィクスチャ。"""
    with patch("tools.alpaca_client._API_KEY", "TEST_KEY"), \
         patch("tools.alpaca_client._SECRET_KEY", "TEST_SECRET"):
        from tools.alpaca_client import AlpacaClient
        c = AlpacaClient.__new__(AlpacaClient)
        c._tc = MagicMock()
        return c


def _make_order_mock(
    order_id:          str   = "test-id",
    status:            str   = "accepted",
    symbol:            str   = "AAPL",
    qty:               str   = "10",
    filled_qty:        str   = "10",
    filled_avg_price:  str   = "100.50",
) -> MagicMock:
    order = MagicMock()
    order.id                = order_id
    order.status            = status
    order.symbol            = symbol
    order.qty               = qty
    order.filled_qty        = filled_qty
    order.filled_avg_price  = filled_avg_price
    order.submitted_at      = None
    return order


# ──────────────────────────────────────────────
# _submit_limit_order
# ──────────────────────────────────────────────

class TestSubmitLimitOrder:
    def test_buy_limit_order_success(self, client):
        """place_buy は指値注文を送信し order_type='limit' を返す"""
        with patch.object(client, "_fetch_current_price", return_value=100.0):
            client._tc.submit_order.return_value = _make_order_mock(
                filled_qty="10", filled_avg_price="100.50",
            )
            result = client._submit_limit_order("AAPL", 10, "buy")

        assert result["success"] is True
        assert result["order_type"] == "limit"
        assert result["limit_price"] == pytest.approx(100.50, abs=0.01)
        assert result["current_price"] == 100.0
        assert result["side"] == "buy"

    def test_sell_limit_price_below_current(self, client):
        """売り指値は現在価格 × 0.995"""
        with patch.object(client, "_fetch_current_price", return_value=200.0):
            client._tc.submit_order.return_value = _make_order_mock(
                symbol="MSFT", qty="5", filled_qty="5", filled_avg_price="199.0",
            )
            result = client._submit_limit_order("MSFT", 5, "sell")

        assert result["success"] is True
        assert result["limit_price"] == pytest.approx(199.0, abs=0.01)

    def test_partial_fill_detected(self, client):
        """filled_qty < requested_qty の場合 partial_fill=True"""
        with patch.object(client, "_fetch_current_price", return_value=50.0):
            client._tc.submit_order.return_value = _make_order_mock(
                status="partially_filled",
                qty="20", filled_qty="5",
            )
            result = client._submit_limit_order("AMD", 20, "buy")

        assert result["partial_fill"] is True
        assert result["filled_qty"]    == pytest.approx(5.0)
        assert result["requested_qty"] == pytest.approx(20.0)

    def test_no_partial_fill_when_fully_filled(self, client):
        """全株約定時は partial_fill=False"""
        with patch.object(client, "_fetch_current_price", return_value=50.0):
            client._tc.submit_order.return_value = _make_order_mock(
                qty="5", filled_qty="5",
            )
            result = client._submit_limit_order("AMD", 5, "buy")

        assert result["partial_fill"] is False
        assert result["filled_qty"] == result["requested_qty"]

    def test_partial_fill_false_when_filled_qty_is_zero(self, client):
        """filled_qty=0 の場合は部分約定ではなく未約定扱い"""
        with patch.object(client, "_fetch_current_price", return_value=100.0):
            client._tc.submit_order.return_value = _make_order_mock(
                status="accepted", qty="10", filled_qty="0",
                filled_avg_price=None,
            )
            result = client._submit_limit_order("AAPL", 10, "buy")

        # 0 < filled_qty(0) < requested_qty(10) は False なので partial_fill=False
        assert result["partial_fill"] is False

    def test_fallback_to_market_on_price_failure(self, client):
        """現在価格取得失敗時は成行注文にフォールバック"""
        with patch.object(client, "_fetch_current_price", return_value=None):
            with patch.object(
                client, "_submit_market_order_fallback",
                return_value={"success": True, "order_type": "market"},
            ) as mock_market:
                client._submit_limit_order("FAIL", 5, "buy")
                mock_market.assert_called_once_with("FAIL", 5, "buy")

    def test_alpaca_api_error_returns_failure(self, client):
        """Alpaca API エラー時は success=False を返す"""
        with patch.object(client, "_fetch_current_price", return_value=100.0):
            client._tc.submit_order.side_effect = RuntimeError("API error")
            result = client._submit_limit_order("NVDA", 3, "buy")

        assert result["success"] is False
        assert "error" in result

    def test_result_contains_required_keys(self, client):
        """成功結果に必須キーが含まれる"""
        with patch.object(client, "_fetch_current_price", return_value=150.0):
            client._tc.submit_order.return_value = _make_order_mock()
            result = client._submit_limit_order("TSLA", 2, "buy")

        required_keys = {
            "success", "order_type", "limit_price", "current_price",
            "requested_qty", "filled_qty", "partial_fill", "side",
        }
        assert required_keys.issubset(result.keys())


# ──────────────────────────────────────────────
# _submit_market_order_fallback
# ──────────────────────────────────────────────

class TestMarketOrderFallback:
    def test_fallback_returns_market_type(self, client):
        """フォールバック成行注文は order_type='market' を返す"""
        client._tc.submit_order.return_value = _make_order_mock()
        result = client._submit_market_order_fallback("AAPL", 5, "buy")

        assert result["success"] is True
        assert result["order_type"] == "market"
        assert result["limit_price"] is None

    def test_fallback_partial_fill_always_false(self, client):
        """成行注文のフォールバックは partial_fill=False（即時全数約定想定）"""
        client._tc.submit_order.return_value = _make_order_mock(
            qty="10", filled_qty="10",
        )
        result = client._submit_market_order_fallback("AAPL", 10, "buy")
        assert result["partial_fill"] is False
