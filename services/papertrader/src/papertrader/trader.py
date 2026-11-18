"""The paper trader's state machine, free of I/O so it can be driven by NATS or by a test.

    on_envelope(e)   apply the venue's book (snapshot, delta, reset); True means a seq gap,
                     so the caller should load a snapshot from the bucket (load_snapshot)
    on_fill(body)    a fill from fills.sim.<symbol>.<account>: the only place fills count
    decide()         the order requests to send now: cancels first, then new quotes
    on_reply(r, a)   the venue's answer to request r
    state(seq)       a StrategyState envelope for the dashboard

One live order per side at most. A quote that should move is cancelled and replaced; a side
that shouldn't quote (position limit, halted) is cancelled. Requests go out in the order
decide() returns them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ingest.book.pybook import BookSide, PyBook
from mdcommon.schema import Envelope, Side, StrategyState
from papertrader.pnl import Position, SideName, to_money
from papertrader.strategy import Params, quotes

Request = dict[str, Any]


@dataclass
class LiveOrder:
    id: int
    price: int
    remaining: int


class PaperTrader:
    def __init__(
        self,
        symbol: str,
        account: str = "mm",
        params: Params | None = None,
        name: str = "imbalance-mm",
    ) -> None:
        self.symbol = symbol
        self.account = account
        self.params = params or Params()
        self.name = name
        self.book = PyBook()
        self.price_scale = 0
        self.size_scale = 0
        self.seq = 0
        self.synced = False
        self.position = Position()
        self.live: dict[SideName, LiveOrder | None] = {"bid": None, "ask": None}
        self.next_id = 1
        self.halted = False
        self.halt_reason = ""
        self.rejected = 0

    # Market data ---------------------------------------------------------------------------

    def on_envelope(self, envelope: Envelope) -> bool:
        if envelope.HasField("analytics") or envelope.HasField("strategy"):
            return False  # their own seqs
        if envelope.HasField("snapshot"):
            self.load_snapshot(envelope)
            return False
        if not self.synced or envelope.seq <= self.seq:
            return False
        if envelope.seq != self.seq + 1:
            self.synced = False
            return True
        self.seq = envelope.seq
        if envelope.HasField("delta"):
            for update in envelope.delta.updates:
                side: BookSide = "bid" if update.side == Side.SIDE_BID else "ask"
                self.book.apply(side, update.price_ticks, update.size_lots)
        elif envelope.HasField("reset"):
            self.book.clear()
            self.synced = False
        return False

    def load_snapshot(self, envelope: Envelope) -> None:
        if envelope.seq < self.seq:
            return
        self.book.clear()
        for level in envelope.snapshot.bids:
            self.book.apply("bid", level.price_ticks, level.size_lots)
        for level in envelope.snapshot.asks:
            self.book.apply("ask", level.price_ticks, level.size_lots)
        self.price_scale = envelope.snapshot.price_scale
        self.size_scale = envelope.snapshot.size_scale
        self.seq = envelope.seq
        self.synced = True

    # Fills and PnL -------------------------------------------------------------------------

    def on_fill(self, body: dict[str, Any]) -> None:
        side: SideName = body["side"]
        self.position.fill(side, int(body["price"]), int(body["size"]))
        live = self.live[side]
        if live and live.id == body["id"]:
            live.remaining -= int(body["size"])
            if live.remaining <= 0:
                self.live[side] = None

    def mid(self) -> float | None:
        bid, ask = self.book.best_bid(), self.book.best_ask()
        return (bid[0] + ask[0]) / 2 if bid and ask else None

    def pnl(self) -> tuple[float, float]:
        """(realized, unrealized) in the quote currency."""
        mid = self.mid()
        unrealized = self.position.unrealized(mid) if mid is not None else 0.0
        return (
            to_money(self.position.realized, self.price_scale, self.size_scale),
            to_money(unrealized, self.price_scale, self.size_scale),
        )

    # Decisions -----------------------------------------------------------------------------

    def decide(self) -> list[Request]:
        if not self.synced:
            return []
        if not self.halted and sum(self.pnl()) < -self.params.max_loss:
            self.halted = True
            self.halt_reason = f"loss limit: total PnL below -{self.params.max_loss:g}"
        wanted = quotes(self.book, self.position.lots, self.params)
        targets: dict[SideName, int | None] = {
            "bid": None if self.halted else wanted.bid,
            "ask": None if self.halted else wanted.ask,
        }
        requests: list[Request] = []
        for side in ("bid", "ask"):
            live, target = self.live[side], targets[side]
            if live and live.price == target:
                continue
            if live:
                requests.append({"op": "cancel", "account": self.account, "id": live.id})
                self.live[side] = None
            if target is not None:
                order = LiveOrder(self.next_id, target, self.params.quote_size_lots)
                self.next_id += 1
                self.live[side] = order
                requests.append(
                    {
                        "op": "new",
                        "account": self.account,
                        "id": order.id,
                        "side": side,
                        "type": "limit",
                        "price": order.price,
                        "size": order.remaining,
                    }
                )
        return requests

    def on_reply(self, request: Request, reply: dict[str, Any]) -> None:
        if request["op"] != "new":
            return
        side: SideName = request["side"]
        live = self.live[side]
        if live is None or live.id != request["id"]:
            return
        # Only fills reduce what is left: they arrive on their own subject, before or after
        # this reply, and counting the reply's "resting" too would count them twice.
        if not reply.get("ok"):
            self.rejected += 1
            self.live[side] = None

    def cancel_all(self) -> list[Request]:
        requests = []
        for side in ("bid", "ask"):
            live = self.live[side]
            if live:
                requests.append({"op": "cancel", "account": self.account, "id": live.id})
                self.live[side] = None
        return requests

    # Reporting -----------------------------------------------------------------------------

    def state(self, venue: str, seq: int, now_ns: int) -> Envelope:
        realized, unrealized = self.pnl()
        bid, ask = self.live["bid"], self.live["ask"]
        envelope = Envelope(venue=venue, symbol=self.symbol, seq=seq, ts_recv_ns=now_ns)
        envelope.strategy.CopyFrom(
            StrategyState(
                name=self.name,
                price_scale=self.price_scale,
                size_scale=self.size_scale,
                position_lots=self.position.lots,
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                bid_price_ticks=bid.price if bid else 0,
                ask_price_ticks=ask.price if ask else 0,
                quote_size_lots=self.params.quote_size_lots,
                fills=self.position.fills,
                traded_lots=self.position.traded_lots,
                halted=self.halted,
                halt_reason=self.halt_reason,
            )
        )
        return envelope
