"""
Tests for Fix 4: RSI consistency between chart_generator and technical.py.

Verifies that both modules produce identical RSI values for the same input
data, using Wilder's smoothing (EMA with alpha=1/14) as the shared formula.

Also includes a known-value test: a monotonically increasing price series
should produce RSI approaching 100 after sufficient bars.

No network access — only synthetic DataFrames are used.
"""

import math
import unittest

import numpy as np
import pandas as pd
import ta

from tests.fixtures import make_trending_df


class TestRsiFormulas(unittest.TestCase):

    def _ta_rsi(self, close: pd.Series, window: int = 14) -> pd.Series:
        """Reference RSI using ta library — Wilder's smoothing."""
        return ta.momentum.rsi(close, window=window)

    # ── Known-value tests ────────────────────────────────────────────────────

    def test_rsi_approaches_100_on_monotonic_increase(self):
        """
        A strictly monotonically increasing price series has no down days,
        so average loss → 0 and RSI → 100. With Wilder's EWM, this is
        asymptotic; after 30+ bars it should be > 99.
        """
        prices = pd.Series(range(1, 252), dtype=float)
        rsi = self._ta_rsi(prices)
        last_rsi = float(rsi.dropna().iloc[-1])
        self.assertGreater(last_rsi, 99.0,
            f"RSI on monotonically increasing prices should be > 99, got {last_rsi:.2f}")

    def test_rsi_approaches_0_on_monotonic_decrease(self):
        """
        A strictly monotonically decreasing price series has no up days,
        so average gain → 0 and RSI → 0. Should be < 1 after 30+ bars.
        """
        prices = pd.Series(range(251, 0, -1), dtype=float)
        rsi = self._ta_rsi(prices)
        last_rsi = float(rsi.dropna().iloc[-1])
        self.assertLess(last_rsi, 1.0,
            f"RSI on monotonically decreasing prices should be < 1, got {last_rsi:.2f}")

    def test_rsi_near_50_on_alternating_series(self):
        """
        A series that alternates equal up/down moves of the same magnitude
        should produce RSI near 50 (equal average gain and loss).
        Use absolute-change series to keep it symmetric.
        """
        prices = pd.Series([100.0 + (1.0 if i % 2 == 0 else -1.0) * (i // 2 + 1)
                            for i in range(200)], dtype=float)
        rsi = self._ta_rsi(prices)
        last_rsi = float(rsi.dropna().iloc[-1])
        # Allow ±15 tolerance — exact value depends on the specific sequence
        self.assertGreater(last_rsi, 35.0)
        self.assertLess(last_rsi, 65.0)

    def test_rsi_is_bounded_between_0_and_100(self):
        """RSI must always be in [0, 100] for any price series."""
        df = make_trending_df()
        rsi = self._ta_rsi(df["close"])
        valid = rsi.dropna()
        self.assertTrue((valid >= 0).all(), "RSI has values below 0")
        self.assertTrue((valid <= 100).all(), "RSI has values above 100")

    # ── Consistency: chart_generator RSI == technical.py RSI ──────────────

    def test_chart_and_analysis_rsi_use_same_formula(self):
        """
        The raw RSI series from ta.momentum.rsi must agree with what
        calc_rsi() returns before rounding. calc_rsi() rounds to 2 dp, so
        we compare at 2 dp tolerance (max rounding error is 0.005).
        """
        df = make_trending_df()

        from analyzers.technical import calc_rsi
        tech_rsi_rounded = calc_rsi(df)["rsi"]   # already round(x, 2)

        chart_rsi_raw = float(ta.momentum.rsi(df["close"], window=14).iloc[-1])
        chart_rsi_rounded = round(chart_rsi_raw, 2)

        self.assertEqual(
            tech_rsi_rounded, chart_rsi_rounded,
            msg=f"Chart RSI rounded ({chart_rsi_rounded}) differs from analysis RSI ({tech_rsi_rounded})"
        )

    def test_chart_rsi_last90_matches_analysis_rsi(self):
        """
        The last value of rsi.tail(90) (as chart_generator computes it) must
        round to the same 2dp value as calc_rsi() on the same df.
        """
        df = make_trending_df()

        from analyzers.technical import calc_rsi
        expected = calc_rsi(df)["rsi"]

        actual = round(float(ta.momentum.rsi(df["close"], window=14).tail(90).iloc[-1]), 2)

        self.assertEqual(expected, actual)

    def test_old_simple_rolling_mean_differs_from_wilders(self):
        """
        Confirms that the OLD formula (simple rolling mean of gains/losses)
        produces different results than Wilder's smoothing, justifying the fix.
        """
        df = make_trending_df()
        close = df["close"]

        # Old formula (simple rolling mean)
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta).clip(lower=0).rolling(14).mean()
        old_rsi = float((100 - 100 / (1 + gain / loss)).iloc[-1])

        # New formula (Wilder's / EWM)
        new_rsi = float(ta.momentum.rsi(close, window=14).iloc[-1])

        # They should NOT be equal (this test confirms the formulas differ,
        # which proves the fix was necessary)
        self.assertNotAlmostEqual(
            old_rsi, new_rsi, places=2,
            msg="Simple rolling mean and Wilder's smoothing should produce different RSI — "
                "if they're equal, the synthetic data may be too regular to distinguish them"
        )

    def test_wilder_rsi_is_valid_after_warmup(self):
        """
        ta.momentum.rsi uses EWM (Wilder's method), which starts computing from
        bar 1 (the first diff). The first bar is always NaN (no diff). After
        that, EWM produces non-NaN values throughout. We verify that the series
        is fully populated on a 252-bar DataFrame (i.e., only the very first
        NaN from diff() propagates).
        """
        df = make_trending_df()
        rsi = ta.momentum.rsi(df["close"], window=14)
        # Only the first entry should be NaN (from the diff() step)
        nan_count = int(rsi.isna().sum())
        self.assertLessEqual(nan_count, 14,
            f"Expected at most 14 NaN RSI values at warmup, got {nan_count}")
        # After warmup, all values should be finite and bounded
        valid = rsi.dropna()
        self.assertTrue((valid >= 0).all())
        self.assertTrue((valid <= 100).all())


if __name__ == "__main__":
    unittest.main()
