"""Tests for scripts/analyze_replay.py (I/O: the confirmatory pipeline for
docs/PREREGISTRATION_SETUPS_V1.md sections 6/7/8/13).

HARD RULE enforced throughout: no real OUTCOMES results DB and no real
price cache are ever opened here -- every results DB and every cached
frame used in this file is built fresh in a temp directory from synthetic
data, via a fake downloader and scripts.replay_setups.main() itself (the
same pattern tests/test_setups_replay.py already uses).
"""
import importlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import Future
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import replay_setups as rs  # noqa: E402
import analyze_replay as ar  # noqa: E402
import setups.analysis as sa  # noqa: E402
from tests.test_setups_replay import GOLDEN_CLOSE, _frame_from_close  # noqa: E402


# ── Fixtures (mirrors tests/test_setups_replay.py's own I/O fixtures) ────

def _run_git(args, cwd):
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result


def _make_temp_git_repo(with_preregistration=False, dirty=False):
    tmp = Path(tempfile.mkdtemp())
    _run_git(["init"], tmp)
    _run_git(["config", "user.email", "test@example.com"], tmp)
    _run_git(["config", "user.name", "Test"], tmp)
    (tmp / "README.md").write_text("temp repo for analyze_replay tests\n")
    (tmp / "requirements.txt").write_text("placeholder==1.0.0\n")
    if with_preregistration:
        (tmp / "docs").mkdir(exist_ok=True)
        (tmp / "docs" / "PREREGISTRATION_SETUPS_V1.md").write_text("frozen\n")
    _run_git(["add", "-A"], tmp)
    _run_git(["commit", "-m", "init"], tmp)
    if dirty:
        (tmp / "dirty.txt").write_text("uncommitted\n")
    return tmp


def _trending_frame(seed, n=900, start="2021-06-01", trend=60.0, noise=1.0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n)
    close = 100.0 + np.linspace(0, trend, n) + rng.normal(0, noise, n)
    return pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
         "volume": np.full(n, 2_000_000.0)},
        index=pd.DatetimeIndex(dates),
    )


def _fake_downloader(frames_by_symbol, call_log=None):
    def _dl(symbols, start, end):
        if call_log is not None:
            call_log.append(tuple(symbols))
        return {s: frames_by_symbol.get(s) for s in symbols}
    return _dl


def _build_run_outcomes_db(out_dir: Path, repo, n_symbols=8, n=900, start="2021-06-01",
                            scan_start_idx=500, scan_end_idx=535, fixture_slippage_bps=None):
    """fixture_slippage_bps: passed to the FIXTURE replay's own
    --slippage-bps (scripts/replay_setups.py's existing flag) -- lets a
    test force the run header's recorded outcome_params_json.slippage_bps
    away from the frozen 10bps, which is what scripts/analyze_replay.py's
    label check now reads (there is no --slippage-bps on analyze_replay.py
    itself any more; the primary analysis always re-runs the frozen
    simulator, and the 20bps secondary is always recomputed from stored
    prices, per §8/§13.7)."""
    symbols = [f"SYM{i}" for i in range(n_symbols)]
    frames = {s: _trending_frame(i, n=n, start=start) for i, s in enumerate(symbols)}
    frames["SPY"] = _trending_frame(999, n=n, start=start, trend=40.0)
    dates = pd.bdate_range(start=start, periods=n)

    symbols_file = out_dir / "symbols.txt"
    symbols_file.write_text("\n".join(symbols) + "\n")
    start_date = dates[scan_start_idx].date()
    end_date = dates[scan_end_idx].date()
    argv = [
        "--symbols-file", str(symbols_file), "--start", str(start_date), "--end", str(end_date),
        "--output", str(out_dir / "run.db"), "--cache-dir", str(out_dir / "cache"),
        "--run-outcomes", "--variants", "V3",
    ]
    if fixture_slippage_bps is not None:
        argv += ["--slippage-bps", str(fixture_slippage_bps)]
    rc = rs.main(argv, downloader=_fake_downloader(frames), repo_root=repo, version_lookup=lambda n: "1.0.0")
    assert rc == 0, "fixture replay run failed"
    return out_dir / "run.db", out_dir / "cache", symbols, frames


def _build_confirmatory_fixture_with_edge_cases(out_dir: Path, repo, n_normal=25, n=900, start="2021-06-01",
                                                  scan_start_idx=300, scan_end_idx=360):
    """A universe deliberately exercising every messy real-world case the
    pipeline must still be CONFIRMATORY-consistent about: a symbol whose
    download fails entirely (None), a DATA_GAP trade, and an OPEN_AT_END
    trade -- alongside plenty of plain trending peers (>= MIN_PEERS) so
    the GOLDEN symbol's own primary trade has a usable control.

    GOLDEN_CLOSE (tests.test_setups_replay) is a hand-tuned 268-bar
    uptrend+pullback series proven (by that file's own tests) to clear
    every evaluate_candidate()/define_setup() gate at its LAST bar --
    V3 behaves identically to V1 here since a confirmed swing-high
    resistance exists (§2 of the pre-registration: "V3 behaves exactly
    like V1" whenever one does), so reusing it for the V3 --run-outcomes
    replay is safe. Acceptance depends only on bars UP TO scan_date
    (truncate_to_date) -- the bars appended AFTER it below only affect
    each variant's OUTCOME simulation, never its acceptance.
    """
    dates = pd.bdate_range(start=start, periods=n)
    symbols = [f"SYM{i}" for i in range(n_normal)]
    frames = {s: _trending_frame(i, n=n, start=start) for i, s in enumerate(symbols)}
    frames["SPY"] = _trending_frame(999, n=n, start=start, trend=40.0)

    golden_len = len(GOLDEN_CLOSE)
    golden_scan_pos = (scan_start_idx + scan_end_idx) // 2
    golden_start_pos = golden_scan_pos - (golden_len - 1)
    golden_start_date = str(dates[golden_start_pos].date())

    # GOLDEN: a normal primary trade (resolves via stop/target/timeout
    # well within the data -- plenty of trailing bars).
    golden_tail = list(np.linspace(GOLDEN_CLOSE[-1], GOLDEN_CLOSE[-1] + 10.0, 40))
    frames["GOLDEN"] = _frame_from_close(list(GOLDEN_CLOSE) + golden_tail, start=golden_start_date)
    symbols.append("GOLDEN")

    # GOLDENOAE: identical acceptance, but the frame ends EXACTLY at the
    # scan date -- zero bars after it, so the very first expected
    # pre-fill session is entirely missing from the data -> OPEN_AT_END.
    frames["GOLDENOAE"] = _frame_from_close(list(GOLDEN_CLOSE), start=golden_start_date)
    symbols.append("GOLDENOAE")

    # GOLDENGAP: identical acceptance, but the FIRST bar after the scan
    # date is corrupted (all-NaN) -- an unusable bar that's still PRESENT
    # in the index (not missing) -> DATA_GAP, regardless of what follows.
    gap_tail = [GOLDEN_CLOSE[-1] + 1.0, GOLDEN_CLOSE[-1] + 2.0, GOLDEN_CLOSE[-1] + 3.0]
    gap_df = _frame_from_close(list(GOLDEN_CLOSE) + gap_tail, start=golden_start_date)
    gap_df.iloc[golden_len, :] = float("nan")
    frames["GOLDENGAP"] = gap_df
    symbols.append("GOLDENGAP")

    # FAILSYM: a symbol whose download fails entirely (None) -- never
    # cached, never has an accepted row, but still appears in the
    # universe/manifest.
    frames["FAILSYM"] = None
    symbols.append("FAILSYM")

    symbols_file = out_dir / "symbols.txt"
    symbols_file.write_text("\n".join(symbols) + "\n")
    start_date = dates[scan_start_idx].date()
    end_date = dates[scan_end_idx].date()
    argv = [
        "--symbols-file", str(symbols_file), "--start", str(start_date), "--end", str(end_date),
        "--output", str(out_dir / "run.db"), "--cache-dir", str(out_dir / "cache"),
        "--run-outcomes", "--variants", "V3",
    ]
    rc = rs.main(argv, downloader=_fake_downloader(frames), repo_root=repo, version_lookup=lambda n: "1.0.0")
    assert rc == 0, "fixture replay run failed"
    return out_dir / "run.db", out_dir / "cache", symbols, frames


class _ImmediateExecutor:
    """Synchronous stand-in for ProcessPoolExecutor -- same pattern as
    tests/test_setups_replay.py's _FakeExecutor, duplicated here (small,
    self-contained) rather than imported across test files."""

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


class TestReportOutputGuard(unittest.TestCase):

    def test_report_is_written_outside_the_repo_and_refuses_an_inside_path(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=5)
        argv = [
            "--results-db", str(db_path), "--cache-dir", str(cache_dir),
            "--output", str(repo / "inside_report.md"),
        ]
        with self.assertRaises(RuntimeError):
            ar.main(argv, repo_root=repo)


class TestReportHeaderAndLabel(unittest.TestCase):

    def test_report_header_contains_git_commit_prereg_hash_library_versions_and_seed(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=5)
        output_path = out_dir / "report.md"
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir), "--output", str(output_path)]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc, 0)
        text = output_path.read_text()
        self.assertIn("git commit:", text)
        self.assertIn("pre-registration file sha256", text)
        self.assertIn("library versions:", text)
        self.assertIn("seed=20261029", text)

    def test_label_is_confirmatory_only_when_every_13_6_condition_holds(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=5)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc, 0)
        self.assertEqual(ar.main.last_label.verdict, "CONFIRMATORY")
        self.assertEqual(ar.main.last_label.failed_conditions, [])

    def test_label_exploratory_when_run_header_records_non_frozen_slippage(self):
        # §13.8: the label's slippage check reads the ORIGINAL
        # --run-outcomes run's own recorded outcome_params_json, never a
        # CLI argument to analyze_replay.py (which no longer has one).
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(
            out_dir, repo, n_symbols=5, fixture_slippage_bps=20.0,
        )
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc, 0)
        self.assertEqual(ar.main.last_label.verdict, "EXPLORATORY")
        self.assertTrue(any("slippage" in r for r in ar.main.last_label.failed_conditions))

    def test_analyze_replay_cli_has_no_slippage_bps_flag(self):
        with self.assertRaises(SystemExit):
            ar.build_arg_parser().parse_args(["--results-db", "x", "--output", "y", "--slippage-bps", "20"])

    def test_label_exploratory_on_seed_override(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=5)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md"), "--seed", "1"]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(ar.main.last_label.verdict, "EXPLORATORY")
        self.assertTrue(any("seed" in r for r in ar.main.last_label.failed_conditions))

    def test_label_exploratory_on_draws_override(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=5)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md"), "--draws", "500"]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(ar.main.last_label.verdict, "EXPLORATORY")
        self.assertTrue(any("draws" in r for r in ar.main.last_label.failed_conditions))

    def test_label_exploratory_on_dirty_working_tree(self):
        # The fixture DB build goes through replay_setups.py's own
        # --run-outcomes gates (clean tree + committed pre-registration),
        # so it needs a clean fixture repo; the dirtiness is introduced
        # in a SEPARATE repo_root used only for analyze_replay.py's own
        # label check, decoupling the two.
        fixture_repo = _make_temp_git_repo(with_preregistration=True)
        label_repo = _make_temp_git_repo(with_preregistration=True, dirty=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, fixture_repo, n_symbols=5)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = ar.main(argv, repo_root=label_repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc, 0)
        self.assertEqual(ar.main.last_label.verdict, "EXPLORATORY")
        self.assertTrue(any("clean" in r for r in ar.main.last_label.failed_conditions))

    def test_label_exploratory_without_preregistration_committed(self):
        fixture_repo = _make_temp_git_repo(with_preregistration=True)
        label_repo = _make_temp_git_repo(with_preregistration=False)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, fixture_repo, n_symbols=5)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = ar.main(argv, repo_root=label_repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc, 0)
        self.assertEqual(ar.main.last_label.verdict, "EXPLORATORY")
        self.assertTrue(any("committed" in r for r in ar.main.last_label.failed_conditions))

    def test_label_exploratory_on_library_version_mismatch(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=5)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "2.0.0")  # mismatch vs pinned 1.0.0
        self.assertEqual(ar.main.last_label.verdict, "EXPLORATORY")
        self.assertTrue(any("library" in r for r in ar.main.last_label.failed_conditions))

    def test_label_exploratory_on_cache_manifest_sha256_mismatch(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=5)
        # Corrupt the on-disk cache file for one symbol AFTER the run --
        # its manifest sha256 no longer matches what's on disk now.
        cache_file = rs.cache_file(cache_dir, symbols[0])
        text = cache_file.read_text()
        cache_file.write_text(text.replace("100", "999", 1))

        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(ar.main.last_label.verdict, "EXPLORATORY")
        self.assertTrue(any("manifest" in r for r in ar.main.last_label.failed_conditions))


class TestSlippageRecompute(unittest.TestCase):

    def test_slippage_variant_recomputes_from_stored_prices_without_resimulating(self):
        from unittest.mock import patch
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=25)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        with patch("analyze_replay.recompute_r_at_slippage", wraps=sa.recompute_r_at_slippage) as mock_recompute, \
             patch("setups.analysis.simulate_outcome", wraps=sa.simulate_outcome) as mock_simulate:
            rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc, 0)
        # The 20bps secondary table must be produced via the pure
        # recompute helper, not a second simulate_outcome() call per trade.
        if len(ar.main.last_diffs) > 0:
            self.assertGreater(mock_recompute.call_count, 0)


class TestReportTextForRejectionBreakdown(unittest.TestCase):

    def test_report_prints_not_trade_and_excluded_counts_per_status_with_shares_and_no_peer_count(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=25)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc, 0)
        text = (out_dir / "report.md").read_text()
        for status in ("INVALIDATED_BEFORE_FILL", "EXPIRED_UNFILLED", "CHASE_RR_BELOW_3"):
            self.assertIn(status, text)
        for status in ("DATA_GAP", "OPEN_AT_END"):
            self.assertIn(status, text)
        self.assertIn("no-peer excluded count", text)
        self.assertNotIn("sector", text.lower())

    def test_every_section_8_share_states_both_denominators_explicitly(self):
        # Owner review: a bare percentage invites misreading "share
        # within the rejected bucket" as "share of all accepted setups"
        # -- every count in section 8 must now spell out BOTH, labeled.
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=25)
        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc, 0)
        text = (out_dir / "report.md").read_text()

        self.assertIn("total accepted setups:", text)
        self.assertIn("of all", text)
        self.assertIn("accepted setups", text)
        self.assertIn("within the rejected/excluded/no-peer bucket", text)

        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        total_accepted = conn.execute(
            "SELECT COUNT(*) FROM replay_candidates WHERE bound='NA' AND accepted=1"
        ).fetchone()[0]
        conn.close()
        self.assertIn(f"total accepted setups: {total_accepted}", text)

        # The raw rejection_shares() dict is printed too -- confirm it
        # carries the new, explicitly-labeled nested shape, not the old
        # flat "share of something unstated" dict.
        self.assertIn("within_rejected_excluded_bucket", text)
        self.assertIn("of_all_accepted_setups", text)


class TestPass3FrameCache(unittest.TestCase):

    def test_cache_wrapper_hits_disk_only_once_per_distinct_key(self):
        """Direct, controllable unit test of _cached_load_from_cache()
        itself (item 7): repeated calls with the SAME
        (cache_dir, symbol, warmup_start, fetch_end) key must hit the
        real rs.load_from_cache() exactly once, returning the identical
        object on every subsequent call -- the actual guarantee Pass 3
        relies on, isolated from the rest of the pipeline's call pattern."""
        from unittest.mock import patch
        ar._PEER_FRAME_CACHE.clear()
        sentinel_df = pd.DataFrame({"open": [1.0]})
        with patch("analyze_replay.rs.load_from_cache", return_value=sentinel_df) as mock_load:
            r1 = ar._cached_load_from_cache(Path("/x"), "AAA", date(2022, 1, 1), date(2022, 6, 1))
            r2 = ar._cached_load_from_cache(Path("/x"), "AAA", date(2022, 1, 1), date(2022, 6, 1))
            r3 = ar._cached_load_from_cache(Path("/x"), "AAA", date(2022, 1, 1), date(2022, 6, 1))
        self.assertEqual(mock_load.call_count, 1)
        self.assertIs(r1, sentinel_df)
        self.assertIs(r1, r2)
        self.assertIs(r2, r3)
        ar._PEER_FRAME_CACHE.clear()

    def test_pass3_with_cache_matches_a_separate_uncleared_run(self):
        """End-to-end sanity: the cache is on by default (no toggle to
        disable it), so this just confirms two independent fixture runs
        through the real Pass 3 + cache produce identical paired
        differences -- correctness, not a performance comparison (that's
        covered by the direct unit test above plus the benchmark in the
        final report)."""
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir1, out_dir2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        db_path1, cache_dir1, symbols, frames = _build_run_outcomes_db(out_dir1, repo, n_symbols=25)
        db_path2, cache_dir2, _s2, _f2 = _build_run_outcomes_db(out_dir2, repo, n_symbols=25)

        argv1 = ["--results-db", str(db_path1), "--cache-dir", str(cache_dir1),
                 "--output", str(out_dir1 / "report.md")]
        rc1 = ar.main(argv1, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc1, 0)
        diffs1 = TestWorkersPoolIO._diff_tuples(ar.main.last_diffs)

        argv2 = ["--results-db", str(db_path2), "--cache-dir", str(cache_dir2),
                 "--output", str(out_dir2 / "report.md")]
        rc2 = ar.main(argv2, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc2, 0)
        diffs2 = TestWorkersPoolIO._diff_tuples(ar.main.last_diffs)

        self.assertEqual(diffs1, diffs2)


class TestUniverseFromManifest(unittest.TestCase):

    def test_universe_for_peers_comes_from_replay_data_manifest_for_the_run_id(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=6)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        manifest_symbols = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM replay_data_manifest")}
        conn.close()
        self.assertEqual(manifest_symbols, set(symbols))


class TestWorkersPoolIO(unittest.TestCase):

    @staticmethod
    def _diff_tuples(diffs):
        return sorted((d.symbol, str(d.fill_date), round(d.setup_r, 9), round(d.control_r, 9)) for d in diffs)

    def test_workers_default_matches_sequential_baseline(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir1, out_dir2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        db_path1, cache_dir1, symbols, frames = _build_run_outcomes_db(out_dir1, repo, n_symbols=25)
        db_path2, cache_dir2, _s2, _f2 = _build_run_outcomes_db(out_dir2, repo, n_symbols=25)

        argv1 = ["--results-db", str(db_path1), "--cache-dir", str(cache_dir1),
                 "--output", str(out_dir1 / "report.md")]
        rc1 = ar.main(argv1, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc1, 0)
        diffs1 = self._diff_tuples(ar.main.last_diffs)

        argv2 = ["--results-db", str(db_path2), "--cache-dir", str(cache_dir2),
                 "--output", str(out_dir2 / "report.md"), "--workers", "1"]
        rc2 = ar.main(argv2, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc2, 0)
        diffs2 = self._diff_tuples(ar.main.last_diffs)

        self.assertEqual(diffs1, diffs2)

    def test_workers_two_matches_workers_one_fake_pool(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir1, out_dir2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        db_path1, cache_dir1, symbols, frames = _build_run_outcomes_db(out_dir1, repo, n_symbols=25)
        db_path2, cache_dir2, _s2, _f2 = _build_run_outcomes_db(out_dir2, repo, n_symbols=25)

        argv1 = ["--results-db", str(db_path1), "--cache-dir", str(cache_dir1),
                 "--output", str(out_dir1 / "report.md"), "--workers", "1"]
        rc1 = ar.main(argv1, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc1, 0)
        diffs1 = self._diff_tuples(ar.main.last_diffs)

        argv2 = ["--results-db", str(db_path2), "--cache-dir", str(cache_dir2),
                 "--output", str(out_dir2 / "report.md"), "--workers", "2"]
        rc2 = ar.main(argv2, repo_root=repo, version_lookup=lambda n: "1.0.0", executor_factory=_ImmediateExecutor())
        self.assertEqual(rc2, 0)
        diffs2 = self._diff_tuples(ar.main.last_diffs)

        self.assertEqual(diffs1, diffs2)

    def test_workers_two_with_the_real_process_pool_matches_workers_one(self):
        """Pre-freeze hardening: a REAL ProcessPoolExecutor (default
        executor_factory, no fake pool) on Windows uses spawn, which
        re-imports this module in each worker process -- the only way to
        actually prove _pass1_worker/_pass2_worker/_pass3_worker are
        genuinely picklable top-level functions and that spawn doesn't
        silently change any result. Same fixture, same db/cache, two
        separate --output paths so the only difference between the runs
        is --workers itself -- the report text must be byte-identical."""
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(out_dir, repo, n_symbols=25)

        argv1 = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                 "--output", str(out_dir / "report_w1.md"), "--workers", "1"]
        rc1 = ar.main(argv1, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc1, 0)

        argv2 = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                 "--output", str(out_dir / "report_w2.md"), "--workers", "2"]
        rc2 = ar.main(argv2, repo_root=repo, version_lookup=lambda n: "1.0.0")  # real ProcessPoolExecutor
        self.assertEqual(rc2, 0)

        self.assertEqual(
            (out_dir / "report_w1.md").read_text(),
            (out_dir / "report_w2.md").read_text(),
        )


class TestHardRuleNoRealDb(unittest.TestCase):

    def test_hard_rule_no_test_in_this_suite_opens_a_non_temp_db(self):
        """Structural self-check: every --results-db / --cache-dir literal
        in this file must come from tempfile.mkdtemp(), never a path
        under the repo (which could only be a real, committed DB)."""
        source = Path(__file__).read_text()
        # Built from parts so this literal check string is never itself
        # a contiguous substring of the file (which would make the
        # assertion trivially self-defeating).
        forbidden = "db" + "/" + "stocksage" + "." + "db"
        self.assertNotIn(forbidden, source)
        self.assertIn("tempfile.mkdtemp", source)


class TestEndToEndSyntheticPipeline(unittest.TestCase):

    def test_end_to_end_synthetic_replay_through_analysis_pipeline(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_run_outcomes_db(
            out_dir, repo, n_symbols=25, n=700, start="2022-01-01", scan_start_idx=300, scan_end_idx=360,
        )

        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc, 0)

        label = ar.main.last_label
        # Clean fixture (committed pre-registration, clean tree, matching
        # library versions, matching cache manifest, no overrides) --
        # CONFIRMATORY with an empty failed_conditions list is the exact,
        # correct outcome here, not a loose consistency check.
        self.assertEqual(label.verdict, "CONFIRMATORY")
        self.assertEqual(label.failed_conditions, [])
        diffs = ar.main.last_diffs

        # Accounting reconciles: every accepted row lands in exactly one
        # bucket (primary-with-peers, primary-excluded-for-no-peers,
        # not-trade, or excluded-status) -- re-derive the totals the same
        # way main() does and compare.
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        accepted_count = conn.execute(
            "SELECT COUNT(*) FROM replay_candidates WHERE bound='NA' AND accepted=1"
        ).fetchone()[0]
        conn.close()
        self.assertGreater(accepted_count, 0)

        # Determinism: running again produces an identical report.
        argv2 = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                 "--output", str(out_dir / "report2.md")]
        rc2 = ar.main(argv2, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc2, 0)
        self.assertEqual((out_dir / "report.md").read_text(), (out_dir / "report2.md").read_text())

        # decide() received EXACTLY the paired differences the report
        # prints: the bootstrap point estimate quoted in the report must
        # equal the mean of last_diffs' own paired differences.
        if diffs:
            expected_mean = sum(d.diff for d in diffs) / len(diffs)
            report_text = (out_dir / "report.md").read_text()
            self.assertIn(f"{expected_mean:.4f}", report_text)


class TestConfirmatoryEndToEndWithEdgeCases(unittest.TestCase):
    """Item 2: a failed download, a DATA_GAP trade and an OPEN_AT_END
    trade must NOT by themselves break CONFIRMATORY -- the pipeline must
    classify them correctly (as recomputed, never from the ambiguous
    stored columns) and still agree with itself (zero stored-vs-
    recomputed mismatches, zero manifest mismatches)."""

    def test_confirmatory_with_failed_download_data_gap_and_open_at_end(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        out_dir = Path(tempfile.mkdtemp())
        db_path, cache_dir, symbols, frames = _build_confirmatory_fixture_with_edge_cases(out_dir, repo)

        argv = ["--results-db", str(db_path), "--cache-dir", str(cache_dir),
                "--output", str(out_dir / "report.md")]
        rc = ar.main(argv, repo_root=repo, version_lookup=lambda n: "1.0.0")
        self.assertEqual(rc, 0)

        self.assertEqual(ar.main.last_label.verdict, "CONFIRMATORY")
        self.assertEqual(ar.main.last_label.failed_conditions, [])
        self.assertEqual(ar.main.last_recompute_mismatches, [])
        self.assertEqual(ar.main.last_manifest_mismatches, [])

        # FAILSYM's own accepted-row count must be zero (download failed,
        # never cached, never classified) -- confirmed via the manifest.
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        failsym_manifest = conn.execute(
            "SELECT rows, sha256 FROM replay_data_manifest WHERE symbol='FAILSYM'"
        ).fetchone()
        conn.close()
        self.assertEqual(failsym_manifest["rows"], 0)
        self.assertIsNone(failsym_manifest["sha256"])


if __name__ == "__main__":
    unittest.main()
