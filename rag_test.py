"""
rag_test.py — ChromaDB を使ったRAG検索プロトタイプ
data/knowledge_base/ のJSONコーパスをベクトルDBに登録し、セマンティック検索をテストします。
"""

import json
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

# ============================================================
# 設定
# ============================================================
CORPUS_DIR   = Path("data/knowledge_base")
CHROMA_DIR   = Path("data/chroma_db")
COLLECTION   = "financial_corpus"
TOP_K        = 2


# ============================================================
# ChromaDB 初期化
# ============================================================
def get_collection(chroma_dir: Path, collection_name: str):
    """PersistentClientを作成し、コレクションを取得または新規作成する。"""
    client = chromadb.PersistentClient(path=str(chroma_dir))
    ef = embedding_functions.DefaultEmbeddingFunction()   # all-MiniLM-L6-v2
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    print(f"  [RAGTest] コレクション '{collection_name}' を取得しました（保存先: {chroma_dir.resolve()}）")
    return collection


# ============================================================
# コーパスのロードと登録
# ============================================================
def load_corpus(corpus_dir: Path) -> list[dict]:
    """knowledge_base/ の全JSONファイルを読み込む。"""
    records = []
    for path in sorted(corpus_dir.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                records.append(json.load(f))
        except Exception as e:
            print(f"  [RAGTest] エラー: {path.name} の読み込み失敗 - {e}")
    return records


def upsert_documents(collection, records: list[dict]) -> None:
    """コーパスをChromaDBに登録する（既存IDは上書き）。"""
    already = set(collection.get()["ids"])

    ids, documents, metadatas = [], [], []
    skipped = 0

    for rec in records:
        meta = rec["metadata"]
        ticker = meta["ticker"]
        summary = rec["content"].get("long_business_summary", "").strip()

        if not summary:
            skipped += 1
            continue
        if ticker in already:
            skipped += 1
            continue

        ids.append(ticker)
        documents.append(summary)
        metadatas.append({
            "ticker":   ticker,
            "name":     meta.get("name", ""),
            "sector":   meta.get("sector", "Unknown"),
            "industry": meta.get("industry", "Unknown"),
            "exchange": meta.get("exchange", "Unknown"),
        })

    if ids:
        # ChromaDB の上限（5461件）に合わせてバッチ分割
        BATCH = 500
        for i in range(0, len(ids), BATCH):
            collection.upsert(
                ids=ids[i:i+BATCH],
                documents=documents[i:i+BATCH],
                metadatas=metadatas[i:i+BATCH],
            )
        print(f"  [RAGTest] {len(ids)}件を登録しました（スキップ: {skipped}件）")
    else:
        print(f"  [RAGTest] 新規登録なし（全{skipped}件は登録済みまたは概要なし）")

    print(f"  [RAGTest] コレクション総ドキュメント数: {collection.count()}件")


# ============================================================
# 検索ユーティリティ
# ============================================================
def run_query(collection, label: str, query: str, where: dict | None = None) -> None:
    """クエリを実行して結果を整形表示する。"""
    kwargs = dict(query_texts=[query], n_results=TOP_K, include=["documents", "metadatas", "distances"])
    if where:
        kwargs["where"] = where

    try:
        results = collection.query(**kwargs)
    except Exception as e:
        print(f"  [RAGTest] エラー: クエリ '{label}' 失敗 - {e}")
        return

    print(f"\n{'='*60}")
    print(f"  クエリ {label}: \"{query}\"")
    if where:
        print(f"  フィルタ: {where}")
    print(f"{'='*60}")

    ids       = results["ids"][0]
    metas     = results["metadatas"][0]
    distances = results["distances"][0]
    docs      = results["documents"][0]

    for rank, (doc_id, meta, dist, doc) in enumerate(zip(ids, metas, distances, docs), 1):
        similarity = round(1 - dist, 4)
        print(f"\n  [{rank}位]  {doc_id} — {meta['name']}")
        print(f"          セクター: {meta['sector']} / 業種: {meta['industry']}")
        print(f"          類似度スコア: {similarity}")
        print(f"          概要(先頭120字): {doc[:120]}...")


# ============================================================
# メイン
# ============================================================
def main():
    print("  [RAGTest] RAG検索プロトタイプを開始します")

    # 1. ChromaDB 初期化
    collection = get_collection(CHROMA_DIR, COLLECTION)

    # 2. コーパスをロードして登録
    records = load_corpus(CORPUS_DIR)
    print(f"  [RAGTest] JSONファイル読み込み: {len(records)}件")
    upsert_documents(collection, records)

    # 3. 検索テスト
    print("\n  [RAGTest] ===== 検索テスト開始 =====")

    # クエリA: e-commerce → Amazon が上位に来るはず
    run_query(
        collection,
        label="A（セマンティック）",
        query="e-commerce and online retail",
    )

    # クエリB: GPU/AI chip → NVIDIA が上位に来るはず
    run_query(
        collection,
        label="B（セマンティック）",
        query="manufacturing artificial intelligence chips and GPUs",
    )

    # クエリC: クラウドインフラ × Technologyセクター限定 → Microsoft/Amazon/Googleが候補
    run_query(
        collection,
        label="C（ハイブリッド: セマンティック + メタデータフィルタ）",
        query="cloud computing infrastructure",
        where={"sector": {"$eq": "Technology"}},
    )

    print(f"\n  [RAGTest] 検索テスト完了")


if __name__ == "__main__":
    main()
