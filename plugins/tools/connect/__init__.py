"""One "Add / bind" window for every way a device joins the house. Opening it opens every window at once: Zigbee
permit-join (zigbee) and the tablet invite (panels); the dashboard reads each source's own list (Zigbee joins,
ESPHome on MQTT, setup networks of yashGen devices and of nodes that lost their Wi-Fi, tablets, new ESP screens)
and adds through each source's own endpoint. Here only what spans them: start, stop, and a code typed once that
is matched against every pending device that shows one. A source whose plug-in is off is skipped."""
import hmac
import sys
import threading
import time

from fastapi import HTTPException, Request
from pydantic import Field

import app as core

PLUGIN = {"name": "Connect", "version": "1.0.0", "icon": "pen"}
PAIRING_MIN = 5                           # the tablet invite, minutes (the dialog refreshes it while open)


def _plugin(name: str):
    return sys.modules.get(f"yashome_plugins.{name}")


def _try(out: dict, key: str, fn, *args):
    try:
        fn(*args)
        out["opened"].append(key)
    except HTTPException as e:
        out["errors"][key] = str(e.detail)
    except Exception as e:                                # noqa: BLE001 - one source down must not stop the others
        out["errors"][key] = str(e)


def _open_now(key: str) -> bool:
    """Is this way open already (opened elsewhere: the Zigbee page, the Panels page, another window)?"""
    zb, pn = _plugin("zigbee"), _plugin("panels")
    if key == "zigbee" and zb is not None:
        return bool(zb._pairing.get("active")) and float(zb._pairing.get("until") or 0) > time.time()
    if key == "tablet" and pn is not None:
        return float((pn._pair_load(pn.PAIR_FLAG, {}) or {}).get("until") or 0) > time.time()
    return False


class WindowBody(core.Body):
    window: str = Field(default="", max_length=40)        # the dialog's own random id: the server counts who keeps a way open


_owners: dict = {}            # way -> {window: monotonic of its last start}: the dialogs keeping it open
_ours: set = set()            # the ways a dialog opened (closed before): the last window to leave closes them
_owners_lock = threading.Lock()   # start and stop run in the server's thread pool: one window's bookkeeping at a time
LEASE_S = 120                 # 2 x the dialog's 60 s renewal: a window not heard of that long (a dead tab, a phone that died) is gone


def _need_window(body: "WindowBody") -> str:
    if not body.window.strip():
        raise HTTPException(400, "window: the dialog's id (an older page: reload it)")
    return body.window.strip()


def _release(window: str = "", now: float = None) -> dict:
    """Let go of a window (closed), and of every window whose lease ran out; a way a dialog opened closes when nobody keeps it."""
    now = time.monotonic() if now is None else now
    out = {"opened": [], "errors": {}}
    zb, pn = _plugin("zigbee"), _plugin("panels")
    for k in list(_owners):
        _owners[k] = {w: t for w, t in _owners[k].items() if w != window and now - t <= LEASE_S}
        if _owners[k] or k not in _ours:
            continue
        del _owners[k]
        _ours.discard(k)
        if k == "zigbee" and zb is not None:
            _try(out, "zigbee", zb.pair_stop)
        if k == "tablet" and pn is not None:
            _try(out, "tablet", pn.pairing, pn.PairingBody(off=True))
    return out


@core.app.post("/api/connect/start")
def connect_start(request: Request, body: WindowBody = WindowBody()):
    """Open every way a device can join through, or keep it open: the dialog calls this every KEEP_OPEN (60 s, ui.js) while it is open
    (Zigbee closes after PERMIT_JOIN_SEC, the tablet invite after PAIRING_MIN: protocol pacing, not settings). What failed is named."""
    core.house_token_only(request)
    window = _need_window(body)
    with _owners_lock:
        return _start(window)


def _start(window: str) -> dict:
    now = time.monotonic()
    for held in _owners.values():                 # the caller is alive: its own holds renew first, whether or not a way reopens below
        if window in held:
            held[window] = now
    _release(now=now)                             # then leases that ran out
    out = {"opened": [], "errors": {}, "started": []}
    zb, pn = _plugin("zigbee"), _plugin("panels")
    was = {k: _open_now(k) for k in ("zigbee", "tablet")}
    if zb is not None:
        _try(out, "zigbee", zb.pair_start)
    if pn is not None:
        _try(out, "tablet", pn.pairing, pn.PairingBody(minutes=PAIRING_MIN))
    for k in out["opened"]:
        if not was[k]:
            _ours.add(k)                          # closed before this call: a dialog opened it, the last one closes it
            out["started"].append(k)
        if k in _ours:
            _owners.setdefault(k, {})[window] = now
    return out


@core.app.post("/api/connect/stop")
def connect_stop(request: Request, body: WindowBody = WindowBody()):
    """A dialog closed: a way it kept open closes once no other dialog keeps it, and only when a dialog opened it (one opened on the
    Zigbee or Panels page stays open)."""
    core.house_token_only(request)
    window = _need_window(body)
    with _owners_lock:
        out = _release(window)
    return {"closed": out["opened"], "errors": out["errors"]}


class CodeBody(core.Body):
    code: str = Field(max_length=16)


def _same(a, b) -> bool:
    return bool(a) and hmac.compare_digest(str(a).strip().encode(), str(b).strip().encode())


@core.app.post("/api/connect/code")
def connect_code(body: CodeBody, request: Request):
    """The devices with this code on their screen: a waiting tablet, a bound one that shows its code again (it is
    known: bound again over its heartbeat, no UDP), a new ESP screen; with the model, so the owner sees what binds.
    A yashGen code is its setup network's key: no list knows it."""
    core.house_token_only(request)
    code = body.code.strip()
    if not code:
        raise HTTPException(400, "code required")
    hits = []
    pn = _plugin("panels")
    if pn is not None:
        for did, d in (pn._pair_load(pn.PAIR_DB, {}) or {}).items():
            if not isinstance(d, dict):
                continue
            rep = d.get("repair") if isinstance(d.get("repair"), dict) else None
            if (rep and _same(rep.get("code"), code)) or (d.get("status") == "pending" and _same(d.get("code"), code)):
                hits.append({"kind": "tablet", "id": did, "model": d.get("model") or "", "name": (rep or d).get("name") or "",
                             "again": bool(rep), "panel": d.get("panel") or ""})
            elif d.get("status") == "bound" and _same(d.get("code"), code):     # a bound tablet shows its code again
                hits.append({"kind": "tablet", "id": did, "model": d.get("model") or "", "name": d.get("name") or "",
                             "bound": True, "panel": d.get("panel") or ""})
        with pn._esp._lock:
            pn._esp._expire()
            new = [dict(e) for e in pn._esp._new.values()]
        for e in new:
            if _same(e.get("code"), code):
                hits.append({"kind": "esp_screen", "id": e.get("mac"), "model": e.get("hw") or "", "ip": e.get("ip") or ""})
    return {"matches": hits}
