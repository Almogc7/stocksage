"""Synthetic OHLCV fixtures for app/patterns/ tests. No real market data
anywhere in this module or in anything that imports it, per the round-1
hard build rule."""
import numpy as np
import pandas as pd


def frame_from_closes(
    closes: list[float],
    start_date: str = "2024-01-02",
    wick: float = 0.1,
    volume: int = 1_000_000,
) -> pd.DataFrame:
    """Builds a clean OHLCV DataFrame from a list of closes: each bar's
    open is the previous close (first bar opens at its own close), high/low
    extend `wick` beyond the open/close body. Dates are consecutive
    business days starting at `start_date`."""
    dates = pd.bdate_range(start=start_date, periods=len(closes)).strftime("%Y-%m-%d").tolist()
    rows = []
    prev_close = closes[0]
    for date, close in zip(dates, closes):
        open_ = prev_close
        high = max(open_, close) + wick
        low = min(open_, close) - wick
        rows.append({"date": date, "open": open_, "high": high, "low": low, "close": close, "volume": volume})
        prev_close = close
    df = pd.DataFrame(rows).set_index("date")
    return df


def payload_from_closes(closes: list[float], start_date: str = "2024-01-02", wick: float = 0.1) -> dict:
    """Same shape as /api/prices's payload (candles + volume lists), built
    from the same synthetic path as frame_from_closes()."""
    df = frame_from_closes(closes, start_date=start_date, wick=wick)
    candles = [
        {"time": date, "open": row.open, "high": row.high, "low": row.low, "close": row.close}
        for date, row in df.iterrows()
    ]
    volume = [{"time": date, "value": int(row.volume)} for date, row in df.iterrows()]
    return {"symbol": "TEST", "range": "2y", "interval": "1d", "candles": candles, "volume": volume}


def flat(n: int, level: float = 100.0) -> list[float]:
    return [level] * n


def ramp(n: int, start: float, end: float) -> list[float]:
    if n == 1:
        return [end]
    step = (end - start) / (n - 1)
    return [start + step * i for i in range(n)]


def random_ohlcv(
    seed: int,
    n_bars: int = 400,
    start_date: str = "2018-01-02",
    start_price: float = 100.0,
) -> pd.DataFrame:
    """A seeded geometric random walk with per-seed volatility, drift and
    gap frequency (derived deterministically from `seed`, so "20 seeds"
    already spans a mix of calm/volatile and trending/flat/gappy paths
    without hand-picking 20 separate configs). Used only for the
    randomized no-look-ahead property test — never real market data."""
    rng = np.random.default_rng(seed)
    vol = 0.01 + (seed % 5) * 0.01  # 1%-5% daily stdev
    drift = [-0.0015, 0.0, 0.0015][seed % 3]
    gap_prob = 0.03 if seed % 2 == 0 else 0.10

    dates = pd.bdate_range(start=start_date, periods=n_bars).strftime("%Y-%m-%d").tolist()
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[int] = []

    prev_close = start_price
    for _ in range(n_bars):
        gap = rng.normal(0, 0.03) if rng.random() < gap_prob else 0.0
        gap = max(-0.2, min(0.2, gap))
        o = prev_close * (1 + gap)

        ret = max(-0.2, min(0.2, drift + rng.normal(0, vol)))
        c = o * (1 + ret)

        wiggle = abs(rng.normal(0, vol * 0.5)) * o
        h = max(o, c) + wiggle
        l = max(0.01, min(o, c) - wiggle)

        v = int(rng.integers(500_000, 2_000_000))
        if rng.random() < 0.03:
            v *= 5  # occasional volume spike, so flag_breakout's relvol gate sometimes clears

        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        volumes.append(v)
        prev_close = c

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=pd.Index(dates, name="date"),
    )
    return df
