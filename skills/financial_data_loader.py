"""
Skill: financial_data_loader
Permission: FundamentalAgent only

Financial Document Pre-processing Pipeline (FFP)
-------------------------------------------------
Loads PDF files from data/raw_documents/, extracts text (via pypdf or
MinerU subprocess when available), splits into ~200-250 token chunks,
and ingests them into a ChromaDB collection (default: financial_filings).

EDGAR Auto-Fetch
----------------
FinancialDocumentLoader.fetch_from_edgar(ticker) downloads the latest
10-Q / 10-K via sec-edgar-downloader, parses HTML/text, and ingests the
result into the same financial_filings ChromaDB collection.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

# ------------------------------------------------------------------ #
# Configuration                                                        #
# ------------------------------------------------------------------ #

_RAW_DIR       = Path("data/raw_documents")
_CHROMA_DIR    = "chroma_db_saved"
_COLLECTION    = "financial_filings"
_CHUNK_SIZE    = 800    # chars ≈ 200-250 tokens for Japanese/English mixed text
_CHUNK_OVERLAP = 80

_MINERU_CMD    = "magic-pdf"   # MinerU CLI (future: pip install mineru)
_EDGAR_DL_DIR  = Path("unzipped_docs") / "edgar_dl"
_EDGAR_MAX_CHARS = 500_000     # ~500 KB plain text per filing


# ------------------------------------------------------------------ #
# HTML → plain text (shared by PDF pipeline and EDGAR fetcher)        #
# ------------------------------------------------------------------ #

def _html_to_text(html: str) -> str:
    """
    Strip HTML / iXBRL markup from SEC EDGAR filings and return clean text.
    Uses lxml for speed on large files; falls back to html.parser.
    """
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "head", "header", "footer",
                     "nav", "ix:header", "ix:hidden", "xbrli:xbrl"]):
        tag.decompose()

    text  = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text  = "\n".join(lines)
    text  = re.sub(r"\n{3,}", "\n\n", text)
    return text


# ------------------------------------------------------------------ #
# Main class                                                           #
# ------------------------------------------------------------------ #

class FinancialDocumentLoader:
    """
    PDF → ChromaDB pre-processing pipeline for Japanese financial reports.

    Usage:
        loader = FinancialDocumentLoader()
        result = loader.run()
    """

    def __init__(
        self,
        raw_dir: str | Path = _RAW_DIR,
        persist_dir: str = _CHROMA_DIR,
        collection_name: str = _COLLECTION,
        chunk_size: int = _CHUNK_SIZE,
        chunk_overlap: int = _CHUNK_OVERLAP,
        use_mineru: bool = False,
    ) -> None:
        self.raw_dir         = Path(raw_dir)
        self.persist_dir     = persist_dir
        self.collection_name = collection_name
        self.chunk_size      = chunk_size
        self.chunk_overlap   = chunk_overlap
        self.use_mineru      = use_mineru
        self._embeddings     = None

    # ---------------------------------------------------------------- #
    # 1. PDF discovery                                                  #
    # ---------------------------------------------------------------- #

    def load_pdfs(self) -> list[Path]:
        """Return sorted list of PDF paths in raw_dir."""
        if not self.raw_dir.exists():
            raise FileNotFoundError(
                f"ディレクトリが見つかりません: {self.raw_dir}\n"
                f"'mkdir -p {self.raw_dir}' で作成後、PDFを配置してください。"
            )
        return sorted(self.raw_dir.glob("*.pdf"))

    # ---------------------------------------------------------------- #
    # 2. PDF parsing                                                    #
    # ---------------------------------------------------------------- #

    def parse_pdf(self, pdf_path: Path) -> list[dict[str, Any]]:
        """
        Extract text from a PDF.

        Dispatches to MinerU (magic-pdf) when self.use_mineru=True and
        the CLI is found on PATH; otherwise falls back to pypdf.

        Returns a list of page dicts:
            [{"page": int, "text": str}, ...]
        """
        if self.use_mineru and shutil.which(_MINERU_CMD):
            return self._parse_with_mineru(pdf_path)
        return self._parse_with_pypdf(pdf_path)

    def _parse_with_pypdf(self, pdf_path: Path) -> list[dict[str, Any]]:
        """Lightweight text extraction using pypdf (default)."""
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        pages  = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            text = self._clean_text(text)
            if text.strip():
                pages.append({"page": i + 1, "text": text})
        return pages

    def _parse_with_mineru(self, pdf_path: Path) -> list[dict[str, Any]]:
        """
        High-accuracy layout-aware parsing via MinerU (magic-pdf) subprocess.

        Expected CLI call:
            magic-pdf -p <pdf_path> -o <output_dir> -m auto

        MinerU outputs a Markdown file. Falls back to pypdf on any failure.
        """
        out_dir = pdf_path.parent / f".mineru_{pdf_path.stem}"
        out_dir.mkdir(exist_ok=True)

        try:
            subprocess.run(
                [_MINERU_CMD, "-p", str(pdf_path), "-o", str(out_dir), "-m", "auto"],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.CalledProcessError as exc:
            print(f"  [FFP] MinerU 失敗 (code={exc.returncode}), pypdf にフォールバック")
            return self._parse_with_pypdf(pdf_path)
        except subprocess.TimeoutExpired:
            print("  [FFP] MinerU タイムアウト, pypdf にフォールバック")
            return self._parse_with_pypdf(pdf_path)

        md_candidates = list(out_dir.rglob("*.md"))
        if not md_candidates:
            print("  [FFP] MinerU 出力 (.md) なし, pypdf にフォールバック")
            return self._parse_with_pypdf(pdf_path)

        md_text = md_candidates[0].read_text(encoding="utf-8")
        return [{"page": 1, "text": self._clean_text(md_text)}]

    def _clean_text(self, text: str) -> str:
        """Normalize whitespace and remove control characters."""
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    # ---------------------------------------------------------------- #
    # 3. Chunking                                                       #
    # ---------------------------------------------------------------- #

    def chunk_text(
        self,
        pages: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Split page texts into overlapping chunks targeting ~200-250 tokens.

        Uses Japanese-aware separators (句点 > 改行 > 読点 > スペース) so
        chunks break at natural sentence boundaries where possible.

        Returns:
            [{"text": str, "metadata": {source, filename, page, chunk_index}}, ...]
        """
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["。\n", "。", "\n\n", "\n", "、", " ", ""],
        )

        chunks     = []
        global_idx = 0

        for page_info in pages:
            texts = splitter.split_text(page_info["text"])
            for text in texts:
                if not text.strip():
                    continue
                chunks.append({
                    "text": text,
                    "metadata": {
                        **metadata,
                        "page":        page_info["page"],
                        "chunk_index": global_idx,
                    },
                })
                global_idx += 1

        return chunks

    # ---------------------------------------------------------------- #
    # 4. ChromaDB ingest                                                #
    # ---------------------------------------------------------------- #

    def _get_embeddings(self):
        if self._embeddings is None:
            from langchain_huggingface import HuggingFaceEmbeddings
            self._embeddings = HuggingFaceEmbeddings(
                model_name="intfloat/multilingual-e5-small"
            )
        return self._embeddings

    def ingest_to_chroma(self, chunks: list[dict[str, Any]]) -> int:
        """
        Embed chunks and upsert into the target ChromaDB collection.
        Returns the number of chunks added.
        """
        from langchain_core.documents import Document
        from langchain_community.vectorstores import Chroma

        if not chunks:
            return 0

        docs = [
            Document(page_content=c["text"], metadata=c["metadata"])
            for c in chunks
        ]
        embeddings = self._get_embeddings()

        if os.path.exists(self.persist_dir):
            db = Chroma(
                persist_directory=self.persist_dir,
                embedding_function=embeddings,
                collection_name=self.collection_name,
            )
            db.add_documents(docs)
        else:
            Chroma.from_documents(
                docs,
                embeddings,
                persist_directory=self.persist_dir,
                collection_name=self.collection_name,
            )

        return len(docs)

    # ---------------------------------------------------------------- #
    # 5. Pipeline entry point                                           #
    # ---------------------------------------------------------------- #

    def run(self) -> dict[str, Any]:
        """
        Execute the full pipeline: discover → parse → chunk → ingest.

        Returns:
            {
                "files_processed": int,
                "total_chunks":    int,
                "results": [
                    {"file": str, "pages": int, "chunks": int, "error": str|None},
                    ...
                ],
                "error": str | None,
            }
        """
        try:
            pdfs = self.load_pdfs()
        except FileNotFoundError as exc:
            return {"files_processed": 0, "total_chunks": 0, "results": [], "error": str(exc)}

        if not pdfs:
            return {
                "files_processed": 0,
                "total_chunks":    0,
                "results":         [],
                "error":           f"'{self.raw_dir}' に PDF ファイルが見つかりません。",
            }

        print(f"  [FFP] {len(pdfs)} 件の PDF を検出")
        print(f"  [FFP] 保存先: {self.persist_dir} / collection: {self.collection_name}")

        results      = []
        total_chunks = 0

        for pdf_path in pdfs:
            print(f"\n  [FFP] 処理中: {pdf_path.name}")
            try:
                pages = self.parse_pdf(pdf_path)
                print(f"  [FFP]   抽出ページ数: {len(pages)}")

                metadata = {
                    "source":     str(pdf_path),
                    "filename":   pdf_path.name,
                    "collection": self.collection_name,
                }
                chunks = self.chunk_text(pages, metadata)
                print(f"  [FFP]   チャンク数: {len(chunks)}")

                n = self.ingest_to_chroma(chunks)
                total_chunks += n
                print(f"  [FFP]   ChromaDB 追加: {n} チャンク ✓")

                results.append({
                    "file":   pdf_path.name,
                    "pages":  len(pages),
                    "chunks": n,
                    "error":  None,
                })

            except Exception as exc:
                print(f"  [FFP]   エラー: {exc}")
                results.append({
                    "file":   pdf_path.name,
                    "pages":  0,
                    "chunks": 0,
                    "error":  str(exc),
                })

        print(f"\n  [FFP] 完了: {len(pdfs)} ファイル, 合計 {total_chunks} チャンク")
        return {
            "files_processed": len(pdfs),
            "total_chunks":    total_chunks,
            "results":         results,
            "error":           None,
        }

    # ---------------------------------------------------------------- #
    # 6. EDGAR auto-fetch                                               #
    # ---------------------------------------------------------------- #

    def fetch_from_edgar(
        self,
        ticker: str,
        prefer_quarterly: bool = True,
        max_chars: int = _EDGAR_MAX_CHARS,
    ) -> dict[str, Any]:
        """
        Download the latest 10-Q (or 10-K) for a US-listed ticker using
        sec-edgar-downloader, parse HTML/text, chunk, and ingest into
        the financial_filings ChromaDB collection.

        Tries 10-Q first when prefer_quarterly=True; falls back to 10-K.
        Stores ticker metadata on every chunk so FundamentalAgent's
        relevance guard can filter by company.

        Returns:
            {
                "ticker":       str,
                "form":         str,    # form type that was ingested
                "chunks_added": int,
                "dl_dir":       str,    # where the filing was saved
                "error":        str | None,
            }
        """
        from sec_edgar_downloader import Downloader

        ticker     = ticker.upper()
        forms      = ["10-Q", "10-K"] if prefer_quarterly else ["10-K", "10-Q"]
        email      = os.getenv("SEC_USER_AGENT_EMAIL", "user@example.com")
        agent_name = os.getenv("SEC_USER_AGENT_NAME", "ECC InvestorAgent")

        dl_base = _EDGAR_DL_DIR
        dl_base.mkdir(parents=True, exist_ok=True)

        last_error: str | None = None

        for form in forms:
            try:
                print(f"  [FFP/EDGAR] {ticker} の最新 {form} をダウンロード中...")
                dl    = Downloader(agent_name, email, str(dl_base))
                count = dl.get(form, ticker, limit=1, download_details=True)

                if count == 0:
                    print(f"  [FFP/EDGAR] {form} が見つかりませんでした。次のフォームを試みます...")
                    last_error = f"{ticker} の {form} が EDGAR に存在しません"
                    continue

                # Locate the downloaded filing directory
                filing_dir = dl_base / "sec-edgar-filings" / ticker / form
                if not filing_dir.exists():
                    last_error = f"ダウンロード後にディレクトリが見つかりません: {filing_dir}"
                    continue

                # Collect HTML files sorted by size (largest = main document)
                html_files = sorted(
                    list(filing_dir.rglob("*.html")) + list(filing_dir.rglob("*.htm")),
                    key=lambda p: p.stat().st_size,
                    reverse=True,
                )
                txt_files = sorted(
                    filing_dir.rglob("*.txt"),
                    key=lambda p: p.stat().st_size,
                    reverse=True,
                )
                # Exclude tiny index/metadata files (< 5 KB)
                doc_files = [
                    f for f in (html_files + list(txt_files))
                    if f.stat().st_size >= 5_000
                ]

                if not doc_files:
                    last_error = f"解析対象ファイルが見つかりません: {filing_dir}"
                    continue

                target = doc_files[0]
                print(f"  [FFP/EDGAR] 処理: {target.name} ({target.stat().st_size:,} bytes)")

                raw = target.read_text(encoding="utf-8", errors="replace")
                if target.suffix.lower() in (".html", ".htm"):
                    text = self._clean_text(_html_to_text(raw))
                else:
                    text = self._clean_text(raw)

                if len(text) > max_chars:
                    text = text[:max_chars]
                    print(f"  [FFP/EDGAR] {max_chars:,} chars に切り詰めました")

                print(f"  [FFP/EDGAR] テキスト抽出完了 ({len(text):,} chars)")

                pages    = [{"page": 1, "text": text}]
                metadata = {
                    "source":     str(target),
                    "filename":   target.name,
                    "ticker":     ticker,
                    "form":       form,
                    "collection": self.collection_name,
                }
                chunks  = self.chunk_text(pages, metadata)
                n       = self.ingest_to_chroma(chunks)

                print(f"  [FFP/EDGAR] ChromaDB ({self.collection_name}) に {n} チャンク追加 ✓")
                return {
                    "ticker":       ticker,
                    "form":         form,
                    "chunks_added": n,
                    "dl_dir":       str(filing_dir),
                    "error":        None,
                }

            except Exception as exc:
                last_error = str(exc)
                print(f"  [FFP/EDGAR] {form} 取得エラー: {exc}")

        return {
            "ticker":       ticker,
            "form":         "",
            "chunks_added": 0,
            "dl_dir":       str(dl_base),
            "error":        last_error or "全フォームの取得に失敗しました",
        }


# ------------------------------------------------------------------ #
# Skill entry point                                                    #
# ------------------------------------------------------------------ #

def run(
    raw_dir: str = str(_RAW_DIR),
    persist_dir: str = _CHROMA_DIR,
    collection_name: str = _COLLECTION,
    chunk_size: int = _CHUNK_SIZE,
    chunk_overlap: int = _CHUNK_OVERLAP,
    use_mineru: bool = False,
) -> dict:
    """
    ECC skill entry point — run the Financial Document Pre-processing Pipeline.

    Args:
        raw_dir:         Directory containing input PDF files
        persist_dir:     ChromaDB persist directory
        collection_name: ChromaDB collection name
        chunk_size:      Target chunk size in characters (~200-250 tokens)
        chunk_overlap:   Overlap between consecutive chunks in characters
        use_mineru:      Use MinerU (magic-pdf) for high-accuracy parsing if installed

    Returns the same dict as FinancialDocumentLoader.run().
    """
    loader = FinancialDocumentLoader(
        raw_dir=raw_dir,
        persist_dir=persist_dir,
        collection_name=collection_name,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        use_mineru=use_mineru,
    )
    return loader.run()
