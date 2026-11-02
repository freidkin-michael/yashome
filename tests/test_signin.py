"""Sign-in by a one-time code: only a signed-in screen makes one; it works once, for 5 minutes, 5 tries;
only POST /api/signin is open."""
import atexit
import os
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

TOKEN = "signin-test-token-0123456789"


class Signin(unittest.TestCase):
    def setUp(self):
        self.old, app.DASHBOARD_TOKEN = app.DASHBOARD_TOKEN, TOKEN
        self.c = TestClient(app.app)
        self.house = {"Authorization": "Bearer " + TOKEN}
        app._signin.update(code="", until=0.0, tries=0)

    def tearDown(self):
        app.DASHBOARD_TOKEN = self.old

    def start(self):
        r = self.c.post("/api/signin/start", headers=self.house)
        self.assertEqual(r.status_code, 200)
        return r.json()["code"]

    def test_only_a_signed_in_screen_makes_a_code(self):
        self.assertEqual(self.c.post("/api/signin/start").status_code, 401)
        self.assertEqual(self.c.post("/api/signin/start", headers={"Authorization": "Bearer nope"}).status_code, 401)
        self.assertRegex(self.start(), r"^\d{6}$")

    def test_a_code_gives_the_token_once(self):
        code = self.start()
        self.assertEqual(self.c.post("/api/signin", json={"code": code}).json(), {"token": TOKEN})
        self.assertEqual(self.c.post("/api/signin", json={"code": code}).status_code, 403)

    def test_five_wrong_tries_kill_the_code(self):
        code = self.start()
        for _ in range(5):
            self.assertEqual(self.c.post("/api/signin", json={"code": "000000" if code != "000000" else "111111"}).status_code, 403)
        self.assertEqual(self.c.post("/api/signin", json={"code": code}).status_code, 403)

    def test_an_old_code_is_dead(self):
        code = self.start()
        app._signin["until"] = 1.0
        self.assertEqual(self.c.post("/api/signin", json={"code": code}).status_code, 403)

    def test_a_plugin_token_never_makes_a_code(self):
        app.TOKEN_CHECKS.append(lambda tok, method, path: tok == "plug-token-0123456789")
        try:
            r = self.c.post("/api/signin/start", headers={"Authorization": "Bearer plug-token-0123456789"})
        finally:
            app.TOKEN_CHECKS.pop()
        self.assertEqual(r.status_code, 403)

    def test_the_fifth_wrong_try_is_the_last_and_a_new_code_starts_over(self):
        code = self.start()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            self.c.post("/api/signin", json={"code": wrong})
        self.assertEqual(self.c.post("/api/signin", json={"code": code}).status_code, 200)
        self.start()
        for _ in range(5):
            self.c.post("/api/signin", json={"code": wrong})
        code = self.start()
        self.assertEqual(self.c.post("/api/signin", json={"code": code}).status_code, 200)

    def test_five_minutes_and_no_empty_code(self):
        r = self.c.post("/api/signin/start", headers=self.house).json()
        self.assertEqual(r["valid_s"], 300)
        self.assertAlmostEqual(app._signin["until"] - __import__("time").time(), 300, delta=5)
        app._signin.update(code="", until=__import__("time").time() + 300, tries=0)
        self.assertEqual(self.c.post("/api/signin", json={"code": ""}).status_code, 403)

    def test_wrong_codes_cannot_flood_the_log(self):
        code = self.start()
        wrong = "000000" if code != "000000" else "111111"
        with self.assertLogs(app.log, level="WARNING") as cm:
            for _ in range(9):
                self.c.post("/api/signin", json={"code": wrong}, headers={"User-Agent": "evil[2Jua"})
        lines = [r for r in cm.output if "[signin] a wrong code" in r]
        self.assertEqual(len(lines), 5)
        self.assertNotIn("", "".join(cm.output))

    def test_only_the_post_is_open(self):
        self.assertEqual(self.c.get("/api/signin").status_code, 401)
        self.assertEqual(self.c.get("/api/backups").status_code, 401)


if __name__ == "__main__":
    unittest.main()
