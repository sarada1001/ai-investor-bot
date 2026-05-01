"""
Skill: edgar_fetcher
Permission: FundamentalAgent only

Downloads the latest 10-K / 10-Q filing from SEC EDGAR for a given ticker,
extracts clean plain text, saves it to unzipped_docs/{ticker}/, and
incrementally ingests it into the shared ChromaDB.

Uses the SEC EDGAR public REST API (no API key needed).
SEC fair-use policy requires a User-Agent header:
  SEC_USER_AGENT_NAME  — app/company name  (set in .env)
  SEC_USER_AGENT_EMAIL — contact e-mail    (set in .env)
"""

from __future__ import annotations

import os
import re
import time
import warnings
from pathlib import Path

from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

# --------------------------------------------------------------------------- #
# Configuration                                                                #
# --------------------------------------------------------------------------- #

_USER_AGENT = (
    f"{os.getenv('SEC_USER_AGENT_NAME', 'ECC InvestorAgent')} "
    f"{os.getenv('SEC_USER_AGENT_EMAIL', 'user@example.com')}"
)
_HEADERS = {"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip, deflate"}

_SEC_BASE  = "https://www.sec.gov"
_DATA_BASE = "https://data.sec.gov"
_TICKERS_URL     = f"{_SEC_BASE}/files/company_tickers.json"
_SUBMISSIONS_URL = f"{_DATA_BASE}/submissions/CIK{{cik:010d}}.json"

_OUTPUT_BASE  = Path("unzipped_docs")
_CHROMA_DIR   = "chroma_db_saved"
_CHUNK_SIZE   = 500
_CHUNK_OVERLAP = 50
_MAX_CHARS    = 500_000   # ~500 KB of plain text per filing; keeps ChromaDB lean

_RETRY_DELAYS = (5, 15, 30)

# --------------------------------------------------------------------------- #
# HTTP helper with rate-limit retry                                            #
# --------------------------------------------------------------------------- #

def _get(url: str, timeout: int = 90) -> "requests.Response":
    import requests as _req
    for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        try:
            r = _req.get(url, headers=_HEADERS, timeout=timeout)
            if r.status_code == 429:
                wait = delay if delay is not None else 60
                print(f"  [EDGAR] 429 rate-limit (attempt {attempt + 1}), {wait}s 待機...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except Exception as exc:
            if delay is None:
                raise
            print(f"  [EDGAR] リクエストエラー ({exc}), {delay}s 後にリトライ...")
            time.sleep(delay)
    raise RuntimeError(f"EDGAR リクエスト失敗: {url}")


# --------------------------------------------------------------------------- #
# CIK resolution                                                              #
# --------------------------------------------------------------------------- #

_cik_cache: dict[str, int] = {}


def _resolve_cik(ticker: str) -> int:
    """Resolve a ticker symbol to its SEC CIK number via company_tickers.json."""
    t = ticker.upper()
    if t in _cik_cache:
        return _cik_cache[t]
    data = _get(_TICKERS_URL).json()
    for entry in data.values():
        if str(entry.get("ticker", "")).upper() == t:
            cik = int(entry["cik_str"])
            _cik_cache[t] = cik
            return cik
    raise ValueError(
        f"Ticker '{ticker}' が SEC company_tickers.json に見つかりません。"
        f"正式なティッカーシンボルか確認してください。"
    )


# --------------------------------------------------------------------------- #
# Latest filing lookup                                                         #
# --------------------------------------------------------------------------- #

def _search_filings_page(entries: dict, form_type: str) -> dict | None:
    """Search a single page of filing entries for the latest matching form."""
    forms    = entries.get("form", [])
    accnos   = entries.get("accessionNumber", [])
    dates    = entries.get("filingDate", [])
    prim_docs = entries.get("primaryDocument", [])

    for i, form in enumerate(forms):
        if form == form_type:
            return {
                "accession":    accnos[i],
                "primary_doc":  prim_docs[i] if i < len(prim_docs) else "",
                "date":         dates[i],
                "form":         form,
            }
    return None


def _get_latest_filing(cik: int, form_type: str) -> dict | None:
    """
    Return metadata for the most recent filing of form_type.
    Searches recent filings first, then paginated archive pages.
    """
    url  = _SUBMISSIONS_URL.format(cik=cik)
    data = _get(url).json()

    # Check recent (fast path)
    hit = _search_filings_page(data.get("filings", {}).get("recent", {}), form_type)
    if hit:
        return {**hit, "cik": cik}

    # Check paginated older filings
    for page_ref in data.get("filings", {}).get("files", []):
        page_data = _get(f"{_DATA_BASE}/submissions/{page_ref['name']}").json()
        hit = _search_filings_page(page_data, form_type)
        if hit:
            return {**hit, "cik": cik}
        time.sleep(0.3)   # polite delay between archive pages

    return None


# --------------------------------------------------------------------------- #
# HTML → clean text                                                            #
# --------------------------------------------------------------------------- #

def _html_to_text(html: str) -> str:
    """
    Strip HTML tags (including iXBRL markup used in modern EDGAR filings),
    XBRL hidden elements, and normalize whitespace.
    Uses lxml for speed on large filings.
    """
    from bs4 import BeautifulSoup

    # lxml is faster than html.parser for large files; fall back gracefully
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements
    for tag in soup(["script", "style", "head", "header", "footer",
                     "nav", "ix:header", "ix:hidden", "xbrli:xbrl"]):
        tag.decompose()

    text = soup.get_text(separator="\n")

    # Normalize: strip blank lines, collapse consecutive newlines
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text  = "\n".join(lines)
    text  = re.sub(r"\n{3,}", "\n\n", text)

    return text


# --------------------------------------------------------------------------- #
# ChromaDB incremental ingest                                                  #
# --------------------------------------------------------------------------- #

def _ingest_to_chroma(
    txt_path: Path,
    ticker: str,
    form: str,
    date: str,
    persist_dir: str,
) -> int:
    """
    Load the saved text file, split into chunks, and upsert into ChromaDB.
    Adds source / ticker / form / date metadata to every chunk.
    Returns the number of chunks added.
    """
    from langchain_community.document_loaders import TextLoader
    from langchain_text_splitters import CharacterTextSplitter
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_community.vectorstores import Chroma

    docs = TextLoader(str(txt_path), encoding="utf-8").load()
    for doc in docs:
        doc.metadata.update({
            "source": str(txt_path),
            "ticker": ticker,
            "form":   form,
            "date":   date,
        })

    splitter = CharacterTextSplitter(
        chunk_size=_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
        separator="\n",
    )
    chunks = splitter.split_documents(docs)

    embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")

    if os.path.exists(persist_dir):
        db = Chroma(persist_directory=persist_dir, embedding_function=embeddings)
        db.add_documents(chunks)
    else:
        db = Chroma.from_documents(chunks, embeddings, persist_directory=persist_dir)

    return len(chunks)


# --------------------------------------------------------------------------- #
# Core: fetch one filing                                                       #
# --------------------------------------------------------------------------- #

def fetch_filing(
    ticker: str,
    form_type: str = "10-K",
    persist_dir: str = _CHROMA_DIR,
    max_chars: int = _MAX_CHARS,
) -> dict:
    """
    Download, clean, save, and ingest the latest SEC filing for ticker.

    Returns:
        {
            "ticker":       str,
            "form":         str,
            "date":         str,
            "output_path":  str,
            "chars":        int,
            "chunks_added": int,
            "already_exists": bool,   # True when file was already on disk
            "error":        str | None,
        }
    """
    ticker = ticker.upper()

    try:
        # 1. Resolve CIK -------------------------------------------------
        print(f"  [EDGAR] {ticker} の CIK を解決中...")
        cik = _resolve_cik(ticker)
        print(f"  [EDGAR] CIK = {cik}")
        time.sleep(0.3)

        # 2. Find latest filing ------------------------------------------
        print(f"  [EDGAR] 最新の {form_type} を検索中...")
        filing = _get_latest_filing(cik, form_type)
        if filing is None:
            return {
                "ticker": ticker, "form": form_type, "date": "",
                "output_path": "", "chars": 0, "chunks_added": 0,
                "already_exists": False,
                "error": f"{ticker} の {form_type} が EDGAR に見つかりません。",
            }

        acc         = filing["accession"]
        acc_nodash  = acc.replace("-", "")
        date        = filing["date"]
        primary_doc = filing["primary_doc"]
        print(f"  [EDGAR] 発見: {form_type} / {date} / accession={acc}")
        time.sleep(0.3)

        # 3. Skip if file already saved ----------------------------------
        out_dir  = _OUTPUT_BASE / ticker
        out_path = out_dir / f"{ticker}_{form_type.replace('/', '_')}_{date.replace('-', '')}.txt"

        if out_path.exists():
            print(f"  [EDGAR] ファイル既存のためダウンロードをスキップ: {out_path}")
            return {
                "ticker": ticker, "form": form_type, "date": date,
                "output_path": str(out_path),
                "chars": out_path.stat().st_size,
                "chunks_added": 0,
                "already_exists": True,
                "error": None,
            }

        # 4. Download document -------------------------------------------
        if not primary_doc:
            # Fallback: pull the index page and find the largest .htm
            idx_url = (
                f"{_SEC_BASE}/Archives/edgar/data/{cik}/{acc_nodash}/"
                f"{acc}-index.json"
            )
            try:
                idx_data = _get(idx_url).json()
                items = idx_data.get("directory", {}).get("item", [])
                htm_items = [d for d in items if d.get("name", "").endswith((".htm", ".html"))]
                htm_items.sort(key=lambda d: int(d.get("size", 0)), reverse=True)
                primary_doc = htm_items[0]["name"] if htm_items else ""
            except Exception:
                pass

        if not primary_doc:
            return {
                "ticker": ticker, "form": form_type, "date": date,
                "output_path": "", "chars": 0, "chunks_added": 0,
                "already_exists": False,
                "error": "primaryDocument が特定できませんでした。",
            }

        doc_url = (
            f"{_SEC_BASE}/Archives/edgar/data/{cik}/{acc_nodash}/{primary_doc}"
        )
        print(f"  [EDGAR] ダウンロード中: {doc_url}")
        raw = _get(doc_url, timeout=180).text
        print(f"  [EDGAR] ダウンロード完了 ({len(raw):,} chars)")
        time.sleep(0.3)

        # 5. Clean HTML to plain text ------------------------------------
        print(f"  [EDGAR] HTML をクリーニング中...")
        if primary_doc.lower().endswith((".htm", ".html", ".xhtml")):
            clean_text = _html_to_text(raw)
        else:
            clean_text = raw  # already plain text (.txt submissions)

        if len(clean_text) > max_chars:
            clean_text = clean_text[:max_chars]
            print(f"  [EDGAR] {max_chars:,} chars に切り詰めました。")

        # 6. Save to unzipped_docs/{ticker}/ -----------------------------
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(clean_text, encoding="utf-8")
        print(f"  [EDGAR] 保存完了: {out_path} ({len(clean_text):,} chars)")

        # 7. Ingest into ChromaDB ----------------------------------------
        print(f"  [EDGAR] ChromaDB にインジェスト中...")
        n_chunks = _ingest_to_chroma(out_path, ticker, form_type, date, persist_dir)
        print(f"  [EDGAR] ChromaDB インジェスト完了 ({n_chunks} チャンク追加)")

        return {
            "ticker":        ticker,
            "form":          form_type,
            "date":          date,
            "output_path":   str(out_path),
            "chars":         len(clean_text),
            "chunks_added":  n_chunks,
            "already_exists": False,
            "error":         None,
        }

    except Exception as exc:
        return {
            "ticker": ticker, "form": form_type, "date": "",
            "output_path": "", "chars": 0, "chunks_added": 0,
            "already_exists": False,
            "error": str(exc),
        }


# --------------------------------------------------------------------------- #
# Skill entry point                                                            #
# --------------------------------------------------------------------------- #

def run(
    ticker: str,
    form_type: str = "10-K",
    prefer_quarterly: bool = False,
    persist_dir: str = _CHROMA_DIR,
    max_chars: int = _MAX_CHARS,
) -> dict:
    """
    ECC skill entry point — fetch the latest SEC filing for a US-listed ticker.

    Args:
        ticker:           US stock ticker symbol (e.g. "AAPL", "MSFT", "NVDA")
        form_type:        "10-K" (annual) | "10-Q" (quarterly). Default: "10-K"
        prefer_quarterly: If True, tries "10-Q" first, falls back to "10-K"
        persist_dir:      ChromaDB persist directory to ingest into
        max_chars:        Maximum plain-text characters to save (default 500,000)

    Returns the same dict as fetch_filing().
    """
    if prefer_quarterly:
        result = fetch_filing(ticker, "10-Q", persist_dir, max_chars)
        if result.get("error"):
            print(f"  [EDGAR] 10-Q 未取得 → 10-K にフォールバック...")
            result = fetch_filing(ticker, "10-K", persist_dir, max_chars)
        return result
    return fetch_filing(ticker, form_type, persist_dir, max_chars)
