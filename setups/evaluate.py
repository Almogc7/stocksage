"""Single entry point for a scan candidate (docs/SETUP_SPEC.md §3-4).

This is the only function later steps (the EOD scan script) should call --
it owns "truncate first, then validate, then filter" so that ordering can't
be gotten wrong by a future caller. The lower-level functions it calls stay
independently unit-tested on their own.

Frame contract for callers (the future EOD scan script, and every test in
this package): `df_full` must have --
  - lowercase column names `open`, `high`, `low`, `close`, `volume` (a
    MultiIndex-column frame, e.g. straight from `yf.download()`, or
    uppercase/mixed-case column names are both tolerated -- normalized
    below -- but the five required columns must exist under some
    case-insensitive spelling of their own name once flattened);
  - an ascending, unique `DatetimeIndex` (checked by
    `validate_scan_data()`'s `MALFORMED_INDEX` gate);
  - adjusted prices, matching `data/fetcher.py::get_historical()`
    (`:40-56`, `auto_adjust=True`, and that function already flattens/
    lowercases its own output the same way `normalize_frame()` does here)
    -- this package does not itself fetch data or re-adjust anything.
This is a pure-function contract; nothing in this package enforces it at
a process boundary (no network/DB access happens here at all).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from setups.data_validation import validate_scan_data
from setups.frame_utils import missing_required_columns, normalize_frame
from setups.params import LIQUIDITY_LOOKBACK_SESSIONS
from setups.reason_codes import MISSING_OHLCV
from setups.results import Result as CandidateResult
from setups.time_utils import truncate_to_date
from setups.trend_filter import evaluate_trend_and_eligibility


def evaluate_candidate(
    df_full: pd.DataFrame, scan_date: date, expected_session_date: date
) -> CandidateResult:
    # (0) Not a DataFrame at all (None, or any other wrong type) --
    # reject before anything downstream (truncate_to_date, normalize_frame)
    # touches an attribute that doesn't exist on it.
    if not isinstance(df_full, pd.DataFrame):
        return CandidateResult(passed=False, reason_code=MISSING_OHLCV)

    # (1) No-look-ahead: truncate before anything else touches the data.
    # Idempotent -- truncating an already-truncated frame to the same date
    # is a no-op.
    df = truncate_to_date(df_full, scan_date)

    # (1b) Normalize once, right after truncation and before validation:
    # flatten MultiIndex columns, lowercase names, and confirm the five
    # required OHLCV columns exist -- a missing one is reported as
    # MISSING_OHLCV rather than letting a later stage raise KeyError.
    df = normalize_frame(df)
    if missing_required_columns(df):
        return CandidateResult(passed=False, reason_code=MISSING_OHLCV)

    # (2) Validate.
    validation = validate_scan_data(df, expected_session_date)
    if not validation.passed:
        return CandidateResult(passed=False, reason_code=validation.reason_code)

    # (3) Liquidity inputs over the truncated frame's tail window only,
    # computed once here and passed down -- trend_filter.py doesn't
    # re-slice df itself. validate_scan_data() above only guarantees the
    # LAST bar's close/volume are non-NaN, not the rest of this window, so
    # a NaN anywhere else in it is caught explicitly here instead of
    # silently falling out of pandas' default skipna=True mean() (which
    # would otherwise let a window that's mostly missing data still
    # produce a "full" average and clear the liquidity gate on, in the
    # extreme, a single real bar).
    window = df.tail(LIQUIDITY_LOOKBACK_SESSIONS)
    if window[["close", "volume"]].isna().any().any():
        return CandidateResult(passed=False, reason_code=MISSING_OHLCV)
    avg_volume = float(window["volume"].mean())
    avg_dollar_vol = float((window["close"] * window["volume"]).mean())

    # (4) Trend/eligibility filter.
    filt = evaluate_trend_and_eligibility(df, avg_volume, avg_dollar_vol)
    if not filt.passed:
        return CandidateResult(passed=False, reason_code=filt.reason_code)

    return CandidateResult(passed=True)
