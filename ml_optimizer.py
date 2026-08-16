"""ml_optimizer.py — v2.0: train RandomForest on entry journal with time-series split."""
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

# Extended feature set v2.0
FEATURE_COLS = [
    # Daily
    "ema10_slope",
    "daily_regime_score",
    # 4H
    "macd_spread_ratio",
    "macd_histogram_rising",
    "macd_slope",
    "vol4h",
    "volume_label_score",
    "dist_breakout_pct",
    "adx_4h",
    "adx_rising",
    "di_plus",
    "di_minus",
    "di_plus_above_minus",
    "obv_above_ema",
    "obv_slope",
    "hidden_accumulation",
    "bb_compression_score",
    "atr_declining",
    "structure_score",
    "overextension_score",
    "dist_ema9_pct",
    "dist_ema21_pct",
    "is_breakout",
    "golden_cross_ok",
    "rs_score",
    # 1H
    "rsi_1h",
    "trigger_strength",
    # Legacy
    "growth_score",
]


def _load_feedback() -> list[dict]:
    if not os.path.exists(FEEDBACK_LOG_PATH):
        return []
    with open(FEEDBACK_LOG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _coerce(val: Any) -> float:
    if val is None or val is False:
        return 0.0
    if val is True:
        return 1.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _build_xy(rows: list[dict]) -> tuple[np.ndarray, np.ndarray] | None:
    X_rows, y_rows = [], []
    for row in rows:
        if row.get("label_int") is None:
            continue
        feat = [_coerce(row.get(col)) for col in FEATURE_COLS]
        X_rows.append(feat)
        y_rows.append(int(row["label_int"]))
    if len(X_rows) < MIN_SAMPLES:
        return None
    return np.array(X_rows, dtype=np.float32), np.array(y_rows, dtype=np.int32)


def train_model(save: bool = True) -> Any | None:
    """Train with time-series split (no data leakage from future)."""
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.metrics import roc_auc_score
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline
    except ImportError:
        logger.error("scikit-learn not installed. Run: pip install scikit-learn")
        return None

    rows = _load_feedback()
    result = _build_xy(rows)
    if result is None:
        available = len([r for r in rows if r.get("label_int") is not None])
        logger.warning("Not enough labeled samples (%s required, %s available).", MIN_SAMPLES, available)
        return None

    X, y = result
    winners = int(y.sum())
    logger.info("Training on %s samples (%s winners, %s losers).", len(y), winners, len(y) - winners)

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(
            n_estimators=300,
            max_depth=7,
            min_samples_leaf=4,
            class_weight="balanced",
            random_state=42,
        )),
    ])

    # Time-series split — NO random shuffle to avoid future leakage
    if len(y) >= 60:
        n_splits = min(5, winners, len(y) - winners)
        if n_splits >= 2:
            tscv = TimeSeriesSplit(n_splits=n_splits)
            auc_scores = []
            for train_idx, val_idx in tscv.split(X):
                pipeline.fit(X[train_idx], y[train_idx])
                prob = pipeline.predict_proba(X[val_idx])[:, 1]
                try:
                    auc = roc_auc_score(y[val_idx], prob)
                    auc_scores.append(auc)
                except Exception:
                    pass
            if auc_scores:
                logger.info("Walk-forward ROC-AUC: %.3f ± %.3f", np.mean(auc_scores), np.std(auc_scores))
        else:
            logger.info("Skipping cross-validation: not enough samples per class.")

    pipeline.fit(X, y)

    if save:
        with open(MODEL_PATH, "wb") as fh:
            pickle.dump({"model": pipeline, "feature_cols": FEATURE_COLS}, fh)
        logger.info("Model saved to %s", MODEL_PATH)

    return pipeline


def load_model() -> dict | None:
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
    if bundle is None:
        return None
    model = bundle.get("model")
    feature_cols = bundle.get("feature_cols", FEATURE_COLS)
    if model is None:
        return None
    try:
        row = [[_coerce(features.get(col)) for col in feature_cols]]
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
