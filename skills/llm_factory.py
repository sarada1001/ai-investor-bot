"""
skills/llm_factory.py — 統一 LLM ファクトリー

環境変数で Ollama / Gemini を切り替える。各スキル・エージェントはこのモジュールを
通じて LLM を取得し、直接 ChatGoogleGenerativeAI を呼ばない。

優先順（デフォルト）:
  1. Ollama（OLLAMA_BASE_URL のサーバーに接続可能な場合）
  2. Gemini API（フォールバック、DISABLE_GEMINI=true で封鎖可能）

環境変数:
  OLLAMA_BASE_URL    : Ollama エンドポイント（デフォルト: http://localhost:11434）
  OLLAMA_MODEL       : 使用モデル（デフォルト: llama3.1）
  FORCE_GEMINI=true  : Ollama を無視して Gemini を常に使用
  DISABLE_GEMINI=true: Gemini フォールバックを完全封鎖（課金ゼロ保証）
"""

from __future__ import annotations

import os
import warnings
from typing import Any

import requests
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "llama3.1")
_DEFAULT_GEMINI  = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
# FORCE_GEMINI / DISABLE_GEMINI は get_llm() 内で毎回 os.getenv() を呼んで読む。
# モジュールレベルで固定すると cron 起動時に .env が未ロードのまま False になる。

_ollama_available: bool | None = None  # None = 未チェック


def _check_ollama() -> bool:
    """Ollama サーバーへの接続を一度だけ確認してキャッシュする。"""
    global _ollama_available
    if _ollama_available is not None:
        return _ollama_available
    try:
        resp = requests.get(f"{_OLLAMA_BASE_URL}/api/tags", timeout=3)
        _ollama_available = resp.status_code == 200
    except Exception:
        _ollama_available = False
    return _ollama_available


def get_llm(
    ollama_model: str | None = None,
    gemini_model: str | None = None,
    temperature: float = 0,
    json_mode: bool = False,
) -> tuple[Any, str]:
    """
    LLM インスタンスとソース名を返す。

    Args:
        json_mode: True の場合、Ollama に format="json" を指定して構造化 JSON 出力を強制する。
                   ニュース/SNS センチメント解析など JSON を返すプロンプト専用。
                   テキスト要約（ManagerAgent の rationale など）では False にすること。

    Returns:
        (llm, source)  source: "ollama" | "gemini"

    Raises:
        RuntimeError: DISABLE_GEMINI=true かつ Ollama 接続不可の場合
    """
    # .env を確実に読む（cron 起動等でインポート前に未ロードの場合も対応）
    load_dotenv(override=False)
    disable_gemini = os.getenv("DISABLE_GEMINI", "false").lower() == "true"
    force_gemini   = os.getenv("FORCE_GEMINI",   "false").lower() == "true"

    model_ollama = ollama_model or _OLLAMA_MODEL
    model_gemini = gemini_model or _DEFAULT_GEMINI

    use_ollama = (not force_gemini) and _check_ollama()

    if use_ollama:
        from langchain_community.chat_models import ChatOllama
        # json_mode=True の場合のみ format="json" を設定する。
        # テキスト生成プロンプト（rationale 要約等）で format="json" を使うと
        # Llama 3.1 が JSON 構造を出力しようとして文章が崩壊するため分離する。
        ollama_kwargs: dict[str, Any] = {
            "base_url":    _OLLAMA_BASE_URL,
            "model":       model_ollama,
            "temperature": temperature,
        }
        if json_mode:
            ollama_kwargs["format"] = "json"
        llm = ChatOllama(**ollama_kwargs)
        return llm, "ollama"

    if disable_gemini:
        raise RuntimeError(
            "DISABLE_GEMINI=true が設定されており、Ollama にも接続できません。"
            f"Ollama サーバー ({_OLLAMA_BASE_URL}) を確認してください。"
        )

    from langchain_google_genai import ChatGoogleGenerativeAI
    llm = ChatGoogleGenerativeAI(
        model       = model_gemini,
        temperature = temperature,
    )
    return llm, "gemini"


def get_llm_instance(
    ollama_model: str | None = None,
    gemini_model: str | None = None,
    temperature: float = 0,
    json_mode: bool = False,
) -> Any:
    """LLM インスタンスのみを返す（ソース名不要な呼び出し元向け）。

    Args:
        json_mode: True にすると Ollama で format="json" を有効化。
                   JSON 構造を返すプロンプト（センチメント解析等）にのみ使用すること。
    """
    llm, _ = get_llm(
        ollama_model=ollama_model,
        gemini_model=gemini_model,
        temperature=temperature,
        json_mode=json_mode,
    )
    return llm


def is_ollama_active() -> bool:
    """現在 Ollama が使用中かどうかを返す（ログ表示用）。"""
    load_dotenv(override=False)
    force_gemini = os.getenv("FORCE_GEMINI", "false").lower() == "true"
    return (not force_gemini) and _check_ollama()
