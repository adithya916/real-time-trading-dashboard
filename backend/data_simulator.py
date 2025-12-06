import time
import random
import asyncio
from dataclasses import dataclass, asdict
from typing import List, Dict
import numpy as np

# Symbols to simulate
SYMBOLS = ['AAPL', 'GOOG', 'MSFT', 'AMZN', 'TSLA']

@dataclass
class OrderBookLevel:
    price: float
    size: int

@dataclass
class MarketTick:
    symbol: str
    price: float
    volume: int
    timestamp: float
    bid: float
    ask: float
    bids: List[Dict] # Top 5
    asks: List[Dict] # Top 5

class MarketDataSimulator:
    def __init__(self, symbols=SYMBOLS):
        self.symbols = symbols
        # Initialize base prices
        self.prices = {s: float(random.randint(100, 2000)) for s in symbols}
        self.tick_count = 0
        self.start_time = time.time()

    def generate_tick(self) -> dict:
        """
        Generates a single market tick for a random symbol.
        Optimized for speed.
        """
        symbol = random.choice(self.symbols)
        current_price = self.prices[symbol]
        
        # Random walk
        change = np.random.normal(0, 0.5)
        new_price = current_price + change
        self.prices[symbol] = new_price
        
        # Spread calculation
        spread = random.uniform(0.01, 0.10)
        bid = new_price - (spread / 2)
        ask = new_price + (spread / 2)
        
        # Level 2 Depth (Top 5)
        bids = [{'price': round(bid - i*0.05, 2), 'size': random.randint(1, 100)} for i in range(5)]
        asks = [{'price': round(ask + i*0.05, 2), 'size': random.randint(1, 100)} for i in range(5)]
        
        return {
            'symbol': symbol,
            'price': round(new_price, 2),
            'volume': random.randint(1, 500),
            'timestamp': time.time(),
            'bid': round(bid, 2),
            'ask': round(ask, 2),
            'bids': bids,
            'asks': asks
        }

    async def run_simulation(self, queue: asyncio.Queue, target_rate=50000):
        """
        Continuously generates ticks and puts them in an asyncio Queue.
        Target rate is ticks per second.
        """
        batch_size = 1000 # Generate in batches to reduce overhead
        while True:
            # Generate a batch of data
            ticks = [self.generate_tick() for _ in range(batch_size)]
            
            # Push to queue (this might be the bottleneck, so we might need a faster way if queue is too slow)
            # For 50k/sec, pure python queue might struggle. 
            # In a real HFT system we'd use C++ or shared memory.
            # Here we will do our best with asyncio.
            for tick in ticks:
                if queue.full():
                    try:
                        queue.get_nowait() # Drop old data if full to maintain real-time
                    except asyncio.QueueEmpty:
                        pass
                await queue.put(tick)
            
            # Yield control briefly to allow other tasks to process
            await asyncio.sleep(0.001)

if __name__ == "__main__":
    # Test performance
    sim = MarketDataSimulator()
    start = time.time()
    count = 0
    for _ in range(50000):
        sim.generate_tick()
    end = time.time()
    print(f"Generated 50,000 ticks in {end - start:.4f}s")

