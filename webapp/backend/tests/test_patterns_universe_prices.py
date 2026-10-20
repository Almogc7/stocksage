import unittest
from unittest import mock

import pandas as pd

from app.patterns import universe_prices


def _ohlcv_df(n: int = 5, base: float = 100.0) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-01-02", periods=n)
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


class FetchUniverseOhlcvTests(unittest.TestCase):
    def test_splits_symbols_into_chunks_of_the_configured_size(self):
        symbols = [f"S{i}" for i in range(120)]
        calls = []

        def fake_download(chunk, period):
            calls.append(list(chunk))
            return _multi_symbol_df(chunk)

        with mock.patch.object(universe_prices, "_download", side_effect=fake_download):
            frames, failed = universe_prices.fetch_universe_ohlcv(symbols, "5y", chunk_size=50)

        self.assertEqual([len(c) for c in calls], [50, 50, 20])
        self.assertEqual(len(frames), 120)
        self.assertEqual(failed, [])

    def test_a_raising_chunk_contributes_its_symbols_to_failed_without_aborting_the_run(self):
        symbols = ["A", "B", "C", "D"]

        def fake_download(chunk, period):
            if "C" in chunk:
                raise RuntimeError("boom")
            return _multi_symbol_df(chunk)

        with mock.patch.object(universe_prices, "_download", side_effect=fake_download):
            frames, failed = universe_prices.fetch_universe_ohlcv(symbols, "5y", chunk_size=2)

        self.assertIn("A", frames)
        self.assertIn("B", frames)
        self.assertEqual(sorted(failed), ["C", "D"])

    def test_single_symbol_chunk_non_multiindex_still_shapes_correctly(self):
        with mock.patch.object(universe_prices, "_download", return_value=_ohlcv_df()):
            frames, failed = universe_prices.fetch_universe_ohlcv(["ONLY"], "5y", chunk_size=50)
        self.assertEqual(failed, [])
        self.assertIn("ONLY", frames)
        self.assertListEqual(list(frames["ONLY"].columns), ["open", "high", "low", "close", "volume"])


class FetchWithFallbackTests(unittest.TestCase):
    def test_fallback_is_never_attempted_for_more_than_the_cap(self):
        symbols = [f"S{i}" for i in range(30)]  # all 30 will "fail" the batch

        def fake_download(chunk, period):
            raise RuntimeError("batch always fails in this test")

        get_prices_calls = []

        def fake_get_prices(symbol, period):
            get_prices_calls.append(symbol)
            raise RuntimeError("fallback always fails too")

        with mock.patch.object(universe_prices, "_download", side_effect=fake_download), mock.patch.object(
            universe_prices.prices_module, "get_prices", side_effect=fake_get_prices
        ):
            frames, still_failed, skipped_count = universe_prices.fetch_with_fallback(
                symbols, "5y", chunk_size=50, fallback_cap=5
            )

        self.assertLessEqual(len(get_prices_calls), 5)
        self.assertEqual(skipped_count, 25)
        self.assertEqual(len(still_failed), 30)

    def test_a_symbol_that_succeeds_on_fallback_ends_up_in_frames(self):
        def fake_download(chunk, period):
            raise RuntimeError("batch fails")

        payload = {
            "candles": [{"time": "2026-01-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5}],
            "volume": [{"time": "2026-01-02", "value": 100}],
        }

        with mock.patch.object(universe_prices, "_download", side_effect=fake_download), mock.patch.object(
            universe_prices.prices_module, "get_prices", return_value=payload
        ):
            frames, still_failed, skipped_count = universe_prices.fetch_with_fallback(
                ["AAPL"], "5y", chunk_size=50, fallback_cap=5
            )

        self.assertIn("AAPL", frames)
        self.assertEqual(still_failed, [])
        self.assertEqual(skipped_count, 0)


if __name__ == "__main__":
    unittest.main()
