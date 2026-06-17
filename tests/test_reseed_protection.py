"""
Tests for Priority 1: watchlist reseed protection.

Verifies that:
  1. Empty database receives the initial seed.
  2. Restart does not duplicate the seed.
  3. /remove persists after restart (simulated by calling populate_from_config again).
  4. /add persists after restart.
  5. A removed default symbol does not return after restart.
  6. A manually added symbol is not removed by seed synchronization.
  7. Existing category data is preserved after re-seeding.
  8. Duplicate symbols are not created.
  9. Database migration preserves current data.
 10. A future configuration change does not silently re-add a user-removed symbol.
 11. Explicitly adding a previously removed symbol re-enables it.
 12. Tests do not access the real local database.

All tests use a temporary file-based SQLite database so that the real
db/stocksage.db is never touched.
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_temp_db():
    """Return (fd, path_string) for a fresh temporary SQLite file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    return fd, path


def _row_count(path: str, table: str = "watchlist") -> int:
    conn = sqlite3.connect(path)
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return count


def _get_symbol_row(path: str, symbol: str) -> dict | None:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM watchlist WHERE symbol = ?", (symbol.upper(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _get_enabled_symbols(path: str) -> set[str]:
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT symbol FROM watchlist WHERE enabled = 1"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


# ── Test class ────────────────────────────────────────────────────────────────

class TestReseedException(unittest.TestCase):
    """
    All tests patch db.database.DB_PATH to a fresh temporary file.
    The real database at db/stocksage.db is never opened.
    """

    DEFAULT_WATCHLIST = {
        "Tech": ["NVDA", "AAPL", "MSFT"],
        "Energy": ["XOM", "CVX"],
    }

    def setUp(self):
        self.fd, self.db_path = _make_temp_db()
        self.path_patcher = patch("db.database.DB_PATH", Path(self.db_path))
        self.path_patcher.start()

    def tearDown(self):
        self.path_patcher.stop()
        os.close(self.fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _init(self, watchlist=None):
        """Simulate application startup: init_db with optional seed."""
        from db.database import init_db
        init_db(watchlist)

    def _restart(self, watchlist=None):
        """Simulate application restart: call init_db again (as run_bot does)."""
        from db.database import init_db
        init_db(watchlist)

    # ── Test 12 (meta): no real DB ─────────────────────────────────────────

    def test_12_tests_use_temp_db_not_real_db(self):
        """Patched DB_PATH must not point to the real database file."""
        import db.database as db_module
        real_db = Path(__file__).parent.parent / "db" / "stocksage.db"
        self.assertNotEqual(
            db_module.DB_PATH.resolve(),
            real_db.resolve(),
            "DB_PATH must not point to the real database during tests",
        )

    # ── Test 1: empty DB receives initial seed ──────────────────────────────

    def test_01_empty_database_receives_initial_seed(self):
        """On a fresh database, all default symbols are inserted."""
        self._init(self.DEFAULT_WATCHLIST)
        enabled = _get_enabled_symbols(self.db_path)
        for symbol in ["NVDA", "AAPL", "MSFT", "XOM", "CVX"]:
            self.assertIn(symbol, enabled, f"{symbol} should be in seed")

    # ── Test 2: restart does not duplicate ─────────────────────────────────

    def test_02_restart_does_not_duplicate_seed(self):
        """Calling init_db twice with the same watchlist must not create duplicate rows."""
        self._init(self.DEFAULT_WATCHLIST)
        count_after_first = _row_count(self.db_path)

        self._restart(self.DEFAULT_WATCHLIST)
        count_after_second = _row_count(self.db_path)

        self.assertEqual(
            count_after_first,
            count_after_second,
            "Row count must not increase on second startup with same watchlist",
        )

    # ── Test 3: /remove persists after restart ──────────────────────────────

    def test_03_remove_persists_after_restart(self):
        """A symbol removed via /remove must still be absent after restart."""
        from db.database import remove_from_watchlist, get_watchlist

        self._init(self.DEFAULT_WATCHLIST)
        remove_from_watchlist("NVDA")

        # Simulate restart with same config
        self._restart(self.DEFAULT_WATCHLIST)

        wl = get_watchlist()
        all_active = [s for symbols in wl.values() for s in symbols]
        self.assertNotIn("NVDA", all_active, "NVDA must remain removed after restart")

    # ── Test 4: /add persists after restart ─────────────────────────────────

    def test_04_add_persists_after_restart(self):
        """A symbol added via /add must survive application restart."""
        from db.database import add_to_watchlist, get_watchlist

        self._init(self.DEFAULT_WATCHLIST)
        add_to_watchlist("CRWD", "Cyber")

        # Simulate restart — CRWD is not in default watchlist
        self._restart(self.DEFAULT_WATCHLIST)

        wl = get_watchlist()
        all_active = [s for symbols in wl.values() for s in symbols]
        self.assertIn("CRWD", all_active, "CRWD must survive restart")

    # ── Test 5: removed default symbol does not return ──────────────────────

    def test_05_removed_default_symbol_does_not_return_after_restart(self):
        """
        The core reseed bug: a symbol that is in the default config
        must not be re-added after being removed, even across restarts.
        """
        from db.database import remove_from_watchlist, get_watchlist

        self._init(self.DEFAULT_WATCHLIST)

        # Remove a symbol that is in the default config
        remove_from_watchlist("AAPL")

        # Multiple restarts — AAPL is still in DEFAULT_WATCHLIST
        for _ in range(3):
            self._restart(self.DEFAULT_WATCHLIST)

        wl = get_watchlist()
        all_active = [s for symbols in wl.values() for s in symbols]
        self.assertNotIn(
            "AAPL",
            all_active,
            "AAPL must not be re-added by seed even after 3 restarts",
        )

    # ── Test 6: manually added symbol not removed by seed ───────────────────

    def test_06_manually_added_symbol_not_removed_by_seed(self):
        """
        A symbol added via /add that is NOT in the default config must
        not be deleted by a seed synchronization.
        """
        from db.database import add_to_watchlist, get_watchlist

        self._init(self.DEFAULT_WATCHLIST)
        add_to_watchlist("PANW", "Cyber")

        # Restart with a narrower config that does not contain PANW
        narrower_config = {"Tech": ["NVDA"]}
        self._restart(narrower_config)

        wl = get_watchlist()
        all_active = [s for symbols in wl.values() for s in symbols]
        self.assertIn("PANW", all_active, "PANW must not be removed by seed sync")

    # ── Test 7: existing category data preserved ─────────────────────────────

    def test_07_existing_category_preserved_after_reseed(self):
        """
        If a symbol already exists in the DB with a user-chosen category,
        a seed operation that places it in a different category must not
        change the existing category.
        """
        from db.database import add_to_watchlist, get_watchlist

        self._init({})  # fresh DB, no seed
        # User adds NVDA to "My Picks"
        add_to_watchlist("NVDA", "My Picks")

        # Now seed runs with NVDA in "Tech"
        self._restart({"Tech": ["NVDA"]})

        wl = get_watchlist()
        # NVDA should still be in "My Picks", not "Tech"
        row = _get_symbol_row(self.db_path, "NVDA")
        self.assertIsNotNone(row)
        self.assertEqual(
            row["category"],
            "My Picks",
            "Seed must not overwrite a user-assigned category",
        )

    # ── Test 8: duplicate symbols not created ────────────────────────────────

    def test_08_duplicate_symbols_not_created(self):
        """
        Seeding a symbol that appears in two categories must not create
        two rows. The second entry is ignored.
        """
        duplicate_config = {
            "Tech": ["NVDA", "AAPL"],
            "Also Tech": ["NVDA"],  # duplicate
        }
        self._init(duplicate_config)

        conn = sqlite3.connect(self.db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM watchlist WHERE symbol = 'NVDA'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1, "NVDA must appear exactly once in the watchlist")

    # ── Test 9: migration preserves existing data ─────────────────────────────

    def test_09_migration_preserves_existing_data(self):
        """
        Simulates upgrading from the old schema (no enabled/removed_at columns)
        to the new schema. Existing rows must retain their data and become
        enabled=1 (the migration default).
        """
        # Manually create old-style schema without the new columns
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE watchlist (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol   TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("INSERT INTO watchlist (symbol, category) VALUES ('NVDA', 'Tech')")
        conn.execute("INSERT INTO watchlist (symbol, category) VALUES ('AAPL', 'Tech')")
        conn.commit()
        conn.close()

        # Running init_db should migrate without error
        from db.database import init_db, get_watchlist
        init_db()  # no seed

        # Old data must still be present and enabled
        wl = get_watchlist()
        all_active = [s for symbols in wl.values() for s in symbols]
        self.assertIn("NVDA", all_active, "NVDA must survive migration")
        self.assertIn("AAPL", all_active, "AAPL must survive migration")

    # ── Test 10: future config change does not re-add removed symbol ─────────

    def test_10_future_config_addition_does_not_restore_removed_symbol(self):
        """
        If the user removes NVDA, and later a new config version also has
        NVDA in a new category, the restart must NOT re-enable NVDA.
        """
        from db.database import remove_from_watchlist, get_watchlist

        self._init(self.DEFAULT_WATCHLIST)
        remove_from_watchlist("NVDA")

        # Future config: new category for NVDA
        future_config = dict(self.DEFAULT_WATCHLIST)
        future_config["AI Leaders"] = ["NVDA", "AMD"]  # NVDA appears in new category
        self._restart(future_config)

        wl = get_watchlist()
        all_active = [s for symbols in wl.values() for s in symbols]
        self.assertNotIn(
            "NVDA",
            all_active,
            "NVDA must not be re-added even if a new config category includes it",
        )

    # ── Test 11: re-adding a removed symbol re-enables it ───────────────────

    def test_11_readding_removed_symbol_reenables_it(self):
        """
        If a user removes NVDA and then explicitly uses /add NVDA again,
        NVDA must appear in the active watchlist with the new category.
        """
        from db.database import add_to_watchlist, remove_from_watchlist, get_watchlist

        self._init(self.DEFAULT_WATCHLIST)
        remove_from_watchlist("NVDA")

        # Verify removed
        wl_after_remove = get_watchlist()
        all_after_remove = [s for syms in wl_after_remove.values() for s in syms]
        self.assertNotIn("NVDA", all_after_remove)

        # User explicitly re-adds NVDA to a new category
        add_to_watchlist("NVDA", "AI Stars")

        wl_after_readd = get_watchlist()
        all_after_readd = [s for syms in wl_after_readd.values() for s in syms]
        self.assertIn("NVDA", all_after_readd, "NVDA must be active after explicit /add")

        # Category must be the new one, not the original seed category
        row = _get_symbol_row(self.db_path, "NVDA")
        self.assertEqual(row["category"], "AI Stars")
        self.assertEqual(row["enabled"], 1)
        self.assertIsNone(row["removed_at"])

    # ── Bonus: soft-delete sets enabled=0 and removed_at ────────────────────

    def test_soft_delete_sets_enabled_and_timestamp(self):
        """remove_from_watchlist must set enabled=0 and record removed_at."""
        from db.database import remove_from_watchlist

        self._init(self.DEFAULT_WATCHLIST)
        remove_from_watchlist("MSFT")

        row = _get_symbol_row(self.db_path, "MSFT")
        self.assertIsNotNone(row, "Row must still exist after soft-delete")
        self.assertEqual(row["enabled"], 0)
        self.assertIsNotNone(row["removed_at"], "removed_at must be set")

    def test_get_watchlist_excludes_disabled_symbols(self):
        """get_watchlist() must never return symbols with enabled=0."""
        from db.database import remove_from_watchlist, get_watchlist

        self._init(self.DEFAULT_WATCHLIST)
        remove_from_watchlist("CVX")

        wl = get_watchlist()
        all_active = [s for syms in wl.values() for s in syms]
        self.assertNotIn("CVX", all_active)
        self.assertIn("XOM", all_active)  # sibling in same category must still appear


if __name__ == "__main__":
    unittest.main()
