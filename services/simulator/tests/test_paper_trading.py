"""End to end in one process: the paper trader quoting on the simulated venue.

The venue runs as fast as possible; every 50 envelopes the trader sees the market, decides,
and its requests go through the order desk exactly as they would over NATS, with fills
delivered from the desk's outbox. Nothing is faked below the transport.
"""

import asyncio
from contextlib import aclosing

from papertrader.strategy import Params
from papertrader.trader import PaperTrader
from simulator.adapter import SimulatorAdapter
from simulator.orders import OrderDesk

START = 1_800_000_000 * 10**9
SYMBOL = "BTC-SIM"


def trade(params: Params, actions: int = 20_000) -> tuple[PaperTrader, list[dict[str, object]]]:
    adapter = SimulatorAdapter(
        [SYMBOL], seed=21, speed=None, max_actions=actions, clock=lambda: START
    )
    desk = OrderDesk(adapter, clock=lambda: START)
    trader = PaperTrader(SYMBOL, params=params)
    fills: list[dict[str, object]] = []

    def deliver() -> None:
        for _, body in desk.outbox:
            fills.append(body)
            trader.on_fill(body)
        desk.outbox.clear()

    async def run() -> None:
        n = 0
        async with aclosing(adapter.stream()) as stream:
            async for envelope in stream:
                trader.on_envelope(envelope)
                n += 1
                if n % 50:
                    continue
                deliver()
                for request in trader.decide():
                    reply = desk.handle(SYMBOL, request)
                    deliver()
                    trader.on_reply(request, reply)
                    if request["op"] == "new":
                        best_bid, best_ask = trader.book.best_bid(), trader.book.best_ask()
                        assert best_bid and best_ask
                        # Post-only against the book the trader saw.
                        if request["side"] == "bid":
                            assert request["price"] < best_ask[0]
                        else:
                            assert request["price"] > best_bid[0]
        deliver()

    asyncio.run(run())
    return trader, fills


def test_the_market_maker_trades_both_ways_within_its_limits() -> None:
    params = Params()
    trader, fills = trade(params)
    sides = {f["side"] for f in fills}
    assert len(fills) >= 20 and sides == {"bid", "ask"}, (len(fills), sides)
    assert all(not f["taker"] for f in fills), "post-only quotes should only ever be makers"
    # Position stays within the limit plus at most one quote that filled on the way there.
    assert abs(trader.position.lots) <= params.max_position_lots + params.quote_size_lots
    assert trader.rejected == 0


def test_reported_pnl_matches_cash_flow_from_the_fills() -> None:
    trader, fills = trade(Params(), actions=8000)
    cash = 0
    lots = 0
    for f in fills:
        size, price = int(f["size"]), int(f["price"])  # type: ignore[call-overload]
        cash += -price * size if f["side"] == "bid" else price * size
        lots += size if f["side"] == "bid" else -size
    mid = trader.mid()
    assert mid is not None and lots == trader.position.lots
    realized, unrealized = trader.pnl()
    equity = (cash + lots * mid) / 10 ** (trader.price_scale + trader.size_scale)
    assert abs((realized + unrealized) - equity) < 1e-6


def test_a_tight_loss_limit_halts_quoting() -> None:
    trader, _ = trade(
        Params(max_loss=0.01, half_spread_ticks=1, skew_ticks_per_lot=0), actions=20_000
    )
    assert trader.halted, "a 1-cent loss limit should trip within 20k actions"
    assert trader.live == {"bid": None, "ask": None}
