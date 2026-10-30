"""Tests for scripts/peer_counts.py -- the --peer-counts-only diagnostic
(docs/PREREGISTRATION_SETUPS_V1.md ambiguity #7), split into its own
script so it structurally cannot affect scripts/analyze_replay.py's
CONFIRMATORY/EXPLORATORY label.

HARD RULE enforced throughout: no real results DB and no real price cache
are ever opened here -- every DB and every cached frame is built fresh in
a temp directory from synthetic data via a fake downloader and
scripts.replay_setups.main() itself.
"""
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import replay_setups as rs  # noqa: E402
import peer_counts as pc  # noqa: E402
import setups.analysis as sa  # noqa: E402


def _run_git(args, cwd):
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result


def _make_temp_git_repo():
    tmp = Path(tempfile.mkdtemp())
    _run_git(["init"], tmp)
    _run_git(["config", "user.email", "test@example.com"], tmp)
    _run_git(["config", "user.name", "Test"], tmp)
    (tmp / "README.md").write_text("temp repo for peer_counts tests\n")
    (tmp / "requirements.txt").write_text("placeholder==1.0.0\n")
    _run_git(["add", "-A"], tmp)
    _run_git(["commit", "-m", "init"], tmp)
    return tmp


def _trending_frame(seed, n=400, start="2023-01-01", trend=30.0, noise=1.0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n)
    close = 100.0 + np.linspace(0, trend, n) + rng.normal(0, noise, n)
    return pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
         "volume": np.full(n, 2_000_000.0)},
        index=pd.DatetimeIndex(dates),
    )


def _fake_downloader(frames_by_symbol):
    def _dl(symbols, start, end):
        return {s: frames_by_symbol.get(s) for s in symbols}
    return _dl


def _build_counts_only_db(out_dir: Path, repo, n_symbols=6, n=400, start="2023-01-01",
                           scan_start_idx=250, scan_end_idx=350):
    symbols = [f"SYM{i}" for i in range(n_symbols)]
    frames = {s: _trending_frame(i, n=n, start=start) for i, s in enumerate(symbols)}
    frames["SPY"] = _trending_frame(999, n=n, start=start, trend=20.0)
    dates = pd.bdate_range(start=start, periods=n)

    symbols_file = out_dir / "symbols.txt"
    symbols_file.write_text("\n".join(symbols) + "\n")
    start_date = dates[scan_start_idx].date()
    end_date = dates[scan_end_idx].date()
    argv = [
        "--symbols-file", str(symbols_file), "--start", str(start_date), "--end", str(end_date),
        "--output", str(out_dir / "counts.db"), "--cache-dir", str(out_dir / "cache"),
        "--counts-only", "--variants", "V3",
    ]
    rc = rs.main(argv, downloader=_fake_downloader(frames), repo_root=repo, version_lookup=lambda n: "1.0.0")
    assert rc == 0, "fixture counts-only run failed"
    return out_dir / "counts.db", out_dir / "cache", symbols


class _ImmediateExecutor:
    def __init__(self, max_workers=1):
        self.max_workers = max_workers

    def submit(self, fn, task):
        future = Future()
        try:
            future.set_result(fn(task))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        pass

    def __call__(self, max_workers=1):
        return self


class TestPeerCountsBasics(unittest.TestCase):

    def test_peer_counts_reads_counts_only_db_and_never_touches_outcome_columns(self):
        repo = _make_temp_git_repo()
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols = _build_counts_only_db(out_dir, repo)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM replay_candidates WHERE bound='LOWER' AND variant='V3' AND accepted=1"
        ).fetchall()
        conn.close()
        for r in rows:
            self.assertIsNone(r["fill_date"])
            self.assertIsNone(r["r_multiple"])

        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = pc.main(argv, repo_root=repo)
        self.assertEqual(rc, 0)
        for row in pc.main.last_results:
            self.assertIn("peer_count", row)

    def test_peer_counts_uses_session_before_scan_date_as_the_reference(self):
        repo = _make_temp_git_repo()
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols = _build_counts_only_db(out_dir, repo)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = pc.main(argv, repo_root=repo)
        self.assertEqual(rc, 0)
        report = (out_dir / "report.md").read_text()
        self.assertIn("approximation", report.lower())
        self.assertIn("reference_date", report)

    def test_peer_counts_never_computes_r_or_a_label(self):
        """Structural: no function from the confirmatory pipeline
        (decide, determine_label, build_control_for_trade,
        month_cluster_bootstrap) is even imported into this script's
        actual code -- only its module docstring is allowed to mention
        them in prose."""
        self.assertFalse(hasattr(pc, "decide"))
        self.assertFalse(hasattr(pc, "determine_label"))
        self.assertFalse(hasattr(pc, "build_control_for_trade"))
        self.assertFalse(hasattr(pc, "month_cluster_bootstrap"))
        self.assertFalse(hasattr(pc, "simulate_outcome"))

    def test_peer_counts_report_is_written_outside_the_repo(self):
        repo = _make_temp_git_repo()
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols = _build_counts_only_db(out_dir, repo)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(repo / "inside.md")]
        with self.assertRaises(RuntimeError):
            pc.main(argv, repo_root=repo)


class TestPeerCountsWorkers(unittest.TestCase):

    def test_peer_counts_workers_default_matches_sequential_baseline(self):
        repo = _make_temp_git_repo()
        out_dir1, out_dir2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        db_path1, cache_dir1, symbols = _build_counts_only_db(out_dir1, repo)
        db_path2, cache_dir2, _s2 = _build_counts_only_db(out_dir2, repo)

        argv1 = ["--results-db", str(db_path1), "--cache-dir", str(cache_dir1), "--output", str(out_dir1 / "r.md")]
        rc1 = pc.main(argv1, repo_root=repo)
        self.assertEqual(rc1, 0)
        results1 = sorted((r["symbol"], str(r["scan_date"]), r["peer_count"]) for r in pc.main.last_results)

        argv2 = ["--results-db", str(db_path2), "--cache-dir", str(cache_dir2), "--output", str(out_dir2 / "r.md"),
                 "--workers", "1"]
        rc2 = pc.main(argv2, repo_root=repo)
        self.assertEqual(rc2, 0)
        results2 = sorted((r["symbol"], str(r["scan_date"]), r["peer_count"]) for r in pc.main.last_results)

        self.assertEqual(results1, results2)

    def test_peer_counts_workers_two_matches_fake_pool(self):
        repo = _make_temp_git_repo()
        out_dir1, out_dir2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        db_path1, cache_dir1, symbols = _build_counts_only_db(out_dir1, repo)
        db_path2, cache_dir2, _s2 = _build_counts_only_db(out_dir2, repo)

        argv1 = ["--results-db", str(db_path1), "--cache-dir", str(cache_dir1), "--output", str(out_dir1 / "r.md"),
                 "--workers", "1"]
        rc1 = pc.main(argv1, repo_root=repo)
        self.assertEqual(rc1, 0)
        results1 = sorted((r["symbol"], str(r["scan_date"]), r["peer_count"]) for r in pc.main.last_results)

        argv2 = ["--results-db", str(db_path2), "--cache-dir", str(cache_dir2), "--output", str(out_dir2 / "r.md"),
                 "--workers", "2"]
        rc2 = pc.main(argv2, repo_root=repo, executor_factory=_ImmediateExecutor())
        self.assertEqual(rc2, 0)
        results2 = sorted((r["symbol"], str(r["scan_date"]), r["peer_count"]) for r in pc.main.last_results)

        self.assertEqual(results1, results2)


if __name__ == "__main__":
    unittest.main()
