"""tools/yashctl against a fake dashboard: what it sends, what it prints, where the token comes from."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CORE = pathlib.Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("yashctl", str(CORE / "tools" / "yashctl"))
_spec = importlib.util.spec_from_loader("yashctl", _loader)
yashctl = importlib.util.module_from_spec(_spec)
_loader.exec_module(yashctl)

DEVICES = [{"id": "lamp_1", "name": "Lamp 1", "category": "switch", "transport": "mqtt",
            "controls": [{"code": "power", "kind": "switch"}, {"code": "brightness", "kind": "bright"}]},
           {"id": "sensor_1", "name": "Sensor 1", "category": "sensor", "transport": "mqtt",
            "controls": [{"code": "temperature", "kind": "sensor"}]},
           {"id": "box_1", "name": "Twin", "category": "media", "transport": "mqtt",
            "controls": [{"code": "input", "kind": "setting_enum"}, {"code": "power", "kind": "switch"}]},
           {"id": "box_2", "name": "Twin", "category": "media", "transport": "mqtt", "controls": []},
           {"id": "zb_1", "name": "ZB 1", "category": "switch", "transport": "z2m",
            "controls": [{"code": "state", "kind": "switch"}]},
           {"id": "odd_1", "name": "Odd", "category": "switch", "transport": "mqtt",
            "controls": [{"code": "power", "kind": "switch"}]}]
STATUS = {"lamp_1": {"online": True, "values": {"power": True, "brightness": 128}},
          "box_1": {"online": False, "values": {}},
          "odd_1": {"online": True, "values": {"power": "ON"}},
          "sensor_1": {"online": True, "values": {"temperature": 21.5}}}


class Fake(BaseHTTPRequestHandler):
    seen: list = []
    health: dict = {"ok": True, "mqtt": True}
    failed: list = []

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Fake.seen.append(("GET", self.path, self.headers.get("Authorization"), None))
        if self.headers.get("Authorization") != "Bearer tok":
            return self.reply(401, {"detail": "token"})
        p = self.path
        if p == "/api/devices":
            return self.reply(200, DEVICES)
        if p.startswith("/api/devices/") and p.endswith("/status"):
            return self.reply(200, STATUS.get(p.split("/")[3], {"online": False, "values": {}}))
        if p == "/api/rooms":
            return self.reply(200, [{"id": "r1", "name": "Room 1", "devices": ["sensor_1"]}])
        if p == "/api/rules":
            return self.reply(200, [{"id": "a|lamp_1/power|inch", "kind": "inching", "enabled": True, "title": "t"}])
        if p == "/api/devices/zb_1/rules":
            return self.reply(404, {"detail": "unknown route"})
        if p == "/api/html":
            self.send_response(200)
            self.end_headers()
            return self.wfile.write(b"<html>")
        if p.endswith("/events"):
            return self.reply(200, {"items": [{"code": "action", "value": "single"}]})
        if p == "/api/favorites":
            return self.reply(200, [{"device": "lamp_1", "code": "power"}])
        if p == "/api/health":
            return self.reply(200, Fake.health)
        if p == "/api/plugins":
            return self.reply(200, {"loaded": [{"id": "tv", "group": "devices", "version": "1",
                                                "required_by": "ir"}], "failed": Fake.failed, "off": ["weather"]})
        return self.reply(404, {"detail": "unknown"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
        Fake.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
        return self.reply(200, {"ok": True})


class Yashctl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def run_cli(self, *argv, token="tok"):
        Fake.seen.clear()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = yashctl.main(["--url", self.url, "--token", token, *argv])
        return rc, out.getvalue()

    def test_set_parses_the_value_and_uses_the_one_control_path(self):
        self.run_cli("set", "lamp_1", "brightness", "200")
        self.assertIn(("POST", "/api/devices/lamp_1/control", "Bearer tok", {"code": "brightness", "value": 200}),
                      Fake.seen)
        self.run_cli("off", "Lamp 1")
        self.assertEqual(Fake.seen[-1][3], {"code": "power", "value": False})

    def test_toggle_reads_the_state_first(self):
        self.run_cli("toggle", "lamp_1")
        self.assertEqual(Fake.seen[-1][3], {"code": "power", "value": False})
        STATUS["lamp_1"]["values"]["power"] = False
        try:
            self.run_cli("toggle", "lamp_1")
            self.assertEqual(Fake.seen[-1][3], {"code": "power", "value": True})
        finally:
            STATUS["lamp_1"]["values"]["power"] = True

    def test_toggle_refuses_an_unknown_state(self):
        with self.assertRaises(yashctl.Fail) as e:
            self.run_cli("toggle", "box_1")
        self.assertEqual(e.exception.exit_code, 2)
        self.assertFalse([s for s in Fake.seen if s[0] == "POST"])

    def test_watch_once_and_unhealthy_exit_codes(self):
        rc, out = self.run_cli("watch", "lamp_1", "--once")
        self.assertIn("brightness=128", out)
        rc, out = self.run_cli("watch", "lamp_1", "--events", "--once")
        self.assertIn('"single"', out)
        Fake.health, Fake.failed = {"ok": True, "mqtt": False}, [{"id": "x", "error": "boom"}]
        try:
            self.assertEqual(self.run_cli("status")[0], 1)
            self.assertEqual(self.run_cli("plugins")[0], 1)
        finally:
            Fake.health, Fake.failed = {"ok": True, "mqtt": True}, []

    def test_on_off_take_switches_only_and_find_the_one_switch(self):
        with self.assertRaises(yashctl.Fail) as e:
            self.run_cli("on", "lamp_1", "brightness")
        self.assertEqual(e.exception.exit_code, 2)
        self.run_cli("on", "box_1")                       # its only switch is "power" here
        self.assertEqual(Fake.seen[-1][3], {"code": "power", "value": True})

    def test_more_exit_codes_and_lookups(self):
        with self.assertRaises(yashctl.Fail) as e:
            self.run_cli("rules", "--device", "zb_1")
        self.assertEqual(e.exception.exit_code, 3)
        with self.assertRaises(yashctl.Fail) as e:
            yashctl.Api(self.url, "tok", 5).get("/api/html")
        self.assertEqual(e.exception.exit_code, 5)
        with self.assertRaises(yashctl.Fail) as e:
            self.run_cli("devices", "--room", "nothing")
        self.assertEqual(e.exception.exit_code, 3)
        self.run_cli("on", "zb 1")
        self.assertEqual(Fake.seen[-1][3], {"code": "state", "value": True})
        with self.assertRaises(yashctl.Fail) as e:
            self.run_cli("toggle", "odd_1")
        self.assertEqual(e.exception.exit_code, 2)

    def test_values_follow_the_channel_kind(self):
        self.run_cli("set", "box_1", "input", "on")
        self.assertEqual(Fake.seen[-1][3], {"code": "input", "value": "on"})       # a choice keeps its text
        with self.assertRaises(yashctl.Fail):
            self.run_cli("set", "lamp_1", "brightness", "bright")

    def test_events_favorites_resync(self):
        rc, out = self.run_cli("events", "lamp_1")
        self.assertIn('"single"', out)
        rc, out = self.run_cli("favorites")
        self.assertIn("lamp_1", out)
        self.run_cli("resync")
        self.assertEqual(Fake.seen[-1][:2], ("POST", "/api/resync"))

    def test_irreversible_actions_need_yes(self):
        for argv in (("rule", "delete", "a|x|inch"), ("relink", "old_1", "lamp_1")):
            with self.assertRaises(yashctl.Fail) as e:
                self.run_cli(*argv)
            self.assertEqual(e.exception.exit_code, 2)
            self.assertFalse([s for s in Fake.seen if s[0] == "POST"])
        self.run_cli("rule", "delete", "--yes", "a|x|inch")
        self.assertEqual(Fake.seen[-1][1:], ("/api/rules/delete", "Bearer tok", {"id": "a|x|inch"}))

    def test_exit_codes(self):
        for argv, code in ((("show", "nothing"), 3), (("show", "Twin"), 3)):
            with self.assertRaises(yashctl.Fail) as e:
                self.run_cli(*argv)
            self.assertEqual(e.exception.exit_code, code, argv)
        self.assertIn("box_1, box_2", str(e.exception))
        with self.assertRaises(yashctl.Fail) as e:
            yashctl.main(["--url", "http://127.0.0.1:9", "--token", "t", "status"])
        self.assertEqual(e.exception.exit_code, 4)

    def test_an_unknown_channel_is_refused_before_anything_is_sent(self):
        with self.assertRaises(SystemExit) as e:
            self.run_cli("on", "sensor_1")
        self.assertIn("no channel 'power'", str(e.exception))
        self.assertFalse([s for s in Fake.seen if s[0] == "POST"])

    def test_rules_enable_and_disable(self):
        self.run_cli("rule", "disable", "a|lamp_1/power|inch")
        self.assertEqual(Fake.seen[-1][1:], ("/api/rules/enable", "Bearer tok", {"id": "a|lamp_1/power|inch",
                                                                                 "enabled": False}))
        self.run_cli("rule", "run", "a|lamp_1/power|inch")
        self.assertEqual(Fake.seen[-1][1], "/api/rules/run")

    def test_tables_and_json(self):
        rc, out = self.run_cli("devices", "--room", "Room 1", "--state")
        self.assertEqual(rc, 0)
        self.assertIn("sensor_1", out)
        self.assertNotIn("lamp_1", out)
        rc, out = self.run_cli("--json", "show", "lamp_1")
        self.assertEqual(json.loads(out)["status"]["values"]["brightness"], 128)
        rc, out = self.run_cli("plugins")
        self.assertIn("required by ir", out)
        self.assertIn("off", out)

    def test_a_wrong_token_is_reported(self):
        with self.assertRaises(yashctl.Fail) as e:
            self.run_cli("status", token="bad")
        self.assertIn("HTTP 401", str(e.exception))
        self.assertEqual(e.exception.exit_code, 5)

    def test_the_token_and_port_come_from_the_checkouts_env(self):
        with tempfile.TemporaryDirectory() as d:
            port = self.srv.server_address[1]
            pathlib.Path(d, ".env").write_text(f"DASHBOARD_TOKEN='tok'  # comment\nDASHBOARD_PORT={port}\nX='a #b'\n")
            self.assertEqual(yashctl.read_env(pathlib.Path(d, ".env"))["X"], "a #b")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(yashctl.main(["--dir", d, "status"]), 0)
            self.assertIn("backend  ok", out.getvalue())

    def test_the_token_from_the_environment(self):
        import os
        os.environ["YASHOME_TOKEN"] = "tok"
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(yashctl.main(["--url", self.url, "status"]), 0)
        finally:
            os.environ.pop("YASHOME_TOKEN")

    def test_values(self):
        self.assertEqual([yashctl.value(v) for v in ("on", "OFF", "12", "1.5", "hdmi_1")], [True, False, 12, 1.5, "hdmi_1"])
        self.assertEqual([yashctl.value("1", "switch"), yashctl.value("on", "setting_enum"), yashctl.value("7", "bright")],
                         [True, "on", 7])
        self.assertEqual(yashctl.value("1"), 1)
        with self.assertRaises(yashctl.Fail):
            yashctl.value("maybe", "switch")
        self.assertEqual(yashctl.pct({"min": 0, "max": 255}, 300), 100)

    def test_sources_of_token_and_url(self):
        import os
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, ".env").write_text("DASHBOARD_TOKEN=from-env-file\nDASHBOARD_PORT=123 # note\n")
            self.assertEqual(yashctl.read_env(pathlib.Path(d, ".env"))["DASHBOARD_PORT"], "123")
            os.environ["YASHOME_TOKEN"] = "tok"
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(yashctl.main(["--url", self.url, "--dir", d, "status"]), 0)
            finally:
                os.environ.pop("YASHOME_TOKEN")
            env = {k: v for k, v in os.environ.items() if not k.startswith("YASHOME_")}
            r = subprocess.run([sys.executable, str(CORE / "tools" / "yashctl"), "--dir", d + "/none", "status"],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)


class AgainstTheRealApp(unittest.TestCase):
    """The same commands against the backend itself (uvicorn on a free port, the fixture house)."""

    @classmethod
    def setUpClass(cls):
        import socket
        import sys
        import time
        sys.path.insert(0, str(CORE / "tests"))
        import test_pure  # noqa: F401
        import app
        import uvicorn
        cls.app = app
        app.DASHBOARD_TOKEN = "real-tok"
        app.DEVICES["yc_lamp"] = {"id": "yc_lamp", "name": "YC lamp", "transport": "mqtt", "category": "switch",
                                  "controls": [{"kind": "switch", "code": "power"}]}
        app.DEVICES["yc_lamp"]["_ctl"] = {"power": app.DEVICES["yc_lamp"]["controls"][0]}
        app._status_cache["yc_lamp"] = {"online": True, "values": {"power": True}, "raw": {}}
        with socket.socket() as so:
            so.bind(("127.0.0.1", 0))
            port = so.getsockname()[1]
        cls.srv = uvicorn.Server(uvicorn.Config(app.app, host="127.0.0.1", port=port, lifespan="off", log_level="error"))
        threading.Thread(target=cls.srv.run, daemon=True).start()
        for _ in range(100):
            if cls.srv.started:
                break
            time.sleep(0.05)
        cls.url = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.should_exit = True
        cls.app.DEVICES.pop("yc_lamp", None)

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = yashctl.main(["--url", self.url, "--token", "real-tok", *argv])
        return rc, out.getvalue()

    def test_reads(self):
        self.assertIn("yc_lamp", self.cli("devices", "--state")[1])
        self.assertIn("on/off", self.cli("show", "YC lamp")[1])
        self.assertIn("(no events)", self.cli("events", "yc_lamp")[1])
        self.assertEqual(self.cli("--json", "rules")[0], 0)
        self.assertEqual(self.cli("--json", "rooms")[0], 0)
        rc, out = self.cli("--json", "plugins")             # other tests leave broken plug-ins: rc may be 1
        self.assertEqual(sorted(json.loads(out)), ["dirs", "failed", "loaded", "off"])

    def test_unknown_device_and_channel(self):
        with self.assertRaises(yashctl.Fail) as e:
            self.cli("on", "yc_lamp", "nope")
        self.assertEqual(e.exception.exit_code, 3)


if __name__ == "__main__":
    unittest.main()
