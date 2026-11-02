"""LG webOS TV as a dashboard device: SSAP over websockets + Wake-on-LAN, live polling.
The TVs are the devices.json entries with transport "webos" (the same file as before the move);
their SSAP client-keys live in webos_keys.json, their current IPs in webos_hosts.json."""
import asyncio
import json
import os
import re
import threading

from fastapi import HTTPException, Request
from pydantic import Field

import app as core

from . import _ssap as webos
from .. import tv as tv_plugin         # the remote's keys (file requires: tv)

PLUGIN = {"name": "LG webOS TV", "version": "1.0.0", "icon": "tv", "menu_group": "devices"}

DATA = core.plugin_data("webos")
DEVICES_PATH = DATA / "devices.json"
WEBOS_KEYS_PATH = core.plugin_state_file("webos", "webos_keys.json")
WEBOS_HOSTS_PATH = DATA / "webos_hosts.json"    # {dev_id: ip}, optional: written by a helper of yours


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _load_keys():
    """The client-keys; a file that exists but cannot be read is NOT treated as empty: a pair
    would then rewrite it with one key and every other TV would need a new on-screen confirm."""
    try:
        keys = json.loads(WEBOS_KEYS_PATH.read_text(encoding="utf-8"))
        if not isinstance(keys, dict):
            raise ValueError("not an object")
        return keys, False
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError) as e:
        core.log.error(f"[webos] {WEBOS_KEYS_PATH.name} unreadable ({e}): pairing is off until it is fixed")
        return {}, True


_webos_keys, _keys_broken = _load_keys()


def _with_keys(d: dict) -> dict:
    """The device as registered: its devices.json entry plus the remote's keys and every player key (never written to the file)."""
    d = dict(d)
    d["controls"] = [dict(c, range=[m for m in webos.MEDIA if not m.startswith("volume")]) if c.get("code") == "media" else c
                     for c in d.get("controls") or [] if c.get("kind") != "tv_key"] + tv_plugin.key_controls(webos.BUTTONS)
    return d


def _register_tvs() -> list:
    """Every webos entry of devices.json; a bad one is skipped with a log line, not all TVs."""
    static = _read_json(DEVICES_PATH, [])
    out = []
    for d in static if isinstance(static, list) else []:
        if not (isinstance(d, dict) and d.get("transport") == "webos"):
            continue
        try:
            out.append(core.register_device(_with_keys(d)))
        except (ValueError, TypeError) as e:
            core.log.error(f"[webos] devices.json entry {str(d.get('id'))[:40]!r} skipped: {e}")
    return out


WEBOS_DEVICES = _register_tvs()


def _tvs():
    return [d for d in list(core.DEVICES.values()) if d.get("transport") == "webos"]


def _webos_apply(cfg, code, val, timeout: float = 15.0):
    """The sync transport (bindings, timers, scheduler run in worker threads): hop onto the
    server loop and wait for the real result. Called from the loop itself, waiting would
    deadlock - there it is scheduled and a failure logged."""
    coro = _webos_control(cfg, code, val)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = core.main_loop
        if loop is None or not loop.is_running():
            coro.close()
            raise RuntimeError("server loop not running, cannot reach the TV")
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            fut.result(timeout)
        except BaseException:
            fut.cancel()                 # a timed-out attempt must not keep a socket open
            raise
        return
    task = asyncio.ensure_future(coro)
    task.add_done_callback(
        lambda t: t.cancelled() or t.exception() and
        core.log.warning(f"[webos] {cfg.get('id')}/{code}={val} failed: {t.exception()}"))


async def _webos_poll_one(d, expect=None) -> bool:
    """Poll one TV into the status cache; returns power. While `expect` (the value a command
    just asked for) is not confirmed, power is NOT written: SSAP still answers a second or
    two after power_off, and a stale "on" would overwrite the right "off" on the card."""
    key = _webos_keys.get(d["id"])
    empty = {"online": False, "values": {}, "raw": {}}
    if not key:
        cur = core.status_copy(d["id"], empty)
        cur.setdefault("values", {})["power"] = False
        cur["online"] = False
        core.store_status(d["id"], cur)
        return False
    new = {}
    try:
        st = await webos.status(_webos_host(d), key)
        power = True
        if st.get("volume") is not None:
            new["volume"] = int(st["volume"])
        if st.get("mute") is not None:
            new["mute"] = bool(st["mute"])
    except Exception:
        power = False               # SSAP down = the TV is off, but on the LAN and wakeable by WoL
    cur = core.status_copy(d["id"], empty)      # read AFTER the await: keep what changed meanwhile
    vals = cur.setdefault("values", {})
    vals.update(new)
    cur["online"] = True
    cur.pop("stale", None)
    if expect is None or bool(power) == bool(expect):
        vals["power"] = power
    core.store_status(d["id"], cur)
    return bool(power)


# Catch-up polls after a command: WoL takes about ten seconds, and without them the card kept
# the old value until the next poller tick. The series stops once the state matches.
_WEBOS_KICK_DELAYS = (2, 4, 7, 12, 20, 30)
_webos_kicks: dict = {}
_webos_expect: dict = {}            # dev_id -> the power a running series waits for; the poller keeps it too


async def _webos_kick_loop(dev_id: str, expect):
    try:
        for delay in _WEBOS_KICK_DELAYS:
            await asyncio.sleep(delay)
            d = core.DEVICES.get(dev_id)
            if d is None:
                return
            power = await _webos_poll_one(d, expect)
            if expect is None or bool(power) == bool(expect):
                return
    except asyncio.CancelledError:
        raise
    except Exception as e:
        core.log.warning(f"[webos] catch-up poll {dev_id}: {e}")
    finally:
        if _webos_kicks.get(dev_id) is asyncio.current_task():   # a cancelled series leaves the new one alone
            _webos_kicks.pop(dev_id, None)
            _webos_expect.pop(dev_id, None)


def _webos_kick(dev_id: str, expect=None):
    """Start a series of catch-up polls (the latest command cancels the previous series)."""
    loop = core.main_loop
    if loop is None or not loop.is_running():
        return

    def _start():
        old = _webos_kicks.get(dev_id)
        if old and not old.done():
            old.cancel()
        _webos_expect[dev_id] = expect
        _webos_kicks[dev_id] = asyncio.ensure_future(_webos_kick_loop(dev_id, expect))

    try:
        asyncio.get_running_loop()
        _start()
    except RuntimeError:
        loop.call_soon_threadsafe(_start)


async def _webos_poller():
    """Live state every ~20 s (a TV is mostly switched by its own remote; polling an off TV
    runs into the 8 s SSAP timeout, so more often makes no sense)."""
    while True:
        tvs = _tvs()
        res = await asyncio.gather(*(asyncio.wait_for(_webos_poll_one(d, _webos_expect.get(d["id"])), 30) for d in tvs),
                                   return_exceptions=True)
        for d, r in zip(tvs, res):
            if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                core.log.warning(f"[webos] poll {d['id']}: {r!r}")
        await asyncio.sleep(20)


def _webos_host(cfg):
    """Current TV IP: the host resolver maps the MAC to the live IP (the container cannot ARP
    the LAN) into webos_hosts.json; the configured static IP otherwise."""
    return _read_json(WEBOS_HOSTS_PATH, {}).get(cfg["id"]) or cfg.get("host")


async def _webos_control(cfg, code, val):
    key = _webos_keys.get(cfg["id"])
    if not key:
        raise HTTPException(400, f"TV not paired: POST /api/webos/{cfg['id']}/pair")
    ip, mac = _webos_host(cfg), cfg.get("mac")
    off = f"{cfg.get('name') or cfg['id']}: the TV is off or unreachable (power it on first)"
    val = _webos_value(cfg, code, val)
    if code != "power" and ((core.status_cache.get(cfg["id"]) or {}).get("values") or {}).get("power") is False:
        raise HTTPException(409, off)               # a TV is never "offline", it is off: say so at once
    try:
        if code == "power":
            if val:
                mac = mac.strip() if isinstance(mac, str) else ""
                hexes = re.sub("[:-]", "", mac)
                if not re.fullmatch(r"[0-9A-Fa-f]{2}([:-]?)[0-9A-Fa-f]{2}(\1[0-9A-Fa-f]{2}){4}", mac):
                    raise HTTPException(400, f"{cfg.get('name') or cfg['id']}: power-on needs a valid mac of the TV "
                                             "in devices.json (aa:bb:cc:dd:ee:ff)")
                webos.wol(":".join(hexes[i:i + 2] for i in range(0, 12, 2)).lower())   # one form the relay takes
            else:
                await webos.command(ip, key, "power_off")
        elif code == "input":
            await webos.command(ip, key, "input", (cfg.get("_webos_inputs") or {}).get(str(val), str(val)))
        elif code == "app":
            await webos.command(ip, key, "app", (cfg.get("_webos_apps") or {}).get(str(val), str(val)))
        elif code in ("volume", "mute", "media"):
            await webos.command(ip, key, code, val)
        elif code.startswith("key_") and code[4:] in webos.BUTTONS:
            await webos.button(ip, key, webos.BUTTONS[code[4:]])
        else:
            raise HTTPException(400, f"unknown webos code {code}")
    except HTTPException:
        raise
    except (OSError, asyncio.TimeoutError) as e:     # could not reach it: off since the last poll
        raise HTTPException(409 if code != "power" else 503, off if code != "power" else f"The TV does not answer. Is it on and on the network? ({type(e).__name__})")
    except Exception as e:
        raise HTTPException(503, f"The TV refused the command: {str(e) or type(e).__name__}")
    core.log.info(f"[control] {cfg['name']}/{code}={val} via webos")
    _webos_kick(cfg["id"], bool(val) if code == "power" else None)   # power: wait for exactly that


_WEBOS_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_ON, _OFF = {"on", "true", "1"}, {"off", "false", "0"}


def _webos_value(cfg: dict, code: str, val):
    """The value a code takes, or 400: input / app an id (or a name of the device's lists), volume
    0-100, mute on/off, media one of the player keys; power as given."""
    name = cfg.get("name") or cfg["id"]
    if code in ("input", "app"):
        known = cfg.get("_webos_inputs" if code == "input" else "_webos_apps") or {}
        v = str(val).strip() if isinstance(val, (str, int)) and not isinstance(val, bool) else ""
        if v in known or _WEBOS_ID.fullmatch(v):
            return v
        raise HTTPException(400, f"{name}: {code} {val!r} is not a known name or an id (letters, digits, _.:-)")
    if code == "volume":
        try:
            f = float(val) if not isinstance(val, bool) else None
            v = int(f) if f is not None and f.is_integer() else None       # 50, 50.0 and "50.0"; not 1.5
        except (TypeError, ValueError, OverflowError):
            v = None
        if v is None or not 0 <= v <= 100:
            raise HTTPException(400, f"{name}: volume is a whole number 0-100, not {val!r}")
        return v
    if code == "mute":
        s = str(val).strip().lower()
        if isinstance(val, bool) or s in _ON | _OFF:
            return val if isinstance(val, bool) else s in _ON
        raise HTTPException(400, f"{name}: mute is on or off, not {val!r}")
    if code.startswith("key_"):
        if code[4:] not in webos.BUTTONS:
            raise HTTPException(400, f"{name}: no remote key {code[4:]!r}")
        return True
    if code == "media" and str(val) not in webos.MEDIA:
        raise HTTPException(400, f"{name}: media is one of {', '.join(sorted(webos.MEDIA))}, not {val!r}")
    return val


@core.app.post("/api/webos/{dev_id}/pair")
async def webos_pair(dev_id: str):
    cfg = core.DEVICES.get(dev_id)
    if not cfg or cfg.get("transport") != "webos":
        raise HTTPException(404, "unknown webos device")
    if _keys_broken:
        raise HTTPException(503, f"{WEBOS_KEYS_PATH.name} is unreadable: fix it (or remove it) and restart first")
    try:
        key = await webos.pair(_webos_host(cfg))
    except Exception as e:
        raise HTTPException(503, f"The TV did not pair: switch it on, press Pairing again and accept on the TV ({str(e) or type(e).__name__})")
    def keep():
        with core.settings_lock:
            _webos_keys[dev_id] = key
            try:
                core.atomic_json_dump(WEBOS_KEYS_PATH, _webos_keys)
                os.chmod(WEBOS_KEYS_PATH, 0o600)
            except Exception as e:
                # the TV hands out a new key only after another confirmation on its screen: say it now
                core.log.warning(f"[webos] pair key NOT persisted: {e}")
                return {"ok": True, "paired": True, "persisted": False, "warning": f"key not saved to disk: {e}"}
        return {"ok": True, "paired": True, "persisted": True}
    return await asyncio.to_thread(keep)            # an fsync under the lock: not on the event loop


@core.app.get("/api/webos/tvs/{dev_id}/lists")
async def webos_lists(dev_id: str):
    """The TV's own inputs and apps ({id: label}) to pick from in Edit; when it does not answer, only those set."""
    cfg = core.DEVICES.get(dev_id)
    if not cfg or cfg.get("transport") != "webos":
        raise HTTPException(404, "unknown webos device")
    key = _webos_keys.get(dev_id)
    if key:
        try:
            got = await asyncio.wait_for(webos.lists(_webos_host(cfg), key), 15)
            return {"inputs": got["inputs"], "apps": got["apps"], "from_tv": True}
        except Exception:                            # off or refused: the lists that are set, said so
            pass
    return {"inputs": {}, "apps": {}, "from_tv": False, "why": "not paired" if not key else "the TV does not answer"}


def _row(d: dict) -> dict:
    return {"id": d["id"], "name": d.get("name") or d["id"], "host": d.get("host") or "", "live_host": _webos_host(d) or "",
            "mac": d.get("mac") or "", "paired": d["id"] in _webos_keys, "inputs": dict(d.get("_webos_inputs") or {}),
            "apps": dict(d.get("_webos_apps") or {})}


@core.app.get("/api/webos/tvs")
def webos_tvs(request: Request):
    """Settings -> TVs -> LG: every LG TV with its address, MAC, pairing, inputs and apps (the house token)."""
    core.house_token_only(request)
    return {"tvs": [_row(d) for d in _tvs()]}


class TvBody(core.Body):
    name: str = Field(min_length=1, max_length=60)
    host: str = Field(max_length=100)
    mac: str = Field("", max_length=17)
    inputs: dict = {}
    apps: dict = {}


_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,98}")
_MAC_RE = re.compile(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")
_lock = threading.Lock()


def _pairs(m: dict, what: str) -> dict:
    """name -> id, at most 40, both short strings: the card's list of that kind."""
    if len(m) > 40 or not all(isinstance(k, str) and isinstance(v, str) and 0 < len(k) <= 40 and 0 < len(v) <= 120 for k, v in m.items()):
        raise HTTPException(400, f"{what}: up to 40 lines of name = id")
    return {k.strip(): v.strip() for k, v in m.items()}


@core.app.put("/api/webos/tvs/{dev_id}")
def webos_tv_put(dev_id: str, body: TvBody, request: Request):
    """Edit an LG TV: its devices.json entry is rewritten and the device updated (the input and app lists of its card)."""
    core.house_token_only(request)
    if not _HOST_RE.fullmatch(body.host.strip()):
        raise HTTPException(400, "address: an IP address or a host name")
    if body.mac.strip() and not _MAC_RE.fullmatch(body.mac.strip()):
        raise HTTPException(400, "MAC: aa:bb:cc:dd:ee:ff")
    inputs, apps = _pairs(body.inputs, "inputs"), _pairs(body.apps, "apps")
    with _lock:
        entries = _read_json(DEVICES_PATH, [])
        d = next((e for e in entries if isinstance(e, dict) and e.get("id") == dev_id and e.get("transport") == "webos"), None) if isinstance(entries, list) else None
        if d is None:
            raise HTTPException(404, "unknown LG TV")
        d.update(name=core.check_name(body.name), host=body.host.strip(), mac=body.mac.strip().lower(), _webos_inputs=inputs, _webos_apps=apps)
        for c in d.get("controls") or []:
            if c.get("code") == "input":
                c["range"] = list(inputs)
            elif c.get("code") == "app":
                c["range"] = list(apps)
        core.atomic_json_dump(DEVICES_PATH, entries, indent=1, ensure_ascii=False)
        core.update_device(_with_keys(d))
    return {"tv": _row(core.DEVICES[dev_id])}


@core.app.delete("/api/webos/tvs/{dev_id}")
def webos_tv_delete(dev_id: str, request: Request):
    """Remove an LG TV: its devices.json entry and its pairing key go, and the device (a rule naming it is reported as gone)."""
    core.house_token_only(request)
    with _lock:
        entries = _read_json(DEVICES_PATH, [])
        keep = [e for e in entries if not (isinstance(e, dict) and e.get("id") == dev_id and e.get("transport") == "webos")] if isinstance(entries, list) else None
        if keep is None or len(keep) == len(entries):
            raise HTTPException(404, "unknown LG TV")
        core.atomic_json_dump(DEVICES_PATH, keep, indent=1, ensure_ascii=False)
        with core.settings_lock:
            if _webos_keys.pop(dev_id, None) is not None and not _keys_broken:
                core.atomic_json_dump(WEBOS_KEYS_PATH, _webos_keys)
        if dev_id in core.DEVICES:
            core.remove_device(dev_id, "webos")
    return {"ok": True}


core.TRANSPORTS["webos"] = lambda cfg, code, val, kind: _webos_apply(cfg, code, val)
core.ASYNC_CONTROL["webos"] = _webos_control
core.STARTUP_TASKS.append(_webos_poller)
