import unittest
from unittest import mock

import pandas as pd

from app.patterns import universe_prices_ranged


def _ohlcv_df(n: int = 5, base: float = 100.0) -> pd.DataFrame:
    idx = pd.bdate_range(end="2021-06-30", periods=n)
    closes = [base + i for i in range(n)]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


def _multi_symbol_df(symbols: list[str]) -> pd.DataFrame:
    frames = {s: _ohlcv_df() for s in symbols}
    return pd.concat(frames, axis=1)


START = "2016-01-04"
END = "2021-06-30"


class FetchUniverseOhlcvRangedTests(unittest.TestCase):
    def test_splits_symbols_into_chunks_of_the_configured_size(self):
        symbols = [f"S{i}" for i in range(120)]
        calls = []

        def fake_download(chunk, start, end):
            calls.append((list(chunk), start, end))
            return _multi_symbol_df(chunk)

        with mock.patch.object(universe_prices_ranged, "_download", side_effect=fake_download):
            frames, failed = universe_prices_ranged.fetch_universe_ohlcv_ranged(symbols, START, END, chunk_size=50)

        self.assertEqual([len(c) for c, _s, _e in calls], [50, 50, 20])
        self.assertTrue(all(s == START and e == END for _c, s, e in calls))
        self.assertEqual(len(frames), 120)
        self.assertEqual(failed, [])

    def test_a_raising_chunk_contributes_its_symbols_to_failed_without_aborting_the_run(self):
        symbols = ["A", "B", "C", "D"]

        def fake_download(chunk, start, end):
            if "C" in chunk:
                raise RuntimeError("boom")
            return _multi_symbol_df(chunk)

        with mock.patch.object(universe_prices_ranged, "_download", side_effect=fake_download):
            frames, failed = universe_prices_ranged.fetch_universe_ohlcv_ranged(symbols, START, END, chunk_size=2)

        self.assertIn("A", frames)
        self.assertIn("B", frames)
        self.assertEqual(sorted(failed), ["C", "D"])

    def test_single_symbol_chunk_non_multiindex_still_shapes_correctly(self):
        with mock.patch.object(universe_prices_ranged, "_download", return_value=_ohlcv_df()):
            frames, failed = universe_prices_ranged.fetch_universe_ohlcv_ranged(["ONLY"], START, END, chunk_size=50)
        self.assertEqual(failed, [])
        self.assertIn("ONLY", frames)
        self.assertListEqual(list(frames["ONLY"].columns), ["open", "high", "low", "close", "volume"])


class FetchWithFallbackRangedTests(unittest.TestCase):
    def test_fallback_is_never_attempted_for_more_than_the_cap(self):
        symbols = [f"S{i}" for i in range(30)]

        def fake_download(chunk, start, end):
            raise RuntimeError("batch always fails in this test")

        single_calls = []

        def fake_download_single(symbol, start, end):
            single_calls.append((symbol, start, end))
            raise RuntimeError("fallback always fails too")

        with mock.patch.object(universe_prices_ranged, "_download", side_effect=fake_download), mock.patch.object(
            universe_prices_ranged, "_download_single", side_effect=fake_download_single
        ):
            frames, still_failed, skipped_count = universe_prices_ranged.fetch_with_fallback_ranged(
                symbols, START, END, chunk_size=50, fallback_cap=5
            )

        self.assertLessEqual(len(single_calls), 5)
        self.assertEqual(skipped_count, 25)
        self.assertEqual(len(still_failed), 30)

    def test_a_symbol_that_succeeds_on_fallback_ends_up_in_frames(self):
        def fake_download(chunk, start, end):
            raise RuntimeError("batch fails")

        with mock.patch.object(universe_prices_ranged, "_download", side_effect=fake_download), mock.patch.object(
            universe_prices_ranged, "_download_single", return_value=_ohlcv_df()
        ):
            frames, still_failed, skipped_count = universe_prices_ranged.fetch_with_fallback_ranged(
                ["AAPL"], START, END, chunk_size=50, fallback_cap=5
            )

        self.assertIn("AAPL", frames)
        self.assertEqual(still_failed, [])
        self.assertEqual(skipped_count, 0)

    def test_fallback_writes_nothing_anywhere_no_state_db_dependency(self):
        # Unlike universe_prices.py's fallback (which calls
        # app.prices.get_prices(), a caching/DB-writing path), this
        # module's fallback must never touch app.prices at all.
        with mock.patch.object(universe_prices_ranged, "_download", side_effect=RuntimeError("fail")), mock.patch.object(
            universe_prices_ranged, "_download_single", return_value=_ohlcv_df()
        ):
            universe_prices_ranged.fetch_with_fallback_ranged(["AAPL"], START, END, chunk_size=50, fallback_cap=5)
        self.assertFalse(hasattr(universe_prices_ranged, "prices_module"))


if __name__ == "__main__":
    unittest.main()
