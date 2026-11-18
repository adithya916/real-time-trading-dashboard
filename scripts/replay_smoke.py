"""Read a replayed feed back from the JetStream stream and check its books against a golden file.

Run after `python -m recorder.replay` published the feed:

    python scripts/replay_smoke.py nats://localhost:4222 binance BTC-USDT services/ingest/tests/golden/binance_btcusdt.json

The stream keeps what was published, so this sees exactly what every consumer saw: the books
rebuilt from it must hash the same as the original live session's, checkpoint for checkpoint.
"""

import asyncio
import sys
from pathlib import Path

import nats
from nats.errors import TimeoutError as NatsTimeout

from ingest.book.golden import book_hashes, first_difference, load_golden
from mdcommon.bus import subject
from mdcommon.schema import Envelope


async def main(url: str, venue: str, symbol: str, golden: Path) -> None:
    nc = await nats.connect(url)
    js = nc.jetstream()
    # Book and trade subjects together, in stream (publish) order.
    sub = await js.subscribe(f"{subject(venue, symbol, 'book')[:-4]}*", ordered_consumer=True)
    envelopes: list[Envelope] = []
    while True:
        try:
            msg = await sub.next_msg(timeout=3)
        except NatsTimeout:
            break
        envelopes.append(Envelope.FromString(msg.data))
    await nc.close()
    got = book_hashes(envelopes)
    want = load_golden(golden)
    difference = first_difference(got, want)
    if difference:
        raise SystemExit(f"replay of {venue} {symbol}: {difference}")
    print(
        f"replay: {len(envelopes)} envelopes, {got['events']} book events, "
        f"{len(got['hashes'])} golden checkpoints match"
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])))
