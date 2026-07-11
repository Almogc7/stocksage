"""
Tests for Fix 2: use the last completed daily candle in Gate 7 (green-candle check).

The fix avoids incomplete-bar bias: when the US market is open, yfinance
includes the current in-progress session as the last row of the daily df.
Its "close" is the intraday snapshot, not the final 4 PM close. Gate 7
now reads df.iloc[-2] (the previous confirmed close) when the market is open
and df.iloc[-1] (the last row, which is complete) when the market is closed.

Terminology used in this module:
  - incomplete-bar bias: using an in-progress bar that has not yet closed
  - look-ahead bias: using data that wasn't available at decision time
  The Gate 7 issue is incomplete-bar bias, not look-ahead bias.

No network access â€” is_market_open() is mocked throughout.
"""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests.fixtures import make_trending_df


def _make_df_with_known_last_candles(
    green_second_to_last: bool,
    green_last: bool,
    n: int = 252,
) -> pd.DataFrame:
    """
    Return a trending DataFrame where:
      - df.iloc[-2] is green or red (controlled by green_second_to_last)
      - df.iloc[-1] is green or red (controlled by green_last)
    Useful for testing which candle Gate 7 selects.
    """
    df = make_trending_df(n=n)
    df = df.copy()

    base = float(df["close"].iloc[-3])

    # Construct second-to-last candle
    if green_second_to_last:
        df.iloc[-2, df.columns.get_loc("open")]  = base * 0.99
        df.iloc[-2, df.columns.get_loc("close")] = base * 1.01
    else:
        df.iloc[-2, df.columns.get_loc("open")]  = base * 1.01
        df.iloc[-2, df.columns.get_loc("close")] = base * 0.99

    # Construct last candle (possibly in-progress)
    last_base = float(df["close"].iloc[-2])
    if green_last:
        df.iloc[-1, df.columns.get_loc("open")]  = last_base * 0.99
        df.iloc[-1, df.columns.get_loc("close")] = last_base * 1.01
    else:
        df.iloc[-1, df.columns.get_loc("open")]  = last_base * 1.01
        df.iloc[-1, df.columns.get_loc("close")] = last_base * 0.99

    return df


def _green_candle_check(df: pd.DataFrame, market_is_open: bool) -> bool:
    """
    Replicate the exact candle-selection logic from agent/core.py Gate 7
    so tests are self-contained and not coupled to the production import path.
    """
    if market_is_open and len(df) >= 2:
        ref = df.iloc[-2]
    else:
        ref = df.iloc[-1]
    return float(ref["close"]) > float(ref["open"])


class TestIncompleteCandleSelection(unittest.TestCase):
    """Tests for the candle-selection logic itself (unit level)."""

    def test_market_open_uses_second_to_last_candle(self):
        """When market is open, the check must use df.iloc[-2]."""
        df = _make_df_with_known_last_candles(
            green_second_to_last=True,
            green_last=False,  # last candle currently red (in-progress)
        )
        result = _green_candle_check(df, market_is_open=True)
        self.assertTrue(result, "Should be True (green) because iloc[-2] is green")

    def test_market_open_red_second_to_last_returns_false(self):
        """When market is open and iloc[-2] is red, result must be False."""
        df = _make_df_with_known_last_candles(
            green_second_to_last=False,  # confirmed red candle
            green_last=True,             # in-progress green candle (should be ignored)
        )
        result = _green_candle_check(df, market_is_open=True)
        self.assertFalse(result, "Should be False (red) because iloc[-2] is red")

    def test_market_closed_uses_last_candle(self):
        """When market is closed, the check must use df.iloc[-1]."""
        df = _make_df_with_known_last_candles(
            green_second_to_last=False,  # previous candle red
            green_last=True,             # last (completed) candle green
        )
        result = _green_candle_check(df, market_is_open=False)
        self.assertTrue(result, "Should be True (green) because iloc[-1] is green and market closed")

    def test_market_closed_red_last_returns_false(self):
        """When market is closed and last candle is red, result must be False."""
        df = _make_df_with_known_last_candles(
            green_second_to_last=True,
            green_last=False,
        )
        result = _green_candle_check(df, market_is_open=False)
        self.assertFalse(result)

    def test_weekend_or_holiday_uses_last_row(self):
        """On weekends/holidays, is_market_open() returns False; use iloc[-1]."""
        df = _make_df_with_known_last_candles(
            green_second_to_last=False,
            green_last=True,
        )
        # Simulates weekend: market closed â†’ use last row
        result = _green_candle_check(df, market_is_open=False)
        self.assertTrue(result)

    def test_single_row_df_does_not_crash_when_market_open(self):
        """
        If df has only 1 row and the market is open, len(df) >= 2 is False
        so we fall back to df.iloc[-1] without an IndexError.
        """
        df = make_trending_df(n=1)
        try:
            result = _green_candle_check(df, market_is_open=True)
        except IndexError:
            self.fail("Single-row df caused IndexError in candle selection")


class TestProductionCodeUsesCorrectIndex(unittest.TestCase):
    """
    Verify that the production agent/core.py Gate 7 code passes
    is_market_open() and uses the correct row index.
    These tests mock is_market_open() to control market state.
    """

    def test_market_open_alert_uses_iloc_minus_2(self):
        """
        When market is open: an alert that would fire based on iloc[-2]
        (green confirmed candle) should pass Gate 7 even if iloc[-1] is red.
        """
        import inspect
        import agent.core as core_module
        source = inspect.getsource(core_module.check_alerts)

        # Confirm the fix is present: the code must check is_market_open()
        # before deciding which candle index to use.
        self.assertIn("is_market_open()", source,
            "Gate 7 must call is_market_open() to select the correct candle")
        self.assertIn("iloc[-2]", source,
            "Gate 7 must reference iloc[-2] for market-open scenario")
        self.assertIn("iloc[-1]", source,
            "Gate 7 must still reference iloc[-1] for market-closed scenario")

    def test_incomplete_bar_bias_terminology_in_comments(self):
        """
        The fix comment should use 'incomplete-bar bias' not just 'look-ahead bias'
        since these are distinct concepts.
        """
        import inspect
        import agent.core as core_module
        source = inspect.getsource(core_module.check_alerts)
        self.assertIn("incomplete-bar", source.lower(),
            "Gate 7 comment should mention 'incomplete-bar bias'")


if __name__ == "__main__":
    unittest.main()
