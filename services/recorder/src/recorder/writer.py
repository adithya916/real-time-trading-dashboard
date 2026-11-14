"""Hourly Parquet files of normalized envelopes.

Layout (Hive-style, so DuckDB and pyarrow can filter on the path):

    <root>/venue=kraken/symbol=BTC-USD/date=2026-11-14/hour=12.parquet

Each row keeps the whole serialized Envelope, so a replay reproduces exactly what was
published, plus a few columns to filter on without decoding: seq, timestamps and kind.
A file is written as <hour>.parquet.partial and renamed when it is closed, so a crash
never leaves a truncated file that looks finished.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

import pyarrow as pa
import pyarrow.parquet as pq

from mdcommon.schema import Envelope

SCHEMA = pa.schema(
    [
        ("seq", pa.uint64()),
        ("ts_exch_ns", pa.int64()),
        ("ts_recv_ns", pa.int64()),
        ("kind", pa.dictionary(pa.int8(), pa.string())),
        ("envelope", pa.binary()),
    ]
)

_Key = tuple[str, str, str, int]  # venue, symbol, date, hour


def _hour_key(envelope: Envelope) -> _Key:
    when = datetime.fromtimestamp(envelope.ts_recv_ns / 1e9, tz=timezone.utc)
    return envelope.venue, envelope.symbol, when.strftime("%Y-%m-%d"), when.hour


def hour_path(root: Path, key: _Key) -> Path:
    venue, symbol, date, hour = key
    return (
        root / f"venue={venue}" / f"symbol={symbol}" / f"date={date}" / f"hour={hour:02d}.parquet"
    )


@dataclass
class _OpenFile:
    path: Path
    writer: pq.ParquetWriter
    rows: list[Envelope] = field(default_factory=list)

    @property
    def partial(self) -> Path:
        return self.path.with_suffix(".parquet.partial")


class ParquetRecorder:
    """Buffers envelopes and writes them as row groups to one file per venue, symbol and hour."""

    def __init__(self, root: Path, *, flush_rows: int = 10_000) -> None:
        self.root = root
        self.flush_rows = flush_rows
        self._open: dict[_Key, _OpenFile] = {}
        self.written = 0

    def add(self, envelope: Envelope) -> None:
        key = _hour_key(envelope)
        open_file = self._open.get(key)
        if open_file is None:
            self._close_older_hours(envelope.venue, envelope.symbol, key)
            open_file = self._open[key] = self._start(key)
        open_file.rows.append(envelope)
        if len(open_file.rows) >= self.flush_rows:
            self._flush(open_file)

    def flush(self) -> None:
        for open_file in self._open.values():
            self._flush(open_file)

    def close(self) -> None:
        for key in list(self._open):
            self._finish(key)

    def __enter__(self) -> ParquetRecorder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _start(self, key: _Key) -> _OpenFile:
        path = hour_path(self.root, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".parquet.partial")
        return _OpenFile(path, pq.ParquetWriter(partial, SCHEMA, compression="zstd"))

    def _flush(self, open_file: _OpenFile) -> None:
        if not open_file.rows:
            return
        rows = open_file.rows
        table = pa.table(
            {
                "seq": pa.array([e.seq for e in rows], pa.uint64()),
                "ts_exch_ns": pa.array([e.ts_exch_ns for e in rows], pa.int64()),
                "ts_recv_ns": pa.array([e.ts_recv_ns for e in rows], pa.int64()),
                "kind": pa.array([e.WhichOneof("payload") for e in rows]).dictionary_encode(),
                "envelope": pa.array([e.SerializeToString() for e in rows], pa.binary()),
            },
            schema=SCHEMA,
        )
        open_file.writer.write_table(table)
        self.written += len(rows)
        open_file.rows = []

    def _finish(self, key: _Key) -> None:
        open_file = self._open.pop(key)
        self._flush(open_file)
        open_file.writer.close()
        open_file.partial.replace(open_file.path)

    def _close_older_hours(self, venue: str, symbol: str, key: _Key) -> None:
        # Envelopes arrive in time order per stream, so a new hour means the old one is done.
        for other in [k for k in self._open if k[:2] == (venue, symbol) and k != key]:
            self._finish(other)


def read_envelopes(root: Path, venue: str = "*", symbol: str = "*") -> Iterator[Envelope]:
    """Every recorded envelope under root, file by file in time order, rows in written order."""
    pattern = f"venue={venue}/symbol={symbol}/date=*/hour=*.parquet"
    for path in sorted(root.glob(pattern), key=lambda p: (p.parent.name, p.name, str(p))):
        table = pq.read_table(path, columns=["envelope"])
        for blob in table.column("envelope").to_pylist():
            if blob is not None:
                yield Envelope.FromString(blob)
