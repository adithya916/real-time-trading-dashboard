import math
from collections import deque


class MarketAnalytics:
    def __init__(self, window_size=20):
        self.window_size = window_size
        # Log returns over the last `window_size` ticks
        self.returns: deque[float] = deque(maxlen=window_size)
        self.last_price = None
        # Running totals since startup, for session VWAP
        self.cum_price_volume = 0.0
        self.cum_volume = 0

    def update(self, price, volume):
        """
        Update the analytics with a new trade tick.
        """
        if self.last_price is not None:
            self.returns.append(math.log(price / self.last_price))
        self.last_price = price

        self.cum_price_volume += price * volume
        self.cum_volume += volume

    def get_volatility(self):
        """
        Rolling volatility: sample standard deviation of the last `window_size` log returns.
        """
        n = len(self.returns)
        if n < 2:
            return 0.0
        mean = sum(self.returns) / n
        return math.sqrt(sum((r - mean) ** 2 for r in self.returns) / (n - 1))

    def get_vwap(self):
        """
        Session Volume-Weighted Average Price (VWAP) since startup.
        """
        if self.cum_volume == 0:
            return self.last_price or 0.0
        return self.cum_price_volume / self.cum_volume
