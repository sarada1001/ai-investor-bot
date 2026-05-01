"""
Skill: social_monitor
Permission: SocialAgent only

Fetches social media posts about a ticker (Reddit r/wallstreetbets / StockTwits style).
Since Reddit/StockTwits public APIs now require authentication, realistic mock posts
are generated and fed to an LLM that evaluates:
  - sentiment    : POSITIVE / NEUTRAL / NEGATIVE
  - hype_score   : 0.0–1.0
      high (0.7+) = emoji-heavy, no fundamental backing (pump & dump pattern)
      low  (<0.3) = concrete FA/TA references, reasoned thesis
"""

from __future__ import annotations

import json
import re
import warnings
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


# ─── Mock post templates ──────────────────────────────────────────────────── #

_HYPE_POSTS: list[str] = [
    "🚀🚀🚀 {t} TO THE MOON!!! YOLO'd my whole paycheck into calls. Diamond hands 💎🙌 this is THE ONE!!!",
    "BUYING MORE {t} CALLS RN 🔥🔥 gonna 10x, trust me bro. GET IN BEFORE IT'S TOO LATE!!!",
    "{t} short squeeze incoming 🚀 apes strong together!! anyone NOT buying is ngmi fr fr",
    "just dumped my 401k into {t} options. wife's boyfriend says I'm dumb but we'll see 😂💰💰",
    "{t} BULLS RISE UP 🦍💪 short sellers will get REKT. $300 PT by EOY no cap!!! 🚀🌕",
]

_ANALYTICAL_POSTS: list[str] = [
    "{t} Q2 earnings: 18% YoY revenue growth, services segment up 24% — solid beat vs analyst consensus.",
    "Analyzed {t} 10-Q: gross margins expanded 200bps YoY. FCF yield at 3.8%, buyback pace accelerating.",
    "TA setup on {t}: MACD golden cross confirmed on daily, RSI at 57 — room to run. Key support $175.",
    "{t} institutional ownership up 3% last quarter per 13F. Smart money quietly accumulating.",
    "DCF for {t} gives ~$210 fair value (WACC 8%, 5yr terminal growth 3%). Current $185 = 13% upside.",
]


def _make_posts(ticker: str, hype_mode: bool) -> list[str]:
    pool = _HYPE_POSTS if hype_mode else _ANALYTICAL_POSTS
    return [p.replace("{t}", ticker) for p in pool]


# ─── LLM analysis ─────────────────────────────────────────────────────────── #

def _llm_analyze(ticker: str, posts: list[str]) -> dict:
    posts_block = "\n".join(f"- {p}" for p in posts)
    prompt = (
        f"以下は株式ティッカー {ticker} に関するSNS投稿（Reddit r/wallstreetbets・StockTwits風）です。\n\n"
        f"【投稿リスト】\n{posts_block}\n\n"
        f"以下の3点を評価してください。\n"
        f"① overall_sentiment: 全体的なセンチメント (\"POSITIVE\", \"NEUTRAL\", \"NEGATIVE\" のいずれか)\n"
        f"② hype_score: 0.0〜1.0 の買い煽り(Hype)スコア\n"
        f"   - 🚀💎YOLO等の絵文字・感情語が多く、PER・売上・チャート根拠なし → 高い(0.7〜1.0)\n"
        f"   - 財務指標・DCF・テクニカル指標への具体的言及あり          → 低い(0.0〜0.3)\n"
        f"③ reason: 判定理由（英語1〜2文）\n\n"
        f"必ず以下のJSON形式のみで出力してください（余分なテキスト不要）:\n"
        '{{"overall_sentiment": "POSITIVE"|"NEUTRAL"|"NEGATIVE", '
        '"hype_score": 0.0-1.0, "reason": "English 1-2 sentences"}}'
    )
    try:
        raw = _get_llm().invoke(prompt).content.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            return {
                "sentiment":  str(parsed.get("overall_sentiment", "NEUTRAL")).upper(),
                "hype_score": float(parsed.get("hype_score", 0.5)),
                "reason":     str(parsed.get("reason", "")),
            }
    except Exception as e:
        return {"sentiment": "NEUTRAL", "hype_score": 0.5, "reason": f"LLM error: {e}"}
    return {"sentiment": "NEUTRAL", "hype_score": 0.5, "reason": "Parse failed"}


# ─── Public API ───────────────────────────────────────────────────────────── #

def fetch_social_sentiment(ticker: str, hype_mode: bool = True) -> dict:
    """
    Generate mock social posts and classify with LLM.

    Returns:
        {
            "ticker":        str,
            "sentiment":     "POSITIVE" | "NEUTRAL" | "NEGATIVE",
            "hype_score":    float,   # 0.0-1.0
            "reason":        str,
            "post_count":    int,
            "source":        str,
            "posts_preview": list[str],
        }
    """
    posts    = _make_posts(ticker, hype_mode=hype_mode)
    analysis = _llm_analyze(ticker, posts)
    return {
        "ticker":        ticker,
        "sentiment":     analysis["sentiment"],
        "hype_score":    analysis["hype_score"],
        "reason":        analysis["reason"],
        "post_count":    len(posts),
        "source":        "mock_reddit_wsb",
        "posts_preview": posts[:2],
    }


def run(ticker: str = "AAPL", hype_mode: bool = True) -> dict:
    """Skill entry point."""
    return fetch_social_sentiment(ticker, hype_mode=hype_mode)
