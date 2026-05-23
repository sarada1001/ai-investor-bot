"""
Skill: social_monitor
Permission: SocialAgent only

Fetches social media posts about a ticker (Reddit r/wallstreetbets / StockTwits style).
Since Reddit/StockTwits public APIs now require authentication, this module supports
two modes controlled by the SOCIAL_USE_MOCK environment variable:

  SOCIAL_USE_MOCK=false (default / 本番):
      データなし扱いでニュートラル 0.0 を返す。
      モックバイアスが加重スコアに混入するのを防ぐ安全側設定。

  SOCIAL_USE_MOCK=true (開発/テスト):
      hype_mode=True → _HYPE_POSTS モック投稿をLLMで評価する。
      hype_mode=False → _ANALYTICAL_POSTS モック投稿をLLMで評価する。

LLM 評価項目:
  - sentiment    : POSITIVE / NEUTRAL / NEGATIVE
  - hype_score   : 0.0–1.0
      high (0.7+) = emoji-heavy, no fundamental backing (pump & dump pattern)
      low  (<0.3) = concrete FA/TA references, reasoned thesis
"""

from __future__ import annotations

import json
import os
import re
import warnings
from dotenv import load_dotenv
from skills.llm_factory import get_llm_instance

warnings.filterwarnings("ignore")
load_dotenv()

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        # json_mode=True: Ollama に format="json" を設定して構造化出力を強制
        _llm = get_llm_instance(json_mode=True)
    return _llm


def _is_mock_enabled() -> bool:
    """SOCIAL_USE_MOCK 環境変数を確認する（デフォルト: false = 本番中立モード）。"""
    load_dotenv(override=False)
    return os.getenv("SOCIAL_USE_MOCK", "false").lower() == "true"


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
        start = raw.find("{")
        if start != -1:
            parsed, _ = json.JSONDecoder().raw_decode(raw, start)
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
    SNS センチメントを取得・分類する。

    SOCIAL_USE_MOCK=false（デフォルト / 本番）:
        実際の SNS API が利用できないため、中立 (NEUTRAL, hype_score=0.0) を返す。
        モックバイアスが加重スコアに混入しないよう安全側に倒す。

    SOCIAL_USE_MOCK=true（開発/テスト時）:
        モック投稿を生成して LLM でセンチメント分類を行う（従来動作）。

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
    if not _is_mock_enabled():
        # 本番モード: SNS API 未接続のため中立を返す（スコアへの不当な影響を排除）
        return {
            "ticker":        ticker,
            "sentiment":     "NEUTRAL",
            "hype_score":    0.0,
            "reason":        "SNS API 未接続（SOCIAL_USE_MOCK=false）。中立スコアを使用。",
            "post_count":    0,
            "source":        "unavailable",
            "posts_preview": [],
        }

    # 開発/テストモード: モック投稿を LLM で評価（従来動作）
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
