"""Setup definition (docs/SETUP_SPEC.md §5), stop/target (§7-8), and the
extension/regime vetoes (§9) -- on a frame that has already cleared
evaluate_candidate() (§3-4). Still a pure function: no network, no DB, no
scheduler.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from analyzers.technical import calc_atr
from setups.calendar_utils import advance_us_trading_days
from setups.frame_utils import missing_required_columns, normalize_frame
from setups.params import RR_EPSILON, SetupParams, V1_PARAMS
from setups.reason_codes import (
    DATA_NOT_FINAL,
    EXTENSION_VETO,
    INSUFFICIENT_HISTORY,
    MISSING_OHLCV,
    NO_RESISTANCE,
    NO_SWING_STRUCTURE,
    PRICE_OUTSIDE_RANGE,
    PULLBACK_LOW_UNCONFIRMED,
    PULLBACK_TOO_OLD,
    PULLBACK_TOO_SHALLOW,
    REGIME_VETO,
    RR_BELOW_3,
    STOP_CAP_EXCEEDED,
)
from setups.swings import find_confirmed_swing_highs
from setups.time_utils import truncate_to_date


@dataclass
class SetupDefinition:
    sh_date: date
    sh_price: float
    sl_date: date
    sl_price: float
    trigger: float
    stop: float
    target: float
    risk: float
    rr: float
    atr: float
    regime: str
    validity_end: date
    scan_date: date
    # §8/§13, V3: which branch produced `target` -- "RESISTANCE" (the only
    # value V1/V2 ever produce) or "MEASURED_3R" (trigger + min_rr * risk,
    # only reachable when target_mode=TARGET_MODE_MEASURED_3R_IF_NO_RESISTANCE
    # and no confirmed resistance exists above the trigger). Defaulted so
    # every existing direct construction (e.g. make_setup() test helpers)
    # stays valid unchanged.
    target_source: str = "RESISTANCE"


@dataclass
class Rejection:
    reason_code: str
    regime: str
    scan_date: date
    # §8/§13, V3: optional diagnostics, filled only at the five reason
    # codes computed from these values (STOP_CAP_EXCEEDED, NO_RESISTANCE,
    # RR_BELOW_3, EXTENSION_VETO, REGIME_VETO) -- every earlier rejection
    # stays at the defaults below, unchanged from before this field existed.
    trigger: float | None = None
    stop: float | None = None
    target: float | None = None
    risk: float | None = None
    rr: float | None = None
    atr: float | None = None


def _position_of_date(df: pd.DataFrame, target_date: date) -> int:
    index_dates = [ts.date() if hasattr(ts, "date") else ts for ts in df.index]
    return index_dates.index(target_date)


def _last_bar_date(df: pd.DataFrame) -> date:
    ts = df.index[-1]
    return ts.date() if hasattr(ts, "date") else ts


def _unrounded_sma150(df: pd.DataFrame) -> float:
    """The extension veto's own SMA150, deliberately NOT
    check_sma150()'s display-rounded field (same fix already applied to
    trend_filter.py, for the same reason -- a sub-boundary rounding flip).
    Extracted as its own function, like calc_atr/find_confirmed_swing_highs,
    so boundary tests can patch an exact value directly."""
    return float(df["close"].rolling(window=150).mean().iloc[-1])


def _compute_regime(spy_df: pd.DataFrame) -> str:
    """Reuses the *pattern* from compute_market_context()
    (analyzers/composite.py:123-128: close vs rolling(150)-mean SMA150),
    not that function itself -- it has an optional live-yfinance-fetch
    fallback when spy_df is None, which this pure-function package must
    never trigger. Missing/short/NaN SPY data -> "UNKNOWN", treated as a
    veto by the caller -- same fail-safe direction compute_market_context()
    itself states ("a data outage must not loosen the bar")."""
    if len(spy_df) < 150:
        return "UNKNOWN"
    spy_close = float(spy_df["close"].iloc[-1])
    spy_sma150 = float(spy_df["close"].rolling(window=150).mean().iloc[-1])
    if math.isnan(spy_close) or math.isnan(spy_sma150):
        return "UNKNOWN"
    return "BULL" if spy_close > spy_sma150 else "BEAR"


def define_setup(
    df: pd.DataFrame,
    scan_date: date,
    spy_df: pd.DataFrame,
    params: SetupParams = V1_PARAMS,
) -> SetupDefinition | Rejection:
    if isinstance(scan_date, datetime):
        scan_date = scan_date.date()

    # Step 0: not a DataFrame at all (None, or any other wrong type) --
    # df is fatal (no data to work with at all); spy_df is NOT fatal, it
    # only ever degrades regime to "UNKNOWN" (step 2) -- same posture as
    # every other spy-data problem below, never an immediate reject.
    if not isinstance(df, pd.DataFrame):
        return Rejection(MISSING_OHLCV, "UNKNOWN", scan_date)
    spy_valid = isinstance(spy_df, pd.DataFrame)

    # Step 0b: normalize + validate shape for BOTH frames -- a missing
    # column on either one returns a Rejection (df) or degrades regime
    # to "UNKNOWN" (spy_df), never raises.
    df = normalize_frame(df)
    if missing_required_columns(df):
        return Rejection(MISSING_OHLCV, "UNKNOWN", scan_date)
    if spy_valid:
        spy_df = normalize_frame(spy_df)
        if missing_required_columns(spy_df):
            spy_valid = False

    # Step 1: no-look-ahead truncation (§13a) -- applies to every
    # indicator this spec uses, not limited to swing levels.
    df = truncate_to_date(df, scan_date)
    if spy_valid:
        spy_df = truncate_to_date(spy_df, scan_date)

    # Step 1a: df staleness -- the last bar must actually BE the scan
    # date (exact equality, same convention as §3's data validation),
    # not merely the latest bar <= scan_date that happened to exist.
    # Checked via `.empty` first: truncate_to_date() returns an empty
    # frame when scan_date is earlier than every row df has (e.g. a
    # newly-listed symbol, or a caller passing a scan_date outside the
    # frame's history) -- _last_bar_date() would otherwise raise
    # IndexError on `df.index[-1]`, breaking this function's own
    # "never raises, always a Rejection" contract.
    if df.empty or _last_bar_date(df) != scan_date:
        return Rejection(DATA_NOT_FINAL, "UNKNOWN", scan_date)

    # Step 1b: window-finiteness guard, before any swing/stop logic.
    window = df.tail(params.swing_lookback_bars)
    ohlc = window[["high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(ohlc).all() or (window["high"] < window["low"]).any():
        return Rejection(MISSING_OHLCV, "UNKNOWN", scan_date)

    # Step 2: regime -- computed and attached regardless of which check,
    # if any, ultimately rejects the candidate (§9: "recorded on every
    # candidate... including ones that failed for other reasons first").
    # A missing (None/wrong-type, step 0), stale, or empty SPY frame
    # (last bar isn't actually the scan date, or there are no rows at
    # all after truncation -- same empty-frame case as step 1a, guarded
    # here the same way to avoid _last_bar_date() raising IndexError)
    # does NOT reject outright the way a missing/stale/empty df does
    # (step 1a) -- it just forces regime="UNKNOWN", which the existing
    # regime veto (step 12) already treats as a reject, same fail-safe
    # direction as every other missing/short/NaN SPY case.
    if spy_valid and not spy_df.empty and _last_bar_date(spy_df) == scan_date:
        regime = _compute_regime(spy_df)
    else:
        regime = "UNKNOWN"

    # Step 3: SH = the most recent confirmed swing high by date.
    confirmed_highs = find_confirmed_swing_highs(df, lookback=params.swing_lookback_bars)
    if not confirmed_highs:
        return Rejection(NO_SWING_STRUCTURE, regime, scan_date)
    sh_date, sh_price = confirmed_highs[-1]

    # Step 4: SL = the GLOBAL minimum low over every bar strictly after SH
    # through the last row (latest occurrence on a tie) -- never
    # restricted to a 2-sessions-old-or-older subset. If that global
    # minimum itself sits in the last 2 rows (not yet confirmed-old),
    # reject outright rather than re-anchoring to an older, higher low.
    sh_pos = _position_of_date(df, sh_date)
    candidates = df.iloc[sh_pos + 1:]
    min_low = candidates["low"].min()
    sl_label = candidates.index[candidates["low"] == min_low][-1]
    sl_pos = df.index.get_loc(sl_label)
    sl_low = float(df["low"].iloc[sl_pos])
    sl_date = df.index[sl_pos]
    if hasattr(sl_date, "date"):
        sl_date = sl_date.date()

    last_pos = len(df) - 1
    if sl_pos >= last_pos - (params.sl_min_age_sessions - 1):
        return Rejection(PULLBACK_LOW_UNCONFIRMED, regime, scan_date)

    # Step 5: recency -- a separate check on the now-confirmed SL, not a
    # search-window bound.
    age_sessions = last_pos - sl_pos
    if age_sessions > params.pullback_max_age_sessions:
        return Rejection(PULLBACK_TOO_OLD, regime, scan_date)

    # Step 6: ATR + pullback depth. Guard against every non-usable shape
    # calc_atr()["atr"] could hand back -- None, a non-numeric value, NaN,
    # +-inf, or <= 0 -- all the same "couldn't get usable data" family as
    # every other MISSING_OHLCV guard in this function.
    try:
        atr = float(calc_atr(df)["atr"])
    except (TypeError, ValueError, ZeroDivisionError):
        return Rejection(MISSING_OHLCV, regime, scan_date)
    if not math.isfinite(atr) or atr <= 0:
        return Rejection(MISSING_OHLCV, regime, scan_date)
    if (sh_price - sl_low) < params.pullback_depth_min_atr * atr:
        return Rejection(PULLBACK_TOO_SHALLOW, regime, scan_date)

    # Step 7: price position.
    current_close = float(df["close"].iloc[-1])
    if not (sl_low < current_close < sh_price):
        return Rejection(PRICE_OUTSIDE_RANGE, regime, scan_date)

    # Step 8: trigger/stop + stop cap (never tightened -- reject instead).
    trigger = sh_price
    stop = sl_low - params.stop_buffer_atr * atr
    risk = trigger - stop
    max_stop = min(params.stop_cap_pct_of_entry * trigger, params.stop_cap_atr_mult * atr)
    if risk > max_stop:
        return Rejection(STOP_CAP_EXCEEDED, regime, scan_date,
                          trigger=trigger, stop=stop, risk=risk, atr=atr)

    # Step 9: target -- sourced from find_confirmed_swing_highs()'s own
    # list (not calc_swing_levels()), V1/Option B. V3 (§13, promoted to
    # primary): if no confirmed resistance exists above the trigger, fall
    # back to a measured-3R target instead of rejecting NO_RESISTANCE --
    # identical to V1 whenever a confirmed resistance DOES exist.
    target_candidates = [price for _, price in confirmed_highs if price > trigger]
    if target_candidates:
        target = min(target_candidates)
        target_source = "RESISTANCE"
    elif params.target_mode == "MEASURED_3R_IF_NO_RESISTANCE":
        target = trigger + params.min_rr * risk
        target_source = "MEASURED_3R"
    else:
        return Rejection(NO_RESISTANCE, regime, scan_date,
                          trigger=trigger, stop=stop, risk=risk, atr=atr)

    # Step 10: 3:1 gate -- rr == min_rr exactly passes (strict "<" rejects,
    # with an epsilon tolerance against float drift). A MEASURED_3R target
    # stores rr as exactly params.min_rr -- belt-and-suspenders with the
    # epsilon, not a substitute for it, since RESISTANCE-sourced targets
    # still go through the division-based computation below.
    rr = params.min_rr if target_source == "MEASURED_3R" else (target - trigger) / risk
    if rr < params.min_rr - RR_EPSILON:
        return Rejection(RR_BELOW_3, regime, scan_date,
                          trigger=trigger, stop=stop, target=target, risk=risk, rr=rr, atr=atr)

    # Step 11: extension veto -- unrounded SMA150, not check_sma150()'s
    # display-rounded field (same fix already applied to trend_filter.py).
    sma150_unrounded = _unrounded_sma150(df)
    if math.isnan(sma150_unrounded):
        return Rejection(INSUFFICIENT_HISTORY, regime, scan_date)
    if (current_close - sma150_unrounded) > params.extension_veto_atr * atr:
        return Rejection(EXTENSION_VETO, regime, scan_date,
                          trigger=trigger, stop=stop, target=target, risk=risk, rr=rr, atr=atr)

    # Step 12: regime veto -- checked last; the regime value itself was
    # already attached in step 2 regardless of this ordering.
    if regime != "BULL":
        return Rejection(REGIME_VETO, regime, scan_date,
                          trigger=trigger, stop=stop, target=target, risk=risk, rr=rr, atr=atr)

    # Step 13: accept.
    validity_end = advance_us_trading_days(scan_date, params.validity_window_sessions)
    return SetupDefinition(
        sh_date=sh_date,
        sh_price=sh_price,
        sl_date=sl_date,
        sl_price=sl_low,
        trigger=trigger,
        stop=stop,
        target=target,
        risk=risk,
        rr=rr,
        atr=atr,
        regime=regime,
        validity_end=validity_end,
        scan_date=scan_date,
        target_source=target_source,
    )
