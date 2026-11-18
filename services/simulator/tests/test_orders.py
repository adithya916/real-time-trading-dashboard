import asyncio
from contextlib import aclosing
from typing import Any

import bookcore
from simulator.adapter import SimulatorAdapter
from simulator.agents import FlowParams
from simulator.market import SimMarket
from simulator.orders import OrderDesk, fills_subject

START = 1_800_000_000 * 10**9
SYMBOL = "BTC-SIM"


def desk_with_book() -> tuple[OrderDesk, SimulatorAdapter]:
    adapter = SimulatorAdapter([SYMBOL], seed=5, speed=None, max_actions=2000, clock=lambda: START)
    market = SimMarket(FlowParams(), seed=5, start_ns=START)
    market.warm_up(3000)
    adapter.markets = {SYMBOL: market}
    return OrderDesk(adapter, clock=lambda: START), adapter


def best(adapter: SimulatorAdapter, side: bookcore.Side) -> int:
    level = adapter.markets[SYMBOL].engine.best(side)
    assert level is not None
    return level[0]


def new(desk: OrderDesk, id_: int, side: str, type_: str, price: int, size: int) -> dict[str, Any]:
    return desk.handle(
        SYMBOL,
        {
            "op": "new",
            "account": "mm",
            "id": id_,
            "side": side,
            "type": type_,
            "price": price,
            "size": size,
        },
    )


def test_an_ioc_at_the_best_ask_fills_as_taker_and_nothing_rests() -> None:
    desk, adapter = desk_with_book()
    ask = best(adapter, bookcore.Side.ASK)
    reply = new(desk, 1, "bid", "ioc", ask, 1)
    assert reply == {"ok": True, "resting": 0}
    assert desk.outbox == [
        (
            fills_subject(SYMBOL, "mm"),
            {"id": 1, "side": "bid", "price": ask, "size": 1, "taker": True, "ts": START},
        )
    ]


def test_a_passive_limit_rests_then_fills_as_maker_when_agents_take_it() -> None:
    adapter = SimulatorAdapter([SYMBOL], seed=5, speed=None, max_actions=3000, clock=lambda: START)
    desk = OrderDesk(adapter, clock=lambda: START)
    replies: list[dict[str, Any]] = []

    async def run_venue() -> None:
        async with aclosing(adapter.stream()) as stream:
            async for envelope in stream:
                if envelope.HasField("snapshot"):  # trading: improve the bid so sellers hit us
                    bid = best(adapter, bookcore.Side.BID) + 1
                    replies.append(new(desk, 7, "bid", "limit", bid, 2))

    asyncio.run(run_venue())
    assert replies == [{"ok": True, "resting": 2}]
    fills = [body for _, body in desk.outbox]
    assert fills, "agents never sold into our bid"
    assert all(f["id"] == 7 and f["side"] == "bid" and not f["taker"] for f in fills)
    assert sum(f["size"] for f in fills) == 2  # filled completely, never more


def test_cancel_takes_a_resting_order_off_and_only_once() -> None:
    desk, adapter = desk_with_book()
    price = best(adapter, bookcore.Side.BID) - 50
    new(desk, 3, "bid", "limit", price, 5)
    assert desk.handle(SYMBOL, {"op": "cancel", "account": "mm", "id": 3}) == {
        "ok": True,
        "cancelled": 5,
    }
    again = desk.handle(SYMBOL, {"op": "cancel", "account": "mm", "id": 3})
    assert again["ok"] is False


def test_bad_requests_are_refused_without_touching_the_book() -> None:
    desk, adapter = desk_with_book()
    resting = adapter.markets[SYMBOL].engine.resting_orders()
    assert new(desk, 1, "bid", "limit", 100, 1)["ok"]
    assert new(desk, 1, "bid", "limit", 100, 1)["error"].startswith("order id 1 already used")
    assert not new(desk, 2, "up", "limit", 100, 1)["ok"]
    assert not new(desk, 3, "bid", "limit", 100, 0)["ok"]  # the engine rejects size 0
    assert not desk.handle("NOPE", {"op": "new"})["ok"]
    assert not desk.handle(SYMBOL, {"op": "fly", "account": "mm", "id": 9})["ok"]
    assert adapter.markets[SYMBOL].engine.resting_orders() == resting + 1
