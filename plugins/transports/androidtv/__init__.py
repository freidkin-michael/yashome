"""Android TV / Google TV (Sony, Philips, TCL, Xiaomi, a Chromecast with Google TV) as a dashboard device: the TVs are
the settings section androidtv_tvs, the house's client certificate lives in plugin-state/androidtv/ and each TV trusts
it after one code on its screen (paired.json lists those). One connection per paired TV: the TV pushes power, volume
and mute; commands are keys over it (WAKEUP / SLEEP for power, Wake-on-LAN when the connection is down)."""
import asyncio
import json
import os
import re
import threading
from typing import Optional

from fastapi import HTTPException, Request
from pydantic import Field

import app as core

from . import _atv as atv
from .. import tv as tv_plugin         # the remote's keys, Add a TV (file requires: tv)

PLUGIN = {"name": "Android TV", "version": "1.0.0", "icon": "tv", "menu_group": "devices"}
TRANSPORT = "androidtv"
STATE = core.plugin_state("androidtv")
CERT, KEY = STATE / "client.pem", STATE / "client.key"
PAIRED_PATH = STATE / "paired.json"
RETRY_S = 30                           # a paired TV that is not connected is tried again this often
MAX_STEPS = 60                         # volume: at most this many key presses for one change
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,63}")
_MAC = re.compile(r"([0-9a-f]{2}:){5}[0-9a-f]{2}")
_APP = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,20}://[^\s]{1,200}|[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")
_ON, _OFF = {"on", "true", "1"}, {"off", "false", "0"}

_lock = threading.RLock()
TVS: list = [t for t in core.cfg_section("androidtv_tvs", []) if isinstance(t, dict) and isinstance(t.get("id"), str)]
_conns: dict = {}                      # dev_id -> a connected client
_pairing: dict = {}                    # dev_id -> the client whose pairing waits for the code on the TV


def _load_paired() -> set:
    try:
        v = json.loads(PAIRED_PATH.read_text(encoding="utf-8"))
        return {x for x in v if isinstance(x, str)} if isinstance(v, list) else set()
    except (OSError, ValueError):
        return set()


_paired = _load_paired()


def _save_paired():
    core.atomic_json_dump(PAIRED_PATH, sorted(_paired))


def _tv(dev_id: str) -> Optional[dict]:
    return next((t for t in TVS if t["id"] == dev_id), None)


def _device(t: dict) -> dict:
    inputs, apps = t.get("inputs") or {}, t.get("apps") or {}
    ctrls = [{"kind": "switch", "code": "power", "label": "Power"},
             {"kind": "bright", "code": "volume", "label": "Volume", "min": 0, "max": 100},
             {"kind": "switch", "code": "mute", "label": "Mute"},
             {"kind": "setting_enum", "code": "media", "label": "Media", "range": list(atv.MEDIA)}]
    if inputs:
        ctrls.append({"kind": "setting_enum", "code": "input", "label": "Input", "range": list(inputs)})
    if apps:
        ctrls.append({"kind": "setting_enum", "code": "app", "label": "App", "range": list(apps)})
    ctrls += tv_plugin.key_controls(atv.REMOTE)
    return {"id": t["id"], "name": t.get("name") or t["id"], "category": "tv", "transport": TRANSPORT,
            "model": "Android TV", "wakes": True, "controls": ctrls}


def _sync():
    """The TVs of the settings -> devices of the house: new ones registered, changed ones replaced, removed ones dropped."""
    live = {t["id"] for t in TVS}
    for did in [k for k, d in list(core.DEVICES.items()) if d.get("transport") == TRANSPORT and k not in live]:
        core.remove_device(did, TRANSPORT)
        _drop(did)
    for t in TVS:
        try:
            (core.update_device if t["id"] in core.DEVICES else core.register_device)(_device(t))
        except ValueError as e:
            core.log.error(f"[androidtv] {t['id']!r} skipped: {e}")


# ---- state: pushed by the TV over its connection; no connection = off (a TV is never offline, it sleeps) ----
def _store(dev_id: str, **vals):
    cur = core.status_copy(dev_id, {"online": True, "values": {}, "raw": {}})
    cur.setdefault("values", {}).update(vals)
    cur["online"] = True
    cur.pop("stale", None)
    core.store_status(dev_id, cur)


def _volume_vals(info: dict) -> dict:
    top = int(info.get("max") or 0)
    out = {"mute": bool(info.get("muted"))}
    if top > 0:
        out["volume"] = round(int(info.get("level") or 0) * 100 / top)
    return out


def _drop(dev_id: str):
    c = _conns.pop(dev_id, None)
    if c is not None:
        try:
            c.disconnect()
        except Exception as e:                       # noqa: BLE001  closing a dead socket must not stop the caller
            core.log.debug(f"[androidtv] {dev_id}: disconnect: {e}")


async def _connect(t: dict):
    """One paired TV connected; its pushes go into the status cache. Raises the package's CannotConnect / InvalidAuth."""
    dev_id = t["id"]
    c = atv.remote(t["host"], str(CERT), str(KEY))
    await asyncio.wait_for(c.async_connect(), 10)
    c.add_is_on_updated_callback(lambda on: _store(dev_id, power=bool(on)))
    c.add_volume_info_updated_callback(lambda info: _store(dev_id, **_volume_vals(info)))
    c.add_is_available_updated_callback(lambda ok: ok or _store(dev_id, power=False))

    def unpaired():                                   # the TV forgot the house: pair it again
        _paired.discard(dev_id)
        _save_paired()
        _drop(dev_id)
    c.keep_reconnecting(unpaired)
    _conns[dev_id] = c
    _store(dev_id, power=bool(c.is_on), **_volume_vals(c.volume_info or {}))


async def _keeper():
    """Every RETRY_S: each paired TV without a connection is connected (one asleep or gone stays off until it answers)."""
    while True:
        for t in list(TVS):
            if t["id"] in _paired and t["id"] not in _conns and t["id"] not in _pairing and not atv.MISSING:
                try:
                    await _connect(t)
                except atv.InvalidAuth:
                    _paired.discard(t["id"])
                    _save_paired()
                except (atv.CannotConnect, atv.ConnectionClosed, OSError, asyncio.TimeoutError):
                    _store(t["id"], power=False)
                except Exception as e:                   # noqa: BLE001  one TV must not stop the others
                    core.log.warning(f"[androidtv] {t['id']}: {type(e).__name__}: {e}")
        await asyncio.sleep(RETRY_S)


# ---- control ----
def _value(t: dict, code: str, val):
    name = t.get("name") or t["id"]
    if code == "volume":
        try:
            f = float(val) if not isinstance(val, bool) else None
            v = int(f) if f is not None and f.is_integer() else None
        except (TypeError, ValueError, OverflowError):
            v = None
        if v is None or not 0 <= v <= 100:
            raise HTTPException(400, f"{name}: volume is a whole number 0-100, not {val!r}")
        return v
    if code in ("mute", "power"):
        s = str(val).strip().lower()
        if isinstance(val, bool) or s in _ON | _OFF:
            return val if isinstance(val, bool) else s in _ON
        raise HTTPException(400, f"{name}: {code} is on or off, not {val!r}")
    if code == "media":
        if str(val) not in atv.MEDIA:
            raise HTTPException(400, f"{name}: media is one of {', '.join(atv.MEDIA)}, not {val!r}")
        return str(val)
    if code.startswith("key_") and code[4:] in atv.REMOTE:
        return True
    if code in ("input", "app"):
        known = t.get("inputs" if code == "input" else "apps") or {}
        if str(val) in known:
            return str(val)
        raise HTTPException(400, f"{name}: {code} {val!r} is not one of {', '.join(known) or 'none set'}")
    raise HTTPException(400, f"{name}: no channel {code!r}")


async def _set_volume(c, target: int):
    """The TV takes volume steps only: as many up / down presses as the change needs (at most MAX_STEPS)."""
    info = c.volume_info or {}
    top, level = int(info.get("max") or 0), int(info.get("level") or 0)
    if top <= 0:
        raise HTTPException(409, "the TV has not told its volume yet: try again in a moment")
    steps = round(target * top / 100) - level
    for _ in range(min(abs(steps), MAX_STEPS)):
        c.send_key_command(atv.UP if steps > 0 else atv.DOWN)
        await asyncio.sleep(0.08)


async def _control(cfg: dict, code: str, val):
    t = _tv(cfg["id"])
    if not t:
        raise HTTPException(404, "unknown Android TV")
    name = t.get("name") or t["id"]
    val = _value(t, code, val)
    c = _conns.get(t["id"])
    if code == "power" and val and c is None:
        if not _MAC.fullmatch(t.get("mac") or ""):
            raise HTTPException(409, f"{name}: not connected and no MAC for Wake-on-LAN (is it paired and on the network?)")
        await asyncio.to_thread(core.wake_on_lan, t["mac"])
        core.log.info(f"[control] {name}/power=True via androidtv (Wake-on-LAN)")
        return
    if c is None:
        raise HTTPException(409, f"{name}: " + ("not paired (Settings -> TVs -> Pairing)" if t["id"] not in _paired
                                               else "the TV is off or unreachable (switch it on first)"))
    try:
        if code == "power":
            c.send_key_command(atv.ON if val else atv.OFF)
        elif code == "volume":
            await _set_volume(c, val)
        elif code == "mute":
            if bool((c.volume_info or {}).get("muted")) != val:
                c.send_key_command(atv.MUTE)
        elif code == "app":
            c.send_launch_app_command(t["apps"][val])
        elif code == "input":
            c.send_key_command(t["inputs"][val])
        elif code == "media":
            c.send_key_command(atv.MEDIA[val])
        else:
            c.send_key_command(atv.REMOTE[code[4:]])
    except (atv.ConnectionClosed, OSError) as e:
        _drop(t["id"])
        raise HTTPException(503, f"{name}: the connection to the TV dropped, try again ({type(e).__name__})")
    core.log.info(f"[control] {name}/{code}={val} via androidtv")


def _apply(cfg, code, val):
    """The sync transport (bindings, timers, the scheduler run in worker threads): onto the main loop."""
    loop = core.main_loop
    if loop is None or not loop.is_running():
        raise HTTPException(503, "the event loop is not running")
    return asyncio.run_coroutine_threadsafe(_control(cfg, code, val), loop).result(30)


# ---- settings (Settings -> TVs: the card, Add a TV, the pairing) ----
def _public(t: dict) -> dict:
    return {**t, "paired": t["id"] in _paired, "connected": t["id"] in _conns}


def _check_lists(inputs: dict, apps: dict):
    if len(inputs) > 10 or not all(isinstance(k, str) and 0 < len(k) <= 40 and v in atv.INPUTS.values() for k, v in inputs.items()):
        raise HTTPException(400, "inputs: name -> TV_INPUT_HDMI_1..4, up to 10")
    if len(apps) > 30 or not all(isinstance(k, str) and 0 < len(k) <= 40 and isinstance(v, str) and _APP.fullmatch(v) for k, v in apps.items()):
        raise HTTPException(400, "apps: name -> a package (com.netflix.ninja) or a link (https://...), up to 30")


async def _ensure_cert():
    """The house's one client certificate (made on first use): each TV trusts it after its pairing."""
    await atv.remote("127.0.0.1", str(CERT), str(KEY)).async_generate_cert_if_missing()
    for f in (CERT, KEY):
        os.chmod(f, 0o600)


class NewTvBody(core.Body):
    name: str = Field(min_length=1, max_length=60)
    host: str = Field(max_length=100)
    mac: str = Field("", max_length=17)


class TvBody(core.Body):
    name: str = Field(min_length=1, max_length=60)
    host: str = Field(max_length=100)
    mac: str = Field("", max_length=17)
    inputs: dict = {}
    apps: dict = {}


class CodeBody(core.Body):
    code: str = Field(min_length=4, max_length=12)


@core.app.get("/api/androidtv")
def androidtv_get(request: Request):
    core.house_token_only(request)
    with _lock:
        return {"tvs": [_public(t) for t in TVS], "missing": atv.MISSING and atv.PACKAGES}


@core.app.post("/api/androidtv/tvs")
async def androidtv_add(body: NewTvBody, request: Request):
    """Add a TV (the tv plug-in's Add a TV); its MAC, when not given, from the certificate it shows before pairing."""
    core.house_token_only(request)
    host, mac = body.host.strip(), body.mac.strip().lower()
    if not _HOST.fullmatch(host):
        raise HTTPException(400, "address: an IP address or a host name")
    if mac and not _MAC.fullmatch(mac):
        raise HTTPException(400, "MAC: aa:bb:cc:dd:ee:ff")
    if any(t.get("host") == host for t in TVS):
        raise HTTPException(409, f"{host}: this TV is already added")
    name = core.check_name(body.name.strip(), "device", [d.get("name") for d in list(core.DEVICES.values())])
    if not mac and not atv.MISSING:
        try:
            await _ensure_cert()
            _, mac = await asyncio.wait_for(atv.remote(host, str(CERT), str(KEY)).async_get_name_and_mac(), 8)
            mac = mac.lower() if _MAC.fullmatch(str(mac).lower()) else ""
        except Exception as e:                       # noqa: BLE001  the MAC can be typed in Edit
            core.log.info(f"[androidtv] {host}: no MAC from its certificate ({type(e).__name__})")
            mac = ""
    base = "atv_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30] or "tv") + "_" + host.split(".")[-1]
    dev_id, n = base, 2
    while dev_id in core.DEVICES or _tv(dev_id):
        dev_id, n = f"{base}_{n}", n + 1
    rec = {"id": dev_id, "name": name, "host": host, "mac": mac, "inputs": {"HDMI 1": atv.INPUTS["HDMI 1"], "HDMI 2": atv.INPUTS["HDMI 2"]},
           "apps": {"YouTube": "https://www.youtube.com", "Netflix": "com.netflix.ninja"}}
    with _lock:
        TVS.append(rec)
        core.save_settings()
        _sync()
    return _public(rec)


@core.app.put("/api/androidtv/tvs/{dev_id}")
def androidtv_put(dev_id: str, body: TvBody, request: Request):
    core.house_token_only(request)
    host, mac = body.host.strip(), body.mac.strip().lower()
    if not _HOST.fullmatch(host):
        raise HTTPException(400, "address: an IP address or a host name")
    if mac and not _MAC.fullmatch(mac):
        raise HTTPException(400, "MAC: aa:bb:cc:dd:ee:ff")
    _check_lists(body.inputs, body.apps)
    with _lock:
        old = _tv(dev_id)
        if not old:
            raise HTTPException(404, "no such TV")
        name = core.check_name(body.name.strip(), "device", [d.get("name") for k, d in list(core.DEVICES.items()) if k != dev_id])
        rec = {"id": dev_id, "name": name, "host": host, "mac": mac, "inputs": body.inputs, "apps": body.apps}
        TVS[TVS.index(old)] = rec
        core.save_settings()
        _sync()
        if old.get("host") != host:
            _drop(dev_id)                            # the keeper connects to the new address
    if old.get("name") != name:
        core.set_device_name(dev_id, name)
    return _public(rec)


@core.app.delete("/api/androidtv/tvs/{dev_id}")
def androidtv_delete(dev_id: str, request: Request):
    core.house_token_only(request)
    with _lock:
        t = _tv(dev_id)
        if not t:
            raise HTTPException(404, "no such TV")
        TVS.remove(t)
        core.save_settings()
        _sync()
        _drop(dev_id)
        if dev_id in _paired:
            _paired.discard(dev_id)
            _save_paired()
    return {"ok": True, "removed": dev_id}


@core.app.post("/api/androidtv/tvs/{dev_id}/pair")
async def androidtv_pair_start(dev_id: str, request: Request):
    """The TV shows a code (the next call takes it)."""
    core.house_token_only(request)
    t = _tv(dev_id)
    if not t:
        raise HTTPException(404, "no such TV")
    if atv.MISSING:
        raise HTTPException(503, f"add to EXTRA_PIP in .env and run make update: {atv.PACKAGES}")
    _drop(dev_id)
    old = _pairing.pop(dev_id, None)
    if old is not None:
        old.disconnect()
    try:
        await _ensure_cert()
        c = atv.remote(t["host"], str(CERT), str(KEY))
        await asyncio.wait_for(c.async_start_pairing(), 10)
    except (atv.CannotConnect, atv.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
        raise HTTPException(503, f"The TV does not answer: switch it on, then press Pairing again ({type(e).__name__})")
    _pairing[dev_id] = c
    return {"ok": True, "code_on_tv": True}


@core.app.post("/api/androidtv/tvs/{dev_id}/pair/code")
async def androidtv_pair_finish(dev_id: str, body: CodeBody, request: Request):
    """The code the TV shows: right = the TV trusts the house's certificate from now on."""
    core.house_token_only(request)
    t, c = _tv(dev_id), _pairing.get(dev_id)
    if not t or c is None:
        raise HTTPException(409, "press Pairing first: the TV shows the code then")
    try:
        await asyncio.wait_for(c.async_finish_pairing(body.code.strip().upper()), 15)
    except atv.InvalidAuth:
        raise HTTPException(409, "the code is wrong: press Pairing again and type the new one")
    except (atv.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
        _pairing.pop(dev_id, None)
        raise HTTPException(409, f"the TV closed the pairing (cancelled on the TV?): press Pairing again ({type(e).__name__})")
    _pairing.pop(dev_id, None)
    with _lock:
        _paired.add(dev_id)
        _save_paired()
    try:
        await _connect(t)
    except Exception as e:                           # noqa: BLE001  paired; the keeper connects in RETRY_S
        core.log.info(f"[androidtv] {dev_id}: paired, first connection later ({type(e).__name__})")
    return {"ok": True, "paired": True}


def _health() -> list:
    rows = []
    if TVS and atv.MISSING:
        rows.append(core.health_row("androidtv", "bad", "Android TV", "the androidtvremote2 package is missing: {0}",
                                    "Add them to EXTRA_PIP in .env and run make update", [atv.PACKAGES]))
    for t in TVS:
        if t["id"] not in _paired:
            rows.append(core.health_row(f"androidtv:{t['id']}", "warn", "Android TV", "{0}: not paired", "Pair it on the TVs page",
                                        [t.get("name") or t["id"]], link="tv"))
    return rows


tv_plugin.KNOWN_HOSTS.append(lambda: {t.get("host"): t.get("name") or t["id"] for t in TVS})   # the tv plug-in's Add a TV
core.SETTINGS_SECTIONS["androidtv_tvs"] = lambda: TVS
core.TRANSPORTS[TRANSPORT] = lambda cfg, code, val, kind: _apply(cfg, code, val)
core.ASYNC_CONTROL[TRANSPORT] = _control
core.HEALTH_CHECKS.append(_health)
if "wakes" not in core.DEVICE_FIELDS:                 # the tv plug-in's hold while it wakes (tizen may have added it)
    core.DEVICE_FIELDS.append("wakes")
core.STARTUP_TASKS.append(_keeper)
_sync()
