"""Candle history from TimescaleDB, for charts that open with the past already drawn.

    GET /history/candles?venue=kraken&symbol=BTC-USD&interval=1m&limit=500

Reads the continuous aggregates analytics maintains (candles_1m, candles_5m, candles_1h) and
returns the newest `limit` candles, oldest first, as prices and sizes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)
VIEWS = {"1m": "candles_1m", "5m": "candles_5m", "1h": "candles_1h"}
MAX_LIMIT = 5000


class Unavailable(Exception):
    """No database configured, or it can't be reached right now."""


class History:
    def __init__(self, db_url: str | None) -> None:
        self.db_url = db_url
        self._pool: Any = None
        self._lock = asyncio.Lock()

    async def pool(self) -> Any:
        if not self.db_url:
            raise Unavailable("this gateway has no database (start it with --db)")
        async with self._lock:
            if self._pool is None:
                import asyncpg

                try:
                    self._pool = await asyncpg.create_pool(
                        self.db_url, min_size=1, max_size=4, timeout=3
                    )
                except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
                    raise Unavailable(f"database unreachable ({exc!r})") from exc
        return self._pool

    async def candles(
        self, venue: str, symbol: str, interval: str, limit: int
    ) -> list[dict[str, Any]]:
        view = VIEWS[interval]  # validated by the caller; never interpolate user input
        pool = await self.pool()
        try:
            rows = await pool.fetch(
                f"SELECT bucket, open_ticks, high_ticks, low_ticks, close_ticks, volume_lots,"
                f" trades, price_scale, size_scale FROM {view}"
                " WHERE venue = $1 AND symbol = $2 ORDER BY bucket DESC LIMIT $3",
                venue,
                symbol,
                min(limit, MAX_LIMIT),
            )
        except Exception as exc:
            raise Unavailable(f"history query failed ({exc!r})") from exc
        return [candle(row) for row in reversed(rows)]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()


def candle(row: Any) -> dict[str, Any]:
    price, size = 10 ** row["price_scale"], 10 ** row["size_scale"]
    return {
        "time": int(row["bucket"].timestamp()),
        "open": row["open_ticks"] / price,
        "high": row["high_ticks"] / price,
        "low": row["low_ticks"] / price,
        "close": row["close_ticks"] / price,
        "volume": int(row["volume_lots"]) / size,
        "trades": row["trades"],
    }
