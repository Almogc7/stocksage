"""
YFinanceProvider — thin wrapper around the existing, unmodified
data.fetcher.get_historical(). Used as the fallback provider by
MarketDataService when the primary provider (Stooq) fails.

This does not change get_historical() or any of its existing production
callers (agent/core.py, bot/telegram_bot.py, dashboard.py) in any way.
"""
from __future__ import annotations

import pandas as pd

from data.fetcher import get_historical
from data.providers.base import MarketDataProvider


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    def fetch_history(self, symbol: str, period: str, interval: str) -> pd.DataFrame | None:
        return get_historical(symbol, period=period, interval=interval)
