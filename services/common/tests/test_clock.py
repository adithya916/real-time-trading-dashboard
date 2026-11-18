import asyncio

import pytest

from mdcommon.clock import ReplayClock, WallClock


class FakeTime:
    def __init__(self) -> None:
        self.ns = 1_000_000_000
        self.sleeps: list[float] = []

    def wall(self) -> int:
        return self.ns

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.ns += int(seconds * 1e9)


def play(clock: ReplayClock, events: list[int]) -> None:
    async def run() -> None:
        for event in events:
            await clock.wait_for(event)

    asyncio.run(run())


def test_events_play_at_their_recorded_spacing() -> None:
    t = FakeTime()
    clock = ReplayClock(1.0, wall_ns=t.wall, sleep=t.sleep)
    play(clock, [5_000_000_000, 5_500_000_000, 7_000_000_000])
    assert t.sleeps == pytest.approx([0.5, 1.5])


def test_speed_divides_the_waits() -> None:
    t = FakeTime()
    clock = ReplayClock(10.0, wall_ns=t.wall, sleep=t.sleep)
    play(clock, [0, 1_000_000_000, 3_000_000_000])
    assert t.sleeps == pytest.approx([0.1, 0.2])
    assert clock.now_ns() == 3_000_000_000


def test_slow_processing_is_absorbed_instead_of_adding_drift() -> None:
    t = FakeTime()
    clock = ReplayClock(1.0, wall_ns=t.wall, sleep=t.sleep)

    async def run() -> None:
        await clock.wait_for(0)
        t.ns += 700_000_000  # 0.7 s spent handling the first event
        await clock.wait_for(1_000_000_000)  # due 1 s after the first: wait only 0.3 s
        t.ns += 2_000_000_000  # far behind now
        await clock.wait_for(2_000_000_000)  # already late: no wait

    asyncio.run(run())
    assert t.sleeps == pytest.approx([0.3])


def test_as_fast_as_possible_never_sleeps_and_tracks_the_latest_event() -> None:
    t = FakeTime()
    clock = ReplayClock(None, wall_ns=t.wall, sleep=t.sleep)
    assert clock.now_ns() == 0
    play(clock, [10, 50, 30])
    assert t.sleeps == []
    assert clock.now_ns() == 50


def test_speed_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ReplayClock(0)


def test_the_wall_clock_never_waits() -> None:
    clock = WallClock()
    asyncio.run(clock.wait_for(0))
    assert clock.now_ns() > 1_700_000_000 * 10**9
