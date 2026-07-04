"""Tests for scanners/strong_trend_scanner.py (Phase 7).

All price data is synthetic and seeded into a temp SQLite DB via
db.database.insert_stock_prices() (same pattern as Phases 3/4/6 tests).
No network calls; db/stocksage.db is never touched.

Fixture design note: each "condition X fails" fixture below was verified
against the actual sma()/is_rising() functions (analyzers/cached_indicators.py)
before being fixed into this file, to guarantee it isolates the targeted
condition rather than guessing at synthetic-data behavior.
"""
import importlib
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scanners.strong_trend_scanner import StrongTrendScanner


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


def _tmp_db_path() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


def _make_rows(closes: list[float], *, symbol: str = "NVDA", timeframe: str = "1d") -> list[dict]:
    dates = pd.bdate_range(start="2026-01-01", periods=len(closes))
    rows = []
    for d, c in zip(dates, closes):
        rows.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "date": d.strftime("%Y-%m-%d"),
            "open": c, "high": c, "low": c, "close": c,
            "volume": 1_000_000,
            "source": "yfinance",
        })
    return rows


def _uptrend(n: int = 260, start: float = 100.0, step: float = 0.5) -> list[float]:
    return [start + i * step for i in range(n)]


def _rise_then_decline(
    rise_days: int, decline_days: int, *, rise_step: float = 0.5, decline_step: float = 1.0, start: float = 100.0
) -> list[float]:
    closes = [start + i * rise_step for i in range(rise_days)]
    peak = closes[-1]
    closes += [peak - i * decline_step for i in range(1, decline_days + 1)]
    return closes


class TestStrongTrendScanner(unittest.TestCase):

    def setUp(self):
        path = _tmp_db_path()
        self.db = _reload_db(path)
        self.db.init_db({"AI & Semiconductors": ["NVDA"]})
        self.scanner = StrongTrendScanner()

    def _seed(self, closes: list[float], symbol: str = "NVDA"):
        self.db.insert_stock_prices(_make_rows(closes, symbol=symbol))

    def test_passing_symbol(self):
        self._seed(_uptrend())
        result = self.scanner.scan("NVDA")
        self.assertTrue(result["passed"])
        self.assertEqual(result["score"], 100)
        self.assertTrue(all(result["conditions"].values()))
        self.assertIn("PASS", result["reason"])

    def test_not_enough_data(self):
        self._seed(_uptrend(n=100))
        result = self.scanner.scan("NVDA")
        self.assertFalse(result["passed"])
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["conditions"]["min_candles"])
        for key in (
            "close_above_sma20", "close_above_sma50", "sma20_above_sma50",
            "sma50_above_sma150", "sma150_above_sma200", "sma150_rising", "sma200_rising",
        ):
            self.assertIsNone(result["conditions"][key])
        self.assertIn("insufficient data", result["reason"])
        self.assertIsNone(result["latest_close"])

    def test_close_below_sma20(self):
        closes = _uptrend()
        closes[-1] = 220.0  # between sma50 and sma20 -- isolates this condition
        self._seed(closes)
        result = self.scanner.scan("NVDA")
        self.assertFalse(result["passed"])
        self.assertFalse(result["conditions"]["close_above_sma20"])
        self.assertTrue(result["conditions"]["close_above_sma50"])

    def test_close_below_sma50(self):
        closes = _uptrend()
        closes[-1] = 150.0
        self._seed(closes)
        result = self.scanner.scan("NVDA")
        self.assertFalse(result["passed"])
        self.assertFalse(result["conditions"]["close_above_sma50"])

    def test_sma20_not_above_sma50(self):
        self._seed(_rise_then_decline(230, 30))
        result = self.scanner.scan("NVDA")
        self.assertFalse(result["passed"])
        self.assertFalse(result["conditions"]["sma20_above_sma50"])

    def test_sma50_not_above_sma150(self):
        self._seed(_rise_then_decline(210, 50))
        result = self.scanner.scan("NVDA")
        self.assertFalse(result["passed"])
        self.assertFalse(result["conditions"]["sma50_above_sma150"])
        self.assertTrue(result["conditions"]["sma150_above_sma200"])

    def test_sma150_not_above_sma200(self):
        self._seed(_rise_then_decline(150, 110))
        result = self.scanner.scan("NVDA")
        self.assertFalse(result["passed"])
        self.assertFalse(result["conditions"]["sma150_above_sma200"])

    def test_sma150_not_rising(self):
        self._seed(_rise_then_decline(205, 55))
        result = self.scanner.scan("NVDA")
        self.assertFalse(result["passed"])
        self.assertFalse(result["conditions"]["sma150_rising"])
        self.assertTrue(result["conditions"]["sma150_above_sma200"])

    def test_sma200_not_rising(self):
        self._seed(_rise_then_decline(190, 70))
        result = self.scanner.scan("NVDA")
        self.assertFalse(result["passed"])
        self.assertFalse(result["conditions"]["sma200_rising"])

    def test_structured_output_shape(self):
        self._seed(_uptrend())
        result = self.scanner.scan("NVDA")
        expected_top_level_keys = {
            "symbol", "scanner_name", "timeframe", "passed", "score",
            "reason", "conditions", "indicator_values", "latest_close",
        }
        self.assertEqual(set(result.keys()), expected_top_level_keys)
        self.assertEqual(result["symbol"], "NVDA")
        self.assertEqual(result["scanner_name"], "StrongTrendScanner")
        self.assertEqual(result["timeframe"], "1d")
        self.assertIsInstance(result["passed"], bool)
        self.assertIsInstance(result["score"], int)
        self.assertIsInstance(result["reason"], str)
        self.assertIsInstance(result["conditions"], dict)
        self.assertIsInstance(result["indicator_values"], dict)
        expected_conditions = {
            "min_candles", "close_above_sma20", "close_above_sma50",
            "sma20_above_sma50", "sma50_above_sma150", "sma150_above_sma200",
            "sma150_rising", "sma200_rising",
        }
        self.assertEqual(set(result["conditions"].keys()), expected_conditions)
        expected_indicator_keys = {"sma20", "sma50", "sma150", "sma200", "candles_used"}
        self.assertEqual(set(result["indicator_values"].keys()), expected_indicator_keys)
        self.assertIsInstance(result["latest_close"], float)

    def test_scanner_never_raises_on_unknown_symbol(self):
        result = self.scanner.scan("NOSUCHSYMBOL")
        self.assertFalse(result["passed"])
        self.assertEqual(result["score"], 0)

    def test_symbol_is_normalized_to_uppercase(self):
        self._seed(_uptrend())
        result = self.scanner.scan("nvda")
        self.assertEqual(result["symbol"], "NVDA")

    def test_does_not_write_scanner_results_or_runs(self):
        """This phase deliberately does not persist anything."""
        self._seed(_uptrend())
        self.scanner.scan("NVDA")
        with self.db._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                " AND name IN ('scanner_runs', 'scanner_results')"
            ).fetchone()[0]
        self.assertEqual(count, 2, "tables should exist (Phase 3) but scanner.scan() must not write to them")
        with self.db._connect() as conn:
            runs = conn.execute("SELECT COUNT(*) FROM scanner_runs").fetchone()[0]
            results = conn.execute("SELECT COUNT(*) FROM scanner_results").fetchone()[0]
        self.assertEqual(runs, 0)
        self.assertEqual(results, 0)


if __name__ == "__main__":
    unittest.main()
