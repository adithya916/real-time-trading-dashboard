"""Microstructure metrics for one venue and symbol, fed by bus envelopes.

Definitions (b, a: best bid and ask prices; qb, qa: their sizes):

    mid               (a + b) / 2
    microprice        (a * qb + b * qa) / (qb + qa)
                      leans toward the side with less size, where the next trade is likelier
    spread_bps        (a - b) / mid * 10,000
    imbalance_l1      (qb - qa) / (qb + qa), from -1 (all asks) to +1 (all bids)
    imbalance_l5      the same over the top five levels per side
    depth_10bps_*     total size within 10 bps of the mid on each side
    realized_vol_Ns   sqrt of the summed squared log returns of the mid, sampled every 100 ms
                      over the last N seconds (not annualized)
    vwap_session      volume-weighted average trade price since the service started
    twap_60s          average of the mid samples over the last 60 seconds

Everything is computed in prices and sizes, using the scales from the latest snapshot.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from ingest.book.pybook import BookSide, PyBook
from mdcommon.schema import Analytics, Envelope

SAMPLE_INTERVAL_NS = 100_000_000
VOL_WINDOWS_S = (1, 10, 60)
HISTORY_NS = 60 * 1_000_000_000


@dataclass
class SymbolAnalytics:
    book: PyBook = field(default_factory=PyBook)
    price_scale: int = 0
    size_scale: int = 0
    synced: bool = False
    traded_value: float = 0.0
    traded_size: float = 0.0
    # (ts_ns, mid) samples, at most one per SAMPLE_INTERVAL_NS, kept for HISTORY_NS.
    mids: deque[tuple[int, float]] = field(default_factory=deque)

    def on_envelope(self, envelope: Envelope) -> None:
        if envelope.HasField("snapshot"):
            snapshot = envelope.snapshot
            self.book.clear()
            for level in snapshot.bids:
                self.book.apply("bid", level.price_ticks, level.size_lots)
            for level in snapshot.asks:
                self.book.apply("ask", level.price_ticks, level.size_lots)
            self.price_scale, self.size_scale = snapshot.price_scale, snapshot.size_scale
            self.synced = True
        elif envelope.HasField("delta") and self.synced:
            for update in envelope.delta.updates:
                side: BookSide = "bid" if update.side == 1 else "ask"
                self.book.apply(side, update.price_ticks, update.size_lots)
        elif envelope.HasField("reset"):
            self.book.clear()
            self.synced = False
        elif envelope.HasField("trade") and self.price_scale:
            price = envelope.trade.price_ticks / 10**self.price_scale
            size = envelope.trade.size_lots / 10**self.size_scale
            self.traded_value += price * size
            self.traded_size += size

    def _price(self, ticks: int) -> float:
        return ticks / 10.0**self.price_scale

    def _size(self, lots: int) -> float:
        return lots / 10.0**self.size_scale

    def sample(self, now_ns: int) -> None:
        """Record the mid for the volatility and TWAP windows, at most every 100 ms."""
        bid, ask = self.book.best_bid(), self.book.best_ask()
        if not self.synced or bid is None or ask is None:
            return
        if self.mids and now_ns - self.mids[-1][0] < SAMPLE_INTERVAL_NS:
            return
        self.mids.append((now_ns, (self._price(bid[0]) + self._price(ask[0])) / 2))
        while self.mids and now_ns - self.mids[0][0] > HISTORY_NS:
            self.mids.popleft()

    def _realized_vol(self, now_ns: int, window_s: int) -> float:
        start = now_ns - window_s * 1_000_000_000
        mids = [m for t, m in self.mids if t >= start]
        returns = [math.log(b / a) for a, b in zip(mids, mids[1:], strict=False) if a > 0]
        return math.sqrt(sum(r * r for r in returns))

    def compute(self, now_ns: int) -> Analytics | None:
        """Current metrics, or None while the book is not in sync or one side is empty."""
        bid, ask = self.book.best_bid(), self.book.best_ask()
        if not self.synced or bid is None or ask is None:
            return None
        b, a = self._price(bid[0]), self._price(ask[0])
        qb, qa = self._size(bid[1]), self._size(ask[1])
        mid = (a + b) / 2
        top_bid = sum(self._size(q) for _, q in self.book.top("bid", 5))
        top_ask = sum(self._size(q) for _, q in self.book.top("ask", 5))
        band = mid * 0.001
        recent = [m for t, m in self.mids if t >= now_ns - HISTORY_NS]
        vols = {w: self._realized_vol(now_ns, w) for w in VOL_WINDOWS_S}
        return Analytics(
            mid=mid,
            microprice=(a * qb + b * qa) / (qb + qa),
            spread_bps=(a - b) / mid * 10_000,
            imbalance_l1=(qb - qa) / (qb + qa),
            imbalance_l5=(top_bid - top_ask) / (top_bid + top_ask),
            depth_10bps_bid=sum(
                self._size(q) for p, q in self.book.top("bid") if self._price(p) >= mid - band
            ),
            depth_10bps_ask=sum(
                self._size(q) for p, q in self.book.top("ask") if self._price(p) <= mid + band
            ),
            realized_vol_1s=vols[1],
            realized_vol_10s=vols[10],
            realized_vol_60s=vols[60],
            vwap_session=self.traded_value / self.traded_size if self.traded_size else 0.0,
            twap_60s=sum(recent) / len(recent) if recent else mid,
        )
