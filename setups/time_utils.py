"""No-look-ahead truncation helper shared by every indicator call in the
setups/ stack (docs/SETUP_SPEC.md §5's confirmation-lag section, generalized
in §13a: every indicator, in both production and historical replay, must be
computed from a dataframe truncated to the scan date).
"""
from datetime import date, datetime

import pandas as pd


def truncate_to_date(df: pd.DataFrame, scan_date: date) -> pd.DataFrame:
    """Returns a NEW DataFrame containing only rows whose index date is
    <= scan_date. Never mutates `df`. Works for both tz-aware and tz-naive
    DatetimeIndex values (comparison is done on the plain date, not the
    full timestamp). Idempotent: truncating an already-truncated frame to
    the same date is a no-op.
    """
    if isinstance(scan_date, datetime):
        scan_date = scan_date.date()
    index_dates = pd.Series(df.index).apply(
        lambda ts: ts.date() if hasattr(ts, "date") else ts
    )
    mask = (index_dates <= scan_date).values
    return df.loc[mask].copy()
