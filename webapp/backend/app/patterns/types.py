"""Shared dataclasses for the pattern-detection module."""
from dataclasses import dataclass, field
from typing import Literal

PivotKind = Literal["high", "low"]
Direction = Literal["long", "short"]
PatternName = Literal["trend_pullback", "flag_breakout", "double_top_bottom"]


@dataclass(frozen=True)
class Pivot:
    date: str
    price: float
    kind: PivotKind
    confirmed_on: str  # first date this pivot could have been known, always >= date


@dataclass(frozen=True)
class Detection:
    pattern: PatternName
    direction: Direction
    symbol: str
    start_date: str
    end_date: str
    confirmed_on: str  # date of the bar whose CLOSE confirms the pattern
    levels: dict = field(default_factory=dict)
    rules: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "direction": self.direction,
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "confirmed_on": self.confirmed_on,
            "levels": dict(self.levels),
            "rules": dict(self.rules),
        }
