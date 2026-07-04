"""
Historical OHLCV storage for the scanner engine (Phase 4, updated in
Phase 5 to fetch through MarketDataService instead of yfinance directly).

Fetches daily history via MarketDataService (Stooq primary, yfinance
fallback — Phase 5) and persists it into the stock_prices table (added in
Phase 3) using the insert_stock_prices()/get_stock_prices() helpers already
in db/database.py.

What this module does NOT do:
  - No scanner/scoring logic — this is a pure fetch-and-cache layer.
  - Does not touch agent/core.py's live alert path or any Telegram code.
  - Does not introduce incremental fetching — every call still re-fetches
    the full requested period (kept for Phase 5; see data/market_data_service.py
    docstring for the provider-fallback design this builds on).

Idempotency: insert_stock_prices() upserts on UNIQUE(symbol, timeframe,
date), so calling fetch_and_store_history() again for the same symbol/period
updates existing rows in place rather than creating duplicates.

Provider source: each stored row's `source` column reflects whichever
provider (e.g. "stooq" or "yfinance") actually returned the data for that
fetch, not a fixed default.
"""
from __future__ import annotations

import math

from data.market_data_service import MarketDataService
from db.database import get_stock_prices, insert_stock_prices

# "1y" comfortably covers the >=250 completed daily candles this phase
# requires (roughly 252 trading days/year).
DEFAULT_PERIOD = "1y"
DEFAULT_INTERVAL = "1d"


def _safe_float(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _safe_int(value) -> int | None:
    f = _safe_float(value)
    return None if f is None else int(f)


def fetch_and_store_history(
    symbol: str,
    *,
    period: str = DEFAULT_PERIOD,
    interval: str = DEFAULT_INTERVAL,
    service: MarketDataService | None = None,
) -> int:
    """
    Fetch daily OHLCV history for `symbol` via MarketDataService (Stooq
    primary, yfinance fallback) and upsert it into stock_prices.

    `service` defaults to a fresh MarketDataService() (Stooq then yfinance);
    pass an explicit instance to control provider order/behavior, or a fake
    in tests to avoid any real network/provider call.

    Returns the number of rows written (0 if every provider failed or
    returned no usable data — never raises on a fetch failure).

    Rows with no usable 'close' value are skipped (stock_prices.close is
    NOT NULL); all other OHLCV fields are stored as-is, coerced to
    plain Python float/int (NaN becomes None). The `source` column on every
    stored row reflects whichever provider actually supplied the data.
    """
    sym = symbol.upper()
    svc = service or MarketDataService()
    result = svc.fetch_history(sym, period=period, interval=interval)

    if not result.ok:
        print(
            f"[history_store] Warning: no historical data to store for {sym}"
            f" (attempted={result.attempted}, failures={result.failures})"
        )
        return 0

    df = result.df
    source = result.provider_name

    rows: list[dict] = []
    for idx, row in df.iterrows():
        close = _safe_float(row.get("close"))
        if close is None:
            continue
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        rows.append({
            "symbol": sym,
            "timeframe": interval,
            "date": date_str,
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": close,
            "volume": _safe_int(row.get("volume")),
            "source": source,
        })

    if not rows:
        print(f"[history_store] Warning: no usable rows to store for {sym}")
        return 0

    written = insert_stock_prices(rows)
    print(f"[history_store] Stored {written} row(s) for {sym} (timeframe={interval}, source={source})")
    return written


def get_latest_prices(symbol: str, timeframe: str = DEFAULT_INTERVAL, n: int = 250) -> list[dict]:
    """Return the most recent `n` stored bars for `symbol`/`timeframe`,
    ascending by date. Read-only; does not fetch anything."""
    return get_stock_prices(symbol, timeframe=timeframe, limit=n)
