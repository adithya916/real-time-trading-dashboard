"""The NATS JetStream bus: subject names, stream settings and a small publisher.

Subjects are md.<venue>.<symbol>.<kind>:

    book       snapshots, deltas and resets, on one subject so they stay in order
    trade      trades
    analytics  derived metrics
    strategy   a trading strategy's state (the paper trader)

Stream MD captures everything under md.> so a session can be replayed. The KV bucket
"snap" holds the latest snapshot per venue and symbol, so a new consumer can start from it
instead of waiting for the next snapshot on the stream.

Publishing uses core NATS (no per-message ack); JetStream still stores every message that
matches the stream's subjects. That keeps the hot path to one network write per envelope.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from mdcommon.schema import Envelope

if TYPE_CHECKING:
    from nats.aio.client import Client
    from nats.js.client import JetStreamContext
    from nats.js.kv import KeyValue

STREAM = "MD"
SUBJECTS = ["md.>"]
SNAPSHOT_BUCKET = "snap"
STREAM_MAX_AGE_S = 24 * 3600
STREAM_MAX_BYTES = 1024**3

_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")
_KIND_BY_PAYLOAD = {
    "snapshot": "book",
    "delta": "book",
    "reset": "book",
    "trade": "trade",
    "analytics": "analytics",
    "strategy": "strategy",
}


def _token(value: str, what: str) -> str:
    if not _TOKEN.match(value):
        raise ValueError(f"{what} {value!r} can't be a NATS subject token")
    return value


def subject(venue: str, symbol: str, kind: str) -> str:
    if kind not in {"book", "trade", "analytics", "strategy"}:
        raise ValueError(f"unknown subject kind {kind!r}")
    return f"md.{_token(venue, 'venue')}.{_token(symbol, 'symbol')}.{kind}"


def subject_for(envelope: Envelope) -> str:
    payload = envelope.WhichOneof("payload")
    if payload is None:
        raise ValueError("envelope has no payload")
    return subject(envelope.venue, envelope.symbol, _KIND_BY_PAYLOAD[payload])


def snapshot_key(venue: str, symbol: str) -> str:
    return f"{_token(venue, 'venue')}.{_token(symbol, 'symbol')}"


async def ensure_stream(js: JetStreamContext) -> None:
    """Create the MD stream and snapshot bucket, or bring them to the current settings.

    Safe to call from every service at startup.
    """
    from nats.js.api import (
        DiscardPolicy,
        KeyValueConfig,
        StorageType,
        StreamConfig,
    )
    from nats.js.errors import BadRequestError, NotFoundError

    config = StreamConfig(
        name=STREAM,
        subjects=SUBJECTS,
        storage=StorageType.FILE,
        max_age=STREAM_MAX_AGE_S,
        max_bytes=STREAM_MAX_BYTES,
        discard=DiscardPolicy.OLD,
    )
    try:
        await js.stream_info(STREAM)
    except NotFoundError:
        await js.add_stream(config)
    else:
        try:
            await js.update_stream(config)
        except BadRequestError:
            pass  # a setting that can't change in place; keep the existing stream
    await js.create_key_value(
        KeyValueConfig(bucket=SNAPSHOT_BUCKET, history=1, storage=StorageType.FILE)
    )


class BusPublisher:
    """Publishes envelopes to their subjects and keeps the latest snapshot per symbol."""

    def __init__(self, nc: Client, js: JetStreamContext, snapshots: KeyValue) -> None:
        self.nc = nc
        self.js = js
        self.snapshots = snapshots
        self.published = 0

    @classmethod
    async def connect(cls, url: str) -> BusPublisher:
        import nats

        nc = await nats.connect(url, name="market-data", max_reconnect_attempts=-1)
        js = nc.jetstream()
        await ensure_stream(js)
        return cls(nc, js, await js.key_value(SNAPSHOT_BUCKET))

    async def publish(self, envelope: Envelope) -> None:
        await self.nc.publish(subject_for(envelope), envelope.SerializeToString())
        self.published += 1

    async def put_snapshot(self, envelope: Envelope) -> None:
        if not envelope.HasField("snapshot"):
            raise ValueError("only snapshot envelopes go in the snapshot bucket")
        await self.snapshots.put(
            snapshot_key(envelope.venue, envelope.symbol), envelope.SerializeToString()
        )

    async def close(self) -> None:
        await self.nc.drain()
