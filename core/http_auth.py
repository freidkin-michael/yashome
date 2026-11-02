# The core: the FastAPI app, security headers, tokens, routes open or marked for use.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
app = FastAPI(title="Yashome")


@app.middleware("http")
async def _security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    # the page has inline script, so CSP cannot stop inline injection; it stops what an
    # injected script would do next: load code or send the token to another host
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    return resp


_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'self'")


# One shared bearer token gates everything that carries state (/api, /ws). Only
# the PWA shell stays open so the token prompt can load. Empty token = FAIL
# CLOSED: a lost .env must never silently reopen the house.
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
_OPEN_PATHS = {"/", "/app.js", "/app.css", "/manifest.json", "/sw.js", "/i18n.js", "/display.js", "/favicon.ico"}
_OPEN_PREFIXES = ("/icon-",)
# a plug-in's public assets: GET /plugins/<name>/ui.js | i18n.json | static/<file>; routes a plug-in
# adds under /plugins/ (any other shape or method) need the token like everything else
_OPEN_ASSET = re.compile(r"^/plugins/[a-z][a-z0-9_]{0,31}/(ui\.js|i18n\.json|static/[A-Za-z0-9_][A-Za-z0-9_.-]{0,63})$")
if not DASHBOARD_TOKEN:
    log.error("DASHBOARD_TOKEN is not set: every /api call will be refused (401)")


def request_token(headers, query=None) -> str:
    """The key a request carries: Authorization: Bearer, else X-Token, else ?token= when `query` is given (a
    WebSocket, where no header can be set; a URL ends up in access logs and browser history)."""
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-token") or ((query or {}).get("token", "") if query is not None else "")


def is_house_token(headers) -> bool:
    """The request carries the house token itself (Bearer or X-Token), not one a plug-in issued."""
    tok = request_token(headers)
    if RESTRICTED:                                    # the guest in control; a watcher only on a route listed for watchers
        role = restricted_role(headers)
        return role == "control" or (role == "view" and _RESTRICTED_NEED.get() == "view")
    return bool(DASHBOARD_TOKEN) and hmac.compare_digest(tok.encode(), DASHBOARD_TOKEN.encode())


def restricted_role(headers, query=None, method: str = "", path: str = "") -> "str | None":
    """Demo mode: "view" or "control" for the demo session whose token this request carries, else None."""
    tok = request_token(headers, query or {})
    if not RESTRICTED or not tok or len(tok) > 256:
        return None
    for fn in list(RESTRICTED_ROLE):
        try:
            role = fn(tok, method, path)
        except Exception as e:                    # noqa: BLE001  a broken check gives no role
            log.error(f"[demo] role check {getattr(fn, '__name__', fn)}: {e}")
            continue
        if role in ("view", "control"):
            return role
    return None


def _restricted_refusal(method: str, scope, headers, query=None):
    """Demo mode: a 403 for a route RESTRICTED_ALLOW does not name, or that needs control a watcher lacks; None = go on."""
    r = _matched_route(scope)
    need = RESTRICTED_ALLOW.get(("GET" if method == "HEAD" else method, getattr(r, "path", None)))
    if need not in ("view", "control"):
        return _refuse(403, RESTRICTED_REFUSED)
    _RESTRICTED_NEED.set(need)
    if need == "control" and restricted_role(headers, query, method, scope.get("path", "")) != "control":
        return _refuse(403, "Not yet: wait for your turn")
    return None


async def _restricted_body_refusal(request):
    """Demo mode: a body over RESTRICTED_BODY_MAX is refused before it is parsed; RESTRICTED_CHECKS judge the rest."""
    if request.method in ("GET", "HEAD"):
        return None
    try:
        size = int(request.headers.get("content-length") or 0)
    except ValueError:
        return _refuse(400, "bad Content-Length")
    if size > RESTRICTED_BODY_MAX:
        return _refuse(413, "request too large")
    chunks, n = [], 0                             # read in pieces: a chunked body has no Content-Length to trust
    async for c in request.stream():
        n += len(c)
        if n > RESTRICTED_BODY_MAX:
            return _refuse(413, "request too large")
        chunks.append(c)
    body = request._body = b"".join(chunks)       # the route reads this copy (starlette replays _body)
    r = _matched_route(request.scope)
    params = r.matches(request.scope)[1].get("path_params", {}) if r is not None else {}
    for fn in list(RESTRICTED_CHECKS):
        try:
            why = fn(request.method, getattr(r, "path", ""), params, body)
        except Exception as e:                    # noqa: BLE001  a broken check refuses, never admits
            log.error(f"[demo] check {getattr(fn, '__name__', fn)}: {e}")
            why = "refused"
        if why:
            return _refuse(400, str(why))
    return None


def _token_ok(headers, query=None, method: str = "", path: str = "") -> bool:
    """Bearer or X-Token header; ?token= only where a header is impossible (the
    WebSocket), since a URL ends up in access logs and browser history."""
    tok = request_token(headers, query or {})
    # bytes: compare_digest raises on non-ASCII str, which would be a 500 instead of a 401
    if bool(DASHBOARD_TOKEN) and hmac.compare_digest(tok.encode(), DASHBOARD_TOKEN.encode()):
        return True
    if not tok or len(tok) > 256:
        return False
    for fn in list(TOKEN_CHECKS):
        try:
            if fn(tok, method, path) is True:
                return True
        except Exception as e:                    # noqa: BLE001  a broken check refuses, never admits
            log.error(f"[plugins] token check {getattr(fn, '__name__', fn)}: {e}")
    return False


def _same_origin(headers) -> bool:
    """A browser sends Origin on cross-site POSTs and WebSocket handshakes. When a
    proxy injects the token, that header is the only thing telling a page on
    another site apart from the dashboard itself. No Origin = not a browser."""
    origin = headers.get("origin")
    if not origin:
        return True
    o = origin.split("://", 1)[-1].rstrip("/").lower()
    hosts = {(headers.get(h) or "").lower() for h in ("host", "x-forwarded-host")}
    return o in hosts or o in _EXTRA_ORIGINS


# A proxy that rewrites Host and sends no X-Forwarded-Host (plain nginx proxy_pass) would
# make the dashboard's own requests look cross-site: list its public name(s) here.
_EXTRA_ORIGINS = {x.strip().split("://", 1)[-1].rstrip("/").lower()
                  for x in os.environ.get("DASHBOARD_ORIGINS", "").split(",") if x.strip()}


def open_route(fn):
    """Decorator for a plug-in route that a device calls before it has the token (pairing):
    put it above @core.app.<method>(...). Opens that one route (the last one added for fn),
    not other paths of the same function; only a route the same plug-in adds can be opened."""
    r = next((r for r in reversed(app.router.routes) if getattr(r, "endpoint", None) is fn), None)
    if r is None:
        raise ValueError("open_route: put it above @core.app.<method>(...), the route must exist first")
    OPEN_ROUTES[id(r)] = r
    return fn


def use_route(fn):
    """Decorator: this route is using the house (a device, a rule, the player, a voice question), not setting it
    up. A key that is not the house token (a full-access phone) may change only such routes; it reads every GET.
    Put it above @core.app.<method>(...); it marks that one route (the last one added for fn)."""
    r = next((r for r in reversed(app.router.routes) if getattr(r, "endpoint", None) is fn), None)
    if r is None:
        raise ValueError("use_route: put it above @core.app.<method>(...), the route must exist first")
    USE_ROUTES[id(r)] = r
    return fn


def is_use_route(method: str, path: str) -> bool:
    """Reading (GET/HEAD) or a route marked use_route: what a full-access key that is not the house token may call.
    method WS = a WebSocket. The route is the one routing will really hand the request to."""
    if method in ("GET", "HEAD"):
        return True
    scope = {"type": "websocket" if method == "WS" else "http", "path": path, "root_path": "",
             "method": "GET" if method == "WS" else method, "headers": [], "query_string": b""}
    r = _matched_route(scope)
    return r is not None and id(r) in USE_ROUTES


def _matched_route(scope):
    """The route routing will really hand this request to (first full match), or None."""
    for r in app.router.routes:
        if r.matches(scope)[0] == Match.FULL:
            return r
    return None


def _matched_endpoint(scope):
    return getattr(_matched_route(scope), "endpoint", None)


def _routes_to_asset(scope) -> bool:
    """Open without the token only what routing really hands to plugin_asset: a plug-in route whose
    pattern also covers a public-looking path ({f}, {p:path}) matches first and stays closed."""
    return _matched_endpoint(scope) is plugin_asset


@app.middleware("http")
async def _require_token(request, call_next):
    p = request.url.path
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request.headers):
        return _refuse(403, "cross-origin request refused")
    if RESTRICTED and (refused := _restricted_refusal(request.method, request.scope, request.headers)
                      or await _restricted_body_refusal(request)):
        return refused
    # open pages are read with GET/HEAD only; a plug-in page (added at any time) is never the API
    # nor under /plugins/, as _hooks_check says at load time
    page = request.method in ("GET", "HEAD")
    plugin_open = page and p in OPEN_PATHS and not p.startswith(("/api", "/ws", "/plugins/"))
    asset = page and _OPEN_ASSET.match(p) and _routes_to_asset(request.scope)
    shell = page and (p in _OPEN_PATHS or p.startswith(_OPEN_PREFIXES)) or (request.method == "POST" and p == "/api/signin")
    if shell or plugin_open or asset or _token_ok(request.headers, None, request.method, p) \
            or (OPEN_ROUTES and id(_matched_route(request.scope)) in OPEN_ROUTES):
        if "settings" in _load_failed and request.method != "GET" and _writes_settings(p):
            return _refuse(503, f"{_settings_bad()}: fix or remove it and restart; "
                                "nothing that is kept in it can change until then")
        return await call_next(request)
    return _refuse(401, "token required")


class _WsTokenGuard:
    """The http middleware above never sees a WebSocket: this gives every WebSocket route (a
    plug-in's too, not only /ws) the same token and Origin check before its handler runs."""
    def __init__(self, asgi):
        self.asgi = asgi

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            ws = WebSocket(scope, receive, send)
            if RESTRICTED and _restricted_refusal("WS", scope, ws.headers, ws.query_params):
                await ws.close(code=4403)
                return
            if RESTRICTED:                            # a demo session holds at most RESTRICTED_WS_MAX WebSockets
                key = hashlib.sha256(request_token(ws.headers, ws.query_params).encode()).hexdigest()   # the one auth reads
                if _restricted_ws.get(key, 0) >= RESTRICTED_WS_MAX:
                    await ws.close(code=4429)
                    return
                _restricted_ws[key] = _restricted_ws.get(key, 0) + 1
                try:
                    return await self._guarded(ws, scope, receive, send)
                finally:
                    _restricted_ws[key] -= 1
                    if _restricted_ws[key] <= 0:
                        _restricted_ws.pop(key, None)
            return await self._guarded(ws, scope, receive, send)
        await self.asgi(scope, receive, send)

    async def _guarded(self, ws, scope, receive, send):
        if not _same_origin(ws.headers) or not (
                _token_ok(ws.headers, ws.query_params, "WS", ws.url.path)
                or (OPEN_ROUTES and id(_matched_route(scope)) in OPEN_ROUTES)):
            await ws.close(code=4401)
            return
        await self.asgi(scope, receive, send)


_restricted_ws: dict = {}          # a demo session's token hash -> its open WebSockets


app.add_middleware(_WsTokenGuard)


# endpoints whose change lives in settings.json: refused up front while it is unreadable, so the
# running state never drifts from what a restart would load
_SETTINGS_WRITERS = ("/api/favorites", "/api/rooms", "/api/tab_order", "/api/tile_sizes", "/api/bindings",
                     "/api/automation", "/api/rules/enable", "/api/rules/delete",
                     "/api/devices/relink", "/api/devices/merge")   # /api/rules/run writes nothing


def _writes_settings(path: str) -> bool:
    if path.startswith(_SETTINGS_WRITERS):
        return True
    # /api/devices/{id}/rename, .../channel/{code}/rename, .../dimrange, .../leftovers
    return path.startswith("/api/devices/") and path.endswith(("/rename", "/dimrange", "/leftovers"))


def _refuse(code: int, why: str):
    # this middleware answers before _security_headers runs, so it adds the same headers itself
    r = JSONResponse({"detail": why}, status_code=code)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Content-Security-Policy"] = _CSP
    return r


def _need_broker():
    """Everything that talks to devices goes through the broker: without it, say so (503)."""
    if _mqtt_client is None or not _mqtt_client.is_connected():
        raise HTTPException(503, "broker not reachable")


_ID_LIKE = re.compile(r"[A-Za-z0-9_-]+")


def check_name(name, kind: str = "device", taken=(), most: int = 60, id_like: bool = False) -> str:
    """A name typed in the dashboard: trimmed, not empty, at most `most` characters, no control character, none of
    `taken` (in any case); an id-like one (it becomes an id or a topic) only latin letters, digits, - and _.
    -> the trimmed name; else 400/409 with an English text the dashboard translates (i18n.js)."""
    n = name.strip() if isinstance(name, str) else ""
    if not n:
        raise HTTPException(400, "name is empty")
    if len(n) > most:
        raise HTTPException(400, f"name is too long: at most {most} characters")
    if not n.isprintable():
        raise HTTPException(400, "name has a control character")
    if id_like and not _ID_LIKE.fullmatch(n):
        raise HTTPException(400, "name: only latin letters, digits, - and _")
    low = n.casefold()
    if any(isinstance(t, str) and t.strip().casefold() == low for t in taken):
        raise HTTPException(409, f"'{n}' is already taken by another {kind}")
    return n


def _check_topic_name(name: str, what: str = "name"):
    """Names that end up inside MQTT topics: a wildcard or / in there is a
    protocol error that makes the broker drop our session; | is the rule-id
    separator. NUL and other control characters are not allowed in a topic either."""
    if (not isinstance(name, str) or not name or len(name) > 64 or not name.isprintable()
            or any(ch in name for ch in "/+#|")):
        raise HTTPException(400, f"{what} must be 1-64 printable chars without / + # |")
