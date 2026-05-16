import sys
from pprint import pprint

from data.fetcher import get_current_price, get_historical
from analyzers.technical import full_analysis
from db.database import init_db

SYMBOL = "NVDA"


def main():
    print("--- init_db ---")
    init_db()
    print("DB initialised.\n")

    print(f"--- get_historical({SYMBOL}, period='1y') ---")
    df = get_historical(SYMBOL, period="1y")
    if df is None:
        print("ERROR: get_historical returned None")
        sys.exit(1)
    print(f"Shape: {df.shape}  |  Columns: {list(df.columns)}")
    print(df.tail(3).to_string())
    print()

    print(f"--- get_current_price({SYMBOL}) ---")
    price_data = get_current_price(SYMBOL)
    if price_data is None:
        print("ERROR: get_current_price returned None")
        sys.exit(1)
    pprint(price_data)
    print()

    current_price = price_data["price"]

    print(f"--- full_analysis({SYMBOL}) ---")
    result = full_analysis(SYMBOL, df, current_price)

    sections = {
        "Overview":     ["symbol", "current_price", "recommendation", "buy_score", "sell_score"],
        "EMA":          ["ema150", "above_ema150", "pct_from_ema"],
        "RSI":          ["rsi", "signal"],
        "MACD":         ["macd", "signal_line", "histogram", "crossover"],
        "Bollinger":    ["upper", "middle", "lower", "position"],
        "ATR":          ["atr", "atr_pct", "volatility", "stop_loss_1x", "stop_loss_15x", "take_profit_2x"],
        "Pivots":       ["pp", "r1", "r2", "r3", "s1", "s2", "s3"],
        "Swing Levels": ["nearest_resistance", "nearest_support", "swing_highs", "swing_lows"],
    }

    for section, keys in sections.items():
        print(f"  {section}:")
        for k in keys:
            if k in result:
                print(f"    {k:<22} {result[k]}")
        print()

    print("All systems OK")


if __name__ == "__main__":
    main()
