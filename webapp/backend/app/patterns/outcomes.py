"""Backtest-only forward-outcome scoring. Not used by the live /api/patterns
endpoint — only by scripts/backtest_patterns.py.

Entry timing: `confirmed_on` is the date of the bar whose CLOSE confirms a
pattern, so nobody could have acted on it before the next session.
Therefore entry = the NEXT bar's OPEN after `confirmed_on`; T+5/10/20 are
counted from that entry bar (entry bar = bar 0, "Nth bar from entry
inclusive", mirroring the bot's compute_outcome() "Nth completed bar"
convention); the stop scan starts WITH the entry bar itself, since the
position is opened intrabar at that bar's open and a stop breach later
the same session is a real loss, not a look-ahead issue. T+N close fields
are always the raw market closes (informational, same as
compute_outcome()'s unconditional close_t1..t10), independent of whether
a stop was hit in between — the barrier/r_multiple is tracked separately.

Gaps: if the entry bar's own OPEN is already on the wrong side of the
stop, the setup gapped through its stop before the trade could begin —
the detection is EXCLUDED, not scored. If a later bar's OPEN is already
past the stop (overnight gap), the exit price is that bar's OPEN, not the
stop level (nobody could have been filled at a price nobody traded at);
only an ordinary intrabar touch (open still clear, low/high crosses the
stop) exits at the stop level itself.
"""
import pandas as pd

from . import config


def compute_forward_outcome(
    bars: pd.DataFrame,
    confirmed_on: str,
    direction: str,
    stop_price: float,
    horizons: tuple[int, ...] = config.FORWARD_HORIZONS,
) -> dict:
    """`bars`: OHLC DataFrame indexed by date string, ascending, containing
    `confirmed_on` and (ideally) bars beyond it."""
    dates = bars.index.to_list()
    if confirmed_on not in dates:
        raise ValueError(f"confirmed_on {confirmed_on!r} not found in bars")
    confirm_idx = dates.index(confirmed_on)
    entry_idx = confirm_idx + 1
    max_horizon = max(horizons)

    if entry_idx >= len(dates):
        return {"excluded": True, "reason": "no_bar_after_confirmation"}

    entry_date = dates[entry_idx]
    entry_price = float(bars["open"].iloc[entry_idx])

    gapped_through_at_entry = (
        (direction == "long" and entry_price <= stop_price)
        or (direction == "short" and entry_price >= stop_price)
    )
    if gapped_through_at_entry:
        return {
            "excluded": True,
            "reason": "entry_already_through_stop",
            "entry_date": entry_date,
            "entry_price": entry_price,
        }

    scan_end_idx = min(entry_idx + max_horizon - 1, len(dates) - 1)
    exit_idx: int | None = None
    exit_price: float | None = None
    exit_date: str | None = None

    for j in range(entry_idx, scan_end_idx + 1):
        o = float(bars["open"].iloc[j])
        lo = float(bars["low"].iloc[j])
        hi = float(bars["high"].iloc[j])
        if direction == "long":
            if o <= stop_price:
                exit_idx, exit_price = j, o
            elif lo <= stop_price:
                exit_idx, exit_price = j, stop_price
        else:
            if o >= stop_price:
                exit_idx, exit_price = j, o
            elif hi >= stop_price:
                exit_idx, exit_price = j, stop_price
        if exit_idx is not None:
            exit_date = dates[j]
            break

    closes: dict[int, float | None] = {}
    for h in horizons:
        idx = entry_idx + h - 1
        closes[h] = float(bars["close"].iloc[idx]) if idx < len(dates) else None

    first_barrier_hit = "stop_loss" if exit_idx is not None else "none"
    window_complete = (scan_end_idx - entry_idx + 1) >= max_horizon

    r_multiple = None
    risk = (entry_price - stop_price) if direction == "long" else (stop_price - entry_price)
    if exit_idx is not None and risk > 0:
        r_multiple = (
            (exit_price - entry_price) / risk if direction == "long" else (entry_price - exit_price) / risk
        )
    elif exit_idx is None and window_complete and risk > 0:
        timeout_close = closes.get(max_horizon)
        if timeout_close is not None:
            exit_price = timeout_close
            exit_date = dates[scan_end_idx]
            r_multiple = (
                (exit_price - entry_price) / risk
                if direction == "long"
                else (entry_price - exit_price) / risk
            )

    return {
        "excluded": False,
        "reason": None,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "first_barrier_hit": first_barrier_hit,
        "r_multiple": r_multiple,
        "closes": closes,
    }


def excess_return(position_return: float, direction: str, spy_entry: float, spy_exit: float) -> float:
    """Signed consistently for shorts: a short is compared against what
    SHORTING SPY over the same dates would have returned, not against raw
    SPY price return — so a short that merely falls with a falling
    market, no more than SPY itself fell, scores ~0, not a false
    positive."""
    spy_price_return = (spy_exit - spy_entry) / spy_entry
    benchmark_return = spy_price_return if direction == "long" else -spy_price_return
    return position_return - benchmark_return
