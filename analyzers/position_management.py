"""
Position management — trailing stops and exit signals for LONG-only swing
trades, built on top of the composite scoring engine's stop sizing.

Advisory-only and standalone: nothing here is wired into check_alerts(),
alert sending, or trade execution. Call it to answer "what should my stop be
right now" and "should I consider exiting/trimming right now" for an open
position; scripts/run_position_check.py is the CLI wrapper.

Staged trailing policy (R = multiples of initial risk = entry - initial_stop):
  Stage 0  (R < 1.0)        stop holds at the initial ATR-sized stop
  Stage 1  (1.0 <= R <= 1.5) stop moves to breakeven (entry price)
  Stage 2  (R > 1.5)         Chandelier Exit: highest high since entry minus
                             3.0x ATR (2.5x when exit signals suggest
                             tightening), floored at breakeven
  The stop is monotonically non-decreasing: a new calculation can never
  lower it below where it already is (previous_stop wins).

Exit signals (advisory flags, NOT sell decisions — confirmed 2026-07-12):
  bearish_rsi_divergence  within the last 20 completed bars, the most recent
                          high (last 3 bars) exceeds the prior peak (bars
                          4-20 back) but RSI(14) at the recent high is lower
                          than at the prior peak
  climax_volume           last completed bar volume >= 3x the 20-bar average
                          AND the bar is red or has an upper wick >= 50% of
                          its range, after a >= +15% close-to-trough move
                          over the prior 10 completed bars
  rsi_extreme             RSI(14) >= 80 on the last completed bar

Completed-bars discipline: every indicator input (RSI, ATR, volume,
highest-high) comes from CLOSED bars only, same as analyzers/composite.py —
the in-progress session is sliced off up front via composite._completed_bars.

Stop sizing reuse: the initial stop uses composite._stop_multiplier's
score-keyed 2.0-3.0x ATR bands directly — the two modules can never disagree.
"""
import logging
from datetime import date

import pandas as pd
import ta

from analyzers.composite import _completed_bars, _flatten, _stop_multiplier

logger = logging.getLogger("stocksage.position")

# ── Policy constants (module-level, not config — same convention as the
#    MACD/Bollinger params in technical.py) ───────────────────────────────────

BREAKEVEN_R = 1.0          # stage 1 begins at/above this R-multiple
CHANDELIER_R = 1.5         # stage 2 begins strictly above this R-multiple
PARTIAL_EXIT_R = 2.0       # suggest trimming at/above this R-multiple

CHANDELIER_MULT = 3.0        # default stage-2 multiplier (wider than initial)
CHANDELIER_MULT_TIGHT = 2.5  # used when exit signals advise tightening

_RSI_WINDOW = 14
_ATR_WINDOW = 14
_DIVERGENCE_WINDOW = 20    # completed bars examined for divergence
_DIVERGENCE_RECENT = 3     # "recent high" must sit in the last N bars
_CLIMAX_VOL_RATIO = 3.0
_CLIMAX_VOL_WINDOW = 20    # same volume base window as composite/technical
_CLIMAX_MOVE_PCT = 15.0
_CLIMAX_MOVE_BARS = 10
_CLIMAX_WICK_FRACTION = 0.5
_RSI_EXTREME = 80.0


# ── Stops ─────────────────────────────────────────────────────────────────────

def compute_initial_stop(entry_price: float, atr: float, composite_score: float) -> dict:
    """Initial stop from the composite engine's score-keyed ATR bands.

    Reuses composite._stop_multiplier (2.0x below score 70, ramping to 3.0x
    at 100) — never recomputes the bands.
    """
    multiplier = _stop_multiplier(float(composite_score))
    return {
        "stop_price": round(entry_price - multiplier * atr, 4),
        "multiplier": multiplier,
        "atr": atr,
    }


def compute_r_multiple(entry_price: float, initial_stop: float, current_price: float) -> float:
    """R-multiple of the open position: (price - entry) / initial risk."""
    risk = entry_price - initial_stop
    if risk <= 0:
        raise ValueError(
            f"initial_stop ({initial_stop}) must be below entry_price "
            f"({entry_price}) for a long position"
        )
    return (current_price - entry_price) / risk


def compute_trailing_stop(
    entry_price: float,
    initial_stop: float,
    highest_high_since_entry: float,
    current_atr: float,
    current_r_multiple: float,
    previous_stop: float | None = None,
    tighten: bool = False,
) -> dict:
    """Staged trailing stop for an open long position.

    previous_stop: the stop as it currently stands (from the last
    evaluation); the result can never be below it (monotonic). Defaults to
    initial_stop on the first call.
    tighten: use the tighter chandelier multiplier (set when
    check_exit_signals() fired — advisory flags translate to less room).
    """
    prev = initial_stop if previous_stop is None else previous_stop
    chandelier_mult: float | None = None

    if current_r_multiple < BREAKEVEN_R:
        stage, basis, candidate = 0, "initial", initial_stop
    elif current_r_multiple <= CHANDELIER_R:
        stage, basis, candidate = 1, "breakeven", entry_price
    else:
        stage = 2
        chandelier_mult = CHANDELIER_MULT_TIGHT if tighten else CHANDELIER_MULT
        chandelier = highest_high_since_entry - chandelier_mult * current_atr
        # Stages are cumulative: even if R gapped straight past 1.5, the
        # stage-2 stop is never below breakeven.
        if chandelier > entry_price:
            basis, candidate = "chandelier", chandelier
        else:
            basis, candidate = "breakeven", entry_price

    if candidate < prev:
        stop_price, basis, raised = prev, "previous", False
    else:
        stop_price, raised = candidate, candidate > prev

    return {
        "stop_price": round(stop_price, 4),
        "stage": stage,
        "basis": basis,  # initial | breakeven | chandelier | previous (monotonic hold)
        "chandelier_multiplier": chandelier_mult,
        "raised": raised,
    }


# ── Exit signals (advisory flags) ─────────────────────────────────────────────

def _bearish_rsi_divergence(bars: pd.DataFrame, rsi: pd.Series) -> tuple[bool, dict]:
    if len(bars) < _DIVERGENCE_WINDOW:
        return False, {"reason": "insufficient bars"}
    highs = bars["high"].iloc[-_DIVERGENCE_WINDOW:]
    recent = highs.iloc[-_DIVERGENCE_RECENT:]
    earlier = highs.iloc[:-_DIVERGENCE_RECENT]
    recent_pos = int(recent.to_numpy().argmax())
    earlier_pos = int(earlier.to_numpy().argmax())
    recent_high, earlier_high = float(recent.iloc[recent_pos]), float(earlier.iloc[earlier_pos])
    if recent_high <= earlier_high:
        return False, {"reason": "no new high"}
    rsi_win = rsi.iloc[-_DIVERGENCE_WINDOW:]
    rsi_recent = rsi_win.iloc[len(earlier) + recent_pos]
    rsi_earlier = rsi_win.iloc[earlier_pos]
    if pd.isna(rsi_recent) or pd.isna(rsi_earlier):
        return False, {"reason": "rsi unavailable"}
    fired = float(rsi_recent) < float(rsi_earlier)
    return fired, {
        "recent_high": round(recent_high, 4), "earlier_high": round(earlier_high, 4),
        "rsi_at_recent_high": round(float(rsi_recent), 2),
        "rsi_at_earlier_high": round(float(rsi_earlier), 2),
    }


def _climax_volume(bars: pd.DataFrame) -> tuple[bool, dict]:
    needed = _CLIMAX_VOL_WINDOW + 1
    if len(bars) < max(needed, _CLIMAX_MOVE_BARS + 1) or "volume" not in bars.columns:
        return False, {"reason": "insufficient bars"}
    last = bars.iloc[-1]
    avg_vol = float(bars["volume"].iloc[-_CLIMAX_VOL_WINDOW - 1:-1].mean())
    if avg_vol <= 0:
        return False, {"reason": "no volume base"}
    rel_vol = float(last["volume"]) / avg_vol
    if rel_vol < _CLIMAX_VOL_RATIO:
        return False, {"rel_vol": round(rel_vol, 2)}

    red = float(last["close"]) < float(last["open"])
    bar_range = float(last["high"]) - float(last["low"])
    upper_wick = float(last["high"]) - max(float(last["open"]), float(last["close"]))
    long_wick = bar_range > 0 and (upper_wick / bar_range) >= _CLIMAX_WICK_FRACTION
    if not (red or long_wick):
        return False, {"rel_vol": round(rel_vol, 2), "reason": "bar not red / no long wick"}

    trough = float(bars["close"].iloc[-_CLIMAX_MOVE_BARS - 1:-1].min())
    move_pct = (float(last["close"]) - trough) / trough * 100.0
    fired = move_pct >= _CLIMAX_MOVE_PCT
    return fired, {
        "rel_vol": round(rel_vol, 2), "red_bar": red, "long_upper_wick": long_wick,
        "move_pct_prior": round(move_pct, 2),
    }


def check_exit_signals(
    df: pd.DataFrame,
    entry_price: float,
    current_r_multiple: float,
    market_open: bool = False,
) -> dict:
    """Evaluate the three advisory exit flags on COMPLETED bars only.

    Returns which signals fired plus per-signal detail — never a sell
    decision. Callers use `any_fired` to tighten the chandelier multiplier
    or consider a partial exit.
    """
    bars = _completed_bars(_flatten(df), market_open)
    closes = bars["close"]
    rsi = ta.momentum.rsi(closes, window=_RSI_WINDOW)

    div_fired, div_detail = _bearish_rsi_divergence(bars, rsi)
    clx_fired, clx_detail = _climax_volume(bars)
    last_rsi = float(rsi.iloc[-1]) if len(rsi.dropna()) else float("nan")
    rsi_fired = last_rsi == last_rsi and last_rsi >= _RSI_EXTREME

    signals = [name for name, fired in (
        ("bearish_rsi_divergence", div_fired),
        ("climax_volume", clx_fired),
        ("rsi_extreme", rsi_fired),
    ) if fired]

    return {
        "signals": signals,
        "any_fired": bool(signals),
        "entry_price": entry_price,
        "r_multiple": round(current_r_multiple, 2),
        "details": {
            "bearish_rsi_divergence": div_detail,
            "climax_volume": clx_detail,
            "rsi_extreme": {"rsi": round(last_rsi, 2) if last_rsi == last_rsi else None,
                            "threshold": _RSI_EXTREME},
        },
    }


def suggest_partial_exit(current_r_multiple: float) -> dict:
    """Advisory partial-exit recommendation at >= PARTIAL_EXIT_R."""
    suggested = current_r_multiple >= PARTIAL_EXIT_R
    return {
        "suggested": suggested,
        "fraction": "33-50%" if suggested else None,
        "reason": (
            f"position at {current_r_multiple:.2f}R >= {PARTIAL_EXIT_R}R -- "
            "consider selling 33-50% and trailing the remainder"
            if suggested else
            f"position at {current_r_multiple:.2f}R, below the {PARTIAL_EXIT_R}R trim threshold"
        ),
    }


# ── Orchestrator (what the CLI calls) ─────────────────────────────────────────

def _atr_series(bars: pd.DataFrame) -> pd.Series:
    return ta.volatility.average_true_range(
        bars["high"], bars["low"], bars["close"], window=_ATR_WINDOW
    )


def evaluate_position(
    symbol: str,
    df: pd.DataFrame,
    entry_price: float,
    entry_date: date,
    initial_stop: float | None = None,
    entry_score: float | None = None,
    previous_stop: float | None = None,
    market_open: bool = False,
) -> dict:
    """Full advisory evaluation of one open long position.

    initial_stop: pass the stop actually in use when known. When None it is
    reconstructed: ATR(14) as of the entry date x the composite multiplier
    for entry_score (2.0x when entry_score is None/below the BUY bands).
    previous_stop: the stop as it currently stands, for monotonicity across
    repeated evaluations (None on the first check = initial_stop).

    Logs one structured line with the full reasoning (requirement: every
    evaluation leaves a forensic trail).
    """
    symbol = symbol.upper()
    bars = _completed_bars(_flatten(df), market_open)
    bar_dates = pd.to_datetime(bars.index).date
    last_close = float(bars["close"].iloc[-1])

    # ── Initial stop (given or reconstructed as of the entry date) ───────────
    reconstructed = initial_stop is None
    if reconstructed:
        asof = bars.loc[bar_dates <= entry_date]
        if len(asof) < _ATR_WINDOW + 1:
            raise ValueError(f"not enough history before {entry_date} to reconstruct ATR")
        entry_atr = float(_atr_series(asof).iloc[-1])
        initial = compute_initial_stop(entry_price, entry_atr, entry_score or 0.0)
        initial_stop = initial["stop_price"]
    else:
        initial = {"stop_price": initial_stop, "multiplier": None, "atr": None}

    # ── Position state from completed bars ───────────────────────────────────
    since_entry = bars.loc[bar_dates >= entry_date]
    highest_high = float(since_entry["high"].max()) if len(since_entry) else float(bars["high"].iloc[-1])
    current_atr = float(_atr_series(bars).iloc[-1])
    r_multiple = compute_r_multiple(entry_price, initial_stop, last_close)

    # ── Signals first: they decide whether the chandelier tightens ───────────
    exit_signals = check_exit_signals(bars, entry_price, r_multiple, market_open=False)
    trail = compute_trailing_stop(
        entry_price, initial_stop, highest_high, current_atr, r_multiple,
        previous_stop=previous_stop, tighten=exit_signals["any_fired"],
    )
    partial = suggest_partial_exit(r_multiple)

    # A recommended stop at/above the last completed close means the trail
    # was already breached — the position would have been stopped out.
    # Surface that as the action instead of quoting a stop above the market.
    stop_breached = last_close <= trail["stop_price"]
    if stop_breached:
        action = ("trailing stop already breached at the last completed close -- "
                  "the position would have been stopped out; exit")
    elif partial["suggested"] and exit_signals["any_fired"]:
        action = "consider partial exit (33-50%); exit signals also firing -- chandelier tightened"
    elif partial["suggested"]:
        action = "consider partial exit (33-50%), trail the remainder"
    elif exit_signals["any_fired"]:
        action = "hold with tightened trail; watch the fired exit signals"
    else:
        action = "hold; no action needed"

    result = {
        "symbol": symbol,
        "entry_price": entry_price,
        "entry_date": entry_date.isoformat(),
        "last_completed_close": round(last_close, 4),
        "initial_stop": round(initial_stop, 4),
        "initial_stop_reconstructed": reconstructed,
        "initial_stop_detail": initial,
        "r_multiple": round(r_multiple, 2),
        "highest_high_since_entry": round(highest_high, 4),
        "current_atr": round(current_atr, 4),
        "trailing_stop": trail,
        "stop_breached": stop_breached,
        "exit_signals": exit_signals,
        "partial_exit": partial,
        "recommended_action": action,
    }

    logger.info(
        "[position] %s entry=%.2f (%s) close=%.2f R=%.2f stage=%d stop=%.2f "
        "(basis=%s%s raised=%s breached=%s) signals=%s partial_exit=%s action: %s",
        symbol, entry_price, entry_date.isoformat(), last_close, r_multiple,
        trail["stage"], trail["stop_price"], trail["basis"],
        f" {trail['chandelier_multiplier']}xATR" if trail["chandelier_multiplier"] else "",
        trail["raised"], stop_breached,
        ",".join(exit_signals["signals"]) or "none",
        partial["suggested"], action,
    )
    return result
