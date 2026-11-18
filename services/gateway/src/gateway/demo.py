"""Demo mode: replay a recording straight into the gateway, with no NATS.

    python -m gateway --demo services/ingest/tests/fixtures/kraken_btcusd.jsonl.gz kraken:BTC-USD

The recording plays through the real venue adapter at its recorded pace, on a loop, and the
analytics service's code computes metrics in the same process. Viewers see exactly what the
full stack would send them, so the dashboard can be checked on a laptop without Docker.

Each loop is a new adapter session, so it starts again at seq 1 with a fresh snapshot. The demo
shifts seqs to keep counting up across loops, drops the end-of-session BookReset (the next
snapshot replaces the book anyway), and restamps timestamps with the current time so charts
and candles move forward instead of jumping back to when the recording was made.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import aclosing
from pathlib import Path
from typing import Any

from analytics.service import TICK_S as ANALYTICS_TICK_S
from analytics.service import AnalyticsState

from gateway.hub import Hub
from ingest.adapters.base import supervise
from ingest.adapters.registry import load
from ingest.recording import ReplayConnector

log = logging.getLogger(__name__)


async def replay_forever(
    hub: Hub,
    analytics: AnalyticsState,
    recording: Path,
    venue: str,
    symbols: list[str],
    *,
    speed: float | None = 1.0,
    loops: int | None = None,
    clock: Callable[[], int] = time.time_ns,
) -> int:
    """Play the recording into the hub `loops` times (forever if None). Returns envelopes fed."""
    seq_offset: dict[str, int] = {}
    fed = 0
    played = 0
    while loops is None or played < loops:
        replayer = ReplayConnector(recording, speed)
        kwargs: dict[str, Any] = {"connect": replayer}
        if venue == "binance":
            kwargs["fetch"] = replayer.fetch
        adapter = load(venue)(symbols, **kwargs)
        last_seq = dict(seq_offset)
        async with aclosing(supervise(adapter, max_restarts=0)) as envelopes:
            async for envelope in envelopes:
                if envelope.HasField("reset"):
                    continue
                envelope.seq += seq_offset.get(envelope.symbol, 0)
                last_seq[envelope.symbol] = envelope.seq
                now = clock()
                if envelope.ts_exch_ns:
                    envelope.ts_exch_ns = now
                envelope.ts_recv_ns = envelope.ts_bus_ns = now
                analytics.on_envelope(envelope)
                hub.on_envelope(envelope)
                fed += 1
        seq_offset = last_seq
        played += 1
        log.info("demo: played %s %d time(s)", recording.name, played)
    return fed


async def analytics_forever(
    hub: Hub, analytics: AnalyticsState, clock: Callable[[], int] = time.time_ns
) -> None:
    while True:
        await asyncio.sleep(ANALYTICS_TICK_S)
        for envelope in analytics.tick(clock()):
            hub.on_envelope(envelope)


async def run_demo(
    hub: Hub, recording: Path, venue: str, symbols: list[str], speed: float | None
) -> None:
    analytics = AnalyticsState()
    log.info("demo: replaying %s as %s %s", recording, venue, ",".join(symbols))
    await asyncio.gather(
        replay_forever(hub, analytics, recording, venue, symbols, speed=speed),
        analytics_forever(hub, analytics),
    )
