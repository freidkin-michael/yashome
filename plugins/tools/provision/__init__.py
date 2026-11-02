"""provision: recovery of ESPHome nodes that lost their Wi-Fi and opened "<name>-setup" (portal 192.168.4.1).

A relay hands the node the Wi-Fi: a NEIGHBOUR node with firmware/provision.yaml (posts its OWN current
credentials), or the HOST helper (host/esp-provision.py, a spare Wi-Fi card) as the fallback. The bus is
provision/scan, provision/seen/<node>, provision/<node>/do, provision/<node>/result (README.md); neither the
house Wi-Fi password nor the shared setup-AP key goes on MQTT: only the host helper gets them, over HTTP."""
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from typing import Optional

from fastapi import HTTPException, Request

import app as core

PLUGIN = {"name": "ESP recovery via the setup AP", "version": "1.0.0", "icon": "wifi"}

SETUP_SUFFIX = "-setup"
OPEN_HOUSE = "an open -setup AP: not one of the house's nodes"
SERVER = "server"
RESERVED = {SERVER, "auto", "seen", "scan"}
FRESH_S = 90
SCAN_WAIT_S = 7.0
SCAN_POLL_S, RELAY_POLL_S, BACK_POLL_S = 0.25, 0.5, 3.0       # how often a wait looks again
RELAY_WAIT_S = 110.0
HOST_WAIT_S = 150.0
CONFIRM_S = 60.0
MAX_NODE_RELAYS = 2
GONE_AFTER = 3
# the longest a Send Wi-Fi can take: a scan (host leg included), every relay timing out, the confirmation
WORST_S = int(SCAN_WAIT_S + 30 + MAX_NODE_RELAYS * RELAY_WAIT_S + HOST_WAIT_S + CONFIRM_S)
NODE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
FIELD_RE = re.compile(r"[A-Za-z0-9_]{1,32}")
PATH_RE = re.compile(r"/[A-Za-z0-9/_.?=&-]{0,95}")
BSSID_RE = re.compile(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}")
PORTAL_NET = ipaddress.ip_network("192.168.4.0/24")
# Espressif's OUIs (a setup AP of an ESP is one of these); another vendor's MAC may be a fake AP
ESPRESSIF_OUI = frozenset("""
24:0A:C4 24:6F:28 30:AE:A4 3C:71:BF 4C:11:AE 7C:9E:BD 84:CC:A8 8C:AA:B5 A4:CF:12 AC:67:B2 B4:E6:2D C4:4F:33
CC:50:E3 D8:A0:1D E8:DB:84 EC:FA:BC 34:85:18 48:3F:DA 58:BF:25 60:01:94 68:C6:3A 98:F4:AB BC:DD:C2 08:3A:F2
10:52:1C 40:22:D8 78:21:84 94:B9:7E C8:2B:96 F4:CF:A2""".split())

# Portal contract per firmware family; only ESPHome is proven, the other two are documented shapes.
PORTAL_PRESETS = {
    "esphome": {"label": "ESPHome", "host": "192.168.4.1", "method": "POST", "path": "/wifisave",
                "ssid_field": "ssid", "psk_field": "psk", "tested": True},
    "openbeken": {"label": "OpenBeken / LibreTiny (untested)", "host": "192.168.4.1", "method": "GET",
                  "path": "/cfg_wifi_set", "ssid_field": "ssid", "psk_field": "pass", "tested": False},
    "tasmota": {"label": "Tasmota (untested)", "host": "192.168.4.1", "method": "GET",
                "path": "/wi?save=1", "ssid_field": "s1", "psk_field": "p1", "tested": False},
}

_lock = threading.Lock()
_relays: dict = {}
_results: dict = {}
_host_seen: dict = {"aps": [], "ts": 0.0, "error": None}
_last_scan = {"ts": 0.0}
_slots = threading.BoundedSemaphore(2)
_busy: set = set()


def _host_there() -> bool:
    """The host relay is installed on this house's host (tools/host-sockets.sh): its socket file is there."""
    return core.hostsock.there("provision")


def _http(path: str, body: Optional[dict], timeout: float) -> dict:
    """One JSON request to the host relay over its socket; it answers its errors (4xx, 503) as JSON too."""
    status, data = core.hostsock.http("provision", "POST" if body is not None else "GET", path, body, timeout)
    return data if status < 400 else {**data, "ok": False}


def _host_call(path: str, body: Optional[dict] = None, timeout: float = 15.0) -> dict:
    """Call the host relay; raises RuntimeError when it is not installed or does not answer."""
    if not _host_there():
        raise RuntimeError("the host relay is not installed (tools/host-sockets.sh)")
    try:
        return _http(path, body, timeout)
    except (OSError, ValueError) as e:
        raise RuntimeError(str(e)) from None


def _node_ok(node: str) -> bool:
    return bool(NODE_RE.fullmatch(node)) and node not in RESERVED


def _bssid(v) -> Optional[str]:
    v = v.strip().upper() if isinstance(v, str) else ""
    return v if BSSID_RE.fullmatch(v) else None


def _ap_entry(a) -> Optional[dict]:
    if not (isinstance(a, dict) and isinstance(a.get("ssid"), str)):
        return None
    return {**a, "bssid": _bssid(a.get("bssid"))}


def _on_message(parts, payload, retain):
    """provision/seen/<node> (a scan report, retained: a replayed one only says the node exists, its
    air is old) and provision/<node>/result. An empty report removes the node."""
    raw = payload.decode("utf-8", "replace").strip() if isinstance(payload, (bytes, bytearray)) else str(payload or "")
    try:
        data = json.loads(raw) if raw.startswith("{") else {}
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    now = time.time()
    if len(parts) == 3 and parts[1] == "seen":
        node = parts[2]
        if not _node_ok(node):
            return
        with _lock:
            if not raw:
                _relays.pop(node, None)
                return
            r = _relays.setdefault(node, {"known": 0.0, "ts": 0.0, "aps": [], "missed": 0})
            if not retain:
                aps = data.get("aps") if isinstance(data.get("aps"), list) else []
                r.update(known=now, ts=now, missed=0, aps=[e for e in map(_ap_entry, aps[:16]) if e])
    elif len(parts) == 3 and parts[2] == "result" and not retain and _node_ok(parts[1]):
        res = {"ap": data.get("ap"), "id": str(data.get("id") or ""), "ok": data.get("ok") is True,
               "busy": data.get("busy") is True, "detail": str(data.get("detail") or raw)[:200], "ts": now}
        with _lock:
            _results[parts[1]] = res
        core.log.info(f"[provision] {parts[1]}: {res['detail']}")


def _pct_to_dbm(pct) -> int:
    """nmcli gives 0..100, nodes give dBm; roughly 2 * (dBm + 100) - an estimate, used only to rank."""
    try:
        return int(pct) // 2 - 100
    except (TypeError, ValueError):
        return -100


def _scan(wait: "float | None" = None) -> dict:
    """Ask every node and the host at once; collect what they hear, strongest relay first per AP. The
    report carries its own start ("scan_at"): a concurrent scan does not move what this one counts."""
    wait = SCAN_WAIT_S if wait is None else wait
    start = time.time()
    url = _host_there()
    host_box: dict = {}

    def host_leg():
        try:
            data = _host_call("/scan", timeout=wait + 25)
        except RuntimeError as e:
            host_box["error"] = str(e)
            return
        if data.get("ok") is False:
            host_box["error"] = str(data.get("error") or "the host helper failed")[:200]
        else:
            host_box["aps"] = [e for e in map(_ap_entry, data.get("aps") or []) if e]

    t = threading.Thread(target=host_leg, daemon=True) if url else None
    if t:
        t.start()
    mqtt_error = None
    try:
        if core.mqtt_client is None:
            raise RuntimeError("MQTT unavailable")
        core.mqtt_pub(core.mqtt_client, "provision/scan", "1")
    except Exception as e:
        mqtt_error = str(e)
    while not mqtt_error and time.time() < start + wait:
        with _lock:   # a node that missed GONE_AFTER scans in a row is not waited for
            live = [r for r in _relays.values() if r["missed"] < GONE_AFTER]
            if live and all(r["ts"] >= start for r in live):
                break
        time.sleep(SCAN_POLL_S)
    with _lock:
        for r in _relays.values():
            if r["ts"] < start and not mqtt_error:
                r["missed"] += 1
    if t:
        t.join(timeout=wait + 30)
        _host_seen.update(aps=host_box.get("aps") or [], ts=time.time(), error=host_box.get("error"))
    _last_scan["ts"] = max(_last_scan["ts"], start)
    return _report(start, bool(url), mqtt_error)


def _observations(since: float) -> dict:
    """ssid -> {"open", "secured", "setup", "bssids", "candidates": [{via, label, rssi, bssid}]} from
    reports since `since`."""
    out: dict = {}
    with _lock:
        nodes = [(n, list(r["aps"])) for n, r in _relays.items() if r["ts"] >= since]
    host = list(_host_seen["aps"]) if _host_seen["ts"] >= since else []
    for via, aps in nodes + [(SERVER, host)]:
        for ap in aps:
            ssid = ap.get("ssid")
            if not isinstance(ssid, str) or not ssid:
                continue
            e = out.setdefault(ssid, {"open": False, "secured": False, "setup": ssid.endswith(SETUP_SUFFIX),
                                      "bssids": [], "candidates": []})
            e["open" if ap.get("open") else "secured"] = True
            if ap.get("bssid") and ap["bssid"] not in e["bssids"]:
                e["bssids"].append(ap["bssid"])
            rssi = _pct_to_dbm(ap.get("signal")) if via == SERVER else ap.get("rssi")
            e["candidates"].append({"via": via, "label": "Host" if via == SERVER else via,
                                    "rssi": rssi if isinstance(rssi, int) else None, "bssid": ap.get("bssid")})
    for e in out.values():
        e["candidates"].sort(key=lambda c: (c["via"] == SERVER, -(c["rssi"] if c["rssi"] is not None else -100)))
    return out


def _espressif(bssid: Optional[str]) -> Optional[bool]:
    return None if not bssid else bssid[:8] in ESPRESSIF_OUI


def _known_node(name: str) -> bool:
    """A relay node, or a device the house knows by this ESPHome node name or id."""
    if not name:
        return False
    with _lock:
        if name in _relays:
            return True
    return any(isinstance(d, dict) and name in (d.get("esphome_node"), d.get("id"))
               for d in list(core.DEVICES.values()))


def _open_refused(ap: str) -> bool:
    """Every house node secures its setup AP: an open "*-setup" one is a stranger when the house has
    its shared key, and so is an open AP named after a node the house knows."""
    setup = ap.endswith(SETUP_SUFFIX)
    base = ap[:-len(SETUP_SUFFIX)] if setup else ap
    return bool(setup and os.environ.get("PANELS_AP_PASSWORD")) or _known_node(base) or _known_node(ap)


def _report(start: float, host_on: bool, mqtt_error) -> dict:
    obs = _observations(start)
    with _lock:
        relays = [{"node": n, "answered": r["ts"] >= start, "seen": len(r["aps"]) if r["ts"] >= start else 0,
                   "missed": r["missed"], "gone": r["missed"] >= GONE_AFTER} for n, r in sorted(_relays.items())]
    return {
        "aps": [{"ssid": s, "node": s[:-len(SETUP_SUFFIX)] if e["setup"] else None, "open": e["open"],
                 "secured": e["secured"] and not e["open"], "conflict": e["open"] and e["secured"],
                 "setup": e["setup"], "bssids": e["bssids"],
                 "house_open": e["open"] and not e["secured"] and _open_refused(s),
                 "espressif": {b: _espressif(b) for b in e["bssids"]}, "candidates": e["candidates"]}
                for s, e in sorted(obs.items())],
        "relays": relays, "mqtt_error": mqtt_error, "scan_at": start, "worst_s": WORST_S,
        "server": {"configured": host_on, "ok": host_on and not _host_seen["error"], "error": _host_seen["error"]},
        "presets": {k: {"label": v["label"], "tested": v["tested"]} for k, v in PORTAL_PRESETS.items()},
    }


def _portal_spec(preset: str, custom: Optional[dict]) -> dict:
    """A preset, field by field overridable; the portal must be on the setup AP's own network."""
    if preset not in PORTAL_PRESETS:
        raise HTTPException(400, f"unknown portal preset {preset[:32]!r}")
    spec = {k: PORTAL_PRESETS[preset][k] for k in ("host", "method", "path", "ssid_field", "psk_field")}
    for k, v in (custom or {}).items():
        if k in spec and isinstance(v, str) and v.strip():
            spec[k] = v.strip()
    spec["method"] = "GET" if spec["method"].upper() == "GET" else "POST"
    host, _, port = spec["host"].partition(":")
    try:
        inside = ipaddress.IPv4Address(host) in PORTAL_NET and (not port or 0 < int(port) < 65536)
    except ValueError:
        inside = False
    if not inside:
        raise HTTPException(400, "the portal host must be an IPv4 address inside 192.168.4.0/24")
    if not PATH_RE.fullmatch(spec["path"]) or not all(FIELD_RE.fullmatch(spec[k]) for k in ("ssid_field", "psk_field")):
        raise HTTPException(400, "portal path: letters, digits and / _ . - ? = &; field names: letters, digits, _")
    return spec


def _do_payload(ap: str, spec: dict, bssid: str, attempt: str, ap_password: str) -> str:
    """ap|host|METHOD|path|ssid_field|psk_field|bssid|id|ap_password - always 9 fields; the AP password
    is last, so the node takes the rest of the line (it may hold a |)."""
    return "|".join([ap, spec["host"], spec["method"], spec["path"], spec["ssid_field"], spec["psk_field"],
                     bssid, attempt, ap_password])


def _via_node(node: str, ap: str, spec: dict, ap_password: str, bssid: str = "") -> dict:
    """The node goes silent for the excursion and reports on provision/<node>/result when back; only the
    result with this attempt's id and AP counts (a late one of an older attempt is ignored). An empty
    ap_password makes the node use its own compiled one for a "*-setup" AP."""
    attempt = secrets.token_hex(4)
    with _lock:
        _results.pop(node, None)
    try:
        if core.mqtt_client is None:
            raise RuntimeError("MQTT unavailable")
        core.mqtt_pub(core.mqtt_client, f"provision/{node}/do", _do_payload(ap, spec, bssid, attempt, ap_password))
    except Exception as e:
        return {"ok": False, "detail": f"the command did not reach {node}: {e}"}
    deadline = time.time() + RELAY_WAIT_S
    while time.time() < deadline:
        with _lock:
            res = _results.get(node)
        if res and res["id"] == attempt and res["ap"] == ap:
            if res["busy"]:
                return {"ok": False, "busy": True, "detail": "busy with another AP"}
            return {"ok": res["ok"], "detail": res["detail"]}
        time.sleep(RELAY_POLL_S)
    return {"ok": False, "detail": f"{node} did not report within {int(RELAY_WAIT_S)} s (it reboots itself if lost)"}


def _via_host(ap: str, spec: dict, ap_password: str, bssid: str = "", open_ok: bool = False) -> dict:
    ssid, password = os.environ.get("HOUSE_WIFI_SSID") or "", os.environ.get("HOUSE_WIFI_PASSWORD") or ""
    if not (ssid and password):
        return {"ok": False, "detail": "HOUSE_WIFI_SSID / HOUSE_WIFI_PASSWORD are not set in .env"}
    body = {"ap": ap, "ssid": ssid, "password": password, "portal": spec}
    if ap_password:
        body["ap_password"] = ap_password
    if bssid:
        body["bssid"] = bssid
    if open_ok:
        body["allow_open_setup"] = True
    try:
        data = _host_call("/provision", body, timeout=HOST_WAIT_S)
    except RuntimeError as e:
        return {"ok": False, "detail": f"host helper: {e}"}
    out = {"ok": data.get("ok") is True, "detail": str(data.get("error") or "credentials sent"),
           "steps": [str(s) for s in data.get("steps") or []][:20]}
    if data.get("restore_failed") is True:
        out["restore_failed"] = True
    return out


def _node_status(node: str, timeout: float = 4.0) -> dict:
    """The node's LWT: <node>/status, or panel/<node>/status for a panel (retained)."""
    return core.retained_values([f"{node}/status", f"panel/{node}/status"], timeout=timeout)


def _node_back(node: str, since: float, timeout: Optional[float] = None, before: Optional[dict] = None) -> bool:
    """The recovered node shows up: its LWT turns online (it was not, in `before`, at the start), or it
    sends a live scan report. Any device account could fake this too (README.md, Security)."""
    before = before or {}
    deadline = time.time() + (CONFIRM_S if timeout is None else timeout)
    while True:
        with _lock:
            if (_relays.get(node) or {}).get("ts", 0.0) >= since:
                return True
        lwt = _node_status(node)
        if any(v == "online" and before.get(t) != "online" for t, v in lwt.items()):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(BACK_POLL_S)


class ApScan(core.Body):
    wait: float = SCAN_WAIT_S


class ApProvision(core.Body):
    """via: "auto" | "server" | <node>; portal: a key of PORTAL_PRESETS, `custom` overrides its fields;
    ap_password for a secured AP (empty = PANELS_AP_PASSWORD, the house's shared key, for *-setup only)."""
    ap: str
    via: str = "auto"
    portal: str = "esphome"
    custom: Optional[dict] = None
    ap_password: str = ""
    force: bool = False      # the node only LOOKS online (a stale retained status): try anyway


def _slot():
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, "two scans or relays are running already; try again in a minute")


@core.app.post("/api/esphome/ap/scan")
def ap_scan(request: Request, body: ApScan = ApScan()):
    core.house_token_only(request)
    _slot()
    try:
        return _scan(max(3.0, min(20.0, body.wait)))
    finally:
        _slots.release()


@core.app.post("/api/esphome/ap/provision")
def ap_provision(body: ApProvision, request: Request):
    core.house_token_only(request)
    _slot()
    try:
        return _provision(body)
    finally:
        _slots.release()


def _order(ap: str, via: str, since: float) -> list:
    """Who relays, in turn: the chosen one, or (auto) the nodes that hear it, loudest first (at most
    MAX_NODE_RELAYS), the host last."""
    url = _host_there()
    if via == SERVER:
        if not url:
            raise HTTPException(400, "the host relay is not installed on this house's host")
        return [SERVER]
    if via != "auto":
        with _lock:
            known = via in _relays
        if not known or not _node_ok(via):
            raise HTTPException(400, f"{via[:64]!r} is not a known relay node (it never announced on provision/)")
        return [via]
    e = _observations(since).get(ap) or {}
    order = [c["via"] for c in e.get("candidates", []) if c["via"] != SERVER]
    return list(dict.fromkeys(order))[:MAX_NODE_RELAYS] + ([SERVER] if url else [])


def _ap_password(ap: str, given: str, seen: dict) -> tuple:
    """(key, house): the one typed, or for a secured "*-setup" AP the house's PANELS_AP_PASSWORD (every
    house-built node uses it); house = it is that key, which a relay node has compiled in. Never dropped
    because some relay heard the name open; an open AP of a house node is refused."""
    house = os.environ.get("PANELS_AP_PASSWORD") or ""
    setup = ap.endswith(SETUP_SUFFIX)
    if seen.get("open") and seen.get("secured"):
        raise HTTPException(409, "two APs with this name: one open, one secured - refusing")
    if seen.get("open") and _open_refused(ap):
        raise HTTPException(409, OPEN_HOUSE)
    if seen.get("open") and given:
        raise HTTPException(409, f"{ap!r} is heard open, but a password was given - refusing (is it the right AP?)")
    if seen.get("secured") and not given:
        if not setup:
            raise HTTPException(400, f"{ap!r} is secured: give its password (the house key is only for *-setup)")
        if not house:
            raise HTTPException(400, f"{ap!r} is secured: give its password (ap_password) or set PANELS_AP_PASSWORD")
        return house, True
    return given, bool(given) and setup and given == house


def _provision(body: ApProvision) -> dict:
    ap = body.ap.strip()
    if not ap or len(ap.encode()) > 32 or "|" in ap or not ap.isprintable():
        raise HTTPException(400, "ap: the network name, 1-32 bytes, printable, no |")
    pw = body.ap_password
    if pw and not (8 <= len(pw) <= 63 and pw.isascii() and pw.isprintable()):
        raise HTTPException(400, "ap_password: 8-63 printable ASCII characters (WPA2)")
    spec = _portal_spec(body.portal, body.custom)
    since = _last_scan["ts"]
    if time.time() - since > FRESH_S:
        since = _scan()["scan_at"]
    seen = _observations(since).get(ap) or {}
    pw, house_key = _ap_password(ap, pw, seen)
    if len(seen.get("bssids") or []) > 1:
        raise HTTPException(409, f"two APs with this name ({', '.join(seen['bssids'][:4])}) - refusing")
    bssid = (seen.get("bssids") or [""])[0]
    open_ok = bool(seen.get("open"))       # passed _ap_password: an open AP the house may serve
    node = ap[:-len(SETUP_SUFFIX)] if ap.endswith(SETUP_SUFFIX) else ""
    node = node if _node_ok(node) else ""
    before: dict = {}
    if node:
        # a node still online cannot be in its setup AP: someone else uses its name
        before = _node_status(node, timeout=2.0)
        with _lock:
            live = (_relays.get(node) or {}).get("ts", 0.0) >= since
        if (live or "online" in before.values()) and not body.force:
            raise HTTPException(409, f"the node {node} is online: nothing to recover; a twin? (a stale status: try anyway)")
        # forced: the stale "online" is cleared, so the node's own "online" once back is a change again
        for t, v in list(before.items()):
            if v == "online" and core.mqtt_client is not None:
                core.mqtt_pub(core.mqtt_client, t, "offline", retain=True)
                before[t] = "offline"
    order = _order(ap, body.via, since)
    manual = {"ap": ap, "portal": f"http://{spec['host']}", "bssid": bssid or None}
    if not order:
        return {"ok": False, "attempts": [], "manual": manual,
                "detail": "no relay hears this AP and the host relay is not configured"}
    attempts = []
    for via in order:
        with _lock:
            busy = via in _busy
            _busy.add(via)
        if busy:
            attempts.append({"via": via, "ok": False, "busy": True, "detail": "busy with another AP"})
            continue
        started = time.time()
        try:
            res = (_via_host(ap, spec, pw, bssid, open_ok) if via == SERVER else
                   _via_node(via, ap, spec, "" if house_key else pw, bssid))
        finally:
            with _lock:
                _busy.discard(via)
        attempts.append({"via": via, "label": "Host" if via == SERVER else via, **res})
        if not res.get("ok"):
            continue
        out = {"via": via, "attempts": attempts, "manual": manual, "node": node or None,
               "restore_failed": any(a.get("restore_failed") for a in attempts)}
        if node and not _node_back(node, started, before=before):
            return {**out, "ok": False, "pending": True,
                    "detail": "the relay says done; waiting for the node to show up"}
        return {**out, "ok": True, "detail": "the node got the Wi-Fi and is back on the network" if node else
                "the relay says done; the device restarts into the house network"}
    return {"ok": False, "attempts": attempts, "manual": manual,
            "restore_failed": any(a.get("restore_failed") for a in attempts),
            "detail": "no relay managed it; join the AP from a phone and use its portal"}


def _health() -> list:
    rows = []
    url = _host_there()
    title = "ESP recovery: host relay"
    if not url:
        rows.append(core.health_row("provision:host", "ok", title, "not installed"))
    else:
        try:
            data = _host_call("/health", timeout=2)
            ok = data.get("ok") is True
            err = "" if ok else str(data.get("error") or "it said no")[:120]
        except RuntimeError as e:
            ok, err = False, str(e)[:120]
        rows.append(core.health_row("provision:host", "ok", title) if ok else
                    core.health_row("provision:host", "bad", title, "not ready: {0}",
                                    "Is the service running on its host?", (err,), cmd="systemctl status yashome-provision.socket yashome-provision"))
    with _lock:
        n = len(_relays)
        fresh = sum(1 for r in _relays.values() if time.time() - r["known"] < 86400)
    if n:
        rows.append(core.health_row("provision:nodes", "ok", "ESP recovery: relay nodes",
                                    "{0} known, {1} heard from in 24 h", "", (n, fresh)))
    else:
        rows.append(core.health_row("provision:nodes", "ok" if url else "warn", "ESP recovery: relay nodes",
                                    "no node with the provision package announced",
                                    "" if url else "Add firmware/provision.yaml to a node or set up the host relay"))
    return rows


core.MQTT_NAMESPACES.add("provision")
core.MQTT_SUBSCRIPTIONS.extend(["provision/seen/+", "provision/+/result"])
core.MQTT_HANDLERS.append((("provision",), _on_message))
core.HEALTH_CHECKS.append(_health)
