"""
Skill: news_monitor
Permission: NewsAgent only

Two fetch modes:
  1. ticker mode  — yf.Ticker(symbol).news  (US stocks)
  2. company mode — Google News RSS          (Japanese stocks)
"""

import json
import re
import time
import warnings
import urllib.parse
import feedparser
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


def _analyze_sentiment(title: str, summary: str, subject: str) -> tuple[str, str]:
    """Call LLM once to classify sentiment for a single news item."""
    text = title
    if summary:
        text += f"\n概要: {summary[:200]}"

    prompt = (
        f"あなたはプロの株式投資アナリストです。\n"
        f"対象【{subject}】に関する以下のニュースを読み、"
        f"株価への短期的な影響を判定してください。\n\n"
        f"【ニュース】\n{text}\n\n"
        f"必ず以下のJSON形式のみで出力してください（余分なテキスト不要）:\n"
        f'{{"sentiment": "positive"|"negative"|"neutral", "reason": "1~2文の日本語の理由"}}'
    )
    try:
        raw = _get_llm().invoke(prompt).content.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            return str(parsed.get("sentiment", "neutral")), str(parsed.get("reason", ""))
    except Exception as e:
        return "neutral", f"LLM解析エラー: {e}"
    return "neutral", "パース失敗"


# ------------------------------------------------------------------ #
# Mode 1: yfinance (US stocks)                                        #
# ------------------------------------------------------------------ #

def _extract_news_fields(item: dict) -> tuple[str, str, str]:
    """Extract (title, summary, link) from yfinance news item regardless of format."""
    # yfinance 1.x: {'id': ..., 'content': {'title': ..., 'summary': ..., 'canonicalUrl': {...}}}
    content = item.get("content", {})
    if content:
        title = content.get("title", "")
        summary = content.get("summary", "")
        link = (content.get("canonicalUrl") or {}).get("url", "") or content.get("link", "")
        return title, summary, link

    # yfinance 0.x: {'title': ..., 'link': ..., ...}
    title = item.get("title", "")
    summary = item.get("summary", "")
    link = item.get("link", "")
    return title, summary, link


def fetch_ticker_news(ticker: str, max_articles: int = 3) -> dict:
    """
    Fetch news for a US ticker via yfinance and classify sentiment with LLM.

    Returns:
        {
            "ticker": str,
            "articles": [
                {
                    "ticker": str,
                    "title": str,
                    "summary": str,
                    "link": str,
                    "sentiment": "positive" | "negative" | "neutral",
                    "reason": str,
                }
            ],
            "new_count": int,
        }
    """
    raw_news = yf.Ticker(ticker).news or []
    articles = []

    for item in raw_news[:max_articles]:
        title, summary, link = _extract_news_fields(item)
        if not title:
            continue

        sentiment, reason = _analyze_sentiment(title, summary, ticker)
        articles.append({
            "ticker": ticker,
            "title": title,
            "summary": summary[:200] if summary else "",
            "link": link,
            "sentiment": sentiment,
            "reason": reason,
        })
        time.sleep(2)  # API rate limit

    return {"ticker": ticker, "articles": articles, "new_count": len(articles)}


# ------------------------------------------------------------------ #
# Mode 2: Google News RSS (Japanese companies)                        #
# ------------------------------------------------------------------ #

def _fetch_rss_news(companies: list[str], seen_urls: set) -> dict:
    """Fetch from Google News RSS for Japanese company names."""
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

        if is_new:
            sentiment, reason = _analyze_sentiment(title, "", company)
            seen_urls.add(link)
            time.sleep(2)
        else:
            sentiment, reason = "neutral", "分析スキップ（既出記事）"

        articles.append({
            "company": company,
            "title": title,
            "link": link,
            "sentiment": sentiment,
            "reason": reason,
            "is_new": is_new,
        })

    new_count = sum(1 for a in articles if a.get("is_new", True))
    return {"articles": articles, "new_count": new_count}


# ------------------------------------------------------------------ #
# Skill entry point                                                    #
# ------------------------------------------------------------------ #

def run(
    companies: list[str] | None = None,
    ticker: str | None = None,
    max_articles: int = 3,
    seen_urls: set | None = None,
) -> dict:
    """
    Dispatch to yfinance (ticker) or RSS (companies) mode.

    Args:
        ticker:      US stock ticker, e.g. "AAPL" — uses yfinance
        companies:   Japanese company names        — uses Google News RSS
        max_articles: max articles to fetch in ticker mode
        seen_urls:   deduplicate set for RSS mode
    """
    if ticker:
        return fetch_ticker_news(ticker, max_articles)
    return _fetch_rss_news(companies or [], seen_urls or set())
