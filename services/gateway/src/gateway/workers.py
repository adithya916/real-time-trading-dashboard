"""Gateway worker processes: several independent gateways on one port (see __main__.py).

They live in a module of their own because multiprocessing's "spawn" start method imports the
target function by name in each child; a function defined in the package's __main__ can't be
found that way.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
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
        # Missing on Windows (where --workers 1 never asks for it).
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # type: ignore[attr-defined,unused-ignore]
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
