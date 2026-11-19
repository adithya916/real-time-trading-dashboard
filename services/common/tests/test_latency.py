import random

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from mdcommon.latency import Histogram, LatencyRecorder, WindowedHistogram, _highest_in, _index


def test_buckets_are_exact_below_2048_and_within_a_tenth_of_a_percent_above() -> None:
    for v in range(0, 2048):
        assert _highest_in(_index(v)) == v
    rng = random.Random(1)
    for _ in range(20_000):
        v = rng.randrange(2048, 10**12)
        top = _highest_in(_index(v))
        assert v <= top <= v * 1.001


def test_buckets_are_contiguous_and_ordered() -> None:
    previous = -1
    for v in [*range(2000, 70_000, 7), 10**9, 10**9 + 1]:
        i = _index(v)
        assert i >= previous
        previous = i


def test_percentiles_match_sorted_values() -> None:
    rng = random.Random(2)
    values = [int(rng.lognormvariate(8, 1.2)) for _ in range(50_000)]
    h = Histogram()
    for v in values:
        h.record(v)
    ordered = sorted(values)
    for q in (0.5, 0.9, 0.99, 0.999):
        exact = ordered[max(0, int(q * len(ordered) + 0.999999) - 1)]
        assert exact <= h.percentile(q) <= exact * 1.001 + 1
    assert h.percentile(1.0) == max(values)
    assert Histogram().percentile(0.99) == 0


def test_coordinated_omission_correction_fills_in_the_stall() -> None:
    plain, corrected = Histogram(), Histogram()
    # Pinging every 10 ms: 99 fast replies, then one 1-second stall.
    for _ in range(99):
        plain.record(1_000)
        corrected.record_corrected(1_000, 10_000)
    plain.record(1_000_000)
    corrected.record_corrected(1_000_000, 10_000)
    assert plain.percentile(0.99) == 1_000  # one sample in 100 hides the stall
    assert corrected.total == 99 + 100  # the 99 pings that couldn't be sent are added
    assert corrected.percentile(0.75) > 400_000  # and now dominate the tail


def test_the_window_forgets_old_samples_but_keeps_counting() -> None:
    now = [0.0]
    w = WindowedHistogram(window_s=60, now=lambda: now[0])
    w.record(50_000)
    now[0] = 61  # one window later the old sample is still visible (previous window)
    w.record(10)
    assert w.snapshot().max == 50_000
    now[0] = 122  # two windows later it is gone
    w.record(20)
    assert w.snapshot().max == 20
    assert w.count == 3


def test_negative_hops_count_as_clock_skew_not_latency() -> None:
    r = LatencyRecorder()
    r.record_ns("exch_to_recv", 2_000_000, 1_000_000)
    r.record_ns("exch_to_recv", 1_000_000, 4_000_000)
    r.record_ns("exch_to_recv", 0, 4_000_000)  # venue sent no time: skipped
    assert r.negative == {"exch_to_recv": 1}
    assert r.summary()["exch_to_recv"]["count"] == 1
    assert r.summary()["exch_to_recv"]["p50"] == 3.0  # ms


def test_prometheus_exposition() -> None:
    r = LatencyRecorder()
    for us in (100, 200, 300, 40_000):
        r.record_us("bus_to_gateway", us)
    registry = CollectorRegistry()
    registry.register(r)
    text = generate_latest(registry).decode()
    assert 'md_latency_ms{hop="bus_to_gateway",quantile="0.5"} 0.2' in text
    assert 'md_latency_ms{hop="bus_to_gateway",quantile="1"} 40.0' in text
    assert 'md_latency_samples_total{hop="bus_to_gateway"} 4.0' in text


@pytest.mark.parametrize("value", [0, 1, 2047, 2048, 123_456_789])
def test_single_values_round_trip(value: int) -> None:
    h = Histogram()
    h.record(value)
    assert h.percentile(0.5) == value  # capped at the exact max
