import math
import random
import statistics

from analytics import MarketAnalytics


def make_series(n=50, seed=7):
    rng = random.Random(seed)
    prices = [100 * math.exp(rng.gauss(0, 0.001) * i) for i in range(n)]
    volumes = [rng.randint(1, 500) for _ in prices]
    return prices, volumes


def test_volatility_is_stdev_of_last_20_log_returns():
    prices, volumes = make_series()
    analytics = MarketAnalytics()
    for price, volume in zip(prices, volumes, strict=True):
        analytics.update(price, volume)

    returns = [math.log(b / a) for a, b in zip(prices, prices[1:], strict=False)][-20:]
    assert math.isclose(analytics.get_volatility(), statistics.stdev(returns), rel_tol=1e-12)


def test_vwap_covers_the_whole_session():
    prices, volumes = make_series()
    analytics = MarketAnalytics()
    for price, volume in zip(prices, volumes, strict=True):
        analytics.update(price, volume)

    expected = sum(p * v for p, v in zip(prices, volumes, strict=True)) / sum(volumes)
    assert math.isclose(analytics.get_vwap(), expected, rel_tol=1e-12)


def test_volatility_needs_at_least_two_returns():
    analytics = MarketAnalytics()
    assert analytics.get_volatility() == 0.0
    analytics.update(100.0, 10)
    analytics.update(101.0, 10)
    assert analytics.get_volatility() == 0.0
    analytics.update(100.5, 10)
    assert analytics.get_volatility() > 0.0


def test_vwap_before_any_trade_is_zero():
    assert MarketAnalytics().get_vwap() == 0.0
