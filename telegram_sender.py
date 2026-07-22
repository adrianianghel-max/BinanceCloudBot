from __future__ import annotations

import logging
from typing import Sequence

import requests


logger = logging.getLogger(__name__)


def _build_telegram_table(rows: Sequence[dict]) -> str:
    header = "Symbol | Price | Daily | EMA10 Slope | Vol 4H | MACD | RSI 1H | Growth"
    separator = "-" * len(header)
    lines = [header, separator]

    for row in rows:
        lines.append(
            f"{row['symbol']} | {row['price']:.6f} | {row['daily']} | "
            f"{row['ema10_slope']:.3f}% | {row['vol4h']:.2f}x | {row['macd']} | "
            f"{row['rsi_1h']} | {row['growth_score']:.2f}%"
        )

    return "\n".join(lines)


def send_telegram_message(token: str, chat_id: str, rows: Sequence[dict]) -> bool:
    if not token or not chat_id:
        logger.warning("Telegram token/chat id missing. Skipping Telegram notification.")
        return False

    if not rows:
        logger.info("No rows to send to Telegram.")
        return False

    message = "Top 5 Binance USDC candidates\n\n" + "<pre>" + _build_telegram_table(rows) + "</pre>"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, data=payload, timeout=20)
        response.raise_for_status()
        logger.info("Telegram notification sent successfully.")
        return True
    except requests.RequestException as exc:
        response_text = ""
        if getattr(exc, "response", None) is not None:
            response_text = getattr(exc.response, "text", "")
        if response_text:
            logger.error("Failed to send Telegram message: %s | Response: %s", exc, response_text)
        else:
            logger.error("Failed to send Telegram message: %s", exc)
        return False
