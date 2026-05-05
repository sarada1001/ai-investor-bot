"""
hyde_test.py — Multi-HyDE (Hypothetical Document Embeddings) 検証スクリプト

通常の単純検索と Multi-HyDE 検索を比較し、NVIDIAのランキング復帰を検証します。
- 元クエリ: "manufacturing AI chips and GPUs"
- HyDE仮説1: AIインフラ企業のマーケティング的表現
- HyDE仮説2: 製造業としての直接的表現
- 統合手法: Reciprocal Rank Fusion (RRF, k=60)
"""

from pathlib import Path
from collections import defaultdict

import chromadb
from chromadb.utils import embedding_functions

# ============================================================
# 設定
# ============================================================
CHROMA_DIR = Path("data/chroma_db")
COLLECTION = "financial_corpus"
TOP_K_PER_HYDE = 5   # 各HyDE仮説から取得する件数
TOP_K_FINAL = 5      # 最終出力件数
RRF_K = 60           # RRFの定数 (標準値)

# ============================================================
# クエリ定義
# ============================================================
ORIGINAL_QUERY = "manufacturing AI chips and GPUs"

# LLMが生成したと仮定する仮説文書 (ハードコード)
HYDE_1 = (
    "We are a data center scale AI infrastructure company that designs advanced "
    "parallel processing architectures and software platforms for generative AI."
)
HYDE_2 = (
    "Our company manufactures high-performance graphics processing units (GPUs) "
    "and AI accelerators used in supercomputing and enterprise data centers."
)


# ============================================================
# ChromaDB 接続
# ============================================================
def get_collection():
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    ef = embedding_functions.DefaultEmbeddingFunction()  # all-MiniLM-L6-v2
    col = client.get_collection(name=COLLECTION, embedding_function=ef)
    print(f"[HyDE] コレクション '{COLLECTION}' 接続完了 ({col.count()} docs)")
    return col


# ============================================================
# 検索ユーティリティ
# ============================================================
def search(collection, query_text: str, n_results: int) -> list[dict]:
    """ChromaDB を検索し、結果をリストで返す。"""
    results = collection.query(
        query_texts=[query_text],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    hits = []
    for doc_id, meta, dist, doc in zip(
        results["ids"][0],
        results["metadatas"][0],
        results["distances"][0],
        results["documents"][0],
    ):
        hits.append({
            "id":         doc_id,
            "name":       meta.get("name", ""),
            "sector":     meta.get("sector", ""),
            "industry":   meta.get("industry", ""),
            "similarity": round(1 - dist, 4),
            "doc":        doc,
        })
    return hits


# ============================================================
# Reciprocal Rank Fusion
# ============================================================
def reciprocal_rank_fusion(ranked_lists: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """
    複数のランキングリストを RRF で統合する。
    score(d) = Σ 1 / (k + rank(d))  ← rank は 1 始まり
    """
    rrf_scores: dict[str, float] = defaultdict(float)
    doc_meta: dict[str, dict] = {}

    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            doc_id = hit["id"]
            rrf_scores[doc_id] += 1.0 / (k + rank)
            if doc_id not in doc_meta:
                doc_meta[doc_id] = hit

    # スコア降順でソート
    sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)
    fused = []
    for doc_id in sorted_ids:
        entry = dict(doc_meta[doc_id])
        entry["rrf_score"] = round(rrf_scores[doc_id], 6)
        fused.append(entry)
    return fused


# ============================================================
# 結果表示
# ============================================================
def print_results(title: str, hits: list[dict], score_key: str = "similarity") -> None:
    sep = "=" * 65
    print(f"\n{sep}")
    print(f"  {title}")
    print(sep)
    for rank, h in enumerate(hits, 1):
        score = h.get(score_key, "—")
        print(f"  [{rank}位] {h['id']:6s} — {h['name']}")
        print(f"         セクター: {h['sector']} / 業種: {h['industry']}")
        print(f"         スコア ({score_key}): {score}")
        print(f"         概要: {h['doc'][:110]}...")
        print()


def highlight_nvidia(hits: list[dict], score_key: str = "similarity") -> str:
    """NVIDIAの順位を返す（圏外なら '圏外'）。"""
    for rank, h in enumerate(hits, 1):
        if h["id"] == "NVDA":
            return f"{rank}位 (スコア={h.get(score_key, '—')})"
    return "圏外"


# ============================================================
# メイン
# ============================================================
def main():
    print("\n[HyDE] ===== Multi-HyDE 検証スクリプト =====\n")

    collection = get_collection()

    # ----------------------------------------------------------
    # Step 1: 通常検索（ベースライン）
    # ----------------------------------------------------------
    print("\n[Step 1] 通常の単純検索（ベースライン）")
    print(f"  クエリ: \"{ORIGINAL_QUERY}\"")
    baseline_hits = search(collection, ORIGINAL_QUERY, n_results=TOP_K_FINAL)
    print_results(
        f"単純検索: \"{ORIGINAL_QUERY}\" — Top {TOP_K_FINAL}",
        baseline_hits,
        score_key="similarity",
    )

    # ----------------------------------------------------------
    # Step 2: Multi-HyDE 検索
    # ----------------------------------------------------------
    print("\n[Step 2] Multi-HyDE 検索")
    print(f"  HyDE-1: \"{HYDE_1[:80]}...\"")
    print(f"  HyDE-2: \"{HYDE_2[:80]}...\"\n")

    hits_h1 = search(collection, HYDE_1, n_results=TOP_K_PER_HYDE)
    hits_h2 = search(collection, HYDE_2, n_results=TOP_K_PER_HYDE)

    print_results(
        f"HyDE-1 検索結果 (AIインフラ/マーケティング表現) — Top {TOP_K_PER_HYDE}",
        hits_h1,
        score_key="similarity",
    )
    print_results(
        f"HyDE-2 検索結果 (GPU製造/技術表現) — Top {TOP_K_PER_HYDE}",
        hits_h2,
        score_key="similarity",
    )

    # ----------------------------------------------------------
    # Step 3: RRF 統合
    # ----------------------------------------------------------
    print("\n[Step 3] Reciprocal Rank Fusion (RRF, k=60) で統合")
    fused = reciprocal_rank_fusion([hits_h1, hits_h2], k=RRF_K)
    print_results(
        f"Multi-HyDE 統合結果 (RRF) — Top {TOP_K_FINAL}",
        fused[:TOP_K_FINAL],
        score_key="rrf_score",
    )

    # ----------------------------------------------------------
    # Step 4: 比較サマリー
    # ----------------------------------------------------------
    sep = "=" * 65
    print(f"\n{sep}")
    print("  【比較サマリー】NVIDIA (NVDA) のランキング変化")
    print(sep)
    nvda_baseline = highlight_nvidia(baseline_hits, "similarity")
    nvda_h1       = highlight_nvidia(hits_h1,       "similarity")
    nvda_h2       = highlight_nvidia(hits_h2,       "similarity")
    nvda_fused    = highlight_nvidia(fused[:TOP_K_FINAL], "rrf_score")

    print(f"  単純検索 (元クエリ)    : {nvda_baseline}")
    print(f"  HyDE-1 検索            : {nvda_h1}")
    print(f"  HyDE-2 検索            : {nvda_h2}")
    print(f"  Multi-HyDE (RRF統合)   : {nvda_fused}")
    print()

    # RRF全体でのNVDAスコアも確認
    nvda_full = next((h for h in fused if h["id"] == "NVDA"), None)
    if nvda_full:
        full_rank = fused.index(nvda_full) + 1
        print(f"  ※ RRF全順位: {full_rank}位 / {len(fused)}件 (RRFスコア={nvda_full['rrf_score']})")
    else:
        print("  ※ NVDA は両方の中間リストにも存在しませんでした。")
    print(sep)


if __name__ == "__main__":
    main()
