"""ESP panels: the firmware is generated from the layout (firmware/gen.py + profiles/<hw>.yaml), compiled
and flashed by the build plug-in. All panels share one broker account (panels): they read the home/state
bus and write only under panel/; a command on panel/<name>/cmd/<device>/<code> reaches the device only
when the panel's own layout uses it. A new screen on the starter firmware announces itself on panel/_new/.
"""
import hmac
import importlib.util
import ipaddress
import json
import os
import queue
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

from fastapi import HTTPException, Request

import app as core

P = sys.modules[__package__]
HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(f"{__package__}.gen", os.path.join(HERE, "firmware", "gen.py"))
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

OTA_PORT = 3232
SAFE_MODE_WAIT_S = 90                 # a screen restarting into safe mode, and coming back there, before a flash
RESTART_WAIT_S = 30
HOLD_LIMIT_S = 900
JOBS = core.plugin_state_file("panels", "jobs.json")
_STATE_KEYS = {"state_topic", "onoff_topic", "temp_state", "mode_state", "fan_state", "room_state", "power_state"}
ESP_NAME = re.compile(r"[a-z0-9]([a-z0-9-]{0,22}[a-z0-9])?")
_ON, _OFF = {"true", "1", "on"}, {"false", "0", "off"}
_lock = threading.Lock()
_new: dict = {}
_ips: dict = {}


def _load_jobs() -> dict:
    """Builds survive a restart of the backend: their status keeps showing; a held one expires by itself."""
    try:
        d = json.loads(JOBS.read_text())
    except (OSError, ValueError):
        return {}
    jobs = {k: v for k, v in d.items() if isinstance(v, dict) and isinstance(v.get("id"), str)}
    for v in jobs.values():
        if v.get("phase") in ("compiling", "safe_mode", "uploading"):
            v.update(phase="error", error="interrupted by a restart of the backend")
    return jobs


_jobs: dict = _load_jobs()


def _build():
    mod = sys.modules.get(__package__.rsplit(".", 1)[0] + ".build")
    if mod is None or not hasattr(mod, "submit"):
        raise HTTPException(503, "the build plug-in is off: PLUGINS in .env needs build")
    return mod


def _ip_ok(v) -> Optional[str]:
    try:
        return str(ipaddress.ip_address(str(v).strip()))
    except ValueError:
        return None


def _on_mqtt(parts, payload, retain):
    """panel/_new/<mac> (a starter's announce), panel/<name>/ip, panel/<name>/cmd/<device>/<code>."""
    if len(parts) == 3 and parts[1] == "_new":
        topic_mac = mac = parts[2]
        if not re.fullmatch(r"[0-9A-Fa-f]{12}", mac):
            return
        try:
            d = json.loads(payload) if payload else None
        except ValueError:
            d = None
        mac = mac.lower()
        with _lock:
            _expire()
            if not isinstance(d, dict) or not isinstance(d.get("code"), str):
                return                                  # a listed screen leaves by binding or by age only
            if mac in _new or len(_new) >= NEW_MAX:
                return
            _new[mac] = {"mac": mac, "hw": str(d.get("hw", ""))[:32], "code": d["code"][:8],
                         "node": str(d.get("node", ""))[:64], "ip": _ip_ok(d.get("ip")), "seen": time.time(), "topic": topic_mac}
    elif len(parts) == 3 and parts[2] == "ip" and parts[1] in P._layouts:
        ip = _ip_ok(payload.decode(errors="replace")) if payload else None
        with _lock:
            if ip:
                _ips[parts[1]] = ip
            else:
                _ips.pop(parts[1], None)
    elif len(parts) == 5 and parts[2] == "cmd" and not retain:
        name, dev, code = parts[1], parts[3], parts[4]
        if (dev, code) not in allowed_commands(name):
            core.log.warning(f"[panels] {name}: a command for {dev}/{code}, which no widget of it sends, refused")
            return
        core.mqtt_command(dev, code, payload, "/".join(parts))


core.MQTT_SUBSCRIPTIONS.extend(["panel/_new/+", "panel/+/ip", "panel/+/cmd/+/+"])
# its handler is the panels plug-in's one for panel/ (__init__._voice_cmd), which passes these topics on


NEW_MAX, NEW_TTL_S, ARP_FRESH_S = 20, 600, 60


def _expire():
    for k in [k for k, v in _new.items() if time.time() - v["seen"] > NEW_TTL_S]:
        _new.pop(k)


def allowed_commands(name: str) -> set:
    """(device, code) pairs an ESP panel may command: exactly what its widgets send, nothing they only show."""
    lay = P._layouts.get(name)
    if not lay or lay.get("hw") not in gen.profiles():
        return set()
    out = set()
    for w0 in lay.get("widgets") or []:
        try:                              # one widget that does not map leaves the others' commands
            w = bus_layout(name, {**lay, "widgets": [w0]})["widgets"][0]
        except (HTTPException, IndexError) as e:
            core.log.warning(f"[panels] {name}: widget {w0.get('id') if isinstance(w0, dict) else '?'} "
                             f"sends nothing: {getattr(e, 'detail', e)}")
            continue
        for k in P._CMD_KEYS:
            m = re.fullmatch(rf"panel/{re.escape(name)}/cmd/([^/]+)/([^/]+)", str(w.get(k) or ""))
            if m:
                out.add((m.group(1), m.group(2)))
        if w.get("type") == "remote" and isinstance(w.get("device"), str):
            out |= {(w["device"], f"ir_{b['id']}") for b in w.get("buttons") or [] if isinstance(b, dict) and b.get("id")}
    return out


def _arp() -> dict:
    """IPv4 -> MAC of the host's network, from this plug-in's discovery module (the core's discover container)."""
    try:
        d = json.loads((core.plugin_state("panels") / "arp.json").read_text())
        age = time.time() - float(d.get("updated"))
        table = d.get("arp")
    except (OSError, ValueError, TypeError, AttributeError):
        return {}
    if not (0 <= age < ARP_FRESH_S) or not isinstance(table, dict):
        return {}
    return {str(k): str(v).lower() for k, v in table.items()}


def _target(name: str, lay: dict) -> str:
    """The address to flash: the one the panel's MAC has on the network. The MAC comes from a bind by code
    or from a human who confirmed it on the panel's card, never from what a broker client publishes."""
    arp = _arp()
    if not arp:
        raise HTTPException(503, "no view of the network: the discover container is not running")
    mac = str(lay.get("mac") or "").lower()
    if not mac:
        raise HTTPException(409, f"panel {name}: confirm its screen first (its card shows the MAC and address)")
    ip = next((i for i, m in arp.items() if m == mac), None)
    if not ip:
        raise HTTPException(409, f"panel {name} ({mac}) is not on the network now")
    return ip


def candidate(name: str):
    """What a panel without a MAC claims to be: {ip, mac} from its reported address and the host's table,
    shown on its card for a human to confirm (the claim alone is never trusted)."""
    with _lock:
        ip = _ips.get(name)
    mac = _arp().get(ip) if ip else None
    return {"ip": ip, "mac": mac} if mac else None


class MacBody(core.Body):
    mac: str


@core.app.put("/api/panels/{name}/mac")
def confirm_mac(name: str, body: MacBody, request: Request):
    """A human confirmed which screen this panel is (its MAC, as its card showed it)."""
    _house(request)
    lay = P._need(name)
    mac = body.mac.replace(":", "").lower()
    if lay.get("hw") not in gen.profiles() or not re.fullmatch(r"[0-9a-f]{12}", mac):
        raise HTTPException(400, "a MAC of 12 hex digits, for an ESP panel")
    with P._lock:
        same = next((n for n, other in P._layouts.items() if n != name and str(other.get("mac") or "").lower() == mac), None)
        if same is not None:
            raise HTTPException(409, f"this MAC is panel {same}'s: one screen, one panel")
        lay["mac"] = mac
        P._save_layouts()
    return {"ok": True, "panel": name, "mac": mac}


def new_list() -> list:
    with _lock:
        _expire()
        return sorted(({k: v for k, v in d.items() if k not in ("code", "topic")} for d in _new.values()),
                      key=lambda d: d["mac"])


def _owner(topic: str, want: str = "state"):
    """(device, code) behind a widget topic: the house bus itself, or a topic a device declares. When
    several controls share a declared topic, the one that has it as `want` (state | command) wins, then
    a switch: the result does not depend on the order of the controls."""
    m = re.fullmatch(r"home/(?:state|cmd)/([^/]+)/([^/]+)", topic) or re.fullmatch(r"panel/[^/]+/cmd/([^/]+)/([^/]+)", topic)
    if m:
        dev, code = m.group(1), m.group(2)
        ctl = (core.DEVICES.get(dev) or {}).get("_ctl") or {}
        return dev, (code[:-4] if code.endswith("_pct") and code not in ctl else code)
    hits = [(c.get(f"{want}_topic") != topic, c.get("kind") != "switch", did, str(c.get("code") or ""))
            for did, cfg in list(core.DEVICES.items()) for c in cfg.get("controls") or []
            if isinstance(c, dict) and topic in (c.get("state_topic"), c.get("command_topic"))]
    return min(hits)[2:] if hits else None


def _boolean(dev: str, code: str) -> bool:
    """The bus carries this control's value as true/false (a switch), not as its own words or numbers."""
    v = ((core.status_cache.get(dev) or {}).get("values") or {}).get(code)
    ctl = (core.DEVICES.get(dev, {}).get("_ctl") or {}).get(code) or {}
    return isinstance(v, bool) or ctl.get("kind") == "switch" or ctl.get("vtype") == "bool"


def bus_layout(name: str, layout: dict) -> dict:
    """The layout with every widget topic on the house bus: states from home/state/<dev>/<code> (percent
    for a brightness), commands to panel/<name>/cmd/<dev>/<code>. On/off payloads become true/false only
    where the control is a switch; a sensor's words and a scene's payload stay as they are."""
    out = json.loads(json.dumps(layout))
    for w in out.get("widgets") or []:
        bright = state_bool = cmd_bool = False
        for k in P._TOPIC_KEYS:
            t = w.get(k)
            if not isinstance(t, str) or not t:
                continue
            ref = _owner(t, "state" if k in _STATE_KEYS else "command")
            if not ref or ref[0] not in core.DEVICES:
                raise HTTPException(400, f"widget {w.get('id')}: {k} {t} belongs to no device of the house")
            dev, code = ref
            kind = ((core.DEVICES[dev].get("_ctl") or {}).get(code) or {}).get("kind")
            if k in _STATE_KEYS:
                bright = bright or kind == "bright"
                state_bool = state_bool or (k in ("state_topic", "onoff_topic", "power_state") and _boolean(dev, code))
                w[k] = f"home/state/{dev}/{code}_pct" if kind == "bright" else f"home/state/{dev}/{code}"
            else:
                cmd_bool = cmd_bool or (k in ("cmd_topic", "power_cmd") and _boolean(dev, code))
                w[k] = f"panel/{name}/cmd/{dev}/{code}"
        if state_bool and ("state_on" in w or w.get("type") == "toggle"):
            w["state_on"] = "true"
        if cmd_bool:
            if "cmd_on" in w or w.get("type") == "toggle":
                w["cmd_on"], w["cmd_off"] = "true", "false"
            pay = str(w.get("cmd_payload", "")).strip().lower()
            if pay in _ON | _OFF:
                w["cmd_payload"] = "true" if pay in _ON else "false"
        if bright:
            w["state_max"] = w["cmd_max"] = 100
    return out


ACCOUNTS = core.plugin_state_file("panels", "mqtt_accounts.json")
_CORE = core.CHECKOUT                                            # tools/mkpasswd.py
_acct_lock = threading.Lock()


def _accounts() -> list:
    try:
        d = json.loads(ACCOUNTS.read_text())
    except ValueError as e:
        P._BROKEN[str(ACCOUNTS)] = str(e)
        return []
    except OSError:
        return []
    return d if isinstance(d, list) else []


def _acls(a: dict) -> list:
    rw = ("publishClientSend", "publishClientReceive", "subscribePattern", "unsubscribePattern")
    ro = ("publishClientReceive", "subscribePattern", "unsubscribePattern")
    return ([{"acltype": k, "topic": t, "allow": True, "priority": 0} for t in a["topics"] for k in rw]
            + [{"acltype": k, "topic": t, "allow": True, "priority": 0} for t in a["read"] for k in ro])


def _broker(commands: list, wait: float = 5.0) -> bool:
    """Tell the running broker (dynamic security) and wait for ITS answers (each command carries a
    correlationData, so another admin's traffic on the shared response topic never counts). A delete of
    something gone counts as done. False when an answer is
    missing within `wait` or carries another error: a broker still on its file from before the update
    (no $CONTROL right for the backend) answers nothing."""
    if core.paho is None:
        return False
    ids = [secrets.token_hex(6) for _ in commands]
    sent = [{**c, "correlationData": i} for c, i in zip(commands, ids)]
    box = queue.Queue()
    cli = core.paho.Client(core.paho.CallbackAPIVersion.VERSION2, client_id=f"panels-acct-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: box.put(m.payload)
    got = {}
    try:
        core.mqtt_auth(cli).connect(core.MQTT_HOST, core.MQTT_PORT, keepalive=10)
        cli.subscribe("$CONTROL/dynamic-security/v1/response", qos=1)
        cli.loop_start()
        time.sleep(0.3)                                     # the SUBACK before the command
        cli.publish("$CONTROL/dynamic-security/v1", json.dumps({"commands": sent}), qos=1)
        deadline = time.time() + wait
        while len(got) < len(ids) and time.time() < deadline:
            try:
                for r in json.loads(box.get(timeout=max(0.05, deadline - time.time()))).get("responses") or []:
                    if isinstance(r, dict) and r.get("correlationData") in ids:
                        got[r["correlationData"]] = r
            except queue.Empty:
                break
            except ValueError:
                continue
    except Exception as e:                                  # noqa: BLE001 - no broker, no answer
        core.log.error(f"[panels] broker account change not confirmed: {type(e).__name__}: {e}")
        return False
    finally:
        try:
            cli.loop_stop()
            cli.disconnect()
        except Exception:                                   # noqa: BLE001
            pass
    bad = []
    for c, i in zip(commands, ids):
        err = (got.get(i) or {"error": "no answer"}).get("error")
        fine = not err or (c["command"].startswith("delete") and "not found" in err)
        if not fine:
            bad.append((c["command"], err))
    if bad:
        core.log.error(f"[panels] broker account change refused: {bad}")
    return not bad


def _persist():
    """The broker's file for its next start (mkpasswd --accounts-only: no plug-in's other files)."""
    def run():
        r = subprocess.run([sys.executable, str(_CORE / "tools" / "mkpasswd.py"), "--accounts-only"], cwd=str(_CORE),
                           capture_output=True, text=True, timeout=120)
        if r.returncode:
            core.log.error(f"[panels] broker accounts not written: {(r.stderr or r.stdout).strip()[-300:]}")
    threading.Thread(target=run, daemon=True, name="panels-mkpasswd").start()


def _account(name: str) -> tuple:
    """The panel's own broker account (panel-<name>: its panel/<name>/# and the state it shows), made
    once and confirmed by the broker before a firmware carries it; one the broker never took is sent
    again at the next build."""
    user = f"panel-{name}"
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", user):
        raise HTTPException(400, f"panel {name!r}: no broker account for this name (small latin letters, digits, -)")
    with _acct_lock:
        lst = _accounts()
        a = next((x for x in lst if x.get("user") == user), None)
        if a is not None and a.get("synced", True):
            return user, a["password"]
        if a is None:
            a = {"user": user, "password": secrets.token_urlsafe(24), "topics": [f"{P.NS}/{name}/#"],
                 "read": ["home/state/#", "home/weather", "home/clock/#"], "synced": False}
            lst = lst + [a]
        P._guard(ACCOUNTS)
        core.atomic_json_dump(ACCOUNTS, lst)
        os.chmod(ACCOUNTS, 0o600)
        try:
            # replaced as a whole: an old account of this name (its file entry lost, or older rights) goes first
            ok = _broker([{"command": "deleteClient", "username": user}, {"command": "deleteRole", "rolename": user},
                          {"command": "createRole", "rolename": user, "acls": _acls(a)},
                          {"command": "createClient", "username": user, "password": a["password"],
                           "roles": [{"rolename": user}]}])
            if ok:
                a.pop("synced", None)
                core.atomic_json_dump(ACCOUNTS, lst)
                core.log.warning(f"[panels] broker account {user} made")
        finally:
            _persist()
    if not ok:
        raise HTTPException(503, "the broker did not take the panel's account: restart it once "
                                 "(docker compose restart mosquitto), then build again")
    return user, a["password"]


def _drop_account(name: str):
    user = f"panel-{name}"
    with _acct_lock:
        lst = _accounts()
        if not any(x.get("user") == user for x in lst):
            return
        P._guard(ACCOUNTS)
        core.atomic_json_dump(ACCOUNTS, [x for x in lst if x.get("user") != user])
        if not _broker([{"command": "deleteClient", "username": user}, {"command": "deleteRole", "rolename": user}]):
            core.log.warning(f"[panels] {user}: the running broker keeps it until its next start (the file has it no more)")
        _persist()


def _secrets(request: Request, name: str = None) -> str:
    env = os.environ.get
    host, warning = core.node_broker_host(request)
    if warning:
        raise HTTPException(400, f"{warning}: the screens may not reach '{host}'")
    vals = {"wifi_ssid": env("HOUSE_WIFI_SSID"), "wifi_password": env("HOUSE_WIFI_PASSWORD"),        # the house Wi-Fi
            "mqtt_host": host,
            "mqtt_port": core.node_broker_port(),
            "mqtt_user": env("MQTT_PANELS_USER") or "panels", "mqtt_password": env("MQTT_PANELS_PASSWORD"),
            **(dict(zip(("mqtt_user", "mqtt_password"), _account(name))) if name else {}),
            "ota_password": env("PANELS_OTA_PASSWORD"), "ap_password": env("PANELS_AP_PASSWORD")}
    missing = sorted(k for k, v in vals.items() if not v)
    if missing:
        raise HTTPException(400, f"set these in .env first (make update): {', '.join(missing)}")
    return "".join(f"{k}: {json.dumps(v)}\n" for k, v in vals.items())


KEYS = core.plugin_state_file("panels", "service_keys.json")


def _keys() -> dict:
    try:
        d = json.loads(KEYS.read_text())
    except ValueError as e:
        P._BROKEN[str(KEYS)] = str(e)
        return {}
    except OSError:
        return {}
    return d if isinstance(d, dict) else {}


def _save_keys(keys: dict):
    P._guard(KEYS)                    # never the panels' firmware keys overwritten by an empty set
    core.atomic_json_dump(KEYS, keys)
    os.chmod(KEYS, 0o600)


def _key_live(name: str) -> str:
    """What the panel's current firmware takes for restart / safe mode: its key, or PRESS (older firmware)."""
    return (_keys().get(name) or {}).get("live") or "PRESS"


def _key_next(name: str) -> str:
    """A new key for the firmware about to be built; it becomes live once that firmware is in."""
    keys = _keys()
    nxt = secrets.token_urlsafe(24)
    keys[name] = {**(keys.get(name) or {}), "next": nxt}
    _save_keys(keys)
    return nxt


def _key_promote(name: str):
    keys = _keys()
    if (keys.get(name) or {}).get("next"):
        keys[name] = {"live": keys[name]["next"]}
        _save_keys(keys)


def _house(request: Request):
    if not core.is_house_token(request.headers):
        raise HTTPException(403, "only the dashboard flashes or binds a screen")


def _files(name: str, layout: dict, request: Request, starter: bool = False, key: str = None) -> dict:
    try:
        files = gen.generate(name, layout, starter=starter, service_key=key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    files["secrets.yaml"] = _secrets(request, None if starter else name)
    return files


def _port(ip: str, port: int, want_open: bool, wait_s: float) -> bool:
    """Wait until the port is open (or closed): True when it got there in time."""
    deadline, closed = time.monotonic() + wait_s, 0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((ip, port), timeout=2):
                is_open = True
        except OSError:
            is_open = False
        closed = 0 if is_open else closed + 1
        if (want_open and is_open) or (not want_open and closed >= 2):
            return True
        time.sleep(0.3)               # a restart into safe mode keeps the port shut only a second or two
    return False


def _save_jobs():
    try:
        core.atomic_json_dump(JOBS, _jobs)
    except OSError as e:
        core.log.warning(f"[panels] jobs.json: {e}")


def _set(name: str, rec: dict = None, **kw):
    """A job's progress, only into that job: a hung one never writes into the job that replaced it."""
    if rec is not None and _jobs.get(name) is not rec:
        return
    _jobs[name].update(kw)
    _save_jobs()


def _watch(name: str, rec: dict, ip: str, b, press: str, done=None, mac: str = "", payload: str = "PRESS"):
    """A held job: once compiled, the screen restarts into safe mode (no display: the flash write is not
    starved by the screen's DMA). The normal firmware listens on the OTA port too, so the restart is seen
    as the port closing and opening again; only then is the upload released. done() after a good flash."""
    try:
        deadline = time.time() + 2 * HOLD_LIMIT_S
        while (st := b.job(rec["id"])).get("state") != "ready":
            if time.time() > deadline or _jobs.get(name) is not rec:
                b.release(rec["id"], abort=True)
                return _set(name, rec, phase="error", error="the build did not finish in time")
            if st.get("state") in ("done", "error"):
                return _set(name, rec, phase=st["state"], error=st.get("error"))
            time.sleep(2)
        _set(name, rec, phase="safe_mode")
        cli = core.mqtt_client
        if cli is not None:
            cli.publish(press, payload, qos=1)
        why = None
        if not _port(ip, OTA_PORT, False, RESTART_WAIT_S):
            why = f"the screen did not restart within {RESTART_WAIT_S} s"
        elif not _port(ip, OTA_PORT, True, SAFE_MODE_WAIT_S):
            why = f"the screen did not come back in safe mode within {SAFE_MODE_WAIT_S} s"
        elif mac and _arp().get(ip) != mac:
            why = "the address no longer belongs to the screen's MAC: not flashing there"
        if why or _jobs.get(name) is not rec:      # replaced meanwhile: its key is not the one this firmware holds
            b.release(rec["id"], abort=True)
            return _set(name, rec, phase="error", error=why or "replaced by a newer job")
        b.release(rec["id"])
        _set(name, rec, phase="uploading")
        while (st := b.job(rec["id"])).get("state") not in ("done", "error"):
            if time.time() > deadline or _jobs.get(name) is not rec:
                b.release(rec["id"], abort=True)
                return _set(name, rec, phase="error", error="the upload did not finish in time")
            time.sleep(2)
        _set(name, rec, phase=st["state"])
        if st["state"] == "done" and _jobs.get(name) is rec:
            _key_promote(name)
            if done:
                done()
    except Exception as e:                                # noqa: BLE001
        _set(name, rec, phase="error", error=str(e.detail) if isinstance(e, HTTPException) else f"{type(e).__name__}: {e}")
        try:
            b.release(rec["id"], abort=True)              # a held job must not wait out its 10 minutes
        except Exception:                                 # noqa: BLE001
            pass


def _busy(name: str) -> bool:
    rec = _jobs.get(name)
    return bool(rec) and rec.get("hold") and rec.get("phase") in ("compiling", "safe_mode", "uploading") \
        and time.time() - float(rec.get("started") or 0) < HOLD_LIMIT_S


def _start(name: str, b, files: dict, ip: str, press: str, done=None, mac: str = "", payload: str = "PRESS") -> str:
    if _busy(name):
        raise HTTPException(409, f"panel {name} is being flashed already")
    jid = b.submit(name, files, ota={"address": ip, "hold": True})
    rec = _jobs[name] = {"id": jid, "phase": "compiling", "error": None, "started": time.time(), "hold": True}
    _save_jobs()
    threading.Thread(target=_watch, args=(name, rec, ip, b, press, done, mac, payload), daemon=True).start()
    return jid


class BuildBody(core.Body):
    ota: bool = True


@core.app.post("/api/panels/{name}/build")
def build_panel(name: str, body: BuildBody, request: Request):
    """Compile the panel's firmware; with ota, flash it to the panel over the air (through safe mode)."""
    _house(request)
    lay = P._need(name)
    if lay.get("hw") not in gen.profiles():
        raise HTTPException(400, f"no firmware for {lay.get('hw')} panels here (an Android panel shows its layout in a browser)")
    b = _build()
    if not body.ota:
        if _busy(name):
            raise HTTPException(409, f"panel {name} is being flashed already")
        files = _files(name, bus_layout(name, lay), request)
        jid = b.submit(name, files)
        _jobs[name] = {"id": jid, "phase": "compiling", "error": None, "started": time.time()}
        _save_jobs()
        return {"ok": True, "job": jid}
    ip, live = _target(name, lay), _key_live(name)
    files = _files(name, bus_layout(name, lay), request, key=_key_next(name))
    return {"ok": True, "job": _start(name, b, files, ip, f"panel/{name}/safe_mode/command",
                                      mac=str(lay.get("mac")).lower(), payload=live)}


@core.app.get("/api/panels/{name}/build")
def build_status(name: str):
    rec = _jobs.get(name)
    if not rec:
        return {"job": None}
    try:
        st = _build().job(rec["id"])
    except HTTPException:
        st = {"state": "gone"}
    return {"job": rec["id"], "state": st.get("state"), "progress": st.get("progress"), "phase": rec["phase"],
            "error": rec["error"] or st.get("error"), "log": (st.get("log") or [])[-20:],
            "artifacts": st.get("artifacts") or []}


@core.app.post("/api/panels/starter/{hw}")
def build_starter(hw: str, request: Request):
    """The first firmware of a new screen of this hardware, for a USB flash (factory image)."""
    _house(request)
    if hw not in gen.profiles():
        raise HTTPException(404, f"no profile {hw}; known: {', '.join(gen.profiles())}")
    jid = _build().submit(f"starter-{hw}".lower(), _files(f"panel-{hw}", {"hw": hw, "widgets": []}, request, starter=True))
    return {"ok": True, "job": jid, "firmware": f"/api/build/jobs/{jid}/firmware.factory.bin"}


class BindEspBody(core.Body):
    mac: str
    code: str
    panel: str


@core.app.post("/api/panels/bind_esp")
def bind_esp(body: BindEspBody, request: Request):
    """A human compared the code on the new screen: the panel is created for its hardware and its own
    firmware goes to the screen over the air, through safe mode like every flash. The screen stays listed
    as new until its firmware is in."""
    _house(request)
    mac = body.mac.lower()
    with _lock:
        d = dict(_new.get(mac) or {})
    if not d:
        raise HTTPException(404, "no new screen with this address")
    if not d.get("code") or not hmac.compare_digest(d["code"].encode(), body.code.strip().encode()):
        raise HTTPException(400, "code mismatch")
    with _lock:
        twins = [m for m, e in _new.items() if m != mac and e.get("code") == d["code"]]
    if twins:
        raise HTTPException(409, "two new screens show this code: power the other one off, or wait 10 minutes")
    if not ESP_NAME.fullmatch(body.panel):
        raise HTTPException(400, "ESP panel name: 1-24 small latin letters, digits or - inside (it is the node name)")
    if not d.get("ip"):
        raise HTTPException(409, "the new screen did not report its address")
    arp = _arp()
    if not arp:
        raise HTTPException(503, "no view of the network: the discover container is not running")
    if arp.get(d["ip"]) != mac:
        raise HTTPException(409, "the new screen's address does not belong to its MAC: not flashing there")
    prof = gen.profiles().get(d.get("hw"))
    if not prof:
        raise HTTPException(400, f"the screen runs hardware {d.get('hw')!r}, which has no profile here")
    with P._lock:
        same = next((n for n, lay in P._layouts.items() if str(lay.get("mac") or "").lower() == mac), None)
    if same is not None and same != body.panel:
        raise HTTPException(409, f"this MAC is panel {same}'s: one screen, one panel (bind it as {same}, "
                                 f"its layout is kept, or delete {same} first)")
    old = P._layouts.get(body.panel)
    if old is not None and old.get("hw") != d["hw"]:
        raise HTTPException(409, f"panel {body.panel} exists for other hardware ({old.get('hw')})")
    lay = dict(old or {"hw": d["hw"], "w": prof["w"], "h": prof["h"], "widgets": [], "title": body.panel}, mac=mac)
    starter = f"panel-{d['hw']}"

    def done():
        cli = core.mqtt_client
        if cli is not None:
            cli.publish(f"panel/_new/{d['topic']}", None, retain=True)
        with _lock:
            _new.pop(mac, None)
        P._clear_retained(starter)
    jid = _start(body.panel, _build(), _files(body.panel, bus_layout(body.panel, lay), request, key=_key_next(body.panel)),
                 d["ip"], f"panel/_newcmd/{d['topic']}/safe_mode", done, mac, payload=d["code"])
    with P._lock:
        P._layouts[body.panel] = lay
        P._save_layouts()
    with _lock:
        _ips[body.panel] = d["ip"]
    return {"ok": True, "panel": body.panel, "job": jid}



_old_clock_dropped = threading.Event()


def _drop_old_clock(cli):
    """The clock moved to the core's home/clock/: the retained texts this plug-in once sent go, once."""
    if not _old_clock_dropped.is_set():
        for t in ("panel/_clock/time", "panel/_clock/date"):
            cli.publish(t, None, retain=True)
        _old_clock_dropped.set()


core.ON_MQTT_CONNECT.append(_drop_old_clock)


# ---- the screens' and knobs' words in the house's language (gen.WORDS_TOPIC): their firmware has none of its own ----
_words_sent = {"txt": None}


def words_payload(lang: str) -> str:
    return json.dumps({**gen.words(lang), **gen._knob().words(lang)}, ensure_ascii=False, sort_keys=True)


def words_tick():
    """Publishes the words, retained, when the house language changed (or after a reconnect)."""
    cli = core.mqtt_client
    if cli is None:
        return
    txt = words_payload(core.house_lang())
    if txt != _words_sent["txt"]:
        try:
            cli.publish(gen.WORDS_TOPIC, txt, retain=True)
            _words_sent["txt"] = txt
        except Exception as e:                            # noqa: BLE001
            core.log.warning(f"[panels] publish words: {e}")


core.ON_MQTT_CONNECT.append(lambda _cli: _words_sent.update(txt=None))       # a broker restarted: send them again


def _health():
    rows = [core.health_row("panels-discover", "ok", "Panel discovery") if _arp() else
            core.health_row("panels-discover", "bad", "Panel discovery", "new tablets and screens are not found by themselves",
                            "Restart the house; if it stays, the panel finder needs the computer's own network", link="panels", cmd="make update; docker compose logs discover")]
    db = P._db()                       # reads paired.json again: a fixed file clears its mark first
    for path, why in list(P._BROKEN.items()):
        rows.append(core.health_row("panels-file:" + path.rsplit("/", 1)[-1], "bad", "Panels",
                                    "{0} is unreadable: nothing is written over it", "Fix or remove the file ({1})",
                                    (path.rsplit("/", 1)[-1], why[:80])))
    db = P._db()
    for name, lay in list(P._layouts.items()):
        if lay.get("hw") != "android" or not lay.get("device"):
            continue
        live = P._live(lay["device"], db.get(lay["device"]) or {})
        beat = live.get("beat")
        if not beat or time.time() - float(beat) > 600:
            rows.append(core.health_row(f"panel:{name}", "warn", "Panel {0}", "no heartbeat for over 10 min",
                                        "Is the tablet on, with the panel launcher running?", (name,)))
        elif (live.get("caps") or {}).get("front") is False:     # the launcher says another app is in front of it
            rows.append(core.health_row(f"panel-front:{name}", "warn", "Panel {0}", "another app is in front of the panel",
                                        "The launcher brings itself back after 5 minutes; if it stays, look at the tablet", (name,)))
    return rows


core.HEALTH_CHECKS.append(_health)
