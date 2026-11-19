"""Latency histograms per hop, exported to Prometheus as percentiles.

Histogram is a small HdrHistogram: values (microseconds) up to 2,047 are counted exactly, and
above that each power of two is split into 1,024 buckets, so every recorded value is kept to
within 0.1% at any magnitude, in a few KB, with O(1) recording. Percentiles report the top of
the bucket (never understating), and the maximum is kept exactly.

record_corrected() is HdrHistogram's coordinated-omission correction: when something is
measured at a fixed interval and one measurement takes much longer, the measurements that
couldn't happen during that stall are added as well (value - interval, value - 2 x interval,
...), so a stall shows up in the percentiles at its real weight instead of as one sample.

Hops time what happens between two timestamps on the same envelope, for example
ts_bus_ns - ts_recv_ns. LatencyRecorder keeps one WindowedHistogram per hop: the percentiles
cover the last one to two minutes, so a stall an hour ago doesn't sit in p99 forever, while the
counts are totals since start, as Prometheus expects.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

SUB_BUCKETS = 1024  # 2^10: 0.1% resolution
QUANTILES = (0.5, 0.9, 0.99, 0.999)


def _index(value: int) -> int:
    if value < 2 * SUB_BUCKETS:
        return value
    shift = value.bit_length() - 11  # value >> shift falls in [1024, 2047]
    return (shift + 1) * SUB_BUCKETS + ((value >> shift) - SUB_BUCKETS)


def _highest_in(index: int) -> int:
    """The largest value that lands in this bucket."""
    if index < 2 * SUB_BUCKETS:
        return index
    shift = index // SUB_BUCKETS - 1
    mantissa = index % SUB_BUCKETS + SUB_BUCKETS
    return ((mantissa + 1) << shift) - 1


class Histogram:
    def __init__(self) -> None:
        self.counts: dict[int, int] = {}
        self.total = 0
        self.max = 0

    def record(self, value: int, count: int = 1) -> None:
        value = max(0, int(value))
        i = _index(value)
        self.counts[i] = self.counts.get(i, 0) + count
        self.total += count
        self.max = max(self.max, value)

    def record_corrected(self, value: int, expected_interval: int) -> None:
        self.record(value)
        if expected_interval <= 0:
            return
        missing = value - expected_interval
        while missing >= expected_interval:
            self.record(missing)
            missing -= expected_interval

    def merge(self, other: Histogram) -> None:
        for i, n in other.counts.items():
            self.counts[i] = self.counts.get(i, 0) + n
        self.total += other.total
        self.max = max(self.max, other.max)

    def percentile(self, q: float) -> int:
        """The value at quantile q (0..1): at or above that fraction of the recorded values."""
        if not self.total:
            return 0
        if q >= 1:
            return self.max
        rank = max(1, int(q * self.total + 0.999999))  # nearest rank, rounded up
        seen = 0
        for i in sorted(self.counts):
            seen += self.counts[i]
            if seen >= rank:
                return min(_highest_in(i), self.max)
        return self.max


class WindowedHistogram:
    """Percentiles over the last one to two windows; the count is the total since start."""

    def __init__(self, window_s: float = 60.0, now: Callable[[], float] = time.monotonic) -> None:
        self.window_s = window_s
        self._now = now
        self._current = Histogram()
        self._previous = Histogram()
        self._started = now()
        self.count = 0
        self.sum_us = 0

    def _rotate(self) -> None:
        if self._now() - self._started >= self.window_s:
            self._previous, self._current = self._current, Histogram()
            self._started = self._now()

    def record(self, value_us: int) -> None:
        self._rotate()
        self._current.record(value_us)
        self.count += 1
        self.sum_us += max(0, int(value_us))

    def record_corrected(self, value_us: int, expected_interval_us: int) -> None:
        self._rotate()
        before = self._current.total
        self._current.record_corrected(value_us, expected_interval_us)
        self.count += self._current.total - before
        self.sum_us += max(0, int(value_us))

    def snapshot(self) -> Histogram:
        self._rotate()
        merged = Histogram()
        merged.merge(self._previous)
        merged.merge(self._current)
        return merged


class LatencyRecorder(Collector):
    """One windowed histogram per hop. Thread-safe: Prometheus scrapes from its own thread."""

    def __init__(self, window_s: float = 60.0) -> None:
        self.window_s = window_s
        self.hops: dict[str, WindowedHistogram] = {}
        self.negative: dict[str, int] = {}
        self._lock = threading.Lock()

    def record_ns(self, hop: str, start_ns: int, end_ns: int) -> None:
        """Record end - start for a hop. A negative difference means the two clocks disagree
        (venue time against ours): it is counted, not recorded as a latency."""
        if not start_ns or not end_ns:
            return
        delta = end_ns - start_ns
        with self._lock:
            if delta < 0:
                self.negative[hop] = self.negative.get(hop, 0) + 1
                return
            self.hops.setdefault(hop, WindowedHistogram(self.window_s)).record(delta // 1000)

    def record_us(self, hop: str, value_us: int, expected_interval_us: int = 0) -> None:
        with self._lock:
            h = self.hops.setdefault(hop, WindowedHistogram(self.window_s))
            if expected_interval_us:
                h.record_corrected(value_us, expected_interval_us)
            else:
                h.record(value_us)

    def summary(self) -> dict[str, dict[str, float]]:
        """{hop: {"p50": ms, "p99": ms, ..., "max": ms, "count": n}} for logs and tests."""
        out = {}
        with self._lock:
            for hop, wh in self.hops.items():
                h = wh.snapshot()
                row = {f"p{q * 100:g}": h.percentile(q) / 1000 for q in QUANTILES}
                row["max"] = h.max / 1000
                row["count"] = wh.count
                out[hop] = row
        return out

    def collect(self) -> Iterator[Metric]:
        """A prometheus_client collector: latency_ms{hop, quantile} plus count and sum."""
        quantiles = GaugeMetricFamily(
            "md_latency_ms",
            "Latency per hop over the last 1-2 minutes, by quantile (1 = max)",
            labels=["hop", "quantile"],
        )
        counts = CounterMetricFamily(
            "md_latency_samples", "Latency samples per hop", labels=["hop"]
        )
        sums = CounterMetricFamily(
            "md_latency_ms_sum", "Total recorded latency per hop", labels=["hop"]
        )
        negative = CounterMetricFamily(
            "md_latency_clock_skew",
            "Samples where the end came before the start (clocks disagree)",
            labels=["hop"],
        )
        with self._lock:
            for hop, wh in sorted(self.hops.items()):
                h = wh.snapshot()
                for q in QUANTILES:
                    quantiles.add_metric([hop, f"{q:g}"], h.percentile(q) / 1000)
                quantiles.add_metric([hop, "1"], h.max / 1000)
                counts.add_metric([hop], wh.count)
                sums.add_metric([hop], wh.sum_us / 1000)
            for hop, n in sorted(self.negative.items()):
                negative.add_metric([hop], n)
        yield from (quantiles, counts, sums, negative)


# One recorder per process, registered with Prometheus once.
LATENCY = LatencyRecorder()
_registered = False


def register(registry: Any = None) -> LatencyRecorder:
    global _registered
    if not _registered:
        from prometheus_client import REGISTRY

        (registry or REGISTRY).register(LATENCY)
        _registered = True
    return LATENCY


def serve_metrics(port: int) -> None:
    """Expose /metrics on port in a background thread (for services without a web server)."""
    from prometheus_client import start_http_server

    register()
    start_http_server(port)
