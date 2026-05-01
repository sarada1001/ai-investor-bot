"""
Skill: risk_calculator
Permission: RiskAgent only

Calculates recommended position size (shares) and stop-loss price using:
  - Fixed Fractional: risk 2% of account per trade ÷ (ATR × 2) = max shares
  - Kelly Criterion (simplified): win_rate=55%, win/loss ratio=1.5
  The more conservative value (min of the two) becomes recommended_shares.
"""

from __future__ import annotations

import warnings
import yfinance as yf

warnings.filterwarnings("ignore")

_ACCOUNT_BALANCE  = 100_000.0
_RISK_FRACTION    = 0.02
_ATR_MULTIPLIER   = 2.0
_ATR_PCT_FALLBACK = 0.015   # 1.5% of price when yfinance unavailable

_KELLY_WIN_RATE  = 0.55
_KELLY_WIN_RATIO = 1.5


def _fetch_current_price(ticker: str) -> float | None:
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def _calc_atr(ticker: str, current_price: float) -> float:
    try:
        hist = yf.Ticker(ticker).history(period="1mo")
        if len(hist) >= 14:
            high       = hist["High"].values
            low        = hist["Low"].values
            close_prev = hist["Close"].shift(1).values
            tr = [
                max(high[i] - low[i],
                    abs(high[i] - close_prev[i]),
                    abs(low[i]  - close_prev[i]))
                for i in range(1, len(hist))
            ]
            return sum(tr[-14:]) / 14
    except Exception:
        pass
    return current_price * _ATR_PCT_FALLBACK


def _kelly_shares(account_balance: float, current_price: float) -> int:
    kelly_fraction = (
        (_KELLY_WIN_RATE * _KELLY_WIN_RATIO - (1 - _KELLY_WIN_RATE)) / _KELLY_WIN_RATIO
    )
    kelly_fraction = max(0.0, min(kelly_fraction, 0.25))
    return max(1, int(account_balance * kelly_fraction / current_price))


def calculate_position(
    ticker: str,
    account_balance: float = _ACCOUNT_BALANCE,
    current_price: float | None = None,
) -> dict:
    """
    Returns:
        {
            ticker, account_balance, current_price, atr,
            stop_loss_price, stop_loss_pct,
            risk_amount, fixed_fractional_shares, kelly_shares,
            recommended_shares, reason
        }
    """
    if current_price is None:
        current_price = _fetch_current_price(ticker)
    if current_price is None or current_price <= 0:
        return {
            "ticker":             ticker,
            "error":              "current_price の取得に失敗しました",
            "recommended_shares": 1,
            "stop_loss_price":    0.0,
            "stop_loss_pct":      0.0,
            "reason":             "価格データ取得失敗 — デフォルト 1株 を返します",
        }

    atr           = _calc_atr(ticker, current_price)
    stop_distance = _ATR_MULTIPLIER * atr
    stop_loss_price = round(current_price - stop_distance, 2)
    stop_loss_pct   = round(stop_distance / current_price * 100, 2)

    risk_amount  = account_balance * _RISK_FRACTION
    ff_shares    = max(1, int(risk_amount / stop_distance))
    k_shares     = _kelly_shares(account_balance, current_price)
    recommended  = min(ff_shares, k_shares)

    reason = (
        f"口座 ${account_balance:,.0f} の {_RISK_FRACTION:.0%} をリスク (${risk_amount:,.0f})。"
        f" ATR=${atr:.2f} × {_ATR_MULTIPLIER} = ストップ距離 ${stop_distance:.2f}。"
        f" Fixed Fractional={ff_shares}株, Kelly={k_shares}株 → 保守的な {recommended}株を採用。"
    )

    return {
        "ticker":                  ticker,
        "account_balance":         account_balance,
        "current_price":           round(current_price, 2),
        "atr":                     round(atr, 4),
        "stop_loss_price":         stop_loss_price,
        "stop_loss_pct":           stop_loss_pct,
        "risk_amount":             round(risk_amount, 2),
        "fixed_fractional_shares": ff_shares,
        "kelly_shares":            k_shares,
        "recommended_shares":      recommended,
        "reason":                  reason,
    }


def run(
    ticker: str = "AAPL",
    account_balance: float = _ACCOUNT_BALANCE,
    current_price: float | None = None,
) -> dict:
    """Skill entry point."""
    return calculate_position(ticker, account_balance, current_price)
