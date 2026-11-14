"""Record a venue's raw frames while an adapter runs, and load them back for tests.

A recording is gzipped JSON lines, one per received frame: {"recv_ns": ..., "frame": "..."}.
Replaying the frames through a fake socket exercises the adapter on real venue data.
"""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import IO

from ingest.adapters.base import Connector, Socket


class _RecordingSocket:
    def __init__(self, inner: Socket, sink: IO[str]) -> None:
        self._inner = inner
        self._sink = sink

    async def send(self, message: str) -> None:
        await self._inner.send(message)

    async def recv(self) -> str | bytes:
        frame = await self._inner.recv()
        text = frame.decode() if isinstance(frame, bytes) else frame
        self._sink.write(json.dumps({"recv_ns": time.time_ns(), "frame": text}) + "\n")
        return frame


class RecordingConnector:
    """Wraps a connector so every frame received on any connection is appended to `path`."""

    def __init__(self, inner: Connector, path: Path) -> None:
        self._inner = inner
        path.parent.mkdir(parents=True, exist_ok=True)
        self._sink: IO[str] = gzip.open(path, "wt", encoding="utf-8")

    @asynccontextmanager
    async def __call__(self, url: str) -> AsyncIterator[Socket]:
        async with self._inner(url) as socket:
            yield _RecordingSocket(socket, self._sink)

    def close(self) -> None:
        self._sink.close()


def load_frames(path: Path) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line)["frame"] for line in f if line.strip()]
