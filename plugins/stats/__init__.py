"""GET /api/stats: compact counts for a status page widget (Homepage and the like)."""
import time

import app as core

PLUGIN = {"name": "Stats", "version": "1.0.0"}


@core.app.get("/api/stats")
def api_stats():
    online = sum(1 for did in list(core.DEVICES) if core._status_copy(did, {}).get("online"))
    total = len(core.DEVICES)
    with core._bindings_lock:
        nbind = sum(len(g) for codes in core._bindings.values() for g in codes.values())
    cutoff = time.time() - 60
    with core._events_lock:
        ev1m = sum(1 for dq in core._events.values() for e in dq if e.get("ts", 0) >= cutoff)
    return {"online": online, "offline": total - online, "total": total,
            "bindings": nbind, "events_last_minute": ev1m}
