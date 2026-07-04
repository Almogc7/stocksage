"""
Provider abstraction for historical OHLCV market data (Phase 5).

Each provider implements fetch_history() and returns a DataFrame with
lowercase columns (open, high, low, close, volume) and a date index — the
same shape data.fetcher.get_historical() already produces — or None if it
could not retrieve usable data.

Providers should not raise on ordinary failure (bad symbol, network error,
empty response); returning None is the expected failure signal.
MarketDataService additionally catches any exception a provider does raise,
treating it the same as a None return, so a provider bug can never take
down the whole fetch attempt.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    def fetch_history(self, symbol: str, period: str, interval: str) -> pd.DataFrame | None:
        """Return a lowercase-OHLCV DataFrame indexed by date, or None on failure."""
        raise NotImplementedError
