"""Golden replay: each recording, replayed through ingest, rebuilds exactly the pinned books.

To update the golden files after an intended change to the books, run
    UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py
and commit the diff together with the change that explains it.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from ingest.adapters.binance import BinanceAdapter
from ingest.adapters.kraken import KrakenAdapter
from ingest.book.golden import book_hashes, first_difference, load_golden, save_golden
from ingest.recording import ReplayConnector
from ingest.service import publish
from mdcommon.schema import Envelope

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
GOLDEN = TESTS / "golden"


class Collect:
    def __init__(self) -> None:
        self.envelopes: list[Envelope] = []

    async def publish(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)

    async def put_snapshot(self, envelope: Envelope) -> None:
        return None


def replay(name: str) -> list[Envelope]:
    replayer = ReplayConnector(FIXTURES / f"{name}.jsonl.gz")
    adapter: Any
    if name.startswith("binance"):
        adapter = BinanceAdapter(["BTC-USDT"], connect=replayer, fetch=replayer.fetch)
    else:
        adapter = KrakenAdapter(["BTC-USD"], connect=replayer)
    bus = Collect()
    asyncio.run(publish(adapter, bus, max_restarts=0))
    return bus.envelopes


@pytest.mark.parametrize("name", ["kraken_btcusd", "binance_btcusdt"])
def test_replay_rebuilds_the_golden_books(name: str) -> None:
    got = book_hashes(replay(name))
    path = GOLDEN / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        save_golden(path, got)
    want = load_golden(path)
    assert (
        len(got["hashes"]) >= 3
    ), "a golden file should pin the book mid-stream, not only at the end"
    assert first_difference(got, want) is None, first_difference(got, want)


def test_replaying_twice_gives_identical_hashes() -> None:
    assert book_hashes(replay("kraken_btcusd")) == book_hashes(replay("kraken_btcusd"))


def test_a_one_lot_difference_in_the_book_is_caught() -> None:
    envelopes = replay("kraken_btcusd")
    want = book_hashes(envelopes)
    # L2 updates set absolute sizes, so a wrong size heals at the level's next update. Change
    # the last update, which the final book keeps.
    deltas = [e for e in envelopes if e.HasField("delta") and e.delta.updates]
    deltas[-1].delta.updates[-1].size_lots += 1
    message = first_difference(book_hashes(envelopes), want)
    assert message is not None and "differs at event" in message, message
