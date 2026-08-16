"""relative_strength.py — Calculate relative strength vs BTC."""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

import config

logger = logging.getLogger(__name__)


def calculate_relative_strength(
    df_symbol_4h: pd.DataFrame,
    df_btc_4h: pd.DataFrame | None,
    df_symbol_1d: pd.DataFrame | None = None,
    df_btc_1d: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    Returns RS ratio (symbol / BTC) for 4H and 1D timeframes,
    and whether RS is rising.
    """
    result: dict[str, Any] = {
        "rs_4h": None,
        "rs_1d": None,
        "rs_rising": False,
        "rs_strong": False,
        "rs_score": 50.0,
    }

    # 4H relative strength
    lookback = config.RS_LOOKBACK_4H
    if df_btc_4h is not None and len(df_symbol_4h) >= lookback + 1 and len(df_btc_4h) >= lookback + 1:
        sym_ret = (df_symbol_4h["close"].iloc[-1] / df_symbol_4h["close"].iloc[-(lookback + 1)] - 1)
        btc_ret = (df_btc_4h["close"].iloc[-1] / df_btc_4h["close"].iloc[-(lookback + 1)] - 1)
        if btc_ret != 0:
            result["rs_4h"] = float(sym_ret / btc_ret) if btc_ret != 0 else None

    # 1D relative strength
    lookback_1d = config.RS_LOOKBACK_1D
    if df_btc_1d is not None and df_symbol_1d is not None:
        if len(df_symbol_1d) >= lookback_1d + 1 and len(df_btc_1d) >= lookback_1d + 1:
            sym_ret_1d = (df_symbol_1d["close"].iloc[-1] / df_symbol_1d["close"].iloc[-(lookback_1d + 1)] - 1)
            btc_ret_1d = (df_btc_1d["close"].iloc[-1] / df_btc_1d["close"].iloc[-(lookback_1d + 1)] - 1)
            if btc_ret_1d != 0:
                result["rs_1d"] = float(sym_ret_1d / btc_ret_1d)

    # Is RS rising? Compare last 2 windows
    if df_btc_4h is not None and len(df_symbol_4h) >= lookback * 2 + 1 and len(df_btc_4h) >= lookback * 2 + 1:
        sym_ret_prev = (df_symbol_4h["close"].iloc[-(lookback + 1)] / df_symbol_4h["close"].iloc[-(lookback * 2 + 1)] - 1)
        btc_ret_prev = (df_btc_4h["close"].iloc[-(lookback + 1)] / df_btc_4h["close"].iloc[-(lookback * 2 + 1)] - 1)
        rs_prev = (sym_ret_prev / btc_ret_prev) if btc_ret_prev != 0 else None
        if rs_prev is not None and result["rs_4h"] is not None:
            result["rs_rising"] = bool(result["rs_4h"] > rs_prev)

    rs_4h = result["rs_4h"]
    result["rs_strong"] = rs_4h is not None and rs_4h > 1.2

    # RS score contribution 0-100
    if rs_4h is not None:
        if rs_4h > 1.5:
            score = 90
        elif rs_4h > 1.2:
            score = 75
        elif rs_4h > 1.0:
            score = 60
        elif rs_4h > 0.8:
            score = 40
        else:
            score = 20
        if result["rs_rising"]:
            score = min(score + 10, 100)
        result["rs_score"] = float(score)

    return result
