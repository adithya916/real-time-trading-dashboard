"""The C++ L2Book must agree with PyBook, the reference, after every single event.

Real data: the Kraken and Binance recordings are replayed through the adapters, and each
envelope is applied to both books. For Kraken, the C++ checksum must also equal the
checksum Kraken itself sent with every message.

Random data: Hypothesis generates update streams, including deletes of missing levels,
crossing updates and truncation, and the two books are compared after each step.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from bookcore import L2Book, Side, kraken_checksum
from ingest.adapters.binance import BinanceAdapter
from ingest.adapters.kraken import DEPTH as KRAKEN_DEPTH
from ingest.adapters.kraken import KrakenAdapter
from ingest.book.pybook import BookSide, PyBook
from ingest.recording import load_frames, load_rest
from mdcommon.schema import Envelope

FIXTURES = Path(__file__).parents[3] / "services" / "ingest" / "tests" / "fixtures"
TOP = 25
SIDES: dict[BookSide, Side] = {"bid": Side.BID, "ask": Side.ASK}
WIRE_SIDE: dict[int, BookSide] = {1: "bid", 2: "ask"}


def assert_same(py: PyBook, cpp: L2Book, where: str) -> None:
    for name, side in SIDES.items():
        assert cpp.top(side, TOP) == py.top(name, TOP), f"{name}s differ {where}"
    assert len(cpp) == len(py), f"level counts differ {where}"
    assert cpp.crossed() == py.is_crossed(), f"crossed() differs {where}"


def apply_envelope(envelope: Envelope, py: PyBook, cpp: L2Book) -> None:
    if envelope.HasField("snapshot"):
        py.clear()
        cpp.clear()
        for name, levels in (("bid", envelope.snapshot.bids), ("ask", envelope.snapshot.asks)):
            for level in levels:
                py.apply(name, level.price_ticks, level.size_lots)  # type: ignore[arg-type]
                cpp.apply(SIDES[name], level.price_ticks, level.size_lots)  # type: ignore[index]
    elif envelope.HasField("delta"):
        for update in envelope.delta.updates:
            name = WIRE_SIDE[update.side]
            py.apply(name, update.price_ticks, update.size_lots)
            cpp.apply(SIDES[name], update.price_ticks, update.size_lots)


def kraken_envelopes() -> Iterator[Envelope]:
    adapter = KrakenAdapter(["BTC-USD"])
    for frame in load_frames(FIXTURES / "kraken_btcusd.jsonl.gz"):
        yield from adapter.handle(json.loads(frame, parse_float=Decimal))


def binance_envelopes() -> list[Envelope]:
    path = FIXTURES / "binance_btcusdt.jsonl.gz"
    frames, snapshot = load_frames(path), load_rest(path)[0]

    class Socket:
        async def send(self, message: str) -> None: ...

        async def recv(self) -> str:
            if not frames:
                raise ConnectionError("end of recording")
            return frames.pop(0)

    @asynccontextmanager
    async def connect(url: str) -> AsyncIterator[Socket]:
        yield Socket()

    async def fetch(symbol: str) -> dict[str, Any]:
        return snapshot

    async def collect() -> list[Envelope]:
        out: list[Envelope] = []
        adapter = BinanceAdapter(["BTC-USDT"], connect=connect, fetch=fetch)
        try:
            async for envelope in adapter.stream():
                out.append(envelope)
        except ConnectionError:
            pass
        return out

    return asyncio.run(collect())


def test_kraken_recording_gives_identical_books_and_krakens_own_checksums() -> None:
    py, cpp = PyBook(), L2Book()
    checked = 0
    for envelope in kraken_envelopes():
        if not (envelope.HasField("snapshot") or envelope.HasField("delta")):
            continue
        apply_envelope(envelope, py, cpp)
        py.truncate(KRAKEN_DEPTH)
        cpp.truncate(KRAKEN_DEPTH)
        assert_same(py, cpp, f"after seq {envelope.seq}")
        sent = envelope.snapshot.checksum or envelope.delta.checksum
        assert kraken_checksum(cpp) == sent, f"checksum differs from Kraken's at seq {envelope.seq}"
        checked += 1
    assert checked > 1000


def test_binance_recording_gives_identical_books() -> None:
    py, cpp = PyBook(), L2Book()
    envelopes = [e for e in binance_envelopes() if e.HasField("snapshot") or e.HasField("delta")]
    for envelope in envelopes:
        apply_envelope(envelope, py, cpp)
        assert_same(py, cpp, f"after seq {envelope.seq}")
    assert len(envelopes) > 100
    assert len(cpp) > 1500  # the 1,000-level snapshot per side, give or take updates


updates = st.lists(
    st.one_of(
        st.tuples(
            st.sampled_from(["bid", "ask"]),
            st.integers(min_value=1, max_value=300),
            st.integers(min_value=0, max_value=50),  # 0 deletes, often a missing level
        ),
        st.tuples(st.just("truncate"), st.integers(min_value=0, max_value=30), st.just(0)),
    ),
    max_size=400,
)


@settings(max_examples=300, deadline=None)
@given(updates)
def test_random_update_streams_give_identical_books(steps: list[tuple[str, int, int]]) -> None:
    py, cpp = PyBook(), L2Book()
    for i, (action, value, size) in enumerate(steps):
        if action == "truncate":
            py.truncate(value)
            cpp.truncate(value)
        else:
            py.apply(action, value, size)  # type: ignore[arg-type]
            cpp.apply(SIDES[action], value, size)  # type: ignore[index]
        assert_same(py, cpp, f"at step {i}")
