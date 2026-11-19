"""Check the paper trader's state reaches a dashboard viewer through the gateway, and it trades.

Run with the simulator, the paper trader and the gateway running:

    python scripts/strategy_smoke.py ws://localhost:8080/ws/sim/BTC-SIM
"""

import asyncio
import sys
import time

import websockets

from mdcommon.wire import decode_batch


async def main(url: str, timeout_s: float = 90) -> None:
    deadline = time.monotonic() + timeout_s
    seen = None
    async with websockets.connect(url) as ws:
        while time.monotonic() < deadline:
            frame = await asyncio.wait_for(ws.recv(), timeout_s)
            if isinstance(frame, str):
                continue
            for envelope in decode_batch(frame):
                if envelope.HasField("strategy"):
                    seen = envelope.strategy
            if seen is not None and seen.fills > 0:
                break
    if seen is None:
        raise SystemExit("no strategy state reached the viewer")
    if seen.fills == 0:
        raise SystemExit(f"the paper trader never filled: {seen}")
    print(
        f"strategy {seen.name}: {seen.fills} fills, position {seen.position_lots} lots, "
        f"PnL {seen.realized_pnl + seen.unrealized_pnl:+.2f}, quoting "
        f"{seen.bid_price_ticks} / {seen.ask_price_ticks}"
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8080/ws/sim/BTC-SIM"))
