# Real-Time Trading Dashboard

Live crypto order books from Kraken and Binance, normalized into one Protobuf schema, carried over NATS JetStream, enriched with microstructure metrics, and served to a browser dashboard through a conflating WebSocket gateway. The order book and a price-time matching engine also exist in C++ (pybind11), checked against the Python reference book on recorded venue data.

## How it works

```
Kraken v2 WS ─┐                      ┌─> analytics ──(metrics)──┐
Binance WS ───┼─> ingest ──> NATS ───┼─> recorder ──> Parquet   │
recordings ───┘   (adapters,  JetStream                          v
                   PyBook,     md.<venue>.<symbol>.{book,trade,analytics}
                   checksums)        └─> gateway ──(50 ms conflated deltas)──> web
```

- **simulator** is a venue of its own (`sim`: BTC-SIM, ETH-SIM). Zero-intelligence agents send random limit, market and cancel orders around a random-walk fair value, and the C++ matching engine matches them, so trades and the book come from real matching. It runs in the default stack, so there is always a market to watch.
- **papertrader** market-makes BTC-SIM: it quotes around the microprice, leans with the book's imbalance and against its own inventory, and stops at position and loss limits. Orders go to the simulator's order desk over NATS request/reply, and fills come back on their own subject. Its position and PnL are published as `StrategyState` and shown in a dashboard panel.
- **ingest** runs one adapter per venue. Kraken books are verified against the venue's CRC32 checksum on every message; Binance books are synced to a REST snapshot by the documented `U`/`u` rules. A sequence gap or checksum mismatch emits a `BookReset` and resyncs. Every envelope carries a gapless `seq` per symbol and per-hop timestamps.
- **NATS JetStream** keeps 24 h of the stream (`MD` on `md.>`) and the latest snapshot of each book in the `snap` KV bucket, so a consumer that starts late loads the snapshot and applies the stream from the next `seq`.
- **analytics** mirrors every book and publishes mid, microprice, spread, L1/L5 imbalance, depth within 10 bps, realized volatility (1/10/60 s), VWAP and TWAP up to 10 times a second.
- **TimescaleDB** stores every trade (written by analytics in batches with `COPY`); continuous aggregates turn them into 1 m, 5 m and 1 h candles, served by the gateway at `GET /history/candles`.
- **recorder** writes the raw envelopes to hourly, Hive-partitioned Parquet files.
- **gateway** holds one bus subscription and a mirror of every book. Every 50 ms it turns each book's changes into one delta, encodes it once and sends it to every viewer of that symbol, with every trade and the latest metrics. Slow viewers get a fresh snapshot instead of a backlog and are disconnected if they stay behind.
- **web** (React + TypeScript): a Web Worker owns the WebSocket, decodes Protobuf and keeps the book; the page reads one view per animation frame. Panels: metrics, uPlot price lines, Lightweight Charts candles, a canvas depth heatmap, the order book and the trade tape, plus a round-trip latency readout.

Design decisions are in [`docs/adr/`](docs/adr/); the build plan is [`docs/build-plan.html`](docs/build-plan.html) and the history is in [`CHANGELOG.md`](CHANGELOG.md).

## Measured

On a 4-CPU GitHub runner, with the whole stack and the load generator sharing it. Method, raw results and where it breaks: [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md).

| | Before (`v0-baseline`) | Now |
| --- | ---: | ---: |
| Messages to each viewer | 6,277/s | ≤ 20/s, every envelope delivered |
| Viewers on one gateway, all connected | p99 over 100 ms at 50 | 1,000 at p99 119 ms; about 2,000 before connections time out |
| Book updates, one core | ~39k/s | 32.4M/s (C++ `L2Book`) |
| Matching engine | none | 9.4M actions/s |

The plan's 10,000 viewers at p99 < 50 ms was not reached: one Python event loop saturates near 2,000 viewers. Several gateway processes on one port is the next step.

How it fits together: [`docs/architecture.md`](docs/architecture.md). Decisions and their alternatives: [`docs/adr/`](docs/adr/).

## Repository layout

| Path | What lives there |
| --- | --- |
| `proto/` | `marketdata.v1` schema (Envelope, Snapshot, Delta, Trade, BookReset, Analytics) |
| `services/common/` | `mdcommon`: generated schema code, tick conversion, bus subjects and publisher |
| `services/ingest/` | Venue adapters, the reference book `PyBook`, recording and replay |
| `services/analytics/` | Metrics computed from the bus |
| `services/recorder/` | Parquet recorder |
| `services/gateway/` | Conflating WebSocket gateway |
| `services/simulator/` | Agent-based simulated venue on the C++ matching engine, with an order desk |
| `services/papertrader/` | Imbalance market-maker with PnL and risk limits |
| `core/` | C++20 `bookcore`: `L2Book`, `MatchingEngine`, pybind11 bindings, tests, benchmarks |
| `web/` | React dashboard |
| `deploy/` | One Compose file per service, joined by `compose.yaml`; NATS config |
| `bench/` | Benchmark runners and results |
| `scripts/` | Protobuf generation and the integration smoke tests |

## Prerequisites

- **Docker** with Compose v2.20+ (for `include`)
- **[uv](https://docs.astral.sh/uv/)** for Python 3.10–3.12
- **Node.js 18+**
- **[pre-commit](https://pre-commit.com/)** for the formatting hooks
- A C++20 compiler and CMake 3.25+ only for `core/`

## Running

### The stack with live feeds

```bash
docker compose --profile feeds up -d --build --wait
```

This starts NATS, TimescaleDB, the simulator, the paper trader, ingest (Kraken BTC-USD and ETH-USD), analytics and the gateway on port 8080. Without `--profile feeds` everything but the live feeds starts (the simulated venue still trades), and you can replay a recording into it as well:

```bash
bash scripts/gen_proto.sh
uv run --project services/ingest python -m ingest.service --bus nats://localhost:4222 --speed 1 \
  --replay services/ingest/tests/fixtures/kraken_btcusd.jsonl.gz kraken:BTC-USD
```

Prometheus (port 9090) scrapes every service, and Grafana (port 3000, read-only without signing in) opens on the **Market data pipeline** dashboard: latency per hop, throughput, viewers, resyncs and checksum failures, from `deploy/grafana/dashboards/`. Alert rules are in `deploy/prometheus/alerts.yml`.

If a port is taken, set `GATEWAY_PORT`, `NATS_PORT`, `NATS_MONITOR_PORT`, `TIMESCALE_PORT`, `PROMETHEUS_PORT` or `GRAFANA_PORT`. The database password defaults to a local-only value; set `TIMESCALE_PASSWORD` anywhere else.

### Record and replay

```bash
# Record live (or convert a raw venue recording offline with --from-recording file.jsonl.gz)
uv run --project services/recorder python -m recorder kraken BTC-USD --seconds 600 --root data
# Publish it onto the bus again, 10x faster than it happened
uv run --project services/recorder python -m recorder.replay --root data --speed 10 kraken:BTC-USD
```

A replay publishes the recorded envelopes unchanged (same seq and venue timestamps) on the same subjects, so analytics, the candle history and the dashboard treat it like the live session. Golden files in `services/ingest/tests/golden/` pin a hash of each book every 1,000 level updates and at the end of each session. Replaying a recording through ingest, through Parquet, or through the bus in CI must reproduce them exactly.

### On a server

The production setup puts everything behind Caddy on one HTTPS host (dashboard, WebSocket, API and Grafana), with nothing else published. [`docs/DEPLOY.md`](docs/DEPLOY.md) walks through a free Oracle Cloud ARM server, a free `sslip.io` hostname and deploys on every push to `main`.

### Without Docker: demo mode

The gateway can replay a recording into itself, with the analytics computed in the same process and no NATS:

```bash
make demo
# or: cd services/gateway && uv run python -m gateway --demo ../ingest/tests/fixtures/kraken_btcusd.jsonl.gz kraken:BTC-USD
```

It plays 45 s of recorded Kraken BTC/USD at the recorded pace, on a loop, on port 8080. Viewers get exactly the frames the full stack would send, so the dashboard below works against it unchanged. There is no database in demo mode, so the 1 m / 5 m / 1 h candles are built from what arrives live.

### The dashboard

```bash
cd web
npm install
npm run gen   # TypeScript code from proto/
npm run dev
```

Open `http://localhost:5173`. Copy `web/.env.example` to `web/.env` to change the defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_GATEWAY_URL` | `ws://localhost:8080` | Gateway WebSocket base URL |
| `VITE_DEFAULT_VENUE` | `kraken` | Feed shown on load |
| `VITE_DEFAULT_SYMBOL` | `BTC-USD` | Feed shown on load |

### Checks

```bash
pre-commit install   # ruff, ruff-format, Prettier and clang-format on every commit
make check           # lint, typecheck and tests for every Python service and the web app
make cpp-test        # C++ tests (add -DBOOKCORE_SANITIZE=ON for ASan + UBSan)
make bench-cpp       # C++ benchmarks and the Python comparison
```

CI runs the same checks, plus the C++ build under sanitizers and an integration job that starts the Compose stack and replays a Kraken recording through ingest, NATS, analytics and the gateway.

## License

MIT
