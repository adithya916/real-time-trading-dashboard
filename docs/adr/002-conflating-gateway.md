# 002: Conflate book updates per viewer in the gateway

## Context

The legacy server sent every tick to every viewer: about 6,300 messages a second per viewer
(measured, `bench/results/baseline.md`) for a page that draws 10, with one Redis connection per
viewer. p99 latency passed 100 ms at 50 viewers.

A browser only needs the book as it is when it next paints. Trades are different: each one is
an event the viewer should see.

## Decision

The gateway keeps one bus subscription and a mirror of each book. Every 50 ms it turns each
book's changes into **one delta holding the latest size of every changed level**, encodes it
**once**, and queues the same bytes for every viewer of that book. Trades pass through
**losslessly**; analytics are sent when they change.

- New viewers get a snapshot first, then the shared deltas.
- Each viewer has a bounded queue. If a tick's frames don't fit, the viewer's backlog is
  dropped and it gets a fresh snapshot next tick instead. A viewer counts as caught up only
  after it has read everything it was given; one still behind after 5 s is closed with 1013.
- The mirror checks the gapless seq. On a gap or a venue reset it resyncs from the snapshot
  bucket plus the envelopes buffered since, ignoring bucket entries older than what it already
  applied.

This is how exchange and vendor feeds serve slow consumers (interval conflation for quotes,
trades in full), and encoding once per tick is the same idea as sending one message per node
rather than per recipient.

## Consequences

- Book frames per viewer are capped at 20 a second whatever the feed rate; the test suite
  checks the reconstructed book is exact and that frames stay at one per tick.
- Book state is at most 50 ms stale by design.
- Trades are not capped, so a very busy symbol can still overflow a slow viewer's queue; that
  viewer then resyncs from a snapshot and misses the trades it couldn't take.
- Gateway cost per tick is one encode per changed book plus one queue append per viewer.

## Update: one WebSocket message per tick

The first load test (`bench/wsload`, GitHub runner with the whole stack on 4 CPUs) found the
gateway saturating near 500 viewers: round-trip p99 above 1 s, and connections timing out at
1,000. The book was capped at 20 frames a second, but every trade, metrics update and strategy
state was still its own WebSocket send, about 50 a second per viewer on the simulated feed, and
each send costs the event loop a queue hand-off and a socket write.

So each tick's frames for a symbol now travel in **one message**: a size-delimited stream of
envelopes (a varint length before each, Protobuf's standard framing), built once per symbol
per tick. Every in-sync viewer gets the same bytes; a viewer that needs the whole book gets the
snapshot version. Sends per viewer drop to at most 20 a second, whatever the trade rate, and
the per-viewer work per tick is one queue append. Viewer queues now count messages (100, about
5 s), so the slow-viewer rules are unchanged in time.
