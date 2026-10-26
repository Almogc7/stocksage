"""Tests for setups/frame_utils.py's drop_all_nan_ohlc_rows() (docs/
SETUP_SPEC.md §3/§13a) -- yfinance's multi-ticker downloads pad a
late-listed symbol with all-NaN rows for every calendar date before it
existed, covering the whole requested range; these must never count as
real history anywhere in the harness.
"""
import unittest

import numpy as np
import pandas as pd

from setups.frame_utils import drop_all_nan_ohlc_rows


def _real_frame(n=180, start="2026-01-02"):
    dates = pd.bdate_range(start=start, periods=n)
    close = np.linspace(100.0, 150.0, n)
    return pd.DataFrame(
        {
            "open": close * 0.995, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": np.full(n, 1_000_000.0),
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )


def _padded(real: pd.DataFrame, n_pad: int) -> pd.DataFrame:
    pad_dates = pd.bdate_range(end=real.index[0] - pd.Timedelta(days=1), periods=n_pad)
    pad = pd.DataFrame(
        {col: np.nan for col in real.columns}, index=pd.DatetimeIndex(pad_dates, name="Date"),
    )
    return pd.concat([pad, real])


class TestDropAllNanOhlcRows(unittest.TestCase):

    def test_drops_rows_where_all_four_ohlc_columns_are_nan(self):
        real = _real_frame(n=10)
        padded = _padded(real, n_pad=5)
        self.assertEqual(len(padded), 15)

        cleaned = drop_all_nan_ohlc_rows(padded)

        self.assertEqual(len(cleaned), 10)
        self.assertTrue(cleaned["close"].notna().all())

    def test_leaves_a_row_with_only_some_nan_columns_untouched(self):
        """A real data-quality problem (one column NaN, not all four) is
        NOT a padding artifact -- data_validation.py's own MISSING_OHLCV
        checks must still see it."""
        real = _real_frame(n=10)
        real = real.copy()
        real.iloc[-1, real.columns.get_loc("close")] = np.nan  # only close, not open/high/low too

        cleaned = drop_all_nan_ohlc_rows(real)

        self.assertEqual(len(cleaned), 10)  # row kept
        self.assertTrue(pd.isna(cleaned["close"].iloc[-1]))

    def test_returns_a_copy_and_never_mutates_the_input(self):
        real = _real_frame(n=10)
        padded = _padded(real, n_pad=3)
        before = padded.copy(deep=True)

        drop_all_nan_ohlc_rows(padded)

        pd.testing.assert_frame_equal(padded, before)

    def test_missing_ohlc_columns_entirely_returns_a_copy_unchanged(self):
        df = pd.DataFrame({"volume": [1.0, 2.0, np.nan]})
        result = drop_all_nan_ohlc_rows(df)
        pd.testing.assert_frame_equal(result, df)
        self.assertIsNot(result, df)

    def test_padded_frame_cleaned_matches_the_never_padded_frame_exactly(self):
        real = _real_frame(n=180)
        padded = _padded(real, n_pad=100)

        cleaned = drop_all_nan_ohlc_rows(padded)

        pd.testing.assert_frame_equal(cleaned, real)


if __name__ == "__main__":
    unittest.main()
