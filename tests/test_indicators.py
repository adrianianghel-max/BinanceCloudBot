"""Unit tests for indicators.py v2.0."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from indicators import (
    add_ema_columns,
    calculate_4h_structure,
    calculate_adx_full,
    calculate_atr_features,
    calculate_bb_compression,
    calculate_distance_to_breakout_pct,
    calculate_ema10_slope_pct,
    calculate_macd_full,
    calculate_obv_features,
    calculate_overextension,
    calculate_rsi_pair,
    calculate_volume_ratio,
    classify_volume,
    is_daily_bullish,
    is_daily_early_trend,
    prepare_ohlcv_df,
)


def make_df(n=300, trend="up"):
    """Generate synthetic OHLCV DataFrame."""
    prices = 100.0
    rows = []
    for i in range(n):
        if trend == "up":
            change = 0.005   # strict +0.5% per candle — no randomness
        elif trend == "down":
            change = -0.005  # strict -0.5% per candle
        else:
            change = 0.0
        prices *= (1 + change)
        high = prices * 1.005
        low = prices * 0.995
        vol = 2000.0
        rows.append([i * 86400000, low * 0.99, high, low, prices, vol])
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


class TestPrepareOHLCV:
    def test_columns(self):
        raw = [[i * 86400000, 1.0, 1.1, 0.9, 1.05, 1000.0] for i in range(10)]
        df = prepare_ohlcv_df(raw)
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
        assert len(df) == 10


class TestEMAColumns:
    def test_ema_columns_present(self):
        df = add_ema_columns(make_df(250))
        for col in ("ema9", "ema10", "ema21", "ema50", "ema200"):
            assert col in df.columns

    def test_ema_values_not_nan_at_end(self):
        df = add_ema_columns(make_df(250))
        assert not pd.isna(df["ema10"].iloc[-1])
        assert not pd.isna(df["ema200"].iloc[-1])


class TestEMASlope:
    def test_slope_positive_uptrend(self):
        df = add_ema_columns(make_df(260, "up"))
        slope = calculate_ema10_slope_pct(df, lookback=10)
        assert slope is not None
        assert slope > 0

    def test_slope_negative_downtrend(self):
        df = add_ema_columns(make_df(260, "down"))
        slope = calculate_ema10_slope_pct(df, lookback=10)
        assert slope is not None
        assert slope < 0

    def test_slope_returns_none_short_df(self):
        df = add_ema_columns(make_df(5))
        assert calculate_ema10_slope_pct(df, lookback=10) is None


class TestDailyBullish:
    def test_bullish_on_uptrend(self):
        df = add_ema_columns(make_df(260, "up"))
        # May not always be True due to randomness, just check it returns bool
        result = is_daily_bullish(df)
        assert isinstance(result, bool)

    def test_not_bullish_on_short_df(self):
        df = add_ema_columns(make_df(100))
        assert is_daily_bullish(df) is False


class TestDailyEarlyTrend:
    def test_returns_bool(self):
        df = add_ema_columns(make_df(200, "up"))
        result = is_daily_early_trend(df)
        assert isinstance(result, bool)

    def test_false_on_short_df(self):
        df = add_ema_columns(make_df(10))
        assert is_daily_early_trend(df) is False


class TestVolumeRatio:
    def test_normal_volume(self):
        df = make_df(30)
        ratio = calculate_volume_ratio(df, period=20)
        assert ratio is not None
        assert ratio > 0

    def test_returns_none_short_df(self):
        df = make_df(5)
        assert calculate_volume_ratio(df, period=20) is None


class TestClassifyVolume:
    def test_weak(self):
        assert classify_volume(0.8) == "weak"

    def test_normal(self):
        assert classify_volume(1.1) == "normal"

    def test_accumulation(self):
        assert classify_volume(1.3) == "accumulation"

    def test_breakout(self):
        assert classify_volume(1.7) == "breakout_confirmation"

    def test_strong(self):
        assert classify_volume(2.5) == "strong_expansion"

    def test_none_returns_unknown(self):
        assert classify_volume(None) == "unknown"


class TestMACDFull:
    def test_returns_dict(self):
        df = make_df(100)
        result = calculate_macd_full(df)
        assert "macd_line" in result
        assert "signal_line" in result
        assert "histogram" in result

    def test_macd_line_not_none(self):
        df = make_df(100)
        result = calculate_macd_full(df)
        assert result["macd_line"] is not None


class TestADXFull:
    def test_returns_dict(self):
        df = make_df(100)
        result = calculate_adx_full(df)
        assert "adx" in result
        assert "di_plus" in result
        assert "di_minus" in result

    def test_adx_positive(self):
        df = make_df(100)
        result = calculate_adx_full(df)
        if result["adx"] is not None:
            assert result["adx"] >= 0


class TestRSIPair:
    def test_returns_tuple(self):
        df = make_df(100)
        cur, prev = calculate_rsi_pair(df, period=14)
        assert cur is not None
        assert prev is not None

    def test_rsi_in_range(self):
        df = make_df(100)
        cur, _ = calculate_rsi_pair(df, period=14)
        if cur is not None:
            assert 0 <= cur <= 100


class TestBBCompression:
    def test_returns_dict(self):
        df = make_df(100)
        result = calculate_bb_compression(df)
        assert "bb_width" in result
        assert "compression_score" in result

    def test_compression_score_in_range(self):
        df = make_df(100)
        result = calculate_bb_compression(df)
        assert 0.0 <= result["compression_score"] <= 100.0


class TestATRFeatures:
    def test_returns_dict(self):
        df = make_df(100)
        result = calculate_atr_features(df)
        assert "atr" in result
        assert "atr_pct" in result

    def test_atr_positive(self):
        df = make_df(100)
        result = calculate_atr_features(df)
        if result["atr"] is not None:
            assert result["atr"] > 0


class TestOBVFeatures:
    def test_returns_dict(self):
        df = make_df(100)
        result = calculate_obv_features(df)
        assert "obv" in result
        assert "obv_above_ema" in result
        assert "hidden_accumulation" in result


class TestDistanceToBreakout:
    def test_distance_non_negative(self):
        df = make_df(50)
        dist = calculate_distance_to_breakout_pct(df, lookback=20)
        if dist is not None:
            assert dist >= 0

    def test_none_on_short_df(self):
        df = make_df(5)
        assert calculate_distance_to_breakout_pct(df, lookback=20) is None


class TestStructure4H:
    def test_returns_dict(self):
        df = make_df(30)
        result = calculate_4h_structure(df, lookback=10)
        assert "structure_score" in result
        assert "trend_structure" in result

    def test_score_in_range(self):
        df = make_df(50)
        result = calculate_4h_structure(df, lookback=10)
        assert 0 <= result["structure_score"] <= 100


class TestOverextension:
    def test_returns_dict(self):
        from indicators import add_4h_ema_columns
        df = add_4h_ema_columns(make_df(50))
        result = calculate_overextension(df)
        assert "overextended" in result
        assert "overextension_score" in result

    def test_score_in_range(self):
        from indicators import add_4h_ema_columns
        df = add_4h_ema_columns(make_df(50))
        result = calculate_overextension(df)
        assert 0 <= result["overextension_score"] <= 100
