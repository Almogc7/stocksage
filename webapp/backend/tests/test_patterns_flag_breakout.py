import unittest

from app.patterns import config
from app.patterns.flag_breakout import _dedupe_same_bar, _select_survivor, detect_flag_breakout
from app.patterns.types import Detection
from tests._pattern_fixtures import flat, frame_from_closes, ramp


def _candidate(start_date, bars, range_atr, direction="long", confirmed_on="2024-03-01"):
    return Detection(
        pattern="flag_breakout",
        direction=direction,
        symbol="TEST",
        start_date=start_date,
        end_date=confirmed_on,
        confirmed_on=confirmed_on,
        levels={"base_high": 104.1, "base_low": 99.9, "breakout_price": 110.0},
        rules={"base_range_atr": range_atr, "base_bars": bars, "relvol_at_breakout": 2.0},
    )


def _choppy_base(n_cycles: int = 10) -> list[float]:
    base: list[float] = []
    level = 100.0
    for i in range(n_cycles):
        base += ramp(3, level, level + 4 if i % 2 == 0 else level - 4)[1:]
        level = base[-1]
    return base


def _base_then_breakout() -> list[float]:
    closes = flat(20, 100.0) + _choppy_base()
    closes += ramp(3, closes[-1], closes[-1] + 15)[1:]
    return closes


def _base_then_breakdown() -> list[float]:
    closes = flat(20, 100.0) + _choppy_base()
    closes += ramp(3, closes[-1], closes[-1] - 15)[1:]
    return closes


def _bump_volume(df, n_bars: int, value: int = 5_000_000):
    df = df.copy()
    df.loc[df.index[-n_bars:], "volume"] = value
    return df


class FlagBreakoutLongTests(unittest.TestCase):
    def setUp(self):
        df = frame_from_closes(_base_then_breakout())
        self.df = _bump_volume(df, 3)
        self.detections = [d for d in detect_flag_breakout(self.df, "TEST") if d.direction == "long"]

    def test_detects_a_long_breakout(self):
        self.assertTrue(self.detections)

    def test_rules_satisfy_the_configured_thresholds(self):
        for d in self.detections:
            self.assertLessEqual(d.rules["base_range_atr"], config.BASE_MAX_RANGE_ATR)
            self.assertGreaterEqual(d.rules["base_bars"], config.BASE_MIN_BARS)
            self.assertLessEqual(d.rules["base_bars"], config.BASE_MAX_BARS)
            self.assertGreaterEqual(d.rules["relvol_at_breakout"], config.BREAKOUT_RELVOL_MIN)

    def test_breakout_price_clears_base_high(self):
        for d in self.detections:
            self.assertGreater(d.levels["breakout_price"], d.levels["base_high"])

    def test_no_breakout_without_a_volume_spike(self):
        df_flat_volume = frame_from_closes(_base_then_breakout())  # no volume bump
        dets = detect_flag_breakout(df_flat_volume, "TEST")
        self.assertEqual(dets, [])


class FlagBreakoutShortTests(unittest.TestCase):
    def setUp(self):
        df = frame_from_closes(_base_then_breakdown())
        self.df = _bump_volume(df, 3)
        self.detections = [d for d in detect_flag_breakout(self.df, "TEST") if d.direction == "short"]

    def test_detects_a_short_breakdown(self):
        self.assertTrue(self.detections)

    def test_breakout_price_clears_base_low_downward(self):
        for d in self.detections:
            self.assertLess(d.levels["breakout_price"], d.levels["base_low"])


class SameBarTieBreakTests(unittest.TestCase):
    def test_widest_window_wins(self):
        narrow = _candidate("2024-02-01", bars=10, range_atr=1.5)
        wide = _candidate("2024-01-25", bars=20, range_atr=1.5)
        survivor = _select_survivor([narrow, wide])
        self.assertIs(survivor, wide)

    def test_tie_on_bars_is_broken_by_tightest_range(self):
        looser = _candidate("2024-02-01", bars=15, range_atr=3.0)
        tighter = _candidate("2024-01-20", bars=15, range_atr=1.2)
        survivor = _select_survivor([looser, tighter])
        self.assertIs(survivor, tighter)

    def test_tie_on_bars_and_range_is_broken_by_earliest_start_date(self):
        later = _candidate("2024-02-05", bars=15, range_atr=2.0)
        earlier = _candidate("2024-01-10", bars=15, range_atr=2.0)
        survivor = _select_survivor([later, earlier])
        self.assertIs(survivor, earlier)

    def test_decision_order_is_bars_before_range_before_start_date(self):
        # A narrower-but-tighter window must NOT beat a wider-but-looser
        # one — width is checked first, full stop.
        wide_loose = _candidate("2024-02-01", bars=20, range_atr=5.0)
        narrow_tight = _candidate("2024-01-10", bars=10, range_atr=0.5)
        survivor = _select_survivor([wide_loose, narrow_tight])
        self.assertIs(survivor, wide_loose)

    def test_dedupe_same_bar_only_collapses_matching_direction_and_confirmed_on(self):
        a = _candidate("2024-01-01", bars=10, range_atr=1.0, direction="long", confirmed_on="2024-03-01")
        b = _candidate("2024-01-01", bars=20, range_atr=1.0, direction="long", confirmed_on="2024-03-01")
        c = _candidate("2024-01-01", bars=5, range_atr=1.0, direction="short", confirmed_on="2024-03-01")  # different direction
        d = _candidate("2024-01-01", bars=5, range_atr=1.0, direction="long", confirmed_on="2024-03-05")  # different bar
        survivors = _dedupe_same_bar([a, b, c, d])
        self.assertEqual(len(survivors), 3)
        self.assertIn(b, survivors)  # a lost to b (wider)
        self.assertNotIn(a, survivors)
        self.assertIn(c, survivors)
        self.assertIn(d, survivors)

    def test_integration_the_natural_fixture_keeps_the_wider_of_two_same_bar_candidates(self):
        # _base_then_breakout()'s choppy base has two pivots (p_start
        # candidates) one bar apart that both pair with the SAME p_end
        # and therefore the SAME base_high/base_low/range_atr and the
        # SAME breakout bar: an 11-bar window and a 10-bar window. Only
        # the wider (11-bar) one should survive.
        df = _bump_volume(frame_from_closes(_base_then_breakout()), 3)
        dets = [d for d in detect_flag_breakout(df, "TEST") if d.direction == "long"]
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].rules["base_bars"], 11)


class FlagBreakoutVolatilityInvarianceTests(unittest.TestCase):
    def test_the_same_shape_scaled_up_in_price_and_wick_detects_identically(self):
        # Scaling every price AND the wick size by the same factor scales
        # True Range (and therefore ATR) by that same factor, so the
        # ATR-relative rule values should be unchanged even though the
        # raw dollar levels are 3x bigger — proving the threshold means
        # the same thing on a "calmer" vs. "more volatile" (in dollar
        # terms) version of the identical shape.
        factor = 3.0
        base_closes = _base_then_breakout()
        scaled_closes = [c * factor for c in base_closes]

        df_base = _bump_volume(frame_from_closes(base_closes, wick=0.1), 3)
        df_scaled = _bump_volume(frame_from_closes(scaled_closes, wick=0.1 * factor), 3)

        base_dets = [d for d in detect_flag_breakout(df_base, "TEST") if d.direction == "long"]
        scaled_dets = [d for d in detect_flag_breakout(df_scaled, "TEST") if d.direction == "long"]

        self.assertTrue(base_dets)
        self.assertEqual(len(base_dets), len(scaled_dets))
        for b, s in zip(base_dets, scaled_dets):
            self.assertEqual(b.confirmed_on, s.confirmed_on)
            self.assertEqual(b.start_date, s.start_date)
            self.assertAlmostEqual(b.rules["base_range_atr"], s.rules["base_range_atr"], places=6)
            self.assertEqual(b.rules["base_bars"], s.rules["base_bars"])
            self.assertAlmostEqual(b.levels["breakout_price"] * factor, s.levels["breakout_price"], places=4)


class FlagBreakoutNoLookaheadTests(unittest.TestCase):
    def test_truncating_the_series_never_changes_an_already_confirmed_detection(self):
        df = _bump_volume(frame_from_closes(_base_then_breakout()), 3)
        full = detect_flag_breakout(df, "TEST")
        dates = df.index.to_list()

        for n in range(35, len(df), 3):
            truncated = detect_flag_breakout(df.iloc[:n], "TEST")
            cutoff = dates[n - 1]
            already = [d for d in full if d.confirmed_on <= cutoff]
            self.assertEqual(
                [d.to_dict() for d in already],
                [d.to_dict() for d in truncated if d.confirmed_on <= cutoff],
            )
            for d in truncated:
                self.assertLessEqual(d.confirmed_on, cutoff)


if __name__ == "__main__":
    unittest.main()
