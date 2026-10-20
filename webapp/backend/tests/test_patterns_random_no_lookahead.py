"""Randomized no-look-ahead property test: for each of the three pattern
detectors, run SEED_COUNT seeded random-walk OHLCV series (mixed
volatility, drift, and gaps — see tests/_pattern_fixtures.py::
random_ohlcv) and, at several truncation points per series, assert every
detection the full series reports with confirmed_on at or before the
truncation bar is byte-identical to what the truncated series reports.

This is a property test on top of the fixed-fixture no-look-ahead proofs
already in test_patterns_zigzag.py / test_patterns_trend_pullback.py /
test_patterns_flag_breakout.py / test_patterns_double_top_bottom.py /
test_patterns_no_lookahead.py — those pin down specific known shapes;
this one is a much broader, randomized sweep. If this test ever fails,
the fix belongs in the detector, never in this test."""
import unittest

from app.patterns.double_top_bottom import detect_double_top_bottom
from app.patterns.flag_breakout import detect_flag_breakout
from app.patterns.trend_pullback import detect_trend_pullback
from tests._pattern_fixtures import random_ohlcv

SEED_COUNT = 20
N_BARS = 400
TRUNCATION_COUNT = 6
MIN_TRUNCATION_N = 230  # past SMA150 + slope-lookback warm-up


def _truncation_points(n_bars: int) -> list[int]:
    span = n_bars - MIN_TRUNCATION_N
    step = max(1, span // TRUNCATION_COUNT)
    return list(range(MIN_TRUNCATION_N, n_bars, step))[:TRUNCATION_COUNT]


def _assert_no_lookahead(test: unittest.TestCase, detector, df, seed: int) -> int:
    """Runs the generic truncation-invariance check for one detector on
    one series; returns how many (seed, n) combinations were checked."""
    full = detector(df)
    dates = df.index.to_list()
    checked = 0

    for n in _truncation_points(len(df)):
        truncated = detector(df.iloc[:n])
        cutoff = dates[n - 1]
        already = [d.to_dict() for d in full if d.confirmed_on <= cutoff]
        still_there = [d.to_dict() for d in truncated if d.confirmed_on <= cutoff]
        test.assertEqual(
            already,
            still_there,
            msg=f"seed={seed} n={n} cutoff={cutoff}: truncation changed an already-confirmed detection",
        )
        for d in truncated:
            test.assertLessEqual(
                d.confirmed_on,
                cutoff,
                msg=f"seed={seed} n={n}: truncated run reported a detection it couldn't have known yet",
            )
        checked += 1

    return checked


class RandomizedNoLookaheadTests(unittest.TestCase):
    def test_trend_pullback(self):
        checked = 0
        for seed in range(SEED_COUNT):
            df = random_ohlcv(seed=seed, n_bars=N_BARS)
            checked += _assert_no_lookahead(
                self, lambda d: detect_trend_pullback(d, "TEST", sma_period=150), df, seed
            )
        print(f"\n[random no-lookahead] trend_pullback: {SEED_COUNT} seeds x up to {TRUNCATION_COUNT} "
              f"truncation points = {checked} (seed, n) combinations checked, 0 failures.")
        self.assertEqual(checked, SEED_COUNT * TRUNCATION_COUNT)

    def test_flag_breakout(self):
        checked = 0
        for seed in range(SEED_COUNT):
            df = random_ohlcv(seed=seed, n_bars=N_BARS)
            checked += _assert_no_lookahead(self, lambda d: detect_flag_breakout(d, "TEST"), df, seed)
        print(f"\n[random no-lookahead] flag_breakout: {SEED_COUNT} seeds x up to {TRUNCATION_COUNT} "
              f"truncation points = {checked} (seed, n) combinations checked, 0 failures.")
        self.assertEqual(checked, SEED_COUNT * TRUNCATION_COUNT)

    def test_double_top_bottom(self):
        checked = 0
        for seed in range(SEED_COUNT):
            df = random_ohlcv(seed=seed, n_bars=N_BARS)
            checked += _assert_no_lookahead(self, lambda d: detect_double_top_bottom(d, "TEST"), df, seed)
        print(f"\n[random no-lookahead] double_top_bottom: {SEED_COUNT} seeds x up to {TRUNCATION_COUNT} "
              f"truncation points = {checked} (seed, n) combinations checked, 0 failures.")
        self.assertEqual(checked, SEED_COUNT * TRUNCATION_COUNT)


if __name__ == "__main__":
    unittest.main()
