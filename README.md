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

- **ingest** runs one adapter per venue. Kraken books are verified against the venue's CRC32 checksum on every message; Binance books are synced to a REST snapshot by the documented `U`/`u` rules. A sequence gap or checksum mismatch emits a `BookReset` and resyncs. Every envelope carries a gapless `seq` per symbol and per-hop timestamps.
- **NATS JetStream** keeps 24 h of the stream (`MD` on `md.>`) and the latest snapshot of each book in the `snap` KV bucket, so a consumer that starts late loads the snapshot and applies the stream from the next `seq`.
- **analytics** mirrors every book and publishes mid, microprice, spread, L1/L5 imbalance, depth within 10 bps, realized volatility (1/10/60 s), VWAP and TWAP up to 10 times a second.
- **TimescaleDB** stores every trade (written by analytics in batches with `COPY`); continuous aggregates turn them into 1 m, 5 m and 1 h candles, served by the gateway at `GET /history/candles`.
- **recorder** writes the raw envelopes to hourly, Hive-partitioned Parquet files.
- **gateway** holds one bus subscription and a mirror of every book. Every 50 ms it turns each book's changes into one delta, encodes it once and sends it to every viewer of that symbol, with every trade and the latest metrics. Slow viewers get a fresh snapshot instead of a backlog and are disconnected if they stay behind.
- **web** (React + TypeScript): a Web Worker owns the WebSocket, decodes Protobuf and keeps the book; the page reads one view per animation frame. Panels: metrics, uPlot price lines, Lightweight Charts candles, a canvas depth heatmap, the order book and the trade tape, plus a round-trip latency readout.

Design decisions are in [`docs/adr/`](docs/adr/); the build plan is [`docs/build-plan.html`](docs/build-plan.html) and the history is in [`CHANGELOG.md`](CHANGELOG.md).

## Measured

| | Value | Where |
| --- | ---: | --- |
| C++ `L2Book` updates (one core, GitHub runner) | 32.4 M/s | [`bench/results/cpp.md`](bench/results/cpp.md) |
| C++ matching engine actions | 9.4 M/s | same |
| Messages per viewer per symbol, gateway | ≤ 20/s (one frame per 50 ms tick) | `services/gateway` |
| Messages per viewer, the original server (before) | 6,277/s | [`bench/results/baseline.md`](bench/results/baseline.md) |

Driven from Python one message at a time, the C++ book is still slower than the Python one (0.4–0.7×): the gain needs the whole message loop in C++. Latency histograms and a load test of the gateway are next.

## Repository layout

| Path | What lives there |
| --- | --- |
| `proto/` | `marketdata.v1` schema (Envelope, Snapshot, Delta, Trade, BookReset, Analytics) |
| `services/common/` | `mdcommon`: generated schema code, tick conversion, bus subjects and publisher |
| `services/ingest/` | Venue adapters, the reference book `PyBook`, recording and replay |
| `services/analytics/` | Metrics computed from the bus |
| `services/recorder/` | Parquet recorder |
| `services/gateway/` | Conflating WebSocket gateway |
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

This starts NATS, TimescaleDB, ingest (Kraken BTC-USD and ETH-USD), analytics and the gateway on port 8080. Without `--profile feeds` everything but ingest starts, and you can replay a recording into it instead:

```bash
bash scripts/gen_proto.sh
uv run --project services/ingest python -m ingest.service --bus nats://localhost:4222 --speed 1 \
  --replay services/ingest/tests/fixtures/kraken_btcusd.jsonl.gz kraken:BTC-USD
```

If a port is taken, set `GATEWAY_PORT`, `NATS_PORT`, `NATS_MONITOR_PORT` or `TIMESCALE_PORT`. The database password defaults to a local-only value; set `TIMESCALE_PASSWORD` anywhere else.

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
