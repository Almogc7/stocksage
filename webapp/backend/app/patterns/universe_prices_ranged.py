"""Batched, chunked yfinance fetch for an explicit [start, end) DATE RANGE
— for scripts/eval_trend_pullback_holdout.py's held-out period. NOT a
modification of app/patterns/universe_prices.py (frozen, period-only) —
a separate, new module mirroring its chunking/failed-symbol-reporting/
capped-fallback shape exactly, swapping `period=` for `start=`/`end=`.

No caching, no state_db write AT ALL (not even incidentally): the
period-based fallback in universe_prices.py uses app.prices.get_prices(),
which is period-only and therefore can't serve a ranged fallback, so this
module's fallback calls yfinance directly per symbol instead. That means
this entire module — batched path and fallback alike — is 100%
read-only/stateless, unlike universe_prices.py's fallback (which
incidentally writes the webapp's own price_cache row).

The small OHLCV-reshaping helper is duplicated from universe_prices.py's
private _shape() rather than imported, to avoid depending on another
module's private API across files — 6 lines, not worth the coupling."""
import pandas as pd

from . import config


def _download(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        symbols, start=start, end=end, interval="1d", progress=False, auto_adjust=False, group_by="ticker"
    )


def _download_single(symbol: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    return yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=False)


def _shape(raw: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "open": raw["Open"],
            "high": raw["High"],
            "low": raw["Low"],
            "close": raw["Close"],
            "volume": raw["Volume"].fillna(0),
        }
    )
    out.index = [pd.Timestamp(ts).strftime("%Y-%m-%d") for ts in out.index]
    return out.dropna(subset=["open", "high", "low", "close"])


def fetch_universe_ohlcv_ranged(
    symbols: list[str], start: str, end: str, chunk_size: int = config.UNIVERSE_FETCH_CHUNK_SIZE
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """One yf.download(start=, end=) call per chunk of `chunk_size`
    symbols. Returns (frames, failed_symbols) — failed_symbols is every
    symbol whose chunk raised or whose column came back empty."""
    frames: dict[str, pd.DataFrame] = {}
    failed: list[str] = []

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        try:
            raw = _download(chunk, start, end)
        except Exception:
            failed.extend(chunk)
            continue

        multi = isinstance(raw.columns, pd.MultiIndex)
        for symbol in chunk:
            try:
                df = raw[symbol] if multi else raw
                if df is None or df.empty:
                    failed.append(symbol)
                    continue
                shaped = _shape(df)
                if shaped.empty:
                    failed.append(symbol)
                    continue
                frames[symbol] = shaped
            except Exception:
                failed.append(symbol)

    return frames, failed


def fetch_with_fallback_ranged(
    symbols: list[str],
    start: str,
    end: str,
    chunk_size: int = config.UNIVERSE_FETCH_CHUNK_SIZE,
    fallback_cap: int = config.UNIVERSE_FETCH_FALLBACK_CAP,
) -> tuple[dict[str, pd.DataFrame], list[str], int]:
    """fetch_universe_ohlcv_ranged() plus a CAPPED per-symbol retry (direct
    yfinance, uncached — see module docstring) for symbols the batched
    fetch failed on. Returns (frames, still_failed, skipped_count) where
    skipped_count is how many failed symbols were never retried because
    the cap was reached."""
    frames, failed = fetch_universe_ohlcv_ranged(symbols, start, end, chunk_size)
    retry = failed[:fallback_cap]
    skipped = failed[fallback_cap:]

    still_failed: list[str] = list(skipped)
    for symbol in retry:
        try:
            raw = _download_single(symbol, start, end)
            if raw is None or raw.empty:
                still_failed.append(symbol)
                continue
            shaped = _shape(raw)
            if shaped.empty:
                still_failed.append(symbol)
            else:
                frames[symbol] = shaped
        except Exception:
            still_failed.append(symbol)

    return frames, still_failed, len(skipped)
