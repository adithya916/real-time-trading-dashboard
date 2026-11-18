"""Replay recorded Parquet onto the bus, at any speed.

    python -m recorder.replay --root data --bus nats://localhost:4222 kraken:BTC-USD --speed 10

Envelopes go out exactly as recorded (same seq, venue timestamps and receive timestamps), on
the same subjects ingest uses, so analytics, the gateway and the candle history can't tell a
replay from the live session; only ts_bus_ns is stamped anew. Feeds are merged by receive time,
and a ReplayClock paces them: speed 1 is real time, 10 is ten times faster, and omitting
--speed replays as fast as possible.

Like ingest, the replay keeps each book and writes it to the snapshot bucket about once a
second (wall time), so a gateway that joins mid-replay resyncs to it.
"""

from __future__ import annotations

import argparse
import asyncio
import heapq
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ingest.book.pybook import BookSide, PyBook, to_snapshot
from ingest.service import Publisher, parse_feed
from mdcommon.clock import Clock, ReplayClock
from mdcommon.schema import Envelope, Side
from recorder.writer import read_envelopes

log = logging.getLogger(__name__)


def recorded(root: Path, feeds: Iterable[tuple[str, list[str]]]) -> Iterable[Envelope]:
    """Every recorded envelope of the given feeds, merged into receive-time order."""
    streams = [
        read_envelopes(root, venue, symbol) for venue, symbols in feeds for symbol in symbols
    ]
    return heapq.merge(*streams, key=lambda e: e.ts_recv_ns)


@dataclass
class _Book:
    book: PyBook = field(default_factory=PyBook)
    price_scale: int = 0
    size_scale: int = 0
    seq: int = 0
    synced: bool = False

    def apply(self, envelope: Envelope) -> None:
        if envelope.HasField("snapshot"):
            self.book.clear()
            for level in envelope.snapshot.bids:
                self.book.apply("bid", level.price_ticks, level.size_lots)
            for level in envelope.snapshot.asks:
                self.book.apply("ask", level.price_ticks, level.size_lots)
            self.price_scale = envelope.snapshot.price_scale
            self.size_scale = envelope.snapshot.size_scale
            self.synced = True
        elif envelope.HasField("delta"):
            for update in envelope.delta.updates:
                side: BookSide = "bid" if update.side == Side.SIDE_BID else "ask"
                self.book.apply(side, update.price_ticks, update.size_lots)
        elif envelope.HasField("reset"):
            self.book.clear()
            self.synced = False
        if not envelope.HasField("analytics"):
            self.seq = envelope.seq


async def replay(
    envelopes: Iterable[Envelope],
    bus: Publisher,
    clock: Clock,
    *,
    snapshot_every_s: float = 1.0,
    wall_ns: Callable[[], int] = time.time_ns,
) -> int:
    """Publish every envelope when the clock says it is due. Returns how many."""
    books: dict[tuple[str, str], _Book] = {}
    published = 0
    last_refresh = time.monotonic()
    for envelope in envelopes:
        await clock.wait_for(envelope.ts_recv_ns)
        envelope.ts_bus_ns = wall_ns()
        await bus.publish(envelope)
        published += 1
        book = books.setdefault((envelope.venue, envelope.symbol), _Book())
        book.apply(envelope)
        if envelope.HasField("snapshot"):
            await bus.put_snapshot(envelope)
        if time.monotonic() - last_refresh >= snapshot_every_s:
            await refresh(books, bus, wall_ns)
            last_refresh = time.monotonic()
    await refresh(books, bus, wall_ns)
    return published


async def refresh(
    books: dict[tuple[str, str], _Book], bus: Publisher, wall_ns: Callable[[], int]
) -> None:
    for (venue, symbol), book in books.items():
        if not book.synced:
            continue
        now = wall_ns()
        envelope = Envelope(venue=venue, symbol=symbol, seq=book.seq, ts_recv_ns=now, ts_bus_ns=now)
        envelope.snapshot.CopyFrom(to_snapshot(book.book, book.price_scale, book.size_scale))
        await bus.put_snapshot(envelope)


async def run(
    root: Path, feeds: list[tuple[str, list[str]]], bus_url: str, speed: float | None
) -> None:
    from mdcommon.bus import BusPublisher

    bus = await BusPublisher.connect(bus_url)
    try:
        count = await replay(recorded(root, feeds), bus, ReplayClock(speed))
        log.info("replayed %d envelopes", count)
    finally:
        await bus.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay recorded Parquet onto the NATS bus.")
    parser.add_argument("feeds", nargs="+", type=parse_feed, help="venue:SYMBOL[,SYMBOL...]")
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--bus", default="nats://localhost:4222")
    parser.add_argument("--speed", type=float, help="1 = real time; omit for as fast as possible")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.root, args.feeds, args.bus, args.speed))


if __name__ == "__main__":
    main()
