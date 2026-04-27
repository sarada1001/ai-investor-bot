"""
Skill: news_monitor
Permission: NewsAgent only
"""

import time
import feedparser
import urllib.parse
import os
import warnings
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

warnings.filterwarnings("ignore")
load_dotenv()

_llm = None

def _get_llm():
    global _llm
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    return _llm


def run(companies: list[str], seen_urls: set | None = None) -> dict:
    """
    Monitor Google News RSS for given companies and return LLM sentiment analysis.

    Returns:
        {
            "articles": [
                {
                    "company": str,
                    "title": str,
                    "link": str,
                    "sentiment": "positive" | "negative" | "neutral",
                    "reason": str,
                    "is_new": bool,
                }
            ],
            "new_count": int,
        }
    """
    if seen_urls is None:
        seen_urls = set()

    llm = _get_llm()
    articles = []

    for company in companies:
        query = urllib.parse.quote(company)
        url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
        feed = feedparser.parse(url)

        if not feed.entries:
            continue

        entry = feed.entries[0]
        title = entry.title
        link = entry.link
        is_new = link not in seen_urls

        sentiment = "unknown"
        reason = "分析スキップ（既出記事）"

        if is_new:
            try:
                prompt = (
                    f"あなたはプロの株式投資アナリストです。\n"
                    f"対象企業【{company}】の以下のニュースタイトルを読み、"
                    f"株価への影響を判定してください。\n\n"
                    f"【ニュースタイトル】: {title}\n\n"
                    f"必ず以下のJSON形式のみで出力してください（余分なテキスト不要）:\n"
                    f'{{"sentiment": "positive"|"negative"|"neutral", "reason": "1~2文の理由"}}'
                )
                raw = llm.invoke(prompt).content.strip()
                # JSON部分だけ取り出す
                import json, re
                m = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    parsed = json.loads(m.group())
                    sentiment = parsed.get("sentiment", "neutral")
                    reason = parsed.get("reason", "")
                seen_urls.add(link)
                time.sleep(3)  # API rate limit
            except Exception as e:
                sentiment = "error"
                reason = str(e)

        articles.append({
            "company": company,
            "title": title,
            "link": link,
            "sentiment": sentiment,
            "reason": reason,
            "is_new": is_new,
        })

    new_count = sum(1 for a in articles if a["is_new"])
    return {"articles": articles, "new_count": new_count}
