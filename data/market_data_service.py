"""
MarketDataService — tries a list of MarketDataProvider instances, in order,
and returns the first one that produces valid, usable OHLCV data. Default
order: StooqProvider (primary) then YFinanceProvider (fallback).

Failure classification — any of the following on a given provider is treated
as a failure and the next provider in the list is tried:
  1. The provider raised an exception.
  2. It returned None or an empty DataFrame.
  3. The DataFrame is missing one of the required OHLCV columns.
  4. The DataFrame has fewer than `min_candles` rows.

This module does not decide promotion/demotion, does not write to any
table, and does not send Telegram messages — it is a pure fetch layer
sitting in front of data/history_store.py. It is a new, separate component
from data/market_data_validator.py's MarketDataClient (built for the
watchlist eligibility evaluator) and does not touch that module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from data.providers.base import MarketDataProvider
from data.providers.stooq_provider import StooqProvider
from data.providers.yfinance_provider import YFinanceProvider

REQUIRED_COLUMNS = frozenset({"open", "high", "low", "close", "volume"})

# Sanity threshold, not the "at least 250 candles" period requirement (that
# is satisfied by requesting period="1y" — see data/history_store.py). This
# just catches obviously-broken responses (e.g. a delisted symbol coming
# back with 1-2 rows) without penalizing legitimately short fetches.
DEFAULT_MIN_CANDLES = 5


@dataclass
class ProviderFetchResult:
    df: pd.DataFrame | None
    provider_name: str | None
    attempted: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.df is not None


def _classify_failure(df: pd.DataFrame | None, min_candles: int) -> str | None:
    if df is None or df.empty:
        return "empty_or_none"
    cols = {c.lower() for c in df.columns}
    if not REQUIRED_COLUMNS.issubset(cols):
        return "missing_ohlcv_columns"
    if len(df) < min_candles:
        return "insufficient_candles"
    return None


class MarketDataService:
    def __init__(
        self,
        providers: list[MarketDataProvider] | None = None,
        *,
        min_candles: int = DEFAULT_MIN_CANDLES,
    ) -> None:
        self.providers = providers if providers is not None else [StooqProvider(), YFinanceProvider()]
        self.min_candles = min_candles

    def fetch_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> ProviderFetchResult:
        attempted: list[str] = []
        failures: dict[str, str] = {}

        for provider in self.providers:
            attempted.append(provider.name)
            try:
                df = provider.fetch_history(symbol, period, interval)
            except Exception as exc:
                failures[provider.name] = f"exception: {exc}"
                continue

            failure_reason = _classify_failure(df, self.min_candles)
            if failure_reason is not None:
                failures[provider.name] = failure_reason
                continue

            return ProviderFetchResult(
                df=df, provider_name=provider.name, attempted=attempted, failures=failures
            )

        return ProviderFetchResult(df=None, provider_name=None, attempted=attempted, failures=failures)
