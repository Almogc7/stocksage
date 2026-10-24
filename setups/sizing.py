"""Position sizing (docs/SETUP_SPEC.md §10): position_size = floor(
RISK_PER_TRADE_USD / (entry - stop)), exactly as specified.

`risk_usd` is an injected argument here -- reading RISK_PER_TRADE_USD from
the environment is explicitly a later step (§10's own text), not this
pure function's job.
"""
from __future__ import annotations

import math


def position_size(risk_usd: float, entry: float, stop: float) -> int | None:
    """Never raises. Returns None for non-numeric input, NaN/infinite
    input, risk_usd <= 0, or entry <= stop (zero/negative risk distance)."""
    try:
        risk_usd = float(risk_usd)
        entry = float(entry)
        stop = float(stop)
    except (TypeError, ValueError):
        return None

    for value in (risk_usd, entry, stop):
        if math.isnan(value) or math.isinf(value):
            return None

    risk_distance = entry - stop
    if risk_usd <= 0 or risk_distance <= 0:
        return None

    return math.floor(risk_usd / risk_distance)
