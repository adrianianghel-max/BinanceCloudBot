"""scanner.py — v2.0: Multi-Timeframe Crypto Entry Bot."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

import ccxt

import config
from entry_scorer import (
    SIGNAL_NEW_ENTRY,
    SIGNAL_NO_SETUP,
    SIGNAL_PRE_ENTRY,
    SIGNAL_RETEST_ENTRY,
    SIGNAL_STRONG_ENTRY,
    SIGNAL_WATCH,
    build_why_now,
    classify_signal,
    compute_entry_quality,
    compute_hybrid_score,
    compute_risk_reward,
    compute_technical_score,
)
from four_hour_analyzer import analyze_4h
from indicators import (
    add_4h_ema_columns,
    add_ema_columns,
    calculate_stop_price,
    prepare_ohlcv_df,
)
from market_regime import (
    REGIME_BEARISH,
    classify_daily_regime,
    get_btc_market_regime,
    regime_allows_entry,
)
from ml_optimizer import load_model, predict_win_probability
from one_hour_entry import analyze_1h
from outcome_tracker import record_signal
from paper_trading import open_paper_trade
from relative_strength import calculate_relative_strength
from retest_detector import detect_retest_entry
from state_manager import (
    get_alert_state,
    has_signal_state_changed,
    update_alert_state,
)
from telegram_sender import send_telegram_alerts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("scanner_v2")

ALERT_SIGNAL_TYPES = {SIGNAL_PRE_ENTRY, SIGNAL_NEW_ENTRY, SIGNAL_RETEST_ENTRY, SIGNAL_STRONG_ENTRY}


# ─────────────────────────────────────────────────────────────
# Exchange helpers
# ─────────────────────────────────────────────────────────────

def build_exchange(exchange_id: str) -> ccxt.Exchange:
    params: dict[str, Any] = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
    if config.PROXY_URL:
        params["proxies"] = {"http": config.PROXY_URL, "https": config.PROXY_URL}
    return getattr(ccxt, exchange_id)(params)


def with_retries(func, *args, **kwargs):
    delay = config.INITIAL_RETRY_DELAY
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except (ccxt.RateLimitExceeded, ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
            if attempt == config.MAX_RETRIES:
                raise
            logger.warning("Retry %s/%s: %s. Waiting %.1fs.", attempt, config.MAX_RETRIES, exc, delay)
            time.sleep(delay)
            delay *= 2


def create_exchange() -> tuple[ccxt.Exchange, tuple[str, ...]]:
    exchange_ids = [config.PRIMARY_EXCHANGE_ID, *config.FALLBACK_EXCHANGE_IDS]
    last_error: Exception | None = None
    for exchange_id in exchange_ids:
        exchange = build_exchange(exchange_id)
        try:
            with_retries(exchange.load_markets)
            if exchange_id != config.PRIMARY_EXCHANGE_ID:
                logger.warning("Fallback to %s.", exchange_id)
                return exchange, config.FALLBACK_QUOTE_ASSETS
            logger.info("Using %s.", exchange_id)
            return exchange, config.PRIMARY_QUOTE_ASSETS
        except ccxt.BaseError as exc:
            logger.warning("Exchange %s failed: %s", exchange_id, exc)
            last_error = exc
    assert last_error is not None
    raise last_error


def is_leveraged_base(base_asset: str) -> bool:
    upper = base_asset.upper()
    return any(upper.endswith(m) for m in config.LEVERAGED_TOKENS)


def get_quote_symbols(exchange: ccxt.Exchange, quote_assets: tuple[str, ...]) -> list[str]:
    markets = with_retries(exchange.load_markets)
    allowed = {q.upper() for q in quote_assets}
    symbols = []
    for symbol, market in markets.items():
        if not market.get("active") or not market.get("spot"):
            continue
        if str(market.get("quote", "")).upper() not in allowed:
            continue
        if is_leveraged_base(market.get("base", "")):
            continue
        symbols.append(symbol)
    return sorted(symbols)


# ─────────────────────────────────────────────────────────────
# Per-symbol analysis
# ─────────────────────────────────────────────────────────────

def analyze_symbol(
    exchange: ccxt.Exchange,
    symbol: str,
    ml_bundle: dict | None,
    btc_df_4h,
    btc_df_1d,
    market_regime: str,
) -> dict[str, Any] | None:
    try:
        daily_raw = with_retries(exchange.fetch_ohlcv, symbol, config.TF_DAILY, limit=config.DAILY_LIMIT)
        h4_raw = with_retries(exchange.fetch_ohlcv, symbol, config.TF_SETUP, limit=config.H4_LIMIT)
        h1_raw = with_retries(exchange.fetch_ohlcv, symbol, config.TF_ENTRY, limit=config.H1_LIMIT)
    except ccxt.BaseError as exc:
        logger.warning("Skipping %s: %s", symbol, exc)
        return {"symbol": symbol, "error": str(exc)}

    if not daily_raw or not h4_raw:
        return {"symbol": symbol, "error": "Missing OHLCV data"}

    daily_df = add_ema_columns(prepare_ohlcv_df(daily_raw))
    h4_df = add_4h_ema_columns(prepare_ohlcv_df(h4_raw))
    h1_df = add_4h_ema_columns(prepare_ohlcv_df(h1_raw)) if h1_raw else None
    daily_df_sym = prepare_ohlcv_df(daily_raw)

    # ── Daily regime
    daily_regime = classify_daily_regime(daily_df)

    # ── 4H analysis
    h4 = analyze_4h(h4_df, daily_df)

    # ── 1H analysis
    h1 = analyze_1h(h1_df)

    # ── Relative strength vs BTC
    rs = calculate_relative_strength(h4_df, btc_df_4h, daily_df_sym, btc_df_1d)

    # ── Retest detection
    retest = detect_retest_entry(h4_df, h1_df, h4.get("distance_to_breakout_pct"))

    # ── ML features
    ml_features: dict[str, Any] = {
        "ema10_slope": _ema10_slope(daily_df),
        "daily_regime_score": {"BULLISH": 1.0, "EARLY_BULLISH": 0.75, "NEUTRAL": 0.4, "BEARISH": 0.0}.get(daily_regime, 0.4),
        "macd_spread_ratio": h4.get("macd_spread_ratio"),
        "macd_histogram_rising": float(h4.get("macd_histogram_rising") or 0),
        "macd_slope": h4.get("macd_slope"),
        "vol4h": h4.get("volume_ratio"),
        "volume_label_score": {"weak": 0.1, "normal": 0.4, "accumulation": 0.65, "breakout_confirmation": 0.85, "strong_expansion": 1.0}.get(h4.get("volume_label", "normal"), 0.4),
        "dist_breakout_pct": h4.get("distance_to_breakout_pct"),
        "adx_4h": h4.get("adx_4h"),
        "adx_rising": float(h4.get("adx_rising") or 0),
        "di_plus": h4.get("di_plus"),
        "di_minus": h4.get("di_minus"),
        "di_plus_above_minus": float(h4.get("di_plus_above_minus") or 0),
        "obv_above_ema": float(h4.get("obv_above_ema") or 0),
        "obv_slope": h4.get("obv_slope"),
        "hidden_accumulation": float(h4.get("hidden_accumulation") or 0),
        "bb_compression_score": h4.get("bb_compression_score"),
        "atr_declining": float(h4.get("atr_declining") or 0),
        "structure_score": h4.get("structure_score"),
        "overextension_score": h4.get("overextension_score"),
        "dist_ema9_pct": h4.get("dist_ema9_pct"),
        "dist_ema21_pct": h4.get("dist_ema21_pct"),
        "is_breakout": float(h4.get("is_breakout") or 0),
        "golden_cross_ok": float(h4.get("ema9_above_ema21") or 0),
        "rs_score": rs.get("rs_score"),
        "rsi_1h": h1.get("rsi_1h"),
        "trigger_strength": h1.get("trigger_strength"),
        # Legacy
        "growth_score": None,
        "adx_4h_legacy": h4.get("adx_4h"),
    }

    # ── ML probability
    ml_prob = predict_win_probability(ml_features, ml_bundle)

    # ── Technical + hybrid score
    technical_score, score_components = compute_technical_score(daily_regime, market_regime, h4, h1, rs)
    hybrid_score = compute_hybrid_score(technical_score, ml_prob)

    # ── Signal classification
    signal_type = classify_signal(hybrid_score, h4, h1, ml_prob, retest)

    # ── Skip non-alertable or bearish regime blocking
    if signal_type in (SIGNAL_NO_SETUP, SIGNAL_WATCH):
        return {
            "symbol": symbol,
            "signal_type": signal_type,
            "hybrid_score": hybrid_score,
            "technical_score": technical_score,
            "daily_regime": daily_regime,
            "qualified": False,
        }

    if not regime_allows_entry(market_regime, hybrid_score):
        logger.debug("%s blocked by market regime %s", symbol, market_regime)
        return {
            "symbol": symbol,
            "signal_type": SIGNAL_WATCH,
            "hybrid_score": hybrid_score,
            "technical_score": technical_score,
            "daily_regime": daily_regime,
            "qualified": False,
        }

    # ── Fetch current price for qualified signals
    price = None
    try:
        ticker = with_retries(exchange.fetch_ticker, symbol)
        price = float(ticker.get("last") or h4_df["close"].iloc[-1])
    except Exception as exc:
        logger.warning("Could not fetch price for %s: %s", symbol, exc)
        price = float(h4_df["close"].iloc[-1])

    # ── Stop / target
    stop_price = calculate_stop_price(h4_df, h4.get("atr"), atr_multiplier=config.ATR_STOP_MULTIPLIER)
    target_price = price * (1 + config.TARGET_GAIN_PCT / 100) if price else None
    risk_reward = compute_risk_reward(price, stop_price) if price else None
    entry_quality = compute_entry_quality(hybrid_score, signal_type, risk_reward)
    why_now = build_why_now(h4, h1, signal_type, daily_regime)

    result: dict[str, Any] = {
        "symbol": symbol,
        "signal_type": signal_type,
        "entry_price": price,
        "technical_score": technical_score,
        "ml_probability": ml_prob,
        "hybrid_score": hybrid_score,
        "daily_regime": daily_regime,
        "market_regime": market_regime,
        "trend_structure": h4.get("trend_structure"),
        # 4H
        "volume_ratio": h4.get("volume_ratio"),
        "volume_label": h4.get("volume_label"),
        "rsi_4h": h4.get("rsi_4h"),
        "macd_ok": h4.get("macd_ok"),
        "macd_histogram_rising": h4.get("macd_histogram_rising"),
        "adx_4h": h4.get("adx_4h"),
        "adx_rising": h4.get("adx_rising"),
        "di_plus": h4.get("di_plus"),
        "di_minus": h4.get("di_minus"),
        "obv_above_ema": h4.get("obv_above_ema"),
        "hidden_accumulation": h4.get("hidden_accumulation"),
        "bb_compressed": h4.get("bb_compressed"),
        "atr_declining": h4.get("atr_declining"),
        "distance_to_breakout_pct": h4.get("distance_to_breakout_pct"),
        "is_breakout": h4.get("is_breakout"),
        "overextended": h4.get("overextended"),
        # 1H
        "rsi_1h": h1.get("rsi_1h"),
        "rsi_1h_prev": h1.get("rsi_1h_prev"),
        "trigger_ok": h1.get("trigger_ok"),
        "trigger_strength": h1.get("trigger_strength"),
        # RS
        "rs_4h": rs.get("rs_4h"),
        "rs_rising": rs.get("rs_rising"),
        "rs_strong": rs.get("rs_strong"),
        # Retest
        "retest_entry": retest.get("retest_entry"),
        "retest_confidence": retest.get("retest_confidence"),
        # Risk
        "stop_price": stop_price,
        "target_price": target_price,
        "risk_reward": risk_reward,
        "entry_quality": entry_quality,
        "why_now": why_now,
        "score_components": score_components,
        "qualified": True,
    }

    # ── Record for ML feedback
    try:
        record_signal(symbol, price, ml_features)
    except Exception as exc:
        logger.warning("Could not record signal %s: %s", symbol, exc)

    # ── Open paper trade
    try:
        open_paper_trade(symbol, signal_type, price, stop_price, target_price,
                         technical_score, ml_prob, hybrid_score, ml_features)
    except Exception as exc:
        logger.warning("Paper trade error %s: %s", symbol, exc)

    return result


def _ema10_slope(daily_df) -> float | None:
    from indicators import calculate_ema10_slope_pct
    return calculate_ema10_slope_pct(daily_df, lookback=config.EMA_SLOPE_LOOKBACK)


# ─────────────────────────────────────────────────────────────
# Ranking helpers
# ─────────────────────────────────────────────────────────────

def rank_and_select(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return top N per signal type, sorted by hybrid_score."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        st = r.get("signal_type", SIGNAL_NO_SETUP)
        if st in ALERT_SIGNAL_TYPES:
            buckets[st].append(r)
    for st in buckets:
        buckets[st].sort(key=lambda x: x.get("hybrid_score") or 0, reverse=True)
    selected = []
    priority = [SIGNAL_STRONG_ENTRY, SIGNAL_NEW_ENTRY, SIGNAL_RETEST_ENTRY, SIGNAL_PRE_ENTRY]
    seen = set()
    for st in priority:
        for row in buckets[st][: config.TOP_N_PER_SIGNAL_TYPE]:
            sym = row["symbol"]
            if sym not in seen:
                selected.append(row)
                seen.add(sym)
    return selected


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main() -> int:
    exchange, quote_assets = create_exchange()
    logger.info("Loading %s markets...", exchange.id)
    symbols = get_quote_symbols(exchange, quote_assets)
    logger.info("Found %s active spot USDC symbols.", len(symbols))

    logger.info("TELEGRAM_TOKEN_PRESENT=%s", bool(config.TELEGRAM_TOKEN))
    logger.info("TELEGRAM_CHAT_ID_PRESENT=%s", bool(config.TELEGRAM_CHAT_ID))

    # Load ML model
    ml_bundle = load_model()
    if ml_bundle is not None:
        logger.info("ML model loaded. Gate active (threshold=%.0f%%).", config.ML_MIN_WIN_PROBABILITY * 100)
    else:
        logger.info("No ML model. Rules-only mode.")

    # BTC market regime + BTC candles for RS
    btc_df_4h = None
    btc_df_1d = None
    try:
        btc_4h_raw = with_retries(exchange.fetch_ohlcv, config.MARKET_REGIME_SYMBOL, config.TF_SETUP, limit=config.H4_LIMIT)
        btc_1d_raw = with_retries(exchange.fetch_ohlcv, config.MARKET_REGIME_SYMBOL, config.TF_DAILY, limit=60)
        if btc_4h_raw:
            btc_df_4h = prepare_ohlcv_df(btc_4h_raw)
        if btc_1d_raw:
            btc_df_1d = add_ema_columns(prepare_ohlcv_df(btc_1d_raw))
    except Exception as exc:
        logger.warning("Could not fetch BTC candles: %s", exc)

    market_regime = get_btc_market_regime(exchange, btc_df_1d)
    logger.info("Global market regime (BTC): %s", market_regime)

    # Load previous alert state
    alert_state = get_alert_state(config.LAST_ALERTS_PATH)
    previous_signal_states: dict[str, str] = alert_state.get("signal_states", {})

    all_results: list[dict[str, Any]] = []
    total = len(symbols)
    for idx, symbol in enumerate(symbols, start=1):
        try:
            logger.info("[%s/%s] Analyzing %s", idx, total, symbol)
            diag = analyze_symbol(exchange, symbol, ml_bundle, btc_df_4h, btc_df_1d, market_regime)
            if diag and not diag.get("error"):
                all_results.append(diag)
        except Exception as exc:
            logger.error("Error on %s: %s", symbol, exc)
        time.sleep(0.2)

    qualified = [r for r in all_results if r.get("qualified")]
    logger.info("Qualified signals: %s / %s", len(qualified), total)

    # Select top N per signal type
    to_alert = rank_and_select(qualified)

    # Filter: only send if signal changed or is new
    to_send = []
    new_signal_states: dict[str, str] = {}
    for row in to_alert:
        sym = row["symbol"]
        st = row["signal_type"]
        new_signal_states[sym] = st
        if config.ALERT_ONLY_NEW:
            if has_signal_state_changed(sym, st, previous_signal_states):
                to_send.append(row)
        else:
            to_send.append(row)

    if to_send:
        logger.info("Sending %s Telegram alerts.", len(to_send))
        try:
            send_telegram_alerts(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID, to_send)
        except Exception as exc:
            logger.exception("Telegram error: %s", exc)
    else:
        logger.info("No new/changed signals. Telegram skipped.")

    # Persist state
    try:
        current_symbols = [r["symbol"] for r in to_alert]
        update_alert_state(config.LAST_ALERTS_PATH, current_symbols, new_signal_states)
    except Exception as exc:
        logger.error("Could not update alert state: %s", exc)

    # Summary log
    by_type: dict[str, int] = defaultdict(int)
    for r in all_results:
        by_type[r.get("signal_type", SIGNAL_NO_SETUP)] += 1
    for st, cnt in sorted(by_type.items()):
        logger.info("%s: %s", st, cnt)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logger.exception("Scanner failed: %s", exc)
        raise SystemExit(1)
