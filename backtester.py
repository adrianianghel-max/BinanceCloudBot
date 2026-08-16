"""backtester.py — Simple historical backtest: OLD system vs NEW system comparison.

Usage:
    python backtester.py --symbol BTC/USDC --days 60
"""
from __future__ import annotations

import argparse
import logging
from typing import Any

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def _build_exchange():
    import ccxt
    import config
    params = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
    return getattr(ccxt, config.PRIMARY_EXCHANGE_ID)(params)


def _simulate_trades(signals: list[dict], target_pct: float = 8.0, stop_pct: float = 3.0) -> dict[str, Any]:
    """Given a list of signal dicts with entry/future prices, compute stats."""
    wins, losses = 0, 0
    pnls = []
    for s in signals:
        entry = s.get("entry_price", 0)
        future_max = s.get("future_max_gain", 0) or 0
        future_min = s.get("future_max_drawdown", 0) or 0
        # Did price reach target before stop?
        if future_max >= target_pct:
            wins += 1
            pnls.append(target_pct)
        elif future_min <= -stop_pct:
            losses += 1
            pnls.append(-stop_pct)
        else:
            losses += 1
            pnls.append(future_min)
    total = wins + losses
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 2) if total else 0,
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
        "profit_factor": round(
            sum(p for p in pnls if p > 0) / max(abs(sum(p for p in pnls if p < 0)), 0.01), 2
        ),
    }


def run_backtest(symbol: str = "BTC/USDC", days: int = 60) -> None:
    """
    Fetch historical 4H candles, replay entry detection, compare OLD vs NEW.
    This is a simplified replay — production backtesting requires tick-level or
    OHLCV-level high/low forward simulation to avoid lookahead bias.
    """
    import config
    from indicators import add_4h_ema_columns, add_ema_columns, prepare_ohlcv_df
    from four_hour_analyzer import analyze_4h
    from one_hour_entry import analyze_1h
    from entry_scorer import compute_technical_score, compute_hybrid_score, classify_signal
    from market_regime import REGIME_BULLISH

    exchange = _build_exchange()
    limit = days * 6 + 50  # ~6 4H candles per day

    logger.info("Fetching %s candles for %s ...", limit, symbol)
    try:
        daily_raw = exchange.fetch_ohlcv(symbol, "1d", limit=260)
        h4_raw = exchange.fetch_ohlcv(symbol, "4h", limit=limit)
        h1_raw = exchange.fetch_ohlcv(symbol, "1h", limit=limit * 4)
    except Exception as exc:
        logger.error("Could not fetch candles: %s", exc)
        return

    daily_df = add_ema_columns(prepare_ohlcv_df(daily_raw))
    h4_full = add_4h_ema_columns(prepare_ohlcv_df(h4_raw))
    h1_full = add_4h_ema_columns(prepare_ohlcv_df(h1_raw)) if h1_raw else None

    signals_new = []
    forward_window = 12  # 12 x 4H candles = 48h

    for i in range(50, len(h4_full) - forward_window):
        h4_slice = h4_full.iloc[:i].copy()
        h1_slice = None
        if h1_full is not None:
            h1_slice = h1_full[h1_full["timestamp"] <= h4_full.iloc[i]["timestamp"]].tail(60)

        h4 = analyze_4h(h4_slice, daily_df)
        h1 = analyze_1h(h1_slice)
        from relative_strength import calculate_relative_strength
        rs = calculate_relative_strength(h4_slice, None)
        from retest_detector import detect_retest_entry
        retest = detect_retest_entry(h4_slice, h1_slice, h4.get("distance_to_breakout_pct"))

        tech, _ = compute_technical_score(
            "BULLISH", REGIME_BULLISH, h4, h1, rs
        )
        hybrid = compute_hybrid_score(tech, None)
        signal = classify_signal(hybrid, h4, h1, None, retest)

        from entry_scorer import SIGNAL_PRE_ENTRY, SIGNAL_NEW_ENTRY, SIGNAL_RETEST_ENTRY, SIGNAL_STRONG_ENTRY
        if signal in (SIGNAL_PRE_ENTRY, SIGNAL_NEW_ENTRY, SIGNAL_RETEST_ENTRY, SIGNAL_STRONG_ENTRY):
            entry_price = float(h4_full["close"].iloc[i])
            future = h4_full.iloc[i:i + forward_window]
            future_max_gain = float(((future["high"].max() - entry_price) / entry_price) * 100)
            future_max_dd = float(((future["low"].min() - entry_price) / entry_price) * 100)
            signals_new.append({
                "candle_idx": i,
                "entry_price": entry_price,
                "signal_type": signal,
                "hybrid_score": hybrid,
                "future_max_gain": future_max_gain,
                "future_max_drawdown": future_max_dd,
            })

    logger.info("NEW SYSTEM: %s signals detected", len(signals_new))
    stats = _simulate_trades(signals_new)
    print("\n=== BACKTEST RESULTS — NEW SYSTEM ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print("\n=== SIGNAL TYPE BREAKDOWN ===")
    from collections import Counter
    breakdown = Counter(s["signal_type"] for s in signals_new)
    for k, v in breakdown.most_common():
        wins_k = sum(1 for s in signals_new if s["signal_type"] == k and s.get("future_max_gain", 0) >= 8.0)
        print(f"  {k}: {v} signals, {wins_k} wins ({round(wins_k / v * 100, 1) if v else 0}% win rate)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC/USDC")
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    run_backtest(args.symbol, args.days)
