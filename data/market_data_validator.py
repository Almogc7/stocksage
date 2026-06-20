"""
Live market-data retrieval and validation for watchlist eligibility (Phase 3
of the dynamic watchlist lifecycle — see WATCHLIST_AND_ALERTS_DESIGN.md).

What this module DOES:
  - Fetches daily OHLCV history from yfinance (single symbol or batched).
  - Validates and normalizes it: completeness, freshness, sufficient
    history, usable volume.
  - Computes average daily share volume and average daily dollar volume
    over a configurable lookback window.
  - Classifies any failure into an explicit ProviderStatus instead of a
    generic error, and distinguishes transient provider failures from
    permanently invalid/unsupported tickers from data-quality problems.
  - Caches successfully-fetched history for the lifetime of one
    MarketDataClient instance (one evaluation run) and counts cache
    hits/misses/yfinance request counts.
  - Retries transient provider failures a bounded number of times with
    short exponential backoff. Never retries INVALID_SYMBOL.

What this module does NOT do (later phases):
  - Decide promotion/demotion (analyzers/eligibility.py owns the relevance
    score and hysteresis state machine).
  - Write to the watchlist or evaluation_runs tables.
  - Schedule anything or send Telegram messages.

Completed-candle rule (documented limitation):
  Mirrors the existing convention already used by agent/core.py's Gate 9 and
  tested in tests/test_incomplete_candle.py: when the US market is open, the
  last row of a daily-interval yfinance download is still forming and is
  dropped; when the market is closed, the last row is treated as the most
  recently completed session. This module does not consult a market-holiday
  calendar (no new dependency was added for this). If yfinance has not yet
  published a session's candle, or a holiday falls on what would otherwise
  be a trading day, freshness/staleness is judged with simple Mon-Fri
  weekday stepping only — see `_most_recent_expected_trading_day`.

Freshness rule:
  The gap (in calendar days) between the most recent *expected* trading day
  and the most recent *completed* candle must not exceed
  config.ELIGIBILITY_STALE_DAYS, or the symbol is flagged STALE_DATA. Stale
  data is a data-quality problem, not a weak relevance signal — callers
  must not feed a stale result into the relevance score as if it were a
  legitimately weak stock.

Dollar volume:
  average_daily_dollar_volume is the mean of (close * volume) computed
  per completed trading day over the lookback window, not
  avg_volume * latest_close — this is more robust against price drift
  across the window.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from analyzers.eligibility import classify_security_type
from config import (
    ELIGIBILITY_LOOKBACK_DAYS,
    ELIGIBILITY_STALE_DAYS,
    MARKET_DATA_BATCH_SIZE,
    MARKET_DATA_DATA_QUALITY_RETRY_HOURS,
    MARKET_DATA_HISTORY_PERIOD,
    MARKET_DATA_INVALID_SYMBOL_RETRY_HOURS,
    MARKET_DATA_MAX_RETRIES,
    MARKET_DATA_MIN_HISTORY_DAYS,
    MARKET_DATA_PROVIDER_ERROR_RETRY_HOURS,
    MARKET_DATA_RETRY_BACKOFF_SECONDS,
)
from data.fetcher import is_market_open

ET = ZoneInfo("America/New_York")
PROVIDER_NAME = "yfinance"


class ProviderStatus(str, Enum):
    OK = "OK"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    UNSUPPORTED_SECURITY_TYPE = "UNSUPPORTED_SECURITY_TYPE"
    EMPTY_HISTORY = "EMPTY_HISTORY"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MISSING_OHLCV = "MISSING_OHLCV"
    STALE_DATA = "STALE_DATA"
    INCOMPLETE_DAILY_CANDLE = "INCOMPLETE_DAILY_CANDLE"
    ZERO_VOLUME = "ZERO_VOLUME"
    MISSING_VOLUME = "MISSING_VOLUME"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


# Statuses that mean "we never even got a usable OHLCV shape back" — the
# symbol itself could not be confirmed to exist/resolve.
_INVALID_STATUSES = frozenset({
    ProviderStatus.INVALID_SYMBOL,
    ProviderStatus.UNSUPPORTED_SECURITY_TYPE,
    ProviderStatus.EMPTY_HISTORY,
    ProviderStatus.PROVIDER_ERROR,
    ProviderStatus.RATE_LIMITED,
    ProviderStatus.TEMPORARY_FAILURE,
    ProviderStatus.UNKNOWN_ERROR,
})

# failure_type groupings, used to decide retry behavior in later phases.
_PROVIDER_TRANSIENT = frozenset({
    ProviderStatus.PROVIDER_ERROR, ProviderStatus.RATE_LIMITED, ProviderStatus.TEMPORARY_FAILURE,
})
_UNSUPPORTED = frozenset({
    ProviderStatus.INVALID_SYMBOL, ProviderStatus.UNSUPPORTED_SECURITY_TYPE,
})
_DATA_QUALITY = frozenset({
    ProviderStatus.EMPTY_HISTORY, ProviderStatus.INSUFFICIENT_HISTORY,
    ProviderStatus.MISSING_OHLCV, ProviderStatus.STALE_DATA,
    ProviderStatus.INCOMPLETE_DAILY_CANDLE, ProviderStatus.ZERO_VOLUME,
    ProviderStatus.MISSING_VOLUME,
})


def _failure_type(status: ProviderStatus) -> str | None:
    """
    One of: provider_transient, unsupported, data_quality, unknown, or None
    (status is OK). Distinguishing these is the whole point of this module —
    a temporary yfinance outage must never be classified the same way as a
    permanently invalid ticker or a legitimately stale candle.

    NOTE: "legitimate strategy ineligibility" (e.g. low relevance score) is
    intentionally NOT a category here — that judgment belongs to
    analyzers/eligibility.py, not this data-quality layer.
    """
    if status == ProviderStatus.OK:
        return None
    if status in _PROVIDER_TRANSIENT:
        return "provider_transient"
    if status in _UNSUPPORTED:
        return "unsupported"
    if status in _DATA_QUALITY:
        return "data_quality"
    return "unknown"


def _retry_after(failure_type: str | None, now: datetime) -> str | None:
    if failure_type is None:
        return None
    hours = {
        "provider_transient": MARKET_DATA_PROVIDER_ERROR_RETRY_HOURS,
        "unsupported": MARKET_DATA_INVALID_SYMBOL_RETRY_HOURS,
        "data_quality": MARKET_DATA_DATA_QUALITY_RETRY_HOURS,
        "unknown": MARKET_DATA_PROVIDER_ERROR_RETRY_HOURS,
    }[failure_type]
    return (now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class MarketDataResult:
    symbol: str
    normalized_symbol: str
    security_type: str = "stock"
    provider: str = PROVIDER_NAME
    provider_status: ProviderStatus = ProviderStatus.UNKNOWN_ERROR
    is_valid: bool = False
    is_supported: bool = True
    is_stale: bool = False
    is_complete_daily_candle: bool = False
    has_sufficient_history: bool = False
    has_required_ohlcv: bool = False
    latest_close: float | None = None
    latest_volume: int | None = None
    average_daily_volume: float | None = None
    average_daily_dollar_volume: float | None = None
    history_days_available: int = 0
    data_timestamp_utc: str | None = None
    latest_completed_candle_date: str | None = None
    failure_type: str | None = None
    failure_reason: str | None = None
    retry_after_utc: str | None = None
    warnings: list[str] = field(default_factory=list)
    raw_metadata: dict = field(default_factory=dict)

    @property
    def should_evaluate_now(self) -> bool:
        """True only when the symbol's data is fully usable right now."""
        return self.provider_status == ProviderStatus.OK

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["provider_status"] = self.provider_status.value
        return d


def _most_recent_expected_trading_day(reference: date) -> date:
    """Step back from `reference` to the most recent Mon-Fri date.
    Does not know about market holidays — see module docstring."""
    d = reference
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _split_completed_candles(df: pd.DataFrame, market_open: bool) -> pd.DataFrame:
    """Drop the in-progress session's row when the market is currently open.
    See module docstring for the documented holiday-calendar limitation."""
    if df is None or len(df) == 0:
        return df
    if market_open:
        if len(df) >= 2:
            return df.iloc[:-1]
        return df.iloc[0:0]  # single row and it's still forming — nothing confirmed yet
    return df


def _row_date(df: pd.DataFrame, pos: int) -> date:
    idx = df.index[pos]
    if hasattr(idx, "date"):
        return idx.date()
    return pd.Timestamp(idx).date()


def summarize_history(
    symbol: str,
    df: pd.DataFrame | None,
    *,
    security_type: str | None = None,
    market_open: bool | None = None,
    now: datetime | None = None,
    min_history_days: int = MARKET_DATA_MIN_HISTORY_DAYS,
    lookback_days: int = ELIGIBILITY_LOOKBACK_DAYS,
    stale_days: int = ELIGIBILITY_STALE_DAYS,
) -> MarketDataResult:
    """
    Pure validation function — no network calls. Given an already-fetched
    daily OHLCV DataFrame (or None on fetch failure), classify it and
    compute liquidity figures. This is what MarketDataClient calls after
    fetching, and what tests call directly with hand-built DataFrames.
    """
    now = now or datetime.now(timezone.utc)
    sym = symbol.upper()
    sec_type = security_type or classify_security_type(sym)
    if market_open is None:
        market_open = is_market_open()

    result = MarketDataResult(
        symbol=sym,
        normalized_symbol=sym,
        security_type=sec_type,
        data_timestamp_utc=now.strftime("%Y-%m-%d %H:%M:%S"),
    )

    if df is None or len(df) == 0:
        result.provider_status = ProviderStatus.EMPTY_HISTORY
        result.failure_reason = "yfinance returned no rows"
        result.failure_type = _failure_type(result.provider_status)
        result.retry_after_utc = _retry_after(result.failure_type, now)
        return result

    completed = _split_completed_candles(df, market_open)
    result.history_days_available = len(completed)

    if len(completed) == 0:
        result.provider_status = ProviderStatus.INCOMPLETE_DAILY_CANDLE
        result.failure_reason = "no completed daily candle available yet (market open, single-row history)"
        result.failure_type = _failure_type(result.provider_status)
        result.retry_after_utc = _retry_after(result.failure_type, now)
        return result

    result.is_complete_daily_candle = True
    result.latest_completed_candle_date = _row_date(completed, -1).isoformat()

    cols = {c.lower() for c in completed.columns}
    has_close = "close" in cols
    has_volume = "volume" in cols
    has_ohlcv = {"open", "high", "low", "close", "volume"}.issubset(cols)
    result.has_required_ohlcv = has_ohlcv

    closes = completed["close"] if has_close else None
    valid_closes = closes.dropna() if closes is not None else None

    if valid_closes is None or valid_closes.empty:
        result.provider_status = ProviderStatus.MISSING_OHLCV
        result.failure_reason = "no usable 'close' values in completed history"
        result.failure_type = _failure_type(result.provider_status)
        result.retry_after_utc = _retry_after(result.failure_type, now)
        return result

    result.latest_close = round(float(valid_closes.iloc[-1]), 4)
    if pd.isna(closes.iloc[-1]):
        result.warnings.append("latest close was NaN; used last valid close instead")

    volumes = completed["volume"] if has_volume else None
    valid_volumes = None
    if volumes is not None:
        numeric_volumes = pd.to_numeric(volumes, errors="coerce")
        numeric_volumes = numeric_volumes.replace([float("inf"), float("-inf")], pd.NA)
        valid_volumes = numeric_volumes[(numeric_volumes.notna()) & (numeric_volumes >= 0)]

    if volumes is None or valid_volumes is None or valid_volumes.empty:
        result.provider_status = ProviderStatus.MISSING_VOLUME
        result.failure_reason = "no usable 'volume' values in completed history"
        result.failure_type = _failure_type(result.provider_status)
        result.retry_after_utc = _retry_after(result.failure_type, now)
        return result

    latest_vol_raw = pd.to_numeric(pd.Series([volumes.iloc[-1]]), errors="coerce").iloc[0]
    result.latest_volume = int(latest_vol_raw) if pd.notna(latest_vol_raw) and latest_vol_raw >= 0 else None
    if result.latest_volume is None:
        result.warnings.append("latest volume was missing/invalid")

    lookback_volumes = valid_volumes.tail(lookback_days)
    avg_volume = float(lookback_volumes.mean())
    result.average_daily_volume = round(avg_volume, 2)

    aligned_closes = pd.to_numeric(completed["close"], errors="coerce").tail(lookback_days)
    dollar_vol_series = (aligned_closes * numeric_volumes.tail(lookback_days)).dropna()
    result.average_daily_dollar_volume = (
        round(float(dollar_vol_series.mean()), 2) if not dollar_vol_series.empty else None
    )

    result.has_sufficient_history = result.history_days_available >= min_history_days

    expected_reference = now.astimezone(ET).date()
    if market_open:
        expected_reference -= timedelta(days=1)
    expected_trading_day = _most_recent_expected_trading_day(expected_reference)
    latest_completed_date = _row_date(completed, -1)
    gap_days = (expected_trading_day - latest_completed_date).days
    result.is_stale = gap_days > stale_days

    if not result.has_sufficient_history:
        result.provider_status = ProviderStatus.INSUFFICIENT_HISTORY
        result.failure_reason = (
            f"only {result.history_days_available} completed candles available, "
            f"need at least {min_history_days}"
        )
    elif result.is_stale:
        result.provider_status = ProviderStatus.STALE_DATA
        result.failure_reason = f"latest completed candle is {gap_days} day(s) old (max {stale_days})"
    elif avg_volume == 0:
        result.provider_status = ProviderStatus.ZERO_VOLUME
        result.failure_reason = "average daily volume over lookback window is zero"
    else:
        result.provider_status = ProviderStatus.OK

    result.is_valid = result.provider_status not in _INVALID_STATUSES
    result.failure_type = _failure_type(result.provider_status)
    result.retry_after_utc = _retry_after(result.failure_type, now)
    return result


def _classify_exception(exc: Exception) -> ProviderStatus:
    msg = str(exc).lower()
    if any(kw in msg for kw in ("no data found", "not found", "delisted", "possibly delisted")):
        return ProviderStatus.INVALID_SYMBOL
    if any(kw in msg for kw in ("rate limit", "too many requests", "429")):
        return ProviderStatus.RATE_LIMITED
    if any(kw in msg for kw in ("timeout", "timed out", "connection")):
        return ProviderStatus.TEMPORARY_FAILURE
    return ProviderStatus.PROVIDER_ERROR


class MarketDataClient:
    """
    Stateful client for one evaluation run: caches fetched history,
    counts cache hits/misses and yfinance request/error counts, and
    retries transient failures with bounded exponential backoff.

    Do not reuse a single instance across multiple unrelated evaluation
    runs if you want per-run statistics — create a new client per run.
    """

    def __init__(
        self,
        *,
        period: str = MARKET_DATA_HISTORY_PERIOD,
        max_retries: int = MARKET_DATA_MAX_RETRIES,
        backoff_base_seconds: float = MARKET_DATA_RETRY_BACKOFF_SECONDS,
        batch_size: int = MARKET_DATA_BATCH_SIZE,
        sleep_fn=time.sleep,
    ) -> None:
        self.period = period
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.batch_size = batch_size
        self._sleep = sleep_fn

        self._cache: dict[str, pd.DataFrame] = {}
        self.cache_hits = 0
        self.cache_misses = 0
        self.yfinance_request_count = 0
        self.provider_error_count = 0

    # ── fetching ──────────────────────────────────────────────────────────

    def _fetch_single(self, symbol: str) -> tuple[pd.DataFrame | None, ProviderStatus | None]:
        """Fetch one symbol's history with bounded retry/backoff.
        Returns (df, None) on success or (None, ProviderStatus) on failure.
        Never retries INVALID_SYMBOL."""
        attempt = 0
        last_status = ProviderStatus.UNKNOWN_ERROR
        while True:
            self.yfinance_request_count += 1
            try:
                df = yf.download(
                    symbol, period=self.period, interval="1d",
                    progress=False, auto_adjust=True,
                )
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df.columns = [c.lower() for c in df.columns]
                return df, None
            except Exception as exc:
                last_status = _classify_exception(exc)
                self.provider_error_count += 1
                if last_status == ProviderStatus.INVALID_SYMBOL:
                    return None, last_status
                attempt += 1
                if attempt > self.max_retries:
                    return None, last_status
                self._sleep(self.backoff_base_seconds * (2 ** (attempt - 1)))

    def get_history(self, symbol: str) -> tuple[pd.DataFrame | None, ProviderStatus | None]:
        """Single-symbol fetch with caching. Returns (df, error_status)."""
        sym = symbol.upper()
        if sym in self._cache:
            self.cache_hits += 1
            return self._cache[sym], None
        self.cache_misses += 1
        df, status = self._fetch_single(sym)
        if df is not None and not df.empty:
            self._cache[sym] = df
        return df, status

    def get_history_batch(
        self, symbols: list[str]
    ) -> dict[str, tuple[pd.DataFrame | None, ProviderStatus | None]]:
        """
        Batch fetch with chunking and graceful per-symbol degradation: a
        chunk-level failure (or an unparseable symbol within a chunk) falls
        back to a single-symbol fetch for just that symbol, so one bad
        ticker never fails the whole batch. Preserves input order in the
        keys (dict insertion order).
        """
        results: dict[str, tuple[pd.DataFrame | None, ProviderStatus | None]] = {}
        uncached = []
        for raw in symbols:
            sym = raw.upper()
            if sym in self._cache:
                self.cache_hits += 1
                results[sym] = (self._cache[sym], None)
            else:
                uncached.append(sym)

        for i in range(0, len(uncached), self.batch_size):
            chunk = uncached[i : i + self.batch_size]
            self.cache_misses += len(chunk)
            self.yfinance_request_count += 1
            try:
                raw_batch = yf.download(
                    chunk, period=self.period, interval="1d",
                    progress=False, auto_adjust=True, group_by="ticker",
                )
            except Exception:
                self.provider_error_count += 1
                # Whole-chunk failure: degrade to per-symbol fetches.
                for sym in chunk:
                    results[sym] = self.get_history(sym)
                continue

            for sym in chunk:
                try:
                    df = raw_batch if len(chunk) == 1 else raw_batch[sym]
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    df.columns = [c.lower() for c in df.columns]
                    if df.empty:
                        results[sym] = (None, ProviderStatus.EMPTY_HISTORY)
                        continue
                    self._cache[sym] = df
                    results[sym] = (df, None)
                except Exception:
                    # This one symbol within the chunk didn't parse; retry
                    # it individually rather than failing the whole chunk.
                    results[sym] = self.get_history(sym)

        # Re-key in the caller's requested order.
        return {raw.upper(): results[raw.upper()] for raw in symbols}

    # ── validation ────────────────────────────────────────────────────────

    def validate(self, symbol: str, *, security_type: str | None = None) -> MarketDataResult:
        df, status = self.get_history(symbol)
        if status is not None:
            return self._error_result(symbol, status, security_type)
        return summarize_history(symbol, df, security_type=security_type)

    def validate_batch(
        self, symbols: list[str], *, security_types: dict[str, str] | None = None
    ) -> dict[str, MarketDataResult]:
        security_types = security_types or {}
        fetched = self.get_history_batch(symbols)
        out: dict[str, MarketDataResult] = {}
        for raw in symbols:
            sym = raw.upper()
            df, status = fetched[sym]
            if status is not None:
                out[sym] = self._error_result(sym, status, security_types.get(sym))
            else:
                out[sym] = summarize_history(sym, df, security_type=security_types.get(sym))
        return out

    def _error_result(
        self, symbol: str, status: ProviderStatus, security_type: str | None
    ) -> MarketDataResult:
        now = datetime.now(timezone.utc)
        sym = symbol.upper()
        failure_type = _failure_type(status)
        return MarketDataResult(
            symbol=sym,
            normalized_symbol=sym,
            security_type=security_type or classify_security_type(sym),
            provider_status=status,
            is_valid=False,
            data_timestamp_utc=now.strftime("%Y-%m-%d %H:%M:%S"),
            failure_type=failure_type,
            failure_reason=f"provider call failed: {status.value}",
            retry_after_utc=_retry_after(failure_type, now),
        )

    @property
    def stats(self) -> dict:
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "yfinance_request_count": self.yfinance_request_count,
            "provider_error_count": self.provider_error_count,
        }
