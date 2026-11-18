from gateway.hub import Hub

from mdcommon.schema import Envelope, Level, Snapshot, StrategyState

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
    frames = [Envelope.FromString(f) for f in raw if isinstance(f, bytes)]
    assert len(frames) == len(raw)  # no text (ping replies) among them
    kinds = [f.WhichOneof("payload") for f in frames]
    assert kinds == ["snapshot", "strategy"]
    assert frames[1].strategy.fills == 2
    assert hub.mirrors[KEY].synced
    hub.tick(0.05)
    assert viewer.queue.empty()  # nothing new, nothing sent
