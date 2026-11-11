# Real-Time Trading Dashboard

A full-stack trading dashboard prototype built with FastAPI, Redis, and React. A simulated market feed is processed, enriched with live analytics, fanned out over Redis Pub/Sub, and streamed to the browser over WebSockets.

## Features

- **Market Data Simulation**: Generates ticks across 5 symbols with L1 quotes and 5 levels of L2 depth, paced to a configurable target rate (default 50,000 ticks/sec).
- **Real-Time Analytics**: Session VWAP and 20-tick rolling volatility (standard deviation of log returns), updated on every tick in O(window) time.
- **Batched Publishing**: Ticks are drained from the queue in batches and published to Redis in a single pipelined round trip.
- **Per-Symbol Channels**: Each WebSocket client subscribes only to its symbol's Redis channel.
- **Throttled Frontend**: The client keeps only the newest message per 100ms window and parses just that one, so rendering cost doesn't grow with the tick rate. It reconnects automatically if the backend restarts.

## Tech Stack

- **Backend**: Python, FastAPI, Redis, orjson
- **Frontend**: React, Vite, TailwindCSS, Recharts
- **Communication**: WebSockets, Redis Pub/Sub

## Repository layout

| Path | What lives there |
| --- | --- |
| `services/legacy/` | The current FastAPI backend (simulator, analytics, WebSocket server) |
| `web/` | React dashboard |
| `deploy/compose/` | One Compose file per service, joined by `compose.yaml` |
| `docs/build-plan.html` | Interactive 7-day upgrade plan |

The project is being rebuilt in daily lanes of small branches; see `docs/build-plan.html` and `CHANGELOG.md`.

## Prerequisites

- **Docker** with Compose v2.20+ (for `include`)
- **[uv](https://docs.astral.sh/uv/)** for Python (3.10–3.12; the pinned dependencies don't ship wheels for 3.13)
- **Node.js 18+**
- **[pre-commit](https://pre-commit.com/)** for the formatting hooks

## Installation & Running

### Whole stack with Docker

```bash
docker compose up -d --build --wait
```

This starts Redis and the backend on port 8000. If something else already uses a port, set `LEGACY_PORT` or `REDIS_PORT`, for example `LEGACY_PORT=8001 docker compose up -d --build --wait`.

### Backend without Docker

Needs a Redis server on `localhost:6379`.

```bash
cd services/legacy
uv sync
uv run uvicorn main_server:app --reload --port 8000
```

The backend will start simulating data immediately upon launch.

### Frontend

```bash
cd web
npm install
npm run dev
```

Open your browser to `http://localhost:5173`.

### Formatting hooks

```bash
pre-commit install
```

Every commit then runs ruff, ruff-format and Prettier on the files it touches.

### Configuration

Copy `services/legacy/.env.example` to `services/legacy/.env` and `web/.env.example` to `web/.env` to override the defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection used for Pub/Sub |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated origins allowed to call the REST API |
| `VITE_WS_URL` | `ws://localhost:8000` | Backend WebSocket base URL used by the dashboard |

## Architecture Overview

1.  **Data Simulator**: Generates prices with a driftless geometric Brownian motion (5 bps per tick), plus a random spread and depth.
2.  **Ingestion Engine**: Drains the queue in batches of up to 500 ticks, updates per-symbol analytics, and serializes with orjson.
3.  **Broadcast Layer**: Publishes each tick to `market_data_feed:<SYMBOL>` via a Redis pipeline. If Redis is unavailable, ticks are dropped and publishing retries with backoff.
4.  **WebSocket Gateway**: `/ws/stream/{symbol}` subscribes to that symbol's channel and forwards messages; unknown symbols are rejected.
5.  **Client**: Keeps the newest message per 100ms window, updates the stat cards, order book, and a 100-point chart.

## Performance Testing

- **Backend Latency & Throughput**: Every 5 seconds the server logs the P95 latency from tick generation to Redis publish, and the achieved ticks/sec. Actual throughput depends on your CPU; the generator and pipeline share one event loop.
- **Scalability Test**: The "Run Client-Side Scalability Test" button opens 1000 WebSocket connections from the browser and shows how many opened or failed. Browsers cap WebSockets per tab, so use a dedicated load-testing tool for real concurrency numbers.

## License

MIT
