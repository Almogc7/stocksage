"""
Characterization tests for PR 0a (docs/SETUP_SPEC.md §14):

1. Verdict cutoffs, at the exact boundaries. Every score full_analysis()
   can actually produce is a multiple of 5 (base 20 + {15 or 5} RSI + an
   optional subset of {15, 20, 15, 10, 5}), so 34/54/74 are structurally
   unreachable through real scoring -- they are tested directly against the
   extracted pure function. 35/55/75 (the cutoffs themselves) ARE reachable
   and are additionally tested end-to-end through full_analysis().

2. The stop-loss / take-profit formula (price - 1.5*ATR, price + 3.0*ATR),
   against an exactly-known, mocked ATR value -- same convention
   test_rsi_label.py already uses (_MINIMAL_ATR) rather than engineering a
   real OHLCV series to converge to a known ATR by hand.

No network, no real database, no production behavior changed by this file.
"""

import unittest
from unittest.mock import patch

from analyzers.technical import _score_to_verdict, full_analysis
from tests.fixtures import make_trending_df

_MINIMAL_MACD = {"macd": 0.0, "signal_line": 0.0, "histogram": 0.0, "crossover": "none"}
_MINIMAL_BB = {"upper": 120.0, "middle": 110.0, "lower": 100.0, "current_price": 115.0, "position": "middle"}
_MINIMAL_PIVOTS = {"pp": 110.0, "r1": 112.0, "r2": 114.0, "r3": 116.0,
                   "s1": 108.0, "s2": 106.0, "s3": 104.0}
_MINIMAL_SWINGS = {
    "swing_highs": [], "swing_lows": [],
    "nearest_resistance": None, "nearest_support": None,
}
_ABOVE_SMA = {"sma150": 90.0, "above_sma150": True, "pct_from_sma": 10.0}
_HEALTHY_RSI = {"rsi": 55.0, "signal": "neutral"}


def _run_full_analysis(
    atr: float = 2.0,
    macd_bullish: bool = False,
    sma200: float | None = None,
    vwap: float | None = None,
    current_price: float = 100.0,
) -> dict:
    """Call full_analysis() with all expensive helpers mocked -- mirrors
    tests/test_rsi_label.py's _run_full_analysis_with_rsi() exactly, but
    dials the binary scoring components (macd/sma200/vwap) and the ATR
    value instead of RSI, since this file characterizes verdict cutoffs
    and the stop/TP formula, not RSI labeling."""
    df = make_trending_df()
    minimal_atr = {
        "atr": atr, "atr_pct": 1.8, "volatility": "low",
        "stop_loss_1x": current_price - atr, "stop_loss_15x": current_price - atr * 1.5,
        "take_profit_2x": current_price + atr * 2,
    }

    with (
        patch("analyzers.technical.check_sma150", return_value=_ABOVE_SMA),
        patch("analyzers.technical.calc_rsi", return_value=_HEALTHY_RSI),
        patch("analyzers.technical.calc_macd", return_value=_MINIMAL_MACD),
        patch("analyzers.technical.calc_bollinger", return_value=_MINIMAL_BB),
        patch("analyzers.technical.calc_atr", return_value=minimal_atr),
        patch("analyzers.technical.calc_pivot_points", return_value=_MINIMAL_PIVOTS),
        patch("analyzers.technical.calc_swing_levels", return_value=_MINIMAL_SWINGS),
        patch("analyzers.technical._sma200", return_value=sma200),
        patch("analyzers.technical._vwap", return_value=vwap),
        patch("analyzers.technical._macd_bullish_last3", return_value=macd_bullish),
        patch("analyzers.technical._volume_spike", return_value=False),
        patch("analyzers.technical._stoch_rsi_bullish", return_value=False),
    ):
        return full_analysis("TEST", df, current_price)


class TestScoreToVerdictBoundaries(unittest.TestCase):
    """Pure-function boundary tests -- the only way to exercise 34/54/74,
    since every real full_analysis() score is a multiple of 5."""

    def test_34_is_neutral(self):
        self.assertEqual(_score_to_verdict(34), "NEUTRAL")

    def test_35_is_watch(self):
        self.assertEqual(_score_to_verdict(35), "WATCH")

    def test_54_is_watch(self):
        self.assertEqual(_score_to_verdict(54), "WATCH")

    def test_55_is_buy(self):
        self.assertEqual(_score_to_verdict(55), "BUY")

    def test_74_is_buy(self):
        self.assertEqual(_score_to_verdict(74), "BUY")

    def test_75_is_strong_buy(self):
        self.assertEqual(_score_to_verdict(75), "STRONG BUY")


class TestVerdictCutoffsEndToEnd(unittest.TestCase):
    """Real scoring pipeline, dialed to the three reachable cutoff scores,
    cross-checked against the extracted pure function."""

    def test_score_35_is_watch(self):
        # base 20 (price_above_sma150) + 15 (rsi healthy), nothing else
        result = _run_full_analysis()
        self.assertEqual(result["score"], 35)
        self.assertEqual(result["verdict"], "WATCH")
        self.assertEqual(result["verdict"], _score_to_verdict(result["score"]))

    def test_score_55_is_buy(self):
        # +20 (macd bullish) on top of the 35 base
        result = _run_full_analysis(macd_bullish=True)
        self.assertEqual(result["score"], 55)
        self.assertEqual(result["verdict"], "BUY")
        self.assertEqual(result["verdict"], _score_to_verdict(result["score"]))

    def test_score_75_is_strong_buy(self):
        # +15 (sma150 > sma200) + 5 (price > vwap) on top of the 55 base
        result = _run_full_analysis(macd_bullish=True, sma200=80.0, vwap=90.0)
        self.assertEqual(result["score"], 75)
        self.assertEqual(result["verdict"], "STRONG BUY")
        self.assertEqual(result["verdict"], _score_to_verdict(result["score"]))


class TestStopTakeProfitFormula(unittest.TestCase):
    """stop_loss = price - 1.5*ATR, take_profit = price + 3.0*ATR, against
    an exactly-known (mocked) ATR -- no floating-point tolerance needed,
    every operand here is an exact binary float."""

    def test_round_atr(self):
        result = _run_full_analysis(atr=2.0, current_price=100.0)
        self.assertEqual(result["stop_loss"], 97.0)
        self.assertEqual(result["take_profit"], 106.0)

    def test_non_round_atr(self):
        result = _run_full_analysis(atr=3.37, current_price=150.0)
        self.assertEqual(result["stop_loss"], round(150.0 - 1.5 * 3.37, 4))
        self.assertEqual(result["take_profit"], round(150.0 + 3.0 * 3.37, 4))


if __name__ == "__main__":
    unittest.main()
