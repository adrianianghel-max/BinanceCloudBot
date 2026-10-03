from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import ccxt

import config
from indicators import (
    add_ema_columns,
    calculate_adx_value,
    calculate_distance_to_breakout_pct,
    calculate_ema10_slope_pct,
    calculate_growth_score,
    calculate_macd_values,
    calculate_rsi_pair,
    calculate_volume_ratio,
    confirm_breakout,
    detect_accumulation,
    is_daily_bullish,
    is_daily_early_trend,
    prepare_ohlcv_df,
)
from market_data import is_data_fresh
from state_manager import (
    get_alert_state,
    should_send_only_new,
    update_alert_state,
)
from telegram_sender import send_telegram_message
from trader import learned_winner_counts, manage_trading, optimize_daily, setup_file_logging


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("binance_usdc_scanner")


def build_exchange(exchange_id: str) -> ccxt.Exchange:
    exchange_params: dict[str, Any] = {
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    }
    if config.PROXY_URL:
        exchange_params["proxies"] = {
            "http": config.PROXY_URL,
            "https": config.PROXY_URL,
        }
        logger.info("Using proxy for %s.", exchange_id)
    return getattr(ccxt, exchange_id)(exchange_params)


def with_retries(func, *args, **kwargs):
    delay = config.INITIAL_RETRY_DELAY
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except (ccxt.RateLimitExceeded, ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
            if attempt == config.MAX_RETRIES:
                raise
            logger.warning(
                "Retryable error on attempt %s/%s: %s. Retrying in %.1fs.",
                attempt,
                config.MAX_RETRIES,
                exc,
                delay,
            )
            time.sleep(delay)
            delay *= 2


def is_leveraged_base(base_asset: str) -> bool:
    upper = base_asset.upper()
    return any(upper.endswith(marker) for marker in config.LEVERAGED_TOKENS)


def get_quote_symbols(exchange: ccxt.Exchange, quote_assets: tuple[str, ...]) -> list[str]:
    markets = with_retries(exchange.load_markets)
    symbols = []
    allowed_quotes = {q.upper() for q in quote_assets}

    for symbol, market in markets.items():
        if not market.get("active"):
            continue
        if not market.get("spot"):
            continue
        quote = str(market.get("quote", "")).upper()
        if quote not in allowed_quotes:
            continue

        base = market.get("base", "")
        if is_leveraged_base(base):
            continue

        symbols.append(symbol)

    return sorted(symbols)


def _format_float(value: Any, precision: int, suffix: str = "") -> str:
    if value is None:
        value = 0
    try:
        return f"{float(value):.{precision}f}{suffix}"
    except (TypeError, ValueError):
        return f"0.{('0' * precision)}{suffix}"


def create_exchange() -> tuple[ccxt.Exchange, tuple[str, ...]]:
    exchange_ids = [config.PRIMARY_EXCHANGE_ID, *config.FALLBACK_EXCHANGE_IDS]
    last_error: Exception | None = None

    for exchange_id in exchange_ids:
        exchange = build_exchange(exchange_id)
        try:
            with_retries(exchange.load_markets)
            if exchange_id != config.PRIMARY_EXCHANGE_ID:
                logger.warning("Falling back to %s because Binance global is unavailable.", exchange_id)
                return exchange, config.FALLBACK_QUOTE_ASSETS
            else:
                logger.info("Using primary exchange %s.", exchange_id)
                return exchange, config.PRIMARY_QUOTE_ASSETS
        except ccxt.ExchangeNotAvailable as exc:
            logger.warning("Exchange %s unavailable during market load: %s", exchange_id, exc)
            last_error = exc
        except ccxt.BaseError as exc:
            logger.warning("Exchange %s failed during market load: %s", exchange_id, exc)
            last_error = exc

    assert last_error is not None
    raise last_error


def _closed_candles(df, timeframe: str, now: datetime):
    interval = {
        "1d": timedelta(days=1),
        "4h": timedelta(hours=4),
        "1h": timedelta(hours=1),
        "15m": timedelta(minutes=15),
        "5m": timedelta(minutes=5),
    }[timeframe]
    return df.loc[df["timestamp"] + interval <= now].reset_index(drop=True)


def analyze_symbol(
    exchange: ccxt.Exchange,
    symbol: str,
    winner_counts: dict[str, int] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    now = now or datetime.now(timezone.utc)
    winner_counts = winner_counts or {}
    try:
        daily_raw = with_retries(exchange.fetch_ohlcv, symbol, "1d", limit=config.DAILY_LIMIT)
        h4_raw = with_retries(exchange.fetch_ohlcv, symbol, "4h", limit=config.H4_LIMIT)
        h1_raw = with_retries(exchange.fetch_ohlcv, symbol, "1h", limit=config.H1_LIMIT)
    except ccxt.BaseError as exc:
        logger.warning("Skipping %s after exchange error: %s", symbol, exc)
        return {
            "symbol": symbol,
            "error": str(exc),
        }

    if not daily_raw or not h4_raw:
        return {
            "symbol": symbol,
            "error": "Missing OHLCV data",
        }

    daily_df = add_ema_columns(_closed_candles(prepare_ohlcv_df(daily_raw), "1d", now))
    h4_df = _closed_candles(prepare_ohlcv_df(h4_raw), "4h", now)
    h1_df = _closed_candles(prepare_ohlcv_df(h1_raw), "1h", now) if h1_raw else None
    if daily_df.empty or h4_df.empty or (config.USE_1H_FILTER and (h1_df is None or h1_df.empty)):
        return {"symbol": symbol, "error": "No completed OHLCV candles"}

    accumulation_timeframes = [
        timeframe
        for timeframe, frame in (("1d", daily_df), ("4h", h4_df), ("1h", h1_df))
        if frame is not None and detect_accumulation(
            frame,
            volume_acceleration_min=config.ACCUMULATION_VOLUME_ACCEL_MIN,
            max_price_change_pct=config.ACCUMULATION_MAX_PRICE_CHANGE_PCT,
        )
    ]
    breakout_15m_ok = False
    breakout_5m_ok = False
    if accumulation_timeframes:
        for timeframe, limit, result_key in (
            ("15m", config.H15_LIMIT, "15m"),
            ("5m", config.M5_LIMIT, "5m"),
        ):
            try:
                raw = with_retries(exchange.fetch_ohlcv, symbol, timeframe, limit=limit)
                if raw:
                    frame = prepare_ohlcv_df(raw)
                    if not is_data_fresh(frame, timeframe):
                        logger.warning("Stale %s data for %s; breakout not confirmed.", timeframe, symbol)
                        continue
                    frame = _closed_candles(frame, timeframe, now)
                    confirmed = confirm_breakout(
                        frame,
                        lookback=config.LOW_TIMEFRAME_BREAKOUT_LOOKBACK,
                        volume_ratio_min=config.LOW_TIMEFRAME_BREAKOUT_VOLUME_RATIO,
                    )
                    if result_key == "15m":
                        breakout_15m_ok = confirmed
                    else:
                        breakout_5m_ok = confirmed
            except ccxt.BaseError as exc:
                logger.warning("Could not check %s breakout for %s: %s", timeframe, symbol, exc)
    explosion_confirmed = breakout_15m_ok or breakout_5m_ok
    winner_days = winner_counts.get(symbol, 0)

    daily_strict_ok = is_daily_bullish(daily_df)
    if config.ALLOW_EARLY_TREND:
        daily_ok = daily_strict_ok or is_daily_early_trend(daily_df)
    else:
        daily_ok = daily_strict_ok

    ema10_slope = calculate_ema10_slope_pct(daily_df, lookback=config.EMA_SLOPE_LOOKBACK)
    ema_slope_ok = ema10_slope is not None and ema10_slope >= config.MIN_EMA10_SLOPE_PCT

    macd_line, signal_line = calculate_macd_values(h4_df)
    volume_ratio = calculate_volume_ratio(h4_df, period=config.VOLUME_SMA_PERIOD)
    distance_to_breakout = calculate_distance_to_breakout_pct(h4_df, lookback=config.BREAKOUT_LOOKBACK_4H)
    adx_4h = calculate_adx_value(h4_df, period=config.ADX_PERIOD)

    macd_spread_ratio = None
    macd_ok = False
    if macd_line is not None and signal_line is not None:
        macd_spread_ratio = (macd_line - signal_line) / max(abs(signal_line), abs(macd_line), 1e-8)
        macd_ok = macd_line > signal_line and macd_spread_ratio >= config.MIN_MACD_SPREAD_RATIO

    volume_ok = volume_ratio is not None and volume_ratio >= config.VOLUME_RATIO_THRESHOLD
    near_breakout_ok = (
        distance_to_breakout is not None
        and -config.BREAKOUT_ALLOW_OVERSHOOT_PCT <= distance_to_breakout <= config.NEAR_BREAKOUT_MAX_DISTANCE_PCT
    )
    adx_ok = adx_4h is not None and adx_4h >= config.ADX_MIN

    rsi_current = None
    rsi_ok = True
    vol_up_ok = True
    rsi_rising_ok = True
    if config.USE_1H_FILTER and h1_df is not None:
        rsi_current, rsi_previous = calculate_rsi_pair(h1_df, period=config.RSI_PERIOD)
        rsi_rising_ok = rsi_current is not None and rsi_previous is not None and rsi_current > rsi_previous
        rsi_ok = (
            rsi_current is not None
            and rsi_previous is not None
            and config.RSI_MIN <= rsi_current <= config.RSI_MAX
        )
        vol_up_ok = len(h1_df) >= 2 and h1_df["volume"].iloc[-1] > h1_df["volume"].iloc[-2]
    elif config.USE_1H_FILTER:
        rsi_ok = False
        vol_up_ok = False
        rsi_rising_ok = False

    score = None
    if (
        ema10_slope is not None
        and macd_spread_ratio is not None
        and volume_ratio is not None
        and distance_to_breakout is not None
    ):
        score = calculate_growth_score(
            ema10_slope_pct=ema10_slope,
            macd_spread_ratio=macd_spread_ratio,
            volume_ratio=volume_ratio,
            distance_to_breakout_pct=distance_to_breakout,
            rsi_value=rsi_current,
            use_1h_filter=config.USE_1H_FILTER,
        )
        score = min(
            100.0,
            score + min(winner_days * config.WINNER_SCORE_BONUS, config.WINNER_SCORE_BONUS_MAX),
        )

    qualified = (
        daily_ok
        and macd_ok
        and volume_ok
        and near_breakout_ok
        and adx_ok
        and rsi_ok
        and bool(accumulation_timeframes)
        and explosion_confirmed
    )
    price = None
    if qualified:
        ticker = with_retries(exchange.fetch_ticker, symbol)
        price = float(ticker.get("last") or daily_df["close"].iloc[-1])

    data = {
        "symbol": symbol,
        "price": price,
        "daily": "BULLISH" if daily_strict_ok else "NEUTRAL",
        "ema10_slope": ema10_slope,
        "vol4h": volume_ratio,
        "macd": f"{macd_line:.5f}/{signal_line:.5f}" if macd_line is not None and signal_line is not None else "N/A",
        "macd_spread_ratio": macd_spread_ratio,
        "rsi_1h": f"{rsi_current:.2f}" if rsi_current is not None else "N/A",
        "dist_breakout_pct": distance_to_breakout,
        "adx_4h": adx_4h,
        "growth_score": score,
        "accumulation_count": len(accumulation_timeframes),
        "accumulation_timeframes": accumulation_timeframes,
        "breakout_15m_ok": breakout_15m_ok,
        "breakout_5m_ok": breakout_5m_ok,
        "explosion_confirmed": explosion_confirmed,
        "winner_days": winner_days,
        "daily_ok": daily_ok,
        "ema_slope_ok": ema_slope_ok,
        "volume_ok": volume_ok,
        "rsi_ok": rsi_ok,
        "rsi_rising_ok": rsi_rising_ok,
        "macd_ok": macd_ok,
        "adx_ok": adx_ok,
        "near_breakout_ok": near_breakout_ok,
        "vol_up_ok": vol_up_ok,
        "qualified": qualified,
    }

    return data


def print_console_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        logger.info("No symbols matched all filters.")
        return

    header = (
        "| Symbol | Price | RSI | MACD | EMA10 Slope | Vol Ratio | "
        "Dist Breakout % | Accumulations | Explosion | Growth Score |"
    )
    separator = "|---|---:|---|---:|---:|---|---:|---:|---|---:|"

    print(header)
    print(separator)
    for row in rows:
        price = _format_float(row.get("price"), 6)
        ema10_slope = _format_float(row.get("ema10_slope"), 3, "%")
        vol4h = _format_float(row.get("vol4h"), 2, "x")
        dist_breakout = _format_float(row.get("dist_breakout_pct"), 2, "%")
        growth_score = _format_float(row.get("growth_score"), 2, "%")
        print(
            f"| {row.get('symbol', 'N/A')} | {price} | {row.get('rsi_1h', 'N/A')} | "
            f"{row.get('macd', 'N/A')} | {ema10_slope} | {vol4h} | "
            f"{dist_breakout} | {row.get('accumulation_count', 0)} | "
            f"{'YES' if row.get('explosion_confirmed') else 'NO'} | {growth_score} |"
        )


def print_top20_by_score(rows: list[dict[str, Any]]) -> None:
    if not rows:
        logger.info("No rows available for TOP 20 score diagnostic.")
        return

    header = "Symbol | Score | Accumulation | Explosion | Winner Days | RSI | EMA10 Slope | Volume Ratio | Distance To Breakout"
    separator = "-" * len(header)
    print(header)
    print(separator)
    for row in rows[:20]:
        print(
            f"{row.get('symbol', 'N/A')} | "
            f"{_format_float(row.get('growth_score'), 2)} | "
            f"{row.get('accumulation_count', 0)}/3 | "
            f"{'15m' if row.get('breakout_15m_ok') else ''}"
            f"{'+' if row.get('breakout_15m_ok') and row.get('breakout_5m_ok') else ''}"
            f"{'5m' if row.get('breakout_5m_ok') else ('N/A' if not row.get('explosion_confirmed') else '')} | "
            f"{row.get('winner_days', 0)} | "
            f"{row.get('rsi_1h', 'N/A')} | "
            f"{_format_float(row.get('ema10_slope'), 3)} | "
            f"{_format_float(row.get('vol4h'), 2)} | "
            f"{_format_float(row.get('dist_breakout_pct'), 2)}"
        )


def main() -> int:
    now_utc = datetime.now(timezone.utc)
    setup_file_logging()

    exchange, quote_assets = create_exchange()

    logger.info("Loading %s markets...", exchange.id)
    logger.info("Scanning quote assets: %s", ",".join(quote_assets))
    symbols = get_quote_symbols(exchange, quote_assets)
    logger.info("Found %s active spot symbols in selected quotes.", len(symbols))

    logger.info("TELEGRAM_TOKEN_PRESENT=%s", bool(config.TELEGRAM_TOKEN))
    logger.info("TELEGRAM_CHAT_ID_PRESENT=%s", bool(config.TELEGRAM_CHAT_ID))

    # 📅 Recalibrare zilnică (backtest pe ziua anterioară + optimizare parametri).
    # Rulează o singură dată pe zi și aplică parametrii prin optimizer pe config,
    # astfel încât scanarea de azi folosește parametrii optimizați.
    try:
        optimize_daily(exchange, symbols, now_utc)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Recalibrare zilnică eșuată: %s", exc)
    winner_counts = learned_winner_counts()
    logger.info(
        "Recent +%s%% winner symbols learned: %s",
        config.TAKE_PROFIT_PCT * 100,
        winner_counts,
    )

    counters = {
        "TOTAL_SYMBOLS": len(symbols),
        "AFTER_USDC_FILTER": len(symbols),
        "AFTER_VOLUME_FILTER": 0,
        "AFTER_EMA_FILTER": 0,
        "AFTER_EMA_SLOPE_FILTER": 0,
        "AFTER_RSI_FILTER": 0,
        "AFTER_MACD_FILTER": 0,
        "AFTER_ADX_FILTER": 0,
        "AFTER_BREAKOUT_FILTER": 0,
        "AFTER_ACCUMULATION_FILTER": 0,
        "AFTER_EXPLOSION_CONFIRMATION": 0,
        "AFTER_SCORING_FILTER": 0,
        "FINAL_QUALIFIED": 0,
    }

    results: list[dict[str, Any]] = []
    score_pool: list[dict[str, Any]] = []
    skipped_due_to_error = 0
    for idx, symbol in enumerate(symbols, start=1):
        try:
            logger.info("Analyzing [%s/%s] %s", idx, len(symbols), symbol)
            diagnostic = analyze_symbol(exchange, symbol, winner_counts, now_utc)
            if not diagnostic:
                continue

            if diagnostic.get("error"):
                skipped_due_to_error += 1
                continue
            logger.info(
                "%s accumulation=%d/3 (%s), breakout_confirmed=%s",
                symbol,
                diagnostic.get("accumulation_count", 0),
                ",".join(diagnostic.get("accumulation_timeframes", [])) or "none",
                diagnostic.get("explosion_confirmed", False),
            )

            if diagnostic.get("growth_score") is not None:
                score_pool.append(diagnostic)

            if not diagnostic.get("volume_ok", False):
                continue
            counters["AFTER_VOLUME_FILTER"] += 1

            if not diagnostic.get("daily_ok", False):
                continue
            counters["AFTER_EMA_FILTER"] += 1

            if not diagnostic.get("ema_slope_ok", False):
                continue
            counters["AFTER_EMA_SLOPE_FILTER"] += 1

            if not diagnostic.get("rsi_ok", False):
                continue
            counters["AFTER_RSI_FILTER"] += 1

            if not diagnostic.get("macd_ok", False):
                continue
            counters["AFTER_MACD_FILTER"] += 1

            if not diagnostic.get("adx_ok", False):
                continue
            counters["AFTER_ADX_FILTER"] += 1

            if not diagnostic.get("near_breakout_ok", False):
                continue
            counters["AFTER_BREAKOUT_FILTER"] += 1

            if not diagnostic.get("accumulation_count", 0):
                continue
            counters["AFTER_ACCUMULATION_FILTER"] += 1

            if not diagnostic.get("explosion_confirmed", False):
                continue
            counters["AFTER_EXPLOSION_CONFIRMATION"] += 1

            if diagnostic.get("growth_score") is None:
                continue
            counters["AFTER_SCORING_FILTER"] += 1

            if not diagnostic.get("vol_up_ok", False):
                continue

            if diagnostic.get("qualified", False):
                results.append(diagnostic)
                counters["FINAL_QUALIFIED"] += 1
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Error on %s: %s", symbol, exc)
            continue

        time.sleep(0.2)

    for key, value in counters.items():
        logger.info("%s=%s", key, value)
    logger.info("SKIPPED_DUE_TO_ERROR=%s", skipped_due_to_error)

    score_pool.sort(key=lambda x: x.get("growth_score") or 0.0, reverse=True)
    print_top20_by_score(score_pool)

    results.sort(key=lambda x: x["growth_score"] or 0.0, reverse=True)
    print_console_table(results[: config.CONSOLE_TOP_N])

    # 💼 PAPER TRADING — gestionează pozițiile (TP/SL/trailing/EOD) și deschide
    #    altele noi (max 2 simultan, 50 USDC fiecare). Numai simulare — fără ordine reale.
    try:
        manage_trading(exchange, score_pool, now_utc)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Trading cycle eșuat: %s", exc)

    # TOP 5 candidați după scorul de creștere (din score_pool, nu doar cei fully-qualified)
    top_for_telegram = sorted(
        score_pool,
        key=lambda x: x.get("growth_score") or 0.0,
        reverse=True,
    )[:5]

    current_top_symbols = [row["symbol"] for row in top_for_telegram]
    alert_state = get_alert_state(config.LAST_ALERTS_PATH)
    previous_top_symbols = alert_state.get("top_symbols", [])

    should_send = True
    if config.ALERT_ONLY_NEW:
        should_send = should_send_only_new(current_top_symbols, previous_top_symbols)

    if should_send and top_for_telegram:
        try:
            sent = send_telegram_message(
                token=config.TELEGRAM_TOKEN,
                chat_id=config.TELEGRAM_CHAT_ID,
                rows=top_for_telegram,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Telegram error: %s", exc)
            sent = False
        if sent:
            try:
                update_alert_state(config.LAST_ALERTS_PATH, current_top_symbols)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Could not update alert state: %s", exc)
    else:
        logger.info("No new symbol in Top 5. Telegram alert skipped.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Scanner failed: %s", exc)
        raise SystemExit(0)
