# The core: events, moving a device to a new key, relink, orphaned references.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# Events log
# ---------------------------------------------------------------------------
@app.get("/api/devices/{dev_id}/events")
def get_events(dev_id: str):
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    with _events_lock:
        return {"items": list(_events.get(dev_id, []))}


# --- Device key migration: a device whose id changed (a rename in its own system) ---------


def _lost_rule(rid: str, lost: set) -> bool:
    """rid (or a timer token for|<rid> / sched|<dev/code>) belongs to a rule in lost."""
    for p in ("for|", "sched|", "inch|"):
        if rid.startswith(p):
            rid = ("a|" if p != "for|" else "") + rid[len(p):]
    return any(rid == x or rid.startswith(x + "|") for x in lost)


def migrate_device_key(old: str, new: str) -> dict:
    """Move EVERY reference from one device key to another.

    For a plug-in whose device id comes from outside (a zigbee2mqtt friendly_name), a
    rename there silently orphans every place that stored the old one -- bindings (both as a
    source and as a target), favourites, automations, disabled-rule ids, name and
    channel-name overrides, plus the in-memory caches. A remote whose binding
    target was renamed just dies unnoticed otherwise. The settings sections move by their
    _store() entries; the caches and timers are listed here."""
    touched: dict = {}
    lost = set()   # rules of the old key that lost the merge: their disabled ids and timers go
    with _settings_lock:
        for st in _STORES.values():
            if st["migrate"]:
                touched.update(st["migrate"](globals()[st["var"]], old, new, lost))
        touched["timers"] = 0
        _save_settings()

    # in-memory caches keyed by device id
    _cmd_forget(old)                                # commands in flight: the old name will not answer
    with _cache_lock:
        if old in _status_cache:
            _status_cache.setdefault(new, _status_cache.pop(old))
    for d in (_status_ts, _fail_count, _last_control_ts):
        if old in d:
            d.setdefault(new, d.pop(old))
    with _events_lock:
        if old in _events:
            both = list(_events.pop(old)) + list(_events.get(new) or [])
            _events[new] = deque(sorted(both, key=lambda e: e.get("ts", 0))[-EVENTS_MAX:], maxlen=EVENTS_MAX)
    for ak in [k for k in _sensor_arm if k.split("/", 1)[0] == old]:
        _sensor_arm.setdefault(new + ak[len(old):], _sensor_arm.pop(ak))
    # a timer's token carries the key too (inch|<dev>/<code>, for|b|<src>|...): re-arm it under
    # the new token, or an OFF of the renamed channel would not cancel it and a second one runs
    def _rekey(tok):
        parts = tok.split("|")
        out = []
        for p in parts:
            dev, sep, rest = p.partition("/")
            out.append(new + sep + rest if dev == old else p)
        return "|".join(out)
    with _pending_lock:
        moved = [(t, dict(sp)) for t, sp in _pending.items()
                 if sp.get("device") == old or _rekey(t) != t]
    for tok, sp in moved:
        _cancel_deferred(tok)
        if _lost_rule(_rekey(tok), lost):
            continue
        if sp.get("device") == old:
            sp["device"] = new
        _schedule_deferred(_rekey(tok), 0, sp["device"], sp["code"], bool(sp.get("value")),
                           sp.get("label", ""), run_at=float(sp.get("run_at") or 0), tries=int(sp.get("tries") or 0),
                           due=sp.get("due"))
        touched["timers"] += 1

    # the old retained mirror would otherwise feed its readers forever
    cli = _mqtt_client
    if cli is not None:
        for topic in [t for t in list(_mqtt_last_pub) if f"/{old}/" in t]:
            cli.publish(topic, None, retain=True)
            _mqtt_last_pub.pop(topic, None)
    touched["wiring"] = _wiring_rekey(old, new)
    for fn in list(ON_RENAME):                  # the plug-ins' own caches keyed by device id
        try:
            fn(old, new)
        except Exception as e:                  # noqa: BLE001
            log.warning(f"[plugins] on-rename {getattr(fn, '__name__', fn)}: {e}")
    return touched


_migrate_device_key = migrate_device_key       # the name the tests use


def _wiring_rekey(old: str, new: str) -> int:
    """migrate_device_key: a switch or a fed device of the old key is the new key's; an entry that is then there twice is kept once."""
    def change(ws):
        out, seen = [], set()
        for e in ws:
            for side in ("switch", "target"):
                if e[side].get("dev") == old:
                    e[side]["dev"] = new
            if _wiring_key(e) not in seen:
                seen.add(_wiring_key(e))
                out.append(e)
        return out
    with _wiring_lock:
        n = sum(e["switch"]["dev"] == old or e["target"].get("dev") == old for e in _wiring)
    if n:
        try:
            wiring_edit(change, check=False)
        except WiringBroken as e:
            log.warning(f"[wiring] {old} -> {new}: {n} entries keep the old key: {e}")
            return 0
    return n


class RelinkBody(Body):
    stale: str          # key nothing resolves to any more
    into: str           # existing device key to point everything at


@app.post("/api/devices/relink")
def device_relink(body: RelinkBody):
    """Repair path: move references from a key that no longer exists onto a live
    device, without touching the device's own system. Needed when a device id changed
    behind our back (renamed in zigbee2mqtt's own frontend, an ESP node re-flashed under
    another name): every binding, favourite and automation of the old id is orphaned."""
    if "settings" in _load_failed:
        raise HTTPException(503, f"{_settings_bad()}: fix it before relinking")
    stale, into = body.stale, body.into
    if stale in DEVICES:
        raise HTTPException(400, f"'{stale}' still exists -- nothing to repair")
    if into not in DEVICES:
        raise HTTPException(404, f"unknown target device '{into}'")
    touched = _migrate_device_key(stale, into)
    _mqtt_publish_state(into)
    log.info(f"[relink] {stale} -> {into}: {touched}")
    return {"ok": True, "id": into, "from": stale, "migrated": touched}


DATA_REF_KINDS = ("name-override", "channel-names", "channel-dimrange", "favorite", "room", "tab-order", "tile-size", "room-sensor")      # references that are data only: no rule, no firmware


def _orphan_refs() -> dict:
    """{device key: ["kind:where", ...]} of every stored reference to a device nothing resolves to (nothing is logged)."""
    orphans: dict = {}

    def note(kind, key, where):
        if key and key not in DEVICES:
            orphans.setdefault(key, []).append(f"{kind}:{where}")

    with _settings_lock:
        for st in _STORES.values():
            for kind, key, where in (st["refs"](globals()[st["var"]]) if st["refs"] else []):
                note(kind, key, where)
    return orphans


def _prune_data_refs(keys) -> list:
    """Remove the data-only references (DATA_REF_KINDS) to these device keys, whatever still stands for them in the settings: a name, a channel
    name, a favourite, a place in a room or a tab order, a tile size, a room's thermometer. A rule or a panel that names one is never touched. -> [(kind, key, where)]."""
    keys, gone = {k for k in keys if k not in DEVICES}, []
    if not keys:
        return gone
    with _settings_lock:
        for k in keys:
            for store, kind in ((_name_overrides, "name-override"), (_channel_names, "channel-names"), (_channel_dimrange, "channel-dimrange")):
                if k in store:
                    store.pop(k)
                    gone.append((kind, k, k))
        for f in [f for f in _favorites if f.get("device") in keys]:
            _favorites.remove(f)
            gone.append(("favorite", f.get("device"), f.get("code", "")))
        for tab, order in _tab_order.items():
            for k in [d for d in order if d in keys]:
                order.remove(k)
                gone.append(("tab-order", k, tab))
        for scope, sizes in _tile_sizes.items():
            for k in [d for d in list(sizes) if d in keys]:
                sizes.pop(k)
                gone.append(("tile-size", k, scope))
        for k in [d for d in _serves if d in keys]:
            gone.append(("serves", k, _serves.pop(k)))
        for rid, picks in list(_room_sensors.items()):              # a room's thermometer picked by hand: the room goes back to auto
            for kind in [k for k, src in picks.items() if src.rpartition("/")[0] in keys]:
                gone.append(("room-sensor", picks.pop(kind).rpartition("/")[0], rid))
            if not picks:
                _room_sensors.pop(rid)
        if gone:
            _save_settings()

    def drop(rooms):
        for r in rooms:
            for d in [d for d in r.get("devices") or [] if d in keys]:
                r["devices"].remove(d)
                gone.append(("room", d, r.get("name") or r.get("id", "")))
    if any(k in (r.get("devices") or []) for r in _rooms for k in keys):
        rooms_edit(drop)
    return gone


VIRTUAL_PREFIXES = ("blaster:", "panel:")       # ids of the map's things that are no registry device: never a gone device


def forgotten() -> list:
    """The devices that are gone but still stored: [{id, name, data, kept}]; data are the leftovers Forget removes,
    kept a rule or a panel that names it (never removed: reported)."""
    with _settings_lock:
        names = dict(_name_overrides)
    out = []
    for key, places in sorted(_orphan_refs().items()):
        if key.startswith(VIRTUAL_PREFIXES):
            continue
        data = [p for p in places if p.split(":", 1)[0] in DATA_REF_KINDS]
        name = names.get(key)
        out.append({"id": key, "name": name if isinstance(name, str) and name else key, "data": data, "kept": [p for p in places if p not in data]})
    return out


@app.get("/api/devices/forgotten")
def devices_forgotten(request: Request):
    """Settings -> All devices -> Forgotten (the house token)."""
    _house_token_only(request)
    return {"items": forgotten()}


@app.delete("/api/devices/{dev_id}/leftovers")
def device_forget(dev_id: str, request: Request):
    """Forget a gone device: all its data leftovers go in one step; a rule or a panel naming it stays and is returned."""
    _house_token_only(request)
    if dev_id in DEVICES:
        raise HTTPException(400, f"'{dev_id}' exists: nothing to forget")
    gone = _prune_data_refs({dev_id})
    left = _orphan_refs().get(dev_id) or []
    if not gone and not left:
        raise HTTPException(404, "nothing is stored for it")
    log.info(f"[forget] {dev_id}: {len(gone)} leftovers removed, {len(left)} kept")
    return {"ok": True, "removed": [f"{kind}:{where}" for kind, _k, where in gone], "kept": left}


def _audit_orphan_refs() -> dict:
    """Log every stored reference that points at a device key nothing resolves to.
    Silent orphans are the single most expensive failure mode in this stack: a
    remote button whose target was renamed just stops working, with no error
    anywhere. Runs at startup; repair with POST /api/devices/relink."""
    orphans = _orphan_refs()
    if orphans:
        log.warning("[audit] references to devices that do NOT exist "
              "(fix: POST /api/devices/relink):")
        for key, places in sorted(orphans.items()):
            log.info(f"[audit]   '{key}' <- {len(places)} ref(s): {', '.join(places[:6])}"
                  + (" ..." if len(places) > 6 else ""))
    else:
        log.info("[audit] no orphaned device references")
    return orphans


# --- ESPHome / WiFi discovery (the second half of "Add") ---------------------
# ESPHome with an `mqtt:` block publishes retained Home-Assistant-style discovery
# configs to homeassistant/<component>/<node>/<object>/config. Reading those turns
# a WiFi ESP node into a dashboard device without hand-editing mqtt_devices.json,
# which used to be the only way to onboard one.
_HA_DISCOVERY_WILDCARD = "homeassistant/+/+/+/config"
