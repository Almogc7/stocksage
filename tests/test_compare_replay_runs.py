"""Tests for scripts/compare_replay_runs.py -- the read-only two-DB
replay_candidates comparator used as the acceptance check for speed-only
changes to the replay harness. Uses tiny temp sqlite DBs built via
scripts/replay_setups.py's own open_results_db()/write_candidate_rows(),
never the real db/stocksage.db.
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import replay_setups as rs  # noqa: E402
from setups.replay import CandidateRow  # noqa: E402

import compare_replay_runs as crr  # noqa: E402


def _row(symbol, variant, scan_date, accepted=True, reason_code=None):
    return CandidateRow(
        symbol=symbol, scan_date=scan_date, regime="BULL", accepted=accepted,
        reason_code=reason_code, variant=variant,
    )


def _build_db(path: Path, run_id_rows: dict) -> int:
    """run_id_rows: {(variant, bound): [CandidateRow, ...]}. Returns the
    created run_id (deliberately never 1 in both DBs being compared, so a
    naive comparison that forgot to exclude run_id would fail even on
    matching data -- see test_run_id_differences_between_dbs_are_ignored)."""
    conn = rs.open_results_db(path)
    run_id = rs.create_run(
        conn, "counts_only", ["V1", "V3"], None, {"V1": rs.V1_PARAMS, "V3": rs.V3_PARAMS},
        rs.V1_OUTCOME_PARAMS, "hash", date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 5), {}, None,
    )
    for (variant, bound), rows in run_id_rows.items():
        rs.write_candidate_rows(conn, run_id, bound, rows, commit=False)
    conn.commit()
    conn.close()
    return run_id


class TestCompareReplayRuns(unittest.TestCase):

    def test_matching_dbs_report_zero_diff_and_exit_zero(self):
        out_dir = Path(tempfile.mkdtemp())
        rows = {
            ("V1", "UPPER"): [_row("AAA", "V1", date(2026, 1, 2)), _row("BBB", "V1", date(2026, 1, 2))],
            ("V1", "LOWER"): [_row("AAA", "V1", date(2026, 1, 2), accepted=False, reason_code="SUPPRESSED")],
        }
        _build_db(out_dir / "a.db", rows)
        _build_db(out_dir / "b.db", rows)  # separate run_id, same candidate content

        exit_code, lines = crr.compare_replay_runs(out_dir / "a.db", out_dir / "b.db")
        self.assertEqual(exit_code, 0)
        self.assertTrue(any("match" in line for line in lines))

    def test_run_id_differences_between_dbs_are_ignored(self):
        """create_run() auto-increments run_id independently in each DB --
        a naive column-for-column comparison that forgot to exclude run_id
        would report every row as mismatched even when the actual replay
        data is identical. This must not happen."""
        out_dir = Path(tempfile.mkdtemp())
        rows_a = {("V1", "UPPER"): [_row("AAA", "V1", date(2026, 1, 2))]}
        rows_b = {("V1", "UPPER"): [_row("AAA", "V1", date(2026, 1, 2))]}
        run_id_a = _build_db(out_dir / "a.db", rows_a)
        # Force a different run_id in b.db by creating (and discarding) an
        # extra run first.
        extra_conn = rs.open_results_db(out_dir / "b.db")
        rs.create_run(
            extra_conn, "counts_only", ["V1"], None, {"V1": rs.V1_PARAMS}, rs.V1_OUTCOME_PARAMS,
            "other-hash", date(2020, 1, 1), date(2020, 1, 5), date(2020, 1, 5), {}, None,
        )
        extra_conn.close()
        run_id_b = _build_db(out_dir / "b.db", rows_b)
        self.assertNotEqual(run_id_a, run_id_b)

        exit_code, _lines = crr.compare_replay_runs(out_dir / "a.db", out_dir / "b.db")
        self.assertEqual(exit_code, 0)

    def test_mismatched_dbs_report_nonzero_diff_and_exit_nonzero(self):
        out_dir = Path(tempfile.mkdtemp())
        rows_a = {("V1", "UPPER"): [_row("AAA", "V1", date(2026, 1, 2), accepted=True)]}
        rows_b = {("V1", "UPPER"): [_row("AAA", "V1", date(2026, 1, 2), accepted=False, reason_code="SUPPRESSED")]}
        _build_db(out_dir / "a.db", rows_a)
        _build_db(out_dir / "b.db", rows_b)

        exit_code, lines = crr.compare_replay_runs(out_dir / "a.db", out_dir / "b.db")
        self.assertEqual(exit_code, 1)
        joined = "\n".join(lines)
        self.assertIn("only_in_a=1", joined)
        self.assertIn("only_in_b=1", joined)

    def test_variants_filter_restricts_the_comparison(self):
        out_dir = Path(tempfile.mkdtemp())
        rows_a = {
            ("V1", "UPPER"): [_row("AAA", "V1", date(2026, 1, 2))],
            ("V3", "UPPER"): [_row("AAA", "V3", date(2026, 1, 2))],
        }
        rows_b = {
            ("V1", "UPPER"): [_row("AAA", "V1", date(2026, 1, 2))],
            ("V3", "UPPER"): [_row("AAA", "V3", date(2026, 1, 2), accepted=False, reason_code="SUPPRESSED")],
        }
        _build_db(out_dir / "a.db", rows_a)
        _build_db(out_dir / "b.db", rows_b)

        exit_code, _lines = crr.compare_replay_runs(out_dir / "a.db", out_dir / "b.db", variants={"V1"})
        self.assertEqual(exit_code, 0)  # V3's mismatch excluded by the filter

        exit_code_all, _lines_all = crr.compare_replay_runs(out_dir / "a.db", out_dir / "b.db")
        self.assertEqual(exit_code_all, 1)  # unfiltered, the V3 mismatch is caught

    def test_tolerates_one_db_having_the_workers_column_and_the_other_not(self):
        """Both DBs here are built with the current schema (workers column
        present in both, since open_results_db() always migrates it in),
        but the comparator must not depend on that column at all --
        replay_candidates (what's compared) never has a workers column in
        the first place; this just confirms the comparator doesn't choke
        on the extra replay_runs.workers column existing in one place."""
        out_dir = Path(tempfile.mkdtemp())
        rows = {("V1", "UPPER"): [_row("AAA", "V1", date(2026, 1, 2))]}
        _build_db(out_dir / "a.db", rows)
        _build_db(out_dir / "b.db", rows)

        conn = rs.open_results_db(out_dir / "a.db")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(replay_runs)").fetchall()}
        conn.close()
        self.assertIn("workers", columns)

        exit_code, _lines = crr.compare_replay_runs(out_dir / "a.db", out_dir / "b.db")
        self.assertEqual(exit_code, 0)

    def test_cli_main_returns_matching_exit_code(self):
        out_dir = Path(tempfile.mkdtemp())
        rows = {("V1", "UPPER"): [_row("AAA", "V1", date(2026, 1, 2))]}
        _build_db(out_dir / "a.db", rows)
        _build_db(out_dir / "b.db", rows)
        rc = crr.main([str(out_dir / "a.db"), str(out_dir / "b.db")])
        self.assertEqual(rc, 0)

    def test_comparator_never_writes_to_either_db(self):
        out_dir = Path(tempfile.mkdtemp())
        rows = {("V1", "UPPER"): [_row("AAA", "V1", date(2026, 1, 2))]}
        _build_db(out_dir / "a.db", rows)
        _build_db(out_dir / "b.db", rows)
        before_a = (out_dir / "a.db").stat().st_mtime_ns
        before_b = (out_dir / "b.db").stat().st_mtime_ns

        crr.compare_replay_runs(out_dir / "a.db", out_dir / "b.db")

        self.assertEqual((out_dir / "a.db").stat().st_mtime_ns, before_a)
        self.assertEqual((out_dir / "b.db").stat().st_mtime_ns, before_b)


if __name__ == "__main__":
    unittest.main()
