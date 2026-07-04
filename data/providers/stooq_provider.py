"""
StooqProvider — primary historical-data provider (Phase 5), using Stooq's
keyless CSV export endpoint (no API key/config required). Yahoo/yfinance
remains the fallback provider for anything Stooq can't resolve.

Symbol mapping is intentionally best-effort only: Stooq's ticker format
(e.g. "aapl.us" for US equities) doesn't have a reliable general mapping
from the plain tickers used elsewhere in this project (indices like
"^GSPC", crypto like "BTC-USD", foreign listings, etc.). Anything this
mapping gets wrong simply comes back as an unresolved/empty CSV response,
which this provider reports as a clean failure (returns None) so
MarketDataService falls back to yfinance — it is never treated as an error.

Interval support: Stooq's endpoint only offers daily/weekly/monthly bars
(i=d/w/m). Any other interval (e.g. intraday) is an immediate, cheap
failure — no network call is made — so it falls back to yfinance instead.
"""
from __future__ import annotations

import io

import pandas as pd
import requests

from data.providers.base import MarketDataProvider

STOOQ_CSV_URL = "https://stooq.com/q/d/l/"
REQUEST_TIMEOUT_SECONDS = 10

_INTERVAL_MAP: dict[str, str] = {"1d": "d", "1wk": "w", "1mo": "m"}

# Approximate calendar-day lookback per yfinance-style period string, used to
# trim Stooq's full-history response down to roughly what was asked for.
# "max"/unrecognized periods return the full history Stooq provides.
_PERIOD_DAYS: dict[str, int] = {
    "1d": 1, "5d": 5, "1mo": 31, "3mo": 93, "6mo": 186,
    "1y": 365, "2y": 730, "5y": 1825, "10y": 3650,
}

_REQUIRED_COLUMNS = frozenset({"open", "high", "low", "close", "volume"})


def _map_symbol(symbol: str) -> str:
    """Best-effort translation of a plain ticker into Stooq's symbol format.
    Not guaranteed correct for indices/crypto/foreign listings — those are
    expected to come back empty and trigger a fallback to yfinance."""
    sym = symbol.strip()
    if sym.startswith("^"):
        return sym.lower()
    if sym.upper().endswith("-USD"):
        return sym[:-4].lower() + "usd"
    if "." in sym:
        return sym.lower()
    return f"{sym.lower()}.us"


def _slice_to_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    days = _PERIOD_DAYS.get(period)
    if days is None:
        return df
    cutoff = df.index.max() - pd.Timedelta(days=days)
    return df[df.index >= cutoff]


class StooqProvider(MarketDataProvider):
    name = "stooq"

    def __init__(self, *, session=None, timeout: float = REQUEST_TIMEOUT_SECONDS) -> None:
        # `session` defaults to the requests module itself — requests.get(...)
        # works the same way a requests.Session().get(...) call would for our
        # purposes. Tests inject a fake object with a compatible .get().
        self._session = session or requests
        self._timeout = timeout

    def fetch_history(self, symbol: str, period: str, interval: str) -> pd.DataFrame | None:
        stooq_interval = _INTERVAL_MAP.get(interval)
        if stooq_interval is None:
            return None

        params = {"s": _map_symbol(symbol), "i": stooq_interval}
        try:
            resp = self._session.get(STOOQ_CSV_URL, params=params, timeout=self._timeout)
            resp.raise_for_status()
        except Exception:
            return None

        text = resp.text
        if not text or not text.strip():
            return None

        try:
            df = pd.read_csv(io.StringIO(text))
        except Exception:
            return None

        if df.empty:
            return None

        df.columns = [c.lower() for c in df.columns]
        if "date" not in df.columns:
            return None

        try:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
        except Exception:
            return None

        if not _REQUIRED_COLUMNS.issubset(set(df.columns)):
            return None

        df = _slice_to_period(df, period)
        return df if not df.empty else None
