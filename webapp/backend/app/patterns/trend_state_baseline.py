"""Baseline B-trend for round B (scripts/eval_trend_pullback_holdout.py):
mean abnormal return vs. same-date stock peers restricted to peers that
are themselves in a "trend_pullback-style uptrend state" on that date.
NOT part of the frozen v1 or round-1-baseline code — new module, imports
only, no edits to app/patterns/{config,trend_pullback,indicators,
baseline}.py.

PEER DEFINITION (mirrors trend_pullback.py's long branch, by design):
    trend_state(symbol, i, sma_period) =
        SMA(sma_period) and its slope are both defined at bar i
        AND slope > 0
        AND close(i) > SMA(sma_period)(i)
— the two numeric ingredients the detector's long condition depends on
for "trend" (rising-slope, price-vs-SMA), with the pivot-based
higher-high/higher-low structure and the pullback/touch mechanics
dropped, per round B's design brief. A stock is a trend peer on bar i if
EITHER SMA150 or SMA200 satisfies trend_state (OR across config.SMA_PERIODS,
mirroring how a trend_pullback detection can fire via either period).

MISMATCH, FLAGGED: in the real detector, `close > sma` is only ever
evaluated as part of the pullback-bounce confirmation line (together with
the touch-distance and close > touch_low checks) — never a standalone
"is this stock currently above its rising SMA" test on its own. This peer
definition applies it unconditionally, every bar, with no pullback/touch
event required, so B-trend's peer pool is a broader "generic uptrend"
state than what the detector itself ever isolates in practice.

TIMING: a peer is classified as of the last COMPLETED bar before the
entry bar — i.e. the SAME bar as the relevant detection's own
confirmed_on (entry = the next bar's OPEN, so the entry bar's own close
isn't known at decision time). For an eligible bar at absolute index
`entry_idx`, its trend-peer status is `trend_state[entry_idx - 1]` —
`entry_idx - 1` IS that bar's own confirming bar. No-look-ahead: the
rolling SMA/slope computation at any bar only ever uses bars up to and
including that bar.
"""
import pandas as pd

from . import config
from .baseline import HorizonIndex
from .indicators import sma, sma_slope


def compute_trend_state(
    frames: dict[str, pd.DataFrame], sma_periods: tuple[int, ...] = config.SMA_PERIODS
) -> dict[str, list[bool]]:
    """Per symbol: one bool per bar (aligned by absolute bar index, same
    order as `frames[symbol].index`), True iff trend_state holds for
    EITHER sma_period at that bar. Fully vectorized (pandas rolling +
    boolean ops) — no per-bar Python loop, no .iloc — see
    test_trend_state_baseline_performance.py."""
    result: dict[str, list[bool]] = {}
    for symbol, bars in frames.items():
        close = bars["close"]
        state = pd.Series(False, index=bars.index)
        for period in sma_periods:
            sma_s = sma(close, period)
            slope_s = sma_slope(close, period, config.SMA_SLOPE_LOOKBACK_BARS)
            period_state = (slope_s > 0) & (close > sma_s)
            state = state | period_state.fillna(False)
        result[symbol] = state.to_numpy().tolist()
    return result


def build_trend_restricted_date_agg(
    index: HorizonIndex,
    trend_state: dict[str, list[bool]],
    direction: str,
) -> dict[str, tuple[float, int]]:
    """Mirrors HorizonIndex.date_agg's shape but restricted to symbols
    whose CONFIRMING bar (entry_idx - 1) satisfies trend_state — reuses
    index.excess_by_symbol_direction (already computed by the frozen
    build_horizon_index()), no re-computation of excess returns."""
    agg: dict[str, tuple[float, int]] = {}
    for symbol, eligible in index.eligible_by_symbol.items():
        values = index.excess_by_symbol_direction.get(symbol, {}).get(direction, [])
        states = trend_state.get(symbol, [])
        for bar, v in zip(eligible, values):
            if v is None:
                continue
            confirming_idx = bar["entry_idx"] - 1
            if confirming_idx < 0 or confirming_idx >= len(states):
                continue
            if not states[confirming_idx]:
                continue
            s, c = agg.get(bar["entry_date"], (0.0, 0))
            agg[bar["entry_date"]] = (s + v, c + 1)
    return agg


def is_trend_peer(trend_state: dict[str, list[bool]], symbol: str, entry_idx: int) -> bool:
    """Whether `symbol`'s own confirming bar (entry_idx - 1) satisfies
    trend_state — used to decide whether a detection's own contribution
    must be subtracted from the trend-restricted aggregate."""
    states = trend_state.get(symbol, [])
    confirming_idx = entry_idx - 1
    if confirming_idx < 0 or confirming_idx >= len(states):
        return False
    return states[confirming_idx]


def baseline_date_matched_trend(
    trend_agg: dict[str, tuple[float, int]],
    entry_date: str,
    own_excess_if_trend_peer: float | None,
) -> tuple[float | None, int]:
    """Same O(1) subtract-own-contribution shape as
    baseline.baseline_date_matched(), operating on the trend-restricted
    aggregate. Returns (mean, peer_count) — peer_count lets the caller
    apply a minimum-peers floor (prereg_v2.PREREG_MIN_PEERS) without
    needing a second lookup."""
    total_sum, total_count = trend_agg.get(entry_date, (0.0, 0))
    if own_excess_if_trend_peer is not None:
        total_sum -= own_excess_if_trend_peer
        total_count -= 1
    if total_count <= 0:
        return None, total_count
    return total_sum / total_count, total_count
