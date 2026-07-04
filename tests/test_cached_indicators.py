"""Tests for analyzers/cached_indicators.py (Phase 6 — SMA indicators computed
from stock_prices, not live network calls).

Pure-calculation tests (sma/sma_series/is_rising/_closes_series) use
hand-built pandas Series directly, no DB involved. End-to-end tests seed a
temp SQLite DB via db.database.insert_stock_prices() (same pattern as
Phases 3/4/5 tests) and call compute_cached_indicators() through
data.history_store.get_latest_prices(). None of these tests touch
db/stocksage.db or make any network call.
"""
import importlib
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from analyzers.cached_indicators import (
    _closes_series,
    compute_cached_indicators,
    is_rising,
    sma,
    sma_series,
)


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


def _tmp_db_path() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


def _series(values: list[float | None]) -> pd.Series:
    dates = pd.bdate_range(start="2026-01-01", periods=len(values))
    valid = [(d, v) for d, v in zip(dates, values) if v is not None]
    if not valid:
        return pd.Series(dtype=float)
    idx, vals = zip(*valid)
    return pd.Series(vals, index=pd.DatetimeIndex(idx))


def _make_rows(closes: list[float | None], *, symbol: str = "NVDA", timeframe: str = "1d") -> list[dict]:
    dates = pd.bdate_range(start="2026-01-01", periods=len(closes))
    rows = []
    for d, c in zip(dates, closes):
        rows.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "date": d.strftime("%Y-%m-%d"),
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1_000_000,
            "source": "yfinance",
        })
    return rows


# ── Pure calculation tests (no DB) ────────────────────────────────────────

class TestClosesSeries(unittest.TestCase):

    def test_drops_none_close_rows(self):
        rows = _make_rows([100.0, None, 102.0, None, 104.0])
        closes = _closes_series(rows)
        self.assertEqual(len(closes), 3)
        self.assertListEqual(list(closes.values), [100.0, 102.0, 104.0])

    def test_empty_rows_returns_empty_series(self):
        closes = _closes_series([])
        self.assertEqual(len(closes), 0)

    def test_all_none_returns_empty_series(self):
        rows = _make_rows([None, None, None])
        closes = _closes_series(rows)
        self.assertEqual(len(closes), 0)

    def test_non_numeric_close_is_skipped_safely(self):
        rows = _make_rows([100.0, 101.0])
        rows[0]["close"] = "not-a-number"
        closes = _closes_series(rows)
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes.iloc[0], 101.0)


class TestSma(unittest.TestCase):

    def test_enough_data_computes_average(self):
        closes = _series([float(i) for i in range(1, 21)])  # 1..20
        result = sma(closes, 20)
        self.assertAlmostEqual(result, sum(range(1, 21)) / 20)

    def test_uses_only_most_recent_window(self):
        closes = _series([float(i) for i in range(1, 31)])  # 1..30
        result = sma(closes, 20)
        self.assertAlmostEqual(result, sum(range(11, 31)) / 20)

    def test_not_enough_data_returns_none(self):
        closes = _series([100.0] * 19)
        self.assertIsNone(sma(closes, 20))

    def test_exactly_enough_data_computes(self):
        closes = _series([100.0] * 20)
        self.assertEqual(sma(closes, 20), 100.0)

    def test_empty_series_returns_none(self):
        closes = _series([])
        self.assertIsNone(sma(closes, 20))


class TestIsRising(unittest.TestCase):

    def test_rising_trend_is_true(self):
        closes = _series([float(i) for i in range(1, 41)])  # strictly increasing
        self.assertTrue(is_rising(closes, window=20, lookback=10))

    def test_declining_trend_is_false(self):
        closes = _series([float(i) for i in range(40, 0, -1)])  # strictly decreasing
        self.assertFalse(is_rising(closes, window=20, lookback=10))

    def test_flat_trend_is_false_not_none(self):
        closes = _series([100.0] * 40)
        result = is_rising(closes, window=20, lookback=10)
        self.assertFalse(result)
        self.assertIsNotNone(result)

    def test_insufficient_data_returns_none(self):
        closes = _series([100.0] * 25)  # window(20) + lookback(10) = 30 needed
        self.assertIsNone(is_rising(closes, window=20, lookback=10))

    def test_exactly_enough_data_does_not_return_none(self):
        closes = _series([float(i) for i in range(1, 31)])  # exactly 30
        result = is_rising(closes, window=20, lookback=10)
        self.assertIsNotNone(result)


# ── End-to-end tests (temp DB, seeded stock_prices) ───────────────────────

class TestComputeCachedIndicatorsEndToEnd(unittest.TestCase):

    def setUp(self):
        path = _tmp_db_path()
        self.db = _reload_db(path)
        self.db.init_db({"AI & Semiconductors": ["NVDA"]})

    def _seed(self, closes: list[float | None], symbol: str = "NVDA"):
        self.db.insert_stock_prices(_make_rows(closes, symbol=symbol))

    def test_enough_data_computes_all_smas(self):
        self._seed([100.0 + i * 0.1 for i in range(260)])
        result = compute_cached_indicators("NVDA")
        self.assertEqual(result["symbol"], "NVDA")
        self.assertEqual(result["timeframe"], "1d")
        self.assertEqual(result["candles_used"], 250)  # get_latest_prices default n=250
        for key in ("sma20", "sma50", "sma150", "sma200"):
            self.assertIsNotNone(result[key]["value"])
        self.assertEqual(result["insufficient_data_for"], [])

    def test_not_enough_data_reports_insufficient_for_larger_windows(self):
        self._seed([100.0] * 60)
        result = compute_cached_indicators("NVDA")
        self.assertIsNotNone(result["sma20"]["value"])
        self.assertIsNotNone(result["sma50"]["value"])
        self.assertIsNone(result["sma150"]["value"])
        self.assertIsNone(result["sma200"]["value"])
        self.assertIn("sma150", result["insufficient_data_for"])
        self.assertIn("sma200", result["insufficient_data_for"])

    def test_flat_trend_all_smas_not_rising(self):
        self._seed([100.0] * 260)
        result = compute_cached_indicators("NVDA")
        for key in ("sma20", "sma50", "sma150", "sma200"):
            self.assertFalse(result[key]["rising"])

    def test_rising_trend_all_smas_rising(self):
        self._seed([100.0 + i * 0.5 for i in range(260)])
        result = compute_cached_indicators("NVDA")
        for key in ("sma20", "sma50", "sma150", "sma200"):
            self.assertTrue(result[key]["rising"])

    def test_declining_trend_all_smas_not_rising(self):
        self._seed([300.0 - i * 0.5 for i in range(260)])
        result = compute_cached_indicators("NVDA")
        for key in ("sma20", "sma50", "sma150", "sma200"):
            self.assertFalse(result[key]["rising"])

    # Note: stock_prices.close is NOT NULL by schema (Phase 3), so a row with
    # a missing close can never actually reach the DB via insert_stock_prices
    # — there is no end-to-end path to seed one. None-close handling in
    # _closes_series() is defensive/robustness code, already covered directly
    # against hand-built row dicts in TestClosesSeries above.

    def test_no_data_at_all_returns_all_none_safely(self):
        result = compute_cached_indicators("NOSUCHSYMBOL")
        self.assertEqual(result["candles_used"], 0)
        self.assertIsNone(result["as_of_date"])
        for key in ("sma20", "sma50", "sma150", "sma200"):
            self.assertIsNone(result[key]["value"])
            self.assertIsNone(result[key]["rising"])
        self.assertEqual(result["insufficient_data_for"], ["sma20", "sma50", "sma150", "sma200"])

    def test_as_of_date_matches_latest_stored_row(self):
        self._seed([100.0 + i * 0.1 for i in range(260)])
        result = compute_cached_indicators("NVDA")
        latest = self.db.get_latest_stock_price("NVDA")
        self.assertEqual(result["as_of_date"], latest["date"])

    def test_custom_n_and_rising_lookback_are_respected(self):
        self._seed([100.0 + i * 0.2 for i in range(100)])
        result = compute_cached_indicators("NVDA", n=80, rising_lookback=5)
        self.assertEqual(result["candles_used"], 80)
        self.assertIsNotNone(result["sma50"]["value"])
        self.assertTrue(result["sma20"]["rising"])

    def test_timeframe_isolation(self):
        self._seed([100.0] * 260, symbol="NVDA")
        self.db.insert_stock_prices(
            [{**r, "timeframe": "1wk"} for r in _make_rows([50.0 + i for i in range(260)])]
        )
        daily = compute_cached_indicators("NVDA", timeframe="1d")
        weekly = compute_cached_indicators("NVDA", timeframe="1wk")
        self.assertEqual(daily["sma20"]["value"], 100.0)
        self.assertNotEqual(weekly["sma20"]["value"], 100.0)


if __name__ == "__main__":
    unittest.main()
