"""IP cameras, proxied so the dashboard stays same-origin: a snapshot, and an MJPEG stream (the
camera's own or stitched from snapshots). Cameras are the settings section "cameras"
([{id, name, snapshot?, stream?, user?, pass?}]); "${VAR}" in a value is filled from the
environment, so credentials stay in .env and the stored section keeps the placeholders.
Other plug-ins (panels) add cameras with register_source(fn) -> {id: camera}."""
import base64
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

import app as core

PLUGIN = {"name": "Cameras", "version": "1.0.0"}

SECTION = "cameras"
_RAW = core._cfg_section(SECTION, [])
SOURCES: list = []


def register_source(fn):
    """fn() -> {id: {name, stream?, snapshot?, ...}}: cameras another plug-in owns (panels)."""
    SOURCES.append(fn)
    return fn


def _section():
    return _RAW                                       # the raw entries: placeholders, never the secrets


def _expand(c: dict) -> dict:
    return {k: os.path.expandvars(v) if isinstance(v, str) else v for k, v in c.items()}


CAMERAS = {c["id"]: _expand(c) for c in _RAW if isinstance(c, dict) and isinstance(c.get("id"), str)}


def _all() -> dict:
    out = dict(CAMERAS)
    for fn in SOURCES:
        try:
            for cid, cam in (fn() or {}).items():
                out.setdefault(cid, {"id": cid, **cam})
        except Exception as e:                        # noqa: BLE001
            core.log.warning(f"[cameras] a camera source failed: {e}")
    return out


def _camera(cid: str) -> dict:
    cam = _all().get(cid)
    if not cam:
        raise HTTPException(404, "unknown camera")
    return cam


def _open(cam: dict, url: str, timeout: float, data: bytes = None, headers: dict = None):
    if urllib.parse.urlsplit(url or "").scheme not in ("http", "https"):
        raise HTTPException(502, "camera url is not http(s)")
    h = dict(headers or {})
    if cam.get("user"):
        h["Authorization"] = "Basic " + base64.b64encode(f"{cam['user']}:{cam.get('pass', '')}".encode()).decode()
    try:
        return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=timeout)
    except urllib.error.HTTPError as e:
        return e
    except Exception as e:                            # noqa: BLE001  the reason only: a url may carry secrets
        raise HTTPException(502, f"camera unreachable: {type(e).__name__}")


@core.app.get("/api/cameras")
def list_cameras():
    return [{"id": c["id"], "name": c.get("name", c["id"])} for c in _all().values()]


SNAP_MAX = 4 << 20                                # bytes: a frame is far smaller, an endless body stops here
SNAP_S = 8                                        # the whole read, not only each socket read


def _read_capped(r, deadline: float) -> bytes:
    buf = b""
    while chunk := r.read1(65536):
        buf += chunk
        if len(buf) > SNAP_MAX or time.monotonic() > deadline:
            raise HTTPException(502, "camera answer too large or too slow")
    return buf


def _first_frame(r, deadline: float) -> bytes:
    """The first JPEG of a multipart stream (a stream-only camera), never the endless body:
    the part's Content-Length when it has one, else up to the JPEG end marker."""
    buf = b""
    while len(buf) <= SNAP_MAX and time.monotonic() <= deadline and (chunk := r.read1(65536)):
        buf += chunk
        s = buf.find(b"\xff\xd8")
        if s < 0:
            continue
        m = re.search(rb"content-length:\s*(\d+)", buf[:s], re.I)
        if m:
            if len(buf) >= s + int(m.group(1)):
                return buf[s:s + int(m.group(1))]
        elif (e := buf.find(b"\xff\xd9", s + 2)) >= 0:
            return buf[s:e + 2]
    raise HTTPException(502, "no frame from the camera stream")


@core.app.get("/api/camera/{cid}/snapshot")
def camera_snapshot(cid: str):
    cam = _camera(cid)
    deadline = time.monotonic() + SNAP_S
    with _open(cam, cam.get("snapshot") or cam.get("stream"), SNAP_S) as r:
        if getattr(r, "status", 200) >= 400:
            raise HTTPException(502, f"camera answered {r.status}")
        ctype = r.headers.get("Content-Type", "image/jpeg")
        if "multipart" in ctype:
            body, ctype = _first_frame(r, deadline), "image/jpeg"
        else:
            body = _read_capped(r, deadline)
        return Response(body, media_type=ctype, headers={"Cache-Control": "no-store"})


MAX_STREAMS = 6                                   # each open stream holds a worker thread of the shared pool
_streams = threading.BoundedSemaphore(MAX_STREAMS)


class _Slot:
    """One stream slot, given back exactly once: when the body ends, or by the response's
    background task if the client left before the body started."""

    def __init__(self):
        self._lock, self._held = threading.Lock(), True

    def release(self):
        with self._lock:
            if self._held:
                self._held = False
                _streams.release()


def _held(gen, slot):
    try:
        yield from gen
    finally:
        slot.release()


@core.app.get("/api/camera/{cid}/stream")
def camera_stream(cid: str, fps: float = 3.0):
    """MJPEG: the camera's own multipart stream relayed, else stitched from snapshots at fps."""
    cam = _camera(cid)
    if not _streams.acquire(blocking=False):
        raise HTTPException(503, "too many open camera streams")
    slot = _Slot()
    try:
        return _stream(cam, fps, slot)
    except BaseException:
        slot.release()
        raise


def _stream(cam: dict, fps: float, slot: _Slot):
    if cam.get("stream"):
        r = _open(cam, cam["stream"], 30)
        ctype = r.headers.get("Content-Type", "")
        if getattr(r, "status", 200) < 400 and "multipart" in ctype:
            def relay():
                try:
                    while chunk := r.read(16384):
                        yield chunk
                finally:
                    r.close()
            return StreamingResponse(_held(relay(), slot), media_type=ctype, headers={"Cache-Control": "no-cache"},
                                     background=BackgroundTask(slot.release))
        r.close()
    if not cam.get("snapshot"):
        raise HTTPException(502, "camera has neither stream nor snapshot")
    period = 1.0 / max(0.2, min(10.0, fps))

    def stitched():
        while True:
            t0 = time.time()
            try:
                with _open(cam, cam["snapshot"], SNAP_S) as rr:
                    ok = getattr(rr, "status", 200) < 400
                    body = _read_capped(rr, time.monotonic() + SNAP_S) if ok else b""
                if body:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(body)).encode()
                           + b"\r\n\r\n" + body + b"\r\n")
            except HTTPException:
                pass
            time.sleep(max(0.05, period - (time.time() - t0)))
    return StreamingResponse(_held(stitched(), slot), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-cache"}, background=BackgroundTask(slot.release))



core.SETTINGS_SECTIONS[SECTION] = _section
