"""One explicit, fixed stop-level rule per (pattern, direction) — never
tuned by the backtest script. See docs: plan "Fixed stop levels"."""
from . import config
from .types import Detection


def _trend_pullback_long(levels: dict, atr_at_confirm: float) -> float:
    return levels["pullback_low"] - config.TREND_PULLBACK_STOP_ATR_MULT * atr_at_confirm


def _trend_pullback_short(levels: dict, atr_at_confirm: float) -> float:
    return levels["pullback_high"] + config.TREND_PULLBACK_STOP_ATR_MULT * atr_at_confirm


def _flag_breakout_long(levels: dict, atr_at_confirm: float) -> float:
    return levels["base_low"]


def _flag_breakout_short(levels: dict, atr_at_confirm: float) -> float:
    return levels["base_high"]


def _double_bottom_long(levels: dict, atr_at_confirm: float) -> float:
    return min(levels["trough_1"], levels["trough_2"])


def _double_top_short(levels: dict, atr_at_confirm: float) -> float:
    return max(levels["peak_1"], levels["peak_2"])


_STOP_FUNCS = {
    ("trend_pullback", "long"): _trend_pullback_long,
    ("trend_pullback", "short"): _trend_pullback_short,
    ("flag_breakout", "long"): _flag_breakout_long,
    ("flag_breakout", "short"): _flag_breakout_short,
    ("double_top_bottom", "long"): _double_bottom_long,
    ("double_top_bottom", "short"): _double_top_short,
}


def stop_level(detection: Detection, atr_at_confirm: float) -> float:
    """Dispatches on (detection.pattern, detection.direction) via the
    fixed table above. KeyError on any unhandled combination, so a future
    pattern can't silently go without an explicit stop rule."""
    func = _STOP_FUNCS[(detection.pattern, detection.direction)]
    return func(detection.levels, atr_at_confirm)
