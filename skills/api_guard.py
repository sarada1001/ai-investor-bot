"""
skills/api_guard.py — 外部API Rate Limit 安全装置

yfinance / requests の呼び出しに対して:
  - tenacity による指数バックオフ付き自動リトライ（429 / ConnectionError / Timeout）
  - 逐次呼び出し間の最小インターバル（スロットリング）

Usage:
    from skills.api_guard import yf_download_safe, yf_ticker_safe

    df = yf_download_safe("AAPL", period="3mo", progress=False, auto_adjust=True)
    ticker = yf_ticker_safe("MSFT")
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
import yfinance as yf
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ─── 設定 ────────────────────────────────────────────────────────────────────
# リトライ回数（4回 = 初回 + 最大3リトライ）
_MAX_ATTEMPTS = 4
# バックオフ: 5s → 10s → 20s → 40s（最大60s）
_WAIT_MIN_SECS = 5
_WAIT_MAX_SECS = 60
# 逐次呼び出し間の最小インターバル（スロットリング）
_THROTTLE_SECS = 0.5


# ─── 429 判定 ─────────────────────────────────────────────────────────────────

def _is_rate_limit_error(exc: BaseException) -> bool:
    """429 / 接続エラー / タイムアウトを Rate Limit 系エラーとして判定する。"""
    msg = str(exc).lower()
    return (
        "429" in msg
        or "too many requests" in msg
        or "connection" in msg
        or "timeout" in msg
        or "read timed out" in msg
    )


def _log_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    logger.warning(
        "[api_guard] リトライ %d/%d — %s: %s",
        state.attempt_number,
        _MAX_ATTEMPTS,
        type(exc).__name__ if exc else "?",
        str(exc)[:120] if exc else "",
    )


# ─── yf.download セーフラッパー ───────────────────────────────────────────────

@retry(
    retry=retry_if_exception(_is_rate_limit_error),
    wait=wait_exponential(multiplier=1, min=_WAIT_MIN_SECS, max=_WAIT_MAX_SECS),
    stop=stop_after_attempt(_MAX_ATTEMPTS),
    before_sleep=_log_retry,
    reraise=True,
)
def yf_download_safe(tickers: str, **kwargs: Any) -> pd.DataFrame:
    """
    yf.download() の Rate Limit 対応ラッパー。

    429 / ConnectionError / Timeout 発生時に指数バックオフで最大3回リトライ。
    全リトライ失敗後は例外を再 raise（呼び出し元の except Exception が受け取る）。
    """
    result: pd.DataFrame = yf.download(tickers, **kwargs)
    time.sleep(_THROTTLE_SECS)
    return result


# ─── yf.Ticker セーフラッパー ──────────────────────────────────────────────────

@retry(
    retry=retry_if_exception(_is_rate_limit_error),
    wait=wait_exponential(multiplier=1, min=_WAIT_MIN_SECS, max=_WAIT_MAX_SECS),
    stop=stop_after_attempt(_MAX_ATTEMPTS),
    before_sleep=_log_retry,
    reraise=True,
)
def yf_ticker_safe(ticker: str) -> yf.Ticker:
    """
    yf.Ticker() の Rate Limit 対応ラッパー。

    Ticker オブジェクト生成自体はネットワーク呼び出しを発生させないが、
    `.info`, `.news` 等のプロパティアクセス時に呼び出される。
    このラッパーはオブジェクト生成のみを行い、プロパティは呼び出し元で取得する。
    """
    time.sleep(_THROTTLE_SECS)
    return yf.Ticker(ticker)
