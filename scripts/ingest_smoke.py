"""Check what a replayed Kraken recording left on the bus.

Run after: python -m ingest.service --replay services/ingest/tests/fixtures/kraken_btcusd.jsonl.gz kraken:BTC-USD
"""

import asyncio
import sys

import nats

from mdcommon.bus import SNAPSHOT_BUCKET, STREAM, snapshot_key
from mdcommon.schema import Envelope


async def main(url: str) -> None:
    nc = await nats.connect(url)
    js = nc.jetstream()
    info = await js.stream_info(STREAM, subjects_filter="md.kraken.>")
    subjects = info.state.subjects or {}
    book, trades = (
        subjects.get("md.kraken.BTC-USD.book", 0),
        subjects.get("md.kraken.BTC-USD.trade", 0),
    )
    assert book > 1000, f"only {book} book messages on the stream"
    assert trades > 0, "no trades on the stream"

    entry = await (await js.key_value(SNAPSHOT_BUCKET)).get(snapshot_key("kraken", "BTC-USD"))
    assert entry.value is not None
    snapshot = Envelope.FromString(entry.value)
    assert snapshot.seq > 1000 and len(snapshot.snapshot.bids) == 10
    print(f"stream: {book} book and {trades} trade messages; bucket snapshot at seq {snapshot.seq}")
    await nc.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "nats://localhost:4222"))
