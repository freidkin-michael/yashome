# The core: wiring: which switch feeds what; the start of the plug-ins.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# Wiring: which switch channel feeds what (an IR fixture's power, a lamp on the map) - one fact, one file
# ---------------------------------------------------------------------------
# HOUSE/wiring.json: [{id, switch: {dev, code}, target: {dev} | {load, seg?}, note}], taken over once from the plug-ins'
# old copies (WIRING_MIGRATORS; a marker: a lost file never brings them back), then read only (wiring_owns), old fields left.
# Lock order: a plug-in's own lock, then _wiring_lock; WIRING_CHANGED runs after a change with _wiring_lock released.
WIRING_PATH = HOUSE / "wiring.json"
WIRING_MARK = HOUSE / ".wiring-migrated"
_wiring_lock = threading.RLock()
_wiring: list = []
_wiring_state: dict = {"state": "warn", "detail": "not read yet", "args": []}
_wiring_postponed: list = []    # migrators whose files could not be backed up: no wiring write until a start takes them over
_wiring_taken: set = set()      # migrators the file speaks for (the marker), from the start that read it
_WIRING_ID = re.compile(r"[A-Za-z0-9_-]{1,24}")


class WiringBroken(Exception):
    """wiring.json was lost or broken after the migration: nothing is written until the owner fixes it."""


def wiring_postponed() -> bool:
    """A plug-in's migration waits for a backup: its old copy stays in charge (it writes there, the wiring takes no change)."""
    return bool(_wiring_postponed)


def wiring() -> list:
    with _wiring_lock:
        return json.loads(json.dumps(_wiring))


def wiring_owns(name: str) -> bool:
    """The wiring speaks for this plug-in: taken over and readable. Otherwise (not read yet, postponed, broken) its old copy does."""
    return name in _wiring_taken and _wiring_state["state"] != "bad"


def _wiring_norm(e: dict) -> dict:
    """One entry in its stored shape, or ValueError; the devices are not looked up (a migration keeps unknown ones)."""
    sw, tg = e.get("switch"), e.get("target")
    if not isinstance(sw, dict) or not isinstance(sw.get("dev"), str) or not sw["dev"] or not isinstance(sw.get("code", ""), str):
        raise ValueError("an entry needs switch {dev, code}")
    if not isinstance(tg, dict) or bool(tg.get("dev")) == bool(tg.get("load")):
        raise ValueError("an entry needs target {dev} or {load}")
    out_t = {"dev": str(tg["dev"])} if tg.get("dev") else {"load": str(tg["load"])}
    seg = tg.get("seg") or []
    if seg:
        if "load" not in out_t or not (isinstance(seg, list) and len(seg) == 2 and all(isinstance(x, int) for x in seg)):
            raise ValueError("seg is two point numbers of a strip")
        out_t["seg"] = seg
    eid = str(e.get("id") or secrets.token_hex(4))
    if not _WIRING_ID.fullmatch(eid):
        raise ValueError("an entry id is up to 24 letters, digits, _ or -")
    return {"id": eid, "switch": {"dev": sw["dev"][:64], "code": str(sw.get("code") or "")[:64]}, "target": out_t,
            "note": str(e.get("note") or "")[:60]}


def _wiring_key(e: dict) -> tuple:
    t = e["target"]
    return e["switch"]["dev"], e["switch"]["code"], t.get("dev") or "", t.get("load") or "", tuple(t.get("seg") or ())


def _wiring_store(entries: list, check: bool) -> None:
    """Under _wiring_lock: check and write the whole list. check: a new or changed entry's switch must be a device of the house
    with that channel and a dev target must exist (the dashboard's edits; an entry kept as it was is not looked up again)."""
    if _wiring_state["state"] == "bad":
        raise WiringBroken(_wiring_state["detail"].format(*_wiring_state["args"]))
    if _wiring_postponed:
        raise WiringBroken("migration postponed: " + ", ".join(_wiring_postponed))
    new = [_wiring_norm(e) for e in entries]
    if len({e["id"] for e in new}) != len(new) or len({_wiring_key(e) for e in new}) != len(new):
        raise ValueError("an entry is there twice")
    if check:
        old = {_wiring_key(e) for e in _wiring}
        for e in new:
            if _wiring_key(e) in old:
                continue
            d = DEVICES.get(e["switch"]["dev"])
            if d is None or (e["switch"]["code"] and e["switch"]["code"] not in (d.get("_ctl") or {})):
                raise ValueError(f"no such switch channel: {e['switch']['dev']} {e['switch']['code']}".strip())
            if e["target"].get("dev") and e["target"]["dev"] not in DEVICES:
                raise ValueError(f"no such device: {e['target']['dev']}")
    _atomic_json_dump(WIRING_PATH, new, indent=1, ensure_ascii=False)
    _wiring[:] = new
    if _wiring_mark() is None:                      # nothing was migrated before: the plug-ins here write through it from now
        _atomic_json_dump(WIRING_MARK, {"migrators": {m["name"]: {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "count": 0, "backup": ""}
                                                      for m in WIRING_MIGRATORS}})
        _wiring_taken.update(m["name"] for m in WIRING_MIGRATORS)


def _wiring_notify() -> None:
    """After a change (or the start's read), outside _wiring_lock (a plug-in takes its own lock): WIRING_CHANGED."""
    for fn in list(WIRING_CHANGED):
        try:
            fn(wiring())
        except Exception as e:                      # noqa: BLE001  one plug-in's copy must not stop the others'
            log.error(f"[wiring] {getattr(fn, '__name__', fn)}: {e}")


def wiring_put(entries: list, check: bool = True) -> list:
    """The whole list, stored, then WIRING_CHANGED."""
    with _wiring_lock:
        _wiring_store(entries, check)
        snap = wiring()
    _wiring_notify()
    return snap


def wiring_edit(change, check: bool = True) -> list:
    """change(list) -> list on a copy of the current entries, stored under one lock (no lost update); WIRING_CHANGED after it."""
    with _wiring_lock:
        _wiring_store(change(wiring()), check)
        snap = wiring()
    _wiring_notify()
    return snap


def _wiring_backup(files: list, name: str):
    """The files a migration reads, copied with their sha256 in manifest.txt, before anything is written: in backups/ of the
    checkout, else (a read-only one, the demo) in the house's plugin-state/; None when neither takes it."""
    for base in (BACKUP_DIR, PLUGIN_STATE):
        out = base / f"wiring-migration-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{name}"
        try:
            out.mkdir(parents=True, exist_ok=True)
            lines = []
            for f in map(Path, files):
                if f.is_file():
                    data = f.read_bytes()
                    (out / f.name).write_bytes(data)
                    lines.append(f"{hashlib.sha256(data).hexdigest()}  {f.name}  {f}")
            (out / "manifest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            return out
        except OSError as e:
            log.warning(f"[wiring] {name}: no backup in {base} ({e})")
    return None


def _wiring_mark():
    """The marker: {"migrators": {name: {ts, count, backup}}}; None when there is none; an unreadable one counts as empty."""
    try:
        m = json.loads(WIRING_MARK.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("[wiring] .wiring-migrated is unreadable: the plug-ins' copies are merged in again by key")
        return {"migrators": {}}
    return m if isinstance(m, dict) and isinstance(m.get("migrators"), dict) else {"migrators": {}}


def _wiring_boot():
    """Once at start, after the plug-ins: read wiring.json, then take over each plug-in's old copy not taken over yet (its files
    backed up first, merged by key). A plug-in off at one start is taken over at the first start it is on."""
    with _wiring_lock:
        mark = _wiring_mark()
        if mark is not None or WIRING_PATH.exists():
            try:
                data = json.loads(WIRING_PATH.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    raise ValueError("not a list")
                _wiring[:] = [_wiring_norm(e) for e in data]
            except FileNotFoundError:
                _wiring[:] = []
                _wiring_state.update(state="bad", detail="wiring.json is missing; it was migrated at {0}", args=[_wiring_mark_ts()])
                log.error("[wiring] wiring.json is MISSING after the migration: no wiring until it is restored from a backup")
                return
            except (OSError, ValueError, TypeError) as e:
                bad = WIRING_PATH.with_name(f"wiring.json.bad-{time.strftime('%Y%m%d-%H%M%S')}")
                try:
                    os.replace(WIRING_PATH, bad)
                except OSError:
                    bad = WIRING_PATH
                _wiring[:] = []
                _wiring_state.update(state="bad", detail="wiring.json was broken ({0}); kept as {1}", args=[str(e)[:80], bad.name])
                log.error(f"[wiring] wiring.json is BROKEN ({e}): kept as {bad.name}, no wiring until it is restored")
                return
        done = dict((mark or {}).get("migrators") or {})
        now, _wiring_postponed[:] = [], []
        _wiring_taken.clear()
        _wiring_taken.update(done)
        for m in [m for m in WIRING_MIGRATORS if m["name"] not in done]:
            got = []
            for e in m["entries"]() or []:
                try:
                    got.append(_wiring_norm(e))
                except ValueError as err:
                    log.warning(f"[wiring] {m['name']}: an entry not taken over ({err}): {e}")
            if not got and not WIRING_PATH.exists():
                continue                            # nothing kept and no file yet: the first change writes both
            where = _wiring_backup(m.get("files") or [], m["name"]) if got else None
            if got and where is None:                # no backup, no write: the plug-in's old copy stays in charge
                _wiring_postponed.append(m["name"])
                log.error(f"[wiring] {m['name']}: cannot back up its files, its migration is postponed to the next start")
                continue
            have = {_wiring_key(e) for e in _wiring}
            ids = {e["id"] for e in _wiring}
            for e in got:
                if _wiring_key(e) not in have:
                    if e["id"] in ids:
                        e["id"] = secrets.token_hex(4)
                    have.add(_wiring_key(e))
                    ids.add(e["id"])
                    _wiring.append(e)
            _atomic_json_dump(WIRING_PATH, _wiring, indent=1, ensure_ascii=False)
            done[m["name"]] = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(got), "backup": where.name if where else ""}
            _atomic_json_dump(WIRING_MARK, {"migrators": done})
            _wiring_taken.add(m["name"])
            now.append(f"{m['name']} {len(got)}")
            log.info(f"[wiring] {m['name']} taken over: {len(got)} entries; backup in {where}")
        if _wiring_postponed:
            _wiring_state.update(state="warn", detail="cannot back up, migration postponed: {0}", args=[", ".join(_wiring_postponed)])
        elif now:
            _wiring_state.update(state="ok", detail="migrated now: {0}; {1} entries", args=[", ".join(now), len(_wiring)])
        elif WIRING_PATH.exists():
            _wiring_state.update(state="ok", detail="{0} entries", args=[len(_wiring)])
        else:
            _wiring_state.update(state="ok", detail="nothing to take over", args=[])


def _wiring_mark_ts() -> str:
    ts = [v.get("ts") for v in ((_wiring_mark() or {}).get("migrators") or {}).values() if isinstance(v, dict)]
    return min(ts) if ts else "?"


def _wiring_health() -> list:
    s = _wiring_state
    fix = "" if s["state"] != "bad" else "Put wiring.json back from a backup (or the .bad- copy, fixed), then restart"
    return [health_row("wiring", s["state"], "Wiring", s["detail"], fix, s["args"])]


HEALTH_CHECKS.append(_wiring_health)
HEALTH_CHECKS.append(_room_sensor_health)


@app.get("/api/wiring")
def wiring_get(request: Request):
    _house_token_only(request)
    return {"entries": wiring(), "state": _wiring_state["state"]}


class WiringBody(Body):
    entries: list = Field(max_length=2000)


@app.put("/api/wiring")
def wiring_set(body: WiringBody, request: Request):
    _house_token_only(request)
    try:
        return {"entries": wiring_put(body.entries)}
    except WiringBroken as e:
        raise HTTPException(409, f"the wiring cannot be changed: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))


def __getattr__(name):
    target = PLUGIN_API.get(name)
    if target is None:
        raise AttributeError(f"module 'app' has no attribute {name!r}")
    return globals()[target]


_load_plugins()
try:
    _wiring_boot()                                   # after the plug-ins: their migrators are known
    if _wiring_state["state"] == "ok":
        _wiring_notify()                             # the plug-ins it speaks for re-read it (they started on their old copy)
except Exception as _e:                              # noqa: BLE001  the house starts without wiring rather than not at all
    _wiring_state.update(state="bad", detail="the start failed: {0}", args=[str(_e)[:120]])
    log.exception("[wiring] start failed")
# registered after the plug-ins: a route a plug-in adds under /plugins/<name>/ is matched first
app.add_api_route("/plugins/{name}/{path:path}", plugin_asset, methods=["GET", "HEAD"])
