"""
Tests for Fix 5: SQLite cooldown timezone consistency.

Verifies that was_alerted_recently() and get_muted_symbols() both use
the 'utc' modifier in their SQLite datetime() calls, producing correct
results regardless of the host machine's local timezone.

All tests use an in-memory SQLite database; they do NOT touch the
production stocksage.db file.
"""

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


# ── In-memory DB helpers ──────────────────────────────────────────────────────

def _create_in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT NOT NULL,
            alert_type   TEXT NOT NULL,
            message      TEXT NOT NULL,
            triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    return conn


def _insert_alert(conn: sqlite3.Connection, symbol: str, triggered_at: datetime) -> None:
    # Use the same format as log_alert: "YYYY-MM-DD HH:MM:SS" in UTC (space separator)
    ts = triggered_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO alerts (symbol, alert_type, message, triggered_at) VALUES (?,?,?,?)",
        (symbol.upper(), "BUY_SIGNAL", "test message", ts),
    )
    conn.commit()


# ── Patched versions of the production functions ─────────────────────────────
# We patch _connect() to return our in-memory connection so that no file
# I/O happens and production data is never touched.

def _was_alerted_recently_inmem(conn: sqlite3.Connection, symbol: str, hours: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM alerts WHERE symbol = ?"
        " AND triggered_at >= datetime('now', 'utc', ? || ' hours')"
        " LIMIT 1",
        (symbol.upper(), f"-{hours}"),
    ).fetchone()
    return row is not None


def _get_muted_symbols_inmem(conn: sqlite3.Connection, hours: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM alerts"
        " WHERE triggered_at >= datetime('now', 'utc', ? || ' hours')",
        (f"-{hours}",),
    ).fetchall()
    return [row["symbol"] for row in rows]


class TestCooldownUtc(unittest.TestCase):

    def setUp(self):
        self.conn = _create_in_memory_db()
        self.now_utc = datetime.now(timezone.utc)

    def tearDown(self):
        self.conn.close()

    # ── was_alerted_recently ──────────────────────────────────────────────────

    def test_alert_within_cooldown_returns_true(self):
        """An alert logged 1 hour ago should be within the 2-hour cooldown."""
        ts = self.now_utc - timedelta(hours=1)
        _insert_alert(self.conn, "NVDA", ts)
        self.assertTrue(_was_alerted_recently_inmem(self.conn, "NVDA", hours=2))

    def test_alert_outside_cooldown_returns_false(self):
        """An alert logged 3 hours ago should be outside the 2-hour cooldown."""
        ts = self.now_utc - timedelta(hours=3)
        _insert_alert(self.conn, "NVDA", ts)
        self.assertFalse(_was_alerted_recently_inmem(self.conn, "NVDA", hours=2))

    def test_alert_at_exact_boundary_is_within_cooldown(self):
        """An alert exactly at the cooldown boundary is still within it."""
        ts = self.now_utc - timedelta(hours=2, seconds=0)
        _insert_alert(self.conn, "AAPL", ts)
        self.assertTrue(_was_alerted_recently_inmem(self.conn, "AAPL", hours=2))

    def test_no_alert_returns_false(self):
        """A symbol with no alerts should not appear as recently alerted."""
        self.assertFalse(_was_alerted_recently_inmem(self.conn, "TSLA", hours=2))

    def test_case_insensitive_symbol_lookup(self):
        """Symbol lookup is case-insensitive (stored upper, queried upper)."""
        ts = self.now_utc - timedelta(minutes=30)
        _insert_alert(self.conn, "msft", ts)
        self.assertTrue(_was_alerted_recently_inmem(self.conn, "msft", hours=2))
        self.assertTrue(_was_alerted_recently_inmem(self.conn, "MSFT", hours=2))

    # ── get_muted_symbols ─────────────────────────────────────────────────────

    def test_muted_symbols_includes_recent_alert(self):
        ts = self.now_utc - timedelta(hours=1)
        _insert_alert(self.conn, "GOOGL", ts)
        muted = _get_muted_symbols_inmem(self.conn, hours=2)
        self.assertIn("GOOGL", muted)

    def test_muted_symbols_excludes_old_alert(self):
        ts = self.now_utc - timedelta(hours=5)
        _insert_alert(self.conn, "AMZN", ts)
        muted = _get_muted_symbols_inmem(self.conn, hours=2)
        self.assertNotIn("AMZN", muted)

    def test_muted_symbols_deduplicates(self):
        """Multiple alerts for the same symbol → appears only once in muted list."""
        ts1 = self.now_utc - timedelta(minutes=30)
        ts2 = self.now_utc - timedelta(minutes=60)
        _insert_alert(self.conn, "META", ts1)
        _insert_alert(self.conn, "META", ts2)
        muted = _get_muted_symbols_inmem(self.conn, hours=2)
        self.assertEqual(muted.count("META"), 1)

    def test_muted_symbols_empty_when_no_recent_alerts(self):
        ts = self.now_utc - timedelta(hours=10)
        _insert_alert(self.conn, "AMD", ts)
        muted = _get_muted_symbols_inmem(self.conn, hours=2)
        self.assertEqual(muted, [])

    # ── UTC consistency: was_alerted_recently vs get_muted_symbols ────────────

    def test_both_functions_agree_on_muted_symbol(self):
        """was_alerted_recently and get_muted_symbols must agree for any symbol."""
        ts = self.now_utc - timedelta(hours=1)
        _insert_alert(self.conn, "CRWD", ts)
        is_recent = _was_alerted_recently_inmem(self.conn, "CRWD", hours=2)
        is_muted  = "CRWD" in _get_muted_symbols_inmem(self.conn, hours=2)
        self.assertEqual(is_recent, is_muted)

    def test_both_functions_agree_on_unmuted_symbol(self):
        """Both functions should agree that an old alert is not recent."""
        ts = self.now_utc - timedelta(hours=6)
        _insert_alert(self.conn, "PANW", ts)
        is_recent = _was_alerted_recently_inmem(self.conn, "PANW", hours=2)
        is_muted  = "PANW" in _get_muted_symbols_inmem(self.conn, hours=2)
        self.assertEqual(is_recent, is_muted)
        self.assertFalse(is_recent)


class TestProductionFunctionsUseUtc(unittest.TestCase):
    """
    Verify that the production database.py functions contain the 'utc' modifier.
    This is a static code-level check that catches regression without needing
    a running database.
    """

    def test_was_alerted_recently_uses_utc_modifier(self):
        import inspect
        from db.database import was_alerted_recently
        source = inspect.getsource(was_alerted_recently)
        self.assertIn("'utc'", source,
            "was_alerted_recently must use datetime('now', 'utc', ...) in its SQL")

    def test_get_muted_symbols_uses_utc_modifier(self):
        import inspect
        from db.database import get_muted_symbols
        source = inspect.getsource(get_muted_symbols)
        self.assertIn("'utc'", source,
            "get_muted_symbols must use datetime('now', 'utc', ...) in its SQL")

    def test_log_alert_uses_sqlite_compatible_format(self):
        import inspect
        from db.database import log_alert
        source = inspect.getsource(log_alert)
        self.assertNotIn("utcnow()", source,
            "log_alert must not use deprecated datetime.utcnow()")
        self.assertIn("timezone.utc", source,
            "log_alert must use datetime.now(timezone.utc)")
        self.assertIn('strftime("%Y-%m-%d %H:%M:%S")', source,
            "log_alert must store timestamps in SQLite-native format (space, not T) "
            "so that datetime('now','utc',...) comparisons work correctly")


if __name__ == "__main__":
    unittest.main()
