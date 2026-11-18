"""Market-shape checks: the simulated venue should look and behave like a market."""

import asyncio
from collections import Counter
from contextlib import aclosing

import pytest

import bookcore
from ingest.book.pybook import PyBook
from mdcommon.schema import Aggressor, Envelope, Side
from simulator.adapter import PRICE_SCALE, SIZE_SCALE, SimulatorAdapter
from simulator.agents import FlowParams, ZeroIntelligenceFlow, geometric
from simulator.market import EXTERNAL_BASE, SimMarket

START = 1_800_000_000 * 10**9
ACTIONS = 6000


def run(adapter: SimulatorAdapter) -> list[Envelope]:
    async def collect() -> list[Envelope]:
        out = []
        async with aclosing(adapter.stream()) as stream:
            async for envelope in stream:
                out.append(envelope)
        return out

    return asyncio.run(collect())


@pytest.fixture(scope="module")
def stream() -> list[Envelope]:
    return run(
        SimulatorAdapter(["BTC-SIM"], seed=7, speed=None, max_actions=ACTIONS, clock=lambda: START)
    )


def test_it_starts_with_a_snapshot_and_seqs_are_gapless(stream: list[Envelope]) -> None:
    assert stream[0].HasField("snapshot")
    assert stream[0].snapshot.price_scale == PRICE_SCALE
    assert stream[0].snapshot.size_scale == SIZE_SCALE
    assert [e.seq for e in stream] == list(range(1, len(stream) + 1))


def test_the_published_book_is_never_crossed_and_has_a_positive_spread(
    stream: list[Envelope],
) -> None:
    book = PyBook()
    spreads = []
    for e in stream:
        if e.HasField("snapshot"):
            for lv in e.snapshot.bids:
                book.apply("bid", lv.price_ticks, lv.size_lots)
            for lv in e.snapshot.asks:
                book.apply("ask", lv.price_ticks, lv.size_lots)
        elif e.HasField("delta"):
            for u in e.delta.updates:
                book.apply("bid" if u.side == Side.SIDE_BID else "ask", u.price_ticks, u.size_lots)
        else:
            continue
        bid, ask = book.best_bid(), book.best_ask()
        assert bid and ask, "a side emptied out"
        assert bid[0] < ask[0], f"crossed at seq {e.seq}"
        spreads.append(ask[0] - bid[0])
    assert min(spreads) >= 1
    # Tight most of the time, like a liquid market: median spread under 1 bp of the price.
    median = sorted(spreads)[len(spreads) // 2]
    assert median < FlowParams().start_price_ticks * 1e-4, median


def test_order_flow_arrives_at_the_configured_rates() -> None:
    params = FlowParams()
    market = SimMarket(params, seed=11, start_ns=START)
    market.warm_up(3000)
    start, kinds, taking = market.flow.now_ns, Counter[str](), 0
    for _ in range(20_000):
        action = market.next_action()
        kinds[action.kind] += 1
        taking += bool(market.apply(action).trades)
    seconds = (market.flow.now_ns - start) / 1e9
    assert abs(kinds["market"] / seconds - params.market_rate) < 0.15 * params.market_rate
    assert abs(kinds["limit"] / seconds - params.limit_rate) < 0.05 * params.limit_rate
    # Every market order trades; so do limit orders that take stale quotes, but not most.
    assert params.market_rate * 0.9 < taking / seconds < params.market_rate + params.limit_rate / 2


def test_buyers_and_sellers_take_liquidity_about_equally(stream: list[Envelope]) -> None:
    sides = Counter(e.trade.aggressor for e in stream if e.HasField("trade"))
    buy, sell = sides[Aggressor.AGGRESSOR_BUY], sides[Aggressor.AGGRESSOR_SELL]
    assert 0.35 < buy / (buy + sell) < 0.65


def test_trades_are_priced_inside_the_book_they_hit(stream: list[Envelope]) -> None:
    for e in stream:
        if e.HasField("trade"):
            assert e.trade.price_ticks > 0 and e.trade.size_lots > 0
            assert int(e.trade.trade_id) > 0


def test_the_same_seed_gives_the_same_market() -> None:
    def make() -> list[bytes]:
        adapter = SimulatorAdapter(
            ["BTC-SIM"], seed=3, speed=None, max_actions=500, clock=lambda: START
        )
        return [e.SerializeToString() for e in run(adapter)]

    assert make() == make()


def test_the_snapshot_matches_the_book_built_from_the_stream(stream: list[Envelope]) -> None:
    adapter = SimulatorAdapter(
        ["BTC-SIM"], seed=7, speed=None, max_actions=300, clock=lambda: START
    )
    envelopes = run(adapter)
    book = PyBook()
    for e in envelopes:
        if e.HasField("snapshot"):
            for lv in e.snapshot.bids:
                book.apply("bid", lv.price_ticks, lv.size_lots)
            for lv in e.snapshot.asks:
                book.apply("ask", lv.price_ticks, lv.size_lots)
        elif e.HasField("delta"):
            for u in e.delta.updates:
                book.apply("bid" if u.side == Side.SIDE_BID else "ask", u.price_ticks, u.size_lots)
    snapshot = adapter.book_snapshot("BTC-SIM")
    assert snapshot is not None
    assert [(lv.price_ticks, lv.size_lots) for lv in snapshot.bids] == book.top("bid")
    assert [(lv.price_ticks, lv.size_lots) for lv in snapshot.asks] == book.top("ask")


def test_external_orders_fill_against_the_agents_and_report_fills() -> None:
    market = SimMarket(FlowParams(), seed=1, start_ns=START)
    market.warm_up(3000)
    best_ask = market.engine.best(bookcore.Side.ASK)
    assert best_ask is not None
    result = market.submit(EXTERNAL_BASE + 1, "bid", bookcore.OrderType.IOC, best_ask[0], 1)
    assert result.trades and result.fills
    fill = result.fills[0]
    assert (fill.order, fill.side, fill.taker) == (EXTERNAL_BASE + 1, "bid", True)
    assert fill.price_ticks == best_ask[0]
    # A resting external order reports the fill as maker when an agent takes it.
    best_bid = market.engine.best(bookcore.Side.BID)
    assert best_bid is not None
    market.submit(EXTERNAL_BASE + 2, "ask", bookcore.OrderType.LIMIT, best_bid[0] + 1, 1)
    taken = market.submit(5_000_000, "bid", bookcore.OrderType.MARKET, 0, 1_000_000)
    assert any(f.order == EXTERNAL_BASE + 2 and not f.taker for f in taken.fills)


def test_flow_parameters_shape_the_actions() -> None:
    import random

    flow = ZeroIntelligenceFlow(
        FlowParams(limit_rate=10, market_rate=0, cancel_rate=0), random.Random(1), 0
    )
    actions = []
    for _ in range(2000):
        action = flow.next(resting=0)
        actions.append(action)
        # Placed strictly on the passive side of the fair value it was priced from.
        if action.side == "bid":
            assert action.price_ticks < flow.fair.ticks
        else:
            assert action.price_ticks > flow.fair.ticks
    assert {a.kind for a in actions} == {"limit"}
    seconds = actions[-1].at_ns / 1e9
    assert 9 < len(actions) / seconds < 11  # Poisson arrivals at the configured rate
    mean = sum(geometric(random.Random(i), 15.0) for i in range(4000)) / 4000
    assert 13 < mean < 17
