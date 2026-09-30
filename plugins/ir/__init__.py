"""Learned IR/RF remotes. The codes live here, never on a blaster: an ESP node is a dumb pipe
(<node>/tx/{ir,rf}_raw), so a reflashed or swapped board loses nothing.

A REMOTE is a set of codes [{id, name, learned_on, buttons: [{id, name, group?, proto, code, n}]}]
(plugin_data("ir")/ir_remotes.json, grouped by function on disk). An APPLIANCE pairs a remote
with the blaster in that room ({id, name, remote, blaster, device?}; settings "ir_appliances");
it becomes a device of the house with one write-only channel (kind ir_press) per button, so
rules, schedules, favourites, panels and voice reach a learned button as (device, code). The
blasters are the settings section "ir_blasters" ([{node, name, rx}], rx = can learn).

Learning is one session at a time: arm <node>/learn/command ON, wait for <node>/rx/{ir,rf}_raw,
store it. Native-remote state (the AC remote in the living room) is decoded by the blaster's
firmware and published as that AC's own state topics; nothing here takes part in it.

An IR TOGGLE is a device whose power key flips it (a media box): settings "ir_toggles"
[{id, name, category?, model?, power: {remote, button, via}, probe: {host, port, keep_on?}}].
A TCP probe gives its state; "power" sends the frame only when the box is in the other state;
keep_on presses it after a minute of silence, at most once per 5 minutes."""
import json
import socket
import threading
import time
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import app as core

PLUGIN = {"name": "IR remotes", "version": "1.0.0"}

TRANSPORT = "ir"
PROTOS = ("ir", "rf")
DATA = core.plugin_data("ir")
REMOTES_PATH = DATA / "ir_remotes.json"
_load_failed = False
_lock = threading.RLock()
_learn = {"node": None, "proto": None, "until": 0.0, "code": None, "error": None, "sid": 0}
_learn_ev = threading.Event()
_node_status: dict = {}


def _flatten(rec: dict) -> dict:
    out = {k: v for k, v in rec.items() if k != "groups"}
    if not isinstance(rec.get("groups"), list):
        out.setdefault("buttons", [])
        return out
    btns = []
    for g in rec["groups"]:
        gname = (g or {}).get("name") or None
        for b in (g or {}).get("buttons") or []:
            b = dict(b)
            if gname:
                b["group"] = gname
            else:
                b.pop("group", None)
            btns.append(b)
    out["buttons"] = btns
    return out


def _nest(r: dict) -> dict:
    groups, index = [], {}
    for b in r.get("buttons") or []:
        g = b.get("group") or None
        if g not in index:
            index[g] = {"name": g, "buttons": []}
            groups.append(index[g])
        index[g]["buttons"].append({k: v for k, v in b.items() if k != "group"})
    rec = {k: v for k, v in r.items() if k != "buttons"}
    rec["groups"] = groups
    return rec


def _load_remotes() -> list:
    global _load_failed
    if not REMOTES_PATH.exists():
        return []
    try:
        data = json.loads(REMOTES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("remotes") or []
        if not isinstance(data, list):
            raise ValueError("not a list of remotes")
        return [_flatten(r) for r in data if isinstance(r, dict)]
    except (OSError, ValueError) as e:
        _load_failed = True
        core.log.error(f"[ir] cannot read {REMOTES_PATH.name}: {e}; saving remotes is DISABLED until it is fixed")
        return []


REMOTES: list = _load_remotes()
APPLIANCES: list = list(core._cfg_section("ir_appliances", []))
BLASTERS: list = list(core._cfg_section("ir_blasters", []))
TOGGLES: list = [t for t in core._cfg_section("ir_toggles", []) if isinstance(t, dict) and isinstance(t.get("id"), str)]


def _save():
    """settings.json (appliances, blasters) and the remote library, both atomically."""
    core._save_settings()
    if _load_failed:
        core.log.error(f"[ir] NOT saving {REMOTES_PATH.name}: it failed to load at start")
        return
    core._atomic_json_dump(REMOTES_PATH, [_nest(r) for r in REMOTES], indent=2, ensure_ascii=False)


def _writable():
    """The remote library failed to load: a change would be lost at restart, so refuse it."""
    if _load_failed:
        raise HTTPException(503, f"{REMOTES_PATH.name} failed to load at start - fix it and restart")


def _remote(rid):
    return next((r for r in REMOTES if r.get("id") == rid), None)


def _appliance(aid):
    return next((a for a in APPLIANCES if a.get("id") == aid), None)


def _blaster(node):
    return next((n for n in BLASTERS if n.get("node") == node), None)


def _rx_node() -> str:
    n = next((x for x in BLASTERS if x.get("rx")), None)
    return n["node"] if n else ""


def _node_online(node: str) -> bool:
    """From <node>/status when it reaches us; when the core already routes that topic to a
    device of its own (a blaster card, an AC whose availability it is), from that device."""
    if node in _node_status:
        return _node_status[node]
    topic = f"{node}/status"
    for did, d in list(core.DEVICES.items()):
        if d.get("ir_blaster") == node or d.get("availability_topic") == topic:
            return bool(core._status_copy(did, {}).get("online"))
    return False


_TRANSLIT = {'\u0430': 'a', '\u0431': 'b', '\u0432': 'v', '\u0433': 'g', '\u0434': 'd', '\u0435': 'e', '\u0451': 'e', '\u0436': 'zh', '\u0437': 'z', '\u0438': 'i', '\u0439': 'y', '\u043a': 'k', '\u043b': 'l', '\u043c': 'm', '\u043d': 'n', '\u043e': 'o', '\u043f': 'p', '\u0440': 'r', '\u0441': 's', '\u0442': 't', '\u0443': 'u', '\u0444': 'f', '\u0445': 'h', '\u0446': 'c', '\u0447': 'ch', '\u0448': 'sh', '\u0449': 'sch', '\u044a': '', '\u044b': 'y', '\u044c': '', '\u044d': 'e', '\u044e': 'yu', '\u044f': 'ya'}


def _slug(name: str, taken) -> str:
    """A stable readable ASCII id from a typed name (Cyrillic transliterated)."""
    out = []
    for ch in (name or "").strip().lower():
        out.append(_TRANSLIT[ch] if ch in _TRANSLIT else ch if ch.isascii() and ch.isalnum() else "_")
    base = "_".join(filter(None, "".join(out).split("_"))) or "btn"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    return slug


def _device_of(a: dict) -> dict:
    r = _remote(a.get("remote")) or {}
    ctrls = [{"kind": "ir_press", "code": f"ir_{b['id']}",
              "label": f"{b['group']} \u00b7 {b.get('name')}" if b.get("group") else b.get("name"),
              "group": b.get("group"), "remote": a.get("remote"), "button": b["id"],
              "proto": b.get("proto", "ir"), "via": a.get("blaster")} for b in r.get("buttons", [])]
    return {"id": a["id"], "name": a.get("name") or a["id"], "category": "ir", "transport": TRANSPORT,
            "ir_appliance": True, "ir_remote": a.get("remote"), "ir_via": a.get("blaster"),
            "ir_card": a.get("device"), "controls": ctrls}


def _toggle_device(t: dict) -> dict:
    return {"id": t["id"], "name": t.get("name") or t["id"], "category": t.get("category") or "media",
            "transport": TRANSPORT, "model": t.get("model"), "ir_toggle": t.get("power"),
            "controls": [{"kind": "switch", "code": "power", "label": "Power", "text_on": "On", "text_off": "Off"}]}


def _sync():
    """Appliances and toggles -> devices of the house: new ones registered, changed ones replaced,
    deleted ones dropped (only ours: the core refuses anything else)."""
    for t in TOGGLES:
        if t["id"] in core.DEVICES:
            if core.DEVICES[t["id"]].get("transport") == TRANSPORT:
                core.update_device(_toggle_device(t))
            else:
                core.log.error(f"[ir] toggle {t['id']!r}: the id is taken by a {core.DEVICES[t['id']].get('transport')} device")
        else:
            core.register_device(_toggle_device(t))
    live = {a["id"] for a in APPLIANCES} | {t["id"] for t in TOGGLES}
    for did in [k for k, d in list(core.DEVICES.items()) if d.get("transport") == TRANSPORT and k not in live]:
        core.remove_device(did, TRANSPORT)
    for a in APPLIANCES:
        dev = _device_of(a)
        try:                                           # an id another plug-in or the core holds: skip it
            if a["id"] in core.DEVICES:
                core.update_device(dev)
            else:
                core.register_device(dev)
        except ValueError as e:
            core.log.error(f"[ir] appliance {a['id']!r} skipped: {e}")
            continue
        _status(a["id"])


def _status(aid: str):
    a = _appliance(aid)
    if not a or not (core.DEVICES.get(aid) or {}).get("ir_appliance"):
        return
    cur = core._status_copy(aid, {"values": {}, "raw": {}})
    on = _node_online(a.get("blaster"))
    if cur.get("online") != on:
        cur["online"] = on
        cur.setdefault("values", {})
        core._store_status_exact(aid, cur)


def _send(rid: str, bid: str, node: str = None) -> dict:
    r = _remote(rid)
    btn = next((b for b in (r or {}).get("buttons", []) if b.get("id") == bid), None)
    if not btn:
        raise KeyError(f"{rid}/{bid}")
    node, proto = node or _rx_node(), btn.get("proto", "ir")
    if _blaster(node) is None:
        raise RuntimeError(f"unknown blaster {node!r}")
    core._mqtt_pub(core._mqtt_client, f"{node}/tx/{proto}_raw", btn["code"])
    return {"button": btn.get("name"), "via": node, "proto": proto}


def _press(cfg: dict, ctl: dict, val) -> dict:
    """CONTROL_KINDS["ir_press"]: a button of an appliance, from /control, a rule or a panel."""
    return _send(ctl.get("remote"), ctl.get("button"), ctl.get("via"))


def _toggle(cfg: dict, code: str, val) -> dict:
    """"power" of an IR toggle: the frame flips the box, so it goes only when the probed state differs."""
    if code != "power" or not cfg.get("ir_toggle"):
        raise HTTPException(400, f"{cfg['id']}: no channel {code!r}")
    want = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "on")
    cur = (core._status_copy(cfg["id"], {}).get("values") or {}).get("power")
    if cur is not None and bool(cur) == want:
        return {"unchanged": True}
    it = cfg["ir_toggle"]
    return _send(it["remote"], it["button"], it.get("via"))


def _transport(cfg: dict, code: str, val, kind=None):
    _toggle(cfg, code, val)


async def _async_transport(cfg: dict, code: str, val):
    await run_in_threadpool(_toggle, cfg, code, val)


def _probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_loop():
    """Every 20 s: each toggle's state from its TCP probe; keep_on wakes a box that went quiet."""
    down_since: dict = {}
    woke_at: dict = {}
    while True:
        for t in list(TOGGLES):
            pr = t.get("probe") or {}
            if not pr.get("host") or t["id"] not in core.DEVICES:
                continue
            up = _probe(pr["host"], pr.get("port", 5555))
            cur = core._status_copy(t["id"], {"values": {}, "raw": {}})
            if cur.get("online") is not True or (cur.get("values") or {}).get("power") != up:
                cur["online"] = True
                cur.setdefault("values", {})["power"] = up
                core._store_status_exact(t["id"], cur)
            now = time.time()
            if up:
                down_since.pop(t["id"], None)
            elif pr.get("keep_on") and t.get("power"):
                down_since.setdefault(t["id"], now)
                if now - down_since[t["id"]] >= 60 and now - woke_at.get(t["id"], 0) >= 300:
                    try:
                        _send(t["power"]["remote"], t["power"]["button"], t["power"].get("via"))
                        woke_at[t["id"]] = now
                        core.log.warning(f"[ir] {t['id']} silent for {int(now - down_since[t['id']])} s - waking it with the IR power key")
                    except Exception as e:                # noqa: BLE001
                        core.log.warning(f"[ir] {t['id']}: wake-up failed: {e}")
        time.sleep(20)


def _refs(dev_id: str, code: str) -> list:
    """Who points at a channel before it goes: rules, schedules, favourites."""
    out = []
    for src, codes in (core._bindings or {}).items():
        for scode, gestures in (codes or {}).items():
            for gesture, b in (gestures or {}).items():
                if b.get("target") == dev_id and b.get("code") == code:
                    out.append(f"rule: {src}/{scode} ({gesture})")
    if f"{dev_id}/{code}" in (core._autos or {}):
        out.append("schedule/auto-off")
    if any(f.get("device") == dev_id and f.get("code") == code for f in (core._favorites or [])):
        out.append("favourites")
    return out


def _handle(parts, payload, retain=False):
    """<node>/status (nodes no core device fronts) and <node>/rx/{ir_raw,rf_raw,error}."""
    node, rest = parts[0], parts[1:]
    raw = payload.decode("utf-8", "replace").strip() if isinstance(payload, bytes) else str(payload).strip()
    if rest == ["status"]:
        _node_status[node] = raw == "online"
        for a in APPLIANCES:
            if a.get("blaster") == node:
                _status(a["id"])
        return
    if retain or len(rest) != 2 or rest[0] != "rx":
        return
    s = _learn
    if s["node"] != node or time.time() > s["until"]:
        return
    if rest[1] == "error":
        s["error"] = raw or "error"
    elif rest[1] == f"{s['proto']}_raw" and raw:
        s["code"] = raw
    else:
        return
    _learn_ev.set()


def _public_remote(r: dict) -> dict:
    return {"id": r.get("id"), "name": r.get("name"), "learned_on": r.get("learned_on") or _rx_node(),
            "used_by": [{"id": a["id"], "name": a.get("name"), "blaster": a.get("blaster"),
                         "blaster_name": (_blaster(a.get("blaster")) or {}).get("name", a.get("blaster"))}
                        for a in APPLIANCES if a.get("remote") == r.get("id")],
            "buttons": [{"id": b.get("id"), "name": b.get("name"), "group": b.get("group"), "proto": b.get("proto", "ir"),
                         "n": b.get("n") or len((b.get("code") or "").split(","))} for b in r.get("buttons", [])]}


def _public_appliance(a: dict) -> dict:
    r = _remote(a.get("remote")) or {}
    node = a.get("blaster")
    return {"id": a.get("id"), "name": a.get("name"), "remote": a.get("remote"), "remote_name": r.get("name"),
            "blaster": node, "blaster_name": (_blaster(node) or {}).get("name", node), "blaster_online": _node_online(node),
            "device": a.get("device"), "device_name": (core.DEVICES.get(a.get("device")) or {}).get("name") if a.get("device") else None,
            "buttons": [{"id": b.get("id"), "name": b.get("name"), "group": b.get("group"), "proto": b.get("proto", "ir")}
                        for b in r.get("buttons", [])]}


class RemoteBody(BaseModel):
    name: str


class LearnBody(BaseModel):
    name: str
    group: Optional[str] = None
    proto: str = "ir"
    timeout: int = 30


class NameBody(BaseModel):
    name: Optional[str] = None
    group: Optional[str] = None


class SendBody(BaseModel):
    tx: Optional[str] = None


class ApplianceBody(BaseModel):
    name: str
    remote: str
    blaster: str
    device: Optional[str] = None


class AppliancePatch(BaseModel):
    name: Optional[str] = None
    remote: Optional[str] = None
    blaster: Optional[str] = None
    device: Optional[str] = None


@core.app.get("/api/ir/remotes")
def ir_remotes():
    return {"remotes": [_public_remote(r) for r in REMOTES],
            "appliances": [_public_appliance(a) for a in APPLIANCES],
            "blasters": [{"node": n["node"], "name": n.get("name") or n["node"], "rx": bool(n.get("rx")),
                          "device": next((did for did, d in core.DEVICES.items() if d.get("ir_blaster") == n["node"]), None),
                          "online": _node_online(n["node"])} for n in BLASTERS],
            "learn_on": _rx_node(), "learning": _learn["node"] if time.time() <= _learn["until"] else None}


@core.app.post("/api/ir/remotes")
def ir_remote_add(body: RemoteBody):
    _writable()
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "remote name required")
    with _lock:
        r = {"id": _slug(name, {x.get("id") for x in REMOTES}), "name": name, "learned_on": _rx_node(), "buttons": []}
        REMOTES.append(r)
        _save()
    return _public_remote(r)


@core.app.patch("/api/ir/remotes/{rid}")
def ir_remote_rename(rid: str, body: NameBody):
    _writable()
    r = _remote(rid)
    if not r:
        raise HTTPException(404, "no such remote")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "remote name required")
    with _lock:
        r["name"] = name
        _save()
        _sync()
    return _public_remote(r)


@core.app.delete("/api/ir/remotes/{rid}")
def ir_remote_del(rid: str):
    _writable()
    r = _remote(rid)
    if not r:
        raise HTTPException(404, "no such remote")
    doomed = [a for a in APPLIANCES if a.get("remote") == rid]
    refs = [x for a in doomed for b in r.get("buttons", []) for x in _refs(a["id"], f"ir_{b.get('id')}")]
    with _lock:
        REMOTES.remove(r)
        for a in doomed:
            APPLIANCES.remove(a)
        _save()
        _sync()
    return {"ok": True, "removed": rid, "buttons": len(r.get("buttons", [])),
            "appliances": [a.get("name") for a in doomed], "broke": refs}


@core.app.get("/api/ir/appliances")
def ir_appliances():
    return {"appliances": [_public_appliance(a) for a in APPLIANCES]}


@core.app.post("/api/ir/appliances")
def ir_appliance_add(body: ApplianceBody):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "tile name required")
    if not _remote(body.remote):
        raise HTTPException(404, "no such remote")
    if _blaster(body.blaster) is None:
        raise HTTPException(404, "no such blaster")
    if body.device and body.device not in core.DEVICES:
        raise HTTPException(404, "no such device")
    with _lock:
        a = {"id": _slug(name, {x.get("id") for x in APPLIANCES} | set(core.DEVICES)), "name": name,
             "remote": body.remote, "blaster": body.blaster}
        if body.device:
            a["device"] = body.device
        APPLIANCES.append(a)
        _save()
        _sync()
    return _public_appliance(a)


@core.app.patch("/api/ir/appliances/{aid}")
def ir_appliance_patch(aid: str, body: AppliancePatch):
    a = _appliance(aid)
    if not a:
        raise HTTPException(404, "no such tile")
    if body.remote is not None and not _remote(body.remote):
        raise HTTPException(404, "no such remote")
    if body.blaster is not None and _blaster(body.blaster) is None:
        raise HTTPException(404, "no such blaster")
    if body.device and body.device not in core.DEVICES:
        raise HTTPException(404, "no such device")
    name = (body.name or "").strip()
    if not (name or body.remote or body.blaster or body.device is not None):
        raise HTTPException(400, "nothing to change")
    with _lock:
        if name:
            a["name"] = name
        if body.remote:
            a["remote"] = body.remote
        if body.blaster:
            a["blaster"] = body.blaster
        if body.device is not None:
            if body.device:
                a["device"] = body.device
            else:
                a.pop("device", None)
        _save()
        _sync()
    return _public_appliance(a)


@core.app.delete("/api/ir/appliances/{aid}")
def ir_appliance_del(aid: str):
    a = _appliance(aid)
    if not a:
        raise HTTPException(404, "no such tile")
    r = _remote(a.get("remote")) or {}
    refs = [x for b in r.get("buttons", []) for x in _refs(aid, f"ir_{b.get('id')}")]
    with _lock:
        APPLIANCES.remove(a)
        _save()
        _sync()
    return {"ok": True, "removed": aid, "broke": refs}


@core.app.post("/api/ir/remotes/{rid}/learn")
async def ir_learn(rid: str, body: LearnBody):
    """Arm the receiving blaster and wait for one frame (or the timeout): the UI says "press
    a button" for exactly as long as the node is listening."""
    _writable()
    r = _remote(rid)
    if not r:
        raise HTTPException(404, "no such remote")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "button name required")
    proto = (body.proto or "ir").lower()
    if proto not in PROTOS:
        raise HTTPException(400, "proto: ir | rf")
    node = _rx_node()
    if not node:
        raise HTTPException(400, "no blaster has a receiver - nothing to learn with")
    if time.time() <= _learn["until"]:
        raise HTTPException(409, "already learning a button")
    timeout = max(5, min(int(body.timeout or 30), 120))
    _learn_ev.clear()
    sid = _learn["sid"] + 1
    _learn.update(node=node, proto=proto, until=time.time() + timeout, code=None, error=None, sid=sid)
    try:
        core._mqtt_pub(core._mqtt_client, f"{node}/learn/command", "ON")
    except Exception as e:                            # noqa: BLE001
        _learn.update(node=None, until=0.0)
        raise HTTPException(502, f"could not start learning on {node}: {e}")
    got = await run_in_threadpool(_learn_ev.wait, timeout)
    if _learn["sid"] != sid:                         # cancelled, and a new session armed since: leave it alone
        raise HTTPException(409, "learning cancelled")
    code, err = _learn["code"], _learn["error"]
    _learn.update(node=None, proto=None, until=0.0, code=None, error=None)
    try:
        core._mqtt_pub(core._mqtt_client, f"{node}/learn/command", "OFF")
    except Exception as e:                            # noqa: BLE001
        core.log.warning(f"[ir] could not stop learning on {node}: {e}")
    if err == "too_long":
        raise HTTPException(422, "frame too long for one MQTT message - caught noise or a very long protocol, try again")
    if err:
        raise HTTPException(502, f"{node}: {err}")
    if not got or not code:
        raise HTTPException(408, "no frame captured: press the button with the remote at the blaster's receiver from 10-20 cm")
    with _lock:
        r["learned_on"] = node
        btn = {"id": _slug(name, {b.get("id") for b in r.get("buttons", [])}), "name": name, "proto": proto,
               "code": code, "n": len(code.split(","))}
        if (body.group or "").strip():
            btn["group"] = body.group.strip()
        r.setdefault("buttons", []).append(btn)
        _save()
        _sync()
    core.log.info(f"[ir] {r['id']}/{btn['id']}: learned {btn['n']} intervals from {node} ({proto})")
    return {"remote": _public_remote(r), "button": {k: v for k, v in btn.items() if k != "code"}}


@core.app.post("/api/ir/learn/cancel")
def ir_learn_cancel():
    node = _learn["node"]
    _learn.update(until=0.0)
    _learn_ev.set()
    if node:
        try:
            core._mqtt_pub(core._mqtt_client, f"{node}/learn/command", "OFF")
        except Exception as e:                        # noqa: BLE001
            core.log.warning(f"[ir] cancel: {e}")
    return {"ok": True, "was": node}


@core.app.post("/api/ir/remotes/{rid}/buttons/{bid}/send")
def ir_button_send(rid: str, bid: str, body: SendBody = SendBody()):
    if not _remote(rid):
        raise HTTPException(404, "no such remote")
    try:
        info = _send(rid, bid, body.tx)
    except KeyError:
        raise HTTPException(404, "no such button")
    except Exception as e:                            # noqa: BLE001
        raise HTTPException(502, f"send failed: {e}")
    return {"ok": True, "sent": info["button"], "via": info["via"], "proto": info["proto"]}


@core.app.patch("/api/ir/remotes/{rid}/buttons/{bid}")
def ir_button_rename(rid: str, bid: str, body: NameBody):
    _writable()
    r = _remote(rid)
    if not r:
        raise HTTPException(404, "no such remote")
    btn = next((b for b in r.get("buttons", []) if b.get("id") == bid), None)
    if not btn:
        raise HTTPException(404, "no such button")
    name = (body.name or "").strip()
    if not name and body.group is None:
        raise HTTPException(400, "button name or group required")
    with _lock:
        if name:
            btn["name"] = name
        if body.group is not None:
            if body.group.strip():
                btn["group"] = body.group.strip()
            else:
                btn.pop("group", None)
        _save()
        _sync()
    return _public_remote(r)


@core.app.delete("/api/ir/remotes/{rid}/buttons/{bid}")
def ir_button_del(rid: str, bid: str):
    _writable()
    r = _remote(rid)
    if not r:
        raise HTTPException(404, "no such remote")
    btn = next((b for b in r.get("buttons", []) if b.get("id") == bid), None)
    if not btn:
        raise HTTPException(404, "no such button")
    refs = [x for a in APPLIANCES if a.get("remote") == rid for x in _refs(a["id"], f"ir_{bid}")]
    with _lock:
        r["buttons"].remove(btn)
        _save()
        _sync()
    return {"remote": _public_remote(r), "broke": refs}


async def _status_loop():
    """Appliance tiles follow their blaster, including blasters whose status the core routes."""
    import asyncio
    while True:
        for a in list(APPLIANCES):
            _status(a["id"])
        await asyncio.sleep(10)


core.SETTINGS_SECTIONS["ir_appliances"] = lambda: APPLIANCES
core.SETTINGS_SECTIONS["ir_blasters"] = lambda: BLASTERS
core.SETTINGS_SECTIONS["ir_toggles"] = lambda: TOGGLES
core.TRANSPORTS[TRANSPORT] = _transport
core.ASYNC_CONTROL[TRANSPORT] = _async_transport
core.CONTROL_KINDS["ir_press"] = _press
core.DEVICE_FIELDS.extend(["ir_appliance", "ir_remote", "ir_via", "ir_card", "ir_blaster"])
for _n in BLASTERS:
    if isinstance(_n.get("node"), str) and _n["node"]:
        core.MQTT_SUBSCRIPTIONS.extend([f"{_n['node']}/status", f"{_n['node']}/rx/+"])
        core.MQTT_HANDLERS.append(((_n["node"],), _handle))
core.STARTUP_TASKS.append(_status_loop)
core.STARTUP_TASKS.append(_probe_loop)
_sync()
