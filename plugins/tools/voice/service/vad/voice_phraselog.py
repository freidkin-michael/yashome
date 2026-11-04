"""The phrase log of the voice path: every phrase the house heard and what it decided, one JSON line each, kept for months.
It is the material for checking and later training the house model on real phrases. It never acts and never raises: a full disk
or a bad value loses one line, never a command. Files: <VA_PHRASELOG_DIR>/phrases-YYYY-MM.jsonl, the newest KEEP months stay."""
import datetime
import glob
import json
import os
import threading

DIR = os.environ.get("VA_PHRASELOG_DIR", "/house/phraselog")      # empty = no log
KEEP = int(os.environ.get("VA_PHRASELOG_KEEP", "24"))            # monthly files kept
MAX_FIELD = 2000                                                 # a longer value is cut: one runaway reply must not bloat the log

_lock = threading.Lock()
_month = {"name": None}


def _clean(v):
    if isinstance(v, str) and len(v) > MAX_FIELD:
        return v[:MAX_FIELD] + "..."
    if isinstance(v, float):
        return round(v, 3)
    return v


def _prune(d):
    for old in sorted(glob.glob(os.path.join(d, "phrases-*.jsonl")))[:-KEEP]:
        os.remove(old)


def record(**fields):
    """One phrase and its outcome -> one line; None values are left out. Returns True when the line was written."""
    if not DIR:
        return False
    try:
        now = datetime.datetime.now().astimezone()
        line = {"ts": now.isoformat(timespec="seconds")}
        line.update((k, _clean(v)) for k, v in fields.items() if v is not None)
        text = json.dumps(line, ensure_ascii=False, default=str) + "\n"
        name = "phrases-%s.jsonl" % now.strftime("%Y-%m")
        with _lock:
            os.makedirs(DIR, mode=0o755, exist_ok=True)
            path = os.path.join(DIR, name)
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
            os.chmod(path, 0o644)                                # the service runs as root; the host user must read it
            if _month["name"] != name:                           # a new month (or the first line since start): drop the oldest
                _month["name"] = name
                _prune(DIR)
        return True
    except Exception as e:
        print("[phraselog] line lost: %s" % e, flush=True)
        return False
