# The core: a browser without the front door's login asks to come in; a signed-in screen allows it.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# The proxy lets through without its login only /guest, the request routes and keys that start
# with yg_, so the house checks everything about guests itself. A guest key never reaches a lock:
# "no_locks" may use a short list of routes, "view" only reads.
GUEST_PREFIX = "yg_"
GUEST_SCOPES = ("no_locks", "view")
GUEST_HOURS = (1, 2, 8, 24)
GUEST_PENDING_S = 300            # a request waits 5 min for an answer
GUEST_KEEP_S = 900               # a decided request is forgotten after 15 min
GUEST_PENDING_MAX = 10
GUEST_RATE = (5, 600)            # 5 requests per IP in 10 min
GUEST_KEYS_MAX = 50

_guest_lock = threading.RLock()
_guest_reqs: dict = {}           # id -> {secret, code, created, ip, ua, state, token, hours}
_guest_hits: dict = {}           # ip -> [request times]
_guest_keys: dict = {}           # gid -> {sha256, scope, expires, ip, ua, created, label}


def _guest_file() -> Path:
    """plugin-state/guest/keys.json: only sha256 of each key, never the key (the directory made on the first save)."""
    return PLUGIN_STATE / "guest" / "keys.json"


def _guest_save():
    path = plugin_state("guest") / "keys.json"
    _atomic_json_dump(path, _guest_keys, indent=1)
    os.chmod(path, 0o600)


def _guest_load():
    try:
        raw = json.loads(_guest_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception as e:                        # noqa: BLE001  a bad file: no guest key works
        log.error(f"[guest] keys.json unreadable: {e}")
        return
    now = time.time()
    keep = {g: k for g, k in (raw if isinstance(raw, dict) else {}).items()
            if isinstance(k, dict) and k.get("scope") in GUEST_SCOPES and float(k.get("expires") or 0) > now}
    _guest_keys.update(keep)
    if len(keep) != len(raw):
        _guest_save()


try:
    _guest_load()
except Exception as e:                            # noqa: BLE001  never stops the house
    log.error(f"[guest] load: {e}")


def _guest_ua(ua: str) -> str:
    """'Chrome on Windows' from a User-Agent, at most 40 printable characters."""
    ua = ua or ""
    browser = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                   ("Safari/", "Safari")) if k in ua), "Browser")
    osn = next((n for k, n in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
                               ("CrOS", "ChromeOS"), ("Mac OS", "Mac"), ("Linux", "Linux")) if k in ua), "")
    text = f"{browser} on {osn}" if osn else browser
    return "".join(c for c in text if " " <= c <= "~")[:40]


def _guest_ip(request: Request) -> str:
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    return "".join(c for c in ip if c.isalnum() or c in ".:")[:45] or "?"


def _guest_sweep(now: float):
    """Pending requests turn expired after 5 min; decided ones and expired keys are dropped."""
    for rid, r in list(_guest_reqs.items()):
        if r["state"] == "pending" and now - r["created"] > GUEST_PENDING_S:
            r["state"] = "expired"
        if now - r["created"] > GUEST_KEEP_S:
            _guest_reqs.pop(rid, None)
    gone = [g for g, k in _guest_keys.items() if k["expires"] <= now]
    for g in gone:
        _guest_keys.pop(g)
    if gone:
        _guest_save()
    for ip in list(_guest_hits):
        _guest_hits[ip] = [t for t in _guest_hits[ip] if now - t < GUEST_RATE[1]]
        if not _guest_hits[ip]:
            del _guest_hits[ip]


def _guest_key_of(tok: str):
    """The live key record of a yg_ token, else None."""
    if not isinstance(tok, str) or not tok.startswith(GUEST_PREFIX):
        return None
    h = hashlib.sha256(tok.encode()).hexdigest()
    now = time.time()
    with _guest_lock:
        return next((dict(k) for k in _guest_keys.values()
                     if hmac.compare_digest(h, k.get("sha256") or "") and k["expires"] > now), None)


def _is_lockish(dev_id: str) -> bool:
    """A lock or a gate: by its category, a channel named lock/locked/gate, or anything marked sensitive."""
    d = DEVICES.get(dev_id) or {}
    if d.get("category") in ("lock", "gate", "garage") or d.get("sensitive"):
        return True
    codes = [str(c.get("code") or "") for c in d.get("controls") or []] + list((d.get("_ctl") or {}).keys())
    return any("lock" in c.lower() or "gate" in c.lower() for c in codes) \
        or any(c.get("kind") == "lock" for c in d.get("controls") or [])


def lock_reach() -> tuple:
    """(device ids, rule ids) whose use can reach a lock or a gate: the locks, a switch wired to one (through any
    chain of wiring), a device or button a binding leads from to one of them, and every rule acting on them.
    Rebuilt on each call (a guest's change only): the wiring, the bindings and the rules as they are now."""
    devs = {d for d in list(DEVICES) if _is_lockish(d)}
    try:
        wires = [(e["switch"]["dev"], e["target"].get("dev") or e["target"].get("load") or "") for e in wiring()]
    except Exception:                             # noqa: BLE001  unreadable wiring: every wired switch counts
        wires = []
        devs |= {d for d in DEVICES if d}
    with _bindings_lock:
        binds = [(src, b.get("target") or "") for src, codes in _bindings.items() for gs in codes.values()
                 for b in gs.values() if isinstance(b, dict)]
    grew = True
    while grew:
        before = len(devs)
        devs |= {sw for sw, tg in wires if tg in devs}
        devs |= {src for src, tg in binds if tg in devs}
        grew = len(devs) != before
    rules = set()
    for r in _project_rules():
        touched = {t.get("device") for t in r.get("then") or []} | {w.get("device") for w in r.get("when") or []}
        if touched & devs:
            rules.add(r["id"])
    return devs, rules


# what a no_locks key may change: a device, its timer, the player, an IR button, a favourite; never one
# lock_reach names. Rules, bindings, voice and any other route could reach a lock: refused.
_GUEST_USE = (re.compile(r"^/api/devices/([^/]+)/control$"), re.compile(r"^/api/automation/([^/]+)/[^/]+$"),
              re.compile(r"^/api/ir/remotes/([^/]+)/buttons/[^/]+/send$"), re.compile(r"^/api/player/.+$"),
              re.compile(r"^/api/favorites/.+$"))


# never for a guest, reading included: cameras, who came and went, voice, the network and the house's machinery
_GUEST_HIDDEN = ("/api/guest", "/api/family", "/api/signin", "/api/camera", "/api/cameras", "/api/history", "/api/voice",
                 "/api/helper", "/api/build", "/api/computers", "/api/esphome", "/api/fwgen", "/api/pair", "/api/nfc",
                 "/api/zigbee", "/api/backups", "/api/wiring", "/api/touchmouse", "/api/panels", "/api/stats",
                 "/plugins/cameras/", "/plugins/zigbee/ui/")


def _guest_allowed(scope: str, method: str, path: str) -> bool:
    if path.startswith(_GUEST_HIDDEN):
        return False
    if method in ("GET", "HEAD"):
        return True
    if method == "WS":
        return path == "/ws"
    if scope != "no_locks" or not is_use_route(method, path):
        return False
    for rx in _GUEST_USE:
        m = rx.match(path)
        if m:
            return not (m.groups() and m.group(1) in lock_reach()[0])
    return False


def _guest_token_check(tok: str, method: str, path: str) -> bool:
    k = _guest_key_of(tok)
    return k is not None and _guest_allowed(k["scope"], method, path)


TOKEN_CHECKS.append(_guest_token_check)


def _guest_refusal(tok: str, method: str, path: str):
    """A live guest key on a route it may not use: 403 (the browser keeps its key), not 401."""
    k = _guest_key_of(tok)
    return "a guest may not do this" if k is not None and not _guest_allowed(k["scope"], method, path) else None


KEY_REFUSALS.append(_guest_refusal)


def _guest_ws_trim(websocket, payload):
    """The house token's screens hear of waiting browsers (_meta.guest); a guest key that ran out sees nothing."""
    tok = request_token(websocket.headers, websocket.query_params)
    if tok.startswith(GUEST_PREFIX):
        return payload if _guest_key_of(tok) else {}
    if not (DASHBOARD_TOKEN and hmac.compare_digest(tok.encode(), DASHBOARD_TOKEN.encode())):
        return payload
    with _guest_lock:
        now = time.time()
        waiting = [{"id": i, "code": r["code"], "ua": r["ua"]} for i, r in _guest_reqs.items()
                   if r["state"] == "pending" and now - r["created"] <= GUEST_PENDING_S]
    return {**payload, "_meta": {**(payload.get("_meta") or {}), "guest": waiting}} if waiting else payload


WS_FILTERS.append(_guest_ws_trim)


def _not_a_guest(request: Request):
    if request_token(request.headers).startswith(GUEST_PREFIX):
        raise HTTPException(403, "a guest may not do this")


@app.get("/guest")
def guest_page():
    return FileResponse(CHECKOUT / "web" / "guest.html", headers={"Cache-Control": "no-cache"})


@app.post("/api/guest/request")
def guest_request(request: Request):
    ip, now = _guest_ip(request), time.time()
    with _guest_lock:
        _guest_sweep(now)
        hits = _guest_hits.setdefault(ip, [])
        if len(hits) >= GUEST_RATE[0]:
            raise HTTPException(429, "too many requests from this address: try again in 10 minutes")
        if sum(r["state"] == "pending" for r in _guest_reqs.values()) >= GUEST_PENDING_MAX:
            raise HTTPException(429, "too many browsers are waiting: try again in a few minutes")
        hits.append(now)
        rid, secret = secrets.token_urlsafe(16), secrets.token_urlsafe(32)
        code = f"{secrets.randbelow(10 ** 6):06d}"
        ua = _guest_ua(request.headers.get("user-agent", ""))
        _guest_reqs[rid] = {"secret": secret, "code": code, "created": now, "ip": ip, "ua": ua,
                            "state": "pending", "token": "", "hours": 0}
    log.warning(f"[guest] a browser asks to come in: code {code}, {ua}, from {ip}")
    _emit({"kind": "guest_request", "device": "", "id": rid, "code": code, "ua": ua, "source": "guest"})
    _bump()
    return {"id": rid, "secret": secret, "code": code}


open_route(guest_request)


@app.get("/api/guest/request/{rid}")
def guest_request_state(rid: str, secret: str = ""):
    with _guest_lock:
        _guest_sweep(time.time())
        r = _guest_reqs.get(rid)
        if r is None or not hmac.compare_digest(secret.encode(), r["secret"].encode()):
            raise HTTPException(404, "no such request")
        if r["state"] == "allowed" and r["token"]:
            tok, r["token"] = r["token"], ""          # handed out once, never again
            return {"state": "allowed", "token": tok, "hours": r["hours"]}
        return {"state": r["state"]}


open_route(guest_request_state)


@app.get("/api/guest")
def guest_list(request: Request):
    _not_a_guest(request)
    now = time.time()
    with _guest_lock:
        _guest_sweep(now)
        pending = [{"id": i, "code": r["code"], "ip": r["ip"], "ua": r["ua"], "age_s": int(now - r["created"])}
                   for i, r in _guest_reqs.items() if r["state"] == "pending"]
        guests = [{"gid": g, "label": k.get("label", ""), "scope": k["scope"], "expires": k["expires"],
                   "ip": k.get("ip", ""), "ua": k.get("ua", "")} for g, k in _guest_keys.items()]
    return {"pending": pending, "guests": sorted(guests, key=lambda x: x["expires"])}


class GuestAllowBody(Body):
    hours: int = 2
    scope: str = "no_locks"


def _guest_pending(rid: str) -> dict:
    _guest_sweep(time.time())
    r = _guest_reqs.get(rid)
    if r is None:
        raise HTTPException(404, "no such request")
    if r["state"] != "pending":
        raise HTTPException(409, "this request is no longer waiting")
    return r


@use_route
@app.post("/api/guest/{rid}/allow")
def guest_allow(rid: str, body: GuestAllowBody, request: Request):
    _not_a_guest(request)
    if body.hours not in GUEST_HOURS:
        raise HTTPException(400, "hours: 1, 2, 8 or 24")
    if body.scope not in GUEST_SCOPES:
        raise HTTPException(400, "rights: no_locks or view")
    with _guest_lock:
        r = _guest_pending(rid)
        if len(_guest_keys) >= GUEST_KEYS_MAX:
            raise HTTPException(429, "too many guest keys: revoke some first")
        tok, gid, now = GUEST_PREFIX + secrets.token_urlsafe(32), secrets.token_urlsafe(8), time.time()
        _guest_keys[gid] = {"sha256": hashlib.sha256(tok.encode()).hexdigest(), "scope": body.scope,
                            "expires": now + body.hours * 3600, "ip": r["ip"], "ua": r["ua"], "created": now, "label": r["ua"]}
        _guest_save()
        r.update(state="allowed", token=tok, hours=body.hours)
    log.warning(f"[guest] code {r['code']} let in for {body.hours} h ({body.scope}): {r['ua']} from {r['ip']}")
    _bump()
    return {"ok": True, "gid": gid}


@use_route
@app.post("/api/guest/{rid}/deny")
def guest_deny(rid: str, request: Request):
    _not_a_guest(request)
    with _guest_lock:
        r = _guest_pending(rid)
        r["state"] = "denied"
    log.warning(f"[guest] code {r['code']} refused: {r['ua']} from {r['ip']}")
    _bump()
    return {"ok": True}


@use_route
@app.delete("/api/guest/keys/{gid}")
def guest_revoke(gid: str, request: Request):
    _not_a_guest(request)
    with _guest_lock:
        k = _guest_keys.pop(gid, None)
        if k is None:
            raise HTTPException(404, "no such guest")
        _guest_save()
    log.warning(f"[guest] key revoked: {k.get('ua')} from {k.get('ip')}")
    return {"ok": True}
