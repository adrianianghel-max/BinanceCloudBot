from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta


def prepare_ohlcv_df(raw_ohlcv: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(
        raw_ohlcv,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def add_ema_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema10"] = ta.ema(out["close"], length=10)
    out["ema50"] = ta.ema(out["close"], length=50)
    out["ema200"] = ta.ema(out["close"], length=200)
    return out


def calculate_ema10_slope_pct(df: pd.DataFrame, lookback: int = 10) -> Optional[float]:
    if len(df) < lookback + 1:
        return None

    current = df["ema10"].iloc[-1]
    previous = df["ema10"].iloc[-(lookback + 1)]

    if pd.isna(current) or pd.isna(previous) or previous == 0:
        return None

    return ((current - previous) / abs(previous)) * 100.0


def is_daily_bullish(df: pd.DataFrame) -> bool:
    if len(df) < 210:
        return False

    last = df.iloc[-1]

    conditions = [
        last["ema10"] > last["ema50"] > last["ema200"],
        last["close"] > last["ema10"],
    ]
    return all(bool(c) for c in conditions)


def is_daily_early_trend(df: pd.DataFrame, ema50_lookback: int = 5) -> bool:
    if len(df) < max(ema50_lookback + 2, 60):
        return False

    last = df.iloc[-1]
    ema50_now = df["ema50"].iloc[-1]
    ema50_prev = df["ema50"].iloc[-(ema50_lookback + 1)]

    if pd.isna(ema50_now) or pd.isna(ema50_prev):
        return False

    conditions = [
        last["ema10"] > last["ema50"],
        last["close"] > last["ema50"],
        ema50_now > ema50_prev,
    ]
    return all(bool(c) for c in conditions)


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


def calculate_adx_value(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=period)
    if adx_df is None or adx_df.empty:
        return None

    adx_col = [c for c in adx_df.columns if c.startswith("ADX_")]
    if not adx_col:
        return None

    adx_value = adx_df[adx_col[0]].iloc[-1]
    if pd.isna(adx_value):
        return None

    return float(adx_value)


def calculate_adx_di(df: pd.DataFrame, period: int = 14) -> dict[str, Optional[float]]:
    """Returns ADX, DI+, DI- values."""
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=period)
    if adx_df is None or adx_df.empty:
        return {"adx": None, "di_plus": None, "di_minus": None}

    adx_col = [c for c in adx_df.columns if c.startswith("ADX_")]
    di_plus_col = [c for c in adx_df.columns if c.startswith("DMP_")]
    di_minus_col = [c for c in adx_df.columns if c.startswith("DMM_")]

    def _safe(cols):
        if not cols:
            return None
        val = adx_df[cols[0]].iloc[-1]
        return float(val) if not pd.isna(val) else None

    return {
        "adx": _safe(adx_col),
        "di_plus": _safe(di_plus_col),
        "di_minus": _safe(di_minus_col),
    }


def calculate_macd_values(df: pd.DataFrame) -> tuple[Optional[float], Optional[float]]:
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is None or macd_df.empty:
        return None, None

    line_col = [c for c in macd_df.columns if c.startswith("MACD_") and not c.startswith("MACDh_")]
    signal_col = [c for c in macd_df.columns if c.startswith("MACDs_")]

    if not line_col or not signal_col:
        return None, None

    macd_line = macd_df[line_col[0]].iloc[-1]
    signal_line = macd_df[signal_col[0]].iloc[-1]

    if pd.isna(macd_line) or pd.isna(signal_line):
        return None, None

    return float(macd_line), float(signal_line)


def calculate_macd_full(df: pd.DataFrame) -> dict[str, Optional[float]]:
    """Returns MACD line, signal, histogram, and fast-component slope."""
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is None or macd_df.empty:
        return {"macd": None, "signal": None, "histogram": None, "slope": None}

    line_col = [c for c in macd_df.columns if c.startswith("MACD_") and not c.startswith("MACDh_")]
    signal_col = [c for c in macd_df.columns if c.startswith("MACDs_")]
    hist_col = [c for c in macd_df.columns if c.startswith("MACDh_")]

    def _safe(cols, idx=-1):
        if not cols:
            return None
        val = macd_df[cols[0]].iloc[idx]
        return float(val) if not pd.isna(val) else None

    macd_line = _safe(line_col)
    signal_line = _safe(signal_col)
    histogram = _safe(hist_col)

    # Fast-component slope: rate of change of MACD line over last 5 bars
    slope = None
    if line_col and len(macd_df) >= 6:
        macd_series = macd_df[line_col[0]].dropna()
        if len(macd_series) >= 6:
            current = macd_series.iloc[-1]
            previous = macd_series.iloc[-6]
            if not pd.isna(current) and not pd.isna(previous) and abs(previous) > 1e-12:
                slope = ((current - previous) / abs(previous)) * 100.0

    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
        "slope": slope,
    }


def calculate_macd_histogram_slope(df: pd.DataFrame, lookback: int = 5) -> Optional[float]:
    """Slope of MACD histogram (rising histogram = bullish momentum)."""
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is None or macd_df.empty:
        return None

    hist_col = [c for c in macd_df.columns if c.startswith("MACDh_")]
    if not hist_col:
        return None

    hist = macd_df[hist_col[0]].dropna()
    if len(hist) < lookback + 1:
        return None

    current = hist.iloc[-1]
    previous = hist.iloc[-(lookback + 1)]
    if pd.isna(current) or pd.isna(previous):
        return None

    return float(current - previous)


def calculate_stoch_rsi(
    df: pd.DataFrame,
    rsi_length: int = 14,
    stoch_length: int = 14,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> dict[str, Optional[object]]:
    """Stochastic RSI (14,14,3,3). Returns %K, %D, golden-cross status, positive zone."""
    stoch = ta.stochrsi(
        df["close"],
        length=rsi_length,
        rsi_length=rsi_length,
        k_length=k_smooth,
        d_length=d_smooth,
    )
    if stoch is None or stoch.empty:
        return {"k": None, "d": None, "golden_cross": False, "positive_zone": False}

    k_col = [c for c in stoch.columns if c.startswith("STOCHRSIk_")]
    d_col = [c for c in stoch.columns if c.startswith("STOCHRSId_")]

    def _safe(cols, idx=-1):
        if not cols:
            return None
        val = stoch[cols[0]].iloc[idx]
        return float(val) if not pd.isna(val) else None

    k_now = _safe(k_col)
    d_now = _safe(d_col)
    k_prev = _safe(k_col, -2)
    d_prev = _safe(d_col, -2)

    golden_cross = False
    if k_now is not None and d_now is not None and k_prev is not None and d_prev is not None:
        golden_cross = k_prev <= d_prev and k_now > d_now

    positive_zone = k_now is not None and k_now > 50.0

    return {
        "k": k_now,
        "d": d_now,
        "golden_cross": golden_cross,
        "positive_zone": positive_zone,
    }


def calculate_obv(df: pd.DataFrame) -> Optional[float]:
    """On-Balance Volume - current value."""
    obv = ta.obv(df["close"], df["volume"])
    if obv is None or obv.empty:
        return None

    obv_series = obv.dropna()
    if len(obv_series) < 1:
        return None

    return float(obv_series.iloc[-1])


def calculate_obv_rising(df: pd.DataFrame, lookback: int = 5) -> Optional[bool]:
    """True if OBV is rising over the lookback period."""
    obv = ta.obv(df["close"], df["volume"])
    if obv is None or obv.empty:
        return None

    obv_series = obv.dropna()
    if len(obv_series) < lookback + 1:
        return None

    return bool(obv_series.iloc[-1] > obv_series.iloc[-(lookback + 1)])


def calculate_bollinger_bandwidth(df: pd.DataFrame, length: int = 20, std: float = 2.0) -> Optional[float]:
    """Bollinger Band Width - low value = squeeze (pre-breakout)."""
    bb = ta.bbands(df["close"], length=length, std=std)
    if bb is None or bb.empty:
        return None

    upper_col = [c for c in bb.columns if c.startswith("BBU_")]
    lower_col = [c for c in bb.columns if c.startswith("BBL_")]
    mid_col = [c for c in bb.columns if c.startswith("BBM_")]

    if not upper_col or not lower_col or not mid_col:
        return None

    upper = bb[upper_col[0]].iloc[-1]
    lower = bb[lower_col[0]].iloc[-1]
    mid = bb[mid_col[0]].iloc[-1]

    if pd.isna(upper) or pd.isna(lower) or pd.isna(mid) or mid == 0:
        return None

    return float((upper - lower) / mid)


def calculate_bollinger_squeeze(
    df: pd.DataFrame,
    length: int = 20,
    std: float = 2.0,
    lookback: int = 20,
) -> Optional[bool]:
    """True if current bandwidth is in the lowest percentile (squeeze)."""
    bb = ta.bbands(df["close"], length=length, std=std)
    if bb is None or bb.empty:
        return None

    upper_col = [c for c in bb.columns if c.startswith("BBU_")]
    lower_col = [c for c in bb.columns if c.startswith("BBL_")]
    mid_col = [c for c in bb.columns if c.startswith("BBM_")]

    if not upper_col or not lower_col or not mid_col:
        return None

    upper = bb[upper_col[0]]
    lower = bb[lower_col[0]]
    mid = bb[mid_col[0]]

    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    bandwidth = bandwidth.dropna()

    if len(bandwidth) < lookback + 1:
        return None

    current = bandwidth.iloc[-1]
    historical = bandwidth.iloc[-(lookback + 1):-1]

    if pd.isna(current) or historical.empty:
        return None

    percentile = (historical < current).mean()
    return bool(percentile < 0.2)  # current in lowest 20% = squeeze


def calculate_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    atr = ta.atr(df["high"], df["low"], df["close"], length=period)
    if atr is None or atr.empty:
        return None

    val = atr.iloc[-1]
    return float(val) if not pd.isna(val) else None


def calculate_lrs(df: pd.DataFrame, lookback: int = 20) -> Optional[float]:
    """Linear Regression Slope - normalized by price."""
    if len(df) < lookback:
        return None

    close = df["close"].iloc[-lookback:].values
    if len(close) < lookback or np.any(np.isnan(close)):
        return None

    x = np.arange(lookback, dtype=float)
    slope, _ = np.polyfit(x, close, 1)
    price = close[-1]
    if price == 0:
        return None

    return float(slope / price * 100.0)


def calculate_lrc(df: pd.DataFrame, lookback: int = 150) -> Optional[float]:
    """Linear Regression Channel - current price position relative to regression line."""
    if len(df) < lookback:
        return None

    close = df["close"].iloc[-lookback:].values
    if len(close) < lookback or np.any(np.isnan(close)):
        return None

    x = np.arange(lookback, dtype=float)
    slope, intercept = np.polyfit(x, close, 1)
    regression_line = slope * (lookback - 1) + intercept
    current_price = close[-1]

    if regression_line == 0:
        return None

    return float((current_price - regression_line) / abs(regression_line) * 100.0)


def calculate_volume_acceleration(df: pd.DataFrame, short_period: int = 5, long_period: int = 20) -> Optional[float]:
    """Ratio of recent volume to longer-term average volume."""
    if len(df) < long_period:
        return None

    short_vol = df["volume"].iloc[-short_period:].mean()
    long_vol = df["volume"].iloc[-long_period:].mean()

    if pd.isna(short_vol) or pd.isna(long_vol) or long_vol == 0:
        return None

    return float(short_vol / long_vol)


def calculate_price_acceleration(df: pd.DataFrame, lookback: int = 5) -> Optional[float]:
    """Rate of price change over lookback period (%)."""
    if len(df) < lookback + 1:
        return None

    current = df["close"].iloc[-1]
    previous = df["close"].iloc[-(lookback + 1)]

    if pd.isna(current) or pd.isna(previous) or previous == 0:
        return None

    return float((current - previous) / abs(previous) * 100.0)


def calculate_volume_ratio(df: pd.DataFrame, period: int = 20) -> Optional[float]:
    if len(df) < period:
        return None

    volume_sma = df["volume"].rolling(window=period).mean().iloc[-1]
    current_volume = df["volume"].iloc[-1]
    if pd.isna(volume_sma) or volume_sma == 0:
        return None
    return float(current_volume / volume_sma)


def calculate_rsi_pair(df: pd.DataFrame, period: int = 14) -> tuple[Optional[float], Optional[float]]:
    if len(df) < period + 2:
        return None, None

    rsi_series = ta.rsi(df["close"], length=period)
    if rsi_series is None or rsi_series.empty:
        return None, None

    current_rsi = rsi_series.iloc[-1]
    previous_rsi = rsi_series.iloc[-2]
    if pd.isna(current_rsi) or pd.isna(previous_rsi):
        return None, None

    return float(current_rsi), float(previous_rsi)


def calculate_remaining_potential(
    df: pd.DataFrame,
    atr_period: int = 14,
    lookback: int = 20,
) -> dict[str, Optional[float]]:
    """Deterministic estimate of remaining upside potential using ATR and recent momentum."""
    atr = calculate_atr(df, atr_period)
    if atr is None or atr == 0:
        return {"low": None, "high": None, "confidence": None}

    current_price = df["close"].iloc[-1]
    if current_price == 0:
        return {"low": None, "high": None, "confidence": None}

    # Recent momentum (5-bar price change)
    momentum = calculate_price_acceleration(df, lookback=5) or 0.0

    # ATR-based potential: 1.5x to 3x ATR as % of price
    atr_pct = atr / current_price * 100.0
    low = atr_pct * 1.5
    high = atr_pct * 3.0

    # Confidence based on momentum and volume acceleration
    vol_acc = calculate_volume_acceleration(df) or 1.0
    confidence = min(100.0, max(0.0, 50.0 + momentum * 5.0 + (vol_acc - 1.0) * 20.0))

    return {
        "low": round(low, 2),
        "high": round(high, 2),
        "confidence": round(confidence, 1),
    }


def calculate_overextension(df: pd.DataFrame, lookback: int = 20) -> Optional[float]:
    """Distance of current price above recent high (%). Negative = below high."""
    if len(df) < lookback:
        return None

    recent_high = df["high"].iloc[-lookback:].max()
    current_price = df["close"].iloc[-1]

    if pd.isna(recent_high) or recent_high == 0:
        return None

    return float((current_price - recent_high) / recent_high * 100.0)


def calculate_growth_score(
    ema10_slope_pct: float,
    macd_spread_ratio: float,
    volume_ratio: float,
    distance_to_breakout_pct: float,
    rsi_value: Optional[float],
    use_1h_filter: bool,
) -> float:
    # Pre-breakout scoring with explicit weights totaling 100 points.
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

    total = slope_score + volume_score + macd_score + near_breakout_score + rsi_score
    return round(min(total, 100.0), 2)