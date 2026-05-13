"""tests/test_dip_detector.py — detect_dip_entries & 15分キャッシュのユニットテスト

yfinance は一切呼び出さない。yf.download をモックで置き換えてロジックを検証する。
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from skills.screener import (
    _INTRADAY_CACHE_TTL_MINUTES,
    _intraday_cache_key,
    detect_dip_entries,
)

pytestmark = pytest.mark.unit


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────

def _make_ohlcv_df(open_price: float, close_price: float, n: int = 5) -> pd.DataFrame:
    """単純な OHLCV DataFrame を生成（始値 open_price、終値 close_price）。"""
    closes  = [open_price] * (n - 1) + [close_price]
    opens   = [open_price] * n
    return pd.DataFrame({
        "Open":   opens,
        "High":   [max(o, c) * 1.001 for o, c in zip(opens, closes)],
        "Low":    [min(o, c) * 0.999 for o, c in zip(opens, closes)],
        "Close":  closes,
        "Volume": [100_000.0] * n,
    })


def _make_multi_index_raw(tickers: list[str], open_price: float, close_price: float, n: int = 5) -> pd.DataFrame:
    """複数ティッカーの MultiIndex DataFrame を生成する。"""
    frames = {}
    for ticker in tickers:
        frames[ticker] = _make_ohlcv_df(open_price, close_price, n)
    combined = pd.concat(frames, axis=1)
    combined.columns.names = [None, None]
    return combined


# ──────────────────────────────────────────────
# _intraday_cache_key
# ──────────────────────────────────────────────

class TestIntradayCacheKey:
    def test_same_15min_window_returns_same_key(self):
        dt1 = datetime.datetime(2026, 5, 13, 10, 0)
        dt2 = datetime.datetime(2026, 5, 13, 10, 14)
        assert _intraday_cache_key(dt1) == _intraday_cache_key(dt2)

    def test_different_15min_window_returns_different_key(self):
        dt1 = datetime.datetime(2026, 5, 13, 10, 0)
        dt2 = datetime.datetime(2026, 5, 13, 10, 15)
        assert _intraday_cache_key(dt1) != _intraday_cache_key(dt2)

    def test_slot_snaps_to_15min_boundary(self):
        dt = datetime.datetime(2026, 5, 13, 10, 22)
        key = _intraday_cache_key(dt)
        assert key.endswith("_10_15")

    def test_first_slot_of_hour(self):
        dt = datetime.datetime(2026, 5, 13, 9, 0)
        key = _intraday_cache_key(dt)
        assert key.endswith("_09_00")

    def test_last_slot_of_hour(self):
        dt = datetime.datetime(2026, 5, 13, 9, 59)
        key = _intraday_cache_key(dt)
        assert key.endswith("_09_45")

    def test_ttl_constant_is_15(self):
        assert _INTRADAY_CACHE_TTL_MINUTES == 15

    def test_key_contains_date(self):
        dt = datetime.datetime(2026, 5, 13, 10, 0)
        assert "2026-05-13" in _intraday_cache_key(dt)


# ──────────────────────────────────────────────
# detect_dip_entries
# ──────────────────────────────────────────────

class TestDetectDipEntries:
    def test_empty_watchlist_returns_empty(self):
        result = detect_dip_entries([])
        assert result == []

    def test_detects_dip_below_threshold(self):
        """始値 100 → 終値 96 (-4%) は -3% 閾値を超える → 検出される。"""
        df = _make_ohlcv_df(open_price=100.0, close_price=96.0)
        with patch("yfinance.download", return_value=df):
            result = detect_dip_entries(["AAPL"], dip_threshold_pct=-3.0)
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["momentum_pct"] == pytest.approx(-4.0, abs=0.1)

    def test_no_dip_above_threshold_returns_empty(self):
        """始値 100 → 終値 98 (-2%) は -3% 閾値未満 → 検出されない。"""
        df = _make_ohlcv_df(open_price=100.0, close_price=98.0)
        with patch("yfinance.download", return_value=df):
            result = detect_dip_entries(["AAPL"], dip_threshold_pct=-3.0)
        assert result == []

    def test_exact_threshold_is_detected(self):
        """始値 100 → 終値 97 (-3%) は境界値 → 検出される。"""
        df = _make_ohlcv_df(open_price=100.0, close_price=97.0)
        with patch("yfinance.download", return_value=df):
            result = detect_dip_entries(["AAPL"], dip_threshold_pct=-3.0)
        assert len(result) == 1

    def test_result_contains_required_keys(self):
        df = _make_ohlcv_df(open_price=100.0, close_price=95.0)
        with patch("yfinance.download", return_value=df):
            result = detect_dip_entries(["AAPL"], dip_threshold_pct=-3.0)
        assert len(result) == 1
        for key in ("ticker", "momentum_pct", "current_price", "day_open"):
            assert key in result[0], f"キーが欠落: {key}"

    def test_results_sorted_by_magnitude(self):
        """複数銘柄で最も急落したものが先頭にくる。"""
        raw = _make_multi_index_raw(["AAPL", "TSLA"], open_price=100.0, close_price=100.0)
        # AAPL を -5%, TSLA を -4% に上書き
        raw[("AAPL", "Close")] = [100.0] * 4 + [95.0]
        raw[("TSLA", "Close")] = [100.0] * 4 + [96.0]

        with patch("yfinance.download", return_value=raw):
            result = detect_dip_entries(["AAPL", "TSLA"], dip_threshold_pct=-3.0)

        assert len(result) == 2
        assert result[0]["ticker"] == "AAPL"  # より急落
        assert result[0]["momentum_pct"] < result[1]["momentum_pct"]

    def test_network_error_returns_empty(self):
        """yfinance がエラーを投げても空リストを返す（例外を伝播させない）。"""
        with patch("yfinance.download", side_effect=RuntimeError("network error")):
            result = detect_dip_entries(["AAPL"])
        assert result == []

    def test_empty_download_returns_empty(self):
        """ダウンロードデータが空の場合も空リストを返す。"""
        with patch("yfinance.download", return_value=pd.DataFrame()):
            result = detect_dip_entries(["AAPL"])
        assert result == []

    def test_positive_momentum_not_detected(self):
        """値上がり銘柄は検出されない。"""
        df = _make_ohlcv_df(open_price=100.0, close_price=105.0)
        with patch("yfinance.download", return_value=df):
            result = detect_dip_entries(["AAPL"], dip_threshold_pct=-3.0)
        assert result == []

    def test_current_price_equals_last_close(self):
        df = _make_ohlcv_df(open_price=100.0, close_price=94.0, n=5)
        with patch("yfinance.download", return_value=df):
            result = detect_dip_entries(["AAPL"], dip_threshold_pct=-3.0)
        assert len(result) == 1
        assert result[0]["current_price"] == pytest.approx(94.0, abs=0.01)

    def test_day_open_equals_first_open(self):
        df = _make_ohlcv_df(open_price=100.0, close_price=94.0, n=5)
        with patch("yfinance.download", return_value=df):
            result = detect_dip_entries(["AAPL"], dip_threshold_pct=-3.0)
        assert result[0]["day_open"] == pytest.approx(100.0, abs=0.01)

    def test_too_short_df_skipped(self):
        """ローソク足が1本しかない銘柄はスキップされる。"""
        df = _make_ohlcv_df(open_price=100.0, close_price=90.0, n=1)
        with patch("yfinance.download", return_value=df):
            result = detect_dip_entries(["AAPL"], dip_threshold_pct=-3.0)
        assert result == []
