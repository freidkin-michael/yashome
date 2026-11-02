# The core: what plug-ins call: data dirs, devices, parts, merge, hook checks.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# Plug-in loader (docs/plugins.md). Loaded last, so every hook above exists.
# ---------------------------------------------------------------------------
# plugins/ of the checkout ships with the core; plugins-extra/ is where compose mounts the host's
# PLUGINS_EXTRA_DIR (your own). Paths are fixed on purpose: a host path from .env reaches the
# container through env_file and would point nowhere. Which of them load: PLUGINS in .env.
PLUGINS_DIR = ROOT / "plugins"
PLUGINS_EXTRA = ROOT / "plugins-extra"
PLUGIN_PATHS: dict = {}      # id -> the directory it loaded from (its public files are served from there)
_PLUGIN_OFF: list = []       # present but not in PLUGINS: listed in Settings as off


def _enabled_plugins() -> "set | None":
    """PLUGINS in .env: comma separated names; "*" = every plug-in present; empty = none."""
    raw = re.sub(r"\s#.*$", "", os.environ.get("PLUGINS") or "")
    names = {x.strip().strip("\"'") for x in raw.split(",")} - {""}
    return None if "*" in names else names


def plugin_data(name: str) -> Path:
    """The directory a plug-in keeps its own files in (created on first use): HOUSE/plugin-data/<name>."""
    if not _PLUGIN_NAME.match(name or ""):
        raise ValueError(f"plugin_data: {name!r} is not a plug-in name")
    d = PLUGIN_DATA / name
    d.mkdir(parents=True, exist_ok=True)
    return d
PLUGIN_STATE = HOUSE / "plugin-state"


def plugin_state(name: str) -> Path:
    """The directory for what a plug-in writes itself (keys it paired, counters): HOUSE/plugin-state/<name>,
    kept apart from plugin-data/ so a house's git repository holds its configuration only."""
    if not _PLUGIN_NAME.match(name or ""):
        raise ValueError(f"plugin_state: {name!r} is not a plug-in name")
    d = PLUGIN_STATE / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def plugin_state_file(name: str, filename: str, merge=None) -> Path:
    """plugin_state(name)/filename. A copy an older version kept in plugin-data/<name>/ moves there once
    (a marker remembers it: if the new copy is lost later, the old one is NOT taken back). Both there:
    merge(new_bytes, old_bytes) -> bytes when given, and the old one is put aside as .migrated-<time>."""
    new, old = plugin_state(name) / filename, PLUGIN_DATA / name / filename
    marker = old.with_name(f".moved-{filename}")       # next to the old copy: a restored house keeps it
    if old.is_file() and not new.exists() and not marker.exists():
        os.replace(old, new)
        marker.touch()
        log.info(f"[{name}] {filename} moved from plugin-data/ to plugin-state/")
    elif old.is_file() and new.exists():
        if merge:
            fd, tmp = tempfile.mkstemp(dir=new.parent, prefix=new.name + ".", suffix=".tmp")
            with os.fdopen(fd, "wb") as fh:
                fh.write(merge(new.read_bytes(), old.read_bytes()))
            os.replace(tmp, new)
        os.replace(old, old.with_name(f"{filename}.migrated-{time.strftime('%Y%m%d-%H%M%S')}"))
        marker.touch()
        log.warning(f"[{name}] {filename} was in plugin-data/ and plugin-state/: plugin-state/ kept"
                    + (" (merged)" if merge else "") + ", the other put aside")
    elif old.is_file():
        log.error(f"[{name}] plugin-state/{name}/{filename} is gone; the old plugin-data/ copy is not taken back")
    return new


_PLUGIN_ASSETS = (".js", ".mjs", ".css", ".json", ".png", ".svg", ".html")   # served open, like the shell: no secrets in them (the QR's landing page family/static/pair.html)
_PLUGIN_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


_PLUGIN_DEVICES: set = set()   # ids a plug-in added with register_device: the only ones update/remove touch
_DEVICE_OWNER: dict = {}       # id -> the plug-in that registered it (None: not called from a plug-in)
_PLUGIN_PKG = "yashome_plugins"


def _calling_plugin():
    """Name of the plug-in whose code is on the stack (its module is yashome_plugins.<name>...)."""
    f = sys._getframe(1)
    while f is not None:
        mod = f.f_globals.get("__name__", "")
        if mod.startswith(_PLUGIN_PKG + "."):
            return mod.split(".")[1]
        f = f.f_back
    return None


def _plugin_id_ok(did) -> bool:
    """A plug-in's ids come from outside (a zigbee2mqtt friendly_name: spaces, any script) and are
    one MQTT topic level and one REST path segment: printable, without / + # (and |, the rule-id separator)."""
    return (isinstance(did, str) and 0 < len(did) <= 256 and did.isprintable()
            and not any(ch in did for ch in "/+#|"))


def register_device(dev: dict) -> dict:
    """Add a device a plug-in owns (any transport): channel map, saved rename, cache seed -
    the same path mqtt_devices.json entries take, so the dashboard treats it alike."""
    if not _plugin_id_ok(dev.get("id")):
        raise ValueError(f"device id {dev.get('id')!r}: 1-256 printable chars without / + # |")
    if dev["id"] in DEVICES:
        raise ValueError(f"device id {dev['id']!r} is taken (a {DEVICES[dev['id']].get('transport')} device)")
    owner = _TRANSPORT_OWNER.get(dev.get("transport"))
    if owner is not None and owner != _calling_plugin():
        raise ValueError(f"transport {dev.get('transport')!r} belongs to plug-in {owner!r}")
    d = _plugin_device(dev)
    DEVICES[d["id"]] = d
    BY_ID_NAME[d["id"]] = d["name"]
    _PLUGIN_DEVICES.add(d["id"])
    _DEVICE_OWNER[d["id"]] = _calling_plugin()
    with _cache_lock:
        _status_cache.setdefault(d["id"], {"online": False, "values": {}, "raw": {}})
    return d


def _owned(dev_id: str, transport) -> dict:
    d = DEVICES.get(dev_id)
    if dev_id not in _PLUGIN_DEVICES or d is None or d.get("transport") != transport:
        raise ValueError(f"device {dev_id!r} is not a {transport!r} device a plug-in registered")
    if _DEVICE_OWNER.get(dev_id) != _calling_plugin():
        raise ValueError(f"device {dev_id!r} belongs to plug-in {_DEVICE_OWNER.get(dev_id)!r}")
    return d


def _forget_codes(dev_id: str, before: set):
    """Channels a device had and has no more: out of its cached values and the retained mirror."""
    gone = set(before) - set((DEVICES.get(dev_id) or {}).get("_ctl") or {})
    if not gone:
        return
    with _cache_lock:
        st = _status_cache.get(dev_id)
        for part in ("values", "raw"):                   # raw holds the device's own words per code
            if st and isinstance(st.get(part), dict):
                st[part] = {k: v for k, v in st[part].items() if k not in gone}
    _status_cache_dirty.set()
    cli = _mqtt_client
    for code in gone:
        for topic in (f"home/state/{dev_id}/{code}", f"home/state/{dev_id}/{code}_pct"):
            _mqtt_last_pub.pop(topic, None)
            if cli is not None:
                try:
                    cli.publish(topic, None, retain=True)      # empty retained = deleted
                except Exception as e:                         # noqa: BLE001
                    log.warning(f"[mirror] clear {topic}: {e}")


def update_device(dev: dict) -> dict:
    """Replace a device the plug-in registered (same id and transport): new name, category,
    controls. Its state stays, but for the channels it no longer has; a saved rename still wins."""
    _owned(dev.get("id"), dev.get("transport"))
    before = set(DEVICES[dev["id"]].get("_ctl") or {})
    d = _plugin_device(dev)
    DEVICES[d["id"]] = d
    BY_ID_NAME[d["id"]] = d["name"]
    _forget_codes(d["id"], before)
    return d


# One device is one physical thing (docs/devices.md). A plug-in adds a PART to a device another owns (an IR remote to
# a TV, a microphone and a speaker to a tablet): its channels join the device and go to the part's transport.
PARTS: dict = {}       # dev_id -> {part: {plugin, transport, channels, info, cfg, map}}
MERGE_PLAN: list = []  # fn(old, new) -> {"<file or what>": count}: a plug-in's own references a merge moves
DEVICE_MERGED: list = []   # fn(old, new) after a merge: the plug-in stops registering old (and moves its own data)


def extend_device(dev_id: str, part: str, channels: list, transport: str, info=None, cfg=None, map=None) -> None:
    """Add (or replace) a part of a device, registered now or later. A code the device has already stays its own and
    the part is its fallback, through map {code: {value: the part's code}} ("*" for any value); info: what the part
    shows (its remote, blaster); cfg: what the part's transport needs for a command."""
    PARTS.setdefault(dev_id, {})[part] = {"plugin": _calling_plugin(), "transport": transport, "channels": [dict(c) for c in channels or []],
                                         "info": dict(info or {}), "cfg": dict(cfg or {}), "map": dict(map or {})}
    if dev_id in DEVICES:
        _apply_parts(DEVICES[dev_id])


def drop_part(dev_id: str, part: str) -> None:
    if (PARTS.get(dev_id) or {}).pop(part, None) is not None and dev_id in DEVICES:
        _apply_parts(DEVICES[dev_id])


def _apply_parts(d: dict) -> None:
    """The device's own channels, then each part's (a new code is the part's, a code it has already gets the part as fallback)."""
    own = d.setdefault("_own_controls", [c for c in d.get("controls") or [] if not c.get("_part")])
    ctl = [{k: v for k, v in c.items() if k != "_fallback"} for c in own]
    have = {c["code"]: c for c in ctl}
    for name, p in sorted((PARTS.get(d["id"]) or {}).items()):
        for c in _safe_controls(d["id"], p["channels"]):
            if c["code"] in have:
                have[c["code"]].setdefault("_fallback", []).append(name)
                continue
            c = {**c, "part": name, "_part": name, "_transport": p["transport"]}
            c.setdefault("_label0", c.get("label", ""))
            ctl.append(c)
            have[c["code"]] = c
        for code in p.get("map") or {}:                     # a code of the device the part can also do (power by an IR key)
            if code in have and not have[code].get("_part") and name not in have[code].get("_fallback", []):
                have[code].setdefault("_fallback", []).append(name)
    d["controls"] = ctl
    _apply_channel_names(d)
    d["_ctl"] = {c["code"]: c for c in ctl}
    d["parts"] = {n: {"transport": p["transport"], **p["info"]} for n, p in sorted((PARTS.get(d["id"]) or {}).items())}


def _part_cfg(dev_id: str, cfg: dict, ctl: dict) -> dict:
    """What the transport of a part's channel gets: the device with the part's own settings over it."""
    p = (PARTS.get(dev_id) or {}).get(ctl.get("_part") or "")
    return {**cfg, **p["cfg"]} if p else cfg


async def _part_fallback(dev_id: str, cfg: dict, ctl: dict, val):
    """A shared code the device's own transport could not do: the first part that maps it does (a stateless press). -> part or None"""
    for name in ctl.get("_fallback") or []:
        p = (PARTS.get(dev_id) or {}).get(name) or {}
        m = (p.get("map") or {}).get(ctl["code"]) or {}
        code = m.get(str(val).lower()) or m.get("*")
        pc = next((c for c in p.get("channels") or [] if c.get("code") == code), None)
        if not pc or pc.get("kind") not in CONTROL_KINDS:
            continue
        try:
            await run_in_threadpool(CONTROL_KINDS[pc["kind"]], {**cfg, **p["cfg"]}, pc, val)
        except Exception as e:                       # noqa: BLE001 - the next part, or the first error stands
            log.warning(f"[control] {cfg.get('name')}/{ctl['code']}: fallback {name} failed: {e}")
            continue
        _last_control_ts[dev_id] = time.time()
        log.info(f"[control] {cfg.get('name')}/{ctl['code']}={val} via its part {name} ({code})")
        return name
    return None


def merged(old: str) -> "str | None":
    """The device an id was merged into (a plug-in does not register it again)."""
    with _settings_lock:
        return next((new for new, olds in _aliases.items() if old in olds), None)


def merge_plan(old: str, new: str) -> dict:
    """What merging old into new would move, store by store and plug-in by plug-in (nothing is written)."""
    if old == new or old not in DEVICES or new not in DEVICES:
        raise HTTPException(400, "two different devices of the house")
    if DEVICES[old].get("transport") == "mqtt" and old not in _PLUGIN_DEVICES:
        raise HTTPException(400, f"{old} is an MQTT device of the house: merge the other way, or remove it on its page")
    moves: dict = {}
    with _settings_lock:
        for key, st in _STORES.items():
            if st["migrate"]:
                n = sum(st["migrate"](copy.deepcopy(globals()[st["var"]]), old, new, set()).values())
                if n:
                    moves[key] = n
    for fn in list(MERGE_PLAN):
        try:
            moves.update({str(k): int(v) for k, v in (fn(old, new) or {}).items() if v})
        except Exception as e:                       # noqa: BLE001
            moves[f"{getattr(fn, '__module__', '?')}: {type(e).__name__}"] = -1
    keep = set(DEVICES[new].get("_ctl") or {})
    return {"old": old, "new": new, "moves": moves,
            "codes_only_old": sorted(c for c in (DEVICES[old].get("_ctl") or {}) if c not in keep)}


_merge_lock = threading.Lock()


class MergeBody(Body):
    old: str = Field(max_length=256)
    new: str = Field(max_length=256)


@app.get("/api/devices/merge-plan")
def devices_merge_plan(old: str, new: str, request: Request):
    """The dry run of a merge (the house token)."""
    _house_token_only(request)
    return merge_plan(old, new)


@app.post("/api/devices/merge")
def devices_merge(body: MergeBody, request: Request):
    """Merge old into new: a backup first (its name is returned: restoring it is the way back), then every reference
    moves (migrate_device_key and the plug-ins' own), old is remembered as new's alias and is no device any more."""
    _house_token_only(request)
    merge_plan(body.old, body.new)                   # refused at once, before waiting for another merge
    with _merge_lock:                                # one at a time: a merge that ran meanwhile may have removed old or new
        plan = merge_plan(body.old, body.new)
        b = backup_now(light=True)
        if not b.get("ok"):
            raise HTTPException(503, f"no merge without a backup: {b.get('error')}")
        touched = _migrate_device_key(body.old, body.new)
        with _settings_lock:
            _aliases[body.new] = list(dict.fromkeys((_aliases.get(body.new) or []) + [body.old]))
            _save_settings()
        for fn in list(DEVICE_MERGED):
            try:
                fn(body.old, body.new)
            except Exception as e:                   # noqa: BLE001 - the merge stands; the log names who failed
                log.warning(f"[merge] {getattr(fn, '__module__', '?')}: {e}")
        gone = DEVICES.pop(body.old, None)
        BY_ID_NAME.pop(body.old, None)
        _PLUGIN_DEVICES.discard(body.old)
        _DEVICE_OWNER.pop(body.old, None)
        PARTS.pop(body.old, None)
        if gone is not None:
            _device_removed(gone)
    log.warning(f"[merge] {body.old} -> {body.new}: {touched} (backup {b['name']})")
    return {"ok": True, "backup": b["name"], "plan": plan, "migrated": touched}


def _device_removed(dev) -> None:
    """DEVICE_REMOVED for a device that left the house, whatever its transport."""
    for fn in list(DEVICE_REMOVED) if dev else ():
        try:
            fn(dev)
        except Exception as e:                      # noqa: BLE001  one plug-in must not stop the removal
            log.error(f"[plugins] on-removed {getattr(fn, '__name__', fn)}: {e}")


def remove_device(dev_id: str, transport: str, keep_state: bool = False) -> None:
    """Drop a device the plug-in registered; favourites and rules that name it show it as gone.
    keep_state: its last state stays in the cache (a device that only left its system's list for
    a moment, or was renamed there and gets relinked); the cache saver prunes it later."""
    _owned(dev_id, transport)
    dev = DEVICES.pop(dev_id, None)
    BY_ID_NAME.pop(dev_id, None)
    _PLUGIN_DEVICES.discard(dev_id)
    _DEVICE_OWNER.pop(dev_id, None)
    _cmd_forget(dev_id)
    if not keep_state:
        with _cache_lock:
            _status_cache.pop(dev_id, None)
        _device_removed(dev)                     # gone for good (not a relink): the plug-ins forget what they kept about it


def _plugin_device(dev: dict) -> dict:
    if dev.get("transport") in _CORE_TRANSPORTS:
        raise ValueError(f"transport {dev.get('transport')!r} belongs to the core")
    if any(_own_topic(dev.get(t)) for t in ("state_topic", "command_topic", "availability_topic")):
        raise ValueError("a device topic under home/state/ or home/cmd/ is the backend's own bus")
    d = dict(dev)
    if not _CAT_RE.match(str(d.get("category") or "other")):
        d["category"] = "other"
    d["controls"] = _safe_controls(d["id"], d.get("controls"))
    _mark_dimmers(d)
    for c in d["controls"]:
        c.setdefault("_label0", c.get("label", ""))
    _apply_channel_names(d)
    d["_ctl"] = {c["code"]: c for c in d["controls"]}
    d["name"] = _name_overrides.get(d["id"], d.get("name") or d["id"])
    d.setdefault("category", "other")
    _apply_parts(d)
    return d


_HOOK_DICTS = ("TRANSPORTS", "ASYNC_CONTROL", "CONTROL_KINDS", "SETTINGS_SECTIONS", "BINDING_ACTIONS",
               "TRANSPORT_BINDINGS", "STATE_ASK", "NODE_SUBTOPICS", "ROOMS_OWNER", "RESTRICTED_ALLOW")
_HOOK_LISTS = ("MQTT_SUBSCRIPTIONS", "MQTT_HANDLERS", "STARTUP_TASKS", "DEVICE_FIELDS", "ON_MQTT_CONNECT", "TOKEN_CHECKS", "KEY_REFUSALS", "MERGE_PLAN", "DEVICE_MERGED",
               "HEALTH_CHECKS", "MQTT_SHARED_READ", "ON_EVENT", "ON_REPORT",
               "RESYNC_HOOKS", "ON_RENAME", "NAME_OWNERS", "WS_FILTERS", "DEVICE_REMOVED", "DISCOVERY_IGNORE",
               "WIRING_CHANGED", "WIRING_MIGRATORS", "ROOM_REFS", "RESTRICTED_ROLE", "RESTRICTED_CHECKS", "GUEST_NOTIFY", "ADMIN_CHECKS",
               "NFC_WRITE")
_NS_OWNER: dict = {}           # MQTT namespace -> the plug-in that claimed it
_TRANSPORT_OWNER: dict = {}    # transport -> the plug-in that registered it (its devices are its own)
# names the core owns: a plug-in may not take them over
_CORE_TRANSPORTS = {"mqtt"}
_CORE_NAMESPACES = {"home", "homeassistant"}
_CORE_KINDS = {"switch", "bright", "color", "sensor", "sensor_text", "trigger", "trigger_text",
               "setting_bool", "setting_enum", "setting_int"}
_CORE_DEVICE_KEYS = {"id", "name", "category", "transport", "controls", "room", "ieee", "model", "vendor", "description",
                     "esphome", "unsupported"}


def _hooks_snapshot() -> dict:
    g = globals()
    with _cache_lock:
        cache = set(_status_cache)
    return {"dicts": {k: dict(g[k]) for k in _HOOK_DICTS}, "lists": {k: list(g[k]) for k in _HOOK_LISTS},
            "open": set(OPEN_PATHS), "open_ep": dict(OPEN_ROUTES), "use_ep": dict(USE_ROUTES), "devices": dict(DEVICES), "cache": cache,
            "routes": list(app.router.routes), "middleware": list(app.user_middleware),
            "ns": set(MQTT_NAMESPACES), "ns_owner": dict(_NS_OWNER), "pending": set(REGISTRY_PENDING),
            "tr_owner": dict(_TRANSPORT_OWNER)}


def _hooks_restore(snap: dict):
    g = globals()
    for k, v in snap["dicts"].items():
        g[k].clear(); g[k].update(v)
    for k, v in snap["lists"].items():
        g[k][:] = v
    OPEN_PATHS.clear(); OPEN_PATHS.update(snap["open"])
    OPEN_ROUTES.clear(); OPEN_ROUTES.update(snap["open_ep"])
    USE_ROUTES.clear(); USE_ROUTES.update(snap["use_ep"])
    for did in set(DEVICES) - set(snap["devices"]):
        DEVICES.pop(did, None); BY_ID_NAME.pop(did, None); _PLUGIN_DEVICES.discard(did); _DEVICE_OWNER.pop(did, None)
    DEVICES.update(snap["devices"])                      # whatever it replaced comes back too
    with _cache_lock:
        for did in set(_status_cache) - snap["cache"]:
            _status_cache.pop(did, None)
    app.router.routes[:] = snap["routes"]                # by identity: an inserted route cannot take a core one along
    app.user_middleware[:] = snap["middleware"]
    MQTT_NAMESPACES.clear(); MQTT_NAMESPACES.update(snap["ns"])
    _NS_OWNER.clear(); _NS_OWNER.update(snap["ns_owner"])
    _TRANSPORT_OWNER.clear(); _TRANSPORT_OWNER.update(snap["tr_owner"])
    with _registry_cv:
        REGISTRY_PENDING.clear(); REGISTRY_PENDING.update(snap["pending"])
        _registry_cv.notify_all()


def _core_topic(levels: list, name: str, devices=()) -> bool:
    """Not the plug-in's: homeassistant, a namespace another plug-in claimed (MQTT_NAMESPACES),
    anything starting with $ ($SYS, $share, $queue), wildcards at the top, and home/ except
    home/<its own name>/ (home/<device> of the generic MQTT devices, home/cmd and home/state are
    the core's, even for a plug-in so named). Its own namespace is all its own."""
    first = levels[0] if levels else ""
    if first in _NS_OWNER:
        return _NS_OWNER[first] != name
    if first in ("homeassistant", "#", "+", "") or first.startswith("$"):
        return True
    return first == "home" and (len(levels) < 2 or levels[1] != name
                                or name in ("cmd", "state") or name in devices)


def _hooks_check(name: str, snap: dict):
    """What a plug-in registered must not reach into the core's own names or bus, nor into what
    an earlier plug-in registered."""
    g = globals()
    for k in _HOOK_DICTS:
        taken = [n for n, v in snap["dicts"][k].items() if g[k].get(n) is not v]
        if taken:
            raise ValueError(f"{k} {sorted(taken)} are registered by an earlier plug-in (or were removed)")
    for k in _HOOK_LISTS:          # the checks below read only the tail: an insert must not get past them
        old = snap["lists"][k]
        if len(g[k]) < len(old) or any(a is not b for a, b in zip(g[k], old)):
            raise ValueError(f"{k}: append only, what the core and earlier plug-ins put there stays")
    for did, d in snap["devices"].items():
        if DEVICES.get(did) is not d:
            raise ValueError(f"device {did!r} belongs to the core or an earlier plug-in")
    if not MQTT_NAMESPACES >= snap["ns"]:
        raise ValueError("MQTT_NAMESPACES: add only, what earlier plug-ins claimed stays")
    for ns in MQTT_NAMESPACES - snap["ns"]:
        if not isinstance(ns, str) or not ns or any(ch in ns for ch in "/+#") or ns.startswith("$") \
                or ns in _CORE_NAMESPACES:
            raise ValueError(f"MQTT namespace {ns!r}: one plain top-level topic, not the core's (home, homeassistant, $...)")
        used = [t for t in snap["lists"]["MQTT_SUBSCRIPTIONS"] if t.split("/")[0] == ns] + \
               [p for p, _fn in snap["lists"]["MQTT_HANDLERS"] if tuple(p)[:1] == (ns,)]
        if used:
            raise ValueError(f"MQTT namespace {ns!r}: an earlier plug-in already uses it ({used[0]!r})")
        _NS_OWNER[ns] = name                  # checked below: its subscriptions may use it
    new = {k: set(g[k]) - set(snap["dicts"][k]) for k in _HOOK_DICTS}
    for tr in new["TRANSPORTS"] | new["ASYNC_CONTROL"]:
        early = [d for d, x in snap["devices"].items() if x.get("transport") == tr and _DEVICE_OWNER.get(d) != name]
        if early:
            raise ValueError(f"transport {tr!r}: an earlier plug-in already registered devices on it ({early[0]!r})")
        _TRANSPORT_OWNER.setdefault(tr, name)
    if new["SETTINGS_SECTIONS"] & _CORE_SECTIONS:
        raise ValueError(f"settings sections {sorted(new['SETTINGS_SECTIONS'] & _CORE_SECTIONS)} belong to the core")
    if (new["TRANSPORTS"] | new["ASYNC_CONTROL"] | new["TRANSPORT_BINDINGS"]) & _CORE_TRANSPORTS:
        raise ValueError(f"transports {sorted(_CORE_TRANSPORTS)} belong to the core")
    if new["CONTROL_KINDS"] & _CORE_KINDS:
        raise ValueError(f"control kinds {sorted(new['CONTROL_KINDS'] & _CORE_KINDS)} belong to the core")
    if new["BINDING_ACTIONS"] & _CORE_ACTIONS:
        raise ValueError(f"rule actions {sorted(new['BINDING_ACTIONS'] & _CORE_ACTIONS)} belong to the core")
    for k in DEVICE_FIELDS[len(snap["lists"]["DEVICE_FIELDS"]):]:
        if k in _CORE_DEVICE_KEYS or not isinstance(k, str):
            raise ValueError(f"device field {k!r} belongs to the core")
    for pre, _fn in MQTT_HANDLERS[len(snap["lists"]["MQTT_HANDLERS"]):]:
        pre = tuple(pre)
        if not pre or not all(isinstance(x, str) and x and x not in ("+", "#") for x in pre) \
                or _core_topic(list(pre), name, snap["devices"]):
            raise ValueError(f"MQTT handler prefix {pre!r}: a topic of the plug-in's own, not the core's bus")
    for t in MQTT_SUBSCRIPTIONS[len(snap["lists"]["MQTT_SUBSCRIPTIONS"]):]:
        if not isinstance(t, str) or not t or "#" in t[:-1] \
                or any(("+" in lv or "#" in lv) and lv not in ("+", "#") for lv in t.split("/")) \
                or _core_topic(t.split("/"), name, snap["devices"]):
            raise ValueError(f"MQTT subscription {t!r}: a valid filter under a topic of the plug-in's own")
    for r in app.router.routes:
        rp = getattr(r, "path", "")
        if r in snap["routes"] or not rp.startswith("/plugins/"):
            continue
        if not rp.startswith(f"/plugins/{name}/"):
            raise ValueError(f"route {rp!r}: under /plugins/ only /plugins/{name}/...")
        if _OPEN_ASSET.match(rp):
            raise ValueError(f"route {rp!r} looks like a public plug-in file: it would be served without the token")
    own = {id(r) for r in app.router.routes if not any(r is x for x in snap["routes"])}
    if set(OPEN_ROUTES) - set(snap["open_ep"]) - own or set(snap["open_ep"]) - set(OPEN_ROUTES):
        raise ValueError("open_route: only a route this plug-in adds itself can be opened")
    if set(USE_ROUTES) - set(snap["use_ep"]) - own or set(snap["use_ep"]) - set(USE_ROUTES):
        raise ValueError("use_route: only a route this plug-in adds itself can be marked")
    for p in OPEN_PATHS - snap["open"]:
        if not isinstance(p, str) or not p.startswith("/") or p.startswith(("/api", "/ws", "/plugins/")):
            raise ValueError(f"open path {p!r}: a page of the plug-in, never the API")
    for k in new["RESTRICTED_ALLOW"]:
        if not (isinstance(k, tuple) and len(k) == 2 and all(isinstance(x, str) for x in k)) \
                or RESTRICTED_ALLOW[k] not in ("view", "control"):
            raise ValueError(f"RESTRICTED_ALLOW {k!r}: (method, route path) -> 'view' or 'control'")
