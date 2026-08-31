"""Unit tests for market_data.py — Level 2."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import ccxt
import pandas as pd

import config
import market_data
from market_data import (
    fetch_ohlcv,
    fetch_ticker_safe,
    is_data_fresh,
    is_market_data_unavailable,
    with_retries,
)


class TestWithRetries(unittest.TestCase):
    def test_retries_then_success(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ccxt.RateLimitExceeded("rate limited")
            return "ok"

        with patch.object(
            market_data.config, "MAX_RETRIES", 4
        ), patch.object(market_data.config, "INITIAL_RETRY_DELAY", 0.0):
            result = with_retries(flaky)
            self.assertEqual(result, "ok")
            self.assertEqual(calls["n"], 3)

    def test_retries_exhausted(self):
        # Test that a non-ccxt exception is not retried (propagates immediately)
        def boom():
            raise RuntimeError("boom")

        with patch.object(market_data.config, "MAX_RETRIES", 3):
            with self.assertRaises(RuntimeError):
                with_retries(boom)


class TestIsDataFresh(unittest.TestCase):
    def test_empty_df(self):
        self.assertFalse(is_data_fresh(None, "1h"))
        self.assertFalse(is_data_fresh(pd.DataFrame(), "1h"))

    def test_fresh_data(self):
        ts = pd.Timestamp.utcnow() - pd.Timedelta(minutes=5)
        df = pd.DataFrame(
            {
                "timestamp": [ts],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000.0],
            }
        )
        self.assertTrue(is_data_fresh(df, "5m"))

    def test_stale_data(self):
        ts = pd.Timestamp.utcnow() - pd.Timedelta(hours=48)
        df = pd.DataFrame(
            {
                "timestamp": [ts],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000.0],
            }
        )
        self.assertFalse(is_data_fresh(df, "5m"))

    def test_unconfigured_timeframe(self):
        ts = pd.Timestamp.utcnow() - pd.Timedelta(days=365)
        df = pd.DataFrame(
            {
                "timestamp": [ts],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000.0],
            }
        )
        # "1w" has no threshold → considered fresh (no staleness block)
        self.assertTrue(is_data_fresh(df, "1w"))


class TestFetchOHLCV(unittest.TestCase):
    def test_fetch_ohlcv_returns_none_on_empty(self):
        exchange = MagicMock()
        exchange.fetch_ohlcv.return_value = []
        result = fetch_ohlcv(exchange, "DOGE/USDC", "1h", limit=100)
        self.assertIsNone(result)

    def test_fetch_ohlcv_returns_df(self):
        exchange = MagicMock()
        now_ms = int(pd.Timestamp.utcnow().timestamp() * 1000) - 60_000
        raw = [
            [now_ms, 100.0, 101.0, 99.0, 100.5, 1000.0],
        ]
        exchange.fetch_ohlcv.return_value = raw
        df = fetch_ohlcv(exchange, "DOGE/USDC", "5m", limit=100, check_staleness=False)
        self.assertIsNotNone(df)
        self.assertIn("timestamp", df.columns)

    def test_fetch_ohlcv_marks_stale(self):
        exchange = MagicMock()
        stale_ms = int(
            (pd.Timestamp.utcnow() - pd.Timedelta(hours=48)).timestamp() * 1000
        )
        raw = [
            [stale_ms, 100.0, 101.0, 99.0, 100.5, 1000.0],
        ]
        exchange.fetch_ohlcv.return_value = raw
        df = fetch_ohlcv(exchange, "DOGE/USDC", "5m", limit=100, check_staleness=True)
        self.assertIsNone(df)  # stale → NO TRADE


class TestFetchTickerSafe(unittest.TestCase):
    def test_ticker_success(self):
        exchange = MagicMock()
        exchange.fetch_ticker.return_value = {"last": 0.123}
        price = fetch_ticker_safe(exchange, "DOGE/USDC")
        self.assertEqual(price, 0.123)

    def test_ticker_failure(self):
        exchange = MagicMock()
        exchange.fetch_ticker.side_effect = ccxt.ExchangeError("down")
        price = fetch_ticker_safe(exchange, "DOGE/USDC")
        self.assertIsNone(price)


class TestUnavailableMarker(unittest.TestCase):
    def test_marker(self):
        self.assertTrue(is_market_data_unavailable("unavailable"))
        self.assertFalse(is_market_data_unavailable(0))
        self.assertFalse(is_market_data_unavailable(None))


if __name__ == "__main__":
    unittest.main()