"""NILM exploratory module — device discovery by power signature.

STATUS: experimental, disabled by default (ENABLE_NILM=false). This module is
isolated: `process()` is a pure function over the live watt stream, it keeps
no I/O, and any exception is swallowed by the caller. It must never affect
the sampler -> ingest -> dashboard path.

Current approach (placeholder for real research): sustained step detection.
When total power jumps by more than STEP_W and stays there for SETTLE_S,
emit an event {ts, delta_w, direction}. Later work can cluster these steps
into device signatures (on/off pairs, multi-state appliances).
"""

import os

STEP_W = float(os.environ.get("NILM_STEP_W", "100"))
SETTLE_S = float(os.environ.get("NILM_SETTLE_S", "3"))

_state = {"pending": None}


def reset():
    _state["pending"] = None


def process(watts):
    """Feed one watts dict (as produced by ingest.to_watts). Returns an event or None."""
    total = watts["total_w"]
    ts = watts["ts"]
    pending = _state["pending"]
    if pending is None:
        _state["pending"] = {"base": total, "ts": ts}
        return None
    delta = total - pending["base"]
    if abs(delta) < STEP_W:
        # No significant change; refresh the baseline slowly.
        _state["pending"] = {"base": 0.9 * pending["base"] + 0.1 * total, "ts": ts}
        return None
    if ts - pending["ts"] >= SETTLE_S:
        _state["pending"] = {"base": total, "ts": ts}
        return {"ts": ts, "delta_w": round(delta, 1),
                "direction": "on" if delta > 0 else "off"}
    return None
