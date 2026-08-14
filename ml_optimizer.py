"""ml_optimizer.py — Phase 3: train a RandomForest model on feedback_log.json.

Usage:
    python ml_optimizer.py          # train and save model.pkl
    python ml_optimizer.py --score  # show cross-validated metrics only

The model predicts P(WINNER) for a given set of indicator features.
It is loaded at scan time by scanner.py to augment the growth_score.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

FEEDBACK_LOG_PATH = os.getenv("FEEDBACK_LOG_PATH", "feedback_log.json")
MODEL_PATH = os.getenv("MODEL_PATH", "model.pkl")
MIN_SAMPLES = int(os.getenv("MIN_SAMPLES_FOR_TRAINING", "30"))

FEATURE_COLS = [
    "ema10_slope",
    "macd_spread_ratio",
    "vol4h",
    "dist_breakout_pct",
    "adx_4h",
    "growth_score",
    "golden_cross_ok",
    # rsi is optional — may be absent when USE_1H_FILTER=False
]


def _load_feedback() -> list[dict]:
    if not os.path.exists(FEEDBACK_LOG_PATH):
        return []
    with open(FEEDBACK_LOG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _build_xy(rows: list[dict]) -> tuple[np.ndarray, np.ndarray] | None:
    """Extract feature matrix X and label vector y from feedback rows."""
    X_rows, y_rows = [], []
    for row in rows:
        if row.get("label_int") is None:
            continue
        feat = []
        for col in FEATURE_COLS:
            val = row.get(col)
            if val is None:
                val = 0.0
            feat.append(float(val))
        X_rows.append(feat)
        y_rows.append(int(row["label_int"]))

    if len(X_rows) < MIN_SAMPLES:
        return None

    return np.array(X_rows, dtype=np.float32), np.array(y_rows, dtype=np.int32)


def train_model(save: bool = True) -> Any | None:
    """Train a RandomForest classifier and optionally save it to disk."""
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import StratifiedKFold, cross_val_score
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline
    except ImportError:
        logger.error("scikit-learn not installed. Run: pip install scikit-learn")
        return None

    rows = _load_feedback()
    result = _build_xy(rows)
    if result is None:
        logger.warning(
            "Not enough labeled samples (%s required, %s available). Skipping training.",
            MIN_SAMPLES,
            len([r for r in rows if r.get("label_int") is not None]),
        )
        return None

    X, y = result
    winners = int(y.sum())
    logger.info("Training on %s samples (%s winners, %s losers).", len(y), winners, len(y) - winners)

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(
            n_estimators=200,
            max_depth=6,
            min_samples_leaf=5,
            class_weight="balanced",
            random_state=42,
        )),
    ])

    if len(y) >= 50:
        cv = StratifiedKFold(n_splits=min(5, winners, len(y) - winners), shuffle=True, random_state=42)
        scores = cross_val_score(pipeline, X, y, cv=cv, scoring="roc_auc")
        logger.info("Cross-val ROC-AUC: %.3f ± %.3f", scores.mean(), scores.std())

    pipeline.fit(X, y)

    if save:
        with open(MODEL_PATH, "wb") as fh:
            pickle.dump({"model": pipeline, "feature_cols": FEATURE_COLS}, fh)
        logger.info("Model saved to %s", MODEL_PATH)

    return pipeline


def load_model() -> dict | None:
    """Load the pickled model bundle. Returns None if model not available."""
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        with open(MODEL_PATH, "rb") as fh:
            bundle = pickle.load(fh)
        return bundle
    except Exception as exc:
        logger.warning("Could not load model: %s", exc)
        return None


def predict_win_probability(features: dict[str, Any], bundle: dict | None) -> float | None:
    """Return P(WINNER) in [0, 1] or None if model unavailable."""
    if bundle is None:
        return None
    model = bundle.get("model")
    feature_cols = bundle.get("feature_cols", FEATURE_COLS)
    if model is None:
        return None
    try:
        row = [[float(features.get(col) or 0.0) for col in feature_cols]]
        prob = model.predict_proba(row)[0][1]
        return float(prob)
    except Exception as exc:
        logger.warning("ML prediction error: %s", exc)
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train ML model on feedback_log.json")
    parser.add_argument("--score", action="store_true", help="Only print CV score, do not save")
    args = parser.parse_args()
    train_model(save=not args.score)
