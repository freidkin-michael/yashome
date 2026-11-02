"""Zigbee through zigbee2mqtt: the devices of the coordinator's retained bridge/devices become
devices of transport "z2m" (their friendly_name is the id), controlled via zigbee2mqtt/<name>/set,
state from zigbee2mqtt/<name>. Pairing, renaming the key and removing are routes under /api/zigbee/.
The zigbee2mqtt container, its broker account and secret.yaml come from setup/."""
import asyncio
import json
import os
import queue
import pathlib
import re
import secrets
import socket
import threading
import time

import urllib.error
import urllib.parse
import urllib.request

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import Field

import app as core

PLUGIN = {"name": "Zigbee", "version": "2.0.0", "icon": "network"}
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
_gone_dev: dict = {}     # did -> {"dev": its record, "left": monotonic}, until it is back or gone for good (DEVICE_REMOVED)
_list_suspect = False    # the last device list was empty or lost half: nobody is told a device is gone meanwhile
GONE_KEEP_SEC = 600
GONE_PATH = core.plugin_data("zigbee") / "gone.json"     # the gone records with wall-clock leave times: a restart keeps them
_gone_saved = None
_loaded = False
_sync_lock = threading.Lock()
_devices_cv = threading.Condition()
_bridge_online = None    # zigbee2mqtt/bridge/state: True / False / None = not heard yet
_bridge_since = None     # when it last changed (wall clock)
_bridge_info: dict = {}  # zigbee2mqtt/bridge/info: coordinator, network, serial (the gateway page)
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
    return core.mqtt_client


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
    controls = core.safe_controls(fname, controls_from_exposes(defn.get("exposes")))
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
            "ieee": z.get("ieee_address"), "model": defn.get("model"), "vendor": defn.get("vendor"),
            "description": defn.get("description"),
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
            _gone_dev.pop(did, None)            # back: not gone, whatever an older list or the last run said
            if left is not None and time.monotonic() - left > GONE_KEEP_SEC:
                with core.cache_lock:          # a different device under an old name: nothing of the old one
                    core.status_cache.pop(did, None)
            core.register_device(d)
        elif cur.get("transport") == TRANSPORT:
            core.update_device(d)
        else:
            log.warning(f"[z2m] '{did}' skipped: the id is taken by a {cur.get('transport')} device; rename one of them")
            return False
    except ValueError as e:
        log.warning(f"[z2m] '{did}' skipped: {e}")
        return False
    prev = core.status_copy(did, {"values": {}, "raw": {}})
    if prev.get("waiting") and _bridge_online is not False:   # since the start: its own word decides
        core.mqtt_publish_state(did)   # the mirror says so too (not yesterday's retained online)
        return True
    prev["online"] = prev.get("availability", "online") == "online" and _bridge_online is not False
    prev.pop("stale", None)
    core.store_status_exact(did, prev)
    return True


def sync_devices(devs) -> int:
    """Register every device of `devs`, drop the z2m devices no longer in it (left or renamed). Called under _sync_lock;
    the caller sweeps after letting it go (_sweep_gone tells the plug-ins)."""
    global _list_suspect
    present = set()
    for z in devs:
        if _register(z):
            present.add(z.get("friendly_name"))
    had = _z2m_ids()
    missing = [k for k in had if k not in present]
    known = set(had) | set(_gone_dev)                     # a record still waiting (this run's or the last's) is a device we know
    lacks = [k for k in known if k not in present]
    _list_suspect = bool(lacks) and (not present or len(lacks) * 2 > len(known))   # an empty or half list: a wrong volume, a restore
    if _list_suspect:
        log.warning(f"[z2m] the device list lacks {len(lacks)} of {len(known)} known devices: nobody is told they are gone")
    now = time.monotonic()
    for did in missing:
        if not _list_suspect:
            _gone_dev[did] = {"dev": dict(core.DEVICES.get(did) or {"id": did, "transport": TRANSPORT}), "left": now}
        core.remove_device(did, TRANSPORT, keep_state=True)   # a leave-and-rejoin or a relink keeps the last values
        _gone[did] = now
    _save_gone()
    return len(present)


def _save_gone():
    """Write the gone records when they changed (under _sync_lock): {did: {"left": epoch, "dev": record}}."""
    global _gone_saved
    mono, wall = time.monotonic(), time.time()
    snap = {did: {"left": round(wall - (mono - g["left"]), 1), "dev": g["dev"]} for did, g in _gone_dev.items()}
    if _gone_saved is not None and set(snap) == _gone_saved:
        return
    try:
        core.atomic_json_dump(GONE_PATH, snap, ensure_ascii=False, default=str)
        _gone_saved = set(snap)
    except OSError as e:
        log.warning(f"[z2m] {GONE_PATH.name} not saved: {e}")


def _load_gone():
    """At start: the gone records of the last run, their time counted on. Only _gone_dev: a device in the first list is
    back (no state wiped), and the sweep tells the plug-ins only after a list that is not suspect."""
    global _gone_saved
    try:
        snap = json.loads(GONE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        log.warning(f"[z2m] {GONE_PATH.name} unreadable, ignored: {e}")
        return
    if not isinstance(snap, dict):
        log.warning(f"[z2m] {GONE_PATH.name} is not an object, ignored")
        return
    mono, wall = time.monotonic(), time.time()
    for did, g in snap.items():
        if isinstance(g, dict) and isinstance(g.get("dev"), dict) and isinstance(g.get("left"), (int, float)):
            _gone_dev[did] = {"dev": g["dev"], "left": mono - max(0.0, wall - g["left"])}
    _gone_saved = set(_gone_dev)


def _sweep_gone(now: float = None) -> list:
    """A device out of the z2m list for GONE_KEEP_SEC and not back (a rejoin or a rename brings it back sooner) left for good: the
    plug-ins hear DEVICE_REMOVED once, outside the lock (a plug-in may wait on the broker); none while the last list is suspect."""
    now = time.monotonic() if now is None else now
    done = []
    with _sync_lock:                                      # the minute loop and an unpair race a bridge/devices sync
        for did, g in list(_gone_dev.items()):
            if did in core.DEVICES:
                del _gone_dev[did]
            elif not _list_suspect and now - g["left"] > GONE_KEEP_SEC:
                done.append((did, _gone_dev.pop(did)["dev"]))
        _save_gone()
    for did, dev in done:
        for fn in list(core.DEVICE_REMOVED):
            try:
                fn(dev)
            except Exception as e:                        # noqa: BLE001  one plug-in must not stop the others
                log.error(f"[z2m] on-removed {getattr(fn, '__name__', fn)}: {e}")
    return [did for did, _ in done]


async def _sweep_loop():
    while True:
        await asyncio.sleep(60)
        if _loaded:                                       # a record restored at start waits for the first device list
            await asyncio.to_thread(_sweep_gone)          # a sync may hold the lock: not on the event loop


def _mark_loaded():
    global _loaded
    _loaded = True
    core.registry_ready("zigbee")


def load_devices():
    """The retained device list once, on a short-lived client: the first direct pull at start.
    The main client applies bridge/devices too; whichever comes first wins."""
    if core.paho is None:
        return
    if not ENABLED:
        log.info("[z2m] no zigbee2mqtt (Z2M_SERIAL empty or none): skipping the device list")
        return
    box = queue.Queue()
    cli = core.paho.Client(core.paho.CallbackAPIVersion.VERSION2, client_id=f"home-z2m-loader-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: box.put((m.topic, m.payload))
    try:
        core.mqtt_auth(cli).connect(core.MQTT_HOST, core.MQTT_PORT, keepalive=10)
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
        except Exception:                         # noqa: BLE001  closing a one-off client: what it says no longer matters
            pass
    with _sync_lock:
        if _loaded:
            log.info("[z2m] device list already live from the broker; the loader's copy is not needed")
            return
        n = sync_devices(devs)
        _mark_loaded()
    _sweep_gone()
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
    prev = core.status_copy(name, {})
    prev_vals = prev.get("values") or {}
    avail = prev.get("availability")
    if (retain and avail == "offline") or _bridge_online is False:
        online = False
    else:
        online, avail = True, (avail and "online")
    st = {"online": online, "values": {**prev_vals, **values}, "raw": {**(prev.get("raw") or {}), **raw}}
    if avail:
        st["availability"] = avail
    if online:
        # a z2m frame carries every exposed channel: only the ones that changed answer a command in flight
        core.store_status(name, st, codes={c for c in values if values[c] != prev_vals.get(c)})
    else:
        core.store_status_exact(name, st)
    if not retain:
        core.fire_sensor_triggers(name, cfg, values)
    for c in cfg["controls"]:
        code = c["code"]
        if c.get("kind") != "switch" or code not in values:
            continue
        newv = bool(values[code])
        if code in prev_vals and bool(prev_vals[code]) == newv:
            continue
        if not retain:
            core.inching_hook(name, code, newv)
        elif newv and not core.deferred_active(f"inch|{name}/{code}"):
            core.inching_hook(name, code, True)


def on_state(parts, payload, retain=False):
    """zigbee2mqtt/<friendly_name>: state into the cache; a button `action` into the rule engine
    (src=friendly_name, code="action", gesture=<action>). Only names zigbee2mqtt registered: anyone
    with its account could otherwise invent devices or pose as a generic one and fire its rules.
    An action comes empty on republishes, stale when retained, and twice for one press."""
    if len(parts) != 2 or parts[1] == "bridge":
        return
    name = parts[1]
    try:
        d = core.json_state(payload.decode("utf-8", "replace"))
    except ValueError:                            # not a state frame
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
    with core.events_lock:
        core.events[name].append({"ts": now, "code": "action", "value": action, "t": int(now)})
    cur = core.status_copy(name, {"online": True, "values": {}, "raw": {}})
    cur["online"] = True
    cur["last_event_ts"] = now
    cur["last_event"] = {"code": "action", "value": action}
    core.store_status(name, cur)
    core.bg.submit(core.safe_execute_binding, name, "action", str(action))


def on_availability(name: str, payload: bytes):
    """z2m 2.x: {"state": "online"|"offline"}; older: a bare word. zigbee2mqtt saying a device is
    gone is not a guess: stored without the grace period."""
    if (core.DEVICES.get(name) or {}).get("transport") != TRANSPORT:
        return
    raw = payload.decode("utf-8", "replace").strip()
    if not raw:                                     # a retained message cleared: no word about it
        return
    try:
        raw = (json.loads(raw) or {}).get("state", raw) if raw.startswith("{") else raw
    except ValueError:
        pass
    cur = core.status_copy(name, {"values": {}, "raw": {}})
    said = str(raw).lower() == "online"
    cur["availability"] = "online" if said else "offline"
    cur["online"] = said and _bridge_online is not False     # its bridge down: its own word does not reach
    cur.pop("stale", None)
    core.store_status_exact(name, cur)


def _z2m_ids():
    """The devices this plug-in registered (another plug-in may use transport z2m too)."""
    return [k for k, d in list(core.DEVICES.items())
            if d.get("transport") == TRANSPORT and core.device_owner.get(k) == "zigbee"]


def _on_bridge_state(raw):
    """Back after an outage: bridge/devices is not replayed to a running subscriber, so bring back
    what the devices' own availability does not contradict. Down (every time, not only on the
    change): none of its devices can be reached, whatever they said last."""
    global _bridge_online
    try:
        st = (json.loads(raw) or {}).get("state") if raw.startswith("{") else raw
    except ValueError:
        st = raw
    global _bridge_since
    was = _bridge_online
    _bridge_online = str(st).lower() == "online"
    if was != _bridge_online:
        _bridge_since = time.time()
    if _bridge_online and was is False:
        for did in _z2m_ids():
            cur = core.status_copy(did, {"values": {}, "raw": {}})
            if not cur.get("online") and cur.get("availability", "online") == "online":
                cur["online"] = True
                core.store_status_exact(did, cur)
        log.info("[z2m] zigbee2mqtt is back online")
    if _bridge_online is False:
        for did in _z2m_ids():
            cur = core.status_copy(did, {"values": {}, "raw": {}})
            if cur.get("online") or cur.get("waiting"):     # a device waiting since the start too
                cur["online"] = False
                core.store_status_exact(did, cur)
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
    except ValueError:                            # a plain-text bridge topic: nothing to read
        return
    if topic == "bridge" and sub == "info" and isinstance(data, dict):
        cfg = data.get("config") or {}
        _bridge_info.clear()
        _bridge_info.update({"coordinator": data.get("coordinator") or {}, "network": data.get("network") or {},
                             "serial": cfg.get("serial") or {}, "version": data.get("version") or "", "at": time.time()})
        return
    if topic == "bridge" and sub == "devices" and isinstance(data, list):
        with _sync_lock:
            sync_devices(data)
            _mark_loaded()
        _sweep_gone()
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
    core.mqtt_pub(cli, f"{NS}/{cfg['id']}/set", json.dumps(payload))
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
    sfx = "_" + core.chan_of(z_code) if core.chan_of(z_code) else ""
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
        core.mqtt_pub(cli, f"{NS}/{target}/set", json.dumps(payload))
    except Exception as e:
        log.warning(f"[binding] z2m publish failed {target}: {e}")
        return {"target": target, "code": z_code, "error": "publish failed"}
    if b.get("action") in ("on", "off", "toggle"):
        core.cancel_deferred(f"sched|{target}/{z_code}")
    log.info(f"[binding] {src_id}/{code}={gesture} -> z2m {target} {payload}")
    return {"target": target, "code": z_code, "value": payload}


def ask(did: str) -> bool:
    """One device back after an outage (or waiting since the start): /get it once (docs/device-state.md);
    False for one that cannot be read (a sleeping remote, EF00-only, no switch or brightness)."""
    c, cli = core.DEVICES.get(did) or {}, _cli()
    codes = [ctl["code"] for ctl in c.get("controls", []) if ctl.get("kind") in ("switch", "bright") and not ctl.get("ro")]
    if c.get("_skip_get") or not codes or cli is None:
        return False
    cli.publish(f"{NS}/{did}/get", json.dumps({code: "" for code in codes}))
    _asked[did] = time.monotonic()
    return True


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
        except Exception as e:                    # noqa: BLE001  one device's ask must not stop the others
            core.log.warning(f"[z2m] state ask {did}: {type(e).__name__}: {e}")
    return n


def on_rename(old, new):
    for d in (_seen, _asked, _last_action):
        if old in d:
            d.setdefault(new, d.pop(old))


class PairAdd(core.Body):
    ieee: str
    friendly_name: str = ""
    name: str = ""


class RenameBody(core.Body):
    id: str
    to: str


class RemoveBody(core.Body):
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
        if not (_pairing.get("active") and _pairing.get("until", 0) > time.time()):
            _pairing_found.clear()                    # a new window; one kept open (renewed) keeps what joined so far
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
        want = _check_key(want, but=cur)
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


Z2M_WORDS = {"bridge", "set", "get", "availability"}     # topic levels zigbee2mqtt keeps for itself


def _check_key(name, but: str = "") -> str:
    """A friendly_name as zigbee2mqtt allows it (Cyrillic and spaces are fine): checked like any name, new among the
    device ids in any case (it becomes one), no / + # |, not one of zigbee2mqtt's own words. -> the trimmed name."""
    n = core.check_name(name, "device", [k for k in list(core.DEVICES) if k != but], most=64)
    core.check_topic_name(n)
    if n.casefold() in Z2M_WORDS:
        raise HTTPException(400, f"'{n}' is a word zigbee2mqtt keeps for itself")
    return n


def _ask_rename(old, new):
    """The rename request, sent only once the broker confirmed the subscription for the answer
    (sent earlier, z2m's answer could beat it: a 504 while z2m had renamed the device)."""
    box: list = []
    waiter = core.paho.Client(core.paho.CallbackAPIVersion.VERSION2, client_id=f"home-rename-{secrets.token_hex(3)}")
    waiter.on_message = lambda c, u, m: box.append(m.payload)
    subacked = threading.Event()
    waiter.on_subscribe = lambda *a: subacked.set()
    try:
        core.mqtt_auth(waiter).connect(core.MQTT_HOST, core.MQTT_PORT, keepalive=10)
        waiter.subscribe(f"{NS}/bridge/response/device/rename")
        waiter.loop_start()
        if not subacked.wait(5):
            raise HTTPException(504, "the broker did not confirm the subscription for the rename answer")
        core.mqtt_pub(_cli(), f"{NS}/bridge/request/device/rename", json.dumps({"from": old, "to": new}))
        deadline = time.time() + 10
        while not box and time.time() < deadline:
            time.sleep(0.2)
    finally:
        try:
            waiter.loop_stop(); waiter.disconnect()
        except Exception:                         # noqa: BLE001  closing a one-off client: what it says no longer matters
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
    if "settings" in core.load_failed:
        raise HTTPException(503, f"{core.settings_bad()}: fix it before renaming")
    if _cli() is None:
        raise HTTPException(503, "MQTT bridge not connected")
    cfg = core.DEVICES.get(old)
    if not cfg:
        raise HTTPException(404, "unknown device")
    if cfg.get("transport") != TRANSPORT:
        raise HTTPException(400, "only z2m devices have a renameable key; "
                                 "use /api/devices/{id}/rename for a display name")
    new = _check_key(new, but=old)
    if new == old:
        raise HTTPException(400, "same name")
    before = core.status_copy(old, {})
    _gone.pop(new, None)                    # the new name's own past must not wipe the state moved to it
    with core.cache_lock:
        core.status_cache.pop(new, None)   # nor may a long-gone device of that name lend it its values
    _ask_rename(old, new)
    wait_devices(lambda: new in core.DEVICES and old not in core.DEVICES)
    touched = core.migrate_device_key(old, new)
    if before.get("values"):
        core.store_status_exact(new, before)          # the renamed device's own state, not a leftover of the name
    log.info(f"[rename] {old} -> {new}: {touched}")
    if new not in core.DEVICES:
        log.warning(f"[rename] '{new}' is not in the device list yet; it appears with the next bridge/devices")
        return {"ok": True, "id": new, "from": old, "migrated": touched, "pending": True}
    core.mqtt_publish_state(new)
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
    if body.id not in core.DEVICES and body.id in _gone:      # unpaired by the owner: gone for good now, not after the rejoin window
        _gone[body.id] = time.monotonic() - GONE_KEEP_SEC - 1
        with _sync_lock:
            if body.id in _gone_dev:
                _gone_dev[body.id]["left"] = _gone[body.id]
        _sweep_gone()
    return {"ok": True, "id": body.id, "removed": body.id not in core.DEVICES, "force": bool(body.force)}


_load_gone()
core.MQTT_NAMESPACES.add(NS)
# in this order: the broker replays retained messages in subscription order, registry first
core.MQTT_SUBSCRIPTIONS.extend([f"{NS}/bridge/state", f"{NS}/bridge/info", f"{NS}/bridge/devices", f"{NS}/bridge/event",
                                f"{NS}/+/availability", f"{NS}/+"])
core.MQTT_HANDLERS.append(((NS,), on_message))
core.TRANSPORTS[TRANSPORT] = publish_set
core.TRANSPORT_BINDINGS[TRANSPORT] = binding
core.STATE_ASK[TRANSPORT] = ask
core.RESYNC_HOOKS.append(get_all)
core.ON_RENAME.append(on_rename)
core.STARTUP_TASKS.append(load_devices)
core.STARTUP_TASKS.append(_sweep_loop)
if ENABLED:
    core.registry_pending("zigbee")


def _data_dir() -> pathlib.Path:
    """zigbee2mqtt's data (configuration.yaml, coordinator_backup.json): Z2M_DATA_DIR, relative to the checkout."""
    return pathlib.Path(core.CHECKOUT) / (os.environ.get("Z2M_DATA_DIR") or "config/zigbee2mqtt")


def _backup() -> dict:
    try:
        return json.loads((_data_dir() / "coordinator_backup.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _conf_serial() -> dict:
    """serial: port / adapter of configuration.yaml (zigbee2mqtt down: the page still shows what it is set to)."""
    try:
        text = (_data_dir() / "configuration.yaml").read_text(encoding="utf-8")
    except OSError:
        return {}
    m = re.search(r"^serial:[^\n]*\n((?:[ \t]+[^\n]*\n)*)", text, re.M)
    return {k: v.strip().strip("\"'") for k, v in re.findall(r"^[ \t]+(port|adapter):[ \t]*([^#\n]*)", m.group(1) if m else "", re.M)}


def gateway() -> dict:
    """What the gateway page shows: the coordinator and its network as zigbee2mqtt reports them, else from its files."""
    info, back, conf = dict(_bridge_info), _backup(), _conf_serial()
    co, net, ser = info.get("coordinator") or {}, info.get("network") or {}, info.get("serial") or {}
    pan = net.get("pan_id") if net.get("pan_id") is not None else (int(back["pan_id"], 16) if back.get("pan_id") else None)
    ext = net.get("extended_pan_id") or (("0x" + back["extended_pan_id"]) if back.get("extended_pan_id") else "")
    return {"port": ser.get("port") or conf.get("port") or "", "adapter": ser.get("adapter") or conf.get("adapter") or "",
            "coordinator": co.get("type") or "", "firmware": (co.get("meta") or {}).get("revision") or "",
            "ieee": co.get("ieee_address") or back.get("coordinator_ieee") or "", "channel": net.get("channel") or back.get("channel"),
            "pan_id": pan, "ext_pan_id": ext, "key_set": bool((back.get("network_key") or {}).get("key")),
            "version": info.get("version") or "", "online": _bridge_online, "since": _bridge_since, "info_at": info.get("at")}


@core.app.get("/api/zigbee/gateway")
def api_gateway(request: Request):
    core.house_token_only(request)
    return gateway()


@core.app.get("/api/zigbee/gateway/key")
def api_gateway_key(request: Request):
    """The network key, on "Show" only (never in the page's first answer)."""
    core.house_token_only(request)
    key = (_backup().get("network_key") or {}).get("key")
    if not key:
        raise HTTPException(404, "no network key in zigbee2mqtt's coordinator backup")
    return {"key": key}


class GatewayCheckBody(core.Body):
    port: str = Field("", max_length=200)          # empty: the one in use


@core.app.post("/api/zigbee/gateway/check")
def api_gateway_check(body: GatewayCheckBody, request: Request):
    """A network coordinator: a TCP connect to it (3 s); the one in use while zigbee2mqtt is online needs none."""
    core.house_token_only(request)
    port = body.port.strip() or gateway()["port"]
    if not body.port.strip() and _bridge_online:
        return {"ok": True, "how": "zigbee2mqtt is connected through it"}
    m = re.fullmatch(r"tcp://([A-Za-z0-9.\-]+):(\d{1,5})", port)
    if not m:
        return {"ok": None, "how": "a USB stick is checked by zigbee2mqtt itself"}
    try:
        socket.create_connection((m.group(1), int(m.group(2))), timeout=3).close()
        return {"ok": True, "how": "it answers on {0}", "args": [f"{m.group(1)}:{m.group(2)}"]}
    except OSError as e:
        return {"ok": False, "how": "no answer from {0}: {1}", "args": [f"{m.group(1)}:{m.group(2)}", type(e).__name__]}


# zigbee2mqtt's own page, through the dashboard's port: the container publishes no port (only the compose network reaches
# it; compose.yml sets its frontend.base_url to UI). The page is opened from Settings -> Zigbee -> Gateway, which asks for a
# pass (a cookie for UI only, 12 h, kept in memory: a restart asks again); the house fills in zigbee2mqtt's own token.
UI = "/plugins/zigbee/ui"
Z2M = "zigbee2mqtt:8080"
UI_PASS_S = 12 * 3600
_ui_passes: dict = {}                 # pass -> its end (time.time())
_UI_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
           "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")


def _ui_pass_ok(cookies) -> bool:
    now = time.time()
    for k in [k for k, end in _ui_passes.items() if end < now]:
        _ui_passes.pop(k, None)
    return (cookies.get("z2m_pass") or "") in _ui_passes


@core.app.post("/api/zigbee/ui")
def api_ui_open(request: Request):
    """A pass for zigbee2mqtt's page (the house token only): the answer sets the cookie, the page opens UI/."""
    core.house_token_only(request)
    tok = secrets.token_urlsafe(32)
    _ui_passes[tok] = time.time() + UI_PASS_S
    r = JSONResponse({"url": UI + "/"})
    r.set_cookie("z2m_pass", tok, max_age=UI_PASS_S, path=UI, httponly=True, samesite="strict")
    return r


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


_ui_opener = urllib.request.build_opener(_NoRedirect)


@core.app.get(UI + "/{p:path}")
def ui_page(p: str, request: Request):
    """GET UI/<path>: zigbee2mqtt's page and files, with a pass; its answer as it is (a redirect too), the page's own CSP."""
    if not _ui_pass_ok(request.cookies):
        return Response("Open it from Settings -> Zigbee -> Gateway.", status_code=401, media_type="text/plain")
    if ".." in p.split("/"):
        return Response("not found", status_code=404, media_type="text/plain")
    url = f"http://{Z2M}{UI}/{p}" + (f"?{request.url.query}" if request.url.query else "")
    try:
        r = _ui_opener.open(urllib.request.Request(url, headers={"Accept-Encoding": "identity"}), timeout=15)
    except urllib.error.HTTPError as e:
        r = e
    except OSError:
        return Response("zigbee2mqtt does not answer", status_code=502, media_type="text/plain")
    with r:
        body = r.read()
        keep = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "location", "cache-control", "etag", "last-modified")}
    keep["Content-Security-Policy"] = _UI_CSP
    return Response(body, status_code=r.status if hasattr(r, "status") else r.code, headers=keep)


@core.app.websocket(UI + "/api")
async def ui_socket(ws: WebSocket):
    """zigbee2mqtt's live link for its page, with a pass; the house adds zigbee2mqtt's token."""
    if not _ui_pass_ok(ws.cookies):
        await ws.close(code=4401)
        return
    from websockets.asyncio.client import connect
    tok = os.environ.get("Z2M_AUTH_TOKEN") or ""
    try:
        up = await connect(f"ws://{Z2M}{UI}/api" + (f"?token={urllib.parse.quote(tok)}" if tok else ""), max_size=None, open_timeout=10)
    except (OSError, Exception):          # noqa: BLE001  any failure to reach it is the same answer
        await ws.close(code=1011)
        return
    await ws.accept()

    async def down():
        async for m in up:
            await (ws.send_text(m) if isinstance(m, str) else ws.send_bytes(m))

    async def upward():
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return
            await up.send(m["text"] if m.get("text") is not None else m.get("bytes") or b"")

    tasks = [asyncio.create_task(down()), asyncio.create_task(upward())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await up.close()
        try:
            await ws.close()
        except RuntimeError:
            pass


core.open_route(ui_page)
core.open_route(ui_socket)


def _health():
    if _bridge_online is True:
        return [core.health_row("zigbee", "ok", "Zigbee (zigbee2mqtt)")]
    if _bridge_online is False:
        return [core.health_row("zigbee", "bad", "Zigbee (zigbee2mqtt)", "offline",
                                "Is the coordinator on and reachable?", link="zigbee/gateway", cmd="docker compose logs zigbee2mqtt")]
    return [core.health_row("zigbee", "warn", "Zigbee (zigbee2mqtt)", "not heard from yet",
                            "Is the zigbee2mqtt container running?", link="zigbee/gateway", cmd="docker compose ps")]


core.HEALTH_CHECKS.append(_health)
