"""Golden replay through Parquet: record a session, replay it, get the same books.

The recordings go through the venue adapters (as ingest runs them), into the Parquet recorder,
and back out through recorder.replay. Every book must hash exactly as the golden files that
ingest's own replay is checked against.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ingest.adapters.binance import BinanceAdapter
from ingest.adapters.kraken import KrakenAdapter
from ingest.book.golden import book_hashes, first_difference, load_golden
from ingest.recording import ReplayConnector
from ingest.service import publish
from mdcommon.clock import ReplayClock
from mdcommon.schema import Envelope, Trade
from recorder.replay import recorded, replay
from recorder.writer import ParquetRecorder

INGEST_TESTS = Path(__file__).parents[2] / "ingest" / "tests"
FEEDS = {
    "kraken_btcusd": ("kraken", "BTC-USD"),
    "binance_btcusdt": ("binance", "BTC-USDT"),
}


class Bus:
    def __init__(self) -> None:
        self.published: list[Envelope] = []
        self.snapshots: list[Envelope] = []

    async def publish(self, envelope: Envelope) -> None:
        self.published.append(envelope)

    async def put_snapshot(self, envelope: Envelope) -> None:
        self.snapshots.append(envelope)


def record(name: str, root: Path) -> list[Envelope]:
    """Run the fixture through its adapter into Parquet under root; return what was recorded."""
    venue, symbol = FEEDS[name]
    replayer = ReplayConnector(INGEST_TESTS / "fixtures" / f"{name}.jsonl.gz")
    clock = replayer.clock.now_ns
    adapter: Any = (
        BinanceAdapter([symbol], connect=replayer, fetch=replayer.fetch, clock=clock)
        if venue == "binance"
        else KrakenAdapter([symbol], connect=replayer, clock=clock)
    )
    bus = Bus()
    asyncio.run(publish(adapter, bus, max_restarts=0))
    with ParquetRecorder(root) as recorder:
        for envelope in bus.published:
            recorder.add(envelope)
    return bus.published


@pytest.mark.parametrize("name", list(FEEDS))
def test_a_parquet_replay_rebuilds_the_golden_books(name: str, tmp_path: Path) -> None:
    venue, symbol = FEEDS[name]
    original = record(name, tmp_path)
    bus = Bus()
    count = asyncio.run(
        replay(recorded(tmp_path, [(venue, [symbol])]), bus, ReplayClock(None), wall_ns=lambda: 7)
    )
    assert count == len(original)
    got = book_hashes(bus.published)
    want = load_golden(INGEST_TESTS / "golden" / f"{name}.json")
    assert first_difference(got, want) is None, first_difference(got, want)
    # Byte for byte, apart from the bus timestamp the replay stamps anew.
    for before, after in zip(original, bus.published, strict=True):
        before.ts_bus_ns = after.ts_bus_ns = 0
        assert before.SerializeToString() == after.SerializeToString()


def test_the_replay_keeps_the_snapshot_bucket_current(tmp_path: Path) -> None:
    record("kraken_btcusd", tmp_path)
    bus = Bus()
    envelopes = list(recorded(tmp_path, [("kraken", ["BTC-USD"])]))
    asyncio.run(replay(envelopes, bus, ReplayClock(None), snapshot_every_s=0.0))
    # Each refresh is tagged with the seq of the last envelope its book includes.
    last_book_seq = max(e.seq for e in envelopes if not e.HasField("analytics"))
    in_sync = [s for s in bus.snapshots if s.snapshot.bids]
    assert in_sync and all(s.seq <= last_book_seq for s in in_sync)
    assert max(s.seq for s in in_sync) >= last_book_seq - 1


def trade(venue: str, symbol: str, seq: int, recv_ns: int) -> Envelope:
    e = Envelope(venue=venue, symbol=symbol, seq=seq, ts_recv_ns=recv_ns)
    e.trade.CopyFrom(Trade(trade_id=str(seq), price_ticks=1, size_lots=1))
    return e


def test_feeds_are_merged_by_receive_time(tmp_path: Path) -> None:
    hour = 1_763_200_000 * 10**9
    with ParquetRecorder(tmp_path) as recorder:
        for i in range(3):
            recorder.add(trade("kraken", "BTC-USD", i + 1, hour + i * 10))
            recorder.add(trade("kraken", "ETH-USD", i + 1, hour + i * 10 + 5))
    merged = list(recorded(tmp_path, [("kraken", ["BTC-USD", "ETH-USD"])]))
    assert [(e.symbol, e.seq) for e in merged] == [
        ("BTC-USD", 1),
        ("ETH-USD", 1),
        ("BTC-USD", 2),
        ("ETH-USD", 2),
        ("BTC-USD", 3),
        ("ETH-USD", 3),
    ]


def test_speed_paces_the_replay_on_receive_times(tmp_path: Path) -> None:
    second = 10**9
    start = 1_763_200_000 * second
    with ParquetRecorder(tmp_path) as recorder:
        for i in range(5):
            recorder.add(trade("kraken", "BTC-USD", i + 1, start + i * second))
    slept: list[float] = []
    now = [0]

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += int(seconds * 1e9)

    clock = ReplayClock(4.0, wall_ns=lambda: now[0], sleep=sleep)
    asyncio.run(replay(recorded(tmp_path, [("kraken", ["BTC-USD"])]), Bus(), clock))
    assert slept == pytest.approx([0.25] * 4)
