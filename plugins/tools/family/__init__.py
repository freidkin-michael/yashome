"""Family phones (the Yash! app): one pairing QR, a server token per phone, rights, revoke, ring.

The Firebase side (custom tokens, FCM pushes, /state) is the family-bridge companion, the container
family-bridge on this host. Without Firebase (FAMILY_FIREBASE_DB empty) the plug-in works through the dashboard alone: the QR holds only the address
and the token, and a ring goes down /api/family/live, which the app keeps open on the house's network.
This plug-in owns the server tokens: every QR
carries a fresh `yfd_...` token for that name and only its sha256 is kept; revoking drops it. The
house token never goes to a phone. Rights per name: none = full access; otherwise
{device: "view" | "control"} and every other device is hidden, in REST and in /ws.
"""
import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import pathlib
import re
import secrets
import sys
import struct
import threading
import time
import zipfile
import urllib.error
import urllib.parse
from typing import Optional
import urllib.request

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

import app as core

PLUGIN = {"name": "Family phones", "version": "2.3.0", "icon": "people"}

DB = core.plugin_state_file("family", "family_devices.json")
BRIDGE = "http://family-bridge:8790"
BRIDGE_KEY = os.environ.get("FAMILY_BRIDGE_KEY") or os.environ.get("BRIDGE_KEY", "")   # cutover-compat: BRIDGE_KEY
_BIND = (os.environ.get("BIND_ADDRESS") or "").strip()
_BIND = "" if _BIND in ("0.0.0.0", "::") else _BIND
APP_ID = os.environ.get("FAMILY_APP_ID") or "com.yash.home"
PREFIX = "yfd_"
CLAIM_PREFIX = "yfc_"
LIVE_MAX = 3
RULE_RING_GAP_S = 60
CLOUD = bool(os.environ.get("FAMILY_FIREBASE_DB"))
RING_MIN_S, RING_MAX_S = 5, 300
LIVE_CHECK_S = 30
AUTO_UPDATE = True              # a phone with an older app is told to fetch the built one (at most once an hour); it asks before installing
UPDATE_GAP_S = 3600            # an automatic update command goes to one phone at most this often
PRESENCE_GRACE_S = 600
# who is home: a phone's own Wi-Fi address in the house's network (BIND_ADDRESS's /24), or a gateway (and Wi-Fi name) the house
# learned from such a report (plugin-state/family/home_wifi.json); a phone may also take the home state of a presence device
# (the plug-in presence, by its Wi-Fi MAC): its "presence" field, set in its window on the Family page
HOME_NETS = [ipaddress.ip_network(f"{_BIND}/24", strict=False)] if _BIND else []   # else the phones' address: _home_nets
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,19}$")

CLAIM_S = 3600
_lock = threading.RLock()
_cache = {"mtime": None, "data": {}, "bad": False}


def _load() -> dict:
    try:
        m = DB.stat().st_mtime_ns
    except FileNotFoundError:
        _cache.update(mtime=None, data={}, bad=False)          # a broken file removed: nothing is broken now
        return {}
    if _cache["mtime"] != m:
        try:
            _cache["data"], _cache["bad"] = json.loads(DB.read_text()), False
        except ValueError:
            core.log.error("[family] family_devices.json unreadable: no phone token is accepted, nothing is written")
            _cache["data"], _cache["bad"] = {}, True
        _cache["mtime"] = m
    return _cache["data"]


def _save(data: dict):
    _load()
    if _cache["bad"]:
        raise HTTPException(503, "family_devices.json is unreadable: fix or remove it first")
    core.atomic_json_dump(DB, data, indent=1, sort_keys=True)     # a unique 0600 temp, fsync, rename
    _cache["data"], _cache["mtime"] = data, DB.stat().st_mtime_ns


def _sha(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _uid_of(tok: str):
    """The phone a `yfd_` token belongs to, or None. The QR never holds one: it holds a one-time code
    the app swaps for its token (/api/family/claim), so a photo of a QR opens nothing once used."""
    if not tok.startswith(PREFIX):
        return None
    h = _sha(tok)
    uid = next((u for u, d in _load().items() if hmac.compare_digest(h, d.get("sha256") or "")), None)
    if uid and _load()[uid].get("claimed") is False:     # a token from before the one-time codes, never used
        return None
    return uid


def _allowed(rights, method: str, path: str) -> bool:
    """What a phone with these rights may call. None = full access: it uses the house (reads everything, changes what
    core.use_route marks: devices, rules, favourites, the player, voice), it does not set it up (pairing, removing,
    layouts, firmware, computers, backups stay with the house token)."""
    if (method, path) in _OPEN_FOR_RESTRICTED or (method, path) in _OPEN_WS:
        return True
    if rights is None:
        return core.is_use_route(method, path)
    if (method, path) in _OPEN_FOR_RESTRICTED or (method == "GET" and path in _FILTERED) or (method, path) in _OPEN_WS:
        return True
    m = _DEV_PATH.match(path)
    return bool(m) and m.group(1) in rights and (m.group(2) != "control" or rights[m.group(1)] == "control")


def _token_check(tok: str, method: str, path: str) -> bool:
    if (method, path) == ("WS", "/api/family/live") and tok.startswith(PREFIX) and _cache["bad"]:
        return True                         # the live route itself closes it 1011 ("later"), not 4401 ("revoked")
    uid = _uid_of(tok)
    return uid is not None and _allowed(_rights(uid), method, path)


core.TOKEN_CHECKS.append(_token_check)


def _token_of(headers, query=None) -> str:
    return core.request_token(headers, query)


def _caller(request: Request):
    """uid of the phone behind this request, or None for the house token (the dashboard)."""
    return _uid_of(_token_of(request.headers, request.query_params))


def _bridge(method: str, path: str, body=None, timeout: float = 20):
    if not BRIDGE_KEY:
        raise HTTPException(503, "FAMILY_BRIDGE_KEY is not set")
    req = urllib.request.Request(BRIDGE + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Bridge-Key": BRIDGE_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"bridge: {e.read().decode(errors='replace')[:200]}")
    except OSError as e:
        raise HTTPException(503, f"bridge unreachable: {e}")


class PairBody(core.Body):
    name: str = Field("", max_length=60)   # the member's name, any language; the id is made from it
    full: bool = False            # a name not listed yet: full access, else nothing until Rights gives devices
    uid: str = Field("", max_length=20)    # New QR of a listed member: its id, its name stays


class RenameBody(core.Body):
    name: str = Field(max_length=60)


class NameBody(core.Body):
    name: str
    secs: int = Field(60, ge=RING_MIN_S, le=RING_MAX_S)


def _name(n: str) -> str:
    n = (n or "").strip().lower()
    if not NAME_RE.match(n):
        raise HTTPException(400, "name: lowercase latin, 2-20 chars")
    return n


def _new_uid(display: str, have) -> str:
    """The internal id of a new member: latin letters of its name when it has some, else "member"; unique."""
    base = re.sub(r"[^a-z0-9_-]", "", display.lower())[:16]
    base = base if NAME_RE.match(base) else "member"
    uid, n = base, 2
    while uid in have:
        uid, n = f"{base}{n}", n + 1
    return uid


def _dashboard_only(request: Request):
    if _caller(request) is not None:
        raise HTTPException(403, "only the dashboard does this")


def _local_list(skip=()) -> list:
    return [{"uid": uid, "name": d.get("name", uid), "role": "", "token": False, "ack": None,
             "state": "active"} for uid, d in sorted(_load().items()) if uid not in skip]


def _link(d, now) -> str:
    if d.get("sha256") and d.get("claimed") is not False:
        return "connected"
    return "waiting" if d.get("claim_sha") and now - d.get("created", 0) <= CLAIM_S else "qr expired"


@core.app.get("/api/family")
async def family_list(request: Request):
    _dashboard_only(request)
    phones = await run_in_threadpool(_bridge, "GET", "/list") if CLOUD else []
    phones += _local_list({p["uid"] for p in phones})      # paired here without the cloud: still listed
    local = _load()
    now = time.time()
    meta = _apk_meta()
    for p in phones:
        d = local.get(p["uid"])
        p["server"] = d is not None
        p["old_app"] = bool(d is not None and d.get("sha256") and not d.get("device_id"))
        p["link"] = None if d is None else _link(d, now)
        p["live"] = bool(_live.get(p["uid"]))
        p["cloud"] = CLOUD
        p["ack"] = max(filter(None, (p.get("ack"), _acks.get(p["uid"]))), default=None)
        p["home"] = _person_state.get(p["uid"])                # True / False, None until published
        p["home_ts"] = int(_lan_seen[p["uid"]] * 1000) if p["uid"] in _lan_seen else None
        p["app"] = _versions.get(p["uid"])
        p["update"] = _needs_update(p["uid"], meta)
        p["presence"] = (d or {}).get("presence") or ""
        r = (d or {}).get("rights")
        p["admin"], p["full"] = d is not None and r is None and d.get("admin") is True, d is not None and r is None
        p["devices_n"] = len(r) if isinstance(r, dict) else 0
    return phones


class PresenceBody(core.Body):
    device: str = Field(default="", max_length=64)            # "" = none


@core.app.get("/api/family/presence/devices")
def family_presence_devices(request: Request):
    """The presence devices a phone may take its home state from (the plug-in presence: a phone's Wi-Fi MAC seen on the house Wi-Fi)."""
    _dashboard_only(request)
    return [{"id": d, "name": x.get("name") or d} for d, x in sorted(core.DEVICES.items()) if x.get("transport") == "presence"]


@core.app.put("/api/family/{uid}/presence")
def family_presence_put(uid: str, body: PresenceBody, request: Request):
    """The phone takes the home state of that presence device too (a phone paired through the cloud has no live link)."""
    core.house_token_only(request)
    uid = _name(uid)
    dev = body.device.strip()
    if dev and (dev not in core.DEVICES or core.DEVICES[dev].get("transport") != "presence"):
        raise HTTPException(400, "not a presence device")
    with _lock:
        data = dict(_load())
        if uid not in data:
            raise HTTPException(404, "unknown phone")
        entry = {k: v for k, v in data[uid].items() if k != "presence"}
        if dev:
            entry["presence"] = dev
        data[uid] = entry
        _save(data)
    return {"uid": uid, "presence": dev}


@core.app.get("/api/family/names")
async def family_names(request: Request):
    """The paired phones, from this house alone (no bridge call): the rule editor's list."""
    _dashboard_only(request)
    return [{"uid": uid, "name": d.get("name", uid)} for uid, d in sorted(_load().items())]


# what a New QR drops: the old token dies, and the phone that claims the code sets its own device id (family_claim)
_PAIRING_KEYS = ("claim_sha", "sha256", "claimed", "created", "name", "device_id")


@core.app.post("/api/family/pair")
async def family_pair(body: PairBody, request: Request):
    _dashboard_only(request)
    if not server_url():
        raise HTTPException(503, "no address for the phones: BIND_ADDRESS, or Settings -> Family -> App")
    have = _load()
    if body.uid or body.name.strip() in have:                    # New QR of a listed member: its id and name stay
        uid = _name(body.uid or body.name)
        display = have.get(uid, {}).get("name") or uid
    else:
        display = core.check_name(body.name, "family member", [d.get("name") for d in have.values()], most=30)
        uid = _new_uid(display, have)
    code = CLAIM_PREFIX + secrets.token_urlsafe(24)               # one use: /api/family/claim swaps it for the token
    link = server_url() + "/plugins/family/static/pair.html" + ("" if APP_ID == "com.yash.home" else "?app=" + APP_ID)
    if CLOUD:
        res = await run_in_threadpool(_bridge, "POST", "/pair", {
            "uid": uid, "srv": server_url(), "tok": code, "display": display, "link": link})
    else:                                                       # the browser draws this QR (static/qrcode.js)
        data = json.dumps({"srv": server_url(), "tok": code, "name": display}, separators=(",", ":")).encode()
        res = {"text": link + "#" + base64.urlsafe_b64encode(data).decode().rstrip("=")}
    def keep():
        with _lock:
            data = dict(_load())
            old = data.get(uid)                                 # a listed name keeps all but the pairing: rights, presence...
            data[uid] = {"claim_sha": _sha(code), "name": display, "created": int(time.time()),
                         **({k: v for k, v in old.items() if k not in _PAIRING_KEYS} if old is not None
                            else {"fresh": True} if body.full else {"rights": {}, "fresh": True})}
            _save(data)
    await run_in_threadpool(keep)
    core.log.warning(f"[family] paired {uid} ({display}): new QR, new server token")
    _close_live(uid)
    return res


@core.app.put("/api/family/{uid}/name")
async def family_rename(uid: str, body: RenameBody, request: Request):
    """A member's name as people see it (any language): the header, Health, voice answers and rules show it; the id stays."""
    _dashboard_only(request)

    def keep():
        with _lock:
            data = dict(_load())
            if uid not in data:
                raise HTTPException(404, "no such family member")
            name = core.check_name(body.name, "family member", [d.get("name") for u, d in data.items() if u != uid], most=30)
            data[uid] = {**data[uid], "name": name}
            _save(data)
            return name
    name = await run_in_threadpool(keep)
    dev = _person_id(uid)
    if dev in core.DEVICES:
        core.set_device_name(dev, name)
    return {"uid": uid, "name": name}


class ClaimBody(core.Body):
    code: str = Field(max_length=64)
    device_id: str = Field("", max_length=40, pattern=r"^(|and-[0-9a-f]{8,32})$")   # the phone's ANDROID_ID


def family_claim(body: ClaimBody):
    """The app's first call: the QR's one-time code for the phone's own token. The code dies here, and
    an hour after the QR anyway; only the new token's sha256 is kept."""
    h = _sha(body.code) if body.code.startswith(CLAIM_PREFIX) else ""
    with _lock:
        data = dict(_load())
        uid = next((u for u, d in data.items() if h and hmac.compare_digest(h, d.get("claim_sha") or "")), None)
        if uid is None or time.time() - data[uid].get("created", 0) > CLAIM_S:
            raise HTTPException(401, "this QR is used or expired: make a new one in the dashboard")
        tok = PREFIX + secrets.token_urlsafe(32)
        entry = {k: v for k, v in data[uid].items() if k not in ("claim_sha", "claimed", "fresh")}
        same = next((u for u, d in data.items() if u != uid and body.device_id
                     and d.get("device_id") == body.device_id), None)
        if same is not None:
            if not data[uid].get("fresh"):              # a listed phone's New QR: never folded into another one
                raise HTTPException(409, f"this phone is paired as {same} already: delete one of the two, then pair")
            # this phone was paired before: its own entry (name, widgets, rules); rights from this QR, the admin's latest choice
            ph = data.pop(uid)
            placeholder, uid = uid, same
            entry = {k: v for k, v in data[uid].items()
                     if k not in ("claim_sha", "claimed", "sha256", "rights", "screen", "fresh")}
            entry.update({k: ph[k] for k in ("rights", "screen") if k in ph})
        data[uid] = {**entry, "sha256": _sha(tok), **({"device_id": body.device_id} if body.device_id else {})}
        _save(data)
    if same is not None:
        _close_live(uid)                # the old token is dead: its sockets close
        if CLOUD:
            _bridge_quiet("/revoke", placeholder)
        core.log.warning(f"[family] {placeholder}'s QR claimed by {uid}'s phone: the same entry, a new token")
    core.log.warning(f"[family] {uid} claimed its QR")
    out = {"tok": tok, "uid": uid, "name": data[uid].get("name", uid)}
    if CLOUD:                       # the Firebase sign-in rides the one-time claim, never the QR itself
        try:
            out["ct"] = _bridge("POST", "/custom_token", {"uid": uid}, timeout=10)["ct"]
        except Exception as e:                  # noqa: BLE001 - the code is spent: still hand over the token
            core.log.warning(f"[family] {uid}: no Firebase sign-in with the claim: {getattr(e, 'detail', e)}")
            out["no_cloud"] = True
    return out


core.app.add_api_route("/api/family/claim", family_claim, methods=["POST"])
core.open_route(family_claim)


def _bridge_quiet(path: str, uid: str):
    try:
        _bridge("POST", path, {"uid": uid})
    except HTTPException as e:
        core.log.warning(f"[family] bridge {path} {uid}: {e.detail}")


def _drop_rings(uid: str):
    """The rules that ring this phone go with it: (how many, saved). A failed save is logged, the delete goes on."""
    n, saved = 0, True
    try:
        with core.bindings_lock:
            for src, codes in core.bindings.items():
                for code, gestures in codes.items():
                    for g in [g for g, b in gestures.items() if b.get("action") == "ring" and b.get("target") == uid]:
                        gestures.pop(g)
                        n += 1
            if n:
                core.save_settings()
    except HTTPException as e:
        saved = False
        core.log.error(f"[family] {uid}: its ring rules not saved as dropped: {e.detail}")
    return n, saved


@core.app.post("/api/family/revoke")
async def family_revoke(body: NameBody, request: Request):
    """Delete a phone for good: its entry (token, QR code, widgets, rights), its open sockets, its
    Firebase sign-in and push token, the rules that ring it. Also a Firebase-only row (no entry here)."""
    _dashboard_only(request)
    core.house_token_only(request)
    uid = _name(body.name)

    def drop():
        with _lock:
            data = dict(_load())
            data.pop(uid, None)
            _save(data)
    await run_in_threadpool(drop)
    _close_live(uid)
    _widget_sent.pop(uid, None)
    _acks.pop(uid, None)
    _rule_rings.pop(uid, None)
    rules, saved = _drop_rings(uid)
    if CLOUD:
        await run_in_threadpool(_bridge, "POST", "/revoke", {"uid": uid})
    core.log.warning(f"[family] deleted {uid} (rules that rang it: {rules})")
    return {"ok": True, "rules": rules, **({} if saved else {"rules_saved": False})}


# Phones without Firebase: the app's service keeps /api/family/live open; uid -> {websocket: its loop}
_live: dict = {}
_live_lock = threading.Lock()
_acks: dict = {}               # uid -> ms of the last ack over the live link (memory only: no disk write per ring)
_rule_rings: dict = {}         # uid -> when a rule last rang it


def _send_live(uid: str, msg: dict) -> bool:
    """msg down every live socket of this phone (from a worker thread, never the event loop's)."""
    with _live_lock:
        socks = list(_live.get(uid, {}).items())
    sent = False
    for ws, loop in socks:
        fut = asyncio.run_coroutine_threadsafe(ws.send_json(msg), loop)
        try:
            fut.result(timeout=5)
            sent = True
        except Exception as e:                              # noqa: BLE001 - a dead socket: the others may work
            fut.cancel()
            with _live_lock:
                _live.get(uid, {}).pop(ws, None)
            asyncio.run_coroutine_threadsafe(ws.close(code=1011), loop)
            core.log.info(f"[family] live {uid}: not sent, socket dropped: {e!r}")
    return sent


def _close_live(uid: str):
    """A revoked or re-paired phone's open sockets close (the old token is dead)."""
    with _live_lock:
        socks = list(_live.pop(uid, {}).items())
    for ws, loop in socks:
        asyncio.run_coroutine_threadsafe(ws.close(code=4401), loop)


def _ring(uid: str, secs: int = 60, cmd: str = "ring") -> dict:
    """A ring (or stop) down every way this house has: the live socket and, with Firebase, a push."""
    if uid not in _load():
        raise HTTPException(404, "unknown phone")
    secs = max(RING_MIN_S, min(RING_MAX_S, int(secs)))
    via, err = [], None
    msg = {"cmd": cmd, "secs": secs} if cmd == "ring" else {"cmd": cmd}
    if _send_live(uid, msg):
        via.append("live")
    if CLOUD:
        try:
            _bridge("POST", "/" + cmd, {"uid": uid, "secs": secs})
            via.append("push")
        except HTTPException as e:
            err = e.detail
    if not via:
        raise HTTPException(409, err or "the phone is not connected: without Firebase the app reaches the "
                                        "house only on its network (or a VPN)")
    return {"ok": True, "via": via}


def _ack(uid: str):
    _acks[uid] = int(time.time() * 1000)


@core.app.post("/api/family/ring")
async def family_ring(body: NameBody, request: Request):
    _dashboard_only(request)
    return await run_in_threadpool(_ring, _name(body.name), body.secs)


@core.app.post("/api/family/stop")
async def family_stop(body: NameBody, request: Request):
    _dashboard_only(request)
    return await run_in_threadpool(_ring, _name(body.name), body.secs, "stop")


@core.app.websocket("/api/family/live")
async def family_live(websocket: WebSocket):
    """The phone's own channel: rings and stops come down, {"ack": 1} goes up. Its token (a header) is
    checked again every LIVE_CHECK_S: revoked or re-paired, the socket closes."""
    tok = _token_of(websocket.headers)
    uid = _uid_of(tok) if core.same_origin(websocket.headers) else None
    if uid is None:
        if _cache["bad"] and core.same_origin(websocket.headers):
            await websocket.accept()                # the phone list is unreadable: try later, not "revoked"
            await websocket.close(code=1011)
        else:
            await websocket.close(code=4401)
        return
    await websocket.accept()
    lan = _is_home_addr(websocket.client.host if websocket.client else "")
    if lan and uid not in _away_since:
        _lan_seen[uid] = time.time()
    _widget_sent.pop(uid, None)                                 # the widgets get the whole state at the next tick
    with _live_lock:
        socks = _live.setdefault(uid, {})
        while len(socks) >= LIVE_MAX:                           # the oldest goes: a phone needs one, a reconnect two
            old = next(iter(socks))
            socks.pop(old)
            asyncio.ensure_future(old.close(code=1000))
        socks[websocket] = asyncio.get_running_loop()
    code = 1011                                                 # 4401 only for a dead token: the app stops then
    try:
        while True:
            try:
                frame = await asyncio.wait_for(websocket.receive(), LIVE_CHECK_S)
            except asyncio.TimeoutError:
                frame = None
            if lan and uid not in _away_since:                  # a newer away report is not undone by the socket
                _lan_seen[uid] = time.time()
            if frame is not None and frame["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(frame.get("code", 1000))
            msg = _frame_json(frame)
            if _uid_of(tok) != uid:
                code = 1011 if _cache["bad"] else 4401      # a broken file is not a revoke
                break
            if isinstance(msg, dict) and ("gw" in msg or "v" in msg):
                _note_report(uid, msg)
            elif isinstance(msg, dict) and msg.get("ack"):
                _ack(uid)
            elif isinstance(msg, dict) and "err" in msg:          # the phone got it but could not ring
                core.log.warning(f"[family] {uid}: ring not started on the phone: {str(msg['err'])[:80]}")
            elif frame is None:
                await websocket.send_json({"cmd": "ping"})
    except WebSocketDisconnect:
        code = None
    except (RuntimeError, ValueError, KeyError):
        pass
    finally:
        if lan and uid not in _away_since:
            _lan_seen[uid] = time.time()
        with _live_lock:
            socks = _live.get(uid, {})
            socks.pop(websocket, None)
            if not socks:
                _live.pop(uid, None)
        if code is not None:
            try:
                await websocket.close(code=code)
            except RuntimeError:
                pass


def _frame_json(frame):
    """A text frame's JSON object; a binary frame or anything else is ignored (None)."""
    try:
        return json.loads(frame["text"]) if frame and frame.get("text") else None
    except ValueError:
        return None


def _ring_validate(body: dict) -> dict:
    uid = _name(str(body.get("target") or ""))
    if uid not in _load():
        raise HTTPException(400, "unknown phone")
    try:
        secs = int(body.get("secs", 60))
    except (TypeError, ValueError):
        raise HTTPException(400, "secs: a number")
    cmd = "stop" if body.get("cmd") == "stop" else "ring"
    return {"action": "ring", "target": uid, "cmd": cmd, "secs": max(RING_MIN_S, min(RING_MAX_S, secs))}


def _ring_rule(b: dict) -> dict:
    """A rule's ring: at most one per RULE_RING_GAP_S and phone, so a flapping rule cannot restart it."""
    uid, cmd = b["target"], b.get("cmd", "ring")
    held = cmd == "ring" and not b.get("_forced")          # Run in the rule list is never held back
    if held:
        with _lock:                                         # two triggers at once: one rings
            before = _rule_rings.get(uid, 0)
            if time.time() - before < RULE_RING_GAP_S:
                return {"action": "ring", "skipped": "rang less than a minute ago"}
            _rule_rings[uid] = time.time()
    try:
        res = _ring(uid, b.get("secs", 60), cmd)
    except Exception:
        if held:
            if before:                                      # it did not go out: the minute is not used
                _rule_rings[uid] = before
            else:
                _rule_rings.pop(uid, None)
        raise
    return {**res, "action": "ring"}


core.BINDING_ACTIONS["ring"] = {
    "validate": _ring_validate,
    "run": _ring_rule,
    "house_token_only": True,                  # a phone, even with full access, rings no other phone
    "describe": lambda b: {"type": "family-ring", "action": "ring", "target": b.get("target"), "secs": b.get("secs", 60),
                           "cmd": b.get("cmd", "ring"), "name": _load().get(b.get("target"), {}).get("name", b.get("target")),
                           "title": f"{b.get('cmd', 'ring')} {b.get('target')}"},
}


APP_DIR = pathlib.Path(__file__).parent / "app"
_app_holder: dict = {}


def _build_plugin():
    mod = sys.modules.get(__package__.rsplit(".", 1)[0] + ".build")
    if mod is None:
        raise HTTPException(503, "the build plug-in is off: PLUGINS in .env needs build (and a computer with the Android app builder)")
    return mod


def _app():
    """The Yash! app's build (the build plug-in's AppBuild): key and APK in plugin-state/family/."""
    mod = _build_plugin()
    if _app_holder.get("mod") is not mod:
        _app_holder.update(mod=mod, app=mod.AppBuild("family", "yash-app", APP_DIR, "yash.apk", firebase=True,
                                                     app_id=None if APP_ID == "com.yash.home" else APP_ID))
    return _app_holder["app"]


@core.app.post("/api/family/app/build")
def family_app_build(request: Request):
    """Build the Yash! app on the build server's app builder, signed with the house's key."""
    core.house_token_only(request)
    return _app().start()


@core.app.get("/api/family/app/build")
def family_app_build_state(request: Request):
    """The build's state, and the built app's version (apk_name, apk_code) for the Family row."""
    core.house_token_only(request)
    meta = _apk_meta()
    return {**_app().status(), **({"apk_name": meta["name"], "apk_code": meta["code"]} if meta else {})}


def _apk_file():
    """The built app: plugin-state (Build the app, build.sh), else an older plugin-data copy."""
    for f in (core.plugin_state("family") / "yash.apk", core.plugin_data("family") / "yash.apk"):
        if f.is_file():
            return f
    return None


def family_apk():
    """The Yash! APK (the Family card links it; the phones fetch updates here): plugin-state/family/yash.apk."""
    f = _apk_file()
    if f is None:
        raise HTTPException(404, "no app here yet: Family phone -> Build the app (or app/build.sh)")
    return FileResponse(f, media_type="application/vnd.android.package-archive", filename="yash.apk")


core.app.add_api_route("/plugins/family/yash.apk", family_apk, methods=["GET", "HEAD"])
core.open_route(family_apk)


_OPEN_FOR_RESTRICTED = {("GET", "/api/health"), ("GET", "/api/family/me"), ("GET", "/api/family/me/app"), ("PUT", "/api/family/me/screen"),
                        ("PUT", "/api/family/me/widgets"), ("GET", "/api/family/me/pull"), ("POST", "/api/family/me/report")}
_FILTERED = {"/api/devices", "/api/rooms", "/api/favorites"}
_OPEN_WS = {("WS", "/ws"), ("WS", "/api/family/live")}
_DEV_PATH = re.compile(r"^/api/devices/([^/]+)/(status|control)$")


def _rights(uid):
    return _load().get(uid, {}).get("rights") if uid else None


def _is_admin(uid) -> bool:
    """An administrator: marked so and with full access (a new QR with fewer rights leaves the mark without effect)."""
    d = _load().get(uid) if uid else None
    return bool(d and d.get("sha256") and d.get("admin") is True and d.get("rights") is None)


def _admin_check(request) -> bool:
    return _is_admin(_caller(request))


core.ADMIN_CHECKS.append(_admin_check)


def _trim(path: str, data, rights: dict):
    if path == "/api/devices":
        return [d for d in data if d.get("id") in rights]
    if path == "/api/favorites":
        return [f for f in data if f.get("device") in rights]
    if path == "/api/rooms":
        rooms = [{**r, "devices": [x for x in r.get("devices", []) if x in rights]} for r in data]
        return [r for r in rooms if r["devices"]]
    return data


@core.app.middleware("http")
async def _family_trim(request: Request, call_next):
    """What a restricted phone may call at all is _token_check's; here its lists lose the hidden devices."""
    resp = await call_next(request)
    if request.method != "GET" or request.url.path not in _FILTERED or resp.status_code != 200:
        return resp
    rights = _rights(_caller(request))
    if rights is None:
        return resp
    body = b"".join([chunk async for chunk in resp.body_iterator])
    keep = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-type")}
    return JSONResponse(_trim(request.url.path, json.loads(body), rights), headers=keep)


def _ws_trim(websocket, payload):
    """A phone's token is checked again on every tick: revoked or re-paired, it sees nothing."""
    tok = _token_of(websocket.headers, websocket.query_params)
    if not tok.startswith(PREFIX):
        return payload
    uid = _uid_of(tok)
    if uid is None:
        return {}
    rights = _rights(uid)
    return payload if rights is None else {k: v for k, v in payload.items() if k in rights}


def _rights_rename(old: str, new: str):
    """A renamed device keeps its place: the phones' rights, their app screens and their widgets."""
    with _lock:
        data = dict(_load())
        moved, fresh = False, []
        for uid, d in data.items():
            d = dict(d)
            r = d.get("rights")
            if isinstance(r, dict) and old in r:
                d["rights"] = {(new if k == old else k): v for k, v in r.items()}
            for key in ("screen", "widgets"):
                items = d.get(key)
                if isinstance(items, list) and any(isinstance(i, dict) and i.get("dev") == old for i in items):
                    d[key] = [{**i, "dev": new} if isinstance(i, dict) and i.get("dev") == old else i for i in items]
            if d != data[uid]:
                data[uid], moved = d, True
                fresh.append(uid)
        if moved:
            _save(data)
    for uid in fresh:                 # the next tick sends the whole state, under the new id
        _widget_sent.pop(uid, None)


core.ON_RENAME.append(_rights_rename)
core.WS_FILTERS.append(_ws_trim)


class RightsBody(core.Body):
    full: bool
    admin: bool = False           # full access plus: writes NFC cards and lets guests in (forces full)
    devices: dict[str, str] = {}


@core.app.get("/api/family/{uid}/rights")
async def family_rights_get(uid: str, request: Request):
    _dashboard_only(request)
    d = _load().get(_name(uid))
    if d is None:
        raise HTTPException(404, "unknown phone")
    r = d.get("rights")
    return {"uid": uid, "name": d.get("name", uid), "full": r is None, "admin": r is None and d.get("admin") is True, "devices": r or {}}


@core.app.put("/api/family/{uid}/rights")
def family_rights_put(uid: str, body: RightsBody, request: Request):
    _dashboard_only(request)
    uid = _name(uid)
    devs = {k: v for k, v in body.devices.items() if k in core.DEVICES and v in ("view", "control")}
    with _lock:
        data = dict(_load())
        if uid not in data:
            raise HTTPException(404, "unknown phone")
        full = body.full or body.admin
        entry = {k: v for k, v in data[uid].items() if k not in ("rights", "admin")}
        if body.admin:
            entry["admin"] = True
        if not full:
            entry["rights"] = devs
        data[uid] = entry
        _save(data)
    core.log.warning(f"[family] rights {uid}: {'administrator' if body.admin else 'full' if full else f'{len(devs)} device(s)'}")
    return {"uid": uid, "full": full, "admin": body.admin, "devices": devs if not full else {}}


class ScreenItem(core.Body):
    dev: str = Field(max_length=64)
    code: str = Field(max_length=64)


class ScreenBody(core.Body):
    items: list[ScreenItem]


def _screen_get(uid: str) -> dict:
    d = _load().get(uid)
    if d is None:
        raise HTTPException(404, "unknown phone")
    return {"uid": uid, "name": d.get("name", uid), "items": d.get("screen", [])}


def _screen_put(uid: str, body: ScreenBody) -> dict:
    rights = _rights(uid)
    items = [i.model_dump() for i in body.items
             if i.dev in core.DEVICES and (rights is None or i.dev in rights)][:60]
    with _lock:
        data = dict(_load())
        if uid not in data:
            raise HTTPException(404, "unknown phone")
        data[uid] = {**data[uid], "screen": items}
        _save(data)
    return {"uid": uid, "items": items}


@core.app.get("/api/family/me")
async def family_me(request: Request):
    uid = _caller(request)
    if uid is None:
        raise HTTPException(404, "not a family phone")
    return {**_screen_get(uid), "admin": _is_admin(uid)}


@core.app.get("/api/family/me/app")
def family_me_app(request: Request):
    """The app the house has built: what "Check for an update" in the app's settings asks; the phone compares its own
    version and fetches /plugins/family/yash.apk (the same command an update from the dashboard sends)."""
    if _caller(request) is None:
        raise HTTPException(404, "not a family phone")
    meta = _apk_meta()
    if meta is None:
        raise HTTPException(404, "no app here yet")
    return {**_update_cmd(meta), "name": meta["name"]}


@core.app.put("/api/family/me/screen")
def family_me_screen(body: ScreenBody, request: Request):
    uid = _caller(request)
    if uid is None:
        raise HTTPException(404, "not a family phone")
    return _screen_put(uid, body)


@core.app.get("/api/family/{uid}/screen")
async def family_screen_get(uid: str, request: Request):
    if _caller(request) not in (None, uid):
        raise HTTPException(403, "another person's screen")
    return _screen_get(_name(uid))


@core.app.put("/api/family/{uid}/screen")
def family_screen_put(uid: str, body: ScreenBody, request: Request):
    if _caller(request) not in (None, uid):
        raise HTTPException(403, "another person's screen")
    return _screen_put(_name(uid), body)


# ---- the phone's home-screen widgets: their channels' state reaches the phone as it changes ----
WIDGETS_MAX = 40
WIDGET_TICK_S = 2
_widget_sent: dict = {}        # uid -> the state last delivered (live or push)
_settings: dict = dict(core.cfg_section("family", {}))
core.SETTINGS_SECTIONS["family"] = lambda: _settings


class WidgetItem(core.Body):
    dev: str = Field(max_length=64)
    code: str = Field(max_length=64)


class WidgetsBody(core.Body):
    items: list[WidgetItem] = Field(max_length=WIDGETS_MAX)


def _visible(uid: str, items: list) -> list:
    """The widget channels this phone may see now (rights change, devices go)."""
    rights = _rights(uid)
    return [i for i in items if isinstance(i, dict) and i.get("dev") in core.DEVICES
            and (rights is None or i.get("dev") in rights)]


def default_server_url() -> str:
    return f"http://{_BIND}:{os.environ.get('DASHBOARD_PORT') or 8765}" if _BIND else ""


def server_url() -> str:
    """The address the phones reach this house at (it goes into every pairing QR): the one set on the Family page, else this house's
    own on its network (BIND_ADDRESS and the dashboard's port)."""
    return (_settings.get("server_url") or default_server_url()).rstrip("/")


class FamilySettings(core.Body):
    widget_push: Optional[bool] = None
    server_url: Optional[str] = Field(default=None, max_length=200)      # "" = this house's own address on its network


def _settings_view() -> dict:
    return {"widget_push": bool(_settings.get("widget_push")), "cloud": CLOUD, "server_url": _settings.get("server_url") or "",
            "server_url_default": default_server_url(), "server_url_warning": _server_url_warning()}


@core.app.get("/api/family/settings")
async def family_settings_get(request: Request):
    _dashboard_only(request)
    return _settings_view()


@core.app.put("/api/family/settings")
def family_settings_put(body: FamilySettings, request: Request):
    """widget_push: a data-less Firebase message may wake a phone's widgets (it then asks the house over HTTPS; nothing of the house
    goes through Google). Off by default. server_url: the address for the phones when it is not this house's own on its network
    (a name over https, a VPN address); "" = that one again. Each field given is set."""
    core.house_token_only(request)
    if body.server_url is not None:
        url = body.server_url.strip().rstrip("/")
        if url and not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?", url):
            raise HTTPException(400, "the address: http(s)://host[:port]")
        if url:
            _settings["server_url"] = url
        else:
            _settings.pop("server_url", None)
    if body.widget_push is not None:
        _settings["widget_push"] = body.widget_push
    core.save_settings()
    return _settings_view()


@core.app.put("/api/family/me/widgets")
def family_me_widgets(body: WidgetsBody, request: Request):
    """The channels this phone's widgets show: their state is sent to it whenever it changes."""
    uid = _caller(request)
    if uid is None:
        raise HTTPException(404, "not a family phone")
    items, seen = [], set()
    for i in _visible(uid, [x.model_dump() for x in body.items]):
        if (i["dev"], i["code"]) not in seen:
            seen.add((i["dev"], i["code"]))
            items.append(i)
    with _lock:
        data = dict(_load())
        if uid not in data:
            raise HTTPException(404, "unknown phone")
        data[uid] = {**data[uid], "widgets": items}
        _save(data)
    _widget_sent.pop(uid, None)
    return {"items": items}


def _widget_state(items: list) -> dict:
    """{dev: {code: value, ..., "online": bool}} of these channels, as the dashboard reads them."""
    out: dict = {}
    for i in items:
        st = core.status_copy(i["dev"], {})
        d = out.setdefault(i["dev"], {"online": bool(st.get("online"))})
        d[i["code"]] = (st.get("values") or {}).get(i["code"])
    return out


TICKLE_GAP_S = 120              # a normal-priority tickle at most this often per phone
TICKLE_HIGH_GAP_S = 10          # a lock's state: sooner
WIDGET_INSTANT = True           # a widget shows the true state whenever the screen is looked at
TICKLE_INSTANT_GAP_S = 5        # instant: a change of a widget channel is a high-priority tickle at most this often per phone; changes inside the gap ride the next pull
TICKLE_RETRY_S = 600            # a tickle the phone has not answered with a pull is repeated at most this often
_tickled: dict = {}             # uid -> when the last tickle went
_tickle_open: dict = {}         # uid -> a tickle went and the phone has not pulled since
_tickle_n: dict = {}            # uid -> tickles in a row the phone has not answered (the gap doubles with each, up to TICKLE_RETRY_S)
_widget_pend: dict = {}         # uid -> [(t, state, when)] of the last pull answers: the one the phone acknowledges becomes the baseline
_pending_cmds: dict = {}        # uid -> commands (an app update) waiting for the phone's next pull


def _tickle(uid: str, high: bool = False) -> bool:
    """A data-less FCM message {cmd: pull}: the phone wakes and asks the house over HTTPS for what it needs. Nothing of the house goes
    through Google. High priority only for what must be instant (a lock, an update the dashboard asked for)."""
    try:
        _bridge("POST", "/tickle", {"uid": uid, "priority": "high" if high else "normal"}, timeout=3)
    except HTTPException as e:
        core.log.info(f"[family] tickle {uid}: {e.detail}")
        return False
    _tickled[uid], _tickle_open[uid] = time.time(), True
    _tickle_n[uid] = _tickle_n.get(uid, 0) + 1
    core.log.info(f"[family] tickle {uid} {'high' if high else 'normal'}")                # counted from the log: tickles per hour
    return True


def _is_lock(dev: str) -> bool:
    return any(c.get("code") in ("locked", "lock") for c in (core.DEVICES.get(dev) or {}).get("controls") or [])


def _widget_tick():
    """One look at every phone's widget channels: what changed goes down the live link (the app screen is open), else (with the
    house's consent, widget_push) a tickle through Firebase; the phone then pulls the whole change. A phone's failure never holds up the others."""
    now = time.time()
    for uid, d in list(_load().items()):
        try:
            items = _visible(uid, d.get("widgets") or [])
            if not items:
                continue
            cur = _widget_state(items)
            last = _widget_sent.get(uid)
            pend = (_widget_pend.get(uid) or [None])[-1]
            if pend and now - pend[2] < TICKLE_RETRY_S:
                last = pend[1]                                   # a pull answer is on its way to the phone: compare with what it carried
            changed = {dev: v for dev, v in cur.items() if last is None or last.get(dev) != v}
            if not changed:
                continue
            if _send_live(uid, {"cmd": "state", "values": changed, "t": int(now * 1000)}):
                _widget_sent[uid] = cur
            elif CLOUD and _settings.get("widget_push"):
                high = WIDGET_INSTANT or any(_is_lock(dev) for dev in changed)            # every channel here is on a widget of this phone
                gap = (TICKLE_INSTANT_GAP_S if WIDGET_INSTANT else TICKLE_HIGH_GAP_S) if high else TICKLE_GAP_S
                if _tickle_open.get(uid):                        # the phone has not answered the last one: a lock's gap doubles each time
                    gap = min(gap * 2 ** max(0, _tickle_n.get(uid, 0) - 1), TICKLE_RETRY_S) if high else max(gap, TICKLE_RETRY_S)

                if now - _tickled.get(uid, 0) >= gap:
                    _tickled[uid] = now                          # one try per gap, however it ends
                    _tickle(uid, high)
        except Exception as e:                                  # noqa: BLE001 - logged; the next phone goes on
            core.log.info(f"[family] widgets of {uid}: not sent ({getattr(e, 'detail', e)})")


@core.app.get("/api/family/me/pull")
def family_me_pull(request: Request, v: int = 0, all: int = 0, ack: int = 0):
    """What a tickle asked the phone to fetch: {cmds: [an app update ...], state: {the widget channels that changed}, t, srv}. The phone says
    in `ack` which answer (its `t`) it applied last: only then is that state the baseline, so an answer lost on the way is sent again. The
    network headers (X-Net-*, percent-encoded) are a report too: every pull tells the house where the phone is. `srv` is the address for
    the phones when the Family page sets one: a phone paired at another moves to it once its own token works there (app 0.32)."""
    uid = _caller(request)
    if uid is None:
        raise HTTPException(404, "not a family phone")
    _announce_seen(uid)
    h = request.headers
    if "x-net-gw" in h:
        un = lambda v: _clean_net(urllib.parse.unquote(v))        # noqa: E731 - clipped and cleaned before use or log
        _note_gateway(uid, un(h["x-net-gw"]), un(h.get("x-net-ssid", "")), un(h.get("x-net-wifi-ip", "")), h.get("x-net-ssid-known", "1") != "0")
    pends = _widget_pend.setdefault(uid, [])
    for pt, ps, _ in pends:
        if ack and ack == pt:
            _widget_sent[uid] = ps
    items = _visible(uid, (_load().get(uid) or {}).get("widgets") or [])
    cur = _widget_state(items)
    last = None if all else _widget_sent.get(uid)                 # all=1: the whole state (the phone's own 15-minute refresh)
    changed = {dev: val for dev, val in cur.items() if last is None or last.get(dev) != val}
    t = int(time.time() * 1000)
    pends.append((t, cur, time.time()))
    del pends[:-4]
    _tickle_open.pop(uid, None)
    _tickle_n.pop(uid, None)
    cmds = _pending_cmds.pop(uid, [])
    if v > 0:
        _versions[uid] = v
        meta = _apk_meta() if AUTO_UPDATE else None
        if _needs_update(uid, meta) and time.time() - _update_sent.get(uid, 0) >= UPDATE_GAP_S and not any(c.get("cmd") == "update" for c in cmds):
            cmds.append(_update_cmd(meta))
            _update_sent[uid] = time.time()
    out = {"cmds": cmds, "state": changed, "t": t}
    if _settings.get("server_url"):                               # only one set on purpose: a phone typed to a VPN address stays there
        out["srv"] = server_url()
    return out


class ReportBody(core.Body):
    gw: str = Field("", max_length=64)
    ssid: str = Field("", max_length=64)
    wifi_ip: str = Field("", max_length=64)
    ssid_known: bool = True          # False: the app may not read the Wi-Fi name (no location access): the name is not asked of it
    v: int = Field(0, ge=0, le=10 ** 9)


@core.app.post("/api/family/me/report")
def family_me_report(body: ReportBody, request: Request):
    """What the app says about itself when its network changes (and at its start): the Wi-Fi gateway and address, the network's name and
    its version. Home or away is decided from it and stays decided until the next report."""
    uid = _caller(request)
    if uid is None:
        raise HTTPException(404, "not a family phone")
    _note_report(uid, body.model_dump())
    return {"ok": True}


class NoteBody(core.Body):
    text: str = Field(..., min_length=1, max_length=200)


NOTE_MAX, NOTE_WINDOW_S = 20, 3600      # notes one phone may log per window
_notes: dict = {}                       # uid -> times of its notes in the window


@core.use_route
@core.app.post("/api/family/me/note")
def family_me_note(body: NoteBody, request: Request):
    """TEMPORARY (the app's rotation guard): one line from a full-access phone into the house log, NOTE_MAX an hour."""
    uid = _caller(request)
    if uid is None:
        raise HTTPException(404, "not a family phone")
    now = time.time()
    with _lock:
        seen = [t for t in _notes.get(uid, []) if now - t < NOTE_WINDOW_S]
        if len(seen) >= NOTE_MAX:
            raise HTTPException(429, "too many notes: try later")
        _notes[uid] = seen + [now]
    text = "".join(ch if ch.isprintable() else " " for ch in body.text)
    core.log.info(f"[family] note {uid}: {text}")
    return {"ok": True}


def _widget_loop():
    while True:
        try:
            _widget_tick()
        except Exception as e:                                  # noqa: BLE001 - logged, the loop goes on
            core.log.warning(f"[family] widget state: {type(e).__name__}: {e}")
        time.sleep(WIDGET_TICK_S)


core.STARTUP_TASKS.append(_widget_loop)


def _server_url_warning() -> str:
    """A house with Firebase whose address for the phones is a LAN or VPN one: the tickle wakes the phone anywhere, but its pull then fails
    away from home (the phone shows no connection). Empty when the address is reachable from outside."""
    host = urllib.parse.urlparse(server_url()).hostname or ""
    try:
        a = ipaddress.ip_address(host)
        private = not a.is_global                  # a LAN, a VPN, loopback, link-local
    except ValueError:
        private = bool(host) and (host.endswith((".local", ".lan", ".internal", ".home.arpa")) or "." not in host)
    if CLOUD and server_url() and private:
        return f"the address for the phones ({host}) is not reachable from outside the house network: away from home they cannot pull (widgets, updates)"
    return ""


def _warn_server_url():
    w = _server_url_warning()
    if w:
        core.log.warning(f"[family] {w}")


core.STARTUP_TASKS.append(_warn_server_url)


# ---- app updates: the house tells a phone to fetch the built APK and install it ----------------
_versions: dict = {}           # uid -> the versionCode its app reported over the live link
_update_sent: dict = {}        # uid -> when an update command last went to it
_meta_cache: dict = {}


def _axml_manifest(raw: bytes) -> dict:
    """The attributes of <manifest> in a binary AndroidManifest.xml: {name: int or str}."""
    pos = struct.unpack_from("<H", raw, 2)[0]                   # the file header, then the chunks
    strings, out = [], {}
    while pos + 8 <= len(raw):
        kind, hsize, size = struct.unpack_from("<HHI", raw, pos)
        if kind == 0x0001:                                      # the string pool
            count, _, flags, start = struct.unpack_from("<IIII", raw, pos + 8)
            utf8 = bool(flags & 0x100)
            for i in range(count):
                off = pos + start + struct.unpack_from("<I", raw, pos + hsize + 4 * i)[0]
                if utf8:
                    off += 2 if raw[off] & 0x80 else 1          # the char length, then the byte length
                    n = raw[off]
                    off += 1
                    if n & 0x80:
                        n, off = ((n & 0x7F) << 8) | raw[off], off + 1
                    strings.append(raw[off:off + n].decode("utf-8", "replace"))
                else:
                    n = struct.unpack_from("<H", raw, off)[0]
                    strings.append(raw[off + 2:off + 2 + 2 * n].decode("utf-16-le", "replace"))
        elif kind == 0x0102:                                    # the first start tag is <manifest>
            ext = pos + 16
            a_start, a_size, a_count = struct.unpack_from("<HHH", raw, ext + 8)
            for i in range(a_count):
                a = ext + a_start + i * a_size
                name, rawv, _, _, dtype, data = struct.unpack_from("<iiHBBI", raw, a + 4)
                out[strings[name]] = strings[rawv] if rawv >= 0 else (data if dtype == 0x10 else None)
            return out
        pos += size
    return out


def _apk_meta():
    """versionCode, versionName, size and sha256 of the built APK (cached by mtime), or None."""
    f = _apk_file()
    if f is None:
        return None
    st = f.stat()
    key = (str(f), st.st_mtime_ns, st.st_size)
    if _meta_cache.get("key") != key:
        meta = None
        try:
            with zipfile.ZipFile(f) as z:
                attrs = _axml_manifest(z.read("AndroidManifest.xml"))
            if isinstance(attrs.get("versionCode"), int):
                meta = {"code": attrs["versionCode"], "name": str(attrs.get("versionName") or ""),
                        "size": st.st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
        except (OSError, KeyError, ValueError, struct.error, zipfile.BadZipFile) as e:
            core.log.warning(f"[family] cannot read the app's version: {type(e).__name__}: {e}")
        _meta_cache.update(key=key, meta=meta)
    return _meta_cache["meta"]


def _needs_update(uid: str, meta) -> bool:
    got = _versions.get(uid)
    return bool(meta and got is not None and got < meta["code"])


def _update_cmd(meta) -> dict:
    return {"cmd": "update", "code": meta["code"], "sha256": meta["sha256"], "size": meta["size"], "path": "/plugins/family/yash.apk"}


def _send_update(uid: str, meta) -> bool:
    """The command goes down the live link; without one it waits for the phone's next pull and the phone is tickled (high priority).
    The app fetches the APK from its own house address."""
    cmd = _update_cmd(meta)
    if not _send_live(uid, cmd):
        if not CLOUD:
            return False
        _pending_cmds[uid] = [c for c in _pending_cmds.get(uid, []) if c.get("cmd") != "update"] + [cmd]
        if not _tickle(uid, True):
            return False
    _update_sent[uid] = time.time()
    return True


GUEST_CMDS_MAX = 5              # entry requests one phone keeps waiting for its next pull


def _admin_send(cmd: dict, same) -> None:
    """cmd to every administrator's phone: the live link first; without one it waits for the next pull (an older one `same`
    names is replaced, at most GUEST_CMDS_MAX of its kind) and the phone is tickled. Nothing of the house goes via Google."""
    for uid in list(_load()):
        if not _is_admin(uid):
            continue
        try:
            if _send_live(uid, cmd):
                continue
            q = _pending_cmds.get(uid, [])
            kind = [c for c in q if c.get("cmd") == cmd["cmd"] and not same(c)] + [cmd]
            _pending_cmds[uid] = [c for c in q if c.get("cmd") != cmd["cmd"]] + kind[-GUEST_CMDS_MAX:]
            if CLOUD:
                _tickle(uid, high=True)
        except Exception as e:                           # noqa: BLE001  one phone never stops the others
            core.log.warning(f"[family] {cmd['cmd']} to {uid}: {type(e).__name__}: {e}")


def _guest_send(req: dict) -> None:
    """A browser asks to come in: every administrator's phone hears it (no other may allow it). Only {id, code, browser}: no secret."""
    cmd = {"cmd": "guest", "id": str(req.get("id") or ""), "code": str(req.get("code") or ""), "ua": str(req.get("ua") or ""),
           "waiting": int(req.get("waiting") or 1), "paused_until": int(req.get("paused_until") or 0)}
    _admin_send(cmd, lambda c: True)              # one notice on the phone: a newer one replaces the waiting one


def _guest_notify(req: dict) -> None:
    threading.Thread(target=_guest_send, args=(dict(req),), daemon=True, name="family-guest").start()


core.GUEST_NOTIFY.append(_guest_notify)


def _nfc_write_send(sid: str) -> None:
    """The house waits for a card to write: the administrators' phones open their "hold the card" screen. One session at a
    time: a waiting older one is replaced. Only the session id goes; the keys stay in the house."""
    _admin_send({"cmd": "nfc_write", "sid": str(sid)}, lambda c: True)


def _nfc_write_notify(sid: str) -> None:
    threading.Thread(target=_nfc_write_send, args=(str(sid),), daemon=True, name="family-nfc-write").start()


core.NFC_WRITE.append(_nfc_write_notify)


_seen_warned: dict = {}


def _announce_seen(uid: str) -> None:
    """The app reported: the voice plug-in's card of this phone (push-to-talk satellite) shows the app as present."""
    try:
        core.mqtt_pub(core.mqtt_client, f"voice/phone/{uid}/seen", "1")
    except Exception as e:                               # noqa: BLE001  no broker: the next report tries again
        if time.time() - _seen_warned.get(uid, 0) > 600:
            _seen_warned[uid] = time.time()
            core.log.warning(f"[family] the app report of {uid} could not be announced: {type(e).__name__}: {e}")


def _note_report(uid: str, msg: dict):
    """What the app says about itself when its network changes (every pull carries it too): its Wi-Fi gateway (home or not) and its version."""
    _announce_seen(uid)
    if "gw" in msg:
        _note_gateway(uid, msg["gw"], msg.get("ssid"), msg.get("wifi_ip"), msg.get("ssid_known", True))
    v = msg.get("v")
    if isinstance(v, int) and not isinstance(v, bool) and v > 0:
        _versions[uid] = v
        meta = _apk_meta() if AUTO_UPDATE else None
        if _needs_update(uid, meta) and time.time() - _update_sent.get(uid, 0) >= UPDATE_GAP_S:
            threading.Thread(target=_send_update, args=(uid, meta), daemon=True).start()


class UpdateBody(core.Body):
    name: str = Field("", max_length=20)          # one phone; empty = every phone with an older app


@core.app.post("/api/family/update")
async def family_update(body: UpdateBody, request: Request):
    """Tell the phones whose app is older than the built APK to fetch and install it."""
    _dashboard_only(request)
    meta = _apk_meta()
    if meta is None:
        raise HTTPException(404, "no app here yet: Family phone -> Build the app (or app/build.sh)")
    names = [_name(body.name)] if body.name else sorted(_load())
    sent, skipped = [], []
    for uid in names:
        if uid not in _load():
            raise HTTPException(404, "unknown phone")
        ok = await run_in_threadpool(_needs_update, uid, meta) and await run_in_threadpool(_send_update, uid, meta)
        (sent if ok else skipped).append(uid)
    return {"sent": sent, "skipped": skipped, "code": meta["code"]}


# ---- presence: a phone with its live link open from the house network is at home ----------------
# Each paired phone gets the device person_<name> (home, event). The address is the socket's own:
# a proxy's or a VPN's address is not a home address (the house's network: BIND_ADDRESS's /24);
# the app also reports its Wi-Fi gateway, which counts when the house learned it (home_wifi.json).
_lan_seen: dict = {}           # uid -> when a live link from a home address was last open
_away_since: dict = {}         # uid -> when a report said "not the house network"; only a home report/pull clears it
_person_state: dict = {}       # uid -> home (bool) as last published
_person_devs: set = set()      # the person_<name> devices this plug-in registered
_started = time.time()


def _is_home_addr(host: str) -> bool:
    try:
        a = ipaddress.ip_address(host)
    except ValueError:
        return False
    a = getattr(a, "ipv4_mapped", None) or a
    return any(a in n for n in _home_nets() if n.version == a.version)


def _home_nets() -> list:
    """The house's network: BIND_ADDRESS's /24, else the /24 of the phones' address when it is a private IPv4 one;
    else none, and no address counts as home (a guessed range would take a friend's router for the house)."""
    if HOME_NETS:
        return HOME_NETS
    try:
        a = ipaddress.ip_address(urllib.parse.urlsplit(server_url()).hostname or "")
    except ValueError:
        return []
    return [ipaddress.ip_network(f"{a}/24", strict=False)] if a.version == 4 and a.is_private else []


_reported: dict = {}           # uid -> (gateway, ssid, wifi address) last reported, to log a change once
NET_HOME_MAX_S = 24 * 3600     # "home" drops on an away report or after this long without any contact; a home pull or report renews it
NET_FIELD_MAX = 64             # a network value from the phone is clipped to this many characters
_net_home: dict = {}           # uid -> [home?, when] from the app's last network report (kept over a restart)


def _net_file():
    return core.plugin_state("family") / "net_home.json"


def _net_save() -> None:
    try:
        core.atomic_json_dump(_net_file(), _net_home, indent=1)
    except OSError as e:
        core.log.warning(f"[family] network reports not saved: {e}")


def _net_load() -> None:
    try:
        for u, v in json.loads(_net_file().read_text(encoding="utf-8")).items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], bool) and isinstance(v[1], (int, float)):
                _net_home[u] = v                                # a file of another shape is not believed
                if not v[0]:
                    _away_since[u] = v[1]                       # an away report survives a restart: the socket may not undo it
    except (OSError, ValueError, AttributeError):
        pass


_net_load()


def _clean_net(v) -> str:
    """A network value from the phone: text only, control characters dropped, at most NET_FIELD_MAX characters."""
    if not isinstance(v, str):
        return ""
    return "".join(c for c in v if c.isprintable())[:NET_FIELD_MAX].strip()


def _wifi_file():
    return core.plugin_state("family") / "home_wifi.json"


def _home_wifi() -> dict:
    """The house's Wi-Fi as the phones showed it: {"gateways": [...], "ssids": [...]} (learned, plugin-state/family/home_wifi.json)."""
    try:
        v = json.loads(_wifi_file().read_text(encoding="utf-8"))
        return {k: [str(x) for x in v.get(k) or [] if isinstance(x, str)][:8] for k in ("gateways", "ssids")}
    except (OSError, ValueError, AttributeError):
        return {"gateways": [], "ssids": []}


def _learn_wifi(gw: str, ssid: str) -> None:
    w, changed = _home_wifi(), False
    for k, v in (("gateways", gw), ("ssids", ssid)):
        if v and v not in w[k]:
            w[k] = (w[k] + [v])[-8:]
            changed = True
    if changed:
        try:
            core.atomic_json_dump(_wifi_file(), w, indent=1, ensure_ascii=False)
            core.log.info(f"[family] the house's Wi-Fi, as a phone at home shows it: gateways {w['gateways']} names {w['ssids']}")
        except OSError as e:
            core.log.warning(f"[family] home Wi-Fi not saved: {e}")


def _note_gateway(uid: str, gw, ssid=None, wifi_ip=None, ssid_known: bool = True) -> bool:
    """The app reports its Wi-Fi network's gateway, its OWN address on that network (a VPN's is skipped) and, with the location
    permission, the network's name. The phone is home when the address is in the house's network or the gateway is one the house
    learned (even when a VPN carries its live link) AND, once a name is learned, the NAME matches (a router elsewhere may have the same
    default address and subnet); a phone that may not read the name (ssid_known False) is judged by the address alone. A report from
    the house's network through a gateway the house knows (or the first one) teaches it that gateway, and the name when none is
    known yet; a report whose name did not match teaches nothing. The socket address is no help here: it is the reverse proxy's."""
    gw_s, ssid_s, ip_s = _clean_net(gw), _clean_net(ssid), _clean_net(wifi_ip)
    known = _home_wifi()
    addr_home = bool(ip_s) and _is_home_addr(ip_s)
    net_ok = addr_home or (bool(gw_s) and gw_s in known["gateways"])
    ok = net_ok and (not known["ssids"] or not ssid_known or ssid_s in known["ssids"])
    first_ssid = ssid_known and not known["ssids"]
    full_ok = ok and (not known["ssids"] or (ssid_known and ssid_s in known["ssids"]))
    if addr_home and full_ok and gw_s and (not known["gateways"] or gw_s in known["gateways"]):
        _learn_wifi(gw_s, ssid_s if first_ssid else "")       # a name is learned once; never from a report that failed it
    if _reported.get(uid) != (gw_s, ssid_s, ip_s):
        _reported[uid] = (gw_s, ssid_s, ip_s)
        core.log.info(f"[family] {uid} reports Wi-Fi gateway {gw_s!r} address {ip_s!r} ssid {ssid_s!r}: {'home' if ok else 'not the house network'}")
    if ok:
        _away_since.pop(uid, None)
        _lan_seen[uid] = time.time()
    else:
        _away_since[uid] = time.time()
        _lan_seen.pop(uid, None)                               # a positive away report: not home at once, no grace
    if (_net_home.get(uid) or [None])[0] != ok or time.time() - (_net_home.get(uid) or [0, 0])[1] > 600:
        _net_home[uid] = [ok, time.time()]                     # a state: home until a report says otherwise (a change is written at once)
        _net_save()
    return ok


def _person_id(uid: str) -> str:
    return "person_" + uid


def _presence_tick(now: float):
    """Register a device per paired phone (drop the revoked), publish home / not home and the event."""
    data = _load()
    paired = {u: d for u, d in data.items() if d.get("sha256") and d.get("claimed") is not False}
    for uid, d in paired.items():
        dev = _person_id(uid)
        if dev not in core.DEVICES:
            try:
                core.register_device({"id": dev, "name": str(d.get("name") or uid), "transport": "family",
                                      "category": "indicator", "virtual": "person", "controls": [
                                          {"kind": "sensor", "code": "home", "label": "At home", "ro": True,
                                           "vtype": "bool"},
                                          {"kind": "trigger_text", "code": "event", "label": "Event", "ro": True,
                                           "values": ["arrival", "departure"]}]})
            except ValueError as e:
                core.log.warning(f"[family] presence device {dev}: {e}")
                continue
            _person_devs.add(dev)
        nh = _net_home.get(uid)
        if nh and nh[0] and now - nh[1] < NET_HOME_MAX_S:
            _lan_seen[uid] = now                               # the last report said home and nothing has said otherwise
        src = d.get("presence")                               # its presence device (Family page), when it has one
        if src and core.status_copy(src, {}).get("values", {}).get("home") is True:
            _lan_seen[uid] = now
        home = now - _lan_seen.get(uid, 0) < PRESENCE_GRACE_S
        prev = _person_state.get(uid)
        if home == prev or (prev is None and not home and now - _started < PRESENCE_GRACE_S):
            continue                  # unchanged, or just started and the phone has not reconnected yet
        _person_state[uid] = home
        st = core.status_copy(dev, {"online": False, "values": {}, "raw": {}})
        st["values"], st["online"] = {**st.get("values", {}), "home": home}, True
        text = "arrival" if home else "departure"
        if prev is not None:
            st.update(last_event_ts=now, last_event={"code": "event", "value": text})
        core.store_status(dev, st)
        core.fire_sensor_triggers(dev, core.DEVICES[dev], {"home": home})
        if prev is not None:
            with core.events_lock:
                core.events[dev].append({"ts": now, "code": "event", "value": text, "t": int(now)})
            core.bg.submit(core.safe_execute_binding, dev, "event", text)
    _presence_forget(keep=set(paired))


def _presence_forget(keep=()):
    """Drop the person_<name> devices (and their state) of every phone not in keep."""
    for dev in [x for x in _person_devs if x[len("person_"):] not in keep]:
        uid = dev[len("person_"):]
        _person_devs.discard(dev)
        _person_state.pop(uid, None)
        _lan_seen.pop(uid, None)
        _away_since.pop(uid, None)
        core.remove_device(dev, "family")
    gone = [u for u in _net_home if u not in keep]             # a phone paired again must not inherit the old "home"
    for u in gone:
        del _net_home[u]
        _away_since.pop(u, None)
        _reported.pop(u, None)
    if gone:
        _net_save()


def _presence_loop():
    while True:
        try:
            _presence_tick(time.time())
        except Exception as e:                                  # noqa: BLE001 - logged, the loop goes on
            core.log.warning(f"[family] presence: {type(e).__name__}: {e}")
        time.sleep(LIVE_CHECK_S)


core.STARTUP_TASKS.append(_presence_loop)


def _health():
    if not CLOUD:
        paired = [u for u, d in _load().items() if d.get("sha256") and d.get("claimed") is not False]
        on = sum(1 for u in paired if _live.get(u))
        return [core.health_row("family", "ok" if on or not paired else "warn", "Family phones",
                                "dashboard only (no Firebase): {0} of {1} connected",
                                "" if on or not paired else "A phone reaches the house only on its network or a VPN",
                                (on, len(paired)))]
    if not BRIDGE_KEY:
        return [core.health_row("family", "warn", "Family bridge", "FAMILY_BRIDGE_KEY is not set", "An update generates it", cmd="make update")]
    try:
        _bridge("GET", "/health", timeout=4)
    except HTTPException as e:
        return [core.health_row("family", "bad", "Family bridge", "{0}",
                                "Is the family-bridge container running?", (e.detail,), cmd="docker compose logs family-bridge")]
    return [core.health_row("family", "ok", "Family bridge")]


core.HEALTH_CHECKS.append(_health)
