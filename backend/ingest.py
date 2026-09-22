"""Validate + enrich v1 sampler payloads. Pure functions (unit-tested)."""

SCHEMA_VERSION = 1


class BadReading(ValueError):
    pass


def validate(payload):
    """Accept schema v1, ignore unknown extra fields, reject anything else."""
    if not isinstance(payload, dict):
        raise BadReading("payload must be an object")
    if payload.get("v") != SCHEMA_VERSION:
        raise BadReading(f"unsupported schema v{payload.get('v')!r}")
    try:
        leg1 = float(payload["leg1_A"])
        leg2 = float(payload["leg2_A"])
        rng = int(payload["range_setting"])
    except (KeyError, TypeError, ValueError) as e:
        raise BadReading(f"missing/invalid amps fields: {e}") from e
    if rng not in (100, 150, 200):
        raise BadReading(f"bad range_setting {rng}")
    if not (0 <= leg1 <= rng and 0 <= leg2 <= rng):
        raise BadReading(f"amps out of range: {leg1}, {leg2}")
    ts = payload.get("ts")
    host = str(payload.get("host", ""))
    if not isinstance(ts, (int, float)) or ts <= 0:
        raise BadReading("bad ts")
    return {"ts": float(ts), "leg1_a": leg1, "leg2_a": leg2,
            "range_setting": rng, "host": host}


def to_watts(reading, volts=120.0):
    """Split-phase: total = V1*I1 + V2*I2 (same assumed volts per leg)."""
    leg1_w = volts * reading["leg1_a"]
    leg2_w = volts * reading["leg2_a"]
    return {"ts": reading["ts"], "leg1_w": leg1_w, "leg2_w": leg2_w,
            "total_w": leg1_w + leg2_w,
            "leg1_a": reading["leg1_a"], "leg2_a": reading["leg2_a"],
            "range_setting": reading["range_setting"], "host": reading["host"],
            "volts": volts}


def pick_table(span_seconds):
    """Route history queries: raw ~3d, then minute/hour/day aggregates."""
    if span_seconds <= 3 * 86400:
        return "readings"
    if span_seconds <= 45 * 86400:
        return "power_1min"
    if span_seconds <= 400 * 86400:
        return "power_1hour"
    return "power_day"
