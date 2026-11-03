# The core: favourites, rooms, tab order, tile sizes.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# Favorites (user-curated channel-level pins for the "Favourites" tab)
# ---------------------------------------------------------------------------
class FavoriteBody(Body):
    alias: str


def _find_fav(device: str, code: str) -> int:
    for i, f in enumerate(_favorites):
        if f.get("device") == device and f.get("code") == code:
            return i
    return -1


@app.get("/api/favorites")
def get_favorites():
    with _favorites_lock:
        return list(_favorites)


@use_route
@app.put("/api/favorites/{device}/{code}")
def put_favorite(device: str, code: str, body: FavoriteBody):
    if device not in DEVICES:
        raise HTTPException(404, "unknown device")
    cfg = DEVICES[device]
    ctl = cfg["_ctl"].get(code)
    if not ctl:
        raise HTTPException(400, f"target has no code '{code}'")
    # switch = indicator tile (a tap toggles)
    if ctl["kind"] != "switch":
        raise HTTPException(400, "favorites support only switch channels")
    with _favorites_lock:
        idx = _find_fav(device, code)
        alias = (body.alias or "").strip()
        entry = {"device": device, "code": code, "alias": check_name(alias, "favourite", most=40) if alias else code}
        if idx >= 0:
            _favorites[idx] = entry
        else:
            _favorites.append(entry)
        _save_settings()
    return {"ok": True, "favorite": entry}


@use_route
@app.delete("/api/favorites/{device}/{code}")
def delete_favorite(device: str, code: str):
    with _favorites_lock:
        idx = _find_fav(device, code)
        if idx < 0:
            raise HTTPException(404, "not in favorites")
        removed = _favorites.pop(idx)
        _save_settings()
    return {"ok": True, "removed": removed}


class ReorderBody(Body):
    order: list[str]  # "device/code" (favourites), room ids or device ids, in the wanted order


@use_route
@app.post("/api/favorites/reorder")
def reorder_favorites(body: ReorderBody):
    with _favorites_lock:
        index = {f"{f['device']}/{f['code']}": f for f in _favorites}
        new = []
        for key in body.order:
            if key in index:
                new.append(index.pop(key))
        # leftover entries appended at the end (preserve any not in order)
        new.extend(index.values())
        _favorites[:] = new
        _save_settings()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Rooms (living zones: named device groups; a device may be in many rooms)
# ---------------------------------------------------------------------------
class RoomBody(Body):
    name: Optional[str] = None               # None = leave the name alone (a PUT that moves devices)
    devices: Optional[list[str]] = None      # None = leave the membership alone


def _find_room(rid: str) -> int:
    for i, r in enumerate(_rooms):
        if r.get("id") == rid:
            return i
    return -1


def _clean_room_devices(devices: list, have=()) -> list:
    """Validate + dedupe a room's device list, preserving order. Only an id this change adds must be a device: one the
    room has already passes (a device not loaded yet, or a stale id the dashboard sends back with the whole list)."""
    unknown = [d for d in devices if d not in DEVICES and d not in have]
    if unknown:
        raise HTTPException(400, f"unknown devices: {', '.join(map(str, unknown))}")
    seen: set = set()
    out = []
    for d in devices:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def get_rooms():
    with _rooms_lock:
        return list(_rooms)


def served_room(dev: str) -> str:
    """The id of the room a device serves: the one picked for it (Serves), else the first room it stands in; '' for none.
    Voice, the room tabs and family rights go by it; the map and the device's own Room by where it stands."""
    with _rooms_lock:
        rid = _serves.get(dev)
        if rid and _find_room(rid) >= 0:
            return rid
        return next((r["id"] for r in _rooms if dev in (r.get("devices") or [])), "")


def served_devices(rid: str) -> list:
    """The devices that serve room `rid`: those standing in it that serve no other room, then those picked to serve it."""
    with _rooms_lock:
        idx = _find_room(rid)
        if idx < 0:
            return []
        other = lambda d: _serves.get(d) not in (None, rid) and _find_room(_serves[d]) >= 0
        here = [d for d in _rooms[idx].get("devices") or [] if not other(d)]
        return here + [d for d, r in _serves.items() if r == rid and d not in here]


ROOM_SENSOR_KINDS = ("temperature", "humidity")
OUTDOOR_CODES = ("wind", "wind_speed", "rain", "rain_rate")    # a weather station's: it is outside, whatever room it is listed in


def _is_kind(c: dict, kind: str) -> bool:
    code = str(c.get("code") or "")
    if c.get("kind") != "sensor":
        return False
    return code in ("temperature", "room_temp") or "temperature" in code.lower() if kind == "temperature" else code == "humidity"


def room_sensor_options(rid: str, kind: str) -> list:
    """Every channel of `kind` in the room, a module's own board ("advanced") too: what a hand may choose. -> [{dev, code, advanced, preferred}]"""
    with _rooms_lock:
        idx = _find_room(rid)
        devs = list(_rooms[idx].get("devices") or []) if idx >= 0 else []
    out = []
    for did in devs:
        d = DEVICES.get(did) or {}
        src = d.get("room_temp_from")
        if src == "room" or (src and src in devs):
            continue                                             # a copy of the room's own reading, or of a sensor the room has itself
        vals = _status_copy(did, {}).get("values") or {}
        outdoor = bool(d.get("outdoor")) or any(c.get("code") in OUTDOOR_CODES for c in d.get("controls") or [])
        for c in d.get("controls") or []:
            if not _is_kind(c, kind):
                continue
            if d.get("category") == "climate" and not isinstance(vals.get(c["code"]), (int, float)):
                continue                                         # an AC's room channel with nothing behind it (an IR AC, no source)
            own = d.get("category") == "climate" and not src     # an AC's own thermometer sits in its box: a board's, like a module's
            out.append({"dev": did, "code": c["code"], "advanced": bool(c.get("advanced")) or own, "preferred": bool(c.get("preferred")),
                        "outdoor": outdoor})
    return out


def room_sensor_order(rid: str, kind: str) -> dict:
    """The sources of a room's reading in the order they are tried: a hand choice alone (offline: no reading, never a silent other),
    else auto: no module board, nothing outdoors; a channel marked preferred, then by id. A hand choice whose device left the
    room is not read: auto, and moved says where it is. An AC that reads the room itself is never the pick (a loop).
    -> {pick, order: ["dev/code"], moved?: {dev, room}}"""
    with _settings_lock:
        pick = (_room_sensors.get(rid) or {}).get(kind)
    if pick and (DEVICES.get(pick.rpartition("/")[0]) or {}).get("room_temp_from") == "room":
        pick = None
    moved = None
    if pick:
        dev = pick.rpartition("/")[0]
        with _rooms_lock:
            here = next((r for r in _rooms if dev in (r.get("devices") or [])), None)
        if here is not None and here.get("id") == rid:
            return {"pick": pick, "order": [pick]}
        moved = {"dev": dev, "room": (here or {}).get("name") or ""}
    opts = [o for o in room_sensor_options(rid, kind) if not o["advanced"] and not o["outdoor"]]
    opts.sort(key=lambda o: (not o["preferred"], o["dev"], o["code"]))
    return {"pick": "auto", "order": [f"{o['dev']}/{o['code']}" for o in opts], **({"moved": moved} if moved else {})}


def _live_value(dev: str, code: str):
    st = _status_copy(dev, {})
    v = (st.get("values") or {}).get(code)
    ok = st.get("online") is not False and not st.get("stale") and isinstance(v, (int, float)) and not isinstance(v, bool)
    return (v if ok else None), st.get("last_seen")


def room_reading(rid: str, kind: str) -> "dict | None":
    """The first live source of the room's order (online, not stale, a number). -> {dev, code, value} or None"""
    for src in room_sensor_order(rid, kind)["order"]:
        dev, _, code = src.rpartition("/")
        v, _seen = _live_value(dev, code)
        if v is not None:
            return {"dev": dev, "code": code, "value": v}
    return None


def room_sensor_silent(rid: str, kind: str) -> "dict | None":
    """A hand-chosen source that gives no reading now: {dev, name, since} (since: its last report, or None). Auto: None."""
    pick = room_sensor_order(rid, kind)["pick"]
    if pick == "auto":
        return None
    dev, _, code = pick.rpartition("/")
    v, seen = _live_value(dev, code)
    return None if v is not None else {"dev": dev, "name": (DEVICES.get(dev) or {}).get("name") or dev, "since": seen}


def _room_sensor_health() -> list:
    rows = []
    for r in get_rooms():
        for k in ROOM_SENSOR_KINDS:
            m = room_sensor_silent(r["id"], k)
            if m:
                when = time.strftime("%H:%M", time.localtime(m["since"])) if m["since"] else "?"
                rows.append(health_row(f"room-sensor-{r['id']}-{k}", "warn", "The chosen " + ("thermometer" if k == "temperature" else "hygrometer") +
                                       " of {0} does not answer", "{1} has not answered since {2}", "Pick another one, or Auto, from the room's temperature",
                                       args=(r.get("name") or r["id"], m["name"], when), link="rooms"))
    return rows


@app.get("/api/rooms")
def api_rooms():
    """The rooms, each with the order its temperature and humidity are read in (the page takes the first live one)."""
    return [{**r, "serves": served_devices(r["id"]), "sensors": {k: room_sensor_order(r["id"], k) for k in ROOM_SENSOR_KINDS}} for r in get_rooms()]


@app.get("/api/rooms/{rid}/sensors")
def get_room_sensors(rid: str):
    """What may read the room's temperature and humidity, with each one's value now, and what reads it."""
    _room_copy(rid)
    out = {}
    for k in ROOM_SENSOR_KINDS:
        opts = room_sensor_options(rid, k)
        for o in opts:
            o["value"] = (_status_copy(o["dev"], {}).get("values") or {}).get(o["code"])
            o["name"] = (DEVICES.get(o["dev"]) or {}).get("name") or o["dev"]
        out[k] = {**room_sensor_order(rid, k), "options": opts, "reading": room_reading(rid, k), "silent": room_sensor_silent(rid, k)}
    return out


class RoomSensorBody(Body):
    kind: str
    source: str = Field(max_length=300)              # "auto" or "dev/code", one of the room's options


@app.put("/api/rooms/{rid}/sensors")
def set_room_sensor(rid: str, body: RoomSensorBody):
    _room_copy(rid)
    if body.kind not in ROOM_SENSOR_KINDS:
        raise HTTPException(400, f"kind is one of: {', '.join(ROOM_SENSOR_KINDS)}")
    if body.source != "auto" and body.source not in {f"{o['dev']}/{o['code']}" for o in room_sensor_options(rid, body.kind)}:
        raise HTTPException(400, f"not a {body.kind} of this room")
    with _settings_lock:
        picks = _room_sensors.setdefault(rid, {})
        if body.source == "auto":
            picks.pop(body.kind, None)
            if not picks:
                _room_sensors.pop(rid, None)
        else:
            picks[body.kind] = body.source
        _save_settings()
    return {"ok": True, **room_sensor_order(rid, body.kind)}


ROOM_LOCKED: set = set()          # devices whose room a plug-in owns (the map: where the icon stands): the room API refuses to move them
ROOM_ZONED: set = set()           # rooms a plug-in owns (the map: a zone is the room): the room API refuses to delete them; the tabs hide them while empty
ROOM_HIDDEN: set = set()          # rooms a plug-in keeps only because something names them (no zone, no device): the tabs hide them


def set_room_locked(devices, rooms=(), hidden=()):
    """The devices, the rooms a plug-in owns and the rooms its tabs hide, replaced in one step (under the lock the room API reads them with)."""
    with _rooms_lock:
        ROOM_LOCKED.clear()
        ROOM_LOCKED.update(devices)
        ROOM_ZONED.clear()
        ROOM_ZONED.update(rooms)
        ROOM_HIDDEN.clear()
        ROOM_HIDDEN.update(hidden)


def _room_sets() -> dict:
    """device -> the names of the rooms it is in now."""
    with _rooms_lock:
        out: dict = {}
        for r in _rooms:
            for d in r.get("devices") or ():
                out.setdefault(d, set()).add(r.get("name") or "")
        return out


def _rooms_moved(before: dict) -> None:
    """ON_ROOM_MOVE for every device that came into a room since `before` (outside the lock: a handler may rename)."""
    for dev, names in _room_sets().items():
        for new in sorted(names - before.get(dev, set())):
            for fn in list(ON_ROOM_MOVE):
                try:
                    fn(dev, sorted(before.get(dev, ())), new)
                except Exception:                      # noqa: BLE001  a follow-up never undoes the move
                    log.exception(f"[rooms] room move hook {getattr(fn, '__name__', fn)} failed for {dev}")


def rooms_edit(fn):
    """Edit the dashboard rooms in one step under the settings lock: `fn(rooms)` gets a deep copy to change in place (an exception leaves the
    rooms as they were); what it leaves is cleaned and saved. A plug-in that owns the rooms (the map) uses it. -> a copy of the new list."""
    had = _room_sets()
    out = _rooms_edit(fn)
    _rooms_moved(had)
    return out


def _rooms_edit(fn):
    with _rooms_lock:
        before = {r.get("id") for r in _rooms}
        work = copy.deepcopy(_rooms)
        fn(work)
        _clean_rooms(work)
        _rooms[:] = work
        for rid in before - {r["id"] for r in work}:
            for d in [d for d, r in _serves.items() if r == rid]:
                _serves.pop(d)                         # a deleted room: its devices serve where they stand again
            _tile_sizes.pop(f"room:{rid}", None)
            _tab_order.pop(f"room:{rid}", None)
            _room_sensors.pop(rid, None)
        _save_settings()
        return copy.deepcopy(work)


@app.get("/api/rooms/locked")
def rooms_locked():
    """The devices whose room comes from a plug-in (the dashboard shows their room selector read-only)."""
    with _rooms_lock:
        return {"devices": sorted(ROOM_LOCKED), "rooms": sorted(ROOM_ZONED), "hidden": sorted(ROOM_HIDDEN)}


def _refuse_locked(old: list, new: list) -> None:
    moved = sorted(ROOM_LOCKED & (set(old) ^ set(new)))
    if moved:
        raise HTTPException(409, f"the room of {moved[0]} comes from the map: move its icon there")


def _rooms_owner(what: str):
    """The owner's handler of `what`, or None: the core keeps the rooms itself (no map: a warning, the tests aside)."""
    fn = ROOMS_OWNER.get(what)
    if fn is None and "pytest" not in sys.modules:
        log.warning(f"[rooms] no plug-in owns the rooms (the map is required): the core {what}s the room itself")
    return fn


def _room_copy(rid: str) -> dict:
    with _rooms_lock:
        idx = _find_room(rid)
        if idx < 0:
            raise HTTPException(404, "unknown room")
        return copy.deepcopy(_rooms[idx])


def _set_room_devices(rid: str, devices: list) -> dict:
    had = _room_sets()
    out = _set_room_devices_locked(rid, devices)
    _rooms_moved(had)
    return out


def _set_room_devices_locked(rid: str, devices: list) -> dict:
    with _rooms_lock:
        idx = _find_room(rid)
        if idx < 0:
            raise HTTPException(404, "unknown room")
        room = _rooms[idx]
        new = _clean_room_devices(devices, room.get("devices") or ())
        _refuse_locked(room.get("devices") or [], new)
        room["devices"] = new
        _save_settings()
        return dict(room)


@app.post("/api/rooms")
def create_room(body: RoomBody):
    name = check_name(body.name, "room", [r.get("name") for r in get_rooms()], most=40)
    devices = _clean_room_devices(body.devices or [])
    own = _rooms_owner("create")
    if own:                                      # outside the rooms' lock: the owner takes its own first, then this one
        _refuse_locked([], devices)
        room = own(name)
        return {"ok": True, "room": _set_room_devices(room["id"], devices) if devices else room}
    had = _room_sets()
    with _rooms_lock:
        _refuse_locked([], devices)
        rid = f"r{int(time.time() * 1000)}"
        while _find_room(rid) >= 0:
            rid += "x"
        room = {"id": rid, "name": name, "devices": devices}
        _rooms.append(room)
        _save_settings()
    _rooms_moved(had)
    return {"ok": True, "room": room}


@app.put("/api/rooms/{rid}")
def update_room(rid: str, body: RoomBody):
    name = None if body.name is None else check_name(body.name, "room", [r.get("name") for r in get_rooms() if r.get("id") != rid], most=40)
    own = _rooms_owner("rename") if name else None
    if own:
        room = _room_copy(rid)
        room = own(rid, name) if name != room.get("name") else room
        return {"ok": True, "room": _set_room_devices(rid, body.devices) if body.devices is not None else room}
    had = _room_sets()
    with _rooms_lock:
        idx = _find_room(rid)
        if idx < 0:
            raise HTTPException(404, "unknown room")
        room = _rooms[idx]
        if name:
            room["name"] = name
        if body.devices is not None:
            new = _clean_room_devices(body.devices, room.get("devices") or ())
            _refuse_locked(room.get("devices") or [], new)
            room["devices"] = new
        _save_settings()
    _rooms_moved(had)
    return {"ok": True, "room": room}


def room_refs(rid: str) -> list:
    """What still names the room: a rule of the house (by id or name) and what the plug-ins tell (ROOM_REFS). -> [{kind, name}]."""
    name = _room_copy(rid).get("name")
    with _rooms_lock:
        out = [{"kind": "rule", "name": k} for k, v in (("bindings", _bindings), ("automations", _autos))
               if any(json.dumps(x, ensure_ascii=False) in json.dumps(v, ensure_ascii=False) for x in (rid, name) if x)]
    for fn in ROOM_REFS:
        try:
            out += [{"kind": str(r.get("kind", "")), "name": str(r.get("name", ""))} for r in fn(rid) or []]
        except Exception as e:                   # one plug-in's answer must not stop a delete
            log.warning(f"[rooms] ROOM_REFS {getattr(fn, '__module__', fn)}: {e}")
    return out


@app.get("/api/rooms/{rid}/refs")
def get_room_refs(rid: str):
    """Asked before a delete: the confirmation names what still names the room. Changes nothing."""
    return {"named_by": room_refs(rid)}


@app.delete("/api/rooms/{rid}")
def delete_room(rid: str):
    own = _rooms_owner("delete")
    if own:
        named, removed = room_refs(rid), _room_copy(rid)
        own(rid)
        return {"ok": True, "removed": removed, "named_by": named}
    with _rooms_lock:
        idx = _find_room(rid)
        if idx < 0:
            raise HTTPException(404, "unknown room")
        if rid in ROOM_ZONED or ROOM_LOCKED & set(_rooms[idx].get("devices") or []):
            raise HTTPException(409, "this room comes from the map: delete its zone there")
        removed = _rooms.pop(idx)
        for d in [d for d, r in _serves.items() if r == rid]:
            _serves.pop(d)
        _tile_sizes.pop(f"room:{rid}", None)
        _tab_order.pop(f"room:{rid}", None)
        _room_sensors.pop(rid, None)
        _save_settings()
    return {"ok": True, "removed": removed}


# ---------------------------------------------------------------------------
# Per-tab manual device order (drag-reorder on the dashboard's category tabs)
# ---------------------------------------------------------------------------
@app.get("/api/tab_order")
def get_tab_order():
    with _tab_order_lock:
        return dict(_tab_order)


@app.post("/api/tab_order/{tab_id}")
def set_tab_order(tab_id: str, body: ReorderBody):
    order = [x for x in body.order if isinstance(x, str)]
    with _tab_order_lock:
        _tab_order[tab_id] = order
        _save_settings()
    return {"ok": True, "tab": tab_id, "count": len(order)}


# ---------------------------------------------------------------------------
# Tile sizes (resize handle on the dashboard grids; scope = tab or room:<id>)
# ---------------------------------------------------------------------------
class TileSizeBody(Body):
    device: str
    w: int = 0
    h: int = 0
    col: int = -1
    hn: Optional[int] = None      # the min height on a narrow screen (<= 640 px); absent = kept as stored


@app.get("/api/tile_sizes")
def get_tile_sizes():
    with _tile_sizes_lock:
        return dict(_tile_sizes)


@app.put("/api/tile_sizes/{scope}")
def put_tile_size(scope: str, body: TileSizeBody):
    """w = columns, h = min-height px, col = pinned column, hn = min-height px on a narrow screen; all 0 (col -1) = auto."""
    if body.device not in DEVICES:
        raise HTTPException(404, "unknown device")
    w = max(0, min(int(body.w), 8))
    h = max(0, min(int(body.h), 2000))
    col = max(-1, min(int(body.col), 32))
    with _tile_sizes_lock:
        sizes = _tile_sizes.setdefault(scope, {})
        hn = (max(0, min(int(body.hn), 2000)) if body.hn is not None
              else int((sizes.get(body.device) or {}).get("hn") or 0))
        if not w and not h and not hn and col < 0:
            sizes.pop(body.device, None)
            if not sizes:
                _tile_sizes.pop(scope, None)
        else:
            sizes[body.device] = {"w": w, "h": h, "col": col, **({"hn": hn} if hn else {})}
        _save_settings()
    return {"ok": True, "scope": scope, "device": body.device,
            "w": w, "h": h, "col": col, "hn": hn}
