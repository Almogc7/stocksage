"""
Cached-DB indicator calculations (Phase 6).

Computes SMA20/50/150/200 (and a "rising vs N rows ago" check for each)
purely from prices already stored in stock_prices (Phase 3) via
data.history_store.get_latest_prices() (Phase 4/5) — no network calls, no
yfinance/Stooq access. This is a separate, DB-only module from
analyzers/technical.py, which remains the single source of truth for the
live-fetched production analysis path (full_analysis(), used by the alert
agent and /analyze). Nothing here is wired into that path, the live alert
flow, or any scanner logic.

"Rising" is row-count-based, not calendar-date-based: the comparison is
between the current SMA and the SMA value `rising_lookback` *stored rows*
earlier in the same series, not the SMA `rising_lookback` calendar days ago.
If stock_prices has gaps (a trading day that was never fetched), this shifts
what "N days ago" actually means — acceptable given the current full-period
refetch design (Phase 4/5), but worth knowing if incremental fetching is
added later.

"Rising" uses a strict `>` comparison, so a flat/constant series is reported
as not rising (rising=False), never as insufficient data.
"""
from __future__ import annotations

import pandas as pd

from data.history_store import get_latest_prices

SMA_WINDOWS: tuple[int, ...] = (20, 50, 150, 200)
DEFAULT_N = 250
DEFAULT_RISING_LOOKBACK = 10


def _closes_series(rows: list[dict]) -> pd.Series:
    """
    Build a date-indexed Series of closes from stock_prices rows, dropping
    any row with a missing/None close. Row order (ascending by date, as
    returned by get_latest_prices) is preserved.
    """
    dates: list[str] = []
    closes: list[float] = []
    for row in rows:
        close = row.get("close")
        if close is None:
            continue
        try:
            closes.append(float(close))
        except (TypeError, ValueError):
            continue
        dates.append(row["date"])
    return pd.Series(closes, index=pd.to_datetime(dates))


def sma(closes: pd.Series, window: int) -> float | None:
    """Simple moving average of the most recent `window` usable closes, or
    None if there aren't enough."""
    if len(closes) < window:
        return None
    return float(closes.tail(window).mean())


def sma_series(closes: pd.Series, window: int) -> pd.Series:
    """Full rolling SMA series (needed to look back N rows for is_rising)."""
    return closes.rolling(window=window).mean()


def is_rising(closes: pd.Series, window: int, lookback: int) -> bool | None:
    """
    True if SMA(window) computed now is greater than SMA(window) computed
    `lookback` stored rows ago. None if there isn't enough history for both
    points (needs at least window + lookback usable closes).
    """
    if len(closes) < window + lookback:
        return None
    series = sma_series(closes, window)
    current = series.iloc[-1]
    past = series.iloc[-1 - lookback]
    if pd.isna(current) or pd.isna(past):
        return None
    return bool(current > past)


def compute_cached_indicators(
    symbol: str,
    timeframe: str = "1d",
    *,
    n: int = DEFAULT_N,
    rising_lookback: int = DEFAULT_RISING_LOOKBACK,
) -> dict:
    """
    Read the most recent `n` stored bars for symbol/timeframe and compute
    SMA20/50/150/200 plus a rising flag for each. Never raises — insufficient
    data for a given SMA window simply yields value=None, rising=None, and
    that window's key is listed in insufficient_data_for.
    """
    rows = get_latest_prices(symbol, timeframe=timeframe, n=n)
    closes = _closes_series(rows)

    result: dict = {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "candles_used": len(closes),
        "as_of_date": closes.index[-1].strftime("%Y-%m-%d") if len(closes) else None,
    }

    insufficient: list[str] = []
    for window in SMA_WINDOWS:
        key = f"sma{window}"
        value = sma(closes, window)
        rising = is_rising(closes, window, rising_lookback)
        result[key] = {"value": round(value, 4) if value is not None else None, "rising": rising}
        if value is None:
            insufficient.append(key)

    result["insufficient_data_for"] = insufficient
    return result
