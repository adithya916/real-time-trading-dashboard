from gateway.hub import Hub

from mdcommon.schema import Envelope, Level, Snapshot, StrategyState
from mdcommon.wire import decode_batch

KEY = ("sim", "BTC-SIM")


def envelope(seq: int, **payload: object) -> Envelope:
    e = Envelope(venue="sim", symbol="BTC-SIM", seq=seq)
    for name, value in payload.items():
        getattr(e, name).CopyFrom(value)
    return e


def test_the_latest_strategy_state_reaches_viewers_once_per_tick() -> None:
    hub = Hub()
    viewer = hub.add_viewer(KEY)
    book = Snapshot(
        bids=[Level(price_ticks=1, size_lots=1)], asks=[Level(price_ticks=2, size_lots=1)]
    )
    hub.on_envelope(envelope(1, snapshot=book))
    # Its own seq, like analytics: it must not disturb the book's gap detection.
    assert not hub.on_envelope(envelope(7, strategy=StrategyState(name="mm", fills=1)))
    assert not hub.on_envelope(envelope(8, strategy=StrategyState(name="mm", fills=2)))
    hub.tick(0.0)
    raw = [viewer.queue.get_nowait() for _ in range(viewer.queue.qsize())]
    assert len(raw) == 1 and isinstance(raw[0], bytes)  # one message for the whole tick
    frames = decode_batch(raw[0])
    kinds = [f.WhichOneof("payload") for f in frames]
    assert kinds == ["snapshot", "strategy"]
    assert frames[1].strategy.fills == 2
    assert hub.mirrors[KEY].synced
    hub.tick(0.05)
    assert viewer.queue.empty()  # nothing new, nothing sent


def test_metrics_show_viewers_frames_and_the_conflation_hold() -> None:
    from fastapi.testclient import TestClient
    from gateway.app import create_app

    hub = Hub()
    book = Snapshot(
        bids=[Level(price_ticks=1, size_lots=1)], asks=[Level(price_ticks=2, size_lots=1)]
    )
    with TestClient(create_app(hub)) as client:
        hub.on_envelope(envelope(1, snapshot=book))
        with client.websocket_connect("/ws/sim/BTC-SIM") as ws:
            delta = Envelope(venue="sim", symbol="BTC-SIM", seq=2)
            delta.delta.updates.add(side=1, price_ticks=1, size_lots=5)
            hub.on_envelope(delta)
            first = decode_batch(ws.receive_bytes())[0]
            text = client.get("/metrics").text
    assert first.ts_gw_send_ns > 0  # frames built by the gateway carry its send time
    assert 'md_gateway_viewers{symbol="BTC-SIM",venue="sim"} ' in text  # global across tests
    assert 'md_gateway_frames_total{kind="snapshot"}' in text
    assert 'md_latency_ms{hop="gateway_hold",quantile="0.99"}' in text
