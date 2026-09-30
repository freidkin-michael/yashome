"""Zigbee through zigbee2mqtt: the devices of the coordinator's retained bridge/devices become
devices of transport "z2m" (their friendly_name is the id), controlled via zigbee2mqtt/<name>/set,
state from zigbee2mqtt/<name>. Pairing, renaming the key and removing are routes under /api/zigbee/.
The zigbee2mqtt container, its broker account and secret.yaml come from setup/."""
import json
import os
import queue
import secrets
import threading
import time

from fastapi import HTTPException
from pydantic import BaseModel

import app as core

PLUGIN = {"name": "Zigbee", "version": "2.0.0"}
TRANSPORT = "z2m"
NS = "zigbee2mqtt"
log = core.log

_serial = os.environ.get("Z2M_SERIAL")
ENABLED = _serial is None or _serial.strip().strip("\"'").lower() not in ("", "none")

_seen: dict = {}         # friendly_name -> monotonic of its last live frame (retained replays do not count)
_asked: dict = {}        # did -> monotonic of the last /get sent to it
GET_SKIP_SEC = 90
GET_BACKOFF_SEC = 600    # an unanswered /get: a 10 s ZCL timeout on a weak link, so not every cycle
_last_action: dict = {}
_gone: dict = {}         # did -> monotonic of its removal: its kept state is a rejoin's only within GONE_KEEP_SEC
GONE_KEEP_SEC = 600
_loaded = False
_sync_lock = threading.Lock()
_devices_cv = threading.Condition()
_bridge_online = None    # zigbee2mqtt/bridge/state: True / False / None = not heard yet
_pairing = {"active": False, "until": 0.0}
_pairing_found: dict = {}
_pairing_lock = threading.Lock()
PERMIT_JOIN_SEC = 254

_SENSOR_LABELS = {
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


def _cli():
    return core._mqtt_client


def _need_bridge():
    if _cli() is None:
        raise HTTPException(503, "MQTT bridge not connected")
    if _bridge_online is False:
        raise HTTPException(503, "zigbee2mqtt is offline")


def _chan_label(raw: str) -> str:
    """Channel suffix of a multi-gang property -> a short label: '_l2' -> '2', '_left' -> 'left'.
    Dimmers use l1/l2/..., the switch modules in houses left/center/right: both survive."""
    s = raw.lstrip("_")
    if len(s) > 1 and s[0] == "l" and s[1:].isdigit():
        return s[1:]
    return s


def controls_from_exposes(exposes):
    """Channels of a z2m definition. Brightness by prefix (a 2-gang dimmer has brightness_l1/_l2;
    min_/max_brightness are config), read-only top-level binary/numeric exposes are sensors
    (writable ones, access bit 2, are device config), linkquality is signal meta."""
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
                                     "label": _chan_label(str(p)[len("state"):])})
                elif f.get("type") == "numeric" and str(p).startswith("brightness"):
                    sfx = _chan_label(str(p)[len("brightness"):])
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
              and e.get("property") != "linkquality" and not (e.get("access", 0) & 2)):
            p = e["property"]
            label, unit = _SENSOR_LABELS.get(p, (p.replace("_", " ").capitalize(), ""))
            controls.append({"kind": "sensor", "code": p, "label": label,
                             "unit": unit, "scale": 1, "ro": True,
                             "vtype": "bool" if etype == "binary" else "num"})
    return controls


def device_from_z2m(z) -> "dict | None":
    """One entry of bridge/devices as a core device dict; None for the coordinator or a name with
    / (a sub-topic in z2m, two path segments in the REST API) or | (the separator of rule ids).
    A device z2m failed to identify exposes linkquality only: registered anyway (Other, a signal
    readout, `unsupported`), or a fresh pairing would vanish without a way to remove it. An
    EF00-only (manuSpecificTuya) device has nothing readable: never /get it (_skip_get)."""
    fname = z.get("friendly_name")
    if not fname or z.get("type") == "Coordinator":
        return None
    if "/" in fname or "|" in fname:
        log.warning(f"[z2m] '{fname}' skipped: rename it in zigbee2mqtt without '/' or '|'")
        return None
    defn = z.get("definition") or {}
    controls = core._safe_controls(fname, controls_from_exposes(defn.get("exposes")))
    clusters = set()
    for ep in (z.get("endpoints") or {}).values():
        clusters.update((ep.get("clusters") or {}).get("input") or [])
    skip_get = "manuSpecificTuya" in clusters and not clusters & {"genOnOff", "genLevelCtrl"}
    unsupported = not controls
    if unsupported:
        controls = [{"kind": "sensor", "code": "linkquality",
                     "label": "Signal", "unit": "lqi", "scale": 1, "ro": True, "vtype": "num"}]
    has_ctrl = any(c["kind"] in ("switch", "bright") for c in controls)
    has_action = any(c["kind"] == "trigger_text" for c in controls)
    category = "switch" if has_ctrl else ("button" if has_action else "sensor")
    return {"id": fname, "name": fname, "transport": TRANSPORT,
            "ieee": z.get("ieee_address"), "model": defn.get("model"),
            "category": category, "controls": controls, "unsupported": unsupported,
            "_skip_get": skip_get}


def _register(z) -> bool:
    """Add or update one device. A paired device counts as online (battery remotes only talk on
    a press); bridge/devices is no news about THIS device, so its availability stays in force."""
    d = device_from_z2m(z)
    if d is None:
        return False
    did = d["id"]
    cur = core.DEVICES.get(did)
    try:
        if cur is None:
            left = _gone.pop(did, None)
            if left is not None and time.monotonic() - left > GONE_KEEP_SEC:
                with core._cache_lock:          # a different device under an old name: nothing of the old one
                    core._status_cache.pop(did, None)
            core.register_device(d)
        elif cur.get("transport") == TRANSPORT:
            core.update_device(d)
        else:
            log.warning(f"[z2m] '{did}' skipped: the id is taken by a {cur.get('transport')} device; rename one of them")
            return False
    except ValueError as e:
        log.warning(f"[z2m] '{did}' skipped: {e}")
        return False
    prev = core._status_copy(did, {"values": {}, "raw": {}})
    prev["online"] = prev.get("availability", "online") == "online" and _bridge_online is not False
    prev.pop("stale", None)
    core._store_status_exact(did, prev)
    return True


def sync_devices(devs) -> int:
    """Register every device of `devs`, drop the z2m devices no longer in it (left or renamed)."""
    present = set()
    for z in devs:
        if _register(z):
            present.add(z.get("friendly_name"))
    for did in [k for k in _z2m_ids() if k not in present]:
        core.remove_device(did, TRANSPORT, keep_state=True)   # a leave-and-rejoin or a relink keeps the last values
        _gone[did] = time.monotonic()
    return len(present)


def _mark_loaded():
    global _loaded
    _loaded = True
    core.registry_ready("zigbee")


def load_devices():
    """The retained device list once, on a short-lived client: the first direct pull at start.
    The main client applies bridge/devices too; whichever comes first wins."""
    if core._paho is None:
        return
    if not ENABLED:
        log.info("[z2m] no zigbee2mqtt (Z2M_SERIAL empty or none): skipping the device list")
        return
    box = queue.Queue()
    cli = core._paho.Client(core._paho.CallbackAPIVersion.VERSION2, client_id=f"home-z2m-loader-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: box.put((m.topic, m.payload))
    try:
        core._mqtt_auth(cli).connect(core.MQTT_HOST, core.MQTT_PORT, keepalive=10)
        cli.subscribe(f"{NS}/bridge/devices")
        cli.subscribe(f"{NS}/bridge/state")
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
    except queue.Empty:
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
    with _sync_lock:
        if _loaded:
            log.info("[z2m] device list already live from the broker; the loader's copy is not needed")
            return
        n = sync_devices(devs)
        _mark_loaded()
    log.info(f"[z2m] registered {n} devices")


def wait_devices(pred, timeout: float = 8.0) -> bool:
    """Wait until pred() holds after zigbee2mqtt republished its device list (a rename, a leave);
    a bridge that is down will not answer, so no wait then."""
    end = time.time() + timeout
    with _devices_cv:
        while not pred() and _bridge_online is not False:
            left = end - time.time()
            if left <= 0:
                break
            _devices_cv.wait(left)
    return pred()


def on_message(parts, payload, retain):
    if len(parts) == 3 and parts[2] == "availability":
        on_availability(parts[1], payload)
    elif len(parts) >= 2 and parts[1] == "bridge":
        on_bridge(parts, payload)
    else:
        on_state(parts, payload, retain)


def _ingest(name, cfg, d, retain):
    """State values of a frame into the cache. A frame means alive, unless availability said
    offline and this is only the retained copy, or zigbee2mqtt itself is down. Auto-off windows
    arm on OFF<->ON edges of ANY turn-on; a retained ON after our downtime arms one too."""
    values, raw = {}, {}
    for c in cfg["controls"]:
        code = c["code"]
        if c["kind"] == "trigger_text" or code not in d:
            continue
        rawv = d[code]
        values[code] = ((rawv == "ON") if isinstance(rawv, str) else bool(rawv)) if c["kind"] == "switch" else rawv
        raw[code] = rawv
    if not values:
        return
    prev = core._status_copy(name, {})
    prev_vals = prev.get("values") or {}
    avail = prev.get("availability")
    if (retain and avail == "offline") or _bridge_online is False:
        online = False
    else:
        online, avail = True, (avail and "online")
    st = {"online": online, "values": {**prev_vals, **values}, "raw": {**(prev.get("raw") or {}), **raw}}
    if avail:
        st["availability"] = avail
    (core._store_status_exact if not online else core._store_status)(name, st)
    if not retain:
        core._fire_sensor_triggers(name, cfg, values)
    for c in cfg["controls"]:
        code = c["code"]
        if c.get("kind") != "switch" or code not in values:
            continue
        newv = bool(values[code])
        if code in prev_vals and bool(prev_vals[code]) == newv:
            continue
        if not retain:
            core._inching_hook(name, code, newv)
        elif newv and not core._deferred_active(f"inch|{name}/{code}"):
            core._inching_hook(name, code, True)


def on_state(parts, payload, retain=False):
    """zigbee2mqtt/<friendly_name>: state into the cache; a button `action` into the rule engine
    (src=friendly_name, code="action", gesture=<action>). Only names zigbee2mqtt registered: anyone
    with its account could otherwise invent devices or pose as a generic one and fire its rules.
    An action comes empty on republishes, stale when retained, and twice for one press."""
    if len(parts) != 2 or parts[1] == "bridge":
        return
    name = parts[1]
    try:
        d = core._json_state(payload.decode("utf-8", "replace"))
    except Exception:
        return
    cfg = core.DEVICES.get(name)
    if not isinstance(d, dict) or (cfg or {}).get("transport") != TRANSPORT:
        return
    if not retain:
        _seen[name] = time.monotonic()
    _ingest(name, cfg, d, retain)
    action = d.get("action")
    if not action or retain:
        return
    now = time.time()
    last = _last_action.get(name)
    if last and last[0] == action and now - last[1] < 0.8:
        return
    _last_action[name] = (action, now)
    with core._events_lock:
        core._events[name].append({"ts": now, "code": "action", "value": action, "t": int(now)})
    cur = core._status_copy(name, {"online": True, "values": {}, "raw": {}})
    cur["online"] = True
    cur["last_event_ts"] = now
    cur["last_event"] = {"code": "action", "value": action}
    core._store_status(name, cur)
    core._bg.submit(core._safe_execute_binding, name, "action", str(action))


def on_availability(name: str, payload: bytes):
    """z2m 2.x: {"state": "online"|"offline"}; older: a bare word. zigbee2mqtt saying a device is
    gone is not a guess: stored without the grace period."""
    if (core.DEVICES.get(name) or {}).get("transport") != TRANSPORT:
        return
    raw = payload.decode("utf-8", "replace").strip()
    try:
        raw = (json.loads(raw) or {}).get("state", raw) if raw.startswith("{") else raw
    except ValueError:
        pass
    cur = core._status_copy(name, {"values": {}, "raw": {}})
    cur["online"] = str(raw).lower() == "online"
    cur["availability"] = "online" if cur["online"] else "offline"
    cur.pop("stale", None)
    core._store_status_exact(name, cur)


def _z2m_ids():
    """The devices this plug-in registered (another plug-in may use transport z2m too)."""
    return [k for k, d in list(core.DEVICES.items())
            if d.get("transport") == TRANSPORT and core._DEVICE_OWNER.get(k) == "zigbee"]


def _on_bridge_state(raw):
    """Back after an outage: bridge/devices is not replayed to a running subscriber, so bring back
    what the devices' own availability does not contradict. Down (every time, not only on the
    change): none of its devices can be reached, whatever they said last."""
    global _bridge_online
    try:
        st = (json.loads(raw) or {}).get("state") if raw.startswith("{") else raw
    except ValueError:
        st = raw
    was = _bridge_online
    _bridge_online = str(st).lower() == "online"
    if _bridge_online and was is False:
        for did in _z2m_ids():
            cur = core._status_copy(did, {"values": {}, "raw": {}})
            if not cur.get("online") and cur.get("availability", "online") == "online":
                cur["online"] = True
                core._store_status_exact(did, cur)
        log.info("[z2m] zigbee2mqtt is back online")
    if _bridge_online is False:
        for did in _z2m_ids():
            cur = core._status_copy(did, {"values": {}, "raw": {}})
            if cur.get("online"):
                cur["online"] = False
                core._store_status_exact(did, cur)
        log.warning("[z2m] zigbee2mqtt reports offline: its devices are marked offline")
    with _devices_cv:
        _devices_cv.notify_all()


def _on_join_event(data):
    """bridge/event device_joined / device_interview -> the pairing modal's list; a completed
    interview is never downgraded by a later event."""
    d = data.get("data") or {}
    ieee = d.get("ieee_address") or d.get("friendly_name")
    if not ieee or data.get("type") not in ("device_joined", "device_interview"):
        return
    defn = d.get("definition") or {}
    status = d.get("status")
    interview = "done" if status == "successful" else "failed" if status == "failed" else "pending"
    with _pairing_lock:
        cur = _pairing_found.get(ieee, {})
        cur.update({
            "ieee": ieee,
            "friendly_name": d.get("friendly_name") or cur.get("friendly_name") or ieee,
            "model": defn.get("model") or cur.get("model"),
            "vendor": defn.get("vendor") or cur.get("vendor"),
            "description": defn.get("description") or cur.get("description"),
        })
        if cur.get("interview") != "done":
            cur["interview"] = interview
        _pairing_found[ieee] = cur


def on_bridge(parts, payload):
    """Bridge traffic: zigbee2mqtt's own state, the live device list, pairing joins."""
    topic = parts[1] if len(parts) > 1 else ""
    sub = parts[2] if len(parts) > 2 else ""
    raw = payload.decode("utf-8", "replace").strip()
    if topic == "bridge" and sub == "state":
        _on_bridge_state(raw)
        return
    try:
        data = json.loads(raw)
    except Exception:
        return
    if topic == "bridge" and sub == "devices" and isinstance(data, list):
        with _sync_lock:
            sync_devices(data)
            _mark_loaded()
        with _devices_cv:
            _devices_cv.notify_all()
    elif topic == "bridge" and sub == "event" and isinstance(data, dict):
        _on_join_event(data)


def publish_set(cfg, code, val, kind):
    """The topic carries the friendly_name, which IS the id - never cfg["name"], a display label."""
    cli = _cli()
    if cli is None or not cli.is_connected():
        raise HTTPException(503, "broker not reachable")
    payload = {code: int(val)} if kind == "bright" else {code: ("ON" if val else "OFF")}
    core._mqtt_pub(cli, f"{NS}/{cfg['id']}/set", json.dumps(payload))
    log.info(f"[control] {cfg['name']}/{code}={val} via z2m")


def binding(src_id, code, gesture, b):
    """A rule's target in zigbee2mqtt: standard-cluster commands the device does itself (a relative
    brightness step on the gang's own endpoint, TOGGLE). None = the core's generic path: an
    EF00-only device has no genOnOff/genLevelCtrl and gets absolute values from the cached level."""
    target = b["target"]
    cli = _cli()
    if (core.DEVICES.get(target) or {}).get("_skip_get") or cli is None:
        return None
    z_code = b.get("code") or "state"
    sfx = "_" + core._chan_of(z_code) if core._chan_of(z_code) else ""
    payload = {
        "on": {z_code: "ON"},
        "off": {z_code: "OFF"},
        "toggle": {z_code: "TOGGLE"},
        "bright_up": {f"brightness_step_onoff{sfx}": 25},
        "bright_down": {f"brightness_step_onoff{sfx}": -25},
    }.get(b.get("action"))
    if payload is None:
        return None
    try:
        core._mqtt_pub(cli, f"{NS}/{target}/set", json.dumps(payload))
    except Exception as e:
        log.warning(f"[binding] z2m publish failed {target}: {e}")
        return {"target": target, "code": z_code}
    if b.get("action") in ("on", "off", "toggle"):
        core._cancel_deferred(f"sched|{target}/{z_code}")
    log.info(f"[binding] {src_id}/{code}={gesture} -> z2m {target} {payload}")
    return {"target": target, "code": z_code, "value": payload}


def get_all(cli, force=False) -> int:
    """Ask the controllable devices to report now (zigbee2mqtt/<name>/get). Skipped: sleeping
    remotes, EF00-only devices, a device that reported by itself within GET_SKIP_SEC (mains devices
    re-report every 8-20 s), one whose last ask is unanswered (for GET_BACKOFF_SEC). force
    (/api/resync) asks everyone."""
    n = 0
    for did, c in list(core.DEVICES.items()):
        if c.get("transport") != TRANSPORT or c.get("_skip_get"):
            continue
        codes = [ctl["code"] for ctl in c.get("controls", []) if ctl.get("kind") in ("switch", "bright") and not ctl.get("ro")]
        if not codes:
            continue
        seen, asked = _seen.get(did), _asked.get(did)      # None = never (monotonic may be small after boot)
        if not force and seen is not None and time.monotonic() - seen < GET_SKIP_SEC:
            continue
        if not force and asked is not None and (seen is None or seen < asked) \
                and time.monotonic() - asked < GET_BACKOFF_SEC:
            continue
        try:
            cli.publish(f"{NS}/{did}/get", json.dumps({code: "" for code in codes}))
            _asked[did] = time.monotonic()
            n += 1
        except Exception:
            pass
    return n


def on_rename(old, new):
    for d in (_seen, _asked, _last_action):
        if old in d:
            d.setdefault(new, d.pop(old))


class PairAdd(BaseModel):
    ieee: str
    friendly_name: str = ""
    name: str = ""


class RenameBody(BaseModel):
    id: str
    to: str


class RemoveBody(BaseModel):
    id: str
    force: bool = False


@core.app.post("/api/zigbee/pair/start")
def pair_start():
    if _cli() is None:
        raise HTTPException(503, "MQTT bridge not connected")
    if not ENABLED:
        raise HTTPException(503, "no zigbee2mqtt configured (Z2M_SERIAL)")
    if _bridge_online is False:
        raise HTTPException(503, "zigbee2mqtt is offline: pairing cannot start")
    with _pairing_lock:
        _pairing_found.clear()
        _pairing["active"] = True
        _pairing["until"] = time.time() + PERMIT_JOIN_SEC
    r = _cli().publish(f"{NS}/bridge/request/permit_join", json.dumps({"value": True, "time": PERMIT_JOIN_SEC}))
    if r.rc != 0 or not _cli().is_connected():
        with _pairing_lock:
            _pairing["active"] = False
        raise HTTPException(503, "broker not reachable: pairing not started")
    return {"active": True, "remaining": PERMIT_JOIN_SEC}


@core.app.post("/api/zigbee/pair/stop")
def pair_stop():
    """z2m needs the time field: {"value": false} alone is rejected as invalid."""
    with _pairing_lock:
        _pairing["active"] = False
    if _cli() is not None:
        _cli().publish(f"{NS}/bridge/request/permit_join", json.dumps({"value": False, "time": 0}))
    return {"active": False}


@core.app.get("/api/zigbee/pair/status")
def pair_status():
    """What joined in this window, plus leftovers of earlier attempts z2m could not identify
    (orphans): listed so a bad pairing can be removed and retried."""
    now = time.time()
    with _pairing_lock:
        active = bool(_pairing["active"] and now < _pairing["until"])
        remaining = max(0, int(_pairing["until"] - now)) if active else 0
        found = []
        for ieee, info in _pairing_found.items():
            fn = info.get("friendly_name") or ieee
            reg = core.DEVICES.get(fn)
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
    seen = {f["friendly_name"] for f in found}
    orphans = [{"id": did, "name": d.get("name", did), "ieee": d.get("ieee")}
               for did, d in list(core.DEVICES.items())
               if d.get("transport") == TRANSPORT and d.get("unsupported") and did not in seen]
    return {"active": active, "remaining": remaining, "found": found, "orphans": orphans}


@core.app.post("/api/zigbee/pair/add")
def pair_add(body: PairAdd):
    """Take a joined device, optionally under a new name: that becomes the id and a topic level,
    so it is checked like a rename and never one that is taken."""
    _need_bridge()
    cur = (body.friendly_name or body.ieee).strip()
    want = (body.name or "").strip() or cur
    if want != cur:
        core._check_topic_name(want)
        if want in core.DEVICES:
            raise HTTPException(409, f"'{want}' is already taken by another device")
        _cli().publish(f"{NS}/bridge/request/device/rename", json.dumps({"from": cur, "to": want}))
    done = wait_devices(lambda: want in core.DEVICES and (want == cur or cur not in core.DEVICES))
    dev = core.DEVICES.get(want)
    if not done or not dev or dev.get("transport") != TRANSPORT:
        raise HTTPException(504 if want != cur else 500, f"device '{want}' did not register -- check z2m / interview")
    with _pairing_lock:
        if body.ieee in _pairing_found:
            _pairing_found[body.ieee]["friendly_name"] = want
    return {"id": want, "name": dev.get("name"), "category": dev.get("category"),
            "unsupported": bool(dev.get("unsupported"))}


def _ask_rename(old, new):
    """The rename request, sent only once the broker confirmed the subscription for the answer
    (sent earlier, z2m's answer could beat it: a 504 while z2m had renamed the device)."""
    box: list = []
    waiter = core._paho.Client(core._paho.CallbackAPIVersion.VERSION2, client_id=f"home-rename-{secrets.token_hex(3)}")
    waiter.on_message = lambda c, u, m: box.append(m.payload)
    subacked = threading.Event()
    waiter.on_subscribe = lambda *a: subacked.set()
    try:
        core._mqtt_auth(waiter).connect(core.MQTT_HOST, core.MQTT_PORT, keepalive=10)
        waiter.subscribe(f"{NS}/bridge/response/device/rename")
        waiter.loop_start()
        if not subacked.wait(5):
            raise HTTPException(504, "the broker did not confirm the subscription for the rename answer")
        core._mqtt_pub(_cli(), f"{NS}/bridge/request/device/rename", json.dumps({"from": old, "to": new}))
        deadline = time.time() + 10
        while not box and time.time() < deadline:
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


@core.app.post("/api/zigbee/device/rename")
def device_rename(body: RenameBody):
    """Rename FOR REAL: the friendly_name in zigbee2mqtt (the id and every MQTT topic), then every
    reference moves with it, whatever the device list says by then (z2m renamed it already).
    POST /api/devices/{id}/rename only sets a display label."""
    old, new = body.id, (body.to or "").strip()
    if "settings" in core._load_failed:
        raise HTTPException(503, "settings.json was unreadable at start: fix it before renaming")
    if _cli() is None:
        raise HTTPException(503, "MQTT bridge not connected")
    cfg = core.DEVICES.get(old)
    if not cfg:
        raise HTTPException(404, "unknown device")
    if cfg.get("transport") != TRANSPORT:
        raise HTTPException(400, "only z2m devices have a renameable key; "
                                 "use /api/devices/{id}/rename for a display name")
    if not new:
        raise HTTPException(400, "empty name")
    if new == old:
        raise HTTPException(400, "same name")
    if new in core.DEVICES:
        raise HTTPException(409, f"'{new}' is already taken")
    core._check_topic_name(new)
    before = core._status_copy(old, {})
    _gone.pop(new, None)                    # the new name's own past must not wipe the state moved to it
    with core._cache_lock:
        core._status_cache.pop(new, None)   # nor may a long-gone device of that name lend it its values
    _ask_rename(old, new)
    wait_devices(lambda: new in core.DEVICES and old not in core.DEVICES)
    touched = core.migrate_device_key(old, new)
    if before.get("values"):
        core._store_status_exact(new, before)          # the renamed device's own state, not a leftover of the name
    log.info(f"[rename] {old} -> {new}: {touched}")
    if new not in core.DEVICES:
        log.warning(f"[rename] '{new}' is not in the device list yet; it appears with the next bridge/devices")
        return {"ok": True, "id": new, "from": old, "migrated": touched, "pending": True}
    core._mqtt_publish_state(new)
    return {"ok": True, "id": new, "from": old, "migrated": touched}


@core.app.post("/api/zigbee/device/remove")
def device_remove(body: RemoveBody):
    """Unpair (a failed-interview leftover must leave before it can pair cleanly); force drops it
    from z2m's database even when the device does not answer."""
    _need_bridge()
    dev = core.DEVICES.get(body.id)
    if not dev or dev.get("transport") != TRANSPORT:
        raise HTTPException(404, f"no z2m device '{body.id}'")
    r = _cli().publish(f"{NS}/bridge/request/device/remove", json.dumps({"id": body.id, "force": bool(body.force)}))
    if r.rc != 0 or not _cli().is_connected():
        raise HTTPException(503, "broker not reachable: nothing removed")
    wait_devices(lambda: body.id not in core.DEVICES)
    return {"ok": True, "id": body.id, "removed": body.id not in core.DEVICES, "force": bool(body.force)}


core.MQTT_NAMESPACES.add(NS)
# in this order: the broker replays retained messages in subscription order, registry first
core.MQTT_SUBSCRIPTIONS.extend([f"{NS}/bridge/state", f"{NS}/bridge/devices", f"{NS}/bridge/event",
                                f"{NS}/+/availability", f"{NS}/+"])
core.MQTT_HANDLERS.append(((NS,), on_message))
core.TRANSPORTS[TRANSPORT] = publish_set
core.TRANSPORT_BINDINGS[TRANSPORT] = binding
core.RESYNC_HOOKS.append(get_all)
core.ON_RENAME.append(on_rename)
core.STARTUP_TASKS.append(load_devices)
if ENABLED:
    core.registry_pending("zigbee")
