import unittest

from app.patterns import config
from app.patterns.stops import stop_level
from app.patterns.types import Detection


def _detection(pattern: str, direction: str, levels: dict) -> Detection:
    return Detection(
        pattern=pattern,
        direction=direction,
        symbol="TEST",
        start_date="2024-01-02",
        end_date="2024-01-10",
        confirmed_on="2024-01-10",
        levels=levels,
    )


class StopLevelTests(unittest.TestCase):
    def test_trend_pullback_long_is_pullback_low_minus_half_atr(self):
        d = _detection("trend_pullback", "long", {"sma_level": 95.0, "pullback_low": 96.0})
        self.assertAlmostEqual(stop_level(d, atr_at_confirm=2.0), 96.0 - config.TREND_PULLBACK_STOP_ATR_MULT * 2.0)

    def test_trend_pullback_short_is_pullback_high_plus_half_atr(self):
        d = _detection("trend_pullback", "short", {"sma_level": 105.0, "pullback_high": 104.0})
        self.assertAlmostEqual(stop_level(d, atr_at_confirm=2.0), 104.0 + config.TREND_PULLBACK_STOP_ATR_MULT * 2.0)

    def test_flag_breakout_long_is_base_low(self):
        d = _detection("flag_breakout", "long", {"base_high": 110.0, "base_low": 100.0, "breakout_price": 111.0})
        self.assertEqual(stop_level(d, atr_at_confirm=1.5), 100.0)

    def test_flag_breakout_short_is_base_high(self):
        d = _detection("flag_breakout", "short", {"base_high": 110.0, "base_low": 100.0, "breakout_price": 99.0})
        self.assertEqual(stop_level(d, atr_at_confirm=1.5), 110.0)

    def test_double_bottom_long_is_the_lower_trough(self):
        d = _detection("double_top_bottom", "long", {"trough_1": 90.0, "trough_2": 88.0, "neckline": 100.0})
        self.assertEqual(stop_level(d, atr_at_confirm=1.0), 88.0)

    def test_double_top_short_is_the_higher_peak_not_the_neckline(self):
        d = _detection("double_top_bottom", "short", {"peak_1": 120.0, "peak_2": 123.0, "neckline": 100.0})
        self.assertEqual(stop_level(d, atr_at_confirm=1.0), 123.0)

    def test_unhandled_pattern_direction_raises(self):
        d = _detection("some_future_pattern", "long", {})
        with self.assertRaises(KeyError):
            stop_level(d, atr_at_confirm=1.0)


if __name__ == "__main__":
    unittest.main()
