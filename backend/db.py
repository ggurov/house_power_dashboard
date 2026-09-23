"""Persistence: TimescaleDB readings + aggregates.

Watts are NEVER stored — only amps (the measured truth). Voltage is an
assumption applied at query time so it stays adjustable.
"""

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("db")

SCHEMA_BASE = [
    """CREATE TABLE IF NOT EXISTS readings (
        ts TIMESTAMPTZ NOT NULL,
        leg1_a DOUBLE PRECISION NOT NULL,
        leg2_a DOUBLE PRECISION NOT NULL,
        range_setting SMALLINT NOT NULL,
        host TEXT NOT NULL DEFAULT '')""",
    "CREATE INDEX IF NOT EXISTS readings_ts_idx ON readings (ts DESC)",
]

# Aggregate view/table names for history routing (CAGGs on TimescaleDB,
# plain rollup tables on Pi-hosted PostgreSQL — same shape).
AGG_VIEWS = ("power_1min", "power_1hour", "power_day")

# Plain-PostgreSQL rollup tables (Pi-hosted, no TimescaleDB on armhf).
# Same names/columns as the continuous aggregates in db/init.sql, refreshed
# by pi-hosted/refresh.sql on a timer — history() works unchanged.
ROLLUP_TABLES = [
    """CREATE TABLE IF NOT EXISTS %s (
        bucket TIMESTAMPTZ NOT NULL,
        host TEXT NOT NULL DEFAULT '',
        leg1_a_avg DOUBLE PRECISION, leg1_a_max DOUBLE PRECISION,
        leg2_a_avg DOUBLE PRECISION, leg2_a_max DOUBLE PRECISION,
        samples BIGINT,
        PRIMARY KEY (bucket, host))""" % t
    for t in AGG_VIEWS
]


class Store:
    """Thin wrapper over a psycopg connection; created via connect()."""

    def __init__(self, conn):
        self.conn = conn

    @classmethod
    def connect(cls, dsn=None):
        import psycopg

        dsn = dsn or os.environ.get("DB_DSN",
                                    "postgresql://postgres:postgres@localhost:5432/house_power")
        conn = psycopg.connect(dsn, autocommit=True)
        store = cls(conn)
        store.ensure_schema()
        return store

    def ensure_schema(self):
        with self.conn.cursor() as cur:
            for stmt in SCHEMA_BASE:
                cur.execute(stmt)
            cur.execute("SELECT 1 FROM pg_available_extensions"
                        " WHERE name = 'timescaledb'")
            if cur.fetchone():
                cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
                cur.execute("SELECT create_hypertable('readings', 'ts',"
                            " if_not_exists => TRUE)")
            else:
                log.info("no TimescaleDB; using plain rollup tables")
                for stmt in ROLLUP_TABLES:
                    cur.execute(stmt)

    def insert(self, ts, leg1_a, leg2_a, range_setting, host):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO readings (ts, leg1_a, leg2_a, range_setting, host)"
                " VALUES (%s, %s, %s, %s, %s)",
                (datetime.fromtimestamp(ts, timezone.utc),
                 leg1_a, leg2_a, range_setting, host))

    def latest(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT ts, leg1_a, leg2_a, range_setting, host FROM readings"
                        " ORDER BY ts DESC LIMIT 1")
            return cur.fetchone()

    def history(self, start, end, table, step_s=1):
        """Return rows (bucket_ts, leg1_a, leg2_a), one per step_s seconds.

        Bucketing is plain epoch arithmetic (no Timescale-only functions) so
        it works on Pi-hosted plain PostgreSQL too. Rollup sources are
        re-averaged weighted by their sample counts. Falls back to raw
        readings if the aggregate view is missing.
        """
        step = max(1, int(step_s))
        try:
            return self._history_from(start, end, table, step)
        except Exception:  # noqa: BLE001
            if table == "readings":
                raise
            log.warning("aggregate %s unavailable, falling back to raw", table)
            return self._history_from(start, end, "readings", step)

    def _history_from(self, start, end, table, step):
        col = "ts" if table == "readings" else "bucket"
        bucket = (f"to_timestamp(floor(extract(epoch from {col})/%s)*%s)")
        if table == "readings":
            select = f"{bucket} AS b, AVG(leg1_a), AVG(leg2_a)"
        else:
            select = (f"{bucket} AS b,"
                      " SUM(leg1_a_avg*samples)/NULLIF(SUM(samples),0),"
                      " SUM(leg2_a_avg*samples)/NULLIF(SUM(samples),0)")
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT {select} FROM {table}"
                f" WHERE {col} >= %s AND {col} <= %s"
                " GROUP BY b ORDER BY b ASC LIMIT 20000",
                (step, step, start, end))
            return cur.fetchall()
