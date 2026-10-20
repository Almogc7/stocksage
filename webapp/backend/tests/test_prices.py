import sqlite3
import unittest
from unittest import mock

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from tests._helpers import TempEnvTestCase
from app import prices
from app.main import app


def _df(with_nan=False, dup=False):
    idx = pd.to_datetime(["2026-01-05", "2026-01-02", "2026-01-06"]).tz_localize("America/New_York")
    df = pd.DataFrame(
        {
            "Open": [10.0, 9.0, 11.0],
            "High": [11.0, 10.0, 12.0],
            "Low": [9.5, 8.5, 10.5],
            "Close": [10.5, 9.5, 11.5],
            "Volume": [1000, 900, 1100],
        },
        index=idx,  # deliberately unsorted
    )
    if with_nan:
        df.loc[idx[0], "Close"] = np.nan
    if dup:
        df = pd.concat([df, df.iloc[[0]]])
    return df


class PricesApiTests(TempEnvTestCase):
    def _get(self, path):
        with TestClient(app) as client:
            return client.get(path)

    def test_lightweight_charts_shape_sorted(self):
        with mock.patch.object(prices, "_download", return_value=_df()):
            body = self._get("/api/prices/aapl?range=6mo").json()
        self.assertEqual(body["symbol"], "AAPL")
        self.assertEqual(body["range"], "6mo")
        self.assertEqual([c["time"] for c in body["candles"]], ["2026-01-02", "2026-01-05", "2026-01-06"])
        self.assertEqual(body["candles"][0], {"time": "2026-01-02", "open": 9.0, "high": 10.0, "low": 8.5, "close": 9.5})
        self.assertEqual(body["volume"][0], {"time": "2026-01-02", "value": 900})

    def test_nan_rows_dropped_and_dates_unique(self):
        with mock.patch.object(prices, "_download", return_value=_df(with_nan=True, dup=True)):
            body = self._get("/api/prices/AAPL").json()
        times = [c["time"] for c in body["candles"]]
        self.assertEqual(times, ["2026-01-02", "2026-01-06"])
        self.assertEqual(len(set(times)), len(times))

    def test_cache_hit_within_ttl(self):
        with mock.patch.object(prices, "_download", return_value=_df()) as dl:
            self._get("/api/prices/AAPL")
            self._get("/api/prices/AAPL")
        self.assertEqual(dl.call_count, 1)

    def test_expired_ttl_refetches(self):
        with mock.patch.object(prices, "_download", return_value=_df()) as dl:
            self._get("/api/prices/AAPL")
            conn = sqlite3.connect(self.state_db)
            conn.execute("UPDATE price_cache SET fetched_at = fetched_at - 100000")
            conn.commit()
            conn.close()
            self._get("/api/prices/AAPL")
        self.assertEqual(dl.call_count, 2)

    def test_bad_range_and_symbol_422(self):
        with mock.patch.object(prices, "_download", return_value=_df()) as dl:
            self.assertEqual(self._get("/api/prices/AAPL?range=10y").status_code, 422)
            self.assertEqual(self._get("/api/prices/AA%20PL").status_code, 422)
        dl.assert_not_called()

    def test_empty_is_404(self):
        with mock.patch.object(prices, "_download", return_value=pd.DataFrame()):
            self.assertEqual(self._get("/api/prices/ZZZZ").status_code, 404)

    def test_fetch_error_502_without_cache(self):
        with mock.patch.object(prices, "_download", side_effect=RuntimeError("boom")):
            self.assertEqual(self._get("/api/prices/AAPL").status_code, 502)

    def test_fetch_error_serves_stale_cache(self):
        with mock.patch.object(prices, "_download", return_value=_df()):
            self._get("/api/prices/AAPL")
        conn = sqlite3.connect(self.state_db)
        conn.execute("UPDATE price_cache SET fetched_at = fetched_at - 100000")
        conn.commit()
        conn.close()
        with mock.patch.object(prices, "_download", side_effect=RuntimeError("boom")):
            resp = self._get("/api/prices/AAPL")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["stale"])

    def test_writes_only_to_state_db(self):
        before = self.bot_db.read_bytes()
        with mock.patch.object(prices, "_download", return_value=_df()):
            self._get("/api/prices/AAPL")
        self.assertEqual(before, self.bot_db.read_bytes())
        self.assertTrue(self.state_db.exists())


if __name__ == "__main__":
    unittest.main()
