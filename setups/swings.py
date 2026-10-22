"""Confirmed swing-high detection, by bar date (docs/SETUP_SPEC.md §5).

calc_swing_levels() (analyzers/technical.py:229-258) applies a 5-bar
fractal rule but discards bar dates/positions on the way out (its window
is built via `.reset_index(drop=True)`, and its return lists are
`sorted(set(...))` -- re-sorted by price and deduplicated). §5's setup
definition needs "the most recent confirmed swing high BY DATE", which
that return shape cannot supply. This module applies the IDENTICAL rule
(analyzers/technical.py:239-243: `for i in range(2, n-2): if highs[i] >
highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and
highs[i] > highs[i+2])`) but keeps each confirmed point's bar date,
returning them in chronological, undeduplicated order. calc_swing_levels()
itself is not modified -- it stays the right tool wherever only the
price-proximity values are needed.

Mechanical consequence of the same rule: the last 2 bars of any window
can never qualify as a swing high (there is no i+1/i+2 data after them
yet) -- this is restated, not newly introduced, by this module.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from analyzers.technical import _flatten


def find_confirmed_swing_highs(df: pd.DataFrame, lookback: int = 50) -> list[tuple[date, float]]:
    """Returns (bar_date, price) pairs for every confirmed swing high in
    the last `lookback` bars, in chronological order, undeduplicated (two
    chronologically distinct points may share an identical rounded
    price -- unlike calc_swing_levels()'s sorted(set(...)), both are kept
    here). The last entry is the most recent by date."""
    df = _flatten(df)
    window = df.iloc[-lookback:]
    highs = window["high"].tolist()
    dates = list(window.index)
    n = len(window)

    confirmed: list[tuple[date, float]] = []
    for i in range(2, n - 2):
        if (highs[i] > highs[i - 1] and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]):
            bar_date = dates[i]
            if isinstance(bar_date, datetime) or hasattr(bar_date, "date"):
                bar_date = bar_date.date()
            confirmed.append((bar_date, round(float(highs[i]), 4)))

    return confirmed
