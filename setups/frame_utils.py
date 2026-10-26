"""Frame normalization shared by the setups/ pipeline (docs/SETUP_SPEC.md
§3's data-validation gap and §5/§13a's indicator contract).

evaluate_candidate() (evaluate.py) calls normalize_frame() once, right
after truncation and before any validation, so every later stage sees a
flat-column, lowercase-column frame regardless of what shape the caller
handed in (a MultiIndex-column frame straight from yf.download(), or one
with uppercase/mixed-case OHLCV column names).
"""
from __future__ import annotations

import pandas as pd

from analyzers.technical import _flatten

REQUIRED_COLUMNS = frozenset({"open", "high", "low", "close", "volume"})


def normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Flattens MultiIndex columns (reusing analyzers.technical._flatten,
    the same defense check_sma150()/_sma200() apply internally), lowercases
    column names, and coerces each present required column to numeric
    (pd.to_numeric(..., errors="coerce")) -- an object-dtype column of
    numeric-looking strings (e.g. straight from a CSV round-trip) becomes
    a real float column, and a column with genuinely non-numeric values
    becomes NaN rather than raising later wherever that column is first
    used in arithmetic or an ordering comparison. Does not check that
    every required column is present -- see missing_required_columns()
    for that. Never mutates the input (returns a new frame).
    """
    df = _flatten(df)
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    for col in REQUIRED_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def missing_required_columns(df: pd.DataFrame) -> set[str]:
    """REQUIRED_COLUMNS not present in df.columns -- callers treat a
    non-empty result as MISSING_OHLCV rather than letting a later stage
    raise KeyError."""
    return set(REQUIRED_COLUMNS) - set(df.columns)


def drop_all_nan_ohlc_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drops any row where open/high/low/close are ALL NaN -- a genuinely
    missing bar, not a partial data problem. yfinance's multi-ticker
    downloads pad a late-listed symbol with rows like this for every
    calendar date before the symbol actually existed, covering the whole
    requested date range; left in place, these inflate every later stage's
    row count (evaluate_candidate()'s MIN_HISTORY_BARS check in particular)
    without being real history. A row with a real NaN in only SOME of the
    four columns is left untouched -- that is a partial-data problem for
    setups/data_validation.py's own MISSING_OHLCV checks to catch, not a
    padding artifact. If ANY of the four columns is missing entirely (not
    just NaN-valued -- an absent column), the frame is returned unchanged:
    there's no way to tell "all four NaN" from "partially present" without
    all four actually being there, and missing_required_columns()/
    MISSING_OHLCV is the correct place to reject that shape, not this
    function guessing which rows would have been padding. Applied wherever
    a frame enters the harness (a fresh multi-ticker download, or a frame
    read back from the on-disk cache) -- never on every call to
    normalize_frame(), so the cost is paid once per fetch/load, not once
    per scan date. Never mutates the input."""
    required = ("open", "high", "low", "close")
    if not all(col in df.columns for col in required):
        return df.copy()
    return df.loc[~df[list(required)].isna().all(axis=1)].copy()
