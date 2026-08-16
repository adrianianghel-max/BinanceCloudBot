"""Unit tests for entry_scorer.py."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from entry_scorer import (
    SIGNAL_NEW_ENTRY,
    SIGNAL_NO_SETUP,
    SIGNAL_PRE_ENTRY,
    SIGNAL_STRONG_ENTRY,
    SIGNAL_WATCH,
    classify_signal,
    compute_entry_quality,
    compute_hybrid_score,
    compute_risk_reward,
    compute_technical_score,
)


def make_h4_good():
    return {
        "structure_score": 85.0, "trend_structure": "BULLISH",
        "bb_compression_score": 60.0, "atr_declining": True,
        "volume_ratio": 1.8, "volume_label": "breakout_confirmation",
        "macd_ok": True, "macd_histogram_rising": True, "macd_slope": 0.5,
        "adx_ok": True, "adx_4h": 28.0, "adx_rising": True,
        "di_plus_above_minus": True, "di_plus_slope": 1.0,
        "obv_above_ema": True, "obv_slope": 2.0, "hidden_accumulation": True,
        "distance_to_breakout_pct": 0.8, "is_breakout": False,
        "breakout_zone": "immediate", "overextended": False, "overextension_score": 0.0,
    }


def make_h4_bad():
    return {
        "structure_score": 20.0, "trend_structure": "BEARISH",
        "bb_compression_score": 0.0, "atr_declining": False,
        "volume_ratio": 0.7, "volume_label": "weak",
        "macd_ok": False, "macd_histogram_rising": False, "macd_slope": -1.0,
        "adx_ok": False, "adx_4h": 10.0, "adx_rising": False,
        "di_plus_above_minus": False, "di_plus_slope": -1.0,
        "obv_above_ema": False, "obv_slope": -1.0, "hidden_accumulation": False,
        "distance_to_breakout_pct": 8.0, "is_breakout": False,
        "breakout_zone": "far", "overextended": False, "overextension_score": 0.0,
    }


def make_h1_good():
    return {"trigger_strength": 4, "trigger_ok": True, "rsi_1h": 62, "rsi_1h_prev": 58, "volume_1h_rising": True}


def make_h1_bad():
    return {"trigger_strength": 0, "trigger_ok": False, "rsi_1h": 45, "rsi_1h_prev": 48, "volume_1h_rising": False}


def make_rs_good():
    return {"rs_4h": 1.5, "rs_rising": True, "rs_strong": True, "rs_score": 90.0}


def make_rs_neutral():
    return {"rs_4h": 1.0, "rs_rising": False, "rs_strong": False, "rs_score": 60.0}


class TestTechnicalScore:
    def test_good_setup_scores_high(self):
        score, _ = compute_technical_score("BULLISH", "BULLISH", make_h4_good(), make_h1_good(), make_rs_good())
        assert score >= 75

    def test_bad_setup_scores_low(self):
        score, _ = compute_technical_score("BEARISH", "BEARISH", make_h4_bad(), make_h1_bad(), make_rs_neutral())
        assert score <= 40

    def test_score_in_range(self):
        score, _ = compute_technical_score("BULLISH", "BULLISH", make_h4_good(), make_h1_good(), make_rs_good())
        assert 0 <= score <= 100

    def test_components_returned(self):
        _, comps = compute_technical_score("BULLISH", "BULLISH", make_h4_good(), make_h1_good(), make_rs_good())
        assert "daily_trend" in comps
        assert "structure_4h" in comps
        assert "volume" in comps


class TestHybridScore:
    def test_no_ml_returns_technical(self):
        assert compute_hybrid_score(75.0, None) == 75.0

    def test_with_ml_blended(self):
        score = compute_hybrid_score(80.0, 0.70)
        assert score == round(0.50 * 80.0 + 0.50 * 70.0, 2)

    def test_score_in_range(self):
        score = compute_hybrid_score(100.0, 1.0)
        assert 0 <= score <= 100


class TestClassifySignal:
    def _retest_no(self):
        return {"retest_entry": False, "retest_confidence": 0}

    def test_no_setup(self):
        st = classify_signal(30, make_h4_bad(), make_h1_bad(), None, self._retest_no())
        assert st == SIGNAL_NO_SETUP

    def test_watch(self):
        st = classify_signal(55, make_h4_bad(), make_h1_bad(), None, self._retest_no())
        assert st == SIGNAL_WATCH

    def test_pre_entry(self):
        st = classify_signal(72, make_h4_good(), make_h1_good(), None, self._retest_no())
        assert st == SIGNAL_PRE_ENTRY

    def test_new_entry(self):
        st = classify_signal(84, make_h4_good(), make_h1_good(), None, self._retest_no())
        assert st == SIGNAL_NEW_ENTRY

    def test_strong_entry(self):
        h4 = {**make_h4_good(), "overextended": False}
        st = classify_signal(92, h4, make_h1_good(), 0.75, self._retest_no())
        assert st == SIGNAL_STRONG_ENTRY

    def test_ml_gate_downgrades(self):
        # ML below 60% should downgrade a NEW_ENTRY
        st = classify_signal(84, make_h4_good(), make_h1_good(), 0.45, self._retest_no())
        assert st in (SIGNAL_PRE_ENTRY, SIGNAL_WATCH)

    def test_retest_entry(self):
        retest = {"retest_entry": True, "retest_confidence": 80}
        st = classify_signal(75, make_h4_good(), make_h1_good(), None, retest)
        from entry_scorer import SIGNAL_RETEST_ENTRY
        assert st == SIGNAL_RETEST_ENTRY


class TestEntryQuality:
    def test_strong_is_very_high(self):
        assert compute_entry_quality(92, SIGNAL_STRONG_ENTRY, 3.0) == "VERY_HIGH"

    def test_no_setup_is_low(self):
        assert compute_entry_quality(30, SIGNAL_NO_SETUP, None) == "LOW"


class TestRiskReward:
    def test_basic(self):
        rr = compute_risk_reward(100.0, 97.0, 8.0)
        assert rr is not None
        assert rr > 0

    def test_invalid_stop_above_entry(self):
        assert compute_risk_reward(100.0, 105.0) is None

    def test_none_stop(self):
        assert compute_risk_reward(100.0, None) is None
