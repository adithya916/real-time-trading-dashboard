"""Run the gateway.

python -m gateway --bus nats://localhost:4222 --port 8080 [--db postgresql://...] [--workers 4]
python -m gateway --demo RECORDING.jsonl.gz kraken:BTC-USD     # no NATS: replay on a loop

--workers N runs N independent gateway processes on one port. Each binds it with SO_REUSEPORT,
so the Linux kernel spreads new connections across them; each keeps its own bus subscription,
book mirrors and 50 ms tick, so nothing is shared and nothing needs locking. One Python
process saturates its event loop near 2,000 viewers (docs/PERFORMANCE.md); N processes use N
cores. Each worker serves its metrics on --metrics-port + its index (Prometheus scrapes them
all); /metrics on the main port answers from whichever worker the connection landed on.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
from pathlib import Path

from gateway.workers import run_workers, serve


def main() -> None:
    parser = argparse.ArgumentParser(description="WebSocket gateway for the market data bus.")
    parser.add_argument("--bus", default="nats://localhost:4222")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", help="TimescaleDB URL for /history/candles")
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("GATEWAY_WORKERS", "1")),
        help="gateway processes sharing the port (Linux; SO_REUSEPORT)",
    )
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=0,
        help="worker i serves /metrics on this port + i (0: only /metrics on the main port)",
    )
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
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.workers > 1 and not hasattr(socket, "SO_REUSEPORT"):
        parser.error("--workers above 1 needs SO_REUSEPORT (Linux)")
    if args.demo:
        recording, feed = Path(args.demo[0]), args.demo[1]
        venue, _, symbols = feed.partition(":")
        if not recording.is_file():
            parser.error(f"no recording at {recording}")
        if not venue or not symbols:
            parser.error(f"expected VENUE:SYMBOL[,SYMBOL...], got {feed!r}")
    if args.workers == 1:
        serve(args, 0)
    else:
        run_workers(args)


if __name__ == "__main__":
    main()
