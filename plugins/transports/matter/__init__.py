"""matter: Matter devices of the house through python-matter-server, run by the role "Matter controller" of plug-in computers
(helper/roles/matter-install.sh: host network, that computer's Bluetooth, its WebSocket on 127.0.0.1:5580). One controller per
house: the house reaches it through the helper's ssh tunnel "matter" (helper:5580). It gets the house's Thread dataset (plug-in
thread), never logged or answered. Every commissioned node is a house device; commands go to device_command.
README.md has the whole design."""
import asyncio
import hashlib
import json
import os
import re
import sys
import threading
import time

from fastapi import HTTPException, Request

import app as core

PLUGIN = {"name": "Matter", "version": "1.0.0", "icon": "network"}

ROLE = "matter"
TRANSPORT = "matter"
URL = (os.environ.get("MATTER_URL") or "").strip() or "ws://helper:5580/ws"    # the helper's tunnel to the controller
TUNNEL_S = 60                      # the tunnel follows the computer with the role this often
RETRY_S = 15                       # a lost connection is tried again after this
CALL_S = 30                        # an API call waits this long for its answer
COMMISSION_S = 330                 # commissioning (Bluetooth, the network, the interview) may take minutes
CACHE = core.plugin_state("matter") / "nodes.json"   # the devices of the last node list: cards before the controller answers

# clusters (Matter Application Cluster spec ids) and the attributes read
ONOFF, LEVEL, COLOR = 0x0006, 0x0008, 0x0300
TEMP, HUMID, OCCUP, BOOL, POWER = 0x0402, 0x0405, 0x0406, 0x0045, 0x002F
DESCRIPTOR, BASIC, NETCOM = 0x001D, 0x0028, 0x0031
# cluster -> (kind, attribute, channel code, label, control kind, unit, value type)
CHANNELS = {
    ONOFF: ("onoff", 0, "state", "", "switch", "", "bool"),
    LEVEL: ("level", 0, "brightness", "Brightness", "bright", "", "num"),
    TEMP: ("temperature", 0, "temperature", "Temperature", "sensor", "\u00b0C", "num"),
    HUMID: ("humidity", 0, "humidity", "Humidity", "sensor", "%", "num"),
    OCCUP: ("occupancy", 0, "occupancy", "Motion", "sensor", "", "bool"),
    BOOL: ("contact", 0, "contact", "Contact", "sensor", "", "bool"),
    POWER: ("battery", 0x000C, "battery", "Battery", "sensor", "%", "num"),
}
# what every node has (commissioning, diagnostics, labels): never "not shown"
INFRA = {0x0003, 0x0004, 0x0005, 0x001D, 0x001E, 0x001F, 0x0025, 0x0028, 0x0029, 0x002A, 0x002B, 0x002C, 0x002D, 0x002E, 0x0030,
         0x0031, 0x0032, 0x0033, 0x0034, 0x0035, 0x0036, 0x0037, 0x0038, 0x0039, 0x003C, 0x003E, 0x003F, 0x0040, 0x0041, 0x0046, 0x0062}
NAMES = {COLOR: "Color", 0x0101: "Door lock", 0x0102: "Window covering", 0x0201: "Thermostat", 0x0202: "Fan", 0x0400: "Illuminance",
         0x0403: "Pressure", 0x003B: "Buttons", 0x0080: "Smoke and CO", 0x005B: "Air quality", 0x0091: "Energy", 0x0090: "Power"}
MANUAL_RE = re.compile(r"^[0-9]{11}([0-9]{10})?$")             # an 11-digit manual pairing code (21 with vendor and product)
QR_RE = re.compile(r"^MT:[0-9A-Z.-]{19,}$")                    # the text of a Matter QR code (base38)
HEX_RE = re.compile(r"^(?:[0-9a-f]{2}){1,254}$")


class MatterError(Exception):
    """The controller answered a call with an error."""

    def __init__(self, code, details):
        super().__init__(details or f"error {code}")
        self.code = code


_lock = threading.RLock()
_srv: dict = {"up": False, "info": {}, "error": None, "since": None, "computer": None, "computers": 0, "checked": False, "thread": False}
_nodes: dict = {}                  # node id -> the node as the controller has it (attributes "ep/cluster/attr" -> value)
_job: dict = {}                    # the commissioning that runs or ended last: {state, kind, started, ended, node, device, error}
_client = None                     # (Client, its event loop) while connected
_given = {"hash": "", "client": None}
_empty_tunnel = [0]


# ---- the WebSocket API of python-matter-server ----
class Client:
    """One connection: calls {message_id, command, args} -> {message_id, result} or {message_id, error_code, details};
    events {event, data} go to on_event."""

    def __init__(self, ws, on_event):
        self.ws, self.on_event, self.pending, self.n = ws, on_event, {}, 0

    async def call(self, command: str, timeout: float = CALL_S, **args):
        self.n += 1
        mid = f"y{self.n}"
        fut = asyncio.get_running_loop().create_future()
        self.pending[mid] = fut
        try:
            await self.ws.send(json.dumps({"message_id": mid, "command": command, "args": args}))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(mid, None)

    async def read(self):
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                fut = self.pending.get(msg.get("message_id")) if isinstance(msg, dict) else None
                if fut is not None and not fut.done():
                    if "error_code" in msg:
                        fut.set_exception(MatterError(msg.get("error_code"), msg.get("details")))
                    else:
                        fut.set_result(msg.get("result"))
                elif isinstance(msg, dict) and "event" in msg:
                    try:
                        self.on_event(msg["event"], msg.get("data"))
                    except Exception as e:  # noqa: BLE001 - one bad event must not end the connection
                        core.log.warning(f"[matter] event {msg.get('event')}: {e}")
        finally:
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("the Matter controller went away"))


async def session(url: str = "") -> None:
    """One connection to the controller: its info, the Thread dataset, the node list, then events until it ends."""
    global _client
    from websockets.asyncio.client import connect
    async with connect(url or URL, open_timeout=10, ping_interval=30, max_size=64 * 2**20) as ws:
        info = json.loads(await asyncio.wait_for(ws.recv(), 10))      # the server sends its info first
        c = Client(ws, on_event)
        reader = asyncio.create_task(c.read())
        try:
            with _lock:
                _srv.update(up=True, info=_info(info), error=None, since=time.time())
                _client = (c, asyncio.get_running_loop())
            core.log.info(f"[matter] controller answers (SDK {_srv['info'].get('sdk_version')})")
            await give_dataset(c)
            sync_nodes(await c.call("start_listening", timeout=120) or [])
            await reader
        finally:
            reader.cancel()
            with _lock:
                if _client and _client[0] is c:
                    _client = None
                _srv.update(up=False, since=time.time())
            _all_offline()


def _info(info) -> dict:
    keep = ("sdk_version", "schema_version", "bluetooth_enabled", "thread_credentials_set", "wifi_credentials_set")
    return {k: info.get(k) for k in keep} if isinstance(info, dict) else {}


async def _run() -> None:
    """The connection, made again RETRY_S after it ended (only while a computer has the role, or MATTER_URL is set)."""
    await asyncio.sleep(5)
    while True:
        if _srv.get("computer") or os.environ.get("MATTER_URL"):
            try:
                await session()
            except Exception as e:  # noqa: BLE001 - the controller or the tunnel is down: next time
                with _lock:
                    _srv.update(up=False, error=str(e)[:160] or e.__class__.__name__)
        await asyncio.sleep(RETRY_S)


def _client_now():
    with _lock:
        c = _client
    if c is None:
        raise HTTPException(503, "the Matter controller does not answer")
    return c


async def call(command: str, timeout: float = CALL_S, **args):
    """One API call on the connection's own loop, from any loop."""
    c, loop = _client_now()
    try:
        if asyncio.get_running_loop() is loop:
            return await c.call(command, timeout, **args)
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(c.call(command, timeout, **args), loop))
    except (asyncio.TimeoutError, ConnectionError, OSError):
        raise HTTPException(503, "the Matter controller does not answer") from None


def call_sync(command: str, timeout: float = CALL_S, **args):
    """The same from a worker thread."""
    c, loop = _client_now()
    try:
        return asyncio.run_coroutine_threadsafe(c.call(command, timeout, **args), loop).result(timeout + 5)
    except (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError):
        raise HTTPException(503, "the Matter controller does not answer") from None


# ---- the Thread dataset: from plug-in thread, to the controller only ----
def _thread_dataset() -> str:
    m = sys.modules.get("yashome_plugins.thread")
    try:
        v = m.dataset() if m is not None and hasattr(m, "dataset") else ""
    except Exception:  # noqa: BLE001 - no network kept: Thread devices wait for one
        v = ""
    return v if isinstance(v, str) and HEX_RE.match(v) else ""


async def give_dataset(c: Client) -> bool:
    """The house's Thread network to the controller (Thread devices join it); once per connection and change."""
    ds = _thread_dataset()
    if not ds:
        return False
    h = hashlib.sha256(ds.encode()).hexdigest()
    if _given["hash"] == h and _given["client"] is c:
        return True
    try:
        await c.call("set_thread_dataset", dataset=ds)
    except MatterError as e:
        core.log.warning(f"[matter] the controller did not take the Thread network (error {e.code})")   # never the dataset
        return False
    _given.update(hash=h, client=c)
    with _lock:
        _srv["thread"] = True
    core.log.info("[matter] the controller has the house's Thread network")
    return True


# ---- nodes -> house devices ----
def _attrs(node: dict) -> dict:
    a = node.get("attributes")
    return a if isinstance(a, dict) else {}


def _get(node: dict, ep: int, cl: int, at: int):
    return _attrs(node).get(f"{ep}/{cl}/{at}")


def endpoints(node: dict) -> dict:
    """{endpoint: set of server clusters}: the Descriptor's ServerList, else the clusters its attributes are of."""
    out: dict = {}
    for key, v in _attrs(node).items():
        try:
            ep, cl, at = (int(x) for x in str(key).split("/"))
        except ValueError:
            continue
        if cl == DESCRIPTOR and at == 1 and isinstance(v, list):
            out.setdefault(ep, set()).update(x for x in v if isinstance(x, int))
        else:
            out.setdefault(ep, set()).add(cl)
    return out


def network(node: dict) -> str | None:
    """thread / wifi / ethernet: NetworkCommissioning's feature map on endpoint 0, else its diagnostics cluster."""
    fm = _get(node, 0, NETCOM, 0xFFFC)
    if isinstance(fm, int) and fm & 7:
        return "wifi" if fm & 1 else "thread" if fm & 2 else "ethernet"
    eps = endpoints(node).get(0, set())
    return "thread" if 0x0035 in eps else "wifi" if 0x0036 in eps else "ethernet" if 0x0037 in eps else None


def device_id(nid: int) -> str:
    return f"matter-{int(nid)}"


def node_name(node: dict) -> str:
    for at in (5, 3):                                              # NodeLabel, ProductName
        v = _get(node, 0, BASIC, at)
        if isinstance(v, str) and v.strip():
            return v.strip()[:60]
    return f"Matter {node.get('node_id')}"


def channels(node: dict) -> tuple:
    """([control], {code: [endpoint, kind]}, [not shown]) of a node: one channel per mapped cluster and endpoint; a kind on
    several endpoints gets _<endpoint> on each code (state_1, brightness_1: the core pairs them)."""
    eps = endpoints(node)
    found = [(ep, cl) for ep in sorted(eps) for cl in sorted(eps[ep]) if cl in CHANNELS]
    found = [(ep, cl) for ep, cl in found if cl != POWER or _get(node, ep, POWER, CHANNELS[POWER][1]) is not None]
    count: dict = {}
    for _, cl in found:
        count[cl] = count.get(cl, 0) + 1
    controls, where = [], {}
    for ep, cl in found:
        kind, _, code, label, ckind, unit, vtype = CHANNELS[cl]
        if count[cl] > 1:
            code, label = f"{code}_{ep}", f"{label} {ep}".strip()
        c = {"kind": ckind, "code": code, "label": label}
        if ckind == "bright":
            c.update(min=0, max=254)
        elif ckind == "sensor":
            c.update(unit=unit, scale=1, ro=True, vtype=vtype)
        controls.append(c)
        where[code] = [ep, kind]
    rest = sorted({cl for ep, cls in eps.items() if ep != 0 for cl in cls} - set(CHANNELS) - INFRA)
    return controls, where, [NAMES.get(cl, f"0x{cl:04x}") for cl in rest]


def device_from_node(node: dict) -> dict:
    nid = int(node["node_id"])
    controls, where, rest = channels(node)
    unsupported = not controls
    if unsupported:
        controls = [{"kind": "sensor", "code": "reachable", "label": "Reachable", "unit": "", "scale": 1, "ro": True, "vtype": "bool"}]
        where = {"reachable": [0, "reachable"]}
    vendor, product = _get(node, 0, BASIC, 1), _get(node, 0, BASIC, 3)
    return {"id": device_id(nid), "name": node_name(node), "transport": TRANSPORT,
            "category": "switch" if any(c["kind"] in ("switch", "bright") for c in controls) else "sensor",
            "controls": controls, "unsupported": unsupported, "vendor": vendor if isinstance(vendor, str) else None,
            "model": product if isinstance(product, str) else None, "_node": nid, "_ep": where, "_not_shown": rest}


def value(kind: str, v):
    """A cluster attribute as the house shows it (None: no value)."""
    if v is None:
        return None
    try:
        if kind in ("onoff", "contact"):
            return bool(v)
        if kind == "occupancy":
            return bool(int(v) & 1)
        if kind in ("temperature", "humidity"):
            return round(int(v) / 100, 2)
        if kind == "battery":
            return round(int(v) / 2)
        if kind == "level":
            return int(v)
    except (TypeError, ValueError):
        return None
    return None


def _values(node: dict, where: dict) -> dict:
    out = {}
    for code, (ep, kind) in where.items():
        if kind == "reachable":
            out[code] = bool(node.get("available"))
            continue
        cl = next(c for c, spec in CHANNELS.items() if spec[0] == kind)
        v = value(kind, _get(node, ep, cl, CHANNELS[cl][1]))
        if v is not None:
            out[code] = v
    return out


def _store(did: str, node: dict, where: dict) -> None:
    prev = core.status_copy(did, {"values": {}, "raw": {}})
    vals = _values(node, where)
    old = prev.get("values") or {}
    st = {**prev, "online": bool(node.get("available")), "values": {**old, **vals}}
    st.pop("stale", None)
    if st["online"]:
        core.store_status(did, st, codes={k for k, v in vals.items() if old.get(k) != v})
    else:
        core.store_status_exact(did, st)


def _register(node: dict) -> str | None:
    """Add or update the node's device; its values into the state. None when the id is another's."""
    d = device_from_node(node)
    cur = core.DEVICES.get(d["id"])
    try:
        if cur is None:
            core.register_device(d)
        elif cur.get("transport") == TRANSPORT:
            if any(cur.get(k) != d.get(k) for k in ("controls", "vendor", "model", "_ep", "_not_shown", "unsupported")) \
                    or [c.get("code") for c in cur.get("controls") or []] != [c["code"] for c in d["controls"]]:
                core.update_device(d)
        else:
            core.log.warning(f"[matter] {d['id']} skipped: the id is taken by a {cur.get('transport')} device")
            return None
    except ValueError as e:
        core.log.warning(f"[matter] {d['id']} skipped: {e}")
        return None
    _store(d["id"], node, d["_ep"])
    return d["id"]


def _mine() -> list:
    return [k for k, d in list(core.DEVICES.items()) if d.get("transport") == TRANSPORT and core.device_owner.get(k) == "matter"]


def _save_cache() -> None:
    devs = [{k: v for k, v in d.items() if k in ("id", "name", "transport", "category", "controls", "unsupported", "vendor", "model",
                                                  "_node", "_ep", "_not_shown")} for d in (core.DEVICES.get(i) for i in _mine()) if d]
    for d in devs:
        d["controls"] = [{k: v for k, v in c.items() if not k.startswith("_") and k != "confirm"} for c in d["controls"]]
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        core.atomic_json_dump(CACHE, devs)
    except OSError as e:
        core.log.warning(f"[matter] {CACHE.name}: {e.strerror}")


def _load_cache() -> None:
    """The last node list's devices, offline until the controller answers: rules and rooms keep their targets."""
    try:
        devs = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for d in devs if isinstance(devs, list) else []:
        if isinstance(d, dict) and str(d.get("id") or "").startswith("matter-") and d["id"] not in core.DEVICES:
            try:
                core.register_device({**d, "transport": TRANSPORT})
            except (ValueError, KeyError, TypeError) as e:
                core.log.warning(f"[matter] cached {d.get('id')}: {e}")


def sync_nodes(nodes: list) -> None:
    """The whole node list (start_listening): every node a device; a device whose node is gone leaves (state kept). An empty
    list while the house had nodes is a controller with a new storage: nothing is dropped, the health page says so."""
    good = [n for n in nodes if isinstance(n, dict) and isinstance(n.get("node_id"), int)]
    with _lock:
        _nodes.clear()
        _nodes.update({n["node_id"]: n for n in good})
    present = {i for i in (_register(n) for n in good) if i}
    had = _mine()
    if not good and had:
        core.log.warning(f"[matter] the controller has no nodes, the house had {len(had)}: kept (a new controller storage?)")
    else:
        for did in had:
            if did not in present:
                core.remove_device(did, TRANSPORT, keep_state=True)
    _save_cache()


def _node_of(nid) -> dict | None:
    with _lock:
        return _nodes.get(nid)


def on_event(event: str, data) -> None:
    """node_added / node_updated: the whole node; attribute_updated: [node id, "ep/cluster/attr", value]; node_removed: its id."""
    if event in ("node_added", "node_updated") and isinstance(data, dict) and isinstance(data.get("node_id"), int):
        with _lock:
            _nodes[data["node_id"]] = data
        had = device_id(data["node_id"]) in core.DEVICES
        if _register(data) and not had:
            _save_cache()
    elif event == "attribute_updated" and isinstance(data, list) and len(data) == 3 and isinstance(data[0], int):
        nid, path, v = data
        with _lock:
            node = _nodes.get(nid)
            if node is None:
                return
            node.setdefault("attributes", {})[str(path)] = v
        d = core.DEVICES.get(device_id(nid))
        if d is None or d.get("transport") != TRANSPORT:
            return
        if str(path).split("/")[1:2] == [str(DESCRIPTOR)]:
            _register(node)                                        # its endpoints changed: its channels too
        else:
            _store(d["id"], node, d.get("_ep") or {})
    elif event == "node_removed" and isinstance(data, int):
        with _lock:
            _nodes.pop(data, None)
        if device_id(data) in core.DEVICES:
            core.remove_device(device_id(data), TRANSPORT)
            _save_cache()
    elif event == "server_info_updated" and isinstance(data, dict):
        with _lock:
            _srv["info"] = _info(data)


def _all_offline() -> None:
    for did in _mine():
        st = core.status_copy(did, {"values": {}, "raw": {}})
        if st.get("online"):
            st["online"] = False
            core.store_status_exact(did, st)


# ---- commands from a card or a rule ----
def _command(cfg: dict, code: str, val) -> tuple:
    spec = (cfg.get("_ep") or {}).get(code)
    if not spec or spec[1] not in ("onoff", "level"):
        raise HTTPException(400, "not a channel you can switch")
    ep, kind = spec
    if kind == "onoff":
        return {"node_id": cfg["_node"], "endpoint_id": ep, "cluster_id": ONOFF, "command_name": "On" if val else "Off", "payload": {}}
    try:
        level = max(0, min(254, int(val)))
    except (TypeError, ValueError):
        raise HTTPException(400, "brightness: a number 0-254") from None
    return {"node_id": cfg["_node"], "endpoint_id": ep, "cluster_id": LEVEL, "command_name": "MoveToLevelWithOnOff",
            "payload": {"level": level, "transitionTime": 0, "optionsMask": 0, "optionsOverride": 0}}


def _refused(e: MatterError) -> HTTPException:
    return HTTPException(502, f"the device did not take it: {e}")


def control(cfg: dict, code: str, val, kind=None) -> None:
    """core.TRANSPORTS: a worker thread (rules, schedules)."""
    try:
        call_sync("device_command", **_command(cfg, code, val))
    except MatterError as e:
        raise _refused(e) from None
    core.log.info(f"[control] {cfg.get('name')}/{code}={val} via matter")


async def control_async(cfg: dict, code: str, val) -> None:
    """core.ASYNC_CONTROL: the card."""
    try:
        await call("device_command", **_command(cfg, code, val))
    except MatterError as e:
        raise _refused(e) from None
    core.log.info(f"[control] {cfg.get('name')}/{code}={val} via matter")


core.TRANSPORTS[TRANSPORT] = control
core.ASYNC_CONTROL[TRANSPORT] = control_async


# ---- the controller's computer and the helper's tunnel ----
def _controllers() -> list:
    pcs = sys.modules.get("yashome_plugins.computers")
    return pcs.with_role(ROLE) if pcs else []


def role_active(computer: dict) -> bool:
    """For the plug-in computers: the house is connected to the controller on this computer."""
    with _lock:
        return bool(_srv["up"] and _srv.get("host") and _srv["host"] == computer.get("host"))


def sync_tunnel() -> None:
    """The tunnel "matter" goes to the computer with the role (the first one when several); none for 3 checks: no tunnel."""
    have = _controllers()
    want = have[0] if have else None
    cur = (core.helper("GET", "/v1/tunnels", None, 5).get("tunnels") or {}).get("matter") or {}
    _empty_tunnel[0] = 0 if want else _empty_tunnel[0] + 1
    if want and cur.get("host") != want["host"]:
        core.helper("POST", "/v1/tunnels", {"role": "matter", "host": want["host"], "user": want["user"], "wsl": ""}, 15)
    elif not want and cur.get("host") and _empty_tunnel[0] >= 3:
        core.helper("POST", "/v1/tunnels", {"role": "matter"}, 15)
    with _lock:
        _srv.update(computer=want["name"] if want else None, host=want["host"] if want else None, computers=len(have),
                    checked=True, others=[c["name"] for c in have[1:]])


def _tunnel_loop() -> None:
    time.sleep(15)
    while True:
        try:
            sync_tunnel()
        except Exception as e:  # noqa: BLE001 - the helper may be down: next time
            core.log.warning(f"[matter] tunnel: {getattr(e, 'detail', e)}")
        time.sleep(TUNNEL_S)


core.STARTUP_TASKS.append(_tunnel_loop)
core.STARTUP_TASKS.append(_run)


# ---- commissioning ----
def parse_code(code: str) -> tuple:
    """(kind, code): "qr" for an MT:... text, "manual" for the 11 (or 21) digits; spaces and dashes go."""
    c = (code or "").strip()
    if c[:3].upper() == "MT:":
        c = "MT:" + c[3:].strip().upper()
        if QR_RE.match(c):
            return "qr", c
    else:
        d = re.sub(r"[\s-]", "", c)
        if MANUAL_RE.match(d):
            return "manual", d
    raise HTTPException(400, "a Matter code: the 11 digits under the QR code, or the text of the QR code (MT:...)")


async def commission(code: str, kind: str, started: float) -> None:
    """commission_with_code: over Bluetooth and the network when the controller has Bluetooth (a new device near it, or a
    code shared from a phone app), else the network only. The code is never logged."""
    try:
        bt = bool(_srv["info"].get("bluetooth_enabled"))
        try:
            c, _ = _client_now()
            await give_dataset(c)
        except HTTPException:
            pass
        node = await call("commission_with_code", timeout=COMMISSION_S, code=code, network_only=not bt)
        nid = node.get("node_id") if isinstance(node, dict) else None
        if isinstance(nid, int):
            on_event("node_added", node)
        res = {"state": "done", "node": nid, "device": device_id(nid) if isinstance(nid, int) else None, "error": None}
        core.log.info(f"[matter] commissioned node {nid} ({kind} code)")
    except MatterError as e:
        res = {"state": "failed", "error": str(e)[:200] or "commissioning failed"}
        core.log.warning(f"[matter] commissioning ({kind} code) failed: {res['error']}")
    except HTTPException as e:
        res = {"state": "failed", "error": str(e.detail)}
    except Exception as e:  # noqa: BLE001 - the job must end with a state
        res = {"state": "failed", "error": str(e)[:200] or e.__class__.__name__}
    with _lock:
        if _job.get("started") == started:
            _job.update(res, ended=time.time())


def _job_view() -> dict:
    with _lock:
        j = dict(_job)
    return {"state": j.get("state") or "idle", "kind": j.get("kind"), "node": j.get("node"), "device": j.get("device"),
            "error": j.get("error"), "seconds": int((j.get("ended") or time.time()) - j["started"]) if j.get("started") else 0}


class CommissionBody(core.Body):
    code: str


@core.app.post("/api/matter/commission")
def api_commission(body: CommissionBody, request: Request):
    """A device into the house's Matter network by its code: a new one (Bluetooth, near the controller's computer) or one
    shared from a phone app (already on the network). Runs in the background: GET follows it."""
    core.house_token_only(request)
    kind, code = parse_code(body.code)
    _, loop = _client_now()
    with _lock:
        if _job.get("state") == "running":
            raise HTTPException(409, "a commissioning runs already")
        started = time.time()
        _job.clear()
        _job.update(state="running", kind=kind, started=started)
    asyncio.run_coroutine_threadsafe(commission(code, kind, started), loop)
    core.log.info(f"[matter] commissioning started ({kind} code)")
    return _job_view()


@core.app.get("/api/matter/commission")
def api_commission_state(request: Request):
    core.house_token_only(request)
    return _job_view()


@core.app.delete("/api/matter/nodes/{nid}")
def api_unpair(nid: int, request: Request):
    """Unpair: the node leaves the house's fabric (remove_node) and its device the house."""
    core.house_token_only(request)
    if _node_of(nid) is None:
        raise HTTPException(404, "no such Matter node")
    try:
        call_sync("remove_node", node_id=nid)
    except MatterError as e:
        raise HTTPException(502, f"the controller did not remove it: {e}") from None
    on_event("node_removed", nid)
    core.log.info(f"[matter] node {nid} unpaired")
    return view()


# ---- the Settings page ----
def node_view(node: dict) -> dict:
    nid = node["node_id"]
    d = core.DEVICES.get(device_id(nid)) or {}
    _, where, rest = channels(node)
    return {"node_id": nid, "device": d.get("id"), "name": d.get("name") or node_name(node), "vendor": _get(node, 0, BASIC, 1),
            "product": _get(node, 0, BASIC, 3), "available": bool(node.get("available")), "network": network(node),
            "bridge": bool(node.get("is_bridge")), "channels": sorted(where), "not_shown": rest}


def view() -> dict:
    with _lock:
        nodes = [dict(n) for n in _nodes.values()]
        s = {k: _srv.get(k) for k in ("up", "error", "computer", "computers", "checked", "thread")}
        s["info"] = dict(_srv["info"])
        s["others"] = list(_srv.get("others") or [])
    s["thread_kept"] = bool(_thread_dataset())
    return {"server": s, "nodes": sorted((node_view(n) for n in nodes), key=lambda n: n["node_id"]), "commission": _job_view()}


@core.app.get("/api/matter")
def api_view(request: Request):
    core.house_token_only(request)
    return view()


# ---- health ----
def health() -> list:
    with _lock:
        s = dict(_srv)
        nodes = [dict(n) for n in _nodes.values()]
    if not s["computer"]:
        if not s["checked"]:
            return []                  # not looked yet
        return [core.health_row("matter", "warn", "Matter", "no controller",
                                "Install the role Matter controller on a computer with Bluetooth (Computers and servers)", link="computers")]
    rows = []
    if not s["up"]:
        rows.append(core.health_row("matter", "bad", "Matter controller", "does not answer on {0}", "Is the computer on? The house tries again",
                                    (s["computer"],), link="computers", cmd="sudo docker logs --tail 50 yashome-matter"))
        return rows
    info = s.get("info") or {}
    if s.get("others"):
        rows.append(core.health_row("matter", "warn", "Matter controller", "on {0}; also installed on {1}: the house uses one",
                                    "Remove the role from the other computers", (s["computer"], ", ".join(s["others"])), link="computers"))
    elif _thread_dataset() and not s.get("thread"):
        rows.append(core.health_row("matter", "warn", "Matter controller", "on {0}; it has no Thread network yet", "", (s["computer"],), link="matter"))
    elif not info.get("bluetooth_enabled"):
        rows.append(core.health_row("matter", "warn", "Matter controller", "on {0}, no Bluetooth: only codes shared from a phone app",
                                    "Bluetooth on that computer, then install the role again", (s["computer"],), link="computers"))
    else:
        rows.append(core.health_row("matter", "ok", "Matter controller", "on {0}", "", (s["computer"],)))
    for n in sorted(nodes, key=lambda n: n["node_id"]):
        if not n.get("available"):
            d = core.DEVICES.get(device_id(n["node_id"])) or {}
            rows.append(core.health_row(f"matter:{n['node_id']}", "warn", "Matter {0}", "not reachable",
                                        "Is it powered? A Thread device needs a border router near it", (d.get("name") or node_name(n),), link="matter"))
    return rows


core.HEALTH_CHECKS.append(health)
_load_cache()
