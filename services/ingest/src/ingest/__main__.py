"""Run one venue adapter for a while and print a summary each second.

uv run python -m ingest kraken BTC-USD ETH-USD --seconds 30
uv run python -m ingest kraken BTC-USD --seconds 60 --record tests/fixtures/kraken_btcusd.jsonl.gz
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections import Counter
from contextlib import aclosing
from pathlib import Path
from typing import Any

from ingest.adapters.base import Connector, supervise, websocket_connect
from ingest.adapters.registry import ADAPTERS, load
from ingest.recording import RecordingConnector
from mdcommon.schema import ResetReason


async def run(venue: str, symbols: list[str], seconds: float, record: Path | None) -> int:
    recorder = RecordingConnector(websocket_connect, record) if record else None
    connect: Connector = recorder if recorder else websocket_connect
    adapter_class = load(venue)
    extra: dict[str, Any] = {}
    default_fetch = getattr(adapter_class, "default_fetch", None)
    if recorder and default_fetch:
        # Venues that sync from a REST snapshot get it recorded alongside the frames.
        extra["fetch"] = recorder.fetcher(default_fetch)
    adapter = adapter_class(symbols, connect=connect, **extra)
    counts: Counter[str] = Counter()
    resets = 0
    start = time.monotonic()
    next_report = start + 1
    try:
        # aclosing() shuts the supervisor (and the socket under it) down in order when we stop
        # early; otherwise asyncio's shutdown closes the nested generators concurrently.
        async with aclosing(supervise(adapter)) as envelopes:
            async for envelope in envelopes:
                kind = str(envelope.WhichOneof("payload"))
                counts[kind] += 1
                if kind == "reset":
                    resets += 1
                    reason = ResetReason.Name(envelope.reset.reason)
                    print(f"  reset {envelope.symbol}: {reason}", flush=True)
                now = time.monotonic()
                if now >= next_report:
                    parts = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
                    print(f"{now - start:5.0f}s  {parts}", flush=True)
                    next_report = now + 1
                if now - start >= seconds:
                    break
    finally:
        if recorder:
            recorder.close()
            print(f"recorded frames to {record}")
    return 1 if resets else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a venue adapter and print what it produces.")
    parser.add_argument("venue", choices=sorted(ADAPTERS))
    parser.add_argument("symbols", nargs="+", help="normalized symbols, for example BTC-USD")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument(
        "--record", type=Path, help="save every received frame (gzipped JSON lines)"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(asyncio.run(run(args.venue, args.symbols, args.seconds, args.record)))


if __name__ == "__main__":
    main()
