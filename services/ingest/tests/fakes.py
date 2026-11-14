"""Test doubles for venue connections."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager


class ConnectionDropped(OSError):
    """What a fake socket raises when its script runs out, like a real socket closing."""


class FakeSocket:
    def __init__(self, incoming: Sequence[str]) -> None:
        self._incoming = list(incoming)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if not self._incoming:
            raise ConnectionDropped("fake socket closed")
        return self._incoming.pop(0)


class FakeVenue:
    """Hands out one FakeSocket per connect() call, from a script of sessions."""

    def __init__(self, sessions: Sequence[Sequence[str]]) -> None:
        self._sessions = [list(s) for s in sessions]
        self.urls: list[str] = []
        self.sockets: list[FakeSocket] = []

    @asynccontextmanager
    async def connect(self, url: str) -> AsyncIterator[FakeSocket]:
        self.urls.append(url)
        if not self._sessions:
            raise ConnectionDropped("no more scripted sessions")
        socket = FakeSocket(self._sessions.pop(0))
        self.sockets.append(socket)
        yield socket
