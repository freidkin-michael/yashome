# The core: settings sections cleaned and migrated at start; the house language; rules switched off.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# Connections & cache
_status_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()   # writers vs the periodic json snapshot
_status_ts: dict[str, float] = {}
_fail_count: dict[str, int] = {}
_last_control_ts: dict[str, float] = {}
# event log + dedup per device
_events: dict[str, deque] = defaultdict(lambda: deque(maxlen=EVENTS_MAX))
_events_lock = threading.Lock()
# One bounded pool for binding execution: a chattering sensor or a stuck button
# used to spawn a thread per event with no ceiling.
_bg = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bind")

# bindings: src_id -> code -> gesture -> {target, code, action}
_bindings: dict = {}
_bindings_lock = _settings_lock           # one re-entrant config lock


def _clean_bindings(raw):
    """{src: {code: {gesture: binding}}}; anything of another shape is dropped with a warning."""
    clean = {}
    for src, codes in raw.items():
        if not isinstance(codes, dict):
            log.warning(f"[settings] bindings of {src!r} skipped: not an object")
            continue
        for code, gestures in codes.items():
            if not isinstance(gestures, dict):
                log.warning(f"[settings] bindings {src}/{code} skipped: not an object")
                continue
            ok = {g: b for g, b in gestures.items() if isinstance(b, dict) and isinstance(b.get("action"), str)
                  and (isinstance(b.get("target"), str) or b["action"] not in _CORE_ACTIONS)}
            if ok:
                clean.setdefault(src, {})[code] = ok
    for codes in clean.values():                # time windows: "9:00" -> "09:00"
        for gestures in codes.values():
            for b in gestures.values():
                tw = b.get("time_window")
                if isinstance(tw, dict):
                    for k in ("from", "to"):
                        if _norm_hm(tw.get(k)):
                            tw[k] = _norm_hm(tw[k])
    raw.clear(); raw.update(clean)        # keep the same object: it is the settings section
    return raw


def _migrate_bindings(v, old, new, lost):
    """As a SOURCE (top-level key) and as a TARGET / condition device."""
    n = {"bindings": 0, "conditions": 0}
    if old in v:
        dst = v.setdefault(new, {})
        for code, gestures in v.pop(old).items():
            for g, entry in gestures.items():
                if g in dst.get(code, {}):
                    lost.add(f"b|{new}|{code}|{g}")
                dst.setdefault(code, {}).setdefault(g, entry)
        n["bindings"] += 1
    for codes in v.values():
        for gestures in codes.values():
            for entry in gestures.values():
                if entry.get("target") == old:
                    entry["target"] = new
                    n["bindings"] += 1
                for c in entry.get("conditions") or []:
                    if c.get("device") == old:
                        c["device"] = new
                        n["conditions"] += 1
    return n


def _binding_refs(v):
    out = []
    for src, codes in v.items():
        out.append(("binding-source", src, src))
        for code, gestures in codes.items():
            for g, entry in gestures.items():
                if entry.get("action") in _CORE_ACTIONS:     # a plug-in action targets an id of its own
                    out.append(("binding-target", entry.get("target"), f"{src}/{code}/{g}"))
                out += [("condition", c.get("device"), f"{src}/{code}/{g}") for c in entry.get("conditions") or []]
    return out


_store("bindings", "_bindings", {}, clean=_clean_bindings, migrate=_migrate_bindings, refs=_binding_refs)


# Favorites: ordered list of channel-level pins {device_id, code, alias}.
# Insertion order is display order. Stored as list (preserves order).
_favorites: list = []
_favorites_lock = _settings_lock          # one re-entrant config lock


def _clean_favorites(raw):
    raw[:] = [f for f in raw if isinstance(f, dict) and isinstance(f.get("device"), str) and isinstance(f.get("code"), str)]
    return raw


def _migrate_favorites(v, old, new, lost):
    n, have = 0, {f.get("code") for f in v if f.get("device") == new}
    for f in list(v):
        if f.get("device") == old:
            if f.get("code") in have:
                v.remove(f)
            else:
                f["device"] = new
            n += 1
    return {"favorites": n}


_store("favorites", "_favorites", [], clean=_clean_favorites, migrate=_migrate_favorites,
       refs=lambda v: [("favorite", f.get("device"), f.get("code", "")) for f in v])


# Rooms (living zones): ordered list of {id, name, devices:[device ids]}.
# A device may live in any number of rooms. Insertion order is display order.
_rooms: list = []
_rooms_lock = _settings_lock              # one re-entrant config lock


def _clean_rooms(raw):
    raw[:] = [r for r in raw if isinstance(r, dict) and isinstance(r.get("id"), str)]
    for r in raw:
        r["name"] = r.get("name") if isinstance(r.get("name"), str) else r["id"]
        r["devices"] = [d for d in (r.get("devices") or []) if isinstance(d, str)] \
            if isinstance(r.get("devices"), list) else []
    return raw


def _migrate_rooms(v, old, new, lost):
    n = 0
    for r in v:
        devs = r.get("devices") or []
        if old in devs:
            r["devices"] = list(dict.fromkeys(new if x == old else x for x in devs))
            n += 1
    return {"rooms": n}


_store("rooms", "_rooms", [], clean=_clean_rooms, migrate=_migrate_rooms,
       refs=lambda v: [("room", d, r.get("name") or r.get("id", "")) for r in v for d in r.get("devices") or []])


# Per-tab manual device order on the dashboard: {tab_id: [device ids]}.
# Ids not in the list render after the listed ones, in natural order.
_tab_order: dict = {}
_tab_order_lock = _settings_lock          # one re-entrant config lock


def _migrate_tab_order(v, old, new, lost):
    n = 0
    for tab, order in v.items():
        if old in order:
            v[tab] = list(dict.fromkeys(new if x == old else x for x in order))
            n += 1
    return {"tab_order": n}


_store("tab_order", "_tab_order", {}, migrate=_migrate_tab_order,
       refs=lambda v: [("tab-order", d, tab) for tab, order in v.items() for d in order])


# Per-tab/room tile sizes: {scope: {device_id: {"w": cols, "h": min px, "col": pinned, "hn": min px on a narrow screen}}}.
# scope = tab id ("switch") or "room:<rid>"; missing entry = auto size.
_tile_sizes: dict = {}
_tile_sizes_lock = _settings_lock         # one re-entrant config lock


def _migrate_tile_sizes(v, old, new, lost):
    n = 0
    for sizes in v.values():
        if old in sizes:
            sizes.setdefault(new, sizes.pop(old))
            n += 1
    return {"tile_sizes": n}


_store("tile_sizes", "_tile_sizes", {}, migrate=_migrate_tile_sizes,
       refs=lambda v: [("tile-size", d, scope) for scope, sizes in v.items() for d in sizes])


# A room's thermometer and hygrometer chosen by hand: {rid: {"temperature": "dev/code", "humidity": "dev/code"}}; none = auto.
_room_sensors: dict = {}


def _migrate_room_sensors(v, old, new, lost):
    n = 0
    for picks in v.values():
        for kind, src in list(picks.items()):
            dev, _, code = src.rpartition("/")
            if dev == old:
                n += 1
                if lost:
                    picks.pop(kind)
                else:
                    picks[kind] = f"{new}/{code}"
    return {"room_sensors": n}


_store("room_sensors", "_room_sensors", {}, migrate=_migrate_room_sensors,
       refs=lambda v: [("room-sensor", src.rpartition("/")[0], rid) for rid, picks in v.items() for src in picks.values()])


# House-wide settings (Settings -> General): {"lang": the house language}. Voice answers, clocks and the
# dashboard of a screen where nobody picked a language follow it (English until one is picked).
_house: dict = {}


def house_langs() -> list:
    """English (the words the code is written in) and every language with a dictionary in i18n.json: a new one needs no code."""
    if RESTRICTED:
        return ["en"]
    try:
        more = json.loads((Path(__file__).parent / "i18n.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        more = {}
    return ["en"] + sorted(k for k in more if re.fullmatch(r"[a-z]{2}", k) and k != "en")


def _clean_house(v) -> dict:
    v = dict(v) if isinstance(v, dict) else {}
    if v.get("lang") not in house_langs():
        v["lang"] = "en"
    v.pop("temp_precision", None)                # an older house chose tenths: every screen shows whole numbers now
    return v


def house_lang() -> str:
    return _house.get("lang") or "en"


_store("house", "_house", {}, clean=_clean_house)


# -- Unified automation rules: a facade over _bindings + _autos ---------------
# We don't keep a parallel store. A "rule" is a uniform view of an existing
# binding gesture or an automations (schedule/inching) entry, addressed by a
# stable reversible id (see _rule_* helpers below). The only new persisted state
# is a set of disabled rule-ids -- uniform enable/disable across all rule types
# without touching the proven storage semantics.
_rules_disabled: set = set()                    # stored after the automations (below)


# Every import-time device (mqtt_devices.json) is in DEVICES now.
_apply_name_overrides()


def _prune_rules_disabled():
    """A disabled id outlives its rule when the row is replaced or removed; re-adding the same
    rule later must not bring it back switched off. Caller holds _settings_lock."""
    live = {r["id"] for r in _project_rules()}
    _rules_disabled.intersection_update(live)


def _rule_enabled(rid: str) -> bool:
    return rid not in _rules_disabled
