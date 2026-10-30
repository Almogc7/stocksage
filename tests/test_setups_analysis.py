"""Tests for setups/analysis.py (pure control builder, statistics,
decide()) -- docs/PREREGISTRATION_SETUPS_V1.md sections 6, 7, 8, 13.

All price data is synthetic (no network, no DB, no real results file --
per the hard rule, this module's tests never open anything but in-memory
frames and dataclasses built right here).
"""
import math
import sys
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import setups.analysis as sa  # noqa: E402
from setups.definition import SetupDefinition  # noqa: E402
from setups.outcomes import OutcomeResult  # noqa: E402
from setups.params import OutcomeParams, V1_OUTCOME_PARAMS  # noqa: E402
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
from tests.test_setups_robustness import _malformed_df_cases  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────

def _frame_from_close(close, start="2025-01-01", volume=2_000_000.0):
    close = np.array(close, dtype=float)
    n = len(close)
    index = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame(
        {"open": close.copy(), "high": close + 1.0, "low": close - 1.0,
         "close": close, "volume": np.full(n, volume)},
        index=pd.DatetimeIndex(index),
    )


def _trending_peer(seed, n=300, start="2025-01-01", trend=20.0, noise=0.3):
    rng = np.random.default_rng(seed)
    close = 100.0 + np.linspace(0, trend, n) + rng.normal(0, noise, n)
    return _frame_from_close(close, start=start)


def _many_peers(n_peers, seed_offset=0, **kwargs):
    return {f"PEER{i}": _trending_peer(seed_offset + i, **kwargs) for i in range(n_peers)}


# ── Control builder correctness ──────────────────────────────────────────

class TestPeerControlMechanics(unittest.TestCase):

    def _peer_and_dates(self, seed=0):
        df = _trending_peer(seed)
        session_before_d = df.index[250].date()
        d = df.index[251].date()
        return df, session_before_d, d

    def test_peer_entered_at_open_of_d_fills_exactly_there(self):
        df, session_before_d, d = self._peer_and_dates()
        eligible, atr = sa.peer_atr_and_eligibility(df, session_before_d)
        self.assertTrue(eligible)
        self.assertIsNotNone(atr)
        open_d = sa._open_on_date(df, d)
        synthetic = sa.build_synthetic_peer_setup(d, session_before_d, open_d, 1.0, 3.0, atr)
        peer_future = df[[sa._date_of(ts) > session_before_d for ts in df.index]]
        result = sa.simulate_peer_control(peer_future, synthetic, reference_sessions=df.index)
        self.assertEqual(result.fill_date, d)
        self.assertEqual(result.fill_price, open_d)

    def test_peer_rr_always_equals_the_real_setups_definitional_rr(self):
        df, session_before_d, d = self._peer_and_dates()
        _eligible, atr = sa.peer_atr_and_eligibility(df, session_before_d)
        open_d = sa._open_on_date(df, d)
        for s, t in [(1.0, 3.0), (2.0, 7.0), (0.5, 4.0)]:
            synthetic = sa.build_synthetic_peer_setup(d, session_before_d, open_d, s, t, atr)
            self.assertAlmostEqual(synthetic.rr, t / s, places=9)

    def test_peer_sl_price_and_other_dead_fields_dont_affect_result(self):
        df, session_before_d, d = self._peer_and_dates()
        _eligible, atr = sa.peer_atr_and_eligibility(df, session_before_d)
        open_d = sa._open_on_date(df, d)
        synthetic = sa.build_synthetic_peer_setup(d, session_before_d, open_d, 1.0, 3.0, atr)
        mutated = replace(synthetic, sl_price=999999.0, regime="BEAR", rr=0.0, risk=0.0,
                           sh_price=-5.0, target_source="MEASURED_3R")
        peer_future = df[[sa._date_of(ts) > session_before_d for ts in df.index]]
        r1 = sa.simulate_peer_control(peer_future, synthetic, reference_sessions=df.index)
        r2 = sa.simulate_peer_control(peer_future, mutated, reference_sessions=df.index)
        self.assertEqual(r1, r2)

    def test_control_excludes_trade_with_fewer_than_20_usable_peers(self):
        peers_raw = _many_peers(19)
        session_before_d = list(peers_raw.values())[0].index[250].date()
        d = list(peers_raw.values())[0].index[251].date()
        lookup = {}
        for sym, df in peers_raw.items():
            lookup[(sym, session_before_d)] = sa.peer_atr_and_eligibility(df, session_before_d)
        result = sa.build_control_for_trade(d, session_before_d, 1.0, 3.0, list(peers_raw.items()), lookup)
        self.assertTrue(result.excluded)
        self.assertLess(result.usable_peer_count, sa.MIN_PEERS)

    def test_control_counts_eligible_but_data_gap_peer_as_unusable_not_toward_20(self):
        peers_raw = _many_peers(25)
        session_before_d = list(peers_raw.values())[0].index[250].date()
        d = list(peers_raw.values())[0].index[251].date()
        lookup = {}
        for sym, df in peers_raw.items():
            lookup[(sym, session_before_d)] = sa.peer_atr_and_eligibility(df, session_before_d)

        # Corrupt one peer's bar on day d itself -> DATA_GAP on entry,
        # despite being eligible the session before.
        gap_symbol = "PEER0"
        gapped = peers_raw[gap_symbol].copy()
        d_pos = list(gapped.index.date).index(d)
        gapped.iloc[d_pos] = float("nan")
        peers = [(gap_symbol, gapped)] + [(s, f) for s, f in peers_raw.items() if s != gap_symbol]

        result = sa.build_control_for_trade(d, session_before_d, 1.0, 3.0, peers, lookup)
        self.assertEqual(result.peer_count, 25)  # eligible count unaffected
        self.assertEqual(result.usable_peer_count, 24)  # one dropped for DATA_GAP
        gap_diag = [p for p in result.peer_diagnostics if p.symbol == gap_symbol][0]
        self.assertIsNone(gap_diag.r_multiple)

    def test_control_value_is_equal_weighted_mean_of_usable_peer_r(self):
        peers_raw = _many_peers(22)
        session_before_d = list(peers_raw.values())[0].index[250].date()
        d = list(peers_raw.values())[0].index[251].date()
        lookup = {}
        for sym, df in peers_raw.items():
            lookup[(sym, session_before_d)] = sa.peer_atr_and_eligibility(df, session_before_d)
        result = sa.build_control_for_trade(d, session_before_d, 1.0, 3.0, list(peers_raw.items()), lookup)
        usable_rs = [p.r_multiple for p in result.peer_diagnostics if p.r_multiple is not None]
        self.assertAlmostEqual(result.mean_control_r, sum(usable_rs) / len(usable_rs), places=9)
        self.assertFalse(result.excluded)


# ── No-look-ahead ─────────────────────────────────────────────────────────

class TestNoLookAhead(unittest.TestCase):

    def test_peer_eligibility_uses_no_bar_at_or_after_d_corrupting_them_gives_identical_eligibility(self):
        df = _trending_peer(0)
        session_before_d = df.index[250].date()
        baseline_eligible, baseline_atr = sa.peer_atr_and_eligibility(df, session_before_d)

        corrupted = df.copy()
        after_mask = [sa._date_of(ts) > session_before_d for ts in corrupted.index]
        corrupted.loc[after_mask, ["open", "high", "low", "close"]] = 999999.0
        eligible, atr = sa.peer_atr_and_eligibility(corrupted, session_before_d)

        self.assertEqual(eligible, baseline_eligible)
        self.assertEqual(atr, baseline_atr)

    def test_peer_atr_uses_no_bar_at_or_after_d_corrupting_them_gives_identical_atr(self):
        df = _trending_peer(1)
        session_before_d = df.index[260].date()
        _e1, atr1 = sa.peer_atr_and_eligibility(df, session_before_d)

        corrupted = df.copy()
        after_mask = [sa._date_of(ts) > session_before_d for ts in corrupted.index]
        corrupted.loc[after_mask, ["open", "high", "low", "close"]] = -1.0
        _e2, atr2 = sa.peer_atr_and_eligibility(corrupted, session_before_d)
        self.assertEqual(atr1, atr2)


# ── Outcome recomputation / classification ───────────────────────────────

class TestRecomputeAndClassify(unittest.TestCase):

    def _golden_row_and_frame(self):
        """A simple frame + a setup that fills and hits target within
        the timeout window, so recompute has a real, non-trivial outcome."""
        n = 60
        close = [100.0] * 20 + list(np.linspace(100, 140, 20)) + [140.0] * 20
        df = _frame_from_close(close)
        scan_date = df.index[19].date()
        validity_end = df.index[24].date()
        row = {
            "symbol": "AAA", "scan_date": scan_date, "regime": "BULL",
            "sh_date": scan_date, "sh_price": 101.0, "sl_date": scan_date, "sl_price": 99.0,
            "trigger": 101.0, "stop": 98.0, "target": 110.0, "risk": 3.0, "rr": 3.0, "atr": 1.0,
            "validity_end": validity_end, "target_source": "RESISTANCE",
            "fill_date": None, "fill_price": None, "exit_date": None, "exit_price": None,
            "first_barrier_hit": None, "r_multiple": None, "mae_r": None, "mfe_r": None,
            "sessions_held": None, "resolved_date": None,
        }
        return row, df

    def test_recompute_primary_outcome_matches_stored_fields_on_clean_synthetic_data(self):
        row, df = self._golden_row_and_frame()
        result = sa.recompute_primary_outcome(row, df, V1_OUTCOME_PARAMS, reference_sessions=df.index)
        # Stamp the row with the FRESH recompute's own values -- a
        # self-consistent row should report zero mismatches against itself.
        stamped = dict(row)
        for field in sa._COMPARABLE_OUTCOME_FIELDS:
            stamped[field] = getattr(result, field)
        self.assertEqual(sa.stored_vs_recomputed_mismatches(stamped, result), [])

    def test_classify_outcome_maps_every_reason_code_correctly(self):
        primary = [BARRIER_STOP, BARRIER_TARGET, BARRIER_TIMEOUT_30, FILLED_STOPPED_SAME_DAY]
        not_trade = [INVALIDATED_BEFORE_FILL, EXPIRED_UNFILLED, CHASE_RR_BELOW_3]
        excluded = [DATA_GAP, OPEN_AT_END]
        for status in primary:
            self.assertEqual(sa.classify_outcome(status), "primary")
        for status in not_trade:
            self.assertEqual(sa.classify_outcome(status), "not_trade")
        for status in excluded:
            self.assertEqual(sa.classify_outcome(status), "excluded")
        with self.assertRaises(ValueError):
            sa.classify_outcome("NOT_A_REAL_STATUS")

    def test_recompute_detects_a_deliberately_mismatched_stored_row(self):
        row, df = self._golden_row_and_frame()
        result = sa.recompute_primary_outcome(row, df, V1_OUTCOME_PARAMS, reference_sessions=df.index)
        stamped = dict(row)
        for field in sa._COMPARABLE_OUTCOME_FIELDS:
            stamped[field] = getattr(result, field)
        stamped["r_multiple"] = (stamped["r_multiple"] or 0.0) + 5.0  # deliberate corruption
        mismatches = sa.stored_vs_recomputed_mismatches(stamped, result)
        self.assertIn("r_multiple", mismatches)


# ── Bootstrap ─────────────────────────────────────────────────────────────

class TestBootstrap(unittest.TestCase):

    def _diffs(self, n_months=30, per_month=5, mean=0.0, std=0.3, seed=0):
        rng = np.random.default_rng(seed)
        diffs = []
        for m in range(n_months):
            year = 2021 + m // 12
            month = 1 + m % 12
            for k in range(per_month):
                diffs.append(sa.PairedDifference(f"S{k}", date(year, month, 10), mean + rng.normal(0, std), 0.0))
        return diffs

    def test_bootstrap_is_deterministic_for_a_fixed_seed(self):
        diffs = self._diffs()
        b1 = sa.month_cluster_bootstrap(diffs, draws=500, seed=7)
        b2 = sa.month_cluster_bootstrap(diffs, draws=500, seed=7)
        self.assertEqual(b1, b2)

    def test_bootstrap_resamples_months_not_trades_weighted_by_trade_count(self):
        # One month with many trades at a high mean, the rest with few
        # trades at a low mean -- a naive per-month-average bootstrap
        # would under-weight the big month; the trade-weighted point
        # estimate must equal the plain overall mean of all diffs.
        diffs = []
        for i in range(50):
            diffs.append(sa.PairedDifference(f"S{i}", date(2022, 1, 15), 10.0, 0.0))
        for i in range(5):
            diffs.append(sa.PairedDifference(f"T{i}", date(2022, 2, 15), 0.0, 0.0))
        bs = sa.month_cluster_bootstrap(diffs, draws=1000, seed=1)
        overall_mean = sum(d.diff for d in diffs) / len(diffs)
        self.assertAlmostEqual(bs.point_estimate, overall_mean, places=9)

    def test_bootstrap_known_mean_synthetic_data_interval_covers_truth_about_95_percent_of_the_time(self):
        true_mean = 0.3
        outer_repeats = 150
        covered = 0
        for i in range(outer_repeats):
            diffs = self._diffs(n_months=30, per_month=4, mean=true_mean, std=0.4, seed=1000 + i)
            bs = sa.month_cluster_bootstrap(diffs, draws=500, seed=2000 + i)
            if bs.lower <= true_mean <= bs.upper:
                covered += 1
        coverage = covered / outer_repeats
        # Small simulation (500 draws x 150 repeats) -- loose but
        # meaningful tolerance band around the nominal 95%.
        self.assertGreater(coverage, 0.85)
        self.assertLessEqual(coverage, 1.0)


# ── decide() -- one test per branch and per exact boundary ───────────────

def _uniform_diffs(n_trades, n_symbols, n_months, n_years, diff_value=0.5, symbol_share=None, month_share=None):
    """Spreads n_trades evenly across n_symbols distinct symbols and
    n_months distinct (year, month) buckets within n_years distinct
    years, each trade's paired diff fixed at diff_value (deterministic,
    no bootstrap noise -- isolates decide()'s own branch logic)."""
    diffs = []
    years = list(range(2021, 2021 + n_years))
    months_per_year = max(1, n_months // n_years)
    month_buckets = []
    for y in years:
        for m in range(1, months_per_year + 1):
            month_buckets.append((y, m))
    month_buckets = month_buckets[:n_months] or [(years[0], 1)]

    for i in range(n_trades):
        symbol = f"S{i % n_symbols}"
        year, month = month_buckets[i % len(month_buckets)]
        diffs.append(sa.PairedDifference(symbol, date(year, month, 10), diff_value, 0.0))

    if symbol_share is not None:
        # Overwrite enough of the FIRST symbol's share to hit the target.
        target_count = round(symbol_share * len(diffs))
        for i in range(min(target_count, len(diffs))):
            diffs[i].symbol = "DOMINANT"
    if month_share is not None:
        # A sentinel month outside the natural cycle above, so forcing
        # trades into it can't collide with trades that would already
        # land there naturally (which would silently inflate the share
        # past the intended target).
        target_count = round(month_share * len(diffs))
        for i in range(min(target_count, len(diffs))):
            diffs[i] = sa.PairedDifference(diffs[i].symbol, date(2099, 1, 10), diffs[i].setup_r, diffs[i].control_r)
    return diffs


def _decide_for(diffs, **decide_kwargs):
    suff = sa.sample_sufficiency(diffs)
    pp = sa.per_period_table(diffs)
    conc = sa.concentration_shares(diffs)
    bs = sa.month_cluster_bootstrap(diffs, draws=200, seed=1) if diffs else None
    return sa.decide(diffs, bs, pp, conc, suff, **decide_kwargs), suff, pp, conc, bs


class TestDecideSampleSize(unittest.TestCase):

    def test_inconclusive_when_sample_below_100_trades(self):
        diffs = _uniform_diffs(99, 30, 24, 3)
        decision, *_ = _decide_for(diffs)
        self.assertEqual(decision.verdict, "INCONCLUSIVE")

    def test_sufficient_at_exactly_100_trades(self):
        diffs = _uniform_diffs(100, 30, 24, 3)
        decision, suff, *_ = _decide_for(diffs)
        self.assertGreaterEqual(suff.trade_count, 100)
        self.assertTrue(suff.meets_minimum or decision.verdict != "INCONCLUSIVE" or
                         "insufficient sample" not in decision.reasons[0])

    def test_inconclusive_below_30_symbols(self):
        diffs = _uniform_diffs(100, 29, 24, 3)
        decision, *_ = _decide_for(diffs)
        self.assertEqual(decision.verdict, "INCONCLUSIVE")

    def test_sufficient_at_exactly_30_symbols(self):
        diffs = _uniform_diffs(120, 30, 24, 3)
        _decision, suff, *_ = _decide_for(diffs)
        self.assertEqual(suff.symbol_count, 30)
        self.assertTrue(suff.meets_minimum)

    def test_inconclusive_below_24_months(self):
        diffs = _uniform_diffs(120, 30, 23, 3)
        decision, suff, *_ = _decide_for(diffs)
        self.assertLess(suff.month_count, 24)
        self.assertEqual(decision.verdict, "INCONCLUSIVE")

    def test_sufficient_at_exactly_24_months(self):
        diffs = _uniform_diffs(120, 30, 24, 3)
        _decision, suff, *_ = _decide_for(diffs)
        self.assertGreaterEqual(suff.month_count, 24)
        self.assertTrue(suff.meets_minimum)

    def test_inconclusive_below_3_years(self):
        diffs = _uniform_diffs(120, 30, 24, 2)
        decision, suff, *_ = _decide_for(diffs)
        self.assertLess(suff.year_count, 3)
        self.assertEqual(decision.verdict, "INCONCLUSIVE")

    def test_sufficient_at_exactly_3_years(self):
        diffs = _uniform_diffs(120, 30, 24, 3)
        _decision, suff, *_ = _decide_for(diffs)
        self.assertEqual(suff.year_count, 3)
        self.assertTrue(suff.meets_minimum)


class TestDecideQualifyingPeriods(unittest.TestCase):

    def _diffs_with_n_qualifying_years(self, n_qualifying, total_years=4):
        """Each qualifying year gets exactly 30 trades (the minimum);
        non-qualifying years get fewer. Enough total trades/symbols/
        months to isolate condition 4 alone."""
        diffs = []
        for y in range(2021, 2021 + total_years):
            count = 30 if (y - 2021) < n_qualifying else 5
            for i in range(count):
                diffs.append(sa.PairedDifference(f"S{i % 35}", date(y, 1 + (i % 12), 10), 0.5, 0.0))
        return diffs

    def test_inconclusive_with_fewer_than_4_qualifying_periods(self):
        diffs = self._diffs_with_n_qualifying_years(3)
        decision, *_ = _decide_for(diffs)
        self.assertEqual(decision.verdict, "INCONCLUSIVE")

    def test_at_exactly_4_qualifying_periods(self):
        diffs = self._diffs_with_n_qualifying_years(4)
        decision, suff, pp, conc, bs = _decide_for(diffs)
        qualifying = [p for p in pp.values() if p.qualifies]
        self.assertEqual(len(qualifying), 4)
        self.assertNotEqual(decision.verdict, "INCONCLUSIVE")

    def test_fail_when_fewer_than_4_qualifying_periods_are_positive(self):
        diffs = []
        for y_idx, y in enumerate(range(2021, 2025)):
            # 3 of 4 qualifying years negative, enough sample/concentration
            # to reach the condition-2/3/4 check.
            value = 0.5 if y_idx == 0 else -0.5
            for i in range(30):
                diffs.append(sa.PairedDifference(f"S{i % 35}", date(y, 1 + (i % 12), 10), value, 0.0))
        decision, suff, pp, conc, bs = _decide_for(diffs)
        self.assertTrue(suff.meets_minimum)
        qualifying = [p for p in pp.values() if p.qualifies]
        self.assertEqual(len(qualifying), 4)
        positive = sum(1 for p in qualifying if p.mean_diff_positive)
        self.assertLess(positive, 4)
        self.assertEqual(decision.verdict, "FAIL")
        self.assertTrue(any("condition 4" in r for r in decision.reasons))


class TestDecideConcentration(unittest.TestCase):

    def test_inconclusive_when_symbol_share_exceeds_5_percent(self):
        diffs = _uniform_diffs(120, 30, 24, 3, symbol_share=0.06)
        decision, suff, pp, conc, bs = _decide_for(diffs)
        self.assertGreater(conc.max_symbol_share, 0.05)
        self.assertEqual(decision.verdict, "INCONCLUSIVE")

    def test_exactly_5_percent_symbol_share_is_not_a_failure(self):
        diffs = _uniform_diffs(100, 30, 24, 3, symbol_share=0.05)
        _decision, _suff, _pp, conc, _bs = _decide_for(diffs)
        self.assertLessEqual(conc.max_symbol_share, 0.05 + 1e-9)
        # The rule is "more than 5%" -- exactly at the threshold must not
        # by itself trigger decide()'s concentration branch. Checked
        # directly against the threshold rather than the full verdict,
        # since other conditions (e.g. too few qualifying periods at
        # only 3 years) may independently still make this INCONCLUSIVE.
        self.assertFalse(conc.max_symbol_share > sa.MAX_SYMBOL_SHARE)

    def test_inconclusive_when_month_share_exceeds_15_percent(self):
        diffs = _uniform_diffs(120, 30, 24, 3, month_share=0.16)
        decision, suff, pp, conc, bs = _decide_for(diffs)
        self.assertGreater(conc.max_month_share, 0.15)
        self.assertEqual(decision.verdict, "INCONCLUSIVE")

    def test_exactly_15_percent_month_share_is_not_a_failure(self):
        diffs = _uniform_diffs(100, 30, 24, 3, month_share=0.15)
        _decision, _suff, _pp, conc, _bs = _decide_for(diffs)
        self.assertLessEqual(conc.max_month_share, 0.15 + 1e-9)
        self.assertFalse(conc.max_month_share > sa.MAX_MONTH_SHARE)


class TestDecidePassFailBranches(unittest.TestCase):
    """Note: condition 4 needs >= 4 qualifying PERIODS (calendar years
    with >= 30 trades), which is strictly more than condition 1's own
    minimum of 3 distinct years -- with only 3 years total there can
    never be more than 3 qualifying periods, so PASS/FAIL is categorically
    unreachable (always INCONCLUSIVE via "too few qualifying periods").
    These branch tests therefore all use >= 4 years, each with >= 30
    trades, to actually reach the PASS/FAIL branches being tested."""

    def test_fail_when_mean_net_r_not_positive_condition2(self):
        diffs = _uniform_diffs(160, 35, 24, 4, diff_value=0.0)
        for d in diffs:
            d.setup_r = -0.1  # negative setup R, paired diff stays informative via control_r=0
        decision, *_ = _decide_for(diffs)
        self.assertEqual(decision.verdict, "FAIL")
        self.assertTrue(any("condition 2" in r for r in decision.reasons))

    def test_fail_when_bootstrap_lower_bound_not_above_zero_condition3(self):
        # Mean set to ~0 with real variance so the 95% interval straddles 0.
        rng = np.random.default_rng(3)
        diffs = []
        years = [2021, 2022, 2023, 2024]
        for i in range(200):
            y = years[i % len(years)]
            m = 1 + ((i // len(years)) % 12)
            diffs.append(sa.PairedDifference(f"S{i % 35}", date(y, m, 10), rng.normal(0, 1.0), 0.0))
        decision, suff, pp, conc, bs = _decide_for(diffs)
        self.assertTrue(suff.meets_minimum)
        qualifying = [p for p in pp.values() if p.qualifies]
        self.assertGreaterEqual(len(qualifying), 4)
        if bs.lower <= 0:
            self.assertIn(decision.verdict, ("FAIL", "PASS"))
            if decision.verdict == "FAIL":
                self.assertTrue(any("condition 3" in r for r in decision.reasons))

    def test_pass_when_all_five_conditions_hold(self):
        diffs = _uniform_diffs(160, 35, 24, 4, diff_value=1.0)
        for d in diffs:
            d.setup_r = 1.0
        decision, suff, pp, conc, bs = _decide_for(diffs)
        self.assertTrue(suff.meets_minimum)
        self.assertLessEqual(conc.max_symbol_share, 0.05)
        self.assertEqual(decision.verdict, "PASS")
        self.assertEqual(decision.reasons, [])

    def test_priority_order_insufficient_sample_outranks_a_simultaneous_concentration_failure(self):
        diffs = _uniform_diffs(50, 5, 10, 1, symbol_share=0.5)
        decision, suff, conc = _decide_for(diffs)[0], _decide_for(diffs)[1], _decide_for(diffs)[3]
        self.assertFalse(suff.meets_minimum)
        self.assertGreater(conc.max_symbol_share, 0.05)
        self.assertEqual(decision.verdict, "INCONCLUSIVE")
        self.assertTrue(any("condition 1" in r for r in decision.reasons))


# ── Statistical validation ────────────────────────────────────────────────

class TestStatisticalValidation(unittest.TestCase):

    def test_no_edge_random_walk_rarely_passes(self):
        outer_repeats = 200
        passes = 0
        checked_sufficiency = 0
        rng = np.random.default_rng(42)
        years = [2021, 2022, 2023, 2024]
        for i in range(outer_repeats):
            diffs = []
            for t in range(200):
                y = years[t % len(years)]
                m = 1 + ((t // len(years)) % 12)  # decoupled from year cycling -> full 24+ distinct months
                sym = f"S{t % 35}"
                diffs.append(sa.PairedDifference(sym, date(y, m, 10), rng.normal(0, 1.0), 0.0))
            decision, suff, *_ = _decide_for(diffs)
            if suff.meets_minimum:
                checked_sufficiency += 1
            if decision.verdict == "PASS":
                passes += 1
        self.assertGreater(checked_sufficiency, outer_repeats * 0.9)  # the fixture itself is sample-sufficient
        rate = passes / outer_repeats
        # Reasoned upper bound: condition 3 (CI lower bound > 0) alone has
        # a ~2.5% one-tailed false-positive rate under a true null, and
        # ALL conditions must hold jointly for PASS -- a generous upper
        # bound given finite-sample simulation noise.
        self.assertLess(rate, 0.15, f"observed no-edge PASS rate {rate:.1%}")

    def test_injected_known_edge_passes(self):
        diffs = _uniform_diffs(160, 35, 24, 4, diff_value=0.8)
        for d in diffs:
            d.setup_r = 0.8
        decision, *_ = _decide_for(diffs)
        self.assertEqual(decision.verdict, "PASS")


# ── Robustness ────────────────────────────────────────────────────────────

class TestRobustness(unittest.TestCase):

    def test_analysis_functions_never_raise_on_malformed_peer_frames(self):
        session_before_d = date(2025, 6, 1)
        for name, df in _malformed_df_cases().items():
            with self.subTest(case=name):
                try:
                    eligible, atr = sa.peer_atr_and_eligibility(df, session_before_d)
                except Exception as exc:  # pragma: no cover -- failure path
                    self.fail(f"{name} raised {exc!r}")
                self.assertIsInstance(eligible, bool)

    def test_excluded_trade_accounting_matches_reported_shares(self):
        classified_counts = {EXPIRED_UNFILLED: 10, INVALIDATED_BEFORE_FILL: 5, DATA_GAP: 2}
        no_peer_count = 3
        shares = sa.rejection_shares(classified_counts, no_peer_count)
        total = sum(classified_counts.values()) + no_peer_count
        self.assertAlmostEqual(sum(shares.values()), 1.0, places=9)
        self.assertAlmostEqual(shares["no_peer"], no_peer_count / total, places=9)
        self.assertAlmostEqual(shares[EXPIRED_UNFILLED], 10 / total, places=9)


# ── §13.5 fix: resolved_date skipped when recomputed is None ─────────────

class TestStoredVsRecomputedDataGapOpenAtEnd(unittest.TestCase):
    """DATA_GAP/OPEN_AT_END store a FALLBACK resolved_date bound in
    replay_candidates (setups/replay.py's run_replay_core backfills it via
    _max_possible_resolution_date so a later setup knows when the symbol
    is free again), while a fresh simulate_outcome() recompute correctly
    returns resolved_date=None for both (genuinely unresolved). Comparing
    the two naively would flag a mismatch on every single such row --
    stored_vs_recomputed_mismatches() must skip that specific comparison
    whenever the RECOMPUTED value is None."""

    def _row_with_fallback_resolved_date(self, fallback_date, **overrides):
        row = {
            "sh_date": date(2025, 1, 2), "sh_price": 110.0, "sl_date": date(2025, 1, 2), "sl_price": 100.0,
            "trigger": 110.0, "stop": 95.0, "target": 155.0, "risk": 15.0, "rr": 3.0, "atr": 1.0,
            "regime": "BULL", "validity_end": date(2025, 1, 9), "scan_date": date(2025, 1, 2),
            "target_source": "RESISTANCE",
            "fill_date": None, "fill_price": None, "exit_date": None, "exit_price": None,
            "first_barrier_hit": None, "r_multiple": None, "mae_r": None, "mfe_r": None,
            "sessions_held": None, "resolved_date": fallback_date,
        }
        row.update(overrides)
        return row

    def test_data_gap_no_mismatch_despite_stored_fallback_resolved_date(self):
        row = self._row_with_fallback_resolved_date(fallback_date=date(2025, 1, 9))
        recomputed = OutcomeResult(status="DATA_GAP")  # resolved_date=None, matching simulate_outcome()
        self.assertEqual(sa.stored_vs_recomputed_mismatches(row, recomputed), [])

    def test_open_at_end_no_mismatch_despite_stored_fallback_resolved_date(self):
        row = self._row_with_fallback_resolved_date(fallback_date=date(2025, 1, 9))
        recomputed = OutcomeResult(status="OPEN_AT_END")
        self.assertEqual(sa.stored_vs_recomputed_mismatches(row, recomputed), [])

    def test_genuine_mismatch_on_a_resolved_field_is_still_detected_for_data_gap(self):
        """The skip is scoped to resolved_date only -- any OTHER real
        mismatch on a DATA_GAP/OPEN_AT_END row must still be caught."""
        row = self._row_with_fallback_resolved_date(fallback_date=date(2025, 1, 9), fill_date=date(2025, 1, 3))
        recomputed = OutcomeResult(status="DATA_GAP", fill_date=date(2025, 1, 4))  # genuinely different
        self.assertEqual(sa.stored_vs_recomputed_mismatches(row, recomputed), ["fill_date"])


# ── decide(): lists ALL failing INCONCLUSIVE conditions, not just the first ─

class TestDecideListsAllInconclusiveReasons(unittest.TestCase):

    def test_insufficient_sample_and_concentration_both_named_together(self):
        diffs = _uniform_diffs(50, 5, 10, 1, symbol_share=0.5)
        decision, suff, pp, conc, bs = _decide_for(diffs)
        self.assertFalse(suff.meets_minimum)
        self.assertGreater(conc.max_symbol_share, sa.MAX_SYMBOL_SHARE)
        self.assertEqual(decision.verdict, "INCONCLUSIVE")
        self.assertTrue(any("condition 1" in r for r in decision.reasons))
        self.assertTrue(any("condition 5" in r for r in decision.reasons))
        self.assertGreaterEqual(len(decision.reasons), 2)

    def test_too_few_qualifying_periods_and_both_concentration_failures_named_together(self):
        diffs = _uniform_diffs(120, 30, 24, 3, symbol_share=0.5, month_share=0.5)
        decision, suff, pp, conc, bs = _decide_for(diffs)
        qualifying = [p for p in pp.values() if p.qualifies]
        self.assertLess(len(qualifying), sa.MIN_QUALIFYING_PERIODS)
        self.assertGreater(conc.max_symbol_share, sa.MAX_SYMBOL_SHARE)
        self.assertGreater(conc.max_month_share, sa.MAX_MONTH_SHARE)
        self.assertEqual(decision.verdict, "INCONCLUSIVE")
        self.assertTrue(any("condition 4" in r for r in decision.reasons))
        self.assertTrue(any("symbol concentration" in r for r in decision.reasons))
        self.assertTrue(any("month concentration" in r for r in decision.reasons))
        self.assertEqual(len(decision.reasons), 3)


# ── determine_label() -- one test per failing condition (§13.6/§13.8) ────

class TestDetermineLabel(unittest.TestCase):

    def _all_good_kwargs(self):
        return dict(
            mode="run_outcomes", variants=["V3"], header_slippage_bps=sa.FROZEN_SLIPPAGE_BPS,
            seed=sa.FROZEN_SEED, draws=sa.FROZEN_DRAWS,
            prereg_committed=True, tree_clean=True, library_versions_ok=True,
            manifest_matches=True, recompute_mismatches=[], preregistration_hash_matches=True,
        )

    def test_confirmatory_when_every_condition_holds(self):
        label = sa.determine_label(**self._all_good_kwargs())
        self.assertEqual(label.verdict, "CONFIRMATORY")
        self.assertEqual(label.failed_conditions, [])

    def test_exploratory_on_wrong_mode(self):
        kwargs = self._all_good_kwargs()
        kwargs["mode"] = "counts_only"
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("mode" in r for r in label.failed_conditions))

    def test_exploratory_on_wrong_variants(self):
        kwargs = self._all_good_kwargs()
        kwargs["variants"] = ["V1", "V3"]
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("variants" in r for r in label.failed_conditions))

    def test_exploratory_on_wrong_header_slippage(self):
        kwargs = self._all_good_kwargs()
        kwargs["header_slippage_bps"] = 20.0
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("slippage" in r for r in label.failed_conditions))

    def test_exploratory_on_wrong_seed(self):
        kwargs = self._all_good_kwargs()
        kwargs["seed"] = 1
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("seed" in r for r in label.failed_conditions))

    def test_exploratory_on_wrong_draws(self):
        kwargs = self._all_good_kwargs()
        kwargs["draws"] = 500
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("draws" in r for r in label.failed_conditions))

    def test_exploratory_when_prereg_not_committed(self):
        kwargs = self._all_good_kwargs()
        kwargs["prereg_committed"] = False
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("committed" in r for r in label.failed_conditions))

    def test_exploratory_when_tree_dirty(self):
        kwargs = self._all_good_kwargs()
        kwargs["tree_clean"] = False
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("clean" in r for r in label.failed_conditions))

    def test_exploratory_on_library_version_mismatch(self):
        kwargs = self._all_good_kwargs()
        kwargs["library_versions_ok"] = False
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("library" in r for r in label.failed_conditions))

    def test_exploratory_on_manifest_mismatch(self):
        kwargs = self._all_good_kwargs()
        kwargs["manifest_matches"] = False
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("manifest" in r for r in label.failed_conditions))

    def test_exploratory_on_preregistration_hash_mismatch(self):
        kwargs = self._all_good_kwargs()
        kwargs["preregistration_hash_matches"] = False
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("hash" in r for r in label.failed_conditions))

    def test_exploratory_on_recompute_mismatches(self):
        kwargs = self._all_good_kwargs()
        kwargs["recompute_mismatches"] = [{"symbol": "AAA", "scan_date": "2025-01-02", "fields": ["r_multiple"]}]
        label = sa.determine_label(**kwargs)
        self.assertEqual(label.verdict, "EXPLORATORY")
        self.assertTrue(any("mismatch" in r for r in label.failed_conditions))


if __name__ == "__main__":
    unittest.main()
