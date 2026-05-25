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
from skills.api_guard import yf_download_safe
from skills.llm_factory import get_llm_instance

warnings.filterwarnings("ignore")
load_dotenv()

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm_instance()
    return _llm


# ------------------------------------------------------------------ #
# Indicator helpers                                                    #
# ------------------------------------------------------------------ #

def _analyze_spy(period: str) -> dict:
    df = yf_download_safe("SPY", period=period, progress=False, auto_adjust=True)
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
    df = yf_download_safe("^VIX", period=period, progress=False, auto_adjust=True)
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

def _derive_macro_score(spy: dict, vix: dict) -> float:
    """
    LLM 不使用の数学的スコア算出（LLM フォールバック / 保険用）。
    SPY 乖離・5日リターン・VIX の 3 軸を重み付き合成して -1.0〜+1.0 を返す。

    SPY 乖離  (30%): ±5% を ±1.0 に正規化
    5日リターン(30%): ±3% を ±1.0 に正規化
    VIX      (40%): VIX=20 が中立, <10 → +1.0, >30 → -1.0（逆スケール）
    """
    spy_score = max(-1.0, min(1.0, spy["diff_pct"] / 5.0))
    ret_score = max(-1.0, min(1.0, spy["return_5d_pct"] / 3.0))
    vix_score = max(-1.0, min(1.0, (20.0 - vix["latest"]) / 10.0))
    return round(0.3 * spy_score + 0.3 * ret_score + 0.4 * vix_score, 4)


def _llm_classify(spy: dict, vix: dict) -> tuple[str, float, str]:
    """
    Ask LLM to judge macro environment with a continuous score.
    Returns (trend, score, reason).
      trend : "positive" | "negative" | "neutral"
      score : float in [-1.0, +1.0]  (LLM 失敗時は数学的フォールバック)
      reason: str
    """
    spy_label = (
        f"SPY={spy['latest_price']:.2f}, SMA乖離{spy['diff_pct']:+.2f}%"
        f"（{'上方' if spy['position'] == 'above' else '下方'}乖離）, "
        f"直近5日リターン{spy['return_5d_pct']:+.2f}%"
    )
    vix_label = (
        f"VIX={vix['latest']:.2f}（{vix['level']}水準, {vix['trend']}）, "
        f"5日平均={vix['avg_5d']:.2f}"
    )

    math_fallback = _derive_macro_score(spy, vix)

    prompt = (
        "あなたはプロのマクロアナリストです。\n"
        "以下の市場全体の指標を読み、スイングトレードに対する現在の相場環境を判定してください。\n\n"
        f"【S&P 500 (SPY)】\n{spy_label}\n\n"
        f"【恐怖指数 (VIX)】\n{vix_label}\n\n"
        "必ず以下のJSON形式のみで出力してください（余分なテキスト不要）:\n"
        '{"score": 0.45, "trend": "positive"|"negative"|"neutral", "reason": "1~2文の日本語の根拠"}\n'
        "score: -1.0（極めてリスクオフ）〜 +1.0（極めてリスクオン）の実数。"
        "単純な +1/0/-1 ではなく、指標の複合的な強度を反映した細かい値を使うこと。\n"
        "trend: score の符号と一致させること"
        "（score > 0.1 → positive, score < -0.1 → negative, else → neutral）。\n"
        "positive=リスクオン（相場堅調・VIX低位安定）, "
        "negative=リスクオフ（相場下落・VIX高騰または急上昇）, "
        "neutral=方向感なしまたは混合シグナル"
    )
    try:
        raw = _get_llm().invoke(prompt).content.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            trend = str(parsed.get("trend", "neutral"))
            try:
                score = float(parsed.get("score", math_fallback))
                score = round(max(-1.0, min(1.0, score)), 4)
            except (TypeError, ValueError):
                score = math_fallback
            return trend, score, str(parsed.get("reason", ""))
    except Exception as e:
        return "neutral", math_fallback, f"LLM分類エラー: {e}"
    return "neutral", math_fallback, "パース失敗"


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

    trend, score, reason = _llm_classify(spy, vix)

    summary = (
        f"SPY {spy['diff_pct']:+.2f}%乖離({spy['position']}) "
        f"/ VIX={vix['latest']:.1f}({vix['level']},{vix['trend']})"
    )

    return {
        "spy": spy,
        "vix": vix,
        "signal_summary": summary,
        "trend": trend,
        "score": score,        # 連続スコア (-1.0〜+1.0)
        "trend_reason": reason,
        "error": None,
    }
