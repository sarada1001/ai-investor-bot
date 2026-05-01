"""
Skill: technical_calc
Permission: TechnicalAgent only

Computes RSI(14), MACD(12/26/9), SMA(25) using pandas-ta.
Falls back to pure-pandas implementation if pandas-ta is unavailable.

Two call modes:
  run(ticker="AAPL")              → single-ticker analysis with LLM trend judgment
  run(tickers={"name": "tick"})   → batch mode (backward compatible, no LLM)
"""

from __future__ import annotations

import json
import re
import warnings
import datetime
import pandas as pd

import yfinance as yf
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

warnings.filterwarnings("ignore")
load_dotenv()

try:
    import pandas_ta as ta
    _TA_AVAILABLE = True
except ImportError:
    _TA_AVAILABLE = False

_llm = None


def _get_llm() -> ChatGoogleGenerativeAI:
    global _llm
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)
    return _llm


# ------------------------------------------------------------------ #
# Indicator helpers                                                    #
# ------------------------------------------------------------------ #

def _calc_rsi(close: pd.Series, period: int = 14) -> float:
    if _TA_AVAILABLE:
        result = ta.rsi(close, length=period)
        return round(float(result.dropna().iloc[-1]), 2)
    # pure-pandas fallback (Wilder smoothing)
    delta = close.diff().dropna()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return round(float((100 - 100 / (1 + rs)).dropna().iloc[-1]), 2)


def _calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    if _TA_AVAILABLE:
        result = ta.macd(close, fast=fast, slow=slow, signal=signal)
        if result is not None and not result.empty:
            macd_col = f"MACD_{fast}_{slow}_{signal}"
            hist_col = f"MACDh_{fast}_{slow}_{signal}"
            sig_col = f"MACDs_{fast}_{slow}_{signal}"
            macd_val = round(float(result[macd_col].dropna().iloc[-1]), 4)
            hist_val = round(float(result[hist_col].dropna().iloc[-1]), 4)
            sig_val = round(float(result[sig_col].dropna().iloc[-1]), 4)
            return {
                "macd": macd_val,
                "signal": sig_val,
                "histogram": hist_val,
                "trend": "golden_cross" if hist_val > 0 else "dead_cross",
            }
    # pure-pandas fallback
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - sig_line
    hist_val = round(float(hist.iloc[-1]), 4)
    return {
        "macd": round(float(macd_line.iloc[-1]), 4),
        "signal": round(float(sig_line.iloc[-1]), 4),
        "histogram": hist_val,
        "trend": "golden_cross" if hist_val > 0 else "dead_cross",
    }


def _calc_sma25(close: pd.Series) -> dict:
    if _TA_AVAILABLE:
        sma = ta.sma(close, length=25)
        sma_val = round(float(sma.dropna().iloc[-1]), 2)
    else:
        sma_val = round(float(close.rolling(25).mean().dropna().iloc[-1]), 2)
    price = round(float(close.iloc[-1]), 2)
    diff_pct = round((price - sma_val) / sma_val * 100, 2)
    return {
        "sma25": sma_val,
        "latest_price": price,
        "diff_pct": diff_pct,
        "position": "above" if price > sma_val else "below",
    }


def _signal_summary(rsi: float, macd: dict, sma: dict) -> str:
    parts = []
    if rsi < 30:
        parts.append(f"RSI売られすぎ({rsi}) ← 買いシグナル")
    elif rsi < 40:
        parts.append(f"RSI弱め({rsi})")
    elif rsi > 70:
        parts.append(f"RSI買われすぎ({rsi}) ← 売りシグナル")
    elif rsi > 60:
        parts.append(f"RSI強め({rsi})")
    else:
        parts.append(f"RSI中立({rsi})")

    label = "ゴールデンクロス" if macd["trend"] == "golden_cross" else "デッドクロス"
    parts.append(f"MACD {label}(histogram={macd['histogram']:+.4f})")

    direction = "上方" if sma["position"] == "above" else "下方"
    parts.append(f"SMA25 {direction}乖離{sma['diff_pct']:+.2f}%")

    return " / ".join(parts)


# ------------------------------------------------------------------ #
# LLM trend classification                                            #
# ------------------------------------------------------------------ #

def _llm_classify_trend(ticker: str, rsi: float, macd: dict, sma: dict) -> tuple[str, str]:
    """
    Ask LLM to judge short-term trend as positive / negative / neutral.
    Returns (trend, reason).
    """
    rsi_label = (
        f"{rsi}（売られすぎ）" if rsi < 30 else
        f"{rsi}（買われすぎ）" if rsi > 70 else
        f"{rsi}（中立域）"
    )
    macd_label = "ゴールデンクロス（上昇圧力）" if macd["trend"] == "golden_cross" else "デッドクロス（下落圧力）"
    sma_label = (
        f"SMA25より{sma['diff_pct']:+.2f}%（上方乖離）" if sma["position"] == "above"
        else f"SMA25より{sma['diff_pct']:+.2f}%（下方乖離）"
    )

    prompt = (
        f"あなたはプロのテクニカルアナリストです。\n"
        f"銘柄【{ticker}】の直近テクニカル指標を読み、スイングトレードの観点から"
        f"短期トレンドを判定してください。\n\n"
        f"【指標】\n"
        f"  RSI(14)  : {rsi_label}\n"
        f"  MACD     : {macd_label}\n"
        f"            macd={macd['macd']:.4f}, signal={macd['signal']:.4f}, histogram={macd['histogram']:+.4f}\n"
        f"  SMA25    : {sma_label}（SMA={sma['sma25']:.2f}, 現在値={sma['latest_price']:.2f}）\n\n"
        f"必ず以下のJSON形式のみで出力してください（余分なテキスト不要）:\n"
        f'{{"trend": "positive"|"negative"|"neutral", "reason": "1~2文の日本語の根拠"}}\n'
        f"positive=強気（上昇見込み）, negative=弱気（下落リスク）, neutral=中立"
    )
    try:
        raw = _get_llm().invoke(prompt).content.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            return str(parsed.get("trend", "neutral")), str(parsed.get("reason", ""))
    except Exception as e:
        return "neutral", f"LLM分類エラー: {e}"
    return "neutral", "パース失敗"


# ------------------------------------------------------------------ #
# Public: single-ticker analysis                                       #
# ------------------------------------------------------------------ #

def analyze_ticker(ticker: str, period: str = "6mo") -> dict:
    """
    Full analysis for one ticker: fetch OHLCV, compute RSI/MACD/SMA25,
    classify trend with LLM.

    Returns:
        {
            "ticker": str,
            "period": str,
            "latest_date": str,      # ISO date of most recent bar
            "latest_price": float,
            "indicators": {
                "rsi": {"value": float, "period": 14},
                "macd": {"macd": float, "signal": float,
                         "histogram": float, "trend": str},
                "sma25": {"sma25": float, "latest_price": float,
                          "diff_pct": float, "position": str},
            },
            "signal_summary": str,   # human-readable one-liner
            "trend": "positive"|"negative"|"neutral",
            "trend_reason": str,
            "error": str | None,
        }
    """
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df.empty or len(df) < 30:
            return {
                "ticker": ticker, "period": period, "error":
                f"データ不足（{len(df)}行）。ティッカー '{ticker}' を確認してください。"
            }

        close = df["Close"].squeeze()
        latest_date = df.index[-1]
        latest_date_str = (
            latest_date.date().isoformat()
            if hasattr(latest_date, "date")
            else str(latest_date)[:10]
        )

        rsi = _calc_rsi(close)
        macd = _calc_macd(close)
        sma = _calc_sma25(close)
        summary = _signal_summary(rsi, macd, sma)
        trend, reason = _llm_classify_trend(ticker, rsi, macd, sma)

        return {
            "ticker": ticker,
            "period": period,
            "latest_date": latest_date_str,
            "latest_price": sma["latest_price"],
            "indicators": {
                "rsi": {"value": rsi, "period": 14},
                "macd": macd,
                "sma25": sma,
            },
            "signal_summary": summary,
            "trend": trend,
            "trend_reason": reason,
            "error": None,
        }
    except Exception as e:
        return {"ticker": ticker, "period": period, "error": str(e)}


# ------------------------------------------------------------------ #
# Public: batch mode (backward compatible)                             #
# ------------------------------------------------------------------ #

def _analyze_batch(tickers: dict[str, str], period: str) -> dict:
    """Batch analysis without LLM — keeps the existing return format."""
    results: dict = {}
    for name, symbol in tickers.items():
        try:
            df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
            if df.empty or len(df) < 30:
                results[name] = {
                    "ticker": symbol,
                    "error": f"データ不足（{len(df)}行）。'{symbol}' を確認してください。",
                }
                continue
            close = df["Close"].squeeze()
            rsi = _calc_rsi(close)
            macd = _calc_macd(close)
            sma = _calc_sma25(close)
            results[name] = {
                "ticker": symbol,
                "rsi": rsi,
                "macd": macd,
                "ma25": sma,        # keep legacy key name "ma25"
                "signal_summary": _signal_summary(rsi, macd, sma),
                "error": None,
            }
        except Exception as e:
            results[name] = {"ticker": symbol, "error": str(e)}
    return {"tickers": results}


# ------------------------------------------------------------------ #
# Skill entry point                                                    #
# ------------------------------------------------------------------ #

def run(
    tickers: dict[str, str] | None = None,
    ticker: str | None = None,
    period: str = "6mo",
) -> dict:
    """
    Dispatch to single-ticker or batch mode.

    Single mode : run(ticker="AAPL")          → full analysis with LLM trend
    Batch mode  : run(tickers={"name":"sym"}) → existing format, no LLM
    """
    if ticker:
        return analyze_ticker(ticker, period)
    if tickers:
        return _analyze_batch(tickers, period)
    return {"error": "引数 'ticker' または 'tickers' を指定してください。"}
