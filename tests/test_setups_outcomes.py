"""Tests for setups/outcomes.py -- the pure outcome-simulation module
(docs/SETUP_SPEC.md §6, §12). All price data is synthetic (no network, no
DB); every ambiguity-table row approved for this module has at least one
dedicated test below.
"""
import sys
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import populate_outcomes as po  # noqa: E402  (parity tests import this directly)

from setups.calendar_utils import advance_us_trading_days  # noqa: E402
from setups.definition import SetupDefinition  # noqa: E402
from setups.outcomes import simulate_outcome  # noqa: E402
from setups.params import V1_OUTCOME_PARAMS  # noqa: E402
from setups.reason_codes import (  # noqa: E402
    BARRIER_STOP,
    BARRIER_TARGET,
    BARRIER_TIMEOUT_30,
    CHASE_RR_BELOW_3,
    DATA_GAP,
    EXPIRED_UNFILLED,
    FILLED_STOPPED_SAME_DAY,
    INVALIDATED_BEFORE_FILL,
    OPEN_AT_END,
)


# ── Shared fixtures ──────────────────────────────────────────────────────

SCAN_DATE = date(2026, 3, 2)  # Monday, clear of US holidays


def sessions_after(start: date, n: int) -> list:
    dates = []
    d = start
    for _ in range(n):
        d = advance_us_trading_days(d, 1)
        dates.append(d)
    return dates


def make_bars(dates: list, specs: list) -> pd.DataFrame:
    """specs: list of (open, high, low, close) tuples."""
    return pd.DataFrame(
        {
            "open":  [s[0] for s in specs],
            "high":  [s[1] for s in specs],
            "low":   [s[2] for s in specs],
            "close": [s[3] for s in specs],
            "volume": [1_000_000] * len(specs),
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )


def make_setup(trigger, stop, sl_price, target, scan_date=SCAN_DATE, validity_sessions=5):
    validity_end = advance_us_trading_days(scan_date, validity_sessions)
    risk = trigger - stop
    rr = (target - trigger) / risk
    return SetupDefinition(
        sh_date=scan_date, sh_price=trigger,
        sl_date=scan_date, sl_price=sl_price,
        trigger=trigger, stop=stop, target=target,
        risk=risk, rr=rr, atr=1.0, regime="BULL",
        validity_end=validity_end, scan_date=scan_date,
    )


# Setup A: trigger=110, stop=95, pullback-low=100, target=155 -- risk=15,
# nominal RR at trigger == exactly 3.0 (the boundary).
SETUP_A = make_setup(trigger=110.0, stop=95.0, sl_price=100.0, target=155.0)

# Setup B: trigger=100, stop=90, target=160 -- risk=10, nominal RR == 6.0,
# wide enough that a modest gap-up fill still clears min_rr.
SETUP_B = make_setup(trigger=100.0, stop=90.0, sl_price=95.0, target=160.0)

BENIGN_A = (100.0, 105.0, 98.0, 102.0)       # no fill, no invalidation (setup A)
FILL_ORDINARY_A = (105.0, 112.0, 104.0, 108.0)  # fills at trigger (110), no same-day stop
TARGET_HIT_A = (140.0, 158.0, 138.0, 150.0)     # hits target (155) next session


class TestPreFillScanAndFill(unittest.TestCase):

    def test_fill_at_exact_trigger_price(self):
        dates = sessions_after(SCAN_DATE, 2)
        df = make_bars(dates, [FILL_ORDINARY_A, TARGET_HIT_A])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.fill_price, 110.0)
        self.assertEqual(result.fill_date, dates[0])
        self.assertEqual(result.status, BARRIER_TARGET)

    def test_fill_gap_above_trigger_fills_at_open(self):
        dates = sessions_after(SCAN_DATE, 2)
        gap_fill = (102.0, 108.0, 95.0, 105.0)  # open(102) > trigger(100), RR still clears
        target_hit = (150.0, 165.0, 148.0, 158.0)
        df = make_bars(dates, [gap_fill, target_hit])
        result = simulate_outcome(SETUP_B, df)
        self.assertEqual(result.fill_price, 102.0)
        self.assertEqual(result.status, BARRIER_TARGET)

    def test_invalidated_before_fill_on_stop_breach(self):
        dates = sessions_after(SCAN_DATE, 1)
        bar = (100.0, 108.0, 93.0, 98.0)  # high<trigger, low<=stop(95)
        df = make_bars(dates, [bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, INVALIDATED_BEFORE_FILL)

    def test_invalidated_before_fill_on_close_below_pullback_low(self):
        dates = sessions_after(SCAN_DATE, 1)
        bar = (100.0, 108.0, 96.0, 99.0)  # high<trigger, low>stop, close<sl_price(100)
        df = make_bars(dates, [bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, INVALIDATED_BEFORE_FILL)

    def test_invalidated_before_fill_never_checks_the_fill_session_itself(self):
        dates = sessions_after(SCAN_DATE, 2)
        # This session's close (99) is below sl_price (100) -- would be
        # INVALIDATED_BEFORE_FILL if checked as a pre-fill-only bar, but
        # its high (112) also crosses the trigger, so it IS the fill
        # session and that check must never run against it.
        fill_bar = (105.0, 112.0, 96.0, 99.0)
        df = make_bars(dates, [fill_bar, TARGET_HIT_A])
        result = simulate_outcome(SETUP_A, df)
        self.assertNotEqual(result.status, INVALIDATED_BEFORE_FILL)
        self.assertEqual(result.fill_date, dates[0])
        self.assertEqual(result.status, BARRIER_TARGET)

    def test_expired_unfilled_full_window_elapsed_no_cross(self):
        dates = sessions_after(SCAN_DATE, 5)  # full validity window
        df = make_bars(dates, [BENIGN_A] * 5)
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, EXPIRED_UNFILLED)


class TestFillRRGateOrdering(unittest.TestCase):
    """§6: the R:R gate is checked BEFORE the same-session stop check."""

    def test_chase_rr_below_3_rejected_no_trade(self):
        dates = sessions_after(SCAN_DATE, 1)
        bar = (130.0, 135.0, 128.0, 132.0)  # gap fill, RR well below 3
        df = make_bars(dates, [bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, CHASE_RR_BELOW_3)
        self.assertEqual(result.fill_price, 130.0)
        self.assertIsNone(result.r_multiple)

    def test_chase_rr_boundary_exactly_3_is_accepted(self):
        dates = sessions_after(SCAN_DATE, 2)
        df = make_bars(dates, [FILL_ORDINARY_A, TARGET_HIT_A])  # RR == 3.0 exactly
        result = simulate_outcome(SETUP_A, df)
        self.assertNotEqual(result.status, CHASE_RR_BELOW_3)
        self.assertEqual(result.status, BARRIER_TARGET)

    def test_chase_rr_fill_at_or_above_target_is_rejected(self):
        dates = sessions_after(SCAN_DATE, 1)
        bar = (200.0, 205.0, 198.0, 202.0)  # fill price (200) >= target (155)
        df = make_bars(dates, [bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, CHASE_RR_BELOW_3)

    def test_gap_fill_rr_below_3_with_same_session_stop_breach_is_chase_not_stopped(self):
        dates = sessions_after(SCAN_DATE, 1)
        # Gap fill (RR below 3) AND this same session's low breaches the
        # stop -- must resolve as CHASE_RR_BELOW_3 (no trade), never as
        # FILLED_STOPPED_SAME_DAY (a loss implies a trade was taken).
        bar = (130.0, 135.0, 90.0, 100.0)
        df = make_bars(dates, [bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, CHASE_RR_BELOW_3)
        self.assertIsNone(result.r_multiple)

    def test_rr_gate_passes_with_same_session_stop_breach_is_filled_stopped_same_day(self):
        dates = sessions_after(SCAN_DATE, 1)
        # Gap fill whose RR still clears min_rr (setup B is wide enough)
        # AND this same session's low breaches the stop -- must resolve
        # as FILLED_STOPPED_SAME_DAY, the converse of the test above.
        bar = (102.0, 108.0, 85.0, 95.0)
        df = make_bars(dates, [bar])
        result = simulate_outcome(SETUP_B, df)
        self.assertEqual(result.status, FILLED_STOPPED_SAME_DAY)
        self.assertEqual(result.exit_price, 90.0)  # stop, open(102) > stop


class TestFilledStoppedSameDay(unittest.TestCase):

    def test_filled_stopped_same_day_exact_minus_1r_before_costs(self):
        dates = sessions_after(SCAN_DATE, 1)
        # open(102) sits between stop(95) and trigger(110): fill=trigger,
        # exit=stop exactly -> -1R before costs.
        bar = (102.0, 112.0, 90.0, 95.0)
        df = make_bars(dates, [bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, FILLED_STOPPED_SAME_DAY)
        self.assertEqual(result.fill_price, 110.0)
        self.assertEqual(result.exit_price, 95.0)
        self.assertEqual(result.sessions_held, 1)
        self.assertEqual(result.first_barrier_hit, BARRIER_STOP)

        slippage = V1_OUTCOME_PARAMS.slippage_bps / 10000.0
        entry_net = 110.0 * (1 + slippage)
        exit_net = 95.0 * (1 - slippage)
        expected_r = (exit_net - entry_net) / 15.0
        self.assertAlmostEqual(result.r_multiple, expected_r, places=9)
        self.assertAlmostEqual(result.mae_r, (90.0 - 110.0) / 15.0, places=9)
        self.assertAlmostEqual(result.mfe_r, (112.0 - 110.0) / 15.0, places=9)

    def test_filled_stopped_same_day_gap_below_stop_worse_than_minus_1r(self):
        dates = sessions_after(SCAN_DATE, 1)
        bar = (80.0, 112.0, 70.0, 85.0)  # open(80) below stop(95): gap-down exit
        df = make_bars(dates, [bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, FILLED_STOPPED_SAME_DAY)
        self.assertEqual(result.fill_price, 110.0)
        self.assertEqual(result.exit_price, 80.0)
        self.assertLess(result.r_multiple, -2.0)


class TestPostFillMonitoring(unittest.TestCase):

    def test_post_fill_ordinary_stop_hit(self):
        dates = sessions_after(SCAN_DATE, 2)
        stop_bar = (98.0, 100.0, 90.0, 92.0)  # open(98) > stop(95): not a gap
        df = make_bars(dates, [FILL_ORDINARY_A, stop_bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, BARRIER_STOP)
        self.assertEqual(result.exit_price, 95.0)
        self.assertEqual(result.sessions_held, 2)
        self.assertEqual(result.first_barrier_hit, BARRIER_STOP)

    def test_post_fill_stop_and_target_same_bar_stop_wins(self):
        dates = sessions_after(SCAN_DATE, 2)
        tie_bar = (100.0, 160.0, 90.0, 120.0)  # low<=stop AND high>=target
        df = make_bars(dates, [FILL_ORDINARY_A, tie_bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, BARRIER_STOP)

    def test_post_fill_gap_below_stop_exits_at_open(self):
        dates = sessions_after(SCAN_DATE, 2)
        gap_bar = (80.0, 85.0, 70.0, 75.0)  # open(80) <= stop(95)
        df = make_bars(dates, [FILL_ORDINARY_A, gap_bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, BARRIER_STOP)
        self.assertEqual(result.exit_price, 80.0)

    def test_post_fill_ordinary_target_hit(self):
        dates = sessions_after(SCAN_DATE, 2)
        df = make_bars(dates, [FILL_ORDINARY_A, TARGET_HIT_A])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, BARRIER_TARGET)
        self.assertEqual(result.exit_price, 155.0)

    def test_post_fill_gap_above_target_capped_at_target(self):
        dates = sessions_after(SCAN_DATE, 2)
        gap_bar = (170.0, 175.0, 165.0, 172.0)  # open(170) >= target(155)
        df = make_bars(dates, [FILL_ORDINARY_A, gap_bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, BARRIER_TARGET)
        self.assertEqual(result.exit_price, 155.0)  # capped, not 170

    def test_mae_and_mfe_are_in_r_units(self):
        dates = sessions_after(SCAN_DATE, 3)
        benign = (105.0, 120.0, 100.0, 115.0)
        stop_bar = (98.0, 102.0, 90.0, 92.0)
        df = make_bars(dates, [FILL_ORDINARY_A, benign, stop_bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, BARRIER_STOP)
        worst_low = min(104.0, 100.0, 90.0)
        best_high = max(112.0, 120.0, 102.0)
        self.assertAlmostEqual(result.mae_r, (worst_low - 110.0) / 15.0, places=9)
        self.assertAlmostEqual(result.mfe_r, (best_high - 110.0) / 15.0, places=9)

    def test_sessions_held_counts_fill_through_exit_inclusive(self):
        dates = sessions_after(SCAN_DATE, 3)
        benign = (105.0, 120.0, 100.0, 115.0)
        stop_bar = (98.0, 102.0, 90.0, 92.0)
        df = make_bars(dates, [FILL_ORDINARY_A, benign, stop_bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.sessions_held, 3)


class TestTimeoutAndDataAvailability(unittest.TestCase):

    def test_timeout_30_on_exactly_the_30th_post_fill_session(self):
        dates = sessions_after(SCAN_DATE, 31)  # fill + 30 post-fill sessions
        df = make_bars(dates, [FILL_ORDINARY_A] + [BENIGN_A] * 30)
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, BARRIER_TIMEOUT_30)
        self.assertEqual(result.first_barrier_hit, BARRIER_TIMEOUT_30)
        self.assertEqual(result.exit_price, BENIGN_A[3])  # close of the 30th session
        self.assertEqual(result.sessions_held, 31)
        self.assertEqual(result.exit_date, dates[-1])

    def test_one_session_short_of_timeout_is_open_at_end_not_timeout(self):
        dates = sessions_after(SCAN_DATE, 30)  # fill + only 29 post-fill sessions
        df = make_bars(dates, [FILL_ORDINARY_A] + [BENIGN_A] * 29)
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, OPEN_AT_END)
        self.assertNotEqual(result.status, BARRIER_TIMEOUT_30)

    def test_data_gap_excludes_and_stops_at_nan_bar(self):
        dates = sessions_after(SCAN_DATE, 2)
        nan_bar = (float("nan"), float("nan"), float("nan"), float("nan"))
        df = make_bars(dates, [FILL_ORDINARY_A, nan_bar])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, DATA_GAP)

    def test_open_at_end_during_validity_window(self):
        dates = sessions_after(SCAN_DATE, 2)  # window needs 5, only 2 available
        df = make_bars(dates, [BENIGN_A, BENIGN_A])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, OPEN_AT_END)

    def test_open_at_end_during_post_fill_monitoring(self):
        dates = sessions_after(SCAN_DATE, 11)  # fill + only 10 post-fill sessions
        df = make_bars(dates, [FILL_ORDINARY_A] + [BENIGN_A] * 10)
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, OPEN_AT_END)

    def test_data_gap_missing_session_inside_validity_window(self):
        window = sessions_after(SCAN_DATE, 5)
        kept_dates = [window[0], window[1], window[3], window[4]]  # skip window[2]
        df = make_bars(kept_dates, [BENIGN_A] * 4)
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, DATA_GAP)

    def test_data_gap_missing_session_inside_post_fill_window(self):
        fill_date = sessions_after(SCAN_DATE, 1)[0]
        post = sessions_after(fill_date, 3)
        kept_dates = [fill_date, post[0], post[2]]  # skip post[1]
        df = make_bars(kept_dates, [FILL_ORDINARY_A, BENIGN_A, BENIGN_A])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, DATA_GAP)


class TestReferenceSessions(unittest.TestCase):

    def test_reference_sessions_catches_gap_the_frame_lacks(self):
        # Fabricated reference calendar (not the real US trading calendar)
        # that claims a Saturday session exists between the fill and the
        # next real trading day -- proves reference_sessions is actually
        # consulted, not silently ignored in favor of the internal
        # calendar walk.
        scan2 = date(2026, 3, 5)  # Thursday
        fill_date = advance_us_trading_days(scan2, 1)        # Friday
        extra_date = fill_date + timedelta(days=1)           # Saturday (fabricated)
        post1 = advance_us_trading_days(fill_date, 1)         # Monday
        post2 = advance_us_trading_days(fill_date, 2)         # Tuesday
        reference_sessions = [fill_date, extra_date, post1, post2]

        setup2 = make_setup(trigger=110.0, stop=95.0, sl_price=100.0, target=155.0,
                             scan_date=scan2)
        df = make_bars([fill_date, post1, post2], [FILL_ORDINARY_A, BENIGN_A, TARGET_HIT_A])

        with_ref = simulate_outcome(setup2, df, reference_sessions=reference_sessions)
        self.assertEqual(with_ref.status, DATA_GAP)

        without_ref = simulate_outcome(setup2, df)  # calendar fallback never invents Saturday
        self.assertNotEqual(without_ref.status, DATA_GAP)
        self.assertEqual(without_ref.status, BARRIER_TARGET)


class TestTargetTouchedOnFillSessionFlag(unittest.TestCase):

    def test_target_touched_on_fill_session_flag_true(self):
        dates = sessions_after(SCAN_DATE, 2)
        fill_bar = (108.0, 156.0, 104.0, 150.0)  # high(156) >= target(155), low(104) > stop
        df = make_bars(dates, [fill_bar, TARGET_HIT_A])
        result = simulate_outcome(SETUP_A, df)
        self.assertTrue(result.target_touched_on_fill_session)
        # ... and the target is still never credited on the fill session.
        self.assertNotEqual(result.exit_date, dates[0])

    def test_target_touched_on_fill_session_flag_false(self):
        dates = sessions_after(SCAN_DATE, 2)
        df = make_bars(dates, [FILL_ORDINARY_A, TARGET_HIT_A])  # high(112) < target(155)
        result = simulate_outcome(SETUP_A, df)
        self.assertFalse(result.target_touched_on_fill_session)


class TestBarsAtOrBeforeScanDateAreFiltered(unittest.TestCase):

    def test_bars_at_or_before_scan_date_are_filtered_not_used(self):
        dates = sessions_after(SCAN_DATE, 2)
        # A leaked scan-date bar with extreme values that would trigger
        # an immediate (wrong) invalidation or fill if it were used.
        leaked = (200.0, 300.0, 1.0, 250.0)
        df = make_bars([SCAN_DATE, dates[0], dates[1]], [leaked, FILL_ORDINARY_A, TARGET_HIT_A])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.fill_date, dates[0])
        self.assertEqual(result.status, BARRIER_TARGET)


class TestTzNormalization(unittest.TestCase):

    def test_tz_aware_index_normalizes_the_same_as_tz_naive(self):
        dates = sessions_after(SCAN_DATE, 2)
        naive_df = make_bars(dates, [FILL_ORDINARY_A, TARGET_HIT_A])
        aware_df = naive_df.copy()
        aware_df.index = pd.DatetimeIndex(dates, name="Date").tz_localize("UTC")

        naive_result = simulate_outcome(SETUP_A, naive_df)
        aware_result = simulate_outcome(SETUP_A, aware_df)

        self.assertEqual(aware_result.status, naive_result.status)
        self.assertEqual(aware_result.fill_date, naive_result.fill_date)
        self.assertEqual(aware_result.exit_date, naive_result.exit_date)
        self.assertEqual(aware_result.r_multiple, naive_result.r_multiple)


class TestPropertyBarsAfterExit(unittest.TestCase):

    def test_property_bars_after_exit_never_change_the_result(self):
        dates = sessions_after(SCAN_DATE, 2)
        stop_bar = (98.0, 100.0, 90.0, 92.0)
        df = make_bars(dates, [FILL_ORDINARY_A, stop_bar])
        baseline = simulate_outcome(SETUP_A, df)
        self.assertEqual(baseline.status, BARRIER_STOP)

        extra_dates = sessions_after(dates[-1], 3)
        wild_bars = [(500.0, 600.0, 1.0, 400.0)] * 3  # would flip everything if looked at
        df_extended = make_bars(dates + extra_dates, [FILL_ORDINARY_A, stop_bar] + wild_bars)
        extended = simulate_outcome(SETUP_A, df_extended)

        self.assertEqual(extended.status, baseline.status)
        self.assertEqual(extended.exit_price, baseline.exit_price)
        self.assertEqual(extended.r_multiple, baseline.r_multiple)
        self.assertEqual(extended.mae_r, baseline.mae_r)
        self.assertEqual(extended.mfe_r, baseline.mfe_r)
        self.assertEqual(extended.sessions_held, baseline.sessions_held)


class TestSlippageArithmeticByHand(unittest.TestCase):

    def test_slippage_arithmetic_by_hand(self):
        setup_c = make_setup(trigger=100.0, stop=90.0, sl_price=95.0, target=130.0)  # RR == 3.0
        dates = sessions_after(SCAN_DATE, 1)
        bar = (95.0, 105.0, 80.0, 90.0)  # open(95) between stop/trigger -> fill=100, exit=stop=90
        df = make_bars(dates, [bar])
        result = simulate_outcome(setup_c, df)
        self.assertEqual(result.status, FILLED_STOPPED_SAME_DAY)
        self.assertEqual(result.fill_price, 100.0)
        self.assertEqual(result.exit_price, 90.0)

        slippage = 10.0 / 10000.0
        entry_net = 100.0 * (1 + slippage)   # 100.1
        exit_net = 90.0 * (1 - slippage)     # 89.91
        expected_r = (exit_net - entry_net) / 10.0   # (89.91 - 100.1) / 10 == -1.019
        self.assertAlmostEqual(expected_r, -1.019, places=9)
        self.assertAlmostEqual(result.r_multiple, expected_r, places=9)


class TestParityWithComputeOutcome(unittest.TestCase):
    """Parity against scripts/populate_outcomes.py's compute_outcome() for
    the mechanics the two share (§12): same-day stop-before-target
    tie-break, MAE through the exit bar, T+0 exclusion."""

    def test_importing_populate_outcomes_has_no_import_time_side_effects(self):
        import logging
        handlers_before = list(logging.getLogger().handlers)
        import importlib
        importlib.reload(po)  # re-import is safe: proves no exception, no side effect
        self.assertEqual(logging.getLogger().handlers, handlers_before)

    def test_parity_stop_before_target_same_day_tie_break_matches_compute_outcome(self):
        entry, stop, tp = 100.0, 90.0, 130.0
        po_bars = pd.DataFrame({"close": [110.0], "high": [135.0], "low": [85.0]})
        po_result = po.compute_outcome(entry, stop, tp, po_bars)
        self.assertEqual(po_result["first_barrier_hit"], "stop_loss")

        setup_d = make_setup(trigger=100.0, stop=90.0, sl_price=95.0, target=200.0)
        dates = sessions_after(SCAN_DATE, 2)
        fill_bar = (95.0, 102.0, 94.0, 98.0)   # fills at 100, no same-day stop
        tie_bar = (100.0, 135.0, 85.0, 110.0)  # low<=stop AND high>=target same bar
        df = make_bars(dates, [fill_bar, tie_bar])
        result = simulate_outcome(setup_d, df)
        self.assertEqual(result.status, BARRIER_STOP)
        self.assertEqual(result.first_barrier_hit, BARRIER_STOP)

    def test_parity_mae_through_exit_bar_matches_compute_outcome(self):
        entry, stop, tp = 100.0, 90.0, 200.0
        po_bars = pd.DataFrame({
            "close": [98.0, 85.0, 50.0],
            "high":  [102.0, 90.0, 55.0],
            "low":   [94.0, 82.0, 40.0],
        })
        po_result = po.compute_outcome(entry, stop, tp, po_bars)
        self.assertEqual(po_result["first_barrier_hit"], "stop_loss")
        po_expected_mae_pct = round((min(94.0, 82.0) - entry) / entry * 100, 2)
        self.assertEqual(po_result["max_adverse_excursion"], po_expected_mae_pct)

        setup_e = make_setup(trigger=100.0, stop=90.0, sl_price=95.0, target=200.0)
        dates = sessions_after(SCAN_DATE, 3)
        fill_bar = (95.0, 102.0, 94.0, 98.0)
        stop_bar = (85.0, 90.0, 82.0, 85.0)
        extreme_dip_after_exit = (50.0, 55.0, 40.0, 45.0)
        df = make_bars(dates, [fill_bar, stop_bar, extreme_dip_after_exit])
        result = simulate_outcome(setup_e, df)
        self.assertEqual(result.status, BARRIER_STOP)
        # Our module's MAE additionally includes the fill session's own
        # low (94) -- it monitors from the fill session onward, unlike
        # compute_outcome's T+0-excluded alert day -- but both agree the
        # post-exit extreme dip (40) is never considered.
        worst_low_ours = min(94.0, 82.0)
        self.assertAlmostEqual(result.mae_r, (worst_low_ours - 100.0) / 10.0, places=9)

    def test_parity_gap_through_stop_exit_price_matches_compute_outcome_convention(self):
        # Non-gap case: compute_outcome has no 'open' field at all, so it
        # always assumes the nominal stop level -- our module must agree
        # exactly whenever there is no gap (tests above cover the
        # gap-exists divergence explicitly).
        entry, stop, tp = 100.0, 90.0, 200.0
        po_bars = pd.DataFrame({"close": [92.0], "high": [93.0], "low": [89.0]})
        po_result = po.compute_outcome(entry, stop, tp, po_bars)
        self.assertEqual(po_result["first_barrier_hit"], "stop_loss")

        setup_f = make_setup(trigger=100.0, stop=90.0, sl_price=85.0, target=200.0)
        dates = sessions_after(SCAN_DATE, 2)
        fill_bar = (95.0, 102.0, 94.0, 98.0)
        stop_bar = (91.0, 93.0, 89.0, 92.0)  # open(91) > stop(90): not a gap
        df = make_bars(dates, [fill_bar, stop_bar])
        result = simulate_outcome(setup_f, df)
        self.assertEqual(result.status, BARRIER_STOP)
        self.assertEqual(result.exit_price, 90.0)  # nominal stop, matching compute_outcome


class TestPreFillWindowCompleteness(unittest.TestCase):
    """A fill-less pre-fill scan is only EXPIRED_UNFILLED if the expected-
    session list genuinely reaches validity_end; otherwise OPEN_AT_END."""

    def test_pre_fill_reference_ends_before_validity_end_is_open_at_end_not_expired(self):
        window = sessions_after(SCAN_DATE, 5)
        reference_sessions = window[:3]  # ends two sessions before validity_end
        df = make_bars(window[:3], [BENIGN_A, BENIGN_A, BENIGN_A])
        result = simulate_outcome(SETUP_A, df, reference_sessions=reference_sessions)
        self.assertEqual(result.status, OPEN_AT_END)
        self.assertNotEqual(result.status, EXPIRED_UNFILLED)

    def test_pre_fill_empty_reference_sessions_is_open_at_end(self):
        dates = sessions_after(SCAN_DATE, 1)
        df = make_bars(dates, [BENIGN_A])
        result = simulate_outcome(SETUP_A, df, reference_sessions=[])
        self.assertEqual(result.status, OPEN_AT_END)

    def test_post_fill_reference_sessions_fewer_than_30_is_open_at_end(self):
        fill_date = sessions_after(SCAN_DATE, 1)[0]
        post = sessions_after(fill_date, 10)  # reference only covers 10, not 30
        reference_sessions = [fill_date] + post
        df = make_bars([fill_date] + post, [FILL_ORDINARY_A] + [BENIGN_A] * 10)
        result = simulate_outcome(SETUP_A, df, reference_sessions=reference_sessions)
        self.assertEqual(result.status, OPEN_AT_END)


class TestFrameGuards(unittest.TestCase):
    """simulate_outcome() never raises on malformed/unusable data -- every
    bad shape resolves to DATA_GAP."""

    def test_df_future_none_is_data_gap(self):
        result = simulate_outcome(SETUP_A, None)
        self.assertEqual(result.status, DATA_GAP)

    def test_df_future_not_a_dataframe_is_data_gap(self):
        result = simulate_outcome(SETUP_A, "not a dataframe")
        self.assertEqual(result.status, DATA_GAP)

    def test_multiindex_and_uppercase_columns_are_normalized_not_a_gap(self):
        dates = sessions_after(SCAN_DATE, 2)
        df = make_bars(dates, [FILL_ORDINARY_A, TARGET_HIT_A])
        df.columns = pd.MultiIndex.from_product([[c.upper() for c in df.columns], ["SETUPCO"]])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, BARRIER_TARGET)

    def test_missing_required_column_is_data_gap(self):
        dates = sessions_after(SCAN_DATE, 1)
        df = make_bars(dates, [FILL_ORDINARY_A]).drop(columns=["low"])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, DATA_GAP)

    def test_unsorted_index_descending_order_is_data_gap(self):
        dates = sessions_after(SCAN_DATE, 2)
        df = make_bars(list(reversed(dates)), [FILL_ORDINARY_A, TARGET_HIT_A])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, DATA_GAP)

    def test_duplicate_dates_in_index_is_data_gap(self):
        dates = sessions_after(SCAN_DATE, 1)
        df = make_bars([dates[0], dates[0]], [FILL_ORDINARY_A, TARGET_HIT_A])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, DATA_GAP)

    def test_missing_session_status_max_is_unreachable_past_the_unsorted_guard(self):
        """_missing_session_status() now uses max(index_dates), not the
        last element -- but any frame where that distinction could matter
        is already rejected by the unsorted/duplicate-index guard above,
        so this just confirms the guard fires first."""
        dates = sessions_after(SCAN_DATE, 2)
        df = make_bars(list(reversed(dates)), [TARGET_HIT_A, FILL_ORDINARY_A])
        result = simulate_outcome(SETUP_A, df)
        self.assertEqual(result.status, DATA_GAP)


class TestSetupValidation(unittest.TestCase):
    """A malformed SetupDefinition is a caller bug -- raises ValueError,
    never silently misbehaves or returns a data-problem status."""

    def test_non_finite_trigger_raises_value_error(self):
        bad_setup = replace(SETUP_A, trigger=float("nan"))
        dates = sessions_after(SCAN_DATE, 1)
        df = make_bars(dates, [FILL_ORDINARY_A])
        with self.assertRaises(ValueError):
            simulate_outcome(bad_setup, df)

    def test_infinite_stop_raises_value_error(self):
        bad_setup = replace(SETUP_A, stop=float("-inf"))
        dates = sessions_after(SCAN_DATE, 1)
        df = make_bars(dates, [FILL_ORDINARY_A])
        with self.assertRaises(ValueError):
            simulate_outcome(bad_setup, df)

    def test_stop_not_less_than_trigger_raises_value_error(self):
        bad_setup = replace(SETUP_A, stop=120.0)  # stop(120) > trigger(110)
        dates = sessions_after(SCAN_DATE, 1)
        df = make_bars(dates, [FILL_ORDINARY_A])
        with self.assertRaises(ValueError):
            simulate_outcome(bad_setup, df)

    def test_target_not_greater_than_trigger_raises_value_error(self):
        bad_setup = replace(SETUP_A, target=105.0)  # target(105) < trigger(110)
        dates = sessions_after(SCAN_DATE, 1)
        df = make_bars(dates, [FILL_ORDINARY_A])
        with self.assertRaises(ValueError):
            simulate_outcome(bad_setup, df)


class TestReferenceSessionsCleaning(unittest.TestCase):
    """reference_sessions is normalized once up front (deduped, sorted,
    missing/unparsable entries dropped) -- the same guarding posture
    df_future itself gets via _frame_is_malformed()."""

    def test_duplicate_date_in_reference_sessions_does_not_inflate_session_count(self):
        fill_date = sessions_after(SCAN_DATE, 1)[0]
        post = sessions_after(fill_date, 29)  # one short of the 30-session timeout
        df = make_bars([fill_date] + post, [FILL_ORDINARY_A] + [BENIGN_A] * 29)

        clean_reference = [fill_date] + post
        result_clean = simulate_outcome(SETUP_A, df, reference_sessions=clean_reference)
        self.assertEqual(result_clean.status, OPEN_AT_END)

        # The exact review repro: duplicating one date must NOT inflate
        # the session count to 30 and falsely resolve as timeout_30.
        duplicated_reference = clean_reference + [post[5]]
        result_dup = simulate_outcome(SETUP_A, df, reference_sessions=duplicated_reference)
        self.assertEqual(result_dup.status, OPEN_AT_END)
        self.assertNotEqual(result_dup.status, BARRIER_TIMEOUT_30)
        self.assertIsNone(result_dup.sessions_held)

    def test_unsorted_reference_sessions_gives_same_result_as_sorted(self):
        fill_date = sessions_after(SCAN_DATE, 1)[0]
        post = sessions_after(fill_date, 2)
        stop_bar = (98.0, 100.0, 90.0, 92.0)
        df = make_bars([fill_date] + post, [FILL_ORDINARY_A, BENIGN_A, stop_bar])

        sorted_reference = [fill_date, post[0], post[1]]
        unsorted_reference = [post[1], fill_date, post[0]]

        result_sorted = simulate_outcome(SETUP_A, df, reference_sessions=sorted_reference)
        result_unsorted = simulate_outcome(SETUP_A, df, reference_sessions=unsorted_reference)

        self.assertEqual(result_sorted.status, BARRIER_STOP)
        self.assertEqual(result_unsorted.status, result_sorted.status)
        self.assertEqual(result_unsorted.exit_price, result_sorted.exit_price)
        self.assertEqual(result_unsorted.sessions_held, result_sorted.sessions_held)

    def test_reference_sessions_with_none_and_nat_entries_does_not_raise(self):
        fill_date = sessions_after(SCAN_DATE, 1)[0]
        post = sessions_after(fill_date, 2)
        stop_bar = (98.0, 100.0, 90.0, 92.0)
        df = make_bars([fill_date] + post, [FILL_ORDINARY_A, BENIGN_A, stop_bar])

        messy_reference = [fill_date, None, pd.NaT, post[0], post[1]]
        clean_reference = [fill_date, post[0], post[1]]

        result_messy = simulate_outcome(SETUP_A, df, reference_sessions=messy_reference)
        result_clean = simulate_outcome(SETUP_A, df, reference_sessions=clean_reference)

        self.assertEqual(result_messy.status, BARRIER_STOP)
        self.assertEqual(result_messy.status, result_clean.status)
        self.assertEqual(result_messy.exit_price, result_clean.exit_price)
        self.assertEqual(result_messy.sessions_held, result_clean.sessions_held)

    def test_tz_aware_and_tz_naive_reference_entries_match(self):
        fill_date = sessions_after(SCAN_DATE, 1)[0]
        post = sessions_after(fill_date, 2)
        stop_bar = (98.0, 100.0, 90.0, 92.0)
        df = make_bars([fill_date] + post, [FILL_ORDINARY_A, BENIGN_A, stop_bar])

        naive_reference = [fill_date, post[0], post[1]]
        mixed_reference = [
            pd.Timestamp(fill_date, tz="UTC"),
            post[0],
            pd.Timestamp(post[1], tz="America/New_York"),
        ]

        result_naive = simulate_outcome(SETUP_A, df, reference_sessions=naive_reference)
        result_mixed = simulate_outcome(SETUP_A, df, reference_sessions=mixed_reference)

        self.assertEqual(result_mixed.status, result_naive.status)
        self.assertEqual(result_mixed.exit_price, result_naive.exit_price)
        self.assertEqual(result_mixed.sessions_held, result_naive.sessions_held)


class TestChaseRrBelow3FloatSafety(unittest.TestCase):
    """RR_EPSILON (§8/§12 float safety, promoting V3 to primary): a
    MEASURED_3R-shaped setup's rr, recomputed at a no-gap fill exactly at
    the trigger, must never spuriously reject as CHASE_RR_BELOW_3 due to
    float drift (target was built as trigger + min_rr*risk; recomputing
    (target-trigger)/risk at fill can land a hair under min_rr by
    subtraction/division rounding alone)."""

    def test_measured_3r_setup_never_chases_on_a_fill_exactly_at_trigger(self):
        import random
        rng = random.Random(4321)
        for _ in range(200):
            trigger = rng.uniform(10.0, 500.0)
            risk_fraction = rng.uniform(0.01, 0.10)
            stop = trigger * (1.0 - risk_fraction)
            risk = trigger - stop
            target = trigger + V1_OUTCOME_PARAMS.min_rr * risk
            setup = make_setup(trigger, stop, stop, target)
            dates = sessions_after(SCAN_DATE, 1)
            bar = (trigger, trigger + 0.01, trigger - 0.01, trigger)  # no-gap fill at trigger
            df = make_bars(dates, [bar])
            result = simulate_outcome(setup, df)
            self.assertNotEqual(result.status, CHASE_RR_BELOW_3,
                                 f"trigger={trigger}, stop={stop}, target={target}")

    def test_existing_chase_rr_boundary_tests_unaffected_by_epsilon(self):
        """Re-exercises TestFillRRGateOrdering's own exact-3.0-boundary
        case to confirm RR_EPSILON doesn't change pre-existing behavior."""
        dates = sessions_after(SCAN_DATE, 2)
        df = make_bars(dates, [FILL_ORDINARY_A, TARGET_HIT_A])  # RR == 3.0 exactly
        result = simulate_outcome(SETUP_A, df)
        self.assertNotEqual(result.status, CHASE_RR_BELOW_3)
        self.assertEqual(result.status, BARRIER_TARGET)


if __name__ == "__main__":
    unittest.main()
