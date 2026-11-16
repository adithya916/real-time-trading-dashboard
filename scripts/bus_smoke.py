"""Publish through NATS and read back from JetStream and the snapshot bucket.

Usage: uv run --project services/common python scripts/bus_smoke.py nats://localhost:4222
"""

import asyncio
import sys

from mdcommon.bus import SNAPSHOT_BUCKET, STREAM, BusPublisher, snapshot_key, subject_for
from mdcommon.schema import Envelope, Level, Snapshot, Trade


async def main(url: str) -> None:
    bus = await BusPublisher.connect(url)
    trade = Envelope(venue="smoke", symbol="TEST-USD", seq=1)
    trade.trade.CopyFrom(Trade(trade_id="t1", price_ticks=100, size_lots=5))
    snapshot = Envelope(venue="smoke", symbol="TEST-USD", seq=2)
    snapshot.snapshot.CopyFrom(Snapshot(bids=[Level(price_ticks=99, size_lots=1)]))

    await bus.publish(trade)
    await bus.publish(snapshot)
    await bus.put_snapshot(snapshot)
    await bus.nc.flush()

    stored = await bus.js.get_last_msg(STREAM, subject_for(trade))
    assert Envelope.FromString(stored.data) == trade, "the stream did not keep the trade"
    kv = await bus.js.key_value(SNAPSHOT_BUCKET)
    entry = await kv.get(snapshot_key("smoke", "TEST-USD"))
    assert entry.value is not None and Envelope.FromString(entry.value) == snapshot

    info = await bus.js.stream_info(STREAM)
    print(f"stream {STREAM}: {info.state.messages} messages; snapshot bucket round-trips")
    await bus.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "nats://localhost:4222"))
