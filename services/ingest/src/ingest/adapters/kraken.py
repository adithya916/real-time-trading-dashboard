"""Kraken spot, WebSocket API v2: L2 book with CRC32 checksums, and trades.

Docs: https://docs.kraken.com/api/docs/guides/spot-ws-book-v2

The book channel sends a snapshot, then updates in which a quantity of 0 deletes the level.
Kraken only maintains `depth` levels per side and does not send deletes for levels pushed past
it, so the local book is truncated after every message. Each message carries a checksum of the
top 10 levels; a mismatch means the local book is wrong, so the adapter emits a BookReset and
drops the connection, and the supervisor reconnects for a fresh snapshot.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ingest.adapters.base import AdapterError, FeedAdapter
from ingest.book.checksum import kraken_checksum
from ingest.book.pybook import BookSide, PyBook, to_snapshot
from mdcommon.schema import (
    Aggressor,
    Delta,
    Envelope,
    LevelUpdate,
    ResetReason,
    Side,
    Snapshot,
    Trade,
    to_ticks,
)

URL = "wss://ws.kraken.com/v2"
DEPTH = 10
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Instrument:
    venue_symbol: str
    price_scale: int
    size_scale: int


# Precision as Kraken publishes it on its instrument channel.
INSTRUMENTS = {
    "BTC-USD": Instrument("BTC/USD", price_scale=1, size_scale=8),
    "ETH-USD": Instrument("ETH/USD", price_scale=2, size_scale=8),
}

_SIDES: tuple[tuple[str, BookSide, Side], ...] = (
    ("bids", "bid", Side.SIDE_BID),
    ("asks", "ask", Side.SIDE_ASK),
)


def parse_ts_ns(value: str) -> int:
    """'2024-05-01T12:00:00.123456Z' to Unix nanoseconds, without float rounding."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (dt - _EPOCH) // timedelta(microseconds=1) * 1000


def _ticks(value: Decimal | int, scale: int) -> int:
    return to_ticks(format(Decimal(value), "f"), scale)


class KrakenAdapter(FeedAdapter):
    venue = "kraken"

    def __init__(self, symbols: list[str], **kwargs: Any) -> None:
        unknown = [s for s in symbols if s not in INSTRUMENTS]
        if unknown:
            raise ValueError(f"no Kraken instrument for {unknown}; known: {sorted(INSTRUMENTS)}")
        super().__init__(symbols, **kwargs)
        self._by_venue_symbol = {INSTRUMENTS[s].venue_symbol: s for s in self.symbols}
        self.books: dict[str, PyBook] = {}

    def subscriptions(self) -> list[dict[str, Any]]:
        venue_symbols = [INSTRUMENTS[s].venue_symbol for s in self.symbols]
        return [
            {
                "method": "subscribe",
                "params": {"channel": "book", "symbol": venue_symbols, "depth": DEPTH},
            },
            {
                "method": "subscribe",
                "params": {"channel": "trade", "symbol": venue_symbols, "snapshot": False},
            },
        ]

    async def stream(self) -> AsyncGenerator[Envelope, None]:
        self.books = {}
        async with self._connect(URL) as socket:
            for message in self.subscriptions():
                await socket.send(json.dumps(message))
            while True:
                frame = await socket.recv()
                message = json.loads(frame, parse_float=Decimal)
                for envelope in self.handle(message):
                    yield envelope

    def handle(self, message: dict[str, Any]) -> Iterator[Envelope]:
        if message.get("method") == "subscribe" and not message.get("success", True):
            raise AdapterError(f"Kraken refused a subscription: {message.get('error')}")
        channel = message.get("channel")
        if channel == "book":
            for entry in message["data"]:
                yield from self._book(message["type"], entry)
        elif channel == "trade":
            for entry in message["data"]:
                yield self._trade(entry)
        # heartbeat, status and subscription acknowledgements carry nothing to forward.

    def _book(self, kind: str, entry: dict[str, Any]) -> Iterator[Envelope]:
        symbol = self._by_venue_symbol[entry["symbol"]]
        inst = INSTRUMENTS[symbol]
        ts_exch = parse_ts_ns(entry["timestamp"]) if "timestamp" in entry else 0

        if kind == "snapshot":
            book = self.books[symbol] = PyBook()
        elif symbol in self.books:
            book = self.books[symbol]
        else:
            raise AdapterError(f"Kraken sent a {symbol} update before its snapshot")

        updates: list[LevelUpdate] = []
        for key, side, wire_side in _SIDES:
            for level in entry.get(key, []):
                price = _ticks(level["price"], inst.price_scale)
                size = _ticks(level["qty"], inst.size_scale)
                book.apply(side, price, size)
                updates.append(LevelUpdate(side=wire_side, price_ticks=price, size_lots=size))
        book.truncate(DEPTH)

        expected = int(entry["checksum"])
        actual = kraken_checksum(book, inst.price_scale, inst.size_scale)
        if actual != expected:
            yield self.reset(symbol, ResetReason.RESET_REASON_CHECKSUM_MISMATCH)
            raise AdapterError(f"{symbol} checksum mismatch: Kraken {expected}, local {actual}")

        if kind == "snapshot":
            snapshot = to_snapshot(book, inst.price_scale, inst.size_scale, expected)
            yield self.wrap(symbol, snapshot, ts_exch)
        else:
            yield self.wrap(symbol, Delta(updates=updates, checksum=expected), ts_exch)

    def book_snapshot(self, symbol: str) -> Snapshot | None:
        book = self.books.get(symbol)
        if book is None:
            return None
        inst = INSTRUMENTS[symbol]
        checksum = kraken_checksum(book, inst.price_scale, inst.size_scale)
        return to_snapshot(book, inst.price_scale, inst.size_scale, checksum)

    def _trade(self, entry: dict[str, Any]) -> Envelope:
        symbol = self._by_venue_symbol[entry["symbol"]]
        inst = INSTRUMENTS[symbol]
        trade = Trade(
            trade_id=str(entry["trade_id"]),
            price_ticks=_ticks(entry["price"], inst.price_scale),
            size_lots=_ticks(entry["qty"], inst.size_scale),
            aggressor=Aggressor.AGGRESSOR_BUY
            if entry["side"] == "buy"
            else Aggressor.AGGRESSOR_SELL,
        )
        return self.wrap(symbol, trade, parse_ts_ns(entry["timestamp"]))
