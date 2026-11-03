# The core: the plug-in hook tables and the settings store.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# Plug-in hooks. A plug-in is a Python package in plugins/ (shipped) or in PLUGINS_EXTRA_DIR,
# enabled by PLUGINS in .env, that does `import app as core`
# and registers itself here when it is imported (see docs/plugins.md). The core never imports a
# plug-in by name; with no plug-ins these stay empty and nothing changes.
# ---------------------------------------------------------------------------
TRANSPORTS: dict = {}        # transport -> fn(cfg, code, val, kind): actuate (worker threads, bindings, timers)
ASYNC_CONTROL: dict = {}     # transport -> async fn(cfg, code, val): actuate from POST /control (event loop)
CONTROL_KINDS: dict = {}     # control kind -> fn(cfg, ctl, val) -> dict: stateless channels (e.g. an IR button)
MQTT_SUBSCRIPTIONS: list = []   # topic filters subscribed after the core's own, on every (re)connect
TOKEN_CHECKS: list = []      # fn(token, method, path) -> True: a token a plug-in issued, for this request only
KEY_REFUSALS: list = []      # fn(token, method, path) -> a reason: a known key, not for this route (403, not 401)
WS_FILTERS: list = []        # fn(websocket, payload) -> payload: what this /ws caller may see
HEALTH_CHECKS: list = []     # fn() -> [row]: a plug-in's rows on the health page (see health_row)
ON_REPORT: list = []         # fn(dev_id, values: dict, ts): a device's own report (not a command's echo), the channels it carries; inline, fast
ON_EVENT: list = []          # fn(event: dict): everything that happens in the house (a state change, a command, a rule, a device going offline or back); called inline: keep it fast, it must not raise
ADMIN_CHECKS: list = []      # fn(request) -> True: the caller is an administrator of the house (a family phone so marked); see is_admin_request
GUEST_NOTIFY: list = []      # fn({id, code, ua}): a browser asks to come in (no secret in it); inline, fast: hand slow work to a thread
TASK_RESTART_S, TASK_RESTART_MAX_S, TASK_HEALTHY_S = 5.0, 300.0, 600.0
_TASK_DEATHS: dict = {}      # a plug-in task's name -> (when, why) of its last death: the health page shows it
ON_MQTT_CONNECT: list = []   # fn(cli) after every (re)connect and subscribe: republish retained state of your own
MQTT_HANDLERS: list = []     # (topic prefix tuple, fn(parts, payload, retain)) tried before home/cmd
NODE_SUBTOPICS: dict = {}    # name -> fn(parts, payload, retain): <node>/<name>/... of ANY node (parts[0] is the node's prefix)
DEVICE_REMOVED: list = []    # fn(device): a device left the house (any transport; not a relink): a plug-in forgets what it kept
WIRING_CHANGED: list = []    # fn(entries): the wiring list was stored, or read at start (README "Wiring"): re-read what it says
WIRING_MIGRATORS: list = []  # {name, files, entries: fn() -> [entry]}: a plug-in's old copy, taken over once, then left as it was
DISCOVERY_IGNORE: list = []  # fn(names: set) -> True: a node that describes itself is not onboarded through HA discovery
STARTUP_TASKS: list = []     # callables (sync or async) run once the broker is up
NFC_WRITE: list = []         # fn(sid): the nfc plug-in waits for a card to write: send {"cmd": "nfc_write", "sid"} to admin phones
_DAYS = "Sun Mon Tue Wed Thu Fri Sat".split()
_MONTHS = "January February March April May June July August September October November December".split()
SETTINGS_SECTIONS: dict = {}  # settings.json key -> fn() returning what to persist for the plug-in
BINDING_ACTIONS: dict = {}   # action -> {"validate": fn(body)->entry, "run": fn(b)->dict, "describe": fn(b)->then}
ROOMS_OWNER: dict = {}       # {"create": fn(name)->room, "rename": fn(rid, name)->room, "delete": fn(rid)}: the plug-in the rooms are (the map)
ON_ROOM_MOVE: list = []      # fn(dev_id, old_room_names, new_room_name) once a device came into a room (an automatic name follows it)
ROOM_REFS: list = []         # fn(rid) -> [{"kind", "name"}]: what of a plug-in names a room (a panel hangs in it, a satellite stands in it)
DEVICE_FIELDS: list = []     # extra device keys GET /api/devices passes to the dashboard
RESYNC_HOOKS: list = []      # fn(cli, force) -> int: ask a transport's devices to report now (30 s loop; force = /api/resync)
TRANSPORT_BINDINGS: dict = {}  # transport -> fn(src, code, gesture, rule) -> dict | None: a rule's own way to a target
STATE_ASK: dict = {}         # transport -> fn(dev_id) -> bool: ask one device for its state now (asked or not)
ON_RENAME: list = []         # fn(old, new) once migrate_device_key moved every reference: re-key your own caches
NAME_OWNERS: list = []       # fn(dev_id, name) -> True: the plug-in keeps that device's display name itself and stored the new one
MQTT_NAMESPACES: set = set()   # top-level topics a plug-in owns (zigbee2mqtt): nobody else subscribes or publishes there
MQTT_SHARED_READ: list = []    # filters inside a plug-in's namespace a generic device may read (a panel's own sensors)
REGISTRY_PENDING: set = set()  # plug-ins whose device list is not in yet (registry_pending / registry_ready)
OPEN_PATHS: set = set()      # extra paths served without the token (a plug-in's own page)
OPEN_ROUTES: dict = {}  # id -> plug-in route served without the token (open_route); by identity, not function
USE_ROUTES: dict = {}   # id -> route a person of the house uses (use_route): what a key other than the house token may change
# restricted mode (YASHOME_RESTRICTED=1, the older YASHOME_DEMO=1 too; the public demo): a route runs only when
# RESTRICTED_ALLOW names it (the plug-in that turns the mode on fills it), all else is 403
RESTRICTED = ((os.environ.get("YASHOME_RESTRICTED") or os.environ.get("YASHOME_DEMO")) or "").strip() == "1"
RESTRICTED_ALLOW: dict = {}        # (method, route path) -> "view" (anyone signed in) | "control" (the guest in control); method WS for a WebSocket
RESTRICTED_ROLE: list = []         # fn(token, method, path) -> "view" | "control" | None: the demo session behind a token
RESTRICTED_CHECKS: list = []       # fn(method, route path, path params, body bytes) -> why it is refused | None: limits on what a guest stores
RESTRICTED_REFUSED = "Not available here"
RESTRICTED_BODY_MAX = 64 * 1024
RESTRICTED_WS_MAX = 3              # WebSockets one demo session may hold at once
_RESTRICTED_NEED: contextvars.ContextVar = contextvars.ContextVar("restricted_need", default="")    # the RESTRICTED_ALLOW entry of this request
PLUGINS: list = []           # manifests for the dashboard: {id, name, version, ui, i18n}
_PLUGIN_TASKS: list = []     # running plug-in coroutines (referenced so they are not collected)
_PLUGIN_ERRORS: dict = {}    # plug-in -> why its import failed (shown in Settings)


def _settings_bad() -> str:
    """Why saves are off, naming the file (settings.json or a plug-in's settings file)."""
    return _SETTINGS_BAD or "settings.json was unreadable at start"


def _cfg_section(key, default):
    """settings.json section, if it has the type of default; else default."""
    return _cfg[key] if isinstance(_cfg.get(key), type(default)) else default


# The settings.json sections the core keeps in module variables. One _store() each says how the
# section loads, saves, moves a device key (migrate_device_key, in registration order) and which
# device keys it holds (the orphan audit); its key also goes in _CORE_SECTIONS (a test checks).
_STORES: dict = {}
_CORE_ACTIONS = {"on", "off", "toggle", "bright_up", "bright_down", "press"}   # the bindings' cleaner reads it


def _store(key, var, default, clean=None, save=None, migrate=None, refs=None):
    """clean(raw) -> value; save(value) -> JSON; migrate(value, old, new, lost) -> {count: n};
    refs(value) -> [(kind, device key, where)]. Loads the section into the variable now."""
    _STORES[key] = {"var": var, "default": default, "clean": clean, "save": save or (lambda v: v),
                    "migrate": migrate, "refs": refs}
    _load_store(key)


def _load_store(key):
    s = _STORES[key]
    raw = _cfg_section(key, copy.deepcopy(s["default"]))
    globals()[s["var"]] = s["clean"](raw) if s["clean"] else raw


def _norm_hm(t) -> "str | None":
    """'7:00', '07:00', '07:00:00' -> '07:00'; anything else -> None. The scheduler matches
    the string against strftime('%H:%M'), so only the canonical form ever fires."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*", str(t or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


_dump_lock = threading.Lock()              # one writer at a time: the last snapshot lands last
_mqtt_devices_lock = threading.RLock()    # read-modify-write of mqtt_devices.json


def _atomic_json_dump(path: Path, obj, **kw):
    """json.dump via a unique sibling temp file + os.replace: a reader never sees a
    torn file and two writers never share a temp name. kw goes to json.dump."""
    with _dump_lock:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, **kw)
                f.flush()
                os.fsync(f.fileno())      # data before the rename, or a power cut leaves 0 bytes
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def _save_settings():
    """Atomically persist all config from the authoritative in-memory vars."""
    with _settings_lock:
        if "settings" in _load_failed:
            log.error(f"[settings] NOT saving: {_settings_bad()}")
            raise HTTPException(503, f"{_settings_bad()}: fix or remove it and restart; nothing is saved until then")
        data = {"_version": 1, **{k: s["save"](globals()[s["var"]]) for k, s in _STORES.items()}}
        for key, get in SETTINGS_SECTIONS.items():   # plug-in sections
            if key in data:
                continue
            try:
                val = get()
                json.dumps(val, ensure_ascii=False)       # must be storable, or it would break every save
                data[key] = val
            except Exception:
                log.exception(f"[settings] section {key!r} of a plug-in is not storable; its last saved value stays")
                if key in _saved_sections:
                    data[key] = _saved_sections[key]
        # a section nobody owns right now (plug-in removed or failing) is carried over, never dropped
        for key, val in _cfg.items():
            data.setdefault(key, val)
        own: dict = {}
        for key in [k for k in data if k in _SECTION_FILE]:
            own.setdefault(_SECTION_FILE[key], {})[key] = data[key]
        for name, sections in sorted(own.items()):      # first: a cut-off save leaves the plug-in file newer
            _atomic_json_dump(plugin_data(name) / PLUGIN_SETTINGS, sections, indent=2, ensure_ascii=False)
        for name, keys in sorted(_SECTION_MOVED.items()):   # a section another plug-in took over leaves its old file
            old = PLUGIN_DATA / name / PLUGIN_SETTINGS
            rest = {k: v for k, v in (json.loads(old.read_text(encoding="utf-8")) if old.is_file() else {}).items()
                    if k not in keys}
            if rest:
                _atomic_json_dump(old, rest, indent=2, ensure_ascii=False)
            elif old.is_file():
                old.unlink()
        _SECTION_MOVED.clear()
        core = {k: v for k, v in data.items() if k not in _SECTION_FILE}
        _atomic_json_dump(SETTINGS_PATH, core, indent=2, ensure_ascii=False)
        _saved_sections.clear()
        _saved_sections.update({k: copy.deepcopy(v) for k, v in data.items() if k in SETTINGS_SECTIONS or k not in _cfg})


_saved_sections: dict = {}   # what the last save wrote for plug-in sections (a failing one falls back to it)


WS_TICK = 2.0
OFFLINE_AFTER_N_FAILS = 4
CONFIRM_WAIT_S = 5.0               # a command to a confirm channel counts this long before the device's report
START_GRACE_S = 35.0               # after a start, a device that neither reports nor has availability is live
DEVICE_SILENT_S = 600.0             # a periodic device this silent is offline (its learned offline_after_s wins)
PERIODIC_GAP_S, PERIODIC_SPAN_S, PERIODIC_MIN = 120.0, 600.0, 10   # "periodic": see _periodic
PERIODIC_KEEP_S = 86400.0          # a learned flag not confirmed for this long is dropped at a start
STATUS_CACHE_SAVE_DEBOUNCE = 10.0  # batch status-cache writes to disk

EVENTS_MAX = 20
