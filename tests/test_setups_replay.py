"""Tests for setups/replay.py (pure core) and scripts/replay_setups.py (I/O)
-- the replay harness, docs/SETUP_SPEC.md §13, rollout step 5.

All price data is synthetic (no network); the I/O tests use an injected
fake downloader, temp directories, and temp sqlite files -- never the real
db/stocksage.db, never a real yfinance call. Git-gate tests (item 1 of the
approved implementation conditions) create their OWN temporary git
repository rather than touching this repo's real working tree.
"""
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from setups.calendar_utils import advance_us_trading_days  # noqa: E402
from setups.definition import SetupDefinition  # noqa: E402
from setups.outcomes import simulate_outcome  # noqa: E402
from setups.params import OutcomeParams, SetupParams, V1_OUTCOME_PARAMS, V1_PARAMS  # noqa: E402
from setups.reason_codes import (  # noqa: E402
    BARRIER_STOP,
    BARRIER_TARGET,
    BARRIER_TIMEOUT_30,
    CHASE_RR_BELOW_3,
    DATA_GAP,
    EXPIRED_UNFILLED,
    FILLED_STOPPED_SAME_DAY,
    INVALIDATED_BEFORE_FILL,
    MISSING_OHLCV,
    OPEN_AT_END,
)
from setups.replay import (  # noqa: E402
    DUPLICATE_SETUP,
    SUPPRESSED,
    CandidateRow,
    _max_possible_resolution_date,
    build_regime_by_date,
    run_replay_core,
)


# ── Shared fixtures ──────────────────────────────────────────────────────

def _ramp(n, direction):
    return [direction] * n


def _frame_from_close(close, start="2026-01-01"):
    close = np.array(close, dtype=float)
    n = len(close)
    index = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame(
        {
            "open": close.copy(), "high": close + 1.0, "low": close - 1.0,
            "close": close, "volume": np.full(n, 2_000_000.0),
        },
        index=pd.DatetimeIndex(index, name="Date"),
    )


def _build_golden_close(
    lead_n=185, lead_lo=90, lead_hi=117, res_extra=44, mid_decline=30,
    sh_rise=4, pb_depth=3, recover=2,
):
    """A hand-tuned, deterministic uptrend + pullback shape that clears
    EVERY gate in evaluate_candidate() + define_setup() at its last bar:
    trend filter, liquidity, swing structure, pullback depth/recency,
    price position, stop cap, 3:1 R:R, extension veto, and regime (BULL).
    Confirmed empirically (not re-derived here): 268 bars, last close=134,
    SH=(…, 136.0), SL=(…, 131.0), trigger=136, stop=130, target=162,
    rr=4.333, atr=2.0. Every bar's high/low is close+-1 and every
    consecutive close differs by exactly 1, so ATR is pinned at 2.0
    throughout regardless of the overall swing's magnitude -- what makes
    the resistance gap tunable without inflating ATR."""
    lead = np.linspace(lead_lo, lead_hi, lead_n).round().astype(int).tolist()
    peak1 = lead_hi + res_extra
    mid = peak1 - mid_decline
    sh = mid + sh_rise
    local = (
        _ramp(peak1 - lead[-1], 1)
        + _ramp(peak1 - mid, -1)
        + _ramp(sh - mid, 1)
        + _ramp(pb_depth, -1)
        + _ramp(recover, 1)
    )
    close = [float(x) for x in lead]
    for step in local:
        close.append(close[-1] + step)
    return close


def _spy_close(n):
    return list(np.linspace(400.0, 420.0, n))


GOLDEN_CLOSE = _build_golden_close()  # 268 bars, last close=134.0


def _golden_frames(extra_flat_sessions=0, start="2026-01-01"):
    """frames={"AAA": df}, spy_df -- the golden series extended with
    `extra_flat_sessions` more bars holding at the last close (price
    sits between SL and SH: the same-setup-redefined-on-consecutive-
    nights scenario)."""
    close = GOLDEN_CLOSE + [GOLDEN_CLOSE[-1]] * extra_flat_sessions
    df = _frame_from_close(close, start=start)
    spy = _frame_from_close(_spy_close(len(close)), start=start)
    return {"AAA": df}, spy, df


def _scan_dates_from(df, start_extra, count):
    """The `count` scan dates at positions
    len(GOLDEN_CLOSE)-1+start_extra .. +count-1 of df's index."""
    base = len(GOLDEN_CLOSE) - 1
    return [df.index[base + start_extra + i].date() for i in range(count)]


SMALL_WINDOW_PARAMS = SetupParams(validity_window_sessions=2)


class TestRowPerSymbolPerDate(unittest.TestCase):

    def test_run_replay_core_returns_a_row_per_symbol_per_date_when_rejected_early(self):
        short_df = _frame_from_close([100.0] * 30)  # far below MIN_HISTORY_BARS
        spy = _frame_from_close(_spy_close(30))
        scan_dates = [short_df.index[-3].date(), short_df.index[-2].date(), short_df.index[-1].date()]
        rows = run_replay_core({"ZZZ": short_df}, spy, scan_dates)
        self.assertEqual(len(rows), 3)
        for row, scan_date in zip(rows, scan_dates):
            self.assertEqual(row.symbol, "ZZZ")
            self.assertEqual(row.scan_date, scan_date)
            self.assertFalse(row.accepted)
            self.assertIsNotNone(row.reason_code)

    def test_every_row_carries_regime_even_when_rejected_before_define_setup_runs(self):
        # Same start/length as spy so their dates align -- short of
        # MIN_HISTORY_BARS(220) so evaluate_candidate rejects before
        # define_setup (and its own regime computation) ever runs, but
        # long enough (>=150) for a real BULL regime on the SPY side.
        short_df = _frame_from_close([100.0] * 200)
        spy = _frame_from_close(_spy_close(200))
        scan_date = short_df.index[-1].date()
        rows = run_replay_core({"ZZZ": short_df}, spy, [scan_date])
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].accepted)
        self.assertEqual(rows[0].regime, "BULL")


class TestAcceptedSetupRow(unittest.TestCase):

    def test_accepted_setup_row_fields_match_define_setup_output(self):
        frames, spy, df = _golden_frames()
        scan_date = df.index[-1].date()
        rows = run_replay_core(frames, spy, [scan_date], params=V1_PARAMS)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row.accepted)
        self.assertIsNone(row.reason_code)
        self.assertEqual(row.regime, "BULL")
        self.assertEqual(row.trigger, 136.0)
        self.assertEqual(row.stop, 130.0)
        self.assertEqual(row.target, 162.0)
        self.assertAlmostEqual(row.rr, 4.333, places=2)
        self.assertEqual(row.atr, 2.0)
        self.assertEqual(row.setup_identity, ("AAA", row.sh_date, row.sl_date))
        self.assertIsNone(row.fill_date)  # counts-only: no outcome fields


class TestDeterminism(unittest.TestCase):

    def test_run_replay_core_is_deterministic_across_two_calls(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=5)
        scan_dates = _scan_dates_from(df, 0, 5)
        rows1 = run_replay_core(frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS)
        rows2 = run_replay_core(frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS)
        self.assertEqual(rows1, rows2)


class TestSuppressionBoundary(unittest.TestCase):
    """SMALL_WINDOW_PARAMS (validity_window_sessions=2) keeps this fast:
    extra=0 accepts; extra=1 (< blocked_until) is suppressed; extra=2
    (== blocked_until, strict '<' lets it through) re-evaluates and finds
    the SAME SH/SL identity -> DUPLICATE_SETUP, not a fresh accept."""

    def test_suppression_blocks_symbol_strictly_before_blocked_until(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=3)
        scan_dates = _scan_dates_from(df, 0, 3)
        rows = run_replay_core(frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS)
        self.assertTrue(rows[0].accepted)
        self.assertEqual(rows[1].reason_code, SUPPRESSED)
        self.assertFalse(rows[1].accepted)

    def test_suppression_allows_new_setup_on_the_scan_date_equal_to_blocked_until(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=3)
        scan_dates = _scan_dates_from(df, 0, 3)
        rows = run_replay_core(frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS)
        self.assertNotEqual(rows[2].reason_code, SUPPRESSED)

    def test_suppression_blocks_symbol_until_validity_end_in_counts_only_upper_bound(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=3)
        scan_dates = _scan_dates_from(df, 0, 3)
        rows = run_replay_core(
            frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS,
            counts_only_block_extra_sessions=0,
        )
        self.assertEqual(rows[0].validity_end, scan_dates[2])  # 2-session window
        self.assertEqual(rows[1].reason_code, SUPPRESSED)

    def test_suppression_blocks_symbol_until_validity_end_plus_timeout_in_counts_only_lower_bound(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=6)
        scan_dates = _scan_dates_from(df, 0, 6)
        outcome_params = replace(V1_OUTCOME_PARAMS, timeout_sessions=2)
        rows = run_replay_core(
            frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS,
            outcome_params=outcome_params,
            counts_only_block_extra_sessions=outcome_params.timeout_sessions,
        )
        # UPPER bound would unblock at scan_dates[2] (validity_end);
        # LOWER adds 2 more sessions -> still suppressed at [2] and [3].
        self.assertTrue(rows[0].accepted)
        for i in (1, 2, 3):
            self.assertEqual(rows[i].reason_code, SUPPRESSED, f"index {i}")
        self.assertNotEqual(rows[4].reason_code, SUPPRESSED)

    def test_suppression_blocks_symbol_until_resolved_date_in_run_outcomes_mode(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=3)
        scan_dates = _scan_dates_from(df, 0, 3)
        rows = run_replay_core(
            frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS, compute_outcomes=True,
        )
        self.assertTrue(rows[0].accepted)
        self.assertEqual(rows[0].resolved_date, scan_dates[2])  # never fills -> EXPIRED_UNFILLED -> validity_end
        self.assertEqual(rows[1].reason_code, SUPPRESSED)
        self.assertEqual(rows[2].reason_code, DUPLICATE_SETUP)

    def test_suppressed_night_emits_a_row_with_suppressed_reason_not_silently_dropped(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=3)
        scan_dates = _scan_dates_from(df, 0, 3)
        rows = run_replay_core(frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS)
        self.assertEqual(len(rows), 3)  # suppressed night is still a row
        self.assertEqual(rows[1].symbol, "AAA")
        self.assertEqual(rows[1].scan_date, scan_dates[1])


class TestDuplicateIdentity(unittest.TestCase):

    def test_duplicate_setup_identity_rejected_and_not_counted(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=3)
        scan_dates = _scan_dates_from(df, 0, 3)
        rows = run_replay_core(frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS)
        accepted = [r for r in rows if r.accepted]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rows[2].reason_code, DUPLICATE_SETUP)
        self.assertFalse(rows[2].accepted)

    def test_duplicate_setup_identity_after_expired_unfilled_still_rejected(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=3)
        scan_dates = _scan_dates_from(df, 0, 3)
        rows = run_replay_core(
            frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS, compute_outcomes=True,
        )
        accepted = [r for r in rows if r.accepted]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rows[2].reason_code, DUPLICATE_SETUP)


class TestDataGapOpenAtEndFallback(unittest.TestCase):
    """Unit-level: exercises _max_possible_resolution_date() directly
    against real DATA_GAP/OPEN_AT_END OutcomeResults from simulate_outcome(),
    not the full define_setup() pipeline -- isolates the fallback logic."""

    def _setup(self, scan_date, validity_end):
        return SetupDefinition(
            sh_date=scan_date, sh_price=110.0, sl_date=scan_date, sl_price=100.0,
            trigger=110.0, stop=95.0, target=155.0, risk=15.0, rr=3.0, atr=1.0,
            regime="BULL", validity_end=validity_end, scan_date=scan_date,
        )

    def test_data_gap_blocks_until_validity_end_when_never_filled(self):
        scan_date = date(2026, 3, 2)
        validity_end = advance_us_trading_days(scan_date, 5)
        setup = self._setup(scan_date, validity_end)
        # NaN bar inside the validity window -> DATA_GAP, never filled.
        dates = [advance_us_trading_days(scan_date, 1)]
        bars = pd.DataFrame(
            {"open": [float("nan")], "high": [float("nan")], "low": [float("nan")], "close": [float("nan")]},
            index=pd.DatetimeIndex(dates),
        )
        result = simulate_outcome(setup, bars)
        self.assertEqual(result.status, DATA_GAP)
        self.assertIsNone(result.resolved_date)
        fallback = _max_possible_resolution_date(setup, result, V1_OUTCOME_PARAMS)
        self.assertEqual(fallback, validity_end)

    def test_data_gap_blocks_until_fill_date_plus_timeout_sessions_when_filled(self):
        scan_date = date(2026, 3, 2)
        validity_end = advance_us_trading_days(scan_date, 5)
        setup = self._setup(scan_date, validity_end)
        fill_date = advance_us_trading_days(scan_date, 1)
        gap_date = advance_us_trading_days(fill_date, 1)
        dates = [fill_date, gap_date]
        bars = pd.DataFrame(
            {
                "open": [105.0, float("nan")], "high": [112.0, float("nan")],
                "low": [104.0, float("nan")], "close": [108.0, float("nan")],
            },
            index=pd.DatetimeIndex(dates),
        )
        result = simulate_outcome(setup, bars)
        self.assertEqual(result.status, DATA_GAP)
        self.assertEqual(result.fill_date, fill_date)
        self.assertIsNone(result.resolved_date)
        outcome_params = replace(V1_OUTCOME_PARAMS, timeout_sessions=10)
        fallback = _max_possible_resolution_date(setup, result, outcome_params)
        self.assertEqual(fallback, advance_us_trading_days(fill_date, 10))

    def test_open_at_end_uses_same_max_possible_resolution_fallback_as_data_gap(self):
        scan_date = date(2026, 3, 2)
        validity_end = advance_us_trading_days(scan_date, 5)
        setup = self._setup(scan_date, validity_end)
        bars = pd.DataFrame(
            {"open": [], "high": [], "low": [], "close": []},
            index=pd.DatetimeIndex([]),
        )
        result = simulate_outcome(setup, bars)
        self.assertEqual(result.status, OPEN_AT_END)
        self.assertIsNone(result.resolved_date)
        fallback = _max_possible_resolution_date(setup, result, V1_OUTCOME_PARAMS)
        self.assertEqual(fallback, validity_end)


class TestNoLookAhead(unittest.TestCase):

    def test_no_look_ahead_corrupting_bars_after_date_d_leaves_earlier_rows_identical(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=2)
        scan_dates = _scan_dates_from(df, 0, 2)
        baseline = run_replay_core(frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS)

        corrupted_close = list(df["close"])
        d_pos = len(GOLDEN_CLOSE) - 1 + 1  # corrupt everything after scan_dates[1]
        for i in range(d_pos + 1, len(corrupted_close)):
            corrupted_close[i] = 999999.0
        corrupted_df = _frame_from_close(corrupted_close, start="2026-01-01")
        corrupted = run_replay_core({"AAA": corrupted_df}, spy, scan_dates, params=SMALL_WINDOW_PARAMS)

        self.assertEqual(baseline, corrupted)


class TestNoMutation(unittest.TestCase):

    def test_run_replay_core_never_mutates_its_inputs(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=2)
        scan_dates = _scan_dates_from(df, 0, 2)
        frames_before = {k: v.copy(deep=True) for k, v in frames.items()}
        spy_before = spy.copy(deep=True)

        run_replay_core(frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS, compute_outcomes=True)

        for symbol, before in frames_before.items():
            pd.testing.assert_frame_equal(frames[symbol], before)
        pd.testing.assert_frame_equal(spy, spy_before)


class TestResolvedDate(unittest.TestCase):
    """OutcomeResult.resolved_date -- setups/outcomes.py's small addition."""

    def _setup(self, trigger=110.0, stop=95.0, sl_price=100.0, target=155.0,
               scan_date=date(2026, 3, 2), validity_sessions=5):
        validity_end = advance_us_trading_days(scan_date, validity_sessions)
        risk = trigger - stop
        rr = (target - trigger) / risk
        return SetupDefinition(
            sh_date=scan_date, sh_price=trigger, sl_date=scan_date, sl_price=sl_price,
            trigger=trigger, stop=stop, target=target, risk=risk, rr=rr, atr=1.0,
            regime="BULL", validity_end=validity_end, scan_date=scan_date,
        )

    def _bars(self, dates, specs):
        return pd.DataFrame(
            {
                "open": [s[0] for s in specs], "high": [s[1] for s in specs],
                "low": [s[2] for s in specs], "close": [s[3] for s in specs],
            },
            index=pd.DatetimeIndex(dates),
        )

    def test_resolved_date_is_exit_date_for_barrier_statuses(self):
        setup = self._setup()
        d1 = advance_us_trading_days(setup.scan_date, 1)
        d2 = advance_us_trading_days(d1, 1)
        dates = [d1, d2]
        fill_bar = (105.0, 112.0, 104.0, 108.0)  # fills at trigger(110), no same-day stop
        target_bar = (140.0, 158.0, 138.0, 150.0)  # hits target(155)
        result = simulate_outcome(setup, self._bars(dates, [fill_bar, target_bar]))
        self.assertEqual(result.status, BARRIER_TARGET)
        self.assertEqual(result.resolved_date, result.exit_date)
        self.assertEqual(result.resolved_date, d2)

    def test_resolved_date_is_fill_date_for_chase_rr_below_3(self):
        setup = self._setup()
        d1 = advance_us_trading_days(setup.scan_date, 1)
        bar = (130.0, 135.0, 128.0, 132.0)  # gap fill, RR well below 3
        result = simulate_outcome(setup, self._bars([d1], [bar]))
        self.assertEqual(result.status, CHASE_RR_BELOW_3)
        self.assertEqual(result.resolved_date, result.fill_date)
        self.assertEqual(result.resolved_date, d1)

    def test_resolved_date_is_the_breaching_session_for_invalidated_before_fill(self):
        setup = self._setup()
        d1 = advance_us_trading_days(setup.scan_date, 1)
        bar = (100.0, 108.0, 93.0, 98.0)  # high<trigger, low<=stop(95)
        result = simulate_outcome(setup, self._bars([d1], [bar]))
        self.assertEqual(result.status, INVALIDATED_BEFORE_FILL)
        self.assertEqual(result.resolved_date, d1)

    def test_resolved_date_is_validity_end_for_expired_unfilled(self):
        setup = self._setup()
        dates = [advance_us_trading_days(setup.scan_date, i) for i in range(1, 6)]
        benign = (100.0, 105.0, 98.0, 102.0)
        result = simulate_outcome(setup, self._bars(dates, [benign] * 5))
        self.assertEqual(result.status, EXPIRED_UNFILLED)
        self.assertEqual(result.resolved_date, setup.validity_end)

    def test_resolved_date_is_none_for_data_gap_and_open_at_end(self):
        setup = self._setup()
        empty = pd.DataFrame({"open": [], "high": [], "low": [], "close": []}, index=pd.DatetimeIndex([]))
        open_at_end = simulate_outcome(setup, empty)
        self.assertEqual(open_at_end.status, OPEN_AT_END)
        self.assertIsNone(open_at_end.resolved_date)

        d1 = advance_us_trading_days(setup.scan_date, 1)
        nan_bar = (float("nan"), float("nan"), float("nan"), float("nan"))
        data_gap = simulate_outcome(setup, self._bars([d1], [nan_bar]))
        self.assertEqual(data_gap.status, DATA_GAP)
        self.assertIsNone(data_gap.resolved_date)


class TestIsolation(unittest.TestCase):

    def test_setups_replay_module_never_imports_agent_core(self):
        result = subprocess.run(
            [sys.executable, "-c",
             "import setups.replay, sys; "
             "assert 'agent.core' not in sys.modules, sys.modules.keys()"],
            capture_output=True, text=True, timeout=30, cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


# ── I/O layer (scripts/replay_setups.py) ─────────────────────────────────

import sqlite3  # noqa: E402
import shutil  # noqa: E402

import replay_setups as rs  # noqa: E402


def _run_git(args, cwd):
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result


def _make_temp_git_repo(with_preregistration=False, requirements_content="placeholder==1.0.0\n"):
    tmp = Path(tempfile.mkdtemp())
    _run_git(["init"], tmp)
    _run_git(["config", "user.email", "test@example.com"], tmp)
    _run_git(["config", "user.name", "Test"], tmp)
    (tmp / "README.md").write_text("temp repo for git-gate tests\n")
    (tmp / "requirements.txt").write_text(requirements_content)
    if with_preregistration:
        (tmp / "docs").mkdir(exist_ok=True)
        (tmp / "docs" / "PREREGISTRATION_SETUPS_V1.md").write_text("frozen pre-registration\n")
    _run_git(["add", "-A"], tmp)
    _run_git(["commit", "-m", "init"], tmp)
    return tmp


def _fixed_frame(start, end, close_value, volume=1_000_000.0):
    dates = pd.bdate_range(start=start, end=end)
    n = len(dates)
    close = np.full(n, float(close_value))
    return pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
         "volume": np.full(n, volume)},
        index=pd.DatetimeIndex(dates),
    )


def _fake_downloader(frames_by_symbol, call_log=None):
    def _dl(symbols, start, end):
        if call_log is not None:
            call_log.append(tuple(symbols))
        return {s: frames_by_symbol.get(s) for s in symbols}
    return _dl


SPY_WIDE = _fixed_frame("2020-01-01", "2026-12-31", 400.0)


class TestGitGates(unittest.TestCase):

    def test_preregistration_in_head_detection(self):
        without = _make_temp_git_repo(with_preregistration=False)
        self.assertFalse(rs.check_preregistration_committed(without))
        with_it = _make_temp_git_repo(with_preregistration=True)
        self.assertTrue(rs.check_preregistration_committed(with_it))

    def test_working_tree_clean_detection(self):
        repo = _make_temp_git_repo()
        clean, _ = rs.check_working_tree_clean(repo)
        self.assertTrue(clean)
        (repo / "dirty.txt").write_text("uncommitted\n")
        clean2, output = rs.check_working_tree_clean(repo)
        self.assertFalse(clean2)
        self.assertIn("dirty.txt", output)

    def test_output_path_inside_repo_working_tree_is_refused(self):
        repo = _make_temp_git_repo()
        with self.assertRaises(RuntimeError):
            rs.resolve_guarded_path(str(repo / "sub" / "out.db"), repo, "--output")

    def test_cache_dir_inside_repo_working_tree_is_refused(self):
        repo = _make_temp_git_repo()
        with self.assertRaises(RuntimeError):
            rs.resolve_guarded_path(str(repo / "cache"), repo, "--cache-dir")

    def test_cache_dir_defaults_outside_the_repo(self):
        self.assertFalse(rs.path_is_inside(rs.DEFAULT_CACHE_DIR, ROOT))

    def test_run_outcomes_refuses_without_preregistration_file(self):
        repo = _make_temp_git_repo(with_preregistration=False)
        out_dir = Path(tempfile.mkdtemp())
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("AAA\n")
        argv = [
            "--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-03",
            "--output", str(out_dir / "run.db"), "--cache-dir", str(out_dir / "cache"),
            "--run-outcomes",
        ]
        downloader = _fake_downloader({"AAA": _fixed_frame("2026-05-01", "2026-06-10", 100.0), "SPY": SPY_WIDE})
        rc = rs.main(argv, downloader=downloader, repo_root=repo)
        self.assertEqual(rc, 1)

    def test_run_outcomes_refuses_with_dirty_working_tree(self):
        repo = _make_temp_git_repo(with_preregistration=True)
        (repo / "dirty.txt").write_text("uncommitted\n")
        out_dir = Path(tempfile.mkdtemp())
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("AAA\n")
        argv = [
            "--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-03",
            "--output", str(out_dir / "run.db"), "--cache-dir", str(out_dir / "cache"),
            "--run-outcomes",
        ]
        downloader = _fake_downloader({"AAA": _fixed_frame("2026-05-01", "2026-06-10", 100.0), "SPY": SPY_WIDE})
        rc = rs.main(argv, downloader=downloader, repo_root=repo)
        self.assertEqual(rc, 1)

    def test_run_outcomes_refuses_with_wrong_library_versions_via_injected_lookup(self):
        repo = _make_temp_git_repo(with_preregistration=True, requirements_content="widgetlib==1.0.0\n")
        out_dir = Path(tempfile.mkdtemp())
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("AAA\n")
        argv = [
            "--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-03",
            "--output", str(out_dir / "run.db"), "--cache-dir", str(out_dir / "cache"),
            "--run-outcomes",
        ]
        downloader = _fake_downloader({"AAA": _fixed_frame("2026-05-01", "2026-06-10", 100.0), "SPY": SPY_WIDE})
        rc = rs.main(argv, downloader=downloader, repo_root=repo,
                     version_lookup=lambda name: "2.0.0")  # mismatch vs pinned 1.0.0
        self.assertEqual(rc, 1)

    def test_run_outcomes_records_git_commit_file_hash_and_versions_in_header(self):
        repo = _make_temp_git_repo(with_preregistration=True, requirements_content="widgetlib==1.0.0\n")
        out_dir = Path(tempfile.mkdtemp())
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("AAA\n")
        output_path = out_dir / "run.db"
        argv = [
            "--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-03",
            "--output", str(output_path), "--cache-dir", str(out_dir / "cache"),
            "--run-outcomes",
        ]
        downloader = _fake_downloader({"AAA": _fixed_frame("2026-05-01", "2026-06-10", 100.0), "SPY": SPY_WIDE})
        rc = rs.main(argv, downloader=downloader, repo_root=repo,
                     version_lookup=lambda name: "1.0.0")  # matches the pin
        self.assertEqual(rc, 0)

        conn = sqlite3.connect(output_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM replay_runs").fetchone()
        conn.close()
        self.assertEqual(row["git_commit"], rs.get_git_commit(repo))
        self.assertEqual(row["preregistration_file_hash"],
                          rs.get_committed_file_sha256(repo, rs.PREREGISTRATION_FILE))
        self.assertIn("widgetlib", row["library_versions_json"])

    def test_run_outcomes_records_outcome_params_and_slippage_bps_in_header(self):
        repo = _make_temp_git_repo(with_preregistration=True, requirements_content="widgetlib==1.0.0\n")
        out_dir = Path(tempfile.mkdtemp())
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("AAA\n")
        output_path = out_dir / "run.db"
        argv = [
            "--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-03",
            "--output", str(output_path), "--cache-dir", str(out_dir / "cache"),
            "--run-outcomes", "--slippage-bps", "7",
        ]
        downloader = _fake_downloader({"AAA": _fixed_frame("2026-05-01", "2026-06-10", 100.0), "SPY": SPY_WIDE})
        rc = rs.main(argv, downloader=downloader, repo_root=repo, version_lookup=lambda name: "1.0.0")
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(output_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM replay_runs").fetchone()
        conn.close()
        self.assertIn('"slippage_bps": 7.0', row["outcome_params_json"])


class TestCountsOnlyIO(unittest.TestCase):

    def _argv(self, out_dir, symbols_file, extra=None):
        argv = [
            "--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
            "--output", str(out_dir / "run.db"), "--cache-dir", str(out_dir / "cache"),
            "--counts-only",
        ]
        return argv + (extra or [])

    def _setup(self, out_dir, symbols=("AAA", "BBB")):
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("\n".join(symbols) + "\n")
        frames = {s: _fixed_frame("2026-05-01", "2026-06-10", 100.0) for s in symbols}
        frames["SPY"] = SPY_WIDE
        return symbols_file, frames

    def test_counts_only_output_has_no_outcome_fields(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        rc = rs.main(self._argv(out_dir, symbols_file), downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(out_dir / "run.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM replay_candidates").fetchall()
        conn.close()
        self.assertGreater(len(rows), 0)
        for row in rows:
            for field in ("fill_date", "fill_price", "exit_date", "exit_price",
                           "first_barrier_hit", "r_multiple", "mae_r", "mfe_r",
                           "sessions_held", "resolved_date"):
                self.assertIsNone(row[field])

    def test_counts_only_states_v3_and_v4_are_not_implemented(self):
        import io
        import contextlib
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rs.main(self._argv(out_dir, symbols_file), downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        self.assertIn("NOT implemented", buf.getvalue())
        self.assertIn("V3", buf.getvalue())
        self.assertIn("V4", buf.getvalue())

    def test_counts_only_reports_both_upper_and_lower_bounds_per_variant(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        rc = rs.main(self._argv(out_dir, symbols_file), downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        summary = rs.main.last_summary
        self.assertIn("V1", summary)
        self.assertIn("upper", summary["V1"])
        self.assertIn("lower", summary["V1"])
        self.assertIn("V2", summary)

    def test_counts_only_threshold_verdict_uses_lower_bound_only(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        rc = rs.main(self._argv(out_dir, symbols_file), downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        summary = rs.main.last_summary
        # Trivial reject-everything data -- 0 setups either way, but the
        # verdict must specifically be read off the LOWER dict's own field.
        self.assertFalse(summary["V1"]["lower"]["thresholds_met"])
        self.assertEqual(summary["V1"]["lower"]["total_setups"], 0)

    def test_variant_column_is_unique_per_run_symbol_scan_date(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        rc = rs.main(self._argv(out_dir, symbols_file), downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(out_dir / "run.db")
        variants = {r[0] for r in conn.execute("SELECT DISTINCT variant FROM replay_candidates")}
        bounds = {r[0] for r in conn.execute("SELECT DISTINCT bound FROM replay_candidates")}
        dupes = conn.execute(
            "SELECT run_id, variant, bound, symbol, scan_date, COUNT(*) c FROM replay_candidates "
            "GROUP BY run_id, variant, bound, symbol, scan_date HAVING c > 1"
        ).fetchall()
        conn.close()
        self.assertEqual(variants, {"V1", "V2"})
        self.assertEqual(bounds, {"UPPER", "LOWER"})
        self.assertEqual(dupes, [])

    def test_limit_symbols_trims_the_universe(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        rc = rs.main(self._argv(out_dir, symbols_file, extra=["--limit-symbols", "1"]),
                     downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(out_dir / "run.db")
        symbols = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM replay_data_manifest")}
        conn.close()
        self.assertEqual(len(symbols), 1)

    def test_progress_and_runtime_estimate_are_printed(self):
        import io
        import contextlib
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rs.main(self._argv(out_dir, symbols_file), downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("done in", output)
        self.assertIn("avg", output)
        self.assertIn("remaining", output)
        self.assertIn("complete in", output)
        self.assertNotIn("heuristic", output)  # the fixed-2s guess is gone

    def test_data_manifest_records_rows_dates_and_sha256(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir, symbols=("AAA",))
        rc = rs.main(self._argv(out_dir, symbols_file), downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(out_dir / "run.db")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM replay_data_manifest WHERE symbol = 'AAA'").fetchone()
        conn.close()
        expected = frames["AAA"]
        self.assertEqual(row["rows"], len(expected))
        self.assertEqual(row["first_date"], str(expected.index.min().date()))
        self.assertEqual(row["last_date"], str(expected.index.max().date()))
        self.assertEqual(row["sha256"], __import__("hashlib").sha256(expected.to_csv().encode()).hexdigest())


class TestDeterminismAndResumeIO(unittest.TestCase):

    def _setup(self, out_dir, symbols=("AAA", "BBB")):
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("\n".join(symbols) + "\n")
        frames = {s: _fixed_frame("2026-05-01", "2026-06-10", 100.0) for s in symbols}
        frames["SPY"] = SPY_WIDE
        return symbols_file, frames

    def test_determinism_two_runs_produce_identical_candidate_rows(self):
        out_dir1, out_dir2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        symbols_file1, frames = self._setup(out_dir1)
        symbols_file2, _ = self._setup(out_dir2)
        argv1 = ["--symbols-file", str(symbols_file1), "--start", "2026-06-01", "--end", "2026-06-05",
                 "--output", str(out_dir1 / "run.db"), "--cache-dir", str(out_dir1 / "cache"), "--counts-only"]
        argv2 = ["--symbols-file", str(symbols_file2), "--start", "2026-06-01", "--end", "2026-06-05",
                 "--output", str(out_dir2 / "run.db"), "--cache-dir", str(out_dir2 / "cache"), "--counts-only"]
        rs.main(argv1, downloader=_fake_downloader(frames))
        rs.main(argv2, downloader=_fake_downloader(frames))

        def _rows(path):
            conn = sqlite3.connect(path)
            rows = conn.execute(
                "SELECT variant, symbol, scan_date, regime, accepted, reason_code "
                "FROM replay_candidates ORDER BY variant, symbol, scan_date"
            ).fetchall()
            conn.close()
            return rows

        self.assertEqual(_rows(out_dir1 / "run.db"), _rows(out_dir2 / "run.db"))

    def test_per_symbol_checkpoint_marks_done_incrementally(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        argv = ["--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
                "--output", str(out_dir / "run.db"), "--cache-dir", str(out_dir / "cache"), "--counts-only"]
        rc = rs.main(argv, downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(out_dir / "run.db")
        statuses = {r[0]: r[1] for r in conn.execute("SELECT symbol, status FROM replay_progress")}
        conn.close()
        self.assertEqual(statuses, {"AAA": "done", "BBB": "done"})

    def test_resume_after_simulated_crash_completes_remaining_symbols_only(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        output_path = out_dir / "run.db"
        cache_dir = out_dir / "cache"

        # Hand-prepare a "partial progress" DB -- the state a prior
        # interrupted run would have left after finishing symbol AAA but
        # crashing before BBB.
        conn = rs.open_results_db(output_path)
        symbols = ["AAA", "BBB"]
        u_hash = rs.universe_hash(symbols)
        params_by_variant = {"V1": rs.V1_PARAMS, "V2": replace(rs.V1_PARAMS, swing_lookback_bars=120)}
        run_id = rs.create_run(
            conn, "counts_only", ["V1", "V2"], None, params_by_variant, rs.V1_OUTCOME_PARAMS,
            u_hash, date(2026, 6, 1), date(2026, 6, 5), date(2026, 6, 5), {}, None,
        )
        rs.mark_symbol_done(conn, run_id, "AAA")
        conn.close()

        call_log = []
        argv = ["--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
                "--output", str(output_path), "--cache-dir", str(cache_dir),
                "--counts-only", "--resume-run-id", str(run_id)]
        rc = rs.main(argv, downloader=_fake_downloader(frames, call_log))
        self.assertEqual(rc, 0)

        fetched_symbols = {s for call in call_log for s in call}
        self.assertNotIn("AAA", fetched_symbols)  # resume must skip the already-done symbol
        self.assertIn("BBB", fetched_symbols)

        conn = sqlite3.connect(output_path)
        statuses = {r[0]: r[1] for r in conn.execute("SELECT symbol, status FROM replay_progress")}
        conn.close()
        self.assertEqual(statuses, {"AAA": "done", "BBB": "done"})

    def test_resume_refuses_on_universe_mismatch(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        output_path = out_dir / "run.db"
        conn = rs.open_results_db(output_path)
        params_by_variant = {"V1": rs.V1_PARAMS, "V2": replace(rs.V1_PARAMS, swing_lookback_bars=120)}
        run_id = rs.create_run(
            conn, "counts_only", ["V1", "V2"], None, params_by_variant, rs.V1_OUTCOME_PARAMS,
            "a-different-universe-hash", date(2026, 6, 1), date(2026, 6, 5), date(2026, 6, 5), {}, None,
        )
        conn.close()
        argv = ["--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
                "--output", str(output_path), "--cache-dir", str(out_dir / "cache"),
                "--counts-only", "--resume-run-id", str(run_id)]
        with self.assertRaises(RuntimeError):
            rs.main(argv, downloader=_fake_downloader(frames))

    def test_resume_refuses_on_mode_mismatch(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        output_path = out_dir / "run.db"
        conn = rs.open_results_db(output_path)
        symbols = ["AAA", "BBB"]
        u_hash = rs.universe_hash(symbols)
        params_by_variant = {"V1": rs.V1_PARAMS}  # as if a prior --run-outcomes run
        run_id = rs.create_run(
            conn, "run_outcomes", ["V1"], "deadbeef", params_by_variant, rs.V1_OUTCOME_PARAMS,
            u_hash, date(2026, 6, 1), date(2026, 6, 5), date(2026, 6, 5), {}, "somehash",
        )
        conn.close()
        argv = ["--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
                "--output", str(output_path), "--cache-dir", str(out_dir / "cache"),
                "--counts-only", "--resume-run-id", str(run_id)]  # mode mismatch: counts_only now
        with self.assertRaises(RuntimeError):
            rs.main(argv, downloader=_fake_downloader(frames))

    def test_resume_refuses_on_params_mismatch_including_v2(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        output_path = out_dir / "run.db"
        conn = rs.open_results_db(output_path)
        symbols = ["AAA", "BBB"]
        u_hash = rs.universe_hash(symbols)
        # V2's swing_lookback_bars differs from what main() would compute (120).
        params_by_variant = {"V1": rs.V1_PARAMS, "V2": replace(rs.V1_PARAMS, swing_lookback_bars=999)}
        run_id = rs.create_run(
            conn, "counts_only", ["V1", "V2"], None, params_by_variant, rs.V1_OUTCOME_PARAMS,
            u_hash, date(2026, 6, 1), date(2026, 6, 5), date(2026, 6, 5), {}, None,
        )
        conn.close()
        argv = ["--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
                "--output", str(output_path), "--cache-dir", str(out_dir / "cache"),
                "--counts-only", "--resume-run-id", str(run_id)]
        with self.assertRaises(RuntimeError):
            rs.main(argv, downloader=_fake_downloader(frames))

    def test_resume_refuses_on_outcome_params_slippage_mismatch(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        output_path = out_dir / "run.db"
        conn = rs.open_results_db(output_path)
        symbols = ["AAA", "BBB"]
        u_hash = rs.universe_hash(symbols)
        params_by_variant = {"V1": rs.V1_PARAMS, "V2": replace(rs.V1_PARAMS, swing_lookback_bars=120)}
        different_outcome_params = replace(rs.V1_OUTCOME_PARAMS, slippage_bps=99.0)
        run_id = rs.create_run(
            conn, "counts_only", ["V1", "V2"], None, params_by_variant, different_outcome_params,
            u_hash, date(2026, 6, 1), date(2026, 6, 5), date(2026, 6, 5), {}, None,
        )
        conn.close()
        argv = ["--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
                "--output", str(output_path), "--cache-dir", str(out_dir / "cache"),
                "--counts-only", "--resume-run-id", str(run_id)]  # default --slippage-bps (10), not 99
        with self.assertRaises(RuntimeError):
            rs.main(argv, downloader=_fake_downloader(frames))

    def test_resume_refuses_on_data_range_mismatch(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file, frames = self._setup(out_dir)
        output_path = out_dir / "run.db"
        conn = rs.open_results_db(output_path)
        symbols = ["AAA", "BBB"]
        u_hash = rs.universe_hash(symbols)
        params_by_variant = {"V1": rs.V1_PARAMS, "V2": replace(rs.V1_PARAMS, swing_lookback_bars=120)}
        run_id = rs.create_run(
            conn, "counts_only", ["V1", "V2"], None, params_by_variant, rs.V1_OUTCOME_PARAMS,
            u_hash, date(2020, 1, 1), date(2020, 1, 10), date(2020, 1, 10), {}, None,
        )
        conn.close()
        argv = ["--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
                "--output", str(output_path), "--cache-dir", str(out_dir / "cache"),
                "--counts-only", "--resume-run-id", str(run_id)]
        with self.assertRaises(RuntimeError):
            rs.main(argv, downloader=_fake_downloader(frames))

    def test_counts_only_report_after_crash_and_resume_equals_uninterrupted_run(self):
        out_dir1, out_dir2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        symbols_file1, frames = self._setup(out_dir1)
        symbols_file2, _ = self._setup(out_dir2)

        # Uninterrupted run.
        argv_full = ["--symbols-file", str(symbols_file1), "--start", "2026-06-01", "--end", "2026-06-05",
                     "--output", str(out_dir1 / "run.db"), "--cache-dir", str(out_dir1 / "cache"),
                     "--counts-only"]
        rc = rs.main(argv_full, downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        full_summary = rs.main.last_summary

        # Simulated crash: AAA already done, BBB still pending.
        conn = rs.open_results_db(out_dir2 / "run.db")
        symbols = ["AAA", "BBB"]
        u_hash = rs.universe_hash(symbols)
        params_by_variant = {"V1": rs.V1_PARAMS, "V2": replace(rs.V1_PARAMS, swing_lookback_bars=120)}
        run_id = rs.create_run(
            conn, "counts_only", ["V1", "V2"], None, params_by_variant, rs.V1_OUTCOME_PARAMS,
            u_hash, date(2026, 6, 1), date(2026, 6, 5), date(2026, 6, 5), {}, None,
        )
        # Pre-seed ALL of AAA's rows exactly as the one-transaction-per-symbol
        # commit in main() would have written them before a crash on BBB.
        scan_dates = rs.build_scan_dates(frames["SPY"], date(2026, 6, 1), date(2026, 6, 5))
        for variant, params in params_by_variant.items():
            upper_rows = rs.process_symbol("AAA", frames["AAA"], frames["SPY"], scan_dates,
                                            params, rs.V1_OUTCOME_PARAMS, False, 0, variant)
            rs.write_candidate_rows(conn, run_id, "UPPER", upper_rows, commit=False)
            lower_rows = rs.process_symbol("AAA", frames["AAA"], frames["SPY"], scan_dates,
                                            params, rs.V1_OUTCOME_PARAMS, False,
                                            rs.V1_OUTCOME_PARAMS.timeout_sessions, variant)
            rs.write_candidate_rows(conn, run_id, "LOWER", lower_rows, commit=False)
        rs.write_manifest(conn, run_id, "AAA", frames["AAA"], commit=False)
        rs.mark_symbol_done(conn, run_id, "AAA", commit=False)
        conn.commit()
        conn.close()

        argv_resume = ["--symbols-file", str(symbols_file2), "--start", "2026-06-01", "--end", "2026-06-05",
                       "--output", str(out_dir2 / "run.db"), "--cache-dir", str(out_dir2 / "cache"),
                       "--counts-only", "--resume-run-id", str(run_id)]
        rc = rs.main(argv_resume, downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        resumed_summary = rs.main.last_summary
        self.assertEqual(resumed_summary, full_summary)


class TestUniverseSources(unittest.TestCase):

    def test_symbols_file_is_an_alternative_to_db_universe(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("bbb\naaa\naaa\n")  # lowercase + duplicate
        symbols = rs.load_universe_from_file(symbols_file)
        self.assertEqual(symbols, ["AAA", "BBB"])

    def test_universe_comes_from_a_read_only_db_connection(self):
        out_dir = Path(tempfile.mkdtemp())
        db_path = out_dir / "watchlist.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE watchlist (symbol TEXT, category TEXT, security_type TEXT, "
            "wl_state TEXT, enabled INTEGER)"
        )
        conn.execute("INSERT INTO watchlist VALUES ('AAA', 'Financials', 'stock', 'ACTIVE', 1)")
        conn.execute("INSERT INTO watchlist VALUES ('SPY', 'ETFs', 'etf', 'ACTIVE', 1)")
        conn.commit()
        conn.close()
        symbols = rs.load_universe_from_db(db_path)
        self.assertEqual(symbols, ["AAA"])


class TestRawDataCache(unittest.TestCase):

    def test_raw_data_cache_avoids_a_second_download_on_rerun(self):
        out_dir = Path(tempfile.mkdtemp())
        cache_dir = out_dir / "cache"
        call_log = []
        frame = _fixed_frame("2026-05-01", "2026-06-10", 100.0)
        downloader = _fake_downloader({"AAA": frame}, call_log)

        result1 = rs.fetch_universe_history(["AAA"], date(2026, 5, 1), date(2026, 6, 10), cache_dir,
                                              downloader=downloader)
        self.assertIsNotNone(result1["AAA"])
        self.assertEqual(len(call_log), 1)

        result2 = rs.fetch_universe_history(["AAA"], date(2026, 5, 1), date(2026, 6, 10), cache_dir,
                                              downloader=downloader)
        self.assertIsNotNone(result2["AAA"])
        self.assertEqual(len(call_log), 1)  # second call served entirely from cache


class TestChunkedDownloader(unittest.TestCase):

    def test_chunked_downloader_retries_then_degrades_to_per_symbol_fetch(self):
        attempts = {"n": 0}

        def flaky_downloader(symbols, start, end):
            attempts["n"] += 1
            if len(symbols) > 1:
                raise RuntimeError("whole-chunk failure")
            return {symbols[0]: _fixed_frame("2026-05-01", "2026-06-10", 100.0)}

        result = rs.fetch_history_chunked(
            ["AAA", "BBB"], date(2026, 5, 1), date(2026, 6, 10),
            chunk_size=50, max_retries=2, downloader=flaky_downloader,
        )
        self.assertIsNotNone(result["AAA"])
        self.assertIsNotNone(result["BBB"])
        # chunk attempted (and failed) max_retries times, then both symbols
        # succeeded individually -- more than just 1 call total.
        self.assertGreaterEqual(attempts["n"], 3)

    def test_normalize_multi_ticker_frame_shaped_like_yfinance_output(self):
        dates = pd.bdate_range(start="2026-05-01", periods=5)
        columns = pd.MultiIndex.from_product([["AAA", "BBB"], ["Open", "High", "Low", "Close", "Volume"]])
        data = np.random.default_rng(0).uniform(90, 110, size=(5, 10))
        raw = pd.DataFrame(data, index=dates, columns=columns)
        result = rs.normalize_multi_ticker_frame(raw, ["AAA", "BBB"])
        self.assertIn("AAA", result)
        self.assertIn("BBB", result)
        self.assertIsNotNone(result["AAA"])
        self.assertEqual(set(result["AAA"].columns), {"open", "high", "low", "close", "volume"})

    def test_normalize_multi_ticker_frame_returns_none_for_empty_symbol(self):
        dates = pd.bdate_range(start="2026-05-01", periods=5)
        columns = pd.MultiIndex.from_product([["AAA"], ["Open", "High", "Low", "Close", "Volume"]])
        data = np.full((5, 5), np.nan)
        raw = pd.DataFrame(data, index=dates, columns=columns)
        result = rs.normalize_multi_ticker_frame(raw, ["AAA", "MISSING"])
        self.assertIsNone(result["AAA"])
        self.assertIsNone(result["MISSING"])


class TestCacheRequestedRangeSemantics(unittest.TestCase):
    """Item 1 of the fix round: a cache hit is decided by the REQUESTED
    range (sidecar .json), not by the data's own actual date bounds."""

    def test_cache_hit_is_based_on_requested_range_not_actual_data_dates(self):
        out_dir = Path(tempfile.mkdtemp())
        cache_dir = out_dir / "cache"
        call_log = []
        # The data's own last real bar is well before the requested end.
        frame = _fixed_frame("2026-05-01", "2026-05-20", 100.0)
        downloader = _fake_downloader({"AAA": frame}, call_log)
        start, end = date(2026, 5, 1), date(2026, 6, 10)

        r1 = rs.fetch_universe_history(["AAA"], start, end, cache_dir, downloader=downloader)
        self.assertIsNotNone(r1["AAA"])
        self.assertEqual(len(call_log), 1)

        r2 = rs.fetch_universe_history(["AAA"], start, end, cache_dir, downloader=downloader)
        self.assertIsNotNone(r2["AAA"])
        self.assertEqual(len(call_log), 1)  # entirely served from cache despite last bar < end

    def test_cache_hit_for_symbol_whose_first_bar_is_after_requested_start(self):
        out_dir = Path(tempfile.mkdtemp())
        cache_dir = out_dir / "cache"
        call_log = []
        frame = _fixed_frame("2026-05-15", "2026-06-10", 100.0)  # starts after requested start
        downloader = _fake_downloader({"AAA": frame}, call_log)
        start, end = date(2026, 5, 1), date(2026, 6, 10)

        rs.fetch_universe_history(["AAA"], start, end, cache_dir, downloader=downloader)
        self.assertEqual(len(call_log), 1)
        rs.fetch_universe_history(["AAA"], start, end, cache_dir, downloader=downloader)
        self.assertEqual(len(call_log), 1)

    def test_cache_only_fails_and_lists_missing_symbols(self):
        out_dir = Path(tempfile.mkdtemp())
        cache_dir = out_dir / "cache"
        with self.assertRaises(RuntimeError) as cm:
            rs.fetch_universe_history(["AAA", "BBB"], date(2026, 5, 1), date(2026, 6, 10),
                                       cache_dir, cache_only=True)
        self.assertIn("AAA", str(cm.exception))
        self.assertIn("BBB", str(cm.exception))

    def test_sidecar_json_records_requested_range_fetched_at_and_sha256(self):
        out_dir = Path(tempfile.mkdtemp())
        cache_dir = out_dir / "cache"
        frame = _fixed_frame("2026-05-01", "2026-06-10", 100.0)
        rs.save_to_cache(cache_dir, "AAA", frame, date(2026, 5, 1), date(2026, 6, 10))
        meta = json.loads(rs.cache_sidecar_file(cache_dir, "AAA").read_text())
        self.assertEqual(meta["requested_start"], "2026-05-01")
        self.assertEqual(meta["requested_end"], "2026-06-10")
        self.assertIn("fetched_at", meta)
        self.assertEqual(meta["sha256"], hashlib.sha256(frame.to_csv().encode()).hexdigest())

    def test_main_with_cache_only_fails_listing_missing_symbols(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("AAA\n")
        frames = {"AAA": _fixed_frame("2026-05-01", "2026-06-10", 100.0), "SPY": SPY_WIDE}
        argv = [
            "--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
            "--output", str(out_dir / "run.db"), "--cache-dir", str(out_dir / "cache"),
            "--counts-only", "--cache-only",
        ]
        with self.assertRaises(RuntimeError):
            rs.main(argv, downloader=_fake_downloader(frames))


class TestChunkedUpfrontDownload(unittest.TestCase):

    def test_chunked_download_happens_up_front_not_per_symbol(self):
        out_dir = Path(tempfile.mkdtemp())
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("AAA\nBBB\nCCC\n")
        frames = {s: _fixed_frame("2026-05-01", "2026-06-10", 100.0) for s in ("AAA", "BBB", "CCC")}
        frames["SPY"] = SPY_WIDE
        call_log = []
        argv = [
            "--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
            "--output", str(out_dir / "run.db"), "--cache-dir", str(out_dir / "cache"),
            "--counts-only", "--chunk-size", "10",
        ]
        rc = rs.main(argv, downloader=_fake_downloader(frames, call_log))
        self.assertEqual(rc, 0)
        non_spy_calls = [c for c in call_log if c != ("SPY",)]
        self.assertEqual(len(non_spy_calls), 1)  # one chunk call for all 3 symbols, not 3 calls
        self.assertEqual(set(non_spy_calls[0]), {"AAA", "BBB", "CCC"})


class TestScanDatesAndFetchRange(unittest.TestCase):

    def test_build_scan_dates_uses_spy_actual_index(self):
        spy = _fixed_frame("2026-06-01", "2026-06-10", 400.0)
        dates = rs.build_scan_dates(spy, date(2026, 6, 3), date(2026, 6, 5))
        expected = [d.date() for d in pd.bdate_range("2026-06-03", "2026-06-05")]
        self.assertEqual(dates, expected)

    def test_resolve_fetch_end_extends_for_run_outcomes_capped_at_yesterday(self):
        end = date(2026, 6, 1)
        today = date(2026, 6, 5)
        fetch_end = rs.resolve_fetch_end(end, "run_outcomes", today=today)
        self.assertEqual(fetch_end, today - timedelta(days=1))

    def test_resolve_fetch_end_extends_normally_when_not_capped(self):
        end = date(2020, 1, 1)
        today = date(2026, 6, 5)
        fetch_end = rs.resolve_fetch_end(end, "run_outcomes", today=today)
        self.assertEqual(fetch_end, end + timedelta(days=rs.RUN_OUTCOMES_FETCH_EXTENSION_DAYS))

    def test_resolve_fetch_end_unchanged_for_counts_only(self):
        end = date(2026, 6, 1)
        self.assertEqual(rs.resolve_fetch_end(end, "counts_only"), end)

    def test_run_outcomes_records_fetch_end_date_and_prints_open_at_end_note(self):
        import contextlib
        import io
        repo = _make_temp_git_repo(with_preregistration=True, requirements_content="widgetlib==1.0.0\n")
        out_dir = Path(tempfile.mkdtemp())
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("AAA\n")
        output_path = out_dir / "run.db"
        argv = [
            "--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-03",
            "--output", str(output_path), "--cache-dir", str(out_dir / "cache"),
            "--run-outcomes",
        ]
        downloader = _fake_downloader({
            "AAA": _fixed_frame("2026-01-01", "2026-12-31", 100.0),
            "SPY": _fixed_frame("2020-01-01", "2026-12-31", 400.0),
        })
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rs.main(argv, downloader=downloader, repo_root=repo, version_lookup=lambda name: "1.0.0")
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(output_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM replay_runs").fetchone()
        conn.close()
        self.assertEqual(row["end_date"], "2026-06-03")
        self.assertNotEqual(row["fetch_end_date"], row["end_date"])
        self.assertIn("OPEN_AT_END", buf.getvalue())


class TestManifestIdempotencyAndTransactionSafety(unittest.TestCase):

    def test_manifest_write_is_idempotent_via_insert_or_replace(self):
        out_dir = Path(tempfile.mkdtemp())
        conn = rs.open_results_db(out_dir / "run.db")
        frame = _fixed_frame("2026-05-01", "2026-06-10", 100.0)
        rs.write_manifest(conn, 1, "AAA", frame)
        rs.write_manifest(conn, 1, "AAA", frame)  # re-write, must not duplicate
        rows = conn.execute(
            "SELECT * FROM replay_data_manifest WHERE run_id=1 AND symbol='AAA'"
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)

    def test_symbol_writes_happen_in_one_transaction_rollback_leaves_nothing(self):
        out_dir = Path(tempfile.mkdtemp())
        db_path = out_dir / "run.db"
        conn = rs.open_results_db(db_path)
        params_by_variant = {"V1": rs.V1_PARAMS}
        run_id = rs.create_run(
            conn, "counts_only", ["V1"], None, params_by_variant, rs.V1_OUTCOME_PARAMS,
            "hash", date(2026, 6, 1), date(2026, 6, 5), date(2026, 6, 5), {}, None,
        )
        frame = _fixed_frame("2026-05-01", "2026-06-10", 100.0)
        rows = rs.process_symbol("AAA", frame, frame, [date(2026, 6, 1)], rs.V1_PARAMS,
                                  rs.V1_OUTCOME_PARAMS, False, 0, "V1")
        rs.write_candidate_rows(conn, run_id, "UPPER", rows, commit=False)
        rs.write_manifest(conn, run_id, "AAA", frame, commit=False)
        rs.mark_symbol_done(conn, run_id, "AAA", commit=False)
        conn.rollback()  # simulated crash before the single per-symbol commit
        conn.close()

        conn2 = sqlite3.connect(db_path)
        candidate_count = conn2.execute(
            "SELECT COUNT(*) FROM replay_candidates WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        manifest_count = conn2.execute(
            "SELECT COUNT(*) FROM replay_data_manifest WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        progress_count = conn2.execute(
            "SELECT COUNT(*) FROM replay_progress WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        conn2.close()
        self.assertEqual(candidate_count, 0)
        self.assertEqual(manifest_count, 0)
        self.assertEqual(progress_count, 0)


class TestRegimeByDatePrecomputed(unittest.TestCase):
    """Code-review fix: run_replay_core()'s optional regime_by_date lets a
    caller that invokes it repeatedly for the same spy_df/scan_dates
    (scripts/replay_setups.py, once per symbol x variant x bound) build
    setups.replay.build_regime_by_date() a single time and pass it in."""

    def test_precomputed_regime_by_date_gives_identical_results(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=2)
        scan_dates = _scan_dates_from(df, 0, 2)
        without = run_replay_core(frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS)
        regime_by_date = build_regime_by_date(spy, scan_dates)
        with_precomputed = run_replay_core(
            frames, spy, scan_dates, params=SMALL_WINDOW_PARAMS, regime_by_date=regime_by_date,
        )
        self.assertEqual(without, with_precomputed)

    def test_accepted_row_regime_equals_the_precomputed_dict_value_for_that_date(self):
        frames, spy, df = _golden_frames()
        scan_date = df.index[-1].date()
        regime_by_date = build_regime_by_date(spy, [scan_date])
        rows = run_replay_core(frames, spy, [scan_date], params=V1_PARAMS, regime_by_date=regime_by_date)
        self.assertTrue(rows[0].accepted)
        self.assertEqual(rows[0].regime, regime_by_date[scan_date])

    def test_corrupting_spy_bars_after_date_d_leaves_dict_values_for_earlier_dates_identical(self):
        frames, spy, df = _golden_frames(extra_flat_sessions=3)
        scan_dates = _scan_dates_from(df, 0, 3)
        d = scan_dates[1]
        baseline = build_regime_by_date(spy, scan_dates)

        spy_dates = [ts.date() if hasattr(ts, "date") else ts for ts in spy.index]
        corrupted_close = list(spy["close"])
        for i, sd in enumerate(spy_dates):
            if sd > d:
                corrupted_close[i] = 999999.0
        corrupted_spy = spy.copy()
        corrupted_spy["close"] = corrupted_close
        corrupted_spy["high"] = [c + 1.0 for c in corrupted_close]
        corrupted_spy["low"] = [c - 1.0 for c in corrupted_close]
        corrupted = build_regime_by_date(corrupted_spy, scan_dates)

        for sd in scan_dates:
            if sd <= d:
                self.assertEqual(corrupted[sd], baseline[sd], f"date {sd}")


class TestRegimeByDateBuiltOnceInMain(unittest.TestCase):

    def test_main_builds_regime_by_date_exactly_once(self):
        from unittest.mock import patch
        out_dir = Path(tempfile.mkdtemp())
        symbols_file = out_dir / "symbols.txt"
        symbols_file.write_text("AAA\nBBB\n")
        frames = {s: _fixed_frame("2026-05-01", "2026-06-10", 100.0) for s in ("AAA", "BBB")}
        frames["SPY"] = SPY_WIDE
        argv = [
            "--symbols-file", str(symbols_file), "--start", "2026-06-01", "--end", "2026-06-05",
            "--output", str(out_dir / "run.db"), "--cache-dir", str(out_dir / "cache"), "--counts-only",
        ]
        with patch("replay_setups.build_regime_by_date", wraps=rs.build_regime_by_date) as mocked:
            rc = rs.main(argv, downloader=_fake_downloader(frames))
        self.assertEqual(rc, 0)
        self.assertEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
