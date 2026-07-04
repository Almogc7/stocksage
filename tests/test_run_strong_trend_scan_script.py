"""Tests for scripts/run_strong_trend_scan.py (Phase 9A — manual CLI entry
point for StrongTrendScanner). All price data is synthetic and seeded into
a temp SQLite DB via db.database.insert_stock_prices(). No network calls;
db/stocksage.db is never touched — every test passes --db pointing at a
temp file.
"""
import importlib
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import scripts.run_strong_trend_scan as script


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
    return [
        {
            "symbol": symbol, "timeframe": timeframe, "date": d.strftime("%Y-%m-%d"),
            "open": c, "high": c, "low": c, "close": c,
            "volume": 1_000_000, "source": "yfinance",
        }
        for d, c in zip(dates, closes)
    ]


def _uptrend(n: int = 260, start: float = 100.0, step: float = 0.5) -> list[float]:
    return [start + i * step for i in range(n)]


def _run_main(argv: list[str]) -> str:
    buf = io.StringIO()
    with patch.object(sys, "argv", ["run_strong_trend_scan.py"] + argv), redirect_stdout(buf):
        script.main()
    return buf.getvalue()


class TestRunStrongTrendScanScript(unittest.TestCase):

    def setUp(self):
        self.db_path = _tmp_db_path()
        self.db = _reload_db(self.db_path)
        self.db.init_db({"AI & Semiconductors": ["NVDA", "AAPL"]})

    def _seed(self, closes: list[float], symbol: str):
        self.db.insert_stock_prices(_make_rows(closes, symbol=symbol))

    # ── de-duplication helper (pure) ──────────────────────────────────────

    def test_dedupe_preserve_order(self):
        result = script._dedupe_preserve_order(["nvda", "AAPL", "NVDA", "msft"])
        self.assertEqual(result, ["NVDA", "AAPL", "MSFT"])

    # ── persisting run ────────────────────────────────────────────────────

    def test_persisting_run_creates_scanner_run_and_results(self):
        self._seed(_uptrend(), "NVDA")
        self._seed(_uptrend(n=50), "AAPL")  # insufficient data

        with self.db._connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM scanner_runs").fetchone()[0]

        output = _run_main(["NVDA", "AAPL", "--db", self.db_path])

        with self.db._connect() as conn:
            after = conn.execute("SELECT COUNT(*) FROM scanner_runs").fetchone()[0]
        self.assertEqual(after - before, 1)

        self.assertIn("run_id=", output)
        self.assertIn("symbols_scanned: 2", output)
        self.assertIn("symbols_passed:  1", output)
        self.assertIn("NVDA", output)
        self.assertIn("insufficient data", output)

    def test_persisting_run_result_row_count_matches_symbols(self):
        self._seed(_uptrend(), "NVDA")
        self._seed(_uptrend(), "AAPL")
        _run_main(["NVDA", "AAPL", "--db", self.db_path])
        with self.db._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM scanner_results").fetchone()[0]
        self.assertEqual(count, 2)

    # ── dry-run ───────────────────────────────────────────────────────────

    def test_dry_run_writes_nothing(self):
        self._seed(_uptrend(), "NVDA")
        with self.db._connect() as conn:
            runs_before = conn.execute("SELECT COUNT(*) FROM scanner_runs").fetchone()[0]
            results_before = conn.execute("SELECT COUNT(*) FROM scanner_results").fetchone()[0]

        output = _run_main(["NVDA", "--dry-run", "--db", self.db_path])

        with self.db._connect() as conn:
            runs_after = conn.execute("SELECT COUNT(*) FROM scanner_runs").fetchone()[0]
            results_after = conn.execute("SELECT COUNT(*) FROM scanner_results").fetchone()[0]

        self.assertEqual(runs_after, runs_before)
        self.assertEqual(results_after, results_before)
        self.assertIn("DRY-RUN", output)
        self.assertIn("nothing was written", output)

    def test_dry_run_reports_passed_and_failed(self):
        self._seed(_uptrend(), "NVDA")
        self._seed(_uptrend(n=50), "AAPL")
        output = _run_main(["NVDA", "AAPL", "--dry-run", "--db", self.db_path])
        self.assertIn("symbols_passed:  1", output)
        self.assertIn("symbols_failed:  1", output)
        self.assertIn("AAPL", output)
        self.assertIn("insufficient data", output)

    # ── timeframe passthrough ────────────────────────────────────────────

    def test_timeframe_argument_is_honored(self):
        self._seed(_uptrend(), "NVDA")
        self.db.insert_stock_prices(_make_rows(_uptrend(start=50.0), symbol="NVDA", timeframe="1wk"))
        output = _run_main(["NVDA", "--dry-run", "--timeframe", "1wk", "--db", self.db_path])
        self.assertIn("symbols_scanned: 1", output)

    # ── de-duplication end-to-end ─────────────────────────────────────────

    def test_duplicate_symbols_are_deduplicated_end_to_end(self):
        self._seed(_uptrend(), "NVDA")
        output = _run_main(["NVDA", "nvda", "NVDA", "--db", self.db_path])
        self.assertIn("symbols_scanned: 1", output)

    # ── top-n ─────────────────────────────────────────────────────────────

    def test_top_n_limits_displayed_passed_symbols(self):
        for i, sym in enumerate(["NVDA", "AAPL"]):
            self._seed(_uptrend(start=100.0 + i * 10), sym)
        output = _run_main(["NVDA", "AAPL", "--dry-run", "--top-n", "1", "--db", self.db_path])
        self.assertIn("Top 1 passed symbols:", output)


if __name__ == "__main__":
    unittest.main()
