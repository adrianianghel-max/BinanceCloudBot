"""backtest_validate.py — Validare separată a modulului Backtester cu date istorice reale.

Folosire (înainte de producție):
    python backtest_validate.py --date 2026-08-29 --limit 10

Descarcă OHLCV real de pe Binance pentru ziua dată și rulează backtest +
optimizare parametri, salvând rezultatul în reports/backtest_YYYY-MM-DD.json.
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, r"D:\BinanceCloudBot")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest de validare cu date reale.")
    parser.add_argument("--date", required=True, help="Ziua de backtest: YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=10, help="Numar simboluri (default 10)")
    args = parser.parse_args()

    from trader import run_backtest_cli
    return run_backtest_cli(args.date, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())