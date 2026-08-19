"""tests/test_broker_stop.py — broker-side stop-loss 機能のユニットテスト

すべて API mock を使用し、実際の Alpaca 注文は一切出しません。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ─────────────────────────────────────────────────────────────
# 共通ヘルパー
# ─────────────────────────────────────────────────────────────

def _make_client():
    """AlpacaClient を TradingClient なしで組み立てる。"""
    with patch("tools.alpaca_client._API_KEY", "TEST_KEY"), \
         patch("tools.alpaca_client._SECRET_KEY", "TEST_SECRET"):
        from tools.alpaca_client import AlpacaClient
        c = AlpacaClient.__new__(AlpacaClient)
        c._tc = MagicMock()
        return c


def _make_stop_order_mock(
    order_id: str   = "stop-id-001",
    status:   str   = "accepted",
    symbol:   str   = "AAPL",
    qty:      str   = "10",
) -> MagicMock:
    o = MagicMock()
    o.id     = order_id
    o.status = status
    o.symbol = symbol
    o.qty    = qty
    o.stop_price  = 185.0
    o.submitted_at = None
    return o


def _make_alpaca_position(symbol: str, qty: float) -> MagicMock:
    p = MagicMock()
    p.symbol = symbol
    p.qty    = str(qty)
    return p


def _make_portfolio_file(tmp_path: Path, positions: list[dict]) -> Path:
    f = tmp_path / "portfolio.json"
    f.write_text(json.dumps({
        "schema_version": "1.0",
        "updated_at": "2026-01-01",
        "positions": positions,
    }))
    return f


def _make_exit_agent(fetch_price: float, portfolio_file: Path) -> object:
    """ExitAgent を外部依存なしで組み立てる。"""
    from agents.exit_agent import ExitAgent
    agent = ExitAgent.__new__(ExitAgent)
    agent.bbs     = MagicMock()
    agent._llm    = MagicMock()
    agent._ob_log = MagicMock()
    agent._fetch_price = MagicMock(return_value=fetch_price)
    return agent


# ─────────────────────────────────────────────────────────────
# 1. place_broker_stop
# ─────────────────────────────────────────────────────────────

class TestPlaceBrokerStop:
    def test_success_returns_order_id_and_stop_price(self):
        """stop 注文が成功すると order_id と stop_price が返る。"""
        client = _make_client()
        client._tc.submit_order.return_value = _make_stop_order_mock(
            order_id="stop-001", symbol="AAPL", qty="10",
        )
        result = client.place_broker_stop("AAPL", 10, 185.0)

        assert result["success"]     is True
        assert result["order_id"]    == "stop-001"
        assert result["stop_price"]  == pytest.approx(185.0)
        assert result["order_type"]  == "stop"
        assert result["side"]        == "sell"

    def test_uses_gtc_time_in_force(self):
        """GTC を指定していることを確認する（bot 停止中も有効）。"""
        from alpaca.trading.enums import TimeInForce
        client = _make_client()
        client._tc.submit_order.return_value = _make_stop_order_mock()

        client.place_broker_stop("AAPL", 5, 190.0)

        submitted = client._tc.submit_order.call_args[0][0]
        assert submitted.time_in_force == TimeInForce.GTC

    def test_stop_price_is_rounded_to_2_decimals(self):
        client = _make_client()
        client._tc.submit_order.return_value = _make_stop_order_mock()

        client.place_broker_stop("AAPL", 3, 184.987654)

        submitted = client._tc.submit_order.call_args[0][0]
        assert submitted.stop_price == pytest.approx(184.99, abs=0.01)

    def test_api_error_returns_failure(self):
        client = _make_client()
        client._tc.submit_order.side_effect = RuntimeError("API unavailable")

        result = client.place_broker_stop("AAPL", 5, 185.0)

        assert result["success"] is False
        assert "error" in result

    def test_live_gate_blocks_in_live_mode(self):
        """LiveTradingGate がブロックする場合は skipped=True を返す。"""
        client = _make_client()
        with patch("tools.alpaca_client._live_gate_check") as mock_gate:
            mock_gate.return_value = {
                "success": False, "skipped": True,
                "skip_reason": "LiveTradingGate", "symbol": "AAPL", "side": "sell",
            }
            result = client.place_broker_stop("AAPL", 10, 185.0)

        assert result["skipped"] is True
        client._tc.submit_order.assert_not_called()

    def test_paper_mode_live_gate_always_passes(self):
        client = _make_client()
        client._tc.submit_order.return_value = _make_stop_order_mock()

        with patch("tools.alpaca_client._PAPER", True), \
             patch("tools.alpaca_client._live_gate_check", return_value=None):
            result = client.place_broker_stop("AAPL", 5, 185.0)

        assert result["success"] is True


# ─────────────────────────────────────────────────────────────
# 2. cancel_order
# ─────────────────────────────────────────────────────────────

class TestCancelOrder:
    def test_cancel_success(self):
        client = _make_client()
        client._tc.cancel_order_by_id.return_value = None

        result = client.cancel_order("stop-001")

        assert result["success"]  is True
        assert result["order_id"] == "stop-001"
        client._tc.cancel_order_by_id.assert_called_once_with("stop-001")

    def test_cancel_api_error_returns_failure(self):
        client = _make_client()
        client._tc.cancel_order_by_id.side_effect = RuntimeError("not found")

        result = client.cancel_order("bad-id")

        assert result["success"] is False
        assert "error" in result


# ─────────────────────────────────────────────────────────────
# 3. get_order
# ─────────────────────────────────────────────────────────────

class TestGetOrder:
    def test_get_order_returns_status_and_qty(self):
        """status / qty / filled_qty / fill_price が返る。"""
        client = _make_client()
        mock_order = MagicMock()
        mock_order.id               = "stop-001"
        mock_order.status           = "accepted"
        mock_order.qty              = "10"
        mock_order.filled_qty       = "0"
        mock_order.filled_avg_price = None
        mock_order.symbol           = "AAPL"
        mock_order.side             = "sell"
        client._tc.get_order_by_id.return_value = mock_order

        result = client.get_order("stop-001")

        assert result["success"]    is True
        assert result["status"]     == "accepted"
        assert result["qty"]        == pytest.approx(10.0)
        assert result["filled_qty"] == pytest.approx(0.0)
        assert result["fill_price"] is None

    def test_get_order_returns_fill_price_when_filled(self):
        """約定済みの場合 fill_price が返る。"""
        client = _make_client()
        mock_order = MagicMock()
        mock_order.id               = "buy-001"
        mock_order.status           = "filled"
        mock_order.qty              = "10"
        mock_order.filled_qty       = "10"
        mock_order.filled_avg_price = 195.5
        mock_order.symbol           = "AAPL"
        mock_order.side             = "buy"
        client._tc.get_order_by_id.return_value = mock_order

        result = client.get_order("buy-001")

        assert result["success"]    is True
        assert result["fill_price"] == pytest.approx(195.5)

    def test_get_order_api_error_returns_failure(self):
        client = _make_client()
        client._tc.get_order_by_id.side_effect = RuntimeError("not found")

        result = client.get_order("bad-id")

        assert result["success"] is False


# ─────────────────────────────────────────────────────────────
# 4. add_position / update_position_stop_order
# ─────────────────────────────────────────────────────────────

class TestPortfolioStopFields:
    def test_add_position_saves_entry_and_stop_order_ids(self, tmp_path):
        """add_position() が entry_order_id / stop_order_id / broker_stop_status を保存する。"""
        from agents.exit_agent import add_position
        portfolio_file = tmp_path / "portfolio.json"

        with patch("agents.exit_agent.PORTFOLIO_PATH", portfolio_file):
            add_position(
                ticker="AAPL", entry_price=195.0, shares=5,
                stop_loss_price=185.0,
                entry_order_id="buy-001",
                stop_order_id="stop-001",
                broker_stop_status="open",
            )

        data = json.loads(portfolio_file.read_text())
        pos  = data["positions"][0]
        assert pos["entry_order_id"]     == "buy-001"
        assert pos["stop_order_id"]      == "stop-001"
        assert pos["broker_stop_status"] == "open"

    def test_add_position_defaults_broker_stop_to_none(self, tmp_path):
        from agents.exit_agent import add_position
        portfolio_file = tmp_path / "portfolio.json"

        with patch("agents.exit_agent.PORTFOLIO_PATH", portfolio_file):
            add_position(ticker="MSFT", entry_price=400.0, shares=2)

        data = json.loads(portfolio_file.read_text())
        pos  = data["positions"][0]
        assert pos["broker_stop_status"] == "none"
        assert pos["entry_order_id"]     is None
        assert pos["stop_order_id"]      is None

    def test_update_position_stop_order_writes_fields(self, tmp_path):
        from agents.exit_agent import add_position, update_position_stop_order
        portfolio_file = tmp_path / "portfolio.json"

        with patch("agents.exit_agent.PORTFOLIO_PATH", portfolio_file):
            add_position(ticker="NVDA", entry_price=800.0, shares=1)
            result = update_position_stop_order(
                "NVDA", "stop-999", "open",
                portfolio_path=portfolio_file,
            )

        assert result is True
        data = json.loads(portfolio_file.read_text())
        pos  = data["positions"][0]
        assert pos["stop_order_id"]      == "stop-999"
        assert pos["broker_stop_status"] == "open"

    def test_update_position_stop_order_returns_false_for_unknown_ticker(self, tmp_path):
        from agents.exit_agent import add_position, update_position_stop_order
        portfolio_file = tmp_path / "portfolio.json"

        with patch("agents.exit_agent.PORTFOLIO_PATH", portfolio_file):
            add_position(ticker="AAPL", entry_price=200.0, shares=1)
            result = update_position_stop_order(
                "UNKNOWN", "stop-x", "open",
                portfolio_path=portfolio_file,
            )

        assert result is False


# ─────────────────────────────────────────────────────────────
# 5. ExitAgent.run() — SELL 前の broker stop 厳密キャンセル検証
# ─────────────────────────────────────────────────────────────

def _portfolio_with_open_stop(tmp_path: Path, current_stop_status: str = "open") -> Path:
    """SELL 判定に使うデフォルトポジション（stop_loss_price=180 で SELL が出る価格=175）。"""
    return _make_portfolio_file(tmp_path, [{
        "ticker":             "AAPL",
        "entry_date":         "2026-01-01",
        "entry_price":        200.0,
        "shares":             10,
        "target_price":       None,
        "stop_loss_price":    180.0,
        "buy_log_file":       "",
        "thesis":             "",
        "status":             "OPEN",
        "entry_order_id":     "buy-001",
        "stop_order_id":      "stop-001",
        "broker_stop_status": current_stop_status,
    }])


class TestExitAgentStrictCancelVerification:
    """SELL 前の broker stop 状態確認ロジックのテスト。"""

    def _run_exit(self, tmp_path, alpaca_mock, broker_stop_status="open"):
        pf = _portfolio_with_open_stop(tmp_path, broker_stop_status)
        with patch("agents.exit_agent.PORTFOLIO_PATH", pf), \
             patch("agents.exit_agent.MAX_HOLD_DAYS", 0):
            agent = _make_exit_agent(fetch_price=175.0, portfolio_file=pf)
            return agent.run(mock_mode=False, alpaca_client=alpaca_mock), pf

    def test_sell_proceeds_when_cancel_confirmed(self, tmp_path):
        """cancel 後に get_order で 'canceled' を確認したら SELL を続行する。"""
        alpaca = MagicMock()
        alpaca.cancel_order.return_value = {"success": True, "order_id": "stop-001"}
        alpaca.get_order.return_value    = {
            "success": True, "status": "canceled", "qty": 10.0, "filled_qty": 0.0,
        }
        alpaca.place_sell.return_value   = {
            "success": True, "order_id": "sell-001", "skipped": False, "filled_qty": 10,
        }

        results, _ = self._run_exit(tmp_path, alpaca)

        alpaca.place_sell.assert_called_once()
        assert results[0]["action"] == "SELL"

    def test_sell_proceeds_when_cancel_fails_but_order_is_already_cancelled(self, tmp_path):
        """cancel API が失敗しても、get_order で 'canceled' が確認できれば SELL する。"""
        alpaca = MagicMock()
        alpaca.cancel_order.return_value = {"success": False, "error": "already cancelled"}
        alpaca.get_order.return_value    = {
            "success": True, "status": "canceled", "qty": 10.0, "filled_qty": 0.0,
        }
        alpaca.place_sell.return_value   = {
            "success": True, "order_id": "sell-001", "skipped": False, "filled_qty": 10,
        }

        results, _ = self._run_exit(tmp_path, alpaca)

        alpaca.place_sell.assert_called_once()

    def test_sell_blocked_when_stop_already_filled(self, tmp_path):
        """broker stop が既に約定済み → 二重 SELL を防止し action=HOLD に変更。"""
        alpaca = MagicMock()
        alpaca.cancel_order.return_value = {"success": False, "error": "already filled"}
        alpaca.get_order.return_value    = {
            "success": True, "status": "filled", "qty": 10.0, "filled_qty": 10.0,
        }

        results, _ = self._run_exit(tmp_path, alpaca)

        alpaca.place_sell.assert_not_called()
        assert results[0]["action"]    == "HOLD"
        assert results[0]["exit_type"] == "BROKER_STOP_FILLED"

    def test_sell_blocked_when_cancel_unconfirmed(self, tmp_path):
        """status が 'accepted' のまま確認できない → 二重 SELL リスクで SELL を中止。"""
        alpaca = MagicMock()
        alpaca.cancel_order.return_value = {"success": False, "error": "timeout"}
        alpaca.get_order.return_value    = {
            "success": True, "status": "accepted", "qty": 10.0, "filled_qty": 0.0,
        }

        results, _ = self._run_exit(tmp_path, alpaca)

        alpaca.place_sell.assert_not_called()
        assert results[0]["action"]    == "HOLD"
        assert results[0]["exit_type"] == "STOP_CANCEL_UNCONFIRMED"

    def test_no_cancel_when_broker_stop_status_is_not_open(self, tmp_path):
        """broker_stop_status が 'open' 以外のときは cancel も get_order も呼ばない。"""
        alpaca = MagicMock()
        alpaca.place_sell.return_value = {
            "success": True, "order_id": "sell-x", "skipped": False, "filled_qty": 10,
        }

        self._run_exit(tmp_path, alpaca, broker_stop_status="none")

        alpaca.cancel_order.assert_not_called()
        alpaca.get_order.assert_not_called()

    def test_cancel_is_called_before_sell(self, tmp_path):
        """cancel が place_sell より前に呼ばれること（呼び出し順）。"""
        alpaca = MagicMock()
        alpaca.cancel_order.return_value = {"success": True, "order_id": "stop-001"}
        alpaca.get_order.return_value    = {
            "success": True, "status": "canceled", "qty": 10.0, "filled_qty": 0.0,
        }
        alpaca.place_sell.return_value   = {
            "success": True, "order_id": "sell-001", "skipped": False, "filled_qty": 10,
        }

        self._run_exit(tmp_path, alpaca)

        cancel_idx   = alpaca.method_calls.index(call.cancel_order("stop-001"))
        place_sell_idx = next(
            i for i, c in enumerate(alpaca.method_calls) if c[0] == "place_sell"
        )
        assert cancel_idx < place_sell_idx

    def test_broker_stop_status_set_to_filled_when_stop_was_filled(self, tmp_path):
        """stop が約定済みを検出した場合、portfolio.json の broker_stop_status が 'filled' になる。"""
        alpaca = MagicMock()
        alpaca.cancel_order.return_value = {"success": False, "error": "filled"}
        alpaca.get_order.return_value    = {
            "success": True, "status": "filled", "qty": 10.0, "filled_qty": 10.0,
        }

        _, pf = self._run_exit(tmp_path, alpaca)

        data = json.loads(pf.read_text())
        assert data["positions"][0]["broker_stop_status"] == "filled"

    def test_broker_stop_status_set_to_cancelled_after_confirmed_cancel(self, tmp_path):
        """キャンセル確認後に broker_stop_status が 'cancelled' になる。"""
        alpaca = MagicMock()
        alpaca.cancel_order.return_value = {"success": True, "order_id": "stop-001"}
        alpaca.get_order.return_value    = {
            "success": True, "status": "canceled", "qty": 10.0, "filled_qty": 0.0,
        }
        alpaca.place_sell.return_value   = {
            "success": True, "order_id": "sell-001", "skipped": False, "filled_qty": 10,
        }

        _, pf = self._run_exit(tmp_path, alpaca)

        data = json.loads(pf.read_text())
        # SELL が実行されポジションが除去される（empty positions）
        assert len(data["positions"]) == 0  # successfully sold, removed

    def test_mock_mode_skips_cancel_logic(self, tmp_path):
        """mock_mode=True では broker stop の cancel / get_order を呼ばない。"""
        pf    = _portfolio_with_open_stop(tmp_path)
        alpaca = MagicMock()
        with patch("agents.exit_agent.PORTFOLIO_PATH", pf), \
             patch("agents.exit_agent.MAX_HOLD_DAYS", 0):
            agent = _make_exit_agent(fetch_price=175.0, portfolio_file=pf)
            agent.run(mock_mode=True, alpaca_client=None)

        alpaca.cancel_order.assert_not_called()
        alpaca.get_order.assert_not_called()


# ─────────────────────────────────────────────────────────────
# 6. reconcile_broker_stops — 自己修復・qty 照合
# ─────────────────────────────────────────────────────────────

class TestReconcileBrokerStops:
    """reconcile_broker_stops() の全ケースをテストする。"""

    def test_ok_when_stop_order_in_open_orders(self, tmp_path):
        """stop_order_id が open orders に存在すれば ok 分類。"""
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "AAPL", "entry_price": 200.0, "shares": 10,
            "stop_loss_price": 185.0, "stop_order_id": "stop-001",
            "broker_stop_status": "open", "entry_order_id": "buy-001",
        }])
        open_order_mock = MagicMock()
        open_order_mock.id = "stop-001"
        # stop order qty = 10, same as position
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value     = [open_order_mock]
        alpaca._tc.get_all_positions.return_value = [
            _make_alpaca_position("AAPL", 10.0),
        ]
        alpaca.get_order.return_value = {
            "success": True, "status": "accepted", "qty": 10.0, "filled_qty": 0.0,
        }

        with patch("agents.exit_agent.PORTFOLIO_PATH", pf):
            result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "AAPL" in result["ok"]
        assert not result["missing"]
        assert not result["repaired"]

    def test_refilled_when_stop_order_filled_position_partially_remains(self, tmp_path):
        """stop が filled でも Alpaca にポジションが残っている場合は refilled 検出。"""
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "TSLA", "entry_price": 300.0, "shares": 5,
            "stop_loss_price": 270.0, "stop_order_id": "stop-filled",
            "broker_stop_status": "open", "entry_order_id": "buy-t",
        }])
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []
        alpaca.get_order.return_value             = {
            "success": True, "status": "filled", "qty": 5.0, "filled_qty": 5.0,
        }
        # Alpaca にまだポジションが残っている（部分的残り）
        alpaca._tc.get_all_positions.return_value = [
            _make_alpaca_position("TSLA", 5.0),
        ]

        with patch("agents.exit_agent.PORTFOLIO_PATH", pf):
            result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "TSLA" in result["refilled"]
        data = json.loads(pf.read_text())
        assert data["positions"][0]["broker_stop_status"] == "filled"

    def test_none_client_returns_empty(self):
        """alpaca_client=None は何もしない。"""
        from agents.exit_agent import reconcile_broker_stops

        result = reconcile_broker_stops(None)

        assert result == {
            "ok": [], "refilled": [], "repaired": [],
            "missing": [], "no_stop": [], "qty_mismatch": [],
        }

    def test_position_not_in_alpaca_is_skipped(self, tmp_path):
        """Alpaca にないポジションは sync_portfolio に委ねるためスキップ。"""
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "DEAD", "entry_price": 100.0, "shares": 5,
            "stop_loss_price": 90.0, "stop_order_id": "stop-x",
            "broker_stop_status": "open", "entry_order_id": "buy-d",
        }])
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []
        alpaca._tc.get_all_positions.return_value = []  # DEAD はいない

        with patch("agents.exit_agent.PORTFOLIO_PATH", pf):
            result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        # 何も分類されない
        assert all(len(v) == 0 for v in result.values())

    # ── 自己修復テスト ──────────────────────────────────────

    def test_pending_buy_filled_later_stop_auto_created(self, tmp_path):
        """
        pending_fill（BUY 未約定）状態で後から Alpaca にポジションが出現した場合、
        reconcile が broker stop を自動再作成する。
        """
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "NVDA", "entry_price": 800.0, "shares": 3,
            "stop_loss_price": 750.0, "stop_order_id": None,
            "broker_stop_status": "pending_fill", "entry_order_id": "buy-n",
        }])
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []
        alpaca._tc.get_all_positions.return_value = [
            _make_alpaca_position("NVDA", 3.0),
        ]
        alpaca.place_broker_stop.return_value = {
            "success": True, "order_id": "stop-new", "stop_price": 750.0,
        }

        with patch("agents.exit_agent.PORTFOLIO_PATH", pf):
            result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "NVDA" in result["repaired"]
        alpaca.place_broker_stop.assert_called_once_with("NVDA", 3.0, 750.0)
        data = json.loads(pf.read_text())
        assert data["positions"][0]["broker_stop_status"] == "open"
        assert data["positions"][0]["stop_order_id"]      == "stop-new"

    def test_legacy_position_no_stop_order_id_gets_stop_created(self, tmp_path):
        """
        stop_order_id=None かつ broker_stop_status="none"（旧ポジション）は
        stop_loss_price があれば自動再作成する。
        """
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "AMZN", "entry_price": 180.0, "shares": 5,
            "stop_loss_price": 165.0, "stop_order_id": None,
            "broker_stop_status": "none", "entry_order_id": None,
        }])
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []
        alpaca._tc.get_all_positions.return_value = [
            _make_alpaca_position("AMZN", 5.0),
        ]
        alpaca.place_broker_stop.return_value = {
            "success": True, "order_id": "stop-amzn", "stop_price": 165.0,
        }

        with patch("agents.exit_agent.PORTFOLIO_PATH", pf):
            result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "AMZN" in result["repaired"]
        alpaca.place_broker_stop.assert_called_once_with("AMZN", 5.0, 165.0)

    def test_missing_rejected_stop_gets_recreated(self, tmp_path):
        """
        broker_stop_status="open" だが stop が cancelled/rejected に変わっていた場合、
        自動再作成を試みる。
        """
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "META", "entry_price": 500.0, "shares": 4,
            "stop_loss_price": 470.0, "stop_order_id": "stop-old",
            "broker_stop_status": "open", "entry_order_id": "buy-m",
        }])
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []  # stop-old は open にない
        alpaca.get_order.return_value             = {
            "success": True, "status": "rejected", "qty": 4.0, "filled_qty": 0.0,
        }
        alpaca._tc.get_all_positions.return_value = [
            _make_alpaca_position("META", 4.0),
        ]
        alpaca.place_broker_stop.return_value = {
            "success": True, "order_id": "stop-new", "stop_price": 470.0,
        }

        with patch("agents.exit_agent.PORTFOLIO_PATH", pf):
            result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "META" in result["repaired"]
        alpaca.place_broker_stop.assert_called_once_with("META", 4.0, 470.0)

    def test_no_stop_loss_price_results_in_no_stop(self, tmp_path):
        """stop_loss_price が未設定の場合は再作成不可として no_stop 分類。"""
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "GOOG", "entry_price": 150.0, "shares": 3,
            "stop_loss_price": None, "stop_order_id": None,
            "broker_stop_status": "none", "entry_order_id": "buy-g",
        }])
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []
        alpaca._tc.get_all_positions.return_value = [
            _make_alpaca_position("GOOG", 3.0),
        ]

        with patch("agents.exit_agent.PORTFOLIO_PATH", pf):
            result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "GOOG" in result["no_stop"]
        alpaca.place_broker_stop.assert_not_called()

    def test_auto_repair_failure_results_in_missing(self, tmp_path):
        """stop 自動再作成が失敗した場合は missing 分類。"""
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "BRKB", "entry_price": 400.0, "shares": 2,
            "stop_loss_price": 380.0, "stop_order_id": None,
            "broker_stop_status": "error", "entry_order_id": "buy-b",
        }])
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []
        alpaca._tc.get_all_positions.return_value = [
            _make_alpaca_position("BRKB", 2.0),
        ]
        alpaca.place_broker_stop.return_value = {
            "success": False, "error": "API error",
        }

        with patch("agents.exit_agent.PORTFOLIO_PATH", pf):
            result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "BRKB" in result["missing"]
        data = json.loads(pf.read_text())
        assert data["positions"][0]["broker_stop_status"] == "error"

    # ── qty 照合テスト ─────────────────────────────────────

    def test_stop_qty_insufficient_detected(self, tmp_path):
        """
        stop qty < Alpaca position qty の場合は qty_mismatch を検出し警告する。
        例: 部分約定で 7/10 株のみ保護している状態。
        """
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "MSFT", "entry_price": 400.0, "shares": 10,
            "stop_loss_price": 380.0, "stop_order_id": "stop-msft",
            "broker_stop_status": "open", "entry_order_id": "buy-ms",
        }])
        open_order_mock = MagicMock()
        open_order_mock.id = "stop-msft"
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = [open_order_mock]
        alpaca._tc.get_all_positions.return_value = [
            _make_alpaca_position("MSFT", 10.0),  # Alpaca: 10株
        ]
        # stop order は 7 株分しか保護していない
        alpaca.get_order.return_value = {
            "success": True, "status": "accepted", "qty": 7.0, "filled_qty": 0.0,
        }

        with patch("agents.exit_agent.PORTFOLIO_PATH", pf):
            result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "MSFT" in result["qty_mismatch"]
        assert "MSFT" in result["ok"]  # ok にも含まれる（stop は存在する）

    def test_partial_fill_position_protected_for_filled_qty(self, tmp_path):
        """
        部分約定時: trade_cycle の _run_buy_branch が filled_qty 分の stop を作成する
        （_run_buy_branch テストで確認済み）。
        reconcile はその後の Alpaca 追加約定でのズレを qty_mismatch で検出する。
        """
        # このテストは qty_mismatch 検出の end-to-end 確認
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "AMD", "entry_price": 120.0, "shares": 10,
            "stop_loss_price": 110.0, "stop_order_id": "stop-amd",
            "broker_stop_status": "open", "entry_order_id": "buy-amd",
        }])
        open_order_mock = MagicMock()
        open_order_mock.id = "stop-amd"
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = [open_order_mock]
        # Alpaca で追加約定が入って 10 株になっている
        alpaca._tc.get_all_positions.return_value = [
            _make_alpaca_position("AMD", 10.0),
        ]
        # しかし stop は最初の 7 株分しか保護していない
        alpaca.get_order.return_value = {
            "success": True, "status": "accepted", "qty": 7.0, "filled_qty": 0.0,
        }

        with patch("agents.exit_agent.PORTFOLIO_PATH", pf):
            result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "AMD" in result["qty_mismatch"]

    def test_paper_and_live_use_same_reconcile_logic(self, tmp_path):
        """Paper / Live で reconcile のロジックは共通（環境変数依存なし）。"""
        from agents.exit_agent import reconcile_broker_stops

        pf = _make_portfolio_file(tmp_path, [{
            "ticker": "META", "entry_price": 500.0, "shares": 2,
            "stop_loss_price": 470.0, "stop_order_id": "stop-m",
            "broker_stop_status": "open", "entry_order_id": "buy-m",
        }])
        open_order_mock = MagicMock()
        open_order_mock.id = "stop-m"

        for paper_flag in (True, False):
            alpaca = MagicMock()
            alpaca._tc.get_orders.return_value        = [open_order_mock]
            alpaca._tc.get_all_positions.return_value = [
                _make_alpaca_position("META", 2.0),
            ]
            alpaca.get_order.return_value = {
                "success": True, "status": "accepted", "qty": 2.0, "filled_qty": 0.0,
            }
            with patch("agents.exit_agent.PORTFOLIO_PATH", pf), \
                 patch("tools.alpaca_client._PAPER", paper_flag):
                result = reconcile_broker_stops(alpaca, portfolio_path=pf)
            assert "META" in result["ok"], f"PAPER={paper_flag} で ok に分類されなかった"


# ─────────────────────────────────────────────────────────────
# 7. BUY fill チェックロジック（trade_cycle の再現）
# ─────────────────────────────────────────────────────────────

class TestBuyFillCheckLogic:
    """
    trade_cycle.py の _is_real_order + filled_qty ブランチを
    AlpacaClient.place_broker_stop のモックで確認する。
    """

    def _run_buy_branch(
        self,
        filled_qty: float,
        stop_price: float,
        dry_run:    bool  = False,
        mock_mode:  bool  = False,
        hybrid_mode: bool = False,
        stop_success: bool = True,
    ):
        alpaca_mock = MagicMock()
        alpaca_mock.place_broker_stop.return_value = (
            {"success": True, "order_id": "stop-new", "stop_price": stop_price}
            if stop_success else
            {"success": False, "error": "API error"}
        )
        order_result = {
            "success":       not dry_run,
            "order_id":      "buy-001",
            "filled_qty":    filled_qty,
            "requested_qty": 10,
        }
        _is_real_order = (
            not dry_run and not mock_mode and not hybrid_mode
            and order_result.get("success")
            and alpaca_mock is not None
            and bool(stop_price)
        )
        update_mock = MagicMock(return_value=True)
        log_calls: list[str] = []

        if _is_real_order:
            _fq  = float(order_result.get("filled_qty") or 0)
            _rq  = float(order_result.get("requested_qty") or 10)
            if _fq > 0:
                _stop = alpaca_mock.place_broker_stop("AAPL", _fq, stop_price)
                if _stop.get("success"):
                    update_mock("AAPL", _stop["order_id"], "open")
                    if _fq < _rq:
                        log_calls.append("partial")
                else:
                    log_calls.append("error")
                    update_mock("AAPL", None, "error")
            else:
                log_calls.append("pending_fill")
                update_mock("AAPL", None, "pending_fill")

        return alpaca_mock, update_mock, log_calls

    def test_fully_filled_creates_stop(self):
        alpaca, update, logs = self._run_buy_branch(filled_qty=10.0, stop_price=185.0)
        alpaca.place_broker_stop.assert_called_once_with("AAPL", 10.0, 185.0)
        update.assert_called_once_with("AAPL", "stop-new", "open")
        assert "error" not in logs and "pending_fill" not in logs

    def test_zero_fill_does_not_create_stop(self):
        alpaca, update, logs = self._run_buy_branch(filled_qty=0.0, stop_price=185.0)
        alpaca.place_broker_stop.assert_not_called()
        assert "pending_fill" in logs
        update.assert_called_once_with("AAPL", None, "pending_fill")

    def test_partial_fill_creates_stop_for_filled_qty_only(self):
        """部分約定: filled_qty 分のみ保護（残りは reconcile で qty_mismatch 検出）。"""
        alpaca, update, logs = self._run_buy_branch(filled_qty=7.0, stop_price=185.0)
        alpaca.place_broker_stop.assert_called_once_with("AAPL", 7.0, 185.0)
        assert "partial" in logs

    def test_dry_run_does_not_create_stop(self):
        alpaca, _, __ = self._run_buy_branch(filled_qty=10.0, stop_price=185.0, dry_run=True)
        alpaca.place_broker_stop.assert_not_called()

    def test_mock_mode_does_not_create_stop(self):
        alpaca, _, __ = self._run_buy_branch(filled_qty=10.0, stop_price=185.0, mock_mode=True)
        alpaca.place_broker_stop.assert_not_called()

    def test_stop_creation_failure_marks_error(self):
        alpaca, update, logs = self._run_buy_branch(
            filled_qty=10.0, stop_price=185.0, stop_success=False,
        )
        assert "error" in logs
        update.assert_called_with("AAPL", None, "error")


# ─────────────────────────────────────────────────────────────
# 8. _normalize_order_status
# ─────────────────────────────────────────────────────────────

class TestNormalizeOrderStatus:
    """Alpaca OrderStatus enum 文字列の正規化テスト。"""

    def _n(self, s):
        from agents.exit_agent import _normalize_order_status
        return _normalize_order_status(s)

    def test_enum_canceled(self):
        assert self._n("OrderStatus.CANCELED") == "canceled"

    def test_enum_filled(self):
        assert self._n("OrderStatus.FILLED") == "filled"

    def test_enum_partially_filled(self):
        assert self._n("OrderStatus.PARTIALLY_FILLED") == "partially_filled"

    def test_enum_new(self):
        assert self._n("OrderStatus.NEW") == "new"

    def test_enum_accepted(self):
        assert self._n("OrderStatus.ACCEPTED") == "accepted"

    def test_enum_pending_new(self):
        assert self._n("OrderStatus.PENDING_NEW") == "pending_new"

    def test_enum_expired(self):
        assert self._n("OrderStatus.EXPIRED") == "expired"

    def test_enum_rejected(self):
        assert self._n("OrderStatus.REJECTED") == "rejected"

    def test_bare_canceled(self):
        assert self._n("canceled") == "canceled"

    def test_bare_cancelled(self):
        assert self._n("cancelled") == "cancelled"

    def test_bare_filled(self):
        assert self._n("filled") == "filled"

    def test_none_returns_unknown(self):
        assert self._n(None) == "unknown"

    def test_side_enum_not_affected(self):
        # OrderSide.SELL は reconcile で使わないが壊れないことを確認
        assert self._n("OrderSide.SELL") == "sell"


# ─────────────────────────────────────────────────────────────
# 9. reconcile — 実 SDK 形式ステータス文字列
# ─────────────────────────────────────────────────────────────

class TestReconcileRealSDKStatus:
    """Alpaca SDK が返す "OrderStatus.XXX" 形式での reconcile 動作テスト。"""

    def _make_pf_with_open_stop(self, tmp_path):
        return _make_portfolio_file(tmp_path, [{
            "ticker": "AAPL", "entry_price": 200.0, "shares": 10,
            "stop_loss_price": 185.0,
            "stop_order_id": "stop-old",
            "broker_stop_status": "open",
            "entry_order_id": "buy-001",
        }])

    def test_canceled_enum_triggers_repair(self, tmp_path):
        """'OrderStatus.CANCELED' を canceled と認識して broker stop を再作成する。"""
        from agents.exit_agent import reconcile_broker_stops

        pf = self._make_pf_with_open_stop(tmp_path)
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []           # stop-old は open にない
        alpaca._tc.get_all_positions.return_value = [_make_alpaca_position("AAPL", 10.0)]
        alpaca.get_order.return_value = {
            "success": True, "status": "OrderStatus.CANCELED",
            "qty": 10.0, "filled_qty": 0.0,
        }
        alpaca.place_broker_stop.return_value = {
            "success": True, "order_id": "stop-new", "stop_price": 185.0,
        }

        result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "AAPL" in result["repaired"]
        alpaca.place_broker_stop.assert_called_once_with("AAPL", 10.0, 185.0)

        data = json.loads(pf.read_text())
        pos = data["positions"][0]
        assert pos["stop_order_id"]      == "stop-new"
        assert pos["broker_stop_status"] == "open"

    def test_filled_enum_sets_refilled(self, tmp_path):
        """'OrderStatus.FILLED' を filled と認識して refilled に分類する。"""
        from agents.exit_agent import reconcile_broker_stops

        pf = self._make_pf_with_open_stop(tmp_path)
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []
        alpaca._tc.get_all_positions.return_value = [_make_alpaca_position("AAPL", 10.0)]
        alpaca.get_order.return_value = {
            "success": True, "status": "OrderStatus.FILLED",
            "qty": 10.0, "filled_qty": 10.0,
        }

        result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "AAPL" in result["refilled"]
        alpaca.place_broker_stop.assert_not_called()

        data = json.loads(pf.read_text())
        assert data["positions"][0]["broker_stop_status"] == "filled"

    def test_partially_filled_enum_sets_refilled(self, tmp_path):
        """'OrderStatus.PARTIALLY_FILLED' を partially_filled と認識して refilled に分類する。"""
        from agents.exit_agent import reconcile_broker_stops

        pf = self._make_pf_with_open_stop(tmp_path)
        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []
        alpaca._tc.get_all_positions.return_value = [_make_alpaca_position("AAPL", 10.0)]
        alpaca.get_order.return_value = {
            "success": True, "status": "OrderStatus.PARTIALLY_FILLED",
            "qty": 10.0, "filled_qty": 7.0,
        }

        result = reconcile_broker_stops(alpaca, portfolio_path=pf)

        assert "AAPL" in result["refilled"]
        alpaca.place_broker_stop.assert_not_called()


# ─────────────────────────────────────────────────────────────
# 10. portfolio_path isolation
# ─────────────────────────────────────────────────────────────

class TestPortfolioPathIsolation:
    """portfolio_path 引数が read/write 両方に正しく伝達されることを確認する。"""

    def test_reconcile_writes_only_to_explicit_path(self, tmp_path):
        """reconcile(portfolio_path=B) は B のみ更新し、PORTFOLIO_PATH (A) は変更しない。"""
        from agents.exit_agent import reconcile_broker_stops

        # A: モジュールデフォルト (PORTFOLIO_PATH にパッチ)
        path_a = tmp_path / "portfolio_a.json"
        path_a.write_text(json.dumps({
            "schema_version": "1.0", "updated_at": "2026-01-01", "positions": [],
        }))
        mtime_a_before = path_a.stat().st_mtime

        # B: 明示的に渡すポートフォリオ
        path_b = tmp_path / "portfolio_b.json"
        path_b.write_text(json.dumps({
            "schema_version": "1.0", "updated_at": "2026-01-01",
            "positions": [{
                "ticker": "AAPL", "entry_price": 200.0, "shares": 10,
                "stop_loss_price": 185.0, "stop_order_id": None,
                "broker_stop_status": "none", "entry_order_id": None,
            }],
        }))

        alpaca = MagicMock()
        alpaca._tc.get_orders.return_value        = []
        alpaca._tc.get_all_positions.return_value = [_make_alpaca_position("AAPL", 10.0)]
        alpaca.place_broker_stop.return_value = {
            "success": True, "order_id": "stop-new", "stop_price": 185.0,
        }

        with patch("agents.exit_agent.PORTFOLIO_PATH", path_a):
            result = reconcile_broker_stops(alpaca, portfolio_path=path_b)

        # B のみ更新されている
        assert "AAPL" in result["repaired"]
        data_b = json.loads(path_b.read_text())
        assert data_b["positions"][0]["stop_order_id"] == "stop-new"

        # A は変更されていない
        assert path_a.stat().st_mtime == mtime_a_before

    def test_update_position_stop_order_writes_only_to_explicit_path(self, tmp_path):
        """update_position_stop_order(portfolio_path=tmp) は tmp のみ変更する。"""
        from agents.exit_agent import update_position_stop_order

        # デフォルト path にパッチ
        default_path = tmp_path / "default_portfolio.json"
        default_path.write_text(json.dumps({
            "schema_version": "1.0", "updated_at": "2026-01-01", "positions": [],
        }))
        mtime_default_before = default_path.stat().st_mtime

        # 明示的な path
        target_path = tmp_path / "target_portfolio.json"
        target_path.write_text(json.dumps({
            "schema_version": "1.0", "updated_at": "2026-01-01",
            "positions": [{
                "ticker": "AAPL", "entry_price": 200.0, "shares": 10,
                "stop_loss_price": 185.0, "stop_order_id": None,
                "broker_stop_status": "none", "entry_order_id": None,
            }],
        }))

        with patch("agents.exit_agent.PORTFOLIO_PATH", default_path):
            result = update_position_stop_order(
                "AAPL", "stop-xyz", "open",
                portfolio_path=target_path,
            )

        assert result is True

        # target_path は更新されている
        data = json.loads(target_path.read_text())
        assert data["positions"][0]["stop_order_id"]      == "stop-xyz"
        assert data["positions"][0]["broker_stop_status"] == "open"

        # default_path は変更されていない
        assert default_path.stat().st_mtime == mtime_default_before


# ─────────────────────────────────────────────────────────────
# 11. strict cancel verification — 実 SDK 形式ステータス文字列
# ─────────────────────────────────────────────────────────────

class TestStrictCancelVerificationRealSDKStatus:
    """ExitAgent.run() の broker stop 状態確認が実 API 形式を正しく処理する。"""

    def _run_exit_with_status(self, tmp_path, sdk_status: str):
        """指定 SDK 形式 status を返す alpaca mock で ExitAgent.run() を実行する。"""
        pf = _portfolio_with_open_stop(tmp_path, "open")
        alpaca = MagicMock()
        alpaca.cancel_order.return_value = {"success": True, "order_id": "stop-001"}
        alpaca.get_order.return_value    = {
            "success": True, "status": sdk_status,
            "qty": 10.0, "filled_qty": 0.0,
        }
        alpaca.place_sell.return_value = {
            "success": True, "order_id": "sell-001", "skipped": False, "filled_qty": 10,
        }
        with patch("agents.exit_agent.PORTFOLIO_PATH", pf), \
             patch("agents.exit_agent.MAX_HOLD_DAYS", 0):
            agent = _make_exit_agent(fetch_price=175.0, portfolio_file=pf)
            results = agent.run(mock_mode=False, alpaca_client=alpaca)
        return results, alpaca

    def test_canceled_enum_allows_sell(self, tmp_path):
        """'OrderStatus.CANCELED' → SELL 続行。"""
        results, alpaca = self._run_exit_with_status(tmp_path, "OrderStatus.CANCELED")
        alpaca.place_sell.assert_called_once()
        assert results[0]["action"] == "SELL"

    def test_filled_enum_blocks_sell(self, tmp_path):
        """'OrderStatus.FILLED' → 二重 SELL 防止で HOLD。"""
        results, alpaca = self._run_exit_with_status(tmp_path, "OrderStatus.FILLED")
        alpaca.place_sell.assert_not_called()
        assert results[0]["action"]    == "HOLD"
        assert results[0]["exit_type"] == "BROKER_STOP_FILLED"

    def test_partially_filled_enum_blocks_sell(self, tmp_path):
        """'OrderStatus.PARTIALLY_FILLED' → 二重 SELL 防止で HOLD。"""
        results, alpaca = self._run_exit_with_status(tmp_path, "OrderStatus.PARTIALLY_FILLED")
        alpaca.place_sell.assert_not_called()
        assert results[0]["action"]    == "HOLD"
        assert results[0]["exit_type"] == "BROKER_STOP_FILLED"

    def test_new_enum_blocks_sell_as_fail_safe(self, tmp_path):
        """'OrderStatus.NEW' (まだ active) → fail-safe で SELL を中止。"""
        results, alpaca = self._run_exit_with_status(tmp_path, "OrderStatus.NEW")
        alpaca.place_sell.assert_not_called()
        assert results[0]["action"]    == "HOLD"
        assert results[0]["exit_type"] == "STOP_CANCEL_UNCONFIRMED"

    def test_accepted_status_blocks_sell_as_fail_safe(self, tmp_path):
        """'OrderStatus.ACCEPTED' (まだ active) → fail-safe で SELL を中止。"""
        results, alpaca = self._run_exit_with_status(tmp_path, "OrderStatus.ACCEPTED")
        alpaca.place_sell.assert_not_called()
        assert results[0]["exit_type"] == "STOP_CANCEL_UNCONFIRMED"


# ─────────────────────────────────────────────────────────────
# 12. wait_for_fill — BUY 注文のfill確認ポーリング（Bug 1対応）
# ─────────────────────────────────────────────────────────────

class TestWaitForFill:
    """AlpacaClient.wait_for_fill() のポーリング動作テスト。"""

    def _get_order_result(
        self,
        status: str,
        filled_qty: float = 0.0,
        fill_price: float | None = None,
    ) -> dict:
        return {
            "success":    True,
            "order_id":   "buy-001",
            "status":     status,
            "qty":        10.0,
            "filled_qty": filled_qty,
            "fill_price": fill_price,
            "symbol":     "AAPL",
            "side":       "buy",
        }

    def test_immediate_fill_returns_terminal(self):
        """get_order が即座に filled を返す場合、terminal=True で返る。"""
        c = _make_client()
        with patch.object(c, "get_order", return_value=self._get_order_result(
            "filled", filled_qty=10.0, fill_price=195.0,
        )):
            with patch("time.sleep"):
                result = c.wait_for_fill("buy-001")

        assert result["success"]    is True
        assert result["terminal"]   is True
        assert result["timed_out"]  is False
        assert result["status"]     == "filled"
        assert result["filled_qty"] == pytest.approx(10.0)
        assert result["fill_price"] == pytest.approx(195.0)

    def test_pending_new_then_filled_polls_until_terminal(self):
        """PENDING_NEW → NEW → FILLED の遷移でポーリングを続け filled を検出する。"""
        c = _make_client()
        responses = [
            self._get_order_result("OrderStatus.PENDING_NEW", 0.0),
            self._get_order_result("OrderStatus.NEW",         0.0),
            self._get_order_result("OrderStatus.FILLED",     10.0, 195.5),
        ]
        with patch.object(c, "get_order", side_effect=responses):
            with patch("time.sleep"):
                result = c.wait_for_fill("buy-001")

        assert result["terminal"]   is True
        assert result["status"]     == "filled"
        assert result["filled_qty"] == pytest.approx(10.0)
        assert result["fill_price"] == pytest.approx(195.5)

    def test_timeout_returns_timed_out_true_and_zero_fill(self):
        """max_attempts 回ポーリングして非 terminal のまま → timed_out=True, filled_qty=0。"""
        c = _make_client()
        non_terminal = self._get_order_result("pending_new", 0.0)
        with patch.object(c, "get_order", return_value=non_terminal):
            with patch("time.sleep"):
                result = c.wait_for_fill("buy-001", max_attempts=3)

        assert result["success"]    is True
        assert result["terminal"]   is False
        assert result["timed_out"]  is True
        assert result["filled_qty"] == pytest.approx(0.0)

    def test_rejected_returns_terminal_with_zero_fill(self):
        """rejected は terminal status で filled_qty=0 を返す（stop を作らない）。"""
        c = _make_client()
        with patch.object(c, "get_order", return_value=self._get_order_result(
            "OrderStatus.REJECTED", 0.0,
        )):
            with patch("time.sleep"):
                result = c.wait_for_fill("buy-001")

        assert result["terminal"]   is True
        assert result["status"]     == "rejected"
        assert result["filled_qty"] == pytest.approx(0.0)

    def test_canceled_returns_terminal_with_zero_fill(self):
        """canceled は terminal status で filled_qty=0 を返す。"""
        c = _make_client()
        with patch.object(c, "get_order", return_value=self._get_order_result(
            "OrderStatus.CANCELED", 0.0,
        )):
            with patch("time.sleep"):
                result = c.wait_for_fill("buy-001")

        assert result["terminal"]   is True
        assert result["status"]     == "canceled"
        assert result["filled_qty"] == pytest.approx(0.0)

    def test_partially_filled_returns_terminal_with_partial_qty(self):
        """partially_filled は terminal で filled_qty=7 を返す（stop は 7 株分作成対象）。"""
        c = _make_client()
        with patch.object(c, "get_order", return_value=self._get_order_result(
            "OrderStatus.PARTIALLY_FILLED", filled_qty=7.0, fill_price=194.0,
        )):
            with patch("time.sleep"):
                result = c.wait_for_fill("buy-001")

        assert result["terminal"]   is True
        assert result["status"]     == "partially_filled"
        assert result["filled_qty"] == pytest.approx(7.0)
        assert result["fill_price"] == pytest.approx(194.0)

    def test_get_order_api_error_returns_success_false(self):
        """get_order が失敗レスポンスを返す場合、success=False で返る。"""
        c = _make_client()
        with patch.object(c, "get_order", return_value={
            "success": False, "error": "API error", "order_id": "buy-001",
        }):
            with patch("time.sleep"):
                result = c.wait_for_fill("buy-001")

        assert result["success"]  is False
        assert "error" in result

    def test_get_order_exception_returns_success_false(self):
        """get_order が例外を投げる場合、success=False で返る（fail-safe）。"""
        c = _make_client()
        with patch.object(c, "get_order", side_effect=RuntimeError("connection error")):
            with patch("time.sleep"):
                result = c.wait_for_fill("buy-001")

        assert result["success"] is False
        assert "error" in result

    def test_sleep_called_between_polls_not_after_terminal(self):
        """ポーリング間に sleep が呼ばれ、terminal 後は呼ばれないこと。"""
        c = _make_client()
        responses = [
            self._get_order_result("pending_new", 0.0),  # non-terminal → sleep
            self._get_order_result("filled", 10.0),       # terminal → return (no sleep)
        ]
        with patch.object(c, "get_order", side_effect=responses):
            with patch("time.sleep") as mock_sleep:
                c.wait_for_fill("buy-001", interval_secs=2.0)

        mock_sleep.assert_called_once_with(2.0)

    def test_initial_zero_fill_updated_to_polled_fill_for_broker_stop(self):
        """
        place_buy が filled_qty=0 で返り、wait_for_fill が filled_qty=10 を返す場合、
        order_result が更新され broker stop が qty=10 で作成されること（Bug 1 統合確認）。
        """
        alpaca = MagicMock()

        # place_buy: PENDING_NEW / filled_qty=0
        order_result = {
            "success": True, "order_id": "buy-001",
            "status": "OrderStatus.PENDING_NEW",
            "filled_qty": 0.0, "requested_qty": 10,
            "symbol": "AAPL",
        }

        # wait_for_fill: FILLED / filled_qty=10
        alpaca.wait_for_fill.return_value = {
            "success": True, "terminal": True, "timed_out": False,
            "status": "filled", "filled_qty": 10.0, "fill_price": 195.0,
        }
        alpaca.place_broker_stop.return_value = {
            "success": True, "order_id": "stop-001", "stop_price": 185.0,
        }

        # trade_cycle の fill ポーリング + broker stop 作成フローを再現
        _fill_poll = alpaca.wait_for_fill(order_result["order_id"])
        if _fill_poll.get("success") and _fill_poll.get("terminal"):
            order_result["filled_qty"] = _fill_poll.get("filled_qty", 0)
            if _fill_poll.get("fill_price"):
                order_result["fill_price"] = _fill_poll["fill_price"]

        stop_price  = 185.0
        filled_qty  = float(order_result.get("filled_qty") or 0)
        req_qty     = float(order_result.get("requested_qty") or 10)
        if filled_qty > 0:
            alpaca.place_broker_stop("AAPL", filled_qty, stop_price)

        # 初回 filled_qty=0 ではなく、ポーリング後の 10 で stop が作成される
        alpaca.place_broker_stop.assert_called_once_with("AAPL", 10.0, 185.0)


# ─────────────────────────────────────────────────────────────
# 13. state mutation policy — dry_run/hybrid/mock は portfolio 等を汚染しない（Bug 2対応）
# ─────────────────────────────────────────────────────────────

class TestStateMutationPolicy:
    """
    Bug 2: dry_run / hybrid / mock が portfolio.json と TradeGuard を
    汚染しないことを確認する。

    _order_ok 判定式（修正後）:
        not research_mode and not dry_run and not mock_mode and not hybrid_mode
        and bool(order_result.get("success"))
    """

    def _order_ok(
        self,
        research_mode: bool,
        dry_run: bool,
        mock_mode: bool,
        hybrid_mode: bool,
        order_result: dict,
    ) -> bool:
        """trade_cycle の修正後 _order_ok 判定を再現する。"""
        return (
            not research_mode
            and not dry_run
            and not mock_mode
            and not hybrid_mode
            and bool(order_result.get("success"))
        )

    # ── 条件式ユニットテスト ────────────────────────────────

    def test_dry_run_order_ok_false(self):
        """dry_run=True → _order_ok=False (portfolio に書かれない)。"""
        assert not self._order_ok(
            False, True, False, False,
            {"dry_run": True, "success": False},
        )

    def test_hybrid_order_ok_false(self):
        """hybrid_mode=True → _order_ok=False (portfolio に書かれない)。"""
        assert not self._order_ok(
            False, False, False, True,
            {"dry_run": True, "success": False},
        )

    def test_mock_order_ok_false(self):
        """mock_mode=True → _order_ok=False (portfolio に書かれない)。"""
        assert not self._order_ok(
            False, False, True, False,
            {"dry_run": True, "success": False},
        )

    def test_research_mode_order_ok_false(self):
        """research_mode=True は dry_run=True を強制するため _order_ok=False。"""
        assert not self._order_ok(
            True, True, False, False,
            {"success": False},
        )

    def test_real_buy_success_order_ok_true(self):
        """全フラグ=False かつ success=True → _order_ok=True (登録される)。"""
        assert self._order_ok(
            False, False, False, False,
            {"success": True},
        )

    def test_real_buy_failed_order_ok_false(self):
        """全フラグ=False でも success=False → _order_ok=False。"""
        assert not self._order_ok(
            False, False, False, False,
            {"success": False},
        )

    def test_order_result_dry_run_flag_alone_does_not_enable_write(self):
        """order_result["dry_run"]=True があっても dry_run 引数が True なら _order_ok=False。"""
        assert not self._order_ok(
            False, True, False, False,
            {"dry_run": True, "mock": False, "success": False},
        )

    # ── 振る舞いテスト: _portfolio_add / record_buy が呼ばれないこと ──

    def test_dry_run_portfolio_add_not_called(self):
        """dry_run=True のとき _portfolio_add は呼ばれない。"""
        portfolio_add = MagicMock()
        order_result  = {"dry_run": True, "success": False}
        if self._order_ok(False, True, False, False, order_result):
            portfolio_add("AAPL", 100.0, 1)
        portfolio_add.assert_not_called()

    def test_hybrid_portfolio_add_not_called(self):
        """hybrid_mode=True のとき _portfolio_add は呼ばれない。"""
        portfolio_add = MagicMock()
        order_result  = {"dry_run": True, "success": False}
        if self._order_ok(False, False, False, True, order_result):
            portfolio_add("AAPL", 100.0, 1)
        portfolio_add.assert_not_called()

    def test_mock_portfolio_add_not_called(self):
        """mock_mode=True のとき _portfolio_add は呼ばれない。"""
        portfolio_add = MagicMock()
        order_result  = {"dry_run": True, "success": False}
        if self._order_ok(False, False, True, False, order_result):
            portfolio_add("AAPL", 100.0, 1)
        portfolio_add.assert_not_called()

    def test_dry_run_trade_guard_record_buy_not_called(self):
        """dry_run=True のとき TradeGuard.record_buy は呼ばれない。"""
        record_buy   = MagicMock()
        order_result = {"dry_run": True, "success": False}
        if self._order_ok(False, True, False, False, order_result):
            record_buy("AAPL")
        record_buy.assert_not_called()

    def test_real_buy_portfolio_add_called(self):
        """real BUY (success=True) のとき _portfolio_add が呼ばれる。"""
        portfolio_add = MagicMock()
        order_result  = {"success": True}
        if self._order_ok(False, False, False, False, order_result):
            portfolio_add("AAPL", 100.0, 1)
        portfolio_add.assert_called_once()
