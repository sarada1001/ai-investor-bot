"""
FundamentalAgent — RAG-driven fundamental analysis agent.

Primary data source  : ChromaDB `financial_filings` collection
                       (populated by skills/financial_data_loader.py via FFP)
Embedding model      : intfloat/multilingual-e5-small  (same as FFP)
Fallback data source : yfinance (when ChromaDB has no relevant chunks)

Public API
----------
    agent = FundamentalAgent()
    result = agent.analyze("エクサウィザーズ")   # Japanese company name / ticker
    result = agent.analyze("AAPL")              # US ticker → yfinance fallback
"""

from __future__ import annotations

import json
import os
import re
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

warnings.filterwarnings("ignore")
load_dotenv()

_PERSIST_DIR          = "chroma_db_saved"
_COLLECTION           = "financial_filings"
_EMBED_MODEL          = "intfloat/multilingual-e5-small"
_RETRY_DELAYS         = (15, 30, 60)
_EDGAR_STALENESS_DAYS = 90   # re-fetch after ~one quarter
_FETCH_LOG_PATH       = Path("data/edgar_fetch_log.json")


def _is_japanese(text: str) -> bool:
    return bool(re.search(r"[぀-ヿ一-鿿]", text))


def _load_fetch_log() -> dict:
    try:
        if _FETCH_LOG_PATH.exists():
            return json.loads(_FETCH_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_fetch_log(log: dict) -> None:
    try:
        _FETCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FETCH_LOG_PATH.write_text(
            json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        print(f"  [FundamentalAgent] フェッチログ保存エラー: {e}")


class FundamentalAgent:
    """
    RAG-driven fundamental analysis agent.

    Flow:
      1. retrieve_chunks(ticker)  — search financial_filings ChromaDB
      2a. If ≥1 chunk found      — inject as Reference Context into LLM prompt
      2b. If 0 chunks found      — fallback to yfinance summary + LLM analysis
    """

    def __init__(
        self,
        persist_dir: str = _PERSIST_DIR,
        collection_name: str = _COLLECTION,
        top_k: int = 5,
    ) -> None:
        self.persist_dir     = persist_dir
        self.collection_name = collection_name
        self.top_k           = top_k
        self._embeddings: HuggingFaceEmbeddings | None = None
        self._db: Chroma | None                       = None
        self._llm                                      = None

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _get_embeddings(self) -> HuggingFaceEmbeddings:
        if self._embeddings is None:
            self._embeddings = HuggingFaceEmbeddings(model_name=_EMBED_MODEL)
        return self._embeddings

    def _get_db(self) -> Chroma | None:
        if self._db is not None:
            return self._db
        if not os.path.exists(self.persist_dir):
            return None
        try:
            self._db = Chroma(
                persist_directory=self.persist_dir,
                embedding_function=self._get_embeddings(),
                collection_name=self.collection_name,
            )
            return self._db
        except Exception as e:
            print(f"  [FundamentalAgent] ChromaDB 接続エラー: {e}")
            return None

    def _get_llm(self):
        if self._llm is None:
            from skills.llm_factory import get_llm_instance
            # json_mode=True: FA プロンプトは JSON 出力を明示要求するため
            # Ollama の format="json" を有効化して構造化出力を強制する。
            # （旧コードでは get_llm_instance() が常に format="json" を設定していたが、
            #   llm_factory のバグ修正 PR で json_mode 引数が追加された際に FA への
            #   伝播が漏れ、意図せず json_mode=False になっていた。）
            self._llm = get_llm_instance(json_mode=True)
        return self._llm

    def _call_llm(self, prompt: str) -> str:
        llm = self._get_llm()
        for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
            try:
                return llm.invoke(prompt).content
            except Exception as e:
                if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and delay is not None:
                    print(f"  [FundamentalAgent] Rate limit (attempt {attempt + 1}), {delay}s 待機...")
                    time.sleep(delay)
                else:
                    raise
        raise RuntimeError("LLM 呼び出しがリトライ上限に達しました")

    def _parse_json(self, raw: str) -> dict[str, Any]:
        """
        Robustly extract a JSON object from LLM output.

        Handles three common LLM formatting issues:
        1. Wrapping in ```json ... ``` code fences
        2. Literal newlines inside string values (should be \\n)
        3. Partial JSON when the model truncates mid-output
        """
        # Strip code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

        # Extract the outermost {...} block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {}

        candidate = m.group()

        # First attempt: direct parse
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # Second attempt: escape unescaped literal newlines inside string values.
        # Replace \n that are NOT already escaped (i.e. not preceded by \)
        repaired = re.sub(r'(?<!\\)\n', r'\\n', candidate)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

        # Third attempt: extract only the keys we care about via individual regex.
        # Graceful degradation — returns whatever fields could be parsed.
        partial: dict[str, Any] = {}
        for key in (
            "reasoning", "revenue_growth", "profitability", "risks", "outlook",
            "investment_signal", "trend", "trend_reason", "data_source",
        ):
            pat = rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"'
            km  = re.search(pat, candidate, re.DOTALL)
            if km:
                partial[key] = km.group(1).replace("\\n", "\n")

        # Try to extract nested analysis dict
        analysis_m = re.search(r'"analysis"\s*:\s*(\{[^}]*\})', candidate, re.DOTALL)
        if analysis_m:
            try:
                partial["analysis"] = json.loads(analysis_m.group(1))
            except json.JSONDecodeError:
                pass

        partial["raw_llm_output"] = raw
        return partial

    # ------------------------------------------------------------------ #
    # Step 1: ChromaDB retrieval                                           #
    # ------------------------------------------------------------------ #

    def _build_queries(self, ticker: str) -> list[str]:
        """
        Build 3 complementary search queries covering revenue, risk, and outlook.
        Covers both Japanese and English documents in the same collection.
        """
        if _is_japanese(ticker):
            return [
                f"{ticker} 売上高 営業利益 成長率 前年同期比",
                f"{ticker} リスク要因 財務 負債 自己資本",
                f"{ticker} 業績見通し 今後の展望 中期計画",
            ]
        # English ticker — try English queries; Japanese fallback covers mixed DBs
        return [
            f"{ticker} revenue growth net sales operating income",
            f"{ticker} risk factors debt financial health",
            f"{ticker} business outlook guidance forecast",
        ]

    def retrieve_chunks(self, ticker: str) -> list[str]:
        """
        Search financial_filings ChromaDB for chunks relevant to ticker.

        Two-phase retrieval
        -------------------
        Phase 1 — metadata filter:
            Look for chunks whose `ticker` metadata equals the requested ticker
            (set by EDGAR fetch). Fast and exact; returns immediately if found.
        Phase 2 — semantic search + text relevance guard:
            Falls through when Phase 1 yields nothing (e.g. PDF-ingested docs
            that carry no ticker metadata). Runs 3 HyDE-style queries and then
            confirms the company name appears in at least one returned chunk.

        Returns a deduplicated list of texts (up to top_k * 2).
        Returns [] when no relevant chunks exist.
        """
        db = self._get_db()
        if db is None:
            return []

        ticker_upper = ticker.upper()

        # ── Phase 1: ticker metadata filter (EDGAR-fetched docs) ─────────
        try:
            meta = db.get(
                where={"ticker": {"$eq": ticker_upper}},
                limit=self.top_k * 2,
            )
            meta_chunks = [t for t in (meta.get("documents") or []) if t and t.strip()]
            if meta_chunks:
                print(f"  [FundamentalAgent] メタデータフィルタで {len(meta_chunks)} チャンク取得")
                return meta_chunks[: self.top_k * 2]
        except Exception as e:
            print(f"  [FundamentalAgent] メタデータ検索エラー（Phase 2 へ）: {e}")

        # ── Phase 2: semantic search + text relevance guard ───────────────
        queries        = self._build_queries(ticker)
        seen: set[str] = set()
        chunks: list[str] = []

        for query in queries:
            try:
                docs = db.similarity_search(query, k=self.top_k)
                for doc in docs:
                    text = doc.page_content.strip()
                    if text and text not in seen:
                        seen.add(text)
                        chunks.append(text)
            except Exception as e:
                print(f"  [FundamentalAgent] 検索エラー ({query[:30]}...): {e}")

        if not chunks:
            return []

        # Relevance guard: at least one chunk must mention this company name.
        # Prevents cross-company contamination in a shared collection
        # (e.g. AAPL query returning ExaWizards chunks).
        ticker_lower = ticker.lower()
        if not any(ticker_lower in chunk.lower() for chunk in chunks):
            print(f"  [FundamentalAgent] チャンクに '{ticker}' の記述なし → 関連データなしと判定")
            return []

        return chunks[: self.top_k * 2]

    # ------------------------------------------------------------------ #
    # Step 2a: RAG-based analysis                                          #
    # ------------------------------------------------------------------ #

    def _analyze_with_rag(self, ticker: str, chunks: list[str]) -> dict[str, Any]:
        """
        Chain-of-Thought RAG analysis.

        Guides the LLM through a 5-step reasoning process grounded solely in
        the retrieved primary-source chunks.  The full CoT trace is stored in
        'reasoning' and per-step breakdowns in 'analysis', making every record
        directly usable as distillation fine-tuning data.
        """
        context_block = "\n\n---\n\n".join(
            f"[参考資料 {i + 1}]\n{chunk}" for i, chunk in enumerate(chunks)
        )

        prompt = (
            "## ペルソナ\n"
            "あなたはウォール街の一流ファンダメンタルズアナリストです。\n"
            "感情・憶測・メディア報道を一切排除し、「提供された一次情報のみ」から論理を構築します。\n"
            "資料に数値が明記されていない事項は「資料に記載なし」と明示し、"
            "推測・補完は絶対に行いません。\n\n"

            "## 一次情報（Reference Context）\n"
            f"以下は {ticker} に関する実際の財務資料（SEC報告書・決算書・IR資料）から"
            f"抽出したチャンクです。\n\n"
            f"{context_block}\n\n"

            "## 分析タスク — Chain-of-Thought 5ステップ\n"
            "上記の一次情報のみを根拠として、以下の5ステップで思考プロセスを必ず言語化してください。\n"
            "各ステップで「どの参考資料の、どの記述を根拠にしたか」を明記すること"
            "（例: 参考資料2より、参考資料1・3より）。\n\n"

            "### Step 1: 【前提の確認】\n"
            "提供された一次情報の中から、直近の業績に関する主要な数値"
            "（売上高・営業利益・純利益・EPS・フリーキャッシュフロー等）をすべて抽出・整理してください。\n"
            "資料に記載がない指標は「記載なし」と明示すること。\n\n"

            "### Step 2: 【財務の健全性評価】\n"
            "Step 1 で抽出した数値を基に、以下を評価してください：\n"
            "- 売上・利益の成長性（前年同期比・前四半期比の変化率を数値で示す）\n"
            "- 利益率の水準とトレンド（粗利益率・営業利益率・純利益率）\n"
            "- セグメント別・地域別の成長格差\n"
            "- バランスシートの健全性（負債比率・自己資本・キャッシュポジション）\n\n"

            "### Step 3: 【リスク要因の洗い出し】\n"
            "経営陣が資料内で明示している懸念事項・リスク要因のみを客観的に列挙してください。\n"
            "資料に記載のない外部リスクの推測追加は厳禁です。\n\n"

            "### Step 4: 【将来展望の評価】\n"
            "経営陣が示すガイダンス・中期計画・成長戦略を評価してください。\n"
            "数値目標が資料に記載されている場合は必ず引用してください。\n\n"

            "### Step 5: 【最終結論】\n"
            "Step 1〜4 の論理的帰結として、スイングトレード観点での最終的な投資判断を下してください。\n"
            "判断は以下の5段階から厳密に1つ選択し、その根拠を Step 1〜4 の分析に紐づけて説明してください：\n"
            "STRONG BUY / BUY / HOLD / SELL / STRONG SELL\n\n"

            "## 出力フォーマット\n"
            "思考プロセス全体を含む、以下の JSON 形式のみで出力してください"
            "（コードブロック・余分なテキスト不要）。\n"
            "reasoning フィールドには Step 1〜5 の完全な思考プロセスを、"
            "改行を \\n でエスケープして1つの文字列として格納してください。\n\n"
            "{\n"
            '  "reasoning": "Step 1〜5の完全な思考プロセス（\\nで改行エスケープ済み）",\n'
            '  "analysis": {\n'
            '    "step1_premise":          "【前提の確認】抽出した主要数値の整理（参考資料番号を引用）",\n'
            '    "step2_financial_health": "【財務の健全性評価】成長性・利益率・バランスシートの評価",\n'
            '    "step3_risks":            "【リスク要因の洗い出し】資料記載のリスクのみ列挙",\n'
            '    "step4_outlook":          "【将来展望の評価】ガイダンス・数値目標の引用と評価",\n'
            '    "step5_conclusion":       "【最終結論】投資判断とStep 1〜4への根拠紐づけ"\n'
            '  },\n'
            '  "revenue_growth":    "（引用元：参考資料N）売上高・利益成長性の要約（数値引用）",\n'
            '  "profitability":     "（引用元：参考資料N）利益率・財務健全性の要約",\n'
            '  "risks":             "（引用元：参考資料N）主要リスク要因の要約",\n'
            '  "outlook":           "（引用元：参考資料N）業績見通し・ガイダンスの要約（数値引用）",\n'
            '  "investment_signal": "STRONG BUY|BUY|HOLD|SELL|STRONG SELL",\n'
            '  "trend":             "positive|negative|neutral",\n'
            '  "trend_reason":      "1〜2文の日本語の根拠（参考資料番号を含める）",\n'
            '  "data_source":       "RAG（一次情報：financial_filings）"\n'
            "}"
        )

        raw    = self._call_llm(prompt)
        result = self._parse_json(raw)

        # investment_signal → trend の整合フォールバック
        if "investment_signal" in result and "trend" not in result:
            _sig_map = {
                "STRONG BUY": "positive", "BUY": "positive",
                "HOLD": "neutral",
                "SELL": "negative",   "STRONG SELL": "negative",
            }
            result["trend"] = _sig_map.get(result.get("investment_signal", ""), "neutral")

        result.setdefault("data_source", "RAG（一次情報：financial_filings）")
        result["chunks_used"]    = len(chunks)
        result["data_available"] = True
        result["fallback_used"]  = False
        result["raw_llm_output"] = raw
        return result

    # ------------------------------------------------------------------ #
    # Step 1b: EDGAR staleness helpers                                    #
    # ------------------------------------------------------------------ #

    def _is_edgar_stale(self, ticker: str) -> bool:
        """Return True if EDGAR data for ticker is older than _EDGAR_STALENESS_DAYS."""
        entry = _load_fetch_log().get(ticker.upper())
        if not entry:
            return False  # never fetched — handled by the 0-chunks path
        try:
            fetched_at = datetime.fromisoformat(entry["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - fetched_at).days >= _EDGAR_STALENESS_DAYS
        except Exception:
            return False

    def _record_edgar_fetch(self, ticker: str, form: str) -> None:
        """Record a successful EDGAR fetch (or freshness check) in the fetch log."""
        log = _load_fetch_log()
        log[ticker.upper()] = {
            "form":       form,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_fetch_log(log)

    # ------------------------------------------------------------------ #
    # Step 1c: EDGAR self-healing fetch                                   #
    # ------------------------------------------------------------------ #

    def _fetch_from_edgar(self, ticker: str) -> dict[str, Any]:
        """
        Trigger FinancialDocumentLoader.fetch_from_edgar() to auto-populate
        the financial_filings collection for a US-listed ticker.

        Called when ChromaDB returns 0 relevant chunks and the ticker looks
        like a US stock (non-Japanese characters).  After a successful fetch
        the DB connection is invalidated so the next retrieve_chunks() call
        picks up the newly ingested data.

        Returns the fetch result dict from FinancialDocumentLoader.
        """
        import sys
        from pathlib import Path as _Path

        # Ensure the project root is on sys.path when this module is imported
        # from a subdirectory (e.g. agents/fundamental_agent.py → skills/)
        project_root = str(_Path(__file__).resolve().parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from skills.financial_data_loader import FinancialDocumentLoader

        loader = FinancialDocumentLoader(
            persist_dir=self.persist_dir,
            collection_name=self.collection_name,
        )
        result = loader.fetch_from_edgar(ticker, prefer_quarterly=True)
        return result

    # ------------------------------------------------------------------ #
    # Step 2b: yfinance fallback                                           #
    # ------------------------------------------------------------------ #

    def _analyze_with_yfinance(self, ticker: str) -> dict[str, Any]:
        """
        Chain-of-Thought fallback analysis using yfinance summary data.

        Applies the same 5-step CoT structure as _analyze_with_rag so that
        yfinance-fallback records remain structurally consistent training
        examples (marked fallback_used=True for downstream quality filtering).
        """
        print(f"  [FundamentalAgent] yfinance フォールバック: {ticker}")
        try:
            import yfinance as yf

            stock = yf.Ticker(ticker)
            info  = stock.info or {}
            summary = {
                "company":        info.get("longName", ticker),
                "sector":         info.get("sector", "N/A"),
                "market_cap":     info.get("marketCap", "N/A"),
                "total_revenue":  info.get("totalRevenue", "N/A"),
                "profit_margin":  info.get("profitMargins", "N/A"),
                "pe_ratio":       info.get("trailingPE", "N/A"),
                "debt_to_equity": info.get("debtToEquity", "N/A"),
                "description":    info.get("longBusinessSummary", "")[:400],
            }
        except Exception as e:
            return {
                "error":          f"yfinance 取得エラー: {e}",
                "data_available": False,
                "fallback_used":  True,
                "data_source":    "yfinance（フォールバック）",
                "trend":          "neutral",
                "trend_reason":   "データ取得失敗",
            }

        summary_text = json.dumps(summary, ensure_ascii=False, indent=2)

        prompt = (
            "## ペルソナ\n"
            "あなたはウォール街の一流ファンダメンタルズアナリストです。\n"
            "感情・憶測を一切排除し、「提供されたデータのみ」から論理を構築します。\n"
            "データに存在しない情報は「データに記載なし」と明示し、推測・補完は行いません。\n\n"

            "## データソース（yfinance — 二次情報）\n"
            f"⚠️ 注意: {ticker} の一次資料（SEC報告書・決算書）がデータベースに存在しないため、\n"
            "yfinance の財務サマリー（二次情報）をフォールバックとして使用します。\n"
            "数値の精度はyfinanceの取得タイミングに依存します。\n\n"
            f"{summary_text}\n\n"

            "## 分析タスク — Chain-of-Thought 5ステップ\n"
            "上記のデータのみを根拠として、以下の5ステップで思考プロセスを言語化してください。\n"
            "各ステップで「上記データのどのフィールドを根拠にしたか」を明記すること"
            "（例: profit_margin より、pe_ratio より）。\n\n"

            "### Step 1: 【前提の確認】\n"
            "提供されたデータから、企業の規模・セクター・主要財務指標を整理してください。\n"
            "データに存在しない指標は「データに記載なし」と明示すること。\n\n"

            "### Step 2: 【財務の健全性評価】\n"
            "Step 1 のデータを基に、以下を評価してください：\n"
            "- 利益率の水準（profit_margin の絶対値とセクター比較的な妥当性）\n"
            "- バランスシートの健全性（debt_to_equity の水準）\n"
            "- バリュエーション（pe_ratio の水準）\n"
            "- 事業規模と収益性のバランス（market_cap vs total_revenue）\n\n"

            "### Step 3: 【リスク要因の洗い出し】\n"
            "提供されたデータ（description・各財務指標）から読み取れるリスク要因を列挙してください。\n"
            "⚠️ 二次情報であるため、リスク評価の確度は一次資料（SEC報告書）より低い点を明示すること。\n\n"

            "### Step 4: 【将来展望の評価】\n"
            "description やセクター情報から読み取れる事業の方向性を評価してください。\n"
            "yfinance には将来ガイダンスが含まれないため、推測にならないよう注意すること。\n\n"

            "### Step 5: 【最終結論】\n"
            "Step 1〜4 の論理的帰結として、スイングトレード観点での最終的な投資判断を下してください。\n"
            "⚠️ 二次情報に基づく判断のため、確信度は一次資料分析より低いことを明示すること。\n"
            "判断は以下の5段階から厳密に1つ選択してください：\n"
            "STRONG BUY / BUY / HOLD / SELL / STRONG SELL\n\n"

            "## 出力フォーマット\n"
            "思考プロセス全体を含む、以下の JSON 形式のみで出力してください"
            "（コードブロック・余分なテキスト不要）。\n"
            "reasoning フィールドには Step 1〜5 の完全な思考プロセスを、"
            "改行を \\n でエスケープして1つの文字列として格納してください。\n\n"
            "{\n"
            '  "reasoning": "Step 1〜5の完全な思考プロセス（\\nで改行エスケープ済み）",\n'
            '  "analysis": {\n'
            '    "step1_premise":          "【前提の確認】企業規模・セクター・主要財務指標の整理",\n'
            '    "step2_financial_health": "【財務の健全性評価】利益率・バランスシート・バリュエーションの評価",\n'
            '    "step3_risks":            "【リスク要因の洗い出し】データから読み取れるリスク（確度低を明示）",\n'
            '    "step4_outlook":          "【将来展望の評価】事業の方向性（推測不可領域を明示）",\n'
            '    "step5_conclusion":       "【最終結論】投資判断と確信度の制限（二次情報による）"\n'
            '  },\n'
            '  "revenue_growth":    "売上高・利益成長性の評価（yfinanceデータより）",\n'
            '  "profitability":     "利益率・財務健全性の評価（yfinanceデータより）",\n'
            '  "risks":             "主要リスク要因（確度：低 — 二次情報）",\n'
            '  "outlook":           "将来展望（推測不可領域あり — yfinanceにガイダンスなし）",\n'
            '  "investment_signal": "STRONG BUY|BUY|HOLD|SELL|STRONG SELL",\n'
            '  "trend":             "positive|negative|neutral",\n'
            '  "trend_reason":      "1〜2文の日本語の根拠（二次情報である旨を含める）",\n'
            '  "data_source":       "yfinance（フォールバック）"\n'
            "}"
        )

        try:
            raw    = self._call_llm(prompt)
            result = self._parse_json(raw)
        except Exception as e:
            result = {"error": str(e), "raw_llm_output": ""}
            raw    = ""

        # investment_signal → trend の整合フォールバック
        if "investment_signal" in result and "trend" not in result:
            _sig_map = {
                "STRONG BUY": "positive", "BUY": "positive",
                "HOLD": "neutral",
                "SELL": "negative",   "STRONG SELL": "negative",
            }
            result["trend"] = _sig_map.get(result.get("investment_signal", ""), "neutral")

        result.setdefault("data_source", "yfinance（フォールバック）")
        result.setdefault("raw_llm_output", raw)
        result["yfinance_summary"] = summary
        result["data_available"]   = True
        result["fallback_used"]    = True
        result["chunks_used"]      = 0
        return result

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def analyze(self, ticker: str) -> dict[str, Any]:
        """
        Run fundamental analysis for the given ticker.

        Self-healing flow
        -----------------
        1. retrieve_chunks()  — search financial_filings ChromaDB
        2. If 0 chunks AND US ticker → _fetch_from_edgar() auto-populates DB
        3. retry retrieve_chunks()
        4. If still 0 chunks → _analyze_with_yfinance() (last resort)

        Returns
        -------
        dict with keys:
            ticker           : str
            trend            : "positive" | "negative" | "neutral"
            trend_reason     : str
            data_available   : bool
            fallback_used    : bool
            edgar_auto_fetch : bool   True when EDGAR was triggered automatically
            data_source      : str
            chunks_used      : int
            revenue_growth   : str
            profitability    : str
            risks            : str
            outlook          : str
            error            : str | None
        """
        print(f"\n  [FundamentalAgent] ── {ticker} ファンダメンタルズ分析 ──")

        chunks          = self.retrieve_chunks(ticker)
        edgar_triggered = False
        fetch_error: str | None = None

        print(f"  [FundamentalAgent] ChromaDB ({self.collection_name}): {len(chunks)} チャンク取得 (初回)")

        # ── Staleness: re-fetch if data is older than one quarter ────────
        if chunks and not _is_japanese(ticker) and self._is_edgar_stale(ticker):
            print(
                f"  [FundamentalAgent] EDGAR データが {_EDGAR_STALENESS_DAYS} 日超 → "
                "最新四半期データを再確認..."
            )
            edgar_triggered = True
            try:
                fetch_result = self._fetch_from_edgar(ticker)
            except Exception as exc:
                print(f"  [FundamentalAgent] EDGAR 再取得エラー (既存データ継続): {exc}")
                fetch_result = {"chunks_added": 0, "form": "", "error": str(exc)}
            if fetch_result.get("chunks_added", 0) > 0:
                self._record_edgar_fetch(ticker, fetch_result.get("form", ""))
                self._db = None
                chunks = self.retrieve_chunks(ticker)
                print(f"  [FundamentalAgent] 最新データ取得後: {len(chunks)} チャンク")
            else:
                # No newer filing → still update the check timestamp
                self._record_edgar_fetch(ticker, fetch_result.get("form", ""))
                print("  [FundamentalAgent] 新しいファイリングなし — 既存データを使用")

        # ── Self-healing: US ticker with no local data → try EDGAR ──────
        if not chunks and not _is_japanese(ticker):
            print(f"  [FundamentalAgent] 該当データなし → EDGAR 自律取得を開始...")
            fetch_result    = self._fetch_from_edgar(ticker)
            edgar_triggered = True

            if fetch_result.get("chunks_added", 0) > 0:
                self._record_edgar_fetch(ticker, fetch_result.get("form", ""))
                print(f"  [FundamentalAgent] EDGAR 取得完了 "
                      f"({fetch_result['chunks_added']} チャンク) — 再検索します...")
                self._db = None
                chunks = self.retrieve_chunks(ticker)
                print(f"  [FundamentalAgent] 再検索結果: {len(chunks)} チャンク")
            else:
                fetch_error = fetch_result.get("error")
                print(f"  [FundamentalAgent] EDGAR 取得失敗: {fetch_error}")

        # ── Analysis path ────────────────────────────────────────────────
        if chunks:
            result = self._analyze_with_rag(ticker, chunks)
        else:
            print(f"  [FundamentalAgent] 有効なチャンクなし → yfinance フォールバック")
            result = self._analyze_with_yfinance(ticker)

        result["ticker"]           = ticker
        result["edgar_auto_fetch"] = edgar_triggered
        result.setdefault("error",          fetch_error)
        result.setdefault("trend",          "neutral")
        result.setdefault("trend_reason",   "")
        result.setdefault("revenue_growth", "N/A")
        result.setdefault("profitability",  "N/A")
        result.setdefault("risks",          "N/A")
        result.setdefault("outlook",        "N/A")
        return result
