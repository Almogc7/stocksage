import unittest

from app.patterns.trend_pullback import detect_trend_pullback
from tests._pattern_fixtures import flat, frame_from_closes, ramp

SMA_PERIOD = 20  # small on purpose, to keep fixtures short; production uses 150/200


def _uptrend_with_pullback() -> list[float]:
    closes = flat(10, 100.0)
    level = 100.0
    for _ in range(8):
        closes += ramp(6, level, level + 10)[1:]
        level = closes[-1]
        closes += ramp(4, level, level - 4)[1:]
        level = closes[-1]
    closes += ramp(5, level, 144.5)[1:]  # dip down near the (rising) SMA
    level = closes[-1]
    closes += ramp(4, level, level + 8)[1:]  # bounce away
    return closes


def _downtrend_with_pullback() -> list[float]:
    closes = flat(10, 200.0)
    level = 200.0
    for _ in range(8):
        closes += ramp(6, level, level - 10)[1:]
        level = closes[-1]
        closes += ramp(4, level, level + 4)[1:]
        level = closes[-1]
    closes += ramp(5, level, level + 6)[1:]  # rally up near the (falling) SMA
    level = closes[-1]
    closes += ramp(4, level, level - 8)[1:]  # drop away
    return closes


class TrendPullbackLongTests(unittest.TestCase):
    def setUp(self):
        self.df = frame_from_closes(_uptrend_with_pullback())
        self.detections = detect_trend_pullback(self.df, "TEST", sma_period=SMA_PERIOD)

    def test_detects_at_least_one_long_pullback(self):
        longs = [d for d in self.detections if d.direction == "long"]
        self.assertTrue(longs)

    def test_rules_reflect_the_actual_trend_and_slope(self):
        longs = [d for d in self.detections if d.direction == "long"]
        for d in longs:
            self.assertTrue(d.rules["higher_high"])
            self.assertTrue(d.rules["higher_low"])
            self.assertGreater(d.rules["sma_slope_per_bar"], 0)

    def test_levels_place_the_pullback_low_near_the_reported_sma(self):
        longs = [d for d in self.detections if d.direction == "long"]
        for d in longs:
            self.assertLessEqual(
                d.levels["pullback_low"] - d.levels["sma_level"],
                1.0 * (d.levels["sma_level"] * 0.05),  # generous bound, not hand-tuned to an exact ATR
            )

    def test_no_short_detections_in_a_clean_uptrend(self):
        shorts = [d for d in self.detections if d.direction == "short"]
        self.assertEqual(shorts, [])


class TrendPullbackShortTests(unittest.TestCase):
    def setUp(self):
        self.df = frame_from_closes(_downtrend_with_pullback())
        self.detections = detect_trend_pullback(self.df, "TEST", sma_period=SMA_PERIOD)

    def test_detects_at_least_one_short_pullback(self):
        shorts = [d for d in self.detections if d.direction == "short"]
        self.assertTrue(shorts)

    def test_rules_reflect_the_actual_downtrend_and_slope(self):
        shorts = [d for d in self.detections if d.direction == "short"]
        for d in shorts:
            self.assertTrue(d.rules["lower_high"])
            self.assertTrue(d.rules["lower_low"])
            self.assertLess(d.rules["sma_slope_per_bar"], 0)

    def test_no_long_detections_in_a_clean_downtrend(self):
        longs = [d for d in self.detections if d.direction == "long"]
        self.assertEqual(longs, [])


class TrendPullbackNoLookaheadTests(unittest.TestCase):
    def test_truncating_the_series_never_changes_an_already_confirmed_detection(self):
        df = frame_from_closes(_uptrend_with_pullback())
        full = detect_trend_pullback(df, "TEST", sma_period=SMA_PERIOD)
        dates = df.index.to_list()

        for n in range(60, len(df), 5):
            truncated = detect_trend_pullback(df.iloc[:n], "TEST", sma_period=SMA_PERIOD)
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
