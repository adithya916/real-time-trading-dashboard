# Changelog

## Unreleased

- Monorepo layout: `services/legacy` (FastAPI backend), `web` (React dashboard), `deploy/compose` (one Compose file per service).
- Repo conventions: LF line endings, EditorConfig, pre-commit hooks (ruff, Prettier), Makefile.
- CI: `ci-python` (ruff, mypy, pytest), `ci-web` (ESLint, Prettier, build) and `integration` (Compose up, health and WebSocket smoke test), each with path filters and one always-reported `*-ok` check for branch protection.
- Legacy backend test suite (12 tests) and mypy fixes.
- Protobuf schema `marketdata.v1` (Envelope with per-hop timestamps; Snapshot, Delta, Trade, BookReset, Analytics; integer prices), linted with buf in CI.
- `services/common` (`mdcommon`): generated schema code plus string-exact tick conversion. Generated code is built by `scripts/gen_proto.sh`, never committed. TypeScript generation follows on Day 5 with the web worker.
- Baseline benchmark (`bench/baseline/run.py`, `bench-baseline` workflow): pipeline throughput and latency, one viewer's message rate, and a viewer ramp until p99 passes 100 ms. Results in `bench/results/baseline.md`, tagged `v0-baseline`.

## Day 2: real market data

- `services/ingest`: the venue adapter contract (`FeedAdapter.stream()`, gapless seq per symbol, `wrap()`/`reset()` helpers) and `supervise()`, which reconnects with capped exponential backoff, rotates sessions before venues drop them, and emits a `BookReset` per symbol after every session. Adapters register with one line in `registry.py`.
- `PyBook`: the reference L2 book (integer ticks, plain dicts) the C++ book will be checked against, with a Hypothesis state-machine test against a dict model.
- Kraken v2 adapter: depth-10 book with CRC32 checksum verification on every message, trades, and reset-and-reconnect on a mismatch. Tested by replaying 45 s of recorded live BTC/USD frames (`python -m ingest kraken BTC-USD --record ...`).
- Binance spot adapter: diff-depth stream synced to a REST snapshot by the documented U/u rules (drop stale events, the first applied event straddles the snapshot, then each U = previous u + 1); a hole emits BookReset(SEQUENCE_GAP) and resyncs on the same connection. The recorder now saves REST snapshots too.
- `services/recorder`: hourly Parquet files partitioned by venue, symbol, date and hour (`.partial` until closed), each row the full serialized envelope plus seq, timestamps and kind. `python -m recorder <venue> <symbols>` records live.

## C++ order book and matching engine

- `core/` (`bookcore`): C++20 static library built with CMake and warnings as errors, a GoogleTest suite, and pybind11 bindings packaged with scikit-build-core. `ci-cpp` checks clang-format, builds and tests in Release and under AddressSanitizer + UBSan, and imports the Python extension.
- `bookcore::L2Book`: each side a sorted array stored worst-first (best price at the back, so top-of-book updates move few elements), binary-search lookups, best/top/crossed/truncate, and Kraken's checksum computed from the integer tick values. Exposed to Python as `bookcore.L2Book`.
- Differential tests: the Kraken and Binance recordings and 300 random Hypothesis update streams go through both `PyBook` and the C++ `L2Book`, which must be identical after every event; the C++ checksum must equal the checksum Kraken sent with each message.
- `bookcore::MatchingEngine`: price-time priority over sorted maps of FIFO price levels, O(1) cancels through an order index, limit / market / IOC orders, and sequence-numbered Accepted / Trade / Cancelled / Rejected events (the same inputs always give the same events). Bound to Python.
- Benchmarks (`core/bench`, `bench/cpp/run.py`, `bench-cpp` workflow): Google Benchmark for book updates, checksums and the matching engine, plus PyBook vs `bookcore.L2Book` driven from Python on the recorded feeds. On a GitHub runner the C++ book does 32.4M updates/s and the engine 9.4M actions/s, but driven from Python it is slower than PyBook even with `L2Book.apply_many` (one call per message): the gain needs the whole message loop in C++.

## Message bus and gateway

- NATS JetStream in Compose (`deploy/compose/nats.yaml`): stream `MD` on `md.>` (24 h / 1 GB on disk) and KV bucket `snap` for the latest snapshot per symbol. `mdcommon.bus` holds the subject scheme `md.<venue>.<symbol>.{book,trade,analytics}`, `ensure_stream()` and `BusPublisher`. The integration job round-trips a message through the stream and the bucket. Decision record: `docs/adr/001-nats-jetstream.md`.
- Ingest publishes to the bus (`python -m ingest.service`): every envelope stamped with `ts_bus_ns`, snapshots plus a once-a-second refresh of each in-sync book into the `snap` bucket at the seq of its last envelope. `--replay` plays a recording through the real adapter. The live container runs with `docker compose --profile feeds up`.
- Kraken deltas now include a delete for every level pushed past depth 10, so consumers can apply them verbatim.
- `services/analytics`: mirrors every book from the bus and publishes mid, microprice, spread (bps), L1 and L5 imbalance, depth within 10 bps, realized volatility over 1/10/60 s, session VWAP and 60 s TWAP to `md.<venue>.<symbol>.analytics` up to 10 times a second, only when they change. Runs in the default Compose stack.
- `services/gateway` (port 8080): one bus subscription mirrors every book (gap detection, resync from the snapshot bucket plus buffered envelopes); every 50 ms each changed book becomes one coalesced delta, encoded once and fanned out to all its viewers, with every trade and the latest analytics. New viewers get a snapshot first; a viewer whose queue overflows gets a fresh snapshot instead of its backlog and is disconnected (1013) if still behind after 5 s. Decision record: `docs/adr/002-conflating-gateway.md`.
