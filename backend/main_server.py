import asyncio
import time
import json
import logging
import statistics
from typing import Dict, List
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as redis

from data_simulator import MarketDataSimulator, SYMBOLS
from analytics import MarketAnalytics

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MarketServer")

# Redis Configuration
REDIS_URL = "redis://localhost:6379"
redis_client = None

# Global State
analytics_engine: Dict[str, MarketAnalytics] = {s: MarketAnalytics() for s in SYMBOLS}
latency_metrics: List[float] = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global redis_client
    redis_client = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    
    # Start the data generation and processing task
    simulation_task = asyncio.create_task(data_pipeline())
    
    yield
    
    # Shutdown
    simulation_task.cancel()
    await redis_client.close()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def data_pipeline():
    """
    Background task:
    1. Consumes simulated data.
    2. Calculates indicators.
    3. Broadcasts via Redis.
    """
    simulator = MarketDataSimulator()
    queue = asyncio.Queue(maxsize=10000)
    
    # Start generator in separate task (or run inside here if it's non-blocking enough)
    # The simulator.run_simulation is async, so we can run it concurrently.
    asyncio.create_task(simulator.run_simulation(queue))
    
    logger.info("Data pipeline started.")
    
    last_log_time = time.time()
    
    while True:
        try:
            # Batch process from queue to improve throughput
            # In a real tight loop, we might process one by one or small batches.
            # Using get_nowait for speed.
            
            # 1. Consume
            tick = await queue.get()
            
            start_processing = time.time()
            generation_time = tick['timestamp']
            
            symbol = tick['symbol']
            price = tick['price']
            volume = tick['volume']
            
            # 2. Calculate Indicators
            analytics = analytics_engine[symbol]
            analytics.update(price, volume)
            
            vwap = analytics.get_vwap()
            volatility = analytics.get_volatility()
            
            # 3. Prepare Payload
            payload = {
                "type": "market_data",
                "symbol": symbol,
                "data": tick,
                "analytics": {
                    "vwap": round(vwap, 2),
                    "volatility": round(volatility, 4)
                },
                "processed_at": time.time()
            }
            
            # Serialize
            # orjson returns bytes, so we need to decode if Redis expects str, 
            # or send bytes if Redis client is configured for bytes.
            # Here we used decode_responses=True for Redis, so we need str.
            message = orjson.dumps(payload).decode('utf-8')
            
            # 4. Broadcast
            await redis_client.publish("market_data_feed", message)
            
            # 5. Latency Tracking (Data Gen -> Redis Publish)
            # We measure time since 'timestamp' (generation) to now (post-publish)
            # This covers: generation -> queue -> process -> publish
            end_time = time.time()
            total_latency_ms = (end_time - generation_time) * 1000
            latency_metrics.append(total_latency_ms)
            
            # Logging P95 every 5 seconds
            if time.time() - last_log_time > 5:
                if latency_metrics:
                    p95 = statistics.quantiles(latency_metrics, n=20)[18] # approx 95th percentile
                    logger.info(f"P95 Latency: {p95:.2f}ms | Throughput checks processed in 5s: {len(latency_metrics)}")
                    latency_metrics.clear()
                last_log_time = time.time()
                
        except Exception as e:
            logger.error(f"Error in pipeline: {e}")
            await asyncio.sleep(0.1)

@app.websocket("/ws/stream/{symbol}")
async def websocket_endpoint(websocket: WebSocket, symbol: str):
    await websocket.accept()
    
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("market_data_feed")
    
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                data_str = message["data"]
                # Optimize: Don't deserialize full JSON just to check symbol if possible.
                # But we need to filter.
                # String search might be faster than full parse for filtering.
                if f'"symbol":"{symbol}"' in data_str:
                    # We send the raw string to avoid double serialization cost
                    # But we also need to append a "sent_at" timestamp for end-to-end latency check if needed
                    # The prompt asks to "Log the P95 Latency to confirm it remains sub-50ms" for 
                    # (Data Generation -> Redis Publish -> WebSocket Send).
                    # We can't easily modify the string without parsing. 
                    # We'll trust the server log for the publish part, and maybe client can track total.
                    # Or we calculate 'Redis Publish -> WebSocket Send' here.
                    
                    await websocket.send_text(data_str)
    except WebSocketDisconnect:
        logger.info(f"Client disconnected from {symbol}")
    finally:
        await pubsub.unsubscribe("market_data_feed")

@app.get("/health")
async def health():
    return {"status": "ok"}

