"""Guest access (core/guest.py): a browser asks, a signed-in screen allows it, the browser gets a short key
that never reaches a lock and never sets the house up."""
import atexit
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

if "app" not in sys.modules:                 # alone: the core on a private copy of the fixtures, as test_pure does
    _FIX = tempfile.mkdtemp(prefix="yashome-test-")
    atexit.register(shutil.rmtree, _FIX, ignore_errors=True)
    shutil.copytree(os.path.join(os.path.dirname(__file__), "fixtures"), _FIX, dirs_exist_ok=True)
    os.environ["HOME_STACK_ROOT"] = _FIX
    os.environ.pop("HOUSE_ROOT", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

HOUSE = "guest-test-house-token-0123456789"


def bearer(t):
    return {"authorization": "Bearer " + t}


def device(did, controls, **kw):
    d = {"id": did, "name": did, "transport": "mqtt", "controls": controls, **kw}
    d["_ctl"] = {c["code"]: c for c in controls}
    return d


class Guest(unittest.TestCase):
    def setUp(self):
        self.saved = (app.DASHBOARD_TOKEN, app.RESTRICTED, dict(app.DEVICES))
        app.DASHBOARD_TOKEN, app.RESTRICTED = HOUSE, False
        app._guest_reqs.clear(); app._guest_hits.clear(); app._guest_keys.clear()
        app.DEVICES["lamp"] = device("lamp", [{"kind": "switch", "code": "state"}], category="switch")
        app.DEVICES["door"] = device("door", [{"kind": "switch", "code": "locked"}], category="switch")
        app.DEVICES["bolt"] = device("bolt", [{"kind": "switch", "code": "state"}], category="lock")
        self.c = TestClient(app.app)

    def tearDown(self):
        app.DASHBOARD_TOKEN, app.RESTRICTED = self.saved[0], self.saved[1]
        app.DEVICES.clear(); app.DEVICES.update(self.saved[2])
        app._guest_reqs.clear(); app._guest_hits.clear(); app._guest_keys.clear()

    def ask(self, ip="203.0.113.7"):
        r = self.c.post("/api/guest/request", headers={"x-real-ip": ip, "user-agent":
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def poll(self, req, secret=None):
        return self.c.get(f"/api/guest/request/{req['id']}", params={"secret": req["secret"] if secret is None else secret})

    def key(self, scope="no_locks", hours=2):
        req = self.ask()
        r = self.c.post(f"/api/guest/{req['id']}/allow", json={"hours": hours, "scope": scope}, headers=bearer(HOUSE))
        self.assertEqual(r.status_code, 200, r.text)
        return self.poll(req).json()["token"]

    def test_a_request_gets_a_code_and_waits(self):
        req = self.ask()
        self.assertRegex(req["code"], r"^\d{6}$")
        self.assertEqual(self.poll(req).json(), {"state": "pending"})
        lst = self.c.get("/api/guest", headers=bearer(HOUSE)).json()
        self.assertEqual(lst["pending"][0]["code"], req["code"])
        self.assertEqual(lst["pending"][0]["ua"], "Chrome on Windows")
        self.assertNotIn("secret", json.dumps(lst))
        self.assertEqual(self.c.get("/api/guest").status_code, 401)

    def test_the_key_is_handed_out_once(self):
        req = self.ask()
        self.c.post(f"/api/guest/{req['id']}/allow", json={"hours": 1, "scope": "view"}, headers=bearer(HOUSE))
        got = self.poll(req).json()
        self.assertEqual((got["state"], got["hours"]), ("allowed", 1))
        self.assertRegex(got["token"], r"^yg_[A-Za-z0-9_-]{43}$")
        self.assertEqual(self.poll(req).json(), {"state": "allowed"})

    def test_a_wrong_secret_or_id_is_404(self):
        req = self.ask()
        self.assertEqual(self.poll(req, "nope").status_code, 404)
        self.assertEqual(self.c.get("/api/guest/request/nope", params={"secret": req["secret"]}).status_code, 404)

    def test_deny_and_expiry(self):
        req = self.ask()
        self.assertEqual(self.c.post(f"/api/guest/{req['id']}/deny", headers=bearer(HOUSE)).status_code, 200)
        self.assertEqual(self.poll(req).json(), {"state": "denied"})
        req = self.ask()
        app._guest_reqs[req["id"]]["created"] -= app.GUEST_PENDING_S + 1
        self.assertEqual(self.poll(req).json(), {"state": "expired"})
        r = self.c.post(f"/api/guest/{req['id']}/allow", json={}, headers=bearer(HOUSE))
        self.assertEqual(r.status_code, 409)

    def test_view_reads_but_changes_nothing(self):
        tok = self.key("view")
        self.assertEqual(self.c.get("/api/devices", headers=bearer(tok)).status_code, 200)
        r = self.c.post("/api/devices/lamp/control", json={"code": "state", "value": True}, headers=bearer(tok))
        self.assertEqual(r.status_code, 403)

    def test_no_locks_uses_a_device_but_not_a_lock_nor_the_setup(self):
        tok = self.key()
        r = self.c.post("/api/devices/lamp/control", json={"code": "state", "value": True}, headers=bearer(tok))
        self.assertNotIn(r.status_code, (401, 403), r.text)
        for dev, code in (("door", "locked"), ("bolt", "state")):
            r = self.c.post(f"/api/devices/{dev}/control", json={"code": code, "value": False}, headers=bearer(tok))
            self.assertEqual(r.status_code, 403, dev)
        for path, body in (("/api/rooms", {"name": "x"}), ("/api/devices/lamp/rename", {"name": "x"}),
                           ("/api/rules/run", {"id": "x"})):
            self.assertEqual(self.c.post(path, json=body, headers=bearer(tok)).status_code, 403, path)

    def test_no_locks_refuses_what_reaches_a_lock_indirectly(self):
        app.DEVICES["wall"] = device("wall", [{"kind": "switch", "code": "state"}], category="switch")
        app.DEVICES["relay"] = device("relay", [{"kind": "switch", "code": "state"}], category="switch")
        app.DEVICES["remote"] = device("remote", [{"kind": "trigger", "code": "action"}, {"kind": "switch", "code": "led"}],
                                       category="button")
        keep_w, keep_b = list(app._wiring), {k: v for k, v in app._bindings.items()}
        app._wiring[:] = [{"id": "w1", "switch": {"dev": "wall", "code": "state"}, "target": {"dev": "relay"}, "note": ""},
                          {"id": "w2", "switch": {"dev": "relay", "code": "state"}, "target": {"dev": "bolt"}, "note": ""}]
        app._bindings["remote"] = {"action": {"single": {"target": "door", "code": "locked", "action": "toggle"}}}
        try:
            devs, rules = app.lock_reach()
            self.assertTrue({"bolt", "door", "relay", "wall", "remote"} <= devs)
            self.assertNotIn("lamp", devs)
            self.assertIn("b|remote|action|single", rules)
            tok = self.key()
            for dev, code in (("wall", "state"), ("relay", "state"), ("remote", "led")):
                r = self.c.post(f"/api/devices/{dev}/control", json={"code": code, "value": True}, headers=bearer(tok))
                self.assertEqual(r.status_code, 403, dev)
            r = self.c.post("/api/automation/wall/state", json={"inching_sec": 5}, headers=bearer(tok))
            self.assertEqual(r.status_code, 403)
            r = self.c.post("/api/rules/run", json={"id": "b|remote|action|single"}, headers=bearer(tok))
            self.assertEqual(r.status_code, 403)
            r = self.c.put("/api/bindings/remote/action/double", json={"target": "lamp", "code": "state", "action": "on"},
                           headers=bearer(tok))
            self.assertEqual(r.status_code, 403)
            r = self.c.post("/api/devices/lamp/control", json={"code": "state", "value": True}, headers=bearer(tok))
            self.assertNotIn(r.status_code, (401, 403), r.text)
        finally:
            app._wiring[:] = keep_w
            app._bindings.clear(); app._bindings.update(keep_b)
            for d in ("wall", "relay", "remote"):
                app.DEVICES.pop(d, None)

    def test_everything_scope_is_refused(self):
        req = self.ask()
        r = self.c.post(f"/api/guest/{req['id']}/allow", json={"hours": 2, "scope": "all"}, headers=bearer(HOUSE))
        self.assertEqual(r.status_code, 400)
        r = self.c.post(f"/api/guest/{req['id']}/allow", json={"hours": 3, "scope": "view"}, headers=bearer(HOUSE))
        self.assertEqual(r.status_code, 400)

    def test_an_expired_or_revoked_key_is_refused(self):
        tok = self.key()
        gid = next(iter(app._guest_keys))
        app._guest_keys[gid]["expires"] = time.time() - 1
        self.assertEqual(self.c.get("/api/devices", headers=bearer(tok)).status_code, 401)
        tok = self.key()
        gid = self.c.get("/api/guest", headers=bearer(HOUSE)).json()["guests"][0]["gid"]
        self.assertEqual(self.c.delete(f"/api/guest/keys/{gid}", headers=bearer(HOUSE)).status_code, 200)
        self.assertEqual(self.c.get("/api/devices", headers=bearer(tok)).status_code, 401)

    def test_a_guest_cannot_let_others_in(self):
        tok = self.key()
        req = self.ask()
        self.assertEqual(self.c.get("/api/guest", headers=bearer(tok)).status_code, 403)
        for path in (f"/api/guest/{req['id']}/allow", f"/api/guest/{req['id']}/deny"):
            self.assertEqual(self.c.post(path, json={}, headers=bearer(tok)).status_code, 403, path)
        gid = next(iter(app._guest_keys))
        self.assertEqual(self.c.delete(f"/api/guest/keys/{gid}", headers=bearer(tok)).status_code, 403)
        self.assertEqual(self.poll(req).json(), {"state": "pending"})

    def test_a_full_access_key_may_approve(self):
        keep = list(app.TOKEN_CHECKS)
        app.TOKEN_CHECKS.append(lambda tok, m, p: tok == "yfd_phone" and app.is_use_route(m, p))
        try:
            req = self.ask()
            r = self.c.post(f"/api/guest/{req['id']}/allow", json={}, headers=bearer("yfd_phone"))
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            app.TOKEN_CHECKS[:] = keep

    def test_rate_limits(self):
        for _ in range(5):
            self.ask("198.51.100.1")
        self.assertEqual(self.c.post("/api/guest/request", headers={"x-real-ip": "198.51.100.1"}).status_code, 429)
        for i in range(5):
            self.ask(f"198.51.100.{10 + i}")
        self.assertEqual(self.c.post("/api/guest/request", headers={"x-real-ip": "198.51.100.99"}).status_code, 429)

    def test_the_state_file_holds_no_key(self):
        tok = self.key()
        path = app._guest_file()
        text = path.read_text()
        self.assertNotIn(tok, text)
        self.assertNotIn(tok[3:], text)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(json.loads(text)[next(iter(app._guest_keys))]["scope"], "no_locks")

    def test_the_page_is_open(self):
        r = self.c.get("/guest")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/api/guest/request", r.text)

    def test_a_house_screen_hears_of_a_waiting_browser(self):
        req = self.ask()
        with self.c.websocket_connect("/ws?token=" + HOUSE) as ws:
            frame = json.loads(ws.receive_text())
        self.assertEqual(frame["_meta"]["guest"][0]["code"], req["code"])
        tok = self.key("view")
        with self.c.websocket_connect("/ws?token=" + tok) as ws:
            self.assertNotIn("_meta", json.loads(ws.receive_text()))


if __name__ == "__main__":
    unittest.main()


def test_a_guest_never_reads_cameras_history_or_the_network(monkeypatch):
    import app as core_app
    allowed = core_app._guest_allowed
    for path in ("/api/cameras", "/api/camera/x/snapshot", "/api/history", "/api/voice/satellites", "/api/zigbee/devices",
                 "/api/computers", "/api/backups", "/api/panels/apk"):
        assert not allowed("view", "GET", path) and not allowed("no_locks", "GET", path), path
    for path in ("/api/devices", "/api/rooms", "/api/map", "/api/weather"):
        assert allowed("view", "GET", path), path
