"""The simulated venue's order entry: outside traders (the paper trader) send orders over NATS.

    request  orders.sim.<symbol>   {"op": "new", "account": "mm", "id": 7, "side": "bid",
                                    "type": "limit" | "ioc" | "market", "price": 500010,
                                    "size": 10}
                                   {"op": "cancel", "account": "mm", "id": 7}
    reply                          {"ok": true, "resting": 10}  or  {"ok": false, "error": "..."}
    fills    fills.sim.<symbol>.<account>
                                   {"id": 7, "side": "bid", "price": 500010, "size": 4,
                                    "taker": false, "ts": <ns>}

Prices are ticks and sizes lots, at the symbol's scales. Fills are published only on the fills
subject, never in the reply, so a trader that counts fills there counts each exactly once;
a taker's fills go out before its reply. Order ids are the trader's own, per account; the desk
maps them to engine ids from EXTERNAL_BASE up.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import bookcore
from simulator.adapter import SimulatorAdapter
from simulator.market import EXTERNAL_BASE, Fill

log = logging.getLogger(__name__)
ORDER_SUBJECTS = "orders.sim.*"
_TYPES = {
    "limit": bookcore.OrderType.LIMIT,
    "ioc": bookcore.OrderType.IOC,
    "market": bookcore.OrderType.MARKET,
}


def fills_subject(symbol: str, account: str) -> str:
    return f"fills.sim.{symbol}.{account}"


FillMessage = tuple[str, dict[str, Any]]  # (subject, body)


class OrderDesk:
    def __init__(self, adapter: SimulatorAdapter, clock: Callable[[], int]) -> None:
        self.adapter = adapter
        self.clock = clock
        self._next_engine_id = EXTERNAL_BASE
        self._engine_id: dict[tuple[str, str, int], int] = {}  # (symbol, account, id) -> engine
        self._owner: dict[int, tuple[str, int]] = {}  # engine id -> (account, id)
        self._resting: dict[int, int] = {}  # engine id -> lots still on the book
        adapter.on_fills = self._agent_fills
        self.outbox: list[FillMessage] = []

    def handle(self, symbol: str, request: dict[str, Any]) -> dict[str, Any]:
        """Apply one request; fills land in self.outbox, in order, before the reply is sent."""
        if symbol not in self.adapter.markets:
            return {"ok": False, "error": f"unknown or not yet trading symbol {symbol!r}"}
        try:
            account, client_id = str(request["account"]), int(request["id"])
            if request["op"] == "new":
                return self._new(symbol, account, client_id, request)
            if request["op"] == "cancel":
                return self._cancel(symbol, account, client_id)
            return {"ok": False, "error": f"unknown op {request['op']!r}"}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": f"bad request: {exc!r}"}

    def _new(
        self, symbol: str, account: str, client_id: int, request: dict[str, Any]
    ) -> dict[str, Any]:
        key = (symbol, account, client_id)
        if key in self._engine_id:
            return {"ok": False, "error": f"order id {client_id} already used"}
        side = request["side"]
        if side not in ("bid", "ask"):
            raise ValueError(f"side {side!r}")
        engine_id = self._next_engine_id
        self._next_engine_id += 1
        self._engine_id[key] = engine_id
        self._owner[engine_id] = (account, client_id)
        size = int(request["size"])
        self._resting[engine_id] = size
        result = self.adapter.submit_external(
            symbol, engine_id, side, _TYPES[request["type"]], int(request.get("price", 0)), size
        )
        for event in result.events:
            if event.type == bookcore.EventType.REJECTED:
                self._resting.pop(engine_id, None)
                return {"ok": False, "error": f"rejected: {event.reason.name}"}
            if event.type == bookcore.EventType.CANCELLED and event.order == engine_id:
                self._resting.pop(engine_id, None)  # IOC/market remainder
        self._publish(symbol, result.fills)
        return {"ok": True, "resting": self._resting.get(engine_id, 0)}

    def _cancel(self, symbol: str, account: str, client_id: int) -> dict[str, Any]:
        engine_id = self._engine_id.get((symbol, account, client_id))
        if engine_id is None or engine_id not in self._resting:
            return {"ok": False, "error": f"order {client_id} is not resting"}
        result = self.adapter.cancel_external(symbol, engine_id)
        self._resting.pop(engine_id, None)
        cancelled = sum(e.size for e in result.events if e.type == bookcore.EventType.CANCELLED)
        return {"ok": True, "cancelled": cancelled}

    def _agent_fills(self, symbol: str, fills: list[Fill]) -> None:
        self._publish(symbol, fills)

    def _publish(self, symbol: str, fills: list[Fill]) -> None:
        now = self.clock()
        for fill in fills:
            owner = self._owner.get(fill.order)
            if owner is None:
                continue
            account, client_id = owner
            left = self._resting.get(fill.order)
            if left is not None:
                if left - fill.size_lots <= 0:
                    self._resting.pop(fill.order)
                else:
                    self._resting[fill.order] = left - fill.size_lots
            self.outbox.append(
                (
                    fills_subject(symbol, account),
                    {
                        "id": client_id,
                        "side": fill.side,
                        "price": fill.price_ticks,
                        "size": fill.size_lots,
                        "taker": fill.taker,
                        "ts": now,
                    },
                )
            )


async def serve(desk: OrderDesk, nc: Any, every_s: float = 0.01) -> asyncio.Task[None]:
    """Answer order requests on NATS; publish fills as they happen (a resting order's fills
    come from agent actions, so a task sends whatever is waiting every few milliseconds)."""

    async def flush() -> None:
        while desk.outbox:
            subject, body = desk.outbox.pop(0)
            await nc.publish(subject, json.dumps(body).encode())

    async def on_request(msg: Any) -> None:
        symbol = msg.subject.rsplit(".", 1)[-1]
        try:
            request = json.loads(msg.data)
        except ValueError:
            reply: dict[str, Any] = {"ok": False, "error": "not JSON"}
        else:
            reply = desk.handle(symbol, request)
        await flush()
        await msg.respond(json.dumps(reply).encode())

    async def pump() -> None:
        while True:
            await flush()
            await asyncio.sleep(every_s)

    await nc.subscribe(ORDER_SUBJECTS, cb=on_request)
    log.info("simulator: taking orders on %s", ORDER_SUBJECTS)
    return asyncio.create_task(pump())
