"""Tests for setups/data_validation.py (docs/SETUP_SPEC.md §3)."""
import unittest

import numpy as np
import pandas as pd

from setups.data_validation import validate_scan_data
from setups.params import MIN_HISTORY_BARS
from setups.reason_codes import (
    DATA_NOT_FINAL,
    INSUFFICIENT_HISTORY,
    MALFORMED_INDEX,
    MISSING_OHLCV,
    ZERO_VOLUME_LAST_BAR,
)


def _good_df(n=MIN_HISTORY_BARS + 5):
    dates = pd.bdate_range(end="2026-06-15", periods=n)
    return pd.DataFrame(
        {
            "close": np.linspace(100, 150, n),
            "open": np.linspace(99, 149, n),
            "high": np.linspace(101, 151, n),
            "low": np.linspace(98, 148, n),
            "volume": np.full(n, 1_000_000.0),
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )


class TestValidateScanData(unittest.TestCase):

    def test_all_clear_passes(self):
        df = _good_df()
        expected = df.index[-1].date()
        result = validate_scan_data(df, expected)
        self.assertTrue(result.passed)
        self.assertIsNone(result.reason_code)

    def test_malformed_index_unsorted(self):
        df = _good_df()
        shuffled = df.iloc[list(reversed(range(len(df))))]
        result = validate_scan_data(shuffled, shuffled.index[0].date())
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, MALFORMED_INDEX)

    def test_malformed_index_duplicate_dates(self):
        df = _good_df()
        dup = pd.concat([df, df.iloc[[-1]]])
        result = validate_scan_data(dup, dup.index[-1].date())
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, MALFORMED_INDEX)

    def test_insufficient_history(self):
        df = _good_df(n=MIN_HISTORY_BARS - 1)
        result = validate_scan_data(df, df.index[-1].date())
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, INSUFFICIENT_HISTORY)

    def test_insufficient_history_takes_priority_over_stale_date(self):
        """A too-short AND stale frame reports INSUFFICIENT_HISTORY, not
        DATA_NOT_FINAL -- order matters."""
        df = _good_df(n=MIN_HISTORY_BARS - 1)
        stale_expected = df.index[-1].date() + pd.Timedelta(days=5)
        result = validate_scan_data(df, stale_expected)
        self.assertEqual(result.reason_code, INSUFFICIENT_HISTORY)

    def test_data_not_final_stale_last_bar(self):
        df = _good_df()
        stale_expected = df.index[-1].date() + pd.Timedelta(days=3)
        result = validate_scan_data(df, stale_expected)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, DATA_NOT_FINAL)

    def test_tz_aware_last_bar_matches_tz_naive_expected_date(self):
        df = _good_df()
        tz_df = df.copy()
        tz_df.index = df.index.tz_localize("America/New_York")
        expected = df.index[-1].date()  # plain, tz-naive date
        result = validate_scan_data(tz_df, expected)
        self.assertTrue(result.passed)

    def test_all_close_nan_is_insufficient_history_not_missing_ohlcv(self):
        """Deliberate behavior change (padded-row fix): history is now
        counted as bars with a FINITE close, not raw row count -- a
        whole-column-NaN close has zero real bars by that definition, so
        it's INSUFFICIENT_HISTORY (the more fundamental problem), caught
        at check (2), never reaching check (4)'s MISSING_OHLCV at all."""
        df = _good_df()
        df.loc[df.index, "close"] = np.nan
        result = validate_scan_data(df, df.index[-1].date())
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, INSUFFICIENT_HISTORY)

    def test_missing_ohlcv_nan_in_last_bar_only(self):
        """A NaN in only the LAST bar's close (every earlier close valid)
        must still be caught -- dropna().empty alone would miss this."""
        df = _good_df()
        df.iloc[-1, df.columns.get_loc("close")] = np.nan
        result = validate_scan_data(df, df.index[-1].date())
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, MISSING_OHLCV)

    def test_zero_volume_last_bar(self):
        df = _good_df()
        df.iloc[-1, df.columns.get_loc("volume")] = 0
        result = validate_scan_data(df, df.index[-1].date())
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, ZERO_VOLUME_LAST_BAR)


if __name__ == "__main__":
    unittest.main()
