import asyncio
import logging
import os
import statistics
import time
from contextlib import asynccontextmanager

import orjson
import redis.asyncio as redis
from analytics import MarketAnalytics
from data_simulator import SYMBOLS, MarketDataSimulator
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MarketServer")

# Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
CORS_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]
CHANNEL_PREFIX = "market_data_feed"
MAX_PUBLISH_BATCH = 500
redis_client = None

# Global State
analytics_engine: dict[str, MarketAnalytics] = {s: MarketAnalytics() for s in SYMBOLS}


def channel_for(symbol: str) -> str:
    return f"{CHANNEL_PREFIX}:{symbol}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global redis_client
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    queue = asyncio.Queue(maxsize=10000)
    simulator = MarketDataSimulator()
    tasks = [
        asyncio.create_task(simulator.run_simulation(queue)),
        asyncio.create_task(data_pipeline(queue)),
    ]

    yield

    # Shutdown
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await redis_client.aclose()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET"],
)


async def data_pipeline(queue: asyncio.Queue):
    """
    Background task:
    1. Drains simulated ticks from the queue in batches.
    2. Calculates indicators.
    3. Publishes each tick to its symbol's Redis channel, one round trip per batch.
    """
    logger.info("Data pipeline started.")

    latency_metrics = []
    last_log_time = time.time()
    retry_delay = 0.1

    while True:
        # 1. Consume everything that is ready, up to the batch limit
        batch = [await queue.get()]
        while len(batch) < MAX_PUBLISH_BATCH and not queue.empty():
            batch.append(queue.get_nowait())

        # 2. Calculate indicators and serialize
        messages = []
        for tick in batch:
            symbol = tick["symbol"]
            analytics = analytics_engine[symbol]
            analytics.update(tick["price"], tick["volume"])

            payload = {
                "type": "market_data",
                "symbol": symbol,
                "data": tick,
                "analytics": {
                    "vwap": round(analytics.get_vwap(), 2),
                    "volatility": round(analytics.get_volatility(), 6),
                },
                "processed_at": time.time(),
            }
            messages.append((channel_for(symbol), orjson.dumps(payload).decode("utf-8")))

        # 3. Broadcast (ticks are dropped if Redis is unavailable; this is a live feed)
        try:
            pipe = redis_client.pipeline(transaction=False)
            for channel, message in messages:
                pipe.publish(channel, message)
            await pipe.execute()
            retry_delay = 0.1
        except redis.RedisError as e:
            logger.error(f"Redis publish failed, retrying in {retry_delay:.1f}s: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 5.0)
            continue

        # 4. Latency tracking (generation -> queue -> process -> publish)
        now = time.time()
        latency_metrics.extend((now - tick["timestamp"]) * 1000 for tick in batch)

        # Log P95 and throughput every 5 seconds
        if now - last_log_time > 5:
            if len(latency_metrics) >= 2:
                p95 = statistics.quantiles(latency_metrics, n=20)[18]
                rate = len(latency_metrics) / (now - last_log_time)
                logger.info(
                    f"P95 latency (generate -> publish): {p95:.2f}ms | throughput: {rate:,.0f} ticks/s"
                )
            latency_metrics.clear()
            last_log_time = now


@app.websocket("/ws/stream/{symbol}")
async def websocket_endpoint(websocket: WebSocket, symbol: str):
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_for(symbol))

    async def forward_messages():
        async for message in pubsub.listen():
            if message["type"] == "message":
                # Already serialized by the pipeline, so send the raw string
                await websocket.send_text(message["data"])

    async def wait_for_disconnect():
        # Clients never send anything; this returns once the socket closes
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    forward_task = asyncio.create_task(forward_messages())
    disconnect_task = asyncio.create_task(wait_for_disconnect())
    try:
        done, _ = await asyncio.wait(
            {forward_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
        )
        error = forward_task.exception() if forward_task in done else None
        if isinstance(error, redis.RedisError):
            logger.warning(f"Stream for {symbol} stopped, Redis error: {error!r}")
            try:
                await websocket.close(code=1011)
            except Exception:
                pass  # Socket was already gone
        else:
            # Any other forwarding error means the send failed because the client went away
            logger.info(f"Client disconnected from {symbol}")
    finally:
        for task in (forward_task, disconnect_task):
            task.cancel()
        await asyncio.gather(forward_task, disconnect_task, return_exceptions=True)
        await pubsub.aclose()


@app.get("/health")
async def health():
    return {"status": "ok"}
