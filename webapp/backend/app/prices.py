"""OHLCV from yfinance, cached in the webapp state DB, shaped for
TradingView Lightweight Charts."""
import json
import math
import re
import time

import pandas as pd

from . import state_db

ALLOWED_RANGES = ("1mo", "3mo", "6mo", "1y", "2y", "5y")
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.^=-]{1,15}$")
CACHE_TTL_SECONDS = 900


class NoData(Exception):
    pass


class UpstreamError(Exception):
    pass


def _download(symbol: str, range_: str) -> pd.DataFrame:
    import yfinance as yf

    return yf.Ticker(symbol).history(period=range_, interval="1d", auto_adjust=False)


def _to_payload(symbol: str, range_: str, df: pd.DataFrame) -> dict:
    candles: list[dict] = []
    volume: list[dict] = []
    seen: set[str] = set()
    for ts, row in df.sort_index().iterrows():
        vals = [row["Open"], row["High"], row["Low"], row["Close"]]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
            continue
        day = pd.Timestamp(ts).strftime("%Y-%m-%d")
        if day in seen:
            continue
        seen.add(day)
        o, h, l, c = (round(float(v), 4) for v in vals)
        candles.append({"time": day, "open": o, "high": h, "low": l, "close": c})
        vol = row.get("Volume")
        volume.append(
            {"time": day, "value": 0 if vol is None or pd.isna(vol) else int(vol)}
        )
    return {
        "symbol": symbol,
        "range": range_,
        "interval": "1d",
        "candles": candles,
        "volume": volume,
    }


def get_prices(symbol: str, range_: str) -> dict:
    now = time.time()
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT fetched_at, payload FROM price_cache WHERE symbol = ? AND range = ?",
            (symbol, range_),
        ).fetchone()
    if row and now - row["fetched_at"] < CACHE_TTL_SECONDS:
        return json.loads(row["payload"])

    try:
        df = _download(symbol, range_)
    except Exception as exc:  # yfinance raises assorted types
        if row:
            return {**json.loads(row["payload"]), "stale": True}
        raise UpstreamError(str(exc)) from exc

    payload = _to_payload(symbol, range_, df) if df is not None and not df.empty else None
    if not payload or not payload["candles"]:
        raise NoData(symbol)

    with state_db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO price_cache (symbol, range, fetched_at, payload)"
            " VALUES (?, ?, ?, ?)",
            (symbol, range_, now, json.dumps(payload)),
        )
    return payload
