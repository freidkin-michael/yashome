"""LG webOS TV as a dashboard device: SSAP over websockets + Wake-on-LAN, live polling.
The TVs are the devices.json entries with transport "webos" (the same file as before the move);
their SSAP client-keys live in webos_keys.json, their current IPs in webos_hosts.json."""
import asyncio
import json
import os

from fastapi import HTTPException

import app as core

from . import _ssap as webos

PLUGIN = {"name": "LG webOS TV", "version": "1.0.0"}

DATA = core.plugin_data("webos")
DEVICES_PATH = DATA / "devices.json"
WEBOS_KEYS_PATH = DATA / "webos_keys.json"
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


def _register_tvs() -> list:
    """Every webos entry of devices.json; a bad one is skipped with a log line, not all TVs."""
    static = _read_json(DEVICES_PATH, [])
    out = []
    for d in static if isinstance(static, list) else []:
        if not (isinstance(d, dict) and d.get("transport") == "webos"):
            continue
        try:
            out.append(core.register_device(d))
        except (ValueError, TypeError) as e:
            core.log.error(f"[webos] devices.json entry {str(d.get('id'))[:40]!r} skipped: {e}")
    return out


WEBOS_DEVICES = _register_tvs()


def _tvs():
    return [d for d in core.DEVICES.values() if d.get("transport") == "webos"]


def _webos_apply(cfg, code, val, timeout: float = 15.0):
    """The sync transport (bindings, timers, scheduler run in worker threads): hop onto the
    server loop and wait for the real result. Called from the loop itself, waiting would
    deadlock - there it is scheduled and a failure logged."""
    coro = _webos_control(cfg, code, val)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = core._MAIN_LOOP
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
        cur = core._status_copy(d["id"], empty)
        cur.setdefault("values", {})["power"] = False
        cur["online"] = False
        core._store_status(d["id"], cur)
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
    cur = core._status_copy(d["id"], empty)      # read AFTER the await: keep what changed meanwhile
    vals = cur.setdefault("values", {})
    vals.update(new)
    cur["online"] = True
    cur.pop("stale", None)
    if expect is None or bool(power) == bool(expect):
        vals["power"] = power
    core._store_status(d["id"], cur)
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
    loop = core._MAIN_LOOP
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
    try:
        if code == "power":
            if val:
                webos.wol(mac)
            else:
                await webos.command(ip, key, "power_off")
        elif code == "input":
            await webos.command(ip, key, "input", (cfg.get("_webos_inputs") or {}).get(str(val), str(val)))
        elif code == "app":
            await webos.command(ip, key, "app", (cfg.get("_webos_apps") or {}).get(str(val), str(val)))
        elif code in ("volume", "mute", "media"):
            await webos.command(ip, key, code, val)
        else:
            raise HTTPException(400, f"unknown webos code {code}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"webos: {str(e) or type(e).__name__}")
    core.log.info(f"[control] {cfg['name']}/{code}={val} via webos")
    _webos_kick(cfg["id"], bool(val) if code == "power" else None)   # power: wait for exactly that


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
        raise HTTPException(502, f"pair failed: {e}")
    with core._settings_lock:
        _webos_keys[dev_id] = key
        try:
            core._atomic_json_dump(WEBOS_KEYS_PATH, _webos_keys)
            os.chmod(WEBOS_KEYS_PATH, 0o600)
        except Exception as e:
            # the TV hands out a new key only after another confirmation on its screen: say it now
            core.log.warning(f"[webos] pair key NOT persisted: {e}")
            return {"ok": True, "paired": True, "persisted": False, "warning": f"key not saved to disk: {e}"}
    return {"ok": True, "paired": True, "persisted": True}


core.TRANSPORTS["webos"] = lambda cfg, code, val, kind: _webos_apply(cfg, code, val)
core.ASYNC_CONTROL["webos"] = _webos_control
core.STARTUP_TASKS.append(_webos_poller)
