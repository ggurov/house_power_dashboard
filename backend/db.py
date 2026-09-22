"""Persistence: TimescaleDB readings + aggregates.

Watts are NEVER stored — only amps (the measured truth). Voltage is an
assumption applied at query time so it stays adjustable.
"""

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("db")

SCHEMA_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS timescaledb",
    """CREATE TABLE IF NOT EXISTS readings (
        ts TIMESTAMPTZ NOT NULL,
        leg1_a DOUBLE PRECISION NOT NULL,
        leg2_a DOUBLE PRECISION NOT NULL,
        range_setting SMALLINT NOT NULL,
        host TEXT NOT NULL DEFAULT '')""",
    "SELECT create_hypertable('readings', 'ts', if_not_exists => TRUE)",
    "CREATE INDEX IF NOT EXISTS readings_ts_idx ON readings (ts DESC)",
]

# (view, bucket-size label, source) for history routing.
AGG_VIEWS = ("power_1min", "power_1hour", "power_day")


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
            for stmt in SCHEMA_STATEMENTS:
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

    def history(self, start, end, table):
        """Return rows (ts, leg1_a, leg2_a) from raw readings or an aggregate view."""
        with self.conn.cursor() as cur:
            if table == "readings":
                cur.execute(
                    "SELECT ts, leg1_a, leg2_a FROM readings"
                    " WHERE ts >= %s AND ts <= %s ORDER BY ts ASC LIMIT 20000",
                    (start, end))
            else:
                cur.execute(
                    f"SELECT bucket, leg1_a_avg, leg2_a_avg FROM {table}"
                    " WHERE bucket >= %s AND bucket <= %s ORDER BY bucket ASC LIMIT 20000",
                    (start, end))
            return cur.fetchall()
