from __future__ import annotations

from typing import Optional

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


def calculate_growth_score(
    ema10_slope_pct: float,
    macd_line: float,
    signal_line: float,
    volume_ratio: float,
    rsi_value: Optional[float],
    use_1h_filter: bool,
) -> float:
    # Transparent weighted scoring without AI/ML.
    slope_norm = min(max((ema10_slope_pct + 0.4) / 1.8, 0.0), 1.0)
    slope_score = slope_norm * 35.0

    denominator = max(abs(signal_line), abs(macd_line), 1e-8)
    macd_strength = (macd_line - signal_line) / denominator
    macd_norm = min(max(macd_strength * 4.0, 0.0), 1.0)
    macd_score = macd_norm * 30.0

    vol_norm = min(max((volume_ratio - 1.0) / 2.0, 0.0), 1.0)
    volume_score = vol_norm * 20.0

    if use_1h_filter:
        rsi_base = rsi_value if rsi_value is not None else 50.0
        rsi_norm = min(max((rsi_base - 50.0) / 20.0, 0.0), 1.0)
        rsi_score = rsi_norm * 15.0
    else:
        rsi_score = 15.0

    total = slope_score + macd_score + volume_score + rsi_score
    return round(min(total, 100.0), 2)
