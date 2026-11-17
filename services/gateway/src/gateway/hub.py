"""Viewers, mirrors and the 50 ms tick that conflates and fans out.

Each tick a mirror's changes become one delta frame, encoded once and handed to every viewer of
that symbol, plus every trade and the latest analytics. A viewer that just connected, or whose
book was replaced by a resync, gets a snapshot frame instead of the delta.

Slow viewers: each has a bounded queue. If it is full, the backlog is dropped and the viewer is
sent a fresh snapshot instead; a viewer still behind after SLOW_LIMIT_S is disconnected (1013).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field

from gateway.mirror import Mirror
from mdcommon.schema import Envelope

Key = tuple[str, str]
QUEUE_FRAMES = 256
SLOW_LIMIT_S = 5.0
CLOSE_FRAME = b""  # sentinel: the sender closes the socket with 1013


@dataclass(eq=False)
class Viewer:
    key: Key
    # Binary Envelope frames, plus text replies to the viewer's latency pings.
    queue: asyncio.Queue[bytes | str] = field(default_factory=lambda: asyncio.Queue(QUEUE_FRAMES))
    needs_snapshot: bool = True
    behind_since: float | None = None
    closed: bool = False
    sent: Counter[str] = field(default_factory=Counter)
    # Queue depth right after the last tick's frames were queued.
    queued_after_last_tick: int = 0

    def offer(self, frame: bytes, kind: str) -> bool:
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            return False
        self.sent[kind] += 1
        return True

    def drop_backlog(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()


class Hub:
    def __init__(self) -> None:
        self.mirrors: dict[Key, Mirror] = {}
        self.viewers: dict[Key, set[Viewer]] = {}
        self.disconnected_slow = 0

    def mirror(self, key: Key) -> Mirror:
        if key not in self.mirrors:
            self.mirrors[key] = Mirror(*key)
        return self.mirrors[key]

    def on_envelope(self, envelope: Envelope) -> bool:
        """Route an envelope to its mirror. True when that mirror needs a bucket snapshot."""
        return self.mirror((envelope.venue, envelope.symbol)).on_envelope(envelope)

    def add_viewer(self, key: Key) -> Viewer:
        viewer = Viewer(key)
        self.viewers.setdefault(key, set()).add(viewer)
        self.mirror(key)
        return viewer

    def remove_viewer(self, viewer: Viewer) -> None:
        self.viewers.get(viewer.key, set()).discard(viewer)

    def tick(self, now: float) -> None:
        for key, mirror in self.mirrors.items():
            delta, trades, analytics, resynced = mirror.drain()
            viewers = self.viewers.get(key, set())
            if not viewers or not mirror.synced:
                continue
            snapshot = (
                mirror.snapshot_frame()
                if resynced or any(v.needs_snapshot for v in viewers)
                else None
            )
            for viewer in list(viewers):
                self._send(
                    viewer, now, snapshot, None if resynced else delta, trades, analytics, resynced
                )

    def _send(
        self,
        viewer: Viewer,
        now: float,
        snapshot: bytes | None,
        delta: bytes | None,
        trades: list[bytes],
        analytics: bytes | None,
        resynced: bool,
    ) -> None:
        if viewer.closed:
            return
        # Caught up only once it has read everything it was given last tick. Dropping a backlog
        # empties the queue too, so an empty queue alone doesn't count.
        if (
            viewer.behind_since is not None
            and viewer.queue.empty()
            and viewer.queued_after_last_tick
        ):
            viewer.behind_since = None
        frames: list[tuple[bytes, str]] = []
        if viewer.needs_snapshot or resynced:
            assert snapshot is not None
            frames.append((snapshot, "snapshot"))
        elif delta is not None:
            frames.append((delta, "delta"))
        frames += [(t, "trade") for t in trades]
        if analytics is not None:
            frames.append((analytics, "analytics"))

        if all(viewer.offer(frame, kind) for frame, kind in frames):
            viewer.needs_snapshot = False
            viewer.queued_after_last_tick = viewer.queue.qsize()
            return

        # Queue full: drop what it hasn't read and resend the whole book next tick.
        viewer.drop_backlog()
        viewer.needs_snapshot = True
        viewer.queued_after_last_tick = 0
        if viewer.behind_since is None:
            viewer.behind_since = now
        elif now - viewer.behind_since >= SLOW_LIMIT_S:
            viewer.closed = True
            viewer.queue.put_nowait(CLOSE_FRAME)
            self.disconnected_slow += 1
