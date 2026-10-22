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
from setups.params import DEFAULT_SECURITY_TYPE, LIQUIDITY_LOOKBACK_SESSIONS, _NON_STOCK_TYPES
from setups.reason_codes import MISSING_OHLCV


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
