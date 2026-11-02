"""stats: the counts Homepage reads, same keys as before the move."""
import sys
import time

import app as core

me = sys.modules["yashome_plugins.stats"]


def test_counts():
    core._store_status("tv_test", {"online": True, "values": {}})
    with core._events_lock:
        core._events["tv_test"].append({"ts": time.time()})
    s = me.api_stats()
    assert set(s) == {"online", "offline", "total", "bindings", "events_last_minute"}
    assert s["total"] == len(core.DEVICES) and s["online"] >= 1 and s["events_last_minute"] >= 1
    assert s["online"] + s["offline"] == s["total"]
