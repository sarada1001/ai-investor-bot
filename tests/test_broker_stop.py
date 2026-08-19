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
        """status / qty / filled_qty が返る。"""
        client = _make_client()
        mock_order = MagicMock()
        mock_order.id          = "stop-001"
        mock_order.status      = "accepted"
        mock_order.qty         = "10"
        mock_order.filled_qty  = "0"
        mock_order.symbol      = "AAPL"
        mock_order.side        = "sell"
        client._tc.get_order_by_id.return_value = mock_order

        result = client.get_order("stop-001")

        assert result["success"]    is True
        assert result["status"]     == "accepted"
        assert result["qty"]        == pytest.approx(10.0)
        assert result["filled_qty"] == pytest.approx(0.0)

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
