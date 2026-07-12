"""Tests for DB schema migration v8 (alert_signals / alert_outcomes) and the
structured-alert write path in log_alert().

Every test uses a temp SQLite file; none touch the real production database
(db/stocksage.db) or write to it in any way.
"""
import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


def _make_v7_db(path: str) -> None:
    """Create a DB matching the schema right before v8 (alerts exists with
    legacy rows, but no alert_signals/alert_outcomes)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE watchlist (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol   TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            enabled  INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,
            alert_type TEXT, message TEXT, triggered_at TIMESTAMP
        );
        CREATE TABLE user_preferences (
            chat_id TEXT PRIMARY KEY, language TEXT DEFAULT 'he'
        );
        INSERT INTO alerts (symbol, alert_type, message, triggered_at)
            VALUES ('NVDA', 'BUY_SIGNAL', 'legacy alert', '2026-07-01 15:00:00');
    """)
    conn.commit()
    conn.close()


_ANALYSIS = {
    "score": 78,
    "verdict": "STRONG BUY",
    "current_price": 100.0,   # deliberately different from the live price
    "rsi": 55.3,
    "atr": 2.5,
    "stop_loss": 97.25,
    "take_profit": 108.5,
    "triggered_signals": ["rsi_healthy_range", "volume_spike"],
}


class TestSchemaMigrationV8(unittest.TestCase):

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

    def test_fresh_db_includes_alert_signals_table(self):
        db = self._fresh_db()
        with db._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(alert_signals)").fetchall()}
        for expected in ("alert_id", "score", "verdict", "price_at_alert",
                          "rsi", "atr", "stop_loss", "take_profit",
                          "triggered_signals"):
            self.assertIn(expected, cols)

    def test_fresh_db_includes_alert_outcomes_table(self):
        db = self._fresh_db()
        with db._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(alert_outcomes)").fetchall()}
        for expected in ("alert_id", "close_t1", "close_t3", "close_t5",
                          "close_t10", "max_adverse_excursion",
                          "first_barrier_hit", "r_multiple", "computed_at"):
            self.assertIn(expected, cols)

    # ── 2. upgrading a pre-v8 DB is additive and lossless ────────────────

    def test_migration_preserves_legacy_alert_rows(self):
        path = self._tmp()
        _make_v7_db(path)
        db = _reload_db(path)
        db.migrate_db()
        with db._connect() as conn:
            rows = conn.execute("SELECT * FROM alerts").fetchall()
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message"], "legacy alert")
        self.assertIn("alert_signals", tables)
        self.assertIn("alert_outcomes", tables)

    def test_migration_is_idempotent(self):
        db = self._fresh_db()
        db.migrate_db()
        db.migrate_db()  # second run must not raise or duplicate anything

    # ── 3. write path ────────────────────────────────────────────────────

    def test_log_alert_returns_id_and_writes_signals_row(self):
        db = self._fresh_db()
        alert_id = db.log_alert("NVDA", "BUY_SIGNAL", "msg",
                                analysis=_ANALYSIS, price_at_alert=101.5)
        self.assertIsInstance(alert_id, int)
        with db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM alert_signals WHERE alert_id = ?", (alert_id,)
            ).fetchone()
        self.assertEqual(row["score"], 78)
        self.assertEqual(row["verdict"], "STRONG BUY")
        self.assertEqual(row["rsi"], 55.3)
        self.assertEqual(row["atr"], 2.5)
        self.assertEqual(row["stop_loss"], 97.25)
        self.assertEqual(row["take_profit"], 108.5)
        self.assertEqual(json.loads(row["triggered_signals"]),
                         ["rsi_healthy_range", "volume_spike"])

    def test_log_alert_uses_live_price_not_analysis_current_price(self):
        # analysis["current_price"] is overwritten with the last close by
        # _base_result() (known inconsistency) — the explicit live price wins.
        db = self._fresh_db()
        alert_id = db.log_alert("NVDA", "BUY_SIGNAL", "msg",
                                analysis=_ANALYSIS, price_at_alert=101.5)
        with db._connect() as conn:
            row = conn.execute(
                "SELECT price_at_alert FROM alert_signals WHERE alert_id = ?",
                (alert_id,),
            ).fetchone()
        self.assertEqual(row["price_at_alert"], 101.5)

    def test_log_alert_without_analysis_is_backward_compatible(self):
        # The legacy 3-arg call (used by older tests/callers) writes only the
        # alerts row — no alert_signals satellite.
        db = self._fresh_db()
        alert_id = db.log_alert("AMD", "BUY_SIGNAL", "msg")
        with db._connect() as conn:
            alerts = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchall()
            signals = conn.execute("SELECT * FROM alert_signals").fetchall()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(signals), 0)

    def test_signals_write_does_not_affect_cooldown(self):
        db = self._fresh_db()
        db.log_alert("NVDA", "BUY_SIGNAL", "msg",
                     analysis=_ANALYSIS, price_at_alert=101.5)
        self.assertTrue(db.was_alerted_today("NVDA"))
        self.assertFalse(db.was_alerted_today("AMD"))

    def test_alert_outcomes_stays_empty_at_fire_time(self):
        # Population is a future job — firing an alert must not create a row.
        db = self._fresh_db()
        db.log_alert("NVDA", "BUY_SIGNAL", "msg",
                     analysis=_ANALYSIS, price_at_alert=101.5)
        with db._connect() as conn:
            rows = conn.execute("SELECT * FROM alert_outcomes").fetchall()
        self.assertEqual(len(rows), 0)

    def test_first_barrier_hit_check_constraint(self):
        db = self._fresh_db()
        alert_id = db.log_alert("NVDA", "BUY_SIGNAL", "msg",
                                analysis=_ANALYSIS, price_at_alert=101.5)
        with db._connect() as conn:
            conn.execute(
                "INSERT INTO alert_outcomes (alert_id, first_barrier_hit) VALUES (?, 'take_profit')",
                (alert_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with db._connect() as conn:
                conn.execute(
                    "INSERT INTO alert_outcomes (alert_id, first_barrier_hit) VALUES (?, 'bogus')",
                    (999,),
                )


if __name__ == "__main__":
    unittest.main()
