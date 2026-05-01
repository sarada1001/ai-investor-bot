"""
Skill: macro_monitor
Permission: MacroAgent only

Monitors market-wide regime using SPY (S&P 500 ETF) and ^VIX (fear index).
Classifies environment as positive / neutral / negative via LLM.
"""

from __future__ import annotations

import json
import re
import warnings
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

warnings.filterwarnings("ignore")
load_dotenv()

_llm = None


def _get_llm() -> ChatGoogleGenerativeAI:
    global _llm
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)
    return _llm


# ------------------------------------------------------------------ #
# Indicator helpers                                                    #
# ------------------------------------------------------------------ #

def _analyze_spy(period: str) -> dict:
    df = yf.download("SPY", period=period, progress=False, auto_adjust=True)
    if df.empty or len(df) < 5:
        return {"error": f"SPY データ不足 ({len(df)} 行)"}

    close = df["Close"].squeeze()
    latest = round(float(close.iloc[-1]), 2)

    # SMA window: min(25, available bars - 1) to handle short periods
    sma_period = min(25, len(close) - 1)
    sma_val = round(float(close.rolling(sma_period).mean().dropna().iloc[-1]), 2)
    diff_pct = round((latest - sma_val) / sma_val * 100, 2)

    # 5-day return (safe against short series)
    lookback = min(5, len(close) - 1)
    ret_5d = round((latest / float(close.iloc[-(lookback + 1)]) - 1) * 100, 2)

    return {
        "latest_price": latest,
        f"sma{sma_period}": sma_val,
        "diff_pct": diff_pct,
        "position": "above" if latest > sma_val else "below",
        "return_5d_pct": ret_5d,
    }


def _analyze_vix(period: str) -> dict:
    df = yf.download("^VIX", period=period, progress=False, auto_adjust=True)
    if df.empty or len(df) < 3:
        return {"error": f"VIX データ不足 ({len(df)} 行)"}

    close = df["Close"].squeeze()
    latest = round(float(close.iloc[-1]), 2)
    avg_5d = round(float(close.iloc[-min(5, len(close)):].mean()), 2)

    level = (
        "low"      if latest < 15 else
        "normal"   if latest < 20 else
        "elevated" if latest < 30 else
        "high"
    )
    # rising/falling defined as >5% deviation from 5d avg
    trend = (
        "rising"  if latest > avg_5d * 1.05 else
        "falling" if latest < avg_5d * 0.95 else
        "stable"
    )

    return {
        "latest": latest,
        "avg_5d": avg_5d,
        "level": level,
        "trend": trend,
    }


# ------------------------------------------------------------------ #
# LLM classification                                                   #
# ------------------------------------------------------------------ #

def _llm_classify(spy: dict, vix: dict) -> tuple[str, str]:
    spy_label = (
        f"SPY={spy['latest_price']:.2f}, SMA乖離{spy['diff_pct']:+.2f}%"
        f"（{'上方' if spy['position'] == 'above' else '下方'}乖離）, "
        f"直近5日リターン{spy['return_5d_pct']:+.2f}%"
    )
    vix_label = (
        f"VIX={vix['latest']:.2f}（{vix['level']}水準, {vix['trend']}）, "
        f"5日平均={vix['avg_5d']:.2f}"
    )

    prompt = (
        "あなたはプロのマクロアナリストです。\n"
        "以下の市場全体の指標を読み、スイングトレードに対する現在の相場環境を判定してください。\n\n"
        f"【S&P 500 (SPY)】\n{spy_label}\n\n"
        f"【恐怖指数 (VIX)】\n{vix_label}\n\n"
        "必ず以下のJSON形式のみで出力してください（余分なテキスト不要）:\n"
        '{"trend": "positive"|"negative"|"neutral", "reason": "1~2文の日本語の根拠"}\n'
        "positive=リスクオン（相場堅調・VIX低位安定）, "
        "negative=リスクオフ（相場下落・VIX高騰または急上昇）, "
        "neutral=方向感なしまたは混合シグナル"
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
# Skill entry point                                                    #
# ------------------------------------------------------------------ #

def run(period: str = "1mo") -> dict:
    """
    Fetch SPY and ^VIX, classify macro environment with LLM.

    Returns:
        {
            "spy": {latest_price, sma25, diff_pct, position, return_5d_pct},
            "vix": {latest, avg_5d, level, trend},
            "signal_summary": str,
            "trend": "positive"|"negative"|"neutral",
            "trend_reason": str,
            "error": None | str,
        }
    """
    spy = _analyze_spy(period)
    vix = _analyze_vix(period)

    if spy.get("error") or vix.get("error"):
        err = spy.get("error") or vix.get("error")
        return {
            "spy": spy, "vix": vix,
            "signal_summary": f"データ取得エラー: {err}",
            "trend": "neutral",
            "trend_reason": f"データ取得失敗: {err}",
            "error": err,
        }

    trend, reason = _llm_classify(spy, vix)

    summary = (
        f"SPY {spy['diff_pct']:+.2f}%乖離({spy['position']}) "
        f"/ VIX={vix['latest']:.1f}({vix['level']},{vix['trend']})"
    )

    return {
        "spy": spy,
        "vix": vix,
        "signal_summary": summary,
        "trend": trend,
        "trend_reason": reason,
        "error": None,
    }
