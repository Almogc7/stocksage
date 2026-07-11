"""
Tests for the EMA->SMA alignment of the 150/200 moving averages (decision D1).

The TradingView "Swing Trade Analyser" Pine Script — the methodology source
of truth — uses ta.sma() for the 150/200 lines, and Stack C's
cached_indicators.py has always been SMA-based. full_analysis() must now
compute SIMPLE moving averages and expose them under sma-named keys.

No network access — synthetic DataFrames only.
"""
import unittest

from analyzers.technical import check_sma150, _sma200, full_analysis
from tests.fixtures import make_trending_df


class TestSmaComputation(unittest.TestCase):

    def test_sma150_matches_rolling_mean_reference(self):
        df = make_trending_df()
        expected = round(float(df["close"].rolling(window=150).mean().iloc[-1]), 4)
        out = check_sma150(df, current_price=float(df["close"].iloc[-1]))
        self.assertEqual(out["sma150"], expected)

    def test_sma150_is_not_the_old_ema(self):
        """Guard against a silent revert: on real-shaped data the EMA and
        SMA of the same window must differ."""
        df = make_trending_df()
        ema150 = float(df["close"].ewm(span=150, adjust=False).mean().iloc[-1])
        out = check_sma150(df, current_price=float(df["close"].iloc[-1]))
        self.assertNotAlmostEqual(out["sma150"], round(ema150, 4), places=2)

    def test_sma200_matches_rolling_mean_reference(self):
        df = make_trending_df(n=252)
        expected = float(df["close"].rolling(window=200).mean().iloc[-1])
        self.assertAlmostEqual(_sma200(df), expected, places=6)

    def test_sma200_none_when_history_below_200_bars(self):
        df = make_trending_df(n=180)
        self.assertIsNone(_sma200(df))

    def test_sma150_nan_on_short_history_reads_as_below(self):
        """<150 bars → rolling mean is NaN → above_sma150 must be False
        (comparison against NaN), preserving the veto-on-unknown behavior
        the evaluator's insufficient-history label relies on."""
        df = make_trending_df(n=100)
        out = check_sma150(df, current_price=float(df["close"].iloc[-1]))
        self.assertFalse(out["above_sma150"])

    def test_pct_from_sma_sign_matches_above_flag(self):
        df = make_trending_df()
        price = float(df["close"].rolling(window=150).mean().iloc[-1]) * 1.10
        out = check_sma150(df, current_price=price)
        self.assertTrue(out["above_sma150"])
        self.assertGreater(out["pct_from_sma"], 0)


class TestFullAnalysisSmaKeys(unittest.TestCase):

    def setUp(self):
        self.df = make_trending_df()
        self.price = float(self.df["close"].iloc[-1])
        self.result = full_analysis("TEST", self.df, self.price)

    def test_sma_keys_present_and_ema_keys_gone(self):
        for key in ("sma150", "sma200", "above_sma150", "pct_from_sma"):
            self.assertIn(key, self.result)
        for stale in ("ema150", "ema200", "above_ema150", "pct_from_ema"):
            self.assertNotIn(stale, self.result)

    def test_triggered_signals_use_sma_names(self):
        joined = " ".join(self.result["triggered_signals"])
        self.assertNotIn("ema", joined)

    def test_veto_below_sma150(self):
        sma150 = float(self.df["close"].rolling(window=150).mean().iloc[-1])
        result = full_analysis("TEST", self.df, sma150 * 0.90)
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["verdict"], "NEUTRAL")
        self.assertEqual(result["triggered_signals"], [])

    def test_uptrend_grants_price_above_sma150_signal(self):
        self.assertIn("price_above_sma150", self.result["triggered_signals"])

    def test_sma150_above_sma200_signal_matches_reference_math(self):
        """The trend signal must fire exactly when the SIMPLE 150-mean is
        above the SIMPLE 200-mean (expectation derived from the data, not
        assumed — the mild-trend fixture can land either way)."""
        sma150 = float(self.df["close"].rolling(window=150).mean().iloc[-1])
        sma200 = float(self.df["close"].rolling(window=200).mean().iloc[-1])
        fired = "sma150_above_sma200" in self.result["triggered_signals"]
        self.assertEqual(fired, sma150 > sma200)


if __name__ == "__main__":
    unittest.main()
