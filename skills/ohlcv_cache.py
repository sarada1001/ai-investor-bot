"""
skills/ohlcv_cache.py — OHLCV ローカルキャッシュ（SQLite）

バックテストやエージェントの逐次 yf.download() 呼び出しをキャッシュし、
API の無駄打ちと Rate Limit リスクを削減する。

キャッシュ仕様:
  - ストレージ: data/cache/ohlcv_cache.db（SQLite）
  - キー: "TICKER_STARTDATE_ENDDATE"（例: "AAPL_2026-02-01_2026-05-01"）
  - TTL: 取得時刻から 24 時間
  - データ: DataFrame を pickle でシリアライズして BLOB に保存

Usage:
    from skills.ohlcv_cache import get_ohlcv

    df = get_ohlcv("AAPL", start=datetime(2026,2,1), end=datetime(2026,5,1))
"""

from __future__ import annotations

import logging
import pickle
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from skills.api_guard import yf_download_safe

logger = logging.getLogger(__name__)

# ─── 設定 ─────────────────────────────────────────────────────────────────────
_CACHE_DIR   = Path("data/cache")
_DB_PATH     = _CACHE_DIR / "ohlcv_cache.db"
_TTL_HOURS   = 24
_BUFFER_DAYS = 60   # run_backtest.py の INDICATOR_BUFFER_DAYS に合わせる

# ─── DB 初期化 ─────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ohlcv "
        "(key TEXT PRIMARY KEY, fetched_at TEXT, data BLOB)"
    )
    conn.commit()
    return conn


# ─── キャッシュ操作 ────────────────────────────────────────────────────────────

def _cache_key(ticker: str, fetch_start: datetime, end: datetime) -> str:
    return f"{ticker}_{fetch_start.date()}_{end.date()}"


def _load(key: str) -> pd.DataFrame | None:
    """キャッシュを読み込む。TTL切れまたは未ヒット時は None を返す。"""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT fetched_at, data FROM ohlcv WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        if row is None:
            return None
        fetched_at = datetime.fromisoformat(row[0])
        if datetime.now() - fetched_at > timedelta(hours=_TTL_HOURS):
            logger.debug("[ohlcv_cache] TTL切れ: %s", key)
            return None
        df: pd.DataFrame = pickle.loads(row[1])
        logger.info("[ohlcv_cache] キャッシュヒット: %s", key)
        return df
    except Exception as e:
        logger.warning("[ohlcv_cache] 読み込みエラー (%s): %s", key, e)
        return None


def _save(key: str, df: pd.DataFrame) -> None:
    """DataFrame をキャッシュに保存する。"""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO ohlcv (key, fetched_at, data) VALUES (?, ?, ?)",
            (key, datetime.now().isoformat(), pickle.dumps(df)),
        )
        conn.commit()
        conn.close()
        logger.debug("[ohlcv_cache] 保存完了: %s", key)
    except Exception as e:
        logger.warning("[ohlcv_cache] 保存エラー (%s): %s", key, e)


# ─── 公開インターフェース ──────────────────────────────────────────────────────

def get_ohlcv(
    ticker: str,
    start: datetime,
    end: datetime,
    buffer_days: int = _BUFFER_DAYS,
) -> pd.DataFrame:
    """
    OHLCV を取得する（キャッシュ優先）。

    バッファ付きで取得する（テクニカル指標の計算に必要な先行データ）。

    Args:
        ticker:      銘柄ティッカー（例: "AAPL"）
        start:       シミュレーション開始日
        end:         シミュレーション終了日
        buffer_days: 指標計算バッファ日数（デフォルト 60）

    Returns:
        OHLCV DataFrame（インデックス: DatetimeIndex）

    Raises:
        ValueError: yfinance からデータを取得できない場合
    """
    fetch_start = start - timedelta(days=buffer_days)
    key = _cache_key(ticker, fetch_start, end)

    cached = _load(key)
    if cached is not None:
        return cached

    logger.info("[ohlcv_cache] フェッチ: %s (%s → %s)", ticker, fetch_start.date(), end.date())
    df = yf_download_safe(
        ticker,
        start=fetch_start.strftime("%Y-%m-%d"),
        end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=True,
    )

    if df is None or df.empty:
        raise ValueError(f"yfinance から {ticker} のデータを取得できませんでした。")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)

    _save(key, df)
    return df


def clear_expired() -> int:
    """TTL 切れエントリを削除し、削除件数を返す。"""
    try:
        cutoff = (datetime.now() - timedelta(hours=_TTL_HOURS)).isoformat()
        conn = _get_conn()
        cur = conn.execute("DELETE FROM ohlcv WHERE fetched_at < ?", (cutoff,))
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        if deleted:
            logger.info("[ohlcv_cache] TTL切れ %d 件を削除", deleted)
        return deleted
    except Exception as e:
        logger.warning("[ohlcv_cache] expired 削除エラー: %s", e)
        return 0


def cache_stats() -> dict:
    """キャッシュの統計情報を返す（デバッグ用）。"""
    try:
        conn = _get_conn()
        total = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        cutoff = (datetime.now() - timedelta(hours=_TTL_HOURS)).isoformat()
        alive = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE fetched_at >= ?", (cutoff,)
        ).fetchone()[0]
        conn.close()
        return {"total": total, "alive": alive, "expired": total - alive, "db": str(_DB_PATH)}
    except Exception as e:
        return {"error": str(e)}
