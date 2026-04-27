"""
Skill: rag_search
Permission: FundamentalAgent only
Multi-HyDE powered ChromaDB search over financial reports.
"""

import os
import warnings
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_google_genai import ChatGoogleGenerativeAI

warnings.filterwarnings("ignore")
load_dotenv()

_embeddings = None
_db_cache: dict = {}
_llm = None


def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")
    return _embeddings


def _get_llm():
    global _llm
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    return _llm


def _load_db(persist_dir: str) -> Chroma:
    if persist_dir not in _db_cache:
        _db_cache[persist_dir] = Chroma(
            persist_directory=persist_dir,
            embedding_function=_get_embeddings(),
        )
    return _db_cache[persist_dir]


def run(query: str, persist_dir: str = "chroma_db_saved", top_k: int = 6) -> dict:
    """
    Multi-HyDE RAG search over persisted ChromaDB financial documents.

    Returns:
        {
            "query": str,
            "hypotheses": str,
            "context_chunks": list[str],
            "analysis": str,
        }
    """
    llm = _get_llm()

    if not os.path.exists(persist_dir):
        return {
            "query": query,
            "hypotheses": "",
            "context_chunks": [],
            "analysis": f"エラー: ChromaDB '{persist_dir}' が見つかりません。先に決算PDFを取り込んでください。",
        }

    # --- HyDE: 仮説的回答を生成して検索精度を高める ---
    hyde_prompt = (
        "あなたはプロの金融アナリストです。\n"
        "以下の【ユーザーの質問】に対して、実際の決算資料にどのように書かれているか、"
        "具体的な数字のプレースホルダー（例: ○○億円、○○%）を含めた"
        "【仮説的な回答】を3パターン作成してください。\n\n"
        f"【ユーザーの質問】\n{query}"
    )
    hypotheses = llm.invoke(hyde_prompt).content

    enhanced_query = query + "\n" + hypotheses
    db = _load_db(persist_dir)
    docs = db.similarity_search(enhanced_query, k=top_k)
    context_chunks = [doc.page_content for doc in docs]
    context = "\n\n".join(context_chunks)

    analysis_prompt = (
        "あなたはプロの機関投資家・金融アナリストです。\n"
        "以下の【決算資料の該当箇所】を元に【質問】に答えてください。\n"
        "資料に書かれていないことは絶対に推測で語らないでください。\n\n"
        f"【決算資料の該当箇所】\n{context}\n\n"
        f"【質問】\n{query}"
    )
    analysis = llm.invoke(analysis_prompt).content

    return {
        "query": query,
        "hypotheses": hypotheses,
        "context_chunks": context_chunks,
        "analysis": analysis,
    }
