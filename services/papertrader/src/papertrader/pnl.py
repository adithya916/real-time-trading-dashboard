"""Position and PnL in integer ticks and lots, by average cost.

Buying adds to a long position at the volume-weighted average price; selling against it
realizes (sell price - average) on the lots closed. A fill larger than the position closes it
and opens the other way at the fill price. Unrealized PnL marks what is left to a price.
All amounts are ticks x lots; to_money() turns them into the quote currency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SideName = Literal["bid", "ask"]


@dataclass
class Position:
    lots: int = 0  # signed: positive is long
    avg_ticks: float = 0.0
    realized: float = 0.0  # ticks x lots
    fills: int = 0
    traded_lots: int = 0

    def fill(self, side: SideName, price_ticks: int, size_lots: int) -> None:
        if size_lots <= 0:
            raise ValueError(f"fill size must be positive, got {size_lots}")
        self.fills += 1
        self.traded_lots += size_lots
        signed = size_lots if side == "bid" else -size_lots
        if self.lots == 0 or (self.lots > 0) == (signed > 0):
            held = abs(self.lots)
            self.avg_ticks = (self.avg_ticks * held + price_ticks * size_lots) / (held + size_lots)
            self.lots += signed
            return
        closing = min(size_lots, abs(self.lots))
        direction = 1 if self.lots > 0 else -1
        self.realized += closing * (price_ticks - self.avg_ticks) * direction
        self.lots += signed
        if self.lots == 0:
            self.avg_ticks = 0.0
        elif (self.lots > 0) != (direction > 0):
            self.avg_ticks = float(price_ticks)  # flipped: the remainder opened at this price

    def unrealized(self, mark_ticks: float) -> float:
        return self.lots * (mark_ticks - self.avg_ticks)


def to_money(ticks_times_lots: float, price_scale: int, size_scale: int) -> float:
    return ticks_times_lots / float(10 ** (price_scale + size_scale))
