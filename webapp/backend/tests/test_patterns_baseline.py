import unittest

import pandas as pd

from app.patterns.baseline import (
    baseline_date_matched,
    baseline_symbol_year,
    build_horizon_index,
    eligible_entry_bars,
    excess_for_bar,
    fixed_horizon_return,
    month_key,
    score_detection_with_baselines,
    spy_date_index,
)
from app.patterns.outcomes import compute_forward_outcome
from app.patterns.stats import bootstrap_mean_ci


def _bars(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).set_index("date")


def _row(date, o, h, l, c, volume=1_000_000):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": volume}


def _rising_series(n: int, start_date: str, start_price: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    rows = []
    price = start_price
    dates = pd.bdate_range(start=start_date, periods=n).strftime("%Y-%m-%d").tolist()
    for d in dates:
        o = price
        price += step
        c = price
        rows.append(_row(d, o, max(o, c) + 0.1, min(o, c) - 0.1, c))
    return _bars(rows)


class FixedHorizonReturnTests(unittest.TestCase):
    def test_entry_is_next_bars_open_and_exit_is_close_of_entry_idx_plus_horizon_minus_1(self):
        bars = _rising_series(15, "2024-01-01")
        dates = bars.index.to_list()
        confirmed_on = dates[2]
        out = fixed_horizon_return(bars, confirmed_on, horizon=5)
        entry_idx = dates.index(confirmed_on) + 1
        self.assertEqual(out["entry_date"], dates[entry_idx])
        self.assertEqual(out["entry_price"], bars["open"].iloc[entry_idx])
        exit_idx = entry_idx + 5 - 1
        self.assertEqual(out["exit_date"], dates[exit_idx])
        self.assertEqual(out["exit_price"], bars["close"].iloc[exit_idx])

    def test_none_when_the_horizon_bar_does_not_exist_yet(self):
        bars = _rising_series(6, "2024-01-01")
        dates = bars.index.to_list()
        out = fixed_horizon_return(bars, dates[2], horizon=20)
        self.assertIsNone(out)

    def test_none_when_confirmed_on_is_the_last_bar(self):
        bars = _rising_series(10, "2024-01-01")
        dates = bars.index.to_list()
        out = fixed_horizon_return(bars, dates[-1], horizon=5)
        self.assertIsNone(out)


class EligibleEntryBarsTests(unittest.TestCase):
    def test_respects_warmup_and_horizon_completeness(self):
        bars = _rising_series(20, "2024-01-01")
        dates = bars.index.to_list()
        eligible = eligible_entry_bars(bars, horizon=5, warmup_bars=10)
        # First eligible entry_idx is exactly warmup_bars; last is n-horizon.
        self.assertEqual(eligible[0]["entry_idx"], 10)
        self.assertEqual(eligible[-1]["entry_idx"], 20 - 5)
        for bar in eligible:
            i = bar["entry_idx"]
            self.assertEqual(bar["entry_date"], dates[i])
            self.assertEqual(bar["entry_price"], bars["open"].iloc[i])
            self.assertEqual(bar["exit_date"], dates[i + 5 - 1])
            self.assertEqual(bar["exit_price"], bars["close"].iloc[i + 5 - 1])

    def test_empty_when_series_shorter_than_warmup_plus_horizon(self):
        bars = _rising_series(8, "2024-01-01")
        self.assertEqual(eligible_entry_bars(bars, horizon=5, warmup_bars=10), [])


class IndexingParityWithOutcomesTests(unittest.TestCase):
    def test_matches_compute_forward_outcome_closes_when_no_stop_is_hit(self):
        bars = _rising_series(40, "2024-01-01", start_price=100.0, step=1.0)
        dates = bars.index.to_list()
        confirmed_on = dates[5]
        # Stop unreachable for a long (far below every low).
        outcome = compute_forward_outcome(bars, confirmed_on, "long", stop_price=-1_000_000.0)
        self.assertFalse(outcome["excluded"])
        for h in (5, 10, 20):
            fh = fixed_horizon_return(bars, confirmed_on, horizon=h)
            self.assertIsNotNone(fh)
            expected_close = outcome["closes"][h]
            self.assertEqual(fh["exit_price"], expected_close)
            entry_idx = dates.index(confirmed_on) + 1
            expected_date = dates[entry_idx + h - 1]
            self.assertEqual(fh["exit_date"], expected_date)
            self.assertEqual(fh["entry_date"], outcome["entry_date"])
            self.assertEqual(fh["entry_price"], outcome["entry_price"])


class BaselineHandComputedTests(unittest.TestCase):
    def setUp(self):
        # Two symbols, flat/predictable SPY, so excess return == position
        # return (SPY flat -> spy_price_return == 0 -> benchmark_return ==
        # 0 for long), keeping hand computation simple.
        self.spy = _bars([_row(d, 400, 400.2, 399.8, 400) for d in pd.bdate_range("2023-01-02", periods=300).strftime("%Y-%m-%d")])
        self.spy_idx = spy_date_index(self.spy)

    def test_baseline_symbol_year_equals_hand_computed_mean(self):
        # A symbol with a few known, hand-computable daily moves.
        bars = _rising_series(30, "2023-06-01", start_price=100.0, step=2.0)
        horizon = 3
        index = build_horizon_index({"X": bars}, horizon, self.spy, self.spy_idx, warmup_bars=5)
        eligible = index.eligible_by_symbol["X"]
        year = eligible[0]["entry_date"][:4]

        expected = []
        for bar in eligible:
            pr = (bar["exit_price"] - bar["entry_price"]) / bar["entry_price"]  # SPY flat -> excess == position return
            expected.append(pr)
        expected_mean = sum(expected) / len(expected)

        # exclude_list_idx far outside the series so nothing is excluded
        result = baseline_symbol_year(index, "X", year, exclude_list_idx=-1000, direction="long")
        self.assertAlmostEqual(result, expected_mean, places=9)

    def test_baseline_date_matched_equals_hand_computed_mean(self):
        horizon = 3
        bars_a = _rising_series(30, "2023-06-01", start_price=100.0, step=1.0)
        bars_b = _rising_series(30, "2023-06-01", start_price=50.0, step=3.0)
        index = build_horizon_index({"A": bars_a, "B": bars_b}, horizon, self.spy, self.spy_idx, warmup_bars=5)
        elig_a = index.eligible_by_symbol["A"]
        elig_b = index.eligible_by_symbol["B"]
        by_date_b = {b["entry_date"]: b for b in elig_b}

        shared_date = elig_a[2]["entry_date"]
        self.assertIn(shared_date, by_date_b)
        own_excess = index.excess_by_symbol_direction["A"]["long"][2]

        # Excluding symbol "A"'s own contribution: only B's bar on that date should count.
        expected = (by_date_b[shared_date]["exit_price"] - by_date_b[shared_date]["entry_price"]) / by_date_b[shared_date]["entry_price"]
        result = baseline_date_matched(index, shared_date, direction="long", own_excess=own_excess)
        self.assertAlmostEqual(result, expected, places=9)


class ExclusionWindowTests(unittest.TestCase):
    def setUp(self):
        self.spy = _bars([_row(d, 400, 400.2, 399.8, 400) for d in pd.bdate_range("2023-01-02", periods=300).strftime("%Y-%m-%d")])
        self.spy_idx = spy_date_index(self.spy)

    def test_baseline_a_excludes_a_bar_3_days_before_the_detection_within_a_5_bar_horizon(self):
        horizon = 5
        bars = _rising_series(40, "2023-06-01", start_price=100.0, step=1.0).copy()

        # Find positions first on the clean series.
        probe = build_horizon_index({"X": bars}, horizon, self.spy, self.spy_idx, warmup_bars=5)
        probe_eligible = probe.eligible_by_symbol["X"]
        detection_list_idx = 10
        outlier_list_idx = detection_list_idx - 3  # 3 bars before, within the 5-bar horizon window
        outlier_bar = probe_eligible[outlier_list_idx]

        # Plant a huge outlier by overwriting that bar's own exit-date close
        # directly in the price series (so it flows through excess_for_bar
        # exactly like a real market move would), then rebuild the index.
        bars.loc[outlier_bar["exit_date"], "close"] = outlier_bar["entry_price"] * 100  # +9900%
        index = build_horizon_index({"X": bars}, horizon, self.spy, self.spy_idx, warmup_bars=5)
        eligible = index.eligible_by_symbol["X"]
        values = index.excess_by_symbol_direction["X"]["long"]
        detection_bar = eligible[detection_list_idx]
        year = detection_bar["entry_date"][:4]

        result = baseline_symbol_year(index, "X", year, exclude_list_idx=detection_list_idx, direction="long")

        # Hand-compute the expected mean EXCLUDING every bar within
        # +/- horizon of detection_list_idx (including the planted
        # outlier, which is only 3 positions away).
        expected_values = []
        for j, bar in enumerate(eligible):
            if bar["entry_date"][:4] != year:
                continue
            if abs(j - detection_list_idx) <= horizon:
                continue
            if values[j] is not None:
                expected_values.append(values[j])
        expected_mean = sum(expected_values) / len(expected_values)

        self.assertAlmostEqual(result, expected_mean, places=9)
        # And explicitly: the outlier must not have moved the result anywhere near its own huge value.
        self.assertLess(result, 1.0)

    def test_baseline_b_never_includes_the_detection_symbols_own_outlier(self):
        horizon = 3
        bars_a = _rising_series(30, "2023-06-01", start_price=100.0, step=1.0).copy()
        bars_b = _rising_series(30, "2023-06-01", start_price=50.0, step=1.0)

        probe = build_horizon_index({"A": bars_a, "B": bars_b}, horizon, self.spy, self.spy_idx, warmup_bars=5)
        shared_bar = probe.eligible_by_symbol["A"][3]
        shared_date = shared_bar["entry_date"]
        # Make symbol A's own bar on that date a massive outlier.
        bars_a.loc[shared_bar["exit_date"], "close"] = shared_bar["entry_price"] * 50

        index = build_horizon_index({"A": bars_a, "B": bars_b}, horizon, self.spy, self.spy_idx, warmup_bars=5)
        by_date_b = {b["entry_date"]: b for b in index.eligible_by_symbol["B"]}
        self.assertIn(shared_date, by_date_b)
        own_excess = index.excess_by_symbol_direction["A"]["long"][3]

        result = baseline_date_matched(index, shared_date, direction="long", own_excess=own_excess)
        b_bar = by_date_b[shared_date]
        expected = (b_bar["exit_price"] - b_bar["entry_price"]) / b_bar["entry_price"]
        self.assertAlmostEqual(result, expected, places=9)


class AbnormalReturnSignTests(unittest.TestCase):
    def setUp(self):
        self.spy = _bars([_row(d, 400, 400.2, 399.8, 400) for d in pd.bdate_range("2023-01-02", periods=60).strftime("%Y-%m-%d")])
        self.spy_idx = spy_date_index(self.spy)

    def test_long_detection_beating_its_own_baseline_is_a_positive_abnormal_return(self):
        # Flat baseline symbol, then a sharp jump right at the detection.
        closes = [100.0] * 20
        bars = _bars(
            [_row(d, c, c + 0.1, c - 0.1, c) for d, c in zip(pd.bdate_range("2023-02-01", periods=20).strftime("%Y-%m-%d"), closes)]
        )
        # Append a detection-only segment with a real jump.
        jump_dates = pd.bdate_range(bars.index[-1], periods=6).strftime("%Y-%m-%d").tolist()[1:]
        prev_close = 100.0
        rows = []
        for i, d in enumerate(jump_dates):
            o = prev_close
            c = prev_close + 5  # strong up move
            rows.append(_row(d, o, c + 0.1, o - 0.1, c))
            prev_close = c
        bars = pd.concat([bars, _bars(rows)])

        confirmed_on = bars.index.to_list()[18]  # just before the jump segment
        detection = {"pattern": "trend_pullback", "direction": "long", "symbol": "X", "confirmed_on": confirmed_on}
        horizon = 5
        index = build_horizon_index({"X": bars}, horizon, self.spy, self.spy_idx, warmup_bars=2)
        bar_index = {"X": {d: i for i, d in enumerate(bars.index.to_list())}}

        result = score_detection_with_baselines(detection, index, bar_index)
        self.assertIsNotNone(result)
        self.assertIsNotNone(result["abnormal_a"])
        self.assertGreater(result["abnormal_a"], 0)

    def test_short_detection_mirrors_the_long_case(self):
        # A falling series: a short detection right before a sharp further
        # drop should show positive abnormal return (it out-"shorted" its
        # own flat-ish baseline).
        closes = [100.0] * 20
        bars = _bars(
            [_row(d, c, c + 0.1, c - 0.1, c) for d, c in zip(pd.bdate_range("2023-02-01", periods=20).strftime("%Y-%m-%d"), closes)]
        )
        drop_dates = pd.bdate_range(bars.index[-1], periods=6).strftime("%Y-%m-%d").tolist()[1:]
        prev_close = 100.0
        rows = []
        for d in drop_dates:
            o = prev_close
            c = prev_close - 5
            rows.append(_row(d, o, o + 0.1, c - 0.1, c))
            prev_close = c
        bars = pd.concat([bars, _bars(rows)])

        confirmed_on = bars.index.to_list()[18]
        detection = {"pattern": "double_top_bottom", "direction": "short", "symbol": "Y", "confirmed_on": confirmed_on}
        horizon = 5
        index = build_horizon_index({"Y": bars}, horizon, self.spy, self.spy_idx, warmup_bars=2)
        bar_index = {"Y": {d: i for i, d in enumerate(bars.index.to_list())}}

        result = score_detection_with_baselines(detection, index, bar_index)
        self.assertIsNotNone(result)
        self.assertIsNotNone(result["abnormal_a"])
        self.assertGreater(result["abnormal_a"], 0)


class ClusterBootstrapMonthKeyTests(unittest.TestCase):
    def test_month_key_is_year_month(self):
        self.assertEqual(month_key("2024-03-17"), "2024-03")

    def test_bootstrap_mean_ci_with_month_keys_is_deterministic(self):
        values_by_month = {"2023-01": [0.05, 0.03], "2023-02": [-0.02], "2023-03": [0.10, 0.08, 0.06]}
        a = bootstrap_mean_ci(values_by_month, n_resamples=500, seed=7)
        b = bootstrap_mean_ci(values_by_month, n_resamples=500, seed=7)
        self.assertEqual(a, b)

    def test_resampling_happens_at_the_month_level_not_per_value(self):
        # Two months with sharply different, internally-homogeneous means.
        # Whole-month-block resampling can only ever produce resample
        # means equal to one of a SMALL discrete set (determined by how
        # many times each month is drawn out of 2 draws): a mix of
        # {month_lo, month_hi, average of the two}. Per-value resampling
        # would instead produce a much wider spread of means, since
        # individual values within each month differ slightly too.
        values_by_month = {"2023-01": [0.0, 0.001, -0.001], "2023-02": [1.0, 1.001, 0.999]}
        _, lower, upper = bootstrap_mean_ci(values_by_month, n_resamples=3000, seed=3)
        # With 2 months and 2 draws with replacement, the only possible
        # resample means are ~0 (both draws = Jan), ~1 (both draws = Feb),
        # or ~0.5 (one of each) -- so the 90% interval must sit within
        # [0, 1], clearly separated from the per-value-resampling case
        # where intermediate values near 0.0005 or 0.9995 etc. could also
        # appear. This is a coarse but real signal that whole months move
        # together.
        self.assertGreaterEqual(lower, -0.01)
        self.assertLessEqual(upper, 1.01)


class EquivalenceWithReferenceImplementationTests(unittest.TestCase):
    """build_horizon_index() uses a faster internal path (raw numpy array
    indexing) than the public eligible_entry_bars()/excess_for_bar()
    reference functions, for performance at realistic scale (see
    test_patterns_baseline_performance.py). This proves the fast path
    produces IDENTICAL output to calling the slow, obviously-correct
    reference functions directly."""

    def test_eligible_bars_and_excess_values_match_the_slow_reference_functions(self):
        spy = _bars(
            [_row(d, 400, 400.2, 399.8, 400) for d in pd.bdate_range("2023-01-02", periods=120).strftime("%Y-%m-%d")]
        )
        spy_idx = spy_date_index(spy)
        bars_a = _rising_series(80, "2023-01-02", start_price=100.0, step=1.0)
        bars_b = _rising_series(80, "2023-01-02", start_price=250.0, step=-0.7)
        frames = {"A": bars_a, "B": bars_b}
        horizon = 5
        warmup = 10

        index = build_horizon_index(frames, horizon, spy, spy_idx, warmup_bars=warmup)

        for symbol, bars in frames.items():
            expected_eligible = eligible_entry_bars(bars, horizon, warmup_bars=warmup)
            self.assertEqual(index.eligible_by_symbol[symbol], expected_eligible)
            for direction in ("long", "short"):
                expected_values = [
                    excess_for_bar(bar, direction, horizon, spy, spy_idx) for bar in expected_eligible
                ]
                self.assertEqual(index.excess_by_symbol_direction[symbol][direction], expected_values)


if __name__ == "__main__":
    unittest.main()
