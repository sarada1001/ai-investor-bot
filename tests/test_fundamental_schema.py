"""tests/test_fundamental_schema.py — FundamentalAgent スキーマ整合テスト

カバー対象:
  - _normalize_fa_result: analysisネスト → トップレベルへの昇格ロジック
  - _parse_json: 部分抽出パス（score数値抽出を含む）
  - _analyze_with_rag / _analyze_with_yfinance: Geminiが analysis にネストした場合の回帰テスト

背景:
  Geminiは trend/score/trend_reason をプロンプトスキーマで指定したトップレベルではなく
  analysis オブジェクト内にネストして返すことがある。Python側がトップレベルのみ参照するため
  neutral/0.0 にフォールバックしてしまう問題を _normalize_fa_result で修正した。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# fundamental_agent.py は langchain / chromadb を module-level でインポートする。
# これらは本 dev 環境にインストールされていないため、インポート前に sys.modules に
# ダミーを差し込んでモジュール収集エラーを回避する。
def _stub_module(name: str) -> ModuleType:
    mod = ModuleType(name)
    sys.modules[name] = mod
    return mod

for _pkg in (
    "dotenv",
    "langchain_community",
    "langchain_community.vectorstores",
    "langchain_huggingface",
    "chromadb",
):
    if _pkg not in sys.modules:
        _stub_module(_pkg)

# dotenv.load_dotenv must be a callable
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None  # type: ignore[attr-defined]

# Chroma / HuggingFaceEmbeddings は属性として参照されるため MagicMock を差し込む
sys.modules["langchain_community.vectorstores"].Chroma = MagicMock()  # type: ignore[attr-defined]
sys.modules["langchain_huggingface"].HuggingFaceEmbeddings = MagicMock()  # type: ignore[attr-defined]

from agents.fundamental_agent import (
    FundamentalAgent,
    _ANALYSIS_PROMOTED_KEYS,
    _normalize_fa_result,
)


# ──────────────────────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────────────────────

def _make_agent() -> FundamentalAgent:
    """LLM/DB接続なしの最小FundamentalAgentインスタンスを返す。"""
    agent = FundamentalAgent.__new__(FundamentalAgent)
    agent.persist_dir     = "chroma_db_saved"
    agent.collection_name = "financial_filings"
    agent.top_k           = 5
    agent._embeddings     = None
    agent._db             = None
    agent._llm            = None
    return agent


def _flat_llm_json(
    trend: str = "positive",
    score: float = 0.72,
    trend_reason: str = "売上増加トレンドが継続",
    investment_signal: str = "BUY",
) -> str:
    """トップレベルに trend/score/trend_reason を含む正常なLLM出力JSON文字列。"""
    return json.dumps({
        "reasoning": "Step1〜5 reasoning text",
        "analysis": {
            "step1_premise":          "前提確認テキスト",
            "step2_financial_health": "財務健全性テキスト",
            "step3_risks":            "リスクテキスト",
            "step4_outlook":          "展望テキスト",
            "step5_conclusion":       "結論テキスト",
        },
        "revenue_growth":    "10% YoY",
        "profitability":     "利益率15%",
        "risks":             "競合リスク",
        "outlook":           "強気",
        "investment_signal": investment_signal,
        "score":             score,
        "trend":             trend,
        "trend_reason":      trend_reason,
        "data_source":       "RAG（一次情報：financial_filings）",
    }, ensure_ascii=False)


def _nested_llm_json(
    trend: str = "positive",
    score: float = 0.72,
    trend_reason: str = "売上増加トレンドが継続",
    investment_signal: str = "BUY",
) -> str:
    """Geminiが trend/score/trend_reason を analysis 内にネストした不正なLLM出力JSON。"""
    return json.dumps({
        "reasoning": "Step1〜5 reasoning text",
        "analysis": {
            "step1_premise":          "前提確認テキスト",
            "step2_financial_health": "財務健全性テキスト",
            "step3_risks":            "リスクテキスト",
            "step4_outlook":          "展望テキスト",
            "step5_conclusion":       "結論テキスト",
            "trend":                  trend,
            "score":                  score,
            "trend_reason":           trend_reason,
        },
        "revenue_growth":    "10% YoY",
        "profitability":     "利益率15%",
        "risks":             "競合リスク",
        "outlook":           "強気",
        "investment_signal": investment_signal,
        # trend/score/trend_reason はトップレベルに存在しない
        "data_source":       "RAG（一次情報：financial_filings）",
    }, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────
# _ANALYSIS_PROMOTED_KEYS の定義確認
# ──────────────────────────────────────────────────────────────────────

class TestAnalysisPromotedKeysConstant:
    def test_contains_required_fields(self):
        for field in ("trend", "score", "trend_reason"):
            assert field in _ANALYSIS_PROMOTED_KEYS, (
                f"_ANALYSIS_PROMOTED_KEYS に '{field}' が含まれていない"
            )


# ──────────────────────────────────────────────────────────────────────
# _normalize_fa_result のユニットテスト
# ──────────────────────────────────────────────────────────────────────

class TestNormalizeFaResult:
    """_normalize_fa_result の全パターンを検証する。"""

    def test_promotes_trend_from_analysis(self):
        result = {"analysis": {"trend": "positive", "step5_conclusion": "BUY"}}
        out = _normalize_fa_result(result)
        assert out["trend"] == "positive"

    def test_promotes_score_from_analysis(self):
        result = {"analysis": {"score": 0.75, "step5_conclusion": "BUY"}}
        out = _normalize_fa_result(result)
        assert out["score"] == 0.75

    def test_promotes_trend_reason_from_analysis(self):
        result = {"analysis": {"trend_reason": "売上増加により"}}
        out = _normalize_fa_result(result)
        assert out["trend_reason"] == "売上増加により"

    def test_promotes_all_three_simultaneously(self):
        result = {
            "investment_signal": "BUY",
            "analysis": {
                "step5_conclusion": "BUY判定",
                "trend":            "positive",
                "score":            0.68,
                "trend_reason":     "参考資料1より売上高が前年比+20%",
            },
        }
        out = _normalize_fa_result(result)
        assert out["trend"]        == "positive"
        assert out["score"]        == 0.68
        assert "参考資料1" in out["trend_reason"]

    def test_does_not_overwrite_existing_top_level_trend(self):
        """トップレベルに既存値があれば analysis の値で上書きしない。"""
        result = {
            "trend": "neutral",  # already set
            "analysis": {"trend": "positive"},
        }
        out = _normalize_fa_result(result)
        assert out["trend"] == "neutral"

    def test_does_not_overwrite_existing_top_level_score(self):
        result = {
            "score": 0.0,  # already set (zero is a valid value, not missing)
            "analysis": {"score": 0.9},
        }
        out = _normalize_fa_result(result)
        assert out["score"] == 0.0

    def test_noop_when_analysis_absent(self):
        result = {"investment_signal": "HOLD"}
        out = _normalize_fa_result(result)
        assert "trend" not in out
        assert "score" not in out

    def test_noop_when_analysis_is_not_dict(self):
        """analysis が dict 以外（文字列など）の場合はクラッシュしない。"""
        result = {"analysis": "some string", "investment_signal": "HOLD"}
        out = _normalize_fa_result(result)
        assert "trend" not in out

    def test_noop_when_analysis_is_none(self):
        result = {"analysis": None}
        out = _normalize_fa_result(result)
        assert "trend" not in out

    def test_returns_same_dict_identity(self):
        """in-place 変更 + 参照返しであることを確認する。"""
        result = {"analysis": {"trend": "negative"}}
        out = _normalize_fa_result(result)
        assert out is result

    def test_analysis_step_fields_remain_nested(self):
        """step1〜5 などの学習データ用フィールドはネストされたまま残る。"""
        result = {
            "analysis": {
                "step1_premise":          "前提",
                "step5_conclusion":       "結論",
                "trend":                  "positive",
            }
        }
        out = _normalize_fa_result(result)
        assert "step1_premise"    in out["analysis"]
        assert "step5_conclusion" in out["analysis"]
        assert out["trend"]       == "positive"


# ──────────────────────────────────────────────────────────────────────
# _parse_json のユニットテスト（score数値抽出を中心に）
# ──────────────────────────────────────────────────────────────────────

class TestParseJson:
    """FundamentalAgent._parse_json の主要パスを検証する。"""

    def _agent(self) -> FundamentalAgent:
        return _make_agent()

    def test_clean_json_top_level_fields(self):
        raw = _flat_llm_json(trend="negative", score=-0.6, trend_reason="売上減少")
        result = self._agent()._parse_json(raw)
        assert result["trend"]        == "negative"
        assert result["score"]        == -0.6
        assert result["trend_reason"] == "売上減少"

    def test_code_fence_stripped(self):
        raw = "```json\n" + _flat_llm_json() + "\n```"
        result = self._agent()._parse_json(raw)
        assert result["trend"] == "positive"

    def test_partial_path_extracts_score_as_float(self):
        """不正JSON（JSONDecodeError）でも score を数値として抽出できる。

        注: _parse_json の第三パスが発動するには { と } の両方が必要。
        また score が数値のため文字列 regex では取れないが、数値専用 regex で取れる。
        """
        malformed = '{"investment_signal": "BUY", "score": 0.65, "trend": "positive" INVALID }'
        result = self._agent()._parse_json(malformed)
        assert "score" in result
        assert result["score"] == pytest.approx(0.65)

    def test_partial_path_extracts_negative_score(self):
        malformed = '{"investment_signal": "SELL", "score": -0.72, "trend": "negative" INVALID }'
        result = self._agent()._parse_json(malformed)
        assert result.get("score") == pytest.approx(-0.72)

    def test_empty_string_returns_empty_dict(self):
        result = self._agent()._parse_json("")
        assert result == {}

    def test_no_json_object_returns_empty_dict(self):
        result = self._agent()._parse_json("just plain text with no braces")
        assert result == {}


# ──────────────────────────────────────────────────────────────────────
# 回帰テスト: _analyze_with_rag でネスト出力を正しく処理する
# ──────────────────────────────────────────────────────────────────────

class TestAnalyzeWithRagNestedSchema:
    """
    Geminiが trend/score/trend_reason を analysis 内にネストして返した場合に、
    _analyze_with_rag が正しい値を result のトップレベルに格納することを検証する。
    """

    def test_nested_trend_score_trend_reason_are_promoted(self):
        agent = _make_agent()
        raw   = _nested_llm_json(trend="negative", score=-0.55, trend_reason="売上減少が継続")

        with patch.object(agent, "_call_llm", return_value=raw):
            result = agent._analyze_with_rag("AAPL", ["chunk1", "chunk2"])

        assert result["trend"]        == "negative",          "trend が analysis からプロモートされていない"
        assert result["score"]        == pytest.approx(-0.55), "score が analysis からプロモートされていない"
        assert "売上減少" in result["trend_reason"],          "trend_reason が analysis からプロモートされていない"

    def test_flat_output_still_works(self):
        """正常なトップレベル出力は引き続き正しく動作する（回帰なし）。"""
        agent = _make_agent()
        raw   = _flat_llm_json(trend="positive", score=0.72, trend_reason="増収増益")

        with patch.object(agent, "_call_llm", return_value=raw):
            result = agent._analyze_with_rag("AAPL", ["chunk1"])

        assert result["trend"]        == "positive"
        assert result["score"]        == pytest.approx(0.72)
        assert "増収増益" in result["trend_reason"]

    def test_nested_score_is_clamped_to_valid_range(self):
        """analysis 内の score が ±1.0 を超えても正規化される。"""
        agent = _make_agent()
        raw   = _nested_llm_json(score=1.5)

        with patch.object(agent, "_call_llm", return_value=raw):
            result = agent._analyze_with_rag("AAPL", ["chunk1"])

        assert result["score"] <= 1.0

    def test_investment_signal_fallback_when_trend_absent_everywhere(self):
        """trend が analysis にも存在しない場合、investment_signal からフォールバックする。"""
        agent = _make_agent()
        raw   = json.dumps({
            "reasoning":         "reasoning",
            "analysis":          {"step5_conclusion": "BUY判定"},
            "investment_signal": "STRONG BUY",
            "revenue_growth":    "N/A",
            "data_source":       "RAG",
        }, ensure_ascii=False)

        with patch.object(agent, "_call_llm", return_value=raw):
            result = agent._analyze_with_rag("AAPL", ["chunk1"])

        assert result["trend"] == "positive"  # investment_signal → trend フォールバック

    def test_result_contains_mandatory_metadata(self):
        """_analyze_with_rag の戻り値が必須メタデータキーを含むことを確認する。"""
        agent = _make_agent()
        raw   = _flat_llm_json()

        with patch.object(agent, "_call_llm", return_value=raw):
            result = agent._analyze_with_rag("AAPL", ["chunk1", "chunk2"])

        for key in ("trend", "score", "trend_reason", "chunks_used", "data_available", "fallback_used"):
            assert key in result, f"必須キー '{key}' が戻り値に存在しない"
        assert result["chunks_used"]    == 2
        assert result["data_available"] is True
        assert result["fallback_used"]  is False


# ──────────────────────────────────────────────────────────────────────
# 回帰テスト: _analyze_with_yfinance でネスト出力を正しく処理する
# ──────────────────────────────────────────────────────────────────────

class TestAnalyzeWithYfinanceNestedSchema:
    """
    yfinance フォールバックパスでも同一の normalize ロジックが適用されることを検証する。
    """

    def _make_yf_mock(self, summary_override: dict | None = None):
        """yfinance Ticker モックを返す。"""
        info = {
            "longName": "Apple Inc.", "sector": "Technology",
            "marketCap": 3_000_000_000_000, "totalRevenue": 400_000_000_000,
            "profitMargins": 0.25, "trailingPE": 30.0,
            "debtToEquity": 1.5, "longBusinessSummary": "Apple designs consumer electronics.",
        }
        if summary_override:
            info.update(summary_override)
        mock_ticker = MagicMock()
        mock_ticker.info = info
        return mock_ticker

    def test_nested_trend_score_trend_reason_are_promoted(self):
        agent = _make_agent()
        raw   = _nested_llm_json(trend="positive", score=0.55, trend_reason="利益率が高水準")

        mock_ticker = self._make_yf_mock()
        with (
            patch("yfinance.Ticker", return_value=mock_ticker),
            patch.object(agent, "_call_llm", return_value=raw),
        ):
            result = agent._analyze_with_yfinance("AAPL")

        assert result["trend"]        == "positive",          "trend が analysis からプロモートされていない"
        assert result["score"]        == pytest.approx(0.55), "score が analysis からプロモートされていない"
        assert "利益率" in result["trend_reason"],            "trend_reason が analysis からプロモートされていない"

    def test_flat_output_still_works(self):
        agent = _make_agent()
        raw   = _flat_llm_json(trend="neutral", score=0.0, trend_reason="データ不足")

        mock_ticker = self._make_yf_mock()
        with (
            patch("yfinance.Ticker", return_value=mock_ticker),
            patch.object(agent, "_call_llm", return_value=raw),
        ):
            result = agent._analyze_with_yfinance("AAPL")

        assert result["trend"]  == "neutral"
        assert result["score"]  == pytest.approx(0.0)
        assert "データ不足" in result["trend_reason"]

    def test_result_contains_mandatory_metadata(self):
        agent = _make_agent()
        raw   = _flat_llm_json()

        mock_ticker = self._make_yf_mock()
        with (
            patch("yfinance.Ticker", return_value=mock_ticker),
            patch.object(agent, "_call_llm", return_value=raw),
        ):
            result = agent._analyze_with_yfinance("AAPL")

        for key in ("trend", "score", "trend_reason", "chunks_used", "data_available", "fallback_used"):
            assert key in result, f"必須キー '{key}' が戻り値に存在しない"
        assert result["chunks_used"]   == 0
        assert result["data_available"] is True
        assert result["fallback_used"]  is True
