"""Robustness tests: evaluate_candidate() and define_setup() must NEVER
raise on malformed input -- always a Result/Rejection/SetupDefinition.
"""
import unittest
from datetime import date, datetime

import numpy as np
import pandas as pd

from setups.definition import Rejection, SetupDefinition, define_setup
from setups.evaluate import evaluate_candidate
from setups.reason_codes import (
    DATA_NOT_FINAL,
    INSUFFICIENT_HISTORY,
    MALFORMED_INDEX,
    MISSING_OHLCV,
)


def _good_df(n=250, start="2026-06-15"):
    dates = pd.bdate_range(end=start, periods=n)
    close = np.linspace(90.0, 150.0, n)
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close,
         "volume": np.full(n, 1_000_000.0)},
        index=pd.DatetimeIndex(dates, name="Date"),
    )


BASE = _good_df(250)
BASE_SPY = _good_df(250)
SCAN = BASE.index[-1].date()


def _malformed_df_cases():
    cases = {}
    cases["none"] = None
    cases["empty"] = BASE.iloc[0:0]
    cases["one_row"] = BASE.iloc[[-1]]

    nan_df = BASE.copy()
    nan_df.loc[:, ["open", "high", "low", "close", "volume"]] = np.nan
    cases["all_nan"] = nan_df

    inf_df = BASE.copy()
    inf_df.iloc[-1, inf_df.columns.get_loc("high")] = np.inf
    cases["inf_values"] = inf_df

    zero_df = BASE.copy()
    zero_df[["open", "high", "low", "close"]] = 0.0
    cases["zero_prices"] = zero_df

    neg_df = BASE.copy()
    for c in ("open", "high", "low", "close"):
        neg_df[c] = -neg_df[c]
    cases["negative_prices"] = neg_df

    cases["duplicate_index"] = pd.concat([BASE, BASE.iloc[[-1]]])
    cases["descending_index"] = BASE.iloc[::-1]

    range_df = BASE.copy()
    range_df.index = pd.RangeIndex(len(BASE))
    cases["rangeindex"] = range_df

    str_df = BASE.copy()
    str_df.index = [str(i) for i in range(len(BASE))]
    cases["string_index"] = str_df

    tz_df = BASE.copy()
    tz_df.index = BASE.index.tz_localize("America/New_York")
    cases["tz_aware"] = tz_df
    cases["tz_naive"] = BASE.copy()

    multi_df = BASE.copy()
    multi_df.columns = pd.MultiIndex.from_product([multi_df.columns, ["TSLA"]])
    cases["multiindex_cols"] = multi_df

    upper_df = BASE.copy()
    upper_df.columns = [c.upper() for c in upper_df.columns]
    cases["uppercase_cols"] = upper_df

    numstr_df = BASE.copy()
    for c in ("open", "high", "low", "close", "volume"):
        numstr_df[c] = numstr_df[c].astype(str)
    cases["object_numeric_strings"] = numstr_df

    garbage_df = BASE.copy()
    garbage_df["close"] = "garbage"
    cases["object_garbage_strings"] = garbage_df

    for col in ("open", "high", "low", "close", "volume"):
        cases[f"missing_{col}"] = BASE.drop(columns=[col])

    cases["1000_rows"] = _good_df(1000)
    cases["minimum_history"] = _good_df(220)
    cases["below_minimum_history"] = _good_df(5)

    return cases


class TestEvaluateCandidateNeverRaises(unittest.TestCase):

    def test_malformed_df_cases(self):
        for name, df in _malformed_df_cases().items():
            with self.subTest(case=name):
                try:
                    result = evaluate_candidate(df, SCAN, SCAN)
                except Exception as e:  # noqa: BLE001 -- this IS the assertion
                    self.fail(f"{name} raised {type(e).__name__}: {e}")
                self.assertTrue(hasattr(result, "passed"))

    def test_malformed_df_exact_reason_codes_where_spec_defines_one(self):
        cases = _malformed_df_cases()
        expected = {
            "one_row": INSUFFICIENT_HISTORY,
            "all_nan": MISSING_OHLCV,
            "duplicate_index": MALFORMED_INDEX,
            "descending_index": MALFORMED_INDEX,
            "string_index": MALFORMED_INDEX,
            "missing_open": MISSING_OHLCV,
            "missing_high": MISSING_OHLCV,
            "missing_low": MISSING_OHLCV,
            "missing_close": MISSING_OHLCV,
            "missing_volume": MISSING_OHLCV,
            "below_minimum_history": INSUFFICIENT_HISTORY,
            "object_garbage_strings": MISSING_OHLCV,
        }
        for name, code in expected.items():
            with self.subTest(case=name):
                result = evaluate_candidate(cases[name], SCAN, SCAN)
                self.assertFalse(result.passed)
                self.assertEqual(result.reason_code, code)

    def test_scan_date_before_all_data(self):
        result = evaluate_candidate(BASE, date(2020, 1, 1), date(2020, 1, 1))
        self.assertFalse(result.passed)

    def test_scan_date_far_after_all_data(self):
        # truncate_to_date(scan_date=2030) keeps every real row (all <=
        # 2030), so the frame is unaffected; passing a mismatched
        # expected_session_date is what actually exercises staleness here.
        result = evaluate_candidate(BASE, date(2030, 1, 1), date(2030, 1, 1))
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, DATA_NOT_FINAL)

    def test_expected_session_date_as_datetime_matches_a_date(self):
        dt_scan = datetime.combine(SCAN, datetime.min.time())
        result = evaluate_candidate(BASE, SCAN, dt_scan)
        self.assertTrue(result.passed)


class TestDefineSetupNeverRaises(unittest.TestCase):

    def test_malformed_df_cases(self):
        for name, df in _malformed_df_cases().items():
            with self.subTest(case=name):
                try:
                    result = define_setup(df, SCAN, BASE_SPY)
                except Exception as e:  # noqa: BLE001
                    self.fail(f"df={name} raised {type(e).__name__}: {e}")
                self.assertIsInstance(result, (SetupDefinition, Rejection))

    def test_malformed_spy_df_cases_never_raise_and_regime_is_unknown(self):
        for name, spy in _malformed_df_cases().items():
            with self.subTest(case=name):
                try:
                    result = define_setup(BASE, SCAN, spy)
                except Exception as e:  # noqa: BLE001
                    self.fail(f"spy_df={name} raised {type(e).__name__}: {e}")
                self.assertIsInstance(result, (SetupDefinition, Rejection))
                if isinstance(result, Rejection) and result.reason_code in (
                    None, "REGIME_VETO",
                ):
                    pass  # just proving no crash; regime value asserted below
                if name in ("none", "empty"):
                    self.assertIsInstance(result, Rejection)
                    self.assertEqual(result.regime, "UNKNOWN")

    def test_spy_df_none_regime_is_unknown(self):
        result = define_setup(BASE, SCAN, None)
        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.regime, "UNKNOWN")

    def test_spy_df_empty_regime_is_unknown(self):
        result = define_setup(BASE, SCAN, BASE_SPY.iloc[0:0])
        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.regime, "UNKNOWN")

    def test_df_none_rejects_missing_ohlcv(self):
        result = define_setup(None, SCAN, BASE_SPY)
        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, MISSING_OHLCV)
        self.assertEqual(result.regime, "UNKNOWN")

    def test_df_empty_rejects_data_not_final(self):
        result = define_setup(BASE.iloc[0:0], SCAN, BASE_SPY)
        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, DATA_NOT_FINAL)

    def test_each_missing_column_rejects_missing_ohlcv(self):
        for col in ("open", "high", "low", "close", "volume"):
            with self.subTest(column=col):
                result = define_setup(BASE.drop(columns=[col]), SCAN, BASE_SPY)
                self.assertIsInstance(result, Rejection)
                self.assertEqual(result.reason_code, MISSING_OHLCV)

    def test_scan_date_before_all_data(self):
        result = define_setup(BASE, date(2020, 1, 1), BASE_SPY)
        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, DATA_NOT_FINAL)

    def test_scan_date_far_after_all_data(self):
        result = define_setup(BASE, date(2030, 1, 1), BASE_SPY)
        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, DATA_NOT_FINAL)

    def test_scan_date_as_datetime_matches_a_date(self):
        dt_scan = datetime.combine(SCAN, datetime.min.time())
        result = define_setup(BASE, dt_scan, BASE_SPY)
        self.assertIsInstance(result, (SetupDefinition, Rejection))
        # must not be misreported as stale just because of the type
        if isinstance(result, Rejection):
            self.assertNotEqual(result.reason_code, DATA_NOT_FINAL)

    def test_1000_rows_and_exactly_minimum_history(self):
        for n in (1000, 220):
            with self.subTest(n=n):
                df = _good_df(n)
                result = define_setup(df, df.index[-1].date(), _good_df(n))
                self.assertIsInstance(result, (SetupDefinition, Rejection))


if __name__ == "__main__":
    unittest.main()
