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
        # json_mode=True: Ollama に format="json" を設定して構造化出力を強制
        _llm = get_llm_instance(json_mode=True)
    return _llm


def _build_sentiment_prompt(articles: list[dict], subject: str) -> str:
    """センチメント解析用プロンプトを構築する。

    Ollama の format="json" モードは JSON オブジェクト ({}) を優先出力するため、
    配列 ([]) ではなく {"results": [...]} 形式で返すよう指示する。
    """
    blocks = []
    for i, a in enumerate(articles, 1):
        target = a.get("subject") or subject
        text = a["title"]
        if a.get("summary"):
            text += f"\nSummary: {a['summary'][:200]}"
        blocks.append(f"[Article {i}] Target stock/company: {target}\n{text}")

    articles_block = "\n\n".join(blocks)
    n = len(articles)

    return (
        "You are a professional stock market analyst.\n"
        f"Read the following {n} news article(s) and assess the short-term impact "
        "on the target stock's price for each article.\n\n"
        f"{articles_block}\n\n"
        'For each article, classify sentiment as exactly "positive", "negative", or "neutral".\n'
        # ── バイアス抑制ルール ───────────────────────────────────────────
        # Llama 3.1 はfew-shot例の値を強くコピーする。
        # 旧プロンプトが "positive" をハードコードしたことで全記事が positive に
        # 偏るアンカー効果が発生したため、3択を明示 + 保守的評価指示を追加する。
        "Default to neutral unless the article contains clear and specific directional "
        "evidence for the target stock. Negative news (risks, downgrades, missed earnings) "
        "MUST be classified as negative, not neutral.\n"
        "Respond ONLY with a JSON object in this exact format (no extra text):\n"
        "{\n"
        '  "results": [\n'
        '    {"index": 1, "sentiment": "positive|negative|neutral", "reason": "1-2 sentence reason in Japanese"},\n'
        "    ...\n"
        "  ]\n"
        "}"
    )


def _extract_results_from_json(raw: str) -> list[dict] | None:
    """LLM 出力から results リストを多段階戦略で抽出する。

    Strategy 1: json.loads() で直接パース → list か {"results": [...]} を確認
    Strategy 2: regex で [...] を抽出してパース
    Returns None if all strategies fail.
    """
    # Strategy 1: 直接 JSON パース
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # "results" キーを優先探索、次に任意の list 値を探す
            for key in ("results", "analysis", "items", "data", "articles", "output"):
                val = data.get(key)
                if isinstance(val, list):
                    return val
            # フォールバック: dict の値から最初に見つかったリストを使用
            for val in data.values():
                if isinstance(val, list) and len(val) > 0:
                    return val
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2a: 非貪欲 regex で最初の [...] を抽出
    m = re.search(r'\[.*?\]', raw, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group())
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 2b: 貪欲 regex（入れ子 JSON オブジェクトを含む配列に対応）
    m2 = re.search(r'\[.*\]', raw, re.DOTALL)
    if m2:
        try:
            parsed = json.loads(m2.group())
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return None


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

    n = len(articles)
    default = [{"sentiment": "neutral", "reason": "解析失敗"} for _ in articles]
    prompt = _build_sentiment_prompt(articles, subject)

    # 最大 2 回リトライ（LLM の確率的出力に対して堅牢化）
    for attempt in range(2):
        try:
            raw = _get_llm().invoke(prompt).content.strip()
            parsed_list = _extract_results_from_json(raw)

            if parsed_list is None:
                # 1回目の失敗ならリトライ
                if attempt == 0:
                    continue
                return default

            result = list(default)
            matched = 0
            for item in parsed_list:
                try:
                    idx = int(item.get("index", 0)) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < n:
                    sentiment = str(item.get("sentiment", "neutral")).lower()
                    # 有効値のみ受け入れる（ハルシネーション対策）
                    if sentiment not in ("positive", "negative", "neutral"):
                        sentiment = "neutral"
                    result[idx] = {
                        "sentiment": sentiment,
                        "reason":    str(item.get("reason", "")),
                    }
                    matched += 1

            # 1件もマッチしなかった場合は再試行
            if matched == 0 and attempt == 0:
                continue

            return result

        except Exception as e:
            if attempt == 0:
                continue
            return [{"sentiment": "neutral", "reason": f"LLM解析エラー: {e}"} for _ in articles]

    return default


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
