"""
Skill: portfolio_monitor
Permission: ExitAgent only

Returns current portfolio positions from data/portfolio.json,
enriched with real-time prices via yfinance and SL/TP status.
"""
from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import yfinance as yf

logger = logging.getLogger(__name__)

_PORTFOLIO_PATH = Path(__file__).parent.parent / "data" / "portfolio.json"


def get_current_portfolio() -> list[dict]:
    """
    Returns live portfolio positions enriched with:
      - current_price (yfinance)
      - pnl_pct
      - stop_loss_hit  (current <= stop_loss_price; ATR-based when available)
      - take_profit_hit (current >= target_price; ATR-based when available)
    """
    if not _PORTFOLIO_PATH.exists():
        return []

    try:
        data = json.loads(_PORTFOLIO_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("[portfolio_monitor] portfolio.json 読み込みエラー: %s", e)
        return []

    result = []
    for pos in data.get("positions", []):
        ticker   = pos["ticker"]
        avg_cost = float(pos["entry_price"])
        shares   = int(pos.get("shares", 0))
        stop     = pos.get("stop_loss_price")
        target   = pos.get("target_price")

        current_price = _fetch_price(ticker)
        pnl_pct = (
            round((current_price - avg_cost) / avg_cost * 100, 2)
            if avg_cost > 0 and current_price > 0
            else 0.0
        )

        # フォールバック閾値（ATR価格が未設定の場合のみ使用）
        _fallback_sl_pct = -5.0
        _fallback_tp_pct = 10.0
        stop_hit = (
            (current_price > 0 and float(stop) >= current_price)
            if stop is not None
            else (pnl_pct <= _fallback_sl_pct)
        )
        tp_hit = (
            (current_price > 0 and current_price >= float(target))
            if target is not None
            else (pnl_pct >= _fallback_tp_pct)
        )

        result.append({
            "ticker":          ticker,
            "shares":          shares,
            "avg_cost":        avg_cost,
            "entry_date":      pos.get("entry_date", ""),
            "current_price":   round(current_price, 2),
            "stop_loss":       round(float(stop), 2) if stop is not None else None,
            "target_price":    round(float(target), 2) if target is not None else None,
            "pnl_pct":         pnl_pct,
            "stop_loss_hit":   stop_hit,
            "take_profit_hit": tp_hit,
            "status":          pos.get("status", "OPEN"),
        })

    return result


def _fetch_price(ticker: str) -> float:
    try:
        hist = yf.Ticker(ticker).history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning("[portfolio_monitor] %s 価格取得失敗: %s", ticker, e)
    return 0.0


def run(ticker: str | None = None) -> dict:
    """Skill entry point."""
    portfolio = get_current_portfolio()
    if ticker:
        matches = [p for p in portfolio if p["ticker"].upper() == ticker.upper()]
        return {"ticker": ticker, "positions": matches}
    return {"positions": portfolio}
