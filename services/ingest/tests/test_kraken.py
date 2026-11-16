import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fakes import FakeVenue

from ingest.adapters.base import AdapterError, FeedAdapter, supervise
from ingest.adapters.kraken import KrakenAdapter, parse_ts_ns
from ingest.recording import load_frames
from mdcommon.schema import Envelope, ResetReason

FIXTURE = Path(__file__).parent / "fixtures" / "kraken_btcusd.jsonl.gz"
FRAMES = load_frames(FIXTURE)  # 45 s of live Kraken BTC/USD, recorded with --record


async def drain(stream: AsyncIterator[Envelope]) -> tuple[list[Envelope], BaseException | None]:
    out: list[Envelope] = []
    try:
        async for envelope in stream:
            out.append(envelope)
    except Exception as exc:
        return out, exc
    return out, None


def replay(
    frames: list[str],
) -> tuple[KrakenAdapter, FakeVenue, list[Envelope], BaseException | None]:
    venue = FakeVenue([frames])
    adapter = KrakenAdapter(["BTC-USD"], connect=venue.connect)
    envelopes, error = asyncio.run(drain(adapter.stream()))
    return adapter, venue, envelopes, error


def corrupt_first_update(frames: list[str]) -> list[str]:
    """Change one quantity in the first book update, so its checksum no longer matches."""
    frames = list(frames)
    for i, frame in enumerate(frames):
        message = json.loads(frame)
        if message.get("channel") == "book" and message.get("type") == "update":
            entry = message["data"][0]
            side = "bids" if entry["bids"] else "asks"
            entry[side][0]["qty"] = 123.456
            frames[i] = json.dumps(message)
            return frames
    raise AssertionError("fixture has no book update")


def kinds(envelopes: list[Envelope]) -> list[str]:
    return [str(e.WhichOneof("payload")) for e in envelopes]


def test_every_checksum_in_the_recording_matches() -> None:
    adapter, _, envelopes, error = replay(FRAMES)

    # The recording just ends, which looks like the socket closing.
    assert isinstance(error, OSError)
    assert "reset" not in kinds(envelopes)
    assert kinds(envelopes)[0] == "snapshot"
    assert kinds(envelopes).count("delta") > 1000
    assert kinds(envelopes).count("trade") > 0
    assert [e.seq for e in envelopes] == list(range(1, len(envelopes) + 1))
    assert not adapter.books["BTC-USD"].is_crossed()


def test_snapshot_is_ten_levels_a_side_with_precision() -> None:
    _, _, envelopes, _ = replay(FRAMES)
    snapshot = envelopes[0].snapshot

    assert (snapshot.price_scale, snapshot.size_scale) == (1, 8)
    assert len(snapshot.bids) == len(snapshot.asks) == 10
    assert snapshot.bids[0].price_ticks < snapshot.asks[0].price_ticks
    assert snapshot.checksum != 0


def test_deltas_carry_venue_timestamps_and_checksums() -> None:
    _, _, envelopes, _ = replay(FRAMES)
    deltas = [e for e in envelopes if e.HasField("delta")]

    assert all(e.ts_exch_ns > 1_600_000_000 * 10**9 for e in deltas)
    assert all(e.delta.updates and e.delta.checksum for e in deltas)


def test_subscribes_to_book_depth_10_and_trades() -> None:
    _, venue, _, _ = replay(FRAMES[:5])
    sent = [json.loads(m)["params"] for m in venue.sockets[0].sent]

    assert venue.urls == ["wss://ws.kraken.com/v2"]
    assert sent == [
        {"channel": "book", "symbol": ["BTC/USD"], "depth": 10},
        {"channel": "trade", "symbol": ["BTC/USD"], "snapshot": False},
    ]


def test_a_wrong_checksum_resets_the_book_and_drops_the_connection() -> None:
    _, _, envelopes, error = replay(corrupt_first_update(FRAMES))

    assert isinstance(error, AdapterError)
    assert "checksum mismatch" in str(error)
    assert kinds(envelopes) == ["snapshot", "reset"]
    assert envelopes[-1].reset.reason == ResetReason.RESET_REASON_CHECKSUM_MISMATCH


def test_supervisor_recovers_with_a_fresh_snapshot() -> None:
    venue = FakeVenue([corrupt_first_update(FRAMES), FRAMES[:50]])
    adapter: FeedAdapter = KrakenAdapter(["BTC-USD"], connect=venue.connect)

    async def no_sleep(_: float) -> None:
        return None

    async def collect() -> list[Envelope]:
        out: list[Envelope] = []
        async for envelope in supervise(adapter, sleep=no_sleep, max_restarts=1):
            out.append(envelope)
        return out

    envelopes = asyncio.run(collect())
    resets = [ResetReason.Name(e.reset.reason) for e in envelopes if e.HasField("reset")]

    assert resets[:2] == ["RESET_REASON_CHECKSUM_MISMATCH", "RESET_REASON_RECONNECT"]
    assert kinds(envelopes)[3] == "snapshot"
    assert len(venue.urls) == 2


def test_a_refused_subscription_is_an_error() -> None:
    refusal = json.dumps(
        {"method": "subscribe", "success": False, "error": "Currency pair not supported"}
    )
    _, _, _, error = replay([refusal])

    assert isinstance(error, AdapterError)
    assert "Currency pair not supported" in str(error)


def test_an_update_before_the_snapshot_is_an_error() -> None:
    update = next(f for f in FRAMES if '"type":"update"' in f and '"channel":"book"' in f)
    _, _, _, error = replay([update])

    assert isinstance(error, AdapterError)
    assert "before its snapshot" in str(error)


def test_unknown_symbols_are_rejected_up_front() -> None:
    with pytest.raises(ValueError, match="no Kraken instrument"):
        KrakenAdapter(["DOGE-EUR"])


def test_timestamps_convert_to_exact_nanoseconds() -> None:
    assert parse_ts_ns("2024-05-01T12:00:00.123456Z") == 1_714_564_800_123_456_000


def test_a_consumer_applying_the_deltas_as_published_keeps_krakens_book() -> None:
    # Kraken drops levels past depth 10 without deletes. The adapter publishes those deletes,
    # so a consumer that just applies every delta, with no truncation of its own, stays exact.
    adapter, _, envelopes, _ = replay(FRAMES)
    consumer: dict[int, dict[int, int]] = {1: {}, 2: {}}
    for envelope in envelopes:
        if envelope.HasField("snapshot"):
            consumer = {
                1: {lv.price_ticks: lv.size_lots for lv in envelope.snapshot.bids},
                2: {lv.price_ticks: lv.size_lots for lv in envelope.snapshot.asks},
            }
        elif envelope.HasField("delta"):
            for update in envelope.delta.updates:
                if update.size_lots:
                    consumer[update.side][update.price_ticks] = update.size_lots
                else:
                    consumer[update.side].pop(update.price_ticks, None)
            assert len(consumer[1]) <= 10 and len(consumer[2]) <= 10
            assert max(consumer[1]) < min(consumer[2]), f"crossed at seq {envelope.seq}"

    book = adapter.books["BTC-USD"]
    assert consumer[1] == book.bids and consumer[2] == book.asks
