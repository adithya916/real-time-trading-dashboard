"""The contract every venue adapter follows, and the supervisor that keeps one running."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import ClassVar, Protocol, TypeAlias

from mdcommon.schema import BookReset, Delta, Envelope, ResetReason, Snapshot, Trade

log = logging.getLogger(__name__)

Payload: TypeAlias = Snapshot | Delta | Trade | BookReset
_PAYLOAD_FIELD: dict[type[Payload], str] = {
    Snapshot: "snapshot",
    Delta: "delta",
    Trade: "trade",
    BookReset: "reset",
}


class Socket(Protocol):
    """The part of a WebSocket connection an adapter uses. The websockets client and the test fake both fit."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


Connector = Callable[[str], AbstractAsyncContextManager[Socket]]


def websocket_connect(url: str) -> AbstractAsyncContextManager[Socket]:
    from websockets.asyncio.client import connect

    # Venue books arrive as large frames; keepalive pings detect a silently dead socket.
    return connect(url, max_size=None, ping_interval=20, ping_timeout=20)


class AdapterError(Exception):
    """The venue sent something the adapter can't continue from; reconnect and resync."""


class FeedAdapter(ABC):
    """One venue. Subclasses implement stream(); everything else is shared.

    Every envelope gets a gapless seq per symbol, so a consumer that sees a jump knows it lost data.
    """

    venue: ClassVar[str]

    def __init__(
        self,
        symbols: Sequence[str],
        *,
        connect: Connector = websocket_connect,
        clock: Callable[[], int] = time.time_ns,
    ) -> None:
        if not symbols:
            raise ValueError("an adapter needs at least one symbol")
        self.symbols = list(symbols)
        self._connect = connect
        self._clock = clock
        self._seq: dict[str, int] = dict.fromkeys(self.symbols, 0)

    @abstractmethod
    def stream(self) -> AsyncGenerator[Envelope, None]:
        """Run one connection: connect, subscribe, and yield envelopes until it ends.

        Return or raise when the connection drops or the book can't be trusted any more;
        the supervisor reconnects and every symbol starts again from a fresh snapshot.
        """

    def wrap(self, symbol: str, payload: Payload, ts_exch_ns: int = 0) -> Envelope:
        """Put a payload in an envelope with the next seq and the receive time."""
        self._seq[symbol] += 1
        envelope = Envelope(
            venue=self.venue,
            symbol=symbol,
            seq=self._seq[symbol],
            ts_exch_ns=ts_exch_ns,
            ts_recv_ns=self._clock(),
        )
        getattr(envelope, _PAYLOAD_FIELD[type(payload)]).CopyFrom(payload)
        return envelope

    def book_snapshot(self, symbol: str) -> Snapshot | None:
        """The current book as a Snapshot while it is in sync, otherwise None.

        It reflects every envelope up to last_seq(symbol), so a consumer can start from it
        and apply the stream from the next seq.
        """
        return None

    def last_seq(self, symbol: str) -> int:
        return self._seq[symbol]

    def reset(
        self,
        symbol: str,
        reason: ResetReason,
        expected_seq: int = 0,
        received_seq: int = 0,
    ) -> Envelope:
        return self.wrap(
            symbol,
            BookReset(reason=reason, expected_seq=expected_seq, received_seq=received_seq),
        )


async def supervise(
    adapter: FeedAdapter,
    *,
    min_backoff_s: float = 0.1,
    max_backoff_s: float = 5.0,
    max_session_s: float = 23 * 3600,
    max_restarts: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> AsyncGenerator[Envelope, None]:
    """Run adapter.stream() forever, reconnecting with exponential backoff.

    Venues drop day-old sockets (Binance at 24 h), so sessions are also rotated before that.
    After every session each symbol gets a BookReset, telling consumers to drop their book
    and wait for the new snapshot. The backoff resets once a session delivers data.
    """
    backoff = min_backoff_s
    restarts = 0
    while True:
        started = monotonic()
        stream = adapter.stream()
        try:
            async for envelope in stream:
                backoff = min_backoff_s
                yield envelope
                if monotonic() - started >= max_session_s:
                    log.info("%s: rotating the connection", adapter.venue)
                    break
            else:
                log.warning("%s: the stream ended", adapter.venue)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            expected = isinstance(exc, OSError | AdapterError | asyncio.TimeoutError)
            log.warning(
                "%s: connection lost (%r); reconnecting in %.1f s",
                adapter.venue,
                exc,
                backoff,
                exc_info=not expected,
            )
        finally:
            await stream.aclose()

        for symbol in adapter.symbols:
            yield adapter.reset(symbol, ResetReason.RESET_REASON_RECONNECT)
        restarts += 1
        if max_restarts is not None and restarts > max_restarts:
            return
        await sleep(backoff)
        backoff = min(backoff * 2, max_backoff_s)
