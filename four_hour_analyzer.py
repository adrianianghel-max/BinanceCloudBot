"""four_hour_analyzer.py — 4H setup analysis: structure, compression, OBV, MACD, ADX, RS."""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

import config
from indicators import (
    calculate_4h_structure,
    calculate_adx_full,
    calculate_atr_features,
    calculate_bb_compression,
    calculate_distance_to_breakout_pct,
    calculate_macd_full,
    calculate_obv_features,
    calculate_overextension,
    calculate_rsi_pair,
    calculate_volume_ratio,
    classify_volume,
    get_golden_cross_candles_ago,
    is_4h_breakout,
)

logger = logging.getLogger(__name__)


def analyze_4h(df_4h: pd.DataFrame, df_daily: pd.DataFrame | None = None) -> dict[str, Any]:
    """Full 4H analysis. Returns a dict of all indicators and sub-scores."""
    result: dict[str, Any] = {}

    # ── Volume
    vol_ratio = calculate_volume_ratio(df_4h, period=config.VOLUME_SMA_PERIOD)
    result["volume_ratio"] = vol_ratio
    result["volume_label"] = classify_volume(vol_ratio)
    result["volume_ok"] = vol_ratio is not None and vol_ratio >= config.MIN_VOLUME_RATIO

    # ── MACD
    macd = calculate_macd_full(df_4h, fast=config.MACD_FAST, slow=config.MACD_SLOW, signal=config.MACD_SIGNAL)
    result.update({
        "macd_line": macd["macd_line"],
        "signal_line": macd["signal_line"],
        "macd_histogram": macd["histogram"],
        "macd_histogram_rising": macd["histogram_rising"],
        "macd_slope": macd["macd_slope"],
        "macd_spread_ratio": macd["spread_ratio"],
        "macd_ok": (
            macd["macd_line"] is not None
            and macd["signal_line"] is not None
            and macd["macd_line"] > macd["signal_line"]
            and (macd["spread_ratio"] or 0) >= config.MIN_MACD_SPREAD_RATIO
        ),
    })

    # ── ADX + DI
    adx = calculate_adx_full(df_4h, period=config.ADX_PERIOD)
    result.update({
        "adx_4h": adx["adx"],
        "di_plus": adx["di_plus"],
        "di_minus": adx["di_minus"],
        "adx_rising": adx["adx_rising"],
        "di_plus_above_minus": adx["di_plus_above_minus"],
        "di_plus_slope": adx["di_plus_slope"],
        "adx_ok": adx["adx"] is not None and adx["adx"] >= config.MIN_ADX,
    })

    # ── RSI 4H
    rsi_4h_cur, rsi_4h_prev = calculate_rsi_pair(df_4h, period=config.RSI_PERIOD)
    result["rsi_4h"] = rsi_4h_cur
    result["rsi_4h_prev"] = rsi_4h_prev
    result["rsi_4h_ok"] = (
        rsi_4h_cur is not None
        and config.RSI_4H_MIN <= rsi_4h_cur <= config.RSI_4H_MAX
    )

    # ── BB Compression
    bb = calculate_bb_compression(df_4h, period=config.BB_PERIOD, std=config.BB_STD)
    result.update({
        "bb_width": bb["bb_width"],
        "bb_width_sma": bb["bb_width_sma"],
        "bb_compressed": bb["bb_compressed"],
        "bb_compression_score": bb["compression_score"],
        "bb_width_declining": bb["bb_width_declining"],
    })

    # ── ATR
    atr = calculate_atr_features(df_4h, period=config.ATR_PERIOD)
    result.update({
        "atr": atr["atr"],
        "atr_pct": atr["atr_pct"],
        "atr_declining": atr["atr_declining"],
    })

    # ── OBV / Accumulation
    obv = calculate_obv_features(df_4h, ema_period=20)
    result.update({
        "obv": obv["obv"],
        "obv_ema": obv["obv_ema"],
        "obv_slope": obv["obv_slope"],
        "obv_above_ema": obv["obv_above_ema"],
        "hidden_accumulation": obv["hidden_accumulation"],
    })

    # ── 4H Structure
    struct = calculate_4h_structure(df_4h, lookback=10)
    result.update({
        "hh": struct["hh"],
        "hl": struct["hl"],
        "lh": struct["lh"],
        "ll": struct["ll"],
        "structure_score": struct["structure_score"],
        "trend_structure": struct["trend_structure"],
    })

    # ── Breakout / distance
    distance = calculate_distance_to_breakout_pct(df_4h, lookback=config.BREAKOUT_LOOKBACK)
    is_bo = is_4h_breakout(df_4h, lookback=config.BREAKOUT_LOOKBACK)
    result["distance_to_breakout_pct"] = distance
    result["is_breakout"] = is_bo
    result["near_breakout_ok"] = (
        distance is not None and 0 <= distance <= config.PRE_ENTRY_MAX_DISTANCE_PCT
    )

    # Classify breakout proximity
    if distance is not None:
        if distance <= 1.0:
            result["breakout_zone"] = "immediate"
        elif distance <= 2.0:
            result["breakout_zone"] = "optimal"
        elif distance <= 3.0:
            result["breakout_zone"] = "early"
        else:
            result["breakout_zone"] = "far"
    else:
        result["breakout_zone"] = "unknown"

    # ── EMA distances
    last = df_4h.iloc[-1]
    close = last["close"]
    for ema in (9, 21, 50):
        col = f"ema{ema}"
        val = last.get(col, float("nan"))
        if not pd.isna(val) and val > 0:
            result[f"dist_ema{ema}_pct"] = float(((close - val) / val) * 100)
        else:
            result[f"dist_ema{ema}_pct"] = None

    # ── Golden cross info
    cross_candles_ago = get_golden_cross_candles_ago(df_4h)
    result["golden_cross_candles_ago"] = cross_candles_ago
    ema9 = last.get("ema9", float("nan"))
    ema21 = last.get("ema21", float("nan"))
    result["ema9_above_ema21"] = (
        not pd.isna(ema9) and not pd.isna(ema21) and bool(ema9 > ema21)
    )

    # ── Overextension
    oe = calculate_overextension(df_4h, rsi_4h=rsi_4h_cur)
    result["overextended"] = oe["overextended"]
    result["overextension_score"] = oe["overextension_score"]

    return result
