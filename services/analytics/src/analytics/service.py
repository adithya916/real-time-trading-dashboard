"""The analytics service: mirror every book on the bus and publish metrics 10 times a second.

    python -m analytics.service --bus nats://localhost:4222

Subscribes to md.*.*.book and md.*.*.trade. Every 100 ms it samples each mid and publishes an
Analytics envelope to md.<venue>.<symbol>.analytics, but only when the metrics changed.
Analytics envelopes carry their own gapless seq per symbol.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from typing import Any

from analytics.metrics import SymbolAnalytics
from mdcommon.schema import Analytics, Envelope

log = logging.getLogger(__name__)
TICK_S = 0.1


class AnalyticsState:
    """All symbols' metrics and the analytics seq per symbol, independent of NATS."""

    def __init__(self) -> None:
        self.symbols: dict[tuple[str, str], SymbolAnalytics] = {}
        self._seq: dict[tuple[str, str], int] = {}
        self._last: dict[tuple[str, str], Analytics] = {}

    def on_envelope(self, envelope: Envelope) -> None:
        key = (envelope.venue, envelope.symbol)
        self.symbols.setdefault(key, SymbolAnalytics()).on_envelope(envelope)

    def tick(self, now_ns: int) -> list[Envelope]:
        """Sample every symbol and return an envelope for each whose metrics changed."""
        out = []
        for key, symbol in self.symbols.items():
            symbol.sample(now_ns)
            metrics = symbol.compute(now_ns)
            if metrics is None or metrics == self._last.get(key):
                continue
            self._last[key] = metrics
            self._seq[key] = self._seq.get(key, 0) + 1
            envelope = Envelope(
                venue=key[0], symbol=key[1], seq=self._seq[key], ts_recv_ns=now_ns, ts_bus_ns=now_ns
            )
            envelope.analytics.CopyFrom(metrics)
            out.append(envelope)
        return out


async def run(bus_url: str) -> None:
    import nats

    from mdcommon.bus import ensure_stream, subject_for

    nc = await nats.connect(bus_url, name="analytics", max_reconnect_attempts=-1)
    await ensure_stream(nc.jetstream())
    state = AnalyticsState()

    async def on_message(msg: Any) -> None:
        state.on_envelope(Envelope.FromString(msg.data))

    await nc.subscribe("md.*.*.book", cb=on_message)
    await nc.subscribe("md.*.*.trade", cb=on_message)
    log.info("analytics: subscribed to books and trades on %s", bus_url)
    published = 0
    try:
        while True:
            await asyncio.sleep(TICK_S)
            for envelope in state.tick(time.time_ns()):
                await nc.publish(subject_for(envelope), envelope.SerializeToString())
                published += 1
    finally:
        log.info("analytics: published %d envelopes", published)
        await nc.drain()


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish market metrics computed from the bus.")
    parser.add_argument("--bus", default="nats://localhost:4222")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.bus))


if __name__ == "__main__":
    main()
