import unittest

from app.patterns.zigzag import find_pivots
from tests._pattern_fixtures import flat, frame_from_closes, ramp


def _zigzag_closes() -> list[float]:
    # Flat warmup (ATR stabilizes small, no spurious reversals), then two
    # big, clean alternating legs (+30 then -30) — amplitude far larger
    # than ATR * ZIGZAG_ATR_MULT, so pivots are unambiguous.
    return flat(20, 100.0) + ramp(11, 100.0, 130.0)[1:] + ramp(11, 130.0, 100.0)[1:]


class FindPivotsTests(unittest.TestCase):
    def test_detects_alternating_high_and_low_near_the_real_extremes(self):
        df = frame_from_closes(_zigzag_closes())
        pivots = find_pivots(df)

        self.assertGreaterEqual(len(pivots), 2)
        kinds = [p.kind for p in pivots]
        # Alternates strictly - no two consecutive pivots of the same kind.
        for a, b in zip(kinds, kinds[1:]):
            self.assertNotEqual(a, b)

        # The up-leg's peak (~130) and the down-leg's trough (~100) should
        # each be represented by some pivot, within a small tolerance.
        prices_by_kind = {"high": [p.price for p in pivots if p.kind == "high"], "low": [p.price for p in pivots if p.kind == "low"]}
        self.assertTrue(any(abs(p - 130.0) < 1.0 for p in prices_by_kind["high"]))

    def test_confirmed_on_is_never_before_the_pivot_date(self):
        df = frame_from_closes(_zigzag_closes())
        pivots = find_pivots(df)
        self.assertTrue(pivots)
        for p in pivots:
            self.assertGreaterEqual(p.confirmed_on, p.date)

    def test_returns_nothing_shorter_than_the_atr_warmup(self):
        df = frame_from_closes(flat(10, 100.0))  # fewer bars than ATR_PERIOD
        self.assertEqual(find_pivots(df), [])


class NoLookaheadTests(unittest.TestCase):
    def test_truncating_the_series_never_changes_an_already_confirmed_pivot(self):
        closes = _zigzag_closes()
        df_full = frame_from_closes(closes)
        full_pivots = find_pivots(df_full)
        full_dates = df_full.index.to_list()

        for n in range(25, len(df_full), 3):
            truncated = find_pivots(df_full.iloc[:n])
            cutoff = full_dates[n - 1]
            already_confirmed = [p for p in full_pivots if p.confirmed_on <= cutoff]
            # Every pivot the full run had confirmed by this cutoff must
            # appear, byte-identical, in the truncated run.
            self.assertEqual(
                [(p.date, p.price, p.kind, p.confirmed_on) for p in already_confirmed],
                [(p.date, p.price, p.kind, p.confirmed_on) for p in truncated if p.confirmed_on <= cutoff],
            )
            # And the truncated run must not report anything it couldn't
            # have known yet (nothing confirmed after the cutoff).
            for p in truncated:
                self.assertLessEqual(p.confirmed_on, cutoff)


if __name__ == "__main__":
    unittest.main()
