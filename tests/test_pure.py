"""Unit tests for the pure logic in app.py. Run via `make test` (inside the home
container, HOME_STACK_ROOT pointed at tests/fixtures so import touches no live
file). No broker, no network. Dates: 2026-09-07 is a Monday."""
import datetime as dt
import os
import sys
import unittest

# a private copy of the fixtures: code under test saves settings, and must not rewrite the repo
import shutil  # noqa: E402
import tempfile  # noqa: E402
import atexit  # noqa: E402
_FIX = tempfile.mkdtemp(prefix="yashome-test-")
atexit.register(shutil.rmtree, _FIX, ignore_errors=True)
shutil.copytree(os.path.join(os.path.dirname(__file__), "fixtures"), _FIX, dirs_exist_ok=True)
os.environ["HOME_STACK_ROOT"] = _FIX
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402


class FixedNow:
    """Stand-in for app.datetime so _in_time_window sees a chosen wall clock."""
    def __init__(self, when):
        self.when = when

    def now(self, tz=None):
        return self.when


class TimeWindow(unittest.TestCase):
    def at(self, y, m, d, hh, mm):
        app.datetime = FixedNow(dt.datetime(y, m, d, hh, mm))

    def tearDown(self):
        app.datetime = dt.datetime

    def test_same_day_inside_and_edges(self):
        tw = {"from": "08:00", "to": "20:00"}
        self.at(2026, 9, 7, 12, 0)
        self.assertTrue(app._in_time_window(tw))
        self.at(2026, 9, 7, 8, 0)
        self.assertTrue(app._in_time_window(tw), "from is inclusive")
        self.at(2026, 9, 7, 20, 0)
        self.assertFalse(app._in_time_window(tw), "to is exclusive")

    def test_overnight_morning_belongs_to_previous_day(self):
        tw = {"from": "22:00", "to": "07:00", "days": [7]}
        self.at(2026, 9, 7, 0, 30)
        self.assertTrue(app._in_time_window(tw), "Monday 00:30 is still Sunday's window")
        self.at(2026, 9, 7, 23, 0)
        self.assertFalse(app._in_time_window(tw), "Monday evening is not Sunday's window")

    def test_malformed_fails_open(self):
        self.at(2026, 9, 7, 3, 0)
        self.assertTrue(app._in_time_window({"from": "25:99", "to": "07:00"}))
        self.assertTrue(app._in_time_window(None))


class Brightness(unittest.TestCase):
    def test_roundtrip_254_and_1000(self):
        for hi in (254, 1000):
            ctl = {"min": 0, "max": hi}
            for pct in range(0, 101):
                back = app._raw_to_pct(ctl, app._pct_to_raw(ctl, pct))
                self.assertLessEqual(abs(back - pct), 1, f"max={hi} pct={pct} -> {back}")

    def test_non_number_and_clamp(self):
        ctl = {"min": 0, "max": 254}
        self.assertIsNone(app._raw_to_pct(ctl, None))
        self.assertEqual(app._pct_to_raw(ctl, 250), 254)
        self.assertEqual(app._pct_to_raw(ctl, -5), 0)


class Z2mExposes(unittest.TestCase):
    def test_two_gang_dimmer_and_meta(self):
        exposes = [
            {"type": "light", "features": [
                {"type": "binary", "property": "state_l1", "access": 7},
                {"type": "numeric", "property": "brightness_l1", "value_max": 1000, "access": 7},
                {"type": "binary", "property": "state_l2", "access": 7},
                {"type": "numeric", "property": "brightness_l2", "value_max": 1000, "access": 7},
                {"type": "numeric", "property": "min_brightness_l1", "access": 7},
            ]},
            {"type": "numeric", "property": "linkquality", "access": 1},
            {"type": "numeric", "property": "battery", "access": 1},
            {"type": "enum", "property": "action", "values": ["single", "double"]},
        ]
        ctls = app._z2m_controls_from_exposes(exposes)
        kinds = [(c["kind"], c["code"]) for c in ctls]
        self.assertIn(("switch", "state_l1"), kinds)
        self.assertIn(("bright", "brightness_l2"), kinds)
        self.assertNotIn(("bright", "min_brightness_l1"), kinds)
        self.assertFalse(any(c["code"] == "linkquality" for c in ctls))
        self.assertEqual(next(c for c in ctls if c["code"] == "brightness_l1")["max"], 1000)
        self.assertEqual(next(c for c in ctls if c["code"] == "battery")["kind"], "sensor")
        self.assertEqual(next(c for c in ctls if c["code"] == "action")["kind"], "trigger_text")


class Conditions(unittest.TestCase):
    def setUp(self):
        app._status_cache.clear()
        app._status_cache["lamp"] = {"online": True, "values": {"state": True, "brightness": 120, "mode": "eco"}}

    def test_ops(self):
        ok = app._conditions_pass
        self.assertTrue(ok([{"device": "lamp", "code": "state", "op": "on"}]))
        self.assertFalse(ok([{"device": "lamp", "code": "state", "op": "off"}]))
        self.assertTrue(ok([{"device": "lamp", "code": "brightness", "op": ">", "value": "100"}]))
        self.assertFalse(ok([{"device": "lamp", "code": "brightness", "op": "<", "value": 100}]))
        self.assertFalse(ok([{"device": "lamp", "code": "mode", "op": "<", "value": 5}]), "incomparable -> False, no raise")
        self.assertFalse(ok([{"device": "ghost", "code": "x", "op": "==", "value": 1}]), "missing device -> False")
        self.assertTrue(ok([]))


class TopicNames(unittest.TestCase):
    def test_check_topic_name(self):
        for bad in ("", "a/b", "a+b", "a#b", "a|b", "x" * 65):
            with self.assertRaises(app.HTTPException):
                app._check_topic_name(bad)
        app._check_topic_name("\u0414\u0430\u0442\u0447\u0438\u043a 1")


class DimStep(unittest.TestCase):
    """up/down on the window scale: floor is the dimmest level, one more down = off."""

    def test_down_reaches_floor_then_off(self):
        self.assertEqual(app._dim_step(20, "down"), 10)
        self.assertEqual(app._dim_step(10, "down"), 0)      # the floor itself
        self.assertIsNone(app._dim_step(0, "down"))         # below the floor -> off
        self.assertIsNone(app._dim_step(4, "down"))         # nearest tenth is 0
        self.assertIsNone(app._dim_step(None, "down"))

    def test_up_from_floor_and_ceiling(self):
        self.assertEqual(app._dim_step(0, "up"), 10)
        self.assertEqual(app._dim_step(29, "up"), 40)       # nearest tenth is 30
        self.assertEqual(app._dim_step(100, "up"), 100)

    def test_switch_for_same_gang(self):
        cfg = {"controls": [
            {"kind": "switch", "code": "state_l1"}, {"kind": "bright", "code": "brightness_l1"},
            {"kind": "switch", "code": "state_l2"}, {"kind": "bright", "code": "brightness_l2"},
        ]}
        self.assertEqual(app._switch_for(cfg, "brightness_l2")["code"], "state_l2")
        self.assertEqual(app._switch_for(cfg, "brightness_l1")["code"], "state_l1")
        single = {"controls": [{"kind": "switch", "code": "state"}, {"kind": "bright", "code": "brightness"}]}
        self.assertEqual(app._switch_for(single, "brightness")["code"], "state")
        self.assertIsNone(app._switch_for({"controls": [{"kind": "bright", "code": "brightness"}]}, "brightness"))


class ReviewFixes(unittest.TestCase):
    def test_off_condition_fails_on_missing_or_offline(self):
        app._status_cache.clear()
        self.assertFalse(app._conditions_pass([{"device": "ghost", "code": "state", "op": "off"}]))
        app._status_cache["lamp"] = {"online": False, "values": {"state": False}}
        self.assertFalse(app._conditions_pass([{"device": "lamp", "code": "state", "op": "off"}]))
        app._status_cache["lamp"]["online"] = True
        self.assertTrue(app._conditions_pass([{"device": "lamp", "code": "state", "op": "off"}]))

    def test_channel_codes_are_plain_names(self):
        ok = app._safe_controls("x", [{"code": "state_l1"}, {"code": "a.b-c"},
                                      {"code": '"><img src=x onerror=alert(1)>'}, {"code": ""}, "junk"])
        self.assertEqual([c["code"] for c in ok], ["state_l1", "a.b-c"])

    def test_same_origin(self):
        self.assertTrue(app._same_origin({"host": "h:8765"}))
        self.assertTrue(app._same_origin({"host": "h:8765", "origin": "http://h:8765"}))
        self.assertFalse(app._same_origin({"host": "h:8765", "origin": "https://evil.example"}))
        self.assertTrue(app._same_origin({"host": "app:8765", "x-forwarded-host": "home.example.org",
                                          "origin": "https://home.example.org"}))

    def test_proxy_origin_and_odd_tokens(self):
        app._EXTRA_ORIGINS.add("home.example.org")
        try:
            self.assertTrue(app._same_origin({"host": "127.0.0.1:8765", "origin": "https://home.example.org"}))
        finally:
            app._EXTRA_ORIGINS.discard("home.example.org")
        tok = app.DASHBOARD_TOKEN
        app.DASHBOARD_TOKEN = "right-token"      # with no token configured the call would fail early
        try:
            self.assertFalse(app._token_ok({"authorization": "Bearer \u0442\u043e\u043a\u0435\u043d"}))
            self.assertTrue(app._token_ok({"authorization": "Bearer right-token"}))
        finally:
            app.DASHBOARD_TOKEN = tok

    def test_token_redacted_in_access_log(self):
        import logging
        rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                ("1.2.3.4:5", "GET", "/?token=s3cret&tab=fav", "1.1", 200), None)
        app._RedactToken().filter(rec)
        self.assertNotIn("s3cret", rec.getMessage())
        self.assertIn("token=***", rec.getMessage())

    def test_own_bus_topics(self):
        self.assertTrue(app._own_topic("home/state/x/y"))
        self.assertFalse(app._own_topic("home/garage/state"))
        self.assertEqual(app._safe_controls("x", [{"code": "a", "state_topic": "home/state/x/a"}]), [])


class Scheduler(unittest.TestCase):
    """DST: a skipped local minute still fires once, a repeated hour does not fire twice."""

    def setUp(self):
        self.fired = []
        self._set, self._arm = app._set_switch, app._arm_inching
        app._set_switch = lambda d, c, on: self.fired.append((d, c, on))
        app._arm_inching = lambda d, c: None
        app._sched_fired.clear()
        app._autos.clear()
        app._autos["lamp/state"] = {"schedule": [{"time": "02:30", "days": [1, 2, 3, 4, 5, 6, 7], "action": "on"}]}

    def tearDown(self):
        app._set_switch, app._arm_inching = self._set, self._arm
        app._autos.clear()

    def test_spring_gap_fires_once(self):
        d = dt.datetime
        app._scheduler_tick(d(2026, 3, 29, 1, 59), d(2026, 3, 29, 1, 58))
        app._scheduler_tick(d(2026, 3, 29, 3, 0), d(2026, 3, 29, 1, 59))   # the clock jumped
        self.assertEqual(len(self.fired), 1)

    def test_repeated_hour_fires_once(self):
        d = dt.datetime
        app._scheduler_tick(d(2026, 10, 25, 2, 30), d(2026, 10, 25, 2, 29))
        app._scheduler_tick(d(2026, 10, 25, 2, 30), d(2026, 10, 25, 2, 29))  # the same minute again
        self.assertEqual(len(self.fired), 1)

    def test_times_normalised(self):
        self.assertEqual(app._norm_hm("7:00"), "07:00")
        self.assertEqual(app._norm_hm("06:00:00"), "06:00")
        self.assertIsNone(app._norm_hm("24:00"))


class Import(unittest.TestCase):
    def test_fixture_schedule_loaded_and_normalised(self):
        # the fixture holds "7:00": importing app survived it; loading stores "07:00"
        import json
        with open(os.path.join(_FIX, "settings.json")) as f:
            app._cfg["automations"] = json.load(f)["automations"]   # other tests clear _autos
        app._load_autos()
        self.assertEqual(app._autos["lamp/state"]["schedule"][0]["time"], "07:00")


class Concurrency(unittest.TestCase):
    def test_status_copy_does_not_touch_the_cache(self):
        app._status_cache["c1"] = {"online": True, "values": {"a": 1}, "raw": {}}
        cp = app._status_copy("c1", {})
        cp["values"]["a"] = 2
        cp["values"]["b"] = 3
        self.assertEqual(app._status_cache["c1"]["values"], {"a": 1})
        self.assertEqual(app._status_copy("missing", {"values": {}})["values"], {})

    def test_wait_z2m_wakes_on_device_list(self):
        import threading
        flag = {"done": False}

        def later():
            flag["done"] = True
            with app._z2m_devices_cv:
                app._z2m_devices_cv.notify_all()
        threading.Timer(0.2, later).start()
        t0 = dt.datetime.now()
        self.assertTrue(app._wait_z2m(lambda: flag["done"], timeout=5))
        self.assertLess((dt.datetime.now() - t0).total_seconds(), 2)


class Round7(unittest.TestCase):
    def test_availability_offline_is_immediate(self):
        app.DEVICES["zdev"] = {"id": "zdev", "name": "z", "transport": "z2m", "controls": [], "_ctl": {}}
        try:
            app._status_cache["zdev"] = {"online": True, "values": {"state": False}, "raw": {}}
            app._handle_z2m_availability("zdev", b'{"state":"offline"}')
            self.assertFalse(app._status_cache["zdev"]["online"])
            self.assertFalse(app._conditions_pass([{"device": "zdev", "code": "state", "op": "off"}]))
        finally:
            app.DEVICES.pop("zdev", None)
            app._status_cache.pop("zdev", None)

    def test_catch_up_runs_in_clock_order(self):
        seen = []
        s, a = app._set_switch, app._arm_inching
        app._set_switch = lambda d, c, on: seen.append(on)
        app._arm_inching = lambda d, c: None
        app._sched_fired.clear(); app._autos.clear()
        app._autos["h/p"] = {"schedule": [{"time": "07:30", "days": [1, 2, 3, 4, 5, 6, 7], "action": "off"},
                                          {"time": "07:00", "days": [1, 2, 3, 4, 5, 6, 7], "action": "on"}]}
        try:
            d = dt.datetime
            app._scheduler_tick(d(2026, 9, 7, 7, 45), d(2026, 9, 7, 6, 59))
            self.assertEqual(seen, [True, False])        # on at 07:00, then off at 07:30
        finally:
            app._set_switch, app._arm_inching = s, a
            app._autos.clear()

    def test_replay_does_not_consume_a_newer_arm(self):
        calls = []
        s = app._set_switch
        app._set_switch = lambda d, c, on: calls.append(d)
        try:
            app._schedule_deferred("tokR", 60, "devR", "state", False, "x")   # armed now, 60 s
            app._fire_deferred("tokR", app._REPLAY)                            # stale boot replay
            self.assertEqual(calls, [])
            self.assertTrue(app._deferred_active("tokR"))
        finally:
            app._cancel_deferred("tokR")
            app._set_switch = s

    def test_disabled_ids_follow_time_normalisation(self):
        keep = set(app._rules_disabled)
        app._rules_disabled = {"a|x/y|sched|7:00|on"}
        try:
            app._load_autos()
            self.assertIn("a|x/y|sched|07:00|on", app._rules_disabled)
        finally:
            app._rules_disabled = keep


class Round8(unittest.TestCase):
    def test_registry_update_keeps_offline(self):
        app._status_cache["zz"] = {"online": False, "availability": "offline", "values": {}, "raw": {}}
        try:
            app._register_z2m_device({"friendly_name": "zz", "type": "Router",
                                      "definition": {"exposes": [{"type": "binary", "property": "state", "access": 7}]}})
            self.assertFalse(app._status_cache["zz"]["online"])
        finally:
            app.DEVICES.pop("zz", None); app._status_cache.pop("zz", None)

    def test_settings_writers(self):
        for p in ("/api/favorites/a/b", "/api/rooms", "/api/devices/x/rename",
                  "/api/devices/x/channel/state/rename", "/api/devices/x/channel/b/dimrange", "/api/rules/delete"):
            self.assertTrue(app._writes_settings(p), p)
        for p in ("/api/devices/x/control", "/api/resync", "/api/zigbee/pair/start"):
            self.assertFalse(app._writes_settings(p), p)


class Round9(unittest.TestCase):
    def test_rules_run_is_not_a_settings_writer(self):
        self.assertFalse(app._writes_settings("/api/rules/run"))
        self.assertTrue(app._writes_settings("/api/rules/enable"))

    def test_wait_returns_at_once_when_z2m_is_down(self):
        app._z2m_bridge_online = False
        try:
            t0 = dt.datetime.now()
            self.assertFalse(app._wait_z2m(lambda: False, timeout=5))
            self.assertLess((dt.datetime.now() - t0).total_seconds(), 0.5)
        finally:
            app._z2m_bridge_online = None

    def test_bridge_state_parsed(self):
        app._handle_z2m_bridge(["zigbee2mqtt", "bridge", "state"], b'{"state":"offline"}')
        self.assertIs(app._z2m_bridge_online, False)
        app._handle_z2m_bridge(["zigbee2mqtt", "bridge", "state"], b"online")
        self.assertIs(app._z2m_bridge_online, True)
        app._z2m_bridge_online = None

    def test_control_without_broker_is_503(self):
        import asyncio
        app.DEVICES["m1"] = {"id": "m1", "name": "m", "transport": "mqtt", "category": "switch",
                             "controls": [{"kind": "switch", "code": "power", "command_topic": "x/set"}]}
        app.DEVICES["m1"]["_ctl"] = {"power": app.DEVICES["m1"]["controls"][0]}
        cli = app._mqtt_client
        app._mqtt_client = None
        try:
            with self.assertRaises(app.HTTPException) as cm:
                asyncio.run(app.control("m1", app.ControlBody(code="power", value=True)))
            self.assertEqual(cm.exception.status_code, 503)
        finally:
            app._mqtt_client = cli
            app.DEVICES.pop("m1", None)

    def test_bridge_offline_takes_its_devices_along(self):
        app.DEVICES["zb1"] = {"id": "zb1", "name": "z", "transport": "z2m", "controls": [], "_ctl": {}}
        app._status_cache["zb1"] = {"online": True, "values": {"state": True}, "raw": {}}
        app._z2m_bridge_online = True
        try:
            app._handle_z2m_bridge(["zigbee2mqtt", "bridge", "state"], b'{"state":"offline"}')
            self.assertFalse(app._status_cache["zb1"]["online"])
            self.assertFalse(app._conditions_pass([{"device": "zb1", "code": "state", "op": "on"}]))
        finally:
            app._z2m_bridge_online = None
            app.DEVICES.pop("zb1", None); app._status_cache.pop("zb1", None)

    def test_pair_start_refused_while_z2m_down(self):
        cli = app._mqtt_client
        app._mqtt_client = object()
        app._z2m_bridge_online = False
        try:
            with self.assertRaises(app.HTTPException) as cm:
                app.zigbee_pair_start()
            self.assertEqual(cm.exception.status_code, 503)
        finally:
            app._mqtt_client = cli
            app._z2m_bridge_online = None

    def test_retained_frame_while_bridge_down_stays_offline(self):
        app.DEVICES["zb2"] = {"id": "zb2", "name": "z", "transport": "z2m", "_skip_get": False,
                              "controls": [{"kind": "switch", "code": "state"}]}
        app.DEVICES["zb2"]["_ctl"] = {"state": app.DEVICES["zb2"]["controls"][0]}
        app._status_cache["zb2"] = {"online": False, "values": {}, "raw": {}}
        app._z2m_bridge_online = False
        try:
            app._handle_z2m_message(["zigbee2mqtt", "zb2"], b'{"state":"ON"}', retain=True)
            self.assertFalse(app._status_cache["zb2"]["online"])
            app._handle_z2m_bridge(["zigbee2mqtt", "bridge", "state"], b'{"state":"offline"}')  # again
            self.assertFalse(app._status_cache["zb2"]["online"])
        finally:
            app._z2m_bridge_online = None
            app.DEVICES.pop("zb2", None); app._status_cache.pop("zb2", None)


class Round15(unittest.TestCase):
    def test_rename_moves_timer_token(self):
        app.DEVICES["olddev"] = {"id": "olddev", "name": "o", "transport": "mqtt", "controls": [], "_ctl": {}}
        s = app._set_switch
        app._set_switch = lambda d, c, on: None
        try:
            app._schedule_deferred("inch|olddev/state", 60, "olddev", "state", False, "inching")
            app._migrate_device_key("olddev", "newdev")
            self.assertFalse(app._deferred_active("inch|olddev/state"))
            self.assertTrue(app._deferred_active("inch|newdev/state"))
            self.assertEqual(app._pending["inch|newdev/state"]["device"], "newdev")
        finally:
            app._cancel_deferred("inch|newdev/state"); app._cancel_deferred("inch|olddev/state")
            app._set_switch = s
            app.DEVICES.pop("olddev", None)

    def test_set_automation_is_strict(self):
        app.DEVICES["sa"] = {"id": "sa", "name": "s", "transport": "mqtt",
                             "controls": [{"kind": "switch", "code": "power"}]}
        app.DEVICES["sa"]["_ctl"] = {"power": app.DEVICES["sa"]["controls"][0]}
        try:
            for body, code in ((app.AutoBody(schedule=[{"time": "07:00", "days": [0], "action": "on"}]), "power"),
                               (app.AutoBody(schedule=[{"time": "07:00", "action": "of"}]), "power"),
                               (app.AutoBody(inching_sec=60), "nope")):
                with self.assertRaises(app.HTTPException):
                    app.set_automation("sa", code, body)
        finally:
            app.DEVICES.pop("sa", None)


class Round17(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._s, self._a = app._set_switch, app._arm_inching
        app._set_switch = lambda d, c, on: self.calls.append(("set", on))
        app._arm_inching = lambda d, c: self.calls.append(("arm", c))
        app.DEVICES["hh"] = {"id": "hh", "name": "h", "transport": "mqtt",
                             "controls": [{"kind": "switch", "code": "state"}]}
        app.DEVICES["hh"]["_ctl"] = {"state": app.DEVICES["hh"]["controls"][0]}

    def tearDown(self):
        app._set_switch, app._arm_inching = self._s, self._a
        app._cancel_deferred("sched|hh/state")
        app.DEVICES.pop("hh", None)

    def test_retried_schedule_on_arms_auto_off(self):
        app._schedule_deferred("sched|hh/state", 60, "hh", "state", True, "schedule retry")
        app._fire_deferred("sched|hh/state", app._pending_handles["sched|hh/state"])
        self.assertEqual(self.calls, [("set", True), ("arm", "state")])

    def test_one_retry_slot_per_channel(self):
        app._schedule_deferred("sched|hh/state", 60, "hh", "state", True, "schedule retry")
        app._schedule_deferred("sched|hh/state", 60, "hh", "state", False, "schedule retry")
        self.assertFalse(app._pending["sched|hh/state"]["value"])       # the later row won

    def test_vanished_channel_retry_dropped(self):
        app._schedule_deferred("sched|hh/gone", 60, "hh", "gone", True, "schedule retry")
        app._fire_deferred("sched|hh/gone", app._pending_handles["sched|hh/gone"])
        self.assertEqual(self.calls, [])
        self.assertFalse(app._deferred_active("sched|hh/gone"))

    def test_any_actuation_cancels_a_schedule_retry(self):
        app._schedule_deferred("sched|hh/state", 60, "hh", "state", True, "schedule retry")
        g = app._mqtt_generic_publish_set
        app._mqtt_generic_publish_set = lambda *a, **k: None
        try:
            app._apply_value("hh", "state", False, "switch")    # e.g. an OFF from a wall button binding
            self.assertFalse(app._deferred_active("sched|hh/state"))
        finally:
            app._mqtt_generic_publish_set = g

    def test_failed_actuation_keeps_the_retry(self):
        app._schedule_deferred("sched|hh/state", 60, "hh", "state", True, "schedule retry")
        g = app._mqtt_generic_publish_set

        def down(*a, **k):
            raise RuntimeError("mqtt publish failed (rc=4)")
        app._mqtt_generic_publish_set = down
        try:
            with self.assertRaises(RuntimeError):
                app._apply_value("hh", "state", False, "switch")
            self.assertTrue(app._deferred_active("sched|hh/state"))   # nothing was delivered
        finally:
            app._mqtt_generic_publish_set = g
