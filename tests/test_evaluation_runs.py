"""Tests for evaluation-run tracking (Phase 2 of the dynamic watchlist lifecycle).

These tests only exercise the bookkeeping table — no yfinance calls, no
promotion/demotion wiring, no scheduler. Every test uses a temp SQLite file;
none touch the real production database.
"""
import importlib
import tempfile
import unittest
from pathlib import Path


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


class TestEvaluationRunTracking(unittest.TestCase):

    def _tmp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        return f.name

    def _db(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({"AI & Semiconductors": ["NVDA"]})
        return db

    def test_migration_creates_evaluation_runs_table(self):
        db = self._db()
        with db._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(evaluation_runs)").fetchall()}
        self.assertIn("run_id", cols)
        self.assertIn("run_type", cols)
        self.assertIn("status", cols)
        self.assertIn("metadata_json", cols)

    def test_migration_is_idempotent(self):
        db = self._db()
        db.migrate_db()
        db.migrate_db()
        with db._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='evaluation_runs'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_create_run_has_started_status(self):
        db = self._db()
        run_id = db.create_evaluation_run("manual")
        run = db.get_evaluation_run(run_id)
        self.assertEqual(run["status"], "started")
        self.assertIsNotNone(run["started_at"])
        self.assertIsNone(run["completed_at"])

    def test_mark_run_success(self):
        db = self._db()
        run_id = db.create_evaluation_run("scheduled")
        db.update_evaluation_run_success(run_id, promotions_count=2, demotions_count=1)
        run = db.get_evaluation_run(run_id)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["promotions_count"], 2)
        self.assertEqual(run["demotions_count"], 1)
        self.assertIsNotNone(run["completed_at"])

    def test_mark_run_failed(self):
        db = self._db()
        run_id = db.create_evaluation_run("scheduled")
        db.update_evaluation_run_failure(run_id, "yfinance total outage")
        run = db.get_evaluation_run(run_id)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_summary"], "yfinance total outage")

    def test_mark_run_partial_failure(self):
        db = self._db()
        run_id = db.create_evaluation_run("scheduled")
        db.update_evaluation_run_partial_failure(
            run_id, "12 symbols failed", provider_error_count=12
        )
        run = db.get_evaluation_run(run_id)
        self.assertEqual(run["status"], "partial_failure")
        self.assertEqual(run["provider_error_count"], 12)

    def test_duration_is_calculated_on_finalize(self):
        db = self._db()
        run_id = db.create_evaluation_run("manual")
        db.update_evaluation_run_success(run_id)
        run = db.get_evaluation_run(run_id)
        self.assertIsInstance(run["duration_seconds"], float)
        self.assertGreaterEqual(run["duration_seconds"], 0.0)

    def test_timestamps_are_consistent_utc_format(self):
        db = self._db()
        run_id = db.create_evaluation_run("manual")
        run = db.get_evaluation_run(run_id)
        # Space separator (not "T"), matching the rest of the codebase's UTC convention.
        self.assertRegex(run["started_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        db.update_evaluation_run_success(run_id)
        run = db.get_evaluation_run(run_id)
        self.assertRegex(run["completed_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_get_last_evaluation_run_returns_most_recent(self):
        db = self._db()
        db.create_evaluation_run("manual")
        run_id_2 = db.create_evaluation_run("scheduled")
        last = db.get_last_evaluation_run()
        self.assertEqual(last["run_id"], run_id_2)

    def test_get_last_successful_evaluation_run_ignores_failures(self):
        db = self._db()
        success_id = db.create_evaluation_run("manual")
        db.update_evaluation_run_success(success_id)
        failed_id = db.create_evaluation_run("scheduled")
        db.update_evaluation_run_failure(failed_id, "outage")

        last_success = db.get_last_successful_evaluation_run()
        self.assertEqual(last_success["run_id"], success_id)

    def test_list_recent_evaluation_runs_ordered_newest_first(self):
        db = self._db()
        first = db.create_evaluation_run("manual")
        second = db.create_evaluation_run("manual")
        third = db.create_evaluation_run("manual")
        runs = db.list_recent_evaluation_runs(limit=10)
        self.assertEqual([r["run_id"] for r in runs], [third, second, first])

    def test_list_recent_evaluation_runs_respects_limit(self):
        db = self._db()
        for _ in range(5):
            db.create_evaluation_run("manual")
        runs = db.list_recent_evaluation_runs(limit=2)
        self.assertEqual(len(runs), 2)

    def test_dry_run_flag_is_stored_correctly(self):
        db = self._db()
        dry_id = db.create_evaluation_run("dry_run", dry_run=True)
        real_id = db.create_evaluation_run("manual", dry_run=False)
        self.assertEqual(db.get_evaluation_run(dry_id)["dry_run"], 1)
        self.assertEqual(db.get_evaluation_run(real_id)["dry_run"], 0)

    def test_metadata_json_round_trips(self):
        db = self._db()
        run_id = db.create_evaluation_run(
            "manual", metadata={"workers": 4, "period": "1y"}
        )
        run = db.get_evaluation_run(run_id)
        self.assertEqual(run["metadata_json"], {"workers": 4, "period": "1y"})

    def test_metadata_json_can_be_set_on_finalize(self):
        db = self._db()
        run_id = db.create_evaluation_run("manual")
        db.update_evaluation_run_success(run_id, metadata={"active_after": 30})
        run = db.get_evaluation_run(run_id)
        self.assertEqual(run["metadata_json"], {"active_after": 30})

    def test_error_summary_stores_plain_text_only(self):
        db = self._db()
        run_id = db.create_evaluation_run("scheduled")
        summary = "Connection timeout after 3 retries for 12 symbols"
        db.update_evaluation_run_failure(run_id, summary)
        run = db.get_evaluation_run(run_id)
        self.assertEqual(run["error_summary"], summary)
        self.assertNotIn("Traceback", run["error_summary"])

    def test_record_evaluation_run_counts_mid_run(self):
        db = self._db()
        run_id = db.create_evaluation_run("scheduled")
        db.record_evaluation_run_counts(run_id, total_symbols_evaluated=50, cache_hits=10)
        run = db.get_evaluation_run(run_id)
        self.assertEqual(run["status"], "started")  # not finalized
        self.assertEqual(run["total_symbols_evaluated"], 50)
        self.assertEqual(run["cache_hits"], 10)

    def test_record_evaluation_run_counts_rejects_unknown_column(self):
        db = self._db()
        run_id = db.create_evaluation_run("scheduled")
        with self.assertRaises(ValueError):
            db.record_evaluation_run_counts(run_id, not_a_real_column=1)

    def test_in_progress_run_can_be_detected(self):
        db = self._db()
        run_id = db.create_evaluation_run("scheduled")
        in_progress = db.get_in_progress_evaluation_run()
        self.assertIsNotNone(in_progress)
        self.assertEqual(in_progress["run_id"], run_id)

    def test_in_progress_run_not_detected_after_finalized(self):
        db = self._db()
        run_id = db.create_evaluation_run("scheduled")
        db.update_evaluation_run_success(run_id)
        self.assertIsNone(db.get_in_progress_evaluation_run())

    def test_cancel_evaluation_run(self):
        db = self._db()
        run_id = db.create_evaluation_run("scheduled")
        db.cancel_evaluation_run(run_id, reason="superseded by manual refresh")
        run = db.get_evaluation_run(run_id)
        self.assertEqual(run["status"], "cancelled")
        self.assertIsNone(db.get_in_progress_evaluation_run())

    def test_finalize_unknown_run_id_raises(self):
        db = self._db()
        with self.assertRaises(ValueError):
            db.update_evaluation_run_success(99999)

    def test_existing_watchlist_rows_not_modified_by_this_phase(self):
        """Creating/finalizing evaluation runs must never touch the watchlist table."""
        db = self._db()
        before = db.get_symbol_status("NVDA")

        run_id = db.create_evaluation_run("manual")
        db.record_evaluation_run_counts(run_id, total_symbols_evaluated=1)
        db.update_evaluation_run_success(run_id, promotions_count=1)

        after = db.get_symbol_status("NVDA")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
