"""Tests for scanners/scanner_runner.py (Phase 8 — scanner run/result
persistence). All price data is synthetic and seeded into a temp SQLite DB
via db.database.insert_stock_prices(). No network calls; db/stocksage.db is
never touched.
"""
import importlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scanners.base_scanner import BaseScanner
from scanners.scanner_runner import run_scanner
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


def _make_rows(closes: list[float], *, symbol: str, timeframe: str = "1d") -> list[dict]:
    dates = pd.bdate_range(start="2026-01-01", periods=len(closes))
    rows = []
    for d, c in zip(dates, closes):
        rows.append({
            "symbol": symbol, "timeframe": timeframe, "date": d.strftime("%Y-%m-%d"),
            "open": c, "high": c, "low": c, "close": c,
            "volume": 1_000_000, "source": "yfinance",
        })
    return rows


def _uptrend(n: int = 260, start: float = 100.0, step: float = 0.5) -> list[float]:
    return [start + i * step for i in range(n)]


class _RaisingScanner(BaseScanner):
    """Deterministically raises for a chosen symbol, passes for everything
    else -- used to test the runner's per-symbol error handling in
    isolation from any real scanner behavior."""

    name = "RaisingScanner"

    def __init__(self, raise_for: str):
        self._raise_for = raise_for.upper()

    def scan(self, symbol: str, timeframe: str = "1d") -> dict:
        if symbol.upper() == self._raise_for:
            raise ValueError("simulated scanner failure")
        return {
            "symbol": symbol.upper(),
            "scanner_name": self.name,
            "timeframe": timeframe,
            "passed": True,
            "score": 100,
            "reason": "PASS: ok",
            "conditions": {"always_true": True},
            "indicator_values": {"dummy": 1.0},
            "latest_close": 123.45,
        }


class TestScannerRunner(unittest.TestCase):

    def setUp(self):
        path = _tmp_db_path()
        self.db = _reload_db(path)
        self.db.init_db({"AI & Semiconductors": ["NVDA", "AAPL"]})

    def _seed(self, closes: list[float], symbol: str):
        self.db.insert_stock_prices(_make_rows(closes, symbol=symbol))

    # ── successful run with passing/failing symbols ──────────────────────

    def test_run_with_passing_and_failing_symbols(self):
        self._seed(_uptrend(), "NVDA")           # passes StrongTrendScanner
        self._seed(_uptrend(n=50), "AAPL")       # insufficient data -> fails
        summary = run_scanner(StrongTrendScanner(), ["NVDA", "AAPL"])

        self.assertEqual(summary["symbols_scanned"], 2)
        self.assertEqual(summary["symbols_passed"], 1)
        self.assertEqual(summary["symbols_failed"], 0)
        self.assertEqual(summary["status"], "completed")

        run = self.db.get_scanner_run(summary["run_id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["symbols_scanned"], 2)
        self.assertEqual(run["symbols_passed"], 1)
        self.assertIsNotNone(run["finished_at"])

        results = self.db.get_scanner_results(summary["run_id"])
        self.assertEqual(len(results), 2)
        by_symbol = {r["symbol"]: r for r in results}
        self.assertTrue(by_symbol["NVDA"]["passed"])
        self.assertFalse(by_symbol["AAPL"]["passed"])
        self.assertEqual(by_symbol["NVDA"]["scanner_name"], "StrongTrendScanner")
        self.assertEqual(by_symbol["NVDA"]["timeframe"], "1d")

    # ── run with one symbol raising ───────────────────────────────────────

    def test_run_with_one_symbol_raising_does_not_crash_run(self):
        summary = run_scanner(_RaisingScanner(raise_for="AAPL"), ["NVDA", "AAPL", "MSFT"])

        self.assertEqual(summary["symbols_scanned"], 3)
        self.assertEqual(summary["symbols_passed"], 2)
        self.assertEqual(summary["symbols_failed"], 1)
        self.assertEqual(summary["status"], "completed_with_errors")
        self.assertIn("AAPL", summary["errors"])
        self.assertIn("simulated scanner failure", summary["errors"]["AAPL"])

        results = self.db.get_scanner_results(summary["run_id"])
        self.assertEqual(len(results), 3, "the errored symbol must still get a result row")
        by_symbol = {r["symbol"]: r for r in results}
        self.assertFalse(by_symbol["AAPL"]["passed"])
        self.assertIn("scanner error", by_symbol["AAPL"]["reason"])
        self.assertTrue(by_symbol["NVDA"]["passed"])
        self.assertTrue(by_symbol["MSFT"]["passed"])

        run = self.db.get_scanner_run(summary["run_id"])
        self.assertEqual(run["status"], "completed_with_errors")
        self.assertIn("AAPL", run["notes"])

    def test_all_symbols_raising_marks_run_failed(self):
        summary = run_scanner(_RaisingScanner(raise_for="NVDA"), ["NVDA"])
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["symbols_scanned"], 1)
        self.assertEqual(summary["symbols_passed"], 0)
        self.assertEqual(summary["symbols_failed"], 1)
        run = self.db.get_scanner_run(summary["run_id"])
        self.assertEqual(run["status"], "failed")

    # ── details_json validity ────────────────────────────────────────────

    def test_details_json_is_valid_json_with_expected_keys(self):
        self._seed(_uptrend(), "NVDA")
        summary = run_scanner(StrongTrendScanner(), ["NVDA"])
        results = self.db.get_scanner_results(summary["run_id"])
        details = json.loads(results[0]["details_json"])
        self.assertIn("conditions", details)
        self.assertIn("indicator_values", details)
        self.assertIn("latest_close", details)
        self.assertIsInstance(details["conditions"], dict)
        self.assertIn("min_candles", details["conditions"])

    def test_error_details_json_is_valid_json(self):
        summary = run_scanner(_RaisingScanner(raise_for="NVDA"), ["NVDA"])
        results = self.db.get_scanner_results(summary["run_id"])
        details = json.loads(results[0]["details_json"])
        self.assertTrue(details["error"])
        self.assertEqual(details["error_type"], "ValueError")
        self.assertIn("simulated scanner failure", details["error_message"])

    # ── counts ────────────────────────────────────────────────────────────

    def test_symbols_scanned_counts_total_attempted_including_errors(self):
        summary = run_scanner(_RaisingScanner(raise_for="AAPL"), ["NVDA", "AAPL"])
        self.assertEqual(summary["symbols_scanned"], 2)

    def test_symbols_passed_excludes_failing_and_errored(self):
        self._seed(_uptrend(), "NVDA")
        self._seed(_uptrend(n=50), "AAPL")  # fails (insufficient data)
        summary = run_scanner(StrongTrendScanner(), ["NVDA", "AAPL"])
        self.assertEqual(summary["symbols_passed"], 1)

    # ── edge cases ────────────────────────────────────────────────────────

    def test_empty_symbol_list(self):
        summary = run_scanner(StrongTrendScanner(), [])
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["symbols_scanned"], 0)
        self.assertEqual(summary["symbols_passed"], 0)
        self.assertEqual(summary["symbols_failed"], 0)
        self.assertEqual(summary["errors"], {})

        run = self.db.get_scanner_run(summary["run_id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["symbols_scanned"], 0)
        self.assertEqual(run["symbols_passed"], 0)

        results = self.db.get_scanner_results(summary["run_id"])
        self.assertEqual(results, [])

    def test_each_run_gets_a_distinct_run_id(self):
        self._seed(_uptrend(), "NVDA")
        summary1 = run_scanner(StrongTrendScanner(), ["NVDA"])
        summary2 = run_scanner(StrongTrendScanner(), ["NVDA"])
        self.assertNotEqual(summary1["run_id"], summary2["run_id"])
        self.assertEqual(len(self.db.get_scanner_results(summary1["run_id"])), 1)
        self.assertEqual(len(self.db.get_scanner_results(summary2["run_id"])), 1)

    def test_run_creates_exactly_one_scanner_runs_row(self):
        self._seed(_uptrend(), "NVDA")
        with self.db._connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM scanner_runs").fetchone()[0]
        run_scanner(StrongTrendScanner(), ["NVDA"])
        with self.db._connect() as conn:
            after = conn.execute("SELECT COUNT(*) FROM scanner_runs").fetchone()[0]
        self.assertEqual(after - before, 1)


if __name__ == "__main__":
    unittest.main()
