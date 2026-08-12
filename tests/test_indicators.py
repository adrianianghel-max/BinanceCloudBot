"""Unit tests for indicators.py — Level 1."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from indicators import (
    add_ema_columns,
    calculate_adx_di,
    calculate_adx_value,
    calculate_atr,
    calculate_bollinger_bandwidth,
    calculate_bollinger_squeeze,
    calculate_distance_to_breakout_pct,
    calculate_ema10_slope_pct,
    calculate_growth_score,
    calculate_lrc,
    calculate_lrs,
    calculate_macd_full,
    calculate_macd_histogram_slope,
    calculate_macd_values,
    calculate_obv,
    calculate_obv_rising,
    calculate_overextension,
    calculate_price_acceleration,
    calculate_remaining_potential,
    calculate_rsi_pair,
    calculate_stoch_rsi,
    calculate_volume_acceleration,
    calculate_volume_ratio,
    is_4h_breakout,
    is_daily_bullish,
    is_daily_early_trend,
    prepare_ohlcv_df,
)


def _make_synthetic_df(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame with deterministic data."""
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range(
        start="2024-01-01", periods=n, freq="5min", tz="UTC"
    )
    # Brownian-ish close price starting at 100
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, size=n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + rng.uniform(0.0, 0.5, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.0, 0.5, size=n)
    volume = rng.uniform(1000.0, 5000.0, size=n)
    # Ensure volume has some trend in the last bars for acceleration tests
    volume[-10:] *= 1.5

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


class TestPrepareOHLCV(unittest.TestCase):
    def test_prepare_ohlcv_df(self):
        raw = [
            [1704067200000, 100.0, 101.0, 99.0, 100.5, 1000.0],
            [1704067500000, 100.5, 102.0, 100.0, 101.0, 1200.0],
        ]
        df = prepare_ohlcv_df(raw)
        self.assertEqual(len(df), 2)
        self.assertListEqual(
            list(df.columns),
            ["timestamp", "open", "high", "low", "close", "volume"],
        )
        self.assertTrue(pd.api.types.is_datetime64tz_dtype(df["timestamp"]))


class TestEMA(unittest.TestCase):
    def test_add_ema_columns(self):
        df = _make_synthetic_df(n=250)
        out = add_ema_columns(df)
        for col in ("ema10", "ema50", "ema200"):
            self.assertIn(col, out.columns)

    def test_ema10_slope_pct(self):
        df = _make_synthetic_df(n=100)
        df = add_ema_columns(df)
        slope = calculate_ema10_slope_pct(df, lookback=10)
        self.assertIsNotNone(slope)
        self.assertIsInstance(slope, float)

    def test_ema10_slope_pct_short_df(self):
        df = _make_synthetic_df(n=5)
        df = add_ema_columns(df)
        self.assertIsNone(calculate_ema10_slope_pct(df, lookback=10))


class TestDaily(unittest.TestCase):
    def test_is_daily_bullish_short(self):
        df = _make_synthetic_df(n=50)
        df = add_ema_columns(df)
        self.assertFalse(is_daily_bullish(df))

    def test_is_daily_early_trend_short(self):
        df = _make_synthetic_df(n=10)
        df = add_ema_columns(df)
        self.assertFalse(is_daily_early_trend(df))


class TestBreakout(unittest.TestCase):
    def test_4h_breakout_short(self):
        df = _make_synthetic_df(n=10)
        self.assertFalse(is_4h_breakout(df, lookback=20))

    def test_distance_to_breakout(self):
        df = _make_synthetic_df(n=50)
        dist = calculate_distance_to_breakout_pct(df, lookback=20)
        self.assertIsNotNone(dist)
        self.assertIsInstance(dist, float)


class TestADX(unittest.TestCase):
    def test_adx_value(self):
        df = _make_synthetic_df(n=100)
        adx = calculate_adx_value(df, period=14)
        self.assertIsNotNone(adx)
        self.assertIsInstance(adx, float)

    def test_adx_di(self):
        df = _make_synthetic_df(n=100)
        result = calculate_adx_di(df, period=14)
        self.assertIn("adx", result)
        self.assertIn("di_plus", result)
        self.assertIn("di_minus", result)

    def test_adx_di_short(self):
        df = _make_synthetic_df(n=5)
        result = calculate_adx_di(df, period=14)
        self.assertEqual(result["adx"], None)


class TestMACD(unittest.TestCase):
    def test_macd_values(self):
        df = _make_synthetic_df(n=100)
        macd_line, signal_line = calculate_macd_values(df)
        self.assertIsNotNone(macd_line)
        self.assertIsNotNone(signal_line)

    def test_macd_values_short(self):
        df = _make_synthetic_df(n=10)
        macd_line, signal_line = calculate_macd_values(df)
        # Not enough data for MACD to produce non-NaN values
        self.assertTrue(macd_line is None or isinstance(macd_line, float))

    def test_macd_full(self):
        df = _make_synthetic_df(n=100)
        result = calculate_macd_full(df)
        for key in ("macd", "signal", "histogram", "slope"):
            self.assertIn(key, result)

    def test_macd_histogram_slope(self):
        df = _make_synthetic_df(n=100)
        slope = calculate_macd_histogram_slope(df, lookback=5)
        self.assertIsInstance(slope, float)


class TestStochRSI(unittest.TestCase):
    def test_stoch_rsi(self):
        df = _make_synthetic_df(n=100)
        result = calculate_stoch_rsi(df)
        self.assertIn("k", result)
        self.assertIn("d", result)
        self.assertIn("golden_cross", result)
        self.assertIn("positive_zone", result)
        # golden_cross / positive_zone must be booleans
        self.assertIsInstance(result["golden_cross"], bool)
        self.assertIsInstance(result["positive_zone"], bool)

    def test_stoch_rsi_short(self):
        df = _make_synthetic_df(n=5)
        result = calculate_stoch_rsi(df)
        self.assertFalse(result["golden_cross"])


class TestOBV(unittest.TestCase):
    def test_obv(self):
        df = _make_synthetic_df(n=100)
        obv = calculate_obv(df)
        self.assertIsNotNone(obv)
        self.assertIsInstance(obv, float)

    def test_obv_rising(self):
        df = _make_synthetic_df(n=100)
        result = calculate_obv_rising(df, lookback=5)
        self.assertIsInstance(result, bool)


class TestBollinger(unittest.TestCase):
    def test_bandwidth(self):
        df = _make_synthetic_df(n=100)
        bw = calculate_bollinger_bandwidth(df, length=20, std=2.0)
        self.assertIsNotNone(bw)
        self.assertGreater(bw, 0.0)

    def test_squeeze(self):
        df = _make_synthetic_df(n=100)
        result = calculate_bollinger_squeeze(df, length=20, std=2.0, lookback=20)
        self.assertIsInstance(result, bool)


class TestATR(unittest.TestCase):
    def test_atr(self):
        df = _make_synthetic_df(n=100)
        atr = calculate_atr(df, period=14)
        self.assertIsNotNone(atr)
        self.assertGreater(atr, 0.0)


class TestRegression(unittest.TestCase):
    def test_lrs(self):
        df = _make_synthetic_df(n=100)
        lrs = calculate_lrs(df, lookback=20)
        self.assertIsNotNone(lrs)
        self.assertIsInstance(lrs, float)

    def test_lrc(self):
        df = _make_synthetic_df(n=155)
        lrc = calculate_lrc(df, lookback=150)
        self.assertIsNotNone(lrc)
        self.assertIsInstance(lrc, float)


class TestAcceleration(unittest.TestCase):
    def test_volume_acceleration(self):
        df = _make_synthetic_df(n=100)
        va = calculate_volume_acceleration(df, short_period=5, long_period=20)
        self.assertIsNotNone(va)
        self.assertGreater(va, 0.0)

    def test_price_acceleration(self):
        df = _make_synthetic_df(n=100)
        pa = calculate_price_acceleration(df, lookback=5)
        self.assertIsNotNone(pa)
        self.assertIsInstance(pa, float)


class TestVolumeRatio(unittest.TestCase):
    def test_volume_ratio(self):
        df = _make_synthetic_df(n=100)
        vr = calculate_volume_ratio(df, period=20)
        self.assertIsNotNone(vr)
        self.assertGreater(vr, 0.0)


class TestRSI(unittest.TestCase):
    def test_rsi_pair(self):
        df = _make_synthetic_df(n=100)
        current, previous = calculate_rsi_pair(df, period=14)
        self.assertIsNotNone(current)
        self.assertIsNotNone(previous)
        self.assertTrue(0.0 <= current <= 100.0)

    def test_rsi_pair_short(self):
        df = _make_synthetic_df(n=5)
        current, previous = calculate_rsi_pair(df, period=14)
        self.assertIsNone(current)
        self.assertIsNone(previous)


class TestRemainingPotential(unittest.TestCase):
    def test_remaining_potential(self):
        df = _make_synthetic_df(n=100)
        result = calculate_remaining_potential(df, atr_period=14, lookback=20)
        self.assertIn("low", result)
        self.assertIn("high", result)
        self.assertIn("confidence", result)
        self.assertIsNotNone(result["low"])
        self.assertIsNotNone(result["high"])
        self.assertIsNotNone(result["confidence"])

    def test_remaining_potential_short(self):
        df = _make_synthetic_df(n=5)
        result = calculate_remaining_potential(df)
        self.assertIsNone(result["low"])


class TestOverextension(unittest.TestCase):
    def test_overextension(self):
        df = _make_synthetic_df(n=100)
        over = calculate_overextension(df, lookback=20)
        self.assertIsNotNone(over)
        self.assertIsInstance(over, float)

    def test_overextension_short(self):
        df = _make_synthetic_df(n=5)
        self.assertIsNone(calculate_overextension(df, lookback=20))


class TestGrowthScore(unittest.TestCase):
    def test_growth_score_bounds(self):
        score = calculate_growth_score(
            ema10_slope_pct=0.5,
            macd_spread_ratio=0.03,
            volume_ratio=2.0,
            distance_to_breakout_pct=1.0,
            rsi_value=65.0,
            use_1h_filter=True,
        )
        self.assertTrue(0.0 <= score <= 100.0)

    def test_growth_score_max(self):
        score = calculate_growth_score(
            ema10_slope_pct=10.0,
            macd_spread_ratio=1.0,
            volume_ratio=10.0,
            distance_to_breakout_pct=0.0,
            rsi_value=72.0,
            use_1h_filter=True,
        )
        self.assertAlmostEqual(score, 100.0, places=2)


if __name__ == "__main__":
    unittest.main()