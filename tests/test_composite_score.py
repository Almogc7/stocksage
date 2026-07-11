"""
Tests for the composite scoring engine (analyzers/composite.py).

Everything is synthetic and offline: the SPY context is injected (never
fetched), the clock is injected for session-fraction math, and no DB is
touched. Pure component scorers are tested in isolation; composite_score()
is exercised end-to-end on fixture data.
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from analyzers import composite as comp
from tests.fixtures import make_trending_df

_ET = ZoneInfo("America/New_York")


def _bull_context(spy_closes=None):
    return {
        "regime": "BULL", "spy_available": True,
        "spy_close": 500.0, "spy_sma150": 480.0,
        "required_rs": 1.0, "required_score": 70,
        "spy_closes": spy_closes,
    }


def _bear_context(spy_closes=None):
    return {
        "regime": "BEAR", "spy_available": True,
        "spy_close": 450.0, "spy_sma150": 480.0,
        "required_rs": 1.2, "required_score": 75,
        "spy_closes": spy_closes,
    }


def _flat_spy_closes(n=252):
    return pd.Series(np.full(n, 100.0))


# ── Pure component scorers ────────────────────────────────────────────────────

class TestRsiPoints(unittest.TestCase):

    def test_healthy_band_full(self):
        for rsi in (45.0, 55.0, 65.0):
            self.assertEqual(comp._rsi_points(rsi), 15.0)

    def test_fringe_partial(self):
        for rsi in (35.0, 44.9, 65.1, 75.0):
            self.assertEqual(comp._rsi_points(rsi), 7.0)

    def test_outside_bounds_zero_not_veto(self):
        """Out-of-band RSI earns 0 points but never disqualifies."""
        self.assertEqual(comp._rsi_points(80.0), 0.0)
        self.assertEqual(comp._rsi_points(20.0), 0.0)


class TestExtensionPoints(unittest.TestCase):

    def test_full_within_ten_percent(self):
        self.assertEqual(comp._extension_points(0.5), 10.0)
        self.assertEqual(comp._extension_points(10.0), 10.0)

    def test_taper_between_ten_and_twenty(self):
        self.assertAlmostEqual(comp._extension_points(15.0), 5.0)

    def test_zero_when_overextended(self):
        self.assertEqual(comp._extension_points(20.0), 0.0)
        self.assertEqual(comp._extension_points(35.0), 0.0)


class TestMacdPoints(unittest.TestCase):

    def _hist(self, values):
        return pd.Series(values, dtype=float)

    def test_positive_increasing_three_bars_full(self):
        self.assertEqual(comp._macd_points(self._hist([0.0, 0.1, 0.2, 0.3])), 15.0)

    def test_positive_increasing_two_bars_partial(self):
        self.assertEqual(comp._macd_points(self._hist([0.5, 0.3, 0.1, 0.4])), 10.0)

    def test_negative_histogram_zero(self):
        self.assertEqual(comp._macd_points(self._hist([0.3, 0.2, 0.1, -0.1])), 0.0)

    def test_positive_but_decreasing_zero(self):
        self.assertEqual(comp._macd_points(self._hist([0.5, 0.4, 0.3, 0.2])), 0.0)


class TestStochPoints(unittest.TestCase):

    def test_valid_cross_scores(self):
        k = pd.Series([0.1, 0.4, 0.45, 0.6])
        d = pd.Series([0.3, 0.5, 0.5, 0.5])
        self.assertEqual(comp._stoch_points(k, d), 10.0)

    def test_cross_above_eighty_scores_zero(self):
        k = pd.Series([0.5, 0.7, 0.75, 0.9])
        d = pd.Series([0.6, 0.8, 0.8, 0.85])
        self.assertEqual(comp._stoch_points(k, d), 0.0)

    def test_cross_from_extreme_oversold_scores_zero(self):
        """A snap from %K < 0.2 is mean-reversion, not continuation."""
        k = pd.Series([0.05, 0.1, 0.15, 0.5])
        d = pd.Series([0.2, 0.3, 0.3, 0.35])
        self.assertEqual(comp._stoch_points(k, d), 0.0)

    def test_no_cross_scores_zero(self):
        k = pd.Series([0.5, 0.55, 0.6, 0.65])
        d = pd.Series([0.3, 0.35, 0.4, 0.45])
        self.assertEqual(comp._stoch_points(k, d), 0.0)


class TestRelvolPoints(unittest.TestCase):

    def test_at_or_below_one_x_zero(self):
        self.assertEqual(comp._relvol_points(1.0), 0.0)
        self.assertEqual(comp._relvol_points(0.4), 0.0)

    def test_full_at_threshold(self):
        self.assertEqual(comp._relvol_points(1.5), 15.0)
        self.assertEqual(comp._relvol_points(3.0), 15.0)

    def test_linear_ramp_midpoint(self):
        self.assertAlmostEqual(comp._relvol_points(1.25), 7.5)


class TestSessionFraction(unittest.TestCase):

    def test_market_closed_is_full_session(self):
        self.assertEqual(comp._session_fraction(None, market_open=False), 1.0)

    def test_midday_is_half(self):
        now = datetime(2026, 7, 10, 12, 45, tzinfo=_ET)  # 195 of 390 min
        self.assertAlmostEqual(comp._session_fraction(now, True), 0.5)

    def test_open_is_floored_not_zero(self):
        now = datetime(2026, 7, 10, 9, 31, tzinfo=_ET)
        self.assertEqual(comp._session_fraction(now, True), 0.1)

    def test_late_session_clamped_to_one(self):
        now = datetime(2026, 7, 10, 16, 30, tzinfo=_ET)
        self.assertEqual(comp._session_fraction(now, True), 1.0)

    def test_early_session_normalization_fixes_false_negative(self):
        """The motivating bug: 30% of a session elapsed with 40% of average
        daily volume already traded is STRONG participation (1.33x), but a
        raw comparison to the full 20-day average would read it as 0.4x."""
        fraction = 0.3
        raw_rel = 0.4
        normalized = raw_rel / fraction
        self.assertEqual(comp._relvol_points(raw_rel), 0.0)      # old-style read
        self.assertGreater(comp._relvol_points(normalized), 0.0)  # normalized read


class TestObvSlope(unittest.TestCase):

    def test_accumulation_gives_positive_slope(self):
        closes = pd.Series(np.linspace(100, 120, 40))   # every day up
        volumes = pd.Series(np.full(40, 1e6))
        self.assertGreater(comp._obv_slope(closes, volumes), 0)

    def test_distribution_gives_negative_slope(self):
        closes = pd.Series(np.linspace(120, 100, 40))   # every day down
        volumes = pd.Series(np.full(40, 1e6))
        self.assertLess(comp._obv_slope(closes, volumes), 0)

    def test_insufficient_data_is_none(self):
        closes = pd.Series(np.linspace(100, 110, 5))
        volumes = pd.Series(np.full(5, 1e6))
        self.assertIsNone(comp._obv_slope(closes, volumes))


class TestRsRatio(unittest.TestCase):

    def _closes(self, start, end, n=100):
        return pd.Series(np.linspace(start, end, n))

    def test_outperformer_above_one(self):
        stock = self._closes(100, 120)  # +20%
        spy = self._closes(100, 110)    # +10%
        rs = comp._rs_ratio(stock, spy, window=60)
        self.assertGreater(rs, 1.0)

    def test_stable_when_spy_flat(self):
        """Raw pct-division explodes on a flat SPY; the (1+r)/(1+r) form
        must return a sane finite ratio."""
        stock = self._closes(100, 110)
        spy = _flat_spy_closes(100)
        rs = comp._rs_ratio(stock, spy, window=60)
        self.assertTrue(np.isfinite(rs))
        self.assertGreater(rs, 1.0)

    def test_both_negative_keeps_ordering(self):
        """Stock -5% vs SPY -20%: raw division gives 0.25 (reads as weak);
        the ratio form correctly reads relative OUTperformance (> 1)."""
        stock = self._closes(100, 95)
        spy = self._closes(100, 80)
        rs = comp._rs_ratio(stock, spy, window=60)
        self.assertGreater(rs, 1.0)

    def test_none_without_spy(self):
        self.assertIsNone(comp._rs_ratio(self._closes(100, 110), None, window=60))

    def test_rs_points_regime_thresholds(self):
        self.assertEqual(comp._rs_points(1.05, required_rs=1.0), 25.0)  # bull: full
        self.assertLess(comp._rs_points(1.05, required_rs=1.2), 25.0)   # bear: partial
        self.assertGreater(comp._rs_points(1.05, required_rs=1.2), 0.0)
        self.assertEqual(comp._rs_points(0.8, required_rs=1.0), 0.0)
        self.assertEqual(comp._rs_points(None, required_rs=1.0), 0.0)


class TestStopMultiplier(unittest.TestCase):

    def test_bands(self):
        self.assertEqual(comp._stop_multiplier(65), 2.0)
        self.assertEqual(comp._stop_multiplier(70), 2.0)
        self.assertEqual(comp._stop_multiplier(75), 2.25)
        self.assertEqual(comp._stop_multiplier(80), 2.5)
        self.assertEqual(comp._stop_multiplier(90), 2.75)
        self.assertEqual(comp._stop_multiplier(100), 3.0)


# ── Market context / regime ───────────────────────────────────────────────────

class TestMarketContext(unittest.TestCase):

    def _spy_df(self, rising: bool):
        df = make_trending_df(n=252, trend=0.002 if rising else -0.002)
        return df

    def test_bull_regime_thresholds(self):
        ctx = comp.compute_market_context(spy_df=self._spy_df(rising=True), market_open=False)
        self.assertEqual(ctx["regime"], "BULL")
        self.assertEqual(ctx["required_rs"], 1.0)
        self.assertEqual(ctx["required_score"], 70)

    def test_bear_regime_thresholds(self):
        ctx = comp.compute_market_context(spy_df=self._spy_df(rising=False), market_open=False)
        self.assertEqual(ctx["regime"], "BEAR")
        self.assertEqual(ctx["required_rs"], 1.2)
        self.assertEqual(ctx["required_score"], 75)

    def test_missing_spy_falls_back_to_strict(self):
        """No SPY data must never loosen the bar: bear thresholds apply."""
        ctx = comp.compute_market_context(spy_df=pd.DataFrame(), market_open=False)
        self.assertEqual(ctx["regime"], "UNKNOWN")
        self.assertFalse(ctx["spy_available"])
        self.assertEqual(ctx["required_rs"], 1.2)
        self.assertEqual(ctx["required_score"], 75)

    def test_short_spy_history_falls_back_to_strict(self):
        ctx = comp.compute_market_context(spy_df=make_trending_df(n=100), market_open=False)
        self.assertEqual(ctx["regime"], "UNKNOWN")


# ── End-to-end composite_score ────────────────────────────────────────────────

class TestCompositeScoreEndToEnd(unittest.TestCase):

    def setUp(self):
        self.df = make_trending_df(n=252, trend=0.002, seed=3)
        self.spy = make_trending_df(n=252, trend=0.001, seed=8)["close"].reset_index(drop=True)

    def test_gate_fail_below_sma150(self):
        df = make_trending_df(n=252, trend=-0.003, seed=5)  # downtrend
        out = comp.composite_score("TEST", df, _bull_context(self.spy), log=False)
        self.assertFalse(out["hard_gate"]["passed"])
        self.assertEqual(out["total_score"], 0.0)
        self.assertFalse(out["flag_buy"])
        self.assertIsNotNone(out["hard_gate"]["reason"])
        self.assertIsNone(out["layers"])

    def test_gate_requires_both_conditions(self):
        out = comp.composite_score("TEST", self.df, _bull_context(self.spy), log=False)
        gate = out["hard_gate"]
        if gate["passed"]:
            self.assertTrue(gate["price_above_sma150"])
            self.assertTrue(gate["sma150_above_sma200"])
        else:
            self.assertIn(False, (gate["price_above_sma150"], gate["sma150_above_sma200"]))

    def test_passing_symbol_reports_every_component(self):
        out = comp.composite_score("TEST", self.df, _bull_context(self.spy), log=False)
        self.assertTrue(out["hard_gate"]["passed"], "fixture should pass the gate")
        lay = out["layers"]
        for layer in ("trend", "momentum", "volume", "relative_strength"):
            self.assertIn(layer, lay)
            self.assertEqual(lay[layer]["max"], 25)
            self.assertGreaterEqual(lay[layer]["points"], 0)
            self.assertLessEqual(lay[layer]["points"], 25)
        for key in ("rsi_pts", "extension_pts", "breakout_pts"):
            self.assertIn(key, lay["trend"])
        for key in ("macd_pts", "stoch_pts"):
            self.assertIn(key, lay["momentum"])
        for key in ("relvol_pts", "obv_pts", "rel_vol", "session_fraction"):
            self.assertIn(key, lay["volume"])
        for key in ("rs_ratio", "required_rs", "window_days"):
            self.assertIn(key, lay["relative_strength"])

    def test_total_is_sum_of_layers_and_bounded(self):
        out = comp.composite_score("TEST", self.df, _bull_context(self.spy), log=False)
        lay = out["layers"]
        expected = sum(lay[l]["points"] for l in
                       ("trend", "momentum", "volume", "relative_strength"))
        self.assertAlmostEqual(out["total_score"], round(expected, 1))
        self.assertLessEqual(out["total_score"], 100.0)

    def test_trend_layer_capped_at_25(self):
        """rsi 15 + extension 10 + breakout 5 = 30 raw; the layer max is 25."""
        out = comp.composite_score("TEST", self.df, _bull_context(self.spy), log=False)
        self.assertLessEqual(out["layers"]["trend"]["points"], 25.0)

    def test_flag_buy_respects_regime_required_score(self):
        bull = comp.composite_score("TEST", self.df, _bull_context(self.spy), log=False)
        bear = comp.composite_score("TEST", self.df, _bear_context(self.spy), log=False)
        self.assertEqual(bull["flag_buy"], bull["total_score"] >= 70)
        self.assertEqual(bear["flag_buy"], bear["total_score"] >= 75)

    def test_market_open_slices_live_bar(self):
        out_closed = comp.composite_score("TEST", self.df, _bull_context(self.spy),
                                          market_open=False, log=False)
        out_open = comp.composite_score("TEST", self.df, _bull_context(self.spy),
                                        market_open=True,
                                        now=datetime(2026, 7, 10, 12, 45, tzinfo=_ET),
                                        log=False)
        self.assertEqual(out_open["bars_completed"], out_closed["bars_completed"] - 1)

    def test_stop_uses_completed_bar_close(self):
        out = comp.composite_score("TEST", self.df, _bull_context(self.spy), log=False)
        stop = out["stop"]
        self.assertLess(stop["stop_price"], out["last_completed_close"])
        self.assertGreaterEqual(stop["multiplier"], 2.0)
        self.assertLessEqual(stop["multiplier"], 3.0)
        expected = round(out["last_completed_close"] - stop["multiplier"] * stop["atr"], 4)
        self.assertAlmostEqual(stop["stop_price"], expected, places=4)

    def test_no_rsi_veto_in_composite(self):
        """An overbought RSI must reduce points, never zero the whole score
        (the legacy engine vetoes RSI > 75; this one does not)."""
        df = make_trending_df(n=252, trend=0.005, seed=13)  # steep → high RSI
        out = comp.composite_score("TEST", df, _bull_context(self.spy), log=False)
        if out["hard_gate"]["passed"]:
            self.assertGreater(out["total_score"], 0.0)


class TestBreakoutBonus(unittest.TestCase):

    def _reclaim_df(self, volume_mult: float) -> pd.DataFrame:
        """~200 flat bars, a dip below SMA150, then a final completed bar
        closing back above SMA150 on volume_mult x average volume."""
        n = 210
        close = np.full(n, 100.0)
        close[-30:-1] = 93.0          # sit below the ~100 SMA150
        close[-1] = 101.0             # reclaim on the last completed bar
        volume = np.full(n, 1_000_000.0)
        volume[-1] = 1_000_000.0 * volume_mult
        return pd.DataFrame({
            "open": close, "close": close,
            "high": close * 1.01, "low": close * 0.99,
            "volume": volume,
        })

    def test_reclaim_on_heavy_volume_earns_bonus(self):
        df = self._reclaim_df(volume_mult=2.5)
        closes, volumes = df["close"], df["volume"]
        sma = closes.rolling(window=150).mean()
        self.assertEqual(comp._breakout_points(closes, sma, volumes), 5.0)

    def test_reclaim_on_light_volume_earns_nothing(self):
        df = self._reclaim_df(volume_mult=1.2)
        closes, volumes = df["close"], df["volume"]
        sma = closes.rolling(window=150).mean()
        self.assertEqual(comp._breakout_points(closes, sma, volumes), 0.0)

    def test_no_cross_no_bonus(self):
        df = make_trending_df(n=252)  # steadily above SMA150, no reclaim
        closes, volumes = df["close"], df["volume"]
        sma = closes.rolling(window=150).mean()
        self.assertEqual(comp._breakout_points(closes, sma, volumes), 0.0)


if __name__ == "__main__":
    unittest.main()
