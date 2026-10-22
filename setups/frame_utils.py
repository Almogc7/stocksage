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
    the same defense check_sma150()/_sma200() apply internally) and
    lowercases column names. Does not check that every required column is
    present -- see missing_required_columns() for that. Never mutates the
    input (returns a new frame).
    """
    df = _flatten(df)
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    return df


def missing_required_columns(df: pd.DataFrame) -> set[str]:
    """REQUIRED_COLUMNS not present in df.columns -- callers treat a
    non-empty result as MISSING_OHLCV rather than letting a later stage
    raise KeyError."""
    return set(REQUIRED_COLUMNS) - set(df.columns)
