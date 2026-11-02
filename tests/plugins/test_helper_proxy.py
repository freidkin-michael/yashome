"""The dashboard's side of the helper: only the house token reaches it, job kinds are checked, the
helper's refusals keep their meaning and a missing helper says what to do."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

import app as core

HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
SEEN = []


class Helper(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        SEEN.append(("GET", self.path, self.headers.get("X-Helper-Key")))
        self._send(200, {"plugins": [], "keys": {}, "git": {}, "previous": None, "job": None})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        SEEN.append(("POST", self.path, body))
        if self.path == "/v1/jobs" and body.get("kind") == "rollback":
            return self._send(409, {"error": "nothing to roll back to"})
        self._send(202 if self.path == "/v1/jobs" else 200, {"ok": True})


@pytest.fixture()
def helper(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Helper)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(core, "HELPER_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("HELPER_KEY", "k" * 48)
    SEEN.clear()
    yield
    srv.shutdown()


def test_the_dashboard_reaches_the_helper_with_its_key(helper):
    c = TestClient(core.app)
    assert c.get("/api/helper/state", headers=HOUSE).status_code == 200
    assert SEEN[-1] == ("GET", "/v1/state", "k" * 48)
    assert c.post("/api/helper/plugins", headers=HOUSE, json={"on": ["zigbee"]}).json() == {"ok": True}
    assert c.post("/api/helper/env", headers=HOUSE, json={"key": "A", "value": "b"}).status_code == 200
    assert c.post("/api/helper/jobs", headers=HOUSE, json={"kind": "apply"}).status_code == 200
    r = c.post("/api/helper/jobs", headers=HOUSE, json={"kind": "rollback"})
    assert r.status_code == 409 and "nothing to roll back" in r.json()["detail"]
    assert c.post("/api/helper/jobs", headers=HOUSE, json={"kind": "rm -rf"}).status_code == 400


def test_only_the_house_token(helper, monkeypatch):
    c = TestClient(core.app)
    assert c.get("/api/helper/state").status_code == 401
    monkeypatch.setattr(core, "TOKEN_CHECKS", core.TOKEN_CHECKS + [lambda tok, m, p: tok == "a-full-phone"])
    phone = {"Authorization": "Bearer a-full-phone"}
    for m, path, body in (("GET", "/api/helper/state", None), ("POST", "/api/helper/jobs", {"kind": "update"}),
                          ("POST", "/api/helper/env", {"key": "A", "value": "b"})):
        assert c.request(m, path, headers=phone, json=body).status_code == 403, path
    assert not SEEN


def test_no_helper_says_what_to_do(monkeypatch):
    monkeypatch.delenv("HELPER_KEY", raising=False)
    r = TestClient(core.app).get("/api/helper/state", headers=HOUSE)
    assert r.status_code == 503 and "make update" in r.json()["detail"]
    monkeypatch.setenv("HELPER_KEY", "k")
    monkeypatch.setattr(core, "HELPER_URL", "http://127.0.0.1:9")
    r = TestClient(core.app).get("/api/helper/state", headers=HOUSE)
    assert r.status_code == 503 and "does not answer" in r.json()["detail"]
