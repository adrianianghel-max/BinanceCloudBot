"""telegram_sender.py — v2.0 rich alert format."""
from __future__ import annotations

import logging
from typing import Sequence

import requests

logger = logging.getLogger(__name__)

BINANCE_TRADE_URL = "https://www.binance.com/en/trade/{base}_{quote}"

SIGNAL_EMOJI = {
    "STRONG_ENTRY": "🔥",
    "NEW_ENTRY": "🚨",
    "RETEST_ENTRY": "🔄",
    "PRE_ENTRY": "👀",
    "WATCH": "⏳",
    "NO_SETUP": "❌",
}


def format_symbol_no_slash(symbol: str) -> str:
    return symbol.replace("/", "")


def _binance_url_for_symbol(symbol: str) -> str:
    base, quote = symbol.split("/")
    return BINANCE_TRADE_URL.format(base=base, quote=quote)


def _fmt(val, precision: int = 2, suffix: str = "") -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.{precision}f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def _arrow(val, prev=None) -> str:
    if val is None:
        return ""
    if prev is not None:
        return " ↑" if val > prev else " ↓"
    return ""


def _build_alert_message(row: dict) -> str:
    signal_type = row.get("signal_type", "UNKNOWN")
    emoji = SIGNAL_EMOJI.get(signal_type, "📊")
    symbol_clean = format_symbol_no_slash(row.get("symbol", "???"))

    entry = _fmt(row.get("entry_price"), 6)
    tech = _fmt(row.get("technical_score"), 1)
    ml = _fmt((row.get("ml_probability") or 0) * 100, 1, "%") if row.get("ml_probability") is not None else "N/A"
    hybrid = _fmt(row.get("hybrid_score"), 1)
    daily = row.get("daily_regime", "N/A")
    trend_struct = row.get("trend_structure", "N/A")
    vol_ratio = _fmt(row.get("volume_ratio"), 2, "x")
    rsi_4h = _fmt(row.get("rsi_4h"), 1)
    rsi_1h_val = row.get("rsi_1h")
    rsi_1h_prev = row.get("rsi_1h_prev")
    rsi_1h = _fmt(rsi_1h_val, 1) + _arrow(rsi_1h_val, rsi_1h_prev)
    macd_ok = "BULLISH ↑" if row.get("macd_ok") and row.get("macd_histogram_rising") else ("BULLISH" if row.get("macd_ok") else "NEUTRAL")
    adx_val = row.get("adx_4h")
    adx_prev = None
    adx_str = _fmt(adx_val, 1) + (" ↑" if row.get("adx_rising") else "")
    di_plus = _fmt(row.get("di_plus"), 1)
    di_minus = _fmt(row.get("di_minus"), 1)
    obv_label = "ACCUMULATION" if row.get("hidden_accumulation") else ("BULLISH" if row.get("obv_above_ema") else "NEUTRAL")
    bb_label = "COMPRESSED → EXPANDING" if row.get("bb_compressed") else "EXPANDING"
    atr_label = "COMPRESSION" if row.get("atr_declining") else "NORMAL"
    dist = _fmt(row.get("distance_to_breakout_pct"), 2, "%")
    rs_val = row.get("rs_4h")
    rs_str = _fmt(rs_val, 2) + (" ↑" if row.get("rs_rising") else "") if rs_val is not None else "N/A"
    stop = _fmt(row.get("stop_price"), 6)
    target = _fmt(row.get("target_price"), 6)
    rr = _fmt(row.get("risk_reward"), 2)
    quality = row.get("entry_quality", "N/A")

    why_now = row.get("why_now", [])
    why_lines = "\n".join(f"• {r}" for r in why_now) if why_now else "• Analiza tehnica completa"

    lines = [
        f"{emoji} <b>{signal_type} — {symbol_clean}</b>",
        "",
        f"Signal: <b>{signal_type}</b>",
        f"Entry: <b>{entry}</b>",
        "",
        f"Technical Score: <b>{tech}/100</b>",
        f"ML Winner Probability: <b>{ml}</b>",
        f"Hybrid Score: <b>{hybrid}</b>",
        "",
        f"Daily: <b>{daily}</b>",
        f"4H: <b>{trend_struct}</b>",
        f"1H: {'CONFIRMAT ✅' if row.get('trigger_ok') else 'PARTIAL'}",
        "",
        f"Volume 4H: <b>{vol_ratio}</b>",
        f"ADX: <b>{adx_str}</b>",
        f"DI+: {di_plus}",
        f"DI-: {di_minus}",
        f"MACD: {macd_ok}",
        f"OBV: {obv_label}",
        f"BB: {bb_label}",
        f"ATR: {atr_label}",
        f"RS vs BTC: {rs_str}",
        "",
        f"Resistance: +{dist}",
        f"RSI 4H: {rsi_4h}",
        f"RSI 1H: {rsi_1h}",
        "",
        f"Stop: {stop}",
        f"Target +8%: {target}",
        "",
        f"<b>WHY NOW:</b>",
        why_lines,
        "",
        f"Entry Quality: {quality}",
        f"R/R: {rr}",
    ]
    return "\n".join(lines)


def _build_inline_keyboard(rows: Sequence[dict]) -> list[list[dict]]:
    keyboard = []
    for row in rows:
        symbol = row.get("symbol", "")
        if "/" not in symbol:
            continue
        symbol_clean = format_symbol_no_slash(symbol)
        keyboard.append([{
            "text": f"🔗 {symbol_clean} pe Binance",
            "url": _binance_url_for_symbol(symbol),
        }])
    return keyboard


def send_telegram_alerts(token: str, chat_id: str, rows: Sequence[dict]) -> bool:
    """Send one Telegram message per qualifying signal row."""
    if not token or not chat_id:
        logger.warning("Telegram token/chat id missing. Skipping notification.")
        return False
    if not rows:
        logger.info("No signals to send to Telegram.")
        return False

    success = False
    for row in rows:
        message = _build_alert_message(row)
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        symbol = row.get("symbol", "")
        keyboard = [[{
            "text": f"🔗 {format_symbol_no_slash(symbol)} pe Binance",
            "url": _binance_url_for_symbol(symbol),
        }]] if "/" in symbol else []
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": keyboard},
        }
        try:
            response = requests.post(url, json=payload, timeout=20)
            response.raise_for_status()
            logger.info("Telegram alert sent: %s [%s]", symbol, row.get("signal_type"))
            success = True
        except requests.RequestException as exc:
            resp_text = ""
            if getattr(exc, "response", None) is not None:
                resp_text = getattr(exc.response, "text", "")
            if resp_text:
                logger.error("Telegram send failed: %s | Response: %s", exc, resp_text)
            else:
                logger.error("Telegram send failed: %s", exc)
    return success


# Legacy compat for old scanner
def send_telegram_message(token: str, chat_id: str, rows: Sequence[dict]) -> bool:
    return send_telegram_alerts(token, chat_id, rows)
