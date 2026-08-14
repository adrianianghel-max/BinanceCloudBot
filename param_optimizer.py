"""param_optimizer.py — Phase 4: Bayesian optimization of scanner config params.

Uses Optuna to find the combination of filter thresholds that maximises
win-rate on historical feedback_log.json data, then patches config.py
with the best values found.

Usage:
    python param_optimizer.py               # run 200 trials and patch config.py
    python param_optimizer.py --trials 50   # faster run for testing
    python param_optimizer.py --dry-run     # print best params, do not patch
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

FEEDBACK_LOG_PATH = os.getenv("FEEDBACK_LOG_PATH", "feedback_log.json")
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.py")
MIN_SAMPLES = int(os.getenv("MIN_SAMPLES_FOR_OPT", "50"))


def _load_feedback() -> list[dict]:
    if not os.path.exists(FEEDBACK_LOG_PATH):
        return []
    with open(FEEDBACK_LOG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _apply_filters(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Return rows that would have passed a given set of filter thresholds."""
    out = []
    for row in rows:
        if row.get("label_int") is None:
            continue

        # Volume filter
        vol = row.get("vol4h") or 0.0
        if vol < params["VOLUME_RATIO_THRESHOLD"]:
            continue

        # EMA slope filter
        slope = row.get("ema10_slope") or 0.0
        if slope < params["MIN_EMA10_SLOPE_PCT"]:
            continue

        # MACD spread filter
        macd_sr = row.get("macd_spread_ratio") or 0.0
        if macd_sr < params["MIN_MACD_SPREAD_RATIO"]:
            continue

        # ADX filter
        adx = row.get("adx_4h") or 0.0
        if adx < params["ADX_MIN"]:
            continue

        # Near-breakout filter
        dist = row.get("dist_breakout_pct")
        if dist is None or dist > params["NEAR_BREAKOUT_MAX_DISTANCE_PCT"]:
            continue

        # RSI filter (only if rsi data present)
        rsi = row.get("rsi_1h")
        if rsi is not None:
            if not (params["RSI_MIN"] <= float(rsi) <= params["RSI_MAX"]):
                continue

        out.append(row)
    return out


def _objective(trial, rows: list[dict]) -> float:
    params = {
        "VOLUME_RATIO_THRESHOLD": trial.suggest_float("VOLUME_RATIO_THRESHOLD", 1.0, 2.5),
        "MIN_EMA10_SLOPE_PCT":    trial.suggest_float("MIN_EMA10_SLOPE_PCT", 0.01, 0.3),
        "MIN_MACD_SPREAD_RATIO":  trial.suggest_float("MIN_MACD_SPREAD_RATIO", 0.001, 0.05),
        "ADX_MIN":                trial.suggest_float("ADX_MIN", 10.0, 30.0),
        "NEAR_BREAKOUT_MAX_DISTANCE_PCT": trial.suggest_float("NEAR_BREAKOUT_MAX_DISTANCE_PCT", 1.0, 8.0),
        "RSI_MIN":                trial.suggest_float("RSI_MIN", 45.0, 65.0),
        "RSI_MAX":                trial.suggest_float("RSI_MAX", 70.0, 90.0),
    }

    filtered = _apply_filters(rows, params)
    if len(filtered) < 5:
        return 0.0

    labels = np.array([r["label_int"] for r in filtered])
    win_rate = labels.mean()

    # Penalize too-restrictive params (very few signals)
    coverage_penalty = min(len(filtered) / max(len(rows), 1), 1.0)

    # Optimise: win_rate weighted by coverage
    return float(win_rate * (0.7 + 0.3 * coverage_penalty))


def run_optimization(n_trials: int = 200) -> dict[str, Any] | None:
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        logger.error("optuna not installed. Run: pip install optuna")
        return None

    rows = _load_feedback()
    labeled = [r for r in rows if r.get("label_int") is not None]
    if len(labeled) < MIN_SAMPLES:
        logger.warning(
            "Not enough labeled samples for optimization (%s required, %s available).",
            MIN_SAMPLES,
            len(labeled),
        )
        return None

    logger.info("Running Optuna optimization on %s samples for %s trials.", len(labeled), n_trials)
    study = optuna.create_study(direction="maximize")
    study.optimize(lambda trial: _objective(trial, labeled), n_trials=n_trials, show_progress_bar=False)

    best = study.best_params
    logger.info("Best params found (value=%.4f): %s", study.best_value, best)
    return best


def patch_config(best_params: dict[str, Any], config_path: str = CONFIG_PATH) -> None:
    """Patch numeric constants in config.py with optimized values."""
    with open(config_path, encoding="utf-8") as fh:
        content = fh.read()

    mapping = {
        "VOLUME_RATIO_THRESHOLD":        best_params.get("VOLUME_RATIO_THRESHOLD"),
        "MIN_EMA10_SLOPE_PCT":           best_params.get("MIN_EMA10_SLOPE_PCT"),
        "MIN_MACD_SPREAD_RATIO":         best_params.get("MIN_MACD_SPREAD_RATIO"),
        "ADX_MIN":                       best_params.get("ADX_MIN"),
        "NEAR_BREAKOUT_MAX_DISTANCE_PCT":best_params.get("NEAR_BREAKOUT_MAX_DISTANCE_PCT"),
        "RSI_MIN":                       best_params.get("RSI_MIN"),
        "RSI_MAX":                       best_params.get("RSI_MAX"),
    }

    for key, value in mapping.items():
        if value is None:
            continue
        # Match lines like: KEY = 1.2  or  KEY = 20.0
        pattern = rf"^({re.escape(key)}\s*=\s*)([\d.]+)"
        replacement = rf"\g<1>{value:.4f}"
        new_content, n = re.subn(pattern, replacement, content, flags=re.MULTILINE)
        if n:
            content = new_content
            logger.info("Patched %s = %.4f", key, value)
        else:
            logger.warning("Could not find %s in config.py to patch.", key)

    with open(config_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    logger.info("config.py patched with optimized parameters.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    best = run_optimization(n_trials=args.trials)
    if best and not args.dry_run:
        patch_config(best)
