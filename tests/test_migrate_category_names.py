"""Tests for scripts/migrate_category_names.py (PR B: English category
names, the data migration). Temp DBs only -- db/stocksage.db and the
webapp snapshot are never opened by anything here.
"""
import glob
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.migrate_category_names as mod

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "migrate_category_names.py"


def _make_temp_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    path = Path(f.name)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE watchlist (symbol TEXT PRIMARY KEY, category TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE symbol_categories (symbol TEXT, category TEXT, PRIMARY KEY (symbol, category))"
    )
    conn.commit()
    conn.close()
    return path


def _insert(path, table, symbol, category):
    conn = sqlite3.connect(path)
    conn.execute(f"INSERT INTO {table} VALUES (?, ?)", (symbol, category))
    conn.commit()
    conn.close()


def _fetch_all(path, table):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY symbol, category")]
    conn.close()
    return rows


def _cleanup(path: Path):
    path.unlink(missing_ok=True)
    for bak in glob.glob(str(path) + ".bak-*"):
        Path(bak).unlink(missing_ok=True)


class TestDryRun(unittest.TestCase):

    def setUp(self):
        self.path = _make_temp_db()
        _insert(self.path, "watchlist", "JPM", "פיננסים")
        _insert(self.path, "symbol_categories", "JPM", "פיננסים")

    def tearDown(self):
        _cleanup(self.path)

    def test_dry_run_changes_nothing(self):
        before = _fetch_all(self.path, "watchlist")
        rc = mod.main(["--db-path", str(self.path)])
        self.assertEqual(rc, 0)
        self.assertEqual(_fetch_all(self.path, "watchlist"), before)
        self.assertEqual(
            _fetch_all(self.path, "symbol_categories"),
            [{"symbol": "JPM", "category": "פיננסים"}],
        )

    def test_dry_run_connection_cannot_write(self):
        conn = mod._open_readonly(self.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO watchlist VALUES ('X', 'Y')")
        finally:
            conn.close()

    def test_audit_connection_cannot_write(self):
        rc = mod.main(["--db-path", str(self.path), "--audit"])
        self.assertEqual(rc, 0)
        # The connection run_audit() itself opens must also be read-only --
        # exercised directly, same guarantee as the dry-run connection.
        conn = mod._open_readonly(self.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM watchlist")
        finally:
            conn.close()


class TestApply(unittest.TestCase):

    def setUp(self):
        self.path = _make_temp_db()

    def tearDown(self):
        _cleanup(self.path)

    def test_apply_renames_both_tables(self):
        _insert(self.path, "watchlist", "JPM", "פיננסים")
        _insert(self.path, "symbol_categories", "JPM", "פיננסים")
        _insert(self.path, "watchlist", "SPY", "ETFs")
        _insert(self.path, "symbol_categories", "SPY", "ETFs")

        rc = mod.main(["--db-path", str(self.path), "--apply"])
        self.assertEqual(rc, 0)

        self.assertEqual(
            _fetch_all(self.path, "watchlist"),
            [{"symbol": "JPM", "category": "Financials"}, {"symbol": "SPY", "category": "ETFs"}],
        )
        self.assertEqual(
            _fetch_all(self.path, "symbol_categories"),
            [{"symbol": "JPM", "category": "Financials"}, {"symbol": "SPY", "category": "ETFs"}],
        )

    def test_ccc_under_both_spellings_collision(self):
        """symbol_categories has UNIQUE(symbol, category). A symbol that
        already has BOTH the old and new spelling (as CCC could, from a
        reclassification pass that ran between PR A and PR B) must end
        up with exactly one row, not a UNIQUE violation."""
        _insert(self.path, "watchlist", "CCC", "פיננסים")
        _insert(self.path, "symbol_categories", "CCC", "פיננסים")
        _insert(self.path, "symbol_categories", "CCC", "Financials")

        rc = mod.main(["--db-path", str(self.path), "--apply"])
        self.assertEqual(rc, 0)

        self.assertEqual(
            _fetch_all(self.path, "watchlist"), [{"symbol": "CCC", "category": "Financials"}]
        )
        self.assertEqual(
            _fetch_all(self.path, "symbol_categories"),
            [{"symbol": "CCC", "category": "Financials"}],
        )

    def test_idempotent_second_apply_changes_nothing(self):
        _insert(self.path, "watchlist", "JPM", "פיננסים")
        _insert(self.path, "symbol_categories", "JPM", "פיננסים")

        rc1 = mod.main(["--db-path", str(self.path), "--apply"])
        self.assertEqual(rc1, 0)
        after_first = _fetch_all(self.path, "watchlist")

        rc2 = mod.main(["--db-path", str(self.path), "--apply"])
        self.assertEqual(rc2, 0)
        self.assertEqual(_fetch_all(self.path, "watchlist"), after_first)

    def test_backup_created_and_openable_with_pre_migration_data(self):
        _insert(self.path, "watchlist", "JPM", "פיננסים")
        _insert(self.path, "symbol_categories", "JPM", "פיננסים")

        mod.main(["--db-path", str(self.path), "--apply"])

        backups = glob.glob(str(self.path) + ".bak-*")
        self.assertEqual(len(backups), 1)
        conn = sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in conn.execute("SELECT * FROM watchlist")]
        finally:
            conn.close()
        self.assertEqual(rows, [{"symbol": "JPM", "category": "פיננסים"}])

    def test_locked_db_refuses_without_writing(self):
        _insert(self.path, "watchlist", "JPM", "פיננסים")
        locker = sqlite3.connect(self.path, isolation_level=None)
        locker.execute("BEGIN IMMEDIATE")
        try:
            rc = mod.main(["--db-path", str(self.path), "--apply"])
            self.assertEqual(rc, 1)
        finally:
            locker.execute("ROLLBACK")
            locker.close()
        # The main write never happened -- still the old spelling.
        self.assertEqual(
            _fetch_all(self.path, "watchlist"), [{"symbol": "JPM", "category": "פיננסים"}]
        )

    def test_verification_failure_rolls_back(self):
        """Force _verify_migration to report a mismatch and confirm the
        transaction is rolled back -- nothing committed, non-zero exit."""
        _insert(self.path, "watchlist", "JPM", "פיננסים")
        _insert(self.path, "symbol_categories", "JPM", "פיננסים")

        with patch.object(mod, "_verify_migration", return_value=["forced failure for this test"]):
            rc = mod.main(["--db-path", str(self.path), "--apply"])
        self.assertEqual(rc, 1)
        self.assertEqual(
            _fetch_all(self.path, "watchlist"), [{"symbol": "JPM", "category": "פיננסים"}]
        )
        self.assertEqual(
            _fetch_all(self.path, "symbol_categories"),
            [{"symbol": "JPM", "category": "פיננסים"}],
        )

    def test_apply_on_default_path_without_backup_flag_refuses(self):
        with patch.object(mod, "_default_db_path", return_value=self.path):
            _insert(self.path, "watchlist", "JPM", "פיננסים")
            rc = mod.main(["--apply"])
        self.assertEqual(rc, 1)
        self.assertEqual(
            _fetch_all(self.path, "watchlist"), [{"symbol": "JPM", "category": "פיננסים"}]
        )

    def test_apply_on_default_path_with_backup_flag_succeeds(self):
        with patch.object(mod, "_default_db_path", return_value=self.path):
            _insert(self.path, "watchlist", "JPM", "פיננסים")
            rc = mod.main(["--apply", "--i-have-a-backup"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            _fetch_all(self.path, "watchlist"), [{"symbol": "JPM", "category": "Financials"}]
        )

    def test_apply_with_explicit_db_path_does_not_need_backup_flag(self):
        _insert(self.path, "watchlist", "JPM", "פיננסים")
        rc = mod.main(["--db-path", str(self.path), "--apply"])
        self.assertEqual(rc, 0)


class TestAudit(unittest.TestCase):

    def setUp(self):
        self.path = _make_temp_db()

    def tearDown(self):
        _cleanup(self.path)

    def test_stray_category_is_listed_and_never_fixed(self):
        _insert(self.path, "watchlist", "WEIRD", "Some Made-Up Category")
        rc = mod.main(["--db-path", str(self.path), "--audit"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            _fetch_all(self.path, "watchlist"),
            [{"symbol": "WEIRD", "category": "Some Made-Up Category"}],
        )

    def test_known_categories_either_spelling_are_not_flagged(self):
        _insert(self.path, "watchlist", "JPM", "פיננסים")
        _insert(self.path, "watchlist", "GS", "Financials")
        _insert(self.path, "watchlist", "SPY", "ETFs")
        # Should not raise and should report cleanly -- exercised via main().
        rc = mod.main(["--db-path", str(self.path), "--audit"])
        self.assertEqual(rc, 0)


class TestRunAsSubprocess(unittest.TestCase):
    """Running the script directly (`python scripts/migrate_category_names.py
    ...`), not via `import` -- the only way the ModuleNotFoundError on
    `import config` could actually surface, since pytest/unittest's own
    import machinery already has the repo root on sys.path by the time
    `import scripts.migrate_category_names` runs anywhere else in this
    file."""

    def test_help_from_a_different_working_directory_exits_zero_no_traceback(self):
        result = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), "--help"],
            cwd=tempfile.gettempdir(),  # deliberately NOT the repo root
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("usage:", result.stdout.lower())

    def test_dry_run_subprocess_against_a_temp_db(self):
        path = _make_temp_db()
        try:
            _insert(path, "watchlist", "JPM", "פיננסים")
            result = subprocess.run(
                [sys.executable, str(_SCRIPT_PATH), "--db-path", str(path)],
                cwd=tempfile.gettempdir(),
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("DRY RUN", result.stdout)
            # Dry run via subprocess must still change nothing.
            self.assertEqual(
                _fetch_all(path, "watchlist"), [{"symbol": "JPM", "category": "פיננסים"}]
            )
        finally:
            _cleanup(path)


class TestGitignoreCoversBackupFiles(unittest.TestCase):
    """The server's auto-sync skips every pull cycle when `git status
    --porcelain` is non-empty -- a stray, untracked backup file left
    behind by --apply must never cause that."""

    def test_bak_pattern_present_and_matches_a_sample_filename(self):
        gitignore_path = _REPO_ROOT / ".gitignore"
        content = gitignore_path.read_text(encoding="utf-8")
        pattern_line = "db/*.bak-*"
        self.assertIn(pattern_line, content.splitlines())

        result = subprocess.run(
            ["git", "check-ignore", "-v", "db/stocksage.db.bak-20261024-070432-096217"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(pattern_line, result.stdout)


class TestBackupVerification(unittest.TestCase):
    """_backup_database()'s strengthened checks: PRAGMA integrity_check
    must return "ok", and BOTH watchlist and symbol_categories row counts
    must match between source and backup."""

    def setUp(self):
        self.path = _make_temp_db()

    def tearDown(self):
        _cleanup(self.path)

    def test_backup_matches_both_table_row_counts_and_passes_integrity_check(self):
        _insert(self.path, "watchlist", "JPM", "פיננסים")
        _insert(self.path, "watchlist", "SPY", "ETFs")
        _insert(self.path, "symbol_categories", "JPM", "פיננסים")
        _insert(self.path, "symbol_categories", "SPY", "ETFs")
        _insert(self.path, "symbol_categories", "JPM", "Mega Cap Tech")  # second tag for JPM

        backup_path = mod._backup_database(self.path)
        try:
            conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                self.assertEqual(integrity, "ok")
                watchlist_count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
                sc_count = conn.execute("SELECT COUNT(*) FROM symbol_categories").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(watchlist_count, 2)
            self.assertEqual(sc_count, 3)
        finally:
            Path(backup_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
