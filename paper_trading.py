"""paper_trading.py — Paper trade journal for entry/exit simulation."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import config

logger = logging.getLogger(__name__)


def _load(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []


def _save(path: str, data: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)


def open_paper_trade(
    symbol: str,
    signal_type: str,
    entry_price: float,
    stop_price: float | None,
    target_price: float | None,
    technical_score: float,
    ml_prob: float | None,
    hybrid_score: float,
    features: dict[str, Any],
) -> None:
    """Record a new paper trade entry."""
    if not config.PAPER_TRADING_ENABLED:
        return
    trades = _load(config.PAPER_TRADING_PATH)
    trade: dict[str, Any] = {
        "id": len(trades) + 1,
        "symbol": symbol,
        "signal_type": signal_type,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "technical_score": technical_score,
        "ml_probability": ml_prob,
        "hybrid_score": hybrid_score,
        "status": "OPEN",
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None,
        "exit_price": None,
        "pnl_pct": None,
        "outcome": None,
        "features": features,
    }
    trades.append(trade)
    _save(config.PAPER_TRADING_PATH, trades)
    logger.info("Paper trade OPEN: %s @ %.6f [%s]", symbol, entry_price, signal_type)


def update_paper_trades(fetch_price_fn) -> None:
    """Check open paper trades against current prices and close if target/stop hit."""
    if not config.PAPER_TRADING_ENABLED:
        return
    trades = _load(config.PAPER_TRADING_PATH)
    updated = False
    for trade in trades:
        if trade.get("status") != "OPEN":
            continue
        symbol = trade["symbol"]
        entry = trade["entry_price"]
        stop = trade.get("stop_price")
        target = trade.get("target_price")
        current = fetch_price_fn(symbol)
        if current is None:
            continue
        pnl_pct = ((current - entry) / entry) * 100.0
        outcome = None
        if target and current >= target:
            outcome = "WIN"
        elif stop and current <= stop:
            outcome = "LOSS"
        if outcome:
            trade["status"] = "CLOSED"
            trade["exit_price"] = current
            trade["pnl_pct"] = round(pnl_pct, 4)
            trade["outcome"] = outcome
            trade["closed_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("Paper trade CLOSED: %s → %s (%.2f%%)", symbol, outcome, pnl_pct)
            updated = True
    if updated:
        _save(config.PAPER_TRADING_PATH, trades)


def get_paper_trading_stats() -> dict[str, Any]:
    """Return win rate and basic P&L stats."""
    trades = _load(config.PAPER_TRADING_PATH)
    closed = [t for t in trades if t.get("status") == "CLOSED"]
    wins = [t for t in closed if t.get("outcome") == "WIN"]
    losses = [t for t in closed if t.get("outcome") == "LOSS"]
    pnls = [t["pnl_pct"] for t in closed if t.get("pnl_pct") is not None]
    return {
        "total_trades": len(trades),
        "open_trades": len(trades) - len(closed),
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "max_drawdown_pct": round(min(pnls), 2) if pnls else 0.0,
    }
