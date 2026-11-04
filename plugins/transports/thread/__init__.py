"""thread: the house's Thread network over its border routers: computers with the role "Thread border router" (plug-in
computers, helper/roles/thread-install.sh). Each router's REST (its 127.0.0.1:8081, no auth) is reached through the helper's
ssh tunnel thread<slot> (helper:8180+slot); its fixed ctl.sh through the helper (status, restart, leader weight, joiner).
The house keeps the dataset (plugin-state/thread/dataset.hex, 0600, never logged or answered) and gives it to every router;
the main router leads (weight 255) and commissions; stuck routers are restarted; nodes come from the main router's REST.
README.md has the whole design."""
import datetime
import ipaddress
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

from fastapi import HTTPException, Request

import app as core

PLUGIN = {"name": "Thread", "version": "1.0.0", "icon": "network"}

ROLE = "thread"
REST_HOST = "helper"               # the helper's tunnels: helper:<SLOT_PORT + slot> -> that router's 127.0.0.1:8081
SLOT_PORT = 8180
SLOTS = 8
CHECK_S = 60                       # every router's REST read this often
STATUS_S = 300                     # ctl.sh status (leader weight, TREL peers) this often; at once when the REST fails
NODES_S = 300                      # the node list asked anew this often
STUCK_CHECKS = 2                   # checks in a row a router is stuck before it is restarted
RESTART_GAP_S = 180                # at most one restart of a router in this time (a hanging radio comes back fast)
RESTART_SHOWN_S = 1800             # a restart stays on the health page this long
PAIR_S = 180
MAIN_WEIGHT, OTHER_WEIGHT = 255, 64
DEFAULTS = {"main": "", "slots": {}, "network": {"name": "Yashome", "channel": 15}, "names": {}, "told": {}}
DATASET = core.plugin_state("thread") / "dataset.hex"
PSKD_RE = re.compile(r"^[0-9A-HJ-NPR-Y]{6,32}$")      # Thread PSKd: upper case, no I O Q Z
# the joiner code every Yash Thread firmware has (yashGen): public, like Zigbee's well-known key; the house lets such a
# node in only while a pairing is open, and the node takes the house's pair request only while it blinks blue
PUBLIC_PSKD = "YASH4THREAD"
STALE_S = 900                      # a device the router has not renewed for this long has left the network
AUTO_PAIR_AFTER_S = 120            # the pair requests go on this long after the pairing window closed (a slow join)
EUI_RE = re.compile(r"^[0-9a-f]{16}$")
HEX_RE = re.compile(r"^[0-9a-f]{2,508}$")
JSONAPI = "application/vnd.api+json"

_lock = threading.RLock()
_cfg: dict = {}
_routers: dict = {}                # computer id -> {name, host, user, slot, main, rest, rcp, node, status, net, bad, why, restarted, error, ts}
_nodes = {"list": [], "ts": None, "asked": 0.0}
_pair: dict = {}                   # the pairing that is open or ended last: {state, eui, until, task, slot, known, router}


def _load_cfg() -> None:
    raw = core.cfg_section("thread", {})
    c = json.loads(json.dumps(DEFAULTS))
    if isinstance(raw, dict):
        if isinstance(raw.get("main"), str):
            c["main"] = raw["main"]
        if isinstance(raw.get("slots"), dict):
            c["slots"] = {str(k): int(v) for k, v in raw["slots"].items() if isinstance(v, int) and 1 <= v <= SLOTS}
        net = raw.get("network") if isinstance(raw.get("network"), dict) else {}
        if isinstance(net.get("name"), str) and 1 <= len(net["name"]) <= 16:
            c["network"]["name"] = net["name"]
        if isinstance(net.get("channel"), int) and 11 <= net["channel"] <= 26:
            c["network"]["channel"] = net["channel"]
        if isinstance(raw.get("names"), dict):
            c["names"] = {str(k): str(v) for k, v in raw["names"].items()}
        if isinstance(raw.get("told"), dict):
            c["told"] = {str(k): str(v) for k, v in list(raw["told"].items())[-64:]}
    _cfg.clear()
    _cfg.update(c)


_load_cfg()
core.SETTINGS_SECTIONS["thread"] = lambda: _cfg


# ---- the dataset: the house's own copy, never logged, never answered ----
def _dataset() -> str:
    try:
        v = DATASET.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeDecodeError):
        return ""
    return v if HEX_RE.match(v) and len(v) % 2 == 0 else ""


def _store_dataset(hexstr: str) -> None:
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATASET.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(hexstr.strip().lower() + "\n")
    os.replace(tmp, DATASET)


def _tlvs(hexstr: str) -> dict:
    """The dataset's TLVs: {type: bytes}; a broken one gives what was read before it."""
    out, b, i = {}, bytes.fromhex(hexstr or ""), 0
    while i + 2 <= len(b):
        t, n = b[i], b[i + 1]
        out[t] = b[i + 2:i + 2 + n]
        i += 2 + n
    return out


def _same_network(a: str, b: str) -> bool:
    """One network: the same extended PAN id (TLV 2) and network key (TLV 5); a newer timestamp is still the same."""
    ta, tb = _tlvs(a), _tlvs(b)
    return bool(ta.get(2)) and ta.get(2) == tb.get(2) and ta.get(5) == tb.get(5)


def network_info() -> dict:
    """What may be shown of the house's network: its name, channel and extended PAN id (no key)."""
    t = _tlvs(_dataset())
    ch = t.get(0) or b""
    return {"kept": bool(t), "name": (t.get(3) or b"").decode("utf-8", "replace") or None,
            "channel": int.from_bytes(ch[1:3], "big") if len(ch) == 3 else None, "extpanid": (t.get(2) or b"").hex() or None}


# ---- a router's REST through the helper's tunnel ----
def _base(slot: int) -> str:
    return f"http://{REST_HOST}:{SLOT_PORT + slot}"


def _rest(slot: int, method: str, path: str, body=None, ctype="application/json", accept="application/json", timeout=8):
    """(status, text) of one REST call; (0, why) when it does not answer."""
    data = None if body is None else (body if ctype == "text/plain" else json.dumps(body)).encode()
    req = urllib.request.Request(_base(slot) + path, data=data, method=method,
                                 headers={"Accept": accept, **({"Content-Type": ctype} if data is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (OSError, ValueError) as e:
        return 0, str(e)[:120]


def _json(slot: int, path: str, accept="application/json"):
    code, text = _rest(slot, "GET", path, accept=accept)
    if code != 200:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _ctl(r: dict, cmd: str, timeout=30, **kw) -> dict:
    """ctl.sh on that router through the helper: {rc, out: {key: value}} (an exception when the helper refuses)."""
    return core.helper("POST", "/v1/hosts/thread", {"host": r["host"], "user": r["user"], "cmd": cmd, **kw}, timeout)


def _computers():
    return sys.modules.get("yashome_plugins.computers")


def _list() -> list:
    """The computers with the role (the computers plug-in's last check), each with its tunnel slot (kept in the settings)."""
    pcs = _computers()
    have = pcs.with_role(ROLE) if pcs else []
    with _lock:
        slots, changed = _cfg["slots"], False
        for c in have:
            if c["id"] not in slots:
                free = [n for n in range(1, SLOTS + 1) if n not in slots.values()]
                if not free:
                    continue
                slots[c["id"]], changed = free[0], True
        for cid in [k for k in slots if k not in {c["id"] for c in have}]:
            slots.pop(cid)
            changed = True
        main = _cfg["main"] if any(c["id"] == _cfg["main"] for c in have) else (have[0]["id"] if have else "")
    if changed:
        core.save_settings()
    return [{**c, "slot": slots[c["id"]], "main": c["id"] == main} for c in have if c["id"] in slots]


def _sync_tunnels(routers: list) -> None:
    """The helper's tunnel thread<slot> goes to each router; a slot no router has loses its tunnel."""
    cur = core.helper("GET", "/v1/tunnels", None, 5).get("tunnels") or {}
    want = {f"thread{r['slot']}": r for r in routers}
    for role, r in want.items():
        if (cur.get(role) or {}).get("host") != r["host"]:
            core.helper("POST", "/v1/tunnels", {"role": role, "host": r["host"], "user": r["user"], "wsl": ""}, 15)
    for role in cur:
        if re.fullmatch(r"thread[0-9]+", role) and role not in want:
            core.helper("POST", "/v1/tunnels", {"role": role}, 15)


# ---- one check of every router ----
def _network(r: dict, st: dict) -> None:
    """The house's dataset on this router: kept / taken from the main router / formed there / given to it. st["net"] says how."""
    slot, house = r["slot"], _dataset()
    code, have = _rest(slot, "GET", "/node/dataset/active", accept="text/plain")
    have = have.strip().lower() if code == 200 and HEX_RE.match(have.strip().lower()) else ""
    if not house:
        if not r["main"]:
            st["net"] = "waits for the main router"
            return
        if have:
            _store_dataset(have)
            st["net"] = "house"
            core.log.info(f"[thread] the house keeps the network {r['name']} has")
            return
        net = _cfg["network"]
        c, _ = _rest(slot, "PUT", "/node/dataset/active", {"NetworkName": net["name"], "Channel": net["channel"]})
        if c not in (200, 201):
            st["net"], st["error"] = "none", f"forming the network failed ({c})"
            return
        _rest(slot, "PUT", "/node/state", "enable")
        c, made = _rest(slot, "GET", "/node/dataset/active", accept="text/plain")
        if c == 200 and HEX_RE.match(made.strip().lower()):
            _store_dataset(made)
            st["net"] = "house"
            core.log.info(f"[thread] network {net['name']} formed on {r['name']}")
        return
    if not have:
        c, _ = _rest(slot, "PUT", "/node/dataset/active", house, ctype="text/plain")
        if c not in (200, 201):
            st["net"], st["error"] = "none", f"giving the network failed ({c})"
            return
        _rest(slot, "PUT", "/node/state", "enable")
        st["net"] = "house"
        core.log.info(f"[thread] {r['name']} got the house's network")
        return
    st["net"] = "house" if _same_network(have, house) else "other"
    if st["net"] == "house" and (st.get("node") or {}).get("state") == "disabled":
        _rest(slot, "PUT", "/node/state", "enable")


def _status(r: dict, st: dict, old: dict, now: float, force: bool) -> None:
    if not force and now - (old.get("status_ts") or 0) < STATUS_S:
        st["status"], st["status_ts"] = old.get("status") or {}, old.get("status_ts")
        return
    try:
        res = _ctl(r, "status")
        st["status"] = res.get("out") or {} if res.get("rc") == 0 else {}
    except HTTPException as e:
        st["status"], st["error"] = {}, f"the computer: {e.detail}"
    st["status_ts"] = now
    if st["status"].get("routes_fixed") not in (None, "", "0"):
        core.log.warning(f"[thread] {r['name']}: {st['status']['routes_fixed']} RA route setting(s) on the LAN put right (a loop between the border routers)")


def _leader_weight(r: dict, st: dict) -> None:
    """The main router leads (255), the others do not compete (64); ot-ctl does not keep it over a restart: set at every check."""
    s = st.get("status") or {}
    want = MAIN_WEIGHT if r["main"] else OTHER_WEIGHT
    if s.get("state") in ("leader", "router", "child") and s.get("leaderweight") not in (None, "", str(want)):
        try:
            if _ctl(r, "leaderweight", weight=want).get("rc") == 0:
                core.log.info(f"[thread] {r['name']}: leader weight {s.get('leaderweight')} -> {want}")
                s["leaderweight"] = str(want)
        except HTTPException as e:
            st["error"] = f"leader weight: {e.detail}"


def _stuck(st: dict) -> str:
    """Why this router needs a restart ('' = it does not): its agent does not answer while its container runs, no radio,
    or out of the network while it has the house's dataset."""
    s = st.get("status") or {}
    if not st["rest"]:
        return "its border router does not answer" if s.get("running") in ("true", "false") else ""
    if not st["rcp"]:
        return "no radio (RCP)"
    if st.get("net") == "house" and (st.get("node") or {}).get("state") in ("disabled", "detached"):
        return "not in the network"
    return ""


def _check_router(r: dict, now: float) -> dict:
    with _lock:
        old = dict(_routers.get(r["id"]) or {})
    st = {"id": r["id"], "name": r["name"], "host": r["host"], "user": r["user"], "slot": r["slot"], "main": r["main"], "ts": now, "error": None,
          "restarted": old.get("restarted"), "restart_why": old.get("restart_why"), "net": old.get("net")}
    node = _json(r["slot"], "/node")
    st["rest"] = isinstance(node, dict)
    st["node"] = {k: node.get(k) for k in ("state", "extAddress", "extPanId", "networkName", "rloc16", "leaderData")} if st["rest"] else {}
    code, ver = _rest(r["slot"], "GET", "/node/coprocessor/version") if st["rest"] else (0, "")
    st["rcp"] = code == 200 and ver.strip().strip('"').strip() != ""
    # status is read every STATUS_S, at once when the REST fails or its state is not the one status saw (e.g. still
    # "disabled" from before the router got the network): the leader weight and the health rows go by it
    seen = (old.get("status") or {}).get("state")
    _status(r, st, old, now, force=not st["rest"] or bool(st["node"].get("state")) and seen != st["node"].get("state"))
    if st["rest"] and st["rcp"]:
        try:
            _network(r, st)
        except OSError as e:
            st["error"] = f"the dataset file: {e.strerror}"
    _leader_weight(r, st)
    why = _stuck(st)
    st["bad"] = (old.get("bad") or 0) + 1 if why else 0
    st["why"] = why
    if why and st["bad"] >= STUCK_CHECKS and now - (old.get("restarted") or 0) > RESTART_GAP_S:
        try:
            res = _ctl(r, "restart", timeout=150)
            if res.get("rc") == 0:
                st.update(restarted=now, restart_why=why, bad=0)
                core.log.warning(f"[thread] {r['name']} restarted: {why}")
            else:
                st["error"] = "the restart did not work"
        except HTTPException as e:
            st["error"] = f"restart: {e.detail}"
    return st


def tick() -> None:
    """One check: the routers' tunnels, each router, the node list."""
    routers = _list()
    try:
        _sync_tunnels(routers)
    except HTTPException as e:
        core.log.warning(f"[thread] tunnels: {e.detail}")
    now = time.time()
    done = {}
    for r in sorted(routers, key=lambda r: not r["main"]):      # the main router first: it has the network the others get
        done[r["id"]] = _check_router(r, now)
    with _lock:
        _routers.clear()
        _routers.update(done)
    main = next((r for r in routers if r["main"]), None)
    if main and done[main["id"]]["rest"]:
        _read_nodes({**main, "node": done[main["id"]]["node"]}, now)
        _pair_follow(main)


def _loop() -> None:
    time.sleep(20)
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001 - the helper or a router may be down: next time
            core.log.warning(f"[thread] check failed: {getattr(e, 'detail', e)}")
        time.sleep(CHECK_S)


core.STARTUP_TASKS.append(_loop)


# ---- nodes: the main router's device collection, the routers' diagnostics, the SRP host names of every router ----
ROLES = ("leader", "router", "child", "detached", "disabled")


def _lq(margin) -> int | None:
    """Link quality 0-3 from a link margin (dB), as OpenThread counts it."""
    if not isinstance(margin, (int, float)):
        return None
    return 3 if margin > 20 else 2 if margin > 10 else 1 if margin > 2 else 0


def _items(doc) -> list:
    data = doc.get("data") if isinstance(doc, dict) else doc
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _ip(v) -> str:
    try:
        return ipaddress.IPv6Address(str(v or "")).compressed
    except ValueError:
        return ""


def _rloc(v) -> int | None:
    try:
        return int(str(v), 0)
    except (TypeError, ValueError):
        return None


def _match(name: str, eui: str, ext: str) -> str | None:
    """The house device a node is: the owner's pick (by EUI-64 or extended address), else an ESPHome node of that host name
    (its id, esphome_node, or the first or second level of its topics: home/thread-node-a/...)."""
    pick = _cfg["names"].get(eui) or _cfg["names"].get(ext)
    if pick:
        return pick
    if not name:
        return None
    keys = {name, name.replace("-", "_"), name.replace("_", "-")}
    for d in list(core.DEVICES.values()):
        if d.get("transport", "mqtt") != "mqtt":
            continue
        if d.get("id") in keys or d.get("esphome_node") in keys:
            return d["id"]
        topics = [str(d.get("availability_topic") or "")] + [str(c.get(k) or "") for c in d.get("controls") or [] for k in ("state_topic", "command_topic")]
        if any(keys & set(t.split("/")[:2]) for t in topics if "/" in t):
            return d["id"]
    return None


def _srp_names() -> dict:
    """{IPv6 address: host name}: the SRP hosts of every border router (a node registers with any one of them)."""
    with _lock:
        lists = [(r.get("status") or {}).get("srp") or [] for r in _routers.values()]
    names: dict = {}
    for x in (x for lst in lists for x in lst):
        host, _, addr = str(x).partition(" ")
        if host and _ip(addr) and host not in names.setdefault(_ip(addr), []):
            names[_ip(addr)].append(host)
    # an address with several names: a re-flashed node's old lease stays until it expires (node-a beside
    # node-a-0a0b0c); the name of a house device wins, else the first one seen
    out = {a: next((h for h in hs if _match(h, "", "")), hs[0]) for a, hs in names.items()}
    with _lock:                       # a node's own word at pairing, where its SRP name is still held by an old key
        told = dict(_cfg.get("told") or {})
    return {**told, **{a: h for a, h in out.items() if a not in told or _match(h, "", "")}}


def _router_names() -> dict:
    """{extended address: computer name} of the border routers."""
    with _lock:
        return {str(r["node"]["extAddress"]).lower(): r["name"] for r in _routers.values() if (r.get("node") or {}).get("extAddress")}


def _epoch(stamp) -> float:
    """An ISO time of the router's REST ("2026-11-04T07:52:28+00:00") as epoch seconds, 0 when there is none."""
    try:
        return datetime.datetime.fromisoformat(str(stamp)).timestamp() if stamp else 0.0
    except ValueError:
        return 0.0


def nodes_from(devices_doc, diags_doc, srp=None, brs=None) -> list:
    """[{ext, eui, name, role, border_router, parent, lq, rssi, links, router, device}] from /api/devices, /api/diagnostics,
    the SRP hosts ({address: name}) and the border routers ({ext: name}). links: [{node, rssi, lq}] of a router, from its own
    diagnostics or (the main router: no diagnostics of itself) from its neighbours'; lq and rssi: its best link."""
    srp, brs = srp or {}, brs or {}
    # the router keeps a device that left (a node that forgot its network rejoins with a new extended address) until it
    # expires; every device collection renews the "updated" stamp of those present: older than STALE_S behind the newest = gone
    stamps = {str((x.get("attributes") or {}).get("extAddress") or "").lower(): str((x.get("attributes") or {}).get("updated") or "")
              for x in _items(devices_doc)}
    newest = max((_epoch(s) for s in stamps.values() if _epoch(s)), default=0)
    devs = {}
    for x in _items(devices_doc):
        a = x.get("attributes") or {}
        ext = str(a.get("extAddress") or x.get("id") or "").lower()
        if not ext:
            continue
        if x.get("type") != "threadBorderRouter" and ext not in brs and _epoch(stamps.get(ext)) and newest - _epoch(stamps.get(ext)) > STALE_S:
            continue
        role = a.get("state") if x.get("type") == "threadBorderRouter" and a.get("state") in ROLES else a.get("role")
        host = str(a.get("hostname") or "").split(".")[0] or srp.get(_ip(a.get("omrIpv6Address"))) or brs.get(ext)
        devs[ext] = {"ext": ext, "eui": str(a.get("eui") or "").lower() or None, "name": host or None, "role": role or None,
                     "border_router": x.get("type") == "threadBorderRouter" or ext in brs, "rloc16": _rloc(a.get("rloc16")) or None,
                     "parent": None, "lq": None, "rssi": None, "links": [], "omr": _ip(a.get("omrIpv6Address")) or None}
    seen, mine = {}, set()                 # seen: ext -> the links others report to it
    for x in _items(diags_doc):
        a = x.get("attributes") or {}
        ext, rloc = str(a.get("extAddress") or "").lower(), _rloc(a.get("rloc16"))
        me = devs.get(ext)
        if me is None or rloc is None:
            continue
        me["rloc16"] = rloc
        for nb in a.get("routerNeighbors") or []:
            other = str(nb.get("extAddress") or "").lower()
            if other not in devs or not isinstance(nb.get("averageRssi"), (int, float)):
                continue
            link = {"rssi": nb["averageRssi"], "lq": _lq(nb.get("linkMargin"))}
            me["links"].append({"node": other, **link})
            seen.setdefault(other, []).append({"node": ext, **link})
            mine.add(ext)
    by_rloc = {d["rloc16"]: d for d in devs.values() if d["rloc16"] is not None}
    for x in _items(diags_doc):
        a = x.get("attributes") or {}
        me, rloc = devs.get(str(a.get("extAddress") or "").lower()), _rloc(a.get("rloc16"))
        if me is None or rloc is None:
            continue
        for ch in a.get("childTable") or []:
            child = by_rloc.get((rloc & 0xFC00) | int(ch.get("childId") or 0))
            if child is not None and child is not me:
                child.update(parent=me["ext"], role=child["role"] or "child")
                child["lq"] = ch.get("linkQuality") if isinstance(ch.get("linkQuality"), int) else child["lq"]
    name = {e: d["name"] or e for e, d in devs.items()}
    out = []
    for e, d in devs.items():
        if e not in mine:
            d["links"] = seen.get(e, [])
        d["links"] = sorted(({**k, "node": name[k["node"]]} for k in d["links"]), key=lambda k: -k["rssi"])
        if d["links"]:
            d.update(rssi=d["links"][0]["rssi"], lq=d["links"][0]["lq"])
        d["parent"] = name.get(d["parent"]) if d["parent"] else None
        d["router"] = d["role"] in ("router", "leader")
        d["device"] = _match(d["name"] or "", d["eui"] or "", d["ext"])
        d.pop("rloc16", None)
        out.append(d)
    return sorted(out, key=lambda d: (not d["border_router"], not d["router"], d["name"] or d["ext"]))


def _ask_devices(slot: int) -> None:
    """A new device collection on the main router, on its own: in one list with network diagnostics it never ran (a
    diagnostic of the router itself fails and the tasks after it time out). The old tasks go, not while a pairing runs."""
    if _pair.get("state") != "open":
        _rest(slot, "DELETE", "/api/actions", accept=JSONAPI)
    _rest(slot, "DELETE", "/api/diagnostics", accept=JSONAPI)
    _rest(slot, "POST", "/api/actions", {"data": [{"type": "updateDeviceCollectionTask",
                                                   "attributes": {"maxAge": 30, "maxRetries": 3, "deviceCount": 64, "timeout": 60}}]},
          ctype=JSONAPI, accept=JSONAPI)


def _ask_diagnostics(slot: int, devs: list, main_ext: str) -> None:
    """The neighbours of each router but the main one (its own diagnostics fail); one task runs at a time and every
    timeout counts from now, so each next one gets more."""
    want = [d["ext"] for d in devs if (d["router"] or d["border_router"]) and d["ext"] != main_ext][:16]
    tasks = [{"type": "getNetworkDiagnosticTask", "attributes": {"destination": ext, "types": ["extAddress", "rloc16", "childTable", "routerNeighbors"],
                                                                 "timeout": 30 + 15 * i}} for i, ext in enumerate(want)]
    if tasks:
        _rest(slot, "POST", "/api/actions", {"data": tasks}, ctype=JSONAPI, accept=JSONAPI)


def _read_nodes(main: dict, now: float) -> None:
    """Every check: the node list from what the main router has; every NODES_S a new device collection, the check after
    it the diagnostics of the routers it found."""
    slot = main["slot"]
    devs = nodes_from(_json(slot, "/api/devices", JSONAPI), _json(slot, "/api/diagnostics", JSONAPI), _srp_names(), _router_names())
    with _lock:
        if devs:
            _nodes.update(list=devs, ts=now)
        stage = _nodes.get("stage")
        due = now - _nodes["asked"] >= NODES_S
        _nodes["stage"] = "devices" if due else "diagnostics" if stage == "devices" else stage
        if due:
            _nodes["asked"] = now
    if due:
        _ask_devices(slot)
    elif stage == "devices":
        _ask_diagnostics(slot, devs, str((main.get("node") or {}).get("extAddress") or "").lower())


def marks() -> dict:
    """{device id: "router"}: the house devices that are Thread routers (the map marks them)."""
    with _lock:
        return {d["device"]: "router" for d in _nodes["list"] if d["device"] and d["router"] and not d["border_router"]}


# ---- pairing: the commissioner on the main router ----
def dataset() -> str:
    """The house's Thread dataset (hex TLVs) for a plug-in that commissions onto it (matter); never log or answer it."""
    return _dataset() or ""


def role_active(computer: dict) -> bool:
    """For the plug-in computers: this computer's border router answers and is on the house's network."""
    with _lock:
        r = _routers.get(computer.get("id")) or next((x for x in _routers.values() if computer.get("host") and x.get("host") == computer["host"]), None)
        return bool(r and r.get("rest") and (r.get("node") or {}).get("state") in ("leader", "router", "child"))


def _main() -> dict | None:
    with _lock:
        return next((dict(r) for r in _routers.values() if r["main"]), None)


def _pair_follow(main: dict) -> None:
    """The open pairing's state: the task's status (with an EUI-64), else a node that was not there before."""
    with _lock:
        p = dict(_pair)
    if p.get("state") != "open":
        return
    now = time.time()
    if p.get("task"):
        doc = _json(p["slot"], "/api/actions/" + p["task"], JSONAPI)
        a = ((doc or {}).get("data") or {}).get("attributes") if isinstance((doc or {}).get("data"), dict) else {}
        s = (a or {}).get("status")
        state = {"completed": "joined", "stopped": "timeout", "failed": "failed"}.get(s)
    else:
        known = set(p.get("known") or [])
        new = [d for d in _nodes["list"] if d["ext"] not in known]
        state = "joined" if new else None
        if new:
            p["node"] = new[0]["name"] or new[0]["ext"]
        if not new and now - (p.get("asked") or 0) > 20:     # the device collection asked again while it is open
            _rest(p["slot"], "POST", "/api/actions", {"data": [{"type": "updateDeviceCollectionTask",
                                                                  "attributes": {"maxAge": 10, "maxRetries": 2, "deviceCount": 64, "timeout": 20}}]},
                  ctype=JSONAPI, accept=JSONAPI)
            p["asked"] = now
    if state is None and now > p["until"] + 15:
        state = "timeout"
    with _lock:
        if _pair.get("started") == p.get("started"):
            _pair.update({k: v for k, v in p.items() if k in ("asked", "node")})
            if state:
                _pair["state"] = state
                core.log.info(f"[thread] pairing on {p['router']}: {state}")


class PairBody(core.Body):
    pskd: str = ""                 # empty: the public code of the Yash firmware (one button, no code to type)
    eui: str = ""


def _srp_hosts() -> dict:
    """{host name: IPv6 address} the routers' SRP servers have now (ctl.sh status of each router that answers)."""
    out = {}
    with _lock:
        rs = [dict(r) for r in _routers.values() if r.get("rest")]
    for r in rs:
        try:
            res = _ctl(r, "status")
        except Exception:                                    # noqa: BLE001 - a router that does not answer adds none
            continue
        for line in (res.get("out") or {}).get("srp") or []:
            h, _, a = line.partition(" ")
            out.setdefault(h.lower(), a)
    return out


def _pair_one(main: dict, name: str, addr: str, pm, broker: str) -> dict | None:
    """The house's pair request to one node over Thread (plug-in pair: its account, OTA password, card); None when the node
    does not answer it at all (an older or a foreign firmware), else {node, ok, error?}."""
    def deliver(rec):
        try:
            res = _ctl(main, "pair", timeout=50, addr=addr, form=pm.thread_form(rec, broker, core.node_broker_port()))
        except Exception as e:                               # noqa: BLE001 - the helper refused or timed out
            raise RuntimeError(str(getattr(e, "detail", e))) from None
        out = res.get("out") or {}
        rep = out.get("pair_reply") or {}
        if not out.get("pair_status"):
            raise RuntimeError(rep.get("error") or "the node did not answer")
        return {"ok": out["pair_status"] == 200 and rep.get("ok") is True, "reply": rep, "error": rep.get("error") or f"HTTP {out['pair_status']}"}
    try:
        r = pm.pair_with(name, deliver, via="thread")
    except HTTPException as e:
        if e.status_code == 502:
            return None
        core.log.warning(f"[thread] pairing {name}: {e.detail}")
        return {"node": name, "ok": False, "error": str(e.detail)[:160]}
    core.log.info(f"[thread] {r['node']} paired over Thread")
    return {"node": r["node"], "ok": True}


def _remember(addr: str, name: str) -> None:
    """The name a node told at its address (the Nodes list shows it; kept for the last 64 addresses)."""
    with _lock:
        told = dict(_cfg.get("told") or {})
        told.pop(addr, None)
        told[addr] = name
        _cfg["told"] = dict(list(told.items())[-64:])
    core.save_settings()


def _probe(main: dict, addr: str) -> tuple:
    """(HTTP status, node name) of an empty pair request to that address (0: no answer); a Yash node waiting for the
    house answers 400 with its name, a paired one 409."""
    try:
        out = (_ctl(main, "pair", timeout=50, addr=addr, form={}) or {}).get("out") or {}
    except Exception:                                        # noqa: BLE001 - the helper refused or timed out
        return 0, ""
    return int(out.get("pair_status") or 0), str((out.get("pair_reply") or {}).get("node") or "").lower()


def _candidates() -> dict:
    """{address: SRP name or ""}: every SRP host of the routers and every node's OMR address from the device collection
    (a node that forgot its network registers with a new SRP key; its old name stays held by the old one)."""
    out = {a: n for n, a in _srp_hosts().items()}
    with _lock:
        for d in _nodes["list"]:
            if d.get("omr") and not d.get("border_router"):
                out.setdefault(d["omr"], "")
    return out


def _auto_pair(started: float) -> None:
    """While a pairing is open (and AUTO_PAIR_AFTER_S after): a Yash node that joined is on Thread but not on the broker;
    each SRP host that is not online and not a name the house has gets the pair request once."""
    pm, broker = sys.modules.get("yashome_plugins.pair"), (os.environ.get("BIND_ADDRESS") or "").strip()
    if pm is None or not broker or broker in ("0.0.0.0", "::"):
        core.log.warning("[thread] no automatic pairing: the plug-in pair or BIND_ADDRESS is missing")
        return
    tried = set()
    while True:
        with _lock:
            p = dict(_pair)
        if p.get("started") != started or time.time() > p.get("until", 0) + AUTO_PAIR_AFTER_S:
            return
        main = _main()
        for addr, srp_name in (_candidates().items() if main and main["rest"] else []):
            if addr in tried or (srp_name and core.retained_values([f"{srp_name}/status"], timeout=2.0).get(f"{srp_name}/status") == "online"):
                continue
            # an empty request first: a Yash node waiting for the house answers 400 and its name (no record is touched);
            # nothing answers yet (still joining, an old lease, an older firmware): asked again in the next round
            st, name = _probe(main, addr)
            if st == 0:
                continue
            if name and pm.NODE_RE.fullmatch(name):
                _remember(addr, name)
            tried.add(addr)
            if st != 400 or not pm.NODE_RE.fullmatch(name) or pm._taken(name):   # 409: paired already; else not ours
                continue
            res = _pair_one(main, name, addr, pm, broker)
            if res:
                with _lock:
                    if _pair.get("started") == started:
                        _pair.setdefault("paired", []).append(res)
        time.sleep(8)


def _pair_view() -> dict:
    with _lock:
        p = dict(_pair)
    return {"state": p.get("state") or "idle", "eui": p.get("eui"), "router": p.get("router"), "node": p.get("node"),
            "left": max(0, int(p["until"] - time.time())) if p.get("state") == "open" else 0, "paired": list(p.get("paired") or [])}


@core.app.post("/api/thread/pair")
def api_pair(body: PairBody, request: Request):
    """The commissioner on the main router lets one device in for 3 minutes: by its EUI-64 (followed to joined / timeout)
    or any device with that PSKd. The PSKd is not kept, logged or answered."""
    core.house_token_only(request)
    pskd, eui = body.pskd.strip().upper() or PUBLIC_PSKD, body.eui.strip().lower().replace(":", "").replace("-", "")
    if not PSKD_RE.match(pskd):
        raise HTTPException(400, "PSKd: 6-32 characters, capital letters and digits, no I, O, Q or Z")
    if eui and not EUI_RE.match(eui):
        raise HTTPException(400, "EUI-64: 16 hexadecimal digits")
    main = _main()
    if not main or not main["rest"]:
        raise HTTPException(503, "the main Thread border router does not answer")
    if not _dataset():
        raise HTTPException(409, "the house has no Thread network yet")
    with _lock:
        if _pair.get("state") == "open" and time.time() < _pair["until"]:
            raise HTTPException(409, "a pairing is open already")
    p = {"state": "open", "eui": eui or None, "router": main["name"], "slot": main["slot"], "started": time.time(),
         "until": time.time() + PAIR_S, "task": None, "known": [d["ext"] for d in _nodes["list"]], "node": None}
    if eui:
        code, text = _rest(main["slot"], "POST", "/api/actions", {"data": [{"type": "addThreadDeviceTask",
                                                                           "attributes": {"eui": eui, "pskd": pskd, "timeout": PAIR_S}}]},
                           ctype=JSONAPI, accept=JSONAPI)
        try:
            data = json.loads(text).get("data") if code in (200, 201, 202) else None
            p["task"] = str((data[0] if isinstance(data, list) else data)["id"])
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise HTTPException(502, f"the border router did not take the device ({code})")
    else:
        res = _ctl(main, "joiner", pskd=pskd, eui="*")
        if res.get("rc") != 0:
            raise HTTPException(502, "the border router did not open the commissioner")
    with _lock:
        _pair.clear()
        _pair.update(p)
    core.log.info(f"[thread] pairing open on {main['name']} for {PAIR_S} s" + (f" (EUI-64 {eui})" if eui else ""))
    threading.Thread(target=_auto_pair, args=(p["started"],), daemon=True, name="thread-pair").start()
    return _pair_view()


@core.app.get("/api/thread/pair")
def api_pair_status(request: Request):
    core.house_token_only(request)
    main = _main()
    if main and main["rest"]:
        _pair_follow(main)
    return _pair_view()


# ---- the Settings page ----
def _view() -> dict:
    with _lock:
        routers = sorted((dict(r) for r in _routers.values()), key=lambda r: (not r["main"], r["name"].lower()))
        nodes = [dict(n) for n in _nodes["list"]]
    for r in routers:
        for k in ("slot", "user"):
            r.pop(k, None)
    return {"network": {**network_info(), "want": dict(_cfg["network"])}, "routers": routers, "nodes": nodes, "nodes_ts": _nodes["ts"],
            "main": next((r["id"] for r in routers if r["main"]), None), "pair": _pair_view(), "marks": marks()}


@core.app.get("/api/thread")
def api_view(request: Request):
    core.house_token_only(request)
    return _view()


@core.app.get("/api/thread/marks")
def api_marks(request: Request):
    """{device id: "router"} for the map (anyone who sees the map)."""
    return marks()


class SettingsBody(core.Body):
    main: str | None = None
    name: str | None = None
    channel: int | None = None


@core.app.patch("/api/thread")
def api_settings(body: SettingsBody, request: Request):
    """The main router (it leads and commissions); the name and channel a new network gets (an existing one keeps its own)."""
    core.house_token_only(request)
    with _lock:
        if body.main is not None:
            if body.main and body.main not in {c["id"] for c in (_computers().with_role(ROLE) if _computers() else [])}:
                raise HTTPException(400, "not a computer with a Thread border router")
            _cfg["main"] = body.main
        if body.name is not None:
            if not 1 <= len(body.name.strip()) <= 16:
                raise HTTPException(400, "a network name of 1-16 characters")
            _cfg["network"]["name"] = body.name.strip()
        if body.channel is not None:
            if not 11 <= body.channel <= 26:
                raise HTTPException(400, "a channel 11-26")
            _cfg["network"]["channel"] = body.channel
    core.save_settings()
    threading.Thread(target=tick, daemon=True).start()
    return _view()


class NameBody(core.Body):
    node: str                      # its EUI-64 or extended address
    device: str = ""               # the house device it is ("" = by its host name again)


@core.app.post("/api/thread/node")
def api_node(body: NameBody, request: Request):
    """Which house device a node is, picked by hand."""
    core.house_token_only(request)
    key = body.node.strip().lower()
    if not EUI_RE.match(key):
        raise HTTPException(400, "node: its EUI-64 or extended address")
    if body.device and body.device not in core.DEVICES:
        raise HTTPException(404, "no such device")
    with _lock:
        if body.device:
            _cfg["names"][key] = body.device
        else:
            _cfg["names"].pop(key, None)
        for n in _nodes["list"]:
            n["device"] = _match(n["name"] or "", n["eui"] or "", n["ext"])
    core.save_settings()
    return _view()


def _on_rename(old: str, new: str) -> None:
    with _lock:
        for k, v in list(_cfg["names"].items()):
            if v == old:
                _cfg["names"][k] = new


core.ON_RENAME.append(_on_rename)


# ---- health ----
def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _health() -> list:
    with _lock:
        routers = [dict(r) for r in _routers.values()]
    if not routers:
        pcs = _computers()
        if pcs and pcs.with_role(ROLE):
            return []                  # installed, not checked yet
        return [core.health_row("thread", "warn", "Thread", "no border router",
                                "Install the role Thread border router on a computer with a Thread radio", link="computers")]
    rows, now, many = [], time.time(), len(routers) > 1
    for r in sorted(routers, key=lambda r: (not r["main"], r["name"])):
        rid, title = f"thread:{r['id']}", "Thread border router {0}"
        s = r.get("status") or {}
        if not r["rest"]:
            rows.append(core.health_row(rid, "bad", title, "its border router does not answer", "The house restarts it; still so: is the computer on?",
                                        (r["name"],), link="computers", cmd="sudo journalctl -t otbr-agent -n 50"))
        elif not r["rcp"]:
            rows.append(core.health_row(rid, "bad", title, "no radio (RCP)", "Is the radio plugged in? The house restarts the border router",
                                        (r["name"],), link="computers"))
        elif r.get("net") == "other":
            rows.append(core.health_row(rid, "bad", title, "on another Thread network", "Remove the role from the computer and install it again",
                                        (r["name"],), link="computers"))
        elif r.get("why"):
            rows.append(core.health_row(rid, "warn", title, r["why"], "", (r["name"],), link="thread"))
        elif many and s.get("trel_peers") == "0":
            rows.append(core.health_row(rid, "warn", title, "no other border router seen over the LAN (TREL)",
                                        "Both on the same LAN, UDP and mDNS let through the firewall", (r["name"],), link="thread"))
        elif r.get("restarted") and now - r["restarted"] < RESTART_SHOWN_S:
            rows.append(core.health_row(rid, "warn", title, "restarted by the house at {1}: {2}", "", (r["name"], _clock(r["restarted"]), r.get("restart_why") or ""),
                                        link="thread"))
        else:
            rows.append(core.health_row(rid, "ok", title, "", "", (r["name"],)))
    main = next((r for r in routers if r["main"]), None)
    leader = next((r for r in routers if (r.get("node") or {}).get("state") == "leader"), None)
    if main and main["rest"]:
        if leader is main:
            rows.append(core.health_row("thread:leader", "ok", "Thread leader", "the main border router {0}", "", (main["name"],)))
        else:
            rows.append(core.health_row("thread:leader", "warn", "Thread leader", "{0} leads, not the main border router {1}" if leader else
                                        "the main border router {1} does not lead", "The house sets its leader weight; it takes over in minutes",
                                        (leader["name"] if leader else "", main["name"]), link="thread"))
    return rows


core.HEALTH_CHECKS.append(_health)
