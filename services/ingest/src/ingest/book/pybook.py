"""A deliberately simple L2 order book.

This is the reference implementation: plain dicts, no cleverness, so it is easy to believe it
is correct. The C++ book (Day 3) must produce exactly the same levels for the same events.

Prices and sizes are integer ticks and lots, the same units as marketdata.v1, so books built
from the same stream compare exactly.
"""

from __future__ import annotations

from typing import Literal

from mdcommon.schema import Level as WireLevel
from mdcommon.schema import Snapshot

BookSide = Literal["bid", "ask"]
Level = tuple[int, int]


class PyBook:
    def __init__(self) -> None:
        self.bids: dict[int, int] = {}
        self.asks: dict[int, int] = {}

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def apply(self, side: BookSide, price: int, size: int) -> None:
        """Set a level's size. Size 0 removes the level; removing a missing level is fine."""
        if size < 0:
            raise ValueError(f"negative size {size} at {price}")
        levels = self.bids if side == "bid" else self.asks
        if size == 0:
            levels.pop(price, None)
        else:
            levels[price] = size

    def top(self, side: BookSide, n: int | None = None) -> list[Level]:
        """Levels best first: highest bid, lowest ask."""
        levels = self.bids if side == "bid" else self.asks
        ordered = sorted(levels.items(), reverse=side == "bid")
        return ordered if n is None else ordered[:n]

    def best_bid(self) -> Level | None:
        return max(self.bids.items(), default=None)

    def best_ask(self) -> Level | None:
        return min(self.asks.items(), default=None)

    def is_crossed(self) -> bool:
        """True when the best bid is at or above the best ask, which a correct book never shows."""
        bid, ask = self.best_bid(), self.best_ask()
        return bid is not None and ask is not None and bid[0] >= ask[0]

    def truncate(self, depth: int) -> list[tuple[BookSide, int]]:
        """Keep only the best `depth` levels per side; return the (side, price) levels dropped.

        Kraken stops updating levels past its depth without sending deletes for them.
        """
        dropped: list[tuple[BookSide, int]] = []
        for side in ("bid", "ask"):
            levels = self.bids if side == "bid" else self.asks
            keep = dict(self.top(side, depth))
            dropped += [(side, price) for price in levels if price not in keep]
            if side == "bid":
                self.bids = keep
            else:
                self.asks = keep
        return dropped

    def __len__(self) -> int:
        return len(self.bids) + len(self.asks)


def to_snapshot(book: PyBook, price_scale: int, size_scale: int, checksum: int = 0) -> Snapshot:
    """The whole book as a marketdata.v1 Snapshot, best levels first."""
    return Snapshot(
        price_scale=price_scale,
        size_scale=size_scale,
        bids=[WireLevel(price_ticks=p, size_lots=q) for p, q in book.top("bid")],
        asks=[WireLevel(price_ticks=p, size_lots=q) for p, q in book.top("ask")],
        checksum=checksum,
    )
