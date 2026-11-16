# 001: NATS JetStream as the market data bus

## Context

The legacy server publishes JSON to Redis Pub/Sub and every viewer holds its own Redis
connection. The rebuild splits the work into services (ingest, analytics, recorder, gateway)
that need a bus between them, and it has to fit a free-tier VM with a few GB of RAM.

What the bus must do:

- carry tens of thousands of small binary messages per second
- keep messages for a while, so a session can be replayed and a consumer that restarts can
  catch up
- let a new consumer start from the latest book without waiting for the next snapshot
- route by venue and symbol without one topic per combination being configured by hand

## Decision

NATS with JetStream.

- Subjects `md.<venue>.<symbol>.{book,trade,analytics}`. Snapshots, deltas and resets share
  the `book` subject so they arrive in order. Wildcards (`md.kraken.>`, `md.*.BTC-USD.book`)
  give consumers any slice without new configuration.
- Stream `MD` captures `md.>` on disk, 24 hours or 1 GB, oldest dropped first.
- Key-value bucket `snap` keeps the latest snapshot per venue and symbol.
- Publishers use core NATS publishes (no per-message ack); JetStream still stores what matches
  the stream. Every service calls `ensure_stream()` at startup, so there is no separate setup
  step.

## Alternatives

- **Redis Pub/Sub** (current): no persistence, so no replay; one connection per subscriber.
- **Redis Streams**: persistence and consumer groups, but no subject wildcards, and memory grows
  with retention unless trimmed by hand.
- **Kafka / Redpanda**: replay and throughput to spare, but a broker wants a gigabyte or more
  of memory on its own, which doesn't fit the free VM.

## Consequences

- One small server (tens of MB resident) replaces Redis once the legacy server is retired.
- Replay comes from the stream; long-term history goes to Parquet through the recorder.
- Core-NATS publishing can drop messages if the server is unreachable. Consumers detect that
  through the gapless `seq` in every envelope and rebuild from the snapshot bucket.
