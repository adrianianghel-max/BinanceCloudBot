"""retest_detector.py — Detect breakout followed by retest of old resistance as support."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

import config

logger = logging.getLogger(__name__)


def detect_retest_entry(
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame | None,
    distance_to_breakout_pct: float | None,
) -> dict[str, Any]:
    """
    Retest sequence:
        BREAKOUT confirmed → price returns near old resistance → resistance becomes support
        → selling volume decreases → bullish 1H candle + volume increase
    Returns retest_entry = True/False and confidence 0-100.
    """
    result = {
        "retest_entry": False,
        "retest_confidence": 0.0,
        "breakout_confirmed": False,
        "near_old_resistance": False,
        "volume_declining_on_pullback": False,
        "bullish_reversal_1h": False,
    }

    if len(df_4h) < config.RETEST_WINDOW_CANDLES + 5:
        return result

    # Step 1: Was there a recent breakout?
    lookback = config.RETEST_WINDOW_CANDLES
    highs = df_4h["high"]
    closes = df_4h["close"]

    # Find the highest close in the lookback window (excluding last 5 candles)
    if len(df_4h) < lookback + 5:
        return result

    historical_highs = highs.iloc[-(lookback + 5):-5]
    resistance_level = float(historical_highs.max())
    current_close = float(closes.iloc[-1])

    # Check if a breakout happened in last 5-15 candles
    breakout_candle_idx = None
    for i in range(5, min(15, len(df_4h))):
        candle_close = closes.iloc[-i]
        candle_prev_high = highs.iloc[-(i + lookback // 2):-i].max() if len(highs) > i + lookback // 2 else None
        if candle_prev_high is not None and candle_close > candle_prev_high * (1 + 0.005):
            breakout_candle_idx = i
            result["breakout_confirmed"] = True
            resistance_level = float(candle_prev_high)
            break

    if not result["breakout_confirmed"]:
        return result

    # Step 2: Is current price near old resistance (now support)?
    tolerance = config.RETEST_TOLERANCE_PCT / 100.0
    dist = abs(current_close - resistance_level) / resistance_level
    result["near_old_resistance"] = bool(dist <= tolerance)

    if not result["near_old_resistance"]:
        return result

    # Step 3: Volume declining on pullback (last 3 candles vs breakout candle)
    if breakout_candle_idx and len(df_4h) >= breakout_candle_idx:
        bo_volume = float(df_4h["volume"].iloc[-breakout_candle_idx])
        pullback_volume_avg = float(df_4h["volume"].iloc[-3:].mean())
        result["volume_declining_on_pullback"] = bool(pullback_volume_avg < bo_volume * 0.7)

    # Step 4: Bullish reversal on 1H
    if df_1h is not None and len(df_1h) >= 3:
        last_1h = df_1h.iloc[-1]
        bullish_candle = last_1h["close"] > last_1h["open"]
        vol_rising = df_1h["volume"].iloc[-1] > df_1h["volume"].iloc[-2]
        result["bullish_reversal_1h"] = bool(bullish_candle and vol_rising)

    # Confidence score
    conf = 0.0
    if result["breakout_confirmed"]:
        conf += 30
    if result["near_old_resistance"]:
        conf += 25
    if result["volume_declining_on_pullback"]:
        conf += 25
    if result["bullish_reversal_1h"]:
        conf += 20
    result["retest_confidence"] = conf
    result["retest_entry"] = conf >= 60

    return result
