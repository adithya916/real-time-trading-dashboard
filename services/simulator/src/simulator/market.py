"""One simulated instrument: agent orders matched by the C++ engine, published as L2 deltas.

The engine is the venue's L3 book. What the market publishes is its top DEPTH levels per side,
like a depth-limited venue feed: after each action the top of each side is compared with the
previous one, and every level that changed (or left the top, as size 0) becomes an update.
Trades come straight from the engine's trade events, priced at the maker's price.

Agent orders get ids from 1 up; external orders (the paper trader) use ids from EXTERNAL_BASE,
so both share one book without colliding.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import bookcore
from mdcommon.schema import Aggressor, Level, LevelUpdate, Side, Snapshot, Trade
from simulator.agents import Action, FlowParams, SideName, ZeroIntelligenceFlow

DEPTH = 25
EXTERNAL_BASE = 1 << 48
_ENGINE_SIDE = {"bid": bookcore.Side.BID, "ask": bookcore.Side.ASK}
_WIRE_SIDE = {bookcore.Side.BID: Side.SIDE_BID, bookcore.Side.ASK: Side.SIDE_ASK}


@dataclass
class Fill:
    """One side of a trade, for whoever owns the order."""

    order: int
    side: SideName
    price_ticks: int
    size_lots: int
    taker: bool


@dataclass
class Result:
    updates: list[LevelUpdate] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)  # fills of external orders only
    events: list[bookcore.Event] = field(default_factory=list)


class _Resting:
    """Agent order ids still on the book, with O(1) random pick and removal."""

    def __init__(self) -> None:
        self.ids: list[int] = []
        self.pos: dict[int, int] = {}

    def add(self, order: int) -> None:
        self.pos[order] = len(self.ids)
        self.ids.append(order)

    def remove(self, order: int) -> None:
        i = self.pos.pop(order, None)
        if i is None:
            return
        last = self.ids.pop()
        if i < len(self.ids):
            self.ids[i] = last
            self.pos[last] = i

    def __len__(self) -> int:
        return len(self.ids)


class SimMarket:
    def __init__(self, params: FlowParams, seed: int, start_ns: int) -> None:
        self.params = params
        self.rng = random.Random(seed)
        self.flow = ZeroIntelligenceFlow(params, self.rng, start_ns)
        self.engine = bookcore.MatchingEngine()
        self._next_agent_id = 1
        self._next_trade_id = 1
        self._agents = _Resting()
        self._remaining: dict[int, int] = {}  # resting size per order, agents and external
        self._top = {"bid": self._depth("bid"), "ask": self._depth("ask")}

    # The agents ----------------------------------------------------------------------------

    def next_action(self) -> Action:
        return self.flow.next(len(self._agents))

    def apply(self, action: Action) -> Result:
        if action.kind == "cancel":
            if not self._agents:
                return Result()
            order = self._agents.ids[self.rng.randrange(len(self._agents))]
            return self.cancel(order)
        order = self._next_agent_id
        self._next_agent_id += 1
        kind = bookcore.OrderType.LIMIT if action.kind == "limit" else bookcore.OrderType.MARKET
        return self.submit(order, action.side, kind, action.price_ticks, action.size_lots)

    def warm_up(self, actions: int) -> None:
        """Build a book before anything is published."""
        for _ in range(actions):
            self.apply(self.next_action())

    # Any order -----------------------------------------------------------------------------

    def submit(
        self, order: int, side: SideName, kind: bookcore.OrderType, price: int, size: int
    ) -> Result:
        return self._after(self.engine.submit(order, _ENGINE_SIDE[side], kind, price, size))

    def cancel(self, order: int) -> Result:
        return self._after(self.engine.cancel(order))

    def snapshot(self, price_scale: int, size_scale: int) -> Snapshot:
        return Snapshot(
            bids=[
                Level(price_ticks=p, size_lots=s)
                for p, s in self.engine.depth(_ENGINE_SIDE["bid"], DEPTH)
            ],
            asks=[
                Level(price_ticks=p, size_lots=s)
                for p, s in self.engine.depth(_ENGINE_SIDE["ask"], DEPTH)
            ],
            price_scale=price_scale,
            size_scale=size_scale,
        )

    # Bookkeeping ---------------------------------------------------------------------------

    def _depth(self, side: SideName) -> dict[int, int]:
        return dict(self.engine.depth(_ENGINE_SIDE[side], DEPTH))

    def _after(self, events: list[bookcore.Event]) -> Result:
        result = Result(events=events)
        for e in events:
            if e.type == bookcore.EventType.ACCEPTED:
                self._remaining[e.order] = e.size
                if e.order < EXTERNAL_BASE and e.price:
                    self._agents.add(e.order)
            elif e.type == bookcore.EventType.TRADE:
                taker_side: SideName = "bid" if e.side == bookcore.Side.BID else "ask"
                maker_side: SideName = "ask" if taker_side == "bid" else "bid"
                result.trades.append(
                    Trade(
                        trade_id=str(self._next_trade_id),
                        price_ticks=e.price,
                        size_lots=e.size,
                        aggressor=Aggressor.AGGRESSOR_BUY
                        if taker_side == "bid"
                        else Aggressor.AGGRESSOR_SELL,
                    )
                )
                self._next_trade_id += 1
                for order, side, taker in (
                    (e.order, taker_side, True),
                    (e.maker, maker_side, False),
                ):
                    self._fill(order, e.size)
                    if order >= EXTERNAL_BASE:
                        result.fills.append(Fill(order, side, e.price, e.size, taker))
            elif e.type == bookcore.EventType.CANCELLED:
                self._done(e.order)
        # Orders that didn't rest (market orders, IOC remainders) are cancelled by the engine;
        # a limit order that filled completely leaves nothing behind either.
        for side in ("bid", "ask"):
            now = self._depth(side)
            before = self._top[side]
            for price, size in now.items():
                if before.get(price) != size:
                    result.updates.append(
                        LevelUpdate(
                            side=_WIRE_SIDE[_ENGINE_SIDE[side]], price_ticks=price, size_lots=size
                        )
                    )
            for price in before.keys() - now.keys():
                result.updates.append(
                    LevelUpdate(side=_WIRE_SIDE[_ENGINE_SIDE[side]], price_ticks=price, size_lots=0)
                )
            self._top[side] = now
        return result

    def _fill(self, order: int, size: int) -> None:
        left = self._remaining.get(order)
        if left is None:
            return
        if left - size <= 0:
            self._done(order)
        else:
            self._remaining[order] = left - size

    def _done(self, order: int) -> None:
        self._remaining.pop(order, None)
        self._agents.remove(order)
