"""Clocks: live code runs on wall time, replays on event time scaled by a speed.

Anything that waits for "when this event happened" takes a Clock, so a live feed and a replay
of it go through the same code. The replay clock schedules every event against the first one
(event at t plays at start + (t - first) / speed), so per-event sleep overhead never adds up
to drift over a long replay.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Protocol


class Clock(Protocol):
    def now_ns(self) -> int:
        """The current time on this clock, in Unix nanoseconds."""
        ...

    async def wait_for(self, event_ns: int) -> None:
        """Return when the event stamped event_ns is due."""
        ...


class WallClock:
    """Live: events are due when they arrive."""

    def now_ns(self) -> int:
        return time.time_ns()

    async def wait_for(self, event_ns: int) -> None:
        return None


class ReplayClock:
    """Event time played back at `speed` times real time; speed None means as fast as possible."""

    def __init__(
        self,
        speed: float | None = 1.0,
        *,
        wall_ns: Callable[[], int] = time.monotonic_ns,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if speed is not None and speed <= 0:
            raise ValueError("speed must be positive, or None for as fast as possible")
        self.speed = speed
        self._wall_ns = wall_ns
        self._sleep = sleep
        self._first_event_ns: int | None = None
        self._start_wall_ns = 0
        self._last_event_ns = 0

    def now_ns(self) -> int:
        """Where the replay is: the time of the latest event released, so whatever is stamped
        while handling an event gets that event's recorded time, at any speed."""
        return self._last_event_ns

    async def wait_for(self, event_ns: int) -> None:
        if self._first_event_ns is None:
            self._first_event_ns, self._start_wall_ns = event_ns, self._wall_ns()
        self._last_event_ns = max(self._last_event_ns, event_ns)
        if self.speed is None:
            return
        due = self._start_wall_ns + (event_ns - self._first_event_ns) / self.speed
        delay_s = (due - self._wall_ns()) / 1e9
        if delay_s > 0:
            await self._sleep(delay_s)
