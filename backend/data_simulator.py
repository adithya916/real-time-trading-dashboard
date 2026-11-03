import time
import math
import random
import asyncio

# Symbols to simulate
SYMBOLS = ['AAPL', 'GOOG', 'MSFT', 'AMZN', 'TSLA']

# Per-tick volatility of the log price (5 bps)
TICK_SIGMA = 0.0005


class MarketDataSimulator:
    def __init__(self, symbols=SYMBOLS):
        self.symbols = symbols
        # Initialize base prices
        self.prices = {s: float(random.randint(100, 2000)) for s in symbols}

    def generate_tick(self) -> dict:
        """
        Generates a single market tick for a random symbol.
        Prices follow a driftless geometric Brownian motion, so they stay positive.
        """
        symbol = random.choice(self.symbols)
        z = random.gauss(0.0, 1.0)
        new_price = self.prices[symbol] * math.exp(TICK_SIGMA * z - 0.5 * TICK_SIGMA ** 2)
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

    async def run_simulation(self, queue: asyncio.Queue, target_rate=50000, batch_size=100):
        """
        Continuously generates ticks and puts them in an asyncio Queue,
        paced so the average rate does not exceed `target_rate` ticks per second.
        Batches are kept small so a CPU that can't reach the target rate
        doesn't block the event loop for long stretches.
        """
        batch_period = batch_size / target_rate
        while True:
            started = time.perf_counter()
            for _ in range(batch_size):
                if queue.full():
                    # Drop the oldest tick so consumers always see recent data
                    queue.get_nowait()
                queue.put_nowait(self.generate_tick())

            # Sleep off the rest of this batch's time slot (always yield at least once)
            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(0.0, batch_period - elapsed))


if __name__ == "__main__":
    # Test performance
    sim = MarketDataSimulator()
    start = time.time()
    for _ in range(50000):
        sim.generate_tick()
    end = time.time()
    print(f"Generated 50,000 ticks in {end - start:.4f}s")
