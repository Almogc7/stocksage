"""Tests for setups/evaluate.py: the no-look-ahead property test run
through the single entry point (docs/SETUP_SPEC.md §5/§13a), plus a
drift-guard test for the copied _NON_STOCK_TYPES constant.
"""
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from tests.fixtures import make_trending_df

import services.watchlist_evaluator as watchlist_evaluator
import db.database as db_database
from setups.evaluate import evaluate_candidate
from setups.params import DEFAULT_SECURITY_TYPE, LIQUIDITY_LOOKBACK_SESSIONS, MIN_HISTORY_BARS, _NON_STOCK_TYPES
from setups.reason_codes import INSUFFICIENT_HISTORY, MISSING_OHLCV, TREND_FILTER_FAILED


class TestNoLookAheadProperty(unittest.TestCase):

    def _run_case(self, seed: int, corrupt_mode: str | None):
        df_full = make_trending_df(n=260, seed=seed)
        scan_idx = 240  # an interior scan date, with future bars after it
        scan_date = df_full.index[scan_idx].date()
        expected_session_date = scan_date

        df_corrupted = df_full.copy()
        if corrupt_mode == "extreme":
            df_corrupted.iloc[scan_idx + 1:] = df_corrupted.iloc[scan_idx + 1:] * 1000.0
        elif corrupt_mode == "nan":
            df_corrupted.iloc[scan_idx + 1:] = np.nan

        clean_truncated = df_full.iloc[: scan_idx + 1]
        clean_result = evaluate_candidate(clean_truncated, scan_date, expected_session_date)
        corrupted_result = evaluate_candidate(df_corrupted, scan_date, expected_session_date)

        self.assertEqual(clean_result.passed, corrupted_result.passed)
        self.assertEqual(clean_result.reason_code, corrupted_result.reason_code)

    def test_extreme_future_values_do_not_change_result(self):
        for seed in range(30):
            with self.subTest(seed=seed):
                self._run_case(seed, corrupt_mode="extreme")

    def test_nan_future_values_do_not_change_result(self):
        for seed in range(30):
            with self.subTest(seed=seed):
                self._run_case(seed, corrupt_mode="nan")


class TestNonStockTypesDriftGuard(unittest.TestCase):

    def test_copied_constant_matches_original(self):
        self.assertEqual(_NON_STOCK_TYPES, watchlist_evaluator._NON_STOCK_TYPES)


class TestDefaultSecurityTypeDriftGuard(unittest.TestCase):

    def test_matches_watchlist_security_type_column_default(self):
        """Lightweight guard against db/database.py's security_type column
        default changing without this module's copy following it."""
        source = Path(db_database.__file__).read_text(encoding="utf-8")
        self.assertIn(f"DEFAULT '{DEFAULT_SECURITY_TYPE}'", source)


class TestLiquidityWindowNanGuard(unittest.TestCase):

    def test_nan_in_liquidity_window_is_rejected_not_silently_averaged(self):
        """A liquidity window that's mostly NaN (everything but the last,
        already-validated bar) must not silently clear the liquidity gate
        on pandas' default skipna=True mean() over effectively one bar."""
        df = make_trending_df(n=260, seed=1)
        scan_date = df.index[-1].date()
        corrupted = df.copy()
        corrupted.iloc[-LIQUIDITY_LOOKBACK_SESSIONS:-1] = np.nan

        result = evaluate_candidate(corrupted, scan_date, scan_date)

        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, MISSING_OHLCV)


class TestPaddedRowsDoNotInflateHistory(unittest.TestCase):
    """2026-10-25 padded-row fix: a multi-ticker yfinance download pads a
    late-listed symbol with all-NaN rows for every date before it
    existed, covering the whole requested range -- these must never
    count as real history (setups/data_validation.py), and never let a
    too-short real history fall through as a different, wrong rejection
    (setups/trend_filter.py)."""

    def test_padded_frame_short_of_min_history_bars_is_insufficient_history_never_trend_filter_failed(self):
        n_real = 180  # short of MIN_HISTORY_BARS (220) in REAL bars
        n_pad = 100
        real = make_trending_df(n=n_real, seed=7)
        pad_dates = pd.bdate_range(end=real.index[0] - pd.Timedelta(days=1), periods=n_pad)
        pad = pd.DataFrame({col: np.nan for col in real.columns}, index=pad_dates)
        padded = pd.concat([pad, real])
        self.assertEqual(len(padded), n_real + n_pad)
        self.assertGreaterEqual(len(padded), MIN_HISTORY_BARS)  # raw row count alone would pass

        scan_date = padded.index[-1].date()
        result = evaluate_candidate(padded, scan_date, scan_date)

        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, INSUFFICIENT_HISTORY)
        self.assertNotEqual(result.reason_code, TREND_FILTER_FAILED)

    def test_padded_then_cleaned_matches_the_same_frame_never_padded(self):
        """Once padding rows are dropped (the harness I/O boundary's
        job), the result is identical to a frame that was never padded
        in the first place -- the leading padding, while still present,
        sits entirely before every lookback window evaluate_candidate()
        ever reads relative to scan_date, so results already match even
        without dropping; this pins that invariant explicitly."""
        from setups.frame_utils import drop_all_nan_ohlc_rows

        real = make_trending_df(n=260, seed=8)
        pad_dates = pd.bdate_range(end=real.index[0] - pd.Timedelta(days=1), periods=50)
        pad = pd.DataFrame({col: np.nan for col in real.columns}, index=pad_dates)
        padded = pd.concat([pad, real])
        scan_date = real.index[-1].date()

        cleaned = drop_all_nan_ohlc_rows(padded)
        pd.testing.assert_frame_equal(cleaned, real, check_names=False)

        clean_result = evaluate_candidate(real, scan_date, scan_date)
        padded_result = evaluate_candidate(padded, scan_date, scan_date)
        cleaned_result = evaluate_candidate(cleaned, scan_date, scan_date)

        self.assertEqual(clean_result.passed, padded_result.passed)
        self.assertEqual(clean_result.reason_code, padded_result.reason_code)
        self.assertEqual(clean_result.passed, cleaned_result.passed)
        self.assertEqual(clean_result.reason_code, cleaned_result.reason_code)


class TestFrameNormalization(unittest.TestCase):
    """evaluate_candidate() normalizes once, right after truncation and
    before validation: flattens MultiIndex columns, lowercases names, and
    confirms the five required OHLCV columns exist."""

    def test_multiindex_columns_do_not_crash_and_still_pass(self):
        df = make_trending_df(n=260, seed=2)
        scan_date = df.index[-1].date()
        multi = df.copy()
        multi.columns = pd.MultiIndex.from_product([multi.columns, ["NVDA"]])

        result = evaluate_candidate(multi, scan_date, scan_date)

        self.assertTrue(result.passed)

    def test_uppercase_column_names_do_not_crash_and_still_pass(self):
        df = make_trending_df(n=260, seed=3)
        scan_date = df.index[-1].date()
        upper = df.copy()
        upper.columns = [c.upper() for c in upper.columns]

        result = evaluate_candidate(upper, scan_date, scan_date)

        self.assertTrue(result.passed)

    def test_frame_missing_volume_column_reports_missing_ohlcv_not_a_crash(self):
        df = make_trending_df(n=260, seed=4).drop(columns=["volume"])
        scan_date = df.index[-1].date()

        result = evaluate_candidate(df, scan_date, scan_date)

        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, MISSING_OHLCV)


if __name__ == "__main__":
    unittest.main()
