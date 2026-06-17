"""Tests for DB schema migration v2 — watchlist states and symbol_categories."""
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


def _make_old_db(path: str) -> None:
    """Create a DB with the original schema (no state columns)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE watchlist (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol   TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        INSERT INTO watchlist (symbol, category) VALUES ('NVDA', 'AI & Semiconductors');
        INSERT INTO watchlist (symbol, category) VALUES ('SPY',  'ETFs');
        INSERT INTO watchlist (symbol, category) VALUES ('^GSPC','מדדים');
        INSERT INTO watchlist (symbol, category) VALUES ('BTC-USD','קריפטו');
        INSERT INTO watchlist (symbol, category) VALUES ('AAPL', 'מגה טק');
    """)
    conn.commit()
    conn.close()


class TestSchemaMigrationV2(unittest.TestCase):

    def _tmp(self):
        f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        f.close()
        return f.name

    def test_migration_preserves_all_data(self):
        path = self._tmp()
        _make_old_db(path)
        db = _reload_db(path)
        db.migrate_db()
        conn = sqlite3.connect(path)
        rows = conn.execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()
        symbols = {r[0] for r in rows}
        conn.close()
        self.assertIn('NVDA', symbols)
        self.assertIn('SPY', symbols)
        self.assertIn('^GSPC', symbols)
        self.assertIn('BTC-USD', symbols)
        self.assertIn('AAPL', symbols)

    def test_migration_is_idempotent(self):
        path = self._tmp()
        _make_old_db(path)
        db = _reload_db(path)
        db.migrate_db()
        db.migrate_db()  # second run must not raise

    def test_symbol_categories_populated_from_watchlist(self):
        path = self._tmp()
        _make_old_db(path)
        db = _reload_db(path)
        db.migrate_db()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT symbol, category FROM symbol_categories").fetchall()
        pairs = {(r['symbol'], r['category']) for r in rows}
        conn.close()
        self.assertIn(('NVDA', 'AI & Semiconductors'), pairs)
        self.assertIn(('SPY', 'ETFs'), pairs)

    def test_etf_assigned_etf_index_context(self):
        path = self._tmp()
        _make_old_db(path)
        db = _reload_db(path)
        db.migrate_db()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT wl_state, security_type FROM watchlist WHERE symbol = 'SPY'"
        ).fetchone()
        conn.close()
        self.assertEqual(row['wl_state'], 'ETF_INDEX_CONTEXT')
        self.assertEqual(row['security_type'], 'etf')

    def test_index_assigned_etf_index_context(self):
        path = self._tmp()
        _make_old_db(path)
        db = _reload_db(path)
        db.migrate_db()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT wl_state, security_type FROM watchlist WHERE symbol = '^GSPC'"
        ).fetchone()
        conn.close()
        self.assertEqual(row['wl_state'], 'ETF_INDEX_CONTEXT')
        self.assertEqual(row['security_type'], 'index')

    def test_stock_assigned_monitor(self):
        path = self._tmp()
        _make_old_db(path)
        db = _reload_db(path)
        db.migrate_db()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT wl_state FROM watchlist WHERE symbol = 'NVDA'"
        ).fetchone()
        conn.close()
        self.assertEqual(row['wl_state'], 'MONITOR')

    def test_crypto_assigned_etf_index_context(self):
        path = self._tmp()
        _make_old_db(path)
        db = _reload_db(path)
        db.migrate_db()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT wl_state, security_type FROM watchlist WHERE symbol = 'BTC-USD'"
        ).fetchone()
        conn.close()
        self.assertEqual(row['wl_state'], 'ETF_INDEX_CONTEXT')
        self.assertEqual(row['security_type'], 'crypto')

    def test_disabled_row_assigned_user_removed(self):
        path = self._tmp()
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE trades (id INTEGER PRIMARY KEY, action TEXT, symbol TEXT,
                quantity REAL, price REAL, note TEXT, traded_at TIMESTAMP);
            CREATE TABLE alerts (id INTEGER PRIMARY KEY, symbol TEXT, alert_type TEXT,
                message TEXT, triggered_at TIMESTAMP);
            CREATE TABLE user_preferences (chat_id TEXT PRIMARY KEY, language TEXT DEFAULT 'he');
            INSERT INTO watchlist (symbol, category, enabled) VALUES ('AAPL', 'מגה טק', 0);
        """)
        conn.commit()
        conn.close()
        db = _reload_db(path)
        db.migrate_db()
        conn2 = sqlite3.connect(path)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT wl_state FROM watchlist WHERE symbol = 'AAPL'").fetchone()
        conn2.close()
        self.assertEqual(row['wl_state'], 'USER_REMOVED')


if __name__ == '__main__':
    unittest.main()
