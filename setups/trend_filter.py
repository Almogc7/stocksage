"""Trend and eligibility filter (docs/SETUP_SPEC.md §4), on an
already-truncated frame -- this module does not truncate; evaluate.py owns
"truncate first, then validate, then filter."
"""
from __future__ import annotations

import math

import pandas as pd

from analyzers.technical import _flatten, _sma200, check_sma150
from config import (
    ELIGIBILITY_MIN_AVG_VOLUME,
    ELIGIBILITY_MIN_DOLLAR_VOL,
    ELIGIBILITY_MIN_PRICE,
)
from setups.params import SLOPE_LOOKBACK_SESSIONS
from setups.reason_codes import LIQUIDITY_VETO, MISSING_OHLCV, TREND_FILTER_FAILED
from setups.results import Result as FilterResult


def evaluate_trend_and_eligibility(
    df: pd.DataFrame, avg_volume: float, avg_dollar_vol: float
) -> FilterResult:
    """avg_volume/avg_dollar_vol are computed by the caller (evaluate.py)
    over the same tail window, from the same already-truncated frame --
    kept as plain arguments so this function stays a narrow, independently
    testable unit, not because it needs a second data source.
    """
    df = _flatten(df)  # same MultiIndex-column defense check_sma150()/_sma200() apply internally
    current_price = float(df["close"].iloc[-1])

    # check_sma150() (analyzers/technical.py) divides by sma150 to get
    # pct_from_sma -- a degenerate frame (e.g. every close exactly 0)
    # makes that a ZeroDivisionError. Not this package's file to fix
    # internally, so the call is guarded here instead.
    try:
        sma = check_sma150(df, current_price)
    except (ZeroDivisionError, ValueError):
        return FilterResult(passed=False, reason_code=MISSING_OHLCV)
    if not sma["above_sma150"]:
        return FilterResult(passed=False, reason_code=TREND_FILTER_FAILED)

    # Computed once and reused below for both the SMA150-vs-SMA200 check
    # and the rising-slope check -- also keeps the SMA150-vs-SMA200
    # comparison on the same unrounded value check_sma150() computes
    # internally, rather than its display-rounded "sma150" field (which
    # could flip a sub-0.00005 boundary case).
    sma150_series = df["close"].rolling(window=150).mean()
    sma150_today = float(sma150_series.iloc[-1])
    # A padded (all-NaN-prefix) frame can have >=150 total rows while the
    # trailing 150-bar window still spans the padding -- rolling().mean()
    # then returns NaN, not None, and a bare "<=" comparison against NaN
    # is always False in Python, which would otherwise fall through as a
    # silent pass. Must reject, never fall through.
    if math.isnan(sma150_today):
        return FilterResult(passed=False, reason_code=TREND_FILTER_FAILED)

    sma200 = _sma200(df)
    if sma200 is None or math.isnan(sma200) or sma150_today <= sma200:
        return FilterResult(passed=False, reason_code=TREND_FILTER_FAILED)

    idx_ago = -1 - SLOPE_LOOKBACK_SESSIONS
    if len(sma150_series) < SLOPE_LOOKBACK_SESSIONS + 1 or pd.isna(sma150_series.iloc[idx_ago]):
        return FilterResult(passed=False, reason_code=TREND_FILTER_FAILED)
    sma150_ago = float(sma150_series.iloc[idx_ago])
    if not (sma150_today > sma150_ago):
        return FilterResult(passed=False, reason_code=TREND_FILTER_FAILED)

    # Liquidity -- hard gates (§4: "reuse as-is... not a taper"), not
    # analyzers/eligibility.py's compute_liquidity_score() (a 0-1 taper
    # score, the wrong shape here).
    if avg_volume < ELIGIBILITY_MIN_AVG_VOLUME:
        return FilterResult(passed=False, reason_code=LIQUIDITY_VETO)

    # Price floor against the current (point) price, matching
    # analyzers/eligibility.py:219-220's own precedent (`price <
    # ELIGIBILITY_MIN_PRICE`) -- that module never averages this constant
    # against a trailing window, so neither does this one; only the
    # dollar-volume gate below is defined as an average (per §4/§12).
    if current_price < ELIGIBILITY_MIN_PRICE:
        return FilterResult(passed=False, reason_code=LIQUIDITY_VETO)

    if avg_dollar_vol < ELIGIBILITY_MIN_DOLLAR_VOL:
        return FilterResult(passed=False, reason_code=LIQUIDITY_VETO)

    return FilterResult(passed=True)
