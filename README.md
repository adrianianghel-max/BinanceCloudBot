# Binance USDC Cloud Scanner + Paper Trading Bot

Automated Binance Spot USDC scanner **and PAPER trading bot** (Python 3.12) for GitHub Actions,
with Telegram alerts, daily backtest/recalibration ("smart trader") and daily reports.

> ⚠️ **100% PAPER / SIMULATED trading.** Nu se trimit ordine reale pe Binance.
> Toate intrările și ieșirile sunt simulate pe baza prețurilor OHLCV / ticker.

## Features

### Scanare (modulul de semnale — nemodificat funcțional)
- Scans all active Binance Spot symbols quoted in `USDC`
- Excludes leveraged tokens ending with: `UP`, `DOWN`, `BULL`, `BEAR`
- Daily filter: `EMA10 > EMA50 > EMA200`, `Close > EMA10`, EMA10 slope > 0.05%
- 4H filter: `MACD line > Signal line`, volume spike, proximity to breakout (≤ 3%),
  `ADX ≥ 20`
- 1H filter: `RSI(14) ∈ [55, 80]`, RSI rising, volume rising
- Transparent growth score (0–100%) and Top-5 Telegram alerts (`ALERT_ONLY_NEW`)

### Trading PAPER (modulul `trader.py`)
- Selectează **top-2 simboluri calificate după growth_score** (max 2 poziții simultane)
- **50 USDC per poziție** (capital total 100 USDC)
- **Take-profit +15%**, **Stop-loss −8%**, **trailing stop** (armat la +5%, pas 3%,
  niciodată sub breakeven), **închidere forțată la 23:59 UTC**
- **Comision 0.1% per tranzacție (buy & sell)** → profit net raportat
- Cooldown 48h per simbol, protecție la drawdown (pauză intrări), cooldown rotire
- Stare persistată în `paper_state.json` (supraviețuiește între rulări GitHub Actions)

### Backtest zilnic + recalibrare ("smart trader")
- La prima rulare a zilei, botul reia **ziua anterioară** cu date reale (OHLCV 1h/4h/1d)
- Testează variații pentru:
  - `NEAR_BREAKOUT_MAX_DISTANCE_PCT` (1.5%–5.0%)
  - `VOLUME_RATIO_THRESHOLD` (1.0–2.0)
  - `MIN_EMA10_SLOPE_PCT` (0.0–0.3)
  - `RSI_MIN/RSI_MAX` (50-70, 55-75, 55-80, 60-85)
  - `ADX_MIN` (15–30)
- Alege combinația cu cel mai mare **profit net** (tie-break: Profit Factor, apoi drawdown)
- **Medie ponderată** cu performanța din ultimele 3–5 zile (`strategy_history.json`)
- **Explorare săptămânală** cu combinații aleatorii (anti-stagnare / descoperire regim nou)
- Backtest limitat la `BACKTEST_SYMBOLS_LIMIT` (25 by default) → se încadrează în 5 minute
  în GitHub Actions

### Raportare zilnică
- `reports/daily_report_YYYY-MM-DD.json` cu:
  - profit net (USDC + % din 100 USDC), tranzacții (buy/sell/fee/net), parametrii folosiți
  - performanță cumulată ultimele 7 zile (total, medie zilnică, deviație standard)
  - ranking top-5 al simbolurilor scanate + pozițiile deschise la final de zi
- Rezumat pe Telegram (dacă `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` sunt setate)
- `trading.log` — log detaliat (rotativ)

## Project Structure

```
project/
|- scanner.py            # scan + semnale + integrare trading cycle
|- trader.py             # Position / Portfolio / Backtester / ParameterOptimizer
|- indicators.py
|- market_data.py        # fetch cu retry + staleness
|- telegram_sender.py
|- config.py
|- state_manager.py
|- backtest_validate.py  # CLI: validare backtest cu date reale
|- run_tests.py
|- tests/
|  |- test_indicators.py
|  |- test_market_data.py
|  `- test_trader.py
|- requirements.txt
|- last_alerts.json
|- config_state.json
|- paper_state.json       # (generat) stare paper trading
|- trading_state.json     # (generat) ultima recalibrare
|- optimized_params.json  # (generat) parametrii optimi
|- strategy_history.json  # (generat) istoric 7 zile
|- reports/               # (generat) daily_report_*.json + backtest_*.json
`- .github/
   `- workflows/
      |- scan.yml
      |- start_scan.yml
      `- stop_scan.yml
```

## Required GitHub Secrets

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

## Workflows

### 1) Run Scanner (`.github/workflows/scan.yml`)
- Schedule la fiecare **15 minute** (monitorizare TP/SL continuă)
- La prima rulare a zilei rulează **backtest + recalibrare**, apoi scanările folosesc
  parametrii optimizați
- Deschide poziții noi doar în fereastra de intrare (default 00:00–22:00 UTC),
  max 2 simultan; închide la TP/SL/trailing și forțat la 23:59 UTC
- Persistă toate fișierele de stare + rapoartele în repo

### 2) Start Auto Scan / 3) Stop Auto Scan — nemodificate (prin `config_state.json`)

## Local Run

```bash
python -m pip install -r requirements.txt
set TELEGRAM_TOKEN=your_token
set TELEGRAM_CHAT_ID=your_chat_id
python scanner.py
```

## Testare

```bash
python run_tests.py                 # unit tests (indicators, market_data, trader)
python backtest_validate.py --date 2026-08-28 --limit 10   # backtest cu date reale
```

## Notes

- Uses `ccxt` with `enableRateLimit=True`, retry and staleness checks
- **Paper trading only** — `LIVE_TRADING_ENABLED = False` în `config.py`
- La importul local al `ccxt` poate apărea o linie benignă
  `fatal: bad revision 'HEAD'` pe stderr (ecdsa version probe); nu afectează funcționarea.
- GitHub Actions: backtest-ul durează < 5 minute (`BACKTEST_SYMBOLS_LIMIT` ajustabil).
