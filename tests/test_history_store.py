"""Tests for data/history_store.py (Phase 4 — historical price storage).

All data.fetcher.get_historical calls are mocked (no network). All DB access
goes through a temp SQLite file; none of these tests touch db/stocksage.db.
"""
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

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


class TestFetchAndStoreHistory(unittest.TestCase):

    def setUp(self):
        path = _tmp_db_path()
        self.db = _reload_db(path)
        self.db.init_db({"AI & Semiconductors": ["NVDA"]})

    def test_fetches_at_least_250_candles_and_stores_them(self):
        df = make_trending_df(n=260)
        with patch("data.history_store.get_historical", return_value=df):
            import data.history_store as hs
            written = hs.fetch_and_store_history("NVDA")
        self.assertEqual(written, 260)
        rows = self.db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 260)

    def test_stores_source_as_yfinance_by_default(self):
        df = make_trending_df(n=260)
        with patch("data.history_store.get_historical", return_value=df):
            import data.history_store as hs
            hs.fetch_and_store_history("NVDA")
        rows = self.db.get_stock_prices("NVDA")
        self.assertTrue(all(r["source"] == "yfinance" for r in rows))

    def test_custom_source_is_stored(self):
        df = make_trending_df(n=260)
        with patch("data.history_store.get_historical", return_value=df):
            import data.history_store as hs
            hs.fetch_and_store_history("NVDA", source="yfinance_v2")
        rows = self.db.get_stock_prices("NVDA")
        self.assertTrue(all(r["source"] == "yfinance_v2" for r in rows))

    def test_stores_ohlcv_values_correctly(self):
        df = make_trending_df(n=260)
        with patch("data.history_store.get_historical", return_value=df):
            import data.history_store as hs
            hs.fetch_and_store_history("NVDA")
        rows = self.db.get_stock_prices("NVDA")
        last_row = rows[-1]
        expected_last = df.iloc[-1]
        self.assertAlmostEqual(last_row["close"], float(expected_last["close"]), places=4)
        self.assertAlmostEqual(last_row["open"], float(expected_last["open"]), places=4)
        self.assertEqual(last_row["volume"], int(expected_last["volume"]))

    def test_running_twice_is_idempotent_no_duplicates(self):
        df = make_trending_df(n=260)
        with patch("data.history_store.get_historical", return_value=df):
            import data.history_store as hs
            hs.fetch_and_store_history("NVDA")
            hs.fetch_and_store_history("NVDA")
        rows = self.db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 260, "second run must upsert, not duplicate")

    def test_running_twice_updates_values_in_place(self):
        df1 = make_trending_df(n=260, seed=1)
        df2 = make_trending_df(n=260, seed=2)  # different closes, same date range
        with patch("data.history_store.get_historical", return_value=df1):
            import data.history_store as hs
            hs.fetch_and_store_history("NVDA")
        with patch("data.history_store.get_historical", return_value=df2):
            hs.fetch_and_store_history("NVDA")
        rows = self.db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 260)
        self.assertAlmostEqual(rows[-1]["close"], float(df2.iloc[-1]["close"]), places=4)

    def test_no_data_returns_zero_and_does_not_raise(self):
        with patch("data.history_store.get_historical", return_value=None):
            import data.history_store as hs
            written = hs.fetch_and_store_history("BADSYM")
        self.assertEqual(written, 0)
        self.assertEqual(self.db.get_stock_prices("BADSYM"), [])

    def test_empty_dataframe_returns_zero(self):
        with patch("data.history_store.get_historical", return_value=pd.DataFrame()):
            import data.history_store as hs
            written = hs.fetch_and_store_history("NVDA")
        self.assertEqual(written, 0)

    def test_rows_with_nan_close_are_skipped(self):
        df = _make_df_with_nan_row(n=260)
        with patch("data.history_store.get_historical", return_value=df):
            import data.history_store as hs
            written = hs.fetch_and_store_history("NVDA")
        self.assertEqual(written, 259)

    def test_timeframe_defaults_to_1d(self):
        df = make_trending_df(n=260)
        with patch("data.history_store.get_historical", return_value=df):
            import data.history_store as hs
            hs.fetch_and_store_history("NVDA")
        rows = self.db.get_stock_prices("NVDA", timeframe="1d")
        self.assertEqual(len(rows), 260)

    def test_symbol_is_normalized_to_uppercase(self):
        df = make_trending_df(n=260)
        with patch("data.history_store.get_historical", return_value=df) as mock_get:
            import data.history_store as hs
            hs.fetch_and_store_history("nvda")
        mock_get.assert_called_once_with("NVDA", period="1y", interval="1d")
        rows = self.db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 260)


class TestGetLatestPrices(unittest.TestCase):

    def setUp(self):
        path = _tmp_db_path()
        self.db = _reload_db(path)
        self.db.init_db({"AI & Semiconductors": ["NVDA"]})

    def test_returns_most_recent_n_ascending(self):
        df = make_trending_df(n=260)
        with patch("data.history_store.get_historical", return_value=df):
            import data.history_store as hs
            hs.fetch_and_store_history("NVDA")
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
        with patch("data.history_store.get_historical", return_value=df):
            import data.history_store as hs
            hs.fetch_and_store_history("NVDA")
            latest = hs.get_latest_prices("NVDA")
        self.assertEqual(len(latest), 250)


if __name__ == "__main__":
    unittest.main()
