import numpy as np
import pandas as pd
import ta


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


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
    rsi = calc_rsi(df)
    macd = calc_macd(df)
    bb = calc_bollinger(df)
    atr = calc_atr(df)
    pivots = calc_pivot_points(df)
    swings = calc_swing_levels(df)

    buy_score = 0
    sell_score = 0

    if not ema["above_ema150"]:
        recommendation = "NO_BUY - below EMA150"
        return {
            "symbol": symbol.upper(),
            "current_price": current_price,
            **ema, **rsi, **macd, **bb, **atr, **pivots, **swings,
            "buy_score": 0,
            "sell_score": sell_score,
            "recommendation": recommendation,
        }

    if rsi["signal"] == "oversold":
        buy_score += 25
    elif rsi["signal"] == "overbought":
        sell_score += 25

    if macd["crossover"] == "bullish":
        buy_score += 20
    elif macd["crossover"] == "bearish":
        sell_score += 20

    if bb["position"] in ("near_lower", "below_lower"):
        buy_score += 15
    elif bb["position"] in ("near_upper", "above_upper"):
        sell_score += 15

    if current_price > pivots["pp"]:
        buy_score += 10

    if atr["volatility"] == "high":
        buy_score -= 10
        sell_score -= 10

    if swings["nearest_support"] is not None:
        pct_from_support = (current_price - swings["nearest_support"]) / current_price * 100
        if pct_from_support <= 2:
            buy_score += 10

    if swings["nearest_resistance"] is not None:
        pct_from_resistance = (swings["nearest_resistance"] - current_price) / current_price * 100
        if pct_from_resistance <= 2:
            sell_score += 10

    buy_score = max(0, min(100, buy_score))
    sell_score = max(0, min(100, sell_score))

    if buy_score >= 70:
        recommendation = "STRONG_BUY"
    elif buy_score >= 50:
        recommendation = "BUY"
    elif buy_score >= 30:
        recommendation = "WATCH"
    else:
        recommendation = "NEUTRAL"

    return {
        "symbol": symbol.upper(),
        "current_price": current_price,
        **ema,
        **rsi,
        **macd,
        **bb,
        **atr,
        **pivots,
        **swings,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "recommendation": recommendation,
    }
