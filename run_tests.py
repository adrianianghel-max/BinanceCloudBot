"""Simple unittest runner for Level 1 tests."""

import sys
import unittest

sys.path.insert(0, r"D:\BinanceCloudBot")

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(
        r"D:\BinanceCloudBot\tests", pattern="test_*.py"
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)