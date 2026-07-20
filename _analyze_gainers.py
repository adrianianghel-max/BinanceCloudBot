import json
from datetime import datetime, timedelta, timezone

import ccxt
import pandas as pd

QUOTE = "USDC"
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


def is_leveraged(base: str) -> bool:
    u = (base or "").upper()
    return any(u.endswith(s) for s in LEVERAGED_SUFFIXES)


def get_symbols(exchange: ccxt.Exchange) -> list[str]:
    markets = exchange.load_markets()
    out = []
    for symbol, market in markets.items():
        if not market.get("active") or not market.get("spot"):
            continue
        if market.get("quote") != QUOTE:
            continue
        if is_leveraged(market.get("base", "")):
            continue
        out.append(symbol)
    return sorted(out)


def to_df(raw):
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def macd(close):
    e12 = ema(close, 12)
    e26 = ema(close, 26)
    line = e12 - e26
    signal = line.ewm(span=9, adjust=False).mean()
    hist = line - signal
    return line, signal, hist


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def detect_explosion_start(h4: pd.DataFrame):
    # Heuristic: first candle where MACD cross up + volume spike + close breaks recent 10-candle high.
    c = h4.copy()
    c["macd"], c["signal"], c["hist"] = macd(c["close"])
    c["vol_sma20"] = c["volume"].rolling(20).mean()
    c["vol_ratio"] = c["volume"] / c["vol_sma20"]
    c["rsi"] = rsi(c["close"], 14)
    c["hh10_prev"] = c["high"].rolling(10).max().shift(1)
    c["macd_cross_up"] = (c["macd"] > c["signal"]) & (c["macd"].shift(1) <= c["signal"].shift(1))
    c["breakout"] = c["close"] > c["hh10_prev"]
    c["vol_ok"] = c["vol_ratio"] >= 1.2

    cand = c[c["macd_cross_up"] & c["breakout"] & c["vol_ok"]].copy()
    if cand.empty:
        # fallback: strongest momentum candle by histogram jump * volume ratio
        c["impulse"] = (c["hist"].diff().fillna(0).clip(lower=0)) * c["vol_ratio"].fillna(0)
        row = c.sort_values("impulse", ascending=False).head(1)
    else:
        row = cand.head(1)

    if row.empty:
        return None

    r = row.iloc[0]
    return {
        "start_ts": r["ts"].isoformat(),
        "start_close": float(r["close"]),
        "start_vol_ratio": float(r.get("vol_ratio", 0) or 0),
        "start_rsi": float(r.get("rsi", 0) or 0),
        "start_macd_spread": float((r.get("macd", 0) or 0) - (r.get("signal", 0) or 0)),
    }


def main():
    ex = ccxt.binanceus({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    symbols = get_symbols(ex)

    now = datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    rows = []

    for s in symbols:
        try:
            d1_raw = ex.fetch_ohlcv(s, "1d", limit=15)
            h4_raw = ex.fetch_ohlcv(s, "4h", limit=120)
            if not d1_raw or not h4_raw:
                continue

            d1 = to_df(d1_raw)
            h4 = to_df(h4_raw)
            close = float(d1["close"].iloc[-1])
            close_7d = float(d1["close"].iloc[-8]) if len(d1) >= 8 else None
            gain_7d = ((close / close_7d) - 1) * 100 if close_7d else None

            # today's gain from first 4h candle of day to latest close
            today_candles = h4[h4["ts"] >= day_start]
            if len(today_candles) >= 1:
                open_today = float(today_candles["open"].iloc[0])
                gain_today = ((close / open_today) - 1) * 100 if open_today else None
            else:
                gain_today = None

            start = detect_explosion_start(h4)
            rows.append(
                {
                    "symbol": s,
                    "gain_7d_pct": None if gain_7d is None else round(gain_7d, 2),
                    "gain_today_pct": None if gain_today is None else round(gain_today, 2),
                    "start": start,
                }
            )
        except Exception:
            continue

    df = pd.DataFrame(rows)
    g7 = (
        df.dropna(subset=["gain_7d_pct"]).sort_values("gain_7d_pct", ascending=False).head(15)
        if not df.empty
        else pd.DataFrame()
    )
    gt = (
        df.dropna(subset=["gain_today_pct"]).sort_values("gain_today_pct", ascending=False).head(15)
        if not df.empty
        else pd.DataFrame()
    )

    report = {
        "generated_at": now.isoformat(),
        "top_7d": g7.to_dict(orient="records"),
        "top_today": gt.to_dict(orient="records"),
    }

    with open("gainer_analysis.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("Saved gainer_analysis.json")


if __name__ == "__main__":
    main()
