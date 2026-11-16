"""Record a venue's raw frames while an adapter runs, and load them back for tests.

A recording is gzipped JSON lines: {"recv_ns": ..., "frame": "..."} per received WebSocket frame,
and {"recv_ns": ..., "rest": "<symbol>", "body": {...}} per REST snapshot (Binance needs those).
Replaying them through fakes exercises an adapter on real venue data.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import IO, Any

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

    def fetcher(
        self, inner: Callable[[str], Awaitable[dict[str, Any]]]
    ) -> Callable[[str], Awaitable[dict[str, Any]]]:
        """Wrap a REST snapshot fetcher so its responses land in the same recording."""

        async def fetch(symbol: str) -> dict[str, Any]:
            body = await inner(symbol)
            line = {"recv_ns": time.time_ns(), "rest": symbol, "body": body}
            self._sink.write(json.dumps(line) + "\n")
            return body

        return fetch

    def close(self) -> None:
        self._sink.close()


def _lines(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_frames(path: Path) -> list[str]:
    return [line["frame"] for line in _lines(path) if "frame" in line]


def load_rest(path: Path) -> list[dict[str, Any]]:
    """REST snapshot bodies in the order they were fetched."""
    return [line["body"] for line in _lines(path) if "rest" in line]


class _ReplaySocket:
    def __init__(self, frames: list[tuple[int, str]], speed: float | None) -> None:
        self._frames = frames
        self._speed = speed
        self._last_ns: int | None = None

    async def send(self, message: str) -> None:
        return None  # subscriptions are already baked into the recording

    async def recv(self) -> str:
        if not self._frames:
            raise ConnectionError("end of recording")
        recv_ns, frame = self._frames.pop(0)
        if self._speed and self._last_ns is not None:
            await asyncio.sleep(max(0, recv_ns - self._last_ns) / 1e9 / self._speed)
        self._last_ns = recv_ns
        return frame


class ReplayConnector:
    """Plays a recording back as one WebSocket session, with its REST snapshots.

    speed=None replays as fast as possible; speed=1.0 keeps the recorded timing. A second
    connection attempt fails, so a supervisor with max_restarts=0 stops at the end.
    """

    def __init__(self, path: Path, speed: float | None = None) -> None:
        lines = _lines(path)
        self._frames = [(line["recv_ns"], line["frame"]) for line in lines if "frame" in line]
        self._rest = [line["body"] for line in lines if "rest" in line]
        self._speed = speed
        self._used = False

    @asynccontextmanager
    async def __call__(self, url: str) -> AsyncIterator[Socket]:
        if self._used:
            raise ConnectionError("the recording has already been replayed")
        self._used = True
        yield _ReplaySocket(list(self._frames), self._speed)

    async def fetch(self, symbol: str) -> dict[str, Any]:
        if not self._rest:
            raise ConnectionError("the recording has no REST snapshot")
        body: dict[str, Any] = self._rest.pop(0) if len(self._rest) > 1 else self._rest[0]
        return body
