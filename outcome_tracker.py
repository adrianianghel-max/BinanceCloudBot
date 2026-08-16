"""outcome_tracker.py — v2.0: signal history + outcome labeling.

WINNER = price reaches +ML_TARGET_GAIN_PCT before stop-loss threshold is hit.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import config

logger = logging.getLogger(__name__)

SIGNALS_HISTORY_PATH = os.getenv("SIGNALS_HISTORY_PATH", "signals_history.json")
FEEDBACK_LOG_PATH = os.getenv("FEEDBACK_LOG_PATH", "feedback_log.json")

WIN_THRESHOLD_PCT = config.ML_TARGET_GAIN_PCT      # +8%
STOP_LOSS_PCT = config.ML_STOP_LOSS_PCT            # -3%

OUTCOME_HORIZONS_S = [
    config.ML_HORIZON_4H_CANDLES * 4 * 3600,       # 12 x 4h = 48h
    24 * 3600,
    48 * 3600,
]


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


def record_signal(symbol: str, price: float, features: dict[str, Any]) -> None:
    history: list[dict] = _load_json(SIGNALS_HISTORY_PATH, [])
    entry: dict[str, Any] = {
        "symbol": symbol,
        "entry_price": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "features": features,
        "outcomes": {},
        "label": None,
    }
    history.append(entry)
    _save_json(SIGNALS_HISTORY_PATH, history)
    logger.info("Signal recorded: %s @ %.6f", symbol, price)


def evaluate_pending_signals(fetch_price_fn) -> int:
    """Fetch current prices and label signals.

    WINNER: current price >= entry * (1 + WIN_THRESHOLD_PCT/100)
    LOSER:  current price <= entry * (1 - STOP_LOSS_PCT/100)
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
            continue
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

        pct_change = ((current_price - entry_price) / entry_price) * 100.0

        if pct_change >= WIN_THRESHOLD_PCT:
            label = "WINNER"
        elif pct_change <= -STOP_LOSS_PCT:
            label = "LOSER"
        else:
            label = "LOSER"  # didn't reach target in time

        entry["outcomes"]["pct_change"] = round(pct_change, 4)
        entry["outcomes"]["current_price"] = current_price
        entry["label"] = label

        feedback_row: dict[str, Any] = {
            "signal_ts": sig_ts_str,
            "symbol": symbol,
            "entry_price": entry_price,
            "current_price": current_price,
            "pct_change": round(pct_change, 4),
            "label": label,
            "label_int": 1 if label == "WINNER" else 0,
        }
        feedback_row.update(entry.get("features", {}))
        feedback.append(feedback_row)
        newly_labeled += 1
        logger.info("Labeled %s: %.2f%% → %s", symbol, pct_change, label)

    if newly_labeled:
        _save_json(SIGNALS_HISTORY_PATH, history)
        _save_json(FEEDBACK_LOG_PATH, feedback)
        logger.info("Labeled %s new signals. Feedback log: %s rows.", newly_labeled, len(feedback))

    return newly_labeled


def collect_feedback_standalone(exchange) -> None:
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
