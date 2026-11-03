"""Samsung Tizen TV (2016+) as a dashboard device: the TVs are the settings section tizen_tvs (Settings -> Samsung TV),
their tokens (one Allow on the TV each) live in plugin-state/tizen/tokens.json. Power on = Wake-on-LAN (core.wake_on_lan),
then an IR press as the fallback when one is set; everything else over the remote channel, UPnP and the REST API."""
import asyncio
import json
import os
import re
import threading
import time
from typing import Optional

from fastapi import HTTPException

import app as core

from . import _tv as tv
from .. import tv as tv_plugin         # the remote's keys (file requires: tv)

PLUGIN = {"name": "Samsung TV", "version": "1.0.0", "icon": "tv", "menu_group": "devices"}
TRANSPORT = "tizen"
TOKENS_PATH = core.plugin_state_file("tizen", "tokens.json")
POLL_S, IDLE_POLL_S, IDLE_AFTER_S = 10, 60, 600     # a TV off for 10 min is asked once a minute
WOL_REPEATS = (20, 45, 90)                           # a WoL soon after standby may wake the network and not the screen
OFF_SETTLE_S = 60                                    # ... so the first one waits this long after our own power-off
MISSES_OFF = 2                                       # UPnP silent twice in a row = off (standby drops it in ~5 s)
HOLD_CONFIRM_S = 150                                 # after our off: UPnP must answer this long without a gap to count
HOLD_OFF_S = 600                                     # ... within this long after it (a TV's own hold_off_s replaces it)
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")
_HOST = re.compile(r"[A-Za-z0-9.-]{1,64}")
_MAC = re.compile(r"[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(\1[0-9A-Fa-f]{2}){4}")
_ON, _OFF = {"on", "true", "1"}, {"off", "false", "0"}

_lock = threading.RLock()
TVS: list = [t for t in core.cfg_section("tizen_tvs", []) if isinstance(t, dict) and isinstance(t.get("id"), str)]
_seen: dict = {}            # dev_id -> {"misses", "off_since", "next"}: the poller's own memory
_expect: dict = {}          # dev_id -> the power a command waits for (a poll writes no other)
_waking: dict = {}          # dev_id -> the wake task (WoL, its repeats, the IR fallback)
_last_on: dict = {}         # dev_id -> when UPnP last answered (monotonic)
_last_off: dict = {}        # dev_id -> when we sent KEY_POWER (monotonic)


def _load_tokens():
    """A file that exists but cannot be read is NOT empty: a pairing would rewrite it and every other TV would need Allow."""
    try:
        t = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
        if not isinstance(t, dict):
            raise ValueError("not an object")
        return t, False
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError) as e:
        core.log.error(f"[tizen] {TOKENS_PATH.name} unreadable ({type(e).__name__}): pairing is off until it is fixed")
        return {}, True


_tokens, _tokens_broken = _load_tokens()


def _save_tokens():
    core.atomic_json_dump(TOKENS_PATH, _tokens)
    os.chmod(TOKENS_PATH, 0o600)


def _tv(dev_id: str) -> Optional[dict]:
    return next((t for t in TVS if t["id"] == dev_id), None)


def _device(t: dict) -> dict:
    inputs, apps = t.get("inputs") or {}, t.get("apps") or {}
    ctrls = [{"kind": "switch", "code": "power", "label": "Power", "toggle": True},       # KEY_POWER, KEY_MUTE: keys that flip
             {"kind": "bright", "code": "volume", "label": "Volume", "min": 0, "max": 100},
             {"kind": "switch", "code": "mute", "label": "Mute", "toggle": True},
             {"kind": "setting_enum", "code": "media", "label": "Media", "range": ["play", "pause", "stop", "rewind", "fast_forward"]}]
    if inputs:
        ctrls.append({"kind": "setting_enum", "code": "input", "label": "Input", "range": list(inputs)})
    if apps:
        ctrls.append({"kind": "setting_enum", "code": "app", "label": "App", "range": list(apps)})
    ctrls += tv_plugin.key_controls(tv.REMOTE)
    return {"id": t["id"], "name": t.get("name") or t["id"], "category": "tv", "transport": TRANSPORT, "model": "Samsung Tizen TV",
            "wakes": True, "no_enforce": ["power"], "controls": ctrls}


def _sync():
    """The TVs of the settings -> devices of the house: new ones registered, changed ones replaced, removed ones dropped."""
    live = {t["id"] for t in TVS}
    for did in [k for k, d in list(core.DEVICES.items()) if d.get("transport") == TRANSPORT and k not in live]:
        core.remove_device(did, TRANSPORT)
        _seen.pop(did, None)
    for t in TVS:
        try:
            (core.update_device if t["id"] in core.DEVICES else core.register_device)(_device(t))
        except ValueError as e:
            core.log.error(f"[tizen] {t['id']!r} skipped: {e}")


# ---- state: the screen is on while UPnP answers (it stops ~5 s after standby; REST lags 20 s) ----
def _read(t: dict) -> dict:
    """{power, volume?, mute?} from one look; raises nothing."""
    try:
        v = tv.volume(t["host"])
    except Exception:                                # noqa: BLE001  silent = not on now
        return {"power": None}
    out = {"power": True, "volume": v}
    try:
        out["mute"] = tv.mute(t["host"])
    except Exception:                                # noqa: BLE001
        pass
    return out


def _hold_confirm_s(dev_id: str) -> float:
    return (_tv(dev_id) or {}).get("hold_confirm_s", HOLD_CONFIRM_S)


def _held(dev_id: str) -> bool:
    """Our own power-off is recent: the TV may bring its network (UPnP too) back by itself within a minute, screen dark."""
    t = _tv(dev_id) or {}
    hold = t.get("hold_off_s", HOLD_OFF_S)
    return dev_id in _last_off and time.monotonic() - _last_off[dev_id] < hold


def _store(dev_id: str, seen_on: Optional[bool], vals: dict):
    """One look into the status cache, debounced: one answer = on, MISSES_OFF silent looks = off; after our own off,
    answers without a gap for hold_confirm_s (_held: this model keeps UPnP up ~80 s with a dark screen after an off). While a
    command waits for a power (_expect), no other is written. A TV is never offline: it is off (and wakeable)."""
    s = _seen.setdefault(dev_id, {"misses": 0, "off_since": None, "next": 0, "up_since": None})
    if seen_on:
        s["up_since"] = s.get("up_since") or time.monotonic()
        s["ups"] = 1
        if _held(dev_id) and time.monotonic() - s["up_since"] < _hold_confirm_s(dev_id):
            seen_on = None                               # not yet: a network back after our off is not a screen
        else:
            _last_off.pop(dev_id, None)
    else:
        s["up_since"], s["ups"] = None, 0
    if seen_on:
        s["misses"], s["off_since"] = 0, None
        _last_on[dev_id] = time.monotonic()
        power = True
    elif seen_on is None and s["ups"]:
        power = None
        vals = {}
    else:
        s["misses"] += 1
        power = False if s["misses"] >= MISSES_OFF else None
        if power is False and s["off_since"] is None:
            s["off_since"] = time.monotonic()
    cur = core.status_copy(dev_id, {"online": True, "values": {}, "raw": {}})
    v = cur.setdefault("values", {})
    v.update({k: x for k, x in vals.items() if k in ("volume", "mute")})
    exp = _expect.get(dev_id)
    if power is not None and (exp is None or exp == power):
        v["power"] = power
        if exp == power:
            _expect.pop(dev_id, None)
    cur["online"] = True
    cur.pop("stale", None)
    core.store_status(dev_id, cur)


async def _poll_one(t: dict):
    r = await asyncio.to_thread(_read, t)
    _store(t["id"], r.pop("power"), r)


async def _poller():
    """Every TV of the settings (a removed one is no longer asked); one off for 10 min is asked once a minute."""
    while True:
        now = time.monotonic()
        due = []
        for t in list(TVS):
            s = _seen.setdefault(t["id"], {"misses": 0, "off_since": None, "next": 0})
            if now >= s["next"] or t["id"] in _expect:
                idle = s["off_since"] is not None and now - s["off_since"] > IDLE_AFTER_S
                s["next"] = now + (IDLE_POLL_S if idle else POLL_S)
                due.append(t)
        res = await asyncio.gather(*(asyncio.wait_for(_poll_one(t), 15) for t in due), return_exceptions=True)
        for t, r in zip(due, res):
            if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                core.log.warning(f"[tizen] poll {t['id']}: {type(r).__name__}")
        await asyncio.sleep(2)


def _power_now(dev_id: str):
    return ((core.status_copy(dev_id, {}).get("values") or {}).get("power"))


# ---- control ----
def _value(t: dict, code: str, val):
    """The value a code takes, or 400 (as the webos plug-in: volume 0-100, mute on/off, a known input / app / media)."""
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
        if str(val) not in tv.MEDIA:
            raise HTTPException(400, f"{name}: media is one of {', '.join(sorted(tv.MEDIA))}, not {val!r}")
        return str(val)
    if code.startswith("key_") and code[4:] in tv.REMOTE:
        return True
    if code in ("input", "app"):
        known = t.get("inputs" if code == "input" else "apps") or {}
        if str(val) in known:
            return str(val)
        raise HTTPException(400, f"{name}: {code} {val!r} is not one of {', '.join(known) or 'none set'}")
    raise HTTPException(400, f"{name}: no channel {code!r}")


async def _wake(dev_id: str, delay: float):
    """Power-on of a TV without an IR on key: a WoL (after `delay`), repeated at WOL_REPEATS. The repeats stop when a
    power-off comes (the task is cancelled) or the TV, seen on since, goes off again (the remote)."""
    loop = asyncio.get_running_loop()
    try:
        await asyncio.sleep(delay)
        t = _tv(dev_id)
        if not t:
            return
        start = loop.time()
        mono0 = time.monotonic()
        await asyncio.to_thread(core.wake_on_lan, t["mac"])
        for at in WOL_REPEATS:
            await asyncio.sleep(max(0.0, start + at - loop.time()))
            seen = _last_on.get(dev_id, 0) > mono0
            if seen and _seen.get(dev_id, {}).get("misses", 0) >= MISSES_OFF:
                return                                   # on, then off again: someone switched it off; leave it off
            await asyncio.to_thread(core.wake_on_lan, t["mac"])         # a WoL to a TV that is on does nothing
    except asyncio.CancelledError:
        raise
    except Exception as e:                           # noqa: BLE001
        core.log.warning(f"[tizen] {dev_id}: power-on: {e}")
    finally:
        if _waking.get(dev_id) is asyncio.current_task():
            _waking.pop(dev_id, None)


async def _power_on(t: dict, name: str, off_at: float):
    """An IR on key (a discrete on) lights the screen in any state, a lit TV ignores it: it goes with one WoL, every time.
    Without one: the WoL series. KEY_POWER is a toggle and never powers on (in the dark phase after an off it did not always)."""
    mac_ok = bool(_MAC.fullmatch(t.get("mac") or ""))
    ir = t.get("ir_on") or {}
    if ir.get("device") and ir.get("code"):
        try:
            await core.control(ir["device"], core.ControlBody(code=ir["code"], value=True))
        except HTTPException as e:
            if not mac_ok:
                raise HTTPException(e.status_code, f"{name}: the IR on key failed: {e.detail}")
            core.log.warning(f"[tizen] {t['id']}: the IR on key failed ({e.detail}): Wake-on-LAN only")
        if mac_ok:
            await asyncio.to_thread(core.wake_on_lan, t["mac"])
        return
    if not mac_ok:
        raise HTTPException(400, f"{name}: power-on needs the TV's MAC (or an IR on key) in its settings")
    _waking[t["id"]] = asyncio.ensure_future(_wake(t["id"], max(0.0, off_at + OFF_SETTLE_S - time.monotonic())))


def _stop_waking(dev_id: str):
    old = _waking.pop(dev_id, None)
    if old and not old.done():
        old.cancel()


async def _control(cfg: dict, code: str, val):
    t = _tv(cfg["id"])
    if not t:
        raise HTTPException(404, "unknown tizen TV")
    name, host = t.get("name") or t["id"], t["host"]
    val = _value(t, code, val)
    on = _power_now(t["id"]) is True
    token = _tokens.get(t["id"])
    try:
        if code == "power":
            off_at = _last_off.pop(t["id"], -1e9) if val else None   # a power-on ends the hold of our last off
            if val and not on:
                _stop_waking(t["id"])
                await _power_on(t, name, off_at)
            elif not val:
                _stop_waking(t["id"])                            # a power-off ends the repeats of a power-on
                if on:
                    if not token:
                        raise HTTPException(400, f"{name}: not paired (Settings -> TVs -> Pair)")
                    await tv.keys(host, token, tv.KEYS["power"])    # KEY_POWER toggles: sent only while it is on
                    _last_off[t["id"]] = time.monotonic()
                    _seen.setdefault(t["id"], {"misses": 0, "off_since": None, "next": 0})["up_since"] = None   # count from the off
            _expect[t["id"]] = bool(val)
        else:
            if not on:
                raise HTTPException(409, f"{name}: the TV is off (switch it on first)")
            if code == "volume":
                await asyncio.to_thread(tv.set_volume, host, val)
            elif code == "mute":
                await asyncio.to_thread(tv.set_mute, host, val)
            elif code == "app":
                await asyncio.to_thread(tv.launch, host, t["apps"][val])
            else:
                if not token:
                    raise HTTPException(400, f"{name}: not paired (Settings -> TVs -> Pair)")
                await tv.keys(host, token, tv.REMOTE[code[4:]] if code.startswith("key_")
                              else tv.MEDIA[val] if code == "media" else t["inputs"][val])
    except HTTPException:
        raise
    except tv.Unauthorized:
        raise HTTPException(409, f"{name}: the TV refused the pairing: pair it again (Settings -> Samsung TV)")
    except (OSError, asyncio.TimeoutError) as e:
        raise HTTPException(503, f"{name}: the TV does not answer. Is it on and on the network? ({type(e).__name__})")
    core.log.info(f"[control] {name}/{code}={val} via tizen")


def _apply(cfg, code, val):
    """The sync transport (bindings, timers, the scheduler run in worker threads): onto the main loop."""
    loop = core.main_loop
    if loop is None or not loop.is_running():
        raise HTTPException(503, "the event loop is not running")
    return asyncio.run_coroutine_threadsafe(_control(cfg, code, val), loop).result(30)


# ---- settings (the dashboard: Settings -> Samsung TV) ----
class TvBody(core.Body):
    id: str
    name: str = ""
    host: str
    mac: str = ""
    inputs: dict = {}
    apps: dict = {}
    ir_on: Optional[dict] = None
    hold_off_s: int = HOLD_OFF_S
    hold_confirm_s: int = HOLD_CONFIRM_S


class AdoptBody(core.Body):
    key: str


def _check(b: TvBody) -> dict:
    if not _ID.fullmatch(b.id):
        raise HTTPException(400, "id: lower-case letters, digits, _ or -, up to 48")
    if not _HOST.fullmatch(b.host):
        raise HTTPException(400, "host: an IP address or a host name")
    if b.mac and not _MAC.fullmatch(b.mac):
        raise HTTPException(400, "mac: aa:bb:cc:dd:ee:ff")
    for what, d, val in (("inputs", b.inputs, re.compile(r"KEY_[A-Z0-9_]{1,30}")), ("apps", b.apps, re.compile(r"[A-Za-z0-9._-]{1,64}"))):
        if len(d) > 30 or not all(isinstance(k, str) and 0 < len(k) <= 40 and isinstance(v, str) and val.fullmatch(v) for k, v in d.items()):
            raise HTTPException(400, f"{what}: name -> {'a key (KEY_HDMI1)' if what == 'inputs' else 'an app id'}, up to 30")
    ir = b.ir_on or {}
    if ir and not (isinstance(ir.get("device"), str) and isinstance(ir.get("code"), str) and ir["device"] in core.DEVICES):
        raise HTTPException(400, "ir_on: {device, code} of a device of the house")
    if not 0 <= b.hold_off_s <= 3600 or not 0 <= b.hold_confirm_s <= 3600:
        raise HTTPException(400, "hold_off_s, hold_confirm_s: 0-3600 s")
    name = b.name.strip()
    if name:                                         # a device name: checked, never cut
        name = core.check_name(name, "device", [d.get("name") for k, d in list(core.DEVICES.items()) if k != b.id])
    out = {"id": b.id, "name": name or b.id, "host": b.host, "mac": b.mac.lower(), "inputs": b.inputs, "apps": b.apps,
           "hold_off_s": b.hold_off_s, "hold_confirm_s": b.hold_confirm_s}
    if ir:
        out["ir_on"] = {"device": ir["device"], "code": ir["code"]}
    return out


def _public(t: dict) -> dict:
    return {**t, "paired": bool(_tokens.get(t["id"]))}


@core.app.get("/api/tizen")
def tizen_get():
    with _lock:
        return {"tvs": [_public(t) for t in TVS],
                "unassigned": sorted(k for k in _tokens if not _tv(k)),           # pairings kept for a TV not set up yet
                "tokens_broken": _tokens_broken}


@core.app.put("/api/tizen/tvs/{dev_id}")
def tizen_put(dev_id: str, body: TvBody):
    if body.id != dev_id:
        raise HTTPException(400, "the id in the path and the body differ")
    rec = _check(body)
    with _lock:
        if dev_id not in {t["id"] for t in TVS} and dev_id in core.DEVICES:
            raise HTTPException(409, f"{dev_id}: the id is taken by another device")
        old = _tv(dev_id)
        if old:
            TVS[TVS.index(old)] = rec
        else:
            TVS.append(rec)
        core.save_settings()
        _sync()
    if (old or {}).get("name") != rec["name"]:
        core.set_device_name(dev_id, rec["name"])    # one name: the device window shows it, an older rename there yields
    return _public(rec)


@core.app.delete("/api/tizen/tvs/{dev_id}")
def tizen_delete(dev_id: str):
    with _lock:
        t = _tv(dev_id)
        if not t:
            raise HTTPException(404, "no such TV")
        TVS.remove(t)
        core.save_settings()
        _sync()
        if _tokens.pop(dev_id, None) is not None and not _tokens_broken:
            _save_tokens()
    return {"ok": True, "removed": dev_id}


@core.app.post("/api/tizen/tvs/{dev_id}/pair")
async def tizen_pair(dev_id: str):
    """The TV asks its owner (Allow, ~30 s); the token it gives is kept for this TV."""
    t = _tv(dev_id)
    if not t:
        raise HTTPException(404, "no such TV")
    if _tokens_broken:
        raise HTTPException(503, f"{TOKENS_PATH.name} is unreadable: fix it (or remove it) and restart first")
    try:
        token = await tv.pair(t["host"])
    except tv.Unauthorized as e:
        raise HTTPException(409, f"The TV did not allow it: press Pairing again and Allow on the TV within 30 s ({e})")
    except (OSError, asyncio.TimeoutError) as e:
        raise HTTPException(503, f"The TV does not answer: switch it on (with its remote), then press Pairing again ({type(e).__name__})")

    def keep():
        with _lock:
            _tokens[dev_id] = token
            _save_tokens()
            if not t.get("mac"):                          # its MAC for Wake-on-LAN: the TV says it, nobody types it
                try:
                    t["mac"] = tv.mac(t["host"])
                    core.save_settings()
                except (OSError, ValueError):
                    pass
        return {"ok": True, "paired": True}
    return await asyncio.to_thread(keep)


# well-known apps, offered while the TV is off; the TV's own list (Load from the TV) adds the rest
KNOWN_APPS = {"YouTube": "111299001912", "Netflix": "3201907018807", "Prime Video": "3201910019365",
              "Disney+": "3201901017640", "Spotify": "3201606009684", "Plex": "3201512006963"}


@core.app.get("/api/tizen/tvs/{dev_id}/apps")
async def tizen_apps(dev_id: str):
    """The apps to pick from: the well-known ones, and those installed on the TV when it answers."""
    t = _tv(dev_id)
    if not t:
        raise HTTPException(404, "no such TV")
    known = [{"id": v, "name": k} for k, v in KNOWN_APPS.items()]
    token = _tokens.get(dev_id)
    if not token:
        return {"apps": known, "from_tv": False, "why": "not paired"}
    try:
        got = await tv.installed_apps(t["host"], token)
    except tv.Unauthorized:
        return {"apps": known, "from_tv": False, "why": "pair it again"}
    except (OSError, asyncio.TimeoutError, ValueError):
        return {"apps": known, "from_tv": False, "why": "the TV does not answer"}
    ids = {a["id"] for a in got}
    return {"apps": sorted(got, key=lambda a: a["name"].lower()) + [a for a in known if a["id"] not in ids], "from_tv": True}


@core.app.post("/api/tizen/tvs/{dev_id}/adopt")
def tizen_adopt(dev_id: str, body: AdoptBody):
    """A pairing kept under another key (made before the TV was set up) becomes this TV's."""
    with _lock:
        if not _tv(dev_id):
            raise HTTPException(404, "no such TV")
        if _tokens_broken:
            raise HTTPException(503, f"{TOKENS_PATH.name} is unreadable")
        if body.key not in _tokens or _tv(body.key):
            raise HTTPException(404, "no such unassigned pairing")
        _tokens[dev_id] = _tokens.pop(body.key)
        _save_tokens()
    return {"ok": True, "paired": True}


tv_plugin.KNOWN_HOSTS.append(lambda: {t.get("host"): t.get("name") or t["id"] for t in TVS})   # the tv plug-in's Add a TV: "already added"


def _health() -> list:
    rows = []
    if _tokens_broken:
        rows.append(core.health_row("tizen", "bad", "Samsung TV", "tokens.json is unreadable: pairing is off", "Fix or remove it, then restart", []))
    for t in TVS:
        if not _tokens.get(t["id"]):
            rows.append(core.health_row(f"tizen:{t['id']}", "warn", "Samsung TV", "{0}: not paired", "Pair it on the TVs page", [t.get("name") or t["id"]], link="tv"))
    return rows


core.SETTINGS_SECTIONS["tizen_tvs"] = lambda: TVS
core.TRANSPORTS[TRANSPORT] = lambda cfg, code, val, kind: _apply(cfg, code, val)
core.ASYNC_CONTROL[TRANSPORT] = _control
core.DEVICE_FIELDS.append("wakes")
def _on_rename(old: str, new: str):
    """The IR on key of a TV names a device: a rename or a merge (the IR remote became the TV's own part) moves it."""
    hit = [t for t in TVS if (t.get("ir_on") or {}).get("device") == old]
    for t in hit:
        t["ir_on"] = {**t["ir_on"], "device": new}
    if hit:
        core.save_settings()


def _merge_plan(old: str, new: str) -> dict:
    n = sum(1 for t in TVS if (t.get("ir_on") or {}).get("device") == old)
    return {"samsung: IR on key": n} if n else {}


core.ON_RENAME.append(_on_rename)
core.MERGE_PLAN.append(_merge_plan)
core.HEALTH_CHECKS.append(_health)
core.STARTUP_TASKS.append(_poller)
_sync()
