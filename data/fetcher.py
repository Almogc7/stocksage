from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ET = ZoneInfo("America/New_York")


def get_current_price(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info

        price = info.last_price
        prev_close = info.previous_close
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

        # three_month_average_volume is None for indices (^VIX, ^GSPC, etc.)
        # and some ETFs; can also be NaN. Coerce safely to int, defaulting to 0.
        avg_vol = info.three_month_average_volume
        try:
            volume = int(avg_vol) if avg_vol is not None else 0
        except (TypeError, ValueError):
            volume = 0
        return {
            "symbol": symbol.upper(),
            "price": round(price, 4),
            "change_pct": round(change_pct, 2),
            "volume": volume,
            "high": round(info.day_high, 4),
            "low": round(info.day_low, 4),
            "open": round(info.open, 4),
        }
    except Exception as e:
        print(f"[fetcher] Warning: could not fetch price for {symbol}: {e}")
        return None


def get_historical(
    symbol: str,
    period: str = "6mo",
    interval: str = "1d",
) -> pd.DataFrame | None:
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
        if df.empty:
            print(f"[fetcher] Warning: no historical data returned for {symbol}")
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        print(f"[fetcher] Warning: could not fetch historical data for {symbol}: {e}")
        return None


def get_multiple_prices(symbols: list[str]) -> dict[str, dict | None]:
    if not symbols:
        return {}

    upper = [s.upper() for s in symbols]
    try:
        raw = yf.download(upper, period="2d", interval="1d", progress=False, auto_adjust=True, group_by="ticker")
    except Exception as e:
        print(f"[fetcher] Warning: bulk download failed: {e}")
        return {s: None for s in upper}

    results: dict[str, dict | None] = {}

    for symbol in upper:
        try:
            if len(upper) == 1:
                df = raw
            else:
                df = raw[symbol]

            if df.empty or len(df) < 2:
                results[symbol] = None
                continue

            today = df.iloc[-1]
            prev = df.iloc[-2]

            def _f(x) -> float:
                return float(x) if not pd.isna(x) else 0.0

            price = _f(today["Close"])
            prev_close = _f(prev["Close"])
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

            if price == 0 or pd.isna(price):
                last_valid = df["Close"].dropna()
                if not last_valid.empty:
                    price = float(last_valid.iloc[-1])
                change_pct = 0.0

            results[symbol] = {
                "symbol": symbol,
                "price": round(price, 4),
                "change_pct": round(change_pct, 2),
                "volume": int(today["Volume"]) if not pd.isna(today["Volume"]) else 0,
                "high": round(_f(today["High"]), 4),
                "low": round(_f(today["Low"]), 4),
                "open": round(_f(today["Open"]), 4),
            }
        except Exception as e:
            print(f"[fetcher] Warning: could not parse data for {symbol}: {e}")
            results[symbol] = None

    return results


def get_52week_high_low(symbol: str) -> dict | None:
    try:
        df = yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            print(f"[fetcher] Warning: no 52-week data returned for {symbol}")
            return None

        high_52w = float(df["High"].max())
        low_52w = float(df["Low"].min())
        current = float(df["Close"].iloc[-1])

        pct_from_high = (current - high_52w) / high_52w * 100
        pct_from_low = (current - low_52w) / low_52w * 100

        return {
            "symbol": symbol.upper(),
            "high_52w": round(high_52w, 4),
            "low_52w": round(low_52w, 4),
            "current": round(current, 4),
            "pct_from_high": round(pct_from_high, 2),
            "pct_from_low": round(pct_from_low, 2),
        }
    except Exception as e:
        print(f"[fetcher] Warning: could not fetch 52-week data for {symbol}: {e}")
        return None


def is_market_open() -> bool:
    now_est = datetime.now(ZoneInfo("America/New_York"))
    if now_est.weekday() >= 5:  # Saturday or Sunday
        return False
    market_open  = now_est.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_est.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now_est <= market_close
