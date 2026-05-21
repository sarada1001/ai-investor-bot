"""
Skill: rag_search
Permission: FundamentalAgent only

Multi-HyDE ChromaDB search over financial reports.

Two call modes:
  run(query="...")                        → single-query (backward compatible)
  run(company="...", queries=["...", ...]) → multi-query + LLM trend judgment
"""

import os
import re
import json
import time
import warnings
import functools
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from skills.llm_factory import get_llm_instance

warnings.filterwarnings("ignore")
load_dotenv()

_embeddings = None
_db_cache: dict = {}
_llm_flash = None   # flash: HyDE 仮説生成（軽量）
_llm_pro   = None   # pro  : 深層分析（gemini-2.5-pro → 2.0-flash に降格してコスト削減）

_RETRY_DELAYS = (15, 30, 60)  # seconds, escalating backoff on 429


def _call_llm(prompt: str, flash: bool = False) -> str:
    """
    Call LLM with exponential backoff on 429 RESOURCE_EXHAUSTED.
    flash=True  → 軽量（Ollama or gemini-2.0-flash）
    flash=False → 深層分析（Ollama or gemini-2.0-flash）
    """
    llm = _get_llm_flash() if flash else _get_llm_pro()
    for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        try:
            return llm.invoke(prompt).content
        except Exception as e:
            is_rate_limit = "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
            if is_rate_limit and delay is not None:
                print(f"  [RAG] Rate limit (attempt {attempt + 1}), {delay}s 待機中...")
                time.sleep(delay)
            else:
                raise
    raise RuntimeError("LLM 呼び出しがリトライ上限に達しました")


def _get_embeddings() -> HuggingFaceEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")
    return _embeddings


def _get_llm_flash():
    """軽量モデル: HyDE 仮説クエリ生成に使用"""
    global _llm_flash
    if _llm_flash is None:
        _llm_flash = get_llm_instance()
    return _llm_flash


def _get_llm_pro():
    """深層分析モデル: gemini-2.5-pro から 2.0-flash に降格してコスト削減"""
    global _llm_pro
    if _llm_pro is None:
        _llm_pro = get_llm_instance(gemini_model="gemini-2.0-flash")
    return _llm_pro


def _load_db(persist_dir: str) -> Chroma | None:
    if not os.path.exists(persist_dir):
        return None
    if persist_dir not in _db_cache:
        _db_cache[persist_dir] = Chroma(
            persist_directory=persist_dir,
            embedding_function=_get_embeddings(),
        )
    return _db_cache[persist_dir]


# ------------------------------------------------------------------ #
# Core: single HyDE-enhanced query                                    #
# ------------------------------------------------------------------ #

def _hyde_search(
    query: str,
    db: Chroma,
    top_k: int,
    ticker_filter: str | None = None,
) -> tuple[list[str], str]:
    """
    Generate hypothetical answers (HyDE), embed them with the query,
    run similarity search, and return (context_chunks, hypotheses_text).
    Falls back to direct similarity search when LLM is rate-limited.

    ticker_filter: when provided, restricts search to docs with that ticker
    metadata (used for EDGAR-ingested English filings to avoid cross-language
    retrieval noise from mixed-language databases).
    """
    try:
        hyde_prompt = (
            "あなたはプロの金融アナリストです。\n"
            "以下の【質問】に対して、実際の決算資料にどのように書かれているか、"
            "具体的な数字のプレースホルダー（例: ○○億円、○○%）を含めた"
            "【仮説的な回答】を3パターン作成してください。\n\n"
            f"【質問】\n{query}"
        )
        hypotheses = _call_llm(hyde_prompt, flash=True)  # 軽量モデルで仮説生成
        enhanced_query = query + "\n" + hypotheses
    except Exception:
        # HyDE 失敗時はクエリ直接検索にフォールバック
        enhanced_query = query
        hypotheses = ""

    where_filter = {"ticker": {"$eq": ticker_filter}} if ticker_filter else None
    docs = db.similarity_search(enhanced_query, k=top_k, filter=where_filter)
    return [doc.page_content for doc in docs], hypotheses


def _generate_analysis(query: str, context_chunks: list[str]) -> str:
    """Synthesize an analysis answer from retrieved context chunks."""
    context = "\n\n".join(context_chunks)
    prompt = (
        "あなたはプロの機関投資家・金融アナリストです。\n"
        "以下の【決算資料の該当箇所】を元に【質問】に答えてください。\n"
        "資料に書かれていないことは絶対に推測で語らないでください。\n\n"
        f"【決算資料の該当箇所】\n{context}\n\n"
        f"【質問】\n{query}"
    )
    return _call_llm(prompt)


# ------------------------------------------------------------------ #
# LLM trend classification                                            #
# ------------------------------------------------------------------ #

def _llm_classify_trend(company: str, analyses_text: str) -> tuple[str, str]:
    """
    Classify fundamental trend as positive / negative / neutral.
    Returns (trend, reason).
    """
    prompt = (
        f"あなたはプロのファンダメンタルズアナリストです。\n"
        f"以下の【{company}に関するファンダメンタルズ分析レポート】を読み、\n"
        f"スイングトレードの観点から中長期的なトレンドを判定してください。\n\n"
        f"【分析レポート】\n{analyses_text[:3000]}\n\n"
        f"必ず以下のJSON形式のみで出力してください（余分なテキスト不要）:\n"
        f'{{"trend": "positive"|"negative"|"neutral", "reason": "1~2文の日本語の根拠"}}\n'
        f"positive=強気（業績良好・成長性高い）, negative=弱気（業績悪化・リスク大）, neutral=中立または情報不足"
    )
    try:
        raw = _call_llm(prompt).strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            return str(parsed.get("trend", "neutral")), str(parsed.get("reason", ""))
    except Exception as e:
        return "neutral", f"LLM分類エラー: {e}"
    return "neutral", "パース失敗"


# ------------------------------------------------------------------ #
# Public: single-query mode (backward compatible)                     #
# ------------------------------------------------------------------ #

def _single_query(query: str, persist_dir: str, top_k: int) -> dict:
    """
    Single HyDE-enhanced RAG query.

    Returns:
        {
            "query": str,
            "hypotheses": str,
            "context_chunks": list[str],
            "analysis": str,
            "error": str | None,
        }
    """
    db = _load_db(persist_dir)
    if db is None:
        return {
            "query": query,
            "hypotheses": "",
            "context_chunks": [],
            "analysis": f"エラー: ChromaDB '{persist_dir}' が見つかりません。先に決算PDFを取り込んでください。",
            "error": f"ChromaDB not found: {persist_dir}",
        }

    try:
        chunks, hypotheses = _hyde_search(query, db, top_k)
        analysis = _generate_analysis(query, chunks)
        return {
            "query": query,
            "hypotheses": hypotheses,
            "context_chunks": chunks,
            "analysis": analysis,
            "error": None,
        }
    except Exception as e:
        return {"query": query, "hypotheses": "", "context_chunks": [], "analysis": "", "error": str(e)}


# ------------------------------------------------------------------ #
# Public: multi-query company analysis mode                           #
# ------------------------------------------------------------------ #

def analyze_company(
    company: str,
    queries: list[str],
    persist_dir: str = "chroma_db_saved",
    top_k: int = 4,
) -> dict:
    """
    Run multiple HyDE-RAG queries for one company, then classify overall
    fundamental trend with LLM.

    Returns:
        {
            "company": str,
            "persist_dir": str,
            "queries_count": int,
            "analyses": [{"query": str, "analysis": str, "chunks_retrieved": int}, ...],
            "trend": "positive"|"negative"|"neutral",
            "trend_reason": str,
            "data_available": bool,   # False when DB has no relevant docs
            "error": str | None,
        }
    """
    db = _load_db(persist_dir)
    if db is None:
        return {
            "company": company,
            "persist_dir": persist_dir,
            "queries_count": 0,
            "analyses": [],
            "trend": "neutral",
            "trend_reason": f"ChromaDB '{persist_dir}' が存在しないためデータなし。",
            "data_available": False,
            "error": f"ChromaDB not found: {persist_dir}",
        }

    # EDGAR インジェスト済みか確認（ticker メタデータ）→ フィルタ検索に使用
    ticker_upper = company.upper()
    use_ticker_filter: str | None = None
    try:
        meta_hit = db.get(where={"ticker": {"$eq": ticker_upper}}, limit=1)
        if meta_hit.get("ids"):
            use_ticker_filter = ticker_upper
    except Exception:
        pass

    analyses = []
    all_analyses_text = ""

    for i, query in enumerate(queries):
        try:
            chunks, _ = _hyde_search(query, db, top_k, ticker_filter=use_ticker_filter)

            # データ関連性チェック
            docs_raw = db.similarity_search(
                query, k=top_k,
                filter={"ticker": {"$eq": ticker_upper}} if use_ticker_filter else None,
            )
            sources = [d.metadata.get("source", "") for d in docs_raw]
            company_found = bool(use_ticker_filter) or (
                any(company in c for c in chunks)
                or any(company in s for s in sources)
            )

            analysis = _generate_analysis(query, chunks)
            analyses.append({
                "query": query,
                "analysis": analysis,
                "chunks_retrieved": len(chunks),
                "company_found_in_context": company_found,
            })
            all_analyses_text += f"\nQ: {query}\nA: {analysis}\n"

            if i < len(queries) - 1:
                time.sleep(6)  # Gemini 無料枠: 10-20 RPM 制限への対策
        except Exception as e:
            analyses.append({
                "query": query,
                "analysis": f"検索エラー: {e}",
                "chunks_retrieved": 0,
                "company_found_in_context": False,
            })

    data_available = any(a.get("company_found_in_context") for a in analyses)

    # 言語ミスマッチ補完: ticker メタデータで直接確認（英語文書を日本語クエリで検索した際の誤検知を防ぐ）
    if not data_available:
        try:
            meta_hit = db.get(where={"ticker": {"$eq": company.upper()}}, limit=1)
            if meta_hit.get("ids"):
                data_available = True
        except Exception:
            pass

    if not data_available:
        trend = "neutral"
        trend_reason = f"DBに{company}固有のドキュメントが見つかりませんでした。"
    else:
        trend, trend_reason = _llm_classify_trend(company, all_analyses_text)
        time.sleep(2)

    return {
        "company": company,
        "persist_dir": persist_dir,
        "queries_count": len(queries),
        "analyses": analyses,
        "trend": trend,
        "trend_reason": trend_reason,
        "data_available": data_available,
        "error": None,
    }


# ------------------------------------------------------------------ #
# Skill entry point                                                    #
# ------------------------------------------------------------------ #

def run(
    query: str | None = None,
    company: str | None = None,
    queries: list[str] | None = None,
    persist_dir: str = "chroma_db_saved",
    top_k: int = 4,
) -> dict:
    """
    ECC skill entry point — dispatch to single or multi-query mode.

    Single mode : run(query="...")
    Company mode: run(company="...", queries=["...", ...])
    """
    if company and queries:
        return analyze_company(company, queries, persist_dir, top_k)
    if query:
        return _single_query(query, persist_dir, top_k)
    return {"error": "引数 'query' または 'company'+'queries' を指定してください。"}
