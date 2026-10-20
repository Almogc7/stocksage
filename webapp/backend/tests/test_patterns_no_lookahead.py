"""The hard no-look-ahead rule, proven once more at the orchestrator level
(detect_patterns()), combining all three pattern detectors over one
series that contains a trend+pullback, a base+breakout, and a double top
all in sequence. Per-detector proofs also live alongside each detector's
own test file (test_patterns_zigzag.py, test_patterns_trend_pullback.py,
test_patterns_flag_breakout.py, test_patterns_double_top_bottom.py)."""
import unittest

from app.patterns.detect import detect_patterns
from tests._pattern_fixtures import flat, payload_from_closes, ramp


def _combo_series() -> list[float]:
    closes = flat(20, 100.0)
    # trend + pullback leg
    level = 100.0
    for _ in range(8):
        closes += ramp(6, level, level + 10)[1:]
        level = closes[-1]
        closes += ramp(4, level, level - 4)[1:]
        level = closes[-1]
    closes += ramp(5, level, level - 6)[1:]
    level = closes[-1]
    closes += ramp(4, level, level + 8)[1:]
    level = closes[-1]
    # base + breakout leg
    for i in range(10):
        closes += ramp(3, level, level + 4 if i % 2 == 0 else level - 4)[1:]
        level = closes[-1]
    closes += ramp(3, level, level + 15)[1:]
    level = closes[-1]
    # double top leg
    closes += ramp(10, level, level + 25)[1:]
    level = closes[-1]
    closes += ramp(8, level, level - 12)[1:]
    level = closes[-1]
    neckline_target = level
    peak1 = closes[-11]
    closes += ramp(8, level, peak1 * 0.99)[1:]
    level = closes[-1]
    closes += ramp(8, level, neckline_target - 10)[1:]
    return closes


class OrchestratorNoLookaheadTests(unittest.TestCase):
    def test_truncating_the_full_payload_never_changes_an_already_confirmed_detection(self):
        payload = payload_from_closes(_combo_series())
        full = detect_patterns(payload, "TEST")
        self.assertTrue(full, "fixture should produce at least one detection across the three patterns")
        all_dates = [c["time"] for c in payload["candles"]]

        for n in range(60, len(all_dates), 7):
            truncated_payload = {
                **payload,
                "candles": payload["candles"][:n],
                "volume": payload["volume"][:n],
            }
            truncated = detect_patterns(truncated_payload, "TEST")
            cutoff = all_dates[n - 1]

            already = [d for d in full if d["confirmed_on"] <= cutoff]
            still_there = [d for d in truncated if d["confirmed_on"] <= cutoff]
            self.assertEqual(already, still_there)

            for d in truncated:
                self.assertLessEqual(d["confirmed_on"], cutoff)


if __name__ == "__main__":
    unittest.main()
