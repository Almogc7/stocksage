import numpy as np
import pandas as pd
import ta


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _ema200(df: pd.DataFrame) -> float | None:
    if len(df) < 200:
        return None
    return float(ta.trend.ema_indicator(df["close"], window=200).iloc[-1])


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

def check_ema150(df: pd.DataFrame, current_price: float) -> dict:
    df = _flatten(df)
    ema150 = float(ta.trend.ema_indicator(df["close"], window=150).iloc[-1])
    above = current_price > ema150
    pct_from_ema = (current_price - ema150) / ema150 * 100
    return {
        "ema150": round(ema150, 4),
        "above_ema150": above,
        "pct_from_ema": round(pct_from_ema, 2),
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

    ema = check_ema150(df, current_price)
    rsi_data = calc_rsi(df)
    macd_data = calc_macd(df)
    bb = calc_bollinger(df)
    atr = calc_atr(df)
    pivots = calc_pivot_points(df)
    swings = calc_swing_levels(df)

    score = 0
    triggered: list[str] = []

    # +15  price above EMA150
    if ema["above_ema150"]:
        score += 15
        triggered.append("price_above_ema150")

    # +10  EMA150 > EMA200 (confirmed uptrend)
    ema200_val = _ema200(df)
    if ema200_val is not None and ema["ema150"] > ema200_val:
        score += 10
        triggered.append("ema150_above_ema200")

    # +20  MACD bullish crossover in last 3 candles
    if _macd_bullish_last3(df):
        score += 20
        triggered.append("macd_bullish_crossover")

    # +15  RSI in healthy zone 40–65
    if 40 <= rsi_data["rsi"] <= 65:
        score += 15
        triggered.append("rsi_healthy_range")

    # +15  volume spike > 1.5× 20-day average
    if _volume_spike(df):
        score += 15
        triggered.append("volume_spike")

    # +15  Stochastic RSI %K crosses above %D from below 30
    if _stoch_rsi_bullish(df):
        score += 15
        triggered.append("stoch_rsi_bullish_cross")

    # +10  price above rolling VWAP
    vwap_val = _vwap(df)
    if vwap_val is not None and current_price > vwap_val:
        score += 10
        triggered.append("above_vwap")

    score = max(0, min(100, score))

    if score >= 86:
        verdict = "STRONG BUY"
    elif score >= 71:
        verdict = "BUY"
    elif score >= 51:
        verdict = "WEAK BUY"
    elif score >= 31:
        verdict = "WATCH"
    else:
        verdict = "AVOID"

    stop_loss = round(current_price - 1.5 * atr["atr"], 4)
    take_profit = round(current_price + 3.0 * atr["atr"], 4)

    return {
        "symbol": symbol.upper(),
        "current_price": current_price,
        **ema,
        **rsi_data,
        **macd_data,
        **bb,
        **atr,
        **pivots,
        **swings,
        "ema200": round(ema200_val, 4) if ema200_val is not None else None,
        "vwap": round(vwap_val, 4) if vwap_val is not None else None,
        "score": score,
        "verdict": verdict,
        "triggered_signals": triggered,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }
