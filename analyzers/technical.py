import pandas as pd
import ta

from config import RSI_HEALTHY_MAX, RSI_HEALTHY_MIN, RSI_VETO_MAX, RSI_VETO_MIN


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


# ── Scoring helpers ───────────────────────────────────────────────────────────
# 150/200 moving averages are SIMPLE (decision D1): the TradingView
# "Swing Trade Analyser" Pine Script — the methodology source of truth —
# uses ta.sma() for the 150/200 lines, and Stack C's cached_indicators.py
# is SMA-based as well.

def _sma200(df: pd.DataFrame) -> float | None:
    if len(df) < 200:
        return None
    return float(df["close"].rolling(window=200).mean().iloc[-1])


def _macd_bullish_last3(df: pd.DataFrame) -> bool:
    """True if a MACD bullish crossover occurred within the last 3 candles."""
    macd_ind = ta.trend.MACD(df["close"])
    macd_line = macd_ind.macd()
    sig_line = macd_ind.macd_signal()
    for i in range(3):
        idx, prev = -(i + 1), -(i + 2)
        try:
            if macd_line.iloc[prev] <= sig_line.iloc[prev] and macd_line.iloc[idx] > sig_line.iloc[idx]:
                return True
        except IndexError:
            break
    return False


def _volume_spike(df: pd.DataFrame, multiplier: float = 1.5, window: int = 20) -> bool:
    """True if the most recent volume exceeds multiplier × 20-day average."""
    if "volume" not in df.columns or len(df) < window + 1:
        return False
    avg_vol = float(df["volume"].iloc[-window - 1:-1].mean())
    curr_vol = float(df["volume"].iloc[-1])
    return avg_vol > 0 and curr_vol > multiplier * avg_vol


def _stoch_rsi_bullish(df: pd.DataFrame) -> bool:
    """%K crossed above %D from below 0.3 (≈30) in the most recent candle."""
    try:
        stoch = ta.momentum.StochRSIIndicator(df["close"])
        k = stoch.stochrsi_k()
        d = stoch.stochrsi_d()
        k_now, k_prev = float(k.iloc[-1]), float(k.iloc[-2])
        d_now, d_prev = float(d.iloc[-1]), float(d.iloc[-2])
        return k_prev <= d_prev and k_now > d_now and k_prev < 0.3
    except Exception:
        return False


def _vwap(df: pd.DataFrame, window: int = 20) -> float | None:
    """Rolling VWAP over the last `window` periods; None if volume is absent."""
    if "volume" not in df.columns:
        return None
    try:
        series = ta.volume.VolumeWeightedAveragePrice(
            df["high"], df["low"], df["close"], df["volume"], window=window
        ).volume_weighted_average_price()
        return float(series.iloc[-1])
    except Exception:
        return None


# ── Indicators ────────────────────────────────────────────────────────────────

def check_sma150(df: pd.DataFrame, current_price: float) -> dict:
    df = _flatten(df)
    sma150 = float(df["close"].rolling(window=150).mean().iloc[-1])
    above = current_price > sma150
    pct_from_sma = (current_price - sma150) / sma150 * 100
    return {
        "sma150": round(sma150, 4),
        "above_sma150": above,
        "pct_from_sma": round(pct_from_sma, 2),
    }


def calc_rsi(df: pd.DataFrame, period: int = 14) -> dict:
    df = _flatten(df)
    rsi = float(ta.momentum.rsi(df["close"], window=period).iloc[-1])
    if rsi < 30:
        signal = "oversold"
    elif rsi > 70:
        signal = "overbought"
    else:
        signal = "neutral"
    return {"rsi": round(rsi, 2), "signal": signal}


def calc_macd(df: pd.DataFrame) -> dict:
    df = _flatten(df)
    close = df["close"]
    macd_ind = ta.trend.MACD(close)
    macd_line = macd_ind.macd()
    signal_line = macd_ind.macd_signal()
    histogram = macd_ind.macd_diff()

    macd_now = float(macd_line.iloc[-1])
    macd_prev = float(macd_line.iloc[-2])
    sig_now = float(signal_line.iloc[-1])
    sig_prev = float(signal_line.iloc[-2])

    if macd_prev <= sig_prev and macd_now > sig_now:
        crossover = "bullish"
    elif macd_prev >= sig_prev and macd_now < sig_now:
        crossover = "bearish"
    else:
        crossover = "none"

    return {
        "macd": round(macd_now, 4),
        "signal_line": round(sig_now, 4),
        "histogram": round(float(histogram.iloc[-1]), 4),
        "crossover": crossover,
    }


def calc_bollinger(df: pd.DataFrame, period: int = 20) -> dict:
    df = _flatten(df)
    close = df["close"]
    bb = ta.volatility.BollingerBands(close, window=period)

    upper = float(bb.bollinger_hband().iloc[-1])
    middle = float(bb.bollinger_mavg().iloc[-1])
    lower = float(bb.bollinger_lband().iloc[-1])
    price = float(close.iloc[-1])
    band_width = upper - lower

    if price > upper:
        position = "above_upper"
    elif price >= upper - band_width * 0.05:
        position = "near_upper"
    elif price <= lower:
        position = "below_lower"
    elif price <= lower + band_width * 0.05:
        position = "near_lower"
    else:
        position = "middle"

    return {
        "upper": round(upper, 4),
        "middle": round(middle, 4),
        "lower": round(lower, 4),
        "current_price": round(price, 4),
        "position": position,
    }


def calc_atr(df: pd.DataFrame, period: int = 14) -> dict:
    df = _flatten(df)
    atr = float(
        ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=period).iloc[-1]
    )
    price = float(df["close"].iloc[-1])
    atr_pct = atr / price * 100

    if atr_pct < 2:
        volatility = "low"
    elif atr_pct < 4:
        volatility = "medium"
    else:
        volatility = "high"

    return {
        "atr": round(atr, 4),
        "atr_pct": round(atr_pct, 2),
        "volatility": volatility,
        "stop_loss_1x": round(price - atr, 4),
        "stop_loss_15x": round(price - atr * 1.5, 4),
        "take_profit_2x": round(price + atr * 2, 4),
    }


def calc_pivot_points(df: pd.DataFrame) -> dict:
    df = _flatten(df)
    candle = df.iloc[-2]
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])

    pp = (high + low + close) / 3
    r1 = 2 * pp - low
    r2 = pp + (high - low)
    r3 = high + 2 * (pp - low)
    s1 = 2 * pp - high
    s2 = pp - (high - low)
    s3 = low - 2 * (high - pp)

    return {
        "pp": round(pp, 4),
        "r1": round(r1, 4),
        "r2": round(r2, 4),
        "r3": round(r3, 4),
        "s1": round(s1, 4),
        "s2": round(s2, 4),
        "s3": round(s3, 4),
    }


def calc_swing_levels(df: pd.DataFrame, lookback: int = 50) -> dict:
    df = _flatten(df)
    window = df.iloc[-lookback:].reset_index(drop=True)
    highs = window["high"].tolist()
    lows = window["low"].tolist()
    n = len(window)

    swing_highs: list[float] = []
    swing_lows: list[float] = []

    for i in range(2, n - 2):
        if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
            swing_highs.append(round(highs[i], 4))
        if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
            swing_lows.append(round(lows[i], 4))

    current = float(df["close"].iloc[-1])

    resistances_above = [h for h in swing_highs if h > current]
    supports_below = [l for l in swing_lows if l < current]

    nearest_resistance = min(resistances_above) if resistances_above else None
    nearest_support = max(supports_below) if supports_below else None

    return {
        "swing_highs": sorted(set(swing_highs)),
        "swing_lows": sorted(set(swing_lows)),
        "nearest_resistance": nearest_resistance,
        "nearest_support": nearest_support,
    }


# ── Full Analysis ─────────────────────────────────────────────────────────────

def full_analysis(symbol: str, df: pd.DataFrame, current_price: float) -> dict:
    df = _flatten(df)

    # Compute all indicators up front so every return path has the full key set.
    sma       = check_sma150(df, current_price)
    rsi_data  = calc_rsi(df)
    macd_data = calc_macd(df)
    bb        = calc_bollinger(df)
    atr       = calc_atr(df)
    pivots    = calc_pivot_points(df)
    swings    = calc_swing_levels(df)

    sma200_val = _sma200(df)
    vwap_val   = _vwap(df)

    rsi        = rsi_data["rsi"]
    stop_loss  = round(current_price - 1.5 * atr["atr"], 4)
    take_profit = round(current_price + 3.0 * atr["atr"], 4)

    def _base_result(score: int, verdict: str, triggered: list[str]) -> dict:
        return {
            "symbol": symbol.upper(),
            "current_price": current_price,
            **sma,
            **rsi_data,
            **macd_data,
            **bb,
            **atr,
            **pivots,
            **swings,
            "sma200":           round(sma200_val, 4) if sma200_val is not None else None,
            "vwap":             round(vwap_val, 4)   if vwap_val   is not None else None,
            "score":            score,
            "verdict":          verdict,
            "triggered_signals": triggered,
            "stop_loss":        stop_loss,
            "take_profit":      take_profit,
            "risk_reward":      "1:2",
            "sell_score":       0,
            # backward-compat aliases for bot/telegram_bot.py
            "buy_score":        score,
            "recommendation":   verdict,
        }

    # ── Veto gates — hard disqualifiers ──────────────────────────────────────
    if not sma["above_sma150"]:
        return _base_result(0, "NEUTRAL", [])
    if rsi < RSI_VETO_MIN:
        return _base_result(0, "NEUTRAL", [])
    if rsi > RSI_VETO_MAX:
        return _base_result(0, "NEUTRAL", [])

    # ── Scoring ───────────────────────────────────────────────────────────────
    score: int = 0
    triggered: list[str] = []

    # +20  price above SMA150 (always true here — kept for score transparency)
    score += 20
    triggered.append("price_above_sma150")

    # +15  SMA150 > SMA200 (long-term uptrend confirmed)
    if sma200_val is not None and sma["sma150"] > sma200_val:
        score += 15
        triggered.append("sma150_above_sma200")

    # +20  MACD bullish crossover within the last 3 candles
    if _macd_bullish_last3(df):
        score += 20
        triggered.append("macd_bullish_crossover")

    # +15  RSI in ideal swing zone (RSI_HEALTHY_MIN–MAX); +5 in the acceptable
    #      fringe between the healthy band and the veto bounds
    if RSI_HEALTHY_MIN <= rsi <= RSI_HEALTHY_MAX:
        score += 15
        triggered.append("rsi_healthy_range")
    else:
        # fringe zone between veto and healthy bounds (veto already blocked
        # rsi < RSI_VETO_MIN and rsi > RSI_VETO_MAX)
        score += 5
        triggered.append("rsi_acceptable_zone")

    # +15  Volume spike — current volume > 1.5× 20-day average
    if _volume_spike(df):
        score += 15
        triggered.append("volume_spike")

    # +10  Stochastic RSI %K crossed above %D from below 0.3
    if _stoch_rsi_bullish(df):
        score += 10
        triggered.append("stoch_rsi_bullish_cross")

    # +5   Price above rolling VWAP
    if vwap_val is not None and current_price > vwap_val:
        score += 5
        triggered.append("above_vwap")

    score = max(0, min(100, score))

    # ── Verdict ───────────────────────────────────────────────────────────────
    if score >= 75:
        verdict = "STRONG BUY"
    elif score >= 55:
        verdict = "BUY"
    elif score >= 35:
        verdict = "WATCH"
    else:
        verdict = "NEUTRAL"

    return _base_result(score, verdict, triggered)
