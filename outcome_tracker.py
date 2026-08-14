"""outcome_tracker.py — Phase 1 & 2: signal history + outcome labeling.

At scan time  : call ``record_signal`` for each qualified symbol.
At review time: call ``evaluate_pending_signals`` to fetch real prices
                and write labeled rows to feedback_log.json.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

SIGNALS_HISTORY_PATH = os.getenv("SIGNALS_HISTORY_PATH", "signals_history.json")
FEEDBACK_LOG_PATH = os.getenv("FEEDBACK_LOG_PATH", "feedback_log.json")

# A signal is a WINNER if price rises by at least this % within the horizon
WIN_THRESHOLD_PCT = float(os.getenv("WIN_THRESHOLD_PCT", "5.0"))

# How many seconds after signal we consider the trade "closed" for labeling
OUTCOME_HORIZONS_S = [
    4 * 3600,   # 4h
    8 * 3600,   # 8h
    24 * 3600,  # 24h
]


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)


# ─────────────────────────────────────────────
# Phase 1 — record a qualified signal
# ─────────────────────────────────────────────

def record_signal(symbol: str, price: float, features: dict[str, Any]) -> None:
    """Append a new signal to signals_history.json.

    ``features`` should contain the indicator values at signal time
    (ema10_slope, macd_spread_ratio, volume_ratio, dist_breakout_pct,
     rsi_1h, adx_4h, growth_score, golden_cross_ok, …).
    """
    history: list[dict] = _load_json(SIGNALS_HISTORY_PATH, [])

    entry: dict[str, Any] = {
        "symbol": symbol,
        "entry_price": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "features": features,
        "outcomes": {},   # filled later by evaluate_pending_signals
        "label": None,    # WINNER / LOSER / None
    }
    history.append(entry)
    _save_json(SIGNALS_HISTORY_PATH, history)
    logger.info("Signal recorded: %s @ %.6f", symbol, price)


# ─────────────────────────────────────────────
# Phase 2 — evaluate pending signals + build feedback_log
# ─────────────────────────────────────────────

def evaluate_pending_signals(fetch_price_fn) -> int:
    """Fetch current prices for pending signals and label them.

    ``fetch_price_fn(symbol) -> float | None``

    Returns the number of signals newly labeled.
    """
    history: list[dict] = _load_json(SIGNALS_HISTORY_PATH, [])
    feedback: list[dict] = _load_json(FEEDBACK_LOG_PATH, [])
    labeled_ids = {row["signal_ts"] for row in feedback}

    now_ts = time.time()
    newly_labeled = 0

    for entry in history:
        sig_ts_str = entry.get("timestamp", "")
        if not sig_ts_str:
            continue

        try:
            sig_dt = datetime.fromisoformat(sig_ts_str)
        except ValueError:
            continue

        sig_ts = sig_dt.timestamp()

        if sig_ts_str in labeled_ids:
            continue  # already processed

        # Only label after the longest horizon has passed
        if now_ts - sig_ts < OUTCOME_HORIZONS_S[-1]:
            continue

        symbol = entry["symbol"]
        entry_price = entry.get("entry_price")
        if not entry_price:
            continue

        current_price = fetch_price_fn(symbol)
        if current_price is None:
            logger.warning("Could not fetch price for %s — skipping.", symbol)
            continue

        pct_change_24h = ((current_price - entry_price) / entry_price) * 100.0
        label = "WINNER" if pct_change_24h >= WIN_THRESHOLD_PCT else "LOSER"

        # Update history entry
        entry["outcomes"]["24h_pct"] = round(pct_change_24h, 4)
        entry["outcomes"]["current_price"] = current_price
        entry["label"] = label

        # Write to feedback_log
        feedback_row: dict[str, Any] = {
            "signal_ts": sig_ts_str,
            "symbol": symbol,
            "entry_price": entry_price,
            "current_price": current_price,
            "pct_change_24h": round(pct_change_24h, 4),
            "label": label,
            "label_int": 1 if label == "WINNER" else 0,
        }
        feedback_row.update(entry.get("features", {}))
        feedback.append(feedback_row)

        newly_labeled += 1
        logger.info(
            "Labeled %s: %.2f%% → %s",
            symbol,
            pct_change_24h,
            label,
        )

    if newly_labeled:
        _save_json(SIGNALS_HISTORY_PATH, history)
        _save_json(FEEDBACK_LOG_PATH, feedback)
        logger.info("Labeled %s new signals. Feedback log now has %s rows.", newly_labeled, len(feedback))

    return newly_labeled


def collect_feedback_standalone(exchange) -> None:
    """Entry point called from the collect_feedback workflow."""

    def _fetch(symbol: str) -> float | None:
        try:
            ticker = exchange.fetch_ticker(symbol)
            val = ticker.get("last")
            return float(val) if val is not None else None
        except Exception as exc:
            logger.warning("Error fetching %s: %s", symbol, exc)
            return None

    count = evaluate_pending_signals(_fetch)
    logger.info("collect_feedback done. Newly labeled: %s", count)
