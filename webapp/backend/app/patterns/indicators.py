"""Pure OHLCV helpers: frame shaping, ATR, SMA. pandas/numpy only — no
scipy, no TA-Lib."""
import numpy as np
import pandas as pd

from . import config


def candles_to_frame(payload: dict) -> pd.DataFrame:
    """Turns an `/api/prices`-shaped payload ({"candles": [...], "volume":
    [...]}) into a DataFrame indexed by date string (YYYY-MM-DD), sorted
    ascending, with open/high/low/close/volume columns (volume 0 where
    missing)."""
    vol_by_date = {v["time"]: v["value"] for v in payload.get("volume", [])}
    rows = [
        {
            "date": c["time"],
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
            "volume": vol_by_date.get(c["time"], 0),
        }
        for c in payload.get("candles", [])
    ]
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return df.set_index("date")


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    a = df["high"] - df["low"]
    b = (df["high"] - prev_close).abs()
    c = (df["low"] - prev_close).abs()
    tr = pd.concat([a, b, c], axis=1).max(axis=1)
    tr.iloc[0] = a.iloc[0]  # no prev close for the first bar
    return tr


def atr(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> pd.Series:
    """Simple rolling mean of True Range (not Wilder's smoothing) —
    deliberately the plainest, most auditable definition, since every
    pattern rule derived from it needs to be explainable in `rules`
    without a second, implicit smoothing parameter."""
    return true_range(df).rolling(period, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def sma_slope(series: pd.Series, period: int, lookback: int) -> pd.Series:
    """SMA(period) now minus SMA(period) `lookback` bars ago, per bar
    (i.e. average slope per bar over that lookback window)."""
    s = sma(series, period)
    return (s - s.shift(lookback)) / lookback


def warmup_bars(period: int = config.SMA_WARMUP_BARS) -> int:
    return period


def is_nan(value) -> bool:
    return value is None or (isinstance(value, float) and np.isnan(value))
