"""Pattern 3: double top (short) / double bottom (long)."""
import pandas as pd

from . import config
from .indicators import atr as atr_series
from .types import Detection
from .zigzag import find_pivots


def detect_double_top_bottom(df: pd.DataFrame, symbol: str) -> list[Detection]:
    pivots = find_pivots(df)  # strictly alternates high/low by construction
    dates = df.index.to_list()
    idx_of_date = {d: i for i, d in enumerate(dates)}
    atr_s = atr_series(df)
    closes = df["close"]
    n = len(df)
    detections: list[Detection] = []

    for k in range(len(pivots) - 2):
        p1, mid, p2 = pivots[k], pivots[k + 1], pivots[k + 2]

        # ATR at the confirming bar (the second peak/trough's own
        # confirmed_on), never a later bar — keeps the volatility-
        # normalized tolerance/depth thresholds no-look-ahead.
        confirm_idx = idx_of_date[p2.confirmed_on]
        atr_at_confirm = atr_s.iloc[confirm_idx]
        if pd.isna(atr_at_confirm):
            continue

        if p1.kind == "high" and mid.kind == "low" and p2.kind == "high":
            h1, h2 = p1.price, p2.price
            peak_diff_atr = abs(h2 - h1) / atr_at_confirm
            if peak_diff_atr <= config.PEAK_TOLERANCE_ATR:
                neckline = mid.price
                lower_peak = min(h1, h2)
                trough_depth_atr = (lower_peak - neckline) / atr_at_confirm
                if trough_depth_atr >= config.MIN_TROUGH_ATR:
                    confirm_start = idx_of_date[p2.confirmed_on]
                    for j in range(confirm_start, n):
                        close_j = closes.iloc[j]
                        if close_j < neckline:
                            detections.append(
                                Detection(
                                    pattern="double_top_bottom",
                                    direction="short",
                                    symbol=symbol,
                                    start_date=p1.date,
                                    end_date=dates[j],
                                    confirmed_on=dates[j],
                                    levels={"peak_1": h1, "peak_2": h2, "neckline": neckline},
                                    rules={
                                        "peak_diff_atr": peak_diff_atr,
                                        "trough_depth_atr": trough_depth_atr,
                                        "neckline_break_pct": (neckline - close_j) / neckline * 100,
                                    },
                                )
                            )
                            break

        if p1.kind == "low" and mid.kind == "high" and p2.kind == "low":
            l1, l2 = p1.price, p2.price
            trough_diff_atr = abs(l2 - l1) / atr_at_confirm
            if trough_diff_atr <= config.PEAK_TOLERANCE_ATR:
                neckline = mid.price
                higher_trough = max(l1, l2)
                neckline_height_atr = (neckline - higher_trough) / atr_at_confirm
                if neckline_height_atr >= config.MIN_TROUGH_ATR:
                    confirm_start = idx_of_date[p2.confirmed_on]
                    for j in range(confirm_start, n):
                        close_j = closes.iloc[j]
                        if close_j > neckline:
                            detections.append(
                                Detection(
                                    pattern="double_top_bottom",
                                    direction="long",
                                    symbol=symbol,
                                    start_date=p1.date,
                                    end_date=dates[j],
                                    confirmed_on=dates[j],
                                    levels={"trough_1": l1, "trough_2": l2, "neckline": neckline},
                                    rules={
                                        "trough_diff_atr": trough_diff_atr,
                                        "neckline_height_atr": neckline_height_atr,
                                        "neckline_break_pct": (close_j - neckline) / neckline * 100,
                                    },
                                )
                            )
                            break

    return detections
