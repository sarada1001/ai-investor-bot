"""
Skill: news_monitor
Permission: NewsAgent only

Two fetch modes:
  1. ticker mode  — yf.Ticker(symbol).news  (US stocks)
  2. company mode — Google News RSS          (Japanese stocks)

API cost optimization: multiple articles are analyzed in a single batch
LLM call instead of one call per article.
"""

import json
import re
import warnings
import urllib.parse
import feedparser
import yfinance as yf
from dotenv import load_dotenv
from skills.llm_factory import get_llm_instance

warnings.filterwarnings("ignore")
load_dotenv()

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm_instance()
    return _llm


def _analyze_sentiment_batch(
    articles: list[dict],
    subject: str,
) -> list[dict]:
    """
    Classify sentiment for multiple news articles in a single LLM call.

    Args:
        articles: list of {"title": str, "summary": str, "subject": str (optional)}
                  "subject" per article overrides the top-level subject (RSS multi-company mode).
        subject:  default target ticker / company name used when article has no "subject".

    Returns:
        list of {"sentiment": str, "reason": str} aligned 1-to-1 with input.
        Falls back to neutral for any entry that cannot be parsed.
    """
    if not articles:
        return []

    blocks = []
    for i, a in enumerate(articles, 1):
        target = a.get("subject") or subject
        text = a["title"]
        if a.get("summary"):
            text += f"\n概要: {a['summary'][:200]}"
        blocks.append(f"【記事{i}】対象銘柄/企業: {target}\n{text}")

    articles_block = "\n\n".join(blocks)
    n = len(articles)

    prompt = (
        "あなたはプロの株式投資アナリストです。\n"
        f"以下の{n}件のニュースをそれぞれ読み、各記事の「対象銘柄/企業」の"
        "株価への短期的な影響を判定してください。\n\n"
        f"{articles_block}\n\n"
        "必ず以下のJSON配列形式のみで出力してください（余分なテキスト不要）:\n"
        "[\n"
        '  {"index": 1, "sentiment": "positive"|"negative"|"neutral", "reason": "1~2文の日本語の理由"},\n'
        "  ...\n"
        "]"
    )

    default = [{"sentiment": "neutral", "reason": "解析失敗"} for _ in articles]

    try:
        raw = _get_llm().invoke(prompt).content.strip()
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if not m:
            return default
        parsed = json.loads(m.group())
        if not isinstance(parsed, list):
            return default

        result = list(default)
        for item in parsed:
            try:
                idx = int(item.get("index", 0)) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < n:
                result[idx] = {
                    "sentiment": str(item.get("sentiment", "neutral")),
                    "reason":    str(item.get("reason", "")),
                }
        return result
    except Exception as e:
        return [{"sentiment": "neutral", "reason": f"LLM解析エラー: {e}"} for _ in articles]


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

    All articles are analyzed in a single batch API call (1 request regardless
    of max_articles, vs. the previous 1 request per article).

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

    # Extract fields for all candidate articles first
    extracted = []
    for item in raw_news[:max_articles]:
        title, summary, link = _extract_news_fields(item)
        if title:
            extracted.append({"title": title, "summary": summary, "link": link})

    if not extracted:
        return {"ticker": ticker, "articles": [], "new_count": 0}

    # Single batch call for all articles
    sentiments = _analyze_sentiment_batch(
        [{"title": a["title"], "summary": a["summary"]} for a in extracted],
        subject=ticker,
    )

    articles = [
        {
            "ticker":    ticker,
            "title":     a["title"],
            "summary":   a["summary"][:200] if a["summary"] else "",
            "link":      a["link"],
            "sentiment": s["sentiment"],
            "reason":    s["reason"],
        }
        for a, s in zip(extracted, sentiments)
    ]

    return {"ticker": ticker, "articles": articles, "new_count": len(articles)}


# ------------------------------------------------------------------ #
# Mode 2: Google News RSS (Japanese companies)                        #
# ------------------------------------------------------------------ #

def _fetch_rss_news(companies: list[str], seen_urls: set) -> dict:
    """Fetch from Google News RSS for Japanese company names."""
    articles = []
    new_pending: list[dict] = []  # collects new articles for batch analysis

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
            seen_urls.add(link)
            new_pending.append({
                "article_idx": len(articles),
                "title":       title,
                "subject":     company,
            })
            articles.append({
                "company":   company,
                "title":     title,
                "link":      link,
                "sentiment": "neutral",
                "reason":    "分析待ち",
                "is_new":    True,
            })
        else:
            articles.append({
                "company":   company,
                "title":     title,
                "link":      link,
                "sentiment": "neutral",
                "reason":    "分析スキップ（既出記事）",
                "is_new":    False,
            })

    # Single batch call for all new articles across companies
    if new_pending:
        batch_input = [
            {"title": p["title"], "summary": "", "subject": p["subject"]}
            for p in new_pending
        ]
        sentiments = _analyze_sentiment_batch(batch_input, subject="各記事の対象銘柄")
        for p, s in zip(new_pending, sentiments):
            idx = p["article_idx"]
            articles[idx]["sentiment"] = s["sentiment"]
            articles[idx]["reason"]    = s["reason"]

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
        ticker:       US stock ticker, e.g. "AAPL" — uses yfinance
        companies:    Japanese company names        — uses Google News RSS
        max_articles: max articles to fetch in ticker mode
        seen_urls:    deduplicate set for RSS mode
    """
    if ticker:
        return fetch_ticker_news(ticker, max_articles)
    return _fetch_rss_news(companies or [], seen_urls or set())
