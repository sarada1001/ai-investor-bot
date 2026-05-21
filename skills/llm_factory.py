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

_OLLAMA_BASE_URL  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL", "llama3.1")
_FORCE_GEMINI     = os.getenv("FORCE_GEMINI", "false").lower() == "true"
_DISABLE_GEMINI   = os.getenv("DISABLE_GEMINI", "false").lower() == "true"
_DEFAULT_GEMINI   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

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
) -> tuple[Any, str]:
    """
    LLM インスタンスとソース名を返す。

    Returns:
        (llm, source)  source: "ollama" | "gemini"

    Raises:
        RuntimeError: DISABLE_GEMINI=true かつ Ollama 接続不可の場合
    """
    model_ollama = ollama_model or _OLLAMA_MODEL
    model_gemini = gemini_model or _DEFAULT_GEMINI

    use_ollama = (not _FORCE_GEMINI) and _check_ollama()

    if use_ollama:
        from langchain_community.chat_models import ChatOllama
        llm = ChatOllama(
            base_url    = _OLLAMA_BASE_URL,
            model       = model_ollama,
            temperature = temperature,
            format      = "json",
        )
        return llm, "ollama"

    if _DISABLE_GEMINI:
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
) -> Any:
    """LLM インスタンスのみを返す（ソース名不要な呼び出し元向け）。"""
    llm, _ = get_llm(ollama_model=ollama_model, gemini_model=gemini_model, temperature=temperature)
    return llm


def is_ollama_active() -> bool:
    """現在 Ollama が使用中かどうかを返す（ログ表示用）。"""
    return (not _FORCE_GEMINI) and _check_ollama()
