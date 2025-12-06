# High-Performance Real-Time Trading Dashboard

A full-stack, low-latency trading dashboard prototype built with FastAPI, Redis, and React. Designed to handle high-throughput market data simulations (50,000+ ticks/sec) with real-time analytics.

## Features

- **High-Frequency Data Simulation**: Generates 50k+ market ticks/sec across 5 symbols with L1/L2 order book data.
- **Real-Time Analytics**: Calculates VWAP and 20-tick Rolling Volatility on the fly.
- **Low-Latency Architecture**: Sub-50ms end-to-end latency using Redis Pub/Sub and WebSockets.
- **Optimized Frontend**: React dashboard with batching strategies to handle high-frequency updates without UI lag.
- **Scalability**: Capable of handling multiple concurrent WebSocket clients.

## Tech Stack

- **Backend**: Python, FastAPI, Redis, NumPy
- **Frontend**: React, Vite, TailwindCSS, Recharts
- **Communication**: WebSockets, Redis Pub/Sub

## Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- **Redis Server** (Must be running on `localhost:6379`)

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

## Architecture Overview

1.  **Data Simulator**: Generates stochastic market data (Geometric Brownian Motion + Noise).
2.  **Ingestion Engine**: Consumes ticks, updates in-memory analytics (deque-based), and serializes to JSON.
3.  **Broadcast Layer**: Pushes updates to Redis Pub/Sub channels (`market_data_feed`).
4.  **WebSocket Gateway**: Subscribes to Redis and forwards data to connected clients.
5.  **Client**: Batches incoming messages (100ms window) and updates the UI efficiently.

## Performance Testing

- **Backend Latency**: The server logs P95 latency metrics (Generation -> Publish) to the console every 5 seconds.
- **Scalability Test**: Click the "Run Client-Side Scalability Test" button in the dashboard to simulate 1000 concurrent dummy connections.

## License

MIT

