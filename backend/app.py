"""Backend: ingest sampler readings, serve history + live SSE + dashboard.

Survives DB or MQTT outages independently: the live hub and /current keep
working from the in-memory latest reading while the DB is down, and the
sampler loop is never blocked by anything here.
"""

import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import ingest

log = logging.getLogger("app")
VOLTS = float(os.environ.get("ASSUMED_VOLTS", "120"))
ENABLE_NILM = os.environ.get("ENABLE_NILM", "false").lower() == "true"


def parse_ts(value):
    """Accept unix float or ISO-8601."""
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.timestamp()


def create_app(store=None):
    @asynccontextmanager
    async def lifespan(app):
        try:
            from db import Store

            app.state.store = Store.connect()
            log.info("DB connected")
        except Exception:  # noqa: BLE001 - live mode survives without the DB
            log.exception("DB unavailable; running live-only (history disabled)")
        if os.environ.get("MQTT_DISABLED") != "1":
            try:
                from mqtt_ingest import start_mqtt

                start_mqtt(app.state.handle_reading)
            except Exception:  # noqa: BLE001
                log.exception("MQTT thread failed to start; HTTP ingest still works")
        else:
            log.info("MQTT disabled")
        yield

    app = FastAPI(title="house_power_dashboard", lifespan=lifespan)
    app.state.store = store
    app.state.latest = None
    app.state.lock = threading.Lock()
    app.state.subscribers = set()
    app.state.events = []  # NILM events (memory only, exploratory)

    nilm = None
    if ENABLE_NILM:
        try:
            from nilm import events as nilm  # noqa: F811
            log.info("NILM module enabled")
        except Exception:  # noqa: BLE001 - NILM must never block the backend
            log.exception("NILM failed to load; continuing without it")
            nilm = None
    app.state.nilm = nilm

    def handle_reading(payload):
        """Shared by HTTP ingest and the MQTT thread."""
        reading = ingest.validate(payload)
        watts = ingest.to_watts(reading, VOLTS)
        with app.state.lock:
            app.state.latest = watts
            subs = list(app.state.subscribers)
        for q in subs:
            try:
                q.put_nowait(watts)
            except asyncio.QueueFull:
                pass
        if nilm is not None:
            try:
                ev = nilm.process(watts)
                if ev:
                    with app.state.lock:
                        app.state.events.append(ev)
                        app.state.events[:] = app.state.events[-500:]
            except Exception:  # noqa: BLE001
                log.exception("NILM process failed")
        if app.state.store is not None:
            try:
                app.state.store.insert(reading["ts"], reading["leg1_a"],
                                       reading["leg2_a"], reading["range_setting"],
                                       reading["host"])
            except Exception:  # noqa: BLE001
                log.exception("DB insert failed")
                raise
        return watts

    app.state.handle_reading = handle_reading

    @app.post("/api/v1/readings")
    async def post_reading(request: Request):
        try:
            payload = await request.json()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"invalid JSON: {e}") from e
        try:
            watts = handle_reading(payload)
        except ingest.BadReading as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"storage unavailable: {e}") from e
        return {"ok": True, **watts}

    @app.get("/api/v1/current")
    async def get_current():
        with app.state.lock:
            latest = app.state.latest
        if latest is None and app.state.store is not None:
            try:
                row = app.state.store.latest()
                if row:
                    ts, l1, l2, rng, host = row
                    latest = ingest.to_watts(
                        {"ts": ts.timestamp(), "leg1_a": l1, "leg2_a": l2,
                         "range_setting": rng, "host": host}, VOLTS)
            except Exception:  # noqa: BLE001
                log.exception("DB latest failed")
        if latest is None:
            raise HTTPException(404, "no readings yet")
        return latest

    @app.get("/api/v1/history")
    async def get_history(
            start: str = Query(..., description="unix ts or ISO-8601"),
            end: str = Query(..., description="unix ts or ISO-8601"),
            resolution: str = Query("auto",
                                    pattern="^(auto|raw|1min|1hour|day)$")):
        try:
            t0, t1 = parse_ts(start), parse_ts(end)
        except ValueError as e:
            raise HTTPException(400, f"bad time: {e}") from e
        if t1 <= t0 or t1 - t0 > 5 * 366 * 86400:
            raise HTTPException(400, "bad range (max 5 years)")
        table = {"raw": "readings", "1min": "power_1min",
                 "1hour": "power_1hour", "day": "power_day"}.get(resolution)
        if table is None:
            table = ingest.pick_table(t1 - t0)
        if app.state.store is None:
            raise HTTPException(503, "storage unavailable")
        s = datetime.fromtimestamp(t0, timezone.utc)
        e = datetime.fromtimestamp(t1, timezone.utc)
        try:
            rows = app.state.store.history(s, e, table)
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(503, f"storage unavailable: {ex}") from ex
        return {"resolution": table, "volts": VOLTS, "points": [
            {"t": (r[0].timestamp() if hasattr(r[0], "timestamp") else float(r[0])),
             "leg1_w": round(VOLTS * r[1], 2), "leg2_w": round(VOLTS * r[2], 2),
             "total_w": round(VOLTS * (r[1] + r[2]), 2)}
            for r in rows]}

    @app.get("/api/v1/stream")
    async def stream(request: Request):
        q = asyncio.Queue(maxsize=30)
        with app.state.lock:
            app.state.subscribers.add(q)
            latest = app.state.latest

        async def gen():
            if latest is not None:
                yield f"data: {json.dumps(latest)}\n\n"
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        watts = await asyncio.wait_for(q.get(), timeout=20)
                        yield f"data: {json.dumps(watts)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                with app.state.lock:
                    app.state.subscribers.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/api/v1/events")
    async def get_events():
        if app.state.nilm is None:
            raise HTTPException(404, "NILM disabled (set ENABLE_NILM=true)")
        with app.state.lock:
            return {"events": list(app.state.events)}

    @app.get("/api/v1/health")
    async def health():
        ok = True
        if app.state.store is not None:
            try:
                app.state.store.latest()
            except Exception:  # noqa: BLE001
                ok = False
        return {"ok": ok, "db": app.state.store is not None and ok,
                "nilm": app.state.nilm is not None}

    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.environ.get("DASH_DIR", ""),
                  os.path.normpath(os.path.join(here, "..", "dashboard")),  # repo checkout
                  os.path.join(here, "dashboard")]                          # container mount
    dash_dir = next((d for d in candidates if d and os.path.isdir(d)), None)
    if dash_dir is not None:
        app.mount("/", StaticFiles(directory=dash_dir, html=True), name="dashboard")
    else:
        @app.get("/", response_class=HTMLResponse)
        async def index():
            return "<h1>house_power_dashboard</h1><p>dashboard/ not mounted</p>"

    return app


app = create_app()
