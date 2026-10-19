"""Orchestrator: runs every pattern 1 detector (one per SMA period) plus
patterns 2 and 3 over a single price payload, returns one flat, date-sorted
list of Detection dicts (JSON-ready)."""
from . import config
from .double_top_bottom import detect_double_top_bottom
from .flag_breakout import detect_flag_breakout
from .indicators import candles_to_frame
from .trend_pullback import detect_trend_pullback
from .types import Detection


def detect_patterns(payload: dict, symbol: str) -> list[dict]:
    """`payload`: the same shape /api/prices returns ({"candles": [...],
    "volume": [...]}). Returns a list of Detection.to_dict(), sorted by
    confirmed_on."""
    df = candles_to_frame(payload)
    if df.empty:
        return []

    detections: list[Detection] = []
    for period in config.SMA_PERIODS:
        detections.extend(detect_trend_pullback(df, symbol, sma_period=period))
    detections.extend(detect_flag_breakout(df, symbol))
    detections.extend(detect_double_top_bottom(df, symbol))

    detections.sort(key=lambda d: d.confirmed_on)
    return [d.to_dict() for d in detections]
