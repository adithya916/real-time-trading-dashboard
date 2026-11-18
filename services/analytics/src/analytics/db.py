"""TimescaleDB: schema migrations and the batched trade writer.

Migrations are the .sql files in analytics/migrations, applied once each in name order and
recorded in schema_migrations. An advisory lock keeps two starting services from racing.

Trades are buffered and written with COPY into a temporary table, then moved into `trades`
with ON CONFLICT DO NOTHING, so a replayed session never counts a trade twice.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mdcommon.schema import Envelope

log = logging.getLogger(__name__)
MIGRATIONS = Path(__file__).parent / "migrations"
MIGRATION_LOCK = 7_310_001  # any constant shared by every service that migrates
COLUMNS = (
    "ts",
    "venue",
    "symbol",
    "trade_id",
    "price_ticks",
    "size_lots",
    "price_scale",
    "size_scale",
    "aggressor",
)
Row = tuple[datetime, str, str, int, int, int, int, int, int]


async def migrate(conn: Any, directory: Path = MIGRATIONS) -> list[str]:
    """Apply every migration not applied yet. Returns the names applied now."""
    applied_now: list[str] = []
    await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        for path in sorted(directory.glob("*.sql")):
            if path.name in done:
                continue
            log.info("migrating: %s", path.name)
            # A multi-statement script runs as one implicit transaction.
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
            applied_now.append(path.name)
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK)
    return applied_now


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def trade_row(envelope: Envelope, price_scale: int, size_scale: int) -> Row | None:
    """A trades row, or None for a trade id that isn't a number. Call it only once the
    symbol's book is in sync, so the scales are the ones its prices were published at."""
    trade = envelope.trade
    try:
        trade_id = int(trade.trade_id)
    except ValueError:
        return None
    ts_ns = envelope.ts_exch_ns or envelope.ts_recv_ns
    ts = EPOCH + timedelta(microseconds=ts_ns // 1000)  # Postgres keeps microseconds
    return (
        ts,
        envelope.venue,
        envelope.symbol,
        trade_id,
        trade.price_ticks,
        trade.size_lots,
        price_scale,
        size_scale,
        int(trade.aggressor),
    )


class TradeWriter:
    """Buffers trade rows and writes them in batches.

    The pool may arrive later (`pool = None` until the database is up and migrated): rows are
    kept meanwhile, up to max_buffered, so trades from the first seconds aren't lost.
    """

    def __init__(
        self,
        pool: Any = None,
        *,
        batch: int = 1000,
        every_s: float = 1.0,
        max_buffered: int = 200_000,
    ) -> None:
        self.pool = pool
        self.batch = batch
        self.every_s = every_s
        self.max_buffered = max_buffered
        self.rows: list[Row] = []
        self.written = 0
        self.dropped = 0
        self._last_flush = time.monotonic()

    def add(self, row: Row | None) -> None:
        if row is None:
            return
        if len(self.rows) >= self.max_buffered:
            self.dropped += 1
            return
        self.rows.append(row)

    def due(self) -> bool:
        if self.pool is None or not self.rows:
            return False
        return len(self.rows) >= self.batch or time.monotonic() - self._last_flush >= self.every_s

    async def flush(self) -> int:
        """Write the buffered rows. Returns how many were new."""
        if self.pool is None or not self.rows:
            return 0
        rows, self.rows = self.rows, []
        self._last_flush = time.monotonic()
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "CREATE TEMP TABLE IF NOT EXISTS trades_in (LIKE trades) ON COMMIT DELETE ROWS"
            )
            await conn.copy_records_to_table("trades_in", records=rows, columns=COLUMNS)
            status = await conn.execute(
                "INSERT INTO trades SELECT * FROM trades_in ON CONFLICT DO NOTHING"
            )
        new = int(status.rsplit(" ", 1)[-1])
        self.written += new
        return new
