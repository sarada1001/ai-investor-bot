"""
test_data_loader.py
-------------------
FFP (Financial Document Pre-processing Pipeline) の動作検証スクリプト。

ステップ:
  1. data/raw_documents/ にテスト用PDFを配置
     (unzipped_docs/ 内の既存決算PDFをコピー、存在しない場合は最小限のPDFを生成)
  2. FinancialDocumentLoader を実行してChromaDBに格納
  3. 類似検索クエリで格納内容を確認
"""

from __future__ import annotations

import sys
import shutil
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ------------------------------------------------------------------ #
# テスト設定                                                           #
# ------------------------------------------------------------------ #

RAW_DIR    = Path("data/raw_documents")
CHROMA_DIR = "chroma_db_saved"
COLLECTION = "financial_filings"

# 既存の実際の決算PDFをテスト用ソースとして優先使用
_EXISTING_PDF_CANDIDATES = [
    Path("unzipped_docs/2026年3月期第３四半期_決算一式/2026年3月期第３四半期_決算ハイライトレポート.pdf"),
    Path("unzipped_docs/2026年3月期第３四半期_決算一式/2026年3月期第３四半期 決算説明資料.pdf"),
    Path("unzipped_docs/2026年3月期第３四半期_決算一式/2026年３月期第３四半期決算短信〔日本基準〕(連結).pdf"),
]

DUMMY_DST = RAW_DIR / "test_financial_report.pdf"

TEST_QUERIES = [
    "売上高 営業利益",
    "AI事業 成長戦略",
    "リスク 市場環境",
]


# ------------------------------------------------------------------ #
# ダミーPDF セットアップ                                               #
# ------------------------------------------------------------------ #

def setup_test_pdf() -> bool:
    """
    テスト用PDFを data/raw_documents/ に配置する。
    既存のPDFがあればコピー、なければ最小限のPDFを生成する。
    Returns True if a real PDF was copied (richer test data).
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for candidate in _EXISTING_PDF_CANDIDATES:
        if candidate.exists():
            shutil.copy2(candidate, DUMMY_DST)
            print(f"  [SETUP] 実PDFをコピー: {candidate.name}")
            return True

    _create_minimal_pdf(DUMMY_DST)
    print(f"  [SETUP] 最小限PDFを生成: {DUMMY_DST.name}")
    return False


def _create_minimal_pdf(path: Path) -> None:
    """
    外部ライブラリ不要の最小限有効PDFを生成する。
    テキストはASCII英語のみ（Type1フォントの制約）。
    """
    lines = [
        "Financial Report Summary - Test Data",
        "Revenue: 10 billion yen  (YoY +15%)",
        "Operating profit: 2 billion yen  (YoY +25%)",
        "Net profit: 1.5 billion yen",
        "AI business growth is driving overall performance.",
        "Cloud service division revenue is expanding.",
        "SaaS revenue ratio has exceeded 50%.",
        "Next fiscal year revenue forecast: 12 billion yen.",
    ]
    content_lines = "".join(
        f"50 {750 - i * 20} Td ({ln}) Tj\n" for i, ln in enumerate(lines)
    )
    stream = f"BT\n/F1 11 Tf\n50 770 Td\n{content_lines}ET\n"
    stream_bytes = stream.encode("latin-1")

    objs: list[bytes] = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 612 792] "
            b"/Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        ),
        f"4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode()
        + stream_bytes
        + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]

    header = b"%PDF-1.4\n"
    body   = b"".join(objs)
    offsets: list[int] = []
    pos = len(header)
    for obj in objs:
        offsets.append(pos)
        pos += len(obj)

    xref_offset = pos
    xref = f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n"
    trailer = (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )

    path.write_bytes(header + body + xref.encode() + trailer.encode())


# ------------------------------------------------------------------ #
# メイン                                                               #
# ------------------------------------------------------------------ #

def main() -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  FFP テスト — Financial Document Loader")
    print(f"  実行時刻  : {ts}")
    print(f"  ChromaDB  : {CHROMA_DIR}")
    print(f"  コレクション: {COLLECTION}")
    print(f"{'='*60}")

    # ── Step 1: テスト用PDF配置 ────────────────────────────────
    print("\n[1/4] テスト用PDFをセットアップ中...")
    real_pdf = setup_test_pdf()
    pdf_type = "実際の決算PDF" if real_pdf else "ダミーPDF（最小限）"
    print(f"  使用PDF種別: {pdf_type}")

    # ── Step 2: ローダー実行 ──────────────────────────────────
    print("\n[2/4] FinancialDocumentLoader を実行中...")
    from skills.financial_data_loader import FinancialDocumentLoader

    loader = FinancialDocumentLoader(
        raw_dir=RAW_DIR,
        persist_dir=CHROMA_DIR,
        collection_name=COLLECTION,
    )
    result = loader.run()

    # ── Step 3: パイプライン結果 ──────────────────────────────
    print(f"\n[3/4] パイプライン実行結果:")
    print(f"  処理ファイル数  : {result['files_processed']}")
    print(f"  総チャンク数    : {result['total_chunks']}")
    if result.get("error"):
        print(f"  エラー          : {result['error']}")
        return

    print(f"\n  [ ファイル別詳細 ]")
    for r in result.get("results", []):
        status = "✓" if not r.get("error") else "✗"
        print(f"  {status} {r['file']}")
        print(f"      抽出ページ数: {r['pages']}, 格納チャンク数: {r['chunks']}")
        if r.get("error"):
            print(f"      エラー: {r['error']}")

    # ── Step 4: ChromaDB 格納確認 ─────────────────────────────
    print(f"\n[4/4] ChromaDB 格納確認（類似検索テスト）...")

    if result["total_chunks"] == 0:
        print("  チャンクが0件のため検証スキップ")
    else:
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_community.vectorstores import Chroma

        embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")
        db = Chroma(
            persist_directory=CHROMA_DIR,
            embedding_function=embeddings,
            collection_name=COLLECTION,
        )

        for query in TEST_QUERIES:
            docs = db.similarity_search(query, k=2)
            print(f"\n  クエリ: '{query}'  (取得: {len(docs)} チャンク)")
            for i, doc in enumerate(docs):
                fname = doc.metadata.get("filename", "?")
                page  = doc.metadata.get("page", "?")
                cidx  = doc.metadata.get("chunk_index", "?")
                snippet = doc.page_content[:100].replace("\n", " ").strip()
                print(f"    [{i+1}] {fname} p.{page} chunk#{cidx}")
                print(f"         {snippet}...")

    print(f"\n{'='*60}")
    print(f"  テスト完了")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
