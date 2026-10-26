"""Tests for setups/trend_filter.py (docs/SETUP_SPEC.md §4)."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests.fixtures import make_falling_df, make_rising_df

from config import ELIGIBILITY_MIN_AVG_VOLUME, ELIGIBILITY_MIN_DOLLAR_VOL, ELIGIBILITY_MIN_PRICE
from setups.reason_codes import LIQUIDITY_VETO, TREND_FILTER_FAILED
from setups.trend_filter import evaluate_trend_and_eligibility

_GOOD_VOLUME = ELIGIBILITY_MIN_AVG_VOLUME * 10
_GOOD_DOLLAR_VOL = ELIGIBILITY_MIN_DOLLAR_VOL * 10


def _low_absolute_price_uptrend_df(n=260, last_close=2.5):
    """A smooth, strictly-increasing synthetic series scaled down to an
    absolute price level below ELIGIBILITY_MIN_PRICE. Trend/SMA comparisons
    are scale-invariant (ratios/orderings survive a uniform positive
    multiplier), so this still clears every trend check while giving a
    below-floor current (point) price -- isolating the price-floor gate
    from the trend gates."""
    close = np.linspace(last_close * 0.4, last_close, n)
    open_ = close * 0.995
    high = close * 1.01
    low = close * 0.985
    dates = pd.bdate_range(end="2026-06-15", periods=n)
    return pd.DataFrame(
        {"close": close, "open": open_, "high": high, "low": low},
        index=pd.DatetimeIndex(dates, name="Date"),
    )


class TestEvaluateTrendAndEligibility(unittest.TestCase):

    def test_passes_strong_uptrend_with_good_liquidity(self):
        df = make_rising_df(n=260)
        result = evaluate_trend_and_eligibility(
            df, avg_volume=_GOOD_VOLUME, avg_dollar_vol=_GOOD_DOLLAR_VOL
        )
        self.assertTrue(result.passed)
        self.assertIsNone(result.reason_code)

    def test_fails_trend_filter_on_downtrend(self):
        df = make_falling_df(n=260)
        result = evaluate_trend_and_eligibility(
            df, avg_volume=_GOOD_VOLUME, avg_dollar_vol=_GOOD_DOLLAR_VOL
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, TREND_FILTER_FAILED)

    def test_fails_liquidity_veto_on_low_volume(self):
        df = make_rising_df(n=260)
        result = evaluate_trend_and_eligibility(
            df, avg_volume=ELIGIBILITY_MIN_AVG_VOLUME - 1, avg_dollar_vol=_GOOD_DOLLAR_VOL
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, LIQUIDITY_VETO)

    def test_fails_liquidity_veto_on_low_point_price(self):
        """The price floor is checked against the current (last-bar,
        point) price -- matching analyzers/eligibility.py's own precedent
        -- not a trailing average."""
        df = _low_absolute_price_uptrend_df(last_close=ELIGIBILITY_MIN_PRICE - 0.5)
        result = evaluate_trend_and_eligibility(
            df, avg_volume=_GOOD_VOLUME, avg_dollar_vol=_GOOD_DOLLAR_VOL
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, LIQUIDITY_VETO)

    def test_fails_liquidity_veto_on_low_dollar_volume(self):
        df = make_rising_df(n=260)
        result = evaluate_trend_and_eligibility(
            df, avg_volume=_GOOD_VOLUME, avg_dollar_vol=ELIGIBILITY_MIN_DOLLAR_VOL - 1
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, LIQUIDITY_VETO)

    def test_flattens_multiindex_columns_like_check_sma150_does(self):
        """A MultiIndex-column frame (the shape yf.download() produces for
        multi-ticker fetches) must not crash the slope check, which reads
        df["close"] directly rather than going through check_sma150()."""
        df = make_rising_df(n=260)
        multi = df.copy()
        # _flatten() (analyzers/technical.py) keeps level 0 -- the field
        # name -- and drops level 1 (ticker), matching yf.download()'s own
        # MultiIndex column shape.
        multi.columns = pd.MultiIndex.from_product([multi.columns, ["TSLA"]])
        result = evaluate_trend_and_eligibility(
            multi, avg_volume=_GOOD_VOLUME, avg_dollar_vol=_GOOD_DOLLAR_VOL
        )
        self.assertTrue(result.passed)

    def test_sma150_equal_to_sma200_at_6_decimals_is_not_a_pass(self):
        """analyzers.technical._sma200() returns an UNROUNDED float
        (confirmed by reading its source -- no round() call), and
        trend_filter.py compares it against its own unrounded SMA150
        series rather than check_sma150()'s display-rounded field. This
        pins that: SMA150 and SMA200 equal out to 6 decimals (effectively
        equal) must fail the strict `sma150 > sma200` gate, not pass it
        due to any rounding drift on either side."""
        df = make_rising_df(n=260)
        sma150_today = float(df["close"].rolling(window=150).mean().iloc[-1])
        boundary_sma200 = round(sma150_today, 6)
        with patch("setups.trend_filter._sma200", return_value=boundary_sma200):
            result = evaluate_trend_and_eligibility(
                df, avg_volume=_GOOD_VOLUME, avg_dollar_vol=_GOOD_DOLLAR_VOL
            )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, TREND_FILTER_FAILED)

    def test_sma200_nan_is_rejected_not_a_silent_pass(self):
        """analyzers.technical._sma200() can return NaN (not None) when
        len(df)>=200 but the trailing 200-bar window itself still spans a
        NaN gap (e.g. yfinance's multi-ticker padding rows). A bare
        `sma150_today <= sma200` with sma200=NaN is always False in
        Python, which would otherwise fall through as a silent pass --
        must reject instead."""
        df = make_rising_df(n=260)
        with patch("setups.trend_filter._sma200", return_value=float("nan")):
            result = evaluate_trend_and_eligibility(
                df, avg_volume=_GOOD_VOLUME, avg_dollar_vol=_GOOD_DOLLAR_VOL
            )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, TREND_FILTER_FAILED)

    def test_padded_frame_with_185_real_bars_is_rejected_not_a_crash_or_pass(self):
        """100 all-NaN padding rows (a late-listed symbol's yfinance
        padding) followed by 185 real bars -- short of the 150-bar
        trailing window check (2026-10-25 padded-row fix) in no obvious
        way crashes or silently passes; this isolates trend_filter.py's
        own NaN-safety from setups/data_validation.py's separate
        INSUFFICIENT_HISTORY gate (185 real bars alone would already be
        caught there, upstream of this function in the real pipeline)."""
        real = make_rising_df(n=185)
        pad_dates = pd.bdate_range(end=real.index[0] - pd.Timedelta(days=1), periods=100)
        pad = pd.DataFrame({col: float("nan") for col in real.columns}, index=pad_dates)
        padded = pd.concat([pad, real])

        result = evaluate_trend_and_eligibility(
            padded, avg_volume=_GOOD_VOLUME, avg_dollar_vol=_GOOD_DOLLAR_VOL
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, TREND_FILTER_FAILED)

    def test_evaluate_trend_and_eligibility_tolerates_missing_volume_column(self):
        """Not this module's own job to report MISSING_OHLCV (that's
        evaluate.py's normalize_frame()/missing_required_columns() gate,
        run before this function is ever reached) -- but this function
        itself never touches "volume" at all, so it must not crash on a
        frame that lacks it."""
        df = make_rising_df(n=260).drop(columns=["volume"])
        result = evaluate_trend_and_eligibility(
            df, avg_volume=_GOOD_VOLUME, avg_dollar_vol=_GOOD_DOLLAR_VOL
        )
        self.assertTrue(result.passed)


if __name__ == "__main__":
    unittest.main()
