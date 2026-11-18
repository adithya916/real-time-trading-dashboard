import asyncio
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analytics.db import COLUMNS, MIGRATIONS, TradeWriter, migrate, trade_row
from analytics.service import AnalyticsState

from mdcommon.schema import Aggressor, Envelope, Level, Snapshot, Trade


def trade(trade_id: str = "42", ts_ns: int = 1_763_123_456_789_123_999) -> Envelope:
    e = Envelope(venue="kraken", symbol="BTC-USD", seq=7, ts_exch_ns=ts_ns, ts_recv_ns=ts_ns + 5)
    e.trade.CopyFrom(
        Trade(
            trade_id=trade_id,
            price_ticks=671_612,
            size_lots=1_500,
            aggressor=Aggressor.AGGRESSOR_BUY,
        )
    )
    return e


def test_a_trade_becomes_a_row_with_microsecond_venue_time() -> None:
    row = trade_row(trade(), price_scale=1, size_scale=8)
    assert row is not None
    assert len(row) == len(COLUMNS)
    ts, venue, symbol, trade_id, price, size, price_scale, size_scale, aggressor = row
    assert ts == datetime(2025, 11, 14, 12, 30, 56, 789123, tzinfo=timezone.utc)
    assert (venue, symbol, trade_id, price, size) == ("kraken", "BTC-USD", 42, 671_612, 1_500)
    assert (price_scale, size_scale, aggressor) == (1, 8, int(Aggressor.AGGRESSOR_BUY))


def test_receive_time_stands_in_when_the_venue_sends_none() -> None:
    row = trade_row(trade(ts_ns=0), 1, 8)
    assert row is not None and row[0].year == 1970  # ts_recv_ns = 5 ns


def test_non_numeric_trade_ids_are_skipped() -> None:
    assert trade_row(trade("abc-1"), 1, 8) is None


def test_trades_are_only_rowed_once_the_book_has_scales() -> None:
    state = AnalyticsState()
    assert not state.on_envelope(trade()).synced
    snap = Envelope(venue="kraken", symbol="BTC-USD", seq=1)
    snap.snapshot.CopyFrom(
        Snapshot(
            bids=[Level(price_ticks=1, size_lots=1)],
            asks=[Level(price_ticks=2, size_lots=1)],
            price_scale=1,
            size_scale=8,
        )
    )
    state.on_envelope(snap)
    symbol = state.on_envelope(trade())
    assert symbol.synced and (symbol.price_scale, symbol.size_scale) == (1, 8)


class FakeConn:
    def __init__(self, applied: set[str] | None = None, insert_status: str = "INSERT 0 0") -> None:
        self.applied = set(applied or ())
        self.executed: list[str] = []
        self.copied: list[tuple[str, list[Any], tuple[str, ...]]] = []
        self.insert_status = insert_status

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append(sql)
        if sql.startswith("INSERT INTO schema_migrations"):
            self.applied.add(args[0])
        return self.insert_status if sql.startswith("INSERT INTO trades") else "OK"

    async def fetch(self, sql: str) -> list[dict[str, str]]:
        return [{"name": n} for n in self.applied]

    async def copy_records_to_table(
        self, table: str, *, records: list[Any], columns: tuple[str, ...]
    ) -> None:
        self.copied.append((table, records, columns))

    @asynccontextmanager
    async def transaction(self) -> Any:
        yield


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self.conn


def test_migrations_apply_once_in_name_order(tmp_path: Path) -> None:
    for name in ["20261201000000_b.sql", "20261118000000_a.sql", "20261120000000_c.sql"]:
        (tmp_path / name).write_text(f"-- {name}", encoding="utf-8")
    conn = FakeConn(applied={"20261120000000_c.sql"})
    applied = asyncio.run(migrate(conn, tmp_path))
    assert applied == ["20261118000000_a.sql", "20261201000000_b.sql"]
    assert conn.executed[0].startswith("SELECT pg_advisory_lock")
    assert conn.executed[-1].startswith("SELECT pg_advisory_unlock")
    assert asyncio.run(migrate(conn, tmp_path)) == []


def test_migration_files_are_named_by_timestamp() -> None:
    names = [p.name for p in MIGRATIONS.glob("*.sql")]
    assert names
    assert all(re.fullmatch(r"\d{14}_[a-z0-9_]+\.sql", n) for n in names)


def test_writer_copies_a_batch_and_counts_new_rows() -> None:
    conn = FakeConn(insert_status="INSERT 0 2")
    writer = TradeWriter(FakePool(conn), batch=3, every_s=60)
    for i in range(3):
        writer.add(trade_row(trade(str(i)), 1, 8))
    writer.add(None)
    assert writer.due()
    assert asyncio.run(writer.flush()) == 2
    table, records, columns = conn.copied[0]
    assert (table, len(records), columns) == ("trades_in", 3, COLUMNS)
    assert any("ON CONFLICT DO NOTHING" in sql for sql in conn.executed)
    assert writer.rows == [] and writer.written == 2
    assert asyncio.run(writer.flush()) == 0  # nothing buffered, nothing sent
    assert len(conn.copied) == 1


def test_writer_is_due_after_the_interval_even_with_a_small_batch() -> None:
    writer = TradeWriter(FakePool(FakeConn()), batch=1000, every_s=0.0)
    assert not writer.due()  # empty
    writer.add(trade_row(trade(), 1, 8))
    assert writer.due()
