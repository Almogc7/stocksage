"""ATR-scaled zigzag pivot detection — the no-look-ahead foundation every
pattern detector in this package is built on.

A pivot is only CONFIRMED once price has reversed away from it by
`ZIGZAG_ATR_MULT * ATR` — a price-threshold, not a fixed bar count, so it
is structurally impossible to know "this was the high/low" before that
reversal has actually happened. `Pivot.confirmed_on` is the date of the
bar that crossed the threshold (always >= `Pivot.date`, the extreme's own
bar); `Pivot.date`/`.price`/`.kind` are fixed the instant the pivot is
confirmed and never revisited afterward, which is exactly what makes
truncating the input series safe: every already-confirmed pivot's fields
are identical whether or not later bars exist (see
tests/test_patterns_zigzag.py and tests/test_patterns_no_lookahead.py).
"""
import pandas as pd

from . import config
from .indicators import atr as atr_series
from .types import Pivot


def find_pivots(
    df: pd.DataFrame,
    atr_period: int = config.ATR_PERIOD,
    atr_mult: float = config.ZIGZAG_ATR_MULT,
) -> list[Pivot]:
    """`df`: DataFrame indexed by date string, with high/low columns (and
    whatever else, ignored). Returns pivots in chronological order."""
    a = atr_series(df, atr_period)
    dates = df.index.to_list()
    highs = df["high"].to_list()
    lows = df["low"].to_list()
    n = len(df)

    start = atr_period  # first index with a non-NaN ATR (min_periods=atr_period)
    if n <= start:
        return []

    mode: str | None = None
    candidate_high_idx, candidate_high = start, highs[start]
    candidate_low_idx, candidate_low = start, lows[start]
    pivots: list[Pivot] = []

    for i in range(start + 1, n):
        h, l = highs[i], lows[i]
        thresh = atr_mult * a.iloc[i]
        if pd.isna(thresh):
            continue

        if mode is None or mode == "seeking_high":
            if h > candidate_high:
                candidate_high, candidate_high_idx = h, i
        if mode is None or mode == "seeking_low":
            if l < candidate_low:
                candidate_low, candidate_low_idx = l, i

        confirmed_high = mode in (None, "seeking_high") and (candidate_high - l) >= thresh
        confirmed_low = mode in (None, "seeking_low") and (h - candidate_low) >= thresh

        if mode is None and confirmed_high and confirmed_low:
            # Degenerate case: both thresholds crossed on the same
            # initializing bar. Deterministic tie-break: whichever
            # exceeded its threshold by more wins; a tie prefers "high".
            excess_high = (candidate_high - l) - thresh
            excess_low = (h - candidate_low) - thresh
            if excess_high >= excess_low:
                confirmed_low = False
            else:
                confirmed_high = False

        if confirmed_high:
            pivots.append(
                Pivot(date=dates[candidate_high_idx], price=candidate_high, kind="high", confirmed_on=dates[i])
            )
            mode = "seeking_low"
            candidate_low, candidate_low_idx = l, i
        elif confirmed_low:
            pivots.append(
                Pivot(date=dates[candidate_low_idx], price=candidate_low, kind="low", confirmed_on=dates[i])
            )
            mode = "seeking_high"
            candidate_high, candidate_high_idx = h, i

    return pivots
