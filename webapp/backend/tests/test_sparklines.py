import sqlite3
import unittest
from unittest import mock

import pandas as pd
from fastapi.testclient import TestClient

from tests._helpers import TempEnvTestCase
from app import sparklines
from app.main import app


def _single_df(closes):
    idx = pd.bdate_range(end="2026-01-02", periods=len(closes))
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
        # Always downloads 1y regardless of the requested range, so there's
        # enough lead-in to classify the SMA150/SMA200 position server-side.
        dl.assert_called_once_with(["MSFT"], "1y")
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

    def test_sma_position_above_when_trending_up_through_200_bars(self):
        closes = [100 + i for i in range(200)]  # strictly rising, last close is the max
        df = _single_df(closes)
        with mock.patch.object(sparklines, "_download", return_value=df):
            body = self._get("/api/sparklines?symbols=AAPL&range=1y").json()
        self.assertEqual(body["data"]["AAPL"]["sma150_pos"], "above")
        self.assertEqual(body["data"]["AAPL"]["sma200_pos"], "above")

    def test_sma_position_below_when_trending_down_through_200_bars(self):
        closes = [300 - i for i in range(200)]  # strictly falling, last close is the min
        df = _single_df(closes)
        with mock.patch.object(sparklines, "_download", return_value=df):
            body = self._get("/api/sparklines?symbols=AAPL&range=1y").json()
        self.assertEqual(body["data"]["AAPL"]["sma150_pos"], "below")
        self.assertEqual(body["data"]["AAPL"]["sma200_pos"], "below")

    def test_sma_position_null_with_insufficient_history(self):
        df = _single_df([100 + i for i in range(80)])  # fewer than 150 bars
        with mock.patch.object(sparklines, "_download", return_value=df):
            body = self._get("/api/sparklines?symbols=AAPL&range=1y").json()
        self.assertIsNone(body["data"]["AAPL"]["sma150_pos"])
        self.assertIsNone(body["data"]["AAPL"]["sma200_pos"])

    def test_sma200_null_with_150_to_199_bars(self):
        df = _single_df([100 + i for i in range(170)])  # enough for 150, not 200
        with mock.patch.object(sparklines, "_download", return_value=df):
            body = self._get("/api/sparklines?symbols=AAPL&range=1y").json()
        self.assertIsNotNone(body["data"]["AAPL"]["sma150_pos"])
        self.assertIsNone(body["data"]["AAPL"]["sma200_pos"])

    def test_closes_trimmed_to_requested_range_despite_1y_download(self):
        df = _single_df([100 + i for i in range(200)])
        with mock.patch.object(sparklines, "_download", return_value=df):
            body = self._get("/api/sparklines?symbols=AAPL&range=1mo").json()
        self.assertEqual(body["range"], "1mo")
        # ~30 calendar days of business-day bars, well under the full 200.
        self.assertLess(len(body["data"]["AAPL"]["closes"]), 40)
        self.assertIsNotNone(body["data"]["AAPL"]["sma150_pos"])

    def test_cached_row_without_sma_fields_normalizes_to_none(self):
        sparklines.state_db.init_schema()
        now = sparklines.time.time()
        legacy_payload = {
            "symbol": "AAPL",
            "range": "3mo",
            "closes": [{"time": "2026-01-02", "close": 10.0}],
        }
        with sparklines.state_db.connect() as conn:
            conn.execute(
                "INSERT INTO sparkline_cache (symbol, range, fetched_at, negative, payload)"
                " VALUES (?, ?, ?, 0, ?)",
                ("AAPL", "3mo", now, sparklines.json.dumps(legacy_payload)),
            )
        with mock.patch.object(sparklines, "_download") as dl:
            body = self._get("/api/sparklines?symbols=AAPL").json()
        dl.assert_not_called()
        self.assertIsNone(body["data"]["AAPL"]["sma150_pos"])
        self.assertIsNone(body["data"]["AAPL"]["sma200_pos"])


if __name__ == "__main__":
    unittest.main()
