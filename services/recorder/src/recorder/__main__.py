"""Record a venue's normalized envelopes to Parquet.

    uv run python -m recorder kraken BTC-USD ETH-USD --seconds 300 --root data
    uv run python -m recorder kraken BTC-USD --from-recording rec.jsonl.gz --root data

The recorder runs the adapter itself. --from-recording plays a raw venue recording (from
python -m ingest --record) through the adapter instead, so it can be turned into Parquet
offline and replayed with python -m recorder.replay.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from contextlib import aclosing
from pathlib import Path
from typing import Any

from ingest.adapters.base import supervise
from ingest.adapters.registry import ADAPTERS, load
from ingest.recording import ReplayConnector
from recorder.writer import ParquetRecorder


async def run(
    venue: str, symbols: list[str], seconds: float, root: Path, source: Path | None = None
) -> None:
    kwargs: dict[str, Any] = {}
    if source:
        replayer = ReplayConnector(source)
        kwargs["connect"] = replayer
        kwargs["clock"] = replayer.clock.now_ns  # keep the recorded receive times
        if venue == "binance":
            kwargs["fetch"] = replayer.fetch
    adapter = load(venue)(symbols, **kwargs)
    start = time.monotonic()
    with ParquetRecorder(root) as recorder:
        restarts = 0 if source else None
        async with aclosing(supervise(adapter, max_restarts=restarts)) as envelopes:
            async for envelope in envelopes:
                recorder.add(envelope)
                if not source and time.monotonic() - start >= seconds:
                    break
    files = sorted(p.relative_to(root) for p in root.rglob("*.parquet"))
    print(f"recorded {recorder.written} envelopes into {len(files)} file(s):")
    for path in files:
        print(f"  {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a venue to hourly Parquet files.")
    parser.add_argument("venue", choices=sorted(ADAPTERS))
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument(
        "--from-recording", type=Path, help="convert a raw venue recording instead of going live"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.venue, args.symbols, args.seconds, args.root, args.from_recording))


if __name__ == "__main__":
    main()
