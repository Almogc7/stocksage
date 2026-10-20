"""Tests for webapp/deploy/snapshot_bot_db.py. Kept under
webapp/backend/tests (not a separate webapp/deploy/tests) so the usual
`python -m unittest discover tests` command picks these up too -- the
script itself is imported via a small sys.path insert since it lives
outside app/."""
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests._helpers import make_temp_bot_db

DEPLOY_DIR = str(Path(__file__).resolve().parents[2] / "deploy")
if DEPLOY_DIR not in sys.path:
    sys.path.insert(0, DEPLOY_DIR)

import snapshot_bot_db  # noqa: E402


class SnapshotBotDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="stocksage_snapshot_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = make_temp_bot_db(self.tmp, [("NVDA", "Semis", "ACTIVE", 1, 1)])

    def _row_count(self, db_path: Path) -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        finally:
            conn.close()

    def test_happy_path_with_directories_that_do_not_exist_yet(self):
        dest = self.tmp / "nested" / "dest" / "bot.db"
        log_path = self.tmp / "nested" / "logs" / "snapshot.log"

        rc = snapshot_bot_db.run_snapshot(self.source, dest, log_path)

        self.assertEqual(rc, 0)
        self.assertTrue(dest.is_file())
        self.assertEqual(self._row_count(dest), 1)
        self.assertTrue(log_path.is_file())
        self.assertIn("snapshot ok", log_path.read_text())

    def test_source_db_is_provably_untouched(self):
        before = (self.source.read_bytes(), self.source.stat().st_mtime_ns)
        dest = self.tmp / "dest.db"
        log_path = self.tmp / "snapshot.log"

        snapshot_bot_db.run_snapshot(self.source, dest, log_path)

        after = (self.source.read_bytes(), self.source.stat().st_mtime_ns)
        self.assertEqual(before, after)

    def test_idempotent_second_run_leaves_a_clean_end_state(self):
        dest = self.tmp / "dest.db"
        log_path = self.tmp / "snapshot.log"

        rc1 = snapshot_bot_db.run_snapshot(self.source, dest, log_path)
        rc2 = snapshot_bot_db.run_snapshot(self.source, dest, log_path)

        self.assertEqual((rc1, rc2), (0, 0))
        self.assertEqual(self._row_count(dest), 1)
        leftover_tmp_files = list(dest.parent.glob(".*.tmp-*"))
        self.assertEqual(leftover_tmp_files, [])

    def test_space_in_path_is_handled_via_as_uri_encoding(self):
        spaced_dir = self.tmp / "stock sage test dir"
        spaced_dir.mkdir()
        source_with_space = spaced_dir / "bot db.db"
        shutil.copy(self.source, source_with_space)
        dest = spaced_dir / "dest db.db"
        log_path = self.tmp / "snapshot.log"

        rc = snapshot_bot_db.run_snapshot(source_with_space, dest, log_path)

        self.assertEqual(rc, 0)
        self.assertEqual(self._row_count(dest), 1)

    def test_corrupted_copy_leaves_previous_good_snapshot_untouched(self):
        dest = self.tmp / "dest.db"
        log_path = self.tmp / "snapshot.log"
        snapshot_bot_db.run_snapshot(self.source, dest, log_path)
        before = dest.read_bytes()

        with mock.patch.object(snapshot_bot_db, "_integrity_check", return_value=False):
            rc = snapshot_bot_db.run_snapshot(self.source, dest, log_path)

        self.assertEqual(rc, 1)
        self.assertEqual(dest.read_bytes(), before)
        self.assertIn("ERROR: integrity_check failed", log_path.read_text())
        leftover_tmp_files = list(dest.parent.glob(".*.tmp-*"))
        self.assertEqual(leftover_tmp_files, [])

    def test_mid_copy_interruption_leaves_no_partial_file_and_no_dest_yet(self):
        dest = self.tmp / "dest.db"
        log_path = self.tmp / "snapshot.log"

        class _BoomConnection(sqlite3.Connection):
            def backup(self, *args, **kwargs):
                raise RuntimeError("boom")

        real_connect = sqlite3.connect

        def fake_connect(database, *args, **kwargs):
            if kwargs.get("uri"):
                kwargs["factory"] = _BoomConnection
            return real_connect(database, *args, **kwargs)

        with mock.patch.object(snapshot_bot_db.sqlite3, "connect", side_effect=fake_connect):
            rc = snapshot_bot_db.run_snapshot(self.source, dest, log_path)

        self.assertEqual(rc, 1)
        self.assertFalse(dest.exists())
        self.assertEqual(list(dest.parent.glob(".*.tmp-*")), [])
        self.assertIn("ERROR: backup failed", log_path.read_text())

    def test_replace_retries_then_gives_up_on_permission_error(self):
        dest = self.tmp / "dest.db"
        log_path = self.tmp / "snapshot.log"
        snapshot_bot_db.run_snapshot(self.source, dest, log_path)  # seed a good snapshot
        before = dest.read_bytes()

        calls = []

        def always_fails(_src, _dst):
            calls.append(1)
            raise PermissionError("locked")

        with mock.patch.object(snapshot_bot_db.os, "replace", side_effect=always_fails), mock.patch.object(
            snapshot_bot_db.time, "sleep"
        ):
            rc = snapshot_bot_db.run_snapshot(self.source, dest, log_path)

        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), snapshot_bot_db.REPLACE_MAX_ATTEMPTS)
        self.assertEqual(dest.read_bytes(), before)
        self.assertEqual(list(dest.parent.glob(".*.tmp-*")), [])
        self.assertIn("ERROR: os.replace failed", log_path.read_text())

    def test_missing_source_db_is_logged_not_raised(self):
        dest = self.tmp / "dest.db"
        log_path = self.tmp / "snapshot.log"
        missing_source = self.tmp / "does-not-exist.db"

        rc = snapshot_bot_db.run_snapshot(missing_source, dest, log_path)

        self.assertEqual(rc, 1)
        self.assertFalse(dest.exists())
        self.assertIn("ERROR: backup failed", log_path.read_text())
        self.assertEqual(list(dest.parent.glob(".*.tmp-*")), [])

    def test_stale_tmp_file_older_than_an_hour_is_removed_at_start(self):
        dest = self.tmp / "dest.db"
        log_path = self.tmp / "snapshot.log"
        stale = self.tmp / f".{dest.name}.tmp-99999"
        stale.write_text("leftover from a crashed run")
        old_time = time.time() - snapshot_bot_db.STALE_TMP_MAX_AGE_SECONDS - 60
        import os as _os
        _os.utime(stale, (old_time, old_time))

        rc = snapshot_bot_db.run_snapshot(self.source, dest, log_path)

        self.assertEqual(rc, 0)
        self.assertFalse(stale.exists())
        self.assertIn("removed stale tmp file", log_path.read_text())

    def test_recent_tmp_file_under_an_hour_old_is_kept(self):
        dest = self.tmp / "dest.db"
        log_path = self.tmp / "snapshot.log"
        recent = self.tmp / f".{dest.name}.tmp-88888"
        recent.write_text("another run in flight")

        rc = snapshot_bot_db.run_snapshot(self.source, dest, log_path)

        self.assertEqual(rc, 0)
        self.assertTrue(recent.exists())
        recent.unlink()

    def test_replace_retries_then_succeeds(self):
        dest = self.tmp / "dest.db"
        log_path = self.tmp / "snapshot.log"

        calls = []
        real_replace = snapshot_bot_db.os.replace

        def fails_twice_then_succeeds(src, dst):
            calls.append(1)
            if len(calls) <= 2:
                raise PermissionError("locked")
            return real_replace(src, dst)

        with mock.patch.object(snapshot_bot_db.os, "replace", side_effect=fails_twice_then_succeeds), mock.patch.object(
            snapshot_bot_db.time, "sleep"
        ):
            rc = snapshot_bot_db.run_snapshot(self.source, dest, log_path)

        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 3)
        self.assertTrue(dest.is_file())
        self.assertNotIn("ERROR", log_path.read_text())


class LoadConfigTests(unittest.TestCase):
    def test_missing_source_raises(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(
            snapshot_bot_db, "load_dotenv", None
        ):
            with self.assertRaises(snapshot_bot_db.SnapshotError):
                snapshot_bot_db.load_config()

    def test_defaults_used_when_dest_and_log_unset(self):
        with mock.patch.dict(
            "os.environ", {"SNAPSHOT_SOURCE_DB_PATH": "C:/some/bot.db"}, clear=True
        ), mock.patch.object(snapshot_bot_db, "load_dotenv", None):
            source, dest, log_path = snapshot_bot_db.load_config()
        self.assertEqual(dest, snapshot_bot_db.DEFAULT_DEST_DB_PATH)
        self.assertEqual(log_path, snapshot_bot_db.DEFAULT_LOG_PATH)


if __name__ == "__main__":
    unittest.main()
