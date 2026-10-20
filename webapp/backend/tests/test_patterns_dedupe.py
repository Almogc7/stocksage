import unittest

from app.patterns import config
from app.patterns.dedupe import dedupe_detections


def _det(symbol, pattern, direction, confirmed_on):
    return {
        "symbol": symbol,
        "pattern": pattern,
        "direction": direction,
        "confirmed_on": confirmed_on,
        "start_date": confirmed_on,
        "end_date": confirmed_on,
        "levels": {},
        "rules": {},
    }


class DedupeDetectionsTests(unittest.TestCase):
    def test_collapses_refires_within_the_cooldown_keeps_ones_outside_it(self):
        # bar-index 3, 8, 35 for the same (symbol, pattern, direction):
        # 8 is within COOLDOWN_BARS (20) of 3 -> dropped; 35 is 32 bars
        # from 3 -> kept.
        bar_index = {"TEST": {f"d{i}": i for i in range(40)}}
        dets = [
            _det("TEST", "flag_breakout", "long", "d3"),
            _det("TEST", "flag_breakout", "long", "d8"),
            _det("TEST", "flag_breakout", "long", "d35"),
        ]
        kept = dedupe_detections(dets, bar_index)
        self.assertEqual([d["confirmed_on"] for d in kept], ["d3", "d35"])

    def test_different_symbol_pattern_or_direction_never_collapse_together(self):
        bar_index = {
            "AAA": {"d3": 3, "d4": 4},
            "BBB": {"d3": 3, "d4": 4},
        }
        dets = [
            _det("AAA", "flag_breakout", "long", "d3"),
            _det("BBB", "flag_breakout", "long", "d4"),  # different symbol
            _det("AAA", "trend_pullback", "long", "d4"),  # different pattern
            _det("AAA", "flag_breakout", "short", "d4"),  # different direction
        ]
        kept = dedupe_detections(dets, bar_index)
        self.assertEqual(len(kept), 4)

    def test_exactly_cooldown_bars_apart_is_still_collapsed(self):
        bar_index = {"TEST": {"d0": 0, "d20": config.COOLDOWN_BARS}}
        dets = [
            _det("TEST", "flag_breakout", "long", "d0"),
            _det("TEST", "flag_breakout", "long", "d20"),
        ]
        kept = dedupe_detections(dets, bar_index)
        self.assertEqual([d["confirmed_on"] for d in kept], ["d0"])


if __name__ == "__main__":
    unittest.main()
