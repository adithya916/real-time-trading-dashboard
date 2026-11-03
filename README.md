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

## Prerequisites

- **Python 3.10–3.12** (the pinned dependencies don't ship wheels for 3.13)
- **Node.js 18+**
- **Redis Server** (defaults to `localhost:6379`)

## Installation & Running

### 1. Backend Setup

```bash
cd backend
python -m venv venv
# Windows
.\venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt

# Run the server
uvicorn main_server:app --reload --port 8000
```

The backend will start simulating data immediately upon launch.

### 2. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Open your browser to `http://localhost:5173`.

### Configuration

Copy `backend/.env.example` to `backend/.env` and `frontend/.env.example` to `frontend/.env` to override the defaults:

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
