"""
Composite scoring engine — weighted, regime-aware, additive to the legacy path.

Runs ALONGSIDE the existing gate system: nothing here is wired into
check_alerts(), the DB, or alert sending. Call composite_score() and inspect
the returned breakdown; scripts/run_composite_scan.py prints an old-vs-new
comparison across the ACTIVE tier.

Structure (see docs/DECISIONS.md and the Step 3 spec):
  Hard gate (binary): last completed close > SMA150 AND SMA150 > SMA200.
  Four weighted layers, 25 pts each, total 0-100:
    trend/extension  — RSI band, % extension above SMA150, breakout bonus
                       (components sum to 30; layer CAPPED at 25)
    momentum         — MACD histogram positive & increasing, StochRSI cross
    volume           — session-normalized relative volume, OBV slope
    relative strength— RS vs SPY, threshold set by market regime
  Regime modifier (never a veto): SPY close vs SPY SMA150, computed ONCE per
  scan cycle via compute_market_context(), adjusts the required RS ratio and
  the required total score for a BUY flag (70 bull / 75 bear).
  Stop sizing: ATR(14) multiplier keyed to the total score (2.0-3.0x).

Completed-bars discipline: every indicator here is computed on CLOSED bars
only — the in-progress session is sliced off up front. The one deliberate
exception is the relative-volume component, which measures the LIVE bar's
volume normalized by the fraction of the session elapsed (that normalization
is the fix for the early-session false-negative issue; comparing a partial
day against a full 20-day average understates participation).

Indicator reuse: SMA150/200, RSI-14, MACD, StochRSI, ATR-14, and the 20-day
volume base all use the exact same library calls / parameters as
analyzers/technical.py, so the two engines never disagree on indicator math —
only on how the results are scored.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import ta

from config import (
    COMPOSITE_BREAKOUT_RELVOL,
    COMPOSITE_EXTENSION_MAX_PCT,
    COMPOSITE_RELVOL_FULL,
    COMPOSITE_REQUIRED_SCORE_BEAR,
    COMPOSITE_REQUIRED_SCORE_BULL,
    COMPOSITE_RS_REQUIRED_BEAR,
    COMPOSITE_RS_REQUIRED_BULL,
    COMPOSITE_RS_WINDOW_DAYS,
    RSI_HEALTHY_MAX,
    RSI_HEALTHY_MIN,
    RSI_VETO_MAX,
    RSI_VETO_MIN,
)

_ET = ZoneInfo("America/New_York")

_VOLUME_AVG_WINDOW = 20      # same base window as technical._volume_spike
_OBV_SLOPE_BARS = 10         # lookback for the OBV linear-regression slope
_SESSION_MINUTES = 390.0     # 9:30-16:00 ET
# Floor for the elapsed-session fraction: below this the normalization
# divides by a tiny number and inflates relative volume absurdly.
_SESSION_FRACTION_FLOOR = 0.1

_LAYER_MAX = 25


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def _completed_bars(df: pd.DataFrame, market_open: bool) -> pd.DataFrame:
    """During market hours the last daily row is the in-progress session —
    drop it so every indicator sees closed bars only (same convention as
    check_alerts()'s green-candle gate and MarketDataClient)."""
    if market_open and len(df) >= 2:
        return df.iloc[:-1]
    return df


# ── Market context / regime (compute ONCE per scan cycle) ────────────────────

def compute_market_context(spy_df: pd.DataFrame | None = None,
                           market_open: bool | None = None) -> dict:
    """Fetch SPY once and derive the regime for the whole cycle.

    Pass spy_df/market_open explicitly in tests; left as None they resolve
    via data.fetcher (live yfinance call). If SPY data is unavailable or too
    short for SMA150, thresholds fall back to the STRICTER bear values and
    the RS layer will score 0 with a reason — the regime never vetoes, and a
    data outage must not loosen the bar.
    """
    if market_open is None or spy_df is None:
        from data.fetcher import get_historical, is_market_open
        if market_open is None:
            market_open = is_market_open()
        if spy_df is None:
            try:
                spy_df = get_historical("SPY", period="1y")
            except Exception:
                spy_df = None

    context = {
        "regime": "UNKNOWN",
        "spy_available": False,
        "spy_close": None,
        "spy_sma150": None,
        "required_rs": COMPOSITE_RS_REQUIRED_BEAR,
        "required_score": COMPOSITE_REQUIRED_SCORE_BEAR,
        "spy_closes": None,  # completed-bar close series for the RS layer
    }
    if spy_df is None or len(spy_df) == 0:
        return context

    spy = _completed_bars(_flatten(spy_df), bool(market_open))
    sma150 = float(spy["close"].rolling(window=150).mean().iloc[-1]) if len(spy) >= 150 else float("nan")
    close = float(spy["close"].iloc[-1])
    if sma150 != sma150:  # NaN — not enough history
        return context

    bull = close > sma150
    context.update({
        "regime": "BULL" if bull else "BEAR",
        "spy_available": True,
        "spy_close": round(close, 4),
        "spy_sma150": round(sma150, 4),
        "required_rs": COMPOSITE_RS_REQUIRED_BULL if bull else COMPOSITE_RS_REQUIRED_BEAR,
        "required_score": COMPOSITE_REQUIRED_SCORE_BULL if bull else COMPOSITE_REQUIRED_SCORE_BEAR,
        "spy_closes": spy["close"],
    })
    return context


# ── Session-elapsed fraction (volume normalization) ──────────────────────────

def _session_fraction(now: datetime | None, market_open: bool) -> float:
    """Fraction of the 9:30-16:00 ET session elapsed, floored at
    _SESSION_FRACTION_FLOOR. 1.0 whenever the market is closed (the measured
    bar is then a full session)."""
    if not market_open:
        return 1.0
    now_et = (now or datetime.now(_ET)).astimezone(_ET)
    elapsed = (now_et.hour - 9) * 60 + (now_et.minute - 30)
    fraction = elapsed / _SESSION_MINUTES
    return min(1.0, max(_SESSION_FRACTION_FLOOR, fraction))


# ── Pure component scorers (unit-testable in isolation) ──────────────────────

def _rsi_points(rsi: float) -> float:
    """15 in the healthy band, 7 in the fringe between veto bounds, else 0.
    NOT a veto — out-of-band RSI just earns nothing here."""
    if RSI_HEALTHY_MIN <= rsi <= RSI_HEALTHY_MAX:
        return 15.0
    if RSI_VETO_MIN <= rsi <= RSI_VETO_MAX:
        return 7.0
    return 0.0


def _extension_points(pct_from_sma: float) -> float:
    """Full 10 pts for 0-COMPOSITE_EXTENSION_MAX_PCT% above SMA150, linear
    taper to 0 at twice that (over-extended entries score nothing)."""
    max_pct = COMPOSITE_EXTENSION_MAX_PCT
    if pct_from_sma <= max_pct:
        return 10.0
    if pct_from_sma >= 2 * max_pct:
        return 0.0
    return 10.0 * (2 * max_pct - pct_from_sma) / max_pct


def _breakout_points(closes: pd.Series, sma150_series: pd.Series,
                     volumes: pd.Series) -> float:
    """+5 when the last COMPLETED bar reclaimed SMA150 (previous completed
    close below its SMA150, current completed close above) on >=
    COMPOSITE_BREAKOUT_RELVOL x the 20-day average volume. Closed bars only
    by construction — the caller passes completed-bar series."""
    if len(closes) < _VOLUME_AVG_WINDOW + 2:
        return 0.0
    prev_close, curr_close = float(closes.iloc[-2]), float(closes.iloc[-1])
    prev_sma, curr_sma = float(sma150_series.iloc[-2]), float(sma150_series.iloc[-1])
    if not (prev_close < prev_sma and curr_close > curr_sma):
        return 0.0
    avg_vol = float(volumes.iloc[-_VOLUME_AVG_WINDOW - 1:-1].mean())
    if avg_vol <= 0:
        return 0.0
    rel = float(volumes.iloc[-1]) / avg_vol
    return 5.0 if rel >= COMPOSITE_BREAKOUT_RELVOL else 0.0


def _macd_points(hist: pd.Series) -> float:
    """15 if the histogram is positive and increasing across the last 3
    completed bars; 10 if positive and increasing over the last 2; else 0."""
    if len(hist.dropna()) < 3:
        return 0.0
    h1, h2, h3 = float(hist.iloc[-1]), float(hist.iloc[-2]), float(hist.iloc[-3])
    if h1 <= 0:
        return 0.0
    if h1 > h2 > h3:
        return 15.0
    if h1 > h2:
        return 10.0
    return 0.0


def _stoch_points(k: pd.Series, d: pd.Series) -> float:
    """10 for a %K-over-%D cross on the last completed bar while %K < 0.8,
    excluding crosses launched from extreme oversold (%K prev < 0.2) — those
    are mean-reversion snaps, not trend continuation."""
    if len(k.dropna()) < 2 or len(d.dropna()) < 2:
        return 0.0
    k_now, k_prev = float(k.iloc[-1]), float(k.iloc[-2])
    d_now, d_prev = float(d.iloc[-1]), float(d.iloc[-2])
    crossed = k_prev <= d_prev and k_now > d_now
    if crossed and k_now < 0.8 and k_prev >= 0.2:
        return 10.0
    return 0.0


def _relvol_points(rel_vol: float) -> float:
    """Linear ramp: 0 pts at <= 1.0x, full 15 at >= COMPOSITE_RELVOL_FULL."""
    if rel_vol <= 1.0:
        return 0.0
    if rel_vol >= COMPOSITE_RELVOL_FULL:
        return 15.0
    return 15.0 * (rel_vol - 1.0) / (COMPOSITE_RELVOL_FULL - 1.0)


def _obv_slope(closes: pd.Series, volumes: pd.Series,
               bars: int = _OBV_SLOPE_BARS) -> float | None:
    """Linear-regression slope of OBV over the last `bars` completed bars
    (np.polyfit degree 1). None if there isn't enough data."""
    if len(closes) < bars + 1:
        return None
    obv = ta.volume.on_balance_volume(closes, volumes)
    tail = obv.iloc[-bars:]
    if tail.isna().any():
        return None
    return float(np.polyfit(np.arange(bars, dtype=float), tail.to_numpy(dtype=float), 1)[0])


def _rs_ratio(stock_closes: pd.Series, spy_closes: pd.Series | None,
              window: int = COMPOSITE_RS_WINDOW_DAYS) -> float | None:
    """RS = (1 + stock_return) / (1 + SPY_return) over `window` completed
    days. Stable when SPY is flat/negative (raw pct-change division is not);
    > 1.0 still reads as 'outperforming SPY'. None if either side lacks data."""
    if spy_closes is None or len(stock_closes) < window + 1 or len(spy_closes) < window + 1:
        return None
    r_stock = float(stock_closes.iloc[-1]) / float(stock_closes.iloc[-window - 1]) - 1.0
    r_spy = float(spy_closes.iloc[-1]) / float(spy_closes.iloc[-window - 1]) - 1.0
    if r_spy <= -1.0:  # degenerate (SPY to zero) — do not divide
        return None
    return (1.0 + r_stock) / (1.0 + r_spy)


def _rs_points(rs: float | None, required_rs: float) -> float:
    """Linear ramp from 0 at RS <= 0.8 to full 25 at RS >= required_rs.
    The regime raises/lowers where 'full' sits — it never disqualifies."""
    if rs is None:
        return 0.0
    if rs >= required_rs:
        return 25.0
    if rs <= 0.8:
        return 0.0
    return 25.0 * (rs - 0.8) / (required_rs - 0.8)


def _stop_multiplier(total_score: float) -> float:
    """ATR multiplier keyed to conviction: 80->2.5 rising to 100->3.0;
    70->2.0 rising to 79->2.5; below the BUY bands, 2.0 for reference."""
    if total_score >= 80:
        return round(2.5 + 0.5 * min((total_score - 80) / 20.0, 1.0), 2)
    if total_score >= 70:
        return round(2.0 + 0.5 * (total_score - 70) / 10.0, 2)
    return 2.0


# ── Main entry point ──────────────────────────────────────────────────────────

def composite_score(symbol: str, df: pd.DataFrame, context: dict,
                    market_open: bool = False,
                    now: datetime | None = None,
                    log: bool = True) -> dict:
    """Score one symbol against the four weighted layers.

    df:          daily OHLCV, may include the in-progress bar (pass
                 market_open=True during the session so it is sliced off).
    context:     dict from compute_market_context() — computed once per cycle.
    now:         injectable clock for the session-fraction math (tests).
    log:         print one [composite] trace line per evaluation (default on;
                 callers that render their own view can pass False).

    Returns the full breakdown with every component sub-score.
    """
    df = _flatten(df)
    completed = _completed_bars(df, market_open)
    closes = completed["close"]
    volumes = completed["volume"] if "volume" in completed.columns else pd.Series(dtype=float)
    last_close = float(closes.iloc[-1])

    result: dict = {
        "symbol": symbol.upper(),
        "engine": "composite_v1",
        "bars_completed": len(completed),
        "last_completed_close": round(last_close, 4),
        "regime": {k: context.get(k) for k in
                   ("regime", "spy_available", "spy_close", "spy_sma150",
                    "required_rs", "required_score")},
        "hard_gate": {"passed": False, "price_above_sma150": False,
                      "sma150_above_sma200": False, "sma150": None, "sma200": None,
                      "reason": None},
        "layers": None,
        "total_score": 0.0,
        "flag_buy": False,
        "stop": None,
    }

    # ── Hard gate: both SMA checks on completed bars ─────────────────────────
    sma150_series = closes.rolling(window=150).mean()
    sma150 = float(sma150_series.iloc[-1]) if len(closes) >= 150 else float("nan")
    sma200 = float(closes.rolling(window=200).mean().iloc[-1]) if len(closes) >= 200 else float("nan")

    price_above = sma150 == sma150 and last_close > sma150
    trend_stack = sma150 == sma150 and sma200 == sma200 and sma150 > sma200
    gate = result["hard_gate"]
    gate.update({
        "price_above_sma150": bool(price_above),
        "sma150_above_sma200": bool(trend_stack),
        "sma150": round(sma150, 4) if sma150 == sma150 else None,
        "sma200": round(sma200, 4) if sma200 == sma200 else None,
    })
    if not (price_above and trend_stack):
        missing = []
        if not price_above:
            missing.append("price<=SMA150" if sma150 == sma150 else "SMA150 unavailable")
        if not trend_stack:
            missing.append("SMA150<=SMA200" if sma200 == sma200 else "SMA200 unavailable")
        gate["reason"] = ", ".join(missing)
        if log:
            print(f"[composite] {result['symbol']} gate=FAIL ({gate['reason']}) total=0")
        return result
    gate["passed"] = True

    # ── Trend / extension layer (cap 25) ─────────────────────────────────────
    rsi = float(ta.momentum.rsi(closes, window=14).iloc[-1])
    pct_from_sma = (last_close - sma150) / sma150 * 100.0
    rsi_pts = _rsi_points(rsi)
    ext_pts = _extension_points(pct_from_sma)
    brk_pts = _breakout_points(closes, sma150_series, volumes)
    trend_pts = min(float(_LAYER_MAX), rsi_pts + ext_pts + brk_pts)

    # ── Momentum layer ────────────────────────────────────────────────────────
    hist = ta.trend.MACD(closes).macd_diff()
    macd_pts = _macd_points(hist)
    try:
        stoch = ta.momentum.StochRSIIndicator(closes)
        stoch_pts = _stoch_points(stoch.stochrsi_k(), stoch.stochrsi_d())
    except Exception:
        stoch_pts = 0.0
    momentum_pts = min(float(_LAYER_MAX), macd_pts + stoch_pts)

    # ── Volume layer ──────────────────────────────────────────────────────────
    # Relative volume measures the LIVE bar during the session (normalized by
    # session fraction); after the close it measures the last completed bar.
    fraction = _session_fraction(now, market_open)
    if market_open and len(df) >= 2 and "volume" in df.columns:
        measured_vol = float(df["volume"].iloc[-1])          # in-progress bar
        base = volumes.iloc[-_VOLUME_AVG_WINDOW:]            # 20 completed bars
    else:
        measured_vol = float(volumes.iloc[-1]) if len(volumes) else 0.0
        base = volumes.iloc[-_VOLUME_AVG_WINDOW - 1:-1]      # 20 bars before it
    avg_vol = float(base.mean()) if len(base) >= _VOLUME_AVG_WINDOW else 0.0
    rel_vol = measured_vol / (avg_vol * fraction) if avg_vol > 0 else 0.0
    relvol_pts = _relvol_points(rel_vol)
    obv_slope = _obv_slope(closes, volumes) if len(volumes) else None
    obv_pts = 10.0 if (obv_slope is not None and obv_slope > 0) else 0.0
    volume_pts = min(float(_LAYER_MAX), relvol_pts + obv_pts)

    # ── Relative strength layer ──────────────────────────────────────────────
    rs = _rs_ratio(closes, context.get("spy_closes"))
    required_rs = float(context.get("required_rs", COMPOSITE_RS_REQUIRED_BEAR))
    rs_pts = _rs_points(rs, required_rs)

    # Round the layer points first and total the ROUNDED values, so the
    # reported total always equals the sum of the reported components.
    trend_pts = round(trend_pts, 1)
    momentum_pts = round(momentum_pts, 1)
    volume_pts = round(volume_pts, 1)
    rs_pts = round(rs_pts, 1)
    total = round(trend_pts + momentum_pts + volume_pts + rs_pts, 1)
    required_score = int(context.get("required_score", COMPOSITE_REQUIRED_SCORE_BEAR))
    flag_buy = total >= required_score

    # ── Stop sizing (completed-bar ATR) ──────────────────────────────────────
    atr = round(float(ta.volatility.average_true_range(
        completed["high"], completed["low"], closes, window=14).iloc[-1]), 4)
    multiplier = _stop_multiplier(total)
    # Derived from the ROUNDED atr/close so the reported numbers reconcile.
    stop_price = round(round(last_close, 4) - multiplier * atr, 4)

    result.update({
        "layers": {
            "trend": {
                "points": round(trend_pts, 1), "max": _LAYER_MAX,
                "rsi_pts": rsi_pts, "extension_pts": round(ext_pts, 1),
                "breakout_pts": brk_pts,
                "rsi": round(rsi, 2), "pct_from_sma": round(pct_from_sma, 2),
            },
            "momentum": {
                "points": round(momentum_pts, 1), "max": _LAYER_MAX,
                "macd_pts": macd_pts, "stoch_pts": stoch_pts,
                "macd_hist": round(float(hist.iloc[-1]), 4),
            },
            "volume": {
                "points": round(volume_pts, 1), "max": _LAYER_MAX,
                "relvol_pts": round(relvol_pts, 1), "obv_pts": obv_pts,
                "rel_vol": round(rel_vol, 2), "session_fraction": round(fraction, 3),
                "obv_slope": obv_slope,
            },
            "relative_strength": {
                "points": round(rs_pts, 1), "max": _LAYER_MAX,
                "rs_ratio": round(rs, 4) if rs is not None else None,
                "required_rs": required_rs,
                "window_days": COMPOSITE_RS_WINDOW_DAYS,
            },
        },
        "total_score": total,
        "flag_buy": flag_buy,
        "stop": {"atr": atr, "multiplier": multiplier,
                 "stop_price": stop_price},
    })

    if log:
        print(
            f"[composite] {result['symbol']} gate=PASS "
            f"trend={trend_pts:.0f}/25 mom={momentum_pts:.0f}/25 "
            f"vol={volume_pts:.0f}/25 rs={rs_pts:.0f}/25 "
            f"total={total:.0f} regime={context.get('regime')}(req {required_score}) "
            f"flag_buy={flag_buy} stop={stop_price:,.2f} ({multiplier}x ATR)"
        )
    return result
