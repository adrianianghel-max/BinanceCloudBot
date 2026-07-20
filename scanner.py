from __future__ import annotations

import logging
import time
from typing import Any

import ccxt

import config
from indicators import (
    add_ema_columns,
    calculate_ema10_slope_pct,
    calculate_growth_score,
    calculate_macd_values,
    calculate_rsi_pair,
    calculate_volume_ratio,
    is_4h_breakout,
    is_daily_bullish,
    is_daily_early_trend,
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
        return None

    if not daily_raw or not h4_raw:
        return None

    daily_df = add_ema_columns(prepare_ohlcv_df(daily_raw))
    h4_df = prepare_ohlcv_df(h4_raw)
    h1_df = prepare_ohlcv_df(h1_raw) if h1_raw else None

    daily_strict_ok = is_daily_bullish(daily_df)
    daily_early_ok = is_daily_early_trend(daily_df, ema50_lookback=config.EMA_MID_SLOPE_LOOKBACK)
    daily_ok = daily_strict_ok or (config.ALLOW_EARLY_TREND and daily_early_ok)

    ema10_slope = calculate_ema10_slope_pct(daily_df, lookback=config.EMA_SLOPE_LOOKBACK)
    if ema10_slope is None:
        return None
    if ema10_slope < config.MIN_EMA10_SLOPE_PCT:
        return None

    macd_line, signal_line = calculate_macd_values(h4_df)
    volume_ratio = calculate_volume_ratio(h4_df, period=config.VOLUME_SMA_PERIOD)
    if macd_line is None or signal_line is None or volume_ratio is None:
        return None

    macd_spread_ratio = (macd_line - signal_line) / max(abs(signal_line), abs(macd_line), 1e-8)
    macd_ok = macd_line > signal_line and macd_spread_ratio >= config.MIN_MACD_SPREAD_RATIO
    volume_ok = volume_ratio > config.VOLUME_RATIO_THRESHOLD
    breakout_ok = True
    if config.USE_4H_BREAKOUT_FILTER:
        breakout_ok = is_4h_breakout(h4_df, lookback=config.BREAKOUT_LOOKBACK_4H)

    rsi_current = None
    rsi_ok = True
    vol_up_ok = True
    if config.USE_1H_FILTER and h1_df is not None:
        rsi_current, rsi_previous = calculate_rsi_pair(h1_df, period=config.RSI_PERIOD)
        if rsi_current is None or rsi_previous is None:
            return None

        rsi_ok = (
            config.RSI_MIN <= rsi_current <= config.RSI_MAX
            and rsi_current > rsi_previous
        )
        vol_up_ok = h1_df["volume"].iloc[-1] > h1_df["volume"].iloc[-2]

    qualified = daily_ok and macd_ok and volume_ok and breakout_ok and rsi_ok and vol_up_ok
    if not qualified:
        return None

    ticker = with_retries(exchange.fetch_ticker, symbol)
    price = float(ticker.get("last") or daily_df["close"].iloc[-1])

    growth_score = calculate_growth_score(
        ema10_slope_pct=ema10_slope,
        macd_line=macd_line,
        signal_line=signal_line,
        volume_ratio=volume_ratio,
        rsi_value=rsi_current,
        use_1h_filter=config.USE_1H_FILTER,
    )

    return {
        "symbol": symbol,
        "price": price,
        "daily": "BULLISH" if daily_strict_ok else "EARLY_TREND",
        "ema10_slope": ema10_slope,
        "vol4h": volume_ratio,
        "macd": f"{macd_line:.5f}/{signal_line:.5f}",
        "rsi_1h": f"{rsi_current:.2f}" if rsi_current is not None else "N/A",
        "growth_score": growth_score,
    }


def print_console_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        logger.info("No symbols matched all filters.")
        return

    header = (
        "| Symbol | Price | Daily | EMA10 Slope | Vol 4H | MACD | "
        "RSI 1H | Growth Score |"
    )
    separator = "|---|---:|---|---:|---:|---|---:|---:|"

    print(header)
    print(separator)
    for row in rows:
        price = _format_float(row.get("price"), 6)
        ema10_slope = _format_float(row.get("ema10_slope"), 3, "%")
        vol4h = _format_float(row.get("vol4h"), 2, "x")
        growth_score = _format_float(row.get("growth_score"), 2, "%")
        print(
            f"| {row.get('symbol', 'N/A')} | {price} | {row.get('daily', 'N/A')} | "
            f"{ema10_slope} | {vol4h} | {row.get('macd', 'N/A')} | "
            f"{row.get('rsi_1h', 'N/A')} | {growth_score} |"
        )


def main() -> int:
    exchange = create_exchange()

    logger.info("Loading %s markets...", exchange.id)
    symbols = get_usdc_symbols(exchange)
    logger.info("Found %s active %s symbols.", len(symbols), config.QUOTE_ASSET)

    results: list[dict[str, Any]] = []
    for idx, symbol in enumerate(symbols, start=1):
        try:
            logger.info("Analyzing [%s/%s] %s", idx, len(symbols), symbol)
            result = analyze_symbol(exchange, symbol)
            if result:
                results.append(result)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Error on %s: %s", symbol, exc)
            continue

        time.sleep(0.2)

    results.sort(key=lambda x: x["growth_score"], reverse=True)
    print_console_table(results)

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
            logger.error("Telegram error: %s", exc)
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
