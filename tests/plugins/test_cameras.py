"""cameras: snapshot and stream through a fake camera; secrets stay placeholders."""
import http.server
import sys
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.cameras"]
SEEN = []


class Cam(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        SEEN.append((self.path, self.headers.get("Authorization")))
        if self.path.startswith("/snap"):
            self._reply(200, b"\xff\xd8JPEG", "image/jpeg")
        elif self.path.startswith("/mjpeg"):             # endless, like a real camera stream
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
            self.end_headers()
            frame = b"\xff\xd8" + b"x" * 70000 + b"\xff\xd9"
            clen = b"Content-Length: %d\r\n" % len(frame) if "len" in self.path else b""
            try:
                while True:
                    self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\n" + clen + b"\r\n" + frame + b"\r\n")
            except OSError:
                pass
        else:
            self._reply(404, b"no", "text/plain")



@pytest.fixture(scope="module")
def cam(monkeypatch_module=None):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Cam)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    raw = {"id": "t_cam", "name": "Test", "snapshot": base + "/snap", "user": "admin", "pass": "${T_CAM_PASS}"}
    import os
    os.environ["T_CAM_PASS"] = "s3cret"
    me._RAW.append(raw)
    me.CAMERAS["t_cam"] = me._expand(raw)
    yield base
    me._RAW.remove(raw)
    me.CAMERAS.pop("t_cam", None)
    srv.shutdown()


AUTH = {"Authorization": f"Bearer {core.DASHBOARD_TOKEN}"}


def test_list_snapshot_with_basic_auth_and_no_token_no_image(cam):
    c = TestClient(core.app)
    assert c.get("/api/cameras").status_code == 401
    assert {"id": "t_cam", "name": "Test"} in c.get("/api/cameras", headers=AUTH).json()
    r = c.get("/api/camera/t_cam/snapshot", headers=AUTH)
    assert r.status_code == 200 and r.content.startswith(b"\xff\xd8")
    assert SEEN[-1][1].startswith("Basic ") and SEEN[-1][1] != "Basic "
    assert c.get("/api/camera/t_cam/snapshot").status_code == 401
    assert c.get("/api/camera/nope/snapshot", headers=AUTH).status_code == 404


def test_the_stored_section_keeps_the_placeholder(cam):
    assert any(x.get("pass") == "${T_CAM_PASS}" for x in me._section())
    assert "s3cret" not in str(me._section())
    assert me.CAMERAS["t_cam"]["pass"] == "s3cret"


def test_stitched_stream_from_snapshots(cam):
    me.CAMERAS["t_cam"].pop("stream", None)
    frames = me.camera_stream("t_cam", fps=10).body_iterator
    import asyncio

    async def first():
        async for chunk in frames:
            return chunk
    chunk = asyncio.run(first())
    assert chunk.startswith(b"--frame\r\nContent-Type: image/jpeg") and b"\xff\xd8JPEG" in chunk


def test_unreachable_says_no_url():
    me.CAMERAS["t_dead"] = {"id": "t_dead", "snapshot": "http://user:pw@127.0.0.1:9/x"}
    try:
        with pytest.raises(HTTPException) as e:
            me.camera_snapshot("t_dead")
        assert "pw" not in str(e.value.detail) and "127.0.0.1" not in str(e.value.detail)
    finally:
        me.CAMERAS.pop("t_dead")


def test_a_source_adds_cameras():
    me.SOURCES.append(lambda: {"t_panel-front": {"name": "Panel", "stream": "http://127.0.0.1:9/s"}})
    try:
        assert any(c["id"] == "t_panel-front" for c in me.list_cameras())
    finally:
        me.SOURCES.pop()


def test_open_streams_are_capped_and_given_back(cam):
    import asyncio
    held = []
    try:
        while True:
            held.append(me.camera_stream("t_cam", fps=1))
    except HTTPException as e:
        assert e.status_code == 503 and len(held) <= me.MAX_STREAMS
    for r in held:
        asyncio.run(r.background())               # the client left before the body started
    held[0].background.func()                     # a second release is a no-op, not an over-release
    assert me._streams._value == me.MAX_STREAMS


@pytest.mark.parametrize("path", ["/mjpeg", "/mjpeg?len"])
def test_snapshot_of_a_stream_only_camera_is_its_first_frame(cam, path):
    me.CAMERAS["t_stream"] = {"id": "t_stream", "stream": cam + path}
    try:
        r = TestClient(core.app).get("/api/camera/t_stream/snapshot", headers=AUTH)
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert r.content.startswith(b"\xff\xd8") and r.content.endswith(b"\xff\xd9") and len(r.content) == 70004
    finally:
        me.CAMERAS.pop("t_stream")


def test_an_endless_snapshot_body_is_cut(cam, monkeypatch):
    monkeypatch.setattr(me, "SNAP_MAX", 100000)
    with me._open({}, cam + "/mjpeg", 8) as r, pytest.raises(HTTPException) as e:
        me._read_capped(r, me.time.monotonic() + 8)
    assert e.value.status_code == 502
