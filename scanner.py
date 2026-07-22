from __future__ import annotations

import logging
import time
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
    is_daily_bullish,
    prepare_ohlcv_df,
)
from state_manager import (
    get_alert_state,
    should_send_only_new,
    update_alert_state,
)
from telegram_sender import send_telegram_message


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("binance_usdc_scanner")


def build_exchange(exchange_id: str) -> ccxt.Exchange:
    return getattr(ccxt, exchange_id)(
        {
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
    )


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


def get_usdc_symbols(exchange: ccxt.Exchange) -> list[str]:
    markets = with_retries(exchange.load_markets)
    symbols = []

    for symbol, market in markets.items():
        if not market.get("active"):
            continue
        if not market.get("spot"):
            continue
        if market.get("quote") != config.QUOTE_ASSET:
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


def create_exchange() -> ccxt.Exchange:
    exchange_ids = [config.PRIMARY_EXCHANGE_ID, *config.FALLBACK_EXCHANGE_IDS]
    last_error: Exception | None = None

    for exchange_id in exchange_ids:
        exchange = build_exchange(exchange_id)
        try:
            with_retries(exchange.load_markets)
            if exchange_id != config.PRIMARY_EXCHANGE_ID:
                logger.warning("Falling back to %s because Binance global is unavailable.", exchange_id)
            else:
                logger.info("Using primary exchange %s.", exchange_id)
            return exchange
        except ccxt.ExchangeNotAvailable as exc:
            logger.warning("Exchange %s unavailable during market load: %s", exchange_id, exc)
            last_error = exc
        except ccxt.BaseError as exc:
            logger.warning("Exchange %s failed during market load: %s", exchange_id, exc)
            last_error = exc

    assert last_error is not None
    raise last_error


def analyze_symbol(exchange: ccxt.Exchange, symbol: str) -> dict[str, Any] | None:
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

    daily_df = add_ema_columns(prepare_ohlcv_df(daily_raw))
    h4_df = prepare_ohlcv_df(h4_raw)
    h1_df = prepare_ohlcv_df(h1_raw) if h1_raw else None

    daily_strict_ok = is_daily_bullish(daily_df)
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
        and 0 <= distance_to_breakout <= config.NEAR_BREAKOUT_MAX_DISTANCE_PCT
    )
    adx_ok = adx_4h is not None and adx_4h >= config.ADX_MIN

    rsi_current = None
    rsi_ok = True
    vol_up_ok = True
    if config.USE_1H_FILTER and h1_df is not None:
        rsi_current, rsi_previous = calculate_rsi_pair(h1_df, period=config.RSI_PERIOD)
        rsi_ok = (
            rsi_current is not None
            and rsi_previous is not None
            and config.RSI_MIN <= rsi_current <= config.RSI_MAX
            and rsi_current > rsi_previous
        )
        vol_up_ok = len(h1_df) >= 2 and h1_df["volume"].iloc[-1] > h1_df["volume"].iloc[-2]
    elif config.USE_1H_FILTER:
        rsi_ok = False
        vol_up_ok = False

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

    qualified = daily_ok and macd_ok and volume_ok and near_breakout_ok and adx_ok and rsi_ok and vol_up_ok
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
        "daily_ok": daily_ok,
        "ema_slope_ok": ema_slope_ok,
        "volume_ok": volume_ok,
        "rsi_ok": rsi_ok,
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
        "Dist Breakout % | Growth Score |"
    )
    separator = "|---|---:|---|---:|---:|---|---:|---:|"

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
            f"{dist_breakout} | {growth_score} |"
        )


def print_top20_by_score(rows: list[dict[str, Any]]) -> None:
    if not rows:
        logger.info("No rows available for TOP 20 score diagnostic.")
        return

    header = "Symbol | Score | RSI | EMA10 Slope | Volume Ratio | Distance To Breakout"
    separator = "-" * len(header)
    print(header)
    print(separator)
    for row in rows[:20]:
        print(
            f"{row.get('symbol', 'N/A')} | "
            f"{_format_float(row.get('growth_score'), 2)} | "
            f"{row.get('rsi_1h', 'N/A')} | "
            f"{_format_float(row.get('ema10_slope'), 3)} | "
            f"{_format_float(row.get('vol4h'), 2)} | "
            f"{_format_float(row.get('dist_breakout_pct'), 2)}"
        )


def main() -> int:
    exchange = create_exchange()

    logger.info("Loading %s markets...", exchange.id)
    symbols = get_usdc_symbols(exchange)
    logger.info("Found %s active %s symbols.", len(symbols), config.QUOTE_ASSET)

    logger.info("TELEGRAM_TOKEN_PRESENT=%s", bool(config.TELEGRAM_TOKEN))
    logger.info("TELEGRAM_CHAT_ID_PRESENT=%s", bool(config.TELEGRAM_CHAT_ID))

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
        "AFTER_SCORING_FILTER": 0,
        "FINAL_QUALIFIED": 0,
    }

    results: list[dict[str, Any]] = []
    score_pool: list[dict[str, Any]] = []
    skipped_due_to_error = 0
    for idx, symbol in enumerate(symbols, start=1):
        try:
            logger.info("Analyzing [%s/%s] %s", idx, len(symbols), symbol)
            diagnostic = analyze_symbol(exchange, symbol)
            if not diagnostic:
                continue

            if diagnostic.get("error"):
                skipped_due_to_error += 1
                continue

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

    top_for_telegram = sorted(
        results,
        key=lambda x: (x["ema10_slope"], x["vol4h"]),
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
