"""Run the gateway.

python -m gateway --bus nats://localhost:4222 --port 8080
python -m gateway --demo RECORDING.jsonl.gz kraken:BTC-USD     # no NATS: replay on a loop
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from gateway.app import create_app
from gateway.hub import Hub


def main() -> None:
    parser = argparse.ArgumentParser(description="WebSocket gateway for the market data bus.")
    parser.add_argument("--bus", default="nats://localhost:4222")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--demo",
        nargs=2,
        metavar=("RECORDING", "VENUE:SYMBOLS"),
        help="replay a recording on a loop instead of reading the bus, e.g. "
        "services/ingest/tests/fixtures/kraken_btcusd.jsonl.gz kraken:BTC-USD",
    )
    parser.add_argument(
        "--speed", type=float, default=1.0, help="demo replay speed (1.0 = recorded pace)"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if not args.demo:
        uvicorn.run(create_app(bus_url=args.bus), host=args.host, port=args.port)
        return

    from gateway.demo import run_demo

    recording, feed = Path(args.demo[0]), args.demo[1]
    venue, _, symbols = feed.partition(":")
    if not recording.is_file():
        parser.error(f"no recording at {recording}")
    if not venue or not symbols:
        parser.error(f"expected VENUE:SYMBOL[,SYMBOL...], got {feed!r}")
    hub = Hub()
    app = create_app(
        hub,
        background=[
            lambda: run_demo(hub, recording, venue, symbols.split(","), args.speed),
        ],
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
