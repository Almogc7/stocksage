"""
Scanner abstraction (Phase 7).

A scanner evaluates one symbol's already-cached stock_prices data (via
analyzers/cached_indicators.py) against a fixed set of conditions and
returns a structured result — never raises, never fetches live data, never
writes to any table, and is not wired into the live alert flow or Telegram.
Persisting results (scanner_runs/scanner_results) and looping over a symbol
universe are separate concerns left to a future orchestration phase.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseScanner(ABC):
    name: str

    @abstractmethod
    def scan(self, symbol: str, timeframe: str = "1d") -> dict:
        """Must never raise; return the shared structured result shape."""
        raise NotImplementedError

    def _insufficient_data_result(
        self,
        symbol: str,
        timeframe: str,
        *,
        candles_used: int,
        min_required: int,
        condition_names: list[str],
        indicator_values: dict | None = None,
    ) -> dict:
        """
        Shared "not enough data" result shape: the candle-count gate itself
        is reported as failed, every other condition is left unevaluated
        (None) rather than guessed at from partial data.
        """
        conditions = {condition_names[0]: False}
        for name in condition_names[1:]:
            conditions[name] = None
        return {
            "symbol": symbol.upper(),
            "scanner_name": self.name,
            "timeframe": timeframe,
            "passed": False,
            "score": 0,
            "reason": f"insufficient data: {candles_used}/{min_required} candles",
            "conditions": conditions,
            "indicator_values": indicator_values or {},
            "latest_close": None,
        }
