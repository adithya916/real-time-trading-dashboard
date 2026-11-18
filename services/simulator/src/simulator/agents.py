"""Order flow for the simulated venue: a random-walk fair value and zero-intelligence traders.

Fair value follows the same driftless geometric Brownian motion the original simulator used,
in continuous time: over dt seconds, log price moves by sigma * sqrt(dt) * N(0, 1).

Around it, zero-intelligence agents (Gode & Sunder; Farmer, Patelli & Zovko) act at random:
    limit orders   arrive at limit_rate per second, buy or sell with equal odds, priced
                   1 + Geometric(mean offset_ticks) ticks on the passive side of fair value
    market orders  arrive at market_rate per second, buy or sell with equal odds
    cancels        each resting agent order is cancelled at cancel_rate per second
Sizes are 1 + Geometric(mean size_lots) lots. Nobody is smart, yet the book that emerges has a
spread, depth that thins away from the mid, and trades whenever flow crosses it. Besides the
market orders, limit orders trade too: when fair value moves, quotes left on the book are stale,
and new limit orders placed around the new fair value take them, as a faster trader would.

Everything comes from one seeded random generator, so a seed replays the same market.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal

Kind = Literal["limit", "market", "cancel"]
SideName = Literal["bid", "ask"]


@dataclass(frozen=True)
class FlowParams:
    start_price_ticks: int = 500_000  # 50,000.0 at price scale 1
    sigma_per_s: float = 0.00005  # 0.5 bps per sqrt(second), about 28% a year
    limit_rate: float = 40.0
    market_rate: float = 4.0
    cancel_rate: float = 0.05  # per resting order, per second
    offset_ticks: float = 15.0
    size_lots: float = 50.0


@dataclass(frozen=True)
class Action:
    at_ns: int
    kind: Kind
    side: SideName
    price_ticks: int = 0
    size_lots: int = 0


class FairValue:
    def __init__(self, start_ticks: int, sigma_per_s: float, rng: random.Random) -> None:
        self.log_price = math.log(start_ticks)
        self.sigma = sigma_per_s
        self.rng = rng

    def advance(self, dt_s: float) -> None:
        if dt_s > 0:
            self.log_price += self.sigma * math.sqrt(dt_s) * self.rng.gauss(0.0, 1.0)

    @property
    def ticks(self) -> float:
        return math.exp(self.log_price)


def geometric(rng: random.Random, mean: float) -> int:
    """0, 1, 2, ... with the given mean."""
    if mean <= 0:
        return 0
    p = 1.0 / (1.0 + mean)
    return int(math.log(1.0 - rng.random()) / math.log(1.0 - p))


class ZeroIntelligenceFlow:
    """The next agent action, given how many agent orders are resting (cancels scale with it)."""

    def __init__(self, params: FlowParams, rng: random.Random, start_ns: int) -> None:
        self.params = params
        self.rng = rng
        self.fair = FairValue(params.start_price_ticks, params.sigma_per_s, rng)
        self.now_ns = start_ns

    def next(self, resting: int) -> Action:
        p = self.params
        cancel_total = p.cancel_rate * resting
        total = p.limit_rate + p.market_rate + cancel_total
        dt_s = self.rng.expovariate(total)
        self.fair.advance(dt_s)
        self.now_ns += max(1, int(dt_s * 1e9))
        side: SideName = "bid" if self.rng.random() < 0.5 else "ask"
        pick = self.rng.random() * total
        if pick < cancel_total:
            return Action(self.now_ns, "cancel", side)
        size = 1 + geometric(self.rng, p.size_lots)
        if pick < cancel_total + p.market_rate:
            return Action(self.now_ns, "market", side, size_lots=size)
        offset = 1 + geometric(self.rng, p.offset_ticks)
        fair = self.fair.ticks
        # Strictly passive against fair value: bids below it, asks above it.
        price = math.ceil(fair) - offset if side == "bid" else math.floor(fair) + offset
        return Action(self.now_ns, "limit", side, price_ticks=max(1, price), size_lots=size)
