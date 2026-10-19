"""Support for the post-hoc, exploratory baseline diagnostic
(scripts/baseline_diagnostic.py) — NOT part of the frozen v1 backtest.
None of app/patterns/{config,zigzag,trend_pullback,flag_breakout,
double_top_bottom,stops,outcomes,dedupe,stats,detect}.py are touched by
this module; everything here is new and only ever imports from them.

Returns here are fixed-horizon and have NO stops — deliberately not
compute_forward_outcome(), which is stop-aware. The question this
diagnostic answers ("is the post-pattern excess return bigger than what
the same stock earns on an ordinary day?") needs the *same yardstick* for
pattern days and ordinary days, and ordinary days have no comparable stop
to begin with, so a stop-aware return wouldn't be comparable to a
baseline built from them. See scripts/baseline_diagnostic.py's own
docstring for the full rationale — these numbers differ from the v1
report's for exactly this reason.

fixed_horizon_return() reimplements only outcomes.py's entry-timing
convention: entry = the next bar's OPEN after confirmed_on; T+N = the
close of bar `entry_idx + N - 1` (entry bar itself = bar 0, the Nth bar
counting it as the 1st) — bar-for-bar identical to
compute_forward_outcome()'s own `closes[N]`, verified directly by
test_patterns_baseline.py's indexing-parity test. `excess_return()`
itself is imported UNMODIFIED from outcomes.py — same signing, same date
alignment as v1.

PERFORMANCE: at realistic scale (~340 symbols x ~1250 bars x a few
thousand detections x 3 horizons), scoring every detection with a loop
over every OTHER symbol (baseline B) or every OTHER bar in its own
symbol-year (baseline A) is O(detections x universe) / O(detections x
bars-per-year) — too slow. Both baselines are instead built from a single
O(symbols x bars) precomputation per horizon (build_horizon_index()):
  - Baseline B: one sum/count per (direction, calendar date), pooled
    across every symbol — O(1) per detection (subtract its own
    contribution, divide).
  - Baseline A: one prefix-sum/prefix-count array per (symbol, direction),
    plus each symbol's year boundaries — O(1) per detection (year total
    minus the excluded window, both via prefix-sum subtraction).
See test_patterns_baseline_performance.py for the realistic-scale timing
test.
"""
from dataclasses import dataclass, field

import pandas as pd

from . import config
from .outcomes import excess_return

DIRECTIONS = ("long", "short")


def fixed_horizon_return(bars: pd.DataFrame, confirmed_on: str, horizon: int) -> dict | None:
    """entry = next bar's open after `confirmed_on`; exit = close of bar
    `entry_idx + horizon - 1`. Returns None if `confirmed_on`, the entry
    bar, or the horizon's exit bar aren't present in `bars`."""
    dates = bars.index.to_list()
    if confirmed_on not in dates:
        return None
    confirm_idx = dates.index(confirmed_on)
    entry_idx = confirm_idx + 1
    if entry_idx >= len(dates):
        return None
    exit_idx = entry_idx + horizon - 1
    if exit_idx >= len(dates):
        return None
    return {
        "entry_idx": entry_idx,
        "entry_date": dates[entry_idx],
        "entry_price": float(bars["open"].iloc[entry_idx]),
        "exit_date": dates[exit_idx],
        "exit_price": float(bars["close"].iloc[exit_idx]),
    }


def eligible_entry_bars(
    bars: pd.DataFrame, horizon: int, warmup_bars: int = config.SMA_WARMUP_BARS
) -> list[dict]:
    """Every bar index i with `warmup_bars <= i` and a complete forward
    window (`i + horizon - 1` within range), treating bar i itself as the
    entry bar (its own open) — the same entry/exit convention as
    fixed_horizon_return(), just enumerated over every bar instead of one
    confirmed_on-anchored bar. Bars are contiguous by construction (i runs
    from `warmup_bars` with no gaps), so list position `j` always equals
    `entry_idx - warmup_bars` — every index below relies on that."""
    dates = bars.index.to_list()
    n = len(dates)
    out: list[dict] = []
    for i in range(warmup_bars, n):
        exit_idx = i + horizon - 1
        if exit_idx >= n:
            break
        out.append(
            {
                "entry_idx": i,
                "entry_date": dates[i],
                "entry_price": float(bars["open"].iloc[i]),
                "exit_date": dates[exit_idx],
                "exit_price": float(bars["close"].iloc[exit_idx]),
            }
        )
    return out


def month_key(date: str) -> str:
    return date[:7]


def _position_return(entry_price: float, exit_price: float, direction: str) -> float:
    if direction == "long":
        return (exit_price - entry_price) / entry_price
    return (entry_price - exit_price) / entry_price


def spy_date_index(spy_frame: pd.DataFrame) -> dict[str, int]:
    return {d: i for i, d in enumerate(spy_frame.index.to_list())}


def excess_for_bar(
    bar: dict,
    direction: str,
    horizon: int,
    spy_frame: pd.DataFrame,
    spy_date_to_idx: dict[str, int],
) -> float | None:
    """Same date-alignment convention as v1's _score_detections(): SPY's
    own entry index (looked up by the bar's entry_date) plus horizon-1,
    not the symbol's own bar offsets."""
    spy_n = len(spy_frame)
    spy_entry_idx = spy_date_to_idx.get(bar["entry_date"])
    if spy_entry_idx is None:
        return None
    spy_exit_idx = spy_entry_idx + horizon - 1
    if spy_exit_idx >= spy_n:
        return None
    spy_entry = float(spy_frame["close"].iloc[spy_entry_idx])
    spy_exit = float(spy_frame["close"].iloc[spy_exit_idx])
    position_return = _position_return(bar["entry_price"], bar["exit_price"], direction)
    return excess_return(position_return, direction, spy_entry, spy_exit)


@dataclass
class HorizonIndex:
    """One-time, O(symbols x bars) precomputation for one horizon, shared
    by every detection scored at that horizon — see build_horizon_index()."""

    horizon: int
    warmup_bars: int
    eligible_by_symbol: dict[str, list[dict]] = field(default_factory=dict)
    # {symbol: {direction: [excess|None, ...]}}, aligned with eligible_by_symbol[symbol]
    excess_by_symbol_direction: dict[str, dict[str, list[float | None]]] = field(default_factory=dict)
    # {symbol: {direction: prefix_sum}} / prefix_count, len == len(eligible)+1
    prefix_sum: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    prefix_count: dict[str, dict[str, list[int]]] = field(default_factory=dict)
    # {symbol: {year: (start, end)}}, half-open, in list-index space
    year_bounds: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    # {direction: {date: (sum, count)}}, pooled across every symbol
    date_agg: dict[str, dict[str, tuple[float, int]]] = field(
        default_factory=lambda: {d: {} for d in DIRECTIONS}
    )


def _fast_eligible_entry_bars(
    dates: "list", opens: "object", closes: "object", horizon: int, warmup_bars: int
) -> list[dict]:
    """Same values/semantics as eligible_entry_bars(), but reads from
    pre-extracted numpy arrays instead of pandas .iloc — single-element
    pandas indexing inside a Python loop is the dominant cost at
    realistic scale (confirmed by profiling), raw numpy indexing is not.
    See test_patterns_baseline.py's EquivalenceWithReferenceImplementationTests
    for the proof this produces identical output to eligible_entry_bars()."""
    n = len(dates)
    out: list[dict] = []
    for i in range(warmup_bars, n):
        exit_idx = i + horizon - 1
        if exit_idx >= n:
            break
        out.append(
            {
                "entry_idx": i,
                "entry_date": dates[i],
                "entry_price": float(opens[i]),
                "exit_date": dates[exit_idx],
                "exit_price": float(closes[exit_idx]),
            }
        )
    return out


def build_horizon_index(
    frames: dict[str, pd.DataFrame],
    horizon: int,
    spy_frame: pd.DataFrame,
    spy_date_to_idx: dict[str, int],
    warmup_bars: int = config.SMA_WARMUP_BARS,
) -> HorizonIndex:
    index = HorizonIndex(horizon=horizon, warmup_bars=warmup_bars)

    spy_closes = spy_frame["close"].to_numpy(dtype=float)
    spy_n = len(spy_closes)

    for symbol, bars in frames.items():
        dates = bars.index.to_numpy()
        opens = bars["open"].to_numpy(dtype=float)
        closes = bars["close"].to_numpy(dtype=float)

        eligible = _fast_eligible_entry_bars(dates, opens, closes, horizon, warmup_bars)
        index.eligible_by_symbol[symbol] = eligible
        if not eligible:
            continue

        year_bounds: dict[str, tuple[int, int]] = {}
        start = 0
        current_year = eligible[0]["entry_date"][:4]
        for j in range(1, len(eligible) + 1):
            y = eligible[j]["entry_date"][:4] if j < len(eligible) else None
            if y != current_year:
                year_bounds[current_year] = (start, j)
                start = j
                current_year = y
        index.year_bounds[symbol] = year_bounds

        index.excess_by_symbol_direction[symbol] = {}
        index.prefix_sum[symbol] = {}
        index.prefix_count[symbol] = {}
        for direction in DIRECTIONS:
            values: list[float | None] = []
            for bar in eligible:
                spy_entry_idx = spy_date_to_idx.get(bar["entry_date"])
                if spy_entry_idx is None:
                    values.append(None)
                    continue
                spy_exit_idx = spy_entry_idx + horizon - 1
                if spy_exit_idx >= spy_n:
                    values.append(None)
                    continue
                position_return = _position_return(bar["entry_price"], bar["exit_price"], direction)
                values.append(
                    excess_return(position_return, direction, spy_closes[spy_entry_idx], spy_closes[spy_exit_idx])
                )
            index.excess_by_symbol_direction[symbol][direction] = values

            psum = [0.0] * (len(values) + 1)
            pcount = [0] * (len(values) + 1)
            for j, v in enumerate(values):
                if v is None:
                    psum[j + 1] = psum[j]
                    pcount[j + 1] = pcount[j]
                else:
                    psum[j + 1] = psum[j] + v
                    pcount[j + 1] = pcount[j] + 1
            index.prefix_sum[symbol][direction] = psum
            index.prefix_count[symbol][direction] = pcount

            agg = index.date_agg[direction]
            for bar, v in zip(eligible, values):
                if v is None:
                    continue
                s, c = agg.get(bar["entry_date"], (0.0, 0))
                agg[bar["entry_date"]] = (s + v, c + 1)

    return index


def baseline_symbol_year(index: HorizonIndex, symbol: str, year: str, exclude_list_idx: int, direction: str) -> float | None:
    """Baseline A: mean excess return of the same symbol's other eligible
    bars in the same calendar year, excluding every bar whose own list
    position is within `horizon` of `exclude_list_idx` (the detection's
    own entry) — not just the exact date — since a nearby bar's forward
    window overlaps the detection's own holding period and realizes
    largely the same price path. O(1): a year total minus an excluded
    window, both via prefix-sum subtraction."""
    bounds = index.year_bounds.get(symbol, {}).get(year)
    if bounds is None:
        return None
    start, end = bounds
    psum = index.prefix_sum[symbol][direction]
    pcount = index.prefix_count[symbol][direction]

    total_sum = psum[end] - psum[start]
    total_count = pcount[end] - pcount[start]

    win_lo = max(start, exclude_list_idx - index.horizon)
    win_hi = min(end, exclude_list_idx + index.horizon + 1)
    if win_hi > win_lo:
        excl_sum = psum[win_hi] - psum[win_lo]
        excl_count = pcount[win_hi] - pcount[win_lo]
    else:
        excl_sum, excl_count = 0.0, 0

    remaining_count = total_count - excl_count
    if remaining_count <= 0:
        return None
    return (total_sum - excl_sum) / remaining_count


def baseline_date_matched(index: HorizonIndex, entry_date: str, direction: str, own_excess: float | None) -> float | None:
    """Baseline B: mean excess return of every OTHER symbol in the
    universe whose eligible bars include this exact entry_date. Only the
    detection's own (symbol, date, direction) contribution is excluded —
    every other symbol's bar on the same date is an independent price
    path, so no window exclusion is needed there. O(1): a precomputed
    per-date sum/count minus the one known contribution."""
    total_sum, total_count = index.date_agg[direction].get(entry_date, (0.0, 0))
    if own_excess is not None:
        total_sum -= own_excess
        total_count -= 1
    if total_count <= 0:
        return None
    return total_sum / total_count


def score_detection_with_baselines(
    detection: dict,
    index: HorizonIndex,
    bar_index: dict[str, dict[str, int]],
) -> dict | None:
    """Ties one detection to its precomputed fixed-horizon excess return
    + both baselines + both abnormal returns, for the horizon `index` was
    built for. O(1) lookups into `index` — no rescanning. `bar_index`:
    {symbol: {date: absolute_bar_index}} (e.g. backtest_patterns.py's
    _bar_index_lookup()), used only to locate the detection's own entry
    bar within `index`'s precomputed arrays."""
    symbol = detection["symbol"]
    direction = detection["direction"]

    confirm_idx = bar_index.get(symbol, {}).get(detection["confirmed_on"])
    if confirm_idx is None:
        return None
    entry_idx = confirm_idx + 1
    list_idx = entry_idx - index.warmup_bars

    eligible = index.eligible_by_symbol.get(symbol, [])
    if list_idx < 0 or list_idx >= len(eligible):
        return None  # entry bar isn't itself eligible for this horizon (too close to warmup or series end)

    bar = eligible[list_idx]
    det_excess = index.excess_by_symbol_direction[symbol][direction][list_idx]
    if det_excess is None:
        return None

    year = bar["entry_date"][:4]
    baseline_a = baseline_symbol_year(index, symbol, year, list_idx, direction)
    baseline_b = baseline_date_matched(index, bar["entry_date"], direction, det_excess)

    return {
        "symbol": symbol,
        "pattern": detection["pattern"],
        "direction": direction,
        "entry_date": bar["entry_date"],
        "year": year,
        "month": month_key(bar["entry_date"]),
        "excess_return": det_excess,
        "baseline_a": baseline_a,
        "baseline_b": baseline_b,
        "abnormal_a": (det_excess - baseline_a) if baseline_a is not None else None,
        "abnormal_b": (det_excess - baseline_b) if baseline_b is not None else None,
    }
