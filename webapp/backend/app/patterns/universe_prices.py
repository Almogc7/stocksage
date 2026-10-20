"""Batched, chunked yfinance fetch for the backtest script's universe
(dozens of symbols) — NOT used by the live /api/patterns endpoint, which
stays single-symbol via prices.get_prices(). No caching, no DB write: a
one-off script run, not repeated live traffic, so a persistent cache
would just be unused state.

Mirrors sparklines.py's _download()/group_by="ticker" batching, but keeps
full OHLCV (sparklines only keeps Close)."""
import pandas as pd

from . import config
from .indicators import candles_to_frame
from .. import prices as prices_module


def _download(symbols: list[str], period: str) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        symbols, period=period, interval="1d", progress=False, auto_adjust=False, group_by="ticker"
    )


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


def fetch_universe_ohlcv(
    symbols: list[str], period: str, chunk_size: int = config.UNIVERSE_FETCH_CHUNK_SIZE
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """One yf.download() call per chunk of `chunk_size` symbols. Returns
    (frames, failed_symbols) — failed_symbols is every symbol whose chunk
    raised or whose column came back empty, surfaced explicitly."""
    frames: dict[str, pd.DataFrame] = {}
    failed: list[str] = []

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        try:
            raw = _download(chunk, period)
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


def fetch_with_fallback(
    symbols: list[str],
    period: str,
    chunk_size: int = config.UNIVERSE_FETCH_CHUNK_SIZE,
    fallback_cap: int = config.UNIVERSE_FETCH_FALLBACK_CAP,
) -> tuple[dict[str, pd.DataFrame], list[str], int]:
    """fetch_universe_ohlcv() plus a CAPPED per-symbol retry (via
    prices.get_prices(), the single-symbol cached/DB-writing path) for
    symbols the batched fetch failed on — capped so a bad batch run can't
    flood the webapp's price_cache table with dozens of individual
    writes. Returns (frames, still_failed, skipped_count) where
    skipped_count is how many failed symbols were never retried at all
    because the cap was reached."""
    frames, failed = fetch_universe_ohlcv(symbols, period, chunk_size)
    retry = failed[:fallback_cap]
    skipped = failed[fallback_cap:]

    still_failed: list[str] = list(skipped)
    for symbol in retry:
        try:
            payload = prices_module.get_prices(symbol, period)
            df = candles_to_frame(payload)
            if df.empty:
                still_failed.append(symbol)
            else:
                frames[symbol] = df
        except Exception:
            still_failed.append(symbol)

    return frames, still_failed, len(skipped)
