"""
Skill: alpaca_trade
Paper Trading via Alpaca Markets API (alpaca-py).

Functions:
  get_account_info()          → dict with cash, buying_power, equity
  place_market_order(symbol, qty, side)  → order dict
  run(action, **kwargs)       → skill registry dispatcher
"""

from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("ALPACA_API_KEY", "")
_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
_PAPER = os.getenv("ALPACA_PAPER_TRADING", "True").lower() != "false"


def _client():
    from alpaca.trading.client import TradingClient
    return TradingClient(_API_KEY, _SECRET_KEY, paper=_PAPER)


def get_account_info() -> dict:
    """Return cash, buying_power, and portfolio equity from the Alpaca account."""
    account = _client().get_account()
    return {
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "equity": float(account.equity),
        "currency": account.currency,
        "paper": _PAPER,
    }


def place_market_order(symbol: str, qty: float, side: str) -> dict:
    """
    Submit a market order.

    Args:
        symbol: Ticker symbol, e.g. "AAPL"
        qty:    Number of shares (fractional supported)
        side:   "buy" or "sell"

    Returns:
        dict with order id, status, symbol, qty, side
    """
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

    req = MarketOrderRequest(
        symbol=symbol.upper(),
        qty=qty,
        side=side_enum,
        time_in_force=TimeInForce.DAY,
    )
    order = _client().submit_order(req)
    return {
        "order_id": str(order.id),
        "status": str(order.status),
        "symbol": order.symbol,
        "qty": float(order.qty),
        "side": str(order.side),
        "type": str(order.order_type),
        "time_in_force": str(order.time_in_force),
        "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
    }


def run(action: str, **kwargs) -> dict:
    """
    Skill registry entry point.

    action="get_account"  → get_account_info()
    action="order"        → place_market_order(symbol, qty, side)
    """
    if action == "get_account":
        return get_account_info()
    if action == "order":
        symbol = kwargs.get("symbol")
        qty = kwargs.get("qty")
        side = kwargs.get("side", "buy")
        if not symbol or qty is None:
            raise ValueError("order requires 'symbol' and 'qty'")
        return place_market_order(symbol, float(qty), side)
    raise ValueError(f"Unknown action: {action!r}. Use 'get_account' or 'order'.")
