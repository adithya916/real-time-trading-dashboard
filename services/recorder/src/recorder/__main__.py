"""Record a venue's normalized envelopes to Parquet.

    uv run python -m recorder kraken BTC-USD ETH-USD --seconds 300 --root data

Until the bus arrives on Day 4 the recorder runs the adapter itself; after that it subscribes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from contextlib import aclosing
from pathlib import Path

from ingest.adapters.base import supervise
from ingest.adapters.registry import ADAPTERS, load
from recorder.writer import ParquetRecorder


async def run(venue: str, symbols: list[str], seconds: float, root: Path) -> None:
    adapter = load(venue)(symbols)
    start = time.monotonic()
    with ParquetRecorder(root) as recorder:
        async with aclosing(supervise(adapter)) as envelopes:
            async for envelope in envelopes:
                recorder.add(envelope)
                if time.monotonic() - start >= seconds:
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
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.venue, args.symbols, args.seconds, args.root))


if __name__ == "__main__":
    main()
