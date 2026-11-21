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
import multiprocessing
import os
import signal
import socket
import sys
from pathlib import Path
from types import FrameType

import uvicorn

from gateway.app import create_app
from gateway.history import History
from gateway.hub import Hub

log = logging.getLogger("gateway")
BACKLOG = 4096


def listening_socket(host: str, port: int, reuse_port: bool) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if reuse_port:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # type: ignore[attr-defined]
    sock.bind((host, port))
    sock.listen(BACKLOG)
    return sock


def serve(args: argparse.Namespace, worker: int) -> None:
    """One gateway process: its own app, bus subscription and socket on the shared port."""
    logging.basicConfig(level=logging.INFO, format=f"%(levelname)s w{worker} %(name)s: %(message)s")
    if args.metrics_port:
        from mdcommon.latency import serve_metrics

        serve_metrics(args.metrics_port + worker)
    history = History(args.db)
    if args.demo:
        from gateway.demo import run_demo

        recording, feed = Path(args.demo[0]), args.demo[1]
        venue, _, symbols = feed.partition(":")
        hub = Hub()
        app = create_app(
            hub,
            history=history,
            background=[lambda: run_demo(hub, recording, venue, symbols.split(","), args.speed)],
            worker=worker,
        )
    else:
        app = create_app(bus_url=args.bus, history=history, worker=worker)
    sock = listening_socket(args.host, args.port, reuse_port=args.workers > 1)
    config = uvicorn.Config(app, log_level="info", backlog=BACKLOG)
    uvicorn.Server(config).run(sockets=[sock])


def run_workers(args: argparse.Namespace) -> None:
    """Start the workers and stop them all when this process is told to stop (or one dies)."""
    ctx = multiprocessing.get_context("spawn")
    workers = [
        ctx.Process(target=serve, args=(args, i), name=f"gateway-{i}") for i in range(args.workers)
    ]
    for p in workers:
        p.start()
    log.info(
        "gateway: %d workers on port %d (pids %s)",
        args.workers,
        args.port,
        [p.pid for p in workers],
    )

    def stop(signum: int, frame: FrameType | None) -> None:
        for p in workers:
            if p.is_alive():
                p.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        # A worker that dies takes the rest down, so the container restarts as a whole.
        while all(p.is_alive() for p in workers):
            workers[0].join(timeout=1.0)
    finally:
        stop(signal.SIGTERM, None)
        for p in workers:
            p.join(timeout=10)
    codes = [p.exitcode for p in workers]
    sys.exit(0 if all(c in (0, -signal.SIGTERM) for c in codes) else 1)


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
