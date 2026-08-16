"""market_regime.py — Daily market regime classification and BTC global regime."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

import config
from indicators import (
    add_ema_columns,
    calculate_adx_full,
    calculate_macd_full,
    is_daily_bullish,
    is_daily_early_trend,
    prepare_ohlcv_df,
)

logger = logging.getLogger(__name__)

REGIME_BULLISH = "BULLISH"
REGIME_EARLY_BULLISH = "EARLY_BULLISH"
REGIME_NEUTRAL = "NEUTRAL"
REGIME_BEARISH = "BEARISH"


def classify_daily_regime(df: pd.DataFrame) -> str:
    """Classify 1D market regime for a symbol."""
    if len(df) < 60:
        return REGIME_NEUTRAL
    if is_daily_bullish(df):
        return REGIME_BULLISH
    if config.ALLOW_EARLY_TREND and is_daily_early_trend(df):
        return REGIME_EARLY_BULLISH
    last = df.iloc[-1]
    ema10 = last.get("ema10", float("nan"))
    ema50 = last.get("ema50", float("nan"))
    close = last.get("close", float("nan"))
    if pd.isna(ema10) or pd.isna(ema50) or pd.isna(close):
        return REGIME_NEUTRAL
    if close < ema50 and ema10 < ema50:
        return REGIME_BEARISH
    return REGIME_NEUTRAL


def get_btc_market_regime(exchange, btc_df_daily: pd.DataFrame | None = None) -> str:
    """Return global market regime based on BTC daily candles."""
    try:
        if btc_df_daily is None:
            raw = exchange.fetch_ohlcv(config.MARKET_REGIME_SYMBOL, "1d", limit=260)
            if not raw:
                return REGIME_NEUTRAL
            btc_df_daily = add_ema_columns(prepare_ohlcv_df(raw))
        regime = classify_daily_regime(btc_df_daily)
        return regime
    except Exception as exc:
        logger.warning("Could not determine BTC market regime: %s", exc)
        return REGIME_NEUTRAL


def regime_allows_entry(market_regime: str, hybrid_score: float) -> bool:
    """Whether a given market regime allows entries."""
    if market_regime == REGIME_BULLISH:
        return True
    if market_regime == REGIME_EARLY_BULLISH:
        return True
    if market_regime == REGIME_NEUTRAL:
        return hybrid_score >= config.NEW_ENTRY_MIN_SCORE
    # BEARISH — block most entries
    return hybrid_score >= config.STRONG_ENTRY_MIN_SCORE


def regime_score_contribution(market_regime: str) -> float:
    """Portion of SCORE_MARKET_REGIME (0-1) based on regime."""
    mapping = {
        REGIME_BULLISH: 1.0,
        REGIME_EARLY_BULLISH: 0.7,
        REGIME_NEUTRAL: 0.4,
        REGIME_BEARISH: 0.0,
    }
    return mapping.get(market_regime, 0.4)
