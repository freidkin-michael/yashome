# The core: the WebSocket and the static shell.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# WebSocket -- push the shared device-state cache
# ---------------------------------------------------------------------------
def _ws_payload() -> dict:
    """One WebSocket frame: every device's status as a screen gets it (cmd_wait included)."""
    return {did: _status_out(did) if did in _status_cache else get_status(did) for did in list(DEVICES)}


WS_MIN_GAP_S = 0.25     # at most four frames a second to a screen: a chatty house sends its latest state
WS_IDLE_POLL_S = 0.1    # how often an idle socket looks for a change (each look costs every socket)
_ws_built: dict = {}    # (version, who) -> (built at, frame text): screens of one caller share a build


_WS_NONCE = {"sec-websocket-key"}   # differs on every connection and carries no credential


def _ws_who(websocket) -> str:
    """Who asks, as the trims could see it: every header but the handshake nonce, and the whole query.
    Two screens share a frame only when all of it is equal (a token in any carrier splits them)."""
    hs = sorted((k.lower(), v) for k, v in websocket.headers.items() if k.lower() not in _WS_NONCE)
    raw = json.dumps([hs, str(websocket.url.query)])
    return hashlib.sha256(raw.encode()).hexdigest()


def _ws_frame(websocket) -> str:
    """The frame for this caller (the WS_FILTERS trims applied), built at most once per WS_MIN_GAP_S
    for all screens with the same request (_ws_who): the full device list is not rebuilt N times."""
    key, now = (_ws_version, _ws_who(websocket)), time.monotonic()
    hit = _ws_built.get(key)
    if hit and now - hit[0] < WS_MIN_GAP_S:
        return hit[1]
    payload = _ws_payload()
    for trim in list(WS_FILTERS):
        payload = trim(websocket, payload)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(_ws_built) > 256:
        _ws_built.clear()
    _ws_built[key] = (now, text)
    return text


@use_route
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    # browsers cannot set headers on a WebSocket, so the token rides in ?token=
    if not _same_origin(websocket.headers) or not _token_ok(websocket.headers, websocket.query_params, "WS", websocket.url.path):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    last, sent_at = None, 0.0
    try:
        while True:
            seen = _ws_version
            text = _ws_frame(websocket)
            if text != last or time.monotonic() - sent_at >= WS_TICK:   # a change, or the tick's heartbeat
                await websocket.send_text(text)
                last, sent_at = text, time.monotonic()
            await asyncio.sleep(WS_MIN_GAP_S)
            while _ws_version == seen and time.monotonic() - sent_at < WS_TICK:
                await asyncio.sleep(WS_IDLE_POLL_S)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"[ws] closed: {e!r}")


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-cache"})


def _load_webpage():
    """tools/webpage.py: the dashboard's script and style from web/ (its parts joined in one scope)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("yashome_webpage", CHECKOUT / "tools" / "webpage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


webpage = _load_webpage()


@app.get("/app.js")
def dashboard_js():
    return Response(webpage.app_js(CHECKOUT), media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/app.css")
def dashboard_css():
    return Response(webpage.app_css(CHECKOUT), media_type="text/css", headers={"Cache-Control": "no-cache"})


# -- PWA: installable web app (manifest + service worker + icons) --------------
@app.get("/manifest.json")
def pwa_manifest():
    return FileResponse(ROOT / "manifest.json", media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def pwa_sw():
    # root scope + no-cache so SW updates apply on reload
    return FileResponse(ROOT / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/icon-192.png")
def pwa_icon192():
    return FileResponse(ROOT / "icon-192.png", media_type="image/png")


@app.get("/favicon.ico")
def favicon():
    # browsers that ignore <link rel="icon"> probe this path directly
    return FileResponse(ROOT / ("favicon-32.png" if (ROOT / "favicon-32.png").exists() else "icon-192.png"), media_type="image/png")


@app.get("/icon-maskable-512.png")
def pwa_icon_maskable():
    return FileResponse(ROOT / "icon-maskable-512.png", media_type="image/png")


@app.get("/icon-512.png")
def pwa_icon512():
    return FileResponse(ROOT / "icon-512.png", media_type="image/png")
