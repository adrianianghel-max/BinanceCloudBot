
def print_console_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        logger.info("No symbols matched all filters.")
        return

    print("| Symbol | Price | Daily | EMA10 Slope | Vol 4H | MACD | RSI 1H | Growth Score |")
    print("|---|---:|---|---:|---:|---|---:|---:|")
    for row in rows:
        print(
            f"| {row['symbol']} | {row['price']:.6f} | {row['daily']} | "
            f"{row['ema10_slope']:.3f}% | {row['vol4h']:.2f}x | {row['macd']} | "
            f"{row['rsi_1h']} | {row['growth_score']:.2f}% |"
        )

def main() -> int:
    exchange = create_exchange()
    logger.info("Loading %s markets...", exchange.id)

    symbols = get_usdc_symbols(exchange)
    logger.info("Found %s active %s symbols.", len(symbols), config.QUOTE_ASSET)

    results: list[dict[str, Any]] = []
    for idx, symbol in enumerate(symbols, start=1):
        logger.info("Analyzing [%s/%s] %s", idx, len(symbols), symbol)
        result = analyze_symbol(exchange, symbol)
        if result:
            results.append(result)

    results.sort(key=lambda x: x["growth_score"], reverse=True)
    print_console_table(results)

    top_for_telegram = sorted(results, key=lambda x: (x["ema10_slope"], x["vol4h"]), reverse=True)[:5]
    current_top_symbols = [row["symbol"] for row in top_for_telegram]
    previous_top_symbols = get_alert_state(config.LAST_ALERTS_PATH).get("top_symbols", [])

    should_send = True
    if config.ALERT_ONLY_NEW:
        should_send = should_send_only_new(current_top_symbols, previous_top_symbols)

    if should_send and top_for_telegram:
        sent = send_telegram_message(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID, top_for_telegram)
        if sent:
            update_alert_state(config.LAST_ALERTS_PATH, current_top_symbols)
    else:
        logger.info("No new symbol in Top 5. Telegram alert skipped.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logger.exception("Scanner failed: %s", exc)
        raise
