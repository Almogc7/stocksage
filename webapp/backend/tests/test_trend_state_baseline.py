import unittest

import pandas as pd

from app.patterns.baseline import build_horizon_index, spy_date_index
from app.patterns.trend_state_baseline import (
    baseline_date_matched_trend,
    build_trend_restricted_date_agg,
    compute_trend_state,
    is_trend_peer,
)


def _bars(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).set_index("date")


def _row(date, o, h, l, c, volume=1_000_000):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": volume}


def _flat_then_rising(n_flat: int, n_rise: int, start_date: str, level: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """Flat for n_flat bars (slope ~0, keeps SMA below later rising
    closes), then a clean rise for n_rise bars."""
    dates = pd.bdate_range(start=start_date, periods=n_flat + n_rise).strftime("%Y-%m-%d").tolist()
    rows = []
    for d in dates[:n_flat]:
        rows.append(_row(d, level, level + 0.1, level - 0.1, level))
    price = level
    for d in dates[n_flat:]:
        o = price
        price += step
        c = price
        rows.append(_row(d, o, max(o, c) + 0.1, min(o, c) - 0.1, c))
    return _bars(rows)


def _flat_spy(n: int, start_date: str) -> pd.DataFrame:
    dates = pd.bdate_range(start=start_date, periods=n).strftime("%Y-%m-%d").tolist()
    return _bars([_row(d, 400, 400.2, 399.8, 400) for d in dates])


class ComputeTrendStateTests(unittest.TestCase):
    def test_state_is_false_while_flat_and_true_once_sma_turns_up_and_price_clears_it(self):
        bars = _flat_then_rising(n_flat=20, n_rise=20, start_date="2023-01-02", level=100.0, step=1.0)
        state = compute_trend_state({"X": bars}, sma_periods=(5, 10))["X"]
        self.assertEqual(len(state), len(bars))
        # Early (flat) bars: SMA flat, slope ~0 -> never counts as uptrend.
        self.assertFalse(any(state[:15]))
        # Well into the rise, both slope>0 and close>SMA should hold.
        self.assertTrue(all(state[30:]))

    def test_is_false_before_warmup_bars_exist(self):
        bars = _flat_then_rising(n_flat=3, n_rise=3, start_date="2023-01-02")
        state = compute_trend_state({"X": bars}, sma_periods=(5, 10))["X"]
        self.assertFalse(any(state))  # never enough bars for either SMA(5) or SMA(10)


class TimingFixTests(unittest.TestCase):
    def test_a_cross_above_sma_only_on_the_entry_bars_own_close_does_not_count(self):
        # Build a series where the confirming bar (second to last) closes
        # BELOW its short SMA, and only the very last bar (what would be
        # the "entry" bar for a detection confirmed the day before) closes
        # above it.
        dates = pd.bdate_range(start="2023-01-02", periods=12).strftime("%Y-%m-%d").tolist()
        closes = [100.0] * 10 + [99.0, 115.0]  # flat, then a dip, then a big jump on the LAST bar only
        rows = []
        prev = closes[0]
        for d, c in zip(dates, closes):
            rows.append(_row(d, prev, max(prev, c) + 0.1, min(prev, c) - 0.1, c))
            prev = c
        bars = _bars(rows)

        state = compute_trend_state({"X": bars}, sma_periods=(5,))["X"]
        confirming_idx = len(bars) - 2  # the dip bar
        entry_idx = len(bars) - 1  # the jump bar

        # The jump bar itself may well show close > SMA (price-vs-SMA
        # true AT the entry bar) -- that's exactly the case this must NOT
        # use. What matters is the CONFIRMING bar's own state.
        self.assertFalse(state[confirming_idx])
        self.assertFalse(is_trend_peer({"X": state}, "X", entry_idx))


class NoLookaheadTests(unittest.TestCase):
    def test_truncating_the_series_never_changes_an_already_computed_trend_state(self):
        bars = _flat_then_rising(n_flat=20, n_rise=40, start_date="2023-01-02", level=100.0, step=1.0)
        full_state = compute_trend_state({"X": bars}, sma_periods=(5, 10))["X"]

        for n in range(25, len(bars), 5):
            truncated_state = compute_trend_state({"X": bars.iloc[:n]}, sma_periods=(5, 10))["X"]
            self.assertEqual(truncated_state, full_state[:n])


class TrendRestrictedBaselineTests(unittest.TestCase):
    def setUp(self):
        self.spy = _flat_spy(80, "2023-01-02")
        self.spy_idx = spy_date_index(self.spy)

    def test_hand_computed_mean_and_peer_count_with_mixed_qualifying_peers(self):
        horizon = 3
        warmup = 15
        # Symbol A: in a clear uptrend (qualifies as a trend peer throughout).
        bars_a = _flat_then_rising(n_flat=20, n_rise=40, start_date="2023-01-02", level=100.0, step=1.0)
        # Symbol B: stays flat the whole time (never qualifies).
        bars_b = _flat_spy(60, "2023-01-02")
        bars_b = _bars([_row(d, 50, 50.2, 49.8, 50) for d in bars_b.index.to_list()])
        frames = {"A": bars_a, "B": bars_b}

        index = build_horizon_index(frames, horizon, self.spy, self.spy_idx, warmup_bars=warmup)
        trend_state = compute_trend_state(frames, sma_periods=(5, 10))
        agg = build_trend_restricted_date_agg(index, trend_state, "long")

        # Pick a date well into A's rise where A is eligible and trending,
        # and B has an eligible bar on the same date but never trends.
        a_eligible = index.eligible_by_symbol["A"]
        late_bar = a_eligible[-5]
        entry_date = late_bar["entry_date"]
        self.assertTrue(trend_state["A"][late_bar["entry_idx"] - 1])

        own_excess = index.excess_by_symbol_direction["A"]["long"][len(a_eligible) - 5]
        mean, peer_count = baseline_date_matched_trend(agg, entry_date, own_excess_if_trend_peer=own_excess)

        # B never qualifies as a trend peer, so excluding A's own
        # contribution should leave NO peers at all on this date.
        self.assertEqual(peer_count, 0)
        self.assertIsNone(mean)

    def test_peer_count_reflects_only_trend_qualifying_symbols(self):
        horizon = 3
        warmup = 15
        bars_a = _flat_then_rising(n_flat=20, n_rise=40, start_date="2023-01-02", level=100.0, step=1.0)
        bars_c = _flat_then_rising(n_flat=20, n_rise=40, start_date="2023-01-02", level=80.0, step=0.8)
        bars_b = _bars([_row(d, 50, 50.2, 49.8, 50) for d in bars_a.index.to_list()])  # flat, never trends
        frames = {"A": bars_a, "B": bars_b, "C": bars_c}

        index = build_horizon_index(frames, horizon, self.spy, self.spy_idx, warmup_bars=warmup)
        trend_state = compute_trend_state(frames, sma_periods=(5, 10))
        agg = build_trend_restricted_date_agg(index, trend_state, "long")

        a_eligible = index.eligible_by_symbol["A"]
        late_bar = a_eligible[-5]
        entry_date = late_bar["entry_date"]
        own_idx = len(a_eligible) - 5
        own_excess = index.excess_by_symbol_direction["A"]["long"][own_idx]

        mean, peer_count = baseline_date_matched_trend(agg, entry_date, own_excess_if_trend_peer=own_excess)
        # Only C should remain as a qualifying peer once A's own
        # contribution is excluded (B never trends, so it was never in
        # the aggregate to begin with).
        self.assertEqual(peer_count, 1)
        self.assertIsNotNone(mean)


if __name__ == "__main__":
    unittest.main()
