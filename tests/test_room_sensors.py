"""A room's thermometer and hygrometer: auto never takes a module's own board (an AC's built-in one is a board too) and never a
copy of a sensor the room has; a hand choice is the only source, and when it does not answer the room shows nothing and Health
says why; the choice follows a rename and goes with the room."""
import atexit
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

TOKEN = "room-sensors-test-token-0123"
T = lambda code="temperature", **k: {"kind": "sensor", "code": code, **k}     # noqa: E731
DEVS = {
    "radar": {"id": "radar", "name": "Radar", "category": "sensor", "controls": [T(advanced=True), T("humidity", advanced=True)]},
    "sht": {"id": "sht", "name": "SHT", "category": "sensor", "controls": [T(), T("humidity")]},
    "aqara": {"id": "aqara", "name": "Aqara", "category": "sensor", "controls": [T(preferred=True)]},
    "ac": {"id": "ac", "name": "AC", "category": "climate", "controls": [T("room_temp")]},
    "ac2": {"id": "ac2", "name": "AC 2", "category": "climate", "room_temp_from": "sht", "controls": [T("room_temp")]},
}


class RoomSensors(unittest.TestCase):
    def setUp(self):
        self.old, app.DASHBOARD_TOKEN = app.DASHBOARD_TOKEN, TOKEN
        self.c = TestClient(app.app)
        self.h = {"Authorization": "Bearer " + TOKEN}
        self.save, app._save_settings = app._save_settings, lambda: None
        self.rooms, self.picks = list(app._rooms), dict(app._room_sensors)
        app._rooms[:] = [{"id": "r_b", "name": "Bath", "devices": ["radar"]}, {"id": "r_l", "name": "Living", "devices": list(DEVS)}]
        app._room_sensors.clear()
        for d in DEVS.values():
            app.DEVICES[d["id"]] = d
        self.status("radar", temperature=38.0, humidity=60)
        self.status("sht", temperature=22.4, humidity=45)
        self.status("aqara", temperature=22.9)
        self.status("ac", room_temp=24.0)
        self.status("ac2", room_temp=22.4)

    def tearDown(self):
        app.DASHBOARD_TOKEN, app._save_settings = self.old, self.save
        app._rooms[:] = self.rooms
        app._room_sensors.clear()
        app._room_sensors.update(self.picks)
        for d in DEVS:
            app.DEVICES.pop(d, None)
            app._status_cache.pop(d, None)

    def test_a_picked_thermometer_that_left_the_room_is_not_read_the_picker_says_where_it_went(self):
        app._room_sensors["r_b"] = {"temperature": "radar/temperature"}
        self.assertEqual(app.room_reading("r_b", "temperature")["value"], 38.0)
        app._rooms[0]["devices"], app._rooms[1]["devices"] = [], app._rooms[1]["devices"] + ["radar"]      # its icon moved to Living
        got = app.room_sensor_order("r_b", "temperature")
        self.assertEqual((got["pick"], got["moved"]), ("auto", {"dev": "radar", "room": "Living"}))
        self.assertIsNone(app.room_reading("r_b", "temperature"))                                     # no stale 38 from the other room
        self.assertEqual(self.c.get("/api/rooms/r_b/sensors", headers=self.h).json()["temperature"]["moved"]["room"], "Living")
        app._rooms[0]["devices"], app._rooms[1]["devices"] = ["radar"], list(DEVS)                    # back: the pick holds again
        self.assertEqual(app.room_sensor_order("r_b", "temperature"), {"pick": "radar/temperature", "order": ["radar/temperature"]})

    def status(self, dev, online=True, stale=False, **values):
        app._status_cache[dev] = {"online": online, "values": values, "raw": {}, "last_seen": time.time() - 600, **({"stale": True} if stale else {})}

    def test_auto_skips_boards_and_copies_then_preferred_then_id(self):
        self.assertEqual(app.room_sensor_order("r_b", "temperature"), {"pick": "auto", "order": []})       # the radar's board: no reading
        self.assertIsNone(app.room_reading("r_b", "temperature"))
        self.assertEqual(app.room_sensor_order("r_l", "temperature")["order"], ["aqara/temperature", "sht/temperature"])   # no AC box, no copy
        self.assertEqual(app.room_reading("r_l", "temperature")["value"], 22.9)
        self.status("aqara", online=False, temperature=22.9)
        self.assertEqual(app.room_reading("r_l", "temperature")["dev"], "sht")                        # the next live one
        self.assertEqual(app.room_reading("r_l", "humidity")["dev"], "sht")

    def test_a_hand_choice_is_the_only_source_and_says_when_it_is_silent(self):
        put = lambda rid, kind, src: self.c.put(f"/api/rooms/{rid}/sensors", headers=self.h, json={"kind": kind, "source": src})   # noqa: E731
        self.assertEqual(put("r_b", "temperature", "sht/temperature").status_code, 400)               # not a sensor of this room
        self.assertEqual(put("r_b", "pressure", "auto").status_code, 400)
        self.assertEqual(put("r_b", "temperature", "radar/temperature").json()["order"], ["radar/temperature"])    # a board, by hand
        self.assertEqual(app.room_reading("r_b", "temperature")["value"], 38.0)
        self.status("radar", stale=True, temperature=38.0)
        self.assertIsNone(app.room_reading("r_b", "temperature"))                                  # never a silent other one
        got = self.c.get("/api/rooms/r_b/sensors", headers=self.h).json()["temperature"]
        self.assertEqual((got["silent"]["name"], got["reading"]), ("Radar", None))
        rows = [r for r in app._room_sensor_health() if r["id"] == "room-sensor-r_b-temperature"]
        self.assertEqual((rows[0]["state"], rows[0]["args"][:2]), ("warn", ["Bath", "Radar"]))
        self.assertEqual(put("r_b", "temperature", "auto").json()["pick"], "auto")
        self.assertEqual(app._room_sensors, {})
        rooms = self.c.get("/api/rooms", headers=self.h).json()
        self.assertEqual(next(r for r in rooms if r["id"] == "r_l")["sensors"]["temperature"]["order"][0], "aqara/temperature")

    def test_the_choice_follows_a_rename_and_goes_with_its_room(self):
        app._room_sensors["r_l"] = {"temperature": "sht/temperature", "humidity": "sht/humidity"}
        app._migrate_room_sensors(app._room_sensors, "sht", "sht2", False)
        self.assertEqual(app._room_sensors["r_l"]["temperature"], "sht2/temperature")
        app._migrate_room_sensors(app._room_sensors, "sht2", None, True)
        self.assertEqual(app._room_sensors["r_l"], {})
        app._room_sensors["r_l"] = {"temperature": "aqara/temperature"}
        app.rooms_edit(lambda rooms: rooms.pop(1))
        self.assertNotIn("r_l", app._room_sensors)

    def test_an_outdoor_station_is_never_auto_and_an_ac_with_nothing_behind_it_is_no_option(self):
        app.DEVICES["station"] = {"id": "station", "name": "Balcony", "category": "sensor", "controls": [T(), {"kind": "sensor", "code": "wind"}]}
        app.DEVICES["ir_ac"] = {"id": "ir_ac", "name": "IR AC", "category": "climate", "controls": [T("room_temp")]}
        try:
            app._rooms[0]["devices"] = ["radar", "station", "ir_ac"]
            self.status("station", temperature=9.0)
            self.status("ir_ac", power=True)                                       # an IR AC: no room reading at all
            opts = {(o["dev"], o["outdoor"]) for o in app.room_sensor_options("r_b", "temperature")}
            self.assertEqual(opts, {("radar", False), ("station", True)})         # the IR AC is not offered
            self.assertEqual(app.room_sensor_order("r_b", "temperature")["order"], [])      # neither a board nor the outdoors
            app._room_sensors["r_b"] = {"temperature": "station/temperature"}
            self.assertEqual(app.room_reading("r_b", "temperature")["value"], 9.0)          # by hand, the owner's word
        finally:
            for d in ("station", "ir_ac"):
                app.DEVICES.pop(d, None)
                app._status_cache.pop(d, None)


if __name__ == "__main__":
    unittest.main()
