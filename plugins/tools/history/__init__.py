"""history: one timeline of everything that happened in the house, kept in a local SQLite file (plugin-state/history/history.db, WAL), never sent anywhere.

What is written (core.ON_EVENT): state changes of devices (with who caused them: app, voice, a rule by name, the watchdog, a timer, or the device itself: a hand at
the wall, a sensor), commands sent, rule firings, devices going offline and back, the watchdog's fixes, issues and "solved by itself", and the WARNING and ERROR
lines of the log (redacted: no token, password or key of any shape, no line that holds a value of the environment's secrets). Noisy numbers are thinned: a
temperature when it moved 0.5, a humidity 3, ... or one sample an hour; signal strengths, uptimes, per-gate radar energies and the like are not kept.

Owner only (the house token): GET /api/history (filters: room, device, kind = states | commands | rules | errors, source, since, until, q, before, limit),
DELETE /api/history (the same filters, or all=1), GET /api/history/info. Settings, optional: plugin-data/history/settings.json {"retention_days": 30, "max_rows": 500000}."""
import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time

from fastapi import HTTPException, Request

import app as core

PLUGIN = {"name": "History", "version": "1.0.0", "icon": "clock"}

CATS = {"state": "states", "online": "states", "offline": "states", "command": "commands", "rule": "rules", "watchdog": "rules", "issue": "errors", "solved": "errors", "error": "errors"}
IGNORE = re.compile(r"^(g\d_.*|linkquality|rssi|wifi.*|uptime|energy|.*_energy|.*_distance|distance|update.*|last_.*|now|current|voltage|power_.*|ip|temperature_calibration)$")
STEP = {"temperature": 0.5, "room_temp": 0.5, "humidity": 3.0, "battery": 5.0, "power": 10.0, "illuminance": None}      # None: a relative step (30 %, at least 20)
SAMPLE_S = 3600
DEFAULTS = {"retention_days": 30, "max_rows": 500000}
SECRET_NAME = re.compile(r"(PASS|TOKEN|KEY|SECRET|PSK|AUTH|CREDENTIAL)", re.I)
_lock = threading.RLock()
_q: "queue.SimpleQueue" = queue.SimpleQueue()
_last: dict = {}                       # (device, code) -> (value logged, time)
_seen: dict = {}                       # (kind, device, code, new) -> time: the same thing twice within 2 s is one
_rooms = {"at": 0.0, "of": {}}
_conn_w = {"c": None}
_log_seen: dict = {}


def _path():
    return core.plugin_state("history") / "history.db"


def _settings() -> dict:
    try:
        raw = json.loads((core.plugin_data("history") / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for k in out:
            if isinstance(raw.get(k), (int, float)) and raw[k] > 0:
                out[k] = raw[k]
    return out


def _connect() -> sqlite3.Connection:
    c = sqlite3.connect(_path(), timeout=10, isolation_level=None, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("""CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, cat TEXT NOT NULL, kind TEXT NOT NULL, device TEXT, name TEXT,
                 room TEXT, code TEXT, old TEXT, new TEXT, source TEXT, level TEXT, extra TEXT)""")
    for col in ("ts", "device", "cat"):
        c.execute(f"CREATE INDEX IF NOT EXISTS ev_{col} ON events({col})")
    return c


def _c() -> sqlite3.Connection:
    if _conn_w["c"] is None:
        _conn_w["c"] = _connect()
    return _conn_w["c"]


def _room_of(dev: str) -> str:
    now = time.time()
    if now - _rooms["at"] > 30:
        _rooms["of"] = {d: r.get("name") or "" for r in core.get_rooms() for d in r.get("devices") or []}
        _rooms["at"] = now
    return _rooms["of"].get(dev, "")


def _redact(text: str):
    """The line without anything that looks like a secret; None when it holds a value of the environment's secrets (the line is dropped)."""
    for k, v in os.environ.items():
        if v and len(v) >= 6 and SECRET_NAME.search(k) and v in text:
            return None
    text = re.sub(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}", r"\1 ***", text)
    text = re.sub(r"(?i)\b(pass(word)?|token|secret|key|psk)\b\s*[=:]\s*\S+", r"\1=***", text)
    text = re.sub(r"\b[A-Fa-f0-9]{24,}\b", "***", text)
    text = re.sub(r"\b[A-Za-z0-9_-]{32,}\b", "***", text)
    return text


def _thin(dev: str, code: str, old, new, now: float) -> bool:
    """Is this change of a channel worth a line? Words and switches always; a number when it moved by its step, or an hourly sample of a slow drift."""
    if IGNORE.match(code or ""):
        return False
    if isinstance(new, bool) or not isinstance(new, (int, float)) or isinstance(old, bool) or not isinstance(old, (int, float)):
        return True
    prev = _last.get((dev, code))
    base = prev[0] if prev else old
    step = STEP.get(code, 1.0)
    need = step if step is not None else max(20.0, 0.3 * abs(base))
    if abs(new - base) >= need or (prev and now - prev[1] >= SAMPLE_S and new != prev[0]):
        _last[(dev, code)] = (new, now)
        return True
    return False


def add(ev: dict) -> bool:
    """Queue an event; False when it was thinned away (a duplicate, a wobble, a signal strength)."""
    kind = ev.get("kind", "")
    cat = CATS.get(kind)
    if not cat:
        return False
    now = ev.get("ts") or time.time()
    dev, code = ev.get("device") or "", ev.get("code") or ""
    if kind == "state" and not _thin(dev, code, ev.get("old"), ev.get("new"), now):
        return False
    key = (kind, dev, code, str(ev.get("new")), ev.get("source") or "", ev.get("text") or "")
    with _lock:
        if now - _seen.get(key, 0) < 2.0:
            return False
        _seen[key] = now
        if len(_seen) > 2000:
            for k in [k for k, t in _seen.items() if now - t > 10]:
                _seen.pop(k)
    extra = {k: v for k, v in ev.items() if k not in ("kind", "device", "code", "old", "new", "source", "ts", "level", "name") and v not in (None, "")}
    d = core.DEVICES.get(dev) or {}
    level = ev.get("level") or ("alert" if kind == "error" else "")
    _q.put((now, cat, kind, dev, ev.get("name") or d.get("name") or dev, _room_of(dev), code, None if ev.get("old") is None else json.dumps(ev["old"], ensure_ascii=False),
            None if ev.get("new") is None else json.dumps(ev["new"], ensure_ascii=False), ev.get("source") or "", level, json.dumps(extra, ensure_ascii=False) if extra else None))
    return True


def flush() -> int:
    """Write what is queued (the writer thread does this every second; tests call it)."""
    rows = []
    while True:
        try:
            rows.append(_q.get_nowait())
        except queue.Empty:
            break
    if not rows:
        return 0
    with _lock:
        c = _c()
        c.execute("BEGIN")
        c.executemany("INSERT INTO events (ts, cat, kind, device, name, room, code, old, new, source, level, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        c.execute("COMMIT")
    return len(rows)


def prune() -> int:
    s = _settings()
    with _lock:
        c = _c()
        n = c.execute("DELETE FROM events WHERE ts < ?", (time.time() - s["retention_days"] * 86400,)).rowcount
        over = c.execute("SELECT COUNT(*) FROM events").fetchone()[0] - int(s["max_rows"])
        if over > 0:
            n += c.execute("DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id LIMIT ?)", (over,)).rowcount
    return n


def _writer():
    last_prune = 0.0
    while True:
        time.sleep(1.0)
        try:
            flush()
            if time.time() - last_prune > 6 * 3600:
                last_prune = time.time()
                prune()
        except Exception as e:                                                     # noqa: BLE001  the house must not stop for its diary
            core.log.warning(f"[history] write failed: {type(e).__name__}: {e}")


def _on_event(ev: dict) -> None:
    add(ev)


class _LogToHistory(logging.Handler):
    """WARNING and ERROR lines of the core and the plug-ins, redacted, one line a minute per text."""

    def emit(self, record):
        try:
            if record.name == "home" and record.getMessage().startswith(("[history]", "[event]")):
                return
            text = _redact(record.getMessage())
            if text is None or text.startswith("[state] mirror publish"):
                return
            text = text[:300]
            now = time.time()
            if now - _log_seen.get(text, 0) < 60:
                return
            _log_seen[text] = now
            if len(_log_seen) > 500:
                for k in [k for k, t in _log_seen.items() if now - t > 60]:
                    _log_seen.pop(k)
            add({"kind": "error", "level": "alert" if record.levelno >= logging.ERROR else "warn", "source": "log", "text": text})
        except Exception:                                                          # noqa: BLE001
            pass


# ---- read, erase ----
def _when(v, default=None):
    if v in (None, ""):
        return default
    try:
        return float(v)
    except ValueError:
        pass
    import datetime
    try:
        return datetime.datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        raise HTTPException(400, "since and until are seconds or an ISO date")


def _where(room, device, kind, source, since, until, q, before=None):
    w, a = [], []
    if room:
        w.append("room = ?"); a.append(room)
    if device:                                   # a merged device: its former ids are its history too
        ids = [device, *core.aliases(device)]
        w.append("device IN (" + ",".join("?" * len(ids)) + ")"); a.extend(ids)
    if kind:
        if kind not in ("states", "commands", "rules", "errors"):
            raise HTTPException(400, "kind: states, commands, rules or errors")
        w.append("cat = ?"); a.append(kind)
    if source:
        w.append("source LIKE ?"); a.append(source + "%")
    s, u = _when(since), _when(until)
    if s is not None:
        w.append("ts >= ?"); a.append(s)
    if u is not None:
        w.append("ts <= ?"); a.append(u)
    if q:
        like = "%" + q.replace("%", "").replace("_", "") + "%"
        w.append("(name LIKE ? OR device LIKE ? OR code LIKE ? OR new LIKE ? OR old LIKE ? OR source LIKE ? OR extra LIKE ? OR room LIKE ?)")
        a += [like] * 8
    if before:
        w.append("id < ?"); a.append(int(before))
    return (" WHERE " + " AND ".join(w)) if w else "", a


def _rowdict(r) -> dict:
    ld = lambda s: None if s is None else json.loads(s)                           # noqa: E731
    return {"id": r[0], "ts": r[1], "cat": r[2], "kind": r[3], "device": r[4], "name": r[5], "room": r[6], "code": r[7], "old": ld(r[8]), "new": ld(r[9]),
            "source": r[10], "level": r[11], **(json.loads(r[12]) if r[12] else {})}


@core.app.get("/api/history")
def api_history(request: Request, room: str = "", device: str = "", kind: str = "", source: str = "", since: str = "", until: str = "", q: str = "",
                before: int = 0, limit: int = 50):
    core.house_token_only(request)
    flush()
    where, args = _where(room, device, kind, source, since, until, q, before)
    limit = max(1, min(200, limit))
    with _lock:
        c = _c()
        rows = c.execute("SELECT id, ts, cat, kind, device, name, room, code, old, new, source, level, extra FROM events" + where + " ORDER BY id DESC LIMIT ?", args + [limit + 1]).fetchall()
    return {"events": [_rowdict(r) for r in rows[:limit]], "more": len(rows) > limit}


@core.app.delete("/api/history")
def api_history_erase(request: Request, room: str = "", device: str = "", kind: str = "", source: str = "", since: str = "", until: str = "", q: str = "", all: int = 0):
    """Erase what matches the filters; with no filter at all it needs all=1 (the whole history)."""
    core.house_token_only(request)
    flush()
    where, args = _where(room, device, kind, source, since, until, q)
    if not where and not all:
        raise HTTPException(400, "give a filter, or all=1 to erase the whole history")
    with _lock:
        c = _c()
        n = c.execute("DELETE FROM events" + where, args).rowcount
    core.log.info(f"[history] erased {n} entries ({'all' if not where else 'a filter'})")
    return {"deleted": n}


@core.app.get("/api/history/info")
def api_history_info(request: Request):
    core.house_token_only(request)
    flush()
    with _lock:
        c = _c()
        n, first = c.execute("SELECT COUNT(*), MIN(ts) FROM events").fetchone()
    return {"count": n, "oldest": first, "retention_days": _settings()["retention_days"], "bytes": _path().stat().st_size if _path().exists() else 0}


core.ON_EVENT.append(_on_event)
core.log.addHandler(_LogToHistory(level=logging.WARNING))
core.STARTUP_TASKS.append(_writer)                                                 # a plain callable: a thread of its own
