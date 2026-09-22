-- house_power_dashboard schema (TimescaleDB).
-- Raw readings at ~1s resolution, kept 90 days.
-- Continuous aggregates (1 min / 1 hour / 1 day) kept indefinitely.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS readings (
    ts            TIMESTAMPTZ NOT NULL,
    leg1_a        DOUBLE PRECISION NOT NULL,
    leg2_a        DOUBLE PRECISION NOT NULL,
    range_setting SMALLINT NOT NULL,
    host          TEXT NOT NULL DEFAULT ''
);

SELECT create_hypertable('readings', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS readings_ts_idx ON readings (ts DESC);

-- 1-minute aggregates (avg/max amps per leg + sample count).
CREATE MATERIALIZED VIEW IF NOT EXISTS power_1min
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', ts) AS bucket,
       host,
       AVG(leg1_a) AS leg1_a_avg,
       MAX(leg1_a) AS leg1_a_max,
       AVG(leg2_a) AS leg2_a_avg,
       MAX(leg2_a) AS leg2_a_max,
       COUNT(*)    AS samples
FROM readings
GROUP BY bucket, host
WITH NO DATA;

-- 1-hour aggregates rolled up from the minute view.
CREATE MATERIALIZED VIEW IF NOT EXISTS power_1hour
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', bucket) AS bucket,
       host,
       AVG(leg1_a_avg) AS leg1_a_avg,
       MAX(leg1_a_max) AS leg1_a_max,
       AVG(leg2_a_avg) AS leg2_a_avg,
       MAX(leg2_a_max) AS leg2_a_max,
       SUM(samples)    AS samples
FROM power_1min
GROUP BY bucket, host
WITH NO DATA;

-- Daily aggregates for multi-year history.
CREATE MATERIALIZED VIEW IF NOT EXISTS power_day
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', bucket) AS bucket,
       host,
       AVG(leg1_a_avg) AS leg1_a_avg,
       MAX(leg1_a_max) AS leg1_a_max,
       AVG(leg2_a_avg) AS leg2_a_avg,
       MAX(leg2_a_max) AS leg2_a_max,
       SUM(samples)    AS samples
FROM power_1hour
GROUP BY bucket, host
WITH NO DATA;

-- Raw data: full resolution ~3 months, then dropped (aggregates live on).
SELECT add_retention_policy('readings', INTERVAL '90 days', if_not_exists => TRUE);

-- Keep aggregates fresh.
SELECT add_continuous_aggregate_policy('power_1min',
    start_offset => INTERVAL '2 hours',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('power_1hour',
    start_offset => INTERVAL '1 day',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('power_day',
    start_offset => INTERVAL '30 days',
    end_offset   => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE);
