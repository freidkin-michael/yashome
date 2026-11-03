"""Wall panels: layouts drawn by the builder, shown by a tablet browser (/panel-view.html) or an ESP screen.

A layout is configuration (plugin-data/panels/layouts.json); an ESP panel gets it retained on
panel/<name>/layout. An Android tablet runs the kiosk launcher: it finds the server through the
discovery module (discover.py), shows a code, a human binds it on the dashboard, and the launcher collects
a key of its own (`ypk_...`, only its sha256 is kept). The key opens only what a panel needs.
"""
import asyncio
import functools
import hashlib
import hmac
import ipaddress
import json
import math
import os
import queue
import re
import secrets
import socket
import struct
import sys
import threading
import time
from typing import List, Literal, Optional

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field, model_validator

import app as core

PLUGIN = {"name": "Wall panels", "version": "2.1.0", "icon": "screen"}

HERE = os.path.dirname(os.path.abspath(__file__))
LAYOUTS = core.plugin_data("panels") / "layouts.json"
PAIR_DB = core.plugin_state_file("panels", "paired.json")
PAIR_FLAG = core.plugin_state("panels") / "pairing.json"
PAIR_SEEN = core.plugin_state("panels") / "discovered.json"
PREFIX = "ypk_"
KEY_WINDOW_S = 600
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
NS = "panel"
core.MQTT_NAMESPACES.add(NS)
core.MQTT_SHARED_READ.extend([f"{NS}/+/sensor/#", f"{NS}/+/status"])   # a screen's own sensors as a device's

_lock = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def w(*args, **kw):
        with _lock:
            return fn(*args, **kw)
    return w


_BROKEN: dict = {}       # a file that did not parse -> why: nothing writes over it (the health page says so)


def _guard(path, recheck: bool = True):
    """Refuse a write over a file that did not parse; a file read fresh each time (paired, keys) is
    checked again, so fixing or removing it heals without a restart."""
    if str(path) not in _BROKEN:
        return
    if recheck:
        try:
            json.loads(path.read_text(encoding="utf-8"))
            _BROKEN.pop(str(path), None)
            return
        except FileNotFoundError:
            _BROKEN.pop(str(path), None)
            return
        except (OSError, ValueError):
            pass
    raise HTTPException(503, f"{path.name} is unreadable: fix or remove it" + ("" if recheck else ", then restart"))


def _load_layouts() -> dict:
    try:
        d = json.loads(LAYOUTS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        core.log.error(f"[panels] layouts.json unreadable: no layout is saved until it is fixed: {e}")
        _BROKEN[str(LAYOUTS)] = str(e)
        return {}
    if not isinstance(d, dict):
        return {}
    out = {n: ({"w": 800, "h": 480, "widgets": v} if isinstance(v, list) else v)
           for n, v in d.items() if isinstance(v, (dict, list))}
    for n, lay in out.items():                    # widgets saved with NaN before it was refused: GET failed on them
        keep = [w for w in lay.get("widgets") or [] if _finite(w)]
        if len(keep) != len(lay.get("widgets") or []):
            core.log.warning(f"[panels] {n}: {len(lay['widgets']) - len(keep)} widget(s) with NaN/infinity dropped")
            lay["widgets"] = keep
    return out


def _finite(w) -> bool:
    try:
        json.dumps(w, allow_nan=False)
        return True
    except (ValueError, OverflowError):
        return False


_layouts: dict = _load_layouts()


def _save_layouts():
    _AWAY_DEVS.clear()                        # a panel's devices may have changed
    _guard(LAYOUTS, recheck=False)            # the memory holds nothing of it: only a restart reads it again
    core.atomic_json_dump(LAYOUTS, _layouts, indent=2, ensure_ascii=False)


def _pair_load(path, default):
    try:
        return json.loads(path.read_text())
    except ValueError as e:
        if path == PAIR_DB:
            if str(path) not in _BROKEN:
                core.log.error(f"[panels] paired.json unreadable: no tablet is paired or unbound until it is fixed: {e}")
            _BROKEN[str(path)] = str(e)
        return default
    except OSError:
        return default


_db_cache = {"mtime": None, "data": {}}


def _db() -> dict:
    """paired.json, parsed again only when it changed: every request of a panel key reads it."""
    try:
        m = PAIR_DB.stat().st_mtime_ns
    except FileNotFoundError:
        _BROKEN.pop(str(PAIR_DB), None)
        return {}
    if _db_cache["mtime"] != m:
        _db_cache["data"], _db_cache["mtime"] = _pair_load(PAIR_DB, {}), m
        try:
            json.loads(PAIR_DB.read_text())
            _BROKEN.pop(str(PAIR_DB), None)            # fixed by hand: the health page sees it at once
        except (OSError, ValueError):
            pass
    return _db_cache["data"]


def _pair_save(path, data, mode=0o600):
    _guard(path)
    core.atomic_json_dump(path, data, indent=1, ensure_ascii=False)
    os.chmod(path, mode)


def _publish(name: str):
    """An ESP panel's layout, retained: after a reboot it gets the current one without a reflash."""
    cli = core.mqtt_client
    lay = _layouts.get(name)
    if cli is None or lay is None or lay.get("hw") == "android":
        return
    try:
        cli.publish(f"{NS}/{name}/layout", json.dumps(lay, ensure_ascii=False), retain=True)
    except Exception as e:                                # noqa: BLE001
        core.log.warning(f"[panels] publish layout {name}: {e}")


def _publish_all(_cli=None):
    with _lock:
        for name in list(_layouts):
            _publish(name)


core.ON_MQTT_CONNECT.append(_publish_all)

_AWAY: dict = {}            # ESP panel -> its away list as last published (retained)


_AWAY_DEVS: dict = {}       # ESP panel -> (when, house device count, its devices)
AWAY_DEVS_S = 10            # rebuilt on a layout save, a device rename, a device added or removed, or after this


def _away_devs(name: str) -> list:
    now, n = time.monotonic(), len(core.DEVICES)
    hit = _AWAY_DEVS.get(name)
    if hit is None or now - hit[0] >= AWAY_DEVS_S or hit[1] != n:
        hit = _AWAY_DEVS[name] = (now, n, sorted(_refs(name)[0]))
    return hit[2]


def _away_of(name: str) -> str:
    """panel/<name>/away: "/<device>#o/<device>#w/", the panel's devices offline or waiting for their
    report ("/" when all are live); the ESP screen greys those widgets (docs/device-state.md). An id
    is a topic level: no "/" or "#" in it, so one id never matches inside another."""
    out = []
    for dev in _away_devs(name):
        st = core.status_cache.get(dev) or {}
        out.append(f"{dev}#o/" if st.get("offline_since") else f"{dev}#w/" if st.get("waiting") else "")
    return "/" + "".join(out)


def _away_tick():
    """Publishes an ESP panel's away list when it changed; one retained topic, not one per device:
    the firmware's MQTT inbound queue (31 events) also takes the SUBACK and retained value of each."""
    cli = core.mqtt_client
    if cli is None:
        return
    with _lock:
        names = [n for n, lay in _layouts.items() if (lay or {}).get("hw") != "android"]
    for name in names:
        txt = _away_of(name)
        if _AWAY.get(name) != txt:
            try:
                cli.publish(f"{NS}/{name}/away", txt, retain=True)
                _AWAY[name] = txt
            except Exception as e:                        # noqa: BLE001
                core.log.warning(f"[panels] publish away {name}: {e}")


AWAY_TICK_S = 1


async def _away_loop():
    while True:
        _away_tick()
        _esp.words_tick()                     # the screens' words, when the house language changed
        await asyncio.sleep(AWAY_TICK_S)


core.STARTUP_TASKS.append(_away_loop)


def _away_reset(_cli):
    _AWAY.clear()                   # a broker without persistence lost them: publish again


core.ON_MQTT_CONNECT.append(_away_reset)
core.ON_RENAME.append(lambda old, new: _AWAY_DEVS.clear())


def _clear_retained(name: str, timeout: float = 2.5) -> int:
    """Every retained panel/<name>/# leftover (a short-lived client lists them) is emptied. Call it
    outside _lock: it takes up to `timeout` seconds."""
    found = {f"{NS}/{name}/layout"}
    if core.paho is not None:
        box = queue.Queue()
        cli = core.paho.Client(core.paho.CallbackAPIVersion.VERSION2, client_id=f"panels-scan-{secrets.token_hex(3)}")
        cli.on_message = lambda c, u, m: m.retain and m.payload and box.put(m.topic)
        try:
            core.mqtt_auth(cli).connect(core.MQTT_HOST, core.MQTT_PORT, keepalive=10)
            cli.subscribe(f"{NS}/{name}/#")
            cli.loop_start()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                found.add(box.get(timeout=max(0.05, min(1.0, deadline - time.monotonic()))))
        except queue.Empty:
            pass
        except Exception as e:                            # noqa: BLE001
            core.log.warning(f"[panels] retained scan: {e}")
        finally:
            cli.loop_stop()
            cli.disconnect()
    main = core.mqtt_client
    if main is None:
        return 0
    for t in sorted(found):
        main.publish(t, None, retain=True)
    return len(found)


def _sha(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _device_of(tok: str):
    """(device id, its record) of a bound panel holding this key, else (None, None)."""
    if not isinstance(tok, str) or not tok.startswith(PREFIX):
        return None, None
    h = _sha(tok)
    for did, d in list(_db().items()):
        if isinstance(d, dict) and d.get("status") == "bound" and hmac.compare_digest(h, d.get("key_sha256", "")):
            return did, d
    return None, None


def _refs(panel: str):
    """(device ids, camera ids, device ids it commands) of this panel's widgets: a panel key sees the
    first, controls only the third (a widget that only shows a sensor gives no control)."""
    devs, cams, ctl = set(), set(), set()
    owner = {t: did for did, cfg in list(core.DEVICES.items()) for c in cfg.get("controls") or [] if isinstance(c, dict)
             for k, t in c.items() if k.endswith("_topic") and isinstance(t, str) and t}
    for w in list((_layouts.get(_resolve(panel)) or {}).get("widgets") or []):
        if not isinstance(w, dict):
            continue
        if isinstance(w.get("device"), str):           # a remote / AC widget presses its device itself
            devs.add(w["device"])
            if w.get("type") in _ACTS:
                ctl.add(w["device"])
        if isinstance(w.get("cam_id"), str) and w["cam_id"]:
            cams.add(w["cam_id"])
        elif w.get("type") == "camera":
            own = (_layouts.get(_resolve(panel)) or {}).get("device")
            if own:
                cams.add(f"{own}-{'back' if w.get('facing') == 'back' else 'front'}")
        for k in _TOPIC_KEYS:
            t = w.get(k)
            if not isinstance(t, str):
                continue
            lv = t.split("/")
            dev = lv[2] if len(lv) >= 3 and lv[0] == "home" and lv[1] in ("cmd", "state") else owner.get(t)
            if dev:
                devs.add(dev)
                acts = w.get("type") in _ACTS
                if acts and (k in _CMD_KEYS or (k == "state_topic" and w.get("type") in _TAP_TYPES)):
                    ctl.add(dev)
    return devs, cams, ctl


_DEV = re.compile(r"^/api/devices/([^/]+)/(status|control)$")
_CAM = re.compile(r"^/api/camera/([^/]+)/snapshot$")
_LAYOUT = re.compile(r"^/api/panels/([^/]+)/layout$")
_OPEN_GET = {"/api/devices", "/api/health", "/api/weather", "/api/cameras", "/api/panels/ui", "/api/touchmouse"}
_TRIMMED = {"/api/devices", "/api/cameras", "/api/touchmouse"}


def _allowed(panel: str, method: str, path: str) -> bool:
    """What a panel key opens: its own layout and the devices and cameras on it, the tiles' data, /ws."""
    if (method, path) in (("WS", "/ws"), ("POST", "/api/panels/heartbeat"), ("GET", "/api/panels/voice/next"),
                          ("POST", "/api/panels/voice/event")) or (method == "GET" and path in _OPEN_GET):
        return True
    m = _DEV.match(path)
    if m:
        if m.group(2) == "status":
            return method == "GET" and m.group(1) in _refs(panel)[0]
        return method == "POST" and m.group(1) in _refs(panel)[2]
    m = _CAM.match(path)
    if m:
        return method == "GET" and m.group(1) in _refs(panel)[1]
    m = _LAYOUT.match(path)
    return bool(m) and method == "GET" and bool(panel) and _resolve(m.group(1)) == _resolve(panel)


def _token_check(tok: str, method: str, path: str) -> bool:
    did, d = _device_of(tok)
    return did is not None and _allowed(d.get("panel", ""), method, path)


core.TOKEN_CHECKS.append(_token_check)


def _panel_of(headers, query=None):
    """The panel behind a panel key in these headers (or ?token= of a WebSocket); None = not a panel key."""
    tok = core.request_token(headers, query)
    if not tok.startswith(PREFIX):
        return None
    did, d = _device_of(tok)
    return d.get("panel", "") if did else ""


@core.app.middleware("http")
async def _panel_trim(request: Request, call_next):
    """The device, camera and touchmouse lists of a panel key hold only what its widgets and bar use."""
    resp = await call_next(request)
    if request.method != "GET" or request.url.path not in _TRIMMED or resp.status_code != 200:
        return resp
    panel = _panel_of(request.headers)
    if panel is None:
        return resp
    body = b"".join([chunk async for chunk in resp.body_iterator])
    devs, cams, _ctl = _refs(panel)
    lay = _layouts.get(panel) or {}
    bar = {b.get("receiver") for b in lay.get("bar") or [] if isinstance(b, dict)} | \
        {w.get("receiver") for w in lay.get("widgets") or [] if isinstance(w, dict) and w.get("type") == "touchmouse"}
    keep = {"/api/devices": devs, "/api/cameras": cams, "/api/touchmouse": bar}[request.url.path]
    data = [x for x in json.loads(body) if isinstance(x, dict) and x.get("id") in keep]
    hdrs = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-type")}
    return JSONResponse(data, headers=hdrs)


def _ws_trim(websocket, payload):
    """Checked on every tick: a key unbound, re-bound or deleted meanwhile sees nothing more."""
    panel = _panel_of(websocket.headers, websocket.query_params)
    if panel is None:
        return payload
    devs = _refs(panel)[0] if panel else set()
    return {k: v for k, v in payload.items() if k in devs}


core.WS_FILTERS.append(_ws_trim)


@core.app.get("/api/panels/ui")
def ui_stamp():
    """Changes when the kiosk page changes: an open tablet reloads itself."""
    stamp = 0
    for f in ("static/view.html", "static/widgets.js", "i18n.json"):
        try:
            stamp = max(stamp, os.stat(os.path.join(HERE, f)).st_mtime_ns)
        except OSError:
            pass
    return {"stamp": str(stamp)}


def _key_of(request: Request) -> str:
    return core.request_token(request.headers)


_HW = {
    "knob128": {"label": "VIEWE knob 1.28in (ESP32-C3)", "kind": "list", "w": 240, "h": 240,
                "can": ["toggle", "dimmer", "climate"]},
}
_KNOB_HWS = {k for k, v in _HW.items() if v["kind"] == "list"}
_KNOB_WIDGETS = {"toggle", "dimmer", "climate"}
_KNOB_ONLY = {"climate"}
_KNOB_MAX_ROWS = 4
_WIDGET_TYPES = {"clock", "date", "temp", "label", "toggle", "dimmer", "button", "sensor",
                 "camera", "voice", "climate", "weather", "ac", "remote", "touchmouse"}
_ANDROID_ONLY = {"camera", "voice", "touchmouse"}
_TILE_ICONS = {"bulb", "plug", "thermo", "drop", "fan", "tv", "ac"}   # an Android tile's icon (view.html TILE_ICONS)
_LEGACY_TYPES = {"temp"}     # an older name: the builder turns it into "sensor" when it loads a layout; valid, never offered
LAYOUT_CHECKS: list = []     # fn(name, body) before a layout is stored: a deployment's own limits (the public demo's)
BUILDER_LIMITS: dict = {}    # what the builder offers: {"hide": [element ids], "only": a panel, "notice": text}; empty = all
_ACTS = {"toggle", "dimmer", "button", "ac", "remote", "climate"}   # only these give a key control: a new type fails closed
_TAP_TYPES = {"toggle", "button", "dimmer"}       # the kiosk taps their state topic's device when no cmd is set
_TOPIC_KEYS = ("state_topic", "cmd_topic", "dim_topic", "onoff_topic", "temp_state", "temp_cmd", "mode_state",
               "mode_cmd", "fan_state", "fan_cmd", "room_state", "power_state", "power_cmd")
_CMD_KEYS = ("cmd_topic", "dim_topic", "temp_cmd", "mode_cmd", "fan_cmd", "power_cmd")


def _resolve(name: str) -> str:
    """A renamed panel answers to its old names too, until its launcher learns the new one."""
    if name in _layouts:
        return name
    return next((n for n, p in list(_layouts.items()) if name in (p.get("was") or [])), name)


def _command_topics() -> set:
    """Command topics the registered MQTT devices declare: a panel may publish there besides home/cmd/."""
    return {c["command_topic"] for cfg in list(core.DEVICES.values()) for c in cfg.get("controls") or []
            if isinstance(c, dict) and isinstance(c.get("command_topic"), str) and c["command_topic"]}


def _core_cmd(topic: str) -> bool:
    """home/cmd/<device>/<code> of a device of the house, no wildcards."""
    lv = topic.split("/")
    return (len(lv) == 4 and lv[:2] == ["home", "cmd"] and lv[2] in core.DEVICES and bool(lv[3])
            and not any(ch in topic for ch in "+#"))


def _need(name: str) -> dict:
    if name not in _layouts:
        raise HTTPException(404, "unknown panel")
    return _layouts[name]


@core.app.get("/api/panels")
def list_panels():
    live = _pair_load(PAIR_DB, {})
    with _lock:
        return [{"name": n, "title": p.get("title") or n, "hw": p.get("hw", "waveshare7"),
                 "rotation": p.get("rotation"), "w": p.get("w", 800), "h": p.get("h", 480),
                 "device": p.get("device"), "charger": p.get("charger"), "room": p.get("room"),
                 "caps": _live(p.get("device"), live.get(p.get("device")) or {}).get("caps") or p.get("caps") or {},
                 "seen": _live(p.get("device"), live.get(p.get("device")) or {}).get("beat"),
                 "can": p.get("can") or list(_HW.get(p.get("hw", "waveshare7"), {}).get("can") or []),
                 "mac": p.get("mac"),
                 "candidate": None if p.get("mac") or p.get("hw") not in _esp.gen.profiles() else _esp.candidate(n),
                 "widgets": len(p.get("widgets", []))} for n, p in _layouts.items()]


@core.app.get("/api/panels/hw")
def list_hw():
    return [{"hw": k, **v} for k, v in _HW.items()]


@core.app.get("/api/panels/{name}/layout")
def get_layout(name: str):
    with _lock:
        return _need(_resolve(name))


class BarItem(core.Body):
    """A status-bar button of an Android panel: the touchpad for a touchmouse receiver."""
    type: Literal["touchmouse"]
    receiver: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

    @model_validator(mode="before")
    @classmethod
    def _old_shape(cls, v):
        # cutover-compat: layouts of the previous stack: {"type": "airmouse", "device": <receiver id>}
        if isinstance(v, dict) and v.get("type") == "airmouse":
            return {"type": "touchmouse", "receiver": v.get("device")}
        return v


class LayoutBody(core.Body):
    hw: Optional[str] = None
    w: int = 800
    h: int = 480
    widgets: list = []
    title: Optional[str] = None
    rotation: Optional[int] = None
    bar: Optional[List[BarItem]] = Field(None, max_length=4)


def _check_widget(w, hw: str, allowed_cmd: set):
    wid, wtype = w.get("id"), w.get("type")
    if wtype not in _WIDGET_TYPES:
        raise HTTPException(400, f"bad widget type '{wtype}' for {wid}")
    if wtype in _ANDROID_ONLY and hw != "android":
        raise HTTPException(400, f"widget '{wtype}' exists only on Android panels")
    if wtype == "touchmouse" and not (isinstance(w.get("receiver"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", w["receiver"])):
        raise HTTPException(400, f"widget {wid}: receiver must be the id of a touchmouse receiver")
    if hw in _KNOB_HWS and wtype not in _KNOB_WIDGETS:
        raise HTTPException(400, f"the knob supports only toggle/dimmer/climate, not '{wtype}'")
    if wtype in _KNOB_ONLY and hw not in _KNOB_HWS:
        raise HTTPException(400, f"widget '{wtype}' exists only on the knob")
    for k, lo, hi in (("temp_min", -50, 100), ("temp_max", -50, 100), ("temp_step", -50, 100),
                      ("state_max", 1, 65535), ("cmd_max", 1, 65535)):
        v = w.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi):
            raise HTTPException(400, f"widget {wid} {k} must be an int {lo}..{hi}")
    for k in ("cmd_on", "cmd_off", "state_on", "cmd_payload", "mode", "power_on", "power_off"):
        v = w.get(k)
        if v is not None and not (isinstance(v, str) and 0 < len(v) <= 32 and "\n" not in v):
            raise HTTPException(400, f"widget {wid} {k} must be a short string")
    try:                             # NaN, infinity or a number past float anywhere in it: GET would fail on it
        json.dumps(w, allow_nan=False)
    except (ValueError, OverflowError):
        raise HTTPException(400, f"widget {wid}: numbers must be finite")
    for k in ("x", "y", "w", "h"):
        if isinstance(w.get(k), bool) or not isinstance(w.get(k), (int, float)) or not math.isfinite(w[k]) \
                or not -10000 <= w[k] <= 100000:
            raise HTTPException(400, f"widget {wid} missing numeric '{k}' (-10000..100000)")
    if w.get("icon") is not None and (w["icon"] not in _TILE_ICONS or hw != "android"):
        raise HTTPException(400, f"widget {wid}: icon is one of {', '.join(sorted(_TILE_ICONS))}, on Android panels")
    for k in ("bg", "fg", "accent"):
        v = w.get(k)
        if v is not None and not (isinstance(v, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v)):
            raise HTTPException(400, f"widget {wid} bad color '{k}'={v}")
    for k in _TOPIC_KEYS:
        v = w.get(k)
        if v is not None and not (isinstance(v, str) and len(v) <= 256 and v.isprintable()):
            raise HTTPException(400, f"widget {wid} {k} must be a topic")
    for k in _CMD_KEYS:              # a panel publishes there verbatim: the core's bus or a device's own topic
        v = w.get(k)
        if v is not None and not (_core_cmd(v) or v in allowed_cmd):
            raise HTTPException(400, f"widget {wid} {k} must start with home/cmd/ or be a command topic of a device")
    bl = w.get("buttons")
    if bl is not None and not (isinstance(bl, list) and 0 < len(bl) <= 48 and all(
            isinstance(b, dict) and isinstance(b.get("id"), str) and 0 < len(b["id"]) <= 32
            and isinstance(b.get("label", ""), str) and len(b.get("label", "")) <= 32 for b in bl)):
        raise HTTPException(400, f"widget {wid} buttons must be up to 48 {{id, label}} entries")
    for k in ("modes", "fans"):
        lv = w.get(k)
        if lv is not None and not (isinstance(lv, list) and 0 < len(lv) <= 8 and all(
                isinstance(p, list) and len(p) == 2 and all(isinstance(s, str) and 0 < len(s) <= 16 for s in p)
                for p in lv)):
            raise HTTPException(400, f"widget {wid} {k} must be up to 8 [value, label] pairs")


def _taken_panel(name: str, but: str = "") -> bool:
    return any(k.casefold() == name.casefold() for k in _layouts if k != but)


@core.app.get("/api/panels/limits")
def builder_limits():
    """What the builder offers here (BUILDER_LIMITS): everything, unless a deployment narrowed it."""
    return BUILDER_LIMITS


@core.app.put("/api/panels/{name}/layout")
def put_layout(name: str, body: LayoutBody):
    core.check_topic_name(name, "panel name")
    for fn in list(LAYOUT_CHECKS):
        fn(name, body)
    if name not in _layouts:                          # a new panel: its name becomes topics (and an ESP node's name)
        if not NAME_RE.fullmatch(name):
            raise HTTPException(400, "panel name: 1-32 latin letters, digits, - or _")
        if _taken_panel(name):
            raise HTTPException(409, f"panel '{name}' already exists")
    if not (16 <= body.w <= 4096 and 16 <= body.h <= 4096):
        raise HTTPException(400, "panel w/h out of range")
    if body.rotation is not None and body.rotation not in (0, 90, 180, 270):
        raise HTTPException(400, "rotation must be 0, 90, 180 or 270")
    allowed_cmd = _command_topics()
    with _lock:
        saved = _layouts.get(name, {})
        if body.hw is not None:
            if body.hw not in _HW:
                raise HTTPException(400, f"unknown hardware '{body.hw}'; known: {', '.join(_HW)}")
            if saved.get("hw") and body.hw != saved["hw"]:
                raise HTTPException(400, f"a panel's hardware is fixed ({saved['hw']}); create a new panel")
        hw = saved.get("hw") or body.hw
        if not hw:
            raise HTTPException(400, "a new panel needs its hardware (hw)")
        if body.bar and hw != "android":
            raise HTTPException(400, "the status bar exists only on Android panels")
        if hw in _KNOB_HWS and len(body.widgets) > _KNOB_MAX_ROWS:
            raise HTTPException(400, f"the knob takes at most {_KNOB_MAX_ROWS} rows")
        if not all(isinstance(w, dict) for w in body.widgets):
            raise HTTPException(400, "widget must be an object")
        ids = [w.get("id") for w in body.widgets]
        if any(not i or not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise HTTPException(400, "widget id missing or duplicated")
        for w in body.widgets:
            _check_widget(w, hw, allowed_cmd)
        if hw in _esp.gen.profiles():         # a new or changed widget of an ESP panel must map to the house bus
            old = {w.get("id"): w for w in saved.get("widgets") or [] if isinstance(w, dict)}
            _esp.bus_layout(name, {"widgets": [w for w in body.widgets if old.get(w.get("id")) != w]})
        # merge, never rebuild: an entry keeps keys the builder does not know
        upd = {"w": body.w, "h": body.h, "widgets": body.widgets}
        if not saved.get("hw"):
            upd["hw"] = hw
        if body.title is not None:
            t = body.title.strip()
            if t and t != saved.get("title"):              # a changed title: checked, never cut
                t = core.check_name(t, "panel", [p.get("title") for k, p in _layouts.items() if k != name], most=40)
            upd["title"] = t or name
        if body.rotation is not None:
            upd["rotation"] = body.rotation
        if body.bar is not None:
            upd["bar"] = [b.model_dump() for b in body.bar]
        _layouts.setdefault(name, {}).update(upd)
        _save_layouts()
        _publish(name)
    return {"ok": True, "name": name, "w": body.w, "h": body.h, "widgets": len(body.widgets)}


@core.app.delete("/api/panels/{name}")
def delete_panel(name: str, request: Request):
    """The layout, the tablets bound to it (their keys die with them) and its retained topics."""
    core.house_token_only(request)
    with _lock:
        _need(name)
        _layouts.pop(name)
        _save_layouts()
        db = _pair_load(PAIR_DB, {})
        gone = sorted(k for k, d in db.items() if isinstance(d, dict) and d.get("panel") == name)
        for k in gone:
            db.pop(k)
            _BEATS.pop(k, None)
        if gone:
            _pair_save(PAIR_DB, db)
    if _esp._jobs.pop(name, None) is not None:
        _esp._save_jobs()
    _esp._drop_account(name)
    keys = _esp._keys()
    if keys.pop(name, None) is not None:
        _esp._save_keys(keys)
    _AWAY.pop(name, None)
    cleared = _clear_retained(name)
    core.log.info(f"[panels] deleted {name}: unpaired {gone or '-'}, retained {cleared}")
    return {"ok": True, "deleted": name, "unpaired": gone, "retained_cleared": cleared}


class RenameBody(core.Body):
    to: str


@core.app.post("/api/panels/{name}/rename")
def rename_panel(name: str, body: RenameBody):
    """Only an Android panel: an ESP panel's name is compiled into its firmware with all its topics."""
    to = (body.to or "").strip()
    if not NAME_RE.fullmatch(to):
        raise HTTPException(400, "panel name: 1-32 latin letters, digits, - or _")
    with _lock:
        if _need(name).get("hw") != "android":
            raise HTTPException(400, "only an Android panel can be renamed; an ESP panel's name is in its firmware")
        if to == name:
            return {"ok": True, "panel": to}
        if _taken_panel(to, but=name) or _resolve(to) != to:
            raise HTTPException(409, f"panel '{to}' already exists")
        rec = _layouts.pop(name)
        rec["was"] = sorted(set(rec.get("was") or []) | {name})
        _layouts[to] = rec
        _save_layouts()
        db = _pair_load(PAIR_DB, {})
        moved = sorted(k for k, d in db.items() if isinstance(d, dict) and d.get("panel") == name)
        for k in moved:
            db[k]["panel"] = to
        if moved:
            _pair_save(PAIR_DB, db)
    _clear_retained(name)
    core.log.info(f"[panels] renamed {name} -> {to}, devices {moved or '-'}")
    return {"ok": True, "panel": to, "devices": moved}


class RoomBody(core.Body):
    room: Optional[str] = None


@core.app.put("/api/panels/{name}/room")
@_locked
def set_room(name: str, body: RoomBody):
    """The room a panel hangs in (None = none); the Panels tab groups by it."""
    p = _need(name)
    if body.room is not None and core.find_room(body.room) < 0:
        raise HTTPException(404, "unknown room")
    if body.room is None:
        p.pop("room", None)
    else:
        p["room"] = body.room
    _save_layouts()
    return {"ok": True, "panel": name, "room": body.room}


core.ROOM_REFS.append(lambda rid: [{"kind": "panel", "name": p.get("title") or n} for n, p in _layouts.items() if p.get("room") == rid])


def _on_rename(old: str, new: str):
    """Widgets carry full MQTT topics, and a charger is a device: both follow a device rename."""
    hit = set()
    with _lock:
        for pname, lay in _layouts.items():
            for w in lay.get("widgets", []):
                if w.get("device") == old:
                    w["device"] = new
                    hit.add(pname)
                for k in _TOPIC_KEYS:
                    t = w.get(k)
                    if isinstance(t, str) and f"/{old}/" in t:
                        w[k] = t.replace(f"/{old}/", f"/{new}/", 1)
                        hit.add(pname)
            if (lay.get("charger") or {}).get("device") == old:
                lay["charger"]["device"] = new
                hit.add(pname)
        if hit:
            _save_layouts()
            for pname in hit:
                _publish(pname)


core.ON_RENAME.append(_on_rename)


def _merge_plan(old: str, new: str) -> dict:
    with _lock:
        n = sum(1 for lay in _layouts.values() for w in lay.get("widgets", [])
                if w.get("device") == old or any(isinstance(w.get(k), str) and f"/{old}/" in w[k] for k in _TOPIC_KEYS))
        n += sum(1 for lay in _layouts.values() if (lay.get("charger") or {}).get("device") == old)
    return {"panels: widgets": n} if n else {}


core.MERGE_PLAN.append(_merge_plan)


class PairingBody(core.Body):
    minutes: int = 5
    off: bool = False


@core.app.post("/api/panels/pairing")
def pairing(body: PairingBody):
    """The discovery module (discover.py) invites panels while this window is open; the word ties answers to it."""
    until = 0 if body.off else time.time() + max(1, min(30, body.minutes)) * 60
    _pair_save(PAIR_FLAG, {"until": until, "magic": "" if body.off else secrets.token_hex(8)}, 0o644)
    return {"ok": True, "seconds": max(0, int(until - time.time()))}


@core.app.get("/api/panels/discovered")
def discovered():
    d = _pair_load(PAIR_SEEN, {})
    flag = _pair_load(PAIR_FLAG, {})
    return {"devices": d.get("devices") or [], "updated": d.get("updated"),
            "pairing": max(0, int(float(flag.get("until") or 0) - time.time())),
            "registered": [{**_public(_live(k, r)), "watch": _watch_of(r), "watch_last": _WATCH.get(k)}
                           for k, r in _pair_load(PAIR_DB, {}).items() if isinstance(r, dict)],
            "esp": _esp.new_list()}


def _public(r: dict) -> dict:
    """A paired record as the dashboard sees it: no key, no code; a bound tablet asking to pair again
    shows as pending (its old binding works until a human checks the new code)."""
    out = {k: v for k, v in r.items() if k not in ("key_sha256", "code", "repair", "cam_key")}
    if isinstance(r.get("repair"), dict):
        out.update(status="pending", repairs=r.get("panel", ""), ip=r["repair"].get("ip"))
    return out


class RegisterBody(core.Body):
    id: str = Field(max_length=64)
    code: str = Field(max_length=16)
    name: str = Field("", max_length=64)
    model: str = Field("", max_length=64)
    android: str = Field("", max_length=32)
    w: int = 0
    h: int = 0
    cams: list = Field([], max_length=8)
    mic: bool = False
    caps: dict = {}


CAPS_MAX = 4096
MAX_PENDING = 20


PENDING_PER_IP = 5
_CAPS_KEYS = {"screen", "cameras", "mic", "speaker", "battery", "charging", "ip", "hold", "app", "app_code", "update_error", "owner", "home_pinned", "android", "ram",
              "sensors", "bt", "cam_port", "charge_ctl", "features", "multitouch", "voice", "voice_port", "wake",
              "sdk", "dpi", "inch", "model", "adb", "dnd", "front", "pkg"}


def _small(caps: dict, cams: list = ()) -> dict:
    """The passport's known keys only, and small."""
    caps = {k: v for k, v in caps.items() if k in _CAPS_KEYS}
    if len(json.dumps([caps, list(cams)])) > CAPS_MAX:
        raise HTTPException(413, f"caps: at most {CAPS_MAX} bytes")
    return caps


def _direct(request: Request):
    """Pairing goes by the tablet's own address: through a reverse proxy every tablet has the proxy's."""
    if any(h in request.headers for h in ("x-forwarded-for", "forwarded", "x-real-ip")):
        raise HTTPException(403, "pair a tablet with the dashboard's own address, not through a reverse proxy")


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@core.app.post("/api/panels/register")
@_locked
def register(body: RegisterBody, request: Request):
    """The tablet introduces itself with its passport (caps). No key here: a human checks its code first."""
    _direct(request)
    if time.time() > float(_pair_load(PAIR_FLAG, {}).get("until", 0)):
        raise HTTPException(403, "pairing is off")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", body.id) or not re.fullmatch(r"[A-Za-z0-9]{1,16}", body.code):
        raise HTTPException(400, "bad id or code")
    caps = _small(body.caps, body.cams)
    db = _pair_load(PAIR_DB, {})
    now, ip = time.time(), _ip(request)
    for k in [k for k, d in db.items() if isinstance(d, dict) and d.get("status") != "bound"
              and now - float(d.get("seen") or 0) > KEY_WINDOW_S]:
        db.pop(k)
    for d in db.values():                         # a pairing again nobody confirmed: the record stays as it was
        if isinstance(d, dict) and isinstance(d.get("repair"), dict) and now - float(d["repair"].get("seen") or 0) > KEY_WINDOW_S:
            d.pop("repair")
    pending = sorted((float(d.get("seen") or 0), k, d.get("ip")) for k, d in db.items()
                     if isinstance(d, dict) and d.get("status") != "bound" and k != body.id)
    if body.id not in db and sum(1 for p in pending if p[2] == ip) >= PENDING_PER_IP:
        raise HTTPException(429, f"at most {PENDING_PER_IP} devices waiting per address")
    for t, k, _ip_ in pending[:max(0, len(pending) - MAX_PENDING + 1)]:
        if now - t < 60:
            raise HTTPException(429, "too many devices waiting: try again in a minute")
        db.pop(k)
    old = db.get(body.id) or {}
    if old.get("status") == "bound":
        rep = old.get("repair") if isinstance(old.get("repair"), dict) else None
        if rep is None and body.code == old.get("code") and not old.get("key_sha256"):
            return {"status": "bound"}            # the bound tablet's own register, before its next poll
        if rep is not None and rep.get("ip") != ip and now - float(rep.get("seen") or 0) < KEY_WINDOW_S:
            raise HTTPException(409, "this id was just registered from another address")
        # the same tablet again (reinstalled): its record, in place, once a human checks the new code
        old["repair"] = {"code": body.code, "name": body.name, "caps": caps, "cams": body.cams, "ip": ip, "seen": now}
        _pair_save(PAIR_DB, db)
        return {"status": "pending"}
    if old.get("ip") not in (None, _ip(request)) and time.time() - float(old.get("seen") or 0) < KEY_WINDOW_S:
        raise HTTPException(409, "this id was just registered from another address")
    db[body.id] = {**body.model_dump(), "caps": caps, "ip": ip, "seen": now, "status": "pending"}
    _pair_save(PAIR_DB, db)
    return {"status": "pending"}


core.open_route(register)


@core.app.get("/api/panels/register/{dev_id}")
@_locked
def register_poll(dev_id: str, request: Request):
    """Open (the tablet has no key yet): the key goes out once, within KEY_WINDOW_S of the bind,
    and only to the address the tablet registered from. A device id alone gets nothing."""
    _direct(request)
    db = _pair_load(PAIR_DB, {})
    d = db.get(dev_id)
    if not isinstance(d, dict):
        raise HTTPException(404, "unknown device")
    if d.get("status") != "bound" or isinstance(d.get("repair"), dict):
        return {"status": "pending" if d.get("repair") else d.get("status", "pending")}
    out = {"status": "bound", "panel": d.get("panel", ""), "url": str(request.base_url).rstrip("/")}
    fresh = time.time() - float(d.get("bound_at") or 0) < KEY_WINDOW_S
    if (d.get("key_sha256") and not d.get("rekey")) or not fresh or not d.get("ip") or d.get("ip") != _ip(request):
        return out
    key = PREFIX + secrets.token_urlsafe(32)
    d["key_sha256"], d["key_given"] = _sha(key), time.time()
    d.pop("rekey", None)
    _pair_save(PAIR_DB, db)
    core.log.warning(f"[panels] key handed to {dev_id} at {_ip(request)}")
    return {**out, "token": key}


core.open_route(register_poll)


_CAPS_VOLATILE = ("battery", "charging", "ip", "hold", "adb", "dnd", "front")


def _durable(caps):
    """The passport without what changes all the time (that stays in paired.json only)."""
    return {k: v for k, v in (caps or {}).items() if k not in _CAPS_VOLATILE}


def _can(caps):
    can = ["clock", "date", "label", "sensor", "toggle", "dimmer", "button", "weather", "ac", "remote", "touchmouse"]
    return can + (["camera"] if caps.get("cameras") else []) + (["voice"] if caps.get("mic") else [])


class BindBody(core.Body):
    id: str
    code: str
    panel: str
    keep_key: bool = False      # a bound tablet moved to a panel: its key stays, it learns the panel from its heartbeat


@core.app.post("/api/panels/bind")
@_locked
def bind(body: BindBody, request: Request):
    """A human checked the code against the tablet's screen. The canvas is the tablet's own screen,
    landscape; portrait is the layout's rotation. A tablet paired again (the same id) keeps its record:
    a new key, the old one revoked."""
    core.house_token_only(request)
    db = _pair_load(PAIR_DB, {})
    d = db.get(body.id)
    if not isinstance(d, dict):
        raise HTTPException(404, "unknown device")
    rep = d.get("repair") if isinstance(d.get("repair"), dict) else None
    if not hmac.compare_digest(str((rep or d).get("code", "")).encode(), body.code.encode()):
        raise HTTPException(400, "code mismatch")
    if not NAME_RE.fullmatch(body.panel):
        raise HTTPException(400, "panel name: 1-32 latin letters, digits, - or _")
    if (_layouts.get(body.panel) or {}).get("hw", "android") != "android":
        raise HTTPException(409, f"panel '{body.panel}' is an ESP panel")
    if rep:
        d.update({k: rep[k] for k in ("code", "caps", "cams", "ip", "seen") if k in rep},
                 name=d.get("name") if d.get("name_set") else rep.get("name") or d.get("name"))   # a name set in the house stays
        d.pop("repair")
        core.log.warning(f"[panels] {body.id} paired again: the same record, a new key")
    short = body.id[:12]                          # cutover-compat: its record from an older launcher (short id)
    if short != body.id and body.id.startswith("and-") and isinstance(db.get(short), dict):
        gone = db.pop(short)
        _rename_refs(short, body.id)
        core.log.warning(f"[panels] {short} (an older launcher's id, panel {gone.get('panel', '-')}) is {body.id}: "
                         "its old record and key go")
    for name, lay in _layouts.items():            # a tablet serves one panel: off the one it served before
        if name != body.panel and lay.get("device") == body.id:
            for k in ("device", "caps", "can"):
                lay.pop(k, None)
    keep = body.keep_key and d.get("status") == "bound" and not rep and bool(d.get("key_sha256"))
    d.update(status="bound", panel=body.panel, bound_at=time.time())
    if keep:
        d["rekey"] = True                         # its key works on; should it poll from its pairing screen, it gets a new one
    else:
        d.pop("key_sha256", None)                 # a new bind, a new key: the old one dies here
        d.pop("rekey", None)
    _pair_save(PAIR_DB, db)
    caps = d.get("caps") or {}
    sc = caps.get("screen") or {}
    w, h = int(sc.get("w") or d.get("w") or 1024), int(sc.get("h") or d.get("h") or 600)
    w, h = max(16, min(4096, max(w, h))), max(16, min(4096, min(w, h)))
    p = _layouts.setdefault(body.panel, {})
    p.setdefault("title", str(d.get("name") or d.get("model") or body.panel)[:40])
    p.setdefault("widgets", [])
    p.update(hw="android", w=w, h=h, device=body.id, caps=_durable(caps), can=_can(caps))
    _save_layouts()
    return {"ok": True, "panel": body.panel, "w": w, "h": h, "can": _can(caps)}


class HomePinBody(core.Body):
    home_pin: Optional[bool] = None
    name: Optional[str] = None           # the tablet's name in the house (it outlives a new pairing)


@core.app.patch("/api/panels/bind/{dev_id}")
@_locked
def home_pin(dev_id: str, body: HomePinBody, request: Request):
    """Settings -> Panels: the tablet keeps its launcher as HOME (pinned, as a device owner) or releases it (another HOME may be chosen);
    the launcher takes it at its next heartbeat."""
    core.house_token_only(request)
    db = _pair_load(PAIR_DB, {})
    d = db.get(dev_id)
    if not isinstance(d, dict) or d.get("status") != "bound":
        raise HTTPException(404, "unknown device")
    if body.home_pin is None and body.name is None:
        raise HTTPException(400, "nothing to change")
    if body.name is not None:
        d["name"] = core.check_name(body.name, "tablet", [x.get("name") for k, x in db.items() if k != dev_id and isinstance(x, dict)], most=64)
        d["name_set"] = True
    if body.home_pin is not None:
        d["home_pin"], d["pin_seq"] = body.home_pin, int(d.get("pin_seq") or 0) + 1  # pressed again: taken even if it equals the last wish
    _pair_save(PAIR_DB, db)
    return {"ok": True, "home_pin": d.get("home_pin", True), "name": d.get("name")}


@core.app.delete("/api/panels/bind/{dev_id}")
@_locked
def unbind(dev_id: str, request: Request):
    core.house_token_only(request)
    db = _pair_load(PAIR_DB, {})
    if db.pop(dev_id, None) is None:
        raise HTTPException(404, "unknown device")
    _BEATS.pop(dev_id, None)
    _pair_save(PAIR_DB, db)
    for p in _layouts.values():
        if p.get("device") == dev_id:
            for k in ("device", "caps", "can"):
                p.pop(k, None)
            _save_layouts()
    return {"ok": True}


def _migrate_id(db: dict, dev_id: str, request: Request):
    """cutover-compat: launchers before the full ANDROID_ID sent "and-" + its first 8 characters. A bound
    record under that short id, whose own key this request holds, takes the full id in place."""
    short = dev_id[:12]
    old = db.get(short)
    if dev_id == short or dev_id in db or not dev_id.startswith("and-") or not isinstance(old, dict):
        return
    if old.get("status") != "bound" or _device_of(_key_of(request))[0] != short:
        return
    db[dev_id] = db.pop(short)
    if short in _BEATS:
        _BEATS[dev_id] = _BEATS.pop(short)
    _rename_refs(short, dev_id)
    _save_layouts()
    _pair_save(PAIR_DB, db)
    core.log.warning(f"[panels] {short} is {dev_id} now (the full ANDROID_ID)")


def _rename_refs(old: str, new: str):
    """A tablet's id changed: the layouts follow (the tablet a panel shows on, the cameras widgets use)."""
    for p in _layouts.values():
        if p.get("device") == old:
            p["device"] = new
        for w in p.get("widgets") or []:
            if isinstance(w, dict) and isinstance(w.get("cam_id"), str) and w["cam_id"].startswith(old + "-"):
                w["cam_id"] = new + w["cam_id"][len(old):]


_BEATS: dict = {}           # tablet id -> its last heartbeat (ip, seen, beat, caps): kept in memory
BEAT_SAVE_S = 1800          # written to paired.json at most this often, or when the address or the passport changes


def _live(did: str, d: dict) -> dict:
    """A paired record with its latest heartbeat."""
    return {**d, **_BEATS.get(did, {})} if isinstance(d, dict) else d


class HeartbeatBody(core.Body):
    id: str
    caps: dict = {}


def _orientation(panel: str) -> str:
    """The canvas shape picks landscape or portrait; rotation 180/270 only flips it."""
    lay = _layouts.get(panel) or {}
    base = "portrait" if (lay.get("h") or 0) > (lay.get("w") or 0) else "landscape"
    return ("reverse_" + base) if (lay.get("rotation") or 0) in (180, 270) else base


CHARGE_MIN = 40                 # the charge window of a tablet without a charger plug of its own (percent)
CHARGE_MAX = 85


def _charge_window(panel: str) -> dict:
    cfg = (_layouts.get(panel) or {}).get("charger") or {}
    return {"min": cfg.get("min", CHARGE_MIN), "max": cfg.get("max", CHARGE_MAX)}


_charger_away: set = set()       # charger plugs found offline (logged once an outage)


def _charge_decision(batt, plug_on, lo, hi):
    """True = switch the charger's plug on, False = off, None = leave it (between the thresholds)."""
    if not isinstance(batt, (int, float)) or isinstance(batt, bool):
        return None
    if batt >= hi and plug_on is not False:
        return False
    if batt <= lo and plug_on is not True:
        return True
    return None


def _charge_guard(panel: str, caps: dict):
    """A tablet without root cannot stop charging; a smart plug on its charger can."""
    cfg = (_layouts.get(panel) or {}).get("charger")
    if not cfg or cfg.get("device") not in core.DEVICES:
        return None
    dev, code = cfg["device"], cfg.get("code", "state")
    cur = ((core.status_cache.get(dev) or {}).get("values") or {}).get(code)
    want = _charge_decision(caps.get("battery"), cur, cfg.get("min", CHARGE_MIN), cfg.get("max", CHARGE_MAX))
    if want is None or core.main_loop is None:
        return None
    if (core.status_cache.get(dev) or {}).get("offline_since"):    # the plug is away: nothing would happen
        if dev not in _charger_away:
            _charger_away.add(dev)
            core.log.warning(f"[panels] {panel}: battery {caps.get('battery')} %, but the charger {dev} is offline")
        return None
    _charger_away.discard(dev)
    fut = asyncio.run_coroutine_threadsafe(core.control(dev, core.ControlBody(code=code, value=want)), core.main_loop)

    def said(f, what=f"[panels] {panel}: battery {caps.get('battery')} % -> {dev}/{code} {'on' if want else 'off'}"):
        e = f.exception()                           # a charger plug that is offline is refused (409)
        (core.log.warning(f"{what} not done: {getattr(e, 'detail', e)}") if e else core.log.info(what))
    fut.add_done_callback(said)
    return want


# ---- a tablet as a voice satellite (docs/voice.md) ----
# The voice plug-in commands a tablet on panel/<id>/voice/cmd {"t": "say/url"|"stop/command"|"volume/set"|"listen/command", "p": payload};
# the tablet holds a long-poll open (GET /api/panels/voice/next) and takes them from here; what the tablet reports (wake, player,
# listen, volume) goes out on the topics of an ESP satellite (<node>/wake, <node>/player/state, ...), so the voice plug-in sees one kind.
VOICE_TTL_S = 30                 # a command older than this is dropped: a chime that comes late is worse than none
VOICE_POLL_S = 25
_VQ: dict = {}                   # device id -> list of (time, command)
_VCOND = threading.Condition()
_VSEEN: dict = {}                # device id -> last long-poll time
_VSTATUS: dict = {}              # device id -> the status last published


def voice_node(devid: str) -> str:
    """The node name of a tablet satellite: the voice plug-in registers it under exactly this name."""
    return "panel-" + re.sub(r"[^a-z0-9_-]+", "-", devid.lower()).strip("-")


def _voice_cmd(parts, payload, retain) -> None:
    """The panel/ topics: panel/<id>/voice/cmd queues a command for that tablet, every other one is the ESP side's
    (the core hands a topic to the FIRST handler of a prefix, so this plug-in has exactly one for panel/)."""
    if not (len(parts) == 4 and parts[2:] == ["voice", "cmd"]):
        _esp._on_mqtt(parts, payload, retain)
        return
    if retain:
        return
    try:
        cmd = json.loads(payload)
        if not isinstance(cmd, dict) or not isinstance(cmd.get("t"), str):
            return
    except ValueError:
        return
    with _VCOND:
        q = _VQ.setdefault(parts[1], [])
        q.append((time.time(), {"t": cmd["t"], "p": str(cmd.get("p", ""))[:500]}))
        del q[:-20]
        _VCOND.notify_all()


core.MQTT_SUBSCRIPTIONS.append(f"{NS}/+/voice/cmd")
core.MQTT_HANDLERS.append(((NS,), _voice_cmd))


def _voice_publish(devid: str, sub: str, value: str, retain: bool = False) -> None:
    try:
        core.mqtt_pub(core.mqtt_client, f"{voice_node(devid)}/{sub}", value, retain)
    except Exception as e:                                   # noqa: BLE001  no broker: the tablet tries again with its next poll
        core.log.warning(f"[panels] voice event {devid}/{sub}: {e}")


def _voice_status(devid: str, online: bool) -> None:
    if _VSTATUS.get(devid) != online:
        _VSTATUS[devid] = online
        _voice_publish(devid, "status", "online" if online else "offline", True)


def _voice_watch() -> None:
    """A tablet that stopped polling is offline for the voice plug-in."""
    while True:
        time.sleep(10)
        for devid, seen in list(_VSEEN.items()):
            if _VSTATUS.get(devid) and time.time() - seen > VOICE_POLL_S + 15:
                _voice_status(devid, False)


threading.Thread(target=_voice_watch, daemon=True, name="panels-voice-watch").start()


def _voice_panel(request: Request, devid: str) -> None:
    """The house token, or the key of THAT panel; a bound panel only."""
    d = _pair_load(PAIR_DB, {}).get(devid)
    if not isinstance(d, dict) or d.get("status") != "bound":
        raise HTTPException(404, "unknown device")
    if not core.is_house_token(request.headers) and _device_of(_key_of(request))[0] != devid:
        raise HTTPException(403, "this key belongs to another panel")


@core.app.get("/api/panels/voice/next")
def voice_next(request: Request, id: str, wait: int = VOICE_POLL_S):
    """The tablet's long-poll: the commands the voice plug-in queued for it, or an empty list after `wait` seconds."""
    _voice_panel(request, id)
    _VSEEN[id] = time.time()
    _voice_status(id, True)
    end = time.time() + max(0, min(int(wait), VOICE_POLL_S))
    with _VCOND:
        while True:
            q = _VQ.get(id) or []
            now = time.time()
            q[:] = [(t, c) for t, c in q if now - t <= VOICE_TTL_S]
            if q or now >= end:
                _VQ[id] = []
                return {"cmds": [c for _t, c in q]}
            _VCOND.wait(min(1.0, end - now))


class VoiceEventBody(core.Body):
    id: str = Field(min_length=1, max_length=64)
    e: Literal["wake", "player", "listen", "volume"]
    v: str = Field(default="", max_length=40)


_VOICE_EVENTS = {"wake": "wake", "player": "player/state", "listen": "listen/state", "volume": "volume/state"}


@core.app.post("/api/panels/voice/event")
def voice_event(body: VoiceEventBody, request: Request):
    """What the tablet tells the voice plug-in: a wake word, the player state (playing / idle), listening ON / OFF, the volume."""
    _voice_panel(request, body.id)
    _VSEEN[body.id] = time.time()
    _voice_publish(body.id, _VOICE_EVENTS[body.e], body.v or ("wake" if body.e == "wake" else ""), body.e in ("listen", "volume"))
    return {"ok": True}


@core.app.post("/api/panels/heartbeat")
@_locked
def heartbeat(body: HeartbeatBody, request: Request):
    """The tablet checks in (address, battery); it learns its panel, charge window and orientation."""
    db = _pair_load(PAIR_DB, {})
    _migrate_id(db, body.id, request)
    d = db.get(body.id)
    if not isinstance(d, dict) or d.get("status") != "bound":
        raise HTTPException(404, "unknown device")
    if not core.is_house_token(request.headers) and _device_of(_key_of(request))[0] != body.id:
        raise HTTPException(403, "this key belongs to another panel")
    now, ip = time.time(), _ip(request) or d.get("ip", "")
    caps = _small(body.caps) if body.caps else {}
    _BEATS[body.id] = {"ip": ip, "seen": now, "beat": now, **({"caps": caps} if caps else {})}
    save = ip != d.get("ip") or not d.get("cam_key") or now - float(d.get("beat") or 0) > BEAT_SAVE_S \
        or (caps and _durable(caps) != _durable(d.get("caps")))
    d["ip"], d["seen"], d["beat"] = ip, now, now
    panel = d.get("panel", "")
    if caps:
        d["caps"] = caps
        pan = _layouts.get(panel)
        if pan is not None and pan.get("caps") != _durable(caps):
            pan["caps"], pan["can"] = _durable(caps), _can(caps)
            _save_layouts()
    if not d.get("cam_key"):
        d["cam_key"] = secrets.token_urlsafe(24)       # the tablet's cameras open only to the house's proxy
    if save:                                           # a beat a minute stays in memory: no flash write each time
        _pair_save(PAIR_DB, db)
    if body.caps:
        _charge_guard(panel, body.caps)
    out = {"ok": True, "panel": panel, "charge": _charge_window(panel), "orientation": _orientation(panel), "cam_key": d["cam_key"],
           "home_pin": d.get("home_pin", True), "home_pin_seq": d.get("pin_seq", 0)}   # the house's wish; a new seq = pressed again
    if caps and _needs_update(caps):
        out["update"] = True                           # a launcher that knows it fetches the published one now
    move = _owner_move(body.id, caps, now)
    if move:
        out["transfer_owner"] = move
    return out


MOVE_EVERY_S, MOVE_CONFIRM_S = 600, 120
_MOVES: dict = {}                                      # tablet id -> when it was last asked to move to APP_ID
_ODD_PKG: dict = {}                                    # tablet id -> a package neither the house's nor listed to move
MIGRATE_FROM = {p.strip() for p in os.environ.get("PANELS_MIGRATE_FROM", "").split(",") if p.strip()}


def _owner_move(did: str, caps: dict, now: float):
    """A launcher of a package listed in PANELS_MIGRATE_FROM is asked to hand the tablet over to the house's (it does
    nothing while that package is not installed there); asked again every MOVE_EVERY_S. One way only: a tablet on the
    house's package, or on one not listed, is never asked. The house's package beating ends it."""
    pkg = (caps or {}).get("pkg")
    if not APP_ID or not pkg:
        return None
    if pkg == APP_ID:
        _ODD_PKG.pop(did, None)
        if _MOVES.pop(did, None) is not None:
            core.log.info(f"[panels] {did}: the tablet is on {APP_ID} now")
        return None
    if pkg not in MIGRATE_FROM:
        _ODD_PKG[did] = pkg
        return None
    _ODD_PKG.pop(did, None)
    if now - _MOVES.get(did, -MOVE_EVERY_S) < MOVE_EVERY_S:
        return None
    _MOVES[did] = now
    core.log.info(f"[panels] {did}: launcher {pkg} asked to move the tablet to {APP_ID}")
    return APP_ID


def _move_health() -> list:
    """The old launcher hands over and goes quiet: the house's package must beat within MOVE_CONFIRM_S."""
    now, rows = time.time(), []
    for did, at in list(_MOVES.items()):
        if float((_BEATS.get(did) or {}).get("beat") or 0) <= at and now - at > MOVE_CONFIRM_S:
            rows.append(core.health_row(f"panels-move-{did}", "warn", "Tablet launcher move",
                                        "{0}: no heartbeat since it was asked to move to {1}",
                                        "Open the tablet over adb: is {1} installed and the device owner?", (did, APP_ID)))
    for did, pkg in list(_ODD_PKG.items()):
        rows.append(core.health_row(f"panels-pkg-{did}", "warn", "Tablet launcher package",
                                    "{0}: its launcher is {1}, neither {2} nor listed to move (PANELS_MIGRATE_FROM)",
                                    "Add {1} to PANELS_MIGRATE_FROM to move it, or install {2} there", (did, pkg, APP_ID)))
    return rows


core.HEALTH_CHECKS.append(_move_health)


CAM_PORTS = {8081}


def _gateway() -> str:
    """This container's default gateway (the docker host side): never a camera address."""
    try:
        for line in open("/proc/net/route").read().splitlines()[1:]:
            f = line.split()
            if f[1] == "00000000":
                return socket.inet_ntoa(struct.pack("<L", int(f[2], 16)))
    except (OSError, ValueError, IndexError):
        pass
    return ""


def _camera_host(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    a = getattr(a, "ipv4_mapped", None) or a
    return not (a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast) and str(a) != _gateway()


def _panel_cameras() -> dict:
    """Cameras of the bound tablets as cameras of the house: the address is the one of the last heartbeat,
    so a DHCP move breaks nothing and unbinding removes them. The launcher serves /snapshot and /stream."""
    out = {}
    for did, d in list(_db().items()):
        d = _live(did, d)
        if not isinstance(d, dict) or d.get("status") != "bound" or not d.get("beat") \
                or not _camera_host(str(d.get("ip") or "")):
            continue
        caps = d.get("caps") or {}
        port = caps.get("cam_port") or 8081
        if isinstance(port, bool) or port not in CAM_PORTS:
            continue
        title = (_layouts.get(d.get("panel")) or {}).get("title") or d.get("name") or did
        for cam in caps.get("cameras") or []:
            f = cam.get("facing") if isinstance(cam, dict) else None
            if f not in ("front", "back"):
                continue
            base = f"http://{d['ip']}:{port}"
            out[f"{did}-{f}"] = {"name": f"{title} - {'front' if f == 'front' else 'rear'}",
                                  "snapshot": f"{base}/snapshot?cam={f}", "stream": f"{base}/stream?cam={f}",
                                  "headers": {"X-Cam-Key": d.get("cam_key", "")}}
    return out


_cameras = sys.modules.get(__name__.rsplit(".", 1)[0] + ".cameras")
if _cameras is not None and hasattr(_cameras, "register_source"):
    _cameras.register_source(_panel_cameras)


class ChargerBody(core.Body):
    device: Optional[str] = None
    code: str = "state"
    max: int = CHARGE_MAX
    min: int = CHARGE_MIN


@core.app.put("/api/panels/{name}/charger")
@_locked
def set_charger(name: str, body: ChargerBody):
    """Charge thresholds; with a device, the plug of the charger that switches at them."""
    p = _need(name)
    if not (5 <= body.min < body.max <= 100):
        raise HTTPException(400, "thresholds: 5 <= min < max <= 100")
    entry = {"max": body.max, "min": body.min}
    if body.device:
        cfg = core.DEVICES.get(body.device)
        if not cfg:
            raise HTTPException(404, "unknown device")
        ctl = cfg["_ctl"].get(body.code)
        if not ctl or ctl.get("kind") != "switch" or ctl.get("ro"):
            raise HTTPException(400, f"'{body.code}' is not a switch of '{cfg.get('name') or body.device}'")
        entry.update(device=body.device, code=body.code)
    p["charger"] = entry
    _save_layouts()
    return {"ok": True, "panel": name, "charger": entry}


@core.app.delete("/api/panels/{name}/charger")
@_locked
def clear_charger(name: str):
    had = _need(name).pop("charger", None)
    _save_layouts()
    return {"ok": True, "removed": had}


def _page(fname: str):
    return FileResponse(os.path.join(HERE, "static", fname), media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


@core.app.get("/panel-view")
@core.app.get("/panel-view.html")
def panel_view():
    """The address already typed into the kiosk launchers: kept. The page may show the launcher's own camera
    stream (MJPEG on its loopback port 8081): the core's img-src would block it, so this page alone allows it."""
    resp = _page("view.html")
    resp.headers["Content-Security-Policy"] = core.csp.replace("img-src 'self' data: blob:",
                                                                 "img-src 'self' data: blob: http://127.0.0.1:8081")
    return resp


@core.app.get("/panel-builder")
def panel_builder():
    return _page("builder.html")


core.OPEN_PATHS.update({"/panel-view", "/panel-view.html", "/panel-builder"})


from . import _apkinfo  # noqa: E402
from . import _esp  # noqa: E402  the ESP side needs everything above
_HW.update({k: {**{key: v[key] for key in ("label", "kind", "chip", "w", "h", "can")}, "firmware": True}
            for k, v in _esp.gen.profiles().items()})


LAUNCHER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher")
APP_ID = (os.environ.get("PANELS_APP_ID") or "").strip() or None
_app_holder: dict = {}


def _launcher_dir():
    """The house's own launcher project (plugin-data/panels/launcher) when it has one, else the shipped one."""
    own = core.plugin_data("panels") / "launcher"
    return own if (own / "app" / "build.gradle.kts").is_file() else LAUNCHER_DIR


def _app():
    """The Home Panel launcher's build (the build plug-in's AppBuild): key and APK in plugin-state/panels/."""
    mod = sys.modules.get(__package__.rsplit(".", 1)[0] + ".build")
    if mod is None:
        raise HTTPException(503, "the build plug-in is off: PLUGINS in .env needs build (and a computer with the Android app builder)")
    if _app_holder.get("mod") is not mod:
        _app_holder.update(mod=mod, app=mod.AppBuild("panels", "home-panel", _launcher_dir(), "panel.apk", app_id=APP_ID))
    return _app_holder["app"]


@core.app.post("/api/panels/launcher")
def panels_app_build(request: Request):
    """Build the Home Panel launcher on the build server's app builder, signed with the house's key."""
    core.house_token_only(request)
    return _app().start()


@core.app.get("/api/panels/launcher")
def panels_app_build_state(request: Request):
    """The build's state, and the published launcher's version (apk_name, apk_code) for the Panels row."""
    core.house_token_only(request)
    pub = _published()
    return {**_app().status(), **({"apk_name": pub["name"], "apk_code": pub["code"]} if pub else {})}


def panels_apk():
    """The launcher for a tablet: open, like the family app (a tablet has no key before it pairs)."""
    for f in (core.plugin_state("panels") / "panel.apk", core.plugin_data("panels") / "panel.apk"):
        if f.is_file():
            return FileResponse(f, media_type="application/vnd.android.package-archive", filename="home-panel.apk")
    raise HTTPException(404, "no launcher here yet: Panels -> Build the app")


core.app.add_api_route("/plugins/panels/panel.apk", panels_apk, methods=["GET", "HEAD"])
core.open_route(panels_apk)


_published_cache: dict = {}


def _published():
    """The published launcher's {code, name, ...}, read again only when the file changes (it is hashed whole); None when there is none."""
    for f in (core.plugin_state("panels") / "panel.apk", core.plugin_data("panels") / "panel.apk"):
        if f.is_file():
            st = f.stat()
            key = (str(f), st.st_mtime_ns, st.st_size)
            if _published_cache.get("key") != key:
                try:
                    _published_cache.update(key=key, info=_apkinfo.describe(f))
                except Exception:                     # noqa: BLE001  a broken file: no update is offered
                    _published_cache.update(key=key, info=None)
            return _published_cache["info"]
    _published_cache.clear()
    return None


def _needs_update(caps: dict) -> bool:
    """The tablet's launcher is older than the published one: by build number when it sends one, else by a version name that differs.
    A newer tablet (a launcher installed by hand) is not told to update, nor one on another package (it moves instead)."""
    pub = _published()
    if not pub:
        return False
    if caps.get("pkg") and pub.get("package") and caps["pkg"] != pub["package"]:
        return False
    code = caps.get("app_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return code < pub.get("code", 0)
    return isinstance(caps.get("app"), str) and bool(caps["app"]) and caps["app"] != pub.get("name")


def panels_app_version():
    """The published launcher's version, for a panel that updates itself (open: nothing secret in it; read once per file change).
    The panel compares `package` and `code` with its own, downloads /plugins/panels/panel.apk and checks size and sha256."""
    pub = _published()
    if pub is None and _published_cache.get("key"):
        raise HTTPException(500, "cannot read the launcher's version")
    if pub is None:
        raise HTTPException(404, "no launcher here yet: Panels -> Build the app")
    return pub


core.app.add_api_route("/plugins/panels/app", panels_app_version, methods=["GET"])
core.open_route(panels_app_version)


# -- launcher watchdog: a bound tablet silent for its minutes gets its launcher started again ------------------
# Per tablet in paired.json "watch": {on, after_min, every_min} (Panels -> Tablets); a try is `am start` only, at most
# once per every_min, one tablet per tick. The host's panel-start helper does it (README "Launcher watchdog").
WATCH_DEFAULT = {"on": True, "after_min": 2, "every_min": 10}
WATCH_TICK_S = 30
PANEL_START_WAIT_S = 35             # above the helper's 30 s for all its adb calls: a slow success is not "no way in"
_watch_busy = threading.Lock()      # single flight: a slow tablet never runs two passes at once
_WATCH: dict = {}                   # tablet id -> {"asked": last call, "at": last real try, "ok", "why"}; "noadb": no way in
_WATCH_SINCE = time.time()          # after a restart the beats in memory are empty: silence counts from here


def _watch_of(d: dict) -> dict:
    w = d.get("watch") if isinstance(d.get("watch"), dict) else {}
    return {**WATCH_DEFAULT, **{k: w[k] for k in WATCH_DEFAULT if k in w}}


def start_launcher(dev_id: str, rec: dict) -> dict:
    """Start the launcher on one tablet through the host's panel-start helper (its socket /run/yashome/panel-start.sock):
    {"ok", "why"}; {"noadb", "why"} when the helper or adb cannot reach it. The helper finds the tablet's address itself;
    only the id goes there."""
    if not core.hostsock.there("panel-start"):
        return {"noadb": True, "why": "the panel-start helper is not installed on this host (tools/host-sockets.sh)"}
    try:
        ans = json.loads(core.hostsock.line("panel-start", f"start-launcher {dev_id}", PANEL_START_WAIT_S) or "{}")
    except (OSError, ValueError) as e:
        return {"noadb": True, "why": f"panel-start helper: {type(e).__name__}"}
    if ans.get("refused"):
        return {"ok": False, "why": "refused: " + str(ans.get("why") or "")}
    return ans


def _watch_tick(now: float | None = None) -> list:
    """One pass: of the bound tablets due, the one tried longest ago gets one try; returns the ids tried (one or none)."""
    if not _watch_busy.acquire(blocking=False):
        return []
    try:
        return _watch_pass(time.time() if now is None else now)
    finally:
        _watch_busy.release()


def _watch_pass(now: float) -> list:
    with _lock:
        db = _pair_load(PAIR_DB, {})
        due = []
        for did, d in db.items():
            if not isinstance(d, dict) or d.get("status") != "bound":
                continue
            w, last = _watch_of(d), _WATCH.get(did) or {}
            beat = max(float(_live(did, d).get("beat") or 0), _WATCH_SINCE)
            if not w["on"]:
                _WATCH.pop(did, None)
                continue
            if now - beat < w["after_min"] * 60:          # alive: the "no way in" hint waits for the next silence
                last.pop("noadb", None)
                continue
            if last.get("asked") and now - last["asked"] < w["every_min"] * 60:
                continue
            due.append((did, dict(_live(did, d))))
    due.sort(key=lambda x: (_WATCH.get(x[0]) or {}).get("asked") or 0)
    tried = []
    for did, rec in due[:1]:                          # one per tick: a slow tablet holds the others back one tick at most
        try:
            r = start_launcher(did, rec) or {}
        except Exception as e:                        # noqa: BLE001  a failed try is logged and waits for every_min
            r = {"ok": False, "why": f"{type(e).__name__}: {e}"}
        if r.get("noadb"):                            # nothing tried; asked again after every_min, like a try
            _WATCH[did] = {**(_WATCH.get(did) or {}), "asked": now, "noadb": True}
            core.log.info(f"[panels] watchdog {did}: no way in: {str(r.get('why') or '')[:200]}")
            continue
        _WATCH[did] = {"asked": now, "at": now, "ok": bool(r.get("ok")), "why": str(r.get("why") or "")[:200]}
        tried.append(did)
        core.log.info(f"[panels] watchdog {did}: silent {int(now - max(float(rec.get('beat') or 0), _WATCH_SINCE))} s, am start -> "
                      f"{'ok' if r.get('ok') else 'failed: ' + _WATCH[did]['why']}")
    return tried


async def _watch_loop():
    while True:
        await asyncio.sleep(WATCH_TICK_S)
        try:
            await asyncio.to_thread(_watch_tick)
        except Exception as e:                        # noqa: BLE001  the loop must outlive one bad pass
            core.log.warning(f"[panels] watchdog: {type(e).__name__}: {e}")


core.STARTUP_TASKS.append(_watch_loop)


class WatchBody(core.Body):
    on: Optional[bool] = None
    after_min: Optional[int] = Field(None, ge=1, le=60)
    every_min: Optional[int] = Field(None, ge=1, le=240)


@core.app.patch("/api/panels/bind/{dev_id}/watch")
@_locked
def watch_set(dev_id: str, body: WatchBody, request: Request):
    """Panels -> Tablets: raise the launcher when it fell (on), after how many silent minutes, at most once per every_min."""
    core.house_token_only(request)
    db = _pair_load(PAIR_DB, {})
    d = db.get(dev_id)
    if not isinstance(d, dict) or d.get("status") != "bound":
        raise HTTPException(404, "unknown device")
    d["watch"] = {**_watch_of(d), **body.model_dump(exclude_none=True)}
    _pair_save(PAIR_DB, db)
    if not d["watch"]["on"]:
        _WATCH.pop(dev_id, None)
    return {"ok": True, "watch": d["watch"]}
