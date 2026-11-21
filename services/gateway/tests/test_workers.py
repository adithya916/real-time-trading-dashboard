"""--workers: several gateway processes on one port. Linux only (SO_REUSEPORT), so CI runs it.

Runs the real command in demo mode (no NATS: each worker replays the recording itself), checks
that connections reach more than one worker, that a viewer on any of them gets the book, and
that stopping the parent stops every worker.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from mdcommon.wire import decode_batch

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "SO_REUSEPORT"), reason="SO_REUSEPORT is Linux/BSD only"
)
FIXTURE = Path(__file__).parents[2] / "ingest" / "tests" / "fixtures" / "kraken_btcusd.jsonl.gz"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def health(port: int) -> dict[str, object]:
    # A fresh connection each time, so the kernel picks a worker each time.
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
        body: dict[str, object] = json.loads(r.read())
        return body


@pytest.fixture
def gateway() -> Iterator[tuple[subprocess.Popen[bytes], int]]:
    port = free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gateway",
            "--workers",
            "2",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--demo",
            str(FIXTURE),
            "kraken:BTC-USD",
            "--speed",
            "20",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while True:
        try:
            health(port)
            break
        except OSError:
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                pytest.fail("the gateway never answered /health")
            time.sleep(0.2)
    yield proc, port
    if proc.poll() is None:
        proc.kill()


def test_connections_spread_across_workers_and_each_serves_the_book(
    gateway: tuple[subprocess.Popen[bytes], int],
) -> None:
    import asyncio

    import websockets

    proc, port = gateway
    workers = {health(port)["worker"] for _ in range(60)}
    assert workers == {0, 1}, f"only reached workers {workers}"

    async def first_frames(n: int) -> list[str]:
        kinds = []
        for _ in range(n):
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws/kraken/BTC-USD") as ws:
                message = await asyncio.wait_for(ws.recv(), 10)
                assert isinstance(message, bytes)
                kinds.append(str(decode_batch(message)[0].WhichOneof("payload")))
        return kinds

    assert set(asyncio.run(first_frames(10))) == {"snapshot"}


def test_stopping_the_parent_stops_every_worker(
    gateway: tuple[subprocess.Popen[bytes], int],
) -> None:
    proc, port = gateway
    pids = {health(port)["pid"] for _ in range(60)}
    assert len(pids) == 2
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) == 0
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(int(str(pid)), 0)
