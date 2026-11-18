"""Check the gateway's candle history against a DuckDB recomputation of the same trades.

Run after the Kraken replay, with analytics writing to TimescaleDB and the gateway reading it:

    python scripts/history_smoke.py http://localhost:8080 services/ingest/tests/fixtures/kraken_btcusd.jsonl.gz

The recording goes through the Kraken adapter again, here, and DuckDB buckets its trades the way
the continuous aggregates should: open and close by trade id, high, low, volume and count. Every
candle the gateway returns must match exactly, for 1 m, 5 m and 1 h.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from ingest.adapters.kraken import KrakenAdapter
from ingest.recording import load_frames

EPOCH = datetime(1970, 1, 1)
INTERVALS = {"1m": "1 minute", "5m": "5 minutes", "1h": "1 hour"}


def recorded_trades(path: Path) -> tuple[list[tuple[datetime, int, int, int]], int, int]:
    """(ts, trade_id, price_ticks, size_lots) for every trade analytics would store."""
    adapter = KrakenAdapter(["BTC-USD"])
    rows, synced, price_scale, size_scale = [], False, 0, 0
    for frame in load_frames(path):
        for e in adapter.handle(json.loads(frame, parse_float=Decimal)):
            if e.HasField("snapshot"):
                synced = True
                price_scale, size_scale = e.snapshot.price_scale, e.snapshot.size_scale
            elif e.HasField("reset"):
                synced = False
            elif e.HasField("trade") and synced:
                ts = EPOCH + timedelta(microseconds=(e.ts_exch_ns or e.ts_recv_ns) // 1000)
                rows.append((ts, int(e.trade.trade_id), e.trade.price_ticks, e.trade.size_lots))
    return rows, price_scale, size_scale


def expected(rows: list[tuple[datetime, int, int, int]], interval: str) -> list[tuple]:
    con = duckdb.connect()
    con.execute("CREATE TABLE t (ts TIMESTAMP, trade_id BIGINT, price BIGINT, size BIGINT)")
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?)", rows)
    return con.execute(
        f"""
        SELECT time_bucket(INTERVAL '{INTERVALS[interval]}', ts) AS bucket,
               arg_min(price, trade_id), max(price), min(price), arg_max(price, trade_id),
               sum(size), count(*)
        FROM (SELECT DISTINCT * FROM t)
        GROUP BY bucket ORDER BY bucket
        """
    ).fetchall()


def fetch(base: str, interval: str) -> list[dict]:
    url = f"{base}/history/candles?venue=kraken&symbol=BTC-USD&interval={interval}&limit=5000"
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def main(base: str, recording: Path, timeout_s: float = 30) -> None:
    rows, price_scale, size_scale = recorded_trades(recording)
    total = len({(r[1], r[0]) for r in rows})
    assert total > 0, "the recording has no trades"
    deadline = time.monotonic() + timeout_s
    while True:  # analytics writes in batches about once a second
        try:
            got = sum(c["trades"] for c in fetch(base, "1m"))
        except OSError as exc:
            got, error = -1, exc
        if got == total:
            break
        if time.monotonic() > deadline:
            raise SystemExit(
                f"history has {got} trades, expected {total} ({locals().get('error')})"
            )
        time.sleep(0.5)

    p, s = 10**price_scale, 10**size_scale
    for interval in INTERVALS:
        want = [
            {
                "time": int(bucket.replace(tzinfo=timezone.utc).timestamp()),
                "open": o / p,
                "high": h / p,
                "low": low / p,
                "close": c / p,
                "volume": v / s,
                "trades": n,
            }
            for bucket, o, h, low, c, v, n in expected(rows, interval)
        ]
        got_candles = fetch(base, interval)
        if got_candles != want:
            for g, w in zip(got_candles, want, strict=False):
                if g != w:
                    print("first difference:\n  gateway", g, "\n  duckdb ", w)
                    break
            raise SystemExit(f"{interval}: {len(got_candles)} candles, expected {len(want)}")
        print(f"history {interval}: {len(want)} candles match DuckDB ({total} trades)")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
