from __future__ import annotations

import logging
from typing import Sequence

import requests


logger = logging.getLogger(__name__)

BINANCE_TRADE_URL = "https://www.binance.com/en/trade/{base}_{quote}"


def format_symbol_no_slash(symbol: str) -> str:
    """Converts '1000CAT/USDC' to '1000CATUSDC'."""
    return symbol.replace("/", "")


def _binance_url_for_symbol(symbol: str) -> str:
    base, quote = symbol.split("/")
    return BINANCE_TRADE_URL.format(base=base, quote=quote)


def _build_telegram_top5(rows: Sequence[dict]) -> str:
    lines = ["🚀 TOP 5 USDC - BREAKOUT ~1h", ""]
    for idx, row in enumerate(rows, start=1):
        symbol_clean = format_symbol_no_slash(row["symbol"])
        golden_cross_flag = "✅ Golden Cross EMA9>EMA21" if row.get("golden_cross_ok") else ""
        golden_line = f"\n   {golden_cross_flag}" if golden_cross_flag else ""

        ml_prob = row.get("ml_prob")
        ml_line = ""
        if ml_prob is not None:
            ml_emoji = "🤖" if ml_prob >= 0.6 else "⚠️"
            ml_line = f"\n   {ml_emoji} ML Win Prob: <b>{ml_prob * 100:.1f}%</b>"

        final_score = row.get("final_score") or row.get("growth_score") or 0
        lines.append(
            f"{idx}. <b>{symbol_clean}</b>\n"
            f"   Scor: <b>{final_score:.2f}%</b> | "
            f"RSI: {row.get('rsi_1h', 'N/A')} | "
            f"EMA10: {row.get('ema10_slope', 0):.2f}% | "
            f"Vol: {row.get('vol4h', 0):.2f}x | "
            f"Dist breakout: {row.get('dist_breakout_pct', 0):.2f}%"
            f"{ml_line}"
            f"{golden_line}"
        )
    return "\n\n".join(lines)


def _build_inline_keyboard(rows: Sequence[dict]) -> list[list[dict]]:
    keyboard = []
    for row in rows:
        symbol_clean = format_symbol_no_slash(row["symbol"])
        keyboard.append(
            [
                {
                    "text": f"🔗 {symbol_clean} pe Binance",
                    "url": _binance_url_for_symbol(row["symbol"]),
                }
            ]
        )
    return keyboard


def send_telegram_message(token: str, chat_id: str, rows: Sequence[dict]) -> bool:
    if not token or not chat_id:
        logger.warning("Telegram token/chat id missing. Skipping Telegram notification.")
        return False

    if not rows:
        logger.info("No rows to send to Telegram.")
        return False

    message = _build_telegram_top5(rows)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": _build_inline_keyboard(rows)},
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        logger.info("Telegram notification sent successfully with Binance links.")
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