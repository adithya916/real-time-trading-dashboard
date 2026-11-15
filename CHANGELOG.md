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
