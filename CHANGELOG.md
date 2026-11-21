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

## Frontend

- The web app is TypeScript (strict, `noUncheckedIndexedAccess`) with typescript-eslint and vitest. `store.ts` is a small external store read through `useSyncExternalStore`, so high-rate data stays out of React state; `panels.ts` is the registry each dashboard view adds itself to with one line. `ci-web` adds typecheck and tests.
- The dashboard reads the gateway: a Web Worker owns the WebSocket, decodes Protobuf (generated with buf and protoc-gen-es into `web/src/gen`, git-ignored, by `npm run gen`), applies snapshots and deltas, and posts one view per animation frame into the store. Views are panels (metrics, chart, order book, trades) that re-render only when their data changes.
- Canvas charts: uPlot draws mid, microprice and VWAP over the last 60 s and Lightweight Charts draws one-second candles built from trades in the worker. Both update in place from the store; Recharts is gone.
- Depth heatmap: the worker records the top 25 levels per side every 250 ms, and a canvas panel scrolls each column in from the right, shading levels by size on a log scale with the mid as a white line.
- Header: venue and symbol picker from the gateway's `/symbols`, and a readout of WebSocket round-trip p50/p99 (the gateway echoes `ping:` text after the frames already queued for that viewer), frames per second and book seq.

## Redis and the legacy server removed

- `services/legacy` (FastAPI + Redis Pub/Sub), its Compose files, its CI matrix entry and the `ws_smoke` integration step are gone; the dashboard has read the gateway since the frontend rebuild. The integration job now health-checks the gateway.
- The baseline benchmark measured that server, so its runner and workflow went with it. The results stay in `bench/results/baseline.md` as the "before" numbers; tag `v0-baseline` still reproduces them.
- README rewritten for the current stack.

## Demo mode

- `python -m gateway --demo <recording> <venue:symbols>` (`make demo`): the gateway plays a recording through the real venue adapter at the recorded pace, on a loop, and computes the analytics in-process, with no NATS or Docker. Seqs keep counting across loops and timestamps are restamped with the current time, so the dashboard runs against it unchanged. The analytics code is a `demo` dependency group, left out of the gateway image.
## Dashboard fixes found with the demo

- Depth heatmap: the scroll-left copy ran under the device-pixel-ratio transform, so at any ratio other than 1 the image grew every column and only the newest column stayed visible; it also blended over the old pixels instead of replacing them. The copy now runs in device pixels with the `copy` composite mode.
- The candle chart no longer takes the mouse wheel or vertical swipes, so the page scrolls over it; dragging still pans.
- Layout: the trade tape sits beside the candles and the heatmap spans the full width (more history), so no grid cell is left empty.

## Candle history

- TimescaleDB in Compose (`deploy/compose/timescale.yaml`). Migrations live in `services/analytics/src/analytics/migrations/`, named by timestamp so two branches never claim the same number; analytics applies them in order at startup under an advisory lock.
- A `trades` hypertable (integer ticks and lots with their scales, unique per venue, symbol, trade id and time) and continuous aggregates `candles_1m`, `candles_5m` and `candles_1h`, with real-time aggregation so the newest candle is current. Open and close are ordered by the venue's trade id, since many trades share a timestamp.
- `analytics --db`: every trade is buffered and written about once a second with `COPY` into a temporary table, then `INSERT ... ON CONFLICT DO NOTHING`, so replaying a session never counts a trade twice.
- `gateway --db`: `GET /history/candles?venue&symbol&interval=1m|5m|1h&limit` returns candles oldest first, or 503 when there is no database.
- The integration job replays the Kraken recording and checks every 1 m, 5 m and 1 h candle against a DuckDB recomputation of the same trades.
- The candle chart has 1s / 1m / 5m / 1h. Longer intervals load history and keep the last bar moving with the live one-second candles; with no database (demo mode) they are built from live data only.

## Replay

- `mdcommon.clock`: a `Clock` interface. Live code uses wall time; `ReplayClock` plays event time at any speed, scheduling each event against the first so per-event overhead never accumulates as drift. Replayed adapters stamp `ts_recv_ns` from it, so a recording replayed or converted keeps its recorded receive times.
- `python -m recorder.replay --root data --speed N venue:SYMBOLS`: recorded Parquet back onto the bus, feeds merged by receive time, envelopes unchanged apart from `ts_bus_ns`, and the snapshot bucket refreshed about once a second so a gateway can join mid-replay. `python -m recorder --from-recording` turns a raw venue recording into Parquet offline.
- Golden replay: `ingest.book.golden` hashes each book (SHA-256 over integer ticks and lots) every 1,000 level updates and before each reset. `services/ingest/tests/golden/` pins the hashes for both recordings, and three paths must reproduce them: ingest's replay (ingest tests), record-to-Parquet-and-replay byte for byte (recorder tests), and Parquet replayed through NATS and read back from the JetStream stream (integration).

## Agent simulator

- `services/simulator`: a simulated venue (`sim`: BTC-SIM, ETH-SIM). Fair value is the original simulator's driftless GBM in continuous time. Zero-intelligence agents send Poisson limit, market and cancel orders around it, sized and priced from geometric distributions, all from one seeded generator. Orders go through `bookcore.MatchingEngine`, so the book and trades come from real price-time matching. Each action publishes its trades and one delta of the top-25 levels it changed, with its simulated time as venue time.
- Market-shape tests: the book is never crossed, the median spread is under 1 bp, limit and market orders arrive at the configured rates, buyers and sellers take liquidity about equally, the same seed gives byte-identical streams, and the snapshot equals the book built from the stream. Limit orders that take stale quotes after fair value moves are a second source of trades, as faster traders would be.
- External orders (for the paper trader) share the book through `submit_external`/`cancel_external`, with ids from 2^48 and fills reported as taker or maker.
- In the default Compose stack. The image compiles the C++ core in a build stage. `ci-python` builds and tests it, and the integration job checks a simulated book reaches a browser through the gateway.

## Paper trader

- The simulator takes orders: request/reply on `orders.sim.<symbol>` (JSON: new limit, IOC or market orders, and cancels) and fills on `fills.sim.<symbol>.<account>`, which is the only place a fill is reported, so it is counted exactly once. The desk maps each trader's own order ids to engine ids.
- `services/papertrader`: an imbalance market-maker. It quotes `microprice + imbalance x 6 ticks - position x 0.1 ticks/lot`, ±8 ticks, post-only, and stops buying or selling at ±200 lots and quoting altogether below a loss limit. PnL is by average cost in integer ticks and lots. The trader is a state machine free of I/O, driven by NATS in the service and directly in tests.
- `StrategyState` joins the schema (subject `md.<venue>.<symbol>.strategy`, its own seq). The gateway forwards the latest one each tick like analytics, the worker keeps a PnL history, and a "Paper trader" panel shows position, realized, unrealized and total PnL, the quotes, the fills and a PnL line.
- Tests: PnL against cash flow (Hypothesis), quote skew and limits, the trader's requote and halt logic, and an end-to-end run against the real simulator and C++ engine. Over 20,000 simulated actions it made 521 fills, all as maker, and lost 0.94: picked off by faster flow, as a naive maker is. Integration checks the paper trader fills on the simulator and its state reaches a browser through the gateway.

## Latency per hop

- `mdcommon.latency`: a small HdrHistogram (exact below 2,048 µs, within 0.1% above, maximum kept exactly) with HdrHistogram's coordinated-omission correction, windowed so percentiles cover the last one to two minutes. `LatencyRecorder` keeps one per hop and is a Prometheus collector: `md_latency_ms{hop, quantile}` for p50, p90, p99, p99.9 and max, plus sample counts. A negative hop (the clocks disagree) is counted as skew, not recorded.
- Hops: `exch_to_recv` (venue time to ours, so it includes the clocks' offset) and `recv_to_bus` in ingest and the simulator, `bus_to_analytics`, `bus_to_gateway`, `gateway_hold` (how long conflation held the oldest change), and `order_round_trip` for the paper trader. Gateway-built frames now carry `ts_gw_send_ns`. The browser still reports only its WebSocket round trip, since its clock isn't ours.
- `mdcommon.metrics` names the shared counters: envelopes, book resets by reason, viewers, frames, resyncs, slow disconnects, trades stored and paper fills. The gateway serves `/metrics`; ingest, analytics, the simulator and the paper trader serve it on ports 9101–9104.
- The integration job prints the gateway's latency table on the run's summary page.

## Dashboards and alerts

- Prometheus and Grafana in the default Compose stack (`deploy/compose/observability.yaml`). Prometheus scrapes every service every 5 s. Grafana is provisioned from the repo (data source, folder, dashboards), opens read-only for anyone without signing in, and starts on the **Market data pipeline** dashboard: p99 and p50 latency per hop, the bus-to-gateway tail, envelopes per second by service and kind, frames to viewers, viewers per feed, resyncs, slow disconnects, book resets by reason, the paper trader's fills and order round trip, and trades stored.
- Alert rules: `ChecksumFailures` (a venue's books keep failing their checksum for 5 minutes), `GatewayLagging` (p99 bus to gateway above 100 ms for 5 minutes) and `ServiceDown`.
- The integration job checks the rules with promtool, waits for every target to be up, confirms the rules loaded, and runs every dashboard query against Prometheus, so a typo fails CI instead of drawing an empty chart.
## Load generator

- `bench/wsload` (Go): an open-model WebSocket load test. Viewers connect at a constant arrival rate up to each step (for example 1,000 → 10,000), and each step is then held and measured.
  - Every viewer reads every frame. It records staleness (now minus the gateway's `ts_gw_send_ns`) for book frames and bus-to-viewer time for trades, reading those fields straight from the Protobuf bytes.
  - It pings every second, recording the round trip with coordinated-omission correction.
  - Results go to a CSV and a markdown table: connected, errors, closed by the gateway, frames and KiB per viewer per second, and p50/p99/p99.9/max of each.
- `bench-load` workflow: vets and tests the Go code (with the race detector), starts the Compose stack and steps viewers up on the simulated BTC-SIM feed. Pull requests run a short ramp; a manual run takes custom steps up to 10,000. The table, the gateway's latency under load and container CPU and memory go on the run summary.

## Fix: simulator book updates out of order

- An external order (the paper trader's) changes the simulator's book the moment it arrives, which can be while the venue waits for its next agent action. Its book update sat in an outbox that was only emptied after that next action had been applied and published, so the two went out in the wrong order and the older one overwrote a level with a stale size. Viewers could then see a crossed book; the integration job caught one. The outbox is now emptied right after the wait, so updates go out in the order the engine applied them. A regression test places orders during the wait and checks the published book never crosses and ends equal to the engine's.
## Deployment

- One host for everything: Caddy (`deploy/Caddyfile`, image `deploy/caddy.Dockerfile` with the dashboard built in) serves the dashboard, proxies `/ws`, `/symbols`, `/history` and `/health` to the gateway and `/grafana` to Grafana, and gets its own TLS certificate. A production build of the dashboard talks to whichever host served it, so one build works on any domain.
- `deploy/compose.prod.yaml` adds Caddy on 80/443 and unpublishes every other port, requires real passwords, and serves Grafana under `/grafana`. `deploy/oracle-setup.sh` prepares a fresh Ubuntu server (Docker, firewall, connection limits, random secrets) and starts it; `docs/DEPLOY.md` covers the Oracle Always Free ARM server and a free `sslip.io` name.
- `deploy` workflow: on each push to `main`, SSH (host key pinned) runs `deploy/remote-deploy.sh` and checks the public health endpoint; without the `DEPLOY_*` secrets it reports "not configured" and passes. The server builds natively on ARM, so there is no cross-compiling.
- Integration starts the production configuration behind Caddy with a local CA and checks that only 80 and 443 are published, that the dashboard, API, `wss://` stream and Grafana answer through it, and that `/metrics` is not exposed.

## Gateway: one message per viewer per tick

- The first load test saturated the gateway near 500 viewers: every trade, metrics update and strategy state was its own WebSocket send, about 50 a second per viewer. Each tick's frames now go in one binary message, a size-delimited stream of envelopes (`mdcommon.wire`, Protobuf's standard framing), built once per symbol and shared by every in-sync viewer. That is at most 20 sends a second per viewer. Viewer queues count messages (100, about 5 s).
- The browser worker (`worker/wire.ts`), the Go load generator (now also reporting messages per viewer per second), the smoke scripts and the tests read the new framing. ADR 002 has the reasoning and numbers.

## Write-up

- `docs/PERFORMANCE.md`: hardware and method (HdrHistograms, coordinated-omission correction, an open load model), before against now, the full load ramp to 10,000 asked-for viewers with a chart, where it breaks and why (one Python event loop near 2,000 viewers; the 10,000-viewer target was not reached), latency per hop, and Python against C++.
- `docs/architecture.md` with diagrams of the pipeline, book sync, fan-out and deployment.
- Decision records 003 (Protobuf with integer ticks), 004 (C++ core with pybind11), 005 (Parquet and TimescaleDB) and 006 (one free server behind Caddy), next to 001 (NATS) and 002 (the conflating gateway).
- `bench/results/wsload.csv` (full ramp), `wsload-per-envelope.csv` (before batching) and `wsload.svg`, drawn by `scripts/plot_load.py`.

## Gateway workers

- `python -m gateway --workers N` (Compose default 4, `GATEWAY_WORKERS`): N gateway processes on one port, each binding it with `SO_REUSEPORT` so the kernel spreads connections across them. Each has its own bus subscription, mirrors and tick, so nothing is shared. A worker that dies stops the rest, so the container restarts as a whole. `/health` reports which worker answered.
- Each worker serves metrics on `--metrics-port` + its index (9110–9113), and Prometheus scrapes them all. The dashboard's latency panels show the worst worker per hop, and a new panel shows viewers per worker.
- A test runs two workers in demo mode on Linux. It checks that connections reach both, that viewers on either get the book, and that SIGTERM to the parent stops every worker. The `bench-load` workflow takes the worker count as an input.
