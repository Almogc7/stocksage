"""Tests for setups/definition.py (docs/SETUP_SPEC.md §5-9)."""
import datetime
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests.fixtures import make_falling_df, make_rising_df, make_trending_df

from setups.definition import Rejection, SetupDefinition, define_setup
from setups.params import SetupParams
from setups.reason_codes import (
    DATA_NOT_FINAL,
    EXTENSION_VETO,
    INSUFFICIENT_HISTORY,
    MISSING_OHLCV,
    NO_RESISTANCE,
    NO_SWING_STRUCTURE,
    PULLBACK_LOW_UNCONFIRMED,
    PULLBACK_TOO_OLD,
    PULLBACK_TOO_SHALLOW,
    PRICE_OUTSIDE_RANGE,
    REGIME_VETO,
    RR_BELOW_3,
    STOP_CAP_EXCEEDED,
)


def _ramp(n, direction):
    return [direction] * n


def _frame_from_close(close, index=None, start="2026-06-15"):
    close = np.array(close, dtype=float)
    n = len(close)
    if index is None:
        index = pd.bdate_range(end=start, periods=n)
    return pd.DataFrame(
        {"open": close.copy(), "high": close + 1.0, "low": close - 1.0,
         "close": close, "volume": np.full(n, 1_000_000.0)},
        index=pd.DatetimeIndex(index, name="Date"),
    )


def _bull_spy(n, index=None, start="2026-06-15"):
    close = np.linspace(400.0, 420.0, n)
    return _frame_from_close(close, index=index, start=start)


def _golden_close():
    """A hand-built close series where high=close+1 and low=close-1 on
    every bar, with consecutive closes moving by at most 1 -- the true
    range is therefore exactly 2 on EVERY bar (the high-low component
    alone already equals 2, and the two gap components,
    |high-prev_close|/|low-prev_close|, are bounded at <=2 by the <=1
    close-to-close step), so ATR(14) comes out to exactly 2.0 with the
    REAL calc_atr(), no mocking needed.

    A 150-bar gentle lead-in (90 -> 117, slope ~0.18/bar) keeps the
    trailing SMA150 close to the later price action so the extension
    veto never trips just from the lead-in's own shape. Then, within the
    last 50 bars (the default swing_lookback_bars):
      - rise to an OLDER, higher confirmed swing high at close=123
        (HIGH=124, the eventual resistance target);
      - decline to 97, rise to close=99 (HIGH=100, SH/trigger), confirmed
        by 2 lower bars on each side;
      - decline to close=94 (LOW=93, SL, the pullback low);
      - hold flat at close=94 for 8 bars (SL stays the global minimum,
        confirmed >=2 sessions old, well within the 15-session recency
        ceiling);
      - recover to close=97 -- the final bar, strictly between SL(93)
        and SH(100).

    Reproduces docs/SETUP_SPEC.md §7's worked example exactly: ATR=2.00,
    trigger=100, SL=93 -> stop=92, risk=8, target=124, rr=3.0.
    """
    lead = np.linspace(90, 117, 150).round().astype(int).tolist()
    local = (
        _ramp(123 - lead[-1], 1)
        + _ramp(123 - 97, -1)
        + _ramp(99 - 97, 1)
        + _ramp(99 - 94, -1)
        + _ramp(8, 0)
        + _ramp(97 - 94, 1)
    )
    close = list(lead)
    for step in local:
        close.append(close[-1] + step)
    return close


class TestGoldenPath(unittest.TestCase):
    """Unmocked: real calc_atr, real swing detection -- proves the real
    arithmetic lines up with §7's worked example before the separately
    mocked boundary tests (below) are trusted."""

    def test_matches_worked_example_exactly(self):
        close = _golden_close()
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)
        scan_date = df.index[-1].date()

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, SetupDefinition)
        self.assertEqual(result.sh_price, 100.0)
        self.assertEqual(result.sl_price, 93.0)
        self.assertEqual(result.trigger, 100.0)
        self.assertEqual(result.stop, 92.0)
        self.assertEqual(result.risk, 8.0)
        self.assertEqual(result.target, 124.0)
        self.assertEqual(result.rr, 3.0)
        self.assertEqual(result.atr, 2.0)
        self.assertEqual(result.regime, "BULL")


class TestStaleness(unittest.TestCase):
    """The golden frame (an accepted setup) reused unmodified -- only
    scan_date or spy_df's own freshness varies -- to isolate staleness
    handling from every structural check."""

    def test_stale_df_rejects_data_not_final(self):
        """scan_date is one calendar day past df's actual last bar --
        the last bar is no longer exactly the scan date, so the
        candidate is rejected outright (df staleness is NOT the same as
        spy staleness, which only degrades the regime -- see below)."""
        close = _golden_close()
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)
        real_scan_date = df.index[-1].date()
        stale_scan_date = real_scan_date + datetime.timedelta(days=1)

        result = define_setup(df, stale_scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, DATA_NOT_FINAL)
        self.assertEqual(result.regime, "UNKNOWN")

    def test_stale_spy_degrades_regime_to_unknown_not_an_immediate_reject(self):
        """spy_df's last bar is a few days older than scan_date -- df
        itself is fresh, so every structural check still passes; only
        the regime veto (checked last) ultimately rejects, via the same
        regime="UNKNOWN" path as a too-short or NaN SPY history."""
        close = _golden_close()
        df = _frame_from_close(close)
        scan_date = df.index[-1].date()
        stale_spy = _bull_spy(len(close), index=df.index).iloc[:-3]

        result = define_setup(df, scan_date, stale_spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, REGIME_VETO)
        self.assertEqual(result.regime, "UNKNOWN")

    def test_scan_date_before_all_df_history_rejects_not_raises(self):
        """scan_date earlier than every row df has (e.g. a newly-listed
        symbol, or a caller bug) leaves truncate_to_date() returning an
        EMPTY frame -- must reject with DATA_NOT_FINAL, never raise
        IndexError trying to read an empty frame's last row."""
        close = _golden_close()
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)

        result = define_setup(df, datetime.date(2020, 1, 1), spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, DATA_NOT_FINAL)
        self.assertEqual(result.regime, "UNKNOWN")

    def test_spy_df_entirely_after_scan_date_degrades_regime_not_raises(self):
        """spy_df's earliest row is already after scan_date, so it's
        EMPTY after truncation -- same empty-frame case as df above, but
        must only degrade regime to "UNKNOWN" (not an immediate reject),
        consistent with every other spy-data-problem case."""
        close = _golden_close()
        df = _frame_from_close(close)
        scan_date = df.index[-1].date()
        late_spy = _bull_spy(
            5, index=pd.bdate_range(start=df.index[-1] + pd.Timedelta(days=1), periods=5)
        )

        result = define_setup(df, scan_date, late_spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, REGIME_VETO)
        self.assertEqual(result.regime, "UNKNOWN")

    def test_tz_aware_indexes_are_not_spuriously_flagged_stale(self):
        """Both df and spy_df have tz-aware indexes whose dates DO match
        scan_date -- must accept normally, not false-positive on
        DATA_NOT_FINAL/regime="UNKNOWN" just from tz-awareness."""
        close = _golden_close()
        naive_index = pd.bdate_range(end="2026-06-15", periods=len(close))
        scan_date = naive_index[-1].date()
        tz_index = naive_index.tz_localize("America/New_York")

        df = _frame_from_close(close, index=tz_index)
        spy = _bull_spy(len(close), index=tz_index)

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, SetupDefinition)
        self.assertEqual(result.regime, "BULL")


class TestAtrGuard(unittest.TestCase):

    def test_atr_none_rejects_missing_ohlcv(self):
        self._assert_bad_atr_rejects(None)

    def test_atr_infinite_rejects_missing_ohlcv(self):
        self._assert_bad_atr_rejects(float("inf"))

    def test_atr_non_numeric_string_rejects_missing_ohlcv(self):
        self._assert_bad_atr_rejects("abc")

    def _assert_bad_atr_rejects(self, bad_atr_value):
        close = _golden_close()
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)
        scan_date = df.index[-1].date()

        with patch("setups.definition.calc_atr", return_value={"atr": bad_atr_value}):
            result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, MISSING_OHLCV)


class TestStructuralRejections(unittest.TestCase):

    def test_no_swing_structure_on_monotonic_frame(self):
        """A strictly monotonic series has no 5-bar fractal high anywhere
        (every candidate's forward neighbors are always higher) -- so
        find_confirmed_swing_highs() is always empty."""
        close = list(range(50, 250))
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)
        scan_date = df.index[-1].date()

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, NO_SWING_STRUCTURE)

    def test_pullback_too_shallow(self):
        """SH-SL < 1.0*ATR. ATR is inflated here by real, large swings
        well AFTER the pullback (within ATR(14)'s own 14-bar lookback),
        while the pullback itself stays tiny -- both real, unmocked."""
        lead = list(np.linspace(90, 95, 150))
        local = [96, 97, 98, 99, 98.7, 98.5]
        level = 102
        for _ in range(9):
            local.append(level)
            level = 106 if level == 102 else 102
        local.append(99.0)
        close = lead + local
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)
        scan_date = df.index[-1].date()

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, PULLBACK_TOO_SHALLOW)

    def test_price_outside_range_already_broken_out(self):
        """§5 item 5, the reachable direction: close already >= SH
        (the breakout already happened same-day, out of scope for a
        *pending* setup). The other direction (close <= SL) cannot occur
        as a DISTINCT rejection given this algorithm: low <= close always
        (basic OHLC ordering), so any close at or below the existing SL
        necessarily drags the bar's own low below SL too -- making that
        bar the new global minimum, which is always the last bar, which
        is always "within the last 2 rows" -- so PULLBACK_LOW_UNCONFIRMED
        (an earlier check, step 4) fires first, every time, before this
        check is ever reached for that direction."""
        close = _golden_close()[:-3] + [99, 100, 101]
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)
        scan_date = df.index[-1].date()

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, PRICE_OUTSIDE_RANGE)

    def test_pullback_too_old(self):
        """SL recency (§5 item 4): the global-minimum low is identified
        correctly, but it's more than 15 sessions before the scan date --
        a separate check from the search itself, since prices recovered
        immediately after touching the low and never revisited it."""
        lead = np.linspace(90, 117, 150).round().astype(int).tolist()
        local = (
            _ramp(123 - lead[-1], 1)
            + _ramp(123 - 97, -1)
            + _ramp(99 - 97, 1)
            + _ramp(99 - 94, -1)
            + _ramp(97 - 94, 1)   # recover immediately -- never revisits the low
            + _ramp(20, 0)        # 20 more bars elapse before the scan date
        )
        close = list(lead)
        for step in local:
            close.append(close[-1] + step)
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)
        scan_date = df.index[-1].date()
        wide_params = SetupParams(swing_lookback_bars=80)

        result = define_setup(df, scan_date, spy, params=wide_params)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, PULLBACK_TOO_OLD)

    def test_pullback_low_unconfirmed_never_reanchors_to_older_minor_low(self):
        """An older, higher (confirmed-old) minor low exists, but the
        TRUE global-minimum low sits in the very last bar (a crash).
        Must reject outright -- never silently fall back to the older,
        non-minimal low as SL."""
        lead = np.linspace(90, 117, 150).round().astype(int).tolist()
        local = (
            _ramp(123 - lead[-1], 1)
            + _ramp(123 - 97, -1)
            + _ramp(99 - 97, 1)
            + _ramp(99 - 96, -1)   # older minor low: close=96 (LOW=95)
            + _ramp(98 - 96, 1)    # recover to 98
            + _ramp(8, 0)          # hold -- the 95 low is confirmed-old
            + _ramp(98 - 89, -1)   # crash to close=89 (LOW=88) on the LAST bar
        )
        close = list(lead)
        for step in local:
            close.append(close[-1] + step)
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)
        scan_date = df.index[-1].date()

        self.assertEqual(close[-1], 89.0)

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, PULLBACK_LOW_UNCONFIRMED)

    def test_no_resistance_when_no_older_confirmed_high_above_sh(self):
        """Same SH/SL/pullback shape as the golden frame, but with the
        older 124 resistance peak removed entirely -- the common,
        expected-by-the-spec-itself case (§8's own arithmetic note: an
        uptrend's most recent high is usually its highest point so far)."""
        lead = np.linspace(90, 95, 150).round().astype(int).tolist()
        local = (
            _ramp(99 - lead[-1], 1)
            + _ramp(99 - 94, -1)
            + _ramp(8, 0)
            + _ramp(97 - 94, 1)
        )
        close = list(lead)
        for step in local:
            close.append(close[-1] + step)
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)
        scan_date = df.index[-1].date()

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, NO_RESISTANCE)
        self.assertEqual(result.regime, "BULL")


class TestRegimeVeto(unittest.TestCase):
    """The golden frame itself (an accepted setup under a BULL spy_df) is
    reused unmodified -- only spy_df varies -- to isolate the regime
    veto from every structural check above it."""

    def test_regime_veto_when_spy_below_its_own_sma150(self):
        close = _golden_close()
        df = _frame_from_close(close)
        scan_date = df.index[-1].date()
        declining_spy = _frame_from_close(np.linspace(420.0, 350.0, len(close)), index=df.index)

        result = define_setup(df, scan_date, declining_spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, REGIME_VETO)
        self.assertEqual(result.regime, "BEAR")

    def test_regime_veto_when_spy_history_too_short(self):
        close = _golden_close()
        df = _frame_from_close(close)
        scan_date = df.index[-1].date()
        short_spy = _frame_from_close(np.linspace(400.0, 420.0, 100), index=df.index[-100:])

        result = define_setup(df, scan_date, short_spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, REGIME_VETO)
        self.assertEqual(result.regime, "UNKNOWN")

    def test_regime_veto_when_spy_close_is_nan(self):
        close = _golden_close()
        df = _frame_from_close(close)
        scan_date = df.index[-1].date()
        spy = _bull_spy(len(close), index=df.index)
        spy.iloc[-1, spy.columns.get_loc("close")] = float("nan")

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, REGIME_VETO)
        self.assertEqual(result.regime, "UNKNOWN")


class TestGuards(unittest.TestCase):

    def test_window_finiteness_guard_catches_an_older_nan_low(self):
        """A NaN in an OLDER bar's low, within the swing_lookback_bars
        window but older than calc_atr()'s own 14-bar lookback -- so a
        bad-ATR guard alone would never catch it. Must reject outright,
        not silently exclude that bar from ever winning a fractal
        comparison (NaN > x is always False)."""
        close = _golden_close()
        df = _frame_from_close(close)
        df.iloc[150 + 5, df.columns.get_loc("low")] = float("nan")
        spy = _bull_spy(len(close), index=df.index)
        scan_date = df.index[-1].date()

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, MISSING_OHLCV)
        self.assertEqual(result.regime, "UNKNOWN")

    def test_insufficient_history_guard_on_short_frame(self):
        """Fewer than 150 bars -> the unrounded SMA150 is NaN -> must
        reject with INSUFFICIENT_HISTORY, not silently pass the
        extension veto via a NaN > x comparison that's always False."""
        local_only = _golden_close()[150:]
        pad = [98]
        for _ in range(19):
            pad.append(pad[-1] + 1)
        close = pad + local_only
        df = _frame_from_close(close)
        spy = _bull_spy(len(close), index=df.index)
        scan_date = df.index[-1].date()

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, INSUFFICIENT_HISTORY)

    def test_missing_column_on_df_rejects_not_raises(self):
        df = _frame_from_close(_golden_close()).drop(columns=["volume"])
        spy = _bull_spy(len(df), index=df.index)
        scan_date = df.index[-1].date()

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.reason_code, MISSING_OHLCV)
        self.assertEqual(result.regime, "UNKNOWN")

    def test_missing_column_on_spy_df_degrades_regime_not_raises(self):
        """A missing spy_df column is a SPY-data problem, same family as
        spy_df=None/empty/stale -- it degrades regime to "UNKNOWN"
        (which the regime veto then rejects) rather than hard-rejecting
        immediately with MISSING_OHLCV the way a missing DF column does."""
        df = _frame_from_close(_golden_close())
        spy = _bull_spy(len(df), index=df.index).drop(columns=["high"])
        scan_date = df.index[-1].date()

        result = define_setup(df, scan_date, spy)

        self.assertIsInstance(result, Rejection)
        self.assertEqual(result.regime, "UNKNOWN")


def _boundary_base_frame():
    """Minimal frame: flat baseline, then a dip to LOW=91 (confirmed-old),
    recovering to a final close of 96. calc_atr/find_confirmed_swing_highs/
    _unrounded_sma150/_compute_regime are mocked in every boundary test
    below -- hand-crafting raw OHLCV to land on an EXACT float boundary
    (rr==3.0 vs a hair under; stop distance exactly at the cap vs a hair
    over; extension exactly at 6*atr vs a hair over) by chance is
    impractical; the golden-path test above already proves the real,
    unmocked arithmetic lines up with the spec's own numbers."""
    close = [100] * 10 + [100, 98, 96, 94, 92] + [93, 94, 95, 96] + [96] * 5
    df = _frame_from_close(close)
    spy = _bull_spy(len(close), index=df.index)
    scan_date = df.index[-1].date()
    sh_date_stub = df.index[0].date()
    return df, spy, scan_date, sh_date_stub


def _run_boundary(df, scan_date, spy, atr, confirmed_highs, sma150):
    with patch("setups.definition.calc_atr", return_value={"atr": atr}), \
         patch("setups.definition.find_confirmed_swing_highs", return_value=confirmed_highs), \
         patch("setups.definition._unrounded_sma150", return_value=sma150), \
         patch("setups.definition._compute_regime", return_value="BULL"):
        return define_setup(df, scan_date, spy)


class TestBoundaries(unittest.TestCase):
    """Real SL=91 (from the base frame's own low column), mocked
    atr/SH-target/sma150/regime -- isolates each gate's exact arithmetic
    boundary. risk = trigger - (91 - 0.5*atr) = 9.5 + 0.5*atr."""

    def test_rr_exactly_3_passes_just_under_rejects(self):
        df, spy, scan_date, sh_date = _boundary_base_frame()
        older = df.index[1].date()

        passing = _run_boundary(df, scan_date, spy, 2.0, [(older, 130.0), (sh_date, 100.0)], 90.0)
        self.assertIsInstance(passing, SetupDefinition)
        self.assertEqual(passing.rr, 3.0)

        failing = _run_boundary(df, scan_date, spy, 2.0, [(older, 129.9), (sh_date, 100.0)], 90.0)
        self.assertIsInstance(failing, Rejection)
        self.assertEqual(failing.reason_code, RR_BELOW_3)

    def test_stop_cap_exactly_at_cap_passes_just_over_rejects(self):
        df, spy, scan_date, sh_date = _boundary_base_frame()
        older = df.index[1].date()

        # atr=2.0 -> risk = 9.5+1.0 = 10.5... recompute with real SL=91:
        # risk = 100 - (91 - 0.5*atr); atr=2.0 -> risk = 100-90 = 10;
        # cap = min(0.12*100, 5*2.0) = min(12, 10) = 10 -> risk == cap (passes).
        passing = _run_boundary(df, scan_date, spy, 2.0, [(older, 130.0), (sh_date, 100.0)], 90.0)
        self.assertIsInstance(passing, SetupDefinition)
        self.assertEqual(passing.risk, 10.0)

        # atr=1.9 -> risk = 100-(91-0.95) = 9.95; cap = min(12, 9.5) = 9.5
        # -> risk(9.95) > cap(9.5), exceeded.
        failing = _run_boundary(df, scan_date, spy, 1.9, [(older, 130.0), (sh_date, 100.0)], 90.0)
        self.assertIsInstance(failing, Rejection)
        self.assertEqual(failing.reason_code, STOP_CAP_EXCEEDED)

    def test_extension_exactly_at_6atr_passes_just_over_rejects(self):
        df, spy, scan_date, sh_date = _boundary_base_frame()
        older = df.index[1].date()
        # current_close = 96 (real, final bar). atr=2.0 -> 6*atr = 12.
        # sma150 = 96-12 = 84 -> diff == 12 -> passes (gate is strict ">").
        passing = _run_boundary(df, scan_date, spy, 2.0, [(older, 130.0), (sh_date, 100.0)], 84.0)
        self.assertIsInstance(passing, SetupDefinition)

        failing = _run_boundary(df, scan_date, spy, 2.0, [(older, 130.0), (sh_date, 100.0)], 83.9)
        self.assertIsInstance(failing, Rejection)
        self.assertEqual(failing.reason_code, EXTENSION_VETO)


def _bull_spy_like(df):
    close = np.linspace(400.0, 420.0, len(df))
    return pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
         "volume": np.full(len(df), 5_000_000.0)},
        index=df.index,
    )


class TestNoLookAheadProperty(unittest.TestCase):

    def _assert_same_result(self, clean, corrupted):
        self.assertEqual(type(clean), type(corrupted))
        self.assertEqual(vars(clean), vars(corrupted))

    def test_random_seeds_df_corruption(self):
        makers = (
            [(make_trending_df, s) for s in range(15)]
            + [(make_falling_df, s) for s in (1, 2)]
            + [(make_rising_df, s) for s in (1, 2)]
        )
        for maker, seed in makers:
            with self.subTest(maker=maker.__name__, seed=seed):
                df_full = maker(n=260, seed=seed)
                scan_idx = 240
                scan_date = df_full.index[scan_idx].date()
                spy_full = _bull_spy_like(df_full)

                clean = define_setup(df_full.iloc[: scan_idx + 1], scan_date,
                                      spy_full.iloc[: scan_idx + 1])

                for corrupt_mode in ("extreme", "nan"):
                    corrupted = df_full.copy()
                    if corrupt_mode == "extreme":
                        corrupted.iloc[scan_idx + 1:] = corrupted.iloc[scan_idx + 1:] * 1000.0
                    else:
                        corrupted.iloc[scan_idx + 1:] = np.nan
                    result = define_setup(corrupted, scan_date, spy_full)
                    self._assert_same_result(clean, result)

    def test_random_seeds_spy_corruption(self):
        df_full = make_trending_df(n=260, seed=3)
        scan_idx = 240
        scan_date = df_full.index[scan_idx].date()
        spy_full = _bull_spy_like(df_full)

        clean = define_setup(df_full.iloc[: scan_idx + 1], scan_date,
                              spy_full.iloc[: scan_idx + 1])

        for corrupt_mode in ("extreme", "nan"):
            spy_corrupted = spy_full.copy()
            if corrupt_mode == "extreme":
                spy_corrupted.iloc[scan_idx + 1:] = spy_corrupted.iloc[scan_idx + 1:] * 1000.0
            else:
                spy_corrupted.iloc[scan_idx + 1:] = np.nan
            result = define_setup(df_full.iloc[: scan_idx + 1], scan_date, spy_corrupted)
            self._assert_same_result(clean, result)

    def test_golden_frame_with_breakout_and_crash_appended(self):
        close = _golden_close()
        base_index = pd.bdate_range(end="2026-06-15", periods=len(close))
        scan_date = base_index[-1].date()
        clean_df = _frame_from_close(close, index=base_index)
        clean_spy = _bull_spy(len(close), index=base_index)
        clean = define_setup(clean_df, scan_date, clean_spy)
        self.assertIsInstance(clean, SetupDefinition)

        extra_dates = pd.bdate_range(start=base_index[-1] + pd.Timedelta(days=1), periods=5)
        full_index = base_index.append(extra_dates)
        spy_tail_close = list(np.linspace(400.0, 420.0, len(close))) + [420.0] * 5

        tails = {
            "breakout": [99, 101, 105, 110, 115],
            "crash": [90, 80, 70, 60, 50],
            "extreme": [v * 1000 for v in [97, 97, 97, 97, 97]],
            "nan": [float("nan")] * 5,
        }
        for name, tail in tails.items():
            with self.subTest(corruption=name):
                df_ext = _frame_from_close(close + tail, index=full_index)
                spy_ext = _frame_from_close(spy_tail_close, index=full_index)
                result = define_setup(df_ext, scan_date, spy_ext)
                self._assert_same_result(clean, result)


if __name__ == "__main__":
    unittest.main()
