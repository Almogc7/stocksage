"""Tests for DB schema migrations v6 (stock_prices) and v7 (scanner_runs /
scanner_results) — the DB-only phase of the scanner engine.

Every test uses a temp SQLite file; none touch the real production database
(db/stocksage.db) or write to it in any way.
"""
import importlib
import sqlite3
import tempfile
import unittest
from pathlib import Path


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


def _make_v5_db(path: str) -> None:
    """Create a DB matching the schema right before v6/v7 (i.e. everything
    up to and including evaluation_run_changes, but no stock_prices/
    scanner_runs/scanner_results)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE watchlist (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol   TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            enabled  INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, action TEXT, symbol TEXT,
            quantity REAL, price REAL, note TEXT, traded_at TIMESTAMP
        );
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY, symbol TEXT,
            alert_type TEXT, message TEXT, triggered_at TIMESTAMP
        );
        CREATE TABLE user_preferences (
            chat_id TEXT PRIMARY KEY, language TEXT DEFAULT 'he'
        );
        CREATE TABLE evaluation_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT, status TEXT DEFAULT 'started', started_at TIMESTAMP
        );
        CREATE TABLE evaluation_run_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, symbol TEXT
        );
        INSERT INTO watchlist (symbol, category) VALUES ('NVDA', 'AI & Semiconductors');
        INSERT INTO watchlist (symbol, category) VALUES ('SPY', 'ETFs');
    """)
    conn.commit()
    conn.close()


class TestSchemaMigrationV6V7(unittest.TestCase):

    def _tmp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        return f.name

    def _fresh_db(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({"AI & Semiconductors": ["NVDA"]})
        return db

    # ── 1. fresh DB creation includes the new tables ─────────────────────

    def test_fresh_db_includes_stock_prices_table(self):
        db = self._fresh_db()
        with db._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(stock_prices)").fetchall()}
        for expected in ("symbol", "timeframe", "date", "open", "high", "low",
                          "close", "volume", "source", "fetched_at"):
            self.assertIn(expected, cols)

    def test_fresh_db_includes_scanner_runs_table(self):
        db = self._fresh_db()
        with db._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(scanner_runs)").fetchall()}
        for expected in ("id", "scanner_name", "started_at", "finished_at",
                          "status", "symbols_scanned", "symbols_passed", "notes"):
            self.assertIn(expected, cols)

    def test_fresh_db_includes_scanner_results_table(self):
        db = self._fresh_db()
        with db._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(scanner_results)").fetchall()}
        for expected in ("id", "run_id", "symbol", "scanner_name", "timeframe",
                          "passed", "score", "reason", "details_json", "scanned_at"):
            self.assertIn(expected, cols)

    def test_fresh_db_includes_expected_indexes(self):
        db = self._fresh_db()
        with db._connect() as conn:
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
        self.assertIn("idx_stock_prices_symbol_timeframe_date", names)
        self.assertIn("idx_scanner_results_run_id", names)
        self.assertIn("idx_scanner_results_symbol_scanner_scanned", names)

    # ── 2. migration from current (v5) schema to v6/v7 ───────────────────

    def test_migration_from_v5_adds_new_tables(self):
        path = self._tmp()
        _make_v5_db(path)
        db = _reload_db(path)
        db.migrate_db()
        with db._connect() as conn:
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        self.assertIn("stock_prices", names)
        self.assertIn("scanner_runs", names)
        self.assertIn("scanner_results", names)

    def test_migration_from_v5_preserves_existing_watchlist_data(self):
        path = self._tmp()
        _make_v5_db(path)
        db = _reload_db(path)
        db.migrate_db()
        with db._connect() as conn:
            rows = conn.execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()
        symbols = {r[0] for r in rows}
        self.assertIn("NVDA", symbols)
        self.assertIn("SPY", symbols)

    # ── 3. idempotency ────────────────────────────────────────────────────

    def test_migrate_db_twice_does_not_raise(self):
        path = self._tmp()
        _make_v5_db(path)
        db = _reload_db(path)
        db.migrate_db()
        db.migrate_db()  # must not raise

    def test_migrate_db_twice_does_not_duplicate_tables(self):
        path = self._tmp()
        _make_v5_db(path)
        db = _reload_db(path)
        db.migrate_db()
        db.migrate_db()
        with db._connect() as conn:
            for table in ("stock_prices", "scanner_runs", "scanner_results"):
                count = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0]
                self.assertEqual(count, 1, f"{table} should exist exactly once")

    def test_migrate_db_twice_preserves_inserted_price_rows(self):
        db = self._fresh_db()
        db.insert_stock_prices([
            {"symbol": "NVDA", "date": "2026-06-15", "close": 120.5},
        ])
        db.migrate_db()  # re-run migration after data exists
        rows = db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 120.5)

    # ── 4. inserting duplicate stock_prices updates safely (no duplicate rows) ──

    def test_insert_stock_prices_basic(self):
        db = self._fresh_db()
        n = db.insert_stock_prices([
            {"symbol": "NVDA", "date": "2026-06-15", "open": 118.0, "high": 121.0,
             "low": 117.5, "close": 120.5, "volume": 5_000_000, "source": "yfinance"},
        ])
        self.assertEqual(n, 1)
        rows = db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 120.5)
        self.assertEqual(rows[0]["source"], "yfinance")

    def test_insert_duplicate_stock_price_updates_in_place(self):
        db = self._fresh_db()
        db.insert_stock_prices([
            {"symbol": "NVDA", "date": "2026-06-15", "close": 120.5, "volume": 1000},
        ])
        db.insert_stock_prices([
            {"symbol": "NVDA", "date": "2026-06-15", "close": 121.9, "volume": 2000},
        ])
        rows = db.get_stock_prices("NVDA")
        self.assertEqual(len(rows), 1, "re-inserting the same (symbol, timeframe, date) must not duplicate")
        self.assertEqual(rows[0]["close"], 121.9)
        self.assertEqual(rows[0]["volume"], 2000)

    def test_insert_stock_prices_different_timeframes_are_distinct(self):
        db = self._fresh_db()
        db.insert_stock_prices([
            {"symbol": "NVDA", "timeframe": "1d", "date": "2026-06-15", "close": 120.5},
            {"symbol": "NVDA", "timeframe": "1h", "date": "2026-06-15", "close": 119.0},
        ])
        daily = db.get_stock_prices("NVDA", timeframe="1d")
        hourly = db.get_stock_prices("NVDA", timeframe="1h")
        self.assertEqual(len(daily), 1)
        self.assertEqual(len(hourly), 1)
        self.assertNotEqual(daily[0]["close"], hourly[0]["close"])

    def test_get_stock_prices_date_range_and_order(self):
        db = self._fresh_db()
        db.insert_stock_prices([
            {"symbol": "NVDA", "date": "2026-06-10", "close": 100.0},
            {"symbol": "NVDA", "date": "2026-06-11", "close": 101.0},
            {"symbol": "NVDA", "date": "2026-06-12", "close": 102.0},
        ])
        rows = db.get_stock_prices("NVDA", start_date="2026-06-11")
        self.assertEqual([r["date"] for r in rows], ["2026-06-11", "2026-06-12"])

    def test_get_stock_prices_limit_keeps_most_recent_ascending(self):
        db = self._fresh_db()
        db.insert_stock_prices([
            {"symbol": "NVDA", "date": "2026-06-10", "close": 100.0},
            {"symbol": "NVDA", "date": "2026-06-11", "close": 101.0},
            {"symbol": "NVDA", "date": "2026-06-12", "close": 102.0},
        ])
        rows = db.get_stock_prices("NVDA", limit=2)
        self.assertEqual([r["date"] for r in rows], ["2026-06-11", "2026-06-12"])

    def test_get_latest_stock_price(self):
        db = self._fresh_db()
        db.insert_stock_prices([
            {"symbol": "NVDA", "date": "2026-06-10", "close": 100.0},
            {"symbol": "NVDA", "date": "2026-06-12", "close": 102.0},
        ])
        latest = db.get_latest_stock_price("NVDA")
        self.assertEqual(latest["date"], "2026-06-12")
        self.assertEqual(latest["close"], 102.0)

    def test_get_latest_stock_price_none_when_missing(self):
        db = self._fresh_db()
        self.assertIsNone(db.get_latest_stock_price("NONEXISTENT"))

    def test_insert_stock_prices_empty_list_is_noop(self):
        db = self._fresh_db()
        self.assertEqual(db.insert_stock_prices([]), 0)

    # ── 5. saving scanner run and scanner results ────────────────────────

    def test_create_and_finish_scanner_run(self):
        db = self._fresh_db()
        run_id = db.create_scanner_run("momentum_scanner")
        run = db.get_scanner_run(run_id)
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["scanner_name"], "momentum_scanner")
        self.assertIsNone(run["finished_at"])

        db.finish_scanner_run(run_id, status="completed", symbols_scanned=50, symbols_passed=5)
        run = db.get_scanner_run(run_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["symbols_scanned"], 50)
        self.assertEqual(run["symbols_passed"], 5)
        self.assertIsNotNone(run["finished_at"])

    def test_get_scanner_run_missing_returns_none(self):
        db = self._fresh_db()
        self.assertIsNone(db.get_scanner_run(99999))

    def test_record_single_scanner_result(self):
        db = self._fresh_db()
        run_id = db.create_scanner_run("momentum_scanner")
        result_id = db.record_scanner_result(
            run_id, "NVDA", "momentum_scanner",
            passed=True, score=82.5, reason="breakout",
            details={"rsi": 61.2},
        )
        self.assertIsInstance(result_id, int)
        results = db.get_scanner_results(run_id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["symbol"], "NVDA")
        self.assertEqual(results[0]["passed"], 1)
        self.assertEqual(results[0]["score"], 82.5)

    def test_record_scanner_results_bulk(self):
        db = self._fresh_db()
        run_id = db.create_scanner_run("momentum_scanner")
        n = db.record_scanner_results(run_id, [
            {"symbol": "NVDA", "scanner_name": "momentum_scanner", "passed": True, "score": 90.0},
            {"symbol": "AAPL", "scanner_name": "momentum_scanner", "passed": False, "score": 40.0,
             "reason": "below EMA150"},
        ])
        self.assertEqual(n, 2)
        results = db.get_scanner_results(run_id)
        self.assertEqual(len(results), 2)
        passed_symbols = {r["symbol"] for r in results if r["passed"] == 1}
        self.assertEqual(passed_symbols, {"NVDA"})

    def test_get_latest_scanner_results_for_symbol(self):
        db = self._fresh_db()
        run1 = db.create_scanner_run("momentum_scanner")
        db.record_scanner_result(run1, "NVDA", "momentum_scanner", passed=True, score=70.0,
                                  scanned_at="2026-06-10 10:00:00")
        run2 = db.create_scanner_run("momentum_scanner")
        db.record_scanner_result(run2, "NVDA", "momentum_scanner", passed=True, score=85.0,
                                  scanned_at="2026-06-12 10:00:00")
        latest = db.get_latest_scanner_results_for_symbol("NVDA")
        self.assertEqual(latest[0]["score"], 85.0)

    def test_scanner_result_details_json_round_trips(self):
        db = self._fresh_db()
        run_id = db.create_scanner_run("momentum_scanner")
        db.record_scanner_result(
            run_id, "NVDA", "momentum_scanner", passed=True,
            details={"triggered_signals": ["price_above_ema150", "volume_spike"]},
        )
        results = db.get_scanner_results(run_id)
        self.assertIn("price_above_ema150", results[0]["details_json"])

    def test_scanner_result_without_run_id_allowed(self):
        """run_id is nullable — a one-off/manual scan result isn't required
        to belong to a tracked scanner_runs row."""
        db = self._fresh_db()
        result_id = db.record_scanner_result(
            None, "NVDA", "ad_hoc_check", passed=True,
        )
        self.assertIsInstance(result_id, int)

    # ── 6. existing watchlist/alerts/evaluation tables still exist ──────

    def test_existing_tables_still_exist_after_migration(self):
        db = self._fresh_db()
        with db._connect() as conn:
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        for expected in ("watchlist", "trades", "alerts", "user_preferences",
                          "symbol_categories", "evaluation_runs",
                          "evaluation_run_changes"):
            self.assertIn(expected, names)

    def test_existing_watchlist_row_untouched_by_new_migrations(self):
        db = self._fresh_db()
        before = db.get_symbol_status("NVDA")
        db.migrate_db()  # re-run; must not touch watchlist rows
        after = db.get_symbol_status("NVDA")
        self.assertEqual(before, after)

    def test_existing_alert_and_evaluation_helpers_still_work(self):
        db = self._fresh_db()
        db.log_alert("NVDA", "BUY_SIGNAL", "test message")
        self.assertTrue(db.was_alerted_recently("NVDA", hours=1))

        run_id = db.create_evaluation_run("manual")
        db.update_evaluation_run_success(run_id)
        run = db.get_evaluation_run(run_id)
        self.assertEqual(run["status"], "success")


if __name__ == "__main__":
    unittest.main()
