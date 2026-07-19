# Binance USDC Cloud Scanner

Automated Binance Spot USDC scanner for GitHub Actions (Python 3.12), with Telegram alerts and stateful smart notifications.

## Features

- Scans all active Binance Spot symbols quoted in `USDC`
- Excludes leveraged tokens ending with: `UP`, `DOWN`, `BULL`, `BEAR`
- Daily filter:
  - `EMA10 > EMA50 > EMA200`
  - `Close > EMA10`
  - EMA10 slope over last 10 daily candles
- 4H filter:
  - `MACD line > Signal line`
  - Current volume > `1.5 x SMA20(volume)`
- Optional 1H filter (configurable in `config.py`):
  - `RSI(14) > 55`
  - RSI increasing vs previous candle
  - Volume increasing
- Transparent growth score (0-100%) based on:
  - Daily EMA10 slope
  - 4H MACD strength
  - 4H volume spike
  - 1H RSI contribution
- Console markdown table output sorted by Growth Score
- Telegram message with Top 5 sorted by EMA10 slope, then 4H volume ratio
- Smart alert mode (`ALERT_ONLY_NEW`) to avoid repeated alerts
- Start/Stop auto scan from GitHub Actions UI via `config_state.json`

## Project Structure

```
project/
|
|- scanner.py
|- indicators.py
|- telegram_sender.py
|- config.py
|- state_manager.py
|- requirements.txt
|- last_alerts.json
|- config_state.json
|
`- .github/
   `- workflows/
      |- scan.yml
      |- start_scan.yml
      `- stop_scan.yml
```

## Required GitHub Secrets

Set these repository secrets in GitHub:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

## Workflows

### 1) Run Scanner (`.github/workflows/scan.yml`)

- Automatic schedule: every 15 minutes
- Manual trigger from Actions tab
- For scheduled runs, checks `config_state.json`:
  - If `auto_scan_enabled` is `false`, scanner is skipped
- Commits updated `last_alerts.json` so smart alert state persists across runs

### 2) Start Auto Scan (`.github/workflows/start_scan.yml`)

- Manual trigger from Actions tab
- Sets `config_state.json` -> `auto_scan_enabled = true`
- Commits and pushes state change

### 3) Stop Auto Scan (`.github/workflows/stop_scan.yml`)

- Manual trigger from Actions tab
- Sets `config_state.json` -> `auto_scan_enabled = false`
- Commits and pushes state change

## Local Run (optional)

```bash
python -m pip install -r requirements.txt
set TELEGRAM_TOKEN=your_token
set TELEGRAM_CHAT_ID=your_chat_id
python scanner.py
```

## Notes

- Uses `ccxt` with `enableRateLimit=True`
- Includes retry handling for network/rate-limit/exchange availability errors
- Logs all major steps and symbol-level failures without stopping the entire scan
