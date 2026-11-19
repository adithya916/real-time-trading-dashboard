import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from gateway.app import create_app
from gateway.hub import CLOSE_FRAME, QUEUE_MESSAGES, SLOW_LIMIT_S, Hub, Viewer
from gateway.mirror import Mirror

from ingest.adapters.kraken import KrakenAdapter
from ingest.recording import load_frames
from mdcommon.schema import BookReset, Delta, Envelope, Level, LevelUpdate, Side, Snapshot, Trade
from mdcommon.wire import decode_batch, split_batch

FIXTURE = Path(__file__).parents[2] / "ingest" / "tests" / "fixtures" / "kraken_btcusd.jsonl.gz"
KEY = ("kraken", "BTC-USD")


def kraken_envelopes() -> tuple[KrakenAdapter, list[Envelope]]:
    adapter = KrakenAdapter(["BTC-USD"])
    out: list[Envelope] = []
    for frame in load_frames(FIXTURE):
        out += adapter.handle(json.loads(frame, parse_float=Decimal))
    return adapter, out


def env(seq: int, **payload: object) -> Envelope:
    e = Envelope(venue="kraken", symbol="BTC-USD", seq=seq)
    for field, value in payload.items():
        getattr(e, field).CopyFrom(value)
    return e


def snapshot(seq: int, bid: int = 100, ask: int = 101) -> Envelope:
    return env(
        seq,
        snapshot=Snapshot(
            bids=[Level(price_ticks=bid, size_lots=1)], asks=[Level(price_ticks=ask, size_lots=1)]
        ),
    )


def delta(seq: int, price: int, size: int, side: Side = Side.SIDE_BID) -> Envelope:
    return env(
        seq, delta=Delta(updates=[LevelUpdate(side=side, price_ticks=price, size_lots=size)])
    )


def apply_frames(book: dict[int, dict[int, int]], frames: list[bytes]) -> None:
    """What a browser does with the frames it receives."""
    for raw in frames:
        e = Envelope.FromString(raw)
        if e.HasField("snapshot"):
            book.clear()
            book[1] = {lv.price_ticks: lv.size_lots for lv in e.snapshot.bids}
            book[2] = {lv.price_ticks: lv.size_lots for lv in e.snapshot.asks}
        elif e.HasField("delta"):
            for u in e.delta.updates:
                if u.size_lots:
                    book[u.side][u.price_ticks] = u.size_lots
                else:
                    book[u.side].pop(u.price_ticks, None)


def drain(viewer: Viewer) -> list[bytes]:
    """Every envelope queued for the viewer, unpacked from the per-tick messages."""
    frames = []
    while not viewer.queue.empty():
        message = viewer.queue.get_nowait()
        assert isinstance(message, bytes)  # no pings in these tests
        frames += split_batch(message)
    return frames


def receive(ws: Any) -> list[Envelope]:
    return decode_batch(ws.receive_bytes())


# --- mirror ---------------------------------------------------------------------------------


def test_a_seq_gap_stops_the_mirror_until_a_bucket_snapshot_closes_it() -> None:
    mirror = Mirror(*KEY)
    mirror.on_envelope(snapshot(10))
    assert mirror.on_envelope(delta(11, 99, 5)) is False
    assert mirror.on_envelope(delta(13, 98, 7)) is True  # 12 never arrived
    assert not mirror.synced
    mirror.on_envelope(delta(14, 97, 2))  # buffered meanwhile

    # The bucket holds the book at seq 12; 13 and 14 come from the buffer.
    bucket = snapshot(12)
    bucket.snapshot.bids.add(price_ticks=99, size_lots=5)
    mirror.load_snapshot(bucket)

    assert mirror.synced and mirror.seq == 14
    assert mirror.book.bids == {100: 1, 99: 5, 98: 7, 97: 2}


def test_a_bucket_snapshot_that_still_leaves_a_hole_keeps_waiting() -> None:
    mirror = Mirror(*KEY)
    mirror.on_envelope(snapshot(10))
    mirror.on_envelope(delta(13, 98, 7))
    mirror.on_envelope(delta(14, 97, 2))
    mirror.load_snapshot(snapshot(11))  # 12 is still missing

    assert not mirror.synced
    assert [e.seq for e in mirror.buffer] == [13, 14]


def test_a_reset_asks_for_the_bucket_and_ignores_older_snapshots() -> None:
    mirror = Mirror(*KEY)
    mirror.on_envelope(snapshot(10))
    assert mirror.on_envelope(env(11, reset=BookReset())) is True
    assert not mirror.synced

    mirror.load_snapshot(snapshot(9, bid=50))  # stale: from before the reset
    assert not mirror.synced
    mirror.load_snapshot(snapshot(11, bid=60))  # written after it
    assert mirror.synced and mirror.book.bids == {60: 1}


def test_envelopes_already_in_the_snapshot_are_skipped() -> None:
    mirror = Mirror(*KEY)
    mirror.on_envelope(snapshot(20))
    assert mirror.on_envelope(delta(19, 50, 1)) is False
    assert 50 not in mirror.book.bids


def test_the_kraken_recording_mirrors_exactly() -> None:
    adapter, envelopes = kraken_envelopes()
    mirror = Mirror(*KEY)
    for e in envelopes:
        assert mirror.on_envelope(e) is False
    assert mirror.book.bids == adapter.books["BTC-USD"].bids
    assert mirror.book.asks == adapter.books["BTC-USD"].asks


# --- hub ------------------------------------------------------------------------------------


def test_a_fast_viewer_rebuilds_the_exact_book_from_one_delta_per_tick() -> None:
    adapter, envelopes = kraken_envelopes()
    hub = Hub()
    viewer = hub.add_viewer(KEY)
    browser: dict[int, dict[int, int]] = {}
    book_frames = ticks = 0
    for i, e in enumerate(envelopes):
        hub.on_envelope(e)
        if i % 20 == 19:  # ~20 envelopes per 50 ms tick
            hub.tick(now=ticks * 0.05)
            ticks += 1
            frames = drain(viewer)
            book_frames += sum(
                1
                for f in frames
                if Envelope.FromString(f).WhichOneof("payload") in {"snapshot", "delta"}
            )
            apply_frames(browser, frames)
    hub.tick(now=ticks * 0.05)
    apply_frames(browser, drain(viewer))

    assert browser[1] == adapter.books["BTC-USD"].bids
    assert browser[2] == adapter.books["BTC-USD"].asks
    assert book_frames <= ticks  # at most one book frame per tick: 20 a second
    assert book_frames < len(envelopes) / 10  # versus one per update without conflation
    assert viewer.sent["trade"] == sum(1 for e in envelopes if e.HasField("trade"))  # lossless


def test_a_viewer_that_never_reads_gets_snapshots_then_is_disconnected() -> None:
    hub = Hub()
    fast, slow = hub.add_viewer(KEY), hub.add_viewer(KEY)
    hub.on_envelope(snapshot(1))
    seq, now = 1, 0.0
    while not slow.closed:
        # A trade every tick: a viewer that reads each tick keeps up, one that never reads
        # fills its queue after QUEUE_MESSAGES ticks.
        seq += 1
        hub.on_envelope(env(seq, trade=Trade(trade_id=str(seq))))
        hub.tick(now)
        drain(fast)
        now += 0.05
        assert now < QUEUE_MESSAGES * 0.05 + SLOW_LIMIT_S + 1, "the slow viewer was never closed"

    assert slow.queue.get_nowait() == CLOSE_FRAME
    assert hub.disconnected_slow == 1
    assert not fast.closed and fast.behind_since is None


def test_a_slow_viewer_that_catches_up_gets_a_fresh_snapshot() -> None:
    hub = Hub()
    viewer = hub.add_viewer(KEY)
    hub.on_envelope(snapshot(1))
    seq = 1
    for tick in range(QUEUE_MESSAGES + 1):  # it never reads: the queue overflows
        seq += 1
        hub.on_envelope(env(seq, trade=Trade(trade_id=str(seq))))
        hub.tick(tick * 0.05)
    overflowed_at = QUEUE_MESSAGES * 0.05
    assert viewer.needs_snapshot and viewer.behind_since == overflowed_at

    hub.on_envelope(delta(seq + 1, 99, 4))
    hub.tick(overflowed_at + 0.05)
    frames = [Envelope.FromString(f) for f in drain(viewer)]  # it reads this time
    assert frames[0].HasField("snapshot") and frames[0].snapshot.bids[0].price_ticks == 100
    assert not viewer.needs_snapshot

    hub.tick(overflowed_at + 0.10)  # it read everything it was given: caught up
    assert viewer.behind_since is None


# --- websocket ------------------------------------------------------------------------------


def test_the_websocket_sends_a_snapshot_then_conflated_deltas() -> None:
    hub = Hub()
    hub.on_envelope(snapshot(1))
    app = create_app(hub)
    with TestClient(app) as client:
        assert client.get("/symbols").json() == [{"venue": "kraken", "symbol": "BTC-USD"}]
        with client.websocket_connect("/ws/kraken/BTC-USD") as ws:
            (first,) = receive(ws)
            assert first.HasField("snapshot") and first.seq == 1
            hub.on_envelope(delta(2, 99, 3))
            hub.on_envelope(delta(3, 99, 4))  # same level twice before the tick
            (second,) = receive(ws)
            assert second.HasField("delta") and second.seq == 3
            assert [(u.price_ticks, u.size_lots) for u in second.delta.updates] == [(99, 4)]


def test_a_text_ping_comes_back_after_the_queued_frames() -> None:
    hub = Hub()
    hub.on_envelope(snapshot(1))
    with (
        TestClient(create_app(hub)) as client,
        client.websocket_connect("/ws/kraken/BTC-USD") as ws,
    ):
        assert receive(ws)[0].HasField("snapshot")
        ws.send_text("ping:12345.5")
        assert ws.receive_text() == "ping:12345.5"
