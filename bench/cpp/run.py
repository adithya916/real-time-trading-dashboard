"""Benchmark the C++ core and compare it with the Python reference on real data.

Run from core/ after building the benchmark binary and installing bookcore:

    cmake -S . -B build-bench -DCMAKE_BUILD_TYPE=Release -DBOOKCORE_TESTS=OFF -DBOOKCORE_BENCH=ON
    cmake --build build-bench
    uv sync --no-editable
    uv run --no-sync python ../bench/cpp/run.py --bench-binary build-bench/bookcore_bench

Two kinds of numbers:
  1. C++ alone (Google Benchmark): book updates, checksums and engine orders per second on
     seeded synthetic streams shaped like a live feed.
  2. The recorded Kraken and Binance updates applied from Python to PyBook and to
     bookcore.L2Book. This includes the cost of crossing into C++ on every call, so it is
     the speedup the ingest service would see if it called the C++ book update by update.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from bookcore import L2Book, Side
from ingest.adapters.binance import BinanceAdapter
from ingest.adapters.kraken import KrakenAdapter
from ingest.book.pybook import PyBook
from ingest.recording import load_frames, load_rest
from mdcommon.schema import Envelope

FIXTURES = Path(__file__).parents[2] / "services" / "ingest" / "tests" / "fixtures"
Update = tuple[str, int, int]
Message = list[Update]


def updates_from(envelopes: list[Envelope]) -> list[Message]:
    """Level updates grouped by the message they arrived in."""
    out: list[Message] = []
    sides = {1: "bid", 2: "ask"}
    for envelope in envelopes:
        if envelope.HasField("snapshot"):
            out.append(
                [("bid", lv.price_ticks, lv.size_lots) for lv in envelope.snapshot.bids]
                + [("ask", lv.price_ticks, lv.size_lots) for lv in envelope.snapshot.asks]
            )
        elif envelope.HasField("delta"):
            out.append(
                [(sides[u.side], u.price_ticks, u.size_lots) for u in envelope.delta.updates]
            )
    return out


def kraken_updates() -> list[Message]:
    adapter = KrakenAdapter(["BTC-USD"])
    envelopes: list[Envelope] = []
    for frame in load_frames(FIXTURES / "kraken_btcusd.jsonl.gz"):
        envelopes += adapter.handle(json.loads(frame, parse_float=Decimal))
    return updates_from(envelopes)


def binance_updates() -> list[Message]:
    path = FIXTURES / "binance_btcusdt.jsonl.gz"
    frames, snapshot = load_frames(path), load_rest(path)[0]

    class Socket:
        async def send(self, message: str) -> None: ...

        async def recv(self) -> str:
            if not frames:
                raise ConnectionError
            return frames.pop(0)

    @asynccontextmanager
    async def connect(url: str) -> AsyncIterator[Socket]:
        yield Socket()

    async def fetch(symbol: str) -> dict[str, Any]:
        return snapshot

    async def collect() -> list[Envelope]:
        out: list[Envelope] = []
        try:
            async for e in BinanceAdapter(["BTC-USDT"], connect=connect, fetch=fetch).stream():
                out.append(e)
        except ConnectionError:
            pass
        return out

    return updates_from(asyncio.run(collect()))


def rate(apply_all: Callable[[], None], count: int, min_seconds: float = 2.0) -> float:
    """Updates per second, repeating the whole stream until at least min_seconds have passed."""
    apply_all()  # warm up
    runs, start = 0, time.perf_counter()
    while (elapsed := time.perf_counter() - start) < min_seconds or runs == 0:
        apply_all()
        runs += 1
    return runs * count / elapsed


def compare(name: str, messages: list[Message]) -> dict[str, Any]:
    updates = [u for message in messages for u in message]
    cpp_updates = [(Side.BID if s == "bid" else Side.ASK, p, q) for s, p, q in updates]
    batches = [[(1 if s == "bid" else 2, p, q) for s, p, q in message] for message in messages]

    def python_book() -> None:
        book = PyBook()
        for side, price, size in updates:
            book.apply(side, price, size)  # type: ignore[arg-type]

    def cpp_per_update() -> None:
        book = L2Book()
        for side, price, size in cpp_updates:
            book.apply(side, price, size)

    def cpp_per_message() -> None:
        book = L2Book()
        for batch in batches:
            book.apply_many(batch)

    py_rate = rate(python_book, len(updates))
    per_update = rate(cpp_per_update, len(updates))
    per_message = rate(cpp_per_message, len(updates))
    return {
        "stream": name,
        "messages": len(messages),
        "updates": len(updates),
        "python_per_s": py_rate,
        "cpp_per_update_per_s": per_update,
        "cpp_per_message_per_s": per_message,
        "speedup_per_update": per_update / py_rate,
        "speedup_per_message": per_message / py_rate,
    }


def google_benchmark(binary: Path) -> list[dict[str, Any]]:
    result = subprocess.run(
        [str(binary), "--benchmark_format=json", "--benchmark_min_time=1s"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        {
            "name": b["name"],
            "items_per_s": b.get("items_per_second"),
            "time": b["real_time"],
            "unit": b["time_unit"],
        }
        for b in json.loads(result.stdout)["benchmarks"]
    ]


def markdown(report: dict[str, Any]) -> str:
    env = report["environment"]
    lines = [
        "# C++ core benchmarks",
        "",
        f"Machine: {env['machine']} · {env['os']} · {env['cpu_count']} CPUs · Python {env['python']}",
        "",
        "## C++ alone (Google Benchmark, seeded synthetic streams)",
        "",
        "| Benchmark | Throughput | Time per run |",
        "| --- | ---: | ---: |",
    ]
    for b in report["google_benchmark"]:
        lines.append(
            f"| {b['name']} | {b['items_per_s'] / 1e6:,.2f} M/s | {b['time']:,.1f} {b['unit']} |"
        )
    lines += [
        "",
        "## Recorded updates applied from Python",
        "",
        "| Stream | Messages | Updates | PyBook | C++, one call per update | C++, one call per message |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for c in report["recorded"]:
        lines.append(
            f"| {c['stream']} | {c['messages']:,} | {c['updates']:,} | "
            f"{c['python_per_s'] / 1e6:,.2f} M/s | "
            f"{c['cpp_per_update_per_s'] / 1e6:,.2f} M/s ({c['speedup_per_update']:.1f}×) | "
            f"{c['cpp_per_message_per_s'] / 1e6:,.2f} M/s ({c['speedup_per_message']:.1f}×) |"
        )
    lines += [
        "",
        "## Method",
        "",
        "- Google Benchmark builds each input up front, so the timed loop is only the book or engine.",
        "  `BM_L2BookApply`: 1M updates, mostly within a few levels of the top, a quarter deletes.",
        "  `BM_MatchingEngine`: 500k actions, 70% limit, 10% market or IOC, 20% cancels.",
        "- The recorded comparison drives each book from Python. One call per update pays the cost",
        "  of entering C++ on every level, which is more than a Python dict update costs, so it is",
        "  slower than PyBook. `apply_many` crosses once per message; Kraken messages carry one or",
        "  two levels, so batching helps little there, while Binance messages carry dozens.",
        "- Release build (-O2/-O3 per the compiler's CMake defaults); single thread.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bench-binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path(__file__).parents[1] / "results" / "cpp")
    args = parser.parse_args()

    print("google benchmark...", flush=True)
    gbench = google_benchmark(args.bench_binary)
    print("recorded streams...", flush=True)
    recorded = [
        compare("Kraken BTC/USD", kraken_updates()),
        compare("Binance BTCUSDT", binance_updates()),
    ]
    report = {
        "environment": {
            "machine": os.environ.get("BENCH_MACHINE") or platform.node(),
            "os": platform.platform(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        },
        "google_benchmark": gbench,
        "recorded": recorded,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    args.out.with_suffix(".md").write_text(markdown(report))
    print(markdown(report))


if __name__ == "__main__":
    main()
