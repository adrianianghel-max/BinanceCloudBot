"""trader.py — Modul de tranzacționare (PAPER TRADING ONLY) pentru BinanceCloudBot.

Transformă scannerul existent într-un bot de trading automatizat care:
  * alege cele mai bune 2 simboluri USDC după growth_score (filtrul existent);
  * simulează cumpărarea / vânzarea a 50 USDC per simbol (fără ordine reale!);
  * aplică TP (+15%), SL (-8%), trailing stop (armat la +5%, pas 3%)
    și închidere forțată la 23:59 UTC;
  * aplică comision 0.1% per tranzacție (Binance spot standard) și calculează
    profit net;
  * rulează zilnic un backtest pe ziua anterioară și recalibrează parametrii
    (NEAR_BREAKOUT_MAX_DISTANCE_PCT, VOLUME_RATIO_THRESHOLD, MIN_EMA10_SLOPE_PCT,
    RSI_MIN/RSI_MAX, ADX_MIN) folosind o medie ponderată pe ultimele 3-5 zile;
  * face explorare aleatorie o dată pe săptămână;
  * generează raport zilnic JSON (reports/daily_report_YYYY-MM-DD.json) + Telegram.

NICIO FUNCȚIE NU TRIMITE ORDINE REALE PE BINANCE.
"""

from __future__ import annotations

import json
import logging
import math
import random
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

import config
from indicators import (
    add_ema_columns,
    calculate_adx_value,
    calculate_distance_to_breakout_pct,
    calculate_ema10_slope_pct,
    calculate_growth_score,
    calculate_macd_values,
    calculate_rsi_pair,
    calculate_volume_ratio,
    is_daily_bullish,
    is_daily_early_trend,
    prepare_ohlcv_df,
)
from market_data import fetch_ticker_safe, with_retries
from state_manager import load_json_state, save_json_state

logger = logging.getLogger("trader")


# ======================================================================
#   LOGGING — trading.log (detaliat, rotativ)
# ======================================================================

def setup_file_logging() -> None:
    """Adaugă un FileHandler rotativ pe trading.log la root logger."""
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "baseFilename", "").endswith(config.TRADING_LOG_PATH):
            return
    try:
        handler = RotatingFileHandler(
            config.TRADING_LOG_PATH,
            maxBytes=config.LOG_MAX_MB * 1024 * 1024,
            backupCount=config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
        root.addHandler(handler)
    except OSError as exc:  # pragma: no cover - mediul de execuție poate fi read-only
        logger.warning("Nu pot deschide trading.log: %s", exc)


# ======================================================================
#   HELPERI DE TIMP
# ======================================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_hhmm(dt: datetime, hhmm: str) -> datetime:
    """Compune un datetime UTC din 'HH:MM' pentru ziua dt."""
    hour, minute = hhmm.split(":")
    return dt.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)


def day_start_utc(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def day_end_utc(dt: datetime) -> datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)


def iso_ts(dt: datetime) -> str:
    return dt.isoformat()


# ======================================================================
#   PARAMETRII — citire / scriere / aplicare pe config
# ======================================================================

# Cheile optimizabile (corespund direct numelor din config.py)
OPTIMIZABLE_KEYS = (
    "NEAR_BREAKOUT_MAX_DISTANCE_PCT",
    "VOLUME_RATIO_THRESHOLD",
    "MIN_EMA10_SLOPE_PCT",
    "RSI_MIN",
    "RSI_MAX",
    "ADX_MIN",
)


def baseline_params() -> dict[str, float]:
    """Parametrii de bază: valorile curente din config.py."""
    return {
        "NEAR_BREAKOUT_MAX_DISTANCE_PCT": float(config.NEAR_BREAKOUT_MAX_DISTANCE_PCT),
        "VOLUME_RATIO_THRESHOLD": float(config.VOLUME_RATIO_THRESHOLD),
        "MIN_EMA10_SLOPE_PCT": float(config.MIN_EMA10_SLOPE_PCT),
        "RSI_MIN": float(config.RSI_MIN),
        "RSI_MAX": float(config.RSI_MAX),
        "ADX_MIN": float(config.ADX_MIN),
    }


def params_key(params: dict[str, float]) -> str:
    """Cheie canonică pentru comparare / istoric."""
    return json.dumps({k: round(float(params[k]), 4) for k in OPTIMIZABLE_KEYS}, sort_keys=True)


def apply_params_to_config(params: dict[str, float]) -> None:
    """Aplică parametrii optimi pe modulul config → scannerul îi folosește imediat."""
    for key in OPTIMIZABLE_KEYS:
        if key in params:
            setattr(config, key, float(params[key]))
    logger.info("Parametri aplicați: rsi=%s-%s dist=%.2f%% vol=%.2fx slope=%.3f%% adx=%.1f",
                config.RSI_MIN, config.RSI_MAX, config.NEAR_BREAKOUT_MAX_DISTANCE_PCT,
                config.VOLUME_RATIO_THRESHOLD, config.MIN_EMA10_SLOPE_PCT, config.ADX_MIN)


# ======================================================================
#   PRELUARE DATE (cu tăiere la o anumită oră pentru backtest)
# ======================================================================

def fetch_ohlcv_until(exchange, symbol: str, timeframe: str, limit: int, end_ts: pd.Timestamp) -> pd.DataFrame:
    """Descarcă OHLCV și reține doar lumânările cu timestamp <= end_ts."""
    raw = with_retries(exchange.fetch_ohlcv, symbol, timeframe, limit=limit)
    if not raw:
        return pd.DataFrame()
    df = prepare_ohlcv_df(raw)
    df = df[df["timestamp"] <= end_ts].reset_index(drop=True)
    return df
# ======================================================================
#   POSITION — o singură poziție simulată (TP / SL / trailing / EOD)
# ======================================================================

class Position:
    """O poziție long simulată, cu exit strategy din config."""

    def __init__(
        self,
        symbol: str,
        entry_price: float,
        size_usdc: float,
        entry_time: datetime,
        fee_rate: float,
        tp_pct: float = None,
        sl_pct: float = None,
        trailing_enabled: bool = None,
        trailing_arm_pct: float = None,
        trailing_step_pct: float = None,
    ) -> None:
        self.symbol = symbol
        self.entry_price = float(entry_price)
        self.size_usdc = float(size_usdc)
        self.quantity = self.size_usdc / self.entry_price
        self.entry_time = entry_time
        self.fee_rate = fee_rate

        self.tp_pct = float(tp_pct) if tp_pct is not None else float(config.TAKE_PROFIT_PCT)
        self.sl_pct = float(sl_pct) if sl_pct is not None else float(config.STOP_LOSS_PCT)
        self.trailing_enabled = bool(trailing_enabled) if trailing_enabled is not None else bool(config.TRAILING_ENABLED)
        self.trailing_arm_pct = float(trailing_arm_pct) if trailing_arm_pct is not None else float(config.TRAILING_ARM_PCT)
        self.trailing_step_pct = float(trailing_step_pct) if trailing_step_pct is not None else float(config.TRAILING_STEP_PCT)

        self.peak_price = self.entry_price        # high-water mark
        self.trailing_armed = False
        self.trailing_stop = 0.0
        self.status = "open"
        self.exit_price: Optional[float] = None
        self.exit_time: Optional[datetime] = None
        self.exit_reason: Optional[str] = None

    # --- prețuri prag ---
    @property
    def tp_price(self) -> float:
        return self.entry_price * (1.0 + self.tp_pct)

    @property
    def sl_price(self) -> float:
        return self.entry_price * (1.0 - self.sl_pct)

    def current_pnl_gross(self, price: float) -> float:
        """Profit brut (fără comisioane) pentru prețul dat."""
        return (price - self.entry_price) * self.quantity

    # --- evaluare la un preț curent -> motiv de ieșire sau None ---
    def evaluate(self, price: float) -> Optional[str]:
        """Verifică TP / SL / trailing la un preț. Actualizează peak-ul.

        Returnează motivul ieșirii: 'take_profit', 'stop_loss', 'trailing_stop' sau None.
        """
        price = float(price)

        # Actualizează high-water mark și armarea trailing-ului
        if price > self.peak_price:
            self.peak_price = price
        if self.trailing_enabled and not self.trailing_armed:
            if self.peak_price >= self.entry_price * (1.0 + self.trailing_arm_pct):
                self.trailing_armed = True
                self.trailing_stop = max(
                    self.entry_price,  # breakeven după armare
                    self.peak_price * (1.0 - self.trailing_step_pct),
                )

        if self.trailing_armed:
            # trailing-ul urcă, nu coboară niciodată sub breakeven
            self.trailing_stop = max(
                self.trailing_stop,
                self.entry_price,
                self.peak_price * (1.0 - self.trailing_step_pct),
            )

        # Ordinea evaluării: SL / trailing (păstrează mai mult profit) apoi TP.
        if price <= self.sl_price:
            return "stop_loss"
        if self.trailing_armed and price <= self.trailing_stop:
            return "trailing_stop"
        if price >= self.tp_price:
            return "take_profit"
        return None

    def close(self, exit_price: float, reason: str, exit_time: datetime) -> dict[str, Any]:
        """Închide poziția și calculează profiturile NET, cu ambele comisioane."""
        self.exit_price = float(exit_price)
        self.exit_reason = reason
        self.exit_time = exit_time
        self.status = "closed"

        buy_fee = self.size_usdc * self.fee_rate
        gross_notional = self.quantity * self.exit_price
        sell_fee = gross_notional * self.fee_rate
        gross_pnl = gross_notional - self.size_usdc
        net_pnl = gross_pnl - buy_fee - sell_fee

        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "amount_usdc": self.size_usdc,
            "gross_pnl": gross_pnl,
            "buy_fee": buy_fee,
            "sell_fee": sell_fee,
            "total_fees": buy_fee + sell_fee,
            "net_pnl": net_pnl,
            "exit_reason": reason,
            "entry_time": iso_ts(self.entry_time),
            "exit_time": iso_ts(exit_time),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "size_usdc": self.size_usdc,
            "quantity": self.quantity,
            "entry_time": iso_ts(self.entry_time),
            "peak_price": self.peak_price,
            "trailing_armed": self.trailing_armed,
            "trailing_stop": self.trailing_stop,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "status": self.status,
            "exit_price": self.exit_price,
            "exit_time": iso_ts(self.exit_time) if self.exit_time else None,
            "exit_reason": self.exit_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Position":
        pos = cls(
            symbol=data["symbol"],
            entry_price=data["entry_price"],
            size_usdc=data["size_usdc"],
            entry_time=datetime.fromisoformat(data["entry_time"]),
            fee_rate=config.FEE_RATE,
            tp_pct=data.get("tp_pct"),
            sl_pct=data.get("sl_pct"),
            trailing_enabled=data.get("trailing_enabled"),
            trailing_arm_pct=data.get("trailing_arm_pct"),
            trailing_step_pct=data.get("trailing_step_pct"),
        )
        pos.peak_price = data.get("peak_price", pos.entry_price)
        pos.trailing_armed = data.get("trailing_armed", False)
        pos.trailing_stop = data.get("trailing_stop", 0.0)
        pos.status = data.get("status", "open")
        if data.get("exit_price") is not None:
            pos.exit_price = data["exit_price"]
            pos.exit_reason = data.get("exit_reason")
            pos.exit_time = datetime.fromisoformat(data["exit_time"]) if data.get("exit_time") else None
        return pos


# ======================================================================
#   PORTFOLIO — max 2 poziții, 50 USDC fiecare, persistare JSON
# ======================================================================

class Portfolio:
    """Portofoliu paper-trading cu max. config.MAX_POSITIONS poziții simultane."""

    def __init__(self, path: str = None) -> None:
        self.path = path or config.PAPER_STATE_PATH
        self.max_positions = int(config.MAX_POSITIONS)
        self.position_size_usdc = float(config.POSITION_SIZE_USDC)
        self.fee_rate = float(config.FEE_RATE)
        self.positions: dict[str, Position] = {}
        self.closed_trades: list[dict[str, Any]] = []
        self.last_close_by_symbol: dict[str, str] = {}
        self.pause_entries_until: Optional[datetime] = None

    # -------- persistare --------
    def load(self) -> "Portfolio":
        state = load_json_state(self.path, {})
        for sym, pdata in state.get("positions", {}).items():
            try:
                self.positions[sym] = Position.from_dict(pdata)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Poziție invalidă în stare pentru %s: %s — ignorată.", sym, exc)
        self.closed_trades = state.get("closed_trades", [])
        self.last_close_by_symbol = state.get("last_close_by_symbol", {})
        pause = state.get("pause_entries_until")
        self.pause_entries_until = datetime.fromisoformat(pause) if pause else None
        return self

    def save(self) -> None:
        save_json_state(self.path, self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "positions": {sym: p.to_dict() for sym, p in self.positions.items()},
            "closed_trades": self.closed_trades,
            "last_close_by_symbol": self.last_close_by_symbol,
            "pause_entries_until": iso_ts(self.pause_entries_until) if self.pause_entries_until else None,
        }
# -------- interogare --------
    def open_count(self) -> int:
        return len(self.positions)

    def can_open(self) -> bool:
        return self.open_count() < self.max_positions

    def is_open(self, symbol: str) -> bool:
        return symbol in self.positions

    def realized_pnl_today(self, day: datetime) -> float:
        """Suma profitului net realizat într-o zi dată."""
        day_start = day_start_utc(day)
        day_end = day_end_utc(day)
        total = 0.0
        for trade in self.closed_trades:
            try:
                t = datetime.fromisoformat(trade["exit_time"])
            except (KeyError, ValueError, TypeError):
                continue
            if day_start <= t <= day_end:
                total += float(trade.get("net_pnl", 0.0))
        return total

    def in_cooldown(self, symbol: str, now: datetime) -> bool:
        last = self.last_close_by_symbol.get(symbol)
        if not last:
            return False
        try:
            last_close = datetime.fromisoformat(last)
        except ValueError:
            return False
        return (now - last_close) < timedelta(hours=config.COOLDOWN_HOURS)

    def entries_paused(self, now: datetime) -> bool:
        return self.pause_entries_until is not None and now < self.pause_entries_until

    # -------- acțiuni --------
    def open_position(self, symbol: str, entry_price: float, entry_time: datetime) -> Position:
        if not self.can_open():
            raise RuntimeError(f"Maxim {self.max_positions} poziții. Nu pot deschide {symbol}.")
        if self.is_open(symbol):
            raise RuntimeError(f"{symbol} este deja deschis.")
        pos = Position(
            symbol=symbol,
            entry_price=entry_price,
            size_usdc=self.position_size_usdc,
            entry_time=entry_time,
            fee_rate=self.fee_rate,
        )
        self.positions[symbol] = pos
        logger.info("🔓 PAPER OPEN %s @ %.8f (%.2f USDC / %.6f %s)",
                    symbol, entry_price, self.position_size_usdc, pos.quantity, symbol.split("/")[0])
        return pos

    def close_position(self, symbol: str, exit_price: float, reason: str, exit_time: datetime) -> dict[str, Any]:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            raise KeyError(f"{symbol} nu are poziție deschisă.")
        trade = pos.close(exit_price, reason, exit_time)
        self.closed_trades.append(trade)
        self.last_close_by_symbol[symbol] = iso_ts(exit_time)
        logger.info("🔒 PAPER CLOSE %s @ %.8f | motiv=%s | net=%+.4f USDC",
                    symbol, exit_price, reason, trade["net_pnl"])
        # Drawdown protection: dacă pierderile zilei depășesc pragul → pauză intrări
        day_pnl = self.realized_pnl_today(exit_time)
        if day_pnl <= -config.MAX_DRAWDOWN_PCT * config.TOTAL_CAPITAL_USDC:
            self.pause_entries_until = exit_time + timedelta(hours=config.DRAWDOWN_PAUSE_HOURS)
            logger.warning("⛔ Drawdown zilnic %+.2f USDC → pauză intrări %d ore.",
                           day_pnl, config.DRAWDOWN_PAUSE_HOURS)
        return trade

    def all_open_positions(self) -> list[Position]:
        return list(self.positions.values())
# ======================================================================
#   BACKTESTER — reia ziua anterioară cu date OHLCV reale
# ======================================================================

class Backtester:
    """Reia o zi de tranzacționare (intra + TP/SL/trailing/EOD) folosind date reale.

    Datele de semnal sunt tăiate la închiderea zilei precedente ziua `day`,
    iar simularea folosește lumânările 1h ale zilei `day`.
    """

    def __init__(self, exchange, symbols: list[str], day: datetime) -> None:
        self.exchange = exchange
        self.symbols = list(symbols)
        self.day = day
        self.signal_end = day_start_utc(day) - timedelta(seconds=1)
        self.day_start = day_start_utc(day)
        self.day_end = day_end_utc(day)
        self.features: list[dict[str, Any]] = []
        self._load_features()

    # -------- încărcare caracteristici pe simbol --------
    def _load_features(self) -> None:
        for symbol in self.symbols:
            try:
                daily_df = fetch_ohlcv_until(self.exchange, symbol, "1d",
                                             config.DAILY_LIMIT + 40, self.signal_end)
                h4_df = fetch_ohlcv_until(self.exchange, symbol, "4h",
                                          config.H4_LIMIT + 20, self.signal_end)
                h1_raw = with_retries(self.exchange.fetch_ohlcv, symbol,
                                      "1h", limit=config.BACKTEST_1H_LIMIT)
                if not h1_raw or daily_df.empty or h4_df.empty:
                    continue
                h1_all = prepare_ohlcv_df(h1_raw)
                h1_sig = h1_all[h1_all["timestamp"] <= self.signal_end].reset_index(drop=True)
                h1_day = h1_all[
                    (h1_all["timestamp"] >= self.day_start)
                    & (h1_all["timestamp"] <= self.day_end)
                ].reset_index(drop=True)
                if h1_sig.empty or h1_day.empty:
                    continue
                feats = self._extract_features(symbol, daily_df, h4_df, h1_sig, h1_day)
                if feats is not None:
                    self.features.append(feats)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Backtest: skip %s: %s", symbol, exc)
        logger.info("Backtest: %d/%d simboluri cu date pentru %s",
                    len(self.features), len(self.symbols), self.day.date())

    def _extract_features(
        self,
        symbol: str,
        daily_df: pd.DataFrame,
        h4_df: pd.DataFrame,
        h1_sig: pd.DataFrame,
        h1_day: pd.DataFrame,
    ) -> Optional[dict[str, Any]]:
        """Calculează o singură dată indicatorii necesari (nu depind de parametrii optimizabili)."""
        daily = add_ema_columns(daily_df)
        daily_ok = is_daily_bullish(daily) or is_daily_early_trend(daily)
        ema10_slope = calculate_ema10_slope_pct(daily, lookback=config.EMA_SLOPE_LOOKBACK)

        macd_line, signal_line = calculate_macd_values(h4_df)
        macd_spread = None
        macd_ok = False
        if macd_line is not None and signal_line is not None:
            macd_spread = (macd_line - signal_line) / max(abs(signal_line), abs(macd_line), 1e-8)
            macd_ok = macd_line > signal_line and macd_spread >= config.MIN_MACD_SPREAD_RATIO

        adx = calculate_adx_value(h4_df, period=config.ADX_PERIOD)
        volume_ratio = calculate_volume_ratio(h4_df, period=config.VOLUME_SMA_PERIOD)
        distance = calculate_distance_to_breakout_pct(h4_df, lookback=config.BREAKOUT_LOOKBACK_4H)
        rsi_cur, rsi_prev = calculate_rsi_pair(h1_sig, period=config.RSI_PERIOD)
        vol_up = len(h1_sig) >= 2 and float(h1_sig["volume"].iloc[-1]) > float(h1_sig["volume"].iloc[-2])

        return {
            "symbol": symbol,
            "daily_ok": daily_ok,
            "ema10_slope": ema10_slope,
            "macd_spread": macd_spread,
            "macd_ok": macd_ok,
            "adx": adx,
            "volume_ratio": volume_ratio,
            "distance": distance,
            "rsi": rsi_cur,
            "rsi_prev": rsi_prev,
            "rsi_rising": rsi_cur is not None and rsi_prev is not None and rsi_cur > rsi_prev,
            "vol_up": vol_up,
            "sim": h1_day.reset_index(drop=True),
        }
# -------- calificare pentru un set de parametri --------
    @staticmethod
    def qualifies(feats: dict[str, Any], params: dict[str, float]) -> bool:
        """Aplică exact filtrele scannerului, cu parametrii dați."""
        if not feats["daily_ok"]:
            return False
        if feats["ema10_slope"] is None or feats["ema10_slope"] < params["MIN_EMA10_SLOPE_PCT"]:
            return False
        if not feats["macd_ok"]:
            return False
        if feats["volume_ratio"] is None or feats["volume_ratio"] < params["VOLUME_RATIO_THRESHOLD"]:
            return False
        if feats["distance"] is None or not (0 <= feats["distance"] <= params["NEAR_BREAKOUT_MAX_DISTANCE_PCT"]):
            return False
        if feats["adx"] is None or feats["adx"] < params["ADX_MIN"]:
            return False
        if feats["rsi"] is None or not (params["RSI_MIN"] <= feats["rsi"] <= params["RSI_MAX"]):
            return False
        if not feats["rsi_rising"] or not feats["vol_up"]:
            return False
        return True

    def growth_score_for(self, feats: dict[str, Any]) -> Optional[float]:
        if (
            feats["ema10_slope"] is None
            or feats["macd_spread"] is None
            or feats["volume_ratio"] is None
            or feats["distance"] is None
        ):
            return None
        return calculate_growth_score(
            ema10_slope_pct=feats["ema10_slope"],
            macd_spread_ratio=feats["macd_spread"],
            volume_ratio=feats["volume_ratio"],
            distance_to_breakout_pct=feats["distance"],
            rsi_value=feats["rsi"],
            use_1h_filter=config.USE_1H_FILTER,
        )
# -------- simulare intraday (1h) --------
    @staticmethod
    def simulate_day(sim_df: pd.DataFrame, params: dict[str, float]) -> Optional[dict[str, Any]]:
        """Simulează poziția pe parcursul zilei. Intrarea = openul primei lumânări 1h."""
        if sim_df is None or sim_df.empty:
            return None

        entry_price = float(sim_df["open"].iloc[0])
        size = config.POSITION_SIZE_USDC
        fee = config.FEE_RATE
        quantity = size / entry_price
        buy_fee = size * fee

        tp_price = entry_price * (1.0 + config.TAKE_PROFIT_PCT)
        sl_price = entry_price * (1.0 - config.STOP_LOSS_PCT)
        arm_price = entry_price * (1.0 + config.TRAILING_ARM_PCT)

        peak = entry_price
        trailing_armed = False
        trailing_stop = 0.0
        exit_price = None
        exit_reason = None
        min_price = entry_price  # pentru drawdown

        for _, candle in sim_df.iterrows():
            low = float(candle["low"])
            high = float(candle["high"])
            min_price = min(min_price, low)

            # 1) SL hard intra-bar
            if low <= sl_price:
                exit_price, exit_reason = sl_price, "stop_loss"
                break
            # 2) trailing stop intra-bar (dacă e armat)
            if trailing_armed and low <= trailing_stop:
                exit_price, exit_reason = trailing_stop, "trailing_stop"
                break
            # 3) TP intra-bar
            if high >= tp_price:
                exit_price, exit_reason = tp_price, "take_profit"
                break
            # 4) update peak / armează trailing
            if high > peak:
                peak = high
            if config.TRAILING_ENABLED and not trailing_armed and peak >= arm_price:
                trailing_armed = True
                trailing_stop = max(entry_price, peak * (1.0 - config.TRAILING_STEP_PCT))

        if exit_price is None:
            exit_price = float(sim_df["close"].iloc[-1])
            exit_reason = "eod"

        gross_notional = quantity * exit_price
        sell_fee = gross_notional * fee
        gross_pnl = gross_notional - size
        net_pnl = gross_pnl - buy_fee - sell_fee
        drawdown_usdc = (min_price - entry_price) / entry_price * size  # <= 0

        return {
            "symbol": None,  # setat de run_combo
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "amount_usdc": size,
            "gross_pnl": gross_pnl,
            "buy_fee": buy_fee,
            "sell_fee": sell_fee,
            "total_fees": buy_fee + sell_fee,
            "net_pnl": net_pnl,
            "exit_reason": exit_reason,
            "drawdown_usdc": drawdown_usdc,
        }

    # -------- rulează o combinație de parametri pe ziua backtest --------
    def run_combo(self, params: dict[str, float]) -> dict[str, Any]:
        candidates = []
        for feats in self.features:
            if self.qualifies(feats, params):
                score = self.growth_score_for(feats)
                if score is not None:
                    candidates.append((score, feats))

        candidates.sort(key=lambda x: x[0], reverse=True)
        selected = candidates[: config.MAX_POSITIONS]

        trades: list[dict[str, Any]] = []
        total_net = 0.0
        total_gross = 0.0
        total_fees = 0.0
        max_dd = 0.0

        for score, feats in selected:
            trade = self.simulate_day(feats["sim"], params)
            if trade is None:
                continue
            trade["symbol"] = feats["symbol"]
            trade["score"] = score
            trades.append(trade)
            total_net += trade["net_pnl"]
            total_gross += trade["gross_pnl"]
            total_fees += trade["total_fees"]
            max_dd = min(max_dd, trade["drawdown_usdc"])

        wins = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
        losses = sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0)
        profit_factor = wins / abs(losses) if abs(losses) > 1e-9 else (99.0 if wins > 0 else 0.0)

        return {
            "pnl_net": total_net,
            "pnl_gross": total_gross,
            "fees": total_fees,
            "trades": trades,
            "max_drawdown_usdc": max_dd,
            "profit_factor": profit_factor,
            "symbols": [t["symbol"] for t in trades],
            "params": params,
        }
# ======================================================================
#   PARAMETEROPTIMIZER — căutare + medie ponderată pe istoric ("smart")
# ======================================================================

class ParameterOptimizer:
    """Generează combinații de parametri, le evaluează pe backtest și alege
    cea mai bună (profit net), cu tie-break Profit Factor / drawdown, blend-uit
    cu performanța medie a combinației din ultimele 3-5 zile (anti-overfitting)."""

    def __init__(self, backtester: Backtester = None) -> None:
        self.backtester = backtester

    # -------- generare combinații --------
    def generate_combos(self, day: datetime, explore: bool = False) -> list[dict[str, float]]:
        base = baseline_params()
        ranges = config.SEARCH_RANGES
        rsi_combos = ranges.get("RSI_COMBOS", [(55, 75)])

        def _make(near, vol, slope, rmin, rmax, adx_):
            return {
                "NEAR_BREAKOUT_MAX_DISTANCE_PCT": float(near),
                "VOLUME_RATIO_THRESHOLD": float(vol),
                "MIN_EMA10_SLOPE_PCT": float(slope),
                "RSI_MIN": float(rmin),
                "RSI_MAX": float(rmax),
                "ADX_MIN": float(adx_),
            }

        # Pool complet (grid)
        pool = []
        for near in ranges["NEAR_BREAKOUT_MAX_DISTANCE_PCT"]:
            for vol in ranges["VOLUME_RATIO_THRESHOLD"]:
                for slope in ranges["MIN_EMA10_SLOPE_PCT"]:
                    for rmin, rmax in rsi_combos:
                        for adx_ in ranges["ADX_MIN"]:
                            pool.append(_make(near, vol, slope, rmin, rmax, adx_))

        combos = [base]
        rng = random.Random((day.toordinal() * 2654 + 13) % 2 ** 31)
        n_search = max(1, config.OPTIMIZER_SEARCH_COMBOS)
        if pool:
            combos.extend(rng.sample(pool, min(n_search, len(pool))))

        if explore:
            for _ in range(max(1, config.OPTIMIZER_RANDOM_COMBOS)):
                near = rng.choice(ranges["NEAR_BREAKOUT_MAX_DISTANCE_PCT"])
                vol = rng.choice(ranges["VOLUME_RATIO_THRESHOLD"])
                slope = rng.choice(ranges["MIN_EMA10_SLOPE_PCT"])
                rmin, rmax = rng.choice(rsi_combos)
                adx_ = rng.choice(ranges["ADX_MIN"])
                combos.append(_make(near, vol, slope, rmin, rmax, adx_))

        # dedupe
        seen = set()
        out = []
        for c in combos:
            k = params_key(c)
            if k not in seen:
                seen.add(k)
                out.append(c)
        return out
# -------- evaluare + selecție --------
    def evaluate_all(self, combos: list[dict[str, float]]) -> dict[str, dict]:
        """Rulează backtest-ul pentru fiecare combinație. Returnează {params_key: result}."""
        results: dict[str, dict] = {}
        for combo in combos:
            try:
                res = self.backtester.run_combo(combo)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Combinație eșuată %s: %s", combo, exc)
                continue
            results[params_key(combo)] = res
        return results

    @staticmethod
    def _history_pnl_for(combo: dict[str, float], history: list[dict], current_day: datetime) -> float:
        """PNL mediu al combinației în istoric (ultimele OPTIMIZER_BLEND_DAYS zile)."""
        window_start = day_start_utc(current_day) - timedelta(days=config.OPTIMIZER_BLEND_DAYS)
        key = params_key(combo)
        pnls = []
        for entry in history:
            try:
                d = datetime.fromisoformat(entry["date"])
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
            except (KeyError, ValueError, TypeError):
                continue
            if d < window_start:
                continue
            if params_key(entry.get("params", {})) == key and entry.get("pnl_net") is not None:
                pnls.append(float(entry["pnl_net"]))
        return (sum(pnls) / len(pnls)) if pnls else 0.0

    def select_best(
        self,
        results: dict[str, dict],
        history: list[dict],
        current_day: datetime,
    ) -> dict[str, float]:
        """Alege combinația cu cel mai bun scor blend (backtest + istoric).

        Scor = W * pnl_backtest + (1-W) * pnl_medie_istoric. Tie-break: Profit Factor,
        apoi drawdown minim.
        """
        best_key = None
        best_score = None
        best_tie = None
        for key, res in results.items():
            backtest_pnl = float(res.get("pnl_net", 0.0))
            combo = res.get("params", {})
            history_pnl = self._history_pnl_for(combo, history, current_day)
            score = (
                config.OPTIMIZER_RECENT_WEIGHT * backtest_pnl
                + (1.0 - config.OPTIMIZER_RECENT_WEIGHT) * history_pnl
            )
            pf = float(res.get("profit_factor", 0.0))
            dd = float(res.get("max_drawdown_usdc", 0.0))
            tie = (pf, -dd, key)  # PF cât mai mare, drawdown cât mai mic

            if best_score is None or score > best_score or (score == best_score and tie > best_tie):
                best_key, best_score, best_tie = key, score, tie

        if best_key is None:
            logger.warning("Nicio combinație evaluată — mă întorc la baseline.")
            return baseline_params()

        best = results[best_key]["params"]
        logger.info("Optimizer a ales scor=%.2f %s (pnl=%+.2f pf=%.2f dd=%+.2f)",
                    best_score, params_key(best),
                    results[best_key]["pnl_net"], results[best_key]["profit_factor"],
                    results[best_key]["max_drawdown_usdc"])
        return best
# ======================================================================
#   ORCHESTRARE — fluxul zilnic integrat în scanner.py
# ======================================================================

def _trading_state() -> dict[str, Any]:
    return load_json_state(config.TRADING_STATE_PATH, {})


def _save_trading_state(state: dict[str, Any]) -> None:
    save_json_state(config.TRADING_STATE_PATH, state)


def _load_history() -> list[dict]:
    return load_json_state(config.STRATEGY_HISTORY_PATH, {"entries": []}).get("entries", [])


def _save_history(entries: list[dict]) -> None:
    save_json_state(config.STRATEGY_HISTORY_PATH, {"entries": entries})


def optimize_daily(exchange, symbols: list[str], now: datetime) -> bool:
    """Recalibrare zilnică: backtest pe ziua precedentă + alegerea parametrilor.

    Rulează o singură dată pe zi (guard pe TRADING_STATE_PATH). Dacă parametrii
    sunt deja optimizați azi, doar reaplică parametrii salvați pe config.
    """
    setup_file_logging()
    if not config.BACKTEST_ENABLED:
        return False

    today = day_start_utc(now)
    state = _trading_state()
    if state.get("last_backtest_date") == today.date().isoformat():
        saved = load_json_state(config.OPTIMIZED_PARAMS_PATH, {})
        if saved.get("params"):
            apply_params_to_config(saved["params"])
        return False

    backtest_day = today - timedelta(days=1)
    universe = list(symbols)[: config.BACKTEST_SYMBOLS_LIMIT]
    logger.info("=== BACKTEST recalibrare pentru %s (%d simboluri) ===",
                backtest_day.date(), len(universe))

    backtester = Backtester(exchange, universe, backtest_day)
    if not backtester.features:
        logger.warning("Nicio dată pentru backtest — păstrez parametrii curenti.")
        state["last_backtest_date"] = today.date().isoformat()
        _save_trading_state(state)
        return False

    explore_due = False
    if config.OPTIMIZER_EXPLORE_WEEKLY:
        # explorare aproximativ o dată pe săptămână
        explore_due = (backtest_day.toordinal() % 7 == 0)
    explore_due = explore_due or bool(state.get("force_explore", False))
    if explore_due:
        logger.info("🧪 ZI DE EXPLORARE — se testează și combinații aleatorii.")

    optimizer = ParameterOptimizer(backtester)
    combos = optimizer.generate_combos(backtest_day, explore=explore_due)
    logger.info("Testez %d combinații de parametri.", len(combos))
    results = optimizer.evaluate_all(combos)

    best = optimizer.select_best(results, _load_history(), backtest_day)
    best_res = results.get(params_key(best), {})

    # Salvăm parametrii + istoric (pentru media ponderată din următoarele zile)
    save_json_state(config.OPTIMIZED_PARAMS_PATH, {
        "params": best,
        "updated_at": iso_ts(now),
        "backtest_day": backtest_day.date().isoformat(),
        "pnl_net": best_res.get("pnl_net"),
        "profit_factor": best_res.get("profit_factor"),
        "max_drawdown_usdc": best_res.get("max_drawdown_usdc"),
        "explore": explore_due,
    })
    history = _load_history()
    history.append({
        "date": backtest_day.date().isoformat(),
        "params": best,
        "pnl_net": best_res.get("pnl_net"),
        "profit_factor": best_res.get("profit_factor"),
        "max_drawdown_usdc": best_res.get("max_drawdown_usdc"),
        "trades": [t["symbol"] for t in best_res.get("trades", [])],
    })
    history = history[-config.OPTIMIZER_HISTORY_DAYS:]
    _save_history(history)

    state["last_backtest_date"] = today.date().isoformat()
    state["force_explore"] = False
    _save_trading_state(state)

    apply_params_to_config(best)
    logger.info("✅ Recalibrare finalizată: %s", params_key(best))
    return True
def _current_price(exchange, symbol: str, fallback_close: Optional[float] = None) -> Optional[float]:
    """Preț curent: ticker-ul real (last) sau, la nevoie, ultimul close 1h."""
    price = fetch_ticker_safe(exchange, symbol)
    if price is not None:
        return price
    if fallback_close is not None and fallback_close > 0:
        return float(fallback_close)
    return None


def manage_trading(exchange, score_pool: list[dict], now: datetime) -> dict[str, Any]:
    """Ciclu de trading PAPER: gestionează pozițiile existente (TP/SL/trailing/EOD),
    deschide poziții noi (top-2 calificate după scor, max 2 simultan) și generează
    raportul zilnic. NU trimite ordine reale."""
    setup_file_logging()
    portfolio = Portfolio().load()
    summary: dict[str, Any] = {"actions": [], "open": 0, "closed": 0}

    eod_reached = now >= parse_hhmm(now, config.EOD_FORCE_CLOSE_AT)

    # ---- 1) Gestionare ieșiri pentru pozițiile deschise ----
    for symbol in list(portfolio.positions.keys()):
        diag = next((d for d in score_pool if d.get("symbol") == symbol), None)
        fallback = diag.get("price") if diag else None
        price = _current_price(exchange, symbol, fallback)
        if price is None:
            logger.warning("Nu pot obține preț pentru %s — păstrez poziția.", symbol)
            continue
        reason = None
        if eod_reached:
            reason = "eod_force_close"
        else:
            reason = portfolio.positions[symbol].evaluate(price)
        if reason:
            trade = portfolio.close_position(symbol, price, reason, now)
            summary["closed"] += 1
            summary["actions"].append({"type": "close", **trade})
        else:
            summary["actions"].append({
                "type": "hold",
                "symbol": symbol,
                "price": price,
                "pnl_gross": portfolio.positions[symbol].current_pnl_gross(price),
            })

    # ---- 2) Raport zilnic pentru ziua care s-a închis (dacă lipsește) ----
    report_day = (now - timedelta(hours=4)).date()
    report_path = Path(config.REPORTS_DIR) / f"daily_report_{report_day.isoformat()}.json"
    if not report_path.exists():
        report = generate_daily_report(portfolio, score_pool, report_day, now)
        if report:
            summary["actions"].append({"type": "report", "path": str(report_path)})
            if config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID:
                sent = send_daily_report_telegram(report)
                summary["actions"].append({"type": "telegram_report", "sent": sent})

    # ---- 3) Deschidere poziții noi (doar în fereastra de intrare) ----
    entry_cutoff = parse_hhmm(now, config.ENTRY_CUTOFF_AT)
    if now < entry_cutoff and not eod_reached and not portfolio.entries_paused(now):
        qualified = [
            d for d in score_pool
            if d.get("qualified")
            and d.get("growth_score") is not None
            and d.get("ema_slope_ok")
            and not portfolio.is_open(d["symbol"])
            and not portfolio.in_cooldown(d["symbol"], now)
        ]
        qualified.sort(key=lambda d: d["growth_score"] or 0.0, reverse=True)
        for diag in qualified:
            if not portfolio.can_open():
                break
            price = _current_price(exchange, diag["symbol"], diag.get("price"))
            if price is None or price <= 0:
                logger.warning("Preț invalid pentru intrare %s.", diag["symbol"])
                continue
            try:
                portfolio.open_position(diag["symbol"], price, now)
                summary["actions"].append({
                    "type": "open",
                    "symbol": diag["symbol"],
                    "price": price,
                    "score": diag["growth_score"],
                })
            except RuntimeError as exc:
                logger.warning("Intrare blocată: %s", exc)

    portfolio.save()
    summary["open"] = portfolio.open_count()
    logger.info("Trading cycle: %s", summary)
    return summary
def generate_daily_report(
    portfolio: Portfolio,
    score_pool: list[dict],
    report_day: datetime.date,
    now: datetime,
) -> Optional[dict[str, Any]]:
    """Generează raportul zilnic JSON (profit net, tranzacții, parametri, ultimele 7 zile)."""
    day_start = datetime(report_day.year, report_day.month, report_day.day, tzinfo=timezone.utc)
    day_end = day_end_utc(day_start)

    trades = [
        t for t in portfolio.closed_trades
        if day_start <= datetime.fromisoformat(t["exit_time"]) <= day_end
    ]
    if not trades:
        logger.info("Nicio tranzacție închisă în ziua %s — raport gol, nu salvez.",
                    report_day.isoformat())
        return None

    total_net = sum(float(t["net_pnl"]) for t in trades)
    total_fees = sum(float(t["total_fees"]) for t in trades)

    # Performanță ultimele 7 zile din istoricul strategiei
    history = _load_history()
    last7 = [h for h in history if h.get("pnl_net") is not None]
    last7 = last7[-7:]

    top5 = sorted(
        [d for d in score_pool if d.get("growth_score") is not None],
        key=lambda d: d["growth_score"] or 0.0,
        reverse=True,
    )[:5]

    report = {
        "date": report_day.isoformat(),
        "generated_at": iso_ts(now),
        "net_profit_usdc": round(total_net, 4),
        "net_profit_pct": round(total_net / config.TOTAL_CAPITAL_USDC * 100.0, 4),
        "total_fees_usdc": round(total_fees, 4),
        "trades": trades,
        "params_used": {
            "TP_PCT": config.TAKE_PROFIT_PCT,
            "SL_PCT": config.STOP_LOSS_PCT,
            "TRAILING_ARM_PCT": config.TRAILING_ARM_PCT,
            "TRAILING_STEP_PCT": config.TRAILING_STEP_PCT,
            "FEE_RATE": config.FEE_RATE,
            **baseline_params(),
        },
        "last_7d": {
            "dates": [h.get("date") for h in last7],
            "pnl_list": [round(float(h["pnl_net"]), 4) for h in last7],
            "total_profit": round(sum(float(h["pnl_net"]) for h in last7), 4),
            "avg_daily": round(
                sum(float(h["pnl_net"]) for h in last7) / max(len(last7), 1), 4
            ),
            "std_daily": round(
                (sum((float(h["pnl_net"]) - sum(float(x["pnl_net"]) for x in last7) / max(len(last7), 1)) ** 2
                     for h in last7) / max(len(last7), 1)) ** 0.5, 4
            ) if last7 else 0.0,
        },
        "top5_ranking": [
            {
                "symbol": d["symbol"],
                "growth_score": d.get("growth_score"),
                "rsi_1h": d.get("rsi_1h"),
                "vol4h": d.get("vol4h"),
                "dist_breakout_pct": d.get("dist_breakout_pct"),
            }
            for d in top5
        ],
        "open_positions_end": portfolio.to_dict().get("positions", {}),
    }

    Path(config.REPORTS_DIR).mkdir(parents=True, exist_ok=True)
    save_json_state(str(Path(config.REPORTS_DIR) / f"daily_report_{report_day.isoformat()}.json"), report)
    logger.info("📊 Raport zilnic %s: net=%+.4f USDC (%s tranzacții).",
                report_day.isoformat(), total_net, len(trades))
    return report


def send_daily_report_telegram(report: dict[str, Any]) -> bool:
    """Trimite rezumatul raportului zilnic pe Telegram (dacă sunt configurate token/chat)."""
    try:
        from telegram_sender import send_telegram_message
    except ImportError:  # pragma: no cover
        logger.warning("telegram_sender indisponibil.")
        return False

    lines = ["📈 <b>Raport zilnic BinanceCloudBot</b>", ""]
    lines.append(f"Data: {report.get('date')}")
    lines.append(f"Profit net: <b>{report.get('net_profit_usdc', 0):+.4f} USDC</b> "
                 f"({report.get('net_profit_pct', 0):+.2f}% din {config.TOTAL_CAPITAL_USDC:.0f} USDC)")
    lines.append(f"Comisioane totale: {report.get('total_fees_usdc', 0):.4f} USDC")
    lines.append("")
    lines.append("Tranzacții:")
    for t in report.get("trades", []):
        lines.append(
            f"• {t.get('symbol')}: {t.get('entry_price'):.6f} → {t.get('exit_price'):.6f} | "
            f"motiv {t.get('exit_reason')} | net {t.get('net_pnl'):+.4f} USDC"
        )
    last7 = report.get("last_7d", {})
    lines.append("")
    lines.append(f"Ultimele 7 zile: total {last7.get('total_profit', 0):+.4f} USDC | "
                 f"medie {last7.get('avg_daily', 0):+.4f} | std {last7.get('std_daily', 0):.4f}")

    try:
        rows = [{"symbol": r["symbol"], "growth_score": r["growth_score"] or 0.0}
                for r in report.get("top5_ranking", [])]
        return send_telegram_message(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID, rows)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Telegram raport eșuat: %s", exc)
        return False
# ======================================================================
#   CLI — backtest separat cu date istorice reale (validare)
# ======================================================================

def run_backtest_cli(date_str: str, limit: int = None) -> int:
    """Rulează backtest + optimizare pentru o zi dată (YYYY-MM-DD), cu date reale.

    Folosit de backtest_validate.py pentru validarea modulului înainte de producție.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    setup_file_logging()
    import ccxt

    day = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    if limit:
        config.BACKTEST_SYMBOLS_LIMIT = limit

    exchange = ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    if config.PROXY_URL:
        exchange.proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL}
    with_retries(exchange.load_markets)

    from scanner import get_quote_symbols
    symbols = get_quote_symbols(exchange, config.PRIMARY_QUOTE_ASSETS)
    logger.info("CLI backtest: %s, univers %d simboluri, limit %d.",
                date_str, len(symbols), config.BACKTEST_SYMBOLS_LIMIT)

    backtester = Backtester(exchange, symbols[: config.BACKTEST_SYMBOLS_LIMIT], day)
    if not backtester.features:
        logger.error("Fără date pentru %s.", date_str)
        return 1

    optimizer = ParameterOptimizer(backtester)
    explore = day.toordinal() % 7 == 0
    combos = optimizer.generate_combos(day, explore=explore)
    results = optimizer.evaluate_all(combos)

    best = optimizer.select_best(results, _load_history(), day)
    best_res = results[params_key(best)]

    print("\n================ BACKTEST REZULTAT ================")
    print(f"Ziua: {date_str} | Combinații testate: {len(results)}")
    print(f"Best params: {best}")
    print(f"PNL net: {best_res['pnl_net']:+.4f} USDC | gross: {best_res['pnl_gross']:+.4f} | "
          f"fees: {best_res['fees']:.4f}")
    print(f"Profit factor: {best_res['profit_factor']:.2f} | "
          f"max drawdown: {best_res['max_drawdown_usdc']:+.4f} USDC")
    print(f"Tranzacții simulate: {best_res['trades']}")
    print("=" * 46)

    save_json_state(str(Path(config.REPORTS_DIR) / f"backtest_{date_str}.json"), {
        "date": date_str,
        "best_params": best,
        "pnl_net": best_res["pnl_net"],
        "pnl_gross": best_res["pnl_gross"],
        "fees": best_res["fees"],
        "profit_factor": best_res["profit_factor"],
        "max_drawdown_usdc": best_res["max_drawdown_usdc"],
        "trades": best_res["trades"],
        "combos_tested": len(results),
    })
    return 0


if __name__ == "__main__":
    import sys

    import ccxt
    ccxt  # noqa

    if len(sys.argv) < 2:
        print("Folosire: python trader.py --backtest YYYY-MM-DD [--limit N]")
        raise SystemExit(2)
    args = sys.argv[1:]
    date_arg = None
    limit_arg = None
    if "--backtest" in args:
        date_arg = args[args.index("--backtest") + 1]
    if "--limit" in args:
        limit_arg = int(args[args.index("--limit") + 1])
    if date_arg is None:
        print("Folosire: python trader.py --backtest YYYY-MM-DD [--limit N]")
        raise SystemExit(2)
    raise SystemExit(run_backtest_cli(date_arg, limit_arg))