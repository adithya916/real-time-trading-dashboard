import argparse
import asyncio
from collections import Counter
from pathlib import Path

import pytest

from ingest.adapters.binance import BinanceAdapter
from ingest.adapters.kraken import KrakenAdapter
from ingest.recording import ReplayConnector
from ingest.service import parse_feed, publish, refresh_snapshots
from mdcommon.bus import subject_for
from mdcommon.schema import Envelope

FIXTURES = Path(__file__).parent / "fixtures"


class FakeBus:
    def __init__(self) -> None:
        self.published: list[Envelope] = []
        self.snapshots: dict[str, Envelope] = {}

    async def publish(self, envelope: Envelope) -> None:
        self.published.append(envelope)

    async def put_snapshot(self, envelope: Envelope) -> None:
        self.snapshots[f"{envelope.venue}.{envelope.symbol}"] = envelope


def replay_kraken(bus: FakeBus) -> tuple[KrakenAdapter, int]:
    adapter = KrakenAdapter(
        ["BTC-USD"], connect=ReplayConnector(FIXTURES / "kraken_btcusd.jsonl.gz")
    )
    count = asyncio.run(publish(adapter, bus, max_restarts=0))
    return adapter, count


def test_every_envelope_reaches_its_subject_stamped_with_bus_time() -> None:
    bus = FakeBus()
    _, count = replay_kraken(bus)
    subjects = Counter(subject_for(e) for e in bus.published)

    assert count == len(bus.published)
    assert subjects["md.kraken.BTC-USD.book"] > 1000
    assert subjects["md.kraken.BTC-USD.trade"] > 0
    assert all(e.ts_bus_ns >= e.ts_recv_ns > 0 for e in bus.published)


def test_the_replay_ends_with_a_reset_and_the_stream_stays_gapless() -> None:
    bus = FakeBus()
    replay_kraken(bus)

    assert bus.published[-1].HasField("reset")
    assert [e.seq for e in bus.published] == list(range(1, len(bus.published) + 1))


def test_the_bucket_holds_the_final_book_at_the_last_book_seq() -> None:
    bus = FakeBus()
    adapter, _ = replay_kraken(bus)
    stored = bus.snapshots["kraken.BTC-USD"]

    # The final refresh runs after the session ended, so the bucket matches the adapter's book.
    assert stored.seq == adapter.last_seq("BTC-USD")
    assert stored.snapshot == adapter.book_snapshot("BTC-USD")
    assert stored.snapshot.checksum != 0


def test_starting_from_a_bucket_snapshot_and_applying_later_seqs_rebuilds_the_book() -> None:
    adapter = KrakenAdapter(
        ["BTC-USD"], connect=ReplayConnector(FIXTURES / "kraken_btcusd.jsonl.gz")
    )
    bus = FakeBus()

    async def run_half() -> Envelope:
        # Take a bucket snapshot partway through, as a late consumer would find it.
        snapshot: Envelope | None = None
        count = 0
        from ingest.adapters.base import supervise

        async for envelope in supervise(adapter, max_restarts=0):
            await bus.publish(envelope)
            count += 1
            if count == 1000:
                await refresh_snapshots(adapter, bus)
                snapshot = bus.snapshots["kraken.BTC-USD"]
        assert snapshot is not None
        return snapshot

    snapshot = asyncio.run(run_half())
    book = {"bid": {}, "ask": {}}  # type: dict[str, dict[int, int]]
    for level in snapshot.snapshot.bids:
        book["bid"][level.price_ticks] = level.size_lots
    for level in snapshot.snapshot.asks:
        book["ask"][level.price_ticks] = level.size_lots
    for envelope in bus.published:
        if envelope.seq > snapshot.seq and envelope.HasField("delta"):
            for update in envelope.delta.updates:
                side = "bid" if update.side == 1 else "ask"
                if update.size_lots:
                    book[side][update.price_ticks] = update.size_lots
                else:
                    book[side].pop(update.price_ticks, None)
            # Kraken keeps 10 levels; deeper ones drop off without a delete.
            for side, reverse in (("bid", True), ("ask", False)):
                book[side] = dict(sorted(book[side].items(), reverse=reverse)[:10])

    final = adapter.book_snapshot("BTC-USD")
    assert final is not None
    assert book["bid"] == {lv.price_ticks: lv.size_lots for lv in final.bids}
    assert book["ask"] == {lv.price_ticks: lv.size_lots for lv in final.asks}


def test_binance_books_reach_the_bucket_only_once_synced() -> None:
    connector = ReplayConnector(FIXTURES / "binance_btcusdt.jsonl.gz")
    adapter = BinanceAdapter(["BTC-USDT"], connect=connector, fetch=connector.fetch)
    assert asyncio.run(refresh_snapshots(adapter, FakeBus())) == 0

    bus = FakeBus()
    asyncio.run(publish(adapter, bus, max_restarts=0))
    assert bus.snapshots["binance.BTC-USDT"].snapshot.bids


def test_feed_specs_parse_venue_and_symbols() -> None:
    assert parse_feed("kraken:BTC-USD,ETH-USD") == ("kraken", ["BTC-USD", "ETH-USD"])
    with pytest.raises(argparse.ArgumentTypeError):
        parse_feed("kraken")


def test_publishing_records_hops_and_counts_resets() -> None:
    from mdcommon.latency import LATENCY
    from mdcommon.metrics import ENVELOPES, RESETS

    before = ENVELOPES.labels("ingest", "kraken", "delta")._value.get()
    resets = RESETS.labels("ingest", "kraken", "reconnect")._value.get()
    LATENCY.hops.pop("recv_to_bus", None)
    adapter = KrakenAdapter(
        ["BTC-USD"], connect=ReplayConnector(FIXTURES / "kraken_btcusd.jsonl.gz")
    )
    asyncio.run(publish(adapter, FakeBus(), max_restarts=0))
    assert ENVELOPES.labels("ingest", "kraken", "delta")._value.get() > before
    assert RESETS.labels("ingest", "kraken", "reconnect")._value.get() == resets + 1
    assert LATENCY.summary()["recv_to_bus"]["count"] > 1000
