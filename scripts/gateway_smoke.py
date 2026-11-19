"""Connect to the gateway and check a book arrives as a snapshot.

Run with the gateway container running: after the Kraken replay, or for the simulated venue.
"""

import asyncio
import sys

import websockets

from mdcommon.wire import decode_batch


async def main(url: str) -> None:
    async with websockets.connect(url) as ws:
        first = decode_batch(await asyncio.wait_for(ws.recv(), 20))[0]
    assert first.HasField("snapshot"), first.WhichOneof("payload")
    # Kraken publishes 10 levels a side, the simulator 25.
    assert len(first.snapshot.bids) >= 10 and len(first.snapshot.asks) >= 10, first.snapshot
    assert first.snapshot.bids[0].price_ticks < first.snapshot.asks[0].price_ticks
    print(f"gateway: snapshot at seq {first.seq}, best bid {first.snapshot.bids[0].price_ticks}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8080/ws/kraken/BTC-USD"))
