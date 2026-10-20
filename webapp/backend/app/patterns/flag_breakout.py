"""Pattern 2: flag/base breakout (long) or breakdown (short).

Every valid (p_start, p_end) base window is scored independently, rather
than picking "the longest" base per p_start across the whole pivot list —
that comparison would depend on pivots that may not exist yet at an
earlier point in time, which would break the no-look-ahead guarantee
(a later, longer-spanning base becoming visible could retroactively
change which base — and therefore which breakout — an earlier truncation
point would have reported). Evaluating every window independently means
each detection's fields depend only on data up to its own confirmed_on.

SAME-BAR TIE-BREAK (named rule, fixed — not tunable, not arbitrary):
several different base windows can independently satisfy
BASE_MAX_RANGE_ATR and confirm a breakout on the exact same bar (same
`direction`, same `confirmed_on`) — e.g. a 12-bar base and a 40-bar base
both ending just before the same breakout day. Exactly one of them must
"win" — its `levels["base_low"]`/`base_high` is what becomes the fixed
stop in stops.py, so this has to be a real, documented decision, not
accidental insertion order. The rule, applied only within a single
(direction, confirmed_on) group:
  1. widest window wins (largest `base_bars`) — more bars backing the
     base is more evidence it was a genuine consolidation, not noise;
  2. tie -> tightest range wins (smallest `base_range_atr`) — a cleaner,
     tighter base is the stronger setup among equally-wide candidates;
  3. tie -> earliest `start_date` wins — a final, fully deterministic
     tie-break (ties past step 2 should be exceedingly rare synthetic
     edge cases, not a real market pattern).
All three fields (`base_bars`, `base_range_atr`, `start_date`) are
already fixed facts about each candidate window by the time it reaches
`confirmed_on` — none of them depend on any bar after `confirmed_on`, so
this selection cannot look ahead. See _select_survivor() /
_dedupe_same_bar() below, and test_patterns_flag_breakout.py's
SameBarTieBreakTests for the behavior this locks in."""
import pandas as pd

from . import config
from .indicators import atr as atr_series
from .types import Detection
from .zigzag import find_pivots


def _select_survivor(group: list[Detection]) -> Detection:
    """The named same-bar tie-break: widest window, then tightest range,
    then earliest start_date. See module docstring."""
    return sorted(
        group,
        key=lambda d: (-d.rules["base_bars"], d.rules["base_range_atr"], d.start_date),
    )[0]


def _dedupe_same_bar(detections: list[Detection]) -> list[Detection]:
    groups: dict[tuple, list[Detection]] = {}
    for d in detections:
        groups.setdefault((d.direction, d.confirmed_on), []).append(d)
    return [_select_survivor(group) for group in groups.values()]


def detect_flag_breakout(df: pd.DataFrame, symbol: str) -> list[Detection]:
    pivots = find_pivots(df)
    dates = df.index.to_list()
    idx_of_date = {d: i for i, d in enumerate(dates)}
    atr_s = atr_series(df)
    n = len(df)
    detections: list[Detection] = []

    for si, p_start in enumerate(pivots):
        start_idx = idx_of_date[p_start.date]
        for p_end in pivots[si + 1 :]:
            end_idx = idx_of_date[p_end.date]
            bars = end_idx - start_idx
            if bars < config.BASE_MIN_BARS:
                continue
            if bars > config.BASE_MAX_BARS:
                break

            # ATR at the base's confirming bar (p_end.confirmed_on), never a
            # later bar — this is what keeps a volatility-normalized
            # threshold no-look-ahead.
            confirm_idx = idx_of_date[p_end.confirmed_on]
            atr_at_confirm = atr_s.iloc[confirm_idx]
            if pd.isna(atr_at_confirm):
                continue
            window = df.iloc[start_idx : end_idx + 1]
            base_high = float(window["high"].max())
            base_low = float(window["low"].min())
            base_range = base_high - base_low
            range_atr = base_range / atr_at_confirm
            if range_atr > config.BASE_MAX_RANGE_ATR:
                continue

            avg_volume = float(window["volume"].mean())
            scan_end = min(confirm_idx + config.BASE_MAX_BARS, n)
            for j in range(confirm_idx, scan_end):
                a = atr_s.iloc[j]
                if pd.isna(a):
                    continue
                bar = df.iloc[j]
                relvol = bar["volume"] / avg_volume if avg_volume else 0.0

                if bar["close"] > base_high + config.BREAKOUT_ATR_MULT * a and relvol >= config.BREAKOUT_RELVOL_MIN:
                    detections.append(
                        Detection(
                            pattern="flag_breakout",
                            direction="long",
                            symbol=symbol,
                            start_date=p_start.date,
                            end_date=dates[j],
                            confirmed_on=dates[j],
                            levels={"base_high": base_high, "base_low": base_low, "breakout_price": float(bar["close"])},
                            rules={"base_range_atr": range_atr, "base_bars": bars, "relvol_at_breakout": relvol},
                        )
                    )
                    break
                if bar["close"] < base_low - config.BREAKOUT_ATR_MULT * a and relvol >= config.BREAKOUT_RELVOL_MIN:
                    detections.append(
                        Detection(
                            pattern="flag_breakout",
                            direction="short",
                            symbol=symbol,
                            start_date=p_start.date,
                            end_date=dates[j],
                            confirmed_on=dates[j],
                            levels={"base_high": base_high, "base_low": base_low, "breakout_price": float(bar["close"])},
                            rules={"base_range_atr": range_atr, "base_bars": bars, "relvol_at_breakout": relvol},
                        )
                    )
                    break

    return _dedupe_same_bar(detections)
