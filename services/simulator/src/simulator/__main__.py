"""Run the simulated venue: publish it to the bus as venue "sim" and take orders (orders.py).

python -m simulator --bus nats://localhost:4222 BTC-SIM ETH-SIM [--seed 1] [--speed 1]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

from ingest.service import publish
from simulator.adapter import INSTRUMENTS, SimulatorAdapter
from simulator.orders import OrderDesk, serve

log = logging.getLogger(__name__)


async def run(symbols: list[str], bus_url: str, seed: int, speed: float) -> None:
    from mdcommon.bus import BusPublisher

    bus = await BusPublisher.connect(bus_url)
    adapter = SimulatorAdapter(symbols, seed=seed, speed=speed)
    pump = await serve(OrderDesk(adapter, time.time_ns), bus.nc)
    try:
        await publish(adapter, bus)
    finally:
        pump.cancel()
        await bus.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a simulated venue to the NATS bus.")
    parser.add_argument("symbols", nargs="+", choices=sorted(INSTRUMENTS))
    parser.add_argument("--bus", default="nats://localhost:4222")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--speed", type=float, default=1.0, help="1 = real time")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.symbols, args.bus, args.seed, args.speed))


if __name__ == "__main__":
    main()
