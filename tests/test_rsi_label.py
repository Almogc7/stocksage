"""
Tests for Fix 6: RSI fringe-zone label correction.

Verifies that:
  - RSI in the ideal swing zone (45–65) → "rsi_healthy_range" in triggered_signals
  - RSI in the acceptable fringe zone (35–44 or 66–75) → "rsi_acceptable_zone",
    NOT "rsi_healthy_range"
  - RSI outside all valid ranges (< 35 or > 75) → veto fires, triggered_signals is empty
  - Boundary values (34, 35, 44, 45, 65, 66, 75, 76) are handled correctly
"""

import unittest
from unittest.mock import patch

from tests.fixtures import make_trending_df


_MINIMAL_MACD = {"macd": 0.0, "signal_line": 0.0, "histogram": 0.0, "crossover": "none"}
_MINIMAL_BB   = {"upper": 120.0, "middle": 110.0, "lower": 100.0, "current_price": 115.0, "position": "middle"}
_MINIMAL_ATR  = {
    "atr": 2.0, "atr_pct": 1.8, "volatility": "low",
    "stop_loss_1x": 113.0, "stop_loss_15x": 112.0, "take_profit_2x": 119.0,
}
_MINIMAL_PIVOTS = {"pp": 110.0, "r1": 112.0, "r2": 114.0, "r3": 116.0,
                   "s1": 108.0, "s2": 106.0, "s3": 104.0}
_MINIMAL_SWINGS = {
    "swing_highs": [], "swing_lows": [],
    "nearest_resistance": None, "nearest_support": None,
}
_ABOVE_SMA = {"sma150": 90.0, "above_sma150": True, "pct_from_sma": 10.0}


def _run_full_analysis_with_rsi(rsi_value: float) -> dict:
    """
    Call full_analysis() with all expensive helpers mocked, injecting a
    specific RSI value so we can test the scoring branch in isolation.
    Current price is always 100.0; SMA150 is 90.0 so price > SMA150.
    """
    from analyzers.technical import full_analysis

    df = make_trending_df()

    with (
        patch("analyzers.technical.check_sma150", return_value=_ABOVE_SMA),
        patch("analyzers.technical.calc_rsi",
              return_value={"rsi": rsi_value, "signal": "neutral"}),
        patch("analyzers.technical.calc_macd", return_value=_MINIMAL_MACD),
        patch("analyzers.technical.calc_bollinger", return_value=_MINIMAL_BB),
        patch("analyzers.technical.calc_atr", return_value=_MINIMAL_ATR),
        patch("analyzers.technical.calc_pivot_points", return_value=_MINIMAL_PIVOTS),
        patch("analyzers.technical.calc_swing_levels", return_value=_MINIMAL_SWINGS),
        patch("analyzers.technical._sma200", return_value=None),
        patch("analyzers.technical._vwap", return_value=None),
        patch("analyzers.technical._macd_bullish_last3", return_value=False),
        patch("analyzers.technical._volume_spike", return_value=False),
        patch("analyzers.technical._stoch_rsi_bullish", return_value=False),
    ):
        return full_analysis("TEST", df, 100.0)


class TestRsiLabel(unittest.TestCase):

    # ── Ideal zone (45–65) ────────────────────────────────────────────────────

    def test_rsi_45_ideal_lower_boundary(self):
        result = _run_full_analysis_with_rsi(45.0)
        self.assertIn("rsi_healthy_range", result["triggered_signals"])
        self.assertNotIn("rsi_acceptable_zone", result["triggered_signals"])
        self.assertEqual(result["score"], 35)  # +20 SMA + +15 RSI ideal

    def test_rsi_55_ideal_midpoint(self):
        result = _run_full_analysis_with_rsi(55.0)
        self.assertIn("rsi_healthy_range", result["triggered_signals"])
        self.assertNotIn("rsi_acceptable_zone", result["triggered_signals"])

    def test_rsi_65_ideal_upper_boundary(self):
        result = _run_full_analysis_with_rsi(65.0)
        self.assertIn("rsi_healthy_range", result["triggered_signals"])
        self.assertNotIn("rsi_acceptable_zone", result["triggered_signals"])

    # ── Fringe-low zone (35–44) ───────────────────────────────────────────────

    def test_rsi_35_fringe_low_boundary(self):
        result = _run_full_analysis_with_rsi(35.0)
        self.assertIn("rsi_acceptable_zone", result["triggered_signals"])
        self.assertNotIn("rsi_healthy_range", result["triggered_signals"])
        self.assertEqual(result["score"], 25)  # +20 SMA + +5 fringe

    def test_rsi_40_fringe_low_midpoint(self):
        result = _run_full_analysis_with_rsi(40.0)
        self.assertIn("rsi_acceptable_zone", result["triggered_signals"])
        self.assertNotIn("rsi_healthy_range", result["triggered_signals"])

    def test_rsi_44_fringe_low_upper_boundary(self):
        result = _run_full_analysis_with_rsi(44.0)
        self.assertIn("rsi_acceptable_zone", result["triggered_signals"])
        self.assertNotIn("rsi_healthy_range", result["triggered_signals"])

    # ── Fringe-high zone (66–75) ──────────────────────────────────────────────

    def test_rsi_66_fringe_high_lower_boundary(self):
        result = _run_full_analysis_with_rsi(66.0)
        self.assertIn("rsi_acceptable_zone", result["triggered_signals"])
        self.assertNotIn("rsi_healthy_range", result["triggered_signals"])

    def test_rsi_70_fringe_high_midpoint(self):
        result = _run_full_analysis_with_rsi(70.0)
        self.assertIn("rsi_acceptable_zone", result["triggered_signals"])
        self.assertNotIn("rsi_healthy_range", result["triggered_signals"])

    def test_rsi_75_fringe_high_upper_boundary(self):
        result = _run_full_analysis_with_rsi(75.0)
        self.assertIn("rsi_acceptable_zone", result["triggered_signals"])
        self.assertNotIn("rsi_healthy_range", result["triggered_signals"])

    # ── Veto zone — score should be 0 and triggered_signals empty ────────────

    def test_rsi_34_below_minimum_veto(self):
        result = _run_full_analysis_with_rsi(34.0)
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["triggered_signals"], [])
        self.assertEqual(result["verdict"], "NEUTRAL")

    def test_rsi_76_above_maximum_veto(self):
        result = _run_full_analysis_with_rsi(76.0)
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["triggered_signals"], [])
        self.assertEqual(result["verdict"], "NEUTRAL")

    # ── Fringe label contributes correct +5 points ────────────────────────────

    def test_fringe_rsi_score_is_five_points_not_fifteen(self):
        result_fringe = _run_full_analysis_with_rsi(42.0)
        result_ideal  = _run_full_analysis_with_rsi(55.0)
        # Ideal gives +15, fringe gives +5 — difference should be exactly 10
        self.assertEqual(result_ideal["score"] - result_fringe["score"], 10)


if __name__ == "__main__":
    unittest.main()
