"""
StrongTrendScanner (Phase 7) — first scanner built on the Phase 6 cached
indicator module. Evaluates a symbol's stored stock_prices data for a
"stacked, rising moving averages" strong-uptrend structure:

  1. At least 220 daily candles exist.
  2. latest close > SMA20
  3. latest close > SMA50
  4. SMA20 > SMA50
  5. SMA50 > SMA150
  6. SMA150 > SMA200
  7. SMA150 is rising vs RISING_LOOKBACK trading days ago
  8. SMA200 is rising vs RISING_LOOKBACK trading days ago

Pure DB-read scanner: no network calls, no writes to any table (scanner_runs/
scanner_results persistence is a separate, not-yet-built orchestration
phase — see module docstring in scanners/base_scanner.py). Not wired into
the live alert flow, Telegram, or any scheduler — dormant/standalone.

MIN_CANDLES=220 is a deliberate margin above the strictest individual need
here (SMA200-rising requires window(200) + RISING_LOOKBACK(5) = 205 usable
closes): once the candle-count gate passes, compute_cached_indicators() is
guaranteed to return non-None values for every SMA this scanner uses, so
there is no partial-data state to handle beyond the gate itself.
"""
from __future__ import annotations

from analyzers.cached_indicators import compute_cached_indicators
from data.history_store import get_latest_prices
from scanners.base_scanner import BaseScanner

MIN_CANDLES = 220
RISING_LOOKBACK = 5

_CONDITION_NAMES: list[str] = [
    "min_candles",
    "close_above_sma20",
    "close_above_sma50",
    "sma20_above_sma50",
    "sma50_above_sma150",
    "sma150_above_sma200",
    "sma150_rising",
    "sma200_rising",
]


class StrongTrendScanner(BaseScanner):
    name = "StrongTrendScanner"

    def scan(self, symbol: str, timeframe: str = "1d") -> dict:
        sym = symbol.upper()
        indicators = compute_cached_indicators(
            sym, timeframe, n=250, rising_lookback=RISING_LOOKBACK
        )
        candles_used = indicators["candles_used"]

        if candles_used < MIN_CANDLES:
            return self._insufficient_data_result(
                sym,
                timeframe,
                candles_used=candles_used,
                min_required=MIN_CANDLES,
                condition_names=_CONDITION_NAMES,
                indicator_values={
                    "sma20": indicators["sma20"]["value"],
                    "sma50": indicators["sma50"]["value"],
                    "sma150": indicators["sma150"]["value"],
                    "sma200": indicators["sma200"]["value"],
                    "candles_used": candles_used,
                },
            )

        sma20 = indicators["sma20"]["value"]
        sma50 = indicators["sma50"]["value"]
        sma150 = indicators["sma150"]["value"]
        sma200 = indicators["sma200"]["value"]
        sma150_rising = indicators["sma150"]["rising"]
        sma200_rising = indicators["sma200"]["rising"]

        latest_rows = get_latest_prices(sym, timeframe=timeframe, n=1)
        latest_close = latest_rows[-1]["close"] if latest_rows else None

        conditions = {
            "min_candles": True,
            "close_above_sma20": latest_close is not None and sma20 is not None and latest_close > sma20,
            "close_above_sma50": latest_close is not None and sma50 is not None and latest_close > sma50,
            "sma20_above_sma50": sma20 is not None and sma50 is not None and sma20 > sma50,
            "sma50_above_sma150": sma50 is not None and sma150 is not None and sma50 > sma150,
            "sma150_above_sma200": sma150 is not None and sma200 is not None and sma150 > sma200,
            "sma150_rising": bool(sma150_rising),
            "sma200_rising": bool(sma200_rising),
        }

        passed_count = sum(1 for v in conditions.values() if v)
        total = len(_CONDITION_NAMES)
        score = round(passed_count / total * 100)
        passed = passed_count == total

        if passed:
            reason = f"PASS: all {total} conditions met"
        else:
            failing = [name for name in _CONDITION_NAMES if not conditions[name]]
            reason = f"FAIL: {passed_count}/{total} conditions met; failing: {', '.join(failing)}"

        return {
            "symbol": sym,
            "scanner_name": self.name,
            "timeframe": timeframe,
            "passed": passed,
            "score": score,
            "reason": reason,
            "conditions": conditions,
            "indicator_values": {
                "sma20": sma20,
                "sma50": sma50,
                "sma150": sma150,
                "sma200": sma200,
                "candles_used": candles_used,
            },
            "latest_close": latest_close,
        }
