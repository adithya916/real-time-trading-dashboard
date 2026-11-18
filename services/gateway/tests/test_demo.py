import asyncio
from pathlib import Path

from analytics.service import AnalyticsState
from fastapi.testclient import TestClient
from gateway.app import create_app
from gateway.demo import replay_forever
from gateway.hub import Hub

from mdcommon.schema import Envelope

FIXTURE = Path(__file__).parents[2] / "ingest" / "tests" / "fixtures" / "kraken_btcusd.jsonl.gz"
KEY = ("kraken", "BTC-USD")
NOW = 1_900_000_000_000_000_000


def play(loops: int) -> tuple[Hub, AnalyticsState, int]:
    hub, analytics = Hub(), AnalyticsState()
    fed = asyncio.run(
        replay_forever(
            hub,
            analytics,
            FIXTURE,
            "kraken",
            ["BTC-USD"],
            speed=None,
            loops=loops,
            clock=lambda: NOW,
        )
    )
    return hub, analytics, fed


def test_one_loop_leaves_an_in_sync_uncrossed_book() -> None:
    hub, _, fed = play(1)
    mirror = hub.mirrors[KEY]
    assert fed > 1000
    assert mirror.synced
    bid, ask = mirror.book.best_bid(), mirror.book.best_ask()
    assert bid is not None and ask is not None and bid[0] < ask[0]


def test_seq_keeps_counting_across_loops_so_the_mirror_stays_in_sync() -> None:
    once, _, _ = play(1)
    twice, _, fed = play(2)
    mirror = twice.mirrors[KEY]
    assert mirror.synced
    # The second loop's seqs follow the first's instead of restarting at 1.
    assert mirror.seq > once.mirrors[KEY].seq
    assert mirror.book.top("bid", 10) == once.mirrors[KEY].book.top("bid", 10)


def test_timestamps_are_restamped_with_the_current_time() -> None:
    hub = Hub()
    seen: list[Envelope] = []
    original = hub.on_envelope

    def record(envelope: Envelope) -> bool:
        seen.append(Envelope.FromString(envelope.SerializeToString()))
        return original(envelope)

    hub.on_envelope = record  # type: ignore[method-assign]
    asyncio.run(
        replay_forever(
            hub,
            AnalyticsState(),
            FIXTURE,
            "kraken",
            ["BTC-USD"],
            speed=None,
            loops=1,
            clock=lambda: NOW,
        )
    )
    trades = [e for e in seen if e.HasField("trade")]
    assert trades
    assert all(e.ts_exch_ns == NOW and e.ts_recv_ns == NOW for e in trades)
    assert not any(e.HasField("reset") for e in seen)


def test_analytics_computed_in_process_reach_the_mirror() -> None:
    hub, analytics, _ = play(1)
    for envelope in analytics.tick(NOW):
        hub.on_envelope(envelope)
    assert hub.mirrors[KEY].analytics is not None


def test_a_viewer_gets_the_replayed_book_over_websocket() -> None:
    hub, _, _ = play(1)
    with TestClient(create_app(hub)) as client:
        assert client.get("/symbols").json() == [{"venue": "kraken", "symbol": "BTC-USD"}]
        with client.websocket_connect("/ws/kraken/BTC-USD") as ws:
            first = Envelope.FromString(ws.receive_bytes())
    assert first.HasField("snapshot")
    assert first.seq == hub.mirrors[KEY].seq
