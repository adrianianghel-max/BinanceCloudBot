"""entry_scorer.py — Technical scoring, hybrid score, signal classification."""
from __future__ import annotations

import logging
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)

# Signal types
SIGNAL_NO_SETUP = "NO_SETUP"
SIGNAL_WATCH = "WATCH"
SIGNAL_PRE_ENTRY = "PRE_ENTRY"
SIGNAL_NEW_ENTRY = "NEW_ENTRY"
SIGNAL_RETEST_ENTRY = "RETEST_ENTRY"
SIGNAL_STRONG_ENTRY = "STRONG_ENTRY"


def compute_technical_score(
    daily_regime: str,
    market_regime: str,
    h4: dict[str, Any],
    h1: dict[str, Any],
    rs: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    """
    Compute technical score 0-100 with per-component breakdown.
    Returns (total_score, component_scores).
    """
    components: dict[str, float] = {}

    # ── 1. Daily trend / regime (15 pts)
    regime_map = {
        "BULLISH": 1.0,
        "EARLY_BULLISH": 0.75,
        "NEUTRAL": 0.4,
        "BEARISH": 0.0,
    }
    components["daily_trend"] = config.SCORE_DAILY_TREND * regime_map.get(daily_regime, 0.4)

    # ── 2. 4H Structure (15 pts)
    struct_score = h4.get("structure_score", 50.0)
    components["structure_4h"] = config.SCORE_4H_STRUCTURE * (struct_score / 100.0)

    # ── 3. Compression (10 pts) — BB + ATR
    bb_score = h4.get("bb_compression_score", 0.0)
    atr_decline = 1.0 if h4.get("atr_declining") else 0.0
    comp_combined = (bb_score * 0.7 + atr_decline * 30.0)
    components["compression"] = config.SCORE_COMPRESSION * min(comp_combined / 100.0, 1.0)

    # ── 4. Volume (10 pts)
    vol_ratio = h4.get("volume_ratio") or 0.0
    vol_norm = min(max((vol_ratio - 1.0) / 1.0, 0.0), 1.0)
    components["volume"] = config.SCORE_VOLUME * vol_norm

    # ── 5. MACD (10 pts)
    macd_bonus = 0.0
    if h4.get("macd_ok"):
        macd_bonus += 0.5
    if h4.get("macd_histogram_rising"):
        macd_bonus += 0.3
    if (h4.get("macd_slope") or 0) > 0:
        macd_bonus += 0.2
    components["macd"] = config.SCORE_MACD * min(macd_bonus, 1.0)

    # ── 6. ADX + DI (10 pts)
    adx_bonus = 0.0
    if h4.get("adx_ok"):
        adx_bonus += 0.4
    if h4.get("adx_rising"):
        adx_bonus += 0.3
    if h4.get("di_plus_above_minus"):
        adx_bonus += 0.2
    if (h4.get("di_plus_slope") or 0) > 0:
        adx_bonus += 0.1
    components["adx_di"] = config.SCORE_ADX_DI * min(adx_bonus, 1.0)

    # ── 7. OBV / Accumulation (10 pts)
    obv_bonus = 0.0
    if h4.get("obv_above_ema"):
        obv_bonus += 0.5
    if (h4.get("obv_slope") or 0) > 0:
        obv_bonus += 0.3
    if h4.get("hidden_accumulation"):
        obv_bonus += 0.2
    components["obv"] = config.SCORE_OBV * min(obv_bonus, 1.0)

    # ── 8. Breakout proximity (10 pts)
    dist = h4.get("distance_to_breakout_pct")
    if dist is not None:
        zone = h4.get("breakout_zone", "far")
        zone_map = {"immediate": 1.0, "optimal": 0.85, "early": 0.65, "far": 0.3}
        bp_norm = zone_map.get(zone, 0.3)
        if h4.get("is_breakout"):
            bp_norm = 1.0
    else:
        bp_norm = 0.0
    components["breakout_proximity"] = config.SCORE_BREAKOUT_PROXIMITY * bp_norm

    # ── 9. Relative strength (5 pts)
    rs_norm = rs.get("rs_score", 50.0) / 100.0
    components["relative_strength"] = config.SCORE_RELATIVE_STRENGTH * rs_norm

    # ── 10. Market regime (5 pts)
    from market_regime import regime_score_contribution
    mr_norm = regime_score_contribution(market_regime)
    components["market_regime"] = config.SCORE_MARKET_REGIME * mr_norm

    # ── Overextension penalty
    oe_score = h4.get("overextension_score", 0.0)
    penalty = (oe_score / 100.0) * 15.0  # max 15 pts deduction

    total = sum(components.values()) - penalty
    total = round(min(max(total, 0.0), 100.0), 2)

    # ── 1H trigger bonus (up to +5)
    trigger_strength = h1.get("trigger_strength", 0)
    h1_bonus = min(trigger_strength * 1.5, 7.5)
    total = round(min(total + h1_bonus, 100.0), 2)

    return total, components


def compute_hybrid_score(technical_score: float, ml_prob: float | None) -> float:
    """Blend technical score with ML probability."""
    if ml_prob is None:
        return technical_score
    return round(
        config.TECHNICAL_WEIGHT * technical_score + config.ML_WEIGHT * (ml_prob * 100.0),
        2,
    )


def classify_signal(
    hybrid_score: float,
    h4: dict[str, Any],
    h1: dict[str, Any],
    ml_prob: float | None,
    retest: dict[str, Any],
) -> str:
    """Classify signal type based on hybrid score, ML gate, and retest."""
    # RETEST check — separate path
    if retest.get("retest_entry") and retest.get("retest_confidence", 0) >= 60:
        # Retest can override a weaker NEW_ENTRY
        return SIGNAL_RETEST_ENTRY

    # ML gate
    if ml_prob is not None and config.USE_ML_GATE:
        if hybrid_score >= config.NEW_ENTRY_MIN_SCORE and ml_prob < config.ML_MIN_WIN_PROBABILITY:
            # Downgrade to PRE_ENTRY if ML doesn't confirm
            hybrid_score = min(hybrid_score, config.PRE_ENTRY_MIN_SCORE + 5)

    # STRONG_ENTRY
    if hybrid_score >= config.STRONG_ENTRY_MIN_SCORE:
        if ml_prob is None or ml_prob >= config.ML_STRONG_ENTRY_PROBABILITY:
            if not h4.get("overextended"):
                return SIGNAL_STRONG_ENTRY
        return SIGNAL_NEW_ENTRY

    # NEW_ENTRY
    if hybrid_score >= config.NEW_ENTRY_MIN_SCORE:
        return SIGNAL_NEW_ENTRY

    # PRE_ENTRY
    if hybrid_score >= config.PRE_ENTRY_MIN_SCORE:
        return SIGNAL_PRE_ENTRY

    # WATCH
    if hybrid_score >= config.WATCH_MIN_SCORE:
        return SIGNAL_WATCH

    return SIGNAL_NO_SETUP


def compute_entry_quality(hybrid_score: float, signal_type: str, risk_reward: float | None) -> str:
    """Return LOW/MEDIUM/HIGH/VERY_HIGH."""
    if signal_type == SIGNAL_STRONG_ENTRY:
        return "VERY_HIGH"
    if signal_type in (SIGNAL_NEW_ENTRY, SIGNAL_RETEST_ENTRY):
        if risk_reward and risk_reward >= 3.0:
            return "HIGH"
        return "MEDIUM" if hybrid_score < 85 else "HIGH"
    if signal_type == SIGNAL_PRE_ENTRY:
        return "MEDIUM"
    return "LOW"


def compute_risk_reward(entry_price: float, stop_price: float | None, target_pct: float = 8.0) -> float | None:
    if stop_price is None or entry_price <= 0 or stop_price >= entry_price:
        return None
    risk = entry_price - stop_price
    reward = entry_price * target_pct / 100.0
    if risk <= 0:
        return None
    return round(reward / risk, 2)


def build_why_now(h4: dict[str, Any], h1: dict[str, Any], signal_type: str, daily_regime: str) -> list[str]:
    """Generate up to 5 human-readable reasons for the signal."""
    reasons = []
    if signal_type == SIGNAL_RETEST_ENTRY:
        reasons.append("Breakout confirmat + retestare suport")
    if h4.get("is_breakout"):
        reasons.append("4H breakout confirmat")
    if h4.get("breakout_zone") == "immediate":
        reasons.append("Pret la marginea rezistentei (< 1%)")
    if h4.get("hidden_accumulation"):
        reasons.append("Acumulare ascunsa detectata (OBV in crestere)")
    if h4.get("bb_compressed") and h4.get("bb_width_declining"):
        reasons.append("Compresie Bollinger in desfasurare")
    if h4.get("adx_rising") and h4.get("di_plus_above_minus"):
        reasons.append("ADX in crestere + DI+ > DI-")
    if h4.get("macd_histogram_rising"):
        reasons.append("MACD histogram in crestere")
    if h1.get("rsi_1h_ok") and h1.get("volume_1h_rising"):
        reasons.append("Confirmare 1H: RSI + volum in crestere")
    if daily_regime in ("BULLISH", "EARLY_BULLISH"):
        reasons.append(f"Trend zilnic: {daily_regime}")
    return reasons[:5]
