"""
Skill: portfolio_monitor
Permission: ExitAgent only

Returns current portfolio positions with average cost, current price,
and stop-loss levels set by RiskAgent. (Mock data for development)
"""
from __future__ import annotations

_MOCK_PORTFOLIO: list[dict] = [
    {
        "ticker":        "NVDA",
        "shares":        50,
        "avg_cost":      120.0,
        "current_price": 115.0,
        "stop_loss":     118.0,
    },
    {
        "ticker":        "AAPL",
        "shares":        30,
        "avg_cost":      175.0,
        "current_price": 182.0,
        "stop_loss":     168.0,
    },
    {
        "ticker":        "TSLA",
        "shares":        20,
        "avg_cost":      210.0,
        "current_price": 205.0,
        "stop_loss":     200.0,
    },
]


def get_current_portfolio() -> list[dict]:
    """Returns mock portfolio positions enriched with P&L and stop-loss status."""
    result = []
    for pos in _MOCK_PORTFOLIO:
        avg_cost = pos["avg_cost"]
        current  = pos["current_price"]
        stop     = pos["stop_loss"]
        pnl_pct  = round((current - avg_cost) / avg_cost * 100, 2)
        result.append({
            **pos,
            "pnl_pct":       pnl_pct,
            "stop_loss_hit": current < stop,
        })
    return result


def run(ticker: str | None = None) -> dict:
    """Skill entry point."""
    portfolio = get_current_portfolio()
    if ticker:
        matches = [p for p in portfolio if p["ticker"].upper() == ticker.upper()]
        return {"ticker": ticker, "positions": matches}
    return {"positions": portfolio}
