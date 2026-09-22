"""Backend tests — fake store, no DB/MQTT needed. Run: pytest backend/tests."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("MQTT_DISABLED", "1")

import ingest  # noqa: E402
from app import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class FakeStore:
    def __init__(self):
        self.rows = []

    def insert(self, ts, l1, l2, rng, host):
        self.rows.append((ts, l1, l2, rng, host))

    def latest(self):
        if not self.rows:
            return None
        from datetime import datetime, timezone

        ts, l1, l2, rng, host = max(self.rows, key=lambda r: r[0])
        return (datetime.fromtimestamp(ts, timezone.utc), l1, l2, rng, host)

    def history(self, start, end, table):
        from datetime import datetime, timezone

        out = []
        for ts, l1, l2, _rng, _h in sorted(self.rows):
            dt = datetime.fromtimestamp(ts, timezone.utc)
            if start <= dt <= end:
                out.append((dt, l1, l2))
        return out


@pytest.fixture()
def client():
    app = create_app(store=FakeStore())
    with TestClient(app) as c:
        yield c


def reading(**kw):
    base = {"v": 1, "ts": 1727123456.0, "leg1_A": 10.0,
            "leg2_A": 5.0, "range_setting": 100, "host": "adcpi1"}
    base.update(kw)
    return base


def test_validate_ok_and_watts():
    r = ingest.validate(reading())
    w = ingest.to_watts(r, 120.0)
    assert (w["leg1_w"], w["leg2_w"], w["total_w"]) == (1200.0, 600.0, 1800.0)


def test_validate_rejects():
    for bad in ({}, {**reading(), "v": 2}, {"v": 1},
                reading(range_setting=300), reading(leg1_A=-1),
                reading(leg1_A=101), reading(ts="yesterday")):
        with pytest.raises(ingest.BadReading):
            ingest.validate(bad)


def test_validate_ignores_extra_fields():
    r = ingest.validate(reading(future_field="ok", v1_extra=1))
    assert r["leg1_a"] == 10.0


def test_pick_table_routing():
    assert ingest.pick_table(3600) == "readings"
    assert ingest.pick_table(10 * 86400) == "power_1min"
    assert ingest.pick_table(100 * 86400) == "power_1hour"
    assert ingest.pick_table(500 * 86400) == "power_day"


def test_ingest_current_roundtrip(client):
    r = client.post("/api/v1/readings", json=reading())
    assert r.status_code == 200, r.text
    cur = client.get("/api/v1/current").json()
    assert cur["total_w"] == 1800.0
    assert cur["range_setting"] == 100


def test_ingest_bad_schema(client):
    assert client.post("/api/v1/readings", json={"v": 99}).status_code == 422
    assert client.post("/api/v1/readings", json="nope").status_code in (400, 422)


def test_history(client):
    for i in range(5):
        client.post("/api/v1/readings", json=reading(ts=1727123456.0 + i))
    h = client.get("/api/v1/history",
                   params={"start": 1727123450, "end": 1727123465}).json()
    assert h["resolution"] == "readings"
    assert len(h["points"]) == 5
    assert h["points"][0]["total_w"] == 1800.0


def test_history_explicit_resolution_passes_table_name(client):
    seen = {}

    class Spy(FakeStore):
        def history(self, s, e, t):
            seen["table"] = t
            return []

    app = create_app(store=Spy())
    with TestClient(app) as c2:
        r = c2.get("/api/v1/history",
                   params={"start": 1727123450, "end": 1727123460,
                           "resolution": "1hour"})
        assert r.status_code == 200
    assert seen["table"] == "power_1hour"


def test_events_disabled_by_default(client):
    assert client.get("/api/v1/events").status_code == 404


def test_health(client):
    assert client.get("/api/v1/health").json()["ok"] is True


def test_nilm_step_detector():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "nilm"))
    from nilm import events
    events.reset()
    base = {"total_w": 500.0, "ts": 1000.0}
    assert events.process(dict(base)) is None
    assert events.process({"total_w": 505.0, "ts": 1001.0}) is None  # noise
    # Big step, not yet settled.
    assert events.process({"total_w": 1800.0, "ts": 1002.0}) is None
    ev = events.process({"total_w": 1805.0, "ts": 1006.0})  # settled
    assert ev and ev["direction"] == "on" and ev["delta_w"] > 1000
