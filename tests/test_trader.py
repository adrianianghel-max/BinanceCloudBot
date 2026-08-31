"""Unit tests for trader.py — PAPER trading engine (Position/Portfolio/Backtester/Optimizer)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
from trader import (
    Backtester,
    ParameterOptimizer,
    Portfolio,
    Position,
    baseline_params,
    params_key,
)


def _make_candles(prices):
    """Creează un DataFrame 1h sintetic din listă de (open, high, low, close)."""
    n = len(prices)
    ts = pd.date_range(start="2026-01-01", periods=n, freq="1h", tz="UTC")
    open_, high, low, close = zip(*prices)
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": [1000.0] * n,
        }
    )


class TestPosition(unittest.TestCase):
    def setUp(self):
        self.pos = Position(
            symbol="SOL/USDC",
            entry_price=100.0,
            size_usdc=50.0,
            entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            fee_rate=0.001,
        )

    def test_take_profit(self):
        reason = self.pos.evaluate(115.0)  # >= +15%
        self.assertEqual(reason, "take_profit")

    def test_stop_loss(self):
        reason = self.pos.evaluate(91.0)  # <= -8%
        self.assertEqual(reason, "stop_loss")

    def test_hold_between(self):
        self.assertIsNone(self.pos.evaluate(104.0))

    def test_trailing_stop_armed_then_triggered(self):
        # +6% => trailing armat (breakeven sau 3% sub peak)
        self.assertIsNone(self.pos.evaluate(106.0))
        self.assertTrue(self.pos.trailing_armed)
        # peak = 106, trailing stop = max(100, 106*0.97)=102.82
        self.assertAlmostEqual(self.pos.trailing_stop, 102.82, places=2)
        # preț scade sub trailing stop => ieșire
        self.assertEqual(self.pos.evaluate(102.0), "trailing_stop")

    def test_trailing_never_below_breakeven(self):
        self.pos.evaluate(105.0)  # +5% armat
        # peak 105 => 105*0.97 = 101.85 > 100
        self.assertGreater(self.pos.trailing_stop, 100.0)
        # și mai sus: 120 => 116.4
        self.pos.evaluate(120.0)
        self.assertAlmostEqual(self.pos.trailing_stop, 116.4, places=1)

    def test_close_net_fees(self):
        trade = self.pos.close(115.0, "take_profit", datetime(2026, 1, 1, 2, tzinfo=timezone.utc))
        buy_fee = 50.0 * 0.001
        sell_fee = self.pos.quantity * 115.0 * 0.001
        expected_net = (115.0 - 100.0) * self.pos.quantity - buy_fee - sell_fee
        self.assertAlmostEqual(trade["net_pnl"], expected_net, places=6)
        self.assertEqual(trade["exit_reason"], "take_profit")
        self.assertEqual(trade["total_fees"], buy_fee + sell_fee)
class TestPortfolio(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_max_positions(self):
        portfolio = Portfolio()
        portfolio.open_position("A/USDC", 1.0, self.now)
        portfolio.open_position("B/USDC", 1.0, self.now)
        self.assertFalse(portfolio.can_open())
        with self.assertRaises(RuntimeError):
            portfolio.open_position("C/USDC", 1.0, self.now)

    def test_cooldown(self):
        portfolio = Portfolio()
        portfolio.open_position("A/USDC", 1.0, self.now)
        portfolio.close_position("A/USDC", 1.1, "take_profit", self.now)
        self.assertTrue(portfolio.in_cooldown("A/USDC", self.now + timedelta(hours=12)))
        self.assertFalse(portfolio.in_cooldown(
            "A/USDC", self.now + timedelta(hours=config.COOLDOWN_HOURS + 1)))

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = td + "/paper_state.json"
            portfolio = Portfolio(path)
            portfolio.open_position("SOL/USDC", 100.0, self.now)
            portfolio.close_position(
                "SOL/USDC", 115.0, "take_profit", self.now + timedelta(hours=1))
            portfolio.save()

            loaded = Portfolio(path).load()
            self.assertEqual(loaded.open_count(), 0)
            self.assertEqual(len(loaded.closed_trades), 1)
            self.assertAlmostEqual(loaded.closed_trades[0]["net_pnl"],
                                   portfolio.closed_trades[0]["net_pnl"], places=6)
            self.assertEqual(loaded.last_close_by_symbol["SOL/USDC"],
                             portfolio.last_close_by_symbol["SOL/USDC"])
class TestBacktester(unittest.TestCase):
    def test_qualifies(self):
        feats = {
            "daily_ok": True,
            "ema10_slope": 0.1,
            "macd_ok": True,
            "volume_ratio": 1.5,
            "distance": 2.0,
            "adx": 25.0,
            "rsi": 60.0,
            "rsi_rising": True,
            "vol_up": True,
        }
        params = baseline_params()
        self.assertTrue(Backtester.qualifies(feats, params))
        # volume sub prag
        f2 = dict(feats, volume_ratio=1.0)
        params2 = dict(params, VOLUME_RATIO_THRESHOLD=1.2)
        self.assertFalse(Backtester.qualifies(f2, params2))

    def test_qualifies_respects_custom_params(self):
        feats = {
            "daily_ok": True,
            "ema10_slope": 0.1,
            "macd_ok": True,
            "volume_ratio": 1.5,
            "distance": 4.5,
            "adx": 25.0,
            "rsi": 60.0,
            "rsi_rising": True,
            "vol_up": True,
        }
        strict = baseline_params()
        strict["NEAR_BREAKOUT_MAX_DISTANCE_PCT"] = 3.0
        wide = baseline_params()
        wide["NEAR_BREAKOUT_MAX_DISTANCE_PCT"] = 5.0
        self.assertFalse(Backtester.qualifies(feats, strict))
        self.assertTrue(Backtester.qualifies(feats, wide))

    def test_simulate_day_tp(self):
        candles = _make_candles([
            (100, 100, 100, 100),
            (100, 116, 96, 115),   # TP +15% atins => take_profit
            (115, 115, 115, 115),
        ])
        result = Backtester.simulate_day(candles, baseline_params())
        self.assertEqual(result["exit_reason"], "take_profit")
        self.assertAlmostEqual(result["exit_price"], 115.0, places=6)
        self.assertGreater(result["net_pnl"], 0)

    def test_simulate_day_sl(self):
        candles = _make_candles([
            (100, 100, 100, 100),
            (100, 100, 91.5, 92.0),
            (92, 100, 92, 95),
        ])
        result = Backtester.simulate_day(candles, baseline_params())
        self.assertEqual(result["exit_reason"], "stop_loss")
        self.assertLess(result["net_pnl"], 0)

    def test_simulate_day_eod(self):
        candles = _make_candles([
            (100, 100, 100, 100),
            (100, 102, 99, 101),
            (101, 103, 101, 102.5),
        ])
        result = Backtester.simulate_day(candles, baseline_params())
        self.assertEqual(result["exit_reason"], "eod")
        self.assertAlmostEqual(result["exit_price"], 102.5, places=6)


class TestParameterOptimizer(unittest.TestCase):
    def test_generate_combos_includes_baseline(self):
        optimizer = ParameterOptimizer()
        combos = optimizer.generate_combos(datetime(2026, 1, 5, tzinfo=timezone.utc))
        self.assertGreater(len(combos), 0)
        self.assertIn(params_key(baseline_params()),
                      {params_key(c) for c in combos})

    def test_generate_combos_deterministic_same_day(self):
        day = datetime(2026, 1, 5, tzinfo=timezone.utc)
        a = ParameterOptimizer().generate_combos(day)
        b = ParameterOptimizer().generate_combos(day)
        self.assertEqual([params_key(c) for c in a], [params_key(c) for c in b])

    def test_select_best_prefers_higher_pnl(self):
        optimizer = ParameterOptimizer()
        p1 = dict(baseline_params())
        p2 = dict(p1, VOLUME_RATIO_THRESHOLD=1.0)
        results = {
            params_key(p1): {"pnl_net": 1.0, "profit_factor": 1.2, "max_drawdown_usdc": -2.0, "params": p1},
            params_key(p2): {"pnl_net": 5.0, "profit_factor": 2.5, "max_drawdown_usdc": -1.0, "params": p2},
        }
        best = optimizer.select_best(results, [], datetime(2026, 1, 5, tzinfo=timezone.utc))
        self.assertEqual(best["VOLUME_RATIO_THRESHOLD"], 1.0)

    def test_select_best_tiebreak_profit_factor(self):
        optimizer = ParameterOptimizer()
        p1 = dict(baseline_params(), RSI_MIN=55)
        p2 = dict(baseline_params(), RSI_MIN=56)
        results = {
            params_key(p1): {"pnl_net": 2.0, "profit_factor": 1.0, "max_drawdown_usdc": -3.0, "params": p1},
            params_key(p2): {"pnl_net": 2.0, "profit_factor": 3.0, "max_drawdown_usdc": -3.0, "params": p2},
        }
        best = optimizer.select_best(results, [], datetime(2026, 1, 5, tzinfo=timezone.utc))
        self.assertEqual(best["RSI_MIN"], 56.0)

    def test_history_blend_uses_previous_days(self):
        optimizer = ParameterOptimizer()
        p = dict(baseline_params())
        p2 = dict(p, ADX_MIN=15.0)
        now = datetime(2026, 1, 5, tzinfo=timezone.utc)
        history = [
            {"date": "2026-01-04", "params": p2, "pnl_net": 8.0},
            {"date": "2026-01-03", "params": p2, "pnl_net": 8.0},
        ]
        results = {
            params_key(p): {"pnl_net": 0.0, "profit_factor": 1.1, "max_drawdown_usdc": -1.0, "params": p},
            params_key(p2): {"pnl_net": 0.0, "profit_factor": 1.1, "max_drawdown_usdc": -1.0, "params": p2},
        }
        best = optimizer.select_best(results, history, now)
        # p2 are istoric bun (8.0, 8.0) => trebuie ales p2
        self.assertEqual(best["ADX_MIN"], 15.0)


if __name__ == "__main__":
    unittest.main()