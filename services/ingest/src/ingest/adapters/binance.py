"""Binance spot: diff-depth stream synced to a REST snapshot, and trades.

Docs: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
("How to manage a local order book correctly").

Binance has no checksum. Instead every depth event carries the range of update ids it covers,
[U, u], and the local book is only right if those ranges chain without a hole:

1. Take the first depth event, then fetch a REST snapshot (lastUpdateId). If the snapshot is
   older than that event, fetch again.
2. Drop events entirely before the snapshot (u <= lastUpdateId).
3. The first event applied must straddle it: U <= lastUpdateId + 1 <= u.
4. After that, every event must start where the previous one ended: U == previous u + 1.

Any hole means the book is wrong: emit BookReset(SEQUENCE_GAP) and sync again from step 1
on the same connection.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ingest.adapters.base import AdapterError, FeedAdapter
from ingest.book.pybook import BookSide, PyBook
from mdcommon.schema import (
    Aggressor,
    Delta,
    Envelope,
    Level,
    LevelUpdate,
    ResetReason,
    Side,
    Snapshot,
    Trade,
    to_ticks,
)

STREAM_URL = "wss://stream.binance.com:9443/stream"
REST_URL = "https://api.binance.com/api/v3/depth"
SNAPSHOT_LIMIT = 1000
MAX_SNAPSHOT_ATTEMPTS = 5

Fetcher = Callable[[str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Instrument:
    venue_symbol: str
    price_scale: int
    size_scale: int


# Scales from the PRICE_FILTER tickSize and LOT_SIZE stepSize in /api/v3/exchangeInfo.
INSTRUMENTS = {
    "BTC-USDT": Instrument("BTCUSDT", price_scale=2, size_scale=5),
    "ETH-USDT": Instrument("ETHUSDT", price_scale=2, size_scale=4),
}

_SIDES: tuple[tuple[str, BookSide, Side], ...] = (
    ("b", "bid", Side.SIDE_BID),
    ("a", "ask", Side.SIDE_ASK),
)


async def fetch_depth_snapshot(venue_symbol: str) -> dict[str, Any]:
    url = f"{REST_URL}?symbol={venue_symbol}&limit={SNAPSHOT_LIMIT}"

    def get() -> dict[str, Any]:
        with urllib.request.urlopen(url, timeout=10) as response:
            body: dict[str, Any] = json.loads(response.read())
            return body

    return await asyncio.to_thread(get)


@dataclass
class _SymbolState:
    book: PyBook = field(default_factory=PyBook)
    # None until a snapshot is loaded; then the id the next applied event must straddle.
    snapshot_id: int | None = None
    # The last applied event's u, once the book is live.
    last_u: int | None = None


class BinanceAdapter(FeedAdapter):
    venue = "binance"
    default_fetch = staticmethod(fetch_depth_snapshot)

    def __init__(self, symbols: list[str], *, fetch: Fetcher | None = None, **kwargs: Any) -> None:
        unknown = [s for s in symbols if s not in INSTRUMENTS]
        if unknown:
            raise ValueError(f"no Binance instrument for {unknown}; known: {sorted(INSTRUMENTS)}")
        super().__init__(symbols, **kwargs)
        self._fetch = fetch or fetch_depth_snapshot
        self._by_venue_symbol = {INSTRUMENTS[s].venue_symbol: s for s in self.symbols}
        self.states: dict[str, _SymbolState] = {}

    def url(self) -> str:
        streams = []
        for symbol in self.symbols:
            name = INSTRUMENTS[symbol].venue_symbol.lower()
            streams += [f"{name}@depth@100ms", f"{name}@trade"]
        return f"{STREAM_URL}?streams={'/'.join(streams)}"

    async def stream(self) -> AsyncGenerator[Envelope, None]:
        self.states = {s: _SymbolState() for s in self.symbols}
        async with self._connect(self.url()) as socket:
            while True:
                message = json.loads(await socket.recv())
                data = message.get("data", message)
                event = data.get("e")
                if event == "depthUpdate":
                    async for envelope in self._depth(data):
                        yield envelope
                elif event == "trade":
                    yield self._trade(data)

    async def _depth(self, event: dict[str, Any]) -> AsyncGenerator[Envelope, None]:
        symbol = self._by_venue_symbol[event["s"]]
        state = self.states[symbol]
        first, last = int(event["U"]), int(event["u"])

        if state.last_u is not None:
            if first != state.last_u + 1:
                yield self.reset(
                    symbol,
                    ResetReason.RESET_REASON_SEQUENCE_GAP,
                    expected_seq=state.last_u + 1,
                    received_seq=first,
                )
                self.states[symbol] = state = _SymbolState()
            else:
                yield self._apply(symbol, state, event)
                return

        if state.snapshot_id is None:
            yield await self._load_snapshot(symbol, state, first)

        assert state.snapshot_id is not None
        if last <= state.snapshot_id:
            return  # entirely before the snapshot
        if first > state.snapshot_id + 1:
            # The stream moved past the snapshot without us seeing the event that joins them.
            yield self.reset(
                symbol,
                ResetReason.RESET_REASON_SEQUENCE_GAP,
                expected_seq=state.snapshot_id + 1,
                received_seq=first,
            )
            self.states[symbol] = state = _SymbolState()
            yield await self._load_snapshot(symbol, state, first)
            assert state.snapshot_id is not None
            if last <= state.snapshot_id:
                return
        yield self._apply(symbol, state, event)

    async def _load_snapshot(self, symbol: str, state: _SymbolState, first_u: int) -> Envelope:
        inst = INSTRUMENTS[symbol]
        for _ in range(MAX_SNAPSHOT_ATTEMPTS):
            body = await self._fetch(inst.venue_symbol)
            if int(body["lastUpdateId"]) + 1 >= first_u:
                break
            await asyncio.sleep(0.2)  # the snapshot predates the stream; Binance says fetch again
        else:
            raise AdapterError(f"{symbol}: no snapshot newer than update {first_u}")

        state.book.clear()
        for key, side in (("bids", "bid"), ("asks", "ask")):
            for price, qty in body[key]:
                state.book.apply(
                    side,  # type: ignore[arg-type]
                    to_ticks(price, inst.price_scale),
                    to_ticks(qty, inst.size_scale),
                )
        state.snapshot_id = int(body["lastUpdateId"])
        state.last_u = None
        return self.wrap(
            symbol,
            Snapshot(
                price_scale=inst.price_scale,
                size_scale=inst.size_scale,
                bids=[Level(price_ticks=p, size_lots=q) for p, q in state.book.top("bid")],
                asks=[Level(price_ticks=p, size_lots=q) for p, q in state.book.top("ask")],
            ),
        )

    def _apply(self, symbol: str, state: _SymbolState, event: dict[str, Any]) -> Envelope:
        inst = INSTRUMENTS[symbol]
        updates: list[LevelUpdate] = []
        for key, side, wire_side in _SIDES:
            for price, qty in event[key]:
                price_ticks = to_ticks(price, inst.price_scale)
                size_lots = to_ticks(qty, inst.size_scale)
                state.book.apply(side, price_ticks, size_lots)
                updates.append(
                    LevelUpdate(side=wire_side, price_ticks=price_ticks, size_lots=size_lots)
                )
        state.last_u = int(event["u"])
        return self.wrap(symbol, Delta(updates=updates), int(event["E"]) * 1_000_000)

    def _trade(self, event: dict[str, Any]) -> Envelope:
        symbol = self._by_venue_symbol[event["s"]]
        inst = INSTRUMENTS[symbol]
        trade = Trade(
            trade_id=str(event["t"]),
            price_ticks=to_ticks(event["p"], inst.price_scale),
            size_lots=to_ticks(event["q"], inst.size_scale),
            # m: the buyer was the resting maker, so the seller crossed the spread.
            aggressor=Aggressor.AGGRESSOR_SELL if event["m"] else Aggressor.AGGRESSOR_BUY,
        )
        return self.wrap(symbol, trade, int(event["T"]) * 1_000_000)
