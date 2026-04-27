"""
Skill: technical_calc
Permission: TechnicalAgent only
Calculates RSI, MACD, and 25-day MA for swing trading.
"""

from __future__ import annotations
import warnings
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False


def _rsi(close: pd.Series, period: int = 14) -> float:
    close = close.dropna()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return round(float(rsi_series.dropna().iloc[-1]), 2)


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    close = close.dropna()
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd": round(float(macd_line.iloc[-1]), 4),
        "signal": round(float(signal_line.iloc[-1]), 4),
        "histogram": round(float(histogram.iloc[-1]), 4),
        "trend": "golden_cross" if float(histogram.iloc[-1]) > 0 else "dead_cross",
    }


def _ma25(close: pd.Series) -> dict:
    close = close.dropna()
    ma = close.rolling(25).mean()
    latest_price = float(close.iloc[-1])
    latest_ma = float(ma.dropna().iloc[-1])
    diff_pct = round((latest_price - latest_ma) / latest_ma * 100, 2)
    return {
        "ma25": round(latest_ma, 2),
        "latest_price": round(latest_price, 2),
        "diff_pct": diff_pct,
        "position": "above" if latest_price > latest_ma else "below",
    }


def _signal_summary(rsi: float, macd: dict, ma: dict) -> str:
    """Generate a human-readable swing trade signal."""
    signals = []

    if rsi < 30:
        signals.append("RSI売られすぎ(買いシグナル)")
    elif rsi > 70:
        signals.append("RSI買われすぎ(売りシグナル)")
    else:
        signals.append(f"RSI中立({rsi})")

    if macd["trend"] == "golden_cross":
        signals.append("MACDゴールデンクロス(上昇圧力)")
    else:
        signals.append("MACDデッドクロス(下落圧力)")

    if ma["position"] == "above":
        signals.append(f"25MA上方乖離+{ma['diff_pct']}%(強気)")
    else:
        signals.append(f"25MA下方乖離{ma['diff_pct']}%(弱気)")

    return " / ".join(signals)


def run(tickers: dict[str, str], period: str = "3mo") -> dict:
    """
    Calculate technical indicators for swing trading.

    Args:
        tickers: {"銘柄名": "Yahoo Finance ticker", ...}
                 e.g. {"エクサウィザーズ": "4840.T", "三菱重工": "7011.T"}
        period:  yfinance period string ("1mo", "3mo", "6mo", "1y")

    Returns:
        {
            "tickers": {
                "銘柄名": {
                    "ticker": str,
                    "rsi": float,
                    "macd": {"macd": float, "signal": float, "histogram": float, "trend": str},
                    "ma25": {"ma25": float, "latest_price": float, "diff_pct": float, "position": str},
                    "signal_summary": str,
                    "error": str | None,
                }
            }
        }
    """
    if not _YF_AVAILABLE:
        return {"tickers": {}, "error": "yfinance がインストールされていません。pip install yfinance"}

    results: dict = {}

    for name, ticker_symbol in tickers.items():
        try:
            df = yf.download(ticker_symbol, period=period, progress=False, auto_adjust=True)
            if df.empty or len(df) < 30:
                results[name] = {
                    "ticker": ticker_symbol,
                    "error": f"データ不足（取得行数: {len(df)}）。ティッカー '{ticker_symbol}' を確認してください。",
                }
                continue

            close = df["Close"].squeeze()
            rsi_val = _rsi(close)
            macd_val = _macd(close)
            ma_val = _ma25(close)

            results[name] = {
                "ticker": ticker_symbol,
                "rsi": rsi_val,
                "macd": macd_val,
                "ma25": ma_val,
                "signal_summary": _signal_summary(rsi_val, macd_val, ma_val),
                "error": None,
            }
        except Exception as e:
            results[name] = {"ticker": ticker_symbol, "error": str(e)}

    return {"tickers": results}
