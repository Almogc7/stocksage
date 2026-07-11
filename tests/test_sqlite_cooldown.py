"""
Tests for the once-per-UTC-day alert cooldown policy (decision D3).

was_alerted_today() / get_muted_symbols() / get_today_alerts() all compare
DATE(triggered_at) against DATE('now'). Timestamps are stored in UTC and
SQLite's 'now' is already UTC, so no timezone modifier may appear in the
SQL: the old hours-based implementation used datetime('now', 'utc', ...),
which double-converts (the 'utc' modifier shifts an already-UTC 'now' by
the machine's local offset) and silently stretched the cooldown window.

Unlike the previous version of this file, these tests call the REAL
db.database functions against a temporary database file — they do not
reimplement the SQL inline. Timestamps are injected directly into the
alerts table so day-boundary cases are deterministic. The production
db/stocksage.db is never touched.
"""
import importlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


class CooldownTestBase(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        self.db.init_db({"AI & Semiconductors": ["NVDA"]})
        self.now_utc = datetime.now(timezone.utc)

    def _insert_alert(self, symbol: str, triggered_at: datetime) -> None:
        """Insert an alert with a controlled UTC timestamp (log_alert always
        stamps 'now', so boundary tests write the row directly)."""
        ts = triggered_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self.db._connect() as conn:
            conn.execute(
                "INSERT INTO alerts (symbol, alert_type, message, triggered_at)"
                " VALUES (?, ?, ?, ?)",
                (symbol.upper(), "BUY_SIGNAL", "test message", ts),
            )

    def _today_early(self) -> datetime:
        """Today (UTC) at 00:00:30 — same UTC day as now, but potentially
        many hours ago. Under the old 2h policy this would NOT have muted;
        under once-per-day it must."""
        return self.now_utc.replace(hour=0, minute=0, second=30, microsecond=0)

    def _yesterday_late(self) -> datetime:
        """Yesterday (UTC) at 23:59:59 — one second before today's boundary."""
        return self.now_utc.replace(
            hour=23, minute=59, second=59, microsecond=0
        ) - timedelta(days=1)


class TestWasAlertedToday(CooldownTestBase):

    def test_alert_now_blocks(self):
        self._insert_alert("NVDA", self.now_utc)
        self.assertTrue(self.db.was_alerted_today("NVDA"))

    def test_alert_early_today_blocks_regardless_of_hours_elapsed(self):
        """Once per day means the whole UTC day — an alert at 00:00:30 UTC
        still blocks at 20:00 UTC (the old 2h window would have re-fired)."""
        self._insert_alert("NVDA", self._today_early())
        self.assertTrue(self.db.was_alerted_today("NVDA"))

    def test_alert_yesterday_does_not_block(self):
        """23:59:59 yesterday is a different UTC day — must not block today."""
        self._insert_alert("NVDA", self._yesterday_late())
        self.assertFalse(self.db.was_alerted_today("NVDA"))

    def test_no_alert_does_not_block(self):
        self.assertFalse(self.db.was_alerted_today("TSLA"))

    def test_case_insensitive_symbol_lookup(self):
        self._insert_alert("msft", self.now_utc)
        self.assertTrue(self.db.was_alerted_today("msft"))
        self.assertTrue(self.db.was_alerted_today("MSFT"))

    def test_log_alert_immediately_blocks(self):
        """The real write path (log_alert) must satisfy the read path."""
        self.db.log_alert("AMD", "BUY_SIGNAL", "msg")
        self.assertTrue(self.db.was_alerted_today("AMD"))


class TestGetMutedSymbols(CooldownTestBase):

    def test_includes_todays_alerts_only(self):
        self._insert_alert("GOOGL", self._today_early())
        self._insert_alert("AMZN", self._yesterday_late())
        muted = self.db.get_muted_symbols()
        self.assertIn("GOOGL", muted)
        self.assertNotIn("AMZN", muted)

    def test_deduplicates_multiple_alerts(self):
        self._insert_alert("META", self._today_early())
        self._insert_alert("META", self.now_utc)
        self.assertEqual(self.db.get_muted_symbols().count("META"), 1)

    def test_empty_when_no_alerts_today(self):
        self._insert_alert("AMD", self._yesterday_late())
        self.assertEqual(self.db.get_muted_symbols(), [])

    def test_agrees_with_was_alerted_today(self):
        """The /status muted list and the alert-loop gate must never disagree."""
        self._insert_alert("CRWD", self.now_utc)
        self._insert_alert("PANW", self._yesterday_late())
        for symbol in ("CRWD", "PANW"):
            self.assertEqual(
                self.db.was_alerted_today(symbol),
                symbol in self.db.get_muted_symbols(),
            )

    def test_agrees_with_get_today_alerts(self):
        """'Alerts today' and 'muted today' use the same UTC-day boundary."""
        self._insert_alert("CRWD", self._today_early())
        self._insert_alert("PANW", self._yesterday_late())
        today_symbols = {a["symbol"] for a in self.db.get_today_alerts()}
        self.assertEqual(today_symbols, set(self.db.get_muted_symbols()))


class TestNoUtcDoubleConversion(unittest.TestCase):
    """Static regression guards for the datetime('now','utc') bug: SQLite's
    'now' is already UTC, so any 'utc' modifier double-converts by the
    machine's local offset."""

    def test_cooldown_sql_has_no_utc_modifier(self):
        import inspect
        import db.database as dbmod
        for fn in (dbmod.was_alerted_today, dbmod.get_muted_symbols,
                   dbmod.get_today_alerts):
            source = inspect.getsource(fn)
            self.assertNotIn(
                "'utc'", source,
                f"{fn.__name__} must not apply the 'utc' modifier to an "
                "already-UTC 'now' (double conversion)",
            )
            self.assertIn(
                "DATE(triggered_at) = DATE('now')", source,
                f"{fn.__name__} must compare UTC calendar days",
            )

    def test_log_alert_stores_sqlite_native_utc(self):
        import inspect
        from db.database import log_alert
        source = inspect.getsource(log_alert)
        self.assertNotIn("utcnow()", source,
            "log_alert must not use deprecated datetime.utcnow()")
        self.assertIn("timezone.utc", source,
            "log_alert must use datetime.now(timezone.utc)")
        self.assertIn('strftime("%Y-%m-%d %H:%M:%S")', source,
            "log_alert must store timestamps in SQLite-native format "
            "(space, not T) so DATE()/string comparisons work")


if __name__ == "__main__":
    unittest.main()
