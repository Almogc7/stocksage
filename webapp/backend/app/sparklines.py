"""Close-only series for many symbols in one batched call, for grid
sparklines. Cached in the webapp state DB; never touches the bot DB.

Every batched download fetches a full 1y of history (regardless of the
requested range) so there is enough lead-in to classify each symbol's last
close against its SMA150/SMA200 server-side; the returned `closes` series is
then trimmed back down to the requested range."""
import json
import math
import time

import pandas as pd

from . import state_db

ALLOWED_RANGES = ("1mo", "3mo", "6mo", "1y")
SPARKLINE_MAX_SYMBOLS = 60
CACHE_TTL_SECONDS = 900
NEGATIVE_CACHE_TTL_SECONDS = 300
DOWNLOAD_RANGE = "1y"
RANGE_DAYS = {"1mo": 30, "3mo": 90, "6mo": 182, "1y": 365}


def _download(symbols: list[str], range_: str) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        symbols,
        period=range_,
        interval="1d",
        progress=False,
        auto_adjust=False,
        group_by="ticker",
    )


def _sma_position(closes: list[float], period: int) -> str | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    avg = sum(window) / period
    return "above" if closes[-1] >= avg else "below"


def _closes_payload(symbol: str, range_: str, df: pd.DataFrame) -> dict | None:
    all_closes: list[dict] = []
    seen: set[str] = set()
    for ts, row in df.sort_index().iterrows():
        close = row.get("Close")
        if close is None or (isinstance(close, float) and math.isnan(close)):
            continue
        day = pd.Timestamp(ts).strftime("%Y-%m-%d")
        if day in seen:
            continue
        seen.add(day)
        all_closes.append({"time": day, "close": round(float(close), 4)})
    if not all_closes:
        return None

    sma150_pos = _sma_position([c["close"] for c in all_closes], 150)
    sma200_pos = _sma_position([c["close"] for c in all_closes], 200)

    days = RANGE_DAYS.get(range_)
    if days is None:
        closes = all_closes
    else:
        cutoff = pd.Timestamp(all_closes[-1]["time"]) - pd.Timedelta(days=days)
        closes = [c for c in all_closes if pd.Timestamp(c["time"]) >= cutoff]

    return {
        "symbol": symbol,
        "range": range_,
        "closes": closes,
        "sma150_pos": sma150_pos,
        "sma200_pos": sma200_pos,
    }


def _load_cached(symbols: list[str], range_: str, now: float) -> dict[str, dict | None]:
    cached: dict[str, dict | None] = {}
    with state_db.connect() as conn:
        rows = conn.execute(
            "SELECT symbol, fetched_at, negative, payload FROM sparkline_cache"
            " WHERE range = ? AND symbol IN ({})".format(
                ",".join("?" * len(symbols))
            ),
            (range_, *symbols),
        ).fetchall()
    for r in rows:
        age = now - r["fetched_at"]
        ttl = NEGATIVE_CACHE_TTL_SECONDS if r["negative"] else CACHE_TTL_SECONDS
        if age < ttl:
            cached[r["symbol"]] = _normalize(json.loads(r["payload"])) if r["payload"] else None
    return cached


def _normalize(payload: dict) -> dict:
    """Back-fills sma150_pos/sma200_pos for cache rows written before they
    existed, so old entries don't break the response shape."""
    payload.setdefault("sma150_pos", None)
    payload.setdefault("sma200_pos", None)
    return payload


def _store(symbol: str, range_: str, now: float, payload: dict | None) -> None:
    with state_db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sparkline_cache"
            " (symbol, range, fetched_at, negative, payload) VALUES (?, ?, ?, ?, ?)",
            (symbol, range_, now, 0 if payload else 1, json.dumps(payload) if payload else None),
        )


def get_sparklines(symbols: list[str], range_: str) -> dict[str, dict | None]:
    now = time.time()
    cached = _load_cached(symbols, range_, now)
    missing = [s for s in symbols if s not in cached]

    result: dict[str, dict | None] = dict(cached)
    if not missing:
        return result

    try:
        raw = _download(missing, DOWNLOAD_RANGE)
    except Exception:
        for s in missing:
            with state_db.connect() as conn:
                row = conn.execute(
                    "SELECT payload FROM sparkline_cache WHERE symbol = ? AND range = ?",
                    (s, range_),
                ).fetchone()
            result[s] = _normalize(json.loads(row["payload"])) if row and row["payload"] else None
        return result

    multi = isinstance(raw.columns, pd.MultiIndex)
    for symbol in missing:
        try:
            df = raw[symbol] if multi else raw
            payload = _closes_payload(symbol, range_, df) if df is not None and not df.empty else None
        except Exception:
            payload = None
        _store(symbol, range_, now, payload)
        result[symbol] = payload

    return result
