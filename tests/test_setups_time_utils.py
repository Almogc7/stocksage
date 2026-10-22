"""Tests for setups/time_utils.py (docs/SETUP_SPEC.md §5/§13a's no-look-
ahead truncation rule)."""
import unittest

import numpy as np
import pandas as pd

from setups.time_utils import truncate_to_date


def _df(n=30):
    dates = pd.bdate_range(end="2026-06-15", periods=n)
    return pd.DataFrame(
        {"close": np.arange(n, dtype=float)}, index=pd.DatetimeIndex(dates, name="Date")
    )


class TestTruncateToDate(unittest.TestCase):

    def test_truncates_to_scan_date_inclusive(self):
        df = _df()
        cutoff = df.index[10].date()
        out = truncate_to_date(df, cutoff)
        self.assertEqual(len(out), 11)
        self.assertEqual(out.index[-1].date(), cutoff)

    def test_tz_naive_and_tz_aware_give_same_result_for_same_wall_dates(self):
        df = _df()
        tz_df = df.copy()
        tz_df.index = df.index.tz_localize("America/New_York")
        cutoff = df.index[10].date()

        naive_out = truncate_to_date(df, cutoff)
        tz_out = truncate_to_date(tz_df, cutoff)

        self.assertEqual(len(naive_out), len(tz_out))
        self.assertEqual(list(naive_out["close"]), list(tz_out["close"]))

    def test_does_not_mutate_input(self):
        df = _df()
        before = df.copy()
        truncate_to_date(df, df.index[5].date())
        pd.testing.assert_frame_equal(df, before)

    def test_idempotent(self):
        df = _df()
        cutoff = df.index[15].date()
        once = truncate_to_date(df, cutoff)
        twice = truncate_to_date(once, cutoff)
        pd.testing.assert_frame_equal(once, twice)


if __name__ == "__main__":
    unittest.main()
