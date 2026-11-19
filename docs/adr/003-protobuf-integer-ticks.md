# 003: Protobuf envelopes with integer prices, end to end

## Context

The original server sent JSON with float prices from the simulator straight to the browser.
The rebuild has several services, two venues with different precisions, a C++ book that must
agree exactly with the Python one, and replays that must rebuild the same books bit for bit.
Every hop also needs timestamps so latency can be measured per hop.

## Decision

One schema, `proto/marketdata/v1/marketdata.proto`, used on the bus, in Parquet and on the
browser WebSocket.

- Every message is an `Envelope`: venue, symbol, a gapless `seq` per venue and symbol, and
  `ts_exch_ns`, `ts_recv_ns`, `ts_bus_ns`, `ts_gw_send_ns`, then one payload (Snapshot, Delta,
  Trade, BookReset, Analytics, StrategyState).
- Prices and sizes are integers: `price_ticks` and `size_lots`, with `price_scale` and
  `size_scale` on the snapshot. Venue decimal strings are converted to ticks on the string
  (`to_ticks("67412.5", 1)`), never through a float.
- Code is generated (Python with grpcio-tools, TypeScript with buf and protoc-gen-es) and not
  committed. `buf lint` and `buf format` run in CI.
- On the WebSocket, one message per gateway tick holds several envelopes, size-delimited
  (Protobuf's standard framing), see ADR 002.

## Alternatives

- **JSON**: readable, but larger, slower to parse in the browser at these rates, unschema'd,
  and floats round differently between Python, C++ and JavaScript.
- **MessagePack**: compact, but schemaless, and its browser decoders measured slower than
  Protobuf in published benchmarks.
- **FlatBuffers / Cap'n Proto**: zero-copy reads, but more ceremony and weaker tooling in
  Python and the browser for little gain at a few thousand messages a second per service.

## Consequences

- Books built from the same stream compare exactly: the C++ and Python books are checked
  against each other on recordings, and golden replay hashes are stable across machines.
- The latency of every hop comes from fields already on the envelope (`mdcommon.latency`).
- Consumers detect lost messages from `seq` alone and resync from the snapshot bucket.
- Reading a frame in the browser needs the generated code (`npm run gen`) and a worker to keep
  decoding off the main thread; the Go load generator reads the few fields it needs straight
  off the wire with `protowire` instead of generating code.
