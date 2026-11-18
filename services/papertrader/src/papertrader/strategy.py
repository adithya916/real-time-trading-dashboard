"""An imbalance market-maker: quote both sides around a fair price that leans with the book.

    microprice   (ask * bid_size + bid * ask_size) / (bid_size + ask_size), from the top level
    imbalance    (bid depth - ask depth) / (bid depth + ask depth) over the top five levels
    center       microprice + imbalance x imbalance_ticks - position x skew_ticks_per_lot
    quotes       bid = floor(center - half_spread), ask = ceil(center + half_spread)

More resting size on the bid means buyers outnumber sellers near the top, so the price is more
likely to tick up: the center leans up. Inventory pushes the other way, so a long position
quotes lower on both sides, sells more readily and buys less, and drifts back toward flat.

Quotes never cross the book (post-only): a bid at or above the best ask moves to one tick
below it. Risk limits: no more buying at max_position, no more selling at -max_position, and
everything stops when total PnL falls below -max_loss.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ingest.book.pybook import PyBook


@dataclass(frozen=True)
class Params:
    half_spread_ticks: float = 8.0
    imbalance_ticks: float = 6.0
    skew_ticks_per_lot: float = 0.1
    quote_size_lots: int = 20
    max_position_lots: int = 200
    max_loss: float = 500.0  # quote currency


@dataclass(frozen=True)
class Quotes:
    bid: int | None
    ask: int | None


NO_QUOTES = Quotes(None, None)


def imbalance(book: PyBook, levels: int = 5) -> float:
    bids = sum(size for _, size in book.top("bid", levels))
    asks = sum(size for _, size in book.top("ask", levels))
    return (bids - asks) / (bids + asks) if bids + asks else 0.0


def quotes(book: PyBook, position_lots: int, params: Params) -> Quotes:
    best_bid, best_ask = book.best_bid(), book.best_ask()
    if best_bid is None or best_ask is None:
        return NO_QUOTES
    (bid, bid_size), (ask, ask_size) = best_bid, best_ask
    micro = (ask * bid_size + bid * ask_size) / (bid_size + ask_size)
    center = (
        micro + imbalance(book) * params.imbalance_ticks - position_lots * params.skew_ticks_per_lot
    )
    our_bid = min(math.floor(center - params.half_spread_ticks), ask - 1)
    our_ask = max(math.ceil(center + params.half_spread_ticks), bid + 1)
    return Quotes(
        bid=our_bid if position_lots < params.max_position_lots else None,
        ask=our_ask if position_lots > -params.max_position_lots else None,
    )
