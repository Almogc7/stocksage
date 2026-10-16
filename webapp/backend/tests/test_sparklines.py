import sqlite3
import unittest
from unittest import mock

import pandas as pd
from fastapi.testclient import TestClient

from tests._helpers import TempEnvTestCase
from app import sparklines
from app.main import app


def _single_df(closes):
    idx = pd.to_datetime([f"2026-01-{i + 2:02d}" for i in range(len(closes))])
    return pd.DataFrame({"Close": closes}, index=idx)


def _multi_df(per_symbol: dict[str, list[float]]) -> pd.DataFrame:
    frames = {sym: _single_df(closes) for sym, closes in per_symbol.items()}
    return pd.concat(frames, axis=1)


class SparklinesApiTests(TempEnvTestCase):
    def _get(self, path):
        with TestClient(app) as client:
            return client.get(path)

    def test_single_batched_call_for_multiple_symbols(self):
        df = _multi_df({"AAPL": [10, 11, 12], "MSFT": [20, 19, 21]})
        with mock.patch.object(sparklines, "_download", return_value=df) as dl:
            body = self._get("/api/sparklines?symbols=aapl,msft&range=3mo").json()
        self.assertEqual(dl.call_count, 1)
        self.assertEqual(body["range"], "3mo")
        self.assertEqual(body["data"]["AAPL"]["closes"][-1]["close"], 12.0)
        self.assertEqual(body["data"]["MSFT"]["closes"][-1]["close"], 21.0)

    def test_cache_hit_skips_refetch(self):
        df = _single_df([10, 11])
        with mock.patch.object(sparklines, "_download", return_value=df) as dl:
            self._get("/api/sparklines?symbols=AAPL")
            self._get("/api/sparklines?symbols=AAPL")
        self.assertEqual(dl.call_count, 1)

    def test_mixed_hit_and_miss_fetches_only_misses_once(self):
        df1 = _single_df([10, 11])
        with mock.patch.object(sparklines, "_download", return_value=df1):
            self._get("/api/sparklines?symbols=AAPL")

        df2 = _single_df([20, 21])
        with mock.patch.object(sparklines, "_download", return_value=df2) as dl:
            body = self._get("/api/sparklines?symbols=AAPL,MSFT").json()
        self.assertEqual(dl.call_count, 1)
        dl.assert_called_once_with(["MSFT"], "3mo")
        self.assertIn("AAPL", body["data"])
        self.assertIn("MSFT", body["data"])

    def test_too_many_symbols_422(self):
        many = ",".join(f"SYM{i}" for i in range(61))
        with mock.patch.object(sparklines, "_download") as dl:
            self.assertEqual(self._get(f"/api/sparklines?symbols={many}").status_code, 422)
        dl.assert_not_called()

    def test_bad_symbol_422(self):
        with mock.patch.object(sparklines, "_download") as dl:
            self.assertEqual(self._get("/api/sparklines?symbols=AA%20PL").status_code, 422)
        dl.assert_not_called()

    def test_one_symbol_failing_does_not_drop_others(self):
        df = _multi_df({"AAPL": [10, 11], "ZZZZ": []})

        def fake_download(symbols, range_):
            return df

        with mock.patch.object(sparklines, "_download", side_effect=fake_download):
            body = self._get("/api/sparklines?symbols=AAPL,ZZZZ").json()
        self.assertIsNotNone(body["data"]["AAPL"])
        self.assertIsNone(body["data"]["ZZZZ"])

    def test_no_data_symbol_is_negative_cached(self):
        empty_df = pd.DataFrame()
        with mock.patch.object(sparklines, "_download", return_value=empty_df) as dl:
            self._get("/api/sparklines?symbols=ZZZZ")
            body = self._get("/api/sparklines?symbols=ZZZZ").json()
        self.assertEqual(dl.call_count, 1)
        self.assertIsNone(body["data"]["ZZZZ"])

    def test_negative_cache_expires_and_retries(self):
        empty_df = pd.DataFrame()
        with mock.patch.object(sparklines, "_download", return_value=empty_df) as dl:
            self._get("/api/sparklines?symbols=ZZZZ")
            conn = sqlite3.connect(self.state_db)
            conn.execute("UPDATE sparkline_cache SET fetched_at = fetched_at - 100000")
            conn.commit()
            conn.close()
            self._get("/api/sparklines?symbols=ZZZZ")
        self.assertEqual(dl.call_count, 2)

    def test_writes_only_to_state_db(self):
        before = self.bot_db.read_bytes()
        with mock.patch.object(sparklines, "_download", return_value=_single_df([10, 11])):
            self._get("/api/sparklines?symbols=AAPL")
        self.assertEqual(before, self.bot_db.read_bytes())
        self.assertTrue(self.state_db.exists())


if __name__ == "__main__":
    unittest.main()
