import asyncio
import random

from data_simulator import SYMBOLS, MarketDataSimulator


def test_prices_stay_positive_and_quotes_are_ordered():
    random.seed(1)
    simulator = MarketDataSimulator()
    for _ in range(20_000):
        tick = simulator.generate_tick()
        assert tick["price"] > 0
        assert tick["bid"] < tick["ask"]
        assert tick["symbol"] in SYMBOLS


def test_tick_has_five_depth_levels_per_side():
    tick = MarketDataSimulator().generate_tick()
    assert len(tick["bids"]) == 5
    assert len(tick["asks"]) == 5
    assert [level["price"] for level in tick["bids"]] == sorted(
        (level["price"] for level in tick["bids"]), reverse=True
    )


def test_full_queue_drops_oldest_ticks():
    async def run():
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        task = asyncio.create_task(
            MarketDataSimulator().run_simulation(queue, target_rate=100_000, batch_size=100)
        )
        await asyncio.sleep(0.2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return [queue.get_nowait()["timestamp"] for _ in range(queue.qsize())]

    timestamps = asyncio.run(run())
    assert len(timestamps) == 50
    assert timestamps == sorted(timestamps)
