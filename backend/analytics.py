import numpy as np
from collections import deque

class MarketAnalytics:
    def __init__(self, window_size=20):
        self.window_size = window_size
        self.prices = deque(maxlen=window_size)
        self.volumes = deque(maxlen=window_size)
        self.last_ema = None
        # Smoothing factor for EMA (2 / (N + 1))
        self.alpha = 2 / (window_size + 1)

    def update(self, price, volume):
        """
        Update the analytics with a new trade tick.
        """
        self.prices.append(price)
        self.volumes.append(volume)
        
        # Update EMA
        if self.last_ema is None:
            self.last_ema = price
        else:
            self.last_ema = (price * self.alpha) + (self.last_ema * (1 - self.alpha))

    def get_volatility(self):
        """
        Calculate 20-Tick Exponential Rolling Volatility.
        Using standard deviation of returns for simplicity in this context, 
        or we can track the EMA of the squared deviations.
        Here we'll use a standard deviation over the window for robustness.
        """
        if len(self.prices) < 2:
            return 0.0
        return np.std(self.prices)

    def get_vwap(self):
        """
        Calculate Volume-Weighted Average Price (VWAP) over the rolling window.
        """
        if not self.prices:
            return 0.0
        
        price_array = np.array(self.prices)
        volume_array = np.array(self.volumes)
        
        total_volume = np.sum(volume_array)
        if total_volume == 0:
            return price_array[-1]
            
        return np.sum(price_array * volume_array) / total_volume

