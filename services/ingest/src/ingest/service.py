"""The ingest service: run venue adapters and publish what they produce to the bus.

    python -m ingest.service --bus nats://localhost:4222 kraken:BTC-USD,ETH-USD binance:BTC-USDT
    python -m ingest.service --bus nats://localhost:4222 --replay rec.jsonl.gz kraken:BTC-USD

Every envelope is stamped with ts_bus_ns and published to md.<venue>.<symbol>.<kind>.
Snapshots also go to the KV bucket, and about once a second each in-sync book is written
there again, tagged with the seq of the last envelope it includes. A consumer that starts late
loads that snapshot and applies the stream from the next seq.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import aclosing
from pathlib import Path
from typing import Any, Protocol

from ingest.adapters.base import FeedAdapter, supervise
from ingest.adapters.registry import load
from ingest.recording import ReplayConnector
from mdcommon.latency import LATENCY, serve_metrics
from mdcommon.metrics import ENVELOPES, RESETS
from mdcommon.schema import Envelope, ResetReason

log = logging.getLogger(__name__)


class Publisher(Protocol):
    async def publish(self, envelope: Envelope) -> None: ...

    async def put_snapshot(self, envelope: Envelope) -> None: ...


async def refresh_snapshots(
    adapter: FeedAdapter, bus: Publisher, clock: Callable[[], int] = time.time_ns
) -> int:
    """Write every in-sync book to the snapshot bucket at its current seq. Returns how many."""
    written = 0
    for symbol in adapter.symbols:
        snapshot = adapter.book_snapshot(symbol)
        if snapshot is None:
            continue
        envelope = Envelope(
            venue=adapter.venue,
            symbol=symbol,
            seq=adapter.last_seq(symbol),
            ts_recv_ns=clock(),
            ts_bus_ns=clock(),
        )
        envelope.snapshot.CopyFrom(snapshot)
        await bus.put_snapshot(envelope)
        written += 1
    return written


async def publish(
    adapter: FeedAdapter,
    bus: Publisher,
    *,
    snapshot_every_s: float = 1.0,
    max_restarts: int | None = None,
    clock: Callable[[], int] = time.time_ns,
    measure: bool = True,
    service: str = "ingest",
) -> int:
    """Publish everything the adapter produces until it stops. Returns how many envelopes.

    With measure, each envelope's hops go into the latency histograms: exch_to_recv (venue
    time to ours, so it includes any offset between the clocks) and recv_to_bus (in-process).
    A replay turns it off: its receive times are the recording's, not now.
    """
    published = 0
    last_refresh = time.monotonic()
    async with aclosing(supervise(adapter, max_restarts=max_restarts)) as envelopes:
        async for envelope in envelopes:
            envelope.ts_bus_ns = clock()
            await bus.publish(envelope)
            published += 1
            kind = envelope.WhichOneof("payload") or "none"
            ENVELOPES.labels(service, envelope.venue, kind).inc()
            if kind == "reset":
                reason = ResetReason.Name(envelope.reset.reason).removeprefix("RESET_REASON_")
                RESETS.labels(service, envelope.venue, reason.lower()).inc()
            if measure:
                LATENCY.record_ns("exch_to_recv", envelope.ts_exch_ns, envelope.ts_recv_ns)
                LATENCY.record_ns("recv_to_bus", envelope.ts_recv_ns, envelope.ts_bus_ns)
            if envelope.HasField("snapshot"):
                await bus.put_snapshot(envelope)
            if time.monotonic() - last_refresh >= snapshot_every_s:
                await refresh_snapshots(adapter, bus, clock)
                last_refresh = time.monotonic()
    await refresh_snapshots(adapter, bus, clock)
    return published


def parse_feed(spec: str) -> tuple[str, list[str]]:
    """'kraken:BTC-USD,ETH-USD' -> ('kraken', ['BTC-USD', 'ETH-USD'])"""
    venue, _, symbols = spec.partition(":")
    if not venue or not symbols:
        raise argparse.ArgumentTypeError(f"expected venue:SYMBOL[,SYMBOL...], got {spec!r}")
    return venue, [s for s in symbols.split(",") if s]


async def run(
    feeds: list[tuple[str, list[str]]],
    bus_url: str,
    replay: Path | None,
    speed: float | None,
    metrics_port: int = 0,
) -> None:
    from mdcommon.bus import BusPublisher

    if metrics_port:
        serve_metrics(metrics_port)
    bus = await BusPublisher.connect(bus_url)
    tasks = []
    for venue, symbols in feeds:
        kwargs: dict[str, Any] = {}
        if replay:
            replayer = ReplayConnector(replay, speed)
            kwargs["connect"] = replayer
            kwargs["clock"] = replayer.clock.now_ns  # recorded receive times, not now
            if venue == "binance":
                kwargs["fetch"] = replayer.fetch
        adapter = load(venue)(symbols, **kwargs)
        tasks.append(publish(adapter, bus, max_restarts=0 if replay else None, measure=not replay))
    try:
        counts = await asyncio.gather(*tasks)
        log.info("published %d envelopes", sum(counts))
    finally:
        await bus.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish venue feeds to the NATS bus.")
    parser.add_argument("feeds", nargs="+", type=parse_feed, help="venue:SYMBOL[,SYMBOL...]")
    parser.add_argument("--bus", default="nats://localhost:4222")
    parser.add_argument("--replay", type=Path, help="play a recording instead of connecting live")
    parser.add_argument("--speed", type=float, help="replay speed; omit for as fast as possible")
    parser.add_argument("--metrics-port", type=int, default=9101, help="0 turns /metrics off")
    args = parser.parse_args()
    if args.replay and len(args.feeds) != 1:
        parser.error("--replay plays one recording, so give exactly one feed")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.feeds, args.bus, args.replay, args.speed, args.metrics_port))


if __name__ == "__main__":
    main()
