import unittest
from datetime import datetime, timezone

import pandas as pd

from scanner import _closed_candles


class TestClosedCandles(unittest.TestCase):
    def test_excludes_active_interval(self):
        now = datetime(2026, 10, 3, 20, 9, tzinfo=timezone.utc)
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    ["2026-10-03T20:00:00Z", "2026-10-03T20:05:00Z"],
                    utc=True,
                ),
                "close": [100.0, 101.0],
            }
        )

        closed = _closed_candles(df, "5m", now)

        self.assertEqual(closed["close"].tolist(), [100.0])
