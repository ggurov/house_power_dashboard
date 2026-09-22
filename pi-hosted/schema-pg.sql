-- Plain-PostgreSQL schema for Pi-hosted installs (no TimescaleDB on armhf).
-- Same table/view names and columns as db/init.sql so the backend is untouched.
-- Aggregation is done by refresh.sql (systemd timer); retention too.

CREATE TABLE IF NOT EXISTS readings (
    ts            TIMESTAMPTZ NOT NULL,
    leg1_a        DOUBLE PRECISION NOT NULL,
    leg2_a        DOUBLE PRECISION NOT NULL,
    range_setting SMALLINT NOT NULL,
    host          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS readings_ts_idx ON readings (ts DESC);

-- Rollup tables mirror the continuous-aggregate shapes in db/init.sql.
CREATE TABLE IF NOT EXISTS power_1min (
    bucket TIMESTAMPTZ NOT NULL,
    host   TEXT NOT NULL DEFAULT '',
    leg1_a_avg DOUBLE PRECISION, leg1_a_max DOUBLE PRECISION,
    leg2_a_avg DOUBLE PRECISION, leg2_a_max DOUBLE PRECISION,
    samples BIGINT,
    PRIMARY KEY (bucket, host)
);
CREATE TABLE IF NOT EXISTS power_1hour (
    bucket TIMESTAMPTZ NOT NULL,
    host   TEXT NOT NULL DEFAULT '',
    leg1_a_avg DOUBLE PRECISION, leg1_a_max DOUBLE PRECISION,
    leg2_a_avg DOUBLE PRECISION, leg2_a_max DOUBLE PRECISION,
    samples BIGINT,
    PRIMARY KEY (bucket, host)
);
CREATE TABLE IF NOT EXISTS power_day (
    bucket TIMESTAMPTZ NOT NULL,
    host   TEXT NOT NULL DEFAULT '',
    leg1_a_avg DOUBLE PRECISION, leg1_a_max DOUBLE PRECISION,
    leg2_a_avg DOUBLE PRECISION, leg2_a_max DOUBLE PRECISION,
    samples BIGINT,
    PRIMARY KEY (bucket, host)
);
