"""
Yashome backend: devices, transports, rules, REST + WebSocket for the dashboard.

Everything is local and goes through MQTT:
- Zigbee devices    -> zigbee2mqtt (discovered from the broker's retained topics)
- generic MQTT      -> mqtt_devices.json (ESPHome, Tasmota, your own firmware)

Rules (bindings, schedules, auto-off) run here, on the server, not on the devices.

Run:
    uvicorn app:app --host 0.0.0.0 --port 8765
"""
import asyncio
import contextlib
import copy
import json
import logging
import operator
import os
import re
import secrets
import tempfile
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
import hmac
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.routing import Match
from pydantic import BaseModel, ConfigDict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")


class _RedactToken(logging.Filter):
    """uvicorn's access log prints the full URL: /ws?token=... and /?token=... would put the
    house key into `docker compose logs` (and into any issue a log gets pasted into)."""
    _re = re.compile(r"(token=)[^&\s\"']+")

    def filter(self, record):
        if record.args:
            record.args = tuple(self._re.sub(r"\1***", a) if isinstance(a, str) else a
                                for a in record.args)
        if isinstance(record.msg, str):
            record.msg = self._re.sub(r"\1***", record.msg)
        return True


for _name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_RedactToken())
log = logging.getLogger("home")

# HOME_STACK_ROOT lets tests point at a fixture tree instead of the live one.
ROOT = Path(os.environ.get("HOME_STACK_ROOT") or Path(__file__).parent)
MQTT_DEVICES_PATH = ROOT / "mqtt_devices.json"  # generic MQTT devices (ESPHome, Tasmota, own firmware)
STATUS_CACHE_PATH = ROOT / "status_cache.json"
SETTINGS_PATH = ROOT / "settings.json"


# -- Unified config: one settings.json holds names/bindings/favorites/automations.
# In-memory vars are authoritative; _save_settings() atomically dumps them all
# under one re-entrant lock (no read-modify-write of the file -> no lost updates).
# Per-section locks alias this same lock. z2m devices come from the coordinator.
_settings_lock = threading.RLock()
_cfg: dict = {}
# Files that failed to parse at boot. While a name is in here the matching
# save is refused: writing the in-memory defaults back would turn one corrupt
# read into the loss of every binding and favourite.
_load_failed: set = set()
if SETTINGS_PATH.exists():
    try:
        _cfg = json.load(open(SETTINGS_PATH))
        if not isinstance(_cfg, dict):
            raise ValueError(f"top level is {type(_cfg).__name__}, not an object")
    except Exception as _e:
        _cfg = {}
        _load_failed.add("settings")
        log.error(f"[settings] {SETTINGS_PATH} unreadable ({_e}); saves DISABLED until fixed")


# ---------------------------------------------------------------------------
# Plug-in hooks. A plug-in is a Python package in the plug-in directory (PLUGINS_DIR in .env,
# /app/plugins in the container) that does `import app as core`
# and registers itself here when it is imported (see docs/plugins.md). The core never imports a
# plug-in by name; with no plug-ins these stay empty and nothing changes.
# ---------------------------------------------------------------------------
TRANSPORTS: dict = {}        # transport -> fn(cfg, code, val, kind): actuate (worker threads, bindings, timers)
ASYNC_CONTROL: dict = {}     # transport -> async fn(cfg, code, val): actuate from POST /control (event loop)
CONTROL_KINDS: dict = {}     # control kind -> fn(cfg, ctl, val) -> dict: stateless channels (e.g. an IR button)
MQTT_SUBSCRIPTIONS: list = []   # topic filters subscribed after the core's own, on every (re)connect
MQTT_HANDLERS: list = []     # (topic prefix tuple, fn(parts, payload, retain)) tried before home/cmd
STARTUP_TASKS: list = []     # callables (sync or async) run once the broker is up
SETTINGS_SECTIONS: dict = {}  # settings.json key -> fn() returning what to persist for the plug-in
BINDING_ACTIONS: dict = {}   # action -> {"validate": fn(body)->entry, "run": fn(b)->dict, "describe": fn(b)->then}
DEVICE_FIELDS: list = []     # extra device keys GET /api/devices passes to the dashboard
OPEN_PATHS: set = set()      # extra paths served without the token (a plug-in's own page)
PLUGINS: list = []           # manifests for the dashboard: {id, name, version, ui, i18n}
_PLUGIN_TASKS: list = []     # running plug-in coroutines (referenced so they are not collected)
_PLUGIN_ERRORS: dict = {}    # plug-in -> why its import failed (shown in Settings)


def _cfg_section(key, default):
    """settings.json section, if it has the type of default; else default."""
    return _cfg[key] if isinstance(_cfg.get(key), type(default)) else default


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
            log.error("[settings] NOT saving: settings.json failed to load at boot")
            raise HTTPException(503, "settings.json was unreadable at start: fix or remove it and restart; "
                                     "nothing is saved until then")
        data = {
            "_version": 1,
            "names": _name_overrides,
            "channel_names": _channel_names,
            "channel_dimrange": _channel_dimrange,
            "bindings": _bindings,
            "favorites": _favorites,
            "rooms": _rooms,
            "tab_order": _tab_order,
            "tile_sizes": _tile_sizes,
            "automations": _autos,
            "rules_disabled": sorted(_rules_disabled),
        }
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
        _atomic_json_dump(SETTINGS_PATH, data, indent=2, ensure_ascii=False)
        _saved_sections.clear()
        _saved_sections.update({k: copy.deepcopy(v) for k, v in data.items() if k in SETTINGS_SECTIONS or k not in _cfg})


_saved_sections: dict = {}   # what the last save wrote for plug-in sections (a failing one falls back to it)


WS_TICK = 2.0
OFFLINE_AFTER_N_FAILS = 4
STATUS_CACHE_SAVE_DEBOUNCE = 10.0  # batch status-cache writes to disk

EVENTS_MAX = 20

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Devices come from two places: zigbee2mqtt (live from the broker) and
# mqtt_devices.json (generic MQTT: ESPHome, Tasmota, your own firmware).
DEVICES: dict = {}

# Each mqtt_devices.json entry is a normal device dict with transport="mqtt"; see _handle_mqtt_state.
if MQTT_DEVICES_PATH.exists():
    try:
        _mds = json.load(open(MQTT_DEVICES_PATH)) or []
        if not isinstance(_mds, list):
            raise ValueError("expected a list of devices")
    except Exception as _e:
        _mds = []
        log.warning(f"[mqtt] failed to load {MQTT_DEVICES_PATH}: {_e}")
    for _i, _md in enumerate(_mds):
        # one bad entry is skipped, it does not take the others (or the whole backend) down
        if not isinstance(_md, dict) or not isinstance(_md.get("id"), str) or not _md["id"]:
            log.warning(f"[mqtt] mqtt_devices.json entry #{_i} skipped: an object with a string \"id\" is needed")
            continue
        if not isinstance(_md.get("name"), str) or not _md["name"].strip():
            _md["name"] = _md["id"]
        if not isinstance(_md.get("controls"), list):
            _md["controls"] = []
        _md["transport"] = "mqtt"
        DEVICES[_md["id"]] = _md

# Channel codes come from outside (z2m exposes, ESPHome discovery, a hand-written JSON) and end
# up in MQTT topics, rule ids and the dashboard's markup, so only plain names are accepted.
_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_CAT_RE = re.compile(r"^[a-z_]{1,32}$")
_MAX_DELAY = 7 * 86400     # longest auto-off / rule duration: a threading.Timer overflows far beyond


def _own_topic(t) -> bool:
    """home/state/# and home/cmd/# are the backend's own bus: a device may not live there."""
    return isinstance(t, str) and (t.startswith("home/state/") or t.startswith("home/cmd/"))


def _safe_controls(dev_id: str, controls) -> list:
    out = []
    for c in controls or []:
        if isinstance(c, dict) and (_own_topic(c.get("state_topic")) or _own_topic(c.get("command_topic"))):
            log.warning(f"[devices] {dev_id}/{c.get('code')}: topics under home/state/ or home/cmd/ are the "
                        "backend's own mirror; channel skipped")
            continue
        if isinstance(c, dict) and _CODE_RE.match(str(c.get("code", ""))):
            out.append(c)
        else:
            code = c.get("code") if isinstance(c, dict) else c
            log.warning(f"[devices] {dev_id}: channel code {str(code)[:40]!r} "
                        "skipped (allowed: letters, digits, _ . -)")
    return out


for _bad in [k for k, d in list(DEVICES.items()) if not (isinstance(k, str) and _CODE_RE.match(k))]:
    log.warning(f"[devices] mqtt_devices.json id {str(_bad)[:40]!r} skipped (allowed: letters, digits, _ . -)")
    DEVICES.pop(_bad)
for _bad in [k for k, d in list(DEVICES.items())
             if any(_own_topic(d.get(t)) for t in ("state_topic", "command_topic", "availability_topic"))]:
    log.warning(f"[devices] {_bad}: a device-level topic under home/state/ or home/cmd/ (the backend's own bus); skipped")
    DEVICES.pop(_bad)
for d in list(DEVICES.values()):
    if not _CAT_RE.match(str(d.get("category") or "other")):
        d["category"] = "other"
    d["controls"] = _safe_controls(d["id"], d.get("controls"))
    for _c in d["controls"]:
        _c.setdefault("_label0", _c.get("label", ""))
    d["_ctl"] = {c["code"]: c for c in d["controls"]}   # channel code -> control

# Device-name overrides (settings.json "names"), applied on top at startup so
# they survive z2m re-registration.
_name_overrides = _cfg_section("names", {})


def _apply_name_overrides():
    """Re-apply the saved display names to whatever is in DEVICES right now.
    z2m devices get theirs in _register_z2m_device, which runs later."""
    for _did, _nm in _name_overrides.items():
        if _did in DEVICES and isinstance(_nm, str) and _nm:
            DEVICES[_did]["name"] = _nm
            BY_ID_NAME[_did] = _nm


_channel_names = _cfg_section("channel_names", {})  # {device_id: {code: custom_label}}
# Per-dimmer-channel usable brightness window, in UI percent (0..100): the LED
# wired to a gang may only light from ~15% and hit full by ~60%, so the pill maps
# its 0..100 travel into [floor, ceil]. Default (no entry) = full 0..100 range.
_channel_dimrange = _cfg_section("channel_dimrange", {})  # {device_id: {bright_code: [floor, ceil]}}

# dev_id -> human name (for log/journal output)
BY_ID_NAME: dict[str, str] = {did: d["name"] for did, d in DEVICES.items()}

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


def _load_bindings():
    """{src: {code: {gesture: binding}}}; anything of another shape is dropped with a warning."""
    global _bindings
    raw = _cfg_section("bindings", {})
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
    raw.clear(); raw.update(clean)        # keep the same object: it is the settings section
    _bindings = raw


_load_bindings()


# Favorites: ordered list of channel-level pins {device_id, code, alias}.
# Insertion order is display order. Stored as list (preserves order).
_favorites: list = []
_favorites_lock = _settings_lock          # one re-entrant config lock


def _load_favorites():
    global _favorites
    _favorites = _cfg_section("favorites", [])
    _favorites[:] = [f for f in _favorites if isinstance(f, dict)
                     and isinstance(f.get("device"), str) and isinstance(f.get("code"), str)]


_load_favorites()


# Rooms (living zones): ordered list of {id, name, devices:[device ids]}.
# A device may live in any number of rooms. Insertion order is display order.
_rooms: list = []
_rooms_lock = _settings_lock              # one re-entrant config lock


def _load_rooms():
    global _rooms
    _rooms = _cfg_section("rooms", [])
    _rooms[:] = [r for r in _rooms if isinstance(r, dict) and isinstance(r.get("id"), str)]
    for r in _rooms:
        r["name"] = r.get("name") if isinstance(r.get("name"), str) else r["id"]
        r["devices"] = [d for d in (r.get("devices") or []) if isinstance(d, str)] \
            if isinstance(r.get("devices"), list) else []


_load_rooms()


# Per-tab manual device order on the dashboard: {tab_id: [device ids]}.
# Ids not in the list render after the listed ones, in natural order.
_tab_order: dict = {}
_tab_order_lock = _settings_lock          # one re-entrant config lock


def _load_tab_order():
    global _tab_order
    _tab_order = _cfg_section("tab_order", {})


_load_tab_order()


# Per-tab/room tile sizes: {scope: {device_id: {"w": cols, "h": min px}}}.
# scope = tab id ("switch") or "room:<rid>"; missing entry = auto size.
_tile_sizes: dict = {}
_tile_sizes_lock = _settings_lock         # one re-entrant config lock


def _load_tile_sizes():
    global _tile_sizes
    _tile_sizes = _cfg_section("tile_sizes", {})


_load_tile_sizes()


# -- Unified automation rules: a facade over _bindings + _autos ---------------
# We don't keep a parallel store. A "rule" is a uniform view of an existing
# binding gesture or an automations (schedule/inching) entry, addressed by a
# stable reversible id (see _rule_* helpers below). The only new persisted state
# is a set of disabled rule-ids -- uniform enable/disable across all rule types
# without touching the proven storage semantics.
_rules_disabled: set = {r for r in (_cfg.get("rules_disabled") or []) if isinstance(r, str)} \
    if isinstance(_cfg.get("rules_disabled"), list) else set()


# Every import-time device (mqtt_devices.json) is in DEVICES now.
_apply_name_overrides()


def _prune_rules_disabled():
    """A disabled id outlives its rule when the row is replaced or removed; re-adding the same
    rule later must not bring it back switched off. Caller holds _settings_lock."""
    live = {r["id"] for r in _project_rules()}
    _rules_disabled.intersection_update(live)


def _rule_enabled(rid: str) -> bool:
    return rid not in _rules_disabled


def _pct_to_raw(ctl: dict, pct) -> int:
    """UI percent 0..100 -> raw units of a bright control."""
    lo, hi = ctl["min"], ctl["max"]
    return int(lo + (hi - lo) * max(0, min(100, int(pct))) / 100)


def _raw_to_pct(ctl: dict, raw):
    """Raw units -> UI percent 0..100; None when raw is not a number."""
    lo, hi = ctl.get("min", 0), ctl.get("max", 0)
    span = (hi - lo) or 1
    try:
        return max(0, min(100, round((raw - lo) / span * 100)))
    except TypeError:
        return None


# The [floor, ceil] window of a dimmer channel (settings.json -> channel_dimrange, UI
# percent). The dashboard pill applies it client-side; MQTT clients cannot, so the
# bridge does it for them: a client's 0..100 is the usable travel, exactly like the pill. Without a window both helpers are the identity.
def _dim_window(dev_id: str, code: str):
    dr = (_channel_dimrange.get(dev_id) or {}).get(code)
    if not dr:
        return None
    try:
        lo, hi = int(dr[0]), int(dr[1])
    except (TypeError, ValueError, IndexError):
        return None
    lo = max(0, min(99, lo))
    hi = max(lo + 1, min(100, hi))
    return None if (lo == 0 and hi == 100) else (lo, hi)


def _win_to_ui(win, pos) -> int:
    """Window-relative 0..100 -> UI percent of the full range (what control() takes)."""
    if not win:
        return max(1, min(100, int(round(pos))))
    lo, hi = win
    return max(1, min(100, int(round(lo + float(pos) / 100 * (hi - lo)))))


def _ui_to_win(win, ui):
    """UI percent -> window-relative 0..100 (what MQTT clients display and step on)."""
    if not win or ui is None:
        return ui
    lo, hi = win
    return max(0, min(100, int(round((ui - lo) / (hi - lo) * 100))))


def _dim_step(pos, direction):
    """One up/down step on the window scale, a tenth from the NEAREST tenth.
    Returns the new window-relative level, or None when a step down is asked at
    the floor: the floor is the dimmest allowed level, so going below it means off."""
    k = int(round((pos or 0) / 10))
    if direction == "down":
        return None if k <= 0 else (k - 1) * 10
    return min(10, k + 1) * 10


def _scale(control: dict, raw_value):
    if raw_value is None:
        return None
    if isinstance(raw_value, bool):       # binary sensors (occupancy, contact...) pass through
        return raw_value
    s = control.get("scale", 1)
    try:
        return round(raw_value * s, 2)
    except Exception:
        return raw_value


# Status cache persists across restarts (bind-mounted dir): after a container
# restart the UI/widget immediately sees last-known states (marked stale)
# instead of everything-offline until the first poll sweep completes.
_status_cache_dirty = threading.Event()


def _load_status_cache():
    if not STATUS_CACHE_PATH.exists():
        return
    try:
        data = json.load(open(STATUS_CACHE_PATH))
    except Exception as e:
        log.warning(f"[status-cache] load failed: {e}")
        return
    now = time.time()
    n = 0
    for did, st in data.items():
        # unknown ids are kept too: a z2m device registers only once the broker answers
        if not isinstance(did, str) or not isinstance(st, dict):
            continue
        if st.get("online"):
            st["stale"] = True
        with _cache_lock:
            _status_cache[did] = st
        _status_ts[did] = now
        n += 1
    log.info(f"[status-cache] restored {n} device states")


def _status_cache_saver_loop():
    while True:
        _status_cache_dirty.wait()
        time.sleep(STATUS_CACHE_SAVE_DEBOUNCE)
        _status_cache_dirty.clear()
        try:
            with _cache_lock:
                snap = copy.deepcopy(_status_cache)
            if _z2m_loaded:                     # registry known: drop removed devices' states
                snap = {k: v for k, v in snap.items() if k in DEVICES}
            _atomic_json_dump(STATUS_CACHE_PATH, snap, ensure_ascii=False)
        except Exception as e:
            log.warning(f"[status-cache] save failed: {e}")


def _status_copy(dev_id: str, default: dict) -> dict:
    """A private copy of a device's cached state to modify and hand back to _store_status.
    Entries in _status_cache are never changed in place: /ws and the snapshot saver read them
    without the lock, and a dict changing size under json.dumps raises."""
    with _cache_lock:
        st = _status_cache.get(dev_id)
        if not st:
            return copy.deepcopy(default)
        out = dict(st)
        for k in ("values", "raw", "last_event"):
            if isinstance(out.get(k), dict):
                out[k] = dict(out[k])
        return out


def _store_status(dev_id: str, st: dict):
    with _cache_lock:
        _store_status_locked(dev_id, st)
    _status_ts[dev_id] = time.time()
    _status_cache_dirty.set()
    try:
        _mqtt_publish_state(dev_id)
    except Exception as e:
        log.warning(f"[state] mirror publish {dev_id} failed: {e}")


def _store_status_exact(dev_id: str, st: dict):
    """Store as given, without the grace period of _store_status: for an explicit word from the
    device side (an availability topic), where "offline" means offline now."""
    with _cache_lock:
        _status_cache[dev_id] = st
        _fail_count[dev_id] = 0
    _status_ts[dev_id] = time.time()
    _status_cache_dirty.set()
    try:
        _mqtt_publish_state(dev_id)
    except Exception as e:
        log.warning(f"[state] mirror publish {dev_id} failed: {e}")


def _store_status_locked(dev_id: str, st: dict):
    if st.get("online"):
        _fail_count[dev_id] = 0
        _status_cache[dev_id] = st
    else:
        fc = _fail_count.get(dev_id, 0) + 1
        _fail_count[dev_id] = fc
        prev = _status_cache.get(dev_id)
        if prev and prev.get("online") and fc < OFFLINE_AFTER_N_FAILS:
            kept = dict(prev)
            kept["stale"] = True
            kept["last_error"] = st.get("error", "")
            _status_cache[dev_id] = kept
        else:
            _status_cache[dev_id] = st


def get_status(dev_id: str) -> dict:
    st = _status_cache.get(dev_id)
    return st if st is not None else {"online": False, "error": "no data yet"}


def _safe_execute_binding(src_id: str, code: str, gesture: str):
    try:
        result = _execute_binding(src_id, code, gesture)
        if result:
            # tag the last event with the action so the UI press log shows it fired
            with _events_lock:
                evs = _events.get(src_id)
                if evs:
                    last = evs[-1]
                    if last.get("code") == code and str(last.get("value")) == gesture:
                        tgt = result.get("target")
                        what = result.get("value", result.get("action", ""))
                        last["fired"] = (f"{BY_ID_NAME.get(tgt, tgt)}.{result.get('code', '')}={what}"
                                         if tgt else str(result.get("action") or what))
    except Exception as e:
        log.warning(f"[binding] {src_id}/{code}/{gesture} -> ERR {e}")


def _fire_sensor_triggers(name, cfg, values):
    """Fire bindings on sensor value transitions.
    Binary sensors -> gesture "true"/"false" on change.
    Numeric sensors -> gesture "below"/"above" on entering the threshold zone,
    re-armed only after leaving it past the hysteresis band (no edge chatter,
    no fire on the first reading after boot)."""
    for c in cfg.get("controls", []):
        if c.get("kind") != "sensor" or c.get("code") == "battery":
            continue
        code = c["code"]
        if code not in values:
            continue
        val = values[code]
        key = f"{name}/{code}"
        prev = _sensor_last.get(key)
        _sensor_last[key] = val
        if c.get("vtype") == "bool":
            nb = bool(val)
            if prev is None or bool(prev) == nb:
                continue                      # no transition / first reading
            g = "true" if nb else "false"
            _bg.submit(_safe_execute_binding, name, code, g)
            continue
        # numeric thresholds
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        with _bindings_lock:
            entries = {g: dict(b) for g, b in
                       (_bindings.get(name, {}).get(code, {}) or {}).items()
                       if g in ("below", "above")}
        for g, b in entries.items():
            thr = b.get("threshold")
            if thr is None:
                continue
            h = b.get("hyst") or 0
            ak = f"{name}/{code}/{g}"
            in_zone = (num < thr) if g == "below" else (num > thr)
            if ak not in _sensor_arm:
                _sensor_arm[ak] = not in_zone   # seed; fire only on a future entry
                continue
            if _sensor_arm[ak] and in_zone:
                _sensor_arm[ak] = False
                _bg.submit(_safe_execute_binding, name, code, g)
            elif not _sensor_arm[ak]:
                left = (num > thr + h) if g == "below" else (num < thr - h)
                if left:
                    _sensor_arm[ak] = True


_MAIN_LOOP: "asyncio.AbstractEventLoop | None" = None   # set in _start_pollers
_AFTER_BROKER = None   # keeps the startup task referenced (asyncio holds tasks weakly)


def _apply_value(dev_id: str, code: str, val, kind: str = "switch"):
    """Push one value to a device over whatever transport it speaks. Single place
    that knows the transport table, shared by /control, bindings and inching."""
    cfg = DEVICES.get(dev_id)
    if cfg is None:
        raise RuntimeError(f"unknown device {dev_id}")
    ctl = cfg["_ctl"].get(code) or {}
    if ctl.get("kind") in CONTROL_KINDS:      # a plug-in's stateless channel (an event, no value)
        CONTROL_KINDS[ctl["kind"]](cfg, ctl, val)
        return
    tr = cfg.get("transport")
    if tr == "z2m":
        _z2m_publish_set(cfg, code, val, kind)
    elif tr == "mqtt":
        _mqtt_generic_publish_set(cfg, code, val, kind)
    elif tr in TRANSPORTS:
        TRANSPORTS[tr](cfg, code, val, kind)
    else:
        raise RuntimeError(f"unsupported transport {tr!r} for {dev_id}")
    if kind == "switch":
        # delivered (a failed publish raised above and keeps the retry): any actuation of the
        # channel makes a schedule retry still waiting for the broker obsolete
        _cancel_deferred(f"sched|{dev_id}/{code}")


def _set_switch(dev_id: str, code: str, on: bool):
    """Turn a switch/plug channel on or off, transport-agnostic, and reflect it
    in the cache. Used by server-side inching (pulse)."""
    cfg = DEVICES.get(dev_id)
    if not cfg:
        raise RuntimeError(f"unknown device {dev_id}")
    _apply_value(dev_id, code, on, "switch")
    cur = _status_copy(dev_id, {"online": True, "values": {}, "raw": {}})
    cur.setdefault("values", {})[code] = on
    cur["online"] = True
    cur.pop("stale", None)
    _store_status(dev_id, cur)
    _last_control_ts[dev_id] = time.time()


# --- Server-side automations: per-channel inching + daily schedule -----------
# Everything runs on OUR server (no device-side inching DP, no vendor-cloud
# schedules). Inching = "after ANY turn-on, auto-off after N minutes" and owns
# the duration for manual / scheduled / binding turn-ons alike. Schedule = just
# on/off times; a scheduled ON inherits the channel's inching window.
_autos: dict = {}            # "<dev>/<code>" -> {"inching_min": int, "schedule": [..]}
_autos_lock = _settings_lock          # one re-entrant config lock
# --- Persistent deferred-action registry ------------------------------------
# One place owns every "do X after N seconds" deadline: inching auto-off and
# rule-action durations (`for`). Each deadline is persisted to disk with its
# absolute run_at, so an app restart replays it with the CORRECT remaining time
# (or fires immediately if it elapsed during downtime) instead of losing it.
_Z2M_ENABLED = ("Z2M_SERIAL" not in os.environ
                or os.environ["Z2M_SERIAL"].strip().strip('"\'').lower() not in ("", "none"))
_PENDING_PATH = ROOT / "pending_timers.json"
_pending: dict = {}            # token -> {run_at, device, code, value, label}
_pending_handles: dict = {}    # token -> threading.Timer
_pending_lock = threading.Lock()


_pending_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="timer-save")


def _pending_write():
    with _pending_lock:
        snap = {k: dict(v) for k, v in _pending.items()}
    try:
        _atomic_json_dump(_PENDING_PATH, snap)
    except Exception as e:
        log.warning(f"[timer] persist failed: {e}")


def _pending_save():
    # one writer thread in submission order: the latest snapshot lands last, and a toggle on
    # the event loop never waits for an fsync (slow on SD cards)
    try:
        _pending_writer.submit(_pending_write)
    except RuntimeError:           # the executor is gone (interpreter shutting down)
        _pending_write()


_REPLAY = object()   # _fire_deferred caller: the boot replay of an elapsed deadline


def _fire_deferred(token: str, timer=None):
    with _pending_lock:
        if timer is _REPLAY:
            if token in _pending_handles:
                return                 # armed again since start: the new timer owns the token
        elif timer is not None and _pending_handles.get(token) is not timer:
            return                     # re-armed or cancelled meanwhile: this timer is stale
        spec = _pending.pop(token, None)
        _pending_handles.pop(token, None)
    if not spec:
        return
    _pending_save()
    try:
        if spec["device"] in DEVICES and spec["code"] not in DEVICES[spec["device"]]["_ctl"]:
            log.warning(f"[timer] {token}: channel {spec['code']} is gone, dropped")   # B-3
            return
        _set_switch(spec["device"], spec["code"], bool(spec["value"]))
        if token.startswith("sched|") and spec["value"]:
            _arm_inching(spec["device"], spec["code"])     # as the scheduler does for a direct ON
        log.info(f"[timer] {token} -> {spec['device']}/{spec['code']}="
              f"{spec['value']} ({spec.get('label', '')})")
    except Exception as e:
        # Losing this means an inching auto-off never happens (worst case: the
        # water heater stays on), so re-arm instead of just logging. Bounded, or
        # a permanently missing device would retry until the process dies.
        tries = int(spec.get("tries") or 0) + 1
        if spec["device"] not in DEVICES and tries > 20:
            log.warning(f"[timer] fire {token} failed: {e} -- the device is gone, giving up after {tries}")
            return
        # a broker outage can last hours: keep trying (15 s, then up to every 10 min), never drop an OFF
        delay = min(600, 15 * (2 ** min(tries - 1, 6)))
        log.warning(f"[timer] fire {token} failed: {e} -- retry {tries} in {delay}s")
        _schedule_deferred(token, delay, spec["device"], spec["code"],
                           bool(spec["value"]), spec.get("label", ""), tries=tries)


def _cancel_deferred(token: str):
    with _pending_lock:
        h = _pending_handles.pop(token, None)
        existed = _pending.pop(token, None) is not None
    if h:
        h.cancel()
    if existed:
        _pending_save()


def _deferred_active(token: str) -> bool:
    with _pending_lock:
        return token in _pending


def _schedule_deferred(token, secs, device, code, value, label="", *, run_at=None,
                       tries=0):
    """Arm a one-shot: set device/code=value after `secs`s (or exactly at the
    absolute epoch `run_at`). Replaces any existing timer for the same token."""
    when = run_at if run_at is not None else (time.time() + max(0, secs))
    spec = {"run_at": when, "device": device, "code": code,
            "value": bool(value), "label": label}
    if tries:
        spec["tries"] = int(tries)
    # replace under one lock: two arms of the same token cannot leave an orphan timer behind
    with _pending_lock:
        old = _pending_handles.pop(token, None)
        t = threading.Timer(max(0.0, when - time.time()), lambda: _fire_deferred(token, t))
        t.daemon = True
        _pending[token] = spec
        _pending_handles[token] = t
    if old:
        old.cancel()
    _pending_save()
    t.start()


def _load_pending():
    """Read pending_timers.json into _pending before the broker connects: an ON that arms a
    timer early then saves the file WITH the other channels' deadlines, not over them. An
    unreadable file is renamed aside (kept for a look), never silently replaced by {}."""
    if not _PENDING_PATH.exists():
        return
    try:
        data = json.load(open(_PENDING_PATH))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except Exception as e:
        aside = _PENDING_PATH.with_name(f"pending_timers.corrupt-{int(time.time())}.json")
        try:
            os.replace(_PENDING_PATH, aside)
        except OSError:
            pass
        log.error(f"[timer] {_PENDING_PATH.name} unreadable ({e}); moved to {aside.name}, timers start empty")
        return
    with _pending_lock:
        for token, spec in data.items():
            if isinstance(spec, dict) and token not in _pending:
                _pending[token] = spec


def _replay_pending_on_boot():
    """Arm the deadlines loaded by _load_pending. Elapsed ones fire immediately; live ones
    re-arm for their remaining time. Tokens armed since then keep their newer timer."""
    with _pending_lock:
        data = {k: dict(v) for k, v in _pending.items() if k not in _pending_handles}
    now = time.time()
    fired = armed = 0
    for token, spec in list(data.items()):
        if token.startswith("sched|") and now - float(spec.get("run_at") or 0) > 1800:
            _cancel_deferred(token)
            log.info(f"[timer] {token}: schedule retry from {int((now - float(spec.get('run_at') or 0)) / 60)} min ago dropped")
            data.pop(token)
            continue
        try:
            dev, code = spec["device"], spec["code"]
            val = bool(spec.get("value"))
            run_at = float(spec.get("run_at") or 0)
            label = spec.get("label", "")
        except Exception:
            continue
        if run_at <= now:
            _fire_deferred(token, _REPLAY)
            fired += 1
        else:
            _schedule_deferred(token, 0, dev, code, val, label, run_at=run_at)
            armed += 1
    if fired or armed:
        log.info(f"[timer] replayed {armed} pending, fired {fired} elapsed")

try:
    from zoneinfo import ZoneInfo
    _LOCAL_TZ = ZoneInfo(os.environ.get("TZ", "UTC"))
except Exception:
    _LOCAL_TZ = None


def _norm_hm(t) -> "str | None":
    """'7:00', '07:00', '07:00:00' -> '07:00'; anything else -> None. The scheduler matches
    the string against strftime('%H:%M'), so only the canonical form ever fires."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*", str(t or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _valid_hm(t) -> bool:
    return _norm_hm(t) is not None


def _load_autos():
    global _autos, _rules_disabled
    _autos = _cfg_section("automations", {})
    for k in [k for k, a in _autos.items() if not isinstance(a, dict)]:
        log.warning(f"[settings] automation {k!r} skipped: not an object")
        _autos.pop(k)
    for a in _autos.values():
        if not isinstance(a.get("schedule"), list):
            a["schedule"] = []
        good = []
        for e in a["schedule"]:
            days = e.get("days") if isinstance(e, dict) else None
            if (isinstance(e, dict) and _norm_hm(e.get("time")) and e.get("action", "on") in ("on", "off")
                    and (days is None or (isinstance(days, list) and days
                                          and all(isinstance(d, int) and 1 <= d <= 7 for d in days)))):
                good.append(e)
            else:
                log.warning(f"[settings] schedule row {e!r} dropped: needs HH:MM, days 1..7, on|off")
        a["schedule"] = good
    # a disabled "a|dev/code|sched|7:00|on" must keep matching its row once that is "07:00"
    fixed = set()
    for rid in _rules_disabled:
        p = rid.split("|")
        if len(p) == 5 and p[2] == "sched" and _norm_hm(p[3]):
            p[3] = _norm_hm(p[3])
        fixed.add("|".join(p))
    _rules_disabled = fixed
    for codes in (_bindings or {}).values():        # binding time windows: "9:00" -> "09:00"
        for gestures in (codes or {}).values():
            for b in (gestures or {}).values():
                tw = b.get("time_window") if isinstance(b, dict) else None
                if isinstance(tw, dict):
                    for k in ("from", "to"):
                        if _norm_hm(tw.get(k)):
                            tw[k] = _norm_hm(tw[k])
    for a in _autos.values():                   # older files may hold "7:00": the scheduler wants "07:00"
        for e in (a.get("schedule") or []) if isinstance(a, dict) else []:
            if isinstance(e, dict) and _norm_hm(e.get("time")):
                e["time"] = _norm_hm(e.get("time"))


_load_autos()


def _inching_sec(dev_id: str, code: str) -> int:
    """Total auto-off seconds for a channel (0 = off). Reads inching_sec; falls
    back to legacy inching_min*60."""
    a = _autos.get(f"{dev_id}/{code}") or {}
    try:
        if a.get("inching_sec") is not None:
            return max(0, int(a.get("inching_sec") or 0))
        return max(0, int(a.get("inching_min") or 0)) * 60
    except Exception:
        return 0


def _cancel_inching(key: str):
    _cancel_deferred(f"inch|{key}")


def _arm_inching(dev_id: str, code: str):
    """Auto-off N seconds after ANY turn-on (manual/scheduled/binding). Re-arms.
    Runs on the persistent deferred registry, so it survives an app restart."""
    key = f"{dev_id}/{code}"
    _cancel_inching(key)
    if not _rule_enabled(f"a|{key}|inch"):
        return
    secs = _inching_sec(dev_id, code)
    if secs <= 0:
        return
    _schedule_deferred(f"inch|{key}", secs, dev_id, code, False, "inching")
    log.info(f"[inching] {key} armed off in {secs}s")


def _inching_hook(dev_id: str, code: str, on: bool):
    if on:
        _arm_inching(dev_id, code)
    else:
        _cancel_inching(f"{dev_id}/{code}")


def _rearm_inching_on_boot():
    """Gap-filler after _replay_pending_on_boot(): if a channel is ON at boot but
    has no restored inching timer (e.g. switched on physically while we were
    down), arm a fresh window. Restored timers keep their exact remaining time."""
    for key in list(_autos.keys()):
        if "/" not in key:
            continue
        dev, code = key.split("/", 1)
        if _inching_sec(dev, code) > 0 and not _deferred_active(f"inch|{key}"):
            st = _status_copy(dev, {})
            if (st.get("values") or {}).get(code):
                _arm_inching(dev, code)


def _scheduler_loop():
    """Once a minute, fire the schedule entries that came due since the previous tick.
    One bad entry must not kill the thread: that would silently stop every
    schedule in the house."""
    log.info("[sched] scheduler thread started")
    prev = None
    while True:
        now = datetime.now(_LOCAL_TZ) if _LOCAL_TZ else datetime.now()
        try:
            _scheduler_tick(now, prev)
        except Exception:
            log.exception("[sched] tick failed")
        prev = now
        time.sleep(max(1, 61 - now.second))        # land once per minute


_sched_fired: dict = {}     # (key, time, action) -> date it last fired: once a day, whatever the clock does


def _scheduler_tick(now, prev=None):
    """Due = local time HH:MM in (previous tick, now], at most once per calendar day. The window
    catches what a skipped hour would miss (spring DST: 02:30 does not exist, it fires at 03:00),
    the per-day guard stops a repeated hour from firing twice (autumn DST: 01:30 comes twice)."""
    cur = now.hour * 60 + now.minute
    if prev is None or prev.date() != now.date():
        lo = cur                                # first tick of the day / of the process: exact minute
    else:
        lo = min(cur, prev.hour * 60 + prev.minute + 1)
        lo = max(lo, cur - 90)                  # a stalled thread must not replay the whole day
    today = now.date()
    wd = now.isoweekday()                      # 1=Mon .. 7=Sun
    with _autos_lock:
        items = [(k, list(a.get("schedule") or [])) for k, a in _autos.items()]
    due = []
    for key, sched in items:
        if "/" not in key:
            continue
        for e in sched:
            hm = _norm_hm(e.get("time"))
            if hm is None:
                continue
            m = int(hm[:2]) * 60 + int(hm[3:])
            if lo <= m <= cur:
                due.append((m, key, hm, e))
    # after a jump several rows are due at once: run them in clock order, so the last one wins
    for m, key, hm, e in sorted(due, key=lambda x: x[0]):
        dev, code = key.split("/", 1)
        if wd not in (e.get("days") or [1, 2, 3, 4, 5, 6, 7]):
            continue
        act = e.get("action")
        if _sched_fired.get((key, hm, act)) == today:
            continue
        if not _rule_enabled(f"a|{key}|sched|{hm}|{act}"):
            continue
        _sched_fired[(key, hm, act)] = today
        _cancel_deferred(f"sched|{key}")   # a newer row supersedes a retry still waiting
        try:
            if act == "on":
                _set_switch(dev, code, True)
                _arm_inching(dev, code)
            elif act == "off":
                _cancel_inching(key)
                _set_switch(dev, code, False)
            log.info(f"[sched] {key} {act} @ {hm}")
        except Exception as ex:
            # fired for today, but not lost: the deferred registry retries it until it lands
            log.warning(f"[sched] {key} {act} failed: {ex} -- retrying")
            _schedule_deferred(f"sched|{key}", 15, dev, code, act == "on", "schedule retry")


def _execute_z2m_binding(src_id, code, gesture, b):
    """Target is a Zigbee device living in zigbee2mqtt (not a generic-MQTT one).
    Drive it by publishing to zigbee2mqtt/<friendly_name>/set."""
    cli = _mqtt_client
    if cli is None:
        return
    target = b["target"]
    action = b["action"]
    z_code = b.get("code") or "state"   # multi-gang: state_left/center/right etc.
    payload = {
        "on": {z_code: "ON"},
        "off": {z_code: "OFF"},
        "toggle": {z_code: "TOGGLE"},
        "bright_up": {"brightness_step_onoff": 25},
        "bright_down": {"brightness_step_onoff": -25},
    }.get(action)
    if payload is None:
        return
    try:
        _mqtt_pub(cli, f"zigbee2mqtt/{target}/set", json.dumps(payload))
    except Exception as e:
        log.warning(f"[binding] z2m publish failed {target}: {e}")
        return
    if action in ("on", "off", "toggle"):
        _cancel_deferred(f"sched|{target}/{z_code}")   # as in _apply_value: the button decided
    log.warning(f"[binding] {src_id}/{code}={gesture} -> z2m {target} {payload}")
    return {"target": target, "code": "state", "value": payload}


def _conditions_pass(conds) -> bool:
    """AND-evaluate a rule's optional conditions against the latest cached state.
    Each cond: {device, code, op, value}. op in <,>,<=,>=,==,!=,on,off.
    'on'/'off' check truthiness; comparisons coerce to float when possible.
    Missing/offline data -> condition fails (safe default: don't fire)."""
    for c in (conds or []):
        dev, ccode, op = c.get("device"), c.get("code"), c.get("op")
        st = _status_copy(dev, {})
        cur = (st.get("values") or {}).get(ccode)
        if cur is None or not st.get("online"):
            return False                       # unknown is not "off": a dead sensor must not fire rules
        if op == "on":
            if not bool(cur):
                return False
            continue
        if op == "off":
            if bool(cur):
                return False
            continue
        target = c.get("value")
        if target is None:                     # empty value box in the UI
            return False
        try:
            a, b2 = float(cur), float(target)
        except (TypeError, ValueError):
            a, b2 = cur, target
        # Evaluate ONLY the chosen comparison: building the whole table first made
        # any mixed pair (text sensor vs number) raise TypeError, which bubbled out
        # of _execute_binding and left the binding silently dead forever.
        cmp_fn = {"<": operator.lt, ">": operator.gt, "<=": operator.le,
                  ">=": operator.ge, "==": operator.eq, "!=": operator.ne}.get(op)
        if cmp_fn is None:
            return False
        try:
            if not cmp_fn(a, b2):
                return False
        except TypeError:                      # not comparable -> condition fails
            log.warning(f"[cond] {dev}/{ccode} {op} {target!r}: incomparable with {cur!r}")
            return False
    return True


def _in_time_window(tw) -> bool:
    """True if now falls inside the rule's optional time window. Window:
    {from:'HH:MM', to:'HH:MM', days:[1..7]}. Handles overnight ranges
    (from>to, e.g. 22:00->07:00); for those, the morning part (cur<to) is
    attributed to the weekday the window started on (the previous day)."""
    if not tw:
        return True
    f, t = _norm_hm(tw.get("from")), _norm_hm(tw.get("to"))
    if not (f and t):
        return True
    days = tw.get("days") or _WEEKDAYS_ALL
    now = datetime.now(_LOCAL_TZ) if _LOCAL_TZ else datetime.now()
    cur = now.strftime("%H:%M")
    wd = now.isoweekday()
    if f <= t:                                   # same-day window
        return (f <= cur < t) and wd in days
    if cur >= f:                                 # overnight, evening part
        return wd in days
    if cur < t:                                  # overnight, morning part
        return (wd - 1 or 7) in days
    return False


def _norm_time_window(tw):
    """Validate + normalize a {from,to,days} window for storage, or None.
    Raises HTTPException on malformed input (used by request handlers)."""
    if tw is None:
        return None
    if not isinstance(tw, dict):
        raise HTTPException(400, "time_window must be an object {from, to, days}")
    if not (_valid_hm(tw.get("from")) and _valid_hm(tw.get("to"))):
        raise HTTPException(400, "time_window needs valid from/to (HH:MM)")
    days = tw.get("days") or _WEEKDAYS_ALL
    if not isinstance(days, list) or not all(isinstance(d, int) and 1 <= d <= 7 for d in days):
        raise HTTPException(400, "time_window days must be 1..7")
    return {"from": _norm_hm(tw["from"]), "to": _norm_hm(tw["to"]), "days": days}


def _apply_for_timer(token: str, b: dict, dev_id: str, code: str) -> bool:
    """If the binding action carries a `for` duration, schedule the revert and
    return True (caller then skips device-level inching for this fire). Honors
    `retrigger`: restart (default) re-arms from now; extend keeps the later
    deadline; ignore is handled by the caller before re-applying the action."""
    fs = b.get("for")
    try:
        fs = int(fs or 0)
    except (TypeError, ValueError):
        fs = 0
    if fs <= 0:
        return False
    revert = b.get("action") != "on"            # on->off ; off->on
    pol = (b.get("retrigger") or "restart").lower()
    tk = f"for|{token}"
    if pol == "extend" and _deferred_active(tk):
        with _pending_lock:
            cur_at = (_pending.get(tk) or {}).get("run_at", 0)
        _schedule_deferred(tk, 0, dev_id, code, revert, "for",
                           run_at=max(cur_at, time.time() + fs))
    else:
        _schedule_deferred(tk, fs, dev_id, code, revert, "for")
    return True


def _execute_binding(src_id: str, code: str, gesture: str, force: bool = False):
    # force=True: a manual "test/run" from the UI -- fire the action regardless of
    # the rule's enabled flag, time window, conditions or for-window guard.
    with _bindings_lock:
        b = _bindings.get(src_id, {}).get(code, {}).get(gesture)
        if not b:
            return
        b = dict(b)
    rid = f"b|{src_id}|{code}|{gesture}"
    if not force and not _rule_enabled(rid):
        return
    # retrigger=ignore: while the `for` window is still open, swallow new triggers
    if not force and b.get("for") and (b.get("retrigger") or "restart").lower() == "ignore" \
            and _deferred_active(f"for|{rid}"):
        log.info(f"[binding] {src_id}/{code}={gesture} -> ignored (for-window active)")
        return
    if not force and not _in_time_window(b.get("time_window")):
        log.info(f"[binding] {src_id}/{code}={gesture} -> skipped (time window)")
        return
    if not force and not _conditions_pass(b.get("conditions")):
        log.info(f"[binding] {src_id}/{code}={gesture} -> skipped (conditions)")
        return
    if b.get("action") in BINDING_ACTIONS:              # a plug-in's action (its target is its own)
        res = BINDING_ACTIONS[b["action"]]["run"](b)
        res = dict(res) if isinstance(res, dict) else {}
        res.setdefault("action", b["action"])                 # the press log shows what fired
        log.info(f"[binding] {src_id}/{code}={gesture} -> {b['action']} {b.get('target')}")
        return res
    target_id = b["target"]
    # The z2m shortcut publishes standard-cluster commands (brightness_step_onoff,
    # state TOGGLE). A Tuya-datapoint device speaks only EF00 and has no
    # genOnOff/genLevelCtrl, so those are rejected outright -- route it through the
    # generic path below, which reads the cached level and sends an absolute value.
    _tgt = DEVICES.get(target_id, {})
    if (b.get("z2m") or _tgt.get("transport") == "z2m") and not _tgt.get("_skip_get"):
        res = _execute_z2m_binding(src_id, code, gesture, b)
        zc = b.get("code") or "state"
        if not _apply_for_timer(rid, b, target_id, zc):
            if b.get("action") == "on":
                _arm_inching(target_id, zc)      # inching window (e.g. shredder)
            elif b.get("action") == "off":
                _cancel_inching(f"{target_id}/{zc}")
        return res
    target_code = b["code"]
    action = b["action"]
    if target_id not in DEVICES:
        return
    tcfg = DEVICES[target_id]
    tctl = tcfg["_ctl"].get(target_code)
    if not tctl:
        return
    tctl_kind = tctl.get("kind", "switch")
    if action in ("on", "press"):             # press: a plug-in's stateless channel (CONTROL_KINDS)
        val = True
    elif action == "off":
        val = False
    elif action == "toggle":
        cur = (_status_cache.get(target_id) or {}).get("values", {}).get(target_code, False)
        val = not bool(cur)
    elif action in ("bright_up", "bright_down"):
        # dimmer step: +/-10% per event, clamped to 1..100 (off only via a switch binding)
        if tctl.get("kind") != "bright":
            return
        raw = (_status_cache.get(target_id) or {}).get("values", {}).get(target_code)
        pct = _raw_to_pct(tctl, raw) or 0
        pct = max(1, min(100, pct + (10 if action == "bright_up" else -10)))
        val = _pct_to_raw(tctl, pct)
    else:
        return
    _apply_value(target_id, target_code, val, tctl_kind)
    if tctl_kind in CONTROL_KINDS:            # an event: nothing to store or mirror
        _last_control_ts[target_id] = time.time()
        return {"target": target_id, "code": target_code, "action": action}
    cur_st = _status_copy(target_id, {"online": True, "values": {}, "raw": {}})
    cur_st.setdefault("values", {})[target_code] = val
    cur_st["online"] = True
    cur_st.pop("stale", None)
    _store_status(target_id, cur_st)
    _last_control_ts[target_id] = time.time()
    if not _apply_for_timer(rid, b, target_id, target_code):
        if action == "on":
            _arm_inching(target_id, target_code)  # inching window (e.g. shredder)
        elif action == "off":
            _cancel_inching(f"{target_id}/{target_code}")
    log.info(f"[binding] {src_id}/{code}={gesture} -> {target_id}/{target_code}={val}")
    return {"target": target_id, "code": target_code, "value": val}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
app = FastAPI(title="Yashome")


@app.middleware("http")
async def _security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    # the page has inline script, so CSP cannot stop inline injection; it stops what an
    # injected script would do next: load code or send the token to another host
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    return resp


_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'self'")


# One shared bearer token gates everything that carries state (/api, /ws). Only
# the PWA shell stays open so the token prompt can load. Empty token = FAIL
# CLOSED: a lost .env must never silently reopen the house.
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
_OPEN_PATHS = {"/", "/manifest.json", "/sw.js", "/i18n.js", "/favicon.ico"}
_OPEN_PREFIXES = ("/icon-",)
# a plug-in's public assets: GET /plugins/<name>/ui.js | i18n.json | static/<file>; routes a plug-in
# adds under /plugins/ (any other shape or method) need the token like everything else
_OPEN_ASSET = re.compile(r"^/plugins/[a-z][a-z0-9_]{0,31}/(ui\.js|i18n\.json|static/[A-Za-z0-9_][A-Za-z0-9_.-]{0,63})$")
if not DASHBOARD_TOKEN:
    log.error("DASHBOARD_TOKEN is not set: every /api call will be refused (401)")


def _token_ok(headers, query=None) -> bool:
    """Bearer or X-Token header; ?token= only where a header is impossible (the
    WebSocket), since a URL ends up in access logs and browser history."""
    auth = headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else (
        headers.get("x-token") or (query or {}).get("token", ""))
    # bytes: compare_digest raises on non-ASCII str, which would be a 500 instead of a 401
    return bool(DASHBOARD_TOKEN) and hmac.compare_digest(tok.encode(), DASHBOARD_TOKEN.encode())


def _same_origin(headers) -> bool:
    """A browser sends Origin on cross-site POSTs and WebSocket handshakes. When a
    proxy injects the token, that header is the only thing telling a page on
    another site apart from the dashboard itself. No Origin = not a browser."""
    origin = headers.get("origin")
    if not origin:
        return True
    o = origin.split("://", 1)[-1].rstrip("/").lower()
    hosts = {(headers.get(h) or "").lower() for h in ("host", "x-forwarded-host")}
    return o in hosts or o in _EXTRA_ORIGINS


# A proxy that rewrites Host and sends no X-Forwarded-Host (plain nginx proxy_pass) would
# make the dashboard's own requests look cross-site: list its public name(s) here.
_EXTRA_ORIGINS = {x.strip().split("://", 1)[-1].rstrip("/").lower()
                  for x in os.environ.get("DASHBOARD_ORIGINS", "").split(",") if x.strip()}


def _routes_to_asset(scope) -> bool:
    """Open without the token only what routing really hands to plugin_asset: a plug-in route whose
    pattern also covers a public-looking path ({f}, {p:path}) matches first and stays closed."""
    for r in app.router.routes:
        if r.matches(scope)[0] == Match.FULL:
            return getattr(r, "endpoint", None) is plugin_asset
    return False


@app.middleware("http")
async def _require_token(request, call_next):
    p = request.url.path
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request.headers):
        return _refuse(403, "cross-origin request refused")
    plugin_open = p in OPEN_PATHS and not p.startswith(("/api", "/ws"))   # a plug-in page, never the API
    asset = request.method in ("GET", "HEAD") and _OPEN_ASSET.match(p) and _routes_to_asset(request.scope)
    if p in _OPEN_PATHS or plugin_open or asset or p.startswith(_OPEN_PREFIXES) or _token_ok(request.headers):
        if "settings" in _load_failed and request.method != "GET" and _writes_settings(p):
            return _refuse(503, "settings.json was unreadable at start: fix or remove it and restart; "
                                "nothing that is kept in it can change until then")
        return await call_next(request)
    return _refuse(401, "token required")


# endpoints whose change lives in settings.json: refused up front while it is unreadable, so the
# running state never drifts from what a restart would load
_SETTINGS_WRITERS = ("/api/favorites", "/api/rooms", "/api/tab_order", "/api/tile_sizes", "/api/bindings",
                     "/api/automation", "/api/rules/enable", "/api/rules/delete",
                     "/api/zigbee/device/rename", "/api/zigbee/device/relink")   # /api/rules/run writes nothing


def _writes_settings(path: str) -> bool:
    if path.startswith(_SETTINGS_WRITERS):
        return True
    # /api/devices/{id}/rename, .../channel/{code}/rename, .../dimrange
    return path.startswith("/api/devices/") and path.endswith(("/rename", "/dimrange"))


def _refuse(code: int, why: str):
    # this middleware answers before _security_headers runs, so it adds the same headers itself
    r = JSONResponse({"detail": why}, status_code=code)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Content-Security-Policy"] = _CSP
    return r


def _need_broker():
    """Everything that talks to devices goes through the broker: without it, say so (503)."""
    if _mqtt_client is None or not _mqtt_client.is_connected():
        raise HTTPException(503, "broker not reachable")


def _check_topic_name(name: str, what: str = "name"):
    """Names that end up inside MQTT topics: a wildcard or / in there is a
    protocol error that makes the broker drop our session; | is the rule-id
    separator."""
    if not name or len(name) > 64 or any(ch in name for ch in "/+#|"):
        raise HTTPException(400, f"{what} must be 1-64 chars without / + # |")


# ---------------------------------------------------------------------------
# MQTT bridge -- LAN clients (displays, scripts) talk to the dashboard over
# mosquitto instead of HTTP.
#   state: home/state/<dev_id>/<code>        retained, plain payload (true/42/3.5)
#          home/state/<dev_id>/<code>_pct    retained, bright codes also as 0-100
#                                            (window-relative when the channel has a
#                                            [floor, ceil] in channel_dimrange)
#          home/state/<dev_id>/online        retained, true/false
#   cmd:   home/cmd/<dev_id>/<code>          true|false|on|off|toggle|<number>
#          bright codes: number = percent 0-100 of the same (window) scale, plus
#          up/down = one tenth of the window, rounded to the nearest tenth
# Commands are re-posted to our own REST /control, so validation, the optimistic
# cache and the logs apply to MQTT and REST alike.
# ---------------------------------------------------------------------------
try:
    import paho.mqtt.client as _paho
except ImportError:
    _paho = None

_mqtt_ready = threading.Event()   # set on CONNACK; publishes are dropped before it


def _mqtt_pub(cli, topic, payload=None, retain=False):
    """Publish and FAIL LOUDLY if the broker link was down. paho does not queue a
    QoS-0 publish while the socket is closed -- it returns rc=MQTT_ERR_NO_CONN and
    drops the message. Every command path used to ignore that, so /control still
    answered ok:True and wrote the value nobody applied into the optimistic cache.
    Only command paths use this; the retained-mirror republishers stay best-effort
    because the 30 s refresh loop covers them."""
    info = cli.publish(topic, payload, retain=retain)
    rc = getattr(info, "rc", 0)
    if rc != 0:
        raise RuntimeError(f"mqtt publish to {topic} failed (rc={rc})")
    return info


MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))


def _mqtt_auth(cli):
    """Broker credentials from the environment; anonymous when unset."""
    user = os.environ.get("MQTT_USER")
    if user:
        cli.username_pw_set(user, os.environ.get("MQTT_PASSWORD", ""))
    return cli
_mqtt_client = None
_mqtt_last_pub: dict = {}    # topic -> last payload, dedup against poller spam
# friendly_name -> monotonic ts of the last SELF-reported z2m frame. Retained
# frames replayed at connect do not count: they say what was true, not that the
# device is currently talking.
_z2m_seen: dict = {}
_z2m_asked: dict = {}        # did -> monotonic of the last /get we sent it
Z2M_GET_SKIP_SEC = 90        # three refresh cycles
Z2M_GET_BACKOFF_SEC = 600    # an unanswered /get is retried this often, not every cycle
_z2m_last_action: dict = {}  # zigbee2mqtt friendly_name -> (action, ts), dedup republishes
_sensor_last: dict = {}      # f"{name}/{code}" -> last sensor value (fire bindings on change)
_sensor_arm: dict = {}       # f"{name}/{code}/{gesture}" -> armed (hysteresis for numeric)

def _mqtt_payload(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _mqtt_publish_state(dev_id: str):
    cli = _mqtt_client
    if cli is None:
        return
    # every device, whatever it speaks, is mirrored to home/state/<id>/<code>: that
    # is the one contract MQTT clients read (docs/mqtt-contract.md)
    st = _status_copy(dev_id, {})
    msgs = {f"home/state/{dev_id}/online": _mqtt_payload(bool(st.get("online")))}
    values = st.get("values") or {}
    for code, val in values.items():
        msgs[f"home/state/{dev_id}/{code}"] = _mqtt_payload(val)
    cfg = DEVICES.get(dev_id)
    if cfg:
        # The percent mirror always carries the LEVEL, including while the channel
        # is off: an off tile reads what it will come back at, not 0%. On/off is
        # therefore NOT encoded in this number: a client takes it from the switch's
        # own state topic and the dashboard from the pill's .on class. Only an ON light parked at the device
        # floor is clamped up to 1%, so it never reads as a dark 0%.
        switches = [c for c in cfg["_ctl"].values()
                    if c.get("kind") == "switch"]
        for ctl in cfg["_ctl"].values():
            if ctl.get("kind") == "bright" and ctl["code"] in values:
                # Resolve "is it on" from the switch on the SAME gang: a multi-gang
                # dimmer has one per channel. This used to read a hardcoded
                # "switch_led" code, which no current device has, so power_on
                # always defaulted to True and an OFF channel kept publishing its
                # last brightness -- an MQTT client then could not tell off from
                # on-at-minimum.
                want = _chan_of(str(ctl.get("code") or ""))
                sw = next((s for s in switches
                           if _chan_of(str(s.get("code") or "")) == want), None)
                if sw is None and len(switches) == 1:
                    sw = switches[0]
                power_on = bool(values.get(sw["code"], True)) if sw else True
                pct = _raw_to_pct(ctl, values.get(ctl["code"]))
                if pct is not None:
                    # window-relative when the channel has a [floor, ceil]: the client's
                    # bar then spans the usable travel, like the dashboard pill
                    pct = _ui_to_win(_dim_window(dev_id, ctl["code"]), pct)
                    if power_on and pct < 1:
                        pct = 1
                    msgs[f"home/state/{dev_id}/{ctl['code']}_pct"] = str(pct)
    for topic, payload in msgs.items():
        if _mqtt_last_pub.get(topic) != payload:
            _mqtt_last_pub[topic] = payload
            try:
                cli.publish(topic, payload, retain=True)
            except Exception:
                pass


def _handle_z2m_message(parts, payload, retain=False):
    """zigbee2mqtt/<friendly_name> state frames. If the device reported a button
    `action`, feed it into the same binding engine as every other button:
    src=friendly_name, code="action", gesture=<action> (e.g. '2_single')."""
    if len(parts) != 2 or parts[1] == "bridge":
        return
    name = parts[1]
    try:
        d = json.loads(payload.decode("utf-8", "replace"))
    except Exception:
        return
    if not isinstance(d, dict):
        return
    # only names zigbee2mqtt registered: anyone with the z2m account could otherwise invent
    # devices (retained home/state spam) or pose as a generic device and fire its bindings
    if (DEVICES.get(name) or {}).get("transport") != "z2m":
        return
    if not retain:
        _z2m_seen[name] = time.monotonic()
    # 1) status ingest for registered z2m devices (switches/brightness/sensors)
    cfg = DEVICES.get(name)
    if cfg and cfg.get("transport") == "z2m":
        values, raw = {}, {}
        for c in cfg["controls"]:
            code = c["code"]
            if c["kind"] == "trigger_text" or code not in d:
                continue
            rawv = d[code]
            if c["kind"] == "switch":
                values[code] = (rawv == "ON") if isinstance(rawv, str) else bool(rawv)
            else:
                values[code] = rawv
            raw[code] = rawv
        if values:
            prev = _status_copy(name, {})
            prev_vals = prev.get("values") or {}
            # a state frame means alive -- unless availability said offline and this is only the
            # retained copy replayed on reconnect
            avail = prev.get("availability")
            if (retain and avail == "offline") or _z2m_bridge_online is False:
                online = False           # a replayed frame while the device or zigbee2mqtt is down
            else:
                online, avail = True, (avail and "online")
            st = {"online": online, "values": {**prev_vals, **values},
                  "raw": {**(prev.get("raw") or {}), **raw}}
            if avail:
                st["availability"] = avail
            (_store_status_exact if not online else _store_status)(name, st)
            if not retain:
                _fire_sensor_triggers(name, cfg, values)   # sensor value-change bindings (live only)
            # Arm/cancel server-side inching on a switch transition, so ANY turn-on
            # (physical wall switch, external app) gets the auto-off window --
            # not just turn-ons we issue via /control. Only act on OFF<->ON edges
            # (equal repeats would otherwise keep resetting the timer -> never fire).
            for c in cfg["controls"]:
                code = c["code"]
                if c.get("kind") != "switch" or code not in values:
                    continue
                newv = bool(values[code])
                if code in prev_vals and bool(prev_vals[code]) == newv:
                    continue
                if not retain:
                    _inching_hook(name, code, newv)
                elif newv and not _deferred_active(f"inch|{name}/{code}"):
                    # a retained ON after our downtime (z2m keeps retain: true): the channel was
                    # switched on while nobody watched, so its auto-off window starts now
                    _inching_hook(name, code, True)
    # 2) button action -> binding engine
    action = d.get("action")
    if not action:                       # state/battery republishes carry empty action
        return
    if retain:                           # stale action from broker on (re)subscribe -- do not re-fire
        return
    now = time.time()
    last = _z2m_last_action.get(name)
    if last and last[0] == action and now - last[1] < 0.8:
        return                           # swallow duplicate publish of one press
    _z2m_last_action[name] = (action, now)
    # event journal: z2m remotes report presses via 'action' -- record them so
    # the "Press log" shows them (and the WS last_event_ts triggers the modal
    # to refresh + flash).
    with _events_lock:
        _events[name].append({"ts": now, "code": "action",
                              "value": action, "t": int(now)})
    cur = _status_copy(name, {"online": True, "values": {}, "raw": {}})
    cur["online"] = True
    cur["last_event_ts"] = now
    cur["last_event"] = {"code": "action", "value": action}
    _store_status(name, cur)
    _bg.submit(_safe_execute_binding, name, "action", str(action))


def _handle_z2m_availability(name: str, payload: bytes):
    """z2m 2.x: {"state": "online"|"offline"}; older: a bare word. An offline device is
    shown as such and fails rule conditions instead of passing on its last value."""
    if name not in DEVICES:
        return
    raw = payload.decode("utf-8", "replace").strip()
    try:
        raw = (json.loads(raw) or {}).get("state", raw) if raw.startswith("{") else raw
    except ValueError:
        pass
    cur = _status_copy(name, {"values": {}, "raw": {}})
    cur["online"] = str(raw).lower() == "online"
    cur["availability"] = "online" if cur["online"] else "offline"   # remembered for bridge/devices
    cur.pop("stale", None)
    # not _store_status: its grace period (keep online for a few failed polls) is for guesses,
    # this is zigbee2mqtt saying the device is gone
    _store_status_exact(name, cur)


def _mqtt_on_message(cli, userdata, msg):
    try:
        hits = _MQTT_TOPIC_IDX.get(msg.topic)   # generic-MQTT DIY devices (exact topic)
        if hits:
            for _did, _role, _code in hits:
                _handle_mqtt_state(_did, _role, _code, msg.payload, msg.retain)
            return
        parts = msg.topic.split("/")
        if parts[0] == "zigbee2mqtt":
            if len(parts) == 3 and parts[2] == "availability":
                _handle_z2m_availability(parts[1], msg.payload)
                return
            if len(parts) >= 2 and parts[1] == "bridge":
                _handle_z2m_bridge(parts, msg.payload)
            else:
                _handle_z2m_message(parts, msg.payload, msg.retain)
            return
        for pre, fn in MQTT_HANDLERS:            # plug-in topics
            if tuple(parts[:len(pre)]) == tuple(pre):
                fn(parts, msg.payload, msg.retain)
                return
        if len(parts) != 4 or parts[:2] != ["home", "cmd"]:
            return
        if msg.retain:            # a command is an event; a retained one would re-run on every reconnect
            log.info(f"[mqtt] ignoring retained command {msg.topic}")
            return
        dev_id, code = parts[2], parts[3]
        if dev_id not in DEVICES:
            log.info(f"[mqtt] cmd for unknown device {dev_id}")
            return
        if code.endswith("_pct"):
            code = code[:-4]
        raw = msg.payload.decode("utf-8", "replace").strip()
        cfg = DEVICES[dev_id]
        ctl = cfg["_ctl"].get(code)
        low = raw.lower()
        if low == "toggle":
            cur = (_status_cache.get(dev_id) or {}).get("values", {}).get(code, False)
            value = not bool(cur)
        elif low in ("true", "on"):
            value = True
        elif low in ("false", "off"):
            value = False
        elif low in ("up", "down") and ctl and ctl.get("kind") == "bright":
            cur_raw = (_status_cache.get(dev_id) or {}).get("values", {}).get(code)
            win = _dim_window(dev_id, code)
            pos = _ui_to_win(win, _raw_to_pct(ctl, cur_raw) or 0)
            nxt = _dim_step(pos, low)
            if nxt is None:
                # already at the floor: this step switches the gang off instead,
                # and a further "down" on an off gang is a no-op
                sw = _switch_for(cfg, code)
                if sw:
                    if not (_status_cache.get(dev_id) or {}).get("values", {}).get(sw["code"], True):
                        return
                    code, value = sw["code"], False
                else:
                    value = _win_to_ui(win, 0)
            else:
                value = _win_to_ui(win, nxt)
        else:
            try:
                value = int(raw)
            except ValueError:
                try:
                    value = float(raw)
                except ValueError:
                    value = raw
            if ctl and ctl.get("kind") == "bright" and isinstance(value, (int, float)):
                # a client's percent is window-relative; control() wants full-range
                value = _win_to_ui(_dim_window(dev_id, code), value)
        # Hand the command to the event loop instead of HTTP-posting to ourselves:
        # this is paho's only network thread, and a blocking round-trip here stalls
        # every MQTT frame and the keepalive for its whole duration.
        if _MAIN_LOOP is None:
            log.warning(f"[mqtt] cmd {msg.topic} arrived before startup, dropped")
            return
        fut = asyncio.run_coroutine_threadsafe(
            control(dev_id, ControlBody(code=code, value=value)), _MAIN_LOOP)
        fut.add_done_callback(lambda f, t=msg.topic, v=raw: _log_cmd_result(f, t, v))
    except Exception as e:
        log.warning(f"[mqtt] cmd error {msg.topic}: {e}")


def _log_cmd_result(fut, topic, raw):
    try:
        fut.result()
    except HTTPException as e:
        log.warning(f"[mqtt] cmd {topic}={raw} -> {e.status_code} {e.detail}")
    except Exception as e:
        log.warning(f"[mqtt] cmd {topic}={raw} failed: {e}")


def _mqtt_on_connect(cli, userdata, flags, rc, properties=None):
    if getattr(rc, "is_failure", False):
        log.error(f"[mqtt] broker refused the connection: {rc}")
        _mqtt_ready.clear()
        return
    log.info(f"[mqtt] connected to {MQTT_HOST}:{MQTT_PORT} rc={rc}")
    try:
        _mqtt_subscribe_all(cli)
    except Exception:
        log.exception("[mqtt] on_connect failed; the link stays up, subscriptions may be partial")
    _mqtt_ready.set()          # startup waits on this before replaying timers


def _mqtt_on_disconnect(cli, userdata, flags, rc, properties=None):
    _mqtt_ready.clear()
    log.warning(f"[mqtt] disconnected: {rc}")


def _mqtt_subscribe_all(cli):
    global _MQTT_TOPIC_IDX
    # Order matters: the broker replays retained messages in subscription order, and a state
    # frame for a name not registered yet is dropped. Registry and bridge state come first.
    cli.subscribe("zigbee2mqtt/bridge/state")    # retained online/offline of zigbee2mqtt itself
    cli.subscribe("zigbee2mqtt/bridge/devices")  # live (re)registration on join/rename/leave
    cli.subscribe("zigbee2mqtt/bridge/event") # device_joined / interview -> pairing discovery
    cli.subscribe("zigbee2mqtt/+/availability")  # online/offline when availability is on in z2m
    cli.subscribe("zigbee2mqtt/+")            # z2m state frames -> ingest / binding engine
    cli.subscribe("home/cmd/#")
    _MQTT_TOPIC_IDX = _mqtt_topic_index()     # generic-MQTT DIY ESP devices
    for _t in _MQTT_TOPIC_IDX:
        cli.subscribe(_t)
    if _MQTT_TOPIC_IDX:
        log.info(f"[mqtt] subscribed {len(_MQTT_TOPIC_IDX)} generic-mqtt topics")
    for t in MQTT_SUBSCRIPTIONS:                 # plug-in topics: last, one bad filter hurts nobody else
        try:
            cli.subscribe(t)
        except Exception as e:
            log.error(f"[plugins] subscribe {t!r} failed: {e}")
    # fresh retained snapshot for every device (a client may hold stale state)
    _mqtt_last_pub.clear()
    for did in list(DEVICES):
        _mqtt_publish_state(did)


def _mqtt_start():
    global _mqtt_client
    if _paho is None:
        log.warning("[mqtt] paho-mqtt not installed, bridge disabled")
        return
    cli = _paho.Client(_paho.CallbackAPIVersion.VERSION2, client_id=f"home-dashboard-{secrets.token_hex(3)}")   # two backends on one broker must not kick each other
    cli.on_connect = _mqtt_on_connect
    cli.on_disconnect = _mqtt_on_disconnect
    cli.on_message = _mqtt_on_message
    cli.suppress_exceptions = True    # a bug in a callback is logged, the network loop lives on
    cli.reconnect_delay_set(min_delay=1, max_delay=30)
    _mqtt_auth(cli)
    _mqtt_client = cli

    def _connect_when_reachable():
        # A fresh install starts the broker and the backend together: the first connect
        # may fail on DNS or a refused socket, and paho's network loop does not retry
        # THAT failure - only lost connections. So knock until the broker answers.
        delay = 1
        while True:
            try:
                cli.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
                break
            except (OSError, ValueError) as e:        # ValueError: empty MQTT_HOST
                log.warning(f"[mqtt] {MQTT_HOST}:{MQTT_PORT} not reachable ({e}); retry in {delay}s")
                time.sleep(delay)
                delay = min(delay * 2, 30)
        cli.loop_start()

    threading.Thread(target=_connect_when_reachable, daemon=True, name="mqtt-connect").start()


# ---------------------------------------------------------------------------
# Zigbee2MQTT devices -- the main transport. Built from z2m's retained
# bridge/devices; controlled via zigbee2mqtt/<name>/set; status ingested from
# zigbee2mqtt/<name>. transport="z2m"; controls are addressed by their z2m property.
# ---------------------------------------------------------------------------
_Z2M_SENSOR_LABELS = {
    "occupancy":   ("Motion", ""),
    "illuminance": ("Illuminance", "lx"),
    "temperature": ("Temperature", "\u00b0C"),
    "humidity":    ("Humidity", "%"),
    "contact":     ("Contact", ""),
    "water_leak":  ("Water leak", ""),
    "smoke":       ("Smoke", ""),
    "gas":         ("Gas", ""),
    "vibration":   ("Vibration", ""),
    "tamper":      ("Tamper", ""),
    "presence":    ("Presence", ""),
}


def _z2m_chan_label(raw: str) -> str:
    """Channel suffix of a multi-gang property -> a short human label.
    'state_l2' passes '_l2' here and gets '2'; multi-gang relays pass '_left' and
    keep 'left'; a single-gang device passes '' and gets ''. Endpoint aliases are
    l1/l2/... on dimmers but left/center/right on the switch modules already in
    the house, so both spellings have to survive."""
    s = raw.lstrip("_")
    if len(s) > 1 and s[0] == "l" and s[1:].isdigit():
        return s[1:]
    return s


def _chan_of(code: str) -> str:
    """Gang suffix of a control code: 'state_l2' -> 'l2', 'brightness' -> ''."""
    for base in ("state", "brightness"):
        if code.startswith(base):
            return code[len(base):].lstrip("_")
    return ""


def _switch_for(cfg, code):
    """The on/off switch on the SAME gang as a brightness code: 'brightness_l2' ->
    'state_l2', 'brightness' -> 'state'. None when the device has no such switch."""
    want = _chan_of(str(code or ""))
    for c in cfg.get("controls", []):
        if c.get("kind") == "switch" and _chan_of(str(c.get("code") or "")) == want:
            return c
    return None


def _z2m_controls_from_exposes(exposes):
    controls = []
    for e in exposes or []:
        etype = e.get("type")
        if etype in ("switch", "light"):
            for f in e.get("features", []):
                p = f.get("property")
                if not p:
                    continue
                if f.get("type") == "binary" and str(p).startswith("state"):
                    controls.append({"kind": "switch", "code": p,
                                     "label": _z2m_chan_label(str(p)[len("state"):])})
                elif f.get("type") == "numeric" and str(p).startswith("brightness"):
                    # Multi-gang dimmers expose brightness_l1 / brightness_l2, so
                    # match by prefix rather than the exact "brightness" -- that
                    # dropped every channel of a 2-gang dimmer. min_brightness /
                    # max_brightness are device config and do not hit this branch
                    # because they do not start with "brightness".
                    sfx = _z2m_chan_label(str(p)[len("brightness"):])
                    controls.append({"kind": "bright", "code": p,
                                     "label": f"Brightness {sfx}" if sfx else "Brightness",
                                     "min": 0,
                                     "max": int(f.get("value_max") or 254)})
        elif etype == "enum" and e.get("property") == "action":
            controls.append({"kind": "trigger_text", "code": "action",
                             "label": "Buttons", "ro": True, "values": e.get("values") or []})
        elif etype == "numeric" and e.get("property") == "battery":
            controls.append({"kind": "sensor", "code": "battery",
                             "label": "Battery", "unit": "%", "scale": 1, "ro": True,
                             "vtype": "num"})
        elif (etype in ("binary", "numeric") and e.get("property")
              and e.get("property") != "linkquality"        # signal meta, not user-facing
              and not (e.get("access", 0) & 2)):
            # read-only top-level sensor (occupancy, illuminance, temperature...).
            # writable exposes (access bit 2) are device config -- skip them.
            p = e["property"]
            label, unit = _Z2M_SENSOR_LABELS.get(
                p, (p.replace("_", " ").capitalize(), ""))
            controls.append({"kind": "sensor", "code": p, "label": label,
                             "unit": unit, "scale": 1, "ro": True,
                             "vtype": "bool" if etype == "binary" else "num"})
    return controls


def _register_z2m_device(z):
    fname = z.get("friendly_name")
    if not fname or z.get("type") == "Coordinator":
        return False
    if fname in DEVICES and DEVICES[fname].get("transport") != "z2m":
        # the id belongs to another device (mqtt_devices.json, a plug-in): never overwrite it
        log.warning(f"[z2m] '{fname}' skipped: the id is taken by a {DEVICES[fname].get('transport')} device; rename one of them")
        return False
    if "/" in fname:
        # a / makes a sub-topic in z2m and cannot be one path segment of our REST API
        log.warning(f"[z2m] '{fname}' skipped: rename it in zigbee2mqtt without '/'")
        return False
    defn = z.get("definition") or {}
    controls = _safe_controls(fname, _z2m_controls_from_exposes(defn.get("exposes")))
    # A device z2m failed to identify (interview read no manufacturer/model, so
    # z2m emitted an "Automatically generated definition" exposing linkquality
    # only) produces zero controls. Registering it anyway is deliberate: dropping
    # it made a freshly paired device vanish from the dashboard with no trace and
    # no way to see or remove it from the UI. It now lands in "Other" with a
    # signal readout and an `unsupported` flag the UI badges.
    # Devices that speak only the Tuya EF00 cluster (manuSpecificTuya, no genOnOff /
    # genLevelCtrl) have no readable attributes: a zigbee2mqtt/<name>/get makes z2m log
    # "No converter available for 'get'" and nothing else. The 30s auto-resync would
    # repeat that forever, so mark them and skip the /get.
    _clusters = set()
    for _ep in (z.get("endpoints") or {}).values():
        _clusters.update((_ep.get("clusters") or {}).get("input") or [])
    skip_get = ("manuSpecificTuya" in _clusters
                and not _clusters & {"genOnOff", "genLevelCtrl"})
    unsupported = not controls
    if unsupported:
        controls = [{"kind": "sensor", "code": "linkquality",
                     "label": "Signal", "unit": "lqi", "scale": 1, "ro": True,
                     "vtype": "num"}]
    _ov = _channel_names.get(fname) or {}
    for _c in controls:
        _c["_label0"] = _c.get("label", "")    # what "reset the name" goes back to
        if _c.get("code") in _ov and _ov[_c["code"]]:
            _c["label"] = _ov[_c["code"]]
    # category = dashboard tab: anything writable is a switch, a remote with an
    # `action` enum is a button, everything else reports and is a sensor
    has_ctrl = any(c["kind"] in ("switch", "bright") for c in controls)
    has_action = any(c["kind"] == "trigger_text" for c in controls)
    category = "switch" if has_ctrl else ("button" if has_action else "sensor")
    d = {"id": fname, "name": _name_overrides.get(fname, fname), "transport": "z2m",
         "ieee": z.get("ieee_address"), "model": defn.get("model"),
         "category": category, "controls": controls, "unsupported": unsupported,
         "_skip_get": skip_get}
    d["_ctl"] = {c["code"]: c for c in controls}
    DEVICES[fname] = d
    BY_ID_NAME[fname] = fname
    # Paired z2m device = online. Battery remotes only transmit on press, so
    # without seeding they'd render offline forever; preserve any cached values.
    prev = _status_copy(fname, {"values": {}, "raw": {}})
    # bridge/devices comes on every join/rename/leave in the network: it is no news about THIS
    # device's liveness, so what its availability topic said last stays in force
    prev["online"] = prev.get("availability", "online") == "online" and _z2m_bridge_online is not False
    prev.pop("stale", None)
    _store_status_exact(fname, prev)
    return True


_z2m_loaded = False        # True once the retained bridge/devices list was read
_z2m_sync_lock = threading.Lock()   # loader and main client never apply a device list at once


def _prune_orphan_state_mirror(window: float = 5.0):
    """Clear retained home/state/<dev>/* branches of devices that no longer exist.

    The mirror is only ever written, never cleaned, so every device removed or
    renamed since the first boot left a frozen retained branch behind, and every
    retained consumer keeps reading those forever.

    Runs on a short-lived client so the ingest path is untouched, and refuses to
    run unless the z2m device list was loaded -- otherwise every z2m device would
    look unknown and the whole mirror would be wiped."""
    if _paho is None:
        return 0
    for _ in range(60):          # the device list arrives from the loader or the main client
        if _z2m_loaded:
            break
        time.sleep(1)
    else:
        log.info("[mirror] prune skipped: the zigbee2mqtt device list never arrived")
        return 0
    topics: set = set()
    cli = _paho.Client(_paho.CallbackAPIVersion.VERSION2, client_id=f"home-mirror-prune-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: topics.add(m.topic)
    try:
        _mqtt_auth(cli).connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        cli.subscribe("home/state/#")
        cli.loop_start()
        time.sleep(window)                 # retained backlog arrives in one burst
    except Exception as e:
        log.warning(f"[mirror] prune skipped: {e}")
        return 0
    finally:
        try:
            cli.loop_stop(); cli.disconnect()
        except Exception:
            pass
    orphans = {}
    for t in topics:
        parts = t.split("/")               # home/state/<dev>/<code>; dev may contain no /
        if len(parts) < 4 or parts[0] != "home" or parts[1] != "state":
            continue
        dev = parts[2]
        if dev not in DEVICES:
            orphans.setdefault(dev, []).append(t)
    if not orphans:
        return 0
    pub = _mqtt_client
    if pub is None:
        return 0
    n = 0
    for dev, ts in orphans.items():
        for t in ts:
            pub.publish(t, None, retain=True)   # empty payload = delete retained
            n += 1
    log.info(f"[mirror] cleared {n} retained topics of {len(orphans)} gone devices: "
          + ", ".join(sorted(orphans)))
    return n


def _load_z2m_devices():
    """Fetch z2m retained device list once (separate short-lived client) and
    register every device into DEVICES so cards/favorites/bindings see them."""
    global _z2m_loaded
    if _paho is None:
        return
    if not _Z2M_ENABLED:
        _z2m_loaded = True        # nothing to load: the mirror prune may run on generic devices alone
        log.info("[z2m] no zigbee2mqtt (Z2M_SERIAL empty or none): skipping the device list")
        return
    import queue as _queue
    box = _queue.Queue()
    cli = _paho.Client(_paho.CallbackAPIVersion.VERSION2, client_id=f"home-z2m-loader-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: box.put((m.topic, m.payload))
    try:
        _mqtt_auth(cli).connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        cli.subscribe("zigbee2mqtt/bridge/devices")
        cli.subscribe("zigbee2mqtt/bridge/state")
        cli.loop_start()
        end = time.time() + 8
        while True:
            topic, payload = box.get(timeout=max(0.1, end - time.time()))
            if topic.endswith("/state"):
                if b"offline" in payload:
                    log.warning("[z2m] zigbee2mqtt reports offline: devices load when it is back")
                    return
                continue
            devs = json.loads(payload.decode("utf-8", "replace"))
            break
    except _queue.Empty:
        log.warning("[z2m] no zigbee2mqtt/bridge/devices within 8 s (zigbee2mqtt down or still starting)")
        return
    except Exception as e:
        log.warning(f"[z2m] device load failed: {e!r}")
        return
    finally:
        try:
            cli.loop_stop(); cli.disconnect()
        except Exception:
            pass
    with _z2m_sync_lock:
        if _z2m_loaded:          # the main client already applied a (newer) bridge/devices
            log.info("[z2m] device list already live from the broker; the loader's copy is not needed")
            return
        n = _sync_z2m_devices(devs)
        _z2m_loaded = True
    log.info(f"[z2m] registered {n} devices into DEVICES")


def _sync_z2m_devices(devs) -> int:
    """Register every z2m device present in `devs` and drop z2m devices that are
    no longer there (left the network or were renamed). Authoritative sync used
    both at startup and live on every bridge/devices update."""
    present = set()
    for z in devs:
        if _register_z2m_device(z):
            present.add(z.get("friendly_name"))
    for did in [k for k, v in list(DEVICES.items())
                if v.get("transport") == "z2m" and k not in present]:
        DEVICES.pop(did, None)
        BY_ID_NAME.pop(did, None)
    return len(present)


# --- Zigbee pairing (permit_join) + live join discovery -------------------
_pairing = {"active": False, "until": 0.0}
_pairing_found: dict = {}          # ieee -> {ieee, friendly_name, model, vendor, description, interview}
_pairing_lock = threading.Lock()


_z2m_devices_cv = threading.Condition()
_z2m_bridge_online = None     # zigbee2mqtt/bridge/state: True / False / None = not heard yet


def _wait_z2m(pred, timeout: float = 8.0) -> bool:
    """Wait until pred() holds after zigbee2mqtt republished its device list (a rename, a
    leave), instead of sleeping a fixed guess. On timeout the list is pulled once directly."""
    end = time.time() + timeout
    with _z2m_devices_cv:
        while not pred() and _z2m_bridge_online is not False:   # a bridge that is down will not answer
            left = end - time.time()
            if left <= 0:
                break
            _z2m_devices_cv.wait(left)
    return pred()      # no second 8 s pull: silence from z2m is the answer here


def _handle_z2m_bridge(parts, payload):
    """Bridge traffic: keep the device registry live and record pairing joins."""
    global _z2m_bridge_online, _z2m_loaded
    topic = parts[1] if len(parts) > 1 else ""
    sub = parts[2] if len(parts) > 2 else ""
    raw = payload.decode("utf-8", "replace").strip()
    if topic == "bridge" and sub == "state":       # 2.x: {"state":"online"}, older: a bare word
        try:
            st = (json.loads(raw) or {}).get("state") if raw.startswith("{") else raw
        except ValueError:
            st = raw
        was = _z2m_bridge_online
        _z2m_bridge_online = str(st).lower() == "online"
        if _z2m_bridge_online and was is False:
            # back after an outage: bridge/devices is not replayed to a running subscriber, so
            # bring back what the devices' own availability does not contradict
            for did in [k for k, d in list(DEVICES.items()) if d.get("transport") == "z2m"]:
                cur = _status_copy(did, {"values": {}, "raw": {}})
                if not cur.get("online") and cur.get("availability", "online") == "online":
                    cur["online"] = True
                    _store_status_exact(did, cur)
            log.info("[z2m] zigbee2mqtt is back online")
        if _z2m_bridge_online is False:          # every time, not only on the change
            # zigbee2mqtt itself is down: none of its devices can be reached, whatever they said
            # last. They come back with the next bridge/devices or their own state frames.
            for did in [k for k, d in list(DEVICES.items()) if d.get("transport") == "z2m"]:
                cur = _status_copy(did, {"values": {}, "raw": {}})
                if cur.get("online"):
                    cur["online"] = False
                    _store_status_exact(did, cur)
            log.warning("[z2m] zigbee2mqtt reports offline: its devices are marked offline")
        with _z2m_devices_cv:
            _z2m_devices_cv.notify_all()
        return
    try:
        data = json.loads(raw)
    except Exception:
        return
    if topic == "bridge" and sub == "devices" and isinstance(data, list):
        with _z2m_sync_lock:
            _sync_z2m_devices(data)
        _z2m_loaded = True       # the registry is known: the mirror prune may run
        with _z2m_devices_cv:
            _z2m_devices_cv.notify_all()   # wake requests waiting for z2m to confirm a change
        return
    if topic == "bridge" and sub == "event" and isinstance(data, dict):
        etype = data.get("type")
        d = data.get("data") or {}
        ieee = d.get("ieee_address") or d.get("friendly_name")
        if not ieee or etype not in ("device_joined", "device_interview"):
            return
        defn = d.get("definition") or {}
        status = d.get("status")          # interview: started/successful/failed
        interview = ("done" if status == "successful"
                     else "failed" if status == "failed" else "pending")
        with _pairing_lock:
            cur = _pairing_found.get(ieee, {})
            cur.update({
                "ieee": ieee,
                "friendly_name": d.get("friendly_name") or cur.get("friendly_name") or ieee,
                "model": defn.get("model") or cur.get("model"),
                "vendor": defn.get("vendor") or cur.get("vendor"),
                "description": defn.get("description") or cur.get("description"),
            })
            # don't downgrade a completed interview back to pending on later events
            if cur.get("interview") != "done":
                cur["interview"] = interview
            _pairing_found[ieee] = cur


def _z2m_publish_set(cfg, code, val, kind):
    cli = _mqtt_client
    if cli is None:
        raise RuntimeError("MQTT not connected")
    payload = {code: int(val)} if kind == "bright" else {code: ("ON" if val else "OFF")}
    # The topic must carry z2m's friendly_name, which for these devices IS the
    # dashboard id. cfg["name"] is the DISPLAY name and may be a settings.json
    # override -- renaming a device from the UI then made every command land on
    # "zigbee2mqtt/<pretty name>/set" and z2m answered "Entity is unknown", while
    # the dashboard's optimistic cache still showed the new value as applied.
    _mqtt_pub(cli, f"zigbee2mqtt/{cfg['id']}/set", json.dumps(payload))


# ---------------------------------------------------------------------------
# Generic MQTT devices -- first-class DIY ESP8266/ESP32 (ESPHome/Tasmota/custom)
# alongside z2m. Configured in mqtt_devices.json (transport="mqtt" is implied):
#
#   {
#     "id": "esp_garage", "name": "Garage", "transport": "mqtt",
#     "category": "switch",                     // dashboard tab (default "switch")
#     "state_topic": "home/esp_garage/state",   // device-level JSON {code: value}
#     "command_topic": "home/esp_garage/set",   // device-level JSON {code: value}
#     "availability_topic": "home/esp_garage/availability",   // LWT, optional
#     "payload_online": "online", "payload_offline": "offline",
#     "controls": [
#       {"kind": "switch", "code": "relay", "label": "Relay",
#        "state_topic": "home/esp_garage/relay/state",        // per-control scalar
#        "command_topic": "home/esp_garage/relay/set",        //   (overrides device-level)
#        "payload_on": "ON", "payload_off": "OFF"},
#       {"kind": "sensor", "code": "temperature", "label": "Temperature", "unit": "degC", "ro": true},
#       {"kind": "sensor", "code": "motion", "label": "Motion", "vtype": "bool", "ro": true}
#     ]
#   }
#
# Two payload shapes are supported, mixable on one device:
#   * device-level state_topic carrying a JSON dict {code: value} (z2m-isomorphic);
#   * per-control state_topic carrying a bare scalar (ESPHome/Tasmota style).
# Conventions for our own firmware: retained state, an availability LWT, and the
# namespace home/<id>/state + home/<id>/set.
# ---------------------------------------------------------------------------
_MQTT_TOPIC_IDX: dict = {}   # topic -> [(dev_id, role, code)]; role: state|avail|ctl


def _mqtt_topic_index() -> dict:
    """Build the exact-topic routing map for every transport=mqtt device.

    One topic can feed SEVERAL cards, so every entry is a LIST of targets: one ESP
    node often fronts more than one device, and all of them take their liveness
    from the node's single `<node>/status`. A one-target-per-topic map silently
    dropped every card but the last one, which then stayed offline while the node
    was plainly publishing."""
    idx = {}

    def add(topic, target):
        idx.setdefault(topic, []).append(target)

    for did, d in list(DEVICES.items()):
        if d.get("transport") != "mqtt":
            continue
        if d.get("state_topic"):
            add(d["state_topic"], (did, "state", None))
        if d.get("availability_topic"):
            add(d["availability_topic"], (did, "avail", None))
        for c in d.get("controls", []):
            if c.get("state_topic"):
                add(c["state_topic"], (did, "ctl", c["code"]))
    return idx


def _mqtt_truthy(rawv, on_str=None) -> bool:
    if isinstance(rawv, bool):
        return rawv
    if isinstance(rawv, (int, float)):
        return bool(rawv)
    s = str(rawv).strip().lower()
    if on_str is not None and s == str(on_str).strip().lower():
        return True
    return s in ("on", "true", "1", "yes", "open", "detected")


def _mqtt_coerce(ctl, rawv):
    """Normalise a raw MQTT value to the type the UI/binding engine expects for
    this control kind: switch/bool -> bool, bright -> int, numeric -> float."""
    kind = ctl.get("kind")
    if kind == "switch" or ctl.get("vtype") == "bool":
        return _mqtt_truthy(rawv, ctl.get("payload_on"))
    if kind == "bright":
        try:
            return int(float(rawv))
        except (TypeError, ValueError):
            return rawv
    if isinstance(rawv, (int, float)):
        return rawv
    try:
        return float(rawv)
    except (TypeError, ValueError):
        return rawv


def _handle_mqtt_state(dev_id, role, code, payload, retain=False):
    """Ingest a generic-MQTT frame into the status cache, then drive the same
    sensor-trigger + server-side inching paths as z2m devices do. `retain` is
    the broker's flag: set only for frames replayed from the retained store."""
    cfg = DEVICES.get(dev_id)
    if not cfg:
        return
    if role == "avail":
        s = payload.decode("utf-8", "replace").strip().lower()
        on = str(cfg.get("payload_online", "online")).strip().lower()
        off = str(cfg.get("payload_offline", "offline")).strip().lower()
        online = (s == on) if s in (on, off) else s not in ("offline", "0", "false", "")
        cur = _status_copy(dev_id, {"online": False, "values": {}, "raw": {}})
        cur["online"] = online
        cur.pop("stale", None)
        _store_status_exact(dev_id, cur)     # no sticky-online grace; mirrors home/state at once
        return
    txt = payload.decode("utf-8", "replace")
    if role == "ctl":
        rawd = {code: txt.strip()}
    else:                                    # device-level JSON {code: value}
        try:
            d = json.loads(txt)
        except Exception:
            return
        if not isinstance(d, dict):
            return
        rawd = d
    values, raw = {}, {}
    for c in cfg["controls"]:
        cc = c["code"]
        if cc not in rawd:
            continue
        v = _mqtt_coerce(c, rawd[cc])
        if c.get("scale") is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
            v = _scale(c, v)   # generic-mqtt sensors honour "scale" too
        values[cc] = v
        raw[cc] = rawd[cc]
    prev = _status_copy(dev_id, {})
    prev_vals = prev.get("values") or {}
    # When a device declares an availability topic, a RETAINED state frame must not
    # assert online: ESPHome publishes its states retained, so on every (re)connect
    # the retained values arrive after the retained LWT "offline" and used to
    # resurrect an unplugged node -- the matrix clock showed hours-old values as live.
    # A LIVE frame is proof of life and heals the stale "offline" a session takeover
    # leaves behind: the broker sends the old session's will AFTER the new birth.
    online = True
    if retain and cfg.get("availability_topic") and "online" in prev:
        online = bool(prev.get("online"))
    _store_status(dev_id, {"online": online,
                           "values": {**prev_vals, **values},
                           "raw": {**(prev.get("raw") or {}), **raw}})
    if values:
        _fire_sensor_triggers(dev_id, cfg, values)
        for c in cfg["controls"]:
            cc = c["code"]
            if c.get("kind") != "switch" or cc not in values:
                continue
            newv = bool(values[cc])
            if cc in prev_vals and bool(prev_vals[cc]) == newv:
                continue
            _inching_hook(dev_id, cc, newv)   # auto-off window on ANY turn-on


def _mqtt_generic_publish_set(cfg, code, val, kind):
    """Send a command to a generic-MQTT device. Per-control command_topic wins
    (bare scalar payload); else the device-level command_topic gets {code: val}."""
    cli = _mqtt_client
    if cli is None:
        raise RuntimeError("MQTT not connected")
    ctl = next((c for c in cfg.get("controls", []) if c["code"] == code), None)
    if ctl is None:
        raise RuntimeError(f"unknown code {code}")
    if kind == "switch":
        scalar = ctl.get("payload_on", "ON") if val else ctl.get("payload_off", "OFF")
    elif kind == "bright":
        scalar = str(int(val))
    else:
        scalar = val if isinstance(val, str) else str(val)
    ctopic = ctl.get("command_topic")
    if ctopic:
        _mqtt_pub(cli, ctopic, str(scalar))
        return
    dtopic = cfg.get("command_topic")
    if dtopic:
        _mqtt_pub(cli, dtopic, json.dumps({code: scalar}))
        return
    raise RuntimeError("no command_topic configured for this device")




def _publish_z2m_get_all(cli, force=False):
    """Ask every controllable z2m device to report its current state now
    (zigbee2mqtt/<name>/get). Reconciles anything changed outside the dashboard
    or whose retained frame went stale; sleeping battery remotes/sensors have no
    writable state and are skipped (a /get would be a no-op).

    A device that reported on its own within Z2M_GET_SKIP_SEC is skipped too:
    mains devices here re-report every 8-20 s, i.e. faster than this loop runs,
    so asking them again is pure noise. On a device whose downlink is weak the
    ask does not merely waste air - it fails, and one ZCL read timing out after
    10 s can dominate the zigbee2mqtt error log while that same device keeps
    publishing its state on its own. force=True is for the
    manual /api/resync, where the caller explicitly wants everyone polled."""
    n = 0
    for did, c in list(DEVICES.items()):
        if c.get("transport") != "z2m":
            continue
        if c.get("_skip_get"):     # EF00-only device: nothing readable to /get
            continue
        codes = [ctl["code"] for ctl in c.get("controls", [])
                 if ctl.get("kind") in ("switch", "bright") and not ctl.get("ro")]
        if not codes:
            continue
        if not force and time.monotonic() - _z2m_seen.get(did, 0.0) < Z2M_GET_SKIP_SEC:
            continue        # it just told us by itself
        asked = _z2m_asked.get(did, 0.0)
        if (not force and asked and _z2m_seen.get(did, 0.0) < asked
                and time.monotonic() - asked < Z2M_GET_BACKOFF_SEC):
            continue        # last ask unanswered (a dead plug cost 120 ZCL timeouts/h)
        try:
            cli.publish(f"zigbee2mqtt/{did}/get",
                        json.dumps({code: "" for code in codes}))
            _z2m_asked[did] = time.monotonic()
            n += 1
        except Exception:
            pass
    return n


def _mqtt_refresh_loop():
    # Keep every surface continuously in sync without any manual refresh:
    #  - an MQTT client with a persistent session: after ITS reconnect
    #    the broker re-sends nothing and an optimistic tile tapped during the
    #    outage stays wrong until the next real state change;
    #  - z2m devices changed outside the dashboard (physical switch, another app)
    #    only report on change, so a missed frame leaves a card stale.
    # Every cycle we (1) ask controllable z2m devices to re-report, then (2) force
    # a full republish of the home/state mirror. Caps any visual desync at one
    # cycle; live changes still propagate instantly on their own.
    while True:
        time.sleep(30)
        cli = _mqtt_client
        if cli is None:
            continue
        try:
            _publish_z2m_get_all(cli)
            _mqtt_last_pub.clear()
            for did in list(DEVICES):
                _mqtt_publish_state(did)
        except Exception as e:
            log.warning(f"[mqtt] refresh cycle failed: {e}")


async def _start_pollers():
    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()   # worker threads bridge async work here
    # in the background: a silent zigbee2mqtt must not hold the API back for 8 s. The main client
    # registers devices from the retained bridge/devices as well; this is the first, direct pull.
    threading.Thread(target=_load_z2m_devices, daemon=True, name="z2m-loader").start()
    _load_status_cache()
    _load_pending()           # before the broker: an early retained ON must not overwrite the file
    _mqtt_start()
    threading.Thread(target=_mqtt_refresh_loop, daemon=True,
                     name="mqtt-refresh").start()
    threading.Thread(target=_status_cache_saver_loop, daemon=True,
                     name="status-cache-saver").start()
    if not SETTINGS_PATH.exists():
        _save_settings()      # first boot only: materialise settings.json
    global _AFTER_BROKER
    _AFTER_BROKER = asyncio.create_task(_after_broker())   # the API is up now; the rest waits for the broker


async def _run_startup_tasks():
    """Plug-in STARTUP_TASKS, started and left running: a coroutine function as a task (a poller),
    a plain callable in a daemon thread. Nothing waits for them - a poll loop is fine - and
    nothing they raise, SystemExit included, stops the core; a death is logged."""
    for task in STARTUP_TASKS:
        name = getattr(task, "__qualname__", repr(task))
        if asyncio.iscoroutinefunction(task):
            async def guarded(fn=task, name=name):
                try:
                    await fn()
                except asyncio.CancelledError:
                    raise
                except BaseException as e:                 # noqa: BLE001 - SystemExit from a plug-in
                    log.error(f"[plugins] task {name} died: {type(e).__name__}: {e}")
            _PLUGIN_TASKS.append(asyncio.create_task(guarded(), name=name))   # held: tasks are kept weakly
            continue

        def run(fn=task, name=name):
            try:
                fn()
            except BaseException as e:                     # noqa: BLE001
                log.error(f"[plugins] start task {name} failed: {type(e).__name__}: {e}")
        threading.Thread(target=run, daemon=True, name=f"plugin-{name}"[:40]).start()


async def _after_broker():
    # Elapsed deadlines fire through MQTT the moment they are replayed, and a
    # QoS-0 publish before CONNACK is silently dropped, so wait for the link.
    if _paho is not None and not await asyncio.to_thread(_mqtt_ready.wait, 15):
        log.info("[mqtt] no CONNACK after 15s, replaying timers anyway")
    def step(fn, what):
        try:
            fn()
        except Exception:
            log.exception(f"[start] {what} failed; the rest of the start goes on")
    # the scheduler first: whatever breaks below, schedules keep running
    threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()
    await _run_startup_tasks()                          # plug-ins first: timers may fire on their devices
    step(_replay_pending_on_boot, "timer replay")      # deferred deadlines (inching + rule `for`)
    step(_rearm_inching_on_boot, "inching re-arm")     # channels on at boot without a timer

    # once more when the retained frames have arrived: the cache read at start may be older
    _t = threading.Timer(20, lambda: step(_rearm_inching_on_boot, "inching re-arm"))
    _t.daemon = True
    _t.start()
    threading.Thread(target=_prune_orphan_state_mirror, daemon=True,
                     name="mirror-prune").start()
    def audit_when_known():
        for _ in range(60):                               # the z2m registry, as for the mirror prune
            if _z2m_loaded:
                break
            time.sleep(1)
        else:
            log.info("[audit] skipped: the zigbee2mqtt device list never arrived")
            return
        step(_audit_orphan_refs, "reference audit")       # loud about silently-dead bindings
    threading.Thread(target=audit_when_known, daemon=True, name="audit").start()


@contextlib.asynccontextmanager
async def _lifespan(_app):
    await _start_pollers()
    yield


app.router.lifespan_context = _lifespan


class ControlBody(BaseModel):
    code: str
    value: object


def _controls_with_dimrange(dev_id, controls):
    """Overlay the per-channel [floor, ceil] window on bright controls for the
    client, without mutating the stored control dicts."""
    dr = _channel_dimrange.get(dev_id) or {}
    out = []
    for c in controls:
        c = {k: v for k, v in c.items() if not k.startswith("_")}   # internal keys stay inside
        if c.get("kind") == "bright" and c.get("code") in dr:
            lo, hi = dr[c["code"]]
            c = {**c, "floor": lo, "ceil": hi}
        out.append(c)
    return out


@app.get("/api/devices")
def list_devices():
    return [{
        "id": d["id"], "name": d["name"], "category": d.get("category"),
        "transport": d.get("transport"),
        "controls": _controls_with_dimrange(d["id"], d.get("controls", [])),
        "room": d.get("room", ""),
        **{k: d.get(k) for k in DEVICE_FIELDS},   # fields a plug-in needs on its cards
        "ieee": d.get("ieee"), "model": d.get("model"),
        "unsupported": bool(d.get("unsupported")),
    } for d in list(DEVICES.values())]


@app.get("/api/devices/{dev_id}/status")
async def status(dev_id: str):
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    # Every transport is push-based (MQTT), so status is always a cache read -- there is nothing left to poll.
    return _status_cache.get(dev_id, {"online": False, "values": {}, "raw": {}})


@app.post("/api/resync")
async def resync():
    """Force every surface back in sync in one shot:
      1. ask each controllable z2m device to report its current state (.../get);
      2. clear the publish-dedup and re-emit the WHOLE home/state/* retained
         mirror (so every retained consumer refreshes).
    The /ws feed then pushes the refreshed cache to every dashboard client on its
    next tick.
    Backend-only primitive on purpose: no UI, the 30 s loop does this by itself."""
    cli = _mqtt_client
    z2m_get = _publish_z2m_get_all(cli, force=True) if cli is not None else 0
    _mqtt_last_pub.clear()
    for did in list(DEVICES):
        try:
            _mqtt_publish_state(did)
        except Exception:
            pass
    return {"ok": True, "z2m_get": z2m_get, "devices": len(DEVICES)}


@app.post("/api/devices/{dev_id}/control")
async def control(dev_id: str, body: ControlBody):
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    cfg = DEVICES[dev_id]
    ctl = cfg["_ctl"].get(body.code)
    if not ctl:
        raise HTTPException(400, f"Unknown code '{body.code}'")
    if ctl.get("ro") or ctl.get("kind") in ("sensor", "sensor_text", "trigger", "trigger_text"):
        raise HTTPException(400, "Read-only")
    if ctl.get("kind") in CONTROL_KINDS:      # a plug-in's stateless channel: no value is stored
        try:
            info = await run_in_threadpool(CONTROL_KINDS[ctl["kind"]], cfg, ctl, body.value)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"send failed: {e}")
        _last_control_ts[dev_id] = time.time()
        return {"ok": True, "code": body.code, "pressed": info}
    val = body.value
    if ctl["kind"] == "switch" and not isinstance(val, bool):
        v = str(val).strip().lower()
        if v in ("1", "true", "on"):
            val = True
        elif v in ("0", "false", "off", ""):
            val = False
        else:
            raise HTTPException(400, f"switch value must be on/off/true/false, got {body.value!r}")
    if ctl["kind"] == "bright":
        try:
            val = _pct_to_raw(ctl, val)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, f"brightness must be a number 0-100, got {val!r}")
    if ctl["kind"] == "color" and cfg.get("transport") == "mqtt":
        # picked "#rrggbb" -> raw 0-255 to each channel command topic
        hexv = str(val).lstrip("#")
        try:
            r = int(hexv[0:2], 16); g = int(hexv[2:4], 16); b = int(hexv[4:6], 16)
        except ValueError:
            raise HTTPException(400, f"bad color '{val}'")
        ch = ctl.get("channels") or {}
        if _mqtt_client is None or not _mqtt_client.is_connected():
            raise HTTPException(503, "broker not reachable")
        for _nm, _cv in (("r", r), ("g", g), ("b", b)):
            _t = ch.get(_nm)
            if _t:
                _mqtt_client.publish(_t, str(_cv))
        val = "#%02x%02x%02x" % (r, g, b)
        cur = _status_copy(dev_id, {"online": True, "values": {}, "raw": {}})
        cur.setdefault("values", {})[body.code] = val
        cur["online"] = True
        _store_status(dev_id, cur)
        _last_control_ts[dev_id] = time.time()
        log.info(f"[control] {cfg['name']}/{body.code}={val} via mqtt(color 3ch)")
        return {"ok": True, "code": body.code, "value": val}
    if cfg.get("transport") in ("z2m", "mqtt") and (_mqtt_client is None or not _mqtt_client.is_connected()):
        raise HTTPException(503, "broker not reachable")
    if cfg.get("transport") == "z2m":
        try:
            _z2m_publish_set(cfg, body.code, val, ctl["kind"])
        except Exception as e:
            raise HTTPException(500, f"z2m error: {e}")
        log.warning(f"[control] {cfg['name']}/{body.code}={val} via z2m")
    elif cfg.get("transport") in ASYNC_CONTROL or cfg.get("transport") in TRANSPORTS:
        try:
            if cfg["transport"] in ASYNC_CONTROL:
                await ASYNC_CONTROL[cfg["transport"]](cfg, body.code, val)
            else:
                await run_in_threadpool(TRANSPORTS[cfg["transport"]], cfg, body.code, val, ctl["kind"])
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"{cfg['transport']}: {e}")
    elif cfg.get("transport") == "mqtt":
        try:
            _mqtt_generic_publish_set(cfg, body.code, val, ctl["kind"])
        except Exception as e:
            raise HTTPException(500, f"mqtt error: {e}")
        log.warning(f"[control] {cfg['name']}/{body.code}={val} via mqtt")
    else:
        raise HTTPException(400,
                            f"unsupported transport '{cfg.get('transport')}'")
    cur = _status_copy(dev_id, {"online": True, "values": {}, "raw": {}})
    cur.setdefault("values", {})[body.code] = val
    cur["online"] = True
    cur.pop("stale", None)
    _store_status(dev_id, cur)
    _last_control_ts[dev_id] = time.time()
    if ctl["kind"] == "switch":
        _cancel_deferred(f"sched|{dev_id}/{body.code}")   # the user decided: a pending schedule retry is void
        _inching_hook(dev_id, body.code, bool(val))   # server-side auto-off window
    return {"ok": True, "code": body.code, "value": val}


@app.get("/api/health")
def health():
    """HTTP is alive since we answered; mqtt = whether paho holds the broker connection now."""
    cli = _mqtt_client
    return {"ok": True, "mqtt": bool(cli is not None and cli.is_connected()), "ts": time.time()}


@app.get("/i18n.js")
def i18n_js():
    return FileResponse(ROOT / "i18n.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


class AutoBody(BaseModel):
    # a field left out keeps its current value: {"inching_sec": 60} must not wipe the schedule
    inching_sec: Optional[int] = None   # total auto-off seconds (0 = off)
    inching_min: Optional[int] = None   # legacy/back-compat; folded into inching_sec
    schedule: Optional[list] = None


@app.get("/api/automation/{dev_id}/{code}")
def get_automation(dev_id: str, code: str):
    a = _autos.get(f"{dev_id}/{code}") or {}
    return {"inching_sec": _inching_sec(dev_id, code),
            "schedule": a.get("schedule") or []}


@app.post("/api/automation/{dev_id}/{code}")
def set_automation(dev_id: str, code: str, body: AutoBody):
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    if (DEVICES[dev_id]["_ctl"].get(code) or {}).get("kind") != "switch":
        raise HTTPException(404, "unknown channel")
    key = f"{dev_id}/{code}"
    cur = _autos.get(key) or {}
    sched = []
    for e in ((cur.get("schedule") or []) if body.schedule is None else body.schedule):
        if not isinstance(e, dict):
            raise HTTPException(400, "a schedule row must be an object {time, days, action}")
        t = _norm_hm(e.get("time"))
        if t is None:
            raise HTTPException(400, f"bad time {e.get('time')!r}: use HH:MM")
        raw_days = e.get("days") or [1, 2, 3, 4, 5, 6, 7]
        try:
            days = sorted({int(d) for d in raw_days})
        except (TypeError, ValueError):
            raise HTTPException(400, "days must be numbers 1..7")
        if not days or any(d < 1 or d > 7 for d in days):
            raise HTTPException(400, "days must be numbers 1..7")
        act = e.get("action", "on")
        if act not in ("on", "off"):
            raise HTTPException(400, "action must be on or off")
        # one row per (time, action): the rule id is built from those two, so two rows
        # differing only in days would share an id -- merge their days instead
        same = next((r for r in sched if r["time"] == t and r["action"] == act), None)
        if same:
            same["days"] = sorted(set(same["days"]) | set(days))
        else:
            sched.append({"time": t, "days": days, "action": act})
    if body.inching_sec is None and body.inching_min is None:
        secs = _inching_sec(dev_id, code)
    else:
        secs = max(0, int(body.inching_sec or 0)) or max(0, int(body.inching_min or 0)) * 60
    if secs > _MAX_DELAY:
        raise HTTPException(400, f"auto-off longer than {_MAX_DELAY // 86400} days")
    with _autos_lock:
        if secs <= 0 and not sched:
            _autos.pop(key, None)
        else:
            _autos[key] = {"inching_sec": secs, "schedule": sched}
        _prune_rules_disabled()
        _save_settings()
    _cancel_inching(key)
    st = _status_copy(dev_id, {})
    if secs > 0 and (st.get("values") or {}).get(code):
        _arm_inching(dev_id, code)
    return {"ok": True}


class RenameBody(BaseModel):
    name: str


@app.post("/api/devices/{dev_id}/channel/{code}/rename")
def rename_channel(dev_id: str, code: str, body: RenameBody):
    """Rename one channel (control) of a multi-gang device. Persisted in
    settings.json -> channel_names so it survives container/image rebuilds.
    Empty name reverts the channel to its auto-generated default label."""
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    name = body.name.strip()
    with _settings_lock:
        dev = DEVICES[dev_id]
        ctrl = next((c for c in dev.get("controls", []) if c.get("code") == code), None)
        if ctrl is None:
            raise HTTPException(404, "unknown channel")
        ov = _channel_names.setdefault(dev_id, {})
        if name:
            ov[code] = name
            ctrl["label"] = name
        else:
            ov.pop(code, None)
            if not ov:
                _channel_names.pop(dev_id, None)
            ctrl["label"] = ctrl.get("_label0", ctrl.get("label", ""))   # the label it registered with
        _save_settings()
    return {"ok": True, "code": code, "label": ctrl["label"]}


class DimRangeBody(BaseModel):
    floor: int
    ceil: int


@app.post("/api/devices/{dev_id}/channel/{code}/dimrange")
def set_dimrange(dev_id: str, code: str, body: DimRangeBody):
    """Set the usable brightness window [floor, ceil] (UI percent 0..100) of one
    dimmer channel. floor>=ceil (or floor 0 & ceil 100) clears it back to the
    default full range. Persisted in settings.json -> channel_dimrange."""
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    lo = max(0, min(100, int(body.floor)))
    hi = max(0, min(100, int(body.ceil)))
    with _settings_lock:
        ctrl = next((c for c in DEVICES[dev_id].get("controls", [])
                     if c.get("code") == code and c.get("kind") == "bright"), None)
        if ctrl is None:
            raise HTTPException(404, "unknown brightness channel")
        dr = _channel_dimrange.setdefault(dev_id, {})
        if lo >= hi or (lo == 0 and hi == 100):     # nothing to constrain -> clear
            dr.pop(code, None)
            if not dr:
                _channel_dimrange.pop(dev_id, None)
            lo, hi = 0, 100
        else:
            dr[code] = [lo, hi]
        _save_settings()
    return {"ok": True, "code": code, "floor": lo, "ceil": hi}


@app.post("/api/devices/{dev_id}/rename")
async def rename_device(dev_id: str, body: RenameBody):
    """Rename a device: in-memory DEVICES and the name overrides in settings.json."""
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "empty name")

    def _do():
        with _settings_lock:
            DEVICES[dev_id]["name"] = name
            _name_overrides[dev_id] = name
            _save_settings()
        return True

    await asyncio.to_thread(_do)
    return {"ok": True, "name": name}


# ---------------------------------------------------------------------------
# Bindings CRUD
# ---------------------------------------------------------------------------
class BindingBody(BaseModel):
    model_config = ConfigDict(extra="allow")   # fields of plug-in actions (BINDING_ACTIONS)
    target: str
    code: Optional[str] = None  # target code
    action: str  # on | off | toggle | bright_up | bright_down
    threshold: Optional[float] = None   # numeric-sensor gestures (below/above)
    hyst: float = 0                     # hysteresis band for numeric thresholds
    conditions: Optional[list] = None   # AND guards {device,code,op,value} checked at fire
    for_sec: Optional[int] = None       # action duration: revert after N s (on<->off)
    retrigger: Optional[str] = None     # restart | extend | ignore (default restart)
    time_window: Optional[dict] = None  # {from:'HH:MM', to:'HH:MM', days:[1..7]}


def _store_binding(src: str, src_code: str, gesture: str, body, entry: dict):
    """What every binding shares, core action or plug-in action: conditions, time window,
    threshold, then the save."""
    # common to every action kind: conditions, time window, sensor threshold
    if body.conditions:
        for c in body.conditions:
            if not isinstance(c, dict):
                raise HTTPException(400, "a condition must be an object {device, code, op, value}")
            op = c.get("op")
            if not (c.get("device") and c.get("code") and op):
                raise HTTPException(400, "each condition needs device, code and op")
            # on/off read truthiness; every other operator needs something to
            # compare against, and an empty value box arrives here as null
            if op not in ("on", "off") and c.get("value") in (None, ""):
                raise HTTPException(400, f"condition '{op}' needs a value")
        entry["conditions"] = body.conditions
    tw = _norm_time_window(body.time_window)
    if tw is not None:
        entry["time_window"] = tw
    if gesture in ("below", "above"):                       # numeric-sensor threshold trigger
        if body.threshold is None:
            raise HTTPException(400, "below/above gesture needs a threshold")
        entry["threshold"] = body.threshold
        entry["hyst"] = body.hyst or 0
        _sensor_arm.pop(f"{src}/{src_code}/{gesture}", None)  # re-seed arm on threshold change
    with _bindings_lock:
        _bindings.setdefault(src, {}).setdefault(src_code, {})[gesture] = entry
        _save_settings()
    return {"ok": True}


@app.put("/api/bindings/{src}/{src_code}/{gesture}")
def put_binding(src: str, src_code: str, gesture: str, body: BindingBody):
    for part, what in ((src, "source"), (src_code, "code"), (gesture, "gesture")):
        _check_topic_name(part, what)          # | would break the rule id b|src|code|gesture
    if src not in DEVICES:
        raise HTTPException(404, "unknown src device")
    if body.action in BINDING_ACTIONS:              # a plug-in's action checks its own fields
        entry = BINDING_ACTIONS[body.action]["validate"](body.model_dump())
        if not (isinstance(entry, dict) and entry.get("action") == body.action and isinstance(entry.get("target"), str)):
            raise HTTPException(502, f"plug-in action {body.action}: validate() must return {{action, target, ...}}")
        return _store_binding(src, src_code, gesture, body, entry)
    if body.target not in DEVICES:
        raise HTTPException(400, "unknown target device")
    if body.action not in ("on", "off", "toggle", "bright_up", "bright_down", "press"):
        raise HTTPException(400, "action must be on|off|toggle|bright_up|bright_down|press")
    if not body.code:
        raise HTTPException(400, "target code required for device action")
    tcfg = DEVICES[body.target]
    tctl = tcfg["_ctl"].get(body.code)
    if not tctl:
        raise HTTPException(400, f"target has no code '{body.code}'")
    if body.action.startswith("bright_") and tctl.get("kind") != "bright":
        raise HTTPException(400, "bright_* action needs a bright target code")
    if (body.action == "press") != (tctl.get("kind") in CONTROL_KINDS):
        raise HTTPException(400, "press is the one action of a stateless channel, and only of it")
    entry = {"target": body.target, "code": body.code, "action": body.action}
    if body.for_sec is not None:
        if body.action not in ("on", "off"):
            raise HTTPException(400, "for-duration only applies to on/off actions")
        if body.for_sec <= 0 or body.for_sec > _MAX_DELAY:
            raise HTTPException(400, f"for-duration must be 1 s .. {_MAX_DELAY // 86400} days")
        entry["for"] = int(body.for_sec)
        pol = (body.retrigger or "restart").lower()
        if pol not in ("restart", "extend", "ignore"):
            raise HTTPException(400, "retrigger must be restart|extend|ignore")
        entry["retrigger"] = pol
    return _store_binding(src, src_code, gesture, body, entry)



# ---------------------------------------------------------------------------
# Unified automation rules -- one read/manage surface over bindings + autos.
# A rule is projected from existing storage with a stable, reversible id:
#   b|<src>|<src_code>|<gesture>      a binding gesture (button / sensor edge)
#   a|<dev>/<code>|sched|<on|off>     a daily-schedule group (one action)
#   a|<dev>/<code>|inch               a channel inching (auto-off after N)
# Creation still goes through the proven PUT /api/bindings + POST /api/automation
# endpoints; this layer adds the unified list, the per-device reverse index, and
# uniform enable/disable + delete.
# ---------------------------------------------------------------------------
_WEEKDAYS_ALL = [1, 2, 3, 4, 5, 6, 7]
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _dname(dev_id: str) -> str:
    return (DEVICES.get(dev_id, {}) or {}).get("name") or BY_ID_NAME.get(dev_id) or dev_id


def _gesture_label(gesture: str) -> str:
    return {"single": "press", "true": "triggered", "false": "cleared",
            "below": "below threshold", "above": "above threshold"}.get(
        gesture, gesture.replace("_", " "))


def _binding_rules():
    out = []
    with _bindings_lock:
        snap = {s: {c: dict(g) for c, g in cs.items()} for s, cs in _bindings.items()}
    for src, codes in snap.items():
        for scode, gestures in codes.items():
            for g, b in gestures.items():
                rid = f"b|{src}|{scode}|{g}"
                tgt = b.get("target")
                when = {"type": "button" if scode == "action" else "sensor",
                        "device": src, "device_name": _dname(src),
                        "code": scode, "gesture": g, "label": _gesture_label(g)}
                if "threshold" in b:
                    when["threshold"] = b["threshold"]
                    when["hyst"] = b.get("hyst", 0)
                if b.get("action") in BINDING_ACTIONS:      # a plug-in's action describes itself
                    try:
                        then = dict(BINDING_ACTIONS[b["action"]]["describe"](b))
                    except Exception as e:
                        log.warning(f"[plugins] describe of {b['action']} failed: {e}")
                        then = {"type": b["action"], "action": b["action"], "title": b["action"]}
                    title = f"{_dname(src)}: {_gesture_label(g)} -> {then.get('title', b['action'])}"
                elif b.get("action") not in _CORE_ACTIONS:
                    # a plug-in's action, but the plug-in is not loaded: keep it recognisable (and
                    # the dashboard keeps it out of the editor), never present it as a device rule
                    then = {"type": "plugin-absent", "action": b.get("action"), "device": tgt,
                            "device_name": tgt, "title": f"{b.get('action')} (plug-in not loaded)"}
                    title = f"{_dname(src)}: {_gesture_label(g)} -> {then['title']}"
                else:
                    then = {"type": "device", "device": tgt, "device_name": _dname(tgt),
                            "code": b.get("code", "state"), "action": b.get("action")}
                    fs = b.get("for")
                    if fs:
                        then["for"] = fs
                    title = (f"{_dname(src)}: {_gesture_label(g)} -> "
                             f"{_dname(tgt)} {b.get('action')}")
                    if fs:
                        title += f" for {fs}s"
                out.append({
                    "id": rid, "kind": "binding", "enabled": _rule_enabled(rid),
                    "when": [when], "conditions": b.get("conditions") or [],
                    "then": [then],
                    "retrigger": b.get("retrigger"),
                    "time_window": b.get("time_window"),
                    "title": title,
                })
    return out


def _auto_rules():
    out = []
    with _autos_lock:
        snap = {k: dict(v) for k, v in _autos.items()}
    for key, a in snap.items():
        if "/" not in key:
            continue
        dev, code = key.split("/", 1)
        # one rule per schedule row (per time) -- discrete, individually managed
        for e in (a.get("schedule") or []):
            t = e.get("time")
            act = e.get("action", "on")
            days = e.get("days") or _WEEKDAYS_ALL
            rid = f"a|{key}|sched|{t}|{act}"
            dlabel = "" if len(days) == 7 else " (" + ",".join(
                _DOW[d - 1] for d in days) + ")"
            out.append({
                "id": rid, "kind": "schedule", "enabled": _rule_enabled(rid),
                "when": [{"type": "time", "times": [{"time": t, "days": days}],
                          "label": t + dlabel}],
                "conditions": [],
                "then": [{"type": "device", "device": dev, "device_name": _dname(dev),
                          "code": code, "action": act}],
                "title": f"{_dname(dev)}: {'on' if act == 'on' else 'off'} at {t}{dlabel}",
            })
        secs = _inching_sec(dev, code)
        if secs > 0:
            rid = f"a|{key}|inch"
            out.append({
                "id": rid, "kind": "inching", "enabled": _rule_enabled(rid),
                "when": [{"type": "state", "device": dev, "device_name": _dname(dev),
                          "code": code, "edge": "on", "label": "turned on"}],
                "conditions": [],
                "then": [{"type": "device", "device": dev, "device_name": _dname(dev),
                          "code": code, "action": "off", "after": secs}],
                "title": f"{_dname(dev)}: auto-off {secs}s after turning on",
            })
    return out


def _project_rules():
    return _binding_rules() + _auto_rules()


def _rule_roles_for_device(rule, dev_id) -> list:
    roles = []
    if any(w.get("device") == dev_id for w in rule["when"]):
        roles.append("trigger")
    if any(c.get("device") == dev_id for c in rule["conditions"]):
        roles.append("condition")
    if any(t.get("device") == dev_id for t in rule["then"]):
        roles.append("action")
    return roles


@app.get("/api/rules")
def get_rules():
    return _project_rules()


@app.get("/api/devices/{dev_id}/rules")
def get_device_rules(dev_id: str):
    """Reverse index: rules referencing this device, with the role(s) it plays."""
    out = []
    for r in _project_rules():
        roles = _rule_roles_for_device(r, dev_id)
        if roles:
            out.append(dict(r, roles=roles))   # the full rule: the dashboard titles it from when/then
    return out


class RuleIdBody(BaseModel):
    id: str
    enabled: Optional[bool] = None


@app.post("/api/rules/enable")
def rule_enable(body: RuleIdBody):
    rid = body.id
    if rid not in {r["id"] for r in _project_rules()}:
        raise HTTPException(404, "no such rule")
    with _settings_lock:
        if body.enabled is False:
            _rules_disabled.add(rid)
        else:
            _rules_disabled.discard(rid)
        _save_settings()
    # disabling an inching rule should drop any armed timer immediately
    if rid.startswith("a|") and rid.endswith("|inch") and body.enabled is False:
        _cancel_inching(rid.split("|", 1)[1].rsplit("|", 1)[0])
    return {"ok": True, "enabled": rid not in _rules_disabled}


@app.post("/api/rules/delete")
def rule_delete(body: RuleIdBody):
    rid = body.id
    parts = rid.split("|")
    if parts[0] == "b" and len(parts) == 4:
        _, src, scode, g = parts
        with _bindings_lock:
            try:
                del _bindings[src][scode][g]
                if not _bindings[src][scode]:
                    del _bindings[src][scode]
                if not _bindings[src]:
                    del _bindings[src]
            except KeyError:
                raise HTTPException(404, "no such rule")
            _rules_disabled.discard(rid)
            _save_settings()
        return {"ok": True}
    if parts[0] == "a" and len(parts) >= 3:
        key = parts[1]
        with _autos_lock:
            a = _autos.get(key)
            if not a:
                raise HTTPException(404, "no such rule")
            if parts[2] == "sched":
                if len(parts) >= 5:                  # a|key|sched|<time>|<action>
                    t, act = parts[3], parts[4]
                    a["schedule"] = [e for e in (a.get("schedule") or [])
                                     if not (e.get("time") == t and e.get("action") == act)]
                else:                                 # legacy a|key|sched|<action>
                    act = parts[3] if len(parts) > 3 else None
                    a["schedule"] = [e for e in (a.get("schedule") or [])
                                     if e.get("action") != act]
            elif parts[2] == "inch":
                a.pop("inching_sec", None)
                a.pop("inching_min", None)
                _cancel_inching(key)
            if not (a.get("schedule") or _inching_sec(*key.split("/", 1)) > 0):
                _autos.pop(key, None)
            _rules_disabled.discard(rid)
            _save_settings()
        return {"ok": True}
    raise HTTPException(400, "bad rule id")


async def _run_then_action(then: dict):
    """Execute one projected `then` action (used by the manual rule-run test)."""
    dev = then.get("device")
    code = then.get("code") or "state"
    action = then.get("action")
    if dev not in DEVICES:
        raise HTTPException(404, f"unknown target device {dev}")
    if action == "toggle":
        cur = (_status_cache.get(dev) or {}).get("values", {}).get(code, False)
        await control(dev, ControlBody(code=code, value=not bool(cur)))
    elif action in ("on", "off"):
        await control(dev, ControlBody(code=code, value=(action == "on")))
    else:
        raise HTTPException(400, f"cannot run action '{action}'")
    return {"target": dev, "code": code, "action": action}


@app.post("/api/rules/run")
async def rule_run(body: RuleIdBody):
    """Manually fire a rule's action(s) to test the wiring -- ignores the rule's
    enabled flag, time window and conditions."""
    rid = body.id
    parts = rid.split("|")
    if parts[0] == "b" and len(parts) == 4:
        _, src, scode, g = parts
        b = (_bindings.get(src, {}).get(scode, {}) or {}).get(g)
        if not b:
            raise HTTPException(404, "no such rule")
        if b.get("action") not in BINDING_ACTIONS and b.get("action") not in _CORE_ACTIONS:
            raise HTTPException(409, f"{b.get('action')}: its plug-in is not loaded")
        if b.get("action") not in BINDING_ACTIONS:
            _need_broker()                               # a plug-in action may not need the broker at all
        try:
            res = await run_in_threadpool(_execute_binding, src, scode, g, True)
        except HTTPException:
            raise
        except Exception as e:
            if b.get("action") in BINDING_ACTIONS:
                raise HTTPException(502, f"{b['action']}: {e}")
            raise
        if res is None:
            raise HTTPException(404, "no such rule")
        return {"ok": True, **(res if isinstance(res, dict) else {})}
    if parts[0] == "a" and len(parts) >= 3:
        _need_broker()
        rule = next((r for r in _project_rules() if r["id"] == rid), None)
        if not rule:
            raise HTTPException(404, "no such rule")
        results = [await _run_then_action(t) for t in rule.get("then", [])]
        return {"ok": True, "results": results}
    raise HTTPException(400, "bad rule id")


# ---------------------------------------------------------------------------
# Favorites (user-curated channel-level pins for the "Favourites" tab)
# ---------------------------------------------------------------------------
class FavoriteBody(BaseModel):
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
        entry = {"device": device, "code": code, "alias": (body.alias or "").strip() or code}
        if idx >= 0:
            _favorites[idx] = entry
        else:
            _favorites.append(entry)
        _save_settings()
    return {"ok": True, "favorite": entry}


@app.delete("/api/favorites/{device}/{code}")
def delete_favorite(device: str, code: str):
    with _favorites_lock:
        idx = _find_fav(device, code)
        if idx < 0:
            raise HTTPException(404, "not in favorites")
        removed = _favorites.pop(idx)
        _save_settings()
    return {"ok": True, "removed": removed}


class ReorderBody(BaseModel):
    order: list[str]  # "device/code" (favourites), room ids or device ids, in the wanted order


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
class RoomBody(BaseModel):
    name: str = ""
    devices: Optional[list[str]] = None      # None = leave the membership alone


def _find_room(rid: str) -> int:
    for i, r in enumerate(_rooms):
        if r.get("id") == rid:
            return i
    return -1


def _clean_room_devices(devices: list) -> list:
    """Validate + dedupe a room's device list, preserving order."""
    unknown = [d for d in devices if d not in list(DEVICES)]
    if unknown:
        raise HTTPException(400, f"unknown devices: {', '.join(map(str, unknown))}")
    seen: set = set()
    out = []
    for d in devices:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


@app.get("/api/rooms")
def get_rooms():
    with _rooms_lock:
        return list(_rooms)


@app.post("/api/rooms")
def create_room(body: RoomBody):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "empty name")
    devices = _clean_room_devices(body.devices or [])
    with _rooms_lock:
        if any(r.get("name") == name for r in _rooms):
            raise HTTPException(409, f"room '{name}' already exists")
        rid = f"r{int(time.time() * 1000)}"
        while _find_room(rid) >= 0:
            rid += "x"
        room = {"id": rid, "name": name, "devices": devices}
        _rooms.append(room)
        _save_settings()
    return {"ok": True, "room": room}


@app.put("/api/rooms/{rid}")
def update_room(rid: str, body: RoomBody):
    with _rooms_lock:
        idx = _find_room(rid)
        if idx < 0:
            raise HTTPException(404, "unknown room")
        room = _rooms[idx]
        name = (body.name or "").strip()
        if name:
            if any(r.get("name") == name and r.get("id") != rid for r in _rooms):
                raise HTTPException(409, f"room '{name}' already exists")
            room["name"] = name
        if body.devices is not None:
            room["devices"] = _clean_room_devices(body.devices)
        _save_settings()
    return {"ok": True, "room": room}


@app.delete("/api/rooms/{rid}")
def delete_room(rid: str):
    with _rooms_lock:
        idx = _find_room(rid)
        if idx < 0:
            raise HTTPException(404, "unknown room")
        removed = _rooms.pop(idx)
        _tile_sizes.pop(f"room:{rid}", None)
        _save_settings()
    return {"ok": True, "removed": removed}


@app.post("/api/rooms/reorder")
def reorder_rooms(body: ReorderBody):
    with _rooms_lock:
        index = {r["id"]: r for r in _rooms}
        new = [index.pop(k) for k in body.order if k in index]
        new.extend(index.values())
        _rooms[:] = new
        _save_settings()
    return {"ok": True}


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
class TileSizeBody(BaseModel):
    device: str
    w: int = 0
    h: int = 0
    col: int = -1


@app.get("/api/tile_sizes")
def get_tile_sizes():
    with _tile_sizes_lock:
        return dict(_tile_sizes)


@app.put("/api/tile_sizes/{scope}")
def put_tile_size(scope: str, body: TileSizeBody):
    """w = columns, h = min-height px, col = pinned column; 0/0/-1 = auto."""
    if body.device not in DEVICES:
        raise HTTPException(404, "unknown device")
    w = max(0, min(int(body.w), 8))
    h = max(0, min(int(body.h), 2000))
    col = max(-1, min(int(body.col), 32))
    with _tile_sizes_lock:
        sizes = _tile_sizes.setdefault(scope, {})
        if not w and not h and col < 0:
            sizes.pop(body.device, None)
            if not sizes:
                _tile_sizes.pop(scope, None)
        else:
            sizes[body.device] = {"w": w, "h": h, "col": col}
        _save_settings()
    return {"ok": True, "scope": scope, "device": body.device,
            "w": w, "h": h, "col": col}


# ---------------------------------------------------------------------------
# Events log
# ---------------------------------------------------------------------------
@app.get("/api/devices/{dev_id}/events")
def get_events(dev_id: str):
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    with _events_lock:
        return {"items": list(_events.get(dev_id, []))}


# --- Zigbee pairing API (used by the dashboard "Add Zigbee" modal) ------------
PERMIT_JOIN_SEC = 254


class ZigbeePairAdd(BaseModel):
    ieee: str
    friendly_name: str = ""
    name: str = ""


@app.post("/api/zigbee/pair/start")
def zigbee_pair_start():
    if _mqtt_client is None:
        raise HTTPException(503, "MQTT bridge not connected")
    if not _Z2M_ENABLED:
        raise HTTPException(503, "no zigbee2mqtt configured (Z2M_SERIAL)")
    if _z2m_bridge_online is False:
        raise HTTPException(503, "zigbee2mqtt is offline: pairing cannot start")
    with _pairing_lock:
        _pairing_found.clear()
        _pairing["active"] = True
        _pairing["until"] = time.time() + PERMIT_JOIN_SEC
    r = _mqtt_client.publish("zigbee2mqtt/bridge/request/permit_join",
                             json.dumps({"value": True, "time": PERMIT_JOIN_SEC}))
    if r.rc != 0 or not _mqtt_client.is_connected():
        with _pairing_lock:
            _pairing["active"] = False
        raise HTTPException(503, "broker not reachable: pairing not started")
    return {"active": True, "remaining": PERMIT_JOIN_SEC}


@app.post("/api/zigbee/pair/stop")
def zigbee_pair_stop():
    with _pairing_lock:
        _pairing["active"] = False
    if _mqtt_client is not None:
        # z2m needs the time field; {"value": false} alone is rejected as invalid
        _mqtt_client.publish("zigbee2mqtt/bridge/request/permit_join",
                             json.dumps({"value": False, "time": 0}))
    return {"active": False}


@app.get("/api/zigbee/pair/status")
def zigbee_pair_status():
    now = time.time()
    with _pairing_lock:
        active = bool(_pairing["active"] and now < _pairing["until"])
        remaining = max(0, int(_pairing["until"] - now)) if active else 0
        found = []
        for ieee, info in _pairing_found.items():
            fn = info.get("friendly_name") or ieee
            reg = DEVICES.get(fn)
            found.append({
                "ieee": ieee, "friendly_name": fn,
                "model": info.get("model"), "vendor": info.get("vendor"),
                "description": info.get("description"),
                "interview": info.get("interview"),
                "registered": bool(reg),
                "name": (reg or {}).get("name", fn),
                "category": (reg or {}).get("category"),
                "unsupported": bool((reg or {}).get("unsupported")),
            })
    # Leftovers from earlier pairing attempts that z2m could not identify. They
    # are already in the mesh but cannot be driven; the modal lists them so a bad
    # pairing can be removed and retried instead of silently piling up.
    seen = {f["friendly_name"] for f in found}
    orphans = [{"id": did, "name": d.get("name", did), "ieee": d.get("ieee")}
               for did, d in list(DEVICES.items())
               if d.get("transport") == "z2m" and d.get("unsupported")
               and did not in seen]
    return {"active": active, "remaining": remaining,
            "found": found, "orphans": orphans}


@app.post("/api/zigbee/pair/add")
def zigbee_pair_add(body: ZigbeePairAdd):
    if _mqtt_client is None:
        raise HTTPException(503, "MQTT bridge not connected")
    if _z2m_bridge_online is False:
        raise HTTPException(503, "zigbee2mqtt is offline")
    cur = (body.friendly_name or body.ieee).strip()
    want = (body.name or "").strip() or cur
    if want != cur:
        _mqtt_client.publish("zigbee2mqtt/bridge/request/device/rename",
                             json.dumps({"from": cur, "to": want}))
    # z2m republishes bridge/devices after the rename: done once the new name is in
    _wait_z2m(lambda: want in DEVICES and (want == cur or cur not in DEVICES))
    dev = DEVICES.get(want)
    if not dev:
        raise HTTPException(500, f"device '{want}' did not register -- check z2m / interview")
    # keep the discovery row (so it shows ok + a Rename button); just track the new name
    with _pairing_lock:
        if body.ieee in _pairing_found:
            _pairing_found[body.ieee]["friendly_name"] = want
    return {"id": want, "name": dev.get("name"), "category": dev.get("category"),
            "unsupported": bool(dev.get("unsupported"))}


class ZigbeeRenameBody(BaseModel):
    id: str
    to: str


def _migrate_device_key(old: str, new: str) -> dict:
    """Move EVERY reference from one device key to another.

    A z2m friendly_name IS the device key here, so renaming a device in z2m
    silently orphans every place that stored the old one -- bindings (both as a
    source and as a target), favourites, automations, disabled-rule ids, name and
    channel-name overrides, plus the in-memory caches. A remote whose binding
    target was renamed just dies unnoticed otherwise. This is the one place that
    knows the whole list."""
    touched = {"bindings": 0, "conditions": 0, "favorites": 0, "rooms": 0,
               "tab_order": 0, "tile_sizes": 0, "automations": 0,
               "rules_disabled": 0, "overrides": 0, "channel_names": 0,
               "timers": 0}

    with _settings_lock:
        # bindings: as a SOURCE (top-level key) and as a TARGET / condition device
        if old in _bindings:
            _bindings[new] = _bindings.pop(old)
            touched["bindings"] += 1
        for codes in _bindings.values():
            for gestures in codes.values():
                for entry in gestures.values():
                    if entry.get("target") == old:
                        entry["target"] = new
                        touched["bindings"] += 1
                    for c in entry.get("conditions") or []:
                        if c.get("device") == old:
                            c["device"] = new
                            touched["conditions"] += 1
        for f in _favorites:
            if f.get("device") == old:
                f["device"] = new
                touched["favorites"] += 1
        for r in _rooms:
            devs = r.get("devices") or []
            if old in devs:
                r["devices"] = [new if x == old else x for x in devs]
                touched["rooms"] += 1
        for tab, order in _tab_order.items():
            if old in order:
                _tab_order[tab] = [new if x == old else x for x in order]
                touched["tab_order"] += 1
        for sizes in _tile_sizes.values():
            if old in sizes:
                sizes[new] = sizes.pop(old)
                touched["tile_sizes"] += 1
        for key in [k for k in _autos if k.split("/", 1)[0] == old]:
            _autos[new + key[len(old):]] = _autos.pop(key)
            touched["automations"] += 1
        for rid in list(_rules_disabled):          # b|<src>|... and a|<dev>/<code>|...
            parts = rid.split("|")
            if len(parts) > 1 and parts[1].split("/", 1)[0] == old:
                parts[1] = new + parts[1][len(old):]
                _rules_disabled.discard(rid)
                _rules_disabled.add("|".join(parts))
                touched["rules_disabled"] += 1
        if old in _name_overrides:
            label = _name_overrides.pop(old)
            # a friendly_name that already equals the display name needs no override
            if label and label != new:
                _name_overrides[new] = label
            touched["overrides"] += 1
        if old in _channel_names:
            _channel_names[new] = _channel_names.pop(old)
            touched["channel_names"] += 1
        _save_settings()

    # in-memory caches keyed by device id
    with _cache_lock:
        if old in _status_cache:
            _status_cache[new] = _status_cache.pop(old)
    for d in (_status_ts, _fail_count, _last_control_ts, _z2m_last_action):
        if old in d:
            d[new] = d.pop(old)
    with _events_lock:
        if old in _events:
            _events[new] = _events.pop(old)
    for ak in [k for k in _sensor_arm if k.split("/", 1)[0] == old]:
        _sensor_arm[new + ak[len(old):]] = _sensor_arm.pop(ak)
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
        if sp.get("device") == old:
            sp["device"] = new
        _schedule_deferred(_rekey(tok), 0, sp["device"], sp["code"], bool(sp.get("value")),
                           sp.get("label", ""), run_at=float(sp.get("run_at") or 0), tries=int(sp.get("tries") or 0))
        touched["timers"] += 1

    # the old retained mirror would otherwise feed its readers forever
    cli = _mqtt_client
    if cli is not None:
        for topic in [t for t in list(_mqtt_last_pub) if f"/{old}/" in t]:
            cli.publish(topic, None, retain=True)
            _mqtt_last_pub.pop(topic, None)
    return touched


@app.post("/api/zigbee/device/rename")
def zigbee_device_rename(body: ZigbeeRenameBody):
    """Rename a z2m device FOR REAL: change its friendly_name in z2m and migrate
    every reference to the new key in one step. `POST /api/devices/{id}/rename`
    only sets a display label; this changes the identity the whole stack uses
    (MQTT topics included), so it is a separate, explicit operation."""
    old, new = body.id, (body.to or "").strip()
    if "settings" in _load_failed:   # z2m would rename, then the migration could not be saved
        raise HTTPException(503, "settings.json was unreadable at start: fix it before renaming")
    if _mqtt_client is None:
        raise HTTPException(503, "MQTT bridge not connected")
    cfg = DEVICES.get(old)
    if not cfg:
        raise HTTPException(404, "unknown device")
    if cfg.get("transport") != "z2m":
        raise HTTPException(400, "only z2m devices have a renameable key; "
                                 "use /api/devices/{id}/rename for a display name")
    if not new:
        raise HTTPException(400, "empty name")
    if new == old:
        raise HTTPException(400, "same name")
    if new in DEVICES:
        raise HTTPException(409, f"'{new}' is already taken")
    _check_topic_name(new)

    box: list = []
    waiter = _paho.Client(_paho.CallbackAPIVersion.VERSION2,
                          client_id=f"home-rename-{secrets.token_hex(3)}")
    waiter.on_message = lambda c, u, m: box.append(m.payload)
    try:
        _mqtt_auth(waiter).connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        waiter.subscribe("zigbee2mqtt/bridge/response/device/rename")
        waiter.loop_start()
        _mqtt_pub(_mqtt_client, "zigbee2mqtt/bridge/request/device/rename",
                  json.dumps({"from": old, "to": new}))
        deadline = time.time() + 10                 # wait for the real answer,
        while not box and time.time() < deadline:   # never a blind sleep
            time.sleep(0.2)
    finally:
        try:
            waiter.loop_stop(); waiter.disconnect()
        except Exception:
            pass
    if not box:
        raise HTTPException(504, "z2m did not answer the rename request")
    try:
        resp = json.loads(box[-1].decode("utf-8", "replace"))
    except Exception:
        resp = {}
    if resp.get("status") != "ok":
        raise HTTPException(502, f"z2m refused: {resp.get('error') or resp}")

    _wait_z2m(lambda: new in DEVICES and old not in DEVICES)   # z2m republishes bridge/devices
    # z2m has renamed it already: move the references in any case, or they point at a
    # name nothing answers to any more
    touched = _migrate_device_key(old, new)
    log.info(f"[rename] {old} -> {new}: {touched}")
    if new not in DEVICES:
        log.warning(f"[rename] '{new}' is not in the device list yet; it appears with the next bridge/devices")
        return {"ok": True, "id": new, "from": old, "migrated": touched, "pending": True}
    _mqtt_publish_state(new)     # seed the mirror under the new key right away
    return {"ok": True, "id": new, "from": old, "migrated": touched}


class ZigbeeRelinkBody(BaseModel):
    stale: str          # key nothing resolves to any more
    into: str           # existing device key to point everything at


@app.post("/api/zigbee/device/relink")
def zigbee_device_relink(body: ZigbeeRelinkBody):
    """Repair path: move references from a key that no longer exists onto a live
    device, WITHOUT touching z2m. Needed because a rename done in the z2m
    frontend (or any other client) changes the device key behind our back and
    silently orphans every binding, favourite and automation --
    z2m also refuses to rename a device back to its IEEE, so there is no way to
    undo it and re-do it through /api/zigbee/device/rename."""
    if "settings" in _load_failed:
        raise HTTPException(503, "settings.json was unreadable at start: fix it before relinking")
    stale, into = body.stale, body.into
    if stale in DEVICES:
        raise HTTPException(400, f"'{stale}' still exists -- nothing to repair; "
                                 "use /api/zigbee/device/rename instead")
    if into not in DEVICES:
        raise HTTPException(404, f"unknown target device '{into}'")
    touched = _migrate_device_key(stale, into)
    _mqtt_publish_state(into)
    log.info(f"[relink] {stale} -> {into}: {touched}")
    return {"ok": True, "id": into, "from": stale, "migrated": touched}


def _audit_orphan_refs() -> dict:
    """Log every stored reference that points at a device key nothing resolves to.
    Silent orphans are the single most expensive failure mode in this stack: a
    remote button whose target was renamed just stops working, with no error
    anywhere. Runs at startup; repair with POST /api/zigbee/device/relink."""
    orphans: dict = {}

    def note(kind, key, where):
        if key and key not in DEVICES:
            orphans.setdefault(key, []).append(f"{kind}:{where}")

    with _settings_lock:
        for src, codes in _bindings.items():
            note("binding-source", src, src)
            for code, gestures in codes.items():
                for g, entry in gestures.items():
                    if entry.get("action") in _CORE_ACTIONS:     # a plug-in action targets an id of its own
                        note("binding-target", entry.get("target"), f"{src}/{code}/{g}")
                    for c in entry.get("conditions") or []:
                        note("condition", c.get("device"), f"{src}/{code}/{g}")
        for f in _favorites:
            note("favorite", f.get("device"), f.get("code", ""))
        for r in _rooms:
            for did in r.get("devices") or []:
                note("room", did, r.get("name") or r.get("id", ""))
        for key in _autos:
            note("automation", key.split("/", 1)[0], key)
        for did in _name_overrides:
            note("name-override", did, did)
        for did in _channel_names:
            note("channel-names", did, did)
    if orphans:
        log.warning("[audit] references to devices that do NOT exist "
              "(fix: POST /api/zigbee/device/relink):")
        for key, places in sorted(orphans.items()):
            log.info(f"[audit]   '{key}' <- {len(places)} ref(s): {', '.join(places[:6])}"
                  + (" ..." if len(places) > 6 else ""))
    else:
        log.info("[audit] no orphaned device references")
    return orphans


class ZigbeeRemove(BaseModel):
    id: str
    force: bool = False


@app.post("/api/zigbee/device/remove")
def zigbee_device_remove(body: ZigbeeRemove):
    """Unpair a z2m device. Needed for failed-interview leftovers: the device has
    to leave the network before it can be paired again cleanly. `force` drops it
    from z2m's database even when the device itself does not answer."""
    if _mqtt_client is None:
        raise HTTPException(503, "MQTT bridge not connected")
    if _z2m_bridge_online is False:
        raise HTTPException(503, "zigbee2mqtt is offline")
    dev = DEVICES.get(body.id)
    if not dev or dev.get("transport") != "z2m":
        raise HTTPException(404, f"no z2m device '{body.id}'")
    r = _mqtt_client.publish("zigbee2mqtt/bridge/request/device/remove",
                             json.dumps({"id": body.id, "force": bool(body.force)}))
    if r.rc != 0 or not _mqtt_client.is_connected():
        raise HTTPException(503, "broker not reachable: nothing removed")
    _wait_z2m(lambda: body.id not in DEVICES)   # z2m republishes its list after the leave
    return {"ok": True, "id": body.id, "removed": body.id not in DEVICES,
            "force": bool(body.force)}


# --- ESPHome / WiFi discovery (the second half of "Add") ---------------------
# ESPHome with an `mqtt:` block publishes retained Home-Assistant-style discovery
# configs to homeassistant/<component>/<node>/<object>/config. Reading those turns
# a WiFi ESP node into a dashboard device without hand-editing mqtt_devices.json,
# which used to be the only way to onboard one.
_HA_DISCOVERY_WILDCARD = "homeassistant/+/+/+/config"


def _ha_key(cfg, *names):
    """HA discovery abbreviates keys (stat_t) but the long form (state_topic) is
    equally legal -- accept either."""
    for n in names:
        v = cfg.get(n)
        if v not in (None, ""):
            return v
    return None


def _esphome_control_from_ha(component, obj, cfg):
    """Map one HA-discovery entry onto our control schema. Returns [] for
    components the dashboard has no widget for (button/select/climate/text)."""
    stat = _ha_key(cfg, "stat_t", "state_topic")
    cmd = _ha_key(cfg, "cmd_t", "command_topic")
    label = _ha_key(cfg, "name") or obj
    if component in ("switch", "light") and cmd:
        out = [{"kind": "switch", "code": obj, "label": label,
                "state_topic": stat, "command_topic": cmd,
                "payload_on": _ha_key(cfg, "pl_on", "payload_on") or "ON",
                "payload_off": _ha_key(cfg, "pl_off", "payload_off") or "OFF"}]
        bcmd = _ha_key(cfg, "bri_cmd_t", "brightness_command_topic")
        if bcmd:
            out.append({"kind": "bright", "code": f"{obj}_bright",
                        "label": f"{label} -- brightness", "min": 0,
                        "max": int(float(_ha_key(cfg, "bri_scl",
                                                 "brightness_scale") or 255)),
                        "state_topic": _ha_key(cfg, "bri_stat_t",
                                               "brightness_state_topic"),
                        "command_topic": bcmd})
        return out
    if component == "number" and cmd:
        try:
            lo, hi = int(float(cfg.get("min", 0))), int(float(cfg.get("max", 100)))
        except (TypeError, ValueError):
            lo, hi = 0, 100
        return [{"kind": "bright", "code": obj, "label": label,
                 "min": lo, "max": hi,
                 "state_topic": stat, "command_topic": cmd}]
    if component == "binary_sensor" and stat:
        return [{"kind": "sensor", "code": obj, "label": label, "ro": True,
                 "vtype": "bool", "scale": 1, "state_topic": stat,
                 "payload_on": _ha_key(cfg, "pl_on", "payload_on") or "ON"}]
    if component == "sensor" and stat:
        return [{"kind": "sensor", "code": obj, "label": label, "ro": True,
                 "unit": _ha_key(cfg, "unit_of_meas", "unit_of_measurement") or "",
                 "scale": 1, "vtype": "num", "state_topic": stat}]
    return []


def _esphome_discover(timeout: float = 6.0) -> dict:
    """Collect the retained HA-discovery configs, grouped per ESPHome node. Uses a
    separate short-lived client, same pattern as _load_z2m_devices."""
    if _paho is None:
        return {}
    import queue as _queue
    box = _queue.Queue()
    cli = _paho.Client(_paho.CallbackAPIVersion.VERSION2,
                       client_id=f"home-esphome-disco-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: box.put((m.topic, m.payload))
    nodes: dict = {}
    try:
        _mqtt_auth(cli).connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        cli.subscribe(_HA_DISCOVERY_WILDCARD)
        cli.loop_start()
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                break
            try:
                topic, payload = box.get(timeout=min(1.5, left))
            except _queue.Empty:
                if box.empty():
                    break
                continue
            parts = topic.split("/")
            if len(parts) != 5:
                continue
            component, node, obj = parts[1], parts[2], parts[3]
            try:
                cfg = json.loads(payload.decode("utf-8", "replace"))
            except Exception:
                continue
            if not isinstance(cfg, dict) or not cfg:
                continue          # empty payload = discovery entry being deleted
            n = nodes.setdefault(node, {"controls": [], "skipped": [],
                                        "availability_topic": None,
                                        # every config topic of this node, so a leftover
                                        # can be cleared without guessing the shape
                                        "config_topics": [],
                                        "model": None, "title": None})
            if topic not in n["config_topics"]:
                n["config_topics"].append(topic)
            n["availability_topic"] = (n["availability_topic"]
                                       or _ha_key(cfg, "avty_t", "availability_topic"))
            dev = cfg.get("dev") or cfg.get("device") or {}
            n["model"] = n["model"] or dev.get("mdl") or dev.get("model")
            n["title"] = n["title"] or dev.get("name")
            try:
                ctrls = _esphome_control_from_ha(component, obj, cfg)
            except Exception as e:                # noqa: BLE001 - someone else's retained JSON
                log.warning(f"[esphome] {topic}: unreadable config skipped ({e})")
                ctrls = []
            if ctrls:
                n["controls"].extend(ctrls)
            else:
                n["skipped"].append(f"{component}/{obj}")
    except Exception as e:
        log.warning(f"[esphome] discovery failed: {e}")
    finally:
        try:
            cli.loop_stop(); cli.disconnect()
        except Exception:
            pass
    return nodes


def _mqtt_node_registered(node: str):
    """Is this ESPHome node already a dashboard device? Matches the explicit
    esphome_node marker, the id, or any topic living under <node>/ -- so the
    hand-written entries that predate this endpoint are recognised too."""
    pref = node + "/"
    for d in list(DEVICES.values()):
        if d.get("transport") != "mqtt":
            continue
        if d.get("esphome_node") == node or d.get("id") == node:
            return d
        for c in d.get("controls", []):
            for key in ("state_topic", "command_topic"):
                if str(c.get(key) or "").startswith(pref):
                    return d
    return None


def _mqtt_devices_upsert(entry: dict):
    """Persist a generic-MQTT device into mqtt_devices.json (replacing same id).
    Written via a temp file so a crash mid-write cannot truncate the registry."""
    with _mqtt_devices_lock:
        cur = []
        if MQTT_DEVICES_PATH.exists():
            try:
                cur = json.load(open(MQTT_DEVICES_PATH)) or []
            except Exception as e:
                raise HTTPException(500, f"mqtt_devices.json unreadable: {e}")
        cur = [e for e in cur if e.get("id") != entry["id"]]
        cur.append({k: v for k, v in entry.items() if not k.startswith("_")})
        _atomic_json_dump(MQTT_DEVICES_PATH, cur, ensure_ascii=False, indent=2)


def _register_mqtt_device(entry: dict) -> dict:
    if any(_own_topic(entry.get(t)) for t in ("state_topic", "command_topic", "availability_topic")):
        raise HTTPException(400, "a device topic under home/state/ or home/cmd/ is the backend's own bus")
    d = dict(entry)
    d["transport"] = "mqtt"
    d["controls"] = _safe_controls(d["id"], d.get("controls"))
    for _c in d["controls"]:
        _c.setdefault("_label0", _c.get("label", ""))
    d["_ctl"] = {c["code"]: c for c in d["controls"]}
    DEVICES[d["id"]] = d
    BY_ID_NAME[d["id"]] = d["name"]
    prev = _status_copy(d["id"], {"values": {}, "raw": {}})
    prev.setdefault("online", False)
    _store_status(d["id"], prev)
    return d


def _mqtt_resubscribe_generic() -> int:
    """Rebuild the exact-topic routing map and subscribe to whatever is new. The
    map is normally built once in _mqtt_on_connect; adding a device from the UI
    must not require a container restart."""
    global _MQTT_TOPIC_IDX
    old = set(_MQTT_TOPIC_IDX)
    _MQTT_TOPIC_IDX = _mqtt_topic_index()
    if _mqtt_client is None:
        return 0
    fresh = [t for t in _MQTT_TOPIC_IDX if t not in old]
    for t in fresh:
        _mqtt_client.subscribe(t)
    return len(fresh)


def _retained_values(topics: list, timeout: float = 4.0) -> dict:
    """Read the retained value of each topic, or None where nothing is retained.
    Used to tell a real node from a leftover: an ESPHome node publishes its LWT
    ("online"/"offline") retained, so NO retained value at all means the node is gone
    and only its discovery configs are still sitting on the broker."""
    if _paho is None or not topics:
        return {}
    import queue as _queue
    box = _queue.Queue()
    cli = _paho.Client(_paho.CallbackAPIVersion.VERSION2, client_id=f"home-lwt-probe-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: box.put((m.topic, m.payload.decode("utf-8", "replace")))
    out: dict = {}
    try:
        _mqtt_auth(cli).connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        for t in set(topics):
            cli.subscribe(t)
        cli.loop_start()
        deadline = time.time() + timeout
        while time.time() < deadline and len(out) < len(set(topics)):
            try:
                t, v = box.get(timeout=max(0.1, deadline - time.time()))
            except _queue.Empty:
                break
            out[t] = v.strip()
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[esphome] lwt probe failed: {e}")
    finally:
        try:
            cli.loop_stop()
            cli.disconnect()
        except Exception:                         # noqa: BLE001
            pass
    return out


@app.get("/api/esphome/discover")
def esphome_discover():
    _need_broker()
    nodes = _esphome_discover()
    lwt = _retained_values([nodes[n].get("availability_topic") for n in nodes
                            if nodes[n].get("availability_topic")])
    out = []
    for node in sorted(nodes):
        n = nodes[node]
        reg = _mqtt_node_registered(node)
        avail = n.get("availability_topic")
        out.append({
            "node": node, "title": n.get("title") or node, "model": n.get("model"),
            "availability_topic": avail,
            # None = neither online nor offline was ever published -> the node itself is
            # gone and these configs are a leftover on the broker.
            "alive": lwt.get(avail) if avail else None,
            "controls": n["controls"], "n_controls": len(n["controls"]),
            "skipped": n["skipped"],
            "registered": bool(reg), "device_id": (reg or {}).get("id"),
        })
    return {"nodes": out}


class EsphomePurge(BaseModel):
    node: str
    # A live node that has since been built with discovery: false keeps its OLD configs
    # on the broker forever -- nothing will ever refresh or delete them. That case needs
    # an explicit decision, hence a flag rather than a silent purge.
    force: bool = False


@app.post("/api/esphome/purge_discovery")
def esphome_purge_discovery(body: EsphomePurge):
    """Clear the retained discovery configs of a node that no longer exists.
    Retained topics live forever, so a renamed or dismantled node keeps offering itself
    in the add dialog until someone publishes an empty payload over each config."""
    node = body.node.strip()
    if not node:
        raise HTTPException(400, "node not given")
    if _mqtt_node_registered(node) and not body.force:
        raise HTTPException(400, f"'{node}' is on the dashboard - remove its card first "
                                 f"(or force, if the configs are stale but the node is alive)")
    nodes = _esphome_discover()
    if node not in nodes:
        raise HTTPException(404, f"'{node}' has no retained configs")
    avail = nodes[node].get("availability_topic")
    if avail and not body.force and (_retained_values([avail]).get(avail) or "").strip():
        raise HTTPException(400, f"'{node}' still publishes its status - it is a live node "
                                 f"(force, if the configs are stale)")
    topics = nodes[node].get("config_topics") or []
    if _mqtt_client is None:
        raise HTTPException(503, "MQTT unavailable")
    for t in topics:
        _mqtt_pub(_mqtt_client, t, None, retain=True)   # empty retained = delete
    log.info(f"[esphome] purged {len(topics)} retained discovery topics of {node}")
    return {"ok": True, "node": node, "purged": len(topics)}


class EsphomeAdd(BaseModel):
    node: str
    id: str = ""
    name: str = ""
    category: str = "switch"
    codes: list = []          # subset of the discovered codes; empty = take all
    # A node that is not answering right now is usually a retired one whose retained
    # configs still sit on the broker; onboarding it yields a card that can never work.
    force: bool = False


@app.post("/api/esphome/add")
def esphome_add(body: EsphomeAdd):
    _need_broker()
    nodes = _esphome_discover()
    n = nodes.get(body.node)
    if not n:
        raise HTTPException(404, f"ESPHome node '{body.node}' not in MQTT discovery "
                                 f"-- check that its yaml has an mqtt: block")
    avail = n.get("availability_topic")
    alive = (_retained_values([avail]).get(avail) or "").strip() if avail else ""
    if alive != "online" and not body.force:
        raise HTTPException(409, f"'{body.node}' is offline - nothing to add "
                                 f"({alive or 'the node never published a status'}). "
                                 f"Power the node on and retry, clean its leftovers off the broker, "
                                 f"or add it with force.")
    controls = n["controls"]
    if body.codes:
        keep = set(body.codes)
        controls = [c for c in controls if c["code"] in keep]
    if not controls:
        raise HTTPException(400, "no usable controls selected")
    did = (body.id or body.node).strip()
    if not _CODE_RE.match(did):
        raise HTTPException(400, "id: 1-64 letters, digits, _ . - (it becomes part of MQTT topics)")
    if not _CAT_RE.match(body.category or "switch"):
        raise HTTPException(400, "category: 1-32 lowercase letters or _")
    prev = _mqtt_node_registered(body.node)
    if did in DEVICES and (prev is None or prev.get("id") != did):
        raise HTTPException(409, f"id '{did}' is already taken by another device")
    entry = {"id": did, "name": (body.name or n.get("title") or did).strip(),
             "transport": "mqtt", "category": body.category or "switch",
             "esphome_node": body.node, "model": n.get("model"),
             "controls": controls}
    if n.get("availability_topic"):
        entry["availability_topic"] = n["availability_topic"]
        entry["payload_online"] = "online"
        entry["payload_offline"] = "offline"
    if any(_own_topic(entry.get(t)) for t in ("state_topic", "command_topic", "availability_topic")):
        raise HTTPException(400, "a device topic under home/state/ or home/cmd/ is the backend's own bus")
    _mqtt_devices_upsert(entry)
    _register_mqtt_device(entry)
    subscribed = _mqtt_resubscribe_generic()
    return {"id": did, "name": entry["name"], "category": entry["category"],
            "controls": [c["code"] for c in controls], "subscribed": subscribed}


class MqttDeviceRemove(BaseModel):
    id: str


@app.post("/api/esphome/remove")
def esphome_remove(body: MqttDeviceRemove):
    """Drop a generic-MQTT device from the registry. Add-without-remove would be a
    trap: a node onboarded with the wrong category or name could not be undone
    from the UI, only by hand-editing mqtt_devices.json."""
    dev = DEVICES.get(body.id)
    if not dev or dev.get("transport") != "mqtt":
        raise HTTPException(404, f"no generic-MQTT device '{body.id}'")
    with _mqtt_devices_lock:
        cur = []
        if MQTT_DEVICES_PATH.exists():
            try:
                cur = json.load(open(MQTT_DEVICES_PATH)) or []
            except Exception as e:
                raise HTTPException(500, f"mqtt_devices.json unreadable: {e}")
        kept = [e for e in cur if e.get("id") != body.id]
        if len(kept) == len(cur):
            raise HTTPException(409, f"'{body.id}' is not in mqtt_devices.json")
        _atomic_json_dump(MQTT_DEVICES_PATH, kept, ensure_ascii=False, indent=2)
    DEVICES.pop(body.id, None)
    BY_ID_NAME.pop(body.id, None)
    with _cache_lock:
        _status_cache.pop(body.id, None)
    # stale topics stay subscribed until the next reconnect; they simply stop
    # resolving in _MQTT_TOPIC_IDX, so nothing is routed to the dead device
    _mqtt_resubscribe_generic()
    return {"ok": True, "id": body.id}


# ---------------------------------------------------------------------------
# WebSocket -- push the shared device-state cache
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    # browsers cannot set headers on a WebSocket, so the token rides in ?token=
    if not _same_origin(websocket.headers) or not _token_ok(websocket.headers, websocket.query_params):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while True:
            payload = {did: get_status(did) for did in list(DEVICES)}
            await websocket.send_json(payload)
            await asyncio.sleep(WS_TICK)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"[ws] closed: {e!r}")


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-cache"})


# -- PWA: installable web app (manifest + service worker + icons) --------------
@app.get("/manifest.json")
def pwa_manifest():
    return FileResponse(ROOT / "manifest.json", media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def pwa_sw():
    # root scope + no-cache so SW updates apply on reload
    return FileResponse(ROOT / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/icon-192.png")
def pwa_icon192():
    return FileResponse(ROOT / "icon-192.png", media_type="image/png")


@app.get("/favicon.ico")
def favicon():
    # browsers that ignore <link rel="icon"> probe this path directly
    return FileResponse(ROOT / ("favicon-32.png" if (ROOT / "favicon-32.png").exists() else "icon-192.png"), media_type="image/png")


@app.get("/icon-maskable-512.png")
def pwa_icon_maskable():
    return FileResponse(ROOT / "icon-maskable-512.png", media_type="image/png")


@app.get("/icon-512.png")
def pwa_icon512():
    return FileResponse(ROOT / "icon-512.png", media_type="image/png")


# ---------------------------------------------------------------------------
# Plug-in loader (docs/plugins.md). Loaded last, so every hook above exists.
# ---------------------------------------------------------------------------
# inside the container always /app/plugins (compose mounts the host's PLUGINS_DIR there); a bare
# `uvicorn app:app` uses plugins/ next to the data. Not read from the environment on purpose: the
# host path in PLUGINS_DIR reaches the container through env_file and would point nowhere.
PLUGINS_DIR = ROOT / "plugins"
_PLUGIN_ASSETS = (".js", ".css", ".json", ".png", ".svg")   # served open, like the shell: no secrets in them
_PLUGIN_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def register_device(dev: dict) -> dict:
    """Add a device a plug-in owns (any transport): channel map, saved rename, cache seed -
    the same path mqtt_devices.json entries take, so the dashboard treats it alike."""
    if not isinstance(dev.get("id"), str) or not _CODE_RE.match(dev["id"]):
        raise ValueError(f"device id {dev.get('id')!r}: 1-64 letters, digits, _ . -")
    if dev["id"] in DEVICES:
        raise ValueError(f"device id {dev['id']!r} is taken (a {DEVICES[dev['id']].get('transport')} device)")
    if dev.get("transport") in _CORE_TRANSPORTS:
        raise ValueError(f"transport {dev.get('transport')!r} belongs to the core")
    if any(_own_topic(dev.get(t)) for t in ("state_topic", "command_topic", "availability_topic")):
        raise ValueError("a device topic under home/state/ or home/cmd/ is the backend's own bus")
    d = dict(dev)
    if not _CAT_RE.match(str(d.get("category") or "other")):
        d["category"] = "other"
    d["controls"] = _safe_controls(d["id"], d.get("controls"))
    for c in d["controls"]:
        c.setdefault("_label0", c.get("label", ""))
    d["_ctl"] = {c["code"]: c for c in d["controls"]}
    d["name"] = _name_overrides.get(d["id"], d.get("name") or d["id"])
    d.setdefault("category", "other")
    DEVICES[d["id"]] = d
    BY_ID_NAME[d["id"]] = d["name"]
    with _cache_lock:
        _status_cache.setdefault(d["id"], {"online": False, "values": {}, "raw": {}})
    return d


_HOOK_DICTS = ("TRANSPORTS", "ASYNC_CONTROL", "CONTROL_KINDS", "SETTINGS_SECTIONS", "BINDING_ACTIONS")
_HOOK_LISTS = ("MQTT_SUBSCRIPTIONS", "MQTT_HANDLERS", "STARTUP_TASKS", "DEVICE_FIELDS")
# names the core owns: a plug-in may not take them over
_CORE_TRANSPORTS = {"z2m", "mqtt"}
_CORE_KINDS = {"switch", "bright", "color", "sensor", "sensor_text", "trigger", "trigger_text",
               "setting_bool", "setting_enum", "setting_int"}
_CORE_ACTIONS = {"on", "off", "toggle", "bright_up", "bright_down", "press"}
_CORE_DEVICE_KEYS = {"id", "name", "category", "transport", "controls", "room", "ieee", "model", "unsupported"}


def _hooks_snapshot() -> dict:
    g = globals()
    with _cache_lock:
        cache = set(_status_cache)
    return {"dicts": {k: dict(g[k]) for k in _HOOK_DICTS}, "lists": {k: list(g[k]) for k in _HOOK_LISTS},
            "open": set(OPEN_PATHS), "devices": dict(DEVICES), "cache": cache,
            "routes": list(app.router.routes), "middleware": list(app.user_middleware)}


def _hooks_restore(snap: dict):
    g = globals()
    for k, v in snap["dicts"].items():
        g[k].clear(); g[k].update(v)
    for k, v in snap["lists"].items():
        g[k][:] = v
    OPEN_PATHS.clear(); OPEN_PATHS.update(snap["open"])
    for did in set(DEVICES) - set(snap["devices"]):
        DEVICES.pop(did, None); BY_ID_NAME.pop(did, None)
    DEVICES.update(snap["devices"])                      # whatever it replaced comes back too
    with _cache_lock:
        for did in set(_status_cache) - snap["cache"]:
            _status_cache.pop(did, None)
    app.router.routes[:] = snap["routes"]                # by identity: an inserted route cannot take a core one along
    app.user_middleware[:] = snap["middleware"]


def _hooks_check(name: str, snap: dict):
    """What a plug-in registered must not reach into the core's own names or bus, nor into what
    an earlier plug-in registered."""
    g = globals()
    for k in _HOOK_DICTS:
        taken = [n for n, v in snap["dicts"][k].items() if g[k].get(n) is not v]
        if taken:
            raise ValueError(f"{k} {sorted(taken)} are registered by an earlier plug-in (or were removed)")
    for did, d in snap["devices"].items():
        if DEVICES.get(did) is not d:
            raise ValueError(f"device {did!r} belongs to the core or an earlier plug-in")
    new = {k: set(g[k]) - set(snap["dicts"][k]) for k in _HOOK_DICTS}
    core_keys = {"_version", "names", "channel_names", "channel_dimrange", "bindings", "favorites", "rooms",
                 "tab_order", "tile_sizes", "automations", "rules_disabled"}
    if new["SETTINGS_SECTIONS"] & core_keys:
        raise ValueError(f"settings sections {sorted(new['SETTINGS_SECTIONS'] & core_keys)} belong to the core")
    if new["TRANSPORTS"] & _CORE_TRANSPORTS or new["ASYNC_CONTROL"] & _CORE_TRANSPORTS:
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
                or pre[0] in ("home", "zigbee2mqtt", "homeassistant", "$SYS"):
            raise ValueError(f"MQTT handler prefix {pre!r}: a topic of the plug-in's own, not the core's bus")
    for t in MQTT_SUBSCRIPTIONS[len(snap["lists"]["MQTT_SUBSCRIPTIONS"]):]:
        if not isinstance(t, str) or not t or "#" in t[:-1] \
                or any(("+" in lv or "#" in lv) and lv not in ("+", "#") for lv in t.split("/")) \
                or t.split("/")[0] in ("home", "zigbee2mqtt", "homeassistant", "$SYS", "#", "+"):
            raise ValueError(f"MQTT subscription {t!r}: a valid filter under a topic of the plug-in's own")
    for r in app.router.routes:
        rp = getattr(r, "path", "")
        if r in snap["routes"] or not rp.startswith("/plugins/"):
            continue
        if not rp.startswith(f"/plugins/{name}/"):
            raise ValueError(f"route {rp!r}: under /plugins/ only /plugins/{name}/...")
        if _OPEN_ASSET.match(rp):
            raise ValueError(f"route {rp!r} looks like a public plug-in file: it would be served without the token")
    for p in OPEN_PATHS - snap["open"]:
        if not isinstance(p, str) or not p.startswith("/") or p.startswith(("/api", "/ws", "/plugins/")):
            raise ValueError(f"open path {p!r}: a page of the plug-in, never the API")


def _load_plugins(pdir: "Path | None" = None, top: str = "yashome_plugins") -> list:
    """Import every <dir>/<name>/ package (alphabetical; names starting with _ are helpers).
    A plug-in registers itself through the hooks when imported. A missing directory = no
    plug-ins; a failing one is logged and skipped - one broken plug-in must not take the
    house down. Returns the ids loaded."""
    import importlib
    import sys as _sys
    import types
    pdir = Path(pdir) if pdir else PLUGINS_DIR
    if not pdir.is_dir():
        return []
    _sys.modules.setdefault("app", _sys.modules[__name__])   # plug-ins do `import app as core`
    pkg = types.ModuleType(top)
    pkg.__path__ = [str(pdir)]
    _sys.modules[top] = pkg
    loaded = []
    for q in sorted(pdir.iterdir()):
        name = q.name
        if name.startswith("_") or not (q / "__init__.py").is_file():
            continue
        if not _PLUGIN_NAME.match(name):
            _PLUGIN_ERRORS[name] = "directory name must be lowercase letters, digits and _ (starting with a letter)"
            log.error(f"[plugins] {name}: {_PLUGIN_ERRORS[name]}")
            continue
        snap = _hooks_snapshot()
        try:
            mod = importlib.import_module(f"{top}.{name}")
            meta = getattr(mod, "PLUGIN", {}) or {}
            if not isinstance(meta, dict):
                raise TypeError("PLUGIN must be a dict like {'name': ..., 'version': ...}")
            _hooks_check(name, snap)
            i18n = {}
            if (q / "i18n.json").is_file():
                i18n = json.loads((q / "i18n.json").read_text(encoding="utf-8"))
                if not isinstance(i18n, dict) or not all(isinstance(v, dict) for v in i18n.values()):
                    raise ValueError("i18n.json must be {lang: {text: translation}}")
        except BaseException as e:              # noqa: BLE001 - SystemExit in a plug-in must not stop us
            if isinstance(e, KeyboardInterrupt):
                raise
            _hooks_restore(snap)                 # what it registered before failing is taken back
            _sys.modules.pop(f"{top}.{name}", None)
            _PLUGIN_ERRORS[name] = f"{type(e).__name__}: {e}"
            log.error(f"[plugins] {name} failed to load: {_PLUGIN_ERRORS[name]}")
            continue
        _PLUGIN_ERRORS.pop(name, None)
        PLUGINS[:] = [m for m in PLUGINS if m["id"] != name]
        PLUGINS.append({"id": name, "name": str(meta.get("name") or name), "version": meta.get("version"),
                        "ui": f"/plugins/{name}/ui.js" if (q / "ui.js").is_file() else None, "i18n": i18n})
        loaded.append(name)
        log.info(f"[plugins] {name} loaded")
    return loaded


@app.get("/api/plugins")
def api_plugins():
    """What the dashboard loads before its first render (scripts, dictionaries) plus the
    plug-ins that failed, for the Settings screen."""
    return {"loaded": PLUGINS, "failed": [{"id": k, "error": v} for k, v in sorted(_PLUGIN_ERRORS.items())],
            "dir": str(PLUGINS_DIR)}


def plugin_asset(name: str, path: str):
    """Public files of a loaded plug-in: ui.js, i18n.json and anything in its static/ folder.
    Nothing else of the package is served (no source, no config). One answer for every miss,
    so the route does not tell which plug-ins exist."""
    miss = HTTPException(404, "not found")
    if not _OPEN_ASSET.match(f"/plugins/{name}/{path}") or not any(m["id"] == name for m in PLUGINS):
        raise miss
    base = (PLUGINS_DIR / name).resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file() or not target.name.lower().endswith(_PLUGIN_ASSETS):
        raise miss                                   # symlinks out of the package, directories
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


_load_plugins()
# registered after the plug-ins: a route a plug-in adds under /plugins/<name>/ is matched first
app.add_api_route("/plugins/{name}/{path:path}", plugin_asset, methods=["GET", "HEAD"])
