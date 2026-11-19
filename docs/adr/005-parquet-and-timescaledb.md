# 005: Parquet for raw history, TimescaleDB for candles

## Context

Two different needs: keep everything that was published so a session can be replayed and
studied exactly, and answer "give me the last 500 one-minute candles" fast enough for a chart
that opens with history. Both on a free server with a few GB of disk and memory.

## Decision

- **Raw events → Parquet.** The recorder writes every envelope, serialized, into hourly files
  partitioned Hive-style by venue, symbol, date and hour, with seq, timestamps and kind as
  columns to filter on without decoding. Files are written as `.partial` and renamed when
  closed. `recorder.replay` publishes them back onto the bus at any speed; DuckDB or pyarrow
  read them for research.
- **Trades → TimescaleDB.** Analytics writes every trade in batches (`COPY` into a temporary
  table, then `INSERT … ON CONFLICT DO NOTHING`, so replays never double count). Continuous
  aggregates build 1 m, 5 m and 1 h candles with real-time aggregation for the newest bucket,
  ordered by venue trade id for open and close. The gateway serves them at
  `/history/candles`.

## Alternatives

- **Everything in TimescaleDB**: one store, but the raw stream is write-once and read
  sequentially, which files do cheaply; keeping it out keeps the database small.
- **QuestDB / ClickHouse**: fast time-series engines, but heavier to run on the free server,
  and their published speed claims are vendor-run; Postgres is what backend reviewers know.
- **Candles computed on read from Parquet**: no database at all, but every chart load would
  scan files; continuous aggregates keep it to an index lookup.

## Consequences

- Replays and research read files; charts read a database. The integration job checks every
  candle against a DuckDB recomputation of the same trades.
- Two retention policies to manage: Parquet by disk space, TimescaleDB by its own policies
  (not configured yet; the free server's disk is the limit).
- Refresh policies reach back to the start because replays insert old trades; only changed
  ranges are recomputed, so that stays cheap.
