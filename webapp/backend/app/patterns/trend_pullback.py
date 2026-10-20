"""Pattern 1: trend structure + pullback to a rising (long) / falling
(short) SMA150 or SMA200."""
import pandas as pd

from . import config
from .indicators import atr as atr_series
from .indicators import sma, sma_slope
from .types import Detection, Pivot
from .zigzag import find_pivots


def _last_two_confirmed(pivots: list[Pivot], kind: str, as_of_date: str) -> list[Pivot]:
    known = [p for p in pivots if p.kind == kind and p.confirmed_on <= as_of_date]
    return known[-2:]


def detect_trend_pullback(df: pd.DataFrame, symbol: str, sma_period: int) -> list[Detection]:
    pivots = find_pivots(df)
    sma_s = sma(df["close"], sma_period)
    slope = sma_slope(df["close"], sma_period, config.SMA_SLOPE_LOOKBACK_BARS)
    atr_s = atr_series(df)
    dates = df.index.to_list()
    n = len(df)

    k = config.PULLBACK_LOOKBACK_BARS
    start = max(sma_period, config.ATR_PERIOD, k) + config.SMA_SLOPE_LOOKBACK_BARS

    detections: list[Detection] = []
    for i in range(start, n):
        if pd.isna(sma_s.iloc[i]) or pd.isna(slope.iloc[i]) or pd.isna(atr_s.iloc[i]):
            continue
        date_i = dates[i]
        sma_i = sma_s.iloc[i]
        slope_i = slope.iloc[i]
        atr_i = atr_s.iloc[i]
        window = df.iloc[i - k + 1 : i + 1]
        close_i = df["close"].iloc[i]

        highs = _last_two_confirmed(pivots, "high", date_i)
        lows = _last_two_confirmed(pivots, "low", date_i)
        has_structure = len(highs) == 2 and len(lows) == 2
        higher_high = has_structure and highs[1].price > highs[0].price
        higher_low = has_structure and lows[1].price > lows[0].price
        lower_high = has_structure and highs[1].price < highs[0].price
        lower_low = has_structure and lows[1].price < lows[0].price

        # Long: uptrend structure, rising SMA, pullback from above.
        if higher_high and higher_low and slope_i > 0:
            touch_idx = window["low"].idxmin()
            touch_low = window.loc[touch_idx, "low"]
            distance = touch_low - sma_i
            if distance <= config.PULLBACK_ATR_MULT * atr_i and close_i > sma_i and close_i > touch_low:
                detections.append(
                    Detection(
                        pattern="trend_pullback",
                        direction="long",
                        symbol=symbol,
                        start_date=touch_idx,
                        end_date=date_i,
                        confirmed_on=date_i,
                        levels={"sma_level": float(sma_i), "pullback_low": float(touch_low)},
                        rules={
                            "sma_period": sma_period,
                            "higher_high": True,
                            "higher_low": True,
                            "sma_slope_per_bar": float(slope_i),
                            "distance_to_sma_pct": float(distance / sma_i * 100),
                        },
                    )
                )

        # Short: downtrend structure, falling SMA, pullback from below.
        if lower_high and lower_low and slope_i < 0:
            touch_idx = window["high"].idxmax()
            touch_high = window.loc[touch_idx, "high"]
            distance = sma_i - touch_high
            if distance <= config.PULLBACK_ATR_MULT * atr_i and close_i < sma_i and close_i < touch_high:
                detections.append(
                    Detection(
                        pattern="trend_pullback",
                        direction="short",
                        symbol=symbol,
                        start_date=touch_idx,
                        end_date=date_i,
                        confirmed_on=date_i,
                        levels={"sma_level": float(sma_i), "pullback_high": float(touch_high)},
                        rules={
                            "sma_period": sma_period,
                            "lower_high": True,
                            "lower_low": True,
                            "sma_slope_per_bar": float(slope_i),
                            "distance_to_sma_pct": float(distance / sma_i * 100),
                        },
                    )
                )

    return detections
