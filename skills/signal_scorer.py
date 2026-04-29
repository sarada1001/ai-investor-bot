"""
Skill: signal_scorer
Extracts structured signals from each agent's BBS output and applies
weighted scoring to produce a deterministic BUY/HOLD/SELL decision.

Signal extraction:
  News       → LLM-classified sentiment per company  → -1 / 0 / +1
  Fundamental → LLM-parsed bullish/neutral/bearish   → -1 / 0 / +1
  Technical   → deterministic from RSI + MACD + MA25 → -1.0 to +1.0

Default weights:  News×0.20, Fundamental×0.50, Technical×0.30

Dynamic weight shifts (mutually exclusive, highest priority wins):
  Major news event  (|news_signal| ≥ 0.8) → News×0.35, FA×0.40, TA×0.25
  Earnings period   (FA query count ≥ 4)  → News×0.15, FA×0.55, TA×0.30
  Strong TA trend   (|tech_signal| ≥ 0.7) → News×0.15, FA×0.40, TA×0.45

Decision thresholds:
  score > +0.30 → BUY
  score < -0.30 → SELL
  else          → HOLD
"""

from __future__ import annotations
import json
import re

BUY_THRESHOLD = 0.30
SELL_THRESHOLD = -0.30

_DEFAULT_WEIGHTS: dict[str, float] = {
    "news": 0.20,
    "fundamental": 0.50,
    "technical": 0.30,
}


# ------------------------------------------------------------------ #
# News signal                                                          #
# ------------------------------------------------------------------ #

def _news_signal(news_data: dict, target_company: str) -> tuple[float, str]:
    """Average sentiment for target_company articles. Returns (signal, reason)."""
    articles = news_data.get("articles", [])
    company_articles = [a for a in articles if a.get("company") == target_company]
    if not company_articles:
        company_articles = articles

    if not company_articles:
        return 0.0, "ニュースなし"

    sentiment_map = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
    scores = [sentiment_map.get(a.get("sentiment", "neutral"), 0.0) for a in company_articles]
    signal = round(sum(scores) / len(scores), 4)

    parts = [
        f"{a['company']}:{a.get('sentiment','?')}"
        for a in company_articles[:3]
    ]
    return signal, " / ".join(parts)


# ------------------------------------------------------------------ #
# Technical signal (deterministic)                                     #
# ------------------------------------------------------------------ #

def _technical_signal(technical_data: dict, target_company: str) -> tuple[float, str]:
    """Compute signal from RSI, MACD, MA25. Returns (signal -1..+1, reason)."""
    data = technical_data.get("tickers", {}).get(target_company, {})

    if data.get("error"):
        return 0.0, f"データエラー: {data['error'][:40]}"

    score = 0.0
    total_w = 0.0
    parts: list[str] = []

    # RSI  (weight=0.40)
    rsi = data.get("rsi")
    if rsi is not None:
        w = 0.40
        if rsi < 30:
            s, label = 1.0, f"RSI売られすぎ({rsi})"
        elif rsi < 40:
            s, label = 0.5, f"RSI弱め({rsi})"
        elif rsi > 70:
            s, label = -1.0, f"RSI買われすぎ({rsi})"
        elif rsi > 60:
            s, label = -0.5, f"RSI強め({rsi})"
        else:
            s, label = 0.0, f"RSI中立({rsi})"
        score += s * w
        total_w += w
        parts.append(label)

    # MACD  (weight=0.30)
    macd = data.get("macd", {})
    if macd:
        w = 0.30
        if macd.get("trend") == "golden_cross":
            s, label = 1.0, "MACDゴールデンクロス"
        else:
            s, label = -1.0, "MACDデッドクロス"
        score += s * w
        total_w += w
        parts.append(label)

    # MA25  (weight=0.30)  — swing trade: mean reversion logic
    ma25 = data.get("ma25", {})
    if ma25:
        w = 0.30
        diff = ma25.get("diff_pct", 0)
        if diff < -5:
            s, label = 0.8, f"25MA大幅下方乖離{diff}%(反発期待)"
        elif diff < 0:
            s, label = -0.3, f"25MA下方乖離{diff}%(弱気)"
        elif diff < 5:
            s, label = 0.3, f"25MA上方乖離+{diff}%(強気)"
        else:
            s, label = -0.5, f"25MA大幅上方乖離+{diff}%(過熱警戒)"
        score += s * w
        total_w += w
        parts.append(label)

    if total_w == 0:
        return 0.0, "指標なし"

    signal = round(max(-1.0, min(1.0, score / total_w)), 4)
    return signal, " / ".join(parts)


# ------------------------------------------------------------------ #
# Fundamental signal (LLM)                                            #
# ------------------------------------------------------------------ #

def extract_fundamental_signal(fundamental_data: dict, llm) -> tuple[float, str]:
    """
    Call LLM once to extract a structured signal from FundamentalAgent output.
    Returns (signal -1/0/+1, reason).
    """
    queries = fundamental_data.get("queries", [])
    if not queries:
        return 0.0, "ファンダメンタルデータなし"

    analyses_text = "\n".join(
        f"Q: {q['query']}\nA: {q['analysis']}" for q in queries
    )
    prompt = (
        "以下のファンダメンタルズ分析を読み、スイングトレードの観点から"
        "株式投資シグナルを判定してください。\n\n"
        f"{analyses_text}\n\n"
        "必ず以下のJSON形式のみで出力してください（余分なテキスト不要）:\n"
        '{"signal": 1 | 0 | -1, "reason": "1~2文の根拠"}\n'
        "signal: 1=強気(財務良好・成長性高い), 0=中立, -1=弱気(業績悪化・懸念材料)"
    )
    try:
        raw = llm.invoke(prompt).content.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            signal = float(parsed.get("signal", 0))
            reason = parsed.get("reason", "")
            return round(max(-1.0, min(1.0, signal)), 4), reason
    except Exception as e:
        return 0.0, f"LLM解析エラー: {e}"
    return 0.0, "パース失敗"


# ------------------------------------------------------------------ #
# Dynamic weight adjustment                                            #
# ------------------------------------------------------------------ #

def _adjust_weights(
    news_signal: float,
    tech_signal: float,
    fundamental_data: dict,
) -> tuple[dict[str, float], str]:
    """
    Returns (weights, reason).
    Mutually exclusive: highest-priority condition wins.
    """
    query_count = len(fundamental_data.get("queries", []))

    if abs(news_signal) >= 0.8:
        weights = {"news": 0.35, "fundamental": 0.40, "technical": 0.25}
        reason = f"重大ニュース(|news|={abs(news_signal):.2f})のためNews重みを上げた"
    elif query_count >= 4:
        weights = {"news": 0.15, "fundamental": 0.55, "technical": 0.30}
        reason = f"決算期({query_count}クエリ)のためFundamental重みを上げた"
    elif abs(tech_signal) >= 0.7:
        weights = {"news": 0.15, "fundamental": 0.40, "technical": 0.45}
        reason = f"強いテクニカルトレンド(|ta|={abs(tech_signal):.2f})のためTechnical重みを上げた"
    else:
        weights = dict(_DEFAULT_WEIGHTS)
        reason = "デフォルトウェイト適用"

    # Ensure sum = 1.0 (normalization guard)
    total = sum(weights.values())
    return {k: round(v / total, 3) for k, v in weights.items()}, reason


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

def run(
    news_data: dict,
    fundamental_data: dict,
    technical_data: dict,
    target_company: str,
    fundamental_signal: float | None = None,
    fundamental_reason: str = "",
    llm=None,
) -> dict:
    """
    Compute weighted signal score for one target_company.

    Pass `fundamental_signal` and `fundamental_reason` if pre-computed
    (avoids redundant LLM calls when scoring multiple companies).

    Returns:
        {
            "action": "BUY" | "HOLD" | "SELL",
            "score": float,          # weighted sum -1..+1
            "confidence": int,       # 0-100, proportional to |score|
            "signals": {"news": float, "fundamental": float, "technical": float},
            "signal_reasons": {"news": str, "fundamental": str, "technical": str},
            "weights": {"news": float, "fundamental": float, "technical": float},
            "weight_reason": str,
        }
    """
    news_sig, news_reason = _news_signal(news_data, target_company)
    tech_sig, tech_reason = _technical_signal(technical_data, target_company)

    if fundamental_signal is None:
        if llm is not None:
            fundamental_signal, fundamental_reason = extract_fundamental_signal(fundamental_data, llm)
        else:
            fundamental_signal, fundamental_reason = 0.0, "LLMなし"

    weights, weight_reason = _adjust_weights(news_sig, tech_sig, fundamental_data)

    score = round(
        news_sig * weights["news"]
        + fundamental_signal * weights["fundamental"]
        + tech_sig * weights["technical"],
        4,
    )

    if score > BUY_THRESHOLD:
        action = "BUY"
    elif score < SELL_THRESHOLD:
        action = "SELL"
    else:
        action = "HOLD"

    confidence = min(100, int(abs(score) * 100))

    return {
        "action": action,
        "score": score,
        "confidence": confidence,
        "signals": {
            "news": news_sig,
            "fundamental": round(fundamental_signal, 4),
            "technical": tech_sig,
        },
        "signal_reasons": {
            "news": news_reason,
            "fundamental": fundamental_reason,
            "technical": tech_reason,
        },
        "weights": weights,
        "weight_reason": weight_reason,
    }
