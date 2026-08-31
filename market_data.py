"""Market data module — Level 2.

Provides deterministic fetch helpers with retry and staleness checking.
Reuses existing retry/connection logic from scanner.py where possible.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import ccxt
import pandas as pd

import config
from indicators import prepare_ohlcv_df

logger = logging.getLogger(__name__)


def with_retries(func, *args, **kwargs):
    """Retry a callable using config.MAX_RETRIES / INITIAL_RETRY_DELAY."""
    delay = config.INITIAL_RETRY_DELAY
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except (ccxt.RateLimitExceeded, ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
            if attempt == config.MAX_RETRIES:
                raise
            logger.warning(
                "Retryable error on attempt %s/%s: %s. Retrying in %.1fs.",
                attempt,
                config.MAX_RETRIES,
                exc,
                delay,
            )
            time.sleep(delay)
            delay *= 2


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    limit: int,
    check_staleness: bool = True,
) -> Optional[pd.DataFrame]:
    """Fetch OHLCV candles and optionally verify they are not stale.

    Returns a prepared DataFrame (timestamp/OHLCV columns) or None
    if data is missing/stale (staleness check enabled).
    In all cases NO TRADE should be attempted on None/empty data.
    """
    raw = with_retries(exchange.fetch_ohlcv, symbol, timeframe, limit=limit)
    if not raw:
        logger.warning("No OHLCV data for %s %s.", symbol, timeframe)
        return None

    df = prepare_ohlcv_df(raw)

    if check_staleness and not is_data_fresh(df, timeframe):
        logger.warning(
            "Stale data for %s %s (last candle %s). NO TRADE.",
            symbol,
            timeframe,
            df["timestamp"].iloc[-1],
        )
        return None

    return df


def is_data_fresh(df: pd.DataFrame, timeframe: str) -> bool:
    """Return True if the last candle is within the staleness threshold.

    Thresholds come from config.STALE_DATA_MAX_AGE_SECONDS.
    If no threshold is configured for the timeframe, data is considered fresh.
    """
    if df is None or df.empty:
        return False

    max_age_seconds = config.STALE_DATA_MAX_AGE_SECONDS.get(timeframe)
    if max_age_seconds is None:
        # No configured threshold → do not block on staleness.
        return True

    last_ts = df["timestamp"].iloc[-1]
    if not hasattr(last_ts, "timestamp"):
        return False

    now_ts = pd.Timestamp.utcnow()
    age_seconds = (now_ts - last_ts).total_seconds()
    return age_seconds <= max_age_seconds


def fetch_ticker_safe(exchange: ccxt.Exchange, symbol: str) -> Optional[float]:
    """Fetch latest trade price with retries. Returns None on failure."""
    try:
        ticker = with_retries(exchange.fetch_ticker, symbol)
        price = float(ticker.get("last"))
        return price if price > 0 else None
    except (ccxt.BaseError, ValueError, TypeError):
        logger.exception("Could not fetch ticker for %s.", symbol)
        return None


def is_market_data_unavailable(value: Any) -> bool:
    """Detect the explicit 'unavailable' marker used for missing historical data."""
    return value == config.MARKET_DATA_UNAVAILABLE_MARK