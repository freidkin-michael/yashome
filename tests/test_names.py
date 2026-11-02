"""A name typed in the dashboard (core.check_name): trimmed, not empty, not too long, no control character, unique in
its kind in any case; an id-like one only [A-Za-z0-9_-]. The device, channel and favourite renames go through it."""
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
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = "names-test-token-0123456789"


class CheckName(unittest.TestCase):
    def fails(self, code, text, *a, **k):
        with self.assertRaises(HTTPException) as e:
            app.check_name(*a, **k)
        self.assertEqual((e.exception.status_code, e.exception.detail), (code, text))

    def test_a_good_name_comes_back_trimmed(self):
        self.assertEqual(app.check_name("  Кладовка свет  "), "Кладовка свет")   # i18n

    def test_empty_long_and_control_characters_are_refused(self):
        self.fails(400, "name is empty", "   ")
        self.fails(400, "name is empty", None)
        self.fails(400, "name is too long: at most 5 characters", "abcdef", most=5)
        self.fails(400, "name has a control character", "a\tb")
        self.fails(400, "name has a control character", "a\x00b")

    def test_a_taken_name_is_refused_in_any_case(self):
        self.fails(409, "'кладовка' is already taken by another device", "кладовка", taken=["Кладовка", None])   # i18n
        self.fails(409, "'HALL' is already taken by another room", "HALL", "room", taken=[" hall "])
        self.assertEqual(app.check_name("Hall 2", taken=["Hall"]), "Hall 2")

    def test_an_id_like_name_is_latin_digits_dash_underscore(self):
        self.assertEqual(app.check_name("panel_7-a", id_like=True), "panel_7-a")
        for bad in ("panel 7", "панель", "a.b", "a/b"):   # i18n
            self.fails(400, "name: only latin letters, digits, - and _", bad, id_like=True)


class Renames(unittest.TestCase):
    def setUp(self):
        self.old, app.DASHBOARD_TOKEN = app.DASHBOARD_TOKEN, TOKEN
        self.c = TestClient(app.app)
        self.h = {"Authorization": "Bearer " + TOKEN}
        self.save, app._save_settings = app._save_settings, lambda: None
        ctl = [{"code": "l1", "kind": "switch", "label": "Left"}, {"code": "l2", "kind": "switch", "label": "Right"}]
        app.DEVICES["nm_a"] = {"id": "nm_a", "name": "Lamp A", "transport": "mqtt", "controls": ctl, "_ctl": {c["code"]: c for c in ctl}}
        app.DEVICES["nm_b"] = {"id": "nm_b", "name": "Lamp B", "transport": "mqtt", "controls": [], "_ctl": {}}

    def tearDown(self):
        app.DASHBOARD_TOKEN, app._save_settings = self.old, self.save
        for k in ("nm_a", "nm_b"):
            app.DEVICES.pop(k, None)
            app._name_overrides.pop(k, None)
            app._channel_names.pop(k, None)
        app._favorites[:] = [f for f in app._favorites if f.get("device") != "nm_a"]

    def test_a_device_cannot_take_another_ones_name_but_may_change_its_own_case(self):
        r = self.c.post("/api/devices/nm_a/rename", headers=self.h, json={"name": " lamp b "})
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "'lamp b' is already taken by another device"))
        self.assertEqual(self.c.post("/api/devices/nm_a/rename", headers=self.h, json={"name": "LAMP A"}).json()["name"], "LAMP A")
        self.assertEqual(self.c.post("/api/devices/nm_a/rename", headers=self.h, json={"name": "a" * 61}).status_code, 400)
        self.assertEqual(self.c.post("/api/devices/nm_a/rename", headers=self.h, json={"name": "  "}).json()["detail"], "name is empty")

    def test_no_request_body_takes_a_string_longer_than_its_cap(self):
        """core.Body: 4096 characters at most in any string of a body, before a route runs (a 3 MB name once reached settings.json)."""
        big = "a" * 5000
        self.assertEqual(self.c.post("/api/devices/nm_a/rename", headers=self.h, json={"name": big}).status_code, 422)
        self.assertEqual(self.c.post("/api/rules/enable", headers=self.h, json={"id": big, "enabled": True}).status_code, 422)
        bodies = [m for m in vars(app).values() if isinstance(m, type) and issubclass(m, app.BaseModel) and m.__module__ == "app"]
        self.assertGreater(len(bodies), 15)
        self.assertEqual([m.__name__ for m in bodies if not issubclass(m, app.Body)], [])

    def test_a_channel_label_is_unique_on_its_device_and_empty_brings_its_own_back(self):
        r = self.c.post("/api/devices/nm_a/channel/l1/rename", headers=self.h, json={"name": "right"})
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "'right' is already taken by another channel"))
        self.assertEqual(self.c.post("/api/devices/nm_a/channel/l1/rename", headers=self.h, json={"name": "Top"}).json()["label"], "Top")
        self.assertEqual(self.c.post("/api/devices/nm_a/channel/l1/rename", headers=self.h, json={"name": "x" * 41}).status_code, 400)

    def test_a_favourite_alias_is_checked_and_empty_is_the_channel(self):
        self.assertEqual(self.c.put("/api/favorites/nm_a/l1", headers=self.h, json={"alias": "a\x07"}).status_code, 400)
        self.assertEqual(self.c.put("/api/favorites/nm_a/l1", headers=self.h, json={"alias": " "}).json()["favorite"]["alias"], "l1")


class Rooms(unittest.TestCase):
    """The core's own rooms (no map plug-in in these tests): checked like any name; a PUT with no name only moves devices."""
    def setUp(self):
        self.old, app.DASHBOARD_TOKEN = app.DASHBOARD_TOKEN, TOKEN
        self.c = TestClient(app.app)
        self.h = {"Authorization": "Bearer " + TOKEN}
        self.save, app._save_settings = app._save_settings, lambda: None
        self.rooms = list(app._rooms)
        app._rooms[:] = [{"id": "r_k", "name": "Kitchen", "devices": []}, {"id": "r_b", "name": "Bed", "devices": []}]

    def tearDown(self):
        app.DASHBOARD_TOKEN, app._save_settings = self.old, self.save
        app._rooms[:] = self.rooms

    def test_a_room_name_is_checked_when_made_and_renamed(self):
        p = lambda body: self.c.post("/api/rooms", headers=self.h, json=body)     # noqa: E731
        self.assertEqual(p({"name": " "}).json()["detail"], "name is empty")
        self.assertEqual(p({"name": "KITCHEN"}).status_code, 409)
        self.assertEqual(p({"name": "x" * 41}).status_code, 400)
        u = lambda body: self.c.put("/api/rooms/r_b", headers=self.h, json=body)  # noqa: E731
        self.assertEqual(u({"name": ""}).status_code, 400)                      # a blank name is refused, not ignored
        self.assertEqual(u({"name": "kitchen"}).status_code, 409)
        self.assertEqual(u({"name": "BED"}).json()["room"]["name"], "BED")     # its own name in another case
        self.assertEqual(u({"devices": []}).status_code, 200)                  # no name: the devices only
        self.assertEqual(app._rooms[1]["name"], "BED")


if __name__ == "__main__":
    unittest.main()
