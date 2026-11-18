-- Trades as integer ticks and lots, with the scales they were published at, and candles built
-- from them by continuous aggregates. Migrations are named by timestamp so two branches never
-- claim the same number; analytics applies them in name order at startup.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE trades (
    ts          timestamptz NOT NULL,  -- venue time
    venue       text        NOT NULL,
    symbol      text        NOT NULL,
    trade_id    bigint      NOT NULL,  -- the venue's id, increasing per symbol; orders open/close
    price_ticks bigint      NOT NULL,
    size_lots   bigint      NOT NULL,
    price_scale smallint    NOT NULL,
    size_scale  smallint    NOT NULL,
    aggressor   smallint    NOT NULL,
    -- Replaying a session twice must not count its trades twice.
    UNIQUE (venue, symbol, trade_id, ts)
);

SELECT create_hypertable('trades', by_range('ts', INTERVAL '1 day'));

-- One view per interval, each straight from trades. materialized_only = false adds trades newer
-- than the last refresh at query time, so the latest candle is always current.
CREATE MATERIALIZED VIEW candles_1m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 minute', ts) AS bucket, venue, symbol,
       first(price_ticks, trade_id) AS open_ticks,
       max(price_ticks) AS high_ticks,
       min(price_ticks) AS low_ticks,
       last(price_ticks, trade_id) AS close_ticks,
       sum(size_lots) AS volume_lots,
       count(*) AS trades,
       max(price_scale) AS price_scale,
       max(size_scale) AS size_scale
FROM trades
GROUP BY bucket, venue, symbol
WITH NO DATA;

CREATE MATERIALIZED VIEW candles_5m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '5 minutes', ts) AS bucket, venue, symbol,
       first(price_ticks, trade_id) AS open_ticks,
       max(price_ticks) AS high_ticks,
       min(price_ticks) AS low_ticks,
       last(price_ticks, trade_id) AS close_ticks,
       sum(size_lots) AS volume_lots,
       count(*) AS trades,
       max(price_scale) AS price_scale,
       max(size_scale) AS size_scale
FROM trades
GROUP BY bucket, venue, symbol
WITH NO DATA;

CREATE MATERIALIZED VIEW candles_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', ts) AS bucket, venue, symbol,
       first(price_ticks, trade_id) AS open_ticks,
       max(price_ticks) AS high_ticks,
       min(price_ticks) AS low_ticks,
       last(price_ticks, trade_id) AS close_ticks,
       sum(size_lots) AS volume_lots,
       count(*) AS trades,
       max(price_scale) AS price_scale,
       max(size_scale) AS size_scale
FROM trades
GROUP BY bucket, venue, symbol
WITH NO DATA;

-- start_offset NULL: replays insert old trades, so refreshes must reach back to the beginning.
-- Only ranges with new trades are recomputed, so this stays cheap.
SELECT add_continuous_aggregate_policy('candles_1m',
    start_offset => NULL, end_offset => INTERVAL '1 minute', schedule_interval => INTERVAL '1 minute');
SELECT add_continuous_aggregate_policy('candles_5m',
    start_offset => NULL, end_offset => INTERVAL '5 minutes', schedule_interval => INTERVAL '5 minutes');
SELECT add_continuous_aggregate_policy('candles_1h',
    start_offset => NULL, end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '1 hour');
