"""one_hour_entry.py — 1H entry trigger / timing confirmation."""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

import config
from indicators import calculate_macd_full, calculate_rsi_pair, calculate_volume_ratio

logger = logging.getLogger(__name__)


def analyze_1h(df_1h: pd.DataFrame | None) -> dict[str, Any]:
    """Full 1H analysis for entry timing. Returns trigger dict."""
    result: dict[str, Any] = {
        "rsi_1h": None,
        "rsi_1h_prev": None,
        "rsi_1h_ok": False,
        "volume_1h_ratio": None,
        "volume_1h_rising": False,
        "macd_1h_bullish": False,
        "price_above_ema9_1h": False,
        "candle_momentum": None,
        "trigger_ok": False,
        "trigger_strength": 0,
    }
    if df_1h is None or len(df_1h) < 20:
        return result

    # RSI
    rsi_cur, rsi_prev = calculate_rsi_pair(df_1h, period=config.RSI_PERIOD)
    result["rsi_1h"] = rsi_cur
    result["rsi_1h_prev"] = rsi_prev
    result["rsi_1h_ok"] = (
        rsi_cur is not None
        and rsi_prev is not None
        and config.RSI_1H_MIN <= rsi_cur <= config.RSI_1H_MAX
        and rsi_cur > rsi_prev
    )

    # Volume rising
    if len(df_1h) >= 2:
        vol_cur = df_1h["volume"].iloc[-1]
        vol_prev = df_1h["volume"].iloc[-2]
        result["volume_1h_rising"] = bool(vol_cur > vol_prev)
    result["volume_1h_ratio"] = calculate_volume_ratio(df_1h, period=20)

    # EMA9 on 1H
    if "ema9" in df_1h.columns:
        ema9 = df_1h["ema9"].iloc[-1]
        close = df_1h["close"].iloc[-1]
        result["price_above_ema9_1h"] = bool(not pd.isna(ema9) and close > ema9)

    # MACD 1H
    macd = calculate_macd_full(df_1h, fast=config.MACD_FAST, slow=config.MACD_SLOW, signal=config.MACD_SIGNAL)
    result["macd_1h_bullish"] = (
        macd["macd_line"] is not None
        and macd["signal_line"] is not None
        and macd["macd_line"] > macd["signal_line"]
    )

    # Candle momentum (body / range ratio)
    if len(df_1h) >= 1:
        last = df_1h.iloc[-1]
        rng = last["high"] - last["low"]
        body = abs(last["close"] - last["open"])
        result["candle_momentum"] = float(body / rng) if rng > 0 else 0.0

    # Trigger strength score 0-5
    strength = 0
    if result["rsi_1h_ok"]:
        strength += 2
    if result["volume_1h_rising"]:
        strength += 1
    if result["price_above_ema9_1h"]:
        strength += 1
    if result["macd_1h_bullish"]:
        strength += 1
    result["trigger_strength"] = strength
    result["trigger_ok"] = strength >= 3

    return result
