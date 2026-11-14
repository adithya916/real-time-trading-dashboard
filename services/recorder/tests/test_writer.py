import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

from ingest.adapters.kraken import KrakenAdapter
from ingest.recording import load_frames
from mdcommon.schema import Envelope, Trade
from recorder.writer import ParquetRecorder, hour_path, read_envelopes

KRAKEN_FIXTURE = (
    Path(__file__).parents[2] / "ingest" / "tests" / "fixtures" / "kraken_btcusd.jsonl.gz"
)


def kraken_envelopes() -> list[Envelope]:
    """Real envelopes: the Day 2 Kraken recording decoded by the adapter itself."""
    adapter = KrakenAdapter(["BTC-USD"])
    out: list[Envelope] = []
    for frame in load_frames(KRAKEN_FIXTURE):
        out.extend(adapter.handle(json.loads(frame, parse_float=Decimal)))
    return out


def trade_at(ts_recv_ns: int, seq: int) -> Envelope:
    envelope = Envelope(venue="test", symbol="AAA", seq=seq, ts_recv_ns=ts_recv_ns)
    envelope.trade.CopyFrom(Trade(trade_id=str(seq)))
    return envelope


NOON = 1_763_121_600 * 10**9  # 2025-11-14 12:00:00 UTC


def test_round_trip_keeps_every_envelope_in_order(tmp_path: Path) -> None:
    envelopes = kraken_envelopes()
    with ParquetRecorder(tmp_path, flush_rows=500) as recorder:
        for envelope in envelopes:
            recorder.add(envelope)

    read_back = list(read_envelopes(tmp_path, venue="kraken"))
    assert [e.SerializeToString() for e in read_back] == [e.SerializeToString() for e in envelopes]
    assert recorder.written == len(envelopes)


def test_index_columns_match_the_envelopes(tmp_path: Path) -> None:
    envelopes = kraken_envelopes()
    with ParquetRecorder(tmp_path) as recorder:
        for envelope in envelopes:
            recorder.add(envelope)

    [path] = list(tmp_path.rglob("*.parquet"))
    table = pq.read_table(path, columns=["seq", "kind"])
    assert table.column("seq").to_pylist() == [e.seq for e in envelopes]
    assert Counter(table.column("kind").to_pylist()) == Counter(
        str(e.WhichOneof("payload")) for e in envelopes
    )


def test_files_are_partitioned_by_hour_and_closed_when_the_hour_ends(tmp_path: Path) -> None:
    recorder = ParquetRecorder(tmp_path)
    recorder.add(trade_at(NOON - 1_000_000_000, 1))
    eleven = hour_path(tmp_path, ("test", "AAA", "2025-11-14", 11))
    recorder.add(trade_at(NOON + 1_000_000_000, 2))

    # The 11:00 file is finished as soon as 12:00 data arrives, without waiting for close().
    assert eleven.exists()
    assert eleven.relative_to(tmp_path).as_posix() == (
        "venue=test/symbol=AAA/date=2025-11-14/hour=11.parquet"
    )
    recorder.close()
    assert hour_path(tmp_path, ("test", "AAA", "2025-11-14", 12)).exists()
    assert [e.seq for e in read_envelopes(tmp_path)] == [1, 2]


def test_an_unclosed_file_stays_partial(tmp_path: Path) -> None:
    recorder = ParquetRecorder(tmp_path, flush_rows=1)
    recorder.add(trade_at(NOON, 1))
    path = hour_path(tmp_path, ("test", "AAA", "2025-11-14", 12))

    assert not path.exists()
    assert path.with_suffix(".parquet.partial").exists()
    assert list(read_envelopes(tmp_path)) == []
    recorder.close()
    assert path.exists() and not path.with_suffix(".parquet.partial").exists()


def test_flushes_write_separate_row_groups(tmp_path: Path) -> None:
    with ParquetRecorder(tmp_path, flush_rows=100) as recorder:
        for seq in range(1, 351):
            recorder.add(trade_at(NOON + seq, seq))

    [path] = list(tmp_path.rglob("*.parquet"))
    assert pq.ParquetFile(path).num_row_groups == 4
    assert [e.seq for e in read_envelopes(tmp_path)] == list(range(1, 351))
