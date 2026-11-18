"""The simulated venue as a feed adapter: venue "sim", published exactly like a real venue.

Each symbol is a SimMarket. The adapter warms each book up, publishes a snapshot, then plays
the agents' actions in time order across symbols, paced by a ReplayClock on simulated time
(speed 1 is real time, None as fast as possible). Every action becomes the trades it caused
and one delta with the levels it changed, stamped with its simulated time as venue time.

External orders (the paper trader) go through submit_external()/cancel_external(); their book
changes and trades are published on the same stream, in order with the agents' actions.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import bookcore
from ingest.adapters.base import FeedAdapter
from mdcommon.clock import ReplayClock
from mdcommon.schema import Delta, Envelope, Snapshot
from simulator.agents import FlowParams, SideName
from simulator.market import Fill, Result, SimMarket

PRICE_SCALE = 1
SIZE_SCALE = 4
WARM_UP_ACTIONS = 3000

# Instruments the simulator knows, by symbol.
INSTRUMENTS: dict[str, FlowParams] = {
    "BTC-SIM": FlowParams(start_price_ticks=500_000),
    "ETH-SIM": FlowParams(start_price_ticks=30_000, sigma_per_s=0.0003, size_lots=200.0),
}


@dataclass
class External:
    """An order from outside the agent population, and what it did."""

    symbol: str
    result: Result


class SimulatorAdapter(FeedAdapter):
    venue = "sim"

    def __init__(
        self,
        symbols: Sequence[str],
        *,
        seed: int = 1,
        speed: float | None = 1.0,
        max_actions: int | None = None,
        params: dict[str, FlowParams] | None = None,
        clock: Callable[[], int] = time.time_ns,
        **kwargs: Any,
    ) -> None:
        super().__init__(symbols, clock=clock, **kwargs)
        catalog = params or INSTRUMENTS
        unknown = [s for s in self.symbols if s not in catalog]
        if unknown:
            raise ValueError(f"unknown simulated symbols {unknown} (known: {sorted(catalog)})")
        self.seed = seed
        self.speed = speed
        self.max_actions = max_actions
        self.catalog = catalog
        self.markets: dict[str, SimMarket] = {}
        self.actions = 0
        self._outbox: asyncio.Queue[External] = asyncio.Queue()
        # Called with fills of external orders that agent actions caused (resting orders hit).
        self.on_fills: Callable[[str, list[Fill]], None] | None = None

    def book_snapshot(self, symbol: str) -> Snapshot | None:
        market = self.markets.get(symbol)
        return market.snapshot(PRICE_SCALE, SIZE_SCALE) if market else None

    def submit_external(
        self,
        symbol: str,
        order: int,
        side: SideName,
        kind: bookcore.OrderType,
        price: int,
        size: int,
    ) -> Result:
        """Submit an order now; its book changes and trades go out with the next envelopes."""
        result = self.markets[symbol].submit(order, side, kind, price, size)
        self._outbox.put_nowait(External(symbol, result))
        return result

    def cancel_external(self, symbol: str, order: int) -> Result:
        result = self.markets[symbol].cancel(order)
        self._outbox.put_nowait(External(symbol, result))
        return result

    def _envelopes(self, symbol: str, result: Result, at_ns: int) -> list[Envelope]:
        out = [self.wrap(symbol, trade, ts_exch_ns=at_ns) for trade in result.trades]
        if result.updates:
            out.append(self.wrap(symbol, Delta(updates=result.updates), ts_exch_ns=at_ns))
        return out

    async def stream(self) -> AsyncGenerator[Envelope, None]:
        start_ns = self._clock()
        # A fresh market per session, seeded per symbol, so a reconnect starts a clean book.
        self.markets = {
            symbol: SimMarket(self.catalog[symbol], self.seed * 1000 + i, start_ns)
            for i, symbol in enumerate(self.symbols)
        }
        for symbol, market in self.markets.items():
            market.warm_up(WARM_UP_ACTIONS)
            market.flow.now_ns = start_ns
            yield self.wrap(symbol, market.snapshot(PRICE_SCALE, SIZE_SCALE), ts_exch_ns=start_ns)

        clock = ReplayClock(self.speed)
        pending = {symbol: market.next_action() for symbol, market in self.markets.items()}
        while self.max_actions is None or self.actions < self.max_actions:
            while not self._outbox.empty():
                external = self._outbox.get_nowait()
                for envelope in self._envelopes(external.symbol, external.result, self._clock()):
                    yield envelope
            symbol = min(pending, key=lambda s: pending[s].at_ns)
            action = pending[symbol]
            await clock.wait_for(action.at_ns)
            market = self.markets[symbol]
            result = market.apply(action)
            if result.fills and self.on_fills:
                self.on_fills(symbol, result.fills)
            for envelope in self._envelopes(symbol, result, action.at_ns):
                yield envelope
            pending[symbol] = market.next_action()
            self.actions += 1
            if self.speed is None and self.actions % 500 == 0:
                await asyncio.sleep(0)  # let other tasks run when not pacing
