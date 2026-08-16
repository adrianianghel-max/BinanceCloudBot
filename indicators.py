"""indicators.py — v2.0: all technical indicator calculations."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta


# ─────────────────────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────────────────────

def prepare_ohlcv_df(raw_ohlcv: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(
        raw_ohlcv,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


# ─────────────────────────────────────────────────────────────
# EMA columns
# ─────────────────────────────────────────────────────────────

def add_ema_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA9/10/21/50/200 to any dataframe."""
    out = df.copy()
    for length in (9, 10, 21, 50, 200):
        out[f"ema{length}"] = ta.ema(out["close"], length=length)
    return out


def add_4h_ema_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA9/21/50 to 4h dataframe."""
    out = df.copy()
    for length in (9, 21, 50):
        out[f"ema{length}"] = ta.ema(out["close"], length=length)
    return out


# ─────────────────────────────────────────────────────────────
# EMA slope
# ─────────────────────────────────────────────────────────────

def calculate_ema10_slope_pct(df: pd.DataFrame, lookback: int = 10) -> Optional[float]:
    if len(df) < lookback + 1:
        return None
    current = df["ema10"].iloc[-1]
    previous = df["ema10"].iloc[-(lookback + 1)]
    if pd.isna(current) or pd.isna(previous) or previous == 0:
        return None
    return ((current - previous) / abs(previous)) * 100.0


def calculate_ema_slope(series: pd.Series, lookback: int = 5) -> Optional[float]:
    """Generic EMA slope % over lookback candles."""
    if len(series) < lookback + 1:
        return None
    cur = series.iloc[-1]
    prev = series.iloc[-(lookback + 1)]
    if pd.isna(cur) or pd.isna(prev) or prev == 0:
        return None
    return ((cur - prev) / abs(prev)) * 100.0


# ─────────────────────────────────────────────────────────────
# Daily trend
# ─────────────────────────────────────────────────────────────

def is_daily_bullish(df: pd.DataFrame) -> bool:
    if len(df) < 210:
        return False
    last = df.iloc[-1]
    return bool(last["ema10"] > last["ema50"] > last["ema200"]) and bool(last["close"] > last["ema10"])


def is_daily_early_trend(df: pd.DataFrame, ema50_lookback: int = 5) -> bool:
    if len(df) < max(ema50_lookback + 2, 60):
        return False
    last = df.iloc[-1]
    ema50_now = df["ema50"].iloc[-1]
    ema50_prev = df["ema50"].iloc[-(ema50_lookback + 1)]
    if pd.isna(ema50_now) or pd.isna(ema50_prev):
        return False
    return bool(last["ema10"] > last["ema50"]) and bool(last["close"] > last["ema50"]) and bool(ema50_now > ema50_prev)


# ─────────────────────────────────────────────────────────────
# Golden cross EMA9/21
# ─────────────────────────────────────────────────────────────

def is_golden_cross_ema9_21(
    df_4h: pd.DataFrame,
    df_daily: pd.DataFrame,
    confirm_candles: int = 1,
) -> bool:
    required_4h = max(confirm_candles + 2, 25)
    if len(df_4h) < required_4h:
        return False
    ema9_now = df_4h["ema9"].iloc[-1]
    ema21_now = df_4h["ema21"].iloc[-1]
    if pd.isna(ema9_now) or pd.isna(ema21_now):
        return False
    if not (ema9_now > ema21_now):
        return False
    cross_confirmed = False
    for i in range(1, confirm_candles + 2):
        ema9_prev = df_4h["ema9"].iloc[-(i + 1)]
        ema21_prev = df_4h["ema21"].iloc[-(i + 1)]
        if pd.isna(ema9_prev) or pd.isna(ema21_prev):
            return False
        if ema9_prev <= ema21_prev:
            cross_confirmed = True
            break
    if not cross_confirmed:
        return False
    if len(df_daily) < 55:
        return False
    last_daily = df_daily.iloc[-1]
    ema10_d = last_daily.get("ema10", float("nan"))
    ema50_d = last_daily.get("ema50", float("nan"))
    close_d = last_daily.get("close", float("nan"))
    if pd.isna(ema10_d) or pd.isna(ema50_d) or pd.isna(close_d):
        return False
    return bool(ema10_d > ema50_d) or bool(close_d > ema50_d)


def get_golden_cross_candles_ago(df_4h: pd.DataFrame, max_lookback: int = 30) -> Optional[int]:
    """Return how many candles ago the EMA9/21 golden cross occurred. None if no cross found."""
    if len(df_4h) < 25:
        return None
    for i in range(1, min(max_lookback, len(df_4h) - 1)):
        ema9_cur = df_4h["ema9"].iloc[-i]
        ema21_cur = df_4h["ema21"].iloc[-i]
        ema9_prev = df_4h["ema9"].iloc[-(i + 1)]
        ema21_prev = df_4h["ema21"].iloc[-(i + 1)]
        if any(pd.isna(v) for v in (ema9_cur, ema21_cur, ema9_prev, ema21_prev)):
            continue
        if ema9_cur > ema21_cur and ema9_prev <= ema21_prev:
            return i
    return None


# ─────────────────────────────────────────────────────────────
# Breakout / distance
# ─────────────────────────────────────────────────────────────

def is_4h_breakout(df: pd.DataFrame, lookback: int = 20) -> bool:
    if len(df) < lookback + 2:
        return False
    previous_high = df["high"].rolling(window=lookback).max().shift(1).iloc[-1]
    close_now = df["close"].iloc[-1]
    if pd.isna(previous_high):
        return False
    return bool(close_now > previous_high)


def calculate_distance_to_breakout_pct(df: pd.DataFrame, lookback: int = 20) -> Optional[float]:
    if len(df) < lookback:
        return None
    highest_high = df["high"].rolling(window=lookback).max().iloc[-1]
    current_price = df["close"].iloc[-1]
    if pd.isna(highest_high) or pd.isna(current_price) or current_price <= 0:
        return None
    distance = ((highest_high - current_price) / current_price) * 100.0
    return float(distance)


# ─────────────────────────────────────────────────────────────
# ADX + DI
# ─────────────────────────────────────────────────────────────

def calculate_adx_full(df: pd.DataFrame, period: int = 14) -> dict:
    """Return adx, di_plus, di_minus, adx_slope (last 3 values), di_plus_slope."""
    result = {"adx": None, "di_plus": None, "di_minus": None,
              "adx_rising": False, "di_plus_above_minus": False, "di_plus_slope": None}
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=period)
    if adx_df is None or adx_df.empty:
        return result
    adx_col = [c for c in adx_df.columns if c.startswith("ADX_")]
    dmp_col = [c for c in adx_df.columns if c.startswith("DMP_")]
    dmn_col = [c for c in adx_df.columns if c.startswith("DMN_")]
    if not adx_col:
        return result
    adx_series = adx_df[adx_col[0]]
    adx_val = adx_series.iloc[-1]
    if pd.isna(adx_val):
        return result
    result["adx"] = float(adx_val)
    if len(adx_series) >= 3:
        a0, a1, a2 = adx_series.iloc[-1], adx_series.iloc[-2], adx_series.iloc[-3]
        result["adx_rising"] = bool(not pd.isna(a0) and not pd.isna(a1) and not pd.isna(a2)
                                    and a0 > a1 and a1 > a2)
    if dmp_col and dmn_col:
        dmp = adx_df[dmp_col[0]].iloc[-1]
        dmn = adx_df[dmn_col[0]].iloc[-1]
        if not pd.isna(dmp) and not pd.isna(dmn):
            result["di_plus"] = float(dmp)
            result["di_minus"] = float(dmn)
            result["di_plus_above_minus"] = bool(dmp > dmn)
        if dmp_col and len(adx_df) >= 3:
            dmp_series = adx_df[dmp_col[0]]
            dmp_cur = dmp_series.iloc[-1]
            dmp_prev = dmp_series.iloc[-3]
            if not pd.isna(dmp_cur) and not pd.isna(dmp_prev) and dmp_prev != 0:
                result["di_plus_slope"] = float(((dmp_cur - dmp_prev) / abs(dmp_prev)) * 100)
    return result


def calculate_adx_value(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    return calculate_adx_full(df, period)["adx"]


# ─────────────────────────────────────────────────────────────
# MACD
# ─────────────────────────────────────────────────────────────

def calculate_macd_full(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """Return macd_line, signal_line, histogram, histogram_rising, macd_slope."""
    result = {"macd_line": None, "signal_line": None, "histogram": None,
              "histogram_rising": False, "macd_slope": None, "spread_ratio": None}
    macd_df = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
    if macd_df is None or macd_df.empty:
        return result
    line_col = [c for c in macd_df.columns if c.startswith("MACD_") and not c.startswith("MACDh_")]
    signal_col = [c for c in macd_df.columns if c.startswith("MACDs_")]
    hist_col = [c for c in macd_df.columns if c.startswith("MACDh_")]
    if not line_col or not signal_col:
        return result
    macd_line = macd_df[line_col[0]].iloc[-1]
    signal_line = macd_df[signal_col[0]].iloc[-1]
    if pd.isna(macd_line) or pd.isna(signal_line):
        return result
    result["macd_line"] = float(macd_line)
    result["signal_line"] = float(signal_line)
    denom = max(abs(signal_line), abs(macd_line), 1e-8)
    result["spread_ratio"] = float((macd_line - signal_line) / denom)
    if hist_col and len(macd_df) >= 3:
        h_series = macd_df[hist_col[0]]
        h0, h1, h2 = h_series.iloc[-1], h_series.iloc[-2], h_series.iloc[-3]
        if not any(pd.isna(v) for v in (h0, h1, h2)):
            result["histogram"] = float(h0)
            result["histogram_rising"] = bool(h0 > h1 and h1 >= h2)
    if len(macd_df) >= 4:
        m0 = macd_df[line_col[0]].iloc[-1]
        m3 = macd_df[line_col[0]].iloc[-4]
        if not pd.isna(m0) and not pd.isna(m3) and m3 != 0:
            result["macd_slope"] = float(((m0 - m3) / abs(m3)) * 100)
    return result


def calculate_macd_values(df: pd.DataFrame) -> tuple[Optional[float], Optional[float]]:
    r = calculate_macd_full(df)
    return r["macd_line"], r["signal_line"]


# ─────────────────────────────────────────────────────────────
# RSI
# ─────────────────────────────────────────────────────────────

def calculate_rsi_pair(df: pd.DataFrame, period: int = 14) -> tuple[Optional[float], Optional[float]]:
    if len(df) < period + 2:
        return None, None
    rsi_series = ta.rsi(df["close"], length=period)
    if rsi_series is None or rsi_series.empty:
        return None, None
    cur, prev = rsi_series.iloc[-1], rsi_series.iloc[-2]
    if pd.isna(cur) or pd.isna(prev):
        return None, None
    return float(cur), float(prev)


# ─────────────────────────────────────────────────────────────
# Volume
# ─────────────────────────────────────────────────────────────

def calculate_volume_ratio(df: pd.DataFrame, period: int = 20) -> Optional[float]:
    if len(df) < period:
        return None
    sma = df["volume"].rolling(window=period).mean().iloc[-1]
    cur = df["volume"].iloc[-1]
    if pd.isna(sma) or sma == 0:
        return None
    return float(cur / sma)


def classify_volume(ratio: Optional[float]) -> str:
    if ratio is None:
        return "unknown"
    if ratio < 1.0:
        return "weak"
    if ratio < 1.2:
        return "normal"
    if ratio < 1.5:
        return "accumulation"
    if ratio < 2.0:
        return "breakout_confirmation"
    return "strong_expansion"


# ─────────────────────────────────────────────────────────────
# OBV / Accumulation
# ─────────────────────────────────────────────────────────────

def calculate_obv_features(df: pd.DataFrame, ema_period: int = 20) -> dict:
    """Return OBV value, OBV EMA20, OBV slope, hidden_accumulation flag."""
    result = {"obv": None, "obv_ema": None, "obv_slope": None,
              "obv_above_ema": False, "hidden_accumulation": False}
    if len(df) < ema_period + 5:
        return result
    obv = ta.obv(df["close"], df["volume"])
    if obv is None or obv.empty:
        return result
    obv_ema = obv.ewm(span=ema_period, adjust=False).mean()
    cur_obv = obv.iloc[-1]
    cur_ema = obv_ema.iloc[-1]
    if pd.isna(cur_obv) or pd.isna(cur_ema):
        return result
    result["obv"] = float(cur_obv)
    result["obv_ema"] = float(cur_ema)
    result["obv_above_ema"] = bool(cur_obv > cur_ema)
    if len(obv) >= 5:
        prev_obv = obv.iloc[-5]
        if not pd.isna(prev_obv) and prev_obv != 0:
            result["obv_slope"] = float(((cur_obv - prev_obv) / abs(prev_obv)) * 100)
    # Hidden accumulation: price roughly sideways but OBV rising
    if len(df) >= 5:
        price_change_pct = abs((df["close"].iloc[-1] - df["close"].iloc[-5]) / df["close"].iloc[-5] * 100)
        obv_change = (cur_obv - obv.iloc[-5]) if len(obv) >= 5 else 0
        result["hidden_accumulation"] = bool(price_change_pct < 3.0 and obv_change > 0)
    return result


# ─────────────────────────────────────────────────────────────
# Bollinger Bands — compression
# ─────────────────────────────────────────────────────────────

def calculate_bb_compression(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> dict:
    """Return BB width, BB width SMA, compression score 0-100."""
    result = {"bb_width": None, "bb_width_sma": None, "compression_score": 0.0,
              "bb_compressed": False, "bb_width_declining": False}
    if len(df) < period * 2:
        return result
    bb = ta.bbands(df["close"], length=period, std=std)
    if bb is None or bb.empty:
        return result
    upper_col = [c for c in bb.columns if "BBU" in c]
    lower_col = [c for c in bb.columns if "BBL" in c]
    mid_col = [c for c in bb.columns if "BBM" in c]
    if not upper_col or not lower_col or not mid_col:
        return result
    upper = bb[upper_col[0]]
    lower = bb[lower_col[0]]
    mid = bb[mid_col[0]]
    if mid.iloc[-1] == 0 or pd.isna(mid.iloc[-1]):
        return result
    bb_width = (upper - lower) / mid
    cur_width = bb_width.iloc[-1]
    if pd.isna(cur_width):
        return result
    result["bb_width"] = float(cur_width)
    sma_width = bb_width.rolling(window=period).mean().iloc[-1]
    if not pd.isna(sma_width):
        result["bb_width_sma"] = float(sma_width)
        result["bb_compressed"] = bool(cur_width < sma_width)
        ratio = 1.0 - (cur_width / sma_width) if sma_width > 0 else 0.0
        result["compression_score"] = float(min(max(ratio * 100, 0.0), 100.0))
    if len(bb_width) >= 5:
        result["bb_width_declining"] = bool(
            bb_width.iloc[-1] < bb_width.iloc[-2] < bb_width.iloc[-3]
        )
    return result


# ─────────────────────────────────────────────────────────────
# ATR — volatility / compression
# ─────────────────────────────────────────────────────────────

def calculate_atr_features(df: pd.DataFrame, period: int = 14) -> dict:
    """Return ATR, ATR%, ATR% declining."""
    result = {"atr": None, "atr_pct": None, "atr_declining": False}
    if len(df) < period + 5:
        return result
    atr_series = ta.atr(df["high"], df["low"], df["close"], length=period)
    if atr_series is None or atr_series.empty:
        return result
    cur_atr = atr_series.iloc[-1]
    cur_close = df["close"].iloc[-1]
    if pd.isna(cur_atr) or cur_close == 0:
        return result
    result["atr"] = float(cur_atr)
    result["atr_pct"] = float(cur_atr / cur_close * 100)
    if len(atr_series) >= 5:
        atr_pct_series = atr_series / df["close"] * 100
        result["atr_declining"] = bool(
            atr_pct_series.iloc[-1] < atr_pct_series.iloc[-3]
        )
    return result


# ─────────────────────────────────────────────────────────────
# 4H Structure: HH/HL/LH/LL
# ─────────────────────────────────────────────────────────────

def calculate_4h_structure(df: pd.DataFrame, lookback: int = 10) -> dict:
    """Detect higher-highs / higher-lows trend structure. Returns structure_score 0-100."""
    result = {"hh": False, "hl": False, "lh": False, "ll": False,
              "structure_score": 50.0, "trend_structure": "NEUTRAL"}
    if len(df) < lookback + 2:
        return result
    highs = df["high"].iloc[-(lookback + 1):]
    lows = df["low"].iloc[-(lookback + 1):]
    pivot_highs = []
    pivot_lows = []
    for i in range(1, len(highs) - 1):
        if highs.iloc[i] > highs.iloc[i - 1] and highs.iloc[i] > highs.iloc[i + 1]:
            pivot_highs.append(float(highs.iloc[i]))
        if lows.iloc[i] < lows.iloc[i - 1] and lows.iloc[i] < lows.iloc[i + 1]:
            pivot_lows.append(float(lows.iloc[i]))
    hh = hl = lh = ll = False
    if len(pivot_highs) >= 2:
        hh = pivot_highs[-1] > pivot_highs[-2]
        lh = pivot_highs[-1] < pivot_highs[-2]
    if len(pivot_lows) >= 2:
        hl = pivot_lows[-1] > pivot_lows[-2]
        ll = pivot_lows[-1] < pivot_lows[-2]
    result["hh"] = hh
    result["hl"] = hl
    result["lh"] = lh
    result["ll"] = ll
    if hh and hl:
        result["trend_structure"] = "BULLISH"
        result["structure_score"] = 85.0
    elif hh and not hl:
        result["trend_structure"] = "WEAK_BULLISH"
        result["structure_score"] = 65.0
    elif hl and not hh:
        result["trend_structure"] = "ACCUMULATION"
        result["structure_score"] = 60.0
    elif ll and lh:
        result["trend_structure"] = "BEARISH"
        result["structure_score"] = 15.0
    else:
        result["trend_structure"] = "NEUTRAL"
        result["structure_score"] = 50.0
    return result


# ─────────────────────────────────────────────────────────────
# Overextension detection
# ─────────────────────────────────────────────────────────────

def calculate_overextension(df: pd.DataFrame, rsi_4h: Optional[float] = None) -> dict:
    """Detect if price is overextended above EMA9/21."""
    result = {"overextended": False, "overextension_score": 0.0}
    if len(df) < 25:
        return result
    last = df.iloc[-1]
    ema9 = last.get("ema9")
    ema21 = last.get("ema21")
    close = last["close"]
    score = 0.0
    if ema9 and not pd.isna(ema9) and ema9 > 0:
        dist_ema9 = ((close - ema9) / ema9) * 100
        if dist_ema9 > 10:
            score += 40
        elif dist_ema9 > 5:
            score += 20
    if ema21 and not pd.isna(ema21) and ema21 > 0:
        dist_ema21 = ((close - ema21) / ema21) * 100
        if dist_ema21 > 15:
            score += 30
    if rsi_4h is not None and rsi_4h > 80:
        score += 30
    result["overextension_score"] = min(score, 100.0)
    result["overextended"] = score >= 60
    return result


# ─────────────────────────────────────────────────────────────
# Stop-loss calculation
# ─────────────────────────────────────────────────────────────

def calculate_stop_price(
    df_4h: pd.DataFrame,
    atr: Optional[float],
    atr_multiplier: float = 1.5,
) -> Optional[float]:
    """Calculate stop price from ATR and EMA21 / recent swing low."""
    close = df_4h["close"].iloc[-1]
    stops = []
    if atr is not None:
        stops.append(close - atr_multiplier * atr)
    ema21 = df_4h.get("ema21", pd.Series(dtype=float))
    if hasattr(ema21, "iloc") and len(ema21) > 0:
        v = ema21.iloc[-1]
        if not pd.isna(v):
            stops.append(float(v))
    if len(df_4h) >= 5:
        swing_low = df_4h["low"].iloc[-5:].min()
        if not pd.isna(swing_low):
            stops.append(float(swing_low))
    return float(max(stops)) if stops else None


# ─────────────────────────────────────────────────────────────
# Legacy growth_score (kept for ML backward compat)
# ─────────────────────────────────────────────────────────────

def calculate_growth_score(
    ema10_slope_pct: float,
    macd_spread_ratio: float,
    volume_ratio: float,
    distance_to_breakout_pct: float,
    rsi_value: Optional[float],
    use_1h_filter: bool,
) -> float:
    slope_norm = min(max(ema10_slope_pct / 0.5, 0.0), 1.0)
    slope_score = slope_norm * 30.0
    vol_norm = min(max((volume_ratio - 1.0) / 1.0, 0.0), 1.0)
    volume_score = vol_norm * 25.0
    macd_norm = min(max(macd_spread_ratio / 0.03, 0.0), 1.0)
    macd_score = macd_norm * 20.0
    near_breakout_norm = min(max((3.0 - max(distance_to_breakout_pct, 0.0)) / 3.0, 0.0), 1.0)
    near_breakout_score = near_breakout_norm * 15.0
    if use_1h_filter:
        rsi_base = rsi_value if rsi_value is not None else 50.0
        rsi_norm = min(max((rsi_base - 55.0) / 17.0, 0.0), 1.0)
        rsi_score = rsi_norm * 10.0
    else:
        rsi_score = 10.0
    return round(min(slope_score + volume_score + macd_score + near_breakout_score + rsi_score, 100.0), 2)
