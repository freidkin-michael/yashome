"""Pairing a fwgen device, the house side (docs/pairing.md): a broker account of its own with a narrow ACL, made and revoked
at run time through the broker's dynamic security, and the device's own OTA password kept by the house."""
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time

from fastapi import HTTPException, Request

import app as core

PLUGIN = {"name": "Device pairing", "version": "0.1.0", "icon": "link"}

NODE_RE = re.compile(r"[a-z][a-z0-9_-]{0,27}")          # "dev-" + node must fit the 32 characters of a broker user
ACCOUNTS = core.plugin_state_file("pair", "mqtt_accounts.json")      # {user, password, topics, read}: what mkpasswd writes at a start
MOVED = core.plugin_state_file("pair", "moved.json")                 # node -> when the person said it moved to another house
PAIRINGS = core.plugin_state_file("pair", "pairings.json")           # node -> {user, ota_password, house_id, state, made}
READABLE = ("home/clock/#", "home/weather")             # the only house topics a device account may be given to read
UNPAIR_WAIT_S = 3.0
_lock = threading.RLock()


def _load(path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(path, data) -> None:
    core.atomic_json_dump(path, data, ensure_ascii=False)
    os.chmod(path, 0o600)


def _acls(a: dict) -> list:
    rw = ("publishClientSend", "publishClientReceive", "subscribePattern", "unsubscribePattern")
    ro = ("publishClientReceive", "subscribePattern", "unsubscribePattern")
    return ([{"acltype": k, "topic": t, "allow": True, "priority": 0} for t in a["topics"] for k in rw]
            + [{"acltype": k, "topic": t, "allow": True, "priority": 0} for t in a["read"] for k in ro])


def _broker(commands: list, wait: float = 5.0) -> bool:
    """Tell the running broker (dynamic security) and wait for ITS answers; a delete of something gone counts as done."""
    if core.paho is None:
        return False
    ids = [secrets.token_hex(6) for _ in commands]
    sent = [{**c, "correlationData": i} for c, i in zip(commands, ids)]
    box, got = queue.Queue(), {}
    cli = core.paho.Client(core.paho.CallbackAPIVersion.VERSION2, client_id=f"pair-acct-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: box.put(m.payload)
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
    except Exception as e:                                  # noqa: BLE001  no broker, no answer
        core.log.error(f"[pair] broker account change not confirmed: {type(e).__name__}: {e}")
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
        if err and not (c["command"].startswith("delete") and "not found" in err):
            bad.append((c["command"], err))
    if bad:
        core.log.error(f"[pair] broker account change refused: {bad}")
    return not bad


def _persist() -> None:
    """The broker's file for its next start (mkpasswd --accounts-only)."""
    def run():
        r = subprocess.run([sys.executable, str(core.CHECKOUT / "tools" / "mkpasswd.py"), "--accounts-only"], cwd=str(core.CHECKOUT),
                           capture_output=True, text=True, timeout=120)
        if r.returncode:
            core.log.error(f"[pair] broker accounts not written: {(r.stderr or r.stdout).strip()[-300:]}")
    threading.Thread(target=run, daemon=True, name="pair-mkpasswd").start()


def _levels(d: dict) -> set:
    """The names a house device answers to: its id, its esphome_node and the first level of its topics."""
    tops = [d.get("availability_topic")] + [c.get(k) for c in d.get("controls", []) for k in ("state_topic", "command_topic")]
    if isinstance(d.get("state_request"), dict):
        tops.append(d["state_request"].get("topic"))
    return {t.split("/", 1)[0] for t in tops if isinstance(t, str) and "/" in t} | {str(d.get("esphome_node") or ""), str(d.get("id") or "")}


def _device_for(node: str):
    return next((d.get("id") for d in list(core.DEVICES.values()) if node in _levels(d)), None)


RESERVED = {"home", "homeassistant", "zigbee2mqtt", "panel", "provision"}


def _taken(node: str) -> str:
    """Why this name may not be a new device's: it is a namespace of the house or of a plug-in, or the first topic level
    (or the id) of a device the house already has; '' when it is free."""
    names = set(RESERVED) | set(core.MQTT_NAMESPACES) | {t.split("/", 1)[0] for t in core.MQTT_SUBSCRIPTIONS}
    names |= {str(p[0]) for p, _fn in core.MQTT_HANDLERS if p} | set(core.NODE_SUBTOPICS)
    if node in names:
        return f"{node!r} is a topic of the house or of a plug-in"
    for d in list(core.DEVICES.values()):
        if node in _levels(d):
            return f"{node!r} is the name of a device the house already has"
    return ""


PANEL_READS = ["home/state/#", "home/weather", "home/clock/#"]      # what a panel shows: the same as the accounts the panels plug-in makes


def _panel_exists(node: str) -> bool:
    lay = _load(core.plugin_data("panels") / "layouts.json")
    return isinstance(lay, dict) and isinstance(lay.get(node), dict)


def make_account(node: str, reads=(), kind: str = "device") -> tuple:
    """The device's own broker account dev-<node>: read and write <node>/# and read what it asks of READABLE; (user, password).
    Made once and confirmed by the broker; a second call for the same node gives the same account."""
    if not NODE_RE.fullmatch(node or ""):
        raise HTTPException(400, "node: a small latin letter first, then letters, digits, - or _, at most 28")
    if kind not in ("device", "panel"):
        raise HTTPException(400, "kind: device or panel")
    bad = [r for r in reads if r not in READABLE]
    if bad and kind == "device":
        raise HTTPException(400, f"a device account may read only {', '.join(READABLE)}")
    if kind == "panel" and not _panel_exists(node):
        raise HTTPException(409, "a panel account is for a panel the house has: there is no layout of this name")
    user = f"dev-{node}"
    with _lock:
        lst = _load(ACCOUNTS) or []
        a = next((x for x in lst if x.get("user") == user), None)
        if a is not None:                                   # the same device paired again: its own record
            if a.get("kind", "device") != kind:
                raise HTTPException(409, f"this node was paired as a {a.get('kind', 'device')}")
            return user, a["password"]
        why = _taken(node)
        if why:
            raise HTTPException(409, f"a device cannot be paired under that name: {why}")
        a = {"user": user, "password": secrets.token_urlsafe(24), "kind": kind,
             "topics": [f"panel/{node}/#"] if kind == "panel" else [f"{node}/#"], "read": PANEL_READS if kind == "panel" else list(reads)}
        _save(ACCOUNTS, lst + [a])
        try:
            ok = _broker([{"command": "deleteClient", "username": user}, {"command": "deleteRole", "rolename": user},
                          {"command": "createRole", "rolename": user, "acls": _acls(a)},
                          {"command": "createClient", "username": user, "password": a["password"], "roles": [{"rolename": user}]}])
            if not ok:
                _save(ACCOUNTS, lst)                         # the broker did not take it: no record of an account it lacks
                raise HTTPException(503, "the broker did not take the device's account: try again, or restart it once")
            core.log.warning(f"[pair] broker account {user} made")
        finally:
            _persist()
    return user, a["password"]


def prepare(node: str, house_id: str = "", reads=(), kind: str = "device") -> dict:
    """What the pair request carries for this node: its account and its own OTA password (the house keeps the OTA password)."""
    user, password = make_account(node, reads, kind)
    with _lock:
        pairs = _load(PAIRINGS) or {}
        rec = pairs.get(node) or {"user": user, "ota_password": secrets.token_urlsafe(24), "state": "new", "made": time.time()}
        rec["user"], rec["house_id"] = user, house_id or rec.get("house_id", "")
        pairs[node] = rec
        _save(PAIRINGS, pairs)
        gone = _load(MOVED) or {}
        if gone.pop(node, None) is not None:                # paired again: no longer "moved"
            _save(MOVED, gone)
    return {"node": node, "mqtt_user": user, "mqtt_password": password, "ota_password": rec["ota_password"], "house_id": rec["house_id"]}


def ota_password(node: str):
    """The OTA password the house keeps for a paired node (None when it never paired)."""
    return ((_load(PAIRINGS) or {}).get(node) or {}).get("ota_password")


def _retained_under(prefix: str, listen: float = 2.5, extra: tuple = ()) -> list:
    """The topics under prefix (and the extra filters) that hold a retained value."""
    return core.retained_topics((prefix + "#",) + tuple(extra), listen)


def clear_retained(node: str, kind: str = "device") -> list:
    """What a gone device left on the broker: every retained topic under its own prefix is emptied (the status, the
    pairing state, the description). A panel keeps the layout the house publishes for it; other nodes are not touched."""
    prefix = f"panel/{node}/" if kind == "panel" else f"{node}/"
    ha = () if kind == "panel" else (f"homeassistant/+/{node}/+/config",)          # the HA discovery configs of the node
    cleared = []
    for t in _retained_under(prefix, extra=ha):
        parts = t.split("/")
        own_ha = len(parts) == 5 and parts[0] == "homeassistant" and parts[2] == node and parts[4] == "config" and bool(ha)
        if not (t.startswith(prefix) or own_ha) or (kind == "panel" and t == prefix + "layout"):
            continue
        try:
            core.mqtt_pub(core.mqtt_client, t, None, retain=True)
            cleared.append(t)
        except Exception as e:                              # noqa: BLE001
            core.log.warning(f"[pair] {node}: could not clear {t}: {e}")
    return cleared


def revoke(node: str, unpair: bool = True) -> bool:
    """The device leaves: it is told to wipe its record (<node>/yash/unpair, when it is reachable), then its broker account, its file
    entries and the OTA password go. The account goes even when the device never answered."""
    if not NODE_RE.fullmatch(node or ""):
        return False
    user = f"dev-{node}"
    with _lock:
        pairs = _load(PAIRINGS) or {}
        accounts = _load(ACCOUNTS) or []
        if node not in pairs and not any(x.get("user") == user for x in accounts):
            return False
        kind = next((x.get("kind", "device") for x in accounts if x.get("user") == user), "device")
    if unpair:
        try:
            core.mqtt_pub(core.mqtt_client, f"{node}/yash/unpair", "1")
            time.sleep(UNPAIR_WAIT_S)
        except Exception as e:                              # noqa: BLE001  the broker may be down: the account goes anyway
            core.log.warning(f"[pair] {node}: could not send unpair: {e}")
    with _lock:
        _save(ACCOUNTS, [x for x in (_load(ACCOUNTS) or []) if x.get("user") != user])
        pairs = _load(PAIRINGS) or {}
        pairs.pop(node, None)
        _save(PAIRINGS, pairs)
        if not _broker([{"command": "deleteClient", "username": user}, {"command": "deleteRole", "rolename": user}]):
            core.log.warning(f"[pair] {user}: the running broker keeps it until its next start (the file has it no more)")
        _persist()
    if unpair:
        clear_retained(node, kind)
    return True


def on_removed(dev: dict) -> None:
    """A generic-MQTT device was removed from the house: a paired one is revoked."""
    pairs = _load(PAIRINGS) or {}
    nodes = [dev.get("esphome_node")] + [t.split("/", 1)[0] for c in dev.get("controls", []) for t in (c.get("state_topic"),) if isinstance(t, str) and "/" in t]
    for node in dict.fromkeys(n for n in nodes if n):
        if node in pairs:
            revoke(node)


CODE_RE = re.compile(r"[A-Z2-7]{8}")
SETUP = "-setup"
HOST_WAIT_S = 150.0


def _host_call(body: dict, timeout: float) -> dict:
    """The provision plug-in's host relay (its socket /run/yashome/provision.sock): one JSON POST to /provision."""
    if not core.hostsock.there("provision"):
        raise RuntimeError("the host relay is not installed (sudo sh tools/host-sockets.sh): it is needed to reach a setup AP")
    try:
        status, data = core.hostsock.http("provision", "POST", "/provision", body, timeout)
    except (OSError, ValueError) as e:
        raise RuntimeError(str(e)) from None
    return data if status < 400 else {**data, "ok": False}


def pair_form(rec: dict, code: str, mqtt_host: str, mqtt_port) -> dict:
    """The fields of the pair request (docs/pairing.md); the Wi-Fi is the house's own."""
    ssid, password = os.environ.get("HOUSE_WIFI_SSID") or "", os.environ.get("HOUSE_WIFI_PASSWORD") or ""
    if not ssid:
        raise HTTPException(503, "HOUSE_WIFI_SSID is not set in .env")
    return {"code": code, "house_id": rec["house_id"], "ssid": ssid, "wifi_password": password, "mqtt_host": mqtt_host,
            "mqtt_port": str(mqtt_port), "mqtt_user": rec["mqtt_user"], "mqtt_password": rec["mqtt_password"], "ota_password": rec["ota_password"]}


class StartBody(core.Body):
    ap: str                      # <node>-setup
    code: str                    # the pairing code: the AP's key and the proof of owning the device
    house_id: str = ""
    bssid: str = ""
    reads: list = []
    kind: str = "device"         # "panel": an existing panel of the house (its account reads the house state)


@core.app.post("/api/pair/start")
def pair_start(body: StartBody, request: Request):
    """Pair the device behind its setup AP: its account and OTA password are made, the host relay joins the AP and posts the
    request; a failed request leaves no account behind."""
    core.house_token_only(request)
    node = body.ap[:-len(SETUP)] if body.ap.endswith(SETUP) else ""
    code = body.code.strip().upper()
    if not NODE_RE.fullmatch(node):
        raise HTTPException(400, "ap: the device's <name>-setup access point")
    if not CODE_RE.fullmatch(code):
        raise HTTPException(400, "code: 8 characters, letters A-Z and digits 2-7")
    host, _warning = core.node_broker_host(request)
    existed = ota_password(node) is not None
    rec = prepare(node, (body.house_id or "yashome")[:23], tuple(body.reads), body.kind)
    form = pair_form(rec, code, host, core.node_broker_port())
    relay = {"ap": body.ap, "ap_password": code, "form": form}
    if body.bssid:
        relay["bssid"] = body.bssid
    try:
        out = _host_call(relay, HOST_WAIT_S)
    except RuntimeError as e:
        if not existed:
            revoke(node, unpair=False)
        raise HTTPException(502, f"host relay: {e}")
    if out.get("ok") is not True:
        if not existed:
            revoke(node, unpair=False)
        steps = "; ".join(str(x) for x in out.get("steps") or [])[:500]        # status lines of the relay: no secrets in them
        raise HTTPException(409, str(out.get("error") or "the device refused the pairing") + (f" [{steps}]" if steps else ""))
    with _lock:
        pairs = _load(PAIRINGS) or {}
        pairs[node] = {**pairs.get(node, {}), "state": "sent", "sent": time.time()}
        _save(PAIRINGS, pairs)
    threading.Thread(target=_finish, args=(node,), daemon=True, name=f"pair-{node}").start()
    return {"ok": True, "node": (out.get("reply") or {}).get("node") or node, "hash": (out.get("reply") or {}).get("hash"),
            "steps": [str(x) for x in out.get("steps") or []][:20]}


def _finish(node: str) -> None:
    """After the request: the device boots into the house, says it is online and describes itself; then its card is made."""
    is_panel = any(a.get("kind") == "panel" for a in _load(ACCOUNTS) or [] if a.get("user") == f"dev-{node}")
    topic = f"panel/{node}/status" if is_panel else f"{node}/status"          # a panel keeps its own topic prefix
    end = time.time() + 150
    while time.time() < end:
        st = core.retained_values([topic], timeout=3.0).get(topic)
        if st == "online":
            break
        time.sleep(3)
    else:
        core.log.warning(f"[pair] {node}: not online 150 s after the pair request")
        return
    with _lock:
        pairs = _load(PAIRINGS) or {}
        if node in pairs:
            pairs[node].update(state="paired", paired=time.time())
            _save(PAIRINGS, pairs)
    if is_panel:                                             # a panel is not a card: the panels plug-in shows it
        return
    for _ in range(20):                                      # its description lands within seconds of the connection
        if _description(node):
            try:
                make_card(node)
            except Exception as e:                           # noqa: BLE001
                core.log.warning(f"[pair] {node}: no card: {e}")
            return
        time.sleep(3)


def _resume() -> None:
    """A restart between the pair request and the first connection must not strand the record at "sent"."""
    for node, rec in list((_load(PAIRINGS) or {}).items()):
        if rec.get("state") == "sent":
            threading.Thread(target=_finish, args=(node,), daemon=True, name=f"pair-{node}").start()


def _description(node: str):
    try:
        return json.loads((core.plugin_data("fwgen") / "devices" / f"{node}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _code_of(topic: str, node: str) -> str:
    parts = topic.split("/")
    return re.sub(r"[^a-z0-9_]", "_", (parts[-2] if len(parts) >= 3 else parts[-1]).lower())[:48] or "x"


def card_from_description(node: str, rec: dict) -> dict:
    """The house's card of a device that describes itself (no discovery): its entities as channels (sensor, binary_sensor,
    text_sensor, switch; others are left for later), the topics expanded with the node's prefix."""
    d = rec["description"]
    dev = d.get("device") or {}
    controls, skipped = [], []
    for e in d.get("entities") or []:
        t = e.get("state_topic")
        if not isinstance(t, str):
            continue
        t = node + t[1:] if t.startswith("~/") else t
        code, label, dom = _code_of(t, node), str(e.get("name") or "")[:60] or _code_of(t, node), e.get("domain")
        adv = e.get("role") in ("rssi", "uptime", "wifi_signal", "ip_address", "ssid", "mac_address")
        if dom == "sensor":
            c = {"kind": "sensor", "code": code, "label": label, "ro": True, "vtype": "num", "state_topic": t}
            if e.get("unit"):
                c["unit"] = e["unit"]
        elif dom == "binary_sensor":
            c = {"kind": "sensor", "code": code, "label": label, "ro": True, "vtype": "bool", "payload_on": "ON", "state_topic": t}
        elif dom == "text_sensor":
            c = {"kind": "sensor_text", "code": code, "label": label, "ro": True, "state_topic": t}
        elif dom == "switch":
            c = {"kind": "switch", "code": code, "label": label, "state_topic": t, "command_topic": t.rsplit("/", 1)[0] + "/command",
                 "payload_on": "ON", "payload_off": "OFF"}
        else:
            skipped.append(f"{dom}:{code}")
            continue
        if adv:
            c["advanced"] = True
        controls.append(c)
    if not controls:
        raise ValueError("the description has no entity the house can show yet")
    has_switch = any(c["kind"] == "switch" for c in controls)
    card = {"id": node, "name": str(dev.get("friendly") or node)[:60], "transport": "mqtt", "category": "switch" if has_switch else "sensor",
            "esphome_node": node, "model": str((dev.get("board") or {}).get("id") or dev.get("chip") or "")[:40],
            "availability_topic": f"{node}/status", "payload_online": "online", "payload_offline": "offline", "controls": controls}
    card["_skipped"] = skipped
    return card


def make_card(node: str) -> dict:
    """Make the house's card of a paired device from its description (the person confirms nothing here: it is the device's own list)."""
    rec = _description(node)
    if not rec:
        raise ValueError("no description of this device yet")
    if node not in (_load(PAIRINGS) or {}):
        raise ValueError("this node is not paired through the house")
    have = core.DEVICES.get(node) or core.DEVICES.get(_device_for(node) or "")
    if have:                                                 # a card made through HA discovery stays: the window offers the gap
        return have
    card = card_from_description(node, rec)
    skipped = card.pop("_skipped")
    core.mqtt_devices_upsert(card)
    core.register_mqtt_device(card)
    core.mqtt_resubscribe_generic()
    core.log.info(f"[pair] card {node}: {len(card['controls'])} channels" + (f", not shown yet: {', '.join(skipped)}" if skipped else ""))
    return card


@core.app.post("/api/pair/{node}/card")
def pair_card(node: str, request: Request):
    core.house_token_only(request)
    try:
        card = make_card(node)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "id": card["id"], "channels": len(card.get("controls", []))}


def mark_moved(node: str) -> bool:
    """The person says the device now lives in another house: it is unpaired and revoked, its card stays (offline) and says so."""
    if node not in (_load(PAIRINGS) or {}):
        return False
    device = _device_for(node)
    revoke(node)
    with _lock:
        gone = _load(MOVED) or {}
        gone[node] = {"ts": time.time(), "device": device}
        _save(MOVED, gone)
    return True


@core.app.post("/api/pair/{node}/moved")
def pair_moved(node: str, request: Request):
    core.house_token_only(request)
    if not mark_moved(node):
        raise HTTPException(404, "no such pairing")
    return {"ok": True}


@core.app.get("/api/pair")
def pair_list(request: Request):
    core.house_token_only(request)
    return {"paired": [{"node": n, "user": r.get("user"), "state": r.get("state"), "house_id": r.get("house_id"), "device": _device_for(n)}
                       for n, r in sorted((_load(PAIRINGS) or {}).items())],
            "moved": [{"node": n, "ts": r.get("ts"), "device": r.get("device") or _device_for(n)} for n, r in sorted((_load(MOVED) or {}).items())]}


@core.app.delete("/api/pair/{node}")
def pair_revoke(node: str, request: Request):
    core.house_token_only(request)
    if not revoke(node):
        raise HTTPException(404, "no such pairing")
    return {"ok": True}


def discovery_ignore(names: set) -> bool:
    """A node paired through the house is not onboarded through HA discovery as well."""
    pairs = _load(PAIRINGS) or {}
    return any(n in pairs for n in names)


core.DEVICE_REMOVED.append(on_removed)
core.DISCOVERY_IGNORE.append(discovery_ignore)
core.STARTUP_TASKS.append(_resume)
