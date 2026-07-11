"""
Shared test fixtures: synthetic OHLCV DataFrames that require no network access.
All dates end on a past Monday so the last bar is always a completed trading day.
"""

import numpy as np
import pandas as pd


def make_trending_df(n: int = 252, seed: int = 42, trend: float = 0.0012) -> pd.DataFrame:
    """
    Generate a synthetic OHLCV DataFrame with a mild upward trend.

    Price starts near 100 and trends up so that after ~150 bars the close
    is above SMA150 — this keeps full_analysis() past the SMA veto gate
    in tests that exercise scoring logic.

    Parameters
    ----------
    n:     number of trading days (≥ 200 recommended to support SMA200)
    seed:  random seed for reproducibility
    trend: mean daily log-return (positive = uptrend)
    """
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(trend, 0.015, n)
    close = 100.0 * np.exp(np.cumsum(log_returns))

    # Open is close offset by a small noise term
    open_ = close * np.exp(rng.normal(0.0, 0.005, n))
    high = np.maximum(close, open_) * (1.0 + rng.uniform(0.001, 0.010, n))
    low = np.minimum(close, open_) * (1.0 - rng.uniform(0.001, 0.010, n))
    volume = rng.integers(1_000_000, 6_000_000, n).astype(float)

    # Use business days ending on a Monday in the past so the last bar
    # is unambiguously a completed trading session.
    dates = pd.bdate_range(end="2026-06-15", periods=n)
    return pd.DataFrame(
        {"close": close, "high": high, "low": low, "open": open_, "volume": volume},
        index=pd.DatetimeIndex(dates, name="Date"),
    )


def make_falling_df(n: int = 252, seed: int = 7) -> pd.DataFrame:
    """
    DataFrame with a strong downtrend; RSI will be well below 45 at the end.
    Useful for testing RSI fringe-low and veto-gate scenarios.
    """
    return make_trending_df(n=n, seed=seed, trend=-0.004)


def make_rising_df(n: int = 252, seed: int = 13) -> pd.DataFrame:
    """
    DataFrame with a strong uptrend; RSI will be well above 65 at the end.
    Useful for testing RSI fringe-high and overbought scenarios.
    """
    return make_trending_df(n=n, seed=seed, trend=0.005)
