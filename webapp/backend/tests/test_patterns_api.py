import unittest
from unittest import mock

import pandas as pd
from fastapi.testclient import TestClient

from app import prices
from app.main import app
from tests._helpers import TempEnvTestCase


def _small_df(n=5):
    idx = pd.bdate_range(end="2026-01-02", periods=n)
    closes = [10.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 0.5 for c in closes],
            "Low": [c - 0.5 for c in closes],
            "Close": closes,
            "Volume": [1000] * n,
        },
        index=idx,
    )


class PatternsApiTests(TempEnvTestCase):
    def _get(self, path):
        with TestClient(app) as client:
            return client.get(path)

    def test_invalid_symbol_rejected(self):
        resp = self._get("/api/patterns/not valid!")
        self.assertEqual(resp.status_code, 422)

    def test_response_shape_for_a_short_history_is_an_empty_detection_list(self):
        with mock.patch.object(prices, "_download", return_value=_small_df()):
            resp = self._get("/api/patterns/AAPL?range=2y")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "AAPL")
        self.assertEqual(body["range"], "2y")
        self.assertEqual(body["detections"], [])

    def test_no_price_data_returns_404(self):
        with mock.patch.object(prices, "_download", return_value=pd.DataFrame()):
            resp = self._get("/api/patterns/AAPL")
        self.assertEqual(resp.status_code, 404)

    def test_default_range_is_2y(self):
        with mock.patch.object(prices, "_download", return_value=_small_df()) as dl:
            resp = self._get("/api/patterns/AAPL")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["range"], "2y")
        dl.assert_called_once_with("AAPL", "2y")


if __name__ == "__main__":
    unittest.main()
