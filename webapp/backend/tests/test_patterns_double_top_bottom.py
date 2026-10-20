import unittest

from app.patterns import config
from app.patterns.double_top_bottom import detect_double_top_bottom
from tests._pattern_fixtures import flat, frame_from_closes, ramp


def _double_top() -> list[float]:
    closes = flat(20, 100.0)
    closes += ramp(10, 100.0, 130.0)[1:]  # peak 1
    closes += ramp(8, 130.0, 115.0)[1:]  # trough / neckline
    closes += ramp(8, 115.0, 129.0)[1:]  # peak 2, within tolerance of peak 1
    closes += ramp(8, 129.0, 108.0)[1:]  # breaks below neckline
    return closes


def _double_bottom() -> list[float]:
    closes = flat(20, 200.0)
    closes += ramp(10, 200.0, 170.0)[1:]  # trough 1
    closes += ramp(8, 170.0, 185.0)[1:]  # peak / neckline
    closes += ramp(8, 185.0, 171.0)[1:]  # trough 2, within tolerance of trough 1
    closes += ramp(8, 171.0, 192.0)[1:]  # breaks above neckline
    return closes


class DoubleTopTests(unittest.TestCase):
    def setUp(self):
        self.df = frame_from_closes(_double_top())
        self.detections = [d for d in detect_double_top_bottom(self.df, "TEST") if d.direction == "short"]

    def test_detects_a_double_top(self):
        self.assertTrue(self.detections)

    def test_rules_satisfy_the_configured_thresholds(self):
        for d in self.detections:
            self.assertLessEqual(d.rules["peak_diff_atr"], config.PEAK_TOLERANCE_ATR)
            self.assertGreaterEqual(d.rules["trough_depth_atr"], config.MIN_TROUGH_ATR)

    def test_confirmed_on_is_strictly_after_the_first_peak_and_equals_the_break_bar(self):
        for d in self.detections:
            self.assertGreater(d.confirmed_on, d.start_date)
            self.assertEqual(d.confirmed_on, d.end_date)  # the neckline-break bar is both


class DoubleBottomTests(unittest.TestCase):
    def setUp(self):
        self.df = frame_from_closes(_double_bottom())
        self.detections = [d for d in detect_double_top_bottom(self.df, "TEST") if d.direction == "long"]

    def test_detects_a_double_bottom(self):
        self.assertTrue(self.detections)

    def test_rules_satisfy_the_configured_thresholds(self):
        for d in self.detections:
            self.assertLessEqual(d.rules["trough_diff_atr"], config.PEAK_TOLERANCE_ATR)
            self.assertGreaterEqual(d.rules["neckline_height_atr"], config.MIN_TROUGH_ATR)


class DoubleTopVolatilityInvarianceTests(unittest.TestCase):
    def test_the_same_shape_scaled_up_in_price_and_wick_detects_identically(self):
        # Scaling every price AND the wick size by the same factor scales
        # ATR by that same factor, so the ATR-relative peak/trough rules
        # should fire identically on a 3x-higher-priced version of the
        # exact same shape.
        factor = 3.0
        base_closes = _double_top()
        scaled_closes = [c * factor for c in base_closes]

        base_dets = [d for d in detect_double_top_bottom(frame_from_closes(base_closes, wick=0.1), "TEST") if d.direction == "short"]
        scaled_dets = [
            d
            for d in detect_double_top_bottom(frame_from_closes(scaled_closes, wick=0.1 * factor), "TEST")
            if d.direction == "short"
        ]

        self.assertTrue(base_dets)
        self.assertEqual(len(base_dets), len(scaled_dets))
        for b, s in zip(base_dets, scaled_dets):
            self.assertEqual(b.confirmed_on, s.confirmed_on)
            self.assertEqual(b.start_date, s.start_date)
            self.assertAlmostEqual(b.rules["peak_diff_atr"], s.rules["peak_diff_atr"], places=6)
            self.assertAlmostEqual(b.rules["trough_depth_atr"], s.rules["trough_depth_atr"], places=6)
            self.assertAlmostEqual(b.levels["neckline"] * factor, s.levels["neckline"], places=4)


class DoubleTopBottomNoLookaheadTests(unittest.TestCase):
    def test_truncating_the_series_never_changes_an_already_confirmed_detection(self):
        df = frame_from_closes(_double_top())
        full = detect_double_top_bottom(df, "TEST")
        dates = df.index.to_list()

        for n in range(45, len(df), 3):
            truncated = detect_double_top_bottom(df.iloc[:n], "TEST")
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
