"""Learned IR/RF remotes. The codes live here, never on a blaster: an ESP node is a dumb pipe
(<node>/tx/{ir,rf}_raw), so a reflashed or swapped board loses nothing.

An appliance may name its POWER FEED ({..., power: {device, code}}: the wall switch channel that powers a lamp with a fan):
while that channel says off, a press is refused (409, the switch named); the last key sent of each group is kept in the
appliance's state as sent_<group> (a group of two keys or more: one key is a toggle) and is cleared when the feed goes
off (IR has no feedback: "sent", never "is").

A REMOTE is a set of codes [{id, name, learned_on, buttons: [{id, name, group?, proto, code, n}]}]
(plugin_data("ir")/ir_remotes.json, grouped by function on disk). An APPLIANCE pairs a remote
with the blaster in that room ({id, name, remote, blaster, device?}; settings "ir_appliances");
it becomes a device of the house with one write-only channel (kind ir_press) per button, so
rules, schedules, favourites, panels and voice reach a learned button as (device, code). The
blasters are the settings section "ir_blasters" ([{node, name, rx}], rx = can learn).

Learning is one session at a time: arm <node>/learn/command ON, wait for <node>/rx/{ir,rf}_raw,
store it. Native-remote state (an air conditioner's own remote) is decoded by the blaster's
firmware and published as that AC's own state topics; nothing here takes part in it.

An IR TOGGLE is a device whose power key flips it (a media box): settings "ir_toggles"
[{id, name, category?, model?, power: {remote, button, via}, probe: {host, port, keep_on?}, touchmouse?}].
A TCP probe gives its state; "power" sends the frame only when the box is in the other state;
keep_on presses it after a minute of silence, at most once per 5 minutes."""
import json
import re
import socket
import threading
import time
from typing import Optional

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

import app as core

PLUGIN = {"name": "IR remotes", "version": "1.0.0", "icon": "ir"}

TRANSPORT = "ir"
PROTOS = ("ir", "rf")
LEARN_MIN_S, LEARN_MAX_S = 5, 120          # a learning window asked for is kept in this range
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
APPLIANCES: list = list(core.cfg_section("ir_appliances", []))
BLASTERS: list = list(core.cfg_section("ir_blasters", []))
TOGGLES: list = [t for t in core.cfg_section("ir_toggles", []) if isinstance(t, dict) and isinstance(t.get("id"), str)]


def _save():
    """The settings (appliances, blasters, toggles: plugin-data/ir/settings.json) and the remote library, both atomically."""
    core.save_settings()
    if _load_failed:
        core.log.error(f"[ir] NOT saving {REMOTES_PATH.name}: it failed to load at start")
        return
    core.atomic_json_dump(REMOTES_PATH, [_nest(r) for r in REMOTES], indent=2, ensure_ascii=False)


def _writable():
    """The remote library failed to load: a change would be lost at restart, so refuse it."""
    if _load_failed:
        raise HTTPException(503, f"{REMOTES_PATH.name} failed to load at start - fix it and restart")


def _remote(rid):
    return next((r for r in REMOTES if r.get("id") == rid), None)


def _group_part(r: dict, group, but=None) -> bool:
    """A group is part of this remote (a keypad, the inputs: `part` on its buttons) when its buttons say so; none yet: not."""
    sib = [b for b in r.get("buttons", []) if (b.get("group") or None) == (group or None) and b is not but]
    return bool(sib) and bool(group) and all(b.get("part") for b in sib)


def _appliance(aid):
    return next((a for a in APPLIANCES if a.get("id") == aid), None)


def _blaster(node):
    return next((n for n in BLASTERS if n.get("node") == node), None)


def _blaster_device(node):
    """The core device that fronts a blaster (its card), or None; a card naming a blaster that was removed fronts nothing."""
    if _blaster(node) is None:
        return None
    return next((did for did, d in list(core.DEVICES.items()) if d.get("ir_blaster") == node and not _own_blaster(did)), None)


def _blaster_name(node) -> str:
    """One name per blaster: its device's when a device fronts it, else the one in ir_blasters."""
    did = _blaster_device(node)
    if did:
        return core.DEVICES[did].get("name") or node
    return (_blaster(node) or {}).get("name") or node


def _rx_node() -> str:
    n = next((x for x in BLASTERS if x.get("rx")), None)
    return n["node"] if n else ""


BLASTER_PREFIX = "blaster:"                             # the device of a blaster no other device fronts
_ID_TAKEN: dict = {}                                     # such a device's id held by another plug-in -> why


def _own_blaster(did) -> bool:
    return isinstance(did, str) and did.startswith(BLASTER_PREFIX)


def _node_online(node: str) -> bool:
    """From <node>/status when it reaches us; when the core already routes that topic to a
    device of its own (a blaster card, an AC whose availability it is), from that device."""
    if node in _node_status:
        return _node_status[node]
    topic = f"{node}/status"
    for did, d in list(core.DEVICES.items()):
        if _own_blaster(did):                          # ours follows this answer: never its source
            continue
        if d.get("ir_blaster") == node or d.get("availability_topic") == topic:
            return bool(core.status_copy(did, {}).get("online"))
    return False


# i18n: Russian names -> ascii ids
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


def _icon_ok(icon) -> bool:
    return bool(icon) and re.fullmatch(r"[a-z0-9_]{1,16}", icon) is not None


HIDE_GROUPS = ("power", "nav", "volume", "channels", "media", "other")     # the groups of keys a remote's face can hide


def _device_of(a: dict) -> dict:
    r = _remote(a.get("remote")) or {}
    ctrls = [{"kind": "ir_press", "code": f"ir_{b['id']}",
              "label": f"{b['group']} \u00b7 {b.get('name')}" if b.get("group") else b.get("name"),
              "group": b.get("group"), "part": bool(b.get("part")), "icon": b.get("icon"), "remote": a.get("remote"), "button": b["id"],
              "proto": b.get("proto", "ir"), "via": a.get("blaster")} for b in r.get("buttons", [])]
    return {"id": a["id"], "name": a.get("name") or a["id"], "category": "ir", "transport": TRANSPORT,
            "ir_appliance": True, "ir_remote": a.get("remote"), "ir_via": a.get("blaster"),
            "ir_card": a.get("device"), "ir_hide": r.get("hide") or [], "ir_power": _power(a), "controls": ctrls}


def _toggle_device(t: dict) -> dict:
    d = {"id": t["id"], "name": t.get("name") or t["id"], "category": t.get("category") or "media",
         "transport": TRANSPORT, "model": t.get("model"), "ir_toggle": t.get("power"),
         "controls": [{"kind": "switch", "code": "power", "label": "Power", "text_on": "On", "text_off": "Off"}]}
    if isinstance(t.get("touchmouse"), dict):               # cutover-compat: a TV box as a touchpad target
        d["touchmouse"] = {**t["touchmouse"], "host": t["touchmouse"].get("host") or (t.get("probe") or {}).get("host")}
    return d


def _device_names(but=()) -> list:
    """The names a tile or a blaster may not take: every device's but its own."""
    return [d.get("name") for k, d in list(core.DEVICES.items()) if k not in but]


def _card_of(node: str):
    """The device that shows this blaster: a card of its node, else its own "blaster:" device."""
    own = BLASTER_PREFIX + node
    return _blaster_device(node) or (own if own in core.DEVICES else None)


def _blaster_dev(b: dict) -> dict:
    return {"id": BLASTER_PREFIX + b["node"], "name": b.get("name") or b["node"], "category": "ir", "transport": TRANSPORT,
            "ir_blaster": b["node"], "controls": []}


def _bare_blasters() -> dict:
    """The blasters no device fronts (no card of the node's own): device id -> blaster."""
    return {BLASTER_PREFIX + b["node"]: b for b in BLASTERS
            if isinstance(b.get("node"), str) and b["node"] and b["node"] not in core.DEVICES and _blaster_device(b["node"]) is None}


def _blaster_status(did: str):
    node = did[len(BLASTER_PREFIX):]
    if not _own_blaster(did) or (core.DEVICES.get(did) or {}).get("ir_blaster") != node:
        return
    cur = core.status_copy(did, {"values": {}, "raw": {}})
    on = _node_online(node)
    if cur.get("online") != on:
        cur["online"] = on
        cur.setdefault("values", {})
        (core.store_status if on else core.store_status_exact)(did, cur)


def _sync():
    """Appliances, toggles and the blasters no device fronts -> devices of the house: new ones registered, changed
    ones replaced, deleted ones dropped (only ours: the core refuses anything else)."""
    for t in TOGGLES:
        if t["id"] in core.DEVICES:
            if core.DEVICES[t["id"]].get("transport") == TRANSPORT:
                core.update_device(_toggle_device(t))
            else:
                core.log.error(f"[ir] toggle {t['id']!r}: the id is taken by a {core.DEVICES[t['id']].get('transport')} device")
        else:
            core.register_device(_toggle_device(t))
    bare = _bare_blasters()
    live = {a["id"] for a in APPLIANCES if not core.merged(a["id"])} | {t["id"] for t in TOGGLES} | set(bare)
    for did in [k for k, d in list(core.DEVICES.items()) if d.get("transport") == TRANSPORT and k not in live]:
        core.remove_device(did, TRANSPORT)
    _ID_TAKEN.clear()
    for did, b in bare.items():
        try:                                           # the id held by another plug-in: Health says so
            (core.update_device if did in core.DEVICES else core.register_device)(_blaster_dev(b))
        except ValueError as e:
            _ID_TAKEN[did] = str(e)
            core.log.error(f"[ir] blaster {b['node']!r} gets no device: {e}")
            continue
        _blaster_status(did)
    for a in APPLIANCES:
        dev = _device_of(a)
        host = core.merged(a["id"])
        if host:                                       # merged into its device: no device of its own, that device's IR part
            core.extend_device(host, "ir", dev["controls"], TRANSPORT, info={"remote": a.get("remote"), "blaster": a.get("blaster"), "appliance": a["id"], "hide": dev.get("ir_hide") or [],
                                     "remote_name": (_remote(a.get("remote")) or {}).get("name") or a.get("remote")},
                               cfg={"id": a["id"], "ir_remote": a.get("remote"), "ir_via": a.get("blaster")}, map=_ir_map(dev["controls"]))
            continue
        try:                                           # an id another plug-in or the core holds: skip it
            if a["id"] in core.DEVICES:
                core.update_device(dev)
            else:
                core.register_device(dev)
        except ValueError as e:
            core.log.error(f"[ir] appliance {a['id']!r} skipped: {e}")
            continue
        _status(a["id"])


def _ir_map(ctrls: list) -> dict:
    """The device's power by the remote's discrete on / off keys (a toggle key is never a fallback: it could switch off)."""
    codes = {c["button"]: c["code"] for c in ctrls if c.get("button")}
    on = next((codes[b] for b in ("power_on", "on", "vklyuchit") if b in codes), None)
    off = next((codes[b] for b in ("power_off", "off", "vyklyuchit") if b in codes), None)
    m = {**({"true": on} if on else {}), **({"false": off} if off else {})}
    return {"power": m} if m else {}


def _merge_plan(old: str, new: str) -> dict:
    a = _appliance(old)
    return {"ir: the appliance becomes the IR part of " + new: 1} if a else {}


def _merged(old: str, new: str):
    """DEVICE_MERGED: an appliance merged into a device: its own device goes, it is that device's IR part from now on."""
    if _appliance(old):
        threading.Timer(0, _sync_locked).start()


def _status(aid: str):
    a = _appliance(aid)
    if not a or not (core.DEVICES.get(aid) or {}).get("ir_appliance"):
        return
    cur = core.status_copy(aid, {"values": {}, "raw": {}})
    on = _node_online(a.get("blaster"))
    if cur.get("online") != on:
        cur["online"] = on
        cur.setdefault("values", {})
        # it never reports: its blaster's word is all there is - back with it at once, gone with it at once
        (core.store_status if on else core.store_status_exact)(aid, cur)


def _send(rid: str, bid: str, node: str = None) -> dict:
    r = _remote(rid)
    btn = next((b for b in (r or {}).get("buttons", []) if b.get("id") == bid), None)
    if not btn:
        raise KeyError(f"{rid}/{bid}")
    node, proto = node or _rx_node(), btn.get("proto", "ir")
    if _blaster(node) is None:
        raise RuntimeError(f"unknown blaster {node!r}")
    core.mqtt_pub(core.mqtt_client, f"{node}/tx/{proto}_raw", btn["code"])
    return {"button": btn.get("name"), "via": node, "proto": proto}


_NOT_A_PRESS = {"off", "false", "0"}


def _feeds() -> dict:
    """{appliance id: {device, code}}: the wiring's feeds; the old "power" fields while the wiring does not speak for IR."""
    if core.wiring_owns("ir"):
        return {e["target"]["dev"]: {"device": e["switch"]["dev"], "code": e["switch"]["code"]} for e in core.wiring() if e["target"].get("dev")}
    return {a["id"]: dict(a["power"]) for a in APPLIANCES if isinstance(a.get("power"), dict) and a["power"].get("device")}


def _power(a: dict, feeds: dict = None):
    return (_feeds() if feeds is None else feeds).get(a.get("id"))


def _powered(a: dict):
    """The power feed's word: True / False, None when there is no feed or it is not known (offline, never reported)."""
    pw = _power(a)
    if not pw or pw.get("device") not in core.DEVICES:
        return None
    st = core.status_copy(pw["device"], {})
    v = (st.get("values") or {}).get(pw.get("code"))
    return v if isinstance(v, bool) and st.get("online") is not False else None


def _feed_ok(did: str, code: str, aid: str = "") -> bool:
    """A channel that can power a fixture: an on/off (switch) channel of another device."""
    return did != aid and ((core.DEVICES.get(did) or {}).get("_ctl") or {}).get(code, {}).get("kind") == "switch"


def _feed_name(a: dict) -> str:
    pw = _power(a) or {}
    d = core.DEVICES.get(pw.get("device")) or {}
    lbl = ((d.get("_ctl") or {}).get(pw.get("code")) or {}).get("label")
    return (d.get("name") or pw.get("device") or "?") + (f" ({lbl})" if lbl else "")


def _set_values(aid: str, change: dict, drop_prefix: str = ""):
    """An appliance's own values (sent_<group>, power), its online word left as it is."""
    cur = core.status_copy(aid, {"values": {}, "raw": {}})
    vals = {k: v for k, v in (cur.get("values") or {}).items() if not (drop_prefix and k.startswith(drop_prefix))}
    vals.update(change)
    if vals != (cur.get("values") or {}):
        cur["values"] = vals
        core.store_status_exact(aid, cur)


def _press(cfg: dict, ctl: dict, val) -> dict:
    """CONTROL_KINDS["ir_press"]: a button of an appliance, from /control, a rule or a panel. A press
    is true (or no value): off / false / 0 sends nothing (home/cmd/<ir>/<button> off is not a press).
    An appliance whose power feed says off refuses it: the frame would reach a dead fixture."""
    if val is False or val == 0 or (isinstance(val, str) and val.strip().lower() in _NOT_A_PRESS):
        raise HTTPException(400, f"{cfg.get('name') or cfg['id']}: a button is pressed with true, not {val!r}")
    a = _appliance(cfg["id"]) or {}
    if _powered(a) is False:
        raise HTTPException(409, f"{cfg.get('name') or cfg['id']}: no power, switch on {_feed_name(a)}")
    out = _send(ctl.get("remote"), ctl.get("button"), ctl.get("via"))
    peers = [c for c in cfg.get("controls") or [] if c.get("kind") == "ir_press" and c.get("group") == ctl.get("group")]
    if _power(a) and len(peers) > 1:                   # one key alone is a toggle: what it last sent says nothing
        _set_values(cfg["id"], {f"sent_{ctl.get('group') or ''}".rstrip("_"): out["button"]})
    return out


def _on_event(ev: dict):
    """ON_EVENT: a power feed changed: the appliances on it say so, and lose their "sent" keys when it went off."""
    if ev.get("kind") != "state":
        return
    feeds = _feeds()
    for a in list(APPLIANCES):
        pw = feeds.get(a["id"])
        if pw and pw.get("device") == ev.get("device") and pw.get("code") == ev.get("code") and isinstance(ev.get("new"), bool):
            _set_values(a["id"], {"power": ev["new"]}, "" if ev["new"] else "sent")


def _toggle(cfg: dict, code: str, val) -> dict:
    """"power" of an IR toggle: the frame flips the box, so it goes only when the probed state differs."""
    if code != "power" or not cfg.get("ir_toggle"):
        raise HTTPException(400, f"{cfg['id']}: no channel {code!r}")
    want = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "on")
    cur = (core.status_copy(cfg["id"], {}).get("values") or {}).get("power")
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
            cur = core.status_copy(t["id"], {"values": {}, "raw": {}})
            if cur.get("online") is not True or (cur.get("values") or {}).get("power") != up:
                cur["online"] = True
                cur.setdefault("values", {})["power"] = up
                core.store_status_exact(t["id"], cur)
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
    with core.bindings_lock:
        for src, codes in (core.bindings or {}).items():
            for scode, gestures in (codes or {}).items():
                for gesture, b in (gestures or {}).items():
                    if b.get("target") == dev_id and b.get("code") == code:
                        out.append(f"rule: {src}/{scode} ({gesture})")
    if f"{dev_id}/{code}" in (core.autos or {}):
        out.append("schedule/auto-off")
    if any(f.get("device") == dev_id and f.get("code") == code for f in (core.favorites or [])):
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
    return {"id": r.get("id"), "name": r.get("name"), "hide": r.get("hide") or [], "learned_on": r.get("learned_on") or _rx_node(),
            "used_by": [{"id": a["id"], "name": a.get("name"), "blaster": a.get("blaster"),
                         "blaster_name": _blaster_name(a.get("blaster"))}
                        for a in APPLIANCES if a.get("remote") == r.get("id")],
            "buttons": [{"id": b.get("id"), "name": b.get("name"), "group": b.get("group"), "icon": b.get("icon"), "proto": b.get("proto", "ir"),
                         "part": bool(b.get("part")), "n": b.get("n") or len((b.get("code") or "").split(","))} for b in r.get("buttons", [])]}


def _public_appliance(a: dict) -> dict:
    r = _remote(a.get("remote")) or {}
    node = a.get("blaster")
    return {"id": a.get("id"), "name": a.get("name"), "remote": a.get("remote"), "remote_name": r.get("name"),
            "blaster": node, "blaster_name": _blaster_name(node), "blaster_online": _node_online(node),
            "device": a.get("device"), "device_name": (core.DEVICES.get(a.get("device")) or {}).get("name") if a.get("device") else None,
            "power": _power(a), "power_name": _feed_name(a) if _power(a) else None, "powered": _powered(a),
            "buttons": [{"id": b.get("id"), "name": b.get("name"), "group": b.get("group"), "proto": b.get("proto", "ir")}
                        for b in r.get("buttons", [])]}


class RemoteBody(core.Body):
    name: str


class LearnBody(core.Body):
    name: str
    group: Optional[str] = None
    icon: Optional[str] = None
    proto: str = "ir"
    timeout: int = 30


class NameBody(core.Body):
    name: Optional[str] = None
    group: Optional[str] = None
    icon: Optional[str] = None          # a key of the dashboard's icon set; empty = text
    part: Optional[bool] = None         # the button's group is part of this remote (a keypad, the inputs), not another device
    hide: Optional[list] = None         # groups of keys the remote's face does not show (HIDE_GROUPS)


class SendBody(core.Body):
    tx: Optional[str] = None


class ApplianceBody(core.Body):
    name: str
    remote: str
    blaster: str
    device: Optional[str] = None


class AppliancePatch(core.Body):
    name: Optional[str] = None
    remote: Optional[str] = None
    blaster: Optional[str] = None
    device: Optional[str] = None
    power: Optional[dict] = None        # {device, code}: the switch channel that powers it; {} = none


def _topics(x):
    """Every string of a device record (its topics, its channels' topics)."""
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _topics(v)
    elif isinstance(x, list):
        for v in x:
            yield from _topics(v)


def _blaster_users(node: str) -> list:
    """What sends through a blaster: tiles, toggles, and devices whose topics are the node's (an air conditioner
    whose frame its firmware builds). Its own card is no user."""
    rx = node == _rx_node()                           # what names no blaster sends through the one that learns
    out = [a.get("name") or a["id"] for a in APPLIANCES if a.get("blaster") == node or (rx and not a.get("blaster"))]
    out += [t.get("name") or t["id"] for t in TOGGLES if t.get("power") and (t["power"].get("via") == node or (rx and not t["power"].get("via")))]   # a probe-only toggle sends nothing
    own = _blaster_device(node)
    for did, d in list(core.DEVICES.items()):
        if did != own and d.get("transport") != TRANSPORT and any(t.startswith(node + "/") for t in _topics(
                {k: v for k, v in d.items() if k.endswith("topic") or k == "controls"})):
            out.append(d.get("name") or did)
    return out


@core.app.get("/api/ir/remotes")
def ir_remotes():
    return {"remotes": [_public_remote(r) for r in REMOTES],
            "appliances": [_public_appliance(a) for a in APPLIANCES],
            "blasters": [{"node": n["node"], "name": _blaster_name(n["node"]), "rx": bool(n.get("rx")),
                          "device": _card_of(n["node"]), "users": _blaster_users(n["node"]),
                          "online": _node_online(n["node"])} for n in BLASTERS],
            "learn_on": _rx_node(), "learning": _learn["node"] if time.time() <= _learn["until"] else None}


@core.app.post("/api/ir/remotes")
def ir_remote_add(body: RemoteBody):
    _writable()
    name = core.check_name(body.name, "remote", [x.get("name") for x in REMOTES], most=40)
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
    if not name and body.hide is None:
        raise HTTPException(400, "remote name required")
    if name:
        name = core.check_name(name, "remote", [x.get("name") for x in REMOTES if x is not r], most=40)
    if body.hide is not None and not (isinstance(body.hide, list) and all(g in HIDE_GROUPS for g in body.hide)):
        raise HTTPException(400, f"hide: groups of {', '.join(HIDE_GROUPS)}")
    with _lock:
        if name:
            r["name"] = name
        if body.hide is not None:
            hide = [g for g in HIDE_GROUPS if g in body.hide]
            if hide:
                r["hide"] = hide
            else:
                r.pop("hide", None)
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
    name = core.check_name(body.name, "device", _device_names(), most=60)
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
    if body.power:
        pd, pc = body.power.get("device"), body.power.get("code")
        if not isinstance(pd, str) or not isinstance(pc, str):
            raise HTTPException(400, "power: {device, code} as strings")
        if pd not in core.DEVICES or pc not in ((core.DEVICES[pd].get("_ctl") or {})):
            raise HTTPException(404, "power: no such device channel")
        if not _feed_ok(pd, pc, aid):
            raise HTTPException(400, "power: an on/off channel of another device (a switch, a socket)")
    name = (body.name or "").strip()
    if not (name or body.remote or body.blaster or body.device is not None or body.power is not None):
        raise HTTPException(400, "nothing to change")
    if name:
        name = core.check_name(name, "device", _device_names(but=(aid,)), most=60)
    legacy = body.power is not None and core.wiring_postponed()   # the wiring waits for its migration: the old place is in charge
    if body.power is not None and not legacy:          # the feed is a wiring entry only ("power" stays as it was)
        feed = [{"switch": {"dev": body.power["device"], "code": body.power["code"]}, "target": {"dev": aid}}] if body.power else []
        try:
            core.wiring_edit(lambda ws: [e for e in ws if e["target"].get("dev") != aid] + feed)
        except core.WiringBroken as e:
            raise HTTPException(409, f"the wiring cannot be changed: {e}")
        except ValueError as e:
            raise HTTPException(400, f"power: {e}")
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
        if legacy:
            if body.power:
                a["power"] = {"device": body.power["device"], "code": body.power["code"]}
            else:
                a.pop("power", None)
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
    try:                                               # the tile is deleted by its owner: its feed goes too
        core.wiring_edit(lambda ws: [e for e in ws if e["target"].get("dev") != aid], check=False)
    except core.WiringBroken as e:
        core.log.warning(f"[ir] {aid}: its wiring entry stays: {e}")
    return {"ok": True, "removed": aid, "broke": refs}


class BlasterBody(core.Body):
    node: Optional[str] = None          # POST: the ESPHome node's name (its topic prefix)
    name: Optional[str] = None
    rx: Optional[bool] = None           # this blaster learns (one blaster does: setting it moves it here)


NODE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")


def _listen(node: str):
    """A blaster's topics, also for one added at run time: subscribed now and on every reconnect."""
    if (node,) in [tuple(p) for p, _fn in core.MQTT_HANDLERS]:
        return
    topics = [f"{node}/status", f"{node}/rx/+"]
    core.MQTT_SUBSCRIPTIONS.extend(t for t in topics if t not in core.MQTT_SUBSCRIPTIONS)    # once each, however often it comes back
    core.MQTT_HANDLERS.append(((node,), _handle))
    cli = core.mqtt_client
    for t in topics if cli is not None else []:
        try:
            cli.subscribe(t)
        except Exception as e:                        # noqa: BLE001 - the next reconnect subscribes it
            core.log.warning(f"[ir] subscribe {t}: {e}")


def _unlisten(node: str):
    """A removed blaster's topics: its handler and last word gone; a topic is unsubscribed only when nothing else of the house reads it
    (the card that fronted the blaster reads <node>/status as its availability over the same client)."""
    used = {t for d in list(core.DEVICES.values()) for t in _topics({k: v for k, v in d.items() if k.endswith("topic") or k == "controls"})}
    mine = [f"{node}/status", f"{node}/rx/+"]
    topics = [f for f in mine if not any(_matches(f, t) for t in used)]
    core.MQTT_SUBSCRIPTIONS[:] = [t for t in core.MQTT_SUBSCRIPTIONS if t not in topics]
    core.MQTT_HANDLERS[:] = [h for h in core.MQTT_HANDLERS if tuple(h[0]) != (node,)]
    _node_status.pop(node, None)
    cli = core.mqtt_client
    for t in topics if cli is not None else []:
        try:
            cli.unsubscribe(t)
        except Exception as e:                        # noqa: BLE001
            core.log.warning(f"[ir] unsubscribe {t}: {e}")


def _matches(filt: str, topic: str) -> bool:
    """MQTT filter matching (+ one level, # the rest): does a subscription to filt deliver topic?"""
    f, t = filt.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(f) == len(t)


def _learns_here(b: dict):
    for x in BLASTERS:
        x["rx"] = x is b


@core.app.post("/api/ir/blasters")
def ir_blaster_add(body: BlasterBody):
    """A blaster of the house: an ESPHome node with the IR package, by its node name; it works at once."""
    node = (body.node or "").strip()
    if not NODE_RE.fullmatch(node) or node in ("home", "homeassistant", "panel") or node in core.MQTT_NAMESPACES:
        raise HTTPException(400, "node: the blaster's ESPHome name (small latin letters, digits, - or _)")
    if _blaster(node) is not None:
        raise HTTPException(409, "this blaster is in the list already")
    name = (body.name or "").strip()
    if name:                                           # a device's name: its card's, or its own device's
        name = core.check_name(name, "device", _device_names(but=(_blaster_device(node), BLASTER_PREFIX + node)), most=60)
    with _lock:
        b = {"node": node, "rx": False}
        BLASTERS.append(b)
        did = _blaster_device(node)
        if name and not did:
            b["name"] = name
        if body.rx or not any(x.get("rx") for x in BLASTERS):
            _learns_here(b)                           # the only one, or asked for: it learns
        _save()
        _sync()                                       # no device fronts it: it gets one
    if name and did:
        core.set_device_name(did, name)               # one name per blaster: a device fronts it, the typed name is the device's
    _listen(node)
    return {"node": node, "name": _blaster_name(node), "rx": b["rx"]}


@core.app.patch("/api/ir/blasters/{node}")
def ir_blaster_patch(node: str, body: BlasterBody):
    """The blaster's one name (its device's when a device fronts it, else its ir_blasters entry), and whether it learns."""
    b = _blaster(node)
    if b is None:
        raise HTTPException(404, "no such blaster")
    name = (body.name or "").strip()
    if not name and body.rx is None:
        raise HTTPException(400, "blaster name required")
    if name:
        name = core.check_name(name, "device", _device_names(but=(_blaster_device(node), BLASTER_PREFIX + node)), most=60)
    if body.rx is False and b.get("rx"):
        raise HTTPException(400, "one blaster learns: choose the one that learns instead")
    did = _blaster_device(node)
    with _lock:
        if body.rx:
            _learns_here(b)
        if name and did:
            b.pop("name", None)
        elif name:
            b["name"] = name
        _save()
        _sync()
    if name and did:
        core.set_device_name(did, name)
    return {"node": node, "name": _blaster_name(node), "rx": bool(b.get("rx"))}


@core.app.delete("/api/ir/blasters/{node}")
def ir_blaster_del(node: str):
    """Forget a blaster; refused (409, the users named) while a tile, a toggle or a device sends through it."""
    b = _blaster(node)
    if b is None:
        raise HTTPException(404, "no such blaster")
    if b.get("rx") and len(BLASTERS) > 1:
        raise HTTPException(409, "this blaster learns: choose another one to learn first")
    users = _blaster_users(node) + (["learning"] if time.time() <= _learn["until"] and _learn.get("node") == node else [])
    if users:
        raise HTTPException(409, "in use by: " + ", ".join(users))
    with _lock:
        BLASTERS.remove(b)
        _save()
        _sync()                                       # its device goes; its map icon stays, marked as gone
    _unlisten(node)
    return {"ok": True, "removed": node}


@core.app.post("/api/ir/remotes/{rid}/learn")
async def ir_learn(rid: str, body: LearnBody):
    """Arm the receiving blaster and wait for one frame (or the timeout): the UI says "press
    a button" for exactly as long as the node is listening."""
    _writable()
    r = _remote(rid)
    if not r:
        raise HTTPException(404, "no such remote")
    name = core.check_name(body.name, "button", [b.get("name") for b in r.get("buttons", [])], most=40)
    proto = (body.proto or "ir").lower()
    if proto not in PROTOS:
        raise HTTPException(400, "proto: ir | rf")
    node = _rx_node()
    if not node:
        raise HTTPException(400, "no blaster has a receiver - nothing to learn with")
    if time.time() <= _learn["until"]:
        raise HTTPException(409, "already learning a button")
    timeout = max(LEARN_MIN_S, min(int(body.timeout or 30), LEARN_MAX_S))
    _learn_ev.clear()
    sid = _learn["sid"] + 1
    _learn.update(node=node, proto=proto, until=time.time() + timeout, code=None, error=None, sid=sid)
    try:
        core.mqtt_pub(core.mqtt_client, f"{node}/learn/command", "ON")
    except Exception as e:                            # noqa: BLE001
        _learn.update(node=None, until=0.0)
        raise HTTPException(502, f"could not start learning on {node}: {e}")
    got = await run_in_threadpool(_learn_ev.wait, timeout)
    if _learn["sid"] != sid:                         # cancelled, and a new session armed since: leave it alone
        raise HTTPException(409, "learning cancelled")
    code, err = _learn["code"], _learn["error"]
    _learn.update(node=None, proto=None, until=0.0, code=None, error=None)
    try:
        core.mqtt_pub(core.mqtt_client, f"{node}/learn/command", "OFF")
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
            if _group_part(r, btn["group"]):
                btn["part"] = True                           # a new key of a keypad joins it
        if _icon_ok(body.icon):
            btn["icon"] = body.icon
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
            core.mqtt_pub(core.mqtt_client, f"{node}/learn/command", "OFF")
        except Exception as e:                        # noqa: BLE001
            core.log.warning(f"[ir] cancel: {e}")
    return {"ok": True, "was": node}


@core.use_route
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
    if not name and body.group is None and body.icon is None and body.part is None:
        raise HTTPException(400, "button name, group or icon required")
    if name:
        name = core.check_name(name, "button", [b.get("name") for b in r.get("buttons", []) if b is not btn], most=40)
    if body.group is not None and body.group.strip():
        body.group = core.check_name(body.group, "group", most=40)
    with _lock:
        if name:
            btn["name"] = name
        if body.group is not None:
            if body.group.strip():
                btn["group"] = body.group.strip()
            else:
                btn.pop("group", None)
            if _group_part(r, btn.get("group"), but=btn):           # moved: it takes its new group's kind, a group is never mixed
                btn["part"] = True
            else:
                btn.pop("part", None)
        if body.part is not None:
            if body.part and btn.get("group"):
                btn["part"] = True
            else:
                btn.pop("part", None)
        if body.icon is not None:
            if _icon_ok(body.icon):
                btn["icon"] = body.icon
            else:
                btn.pop("icon", None)
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
        for did in [k for k in list(core.DEVICES) if _own_blaster(k)]:
            _blaster_status(did)
        await asyncio.sleep(10)


def _on_rename(old: str, new: str):
    """An appliance shown on a renamed device's card stays on it, and keeps its power feed (the core moved the wiring's entry;
    "power" follows only while it is the one in charge)."""
    hit = [a for a in APPLIANCES if a.get("device") == old]
    for a in hit:
        a["device"] = new
    for a in APPLIANCES if not core.wiring_owns("ir") else ():
        if isinstance(a.get("power"), dict) and a["power"].get("device") == old:
            a["power"]["device"] = new
            hit.append(a)
    if hit:
        _sync()
        _save()


def _on_removed(dev: dict):
    """DEVICE_REMOVED: an appliance fed by the removed device forgets the feed's last word (a gone feed refuses nothing). The
    wiring entry stays until its owner removes it (a device may come back); the cards are rebuilt after the current removal."""
    with _lock:
        feeds = _feeds()
        hit = [a for a in APPLIANCES if (feeds.get(a["id"]) or {}).get("device") == dev.get("id")]
        for a in hit:
            _set_values(a["id"], {}, "power")
    if hit:
        threading.Timer(0, _sync_locked).start()


_FEEDS_SEEN: dict = {}


def _wiring_changed(_entries=None):
    """WIRING_CHANGED: an appliance whose feed moved forgets the old feed's word; the cards show the feeds as they are now."""
    global _FEEDS_SEEN
    with _lock:
        feeds = _feeds()
        hit = [a for a in APPLIANCES if feeds.get(a["id"]) != _FEEDS_SEEN.get(a["id"])]
        for a in hit:
            _set_values(a["id"], {}, "power")
        _FEEDS_SEEN = feeds
    if hit:
        threading.Timer(0, _sync_locked).start()


def _wiring_old() -> list:
    """WIRING_MIGRATORS: the feeds the appliances kept before the wiring list."""
    return [{"switch": {"dev": a["power"].get("device"), "code": a["power"].get("code") or ""}, "target": {"dev": a["id"]}}
            for a in APPLIANCES if isinstance(a.get("power"), dict) and a["power"].get("device")]


def _sync_locked():
    with _lock:
        _sync()


def _own_name(dev_id: str, name: str) -> bool:
    """NAME_OWNERS: an appliance's or a toggle's name lives in its record here, nowhere else."""
    rec = _appliance(dev_id) or next((t for t in TOGGLES if t["id"] == dev_id), None)
    if rec is None and _own_blaster(dev_id):
        rec = _blaster(dev_id[len(BLASTER_PREFIX):])  # a blaster's device: the name is the blaster's
    if rec is None:
        return False
    with _lock:
        rec["name"] = name
        _save()
        _sync()
    return True


def _one_name_each():
    """At start: a rename saved in the core's overrides moves into the record, and a blaster a device fronts
    keeps no name of its own (the device's is the one), so no two stored names can disagree."""
    moved = False
    for rec in APPLIANCES + TOGGLES:
        name = core.take_name_override(rec["id"])
        if name:
            rec["name"], moved = name, True
    for b in BLASTERS:
        did = _blaster_device(b.get("node"))
        if did and "name" in b:
            d = core.DEVICES.get(did) or {}
            if b["name"] and (not d.get("name") or d.get("name") == did):   # the device has no name of its own: it takes this one
                core.set_device_name(did, b["name"])
            b.pop("name")
            moved = True
    if moved:
        _save()


def _health() -> list:
    """A tile that extends a device without a face of its own (a blaster card, a switch: set before the rule): it works, it is said."""
    rows = [core.health_row(f"ir:blasterid:{did}", "warn", "IR", "{0}: no device for this blaster, the id is taken: {1}",
                            "Remove or rename that device", [did, why]) for did, why in list(_ID_TAKEN.items())]
    for a in APPLIANCES:
        d = core.DEVICES.get(a.get("device") or "")
        if d is not None and (d.get("category") not in ("tv", "climate") or d.get("ir_blaster") or d.get("ir_appliance")):
            rows.append(core.health_row(f"ir:host:{a['id']}", "warn", "IR", "{0}: its host {1} is not a device with a face of its own",
                                        "IR tile window -> Extends the card: pick a TV or an air conditioner, or none",
                                        [a.get("name") or a["id"], d.get("name") or d["id"]]))
    return rows


core.ON_RENAME.append(_on_rename)
core.MERGE_PLAN.append(_merge_plan)
core.DEVICE_MERGED.append(_merged)
core.HEALTH_CHECKS.append(_health)
core.NAME_OWNERS.append(_own_name)
core.ON_EVENT.append(_on_event)
core.DEVICE_REMOVED.append(_on_removed)
core.WIRING_MIGRATORS.append({"name": "ir", "files": [core.plugin_data("ir") / "settings.json"], "entries": _wiring_old})   # its sections' file
core.WIRING_CHANGED.append(_wiring_changed)
_FEEDS_SEEN = _feeds()                                 # what the cards were built from at load
core.SETTINGS_SECTIONS["ir_appliances"] = lambda: APPLIANCES
core.SETTINGS_SECTIONS["ir_blasters"] = lambda: BLASTERS
core.SETTINGS_SECTIONS["ir_toggles"] = lambda: TOGGLES
core.TRANSPORTS[TRANSPORT] = _transport
core.ASYNC_CONTROL[TRANSPORT] = _async_transport
core.CONTROL_KINDS["ir_press"] = _press
core.DEVICE_FIELDS.extend(["ir_appliance", "ir_remote", "ir_via", "ir_card", "ir_blaster", "ir_hide", "ir_power"])
for _n in BLASTERS:
    if isinstance(_n.get("node"), str) and _n["node"]:
        _listen(_n["node"])
core.STARTUP_TASKS.append(_status_loop)
core.STARTUP_TASKS.append(_probe_loop)
_one_name_each()
_sync()
