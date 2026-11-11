"""Measure the legacy stack: the "before" numbers every later claim is compared with.

Start the stack first (docker compose up -d --build --wait), then:

    uv run --directory bench python baseline/run.py --out results/baseline

Three phases:
  1. pipeline: subscribe to every Redis channel and measure ticks/s and the
     latency from tick generation to a Redis subscriber.
  2. one-client: one WebSocket viewer on one symbol; messages/s, bytes/s and latency.
  3. ramp: more and more viewers at once, spread over all symbols and over several
     client processes, until p99 latency passes the limit.

Latency is "receive time minus the tick's generation timestamp". The generator,
server and this script run on one machine, so they share a clock.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import os
import platform
import subprocess
import time
from collections import Counter
from pathlib import Path

import redis.asyncio as redis
import websockets

SYMBOLS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]
# Parse one message in this many for latency; every message is still counted.
# Parsing all of them would make the load generator the bottleneck.
LATENCY_SAMPLE_EVERY = 10


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q / 100 * (len(ordered) - 1))))
    return ordered[index]


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "samples": len(values),
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99),
        "max_ms": max(values) if values else None,
    }


# ---------------------------------------------------------------- pipeline


async def pipeline_phase(redis_url: str, duration: float, warmup: float) -> dict:
    client = redis.from_url(redis_url)
    pubsub = client.pubsub()
    await pubsub.psubscribe("market_data_feed:*")
    counts: Counter[str] = Counter()
    latencies: list[float] = []
    start = time.monotonic()
    measure_from = start + warmup
    end = measure_from + duration
    try:
        while (now_mono := time.monotonic()) < end:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None or now_mono < measure_from:
                continue
            received = time.time()
            payload = json.loads(message["data"])
            counts[payload["symbol"]] += 1
            latencies.append((received - payload["data"]["timestamp"]) * 1000)
    finally:
        await pubsub.aclose()
        await client.aclose()
    total = sum(counts.values())
    return {
        "ticks_per_s": total / duration,
        "per_symbol_per_s": {s: counts[s] / duration for s in SYMBOLS},
        "generation_to_subscriber": latency_summary(latencies),
    }


# ---------------------------------------------------------------- viewers


async def one_viewer(url: str, duration: float, warmup: float) -> dict:
    count = 0
    nbytes = 0
    latencies: list[float] = []
    async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
        start = time.monotonic()
        measure_from = start + warmup
        end = measure_from + duration
        while (remaining := end - time.monotonic()) > 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), remaining)
            except asyncio.TimeoutError:
                break
            if time.monotonic() < measure_from:
                continue
            count += 1
            nbytes += len(raw)
            if count % LATENCY_SAMPLE_EVERY == 0:
                tick_time = json.loads(raw)["data"]["timestamp"]
                latencies.append((time.time() - tick_time) * 1000)
    return {
        "msgs_per_s": count / duration,
        "bytes_per_s": nbytes / duration,
        "latencies": latencies,
    }


def _viewer_process(args: tuple[list[str], float, float]) -> list[dict]:
    urls, duration, warmup = args

    async def run_all() -> list[dict]:
        results = await asyncio.gather(
            *(one_viewer(u, duration, warmup) for u in urls), return_exceptions=True
        )
        return [
            r if isinstance(r, dict) else {"error": repr(r), "msgs_per_s": 0, "latencies": []}
            for r in results
        ]

    return asyncio.run(run_all())


def run_viewers(base: str, n: int, processes: int, duration: float, warmup: float) -> list[dict]:
    urls = [f"{base}/ws/stream/{SYMBOLS[i % len(SYMBOLS)]}" for i in range(n)]
    processes = max(1, min(processes, n))
    chunks = [urls[i::processes] for i in range(processes)]
    with mp.get_context("spawn").Pool(processes) as pool:
        nested = pool.map(_viewer_process, [(c, duration, warmup) for c in chunks])
    return [r for chunk in nested for r in chunk]


def one_client_phase(base: str, duration: float, warmup: float) -> dict:
    [result] = run_viewers(base, 1, 1, duration, warmup)
    return {
        "symbol": SYMBOLS[0],
        "msgs_per_s": result["msgs_per_s"],
        "bytes_per_s": result.get("bytes_per_s", 0),
        "latency": latency_summary(result["latencies"]),
        "error": result.get("error"),
    }


def ramp_phase(
    base: str, steps: list[int], processes: int, duration: float, warmup: float, limit_ms: float
) -> dict:
    rows = []
    saturated_at = None
    for n in steps:
        results = run_viewers(base, n, processes, duration, warmup)
        latencies = [x for r in results for x in r["latencies"]]
        rates = sorted(r["msgs_per_s"] for r in results)
        summary = latency_summary(latencies)
        row = {
            "viewers": n,
            "errors": sum(1 for r in results if "error" in r),
            "median_msgs_per_s_per_viewer": percentile(rates, 50),
            "total_msgs_per_s": sum(rates),
            "latency": summary,
        }
        rows.append(row)
        print(
            f"  {n:>4} viewers: {row['median_msgs_per_s_per_viewer']:,.0f} msgs/s each, "
            f"p99 {summary['p99_ms'] or 0:,.1f} ms, errors {row['errors']}",
            flush=True,
        )
        if (summary["p99_ms"] or 0) > limit_ms or row["errors"]:
            saturated_at = n
            break
    return {"limit_p99_ms": limit_ms, "steps": rows, "first_step_over_limit": saturated_at}


# ---------------------------------------------------------------- report


def environment() -> dict:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = os.environ.get("GITHUB_SHA", "unknown")[:7]
    return {
        "machine": os.environ.get("BENCH_MACHINE") or platform.node(),
        "os": platform.platform(),
        "cpu_count": os.cpu_count(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "git_sha": sha,
    }


def fmt(value: float | int | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def kib(value: float | None) -> float | None:
    return None if value is None else value / 1024


def markdown(report: dict) -> str:
    env = report["environment"]
    pipe = report["pipeline"]
    one = report["one_client"]
    ramp = report["ramp"]
    gen = pipe["generation_to_subscriber"]
    lines = [
        "# Baseline: legacy stack",
        "",
        f"Machine: {env['machine']} Â· {env['os']} Â· {env['cpu_count']} CPUs Â· "
        f"Python {env['python']} Â· commit {env['git_sha']}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Pipeline throughput, all symbols | {fmt(pipe['ticks_per_s'], 0)} ticks/s |",
        f"| Generation â†’ Redis subscriber latency p50 / p99 | "
        f"{fmt(gen.get('p50_ms'))} / {fmt(gen.get('p99_ms'))} ms |",
        f"| One viewer on {one['symbol']}: messages received | {fmt(one['msgs_per_s'], 0)} /s |",
        f"| One viewer: bytes received | {fmt(kib(one['bytes_per_s']), 0)} KiB/s |",
        f"| One viewer: generation â†’ browser latency p50 / p99 | "
        f"{fmt(one['latency'].get('p50_ms'))} / {fmt(one['latency'].get('p99_ms'))} ms |",
        "| One viewer: messages the current UI draws | 10 /s |",
        "",
        f"## Viewer ramp (stops when p99 passes {fmt(ramp['limit_p99_ms'], 0)} ms)",
        "",
        "| Viewers | Messages/s per viewer (median) | All viewers, messages/s | p50 ms | p99 ms | Errors |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in ramp["steps"]:
        lat = row["latency"]
        lines.append(
            f"| {row['viewers']} | {fmt(row['median_msgs_per_s_per_viewer'], 0)} | "
            f"{fmt(row['total_msgs_per_s'], 0)} | {fmt(lat['p50_ms'])} | {fmt(lat['p99_ms'])} | "
            f"{row['errors']} |"
        )
    over = ramp["first_step_over_limit"]
    lines += [
        "",
        f"p99 first passed the limit at **{over} viewer{'' if over == 1 else 's'}**."
        if over
        else "p99 stayed under the limit at every step.",
        "",
        "## Method and caveats",
        "",
        f"- Each phase discards a {fmt(report['settings']['warmup_s'], 0)} s warm-up, "
        f"then measures for {fmt(report['settings']['duration_s'], 0)} s "
        f"({fmt(report['settings']['ramp_duration_s'], 0)} s per ramp step).",
        "- Latency is receive time minus the tick's generation timestamp. Generator, server "
        "and load generator share one machine, so they share a clock, and they also share CPU.",
        f"- Viewers parse 1 message in {LATENCY_SAMPLE_EVERY} for latency and count all of them.",
        f"- Ramp viewers are spread over {report['settings']['processes']} client processes "
        "and all five symbols.",
        "- The Python load generator can saturate before the server does; Day 7 replaces it "
        "with a Go client that corrects for coordinated omission.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ws", default="ws://localhost:8000")
    parser.add_argument("--redis", default="redis://localhost:6379")
    parser.add_argument("--duration", type=float, default=30, help="seconds per phase")
    parser.add_argument("--ramp-duration", type=float, default=10, help="seconds per ramp step")
    parser.add_argument("--warmup", type=float, default=2)
    parser.add_argument("--steps", default="1,2,5,10,25,50,100")
    parser.add_argument("--processes", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--limit-ms", type=float, default=100)
    parser.add_argument("--out", default="results/baseline", help="path without extension")
    parser.add_argument(
        "--phases", default="pipeline,one,ramp", help="comma-separated subset to run"
    )
    args = parser.parse_args()
    phases = set(args.phases.split(","))

    settings = {
        "duration_s": args.duration,
        "ramp_duration_s": args.ramp_duration,
        "warmup_s": args.warmup,
        "processes": args.processes,
    }
    pipeline = {"ticks_per_s": None, "per_symbol_per_s": {}, "generation_to_subscriber": {}}
    if "pipeline" in phases:
        print("pipeline phase...", flush=True)
        pipeline = asyncio.run(pipeline_phase(args.redis, args.duration, args.warmup))
        print(f"  {pipeline['ticks_per_s']:,.0f} ticks/s", flush=True)
    one = {"symbol": SYMBOLS[0], "msgs_per_s": None, "bytes_per_s": None, "latency": {}}
    if "one" in phases:
        print("one-client phase...", flush=True)
        one = one_client_phase(args.ws, args.duration, args.warmup)
        print(f"  {one['msgs_per_s']:,.0f} msgs/s", flush=True)
    ramp: dict = {"limit_p99_ms": args.limit_ms, "steps": [], "first_step_over_limit": None}
    if "ramp" in phases:
        print("ramp phase...", flush=True)
        steps = [int(s) for s in args.steps.split(",")]
        ramp = ramp_phase(
            args.ws, steps, args.processes, args.ramp_duration, args.warmup, args.limit_ms
        )

    report = {
        "environment": environment(),
        "settings": settings,
        "pipeline": pipeline,
        "one_client": one,
        "ramp": ramp,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    out.with_suffix(".md").write_text(markdown(report))
    print(f"wrote {out.with_suffix('.json')} and {out.with_suffix('.md')}")


if __name__ == "__main__":
    main()
