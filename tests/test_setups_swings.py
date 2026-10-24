"""Tests for setups/swings.py (docs/SETUP_SPEC.md §5)."""
import unittest

import numpy as np
import pandas as pd

from analyzers.technical import calc_swing_levels
from setups.swings import find_confirmed_swing_highs


def _fractal_df(n=60):
    """A flat baseline with one deliberate, isolated spike -- guarantees
    exactly one confirmed 5-bar fractal swing high, at a known date."""
    dates = pd.bdate_range(end="2026-06-15", periods=n)
    high = np.full(n, 100.0)
    spike_pos = 30
    high[spike_pos] = 110.0
    low = high - 2.0
    close = high - 1.0
    open_ = high - 1.0
    volume = np.full(n, 1_000_000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=pd.DatetimeIndex(dates, name="Date"),
    ), dates[spike_pos].date()


class TestFindConfirmedSwingHighs(unittest.TestCase):

    def test_known_single_spike_is_confirmed_at_its_date(self):
        df, spike_date = _fractal_df()
        result = find_confirmed_swing_highs(df, lookback=50)
        self.assertEqual(len(result), 1)
        bar_date, price = result[0]
        self.assertEqual(bar_date, spike_date)
        self.assertEqual(price, 110.0)

    def test_last_two_bars_can_never_confirm(self):
        df, _ = _fractal_df(n=60)
        # Move the spike to the very last bar -- must never be confirmed.
        df2 = df.copy()
        df2.iloc[-1, df2.columns.get_loc("high")] = 999.0
        result = find_confirmed_swing_highs(df2, lookback=50)
        self.assertFalse(any(price == 999.0 for _, price in result))

        df3 = df.copy()
        df3.iloc[-2, df3.columns.get_loc("high")] = 999.0
        result3 = find_confirmed_swing_highs(df3, lookback=50)
        self.assertFalse(any(price == 999.0 for _, price in result3))

    def test_parity_with_calc_swing_levels_as_a_set_of_prices(self):
        """calc_swing_levels() dedupes by price (sorted(set(...))) and
        discards dates; find_confirmed_swing_highs() keeps every
        chronologically distinct point. Comparing as SETS of prices
        absorbs that multiplicity difference -- both apply the identical
        fractal rule over the identical window, so the two price sets
        must be exactly equal."""
        rng = np.random.default_rng(99)
        n = 120
        dates = pd.bdate_range(end="2026-06-15", periods=n)
        close = 100.0 + np.cumsum(rng.normal(0, 1.5, n))
        high = close + rng.uniform(0.5, 2.0, n)
        low = close - rng.uniform(0.5, 2.0, n)
        open_ = close
        volume = np.full(n, 1_000_000.0)
        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=pd.DatetimeIndex(dates, name="Date"),
        )

        mine = {price for _, price in find_confirmed_swing_highs(df, lookback=50)}
        theirs = set(calc_swing_levels(df, lookback=50)["swing_highs"])
        self.assertEqual(mine, theirs)

    def test_empty_when_no_fractal_structure(self):
        n = 60
        dates = pd.bdate_range(end="2026-06-15", periods=n)
        flat = pd.DataFrame(
            {
                "open": np.full(n, 100.0), "high": np.full(n, 100.0),
                "low": np.full(n, 98.0), "close": np.full(n, 99.0),
                "volume": np.full(n, 1_000_000.0),
            },
            index=pd.DatetimeIndex(dates, name="Date"),
        )
        self.assertEqual(find_confirmed_swing_highs(flat, lookback=50), [])


if __name__ == "__main__":
    unittest.main()
