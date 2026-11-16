"""Check that the analytics service published metrics for the replayed Kraken book.

Run after the Kraken replay, with the analytics container running.
"""

import asyncio
import sys
import time

import nats
from nats.js.errors import NotFoundError

from mdcommon.bus import STREAM, subject
from mdcommon.schema import Envelope


async def main(url: str, timeout_s: float = 20) -> None:
    nc = await nats.connect(url)
    js = nc.jetstream()
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            msg = await js.get_last_msg(STREAM, subject("kraken", "BTC-USD", "analytics"))
            break
        except NotFoundError:
            if time.monotonic() > deadline:
                raise SystemExit("no analytics published for kraken BTC-USD") from None
            await asyncio.sleep(0.5)
    metrics = Envelope.FromString(msg.data).analytics
    assert metrics.mid > 10_000, metrics
    assert 0 < metrics.spread_bps < 50, metrics
    assert -1 <= metrics.imbalance_l1 <= 1, metrics
    print(
        f"analytics: mid {metrics.mid:,.1f}, spread {metrics.spread_bps:.2f} bps, microprice {metrics.microprice:,.2f}"
    )
    await nc.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "nats://localhost:4222"))
