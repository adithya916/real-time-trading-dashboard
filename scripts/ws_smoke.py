"""Connect to a running stream and check that real, correctly-labelled ticks arrive.

Usage: python scripts/ws_smoke.py ws://localhost:8000/ws/stream/AAPL
"""

import asyncio
import json
import sys
import time

import websockets

MESSAGES = 200
TIMEOUT_S = 20


async def main(url: str) -> None:
    expected = url.rstrip("/").rsplit("/", 1)[-1].upper()
    async with websockets.connect(url, max_size=None) as ws:
        start = time.perf_counter()
        for _ in range(MESSAGES):
            message = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT_S))
            if message["symbol"] != expected:
                raise SystemExit(f"got {message['symbol']} on the {expected} stream")
        elapsed = time.perf_counter() - start
    print(f"{MESSAGES} {expected} messages in {elapsed:.2f}s ({MESSAGES / elapsed:,.0f}/s)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
