"""EOD data validation (docs/SETUP_SPEC.md §3).

Each check is a hard gate run in a fixed order, short-circuiting the rest,
so e.g. a short-history name is never misreported as a trend-filter
failure, and a stale frame's history length is checked before its date.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from analyzers.technical import _flatten
from setups.params import MIN_HISTORY_BARS
from setups.reason_codes import (
    DATA_NOT_FINAL,
    INSUFFICIENT_HISTORY,
    MALFORMED_INDEX,
    MISSING_OHLCV,
    ZERO_VOLUME_LAST_BAR,
)
from setups.results import Result as ValidationResult


def validate_scan_data(df: pd.DataFrame, expected_session_date: date) -> ValidationResult:
    # Defensive only: evaluate_candidate() already normalizes (flattens +
    # lowercases) before calling this, but a MultiIndex-column frame
    # passed directly must not crash this function either.
    df = _flatten(df)

    # (1) MALFORMED_INDEX -- structural, checked before anything that
    # assumes a well-formed index.
    if not (df.index.is_monotonic_increasing and df.index.is_unique):
        return ValidationResult(passed=False, reason_code=MALFORMED_INDEX)

    # (2) INSUFFICIENT_HISTORY -- before the trend filter and before the
    # date check, so a short-history name is never misreported as
    # TREND_FILTER_FAILED or DATA_NOT_FINAL.
    if len(df) < MIN_HISTORY_BARS:
        return ValidationResult(passed=False, reason_code=INSUFFICIENT_HISTORY)

    # (3) Exact last-bar-date equality (not the tolerant gap_days check
    # data/market_data_validator.py uses). .date() handles a tz-aware
    # last-bar index transparently.
    last_bar_date = df.index[-1]
    if hasattr(last_bar_date, "date"):
        last_bar_date = last_bar_date.date()
    if last_bar_date != expected_session_date:
        return ValidationResult(passed=False, reason_code=DATA_NOT_FINAL)

    # (4) No NaN in close -- both the whole-column case (all-NaN close) and
    # the specific, narrower case of just the LAST bar's close being NaN
    # (which dropna().empty alone would miss whenever any earlier bar has
    # a valid close, letting a NaN current_price silently reach the trend
    # filter and get misreported as TREND_FILTER_FAILED instead).
    if df["close"].dropna().empty or pd.isna(df["close"].iloc[-1]):
        return ValidationResult(passed=False, reason_code=MISSING_OHLCV)

    # (5) Last bar's volume > 0 -- the gap §3 flags: the existing validator
    # only ever checks the lookback *average*, never the last bar.
    last_volume = df["volume"].iloc[-1]
    if pd.isna(last_volume) or last_volume <= 0:
        return ValidationResult(passed=False, reason_code=ZERO_VOLUME_LAST_BAR)

    return ValidationResult(passed=True)
