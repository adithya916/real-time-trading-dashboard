"""One venue/symbol book mirrored from the bus, with gap detection and resync.

Ingest gives book and trade envelopes one gapless seq per symbol, and the gateway reads them
on a single subscription, so they arrive in publish order. A jump in seq means a lost message:
the mirror stops, buffers what keeps arriving, and resyncs from the snapshot bucket (refreshed
by ingest about once a second) plus the buffered envelopes after the snapshot's seq.

Between ticks the mirror collects what changed: the latest size per changed level, every trade,
and the latest analytics and strategy state. The hub turns that into one set of frames per tick.
"""

from __future__ import annotations

from ingest.book.pybook import BookSide, PyBook, to_snapshot
from mdcommon.schema import Delta, Envelope, LevelUpdate, Side

MAX_BUFFER = 50_000
_WIRE_SIDE: dict[BookSide, Side] = {"bid": Side.SIDE_BID, "ask": Side.SIDE_ASK}


class Mirror:
    def __init__(self, venue: str, symbol: str) -> None:
        self.venue = venue
        self.symbol = symbol
        self.book = PyBook()
        self.price_scale = 0
        self.size_scale = 0
        self.synced = False
        self.seq = 0  # last book or trade envelope applied
        self.buffer: list[Envelope] = []
        # Collected since the last tick:
        self.pending: dict[tuple[BookSide, int], int] = {}
        self.trades: list[bytes] = []
        self.analytics: bytes | None = None
        self.strategy: bytes | None = None
        self.resynced = False  # a new snapshot replaced the book; viewers need it whole

    def on_envelope(self, envelope: Envelope) -> bool:
        """Apply one envelope. Returns True when the mirror needs a snapshot from the bucket."""
        if envelope.HasField("analytics"):
            self.analytics = envelope.SerializeToString()
            return False
        if envelope.HasField("strategy"):
            self.strategy = envelope.SerializeToString()
            return False
        if envelope.HasField("snapshot"):
            self.load_snapshot(envelope)
            return False
        if not self.synced:
            if len(self.buffer) < MAX_BUFFER:
                self.buffer.append(envelope)
            return False
        if envelope.seq <= self.seq:
            return False  # already included in the snapshot we started from
        if envelope.seq != self.seq + 1:
            self.synced = False
            self.buffer = [envelope]
            return True
        self._apply(envelope)
        return not self.synced  # a reset: ask the bucket rather than wait for the stream

    def load_snapshot(self, envelope: Envelope) -> None:
        if envelope.seq < self.seq:
            return  # older than what we already applied, e.g. a bucket entry from before a reset
        snapshot = envelope.snapshot
        self.book.clear()
        for level in snapshot.bids:
            self.book.apply("bid", level.price_ticks, level.size_lots)
        for level in snapshot.asks:
            self.book.apply("ask", level.price_ticks, level.size_lots)
        self.price_scale, self.size_scale = snapshot.price_scale, snapshot.size_scale
        self.seq = envelope.seq
        self.synced = True
        self.resynced = True
        self.pending.clear()
        ordered = sorted(self.buffer, key=lambda e: e.seq)
        self.buffer = []
        for i, later in enumerate(ordered):
            if later.seq > self.seq and self.on_envelope(later):
                # Still a hole after this snapshot: keep the rest for the next one.
                self.buffer.extend(ordered[i + 1 :])
                break

    def _apply(self, envelope: Envelope) -> None:
        self.seq = envelope.seq
        if envelope.HasField("delta"):
            for update in envelope.delta.updates:
                side: BookSide = "bid" if update.side == Side.SIDE_BID else "ask"
                self.book.apply(side, update.price_ticks, update.size_lots)
                self.pending[(side, update.price_ticks)] = update.size_lots
        elif envelope.HasField("trade"):
            self.trades.append(envelope.SerializeToString())
        elif envelope.HasField("reset"):
            self.synced = False  # the next snapshot, from the stream or the bucket, brings it back

    def snapshot_frame(self) -> bytes:
        envelope = Envelope(venue=self.venue, symbol=self.symbol, seq=self.seq)
        envelope.snapshot.CopyFrom(to_snapshot(self.book, self.price_scale, self.size_scale))
        return envelope.SerializeToString()

    def drain(self) -> tuple[bytes | None, list[bytes], list[tuple[bytes, str]], bool]:
        """(coalesced delta frame, trade frames, latest (frame, kind) of analytics and strategy,
        resynced) since the last call."""
        delta_frame = None
        if self.pending and not self.resynced:
            envelope = Envelope(venue=self.venue, symbol=self.symbol, seq=self.seq)
            envelope.delta.CopyFrom(
                Delta(
                    updates=[
                        LevelUpdate(side=_WIRE_SIDE[side], price_ticks=price, size_lots=size)
                        for (side, price), size in self.pending.items()
                    ]
                )
            )
            delta_frame = envelope.SerializeToString()
        latest = [
            (frame, kind)
            for frame, kind in ((self.analytics, "analytics"), (self.strategy, "strategy"))
            if frame is not None
        ]
        result = (delta_frame, self.trades, latest, self.resynced)
        self.pending, self.trades, self.resynced = {}, [], False
        self.analytics = self.strategy = None
        return result
