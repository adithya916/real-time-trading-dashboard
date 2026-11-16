import json
import math
from decimal import Decimal
from pathlib import Path

import pytest
from analytics.metrics import SymbolAnalytics
from analytics.service import AnalyticsState

from ingest.adapters.kraken import KrakenAdapter
from ingest.recording import load_frames
from mdcommon.schema import BookReset, Delta, Envelope, Level, LevelUpdate, Side, Snapshot, Trade

FIXTURE = Path(__file__).parents[2] / "ingest" / "tests" / "fixtures" / "kraken_btcusd.jsonl.gz"
SECOND = 1_000_000_000


def envelope(**payload: object) -> Envelope:
    env = Envelope(venue="test", symbol="AAA")
    for field, value in payload.items():
        getattr(env, field).CopyFrom(value)
    return env


def book(bids: list[tuple[int, int]], asks: list[tuple[int, int]]) -> Envelope:
    # price scale 1 (ticks of 0.1), size scale 0
    return envelope(
        snapshot=Snapshot(
            price_scale=1,
            size_scale=0,
            bids=[Level(price_ticks=p, size_lots=q) for p, q in bids],
            asks=[Level(price_ticks=p, size_lots=q) for p, q in asks],
        )
    )


def test_top_of_book_metrics_match_hand_computed_values() -> None:
    metrics = SymbolAnalytics()
    metrics.on_envelope(book(bids=[(1000, 3), (999, 2)], asks=[(1002, 1), (1003, 4)]))
    result = metrics.compute(0)
    assert result is not None

    # bid 100.0 x3, ask 100.2 x1
    assert result.mid == pytest.approx(100.1)
    assert result.microprice == pytest.approx((100.2 * 3 + 100.0 * 1) / 4)  # leans to the ask
    assert result.spread_bps == pytest.approx(0.2 / 100.1 * 10_000)
    assert result.imbalance_l1 == pytest.approx((3 - 1) / 4)
    assert result.imbalance_l5 == pytest.approx((5 - 5) / 10)
    # 10 bps of 100.1 is about 0.1: bids from 100.0 and asks up to 100.2 count
    assert result.depth_10bps_bid == pytest.approx(3)
    assert result.depth_10bps_ask == pytest.approx(1)


def test_deltas_move_the_metrics_and_resets_stop_them() -> None:
    metrics = SymbolAnalytics()
    metrics.on_envelope(book(bids=[(1000, 3)], asks=[(1002, 1)]))
    metrics.on_envelope(
        envelope(
            delta=Delta(
                updates=[
                    LevelUpdate(side=Side.SIDE_ASK, price_ticks=1002, size_lots=0),
                    LevelUpdate(side=Side.SIDE_ASK, price_ticks=1001, size_lots=5),
                ]
            )
        )
    )
    result = metrics.compute(0)
    assert result is not None and result.mid == pytest.approx(100.05)

    metrics.on_envelope(envelope(reset=BookReset()))
    assert metrics.compute(0) is None
    metrics.on_envelope(
        envelope(
            delta=Delta(updates=[LevelUpdate(side=Side.SIDE_BID, price_ticks=1000, size_lots=1)])
        )
    )
    assert metrics.compute(0) is None  # deltas are ignored until the next snapshot


def test_vwap_weights_trades_by_size() -> None:
    metrics = SymbolAnalytics()
    metrics.on_envelope(book(bids=[(1000, 1)], asks=[(1002, 1)]))
    metrics.on_envelope(envelope(trade=Trade(price_ticks=1000, size_lots=1)))
    metrics.on_envelope(envelope(trade=Trade(price_ticks=1010, size_lots=3)))
    result = metrics.compute(0)
    assert result is not None
    assert result.vwap_session == pytest.approx((100.0 * 1 + 101.0 * 3) / 4)


def test_realized_vol_sums_squared_log_returns_inside_each_window() -> None:
    metrics = SymbolAnalytics()
    mids = [1000, 1010, 1000, 1020]  # mid ticks (bid = mid - 1, ask = mid + 1)
    for i, mid in enumerate(mids):
        metrics.on_envelope(book(bids=[(mid - 1, 1)], asks=[(mid + 1, 1)]))
        metrics.sample(i * 5 * SECOND)  # one sample every 5 s
    result = metrics.compute(15 * SECOND)
    assert result is not None

    prices = [m / 10 for m in mids]
    returns = [math.log(b / a) for a, b in zip(prices, prices[1:], strict=False)]
    assert result.realized_vol_60s == pytest.approx(math.sqrt(sum(r * r for r in returns)))
    assert result.realized_vol_10s == pytest.approx(math.sqrt(returns[-1] ** 2 + returns[-2] ** 2))
    assert result.realized_vol_1s == 0.0  # only one sample in the last second
    assert result.twap_60s == pytest.approx(sum(prices) / len(prices))


def test_samples_are_at_most_every_100_ms() -> None:
    metrics = SymbolAnalytics()
    metrics.on_envelope(book(bids=[(1000, 1)], asks=[(1002, 1)]))
    for ns in range(0, 1_000_000_000, 10_000_000):  # every 10 ms for a second
        metrics.sample(ns)
    assert len(metrics.mids) == 10


def test_unchanged_metrics_are_not_published_again() -> None:
    state = AnalyticsState()
    state.on_envelope(book(bids=[(1000, 1)], asks=[(1002, 1)]))
    first = state.tick(0)
    again = state.tick(
        SECOND
    )  # nothing new, but a new sample changes twap/vol only if the mid moved
    assert len(first) == 1 and first[0].seq == 1
    assert again == []


def test_the_kraken_recording_gives_sensible_metrics() -> None:
    adapter = KrakenAdapter(["BTC-USD"])
    state = AnalyticsState()
    published: list[Envelope] = []
    now = 0
    for frame in load_frames(FIXTURE):
        for env in adapter.handle(json.loads(frame, parse_float=Decimal)):
            state.on_envelope(env)
        now += 20_000_000  # pretend frames arrive every 20 ms
        published += state.tick(now)

    assert len(published) > 100
    assert [e.seq for e in published] == list(range(1, len(published) + 1))
    for env in published:
        a = env.analytics
        assert 0 < a.spread_bps < 50
        assert -1 <= a.imbalance_l1 <= 1 and -1 <= a.imbalance_l5 <= 1
        best_bid = a.mid - a.mid * a.spread_bps / 20_000
        best_ask = a.mid + a.mid * a.spread_bps / 20_000
        assert best_bid - 1e-6 <= a.microprice <= best_ask + 1e-6
    last = published[-1].analytics
    assert 10_000 < last.mid < 1_000_000  # BTC in USD
    assert last.vwap_session > 0 and last.realized_vol_60s >= 0
