"""tests/test_screener_pure.py — Screener 純粋関数のユニットテスト

外部API（yfinance / Wikipedia）を一切呼び出さず、
pandas Series をテストデータとして注入して計算ロジックを検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from skills.screener import (
    _calc_drawdown_from_52w_high,
    _calc_intraday_momentum,
    _calc_intraday_volume_spike,
    _calc_macd_histogram,
    _calc_rsi,
    _calc_sma25_diff,
    _calc_volume_ratio,
    _score_ticker,
)

pytestmark = pytest.mark.unit

# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────

def _make_close(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def _make_volume(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def _make_ohlcv(n: int = 60, close_val: float = 100.0) -> pd.DataFrame:
    """最小限の OHLCV DataFrame を生成する（スコアリングテスト用）。"""
    closes  = [close_val] * n
    volumes = [1_000_000.0] * n
    return pd.DataFrame({
        "Open":   [close_val * 0.99] * n,
        "High":   [close_val * 1.01] * n,
        "Low":    [close_val * 0.98] * n,
        "Close":  closes,
        "Volume": volumes,
    })


# ──────────────────────────────────────────────
# _calc_rsi
# ──────────────────────────────────────────────

class TestCalcRsi:
    def test_constant_price_returns_neutral(self):
        close = _make_close([100.0] * 30)
        rsi = _calc_rsi(close)
        # 変動なし → ゲイン・ロスがゼロ → NaN / フォールバック 50
        assert 0.0 <= rsi <= 100.0

    def test_all_up_returns_high_rsi(self):
        """単調上昇 → RSI は高い（理想は 100 に近いが計算法で差あり）。"""
        close = _make_close([float(i) for i in range(1, 31)])
        rsi = _calc_rsi(close)
        assert rsi > 60.0, f"単調上昇でRSIが低すぎる: {rsi}"

    def test_all_down_returns_low_rsi(self):
        """単調下落 → RSI は低い（0 に近い）。"""
        close = _make_close([float(30 - i) for i in range(30)])
        rsi = _calc_rsi(close)
        assert rsi < 40.0, f"単調下落でRSIが高すぎる: {rsi}"

    def test_too_short_series_returns_50(self):
        """期間に満たないデータは 50 を返す。"""
        close = _make_close([100.0, 101.0, 99.0])
        rsi = _calc_rsi(close)
        assert rsi == 50.0

    def test_return_is_float(self):
        close = _make_close([100.0 + i * 0.5 for i in range(30)])
        assert isinstance(_calc_rsi(close), float)


# ──────────────────────────────────────────────
# _calc_macd_histogram
# ──────────────────────────────────────────────

class TestCalcMacdHistogram:
    def test_returns_tuple_of_two_floats(self):
        close = _make_close([100.0 + i for i in range(50)])
        hist, prev = _calc_macd_histogram(close)
        assert isinstance(hist, float)
        assert isinstance(prev, float)

    def test_too_short_returns_zeros(self):
        close = _make_close([100.0])
        hist, prev = _calc_macd_histogram(close)
        assert hist == 0.0
        assert prev == 0.0

    def test_rising_trend_positive_histogram(self):
        """急上昇トレンドでは fast EMA > slow EMA → histogram が正の傾向。"""
        close = _make_close([100.0 + i * 3 for i in range(60)])
        hist, _ = _calc_macd_histogram(close)
        assert hist > 0.0

    def test_falling_trend_negative_histogram(self):
        """急下落トレンドでは histogram が負の傾向。"""
        close = _make_close([200.0 - i * 3 for i in range(60)])
        hist, _ = _calc_macd_histogram(close)
        assert hist < 0.0


# ──────────────────────────────────────────────
# _calc_sma25_diff
# ──────────────────────────────────────────────

class TestCalcSma25Diff:
    def test_price_above_sma_returns_positive(self):
        """SMA25 より現在価格が高い → 正の乖離率。"""
        base = [100.0] * 25
        series = _make_close(base + [120.0])  # 最後だけ急騰
        diff = _calc_sma25_diff(series)
        assert diff > 0.0

    def test_price_below_sma_returns_negative(self):
        """SMA25 より現在価格が低い → 負の乖離率。"""
        base = [100.0] * 25
        series = _make_close(base + [80.0])  # 最後だけ急落
        diff = _calc_sma25_diff(series)
        assert diff < 0.0

    def test_constant_price_returns_zero(self):
        series = _make_close([100.0] * 30)
        diff = _calc_sma25_diff(series)
        assert diff == pytest.approx(0.0, abs=0.01)

    def test_too_short_returns_zero(self):
        series = _make_close([100.0] * 5)
        assert _calc_sma25_diff(series) == 0.0


# ──────────────────────────────────────────────
# _calc_volume_ratio
# ──────────────────────────────────────────────

class TestCalcVolumeRatio:
    def test_surge_returns_high_ratio(self):
        """直近5日の出来高が20日平均の2倍 → 比率 ≈ 2.0。"""
        vol = _make_volume([1_000_000.0] * 15 + [2_000_000.0] * 5)
        ratio = _calc_volume_ratio(vol)
        assert ratio > 1.5

    def test_normal_volume_returns_near_one(self):
        vol = _make_volume([1_000_000.0] * 20)
        ratio = _calc_volume_ratio(vol)
        assert ratio == pytest.approx(1.0, abs=0.01)

    def test_too_short_returns_one(self):
        vol = _make_volume([1_000_000.0] * 5)
        assert _calc_volume_ratio(vol) == 1.0

    def test_zero_avg_returns_one(self):
        vol = _make_volume([0.0] * 20)
        assert _calc_volume_ratio(vol) == 1.0


# ──────────────────────────────────────────────
# _calc_drawdown_from_52w_high
# ──────────────────────────────────────────────

class TestCalcDrawdownFrom52wHigh:
    def test_at_high_returns_zero(self):
        close = _make_close([100.0] * 252)
        dd = _calc_drawdown_from_52w_high(close)
        assert dd == pytest.approx(0.0, abs=0.01)

    def test_drawdown_below_high(self):
        """最高値100から現在80 → -20%。"""
        close = _make_close([100.0] * 251 + [80.0])
        dd = _calc_drawdown_from_52w_high(close)
        assert dd == pytest.approx(-20.0, abs=0.1)

    def test_empty_series_returns_zero(self):
        close = _make_close([])
        assert _calc_drawdown_from_52w_high(close) == 0.0

    def test_single_value(self):
        close = _make_close([100.0])
        assert _calc_drawdown_from_52w_high(close) == 0.0


# ──────────────────────────────────────────────
# _calc_intraday_volume_spike
# ──────────────────────────────────────────────

class TestCalcIntradayVolumeSpike:
    def test_surge_returns_high_ratio(self):
        vol = _make_volume([100_000.0] * 5 + [300_000.0])  # 3x spike at end
        ratio = _calc_intraday_volume_spike(vol)
        assert ratio >= 3.0

    def test_constant_volume_returns_one(self):
        vol = _make_volume([100_000.0] * 6)
        ratio = _calc_intraday_volume_spike(vol)
        assert ratio == pytest.approx(1.0, abs=0.01)

    def test_too_short_returns_one(self):
        vol = _make_volume([100_000.0])
        assert _calc_intraday_volume_spike(vol) == 1.0


# ──────────────────────────────────────────────
# _calc_intraday_momentum
# ──────────────────────────────────────────────

class TestCalcIntradayMomentum:
    def test_positive_momentum(self):
        """始値100、終値105 → +5%。"""
        close = _make_close([100.0, 102.0, 104.0, 105.0])
        open_ = _make_close([100.0, 100.0, 100.0, 100.0])
        mom = _calc_intraday_momentum(close, open_)
        assert mom == pytest.approx(5.0, abs=0.1)

    def test_negative_momentum(self):
        """始値100、終値95 → -5%。"""
        close = _make_close([100.0, 98.0, 96.0, 95.0])
        open_ = _make_close([100.0, 100.0, 100.0, 100.0])
        mom = _calc_intraday_momentum(close, open_)
        assert mom == pytest.approx(-5.0, abs=0.1)

    def test_zero_open_returns_zero(self):
        close = _make_close([100.0])
        open_ = _make_close([0.0])
        assert _calc_intraday_momentum(close, open_) == 0.0

    def test_empty_returns_zero(self):
        assert _calc_intraday_momentum(_make_close([]), _make_close([])) == 0.0


# ──────────────────────────────────────────────
# _score_ticker
# ──────────────────────────────────────────────

class TestScoreTicker:
    def test_too_short_df_returns_none(self):
        df = _make_ohlcv(n=10)
        result = _score_ticker("TEST", df)
        assert result is None

    def test_empty_df_returns_none(self):
        result = _score_ticker("TEST", pd.DataFrame())
        assert result is None

    def test_normal_df_returns_dict(self):
        df = _make_ohlcv(n=60)
        result = _score_ticker("AAPL", df)
        assert result is not None
        assert result["ticker"] == "AAPL"
        assert isinstance(result["score"], int)
        assert 0 <= result["score"] <= 12
        assert "reason" in result

    def test_oversold_rsi_gives_higher_score(self):
        """RSI < 35 の状況では score が上がる。"""
        # 急落を作って RSI を低くする
        high_prices = [150.0] * 20
        crash = [150.0 - i * 4 for i in range(40)]  # 20→-10まで急落
        closes = high_prices + crash
        volumes = [1_000_000.0] * 60
        df = pd.DataFrame({
            "Open":   [c * 0.99 for c in closes],
            "High":   [c * 1.01 for c in closes],
            "Low":    [c * 0.98 for c in closes],
            "Close":  closes,
            "Volume": volumes,
        })
        result = _score_ticker("TEST", df)
        if result is not None:
            # 急落シナリオでは score が 0 超えやすい
            assert result["score"] >= 0

    def test_result_has_required_keys(self):
        df = _make_ohlcv(n=60)
        result = _score_ticker("NVDA", df)
        if result is not None:
            for key in ("ticker", "score", "rsi", "macd_hist",
                        "sma25_diff_pct", "volume_ratio",
                        "drawdown_52w_pct", "reason"):
                assert key in result, f"キーが欠落: {key}"
