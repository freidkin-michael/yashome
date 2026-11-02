"""Demo mode in the core: only routes RESTRICTED_ALLOW names run, "control" ones only for the guest in control,
a session counts as the house token, the house is English only. The demo plug-in's own sweep is in
tests/plugins/test_demo.py."""
import atexit
import os
import pathlib
import shutil
import sys
import tempfile
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
from starlette.websockets import WebSocketDisconnect  # noqa: E402

HOUSE = "demo-test-house-token-0123456789"
ROLES = {"viewer-token-0123456789": "view", "control-token-0123456789": "control"}


def bearer(t):
    return {"authorization": "Bearer " + t}


class DemoMode(unittest.TestCase):
    def setUp(self):
        self.saved = (app.DASHBOARD_TOKEN, app.RESTRICTED, dict(app.RESTRICTED_ALLOW), list(app.RESTRICTED_ROLE), list(app.TOKEN_CHECKS),
                      list(app.RESTRICTED_CHECKS))
        app.DASHBOARD_TOKEN, app.RESTRICTED = HOUSE, True
        app.RESTRICTED_ALLOW.clear()
        app.RESTRICTED_ALLOW.update({("GET", "/api/devices"): "view", ("GET", "/api/health"): "view",
                               ("POST", "/api/resync"): "control", ("WS", "/ws"): "view", ("GET", "/i18n.js"): "view",
                               ("POST", "/api/rules/run"): "control", ("GET", "/api/plugins"): "view"})
        app.RESTRICTED_ROLE[:] = [lambda tok, m, p: ROLES.get(tok)]
        app.TOKEN_CHECKS[:] = [lambda tok, m, p: tok in ROLES]
        self.c = TestClient(app.app)

    def tearDown(self):
        app.DASHBOARD_TOKEN, app.RESTRICTED = self.saved[0], self.saved[1]
        app.RESTRICTED_ALLOW.clear()
        app.RESTRICTED_ALLOW.update(self.saved[2])
        app.RESTRICTED_ROLE[:], app.TOKEN_CHECKS[:], app.RESTRICTED_CHECKS[:] = self.saved[3], self.saved[4], self.saved[5]

    def test_a_route_the_list_does_not_name_is_refused_even_with_the_house_token(self):
        for method, path in (("GET", "/api/backups"), ("POST", "/api/signin/start"), ("POST", "/api/esphome/add"),
                             ("GET", "/api/helper/state"), ("GET", "/no-such-page"), ("OPTIONS", "/api/devices")):
            r = self.c.request(method, path, headers=bearer(HOUSE))
            self.assertEqual((r.status_code, r.json()["detail"]), (403, app.RESTRICTED_REFUSED), (method, path))

    def test_a_viewer_reads_but_does_not_control(self):
        self.assertEqual(self.c.get("/api/devices", headers=bearer("viewer-token-0123456789")).status_code, 200)
        r = self.c.post("/api/resync", headers=bearer("viewer-token-0123456789"))
        self.assertEqual(r.status_code, 403)
        self.assertIn("wait for your turn", r.json()["detail"])

    def test_the_guest_in_control_passes_the_gate(self):
        r = self.c.post("/api/resync", headers=bearer("control-token-0123456789"))
        self.assertNotEqual(r.status_code, 403)

    def test_no_session_is_401_on_a_listed_route_and_403_on_a_control_one(self):
        self.assertEqual(self.c.get("/api/devices").status_code, 401)
        self.assertEqual(self.c.get("/api/devices", headers=bearer("nobody-0123456789")).status_code, 401)
        self.assertEqual(self.c.post("/api/resync", headers=bearer(HOUSE)).status_code, 403)     # not even the house token
        self.assertEqual(self.c.post("/api/resync").status_code, 403)

    def test_a_big_body_is_refused_before_it_is_read(self):
        h = {**bearer("control-token-0123456789"), "content-type": "application/json"}
        r = self.c.post("/api/rules/run", headers=h, content=b'{"id": "' + b"x" * app.RESTRICTED_BODY_MAX + b'"}')
        self.assertEqual(r.status_code, 413)

    def test_demo_checks_judge_the_body_and_the_route_still_reads_it(self):
        seen = []
        app.RESTRICTED_CHECKS.append(lambda m, p, params, body: seen.append((m, p, body)) or ("no" if b"bad" in body else None))
        h = bearer("control-token-0123456789")
        r = self.c.post("/api/rules/run", headers=h, json={"id": "bad"})
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "no"))
        r = self.c.post("/api/rules/run", headers=h, json={"id": "b|nope|x|y"})
        self.assertEqual(r.status_code, 404)                        # the route parsed the body the check read
        self.assertEqual(seen[-1][:2], ("POST", "/api/rules/run"))
        app.RESTRICTED_CHECKS[:] = [lambda m, p, params, body: 1 / 0]
        self.assertEqual(self.c.post("/api/rules/run", headers=h, json={"id": "b|nope|x|y"}).status_code, 400)

    def test_the_plugin_list_names_no_paths_or_errors(self):
        d = self.c.get("/api/plugins", headers=bearer("viewer-token-0123456789")).json()
        self.assertEqual((d["dirs"], d["off"]), ([], []))
        self.assertTrue(all(set(f) == {"id"} for f in d["failed"]))

    def test_the_guest_in_control_is_the_house_token_a_watcher_only_on_a_view_route(self):
        self.assertTrue(app.is_house_token(bearer("control-token-0123456789")))
        self.assertFalse(app.is_house_token(bearer("viewer-token-0123456789")))
        tok = app._RESTRICTED_NEED.set("view")
        try:
            self.assertTrue(app.is_house_token(bearer("viewer-token-0123456789")))
        finally:
            app._RESTRICTED_NEED.reset(tok)
        tok = app._RESTRICTED_NEED.set("control")
        try:
            self.assertFalse(app.is_house_token(bearer("viewer-token-0123456789")))
        finally:
            app._RESTRICTED_NEED.reset(tok)
        self.assertFalse(app.is_house_token(bearer("nobody-0123456789")))
        app.RESTRICTED = False
        self.assertFalse(app.is_house_token(bearer("viewer-token-0123456789")))
        self.assertIsNone(app.restricted_role(bearer("control-token-0123456789")))

    def test_a_broken_role_check_gives_no_role(self):
        app.RESTRICTED_ROLE[:] = [lambda tok, m, p: 1 / 0]
        self.assertIsNone(app.restricted_role(bearer("control-token-0123456789")))

    def test_websockets_go_through_the_gate(self):
        with self.c.websocket_connect("/ws?token=viewer-token-0123456789") as ws:
            self.assertIsInstance(ws.receive_text(), str)
        del app.RESTRICTED_ALLOW[("WS", "/ws")]
        with self.assertRaises(WebSocketDisconnect) as e:
            with self.c.websocket_connect("/ws?token=control-token-0123456789") as ws:
                ws.receive_text()
        self.assertEqual(e.exception.code, 4403)

    def test_a_session_holds_at_most_three_websockets(self):
        url = "/ws?token=viewer-token-0123456789"
        with self.c.websocket_connect(url) as a, self.c.websocket_connect(url) as b, self.c.websocket_connect(url) as c:
            for w in (a, b, c):
                w.receive_text()
            with self.assertRaises(WebSocketDisconnect) as e:
                with self.c.websocket_connect(url) as d:
                    d.receive_text()
            self.assertEqual(e.exception.code, 4429)
        with self.c.websocket_connect(url) as again:                 # closed ones are counted out
            self.assertIsInstance(again.receive_text(), str)

    def test_a_random_query_token_beside_the_real_bearer_counts_as_the_same_session(self):
        h, socks = bearer("viewer-token-0123456789"), []
        try:
            for i in range(3):
                socks.append(self.c.websocket_connect(f"/ws?token=junk{i}", headers=h).__enter__())
                socks[-1].receive_text()
            with self.assertRaises(WebSocketDisconnect) as e:
                with self.c.websocket_connect("/ws?token=junk9", headers=h) as d:
                    d.receive_text()
            self.assertEqual(e.exception.code, 4429)
        finally:
            for w in socks:
                w.__exit__(None, None, None)

    def test_a_chunked_body_with_no_length_is_cut_at_the_limit(self):
        h = {**bearer("control-token-0123456789"), "content-type": "application/json"}
        big = (b"x" * 4096 for _ in range(app.RESTRICTED_BODY_MAX // 4096 + 2))
        self.assertEqual(self.c.post("/api/rules/run", headers=h, content=big).status_code, 413)
        small = iter([b'{"id": ', b'"b|nope|x|y"}'])                # chunked, small: the route still reads it
        self.assertEqual(self.c.post("/api/rules/run", headers=h, content=small).status_code, 404)

    def test_english_only(self):
        self.assertEqual(app.house_langs(), ["en"])
        root, app.ROOT = app.ROOT, pathlib.Path(app.__file__).parent      # the fixtures hold no i18n.js
        try:
            self.assertIn("window.RESTRICTED = true;", self.c.get("/i18n.js").text)
            app.RESTRICTED = False
            self.assertIn("window.RESTRICTED = false;", self.c.get("/i18n.js").text)
        finally:
            app.ROOT = root

    def test_a_plugin_entry_must_be_method_path_to_a_role(self):
        snap = app._hooks_snapshot()
        app.RESTRICTED_ALLOW[("GET", "/api/x")] = "admin"
        with self.assertRaises(ValueError):
            app._hooks_check("t", snap)


if __name__ == "__main__":
    unittest.main()
