import asyncio
from collections.abc import AsyncGenerator, Iterator, Sequence

import pytest

from ingest.adapters.base import FeedAdapter, supervise
from ingest.adapters.registry import load
from mdcommon.schema import Envelope, ResetReason, Trade

Script = Sequence[int | Exception]


class ScriptedAdapter(FeedAdapter):
    """Each stream() call plays the next script: ints become trades, exceptions are raised."""

    venue = "test"

    def __init__(self, sessions: Sequence[Script]) -> None:
        super().__init__(["AAA"], clock=lambda: 42)
        self.sessions = list(sessions)
        self.closed = 0

    async def stream(self) -> AsyncGenerator[Envelope, None]:
        script = self.sessions.pop(0)
        try:
            for item in script:
                if isinstance(item, Exception):
                    raise item
                yield self.wrap("AAA", Trade(trade_id=str(item)))
        finally:
            self.closed += 1


def run(adapter: FeedAdapter, n: int, **kwargs: object) -> tuple[list[Envelope], list[float]]:
    """Collect up to n envelopes from supervise(), recording the backoff sleeps instead of sleeping."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def collect() -> list[Envelope]:
        out: list[Envelope] = []
        async for envelope in supervise(adapter, sleep=fake_sleep, **kwargs):  # type: ignore[arg-type]
            out.append(envelope)
            if len(out) == n:
                break
        return out

    return asyncio.run(collect()), sleeps


def labels(envelopes: list[Envelope]) -> list[str]:
    return [
        e.trade.trade_id if e.HasField("trade") else str(e.WhichOneof("payload")) for e in envelopes
    ]


def test_reconnects_after_a_drop_and_resets_the_book() -> None:
    adapter = ScriptedAdapter([[1, 2, OSError("drop")], [3]])
    envelopes, sleeps = run(adapter, 4)

    assert labels(envelopes) == ["1", "2", "reset", "3"]
    assert envelopes[2].reset.reason == ResetReason.RESET_REASON_RECONNECT
    assert sleeps == [0.1]


def test_seq_stays_gapless_across_reconnects() -> None:
    adapter = ScriptedAdapter([[1, 2, OSError("drop")], [3]])
    envelopes, _ = run(adapter, 4)

    assert [e.seq for e in envelopes] == [1, 2, 3, 4]
    assert {(e.venue, e.symbol, e.ts_recv_ns) for e in envelopes} == {("test", "AAA", 42)}


def test_backoff_doubles_while_failing_and_resets_after_data() -> None:
    adapter = ScriptedAdapter(
        [[OSError()], [OSError()], [OSError()], [1, OSError()], [OSError()], [2]]
    )
    envelopes, sleeps = run(adapter, 7)

    assert labels(envelopes) == ["reset", "reset", "reset", "1", "reset", "reset", "2"]
    assert sleeps == [0.1, 0.2, 0.4, 0.1, 0.2]


def test_backoff_is_capped() -> None:
    adapter = ScriptedAdapter([[OSError()]] * 5 + [[1]])
    _, sleeps = run(adapter, 6, max_backoff_s=0.5)

    assert sleeps == [0.1, 0.2, 0.4, 0.5, 0.5]


def test_long_sessions_are_rotated() -> None:
    ticks: Iterator[float] = iter(range(0, 1000, 10))
    adapter = ScriptedAdapter([[1, 2, 3, 4, 5], [6]])
    envelopes, _ = run(adapter, 5, max_session_s=25, monotonic=lambda: next(ticks))

    assert labels(envelopes) == ["1", "2", "3", "reset", "6"]
    assert adapter.closed >= 1


def test_unexpected_errors_still_reconnect() -> None:
    adapter = ScriptedAdapter([[ValueError("bug in a parser")], [1]])
    envelopes, _ = run(adapter, 2)

    assert labels(envelopes) == ["reset", "1"]


def test_stopping_the_consumer_closes_the_stream() -> None:
    adapter = ScriptedAdapter([[1, 2, 3]])
    run(adapter, 1)

    assert adapter.closed == 1


def test_max_restarts_ends_the_supervisor() -> None:
    adapter = ScriptedAdapter([[OSError()], [OSError()], [1]])
    envelopes, _ = run(adapter, 10, max_restarts=1)

    assert labels(envelopes) == ["reset", "reset"]


def test_registry_names_known_venues_in_its_error() -> None:
    with pytest.raises(ValueError, match="unknown venue 'nope'"):
        load("nope")
