"""Tests for data/history_store.py (Phase 4 — historical price storage;
Phase 5 — updated to fetch via MarketDataService instead of yfinance
directly).

No real network calls: a FakeMarketDataService stands in for
MarketDataService in every test. All DB access goes through a temp SQLite
file; none of these tests touch db/stocksage.db.
"""
import importlib
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from data.market_data_service import ProviderFetchResult
from tests.fixtures import make_trending_df


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


def _tmp_db_path() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


def _make_df_with_nan_row(n: int = 260) -> pd.DataFrame:
    df = make_trending_df(n=n)
    df.iloc[-1, df.columns.get_loc("close")] = np.nan
    return df


class FakeMarketDataService:
    """Stands in for MarketDataService — no providers, no network. Records
    every fetch_history() call for assertion."""

    def __init__(self, df: pd.DataFrame | None, provider_name: str | None = "yfinance"):
        self._df = df
        self._provider_name = provider_name if df is not None else None
        self.calls: list[tuple[str, str, str]] = []

    def fetch_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> ProviderFetchResult:
        self.calls.append((symbol, period, interval))
        return ProviderFetchResult(df=self._df, provider_name=self._provider_name)


class TestFetchAndStoreHistory(unittest.TestCase):

    def setUp(self):
        path = _tmp_db_path()
        self.db = _reload_db(path)
        self.db.init_db({"AI & Semiconductors": ["NVDA"]})

    def test_fetches_at_least_250_candles_and_stores_them(self):
        df = make_trending_df(n=260)
        import data.history_store as hs
        written = hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df))
        self.assertEqual(written, 260)
        rows = self.db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 260)

    def test_stores_source_as_yfinance_when_yfinance_provided_data(self):
        df = make_trending_df(n=260)
        import data.history_store as hs
        hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df, provider_name="yfinance"))
        rows = self.db.get_stock_prices("NVDA")
        self.assertTrue(all(r["source"] == "yfinance" for r in rows))

    def test_stores_source_as_stooq_when_stooq_provided_data(self):
        df = make_trending_df(n=260)
        import data.history_store as hs
        hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df, provider_name="stooq"))
        rows = self.db.get_stock_prices("NVDA")
        self.assertTrue(all(r["source"] == "stooq" for r in rows))

    def test_stores_ohlcv_values_correctly(self):
        df = make_trending_df(n=260)
        import data.history_store as hs
        hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df))
        rows = self.db.get_stock_prices("NVDA")
        last_row = rows[-1]
        expected_last = df.iloc[-1]
        self.assertAlmostEqual(last_row["close"], float(expected_last["close"]), places=4)
        self.assertAlmostEqual(last_row["open"], float(expected_last["open"]), places=4)
        self.assertEqual(last_row["volume"], int(expected_last["volume"]))

    def test_running_twice_is_idempotent_no_duplicates(self):
        df = make_trending_df(n=260)
        import data.history_store as hs
        hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df))
        hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df))
        rows = self.db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 260, "second run must upsert, not duplicate")

    def test_running_twice_updates_values_in_place(self):
        df1 = make_trending_df(n=260, seed=1)
        df2 = make_trending_df(n=260, seed=2)  # different closes, same date range
        import data.history_store as hs
        hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df1))
        hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df2))
        rows = self.db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 260)
        self.assertAlmostEqual(rows[-1]["close"], float(df2.iloc[-1]["close"]), places=4)

    def test_all_providers_failing_returns_zero_and_does_not_raise(self):
        import data.history_store as hs
        written = hs.fetch_and_store_history("BADSYM", service=FakeMarketDataService(None))
        self.assertEqual(written, 0)
        self.assertEqual(self.db.get_stock_prices("BADSYM"), [])

    def test_rows_with_nan_close_are_skipped(self):
        df = _make_df_with_nan_row(n=260)
        import data.history_store as hs
        written = hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df))
        self.assertEqual(written, 259)

    def test_timeframe_defaults_to_1d(self):
        df = make_trending_df(n=260)
        import data.history_store as hs
        hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df))
        rows = self.db.get_stock_prices("NVDA", timeframe="1d")
        self.assertEqual(len(rows), 260)

    def test_non_daily_interval_is_stored_under_its_own_timeframe(self):
        df = make_trending_df(n=260)
        import data.history_store as hs
        hs.fetch_and_store_history(
            "NVDA", interval="1wk", service=FakeMarketDataService(df)
        )
        weekly_rows = self.db.get_stock_prices("NVDA", timeframe="1wk")
        daily_rows = self.db.get_stock_prices("NVDA", timeframe="1d")
        self.assertEqual(len(weekly_rows), 260)
        self.assertEqual(len(daily_rows), 0)

    def test_symbol_is_normalized_to_uppercase(self):
        df = make_trending_df(n=260)
        import data.history_store as hs
        fake = FakeMarketDataService(df)
        hs.fetch_and_store_history("nvda", service=fake)
        self.assertEqual(fake.calls, [("NVDA", "1y", "1d")])
        rows = self.db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 260)

    def test_default_service_is_market_data_service_when_none_passed(self):
        """Sanity check that the default `service=None` path really does
        construct a real MarketDataService (Stooq+yfinance) rather than
        silently doing nothing — without making a real network call."""
        import data.history_store as hs
        from unittest.mock import patch

        with patch("data.history_store.MarketDataService") as MockService:
            MockService.return_value.fetch_history.return_value = ProviderFetchResult(
                df=None, provider_name=None
            )
            hs.fetch_and_store_history("NVDA")
        MockService.assert_called_once_with()


class TestGetLatestPrices(unittest.TestCase):

    def setUp(self):
        path = _tmp_db_path()
        self.db = _reload_db(path)
        self.db.init_db({"AI & Semiconductors": ["NVDA"]})

    def test_returns_most_recent_n_ascending(self):
        df = make_trending_df(n=260)
        import data.history_store as hs
        hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df))
        latest = hs.get_latest_prices("NVDA", n=10)
        self.assertEqual(len(latest), 10)
        dates = [r["date"] for r in latest]
        self.assertEqual(dates, sorted(dates))
        expected_last_date = df.index[-1].strftime("%Y-%m-%d")
        self.assertEqual(latest[-1]["date"], expected_last_date)

    def test_returns_empty_list_when_nothing_stored(self):
        import data.history_store as hs
        self.assertEqual(hs.get_latest_prices("NOSUCHSYMBOL"), [])

    def test_default_n_is_250(self):
        df = make_trending_df(n=260)
        import data.history_store as hs
        hs.fetch_and_store_history("NVDA", service=FakeMarketDataService(df))
        latest = hs.get_latest_prices("NVDA")
        self.assertEqual(len(latest), 250)


if __name__ == "__main__":
    unittest.main()
