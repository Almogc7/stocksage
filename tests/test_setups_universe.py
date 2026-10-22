"""Tests for setups/universe.py (docs/SETUP_SPEC.md §2).

Uses a bare temp sqlite3 connection with a minimal `watchlist` table --
not db.database's heavier init_db()/migration path -- since
get_setup_universe() takes an injected connection and only needs the four
columns it actually reads.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from setups.reason_codes import (
    EXCLUDED_CATEGORY,
    EXCLUDED_SECURITY_TYPE,
    EXCLUDED_WL_STATE,
)
from setups.universe import get_setup_universe


class TestSetupUniverse(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db_path = f.name
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE watchlist (
                symbol TEXT, category TEXT, security_type TEXT,
                wl_state TEXT, enabled INTEGER
            )
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def _insert(self, symbol, category, security_type, wl_state, enabled=1):
        self.conn.execute(
            "INSERT INTO watchlist (symbol, category, security_type, wl_state, enabled) "
            "VALUES (?, ?, ?, ?, ?)",
            (symbol, category, security_type, wl_state, enabled),
        )
        self.conn.commit()

    def test_excluded_wl_state(self):
        self._insert("AAPL", "Tech", "stock", "TEMPORARILY_INELIGIBLE")
        result = get_setup_universe(self.conn)
        self.assertNotIn("AAPL", result.included)
        self.assertEqual(result.excluded[0]["reason_code"], EXCLUDED_WL_STATE)

    def test_excluded_category_etfs(self):
        self._insert("SPY", "ETFs", "stock", "ACTIVE")
        result = get_setup_universe(self.conn)
        self.assertNotIn("SPY", result.included)
        self.assertEqual(result.excluded[0]["reason_code"], EXCLUDED_CATEGORY)

    def test_excluded_category_indices_hebrew(self):
        self._insert("QQQ", "מדדים", "stock", "ACTIVE")
        result = get_setup_universe(self.conn)
        self.assertNotIn("QQQ", result.included)
        self.assertEqual(result.excluded[0]["reason_code"], EXCLUDED_CATEGORY)

    def test_excluded_security_type(self):
        self._insert("VOO", "Tech", "etf", "ACTIVE")
        result = get_setup_universe(self.conn)
        self.assertNotIn("VOO", result.included)
        self.assertEqual(result.excluded[0]["reason_code"], EXCLUDED_SECURITY_TYPE)

    def test_null_security_type_is_treated_as_stock_and_included(self):
        self._insert("NVDA", "Tech", None, "ACTIVE")
        result = get_setup_universe(self.conn)
        self.assertIn("NVDA", result.included)
        self.assertEqual(result.excluded, [])

    def test_empty_security_type_is_treated_as_stock_and_included(self):
        self._insert("AMD", "Tech", "", "ACTIVE")
        result = get_setup_universe(self.conn)
        self.assertIn("AMD", result.included)
        self.assertEqual(result.excluded, [])

    def test_disabled_row_excluded_entirely_no_reason_row(self):
        self._insert("MSFT", "Tech", "stock", "ACTIVE", enabled=0)
        result = get_setup_universe(self.conn)
        self.assertNotIn("MSFT", result.included)
        self.assertEqual(result.excluded, [])

    def test_plain_active_stock_included(self):
        self._insert("TSLA", "Tech", "stock", "ACTIVE")
        result = get_setup_universe(self.conn)
        self.assertIn("TSLA", result.included)
        self.assertEqual(result.excluded, [])

    def test_monitor_tier_included(self):
        self._insert("IONQ", "Quantum Computing", "stock", "MONITOR")
        result = get_setup_universe(self.conn)
        self.assertIn("IONQ", result.included)

    def test_excluded_category_keys_exist_in_config_watchlist(self):
        """Guards against a typo'd or reversed-direction Hebrew key
        silently making EXCLUDED_CATEGORY never fire."""
        self.assertIn("מדדים", config.WATCHLIST)
        self.assertIn("ETFs", config.WATCHLIST)

    def test_works_without_row_factory_set(self):
        """get_setup_universe() must not depend on the caller having set
        conn.row_factory = sqlite3.Row -- it unpacks rows positionally."""
        self._insert("TSLA", "Tech", "stock", "ACTIVE")
        plain_conn = sqlite3.connect(self.db_path)  # default tuple row factory
        try:
            result = get_setup_universe(plain_conn)
        finally:
            plain_conn.close()
        self.assertIn("TSLA", result.included)


if __name__ == "__main__":
    unittest.main()
