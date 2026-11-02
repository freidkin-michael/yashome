"""GET /api/stats: compact counts for a status page widget (Homepage and the like)."""
import time

import app as core

PLUGIN = {"name": "Stats", "version": "1.0.0", "icon": "chart"}


@core.app.get("/api/stats")
def api_stats():
    online = sum(1 for did in list(core.DEVICES) if core.status_copy(did, {}).get("online"))
    total = len(core.DEVICES)
    with core.bindings_lock:
        nbind = sum(len(g) for codes in core.bindings.values() for g in codes.values())
    cutoff = time.time() - 60
    with core.events_lock:
        ev1m = sum(1 for dq in core.events.values() for e in dq if e.get("ts", 0) >= cutoff)
    return {"online": online, "offline": total - online, "total": total,
            "bindings": nbind, "events_last_minute": ev1m}
