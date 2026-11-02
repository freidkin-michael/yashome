"""Fixes from the full core-backend review of 2026-09-27 (CB-NN, PC-09)."""
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import test_pure  # noqa: E402,F401  (sets HOME_STACK_ROOT to a private copy of the fixtures)
import app  # noqa: E402
from tools.webpage import page as _dashboard_page   # index.html with web/app.css and web/js/*.js inline
HOUSE_REQ = type("HouseRequest", (), {"headers": {"authorization": "Bearer " + app.DASHBOARD_TOKEN}})()


def _mw(method, path):
    """Run the token middleware on a request without a token: 'passed' or the refusal's status."""
    from starlette.requests import Request
    scope = {"type": "http", "method": method, "path": path, "root_path": "", "headers": [],
             "query_string": b""}

    async def nxt(_req):
        return "passed"
    r = asyncio.run(app._require_token(Request(scope), nxt))
    return r if r == "passed" else r.status_code


class NonFiniteState(unittest.TestCase):
    def setUp(self):
        app.DEVICES["nf"] = {"id": "nf", "name": "n", "transport": "mqtt", "category": "sensor",
                             "state_topic": "home/nf/state",
                             "controls": [{"kind": "sensor", "code": "t", "state_topic": "home/nf/t"},
                                          {"kind": "bright", "code": "b"}]}
        app.DEVICES["nf"]["_ctl"] = {c["code"]: c for c in app.DEVICES["nf"]["controls"]}

    def tearDown(self):
        app.DEVICES.pop("nf", None)
        app._status_cache.pop("nf", None)

    def test_nan_scalar_and_json_literals_stay_json(self):
        app._handle_mqtt_state("nf", "ctl", "t", b"nan")
        app._handle_mqtt_state("nf", "state", None, b'{"b": Infinity, "t": NaN}')
        app._handle_mqtt_state("nf", "state", None, b'{"b": "inf"}')
        vals = app._status_cache["nf"]["values"]
        self.assertIsNone(vals["t"])
        self.assertIsNone(vals["b"])
        json.dumps(app._status_cache["nf"], allow_nan=False)    # raises on NaN/inf


class TopicNameControlChars(unittest.TestCase):
    def test_nul_and_control_chars_refused(self):
        for bad in ("a\x00b", "a\nb", "tab\t", "del\x7f", None, 5):
            with self.assertRaises(app.HTTPException):
                app._check_topic_name(bad)
        app._check_topic_name("Lamp 2")


class PluginActionNotJson(unittest.TestCase):
    def test_non_json_entry_refused_and_not_stored(self):
        app.BINDING_ACTIONS["t_nojson"] = {"validate": lambda b: {"action": "t_nojson", "target": "x",
                                                                  "when": object()}}
        app.DEVICES["pj"] = {"id": "pj", "name": "p", "transport": "mqtt",
                             "controls": [{"kind": "trigger_text", "code": "action"}]}
        app.DEVICES["pj"]["_ctl"] = {"action": app.DEVICES["pj"]["controls"][0]}
        try:
            with self.assertRaises(app.HTTPException) as cm:
                app.put_binding("pj", "action", "single", app.BindingBody(target="x", action="t_nojson"), HOUSE_REQ)
            self.assertEqual(cm.exception.status_code, 502)
            self.assertNotIn("single", app._bindings.get("pj", {}).get("action", {}))
            app._save_settings()                   # still saves
        finally:
            app.BINDING_ACTIONS.pop("t_nojson", None)
            app._bindings.pop("pj", None)
            app.DEVICES.pop("pj", None)


class NegativeHyst(unittest.TestCase):
    def test_negative_hyst_refused(self):
        app.DEVICES["hs"] = {"id": "hs", "name": "h", "transport": "mqtt",
                             "controls": [{"kind": "sensor", "code": "t"}, {"kind": "switch", "code": "p"}]}
        app.DEVICES["hs"]["_ctl"] = {c["code"]: c for c in app.DEVICES["hs"]["controls"]}
        try:
            for thr, hyst in ((20, -1), (float("nan"), 1), (20, float("inf"))):
                with self.assertRaises(app.HTTPException):
                    app.put_binding("hs", "t", "above", app.BindingBody(target="hs", code="p", action="on",
                                                                        threshold=thr, hyst=hyst), HOUSE_REQ)
            self.assertNotIn("hs", app._bindings)
        finally:
            app._bindings.pop("hs", None)
            app.DEVICES.pop("hs", None)


class InchingArmed(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._s, self._a, self._c = app._set_switch, app._arm_inching, app._cancel_inching
        self._g = app._mqtt_generic_publish_set
        app._set_switch = lambda d, c, on: self.calls.append(("set", on))
        app._arm_inching = lambda d, c: self.calls.append(("arm", c))
        app._cancel_inching = lambda k: self.calls.append(("cancel", k))
        app._mqtt_generic_publish_set = lambda *a, **k: None
        app.DEVICES["ia"] = {"id": "ia", "name": "i", "transport": "mqtt",
                             "controls": [{"kind": "switch", "code": "p"}, {"kind": "trigger_text", "code": "b"}]}
        app.DEVICES["ia"]["_ctl"] = {c["code"]: c for c in app.DEVICES["ia"]["controls"]}

    def tearDown(self):
        app._set_switch, app._arm_inching, app._cancel_inching = self._s, self._a, self._c
        app._mqtt_generic_publish_set = self._g
        app._cancel_deferred("for|x")
        app._bindings.pop("ia", None)
        app.DEVICES.pop("ia", None)
        app._status_cache.pop("ia", None)

    def test_toggle_to_on_arms_and_to_off_cancels(self):
        app._status_cache["ia"] = {"online": True, "values": {"p": False}, "raw": {}}
        app._bindings["ia"] = {"b": {"single": {"target": "ia", "code": "p", "action": "toggle"}}}
        app._execute_binding("ia", "b", "single", force=True)
        app._execute_binding("ia", "b", "single", force=True)
        self.assertEqual(self.calls, [("arm", "p"), ("cancel", "ia/p")])

    def test_for_revert_to_on_arms(self):
        app._schedule_deferred("for|x", 60, "ia", "p", True, "for")
        app._fire_deferred("for|x", app._pending_handles["for|x"])
        self.assertEqual(self.calls, [("set", True), ("arm", "p")])




class OpenPaths(unittest.TestCase):
    def test_open_only_for_reads_and_never_under_plugins(self):
        self.assertEqual(_mw("GET", "/"), "passed")
        self.assertEqual(_mw("GET", "/icon-192.png"), "passed")
        self.assertEqual(_mw("POST", "/icon-x"), 401)
        self.assertEqual(_mw("POST", "/"), 401)
        app.OPEN_PATHS.update({"/t_page", "/plugins/hello/t_late"})
        try:
            self.assertEqual(_mw("GET", "/t_page"), "passed")
            self.assertEqual(_mw("DELETE", "/t_page"), 401)
            self.assertEqual(_mw("GET", "/plugins/hello/t_late"), 401)   # added after the load check
        finally:
            app.OPEN_PATHS.difference_update({"/t_page", "/plugins/hello/t_late"})


class WsGuard(unittest.TestCase):
    def test_websocket_without_token_closed_before_the_route(self):
        sent, reached = [], []

        async def inner(scope, receive, send):
            reached.append(scope["path"])

        async def send(msg):
            sent.append(msg)

        async def receive():
            return {"type": "websocket.connect"}
        g = app._WsTokenGuard(inner)
        scope = {"type": "websocket", "path": "/plugins/hello/live", "root_path": "", "headers": [],
                 "query_string": b"", "scheme": "ws", "server": ("x", 80)}
        asyncio.run(g(scope, receive, send))
        self.assertEqual(reached, [])
        self.assertEqual(sent[0]["type"], "websocket.close")
        keep = app.DASHBOARD_TOKEN
        app.DASHBOARD_TOKEN = "t0ken"
        try:
            asyncio.run(g({**scope, "query_string": b"token=t0ken"}, receive, send))
            self.assertEqual(reached, ["/plugins/hello/live"])
        finally:
            app.DASHBOARD_TOKEN = keep


class HookListsAppendOnly(unittest.TestCase):
    def test_insert_at_the_front_refused(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "ins").mkdir()
        (d / "ins" / "__init__.py").write_text(
            "import app as core\ncore.MQTT_HANDLERS.insert(0, (('home', 'cmd'), print))\n")
        before = list(app.MQTT_HANDLERS)
        self.assertEqual(app._load_plugins(d, top="t_ins"), [])
        self.assertIn("ins", app._PLUGIN_ERRORS)
        self.assertEqual(app.MQTT_HANDLERS, before)


class BadDimRange(unittest.TestCase):
    def test_malformed_window_ignored(self):
        ctl = [{"kind": "bright", "code": "brightness"}]
        for bad in ([10], "x", None, [5, "y"]):
            app._channel_dimrange["dr"] = {"brightness": bad}
            self.assertNotIn("floor", app._controls_with_dimrange("dr", ctl)[0])
        app._channel_dimrange["dr"] = [10]
        self.assertNotIn("floor", app._controls_with_dimrange("dr", ctl)[0])
        app._channel_dimrange["dr"] = {"brightness": [10, 80]}
        try:
            self.assertEqual(app._controls_with_dimrange("dr", ctl)[0]["floor"], 10)
        finally:
            app._channel_dimrange.pop("dr", None)

    def test_post_over_a_list_entry(self):
        app.DEVICES["drp"] = {"id": "drp", "name": "d", "controls": [{"kind": "bright", "code": "brightness"}]}
        app._channel_dimrange["drp"] = [10]
        try:
            r = app.set_dimrange("drp", "brightness", app.DimRangeBody(floor=10, ceil=80))
            self.assertEqual((r["floor"], r["ceil"]), (10, 80))
            self.assertEqual(app._channel_dimrange["drp"], {"brightness": [10, 80]})
        finally:
            app._channel_dimrange.pop("drp", None)
            app.DEVICES.pop("drp", None)


class ScheduleRetryFollowsRule(unittest.TestCase):
    def setUp(self):
        app.DEVICES["sr"] = {"id": "sr", "name": "s", "transport": "mqtt",
                             "controls": [{"kind": "switch", "code": "p"}]}
        app.DEVICES["sr"]["_ctl"] = {"p": app.DEVICES["sr"]["controls"][0]}
        app._autos["sr/p"] = {"schedule": [{"time": "07:00", "action": "on", "days": [1]},
                                           {"time": "22:00", "action": "off", "days": [1]}]}

    def tearDown(self):
        app._cancel_deferred("sched|sr/p")
        app._rules_disabled.difference_update({"a|sr/p|sched|07:00|on", "a|sr/p|sched|22:00|off"})
        app._autos.pop("sr/p", None)
        app.DEVICES.pop("sr", None)

    def test_disable_and_delete_drop_the_retry_of_that_action(self):
        rids = {r["id"] for r in app._project_rules()}
        on_id = next(r for r in rids if r.startswith("a|sr/p|sched|") and r.endswith("|on"))
        off_id = next(r for r in rids if r.startswith("a|sr/p|sched|") and r.endswith("|off"))
        app._schedule_deferred("sched|sr/p", 60, "sr", "p", True, "schedule retry")
        app.rule_enable(app.RuleIdBody(id=off_id, enabled=False), HOUSE_REQ)
        self.assertTrue(app._deferred_active("sched|sr/p"))          # the other row's retry stays
        app.rule_enable(app.RuleIdBody(id=on_id, enabled=False), HOUSE_REQ)
        self.assertFalse(app._deferred_active("sched|sr/p"))
        app._schedule_deferred("sched|sr/p", 60, "sr", "p", False, "schedule retry")
        app.rule_delete(app.RuleIdBody(id=off_id), HOUSE_REQ)
        self.assertFalse(app._deferred_active("sched|sr/p"))




class PctSuffix(unittest.TestCase):
    def test_real_control_named_pct_is_not_stripped(self):
        app.DEVICES["pc"] = {"id": "pc", "name": "p", "transport": "mqtt",
                             "controls": [{"kind": "setting_int", "code": "fan_pct"},
                                          {"kind": "bright", "code": "brightness"}]}
        app.DEVICES["pc"]["_ctl"] = {c["code"]: c for c in app.DEVICES["pc"]["controls"]}
        got = []

        async def fake(dev, body):
            got.append(body.code)
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        ctl, main = app.control, app._MAIN_LOOP
        app.control, app._MAIN_LOOP = fake, loop
        try:
            for code in ("fan_pct", "brightness_pct"):
                app._mqtt_on_message(None, None, types.SimpleNamespace(
                    topic=f"home/cmd/pc/{code}", payload=b"40", retain=False))
            end = time.time() + 2
            while len(got) < 2 and time.time() < end:
                time.sleep(0.01)
            self.assertEqual(got, ["fan_pct", "brightness"])
        finally:
            app.control, app._MAIN_LOOP = ctl, main
            loop.call_soon_threadsafe(loop.stop)
            app.DEVICES.pop("pc", None)


class MqttDevicesNonObject(unittest.TestCase):
    def test_upsert_keeps_foreign_entries(self):
        path, keep = pathlib.Path(tempfile.mkdtemp()) / "mqtt_devices.json", app.MQTT_DEVICES_PATH
        path.write_text(json.dumps(["junk", 5, {"id": "old", "name": "o"}]))
        app.MQTT_DEVICES_PATH = path
        try:
            app._mqtt_devices_upsert({"id": "old", "name": "n", "_x": 1})
            self.assertEqual(json.loads(path.read_text()), ["junk", 5, {"id": "old", "name": "n"}])
            path.write_text('{"a": 1}')
            with self.assertRaises(app.HTTPException):
                app._mqtt_devices_upsert({"id": "a"})
        finally:
            app.MQTT_DEVICES_PATH = keep


class DiscoveryTopics(unittest.TestCase):
    def test_core_bus_and_wildcard_topics_dropped(self):
        node = {"availability_topic": None, "model": None, "title": None, "controls": [
            {"kind": "switch", "code": "ok", "state_topic": "n1/ok/state", "command_topic": "n1/ok/set"},
            {"kind": "switch", "code": "zb", "state_topic": None,
             "command_topic": "ns9/bridge/request/permit_join"},
            {"kind": "switch", "code": "wc", "state_topic": "n1/#", "command_topic": "n1/wc/set"},
            {"kind": "switch", "code": "sy", "state_topic": "$SYS/x", "command_topic": "n1/sy/set"},
            {"kind": "switch", "code": "bo", "state_topic": None, "command_topic": "home/provision/n2/do"}]}
        saved = []
        keep = (app._need_broker, app._esphome_discover, app._mqtt_devices_upsert,
                app._register_mqtt_device, app._mqtt_resubscribe_generic)
        app._need_broker = lambda: None
        app._esphome_discover = lambda: {"n1": node}
        app._mqtt_devices_upsert = saved.append
        app._register_mqtt_device = lambda e: e
        app._mqtt_resubscribe_generic = lambda: 0
        only = app._BACKEND_ONLY
        app._BACKEND_ONLY = ["home/provision/+/do"]
        app.MQTT_NAMESPACES.add("ns9")            # a plug-in's namespace: no node may point into it
        try:
            r = app.esphome_add(app.EsphomeAdd(node="n1", id="t_n1", force=True))
            self.assertEqual(r["controls"], ["ok"])
            node["availability_topic"] = "ns9/x"
            with self.assertRaises(app.HTTPException):
                app.esphome_add(app.EsphomeAdd(node="n1", id="t_n1", force=True))
        finally:
            app._BACKEND_ONLY = only
            app.MQTT_NAMESPACES.discard("ns9")
            (app._need_broker, app._esphome_discover, app._mqtt_devices_upsert,
             app._register_mqtt_device, app._mqtt_resubscribe_generic) = keep





class RelinkMergesAndChannelNames(unittest.TestCase):
    def test_relink_keeps_what_the_target_has(self):
        app._bindings["old1"] = {"action": {"single": {"target": "x", "code": "power", "action": "toggle"},
                                            "double": {"target": "y", "code": "power", "action": "toggle"}}}
        app._bindings["new1"] = {"action": {"single": {"target": "z", "code": "power", "action": "on"}}}
        app._channel_names["old1"] = {"a": "Old A", "b": "Old B"}
        app._channel_names["new1"] = {"a": "New A"}
        app._name_overrides["old1"] = "Old name"
        app._name_overrides["new1"] = "New name"
        try:
            app._migrate_device_key("old1", "new1")
            b = app._bindings["new1"]["action"]
            self.assertEqual(b["single"]["target"], "z")
            self.assertEqual(b["double"]["target"], "y")
            self.assertEqual(app._channel_names["new1"], {"a": "New A", "b": "Old B"})
            self.assertEqual(app._name_overrides["new1"], "New name")
            self.assertNotIn("old1", app._bindings)
        finally:
            for k in ("old1", "new1"):
                app._bindings.pop(k, None)
                app._channel_names.pop(k, None)
                app._name_overrides.pop(k, None)

    def test_a_rename_moves_the_brightness_windows(self):
        app._channel_dimrange["dr_old"] = {"b1": [10, 60], "b2": [5, 90]}
        app._channel_dimrange["dr_new"] = {"b1": [20, 80]}
        app._channel_dimrange["dr_same"] = {"b1": [15, 70]}
        try:
            touched = app._migrate_device_key("dr_old", "dr_new")
            self.assertEqual(app._channel_dimrange["dr_new"], {"b1": [20, 80], "b2": [5, 90]})
            self.assertNotIn("dr_old", app._channel_dimrange)
            self.assertEqual(touched["channel_dimrange"], 1)
            app._migrate_device_key("dr_same", "dr_same")
            self.assertEqual(app._channel_dimrange["dr_same"], {"b1": [15, 70]})
        finally:
            for k in ("dr_old", "dr_new", "dr_same"):
                app._channel_dimrange.pop(k, None)

    def test_channel_renames_reach_generic_and_plugin_devices(self):
        app._channel_names["gen1"] = {"power": "Lamp"}
        app._channel_names["plug1"] = {"power": "Fan"}
        try:
            d = app._register_mqtt_device({"id": "gen1", "name": "g", "controls": [{"kind": "switch", "code": "power", "label": "Power"}]})
            self.assertEqual(d["_ctl"]["power"]["label"], "Lamp")
            self.assertEqual(d["_ctl"]["power"]["_label0"], "Power")
            p = app.register_device({"id": "plug1", "name": "p", "transport": "t_cn", "controls": [{"kind": "switch", "code": "power", "label": "Power"}]})
            self.assertEqual(p["_ctl"]["power"]["label"], "Fan")
            u = app.update_device({"id": "plug1", "name": "p", "transport": "t_cn", "controls": [{"kind": "switch", "code": "power", "label": "Power"}]})
            self.assertEqual(u["_ctl"]["power"]["label"], "Fan")
        finally:
            for k in ("gen1", "plug1"):
                app._channel_names.pop(k, None)
                app.DEVICES.pop(k, None)
            app._PLUGIN_DEVICES.discard("plug1")


class RelinkLoserLeavesNoState(unittest.TestCase):
    def test_disabled_ids_of_the_loser_are_dropped(self):
        app._bindings["rl_old"] = {"action": {"single": {"target": "x", "code": "power", "action": "on"}}}
        app._bindings["rl_new"] = {"action": {"single": {"target": "y", "code": "power", "action": "on"}}}
        app._autos["rl_old/state"] = {"schedule": [{"time": "07:00", "action": "on"}]}
        app._autos["rl_new/state"] = {"schedule": [{"time": "08:00", "action": "on"}]}
        app._rules_disabled.update({"b|rl_old|action|single", "a|rl_old/state|sched|07:00|on"})
        save = app._save_settings
        app._save_settings = lambda: None
        try:
            app._migrate_device_key("rl_old", "rl_new")
            self.assertNotIn("b|rl_new|action|single", app._rules_disabled)
            self.assertNotIn("a|rl_new/state|sched|07:00|on", app._rules_disabled)
            self.assertEqual(app._bindings["rl_new"]["action"]["single"]["target"], "y")
            self.assertTrue(app._lost_rule("for|b|rl_new|action|single", {"b|rl_new|action|single"}))
            self.assertTrue(app._lost_rule("sched|rl_new/state", {"a|rl_new/state"}))
            self.assertFalse(app._lost_rule("sched|rl_new/state2", {"a|rl_new/state"}))
        finally:
            app._save_settings = save
            for k in ("rl_old", "rl_new"):
                app._bindings.pop(k, None)
                app._autos.pop(k + "/state", None)
            app._rules_disabled.difference_update({"b|rl_old|action|single", "a|rl_old/state|sched|07:00|on",
                                                   "b|rl_new|action|single", "a|rl_new/state|sched|07:00|on"})


if __name__ == "__main__":
    unittest.main()


class RuleSafety(unittest.TestCase):
    def setUp(self):
        app.DEVICES["rs"] = {"id": "rs", "name": "rs", "transport": "mqtt", "controls": [
            {"kind": "switch", "code": "p"}, {"kind": "sensor", "code": "t", "state_topic": "rs/t"},
            {"kind": "sensor", "code": "m", "vtype": "bool", "state_topic": "rs/m"}, {"kind": "trigger_text", "code": "b"}]}
        app.DEVICES["rs"]["_ctl"] = {c["code"]: c for c in app.DEVICES["rs"]["controls"]}
        self._save, app._save_settings = app._save_settings, lambda: None

    def tearDown(self):
        app._save_settings = self._save
        app._bindings.pop("rs", None)
        app.DEVICES.pop("rs", None)
        app._status_cache.pop("rs", None)
        app._sensor_last.pop("rs/m", None)
        app._cancel_deferred("for|b|rs|b|single")

    def test_a_rule_never_targets_a_read_only_channel(self):
        with self.assertRaises(app.HTTPException) as e:
            app.put_binding("rs", "b", "single", app.BindingBody(target="rs", code="t", action="on"), HOUSE_REQ)
        self.assertEqual(e.exception.status_code, 400)
        app.put_binding("rs", "b", "single", app.BindingBody(target="rs", code="p", action="on"), HOUSE_REQ)
        self.assertEqual(app._bindings["rs"]["b"]["single"]["code"], "p")

    def test_a_deleted_or_disabled_rule_leaves_no_revert_behind(self):
        for how in ("delete", "enable"):
            app._bindings["rs"] = {"b": {"single": {"target": "rs", "code": "p", "action": "on", "for": 60}}}
            app._schedule_deferred("for|b|rs|b|single", 60, "rs", "p", False, "for")
            if how == "delete":
                app.rule_delete(app.RuleIdBody(id="b|rs|b|single"), HOUSE_REQ)
            else:
                app.rule_enable(app.RuleIdBody(id="b|rs|b|single", enabled=False), HOUSE_REQ)
            self.assertNotIn("for|b|rs|b|single", app._pending, how)
            app._rules_disabled.discard("b|rs|b|single")

    def test_a_retained_frame_fires_no_rule(self):
        fired = []
        keep, app._bg.submit = app._bg.submit, lambda fn, *a: fired.append(a)
        try:
            app._handle_mqtt_state("rs", "ctl", "m", b"ON", retain=False)       # the first reading: no edge
            app._handle_mqtt_state("rs", "ctl", "m", b"OFF", retain=True)       # a reconnect's retained frame
            self.assertEqual(fired, [])
            app._handle_mqtt_state("rs", "ctl", "m", b"OFF", retain=False)
            self.assertEqual(fired, [("rs", "m", "false")])
        finally:
            app._bg.submit = keep


class DimmersWait(unittest.TestCase):
    def test_brightness_and_its_switch_are_confirm_channels_when_reported(self):
        def dev(controls, **kw):
            import copy
            d = {"id": "dw", "transport": "mqtt", "controls": copy.deepcopy(controls), **kw}
            app._mark_dimmers(d)
            return {c["code"]: c.get("confirm") for c in d["controls"]}
        esp = [{"kind": "switch", "code": "light1", "state_topic": "n/s"}, {"kind": "bright", "code": "light1_bright", "state_topic": "n/b"},
               {"kind": "switch", "code": "relay", "state_topic": "n/r"}]
        self.assertEqual(dev(esp), {"light1": True, "light1_bright": True, "relay": None})
        z = [{"kind": "switch", "code": "state_l2"}, {"kind": "bright", "code": "brightness_l2"}, {"kind": "switch", "code": "state"}]
        self.assertEqual(dev(z, transport="zigbee"), {"state_l2": True, "brightness_l2": True, "state": None})
        self.assertEqual(dev(z, state_topic="z/dev"), {"state_l2": True, "brightness_l2": True, "state": None})
        self.assertEqual(dev(z), {"state_l2": None, "brightness_l2": None, "state": None})   # never reported
        own = [{"kind": "bright", "code": "brightness", "state_topic": "a", "confirm": False}, {"kind": "switch", "code": "state", "state_topic": "b"}]
        self.assertEqual(dev(own), {"brightness": False, "state": True})
        ro = [{"kind": "bright", "code": "brightness", "state_topic": "a", "ro": True}, {"kind": "switch", "code": "state", "state_topic": "b"}]
        self.assertEqual(dev(ro), {"brightness": None, "state": None})

    def test_every_way_a_device_comes_in_marks_its_dimmer(self):
        ctl = [{"kind": "switch", "code": "light", "command_topic": "e/s", "state_topic": "e/ss"},
               {"kind": "bright", "code": "light_bright", "command_topic": "e/b", "state_topic": "e/bs"}]
        import json
        import os
        import shutil
        import subprocess
        import sys
        import tempfile
        core = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        root = tempfile.mkdtemp()
        shutil.copytree(os.path.join(core, "tests", "fixtures"), root, dirs_exist_ok=True)
        with open(os.path.join(root, "mqtt_devices.json"), "w") as f:
            json.dump([{"id": "dw5", "name": "F", "category": "light", "controls": ctl}], f)
        code = "import app, json; print(json.dumps([c.get('confirm') for c in app.DEVICES['dw5']['controls']]))"
        r = subprocess.run([sys.executable, "-c", code], cwd=core, env={**os.environ, "HOME_STACK_ROOT": root},
                           capture_output=True, text=True, timeout=60)
        shutil.rmtree(root, ignore_errors=True)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]), [True, True], r.stderr[-300:])
        p = app._plugin_device({"id": "dw3", "name": "P", "transport": "zigbee",
                                "controls": [{"kind": "switch", "code": "state"}, {"kind": "bright", "code": "brightness"}]})
        self.assertTrue(all(c.get("confirm") for c in p["controls"]))
        try:
            d = app._register_mqtt_device({"id": "dw4", "name": "E", "category": "light", "controls": ctl})
            self.assertTrue(all(c.get("confirm") for c in d["controls"]))
        finally:
            for k in ("dw4",):
                app.DEVICES.pop(k, None)
                app.BY_ID_NAME.pop(k, None)
                app._status_cache.pop(k, None)

    def test_a_command_to_a_dimmer_leaves_the_cache_to_the_device(self):
        import asyncio
        sent = []

        class Cli:
            def is_connected(self):
                return True

            def publish(self, topic, payload=None, *a, **k):
                sent.append((topic, payload))
        d = {"id": "dw2", "name": "Dim", "transport": "mqtt", "category": "light", "controls": [
            {"kind": "bright", "code": "brightness", "command_topic": "x/b", "state_topic": "x/bs", "min": 0, "max": 100},
            {"kind": "switch", "code": "state", "command_topic": "x/s", "state_topic": "x/ss"},
            {"kind": "switch", "code": "relay", "command_topic": "x/r", "state_topic": "x/rs"}]}
        app._mark_dimmers(d)
        d["_ctl"] = {c["code"]: c for c in d["controls"]}
        app.DEVICES["dw2"] = d
        cli = app._mqtt_client
        app._mqtt_client = Cli()
        app._status_cache["dw2"] = {"online": True, "values": {"brightness": 10, "state": False, "relay": False}}
        try:
            r = asyncio.run(app.control("dw2", app.ControlBody(code="brightness", value=80)))
            self.assertTrue(r["waiting"])
            self.assertIn(("x/b", "80"), [(t, str(p)) for t, p in sent])
            r = asyncio.run(app.control("dw2", app.ControlBody(code="state", value=True)))
            self.assertTrue(r["waiting"])
            self.assertEqual(app._status_cache["dw2"]["values"], {"brightness": 10, "state": False, "relay": False})
            r = asyncio.run(app.control("dw2", app.ControlBody(code="relay", value=True)))
            self.assertNotIn("waiting", r)                                    # a plain switch: as before
            self.assertTrue(app._status_cache["dw2"]["values"]["relay"])
        finally:
            app._mqtt_client = cli
            app.DEVICES.pop("dw2", None)
            app._status_cache.pop("dw2", None)


class _Connected:
    """A broker client that is connected and records what is published."""

    def __init__(self):
        self.sent = {}

    def is_connected(self):
        return True

    def publish(self, t, p=None, retain=False, *a, **k):
        self.sent[t] = p


class DeviceLife(unittest.TestCase):
    """docs/device-state.md: live / waiting / offline from what the core stores."""
    ID = "lf1"

    def setUp(self):
        app.DEVICES[self.ID] = {"id": self.ID, "name": "L", "transport": "mqtt", "category": "switch",
                                "availability_topic": "lf/status", "controls": [
                                    {"kind": "switch", "code": "power", "state_topic": "lf/p", "command_topic": "lf/p/set"}]}
        app.DEVICES[self.ID]["_ctl"] = {"power": app.DEVICES[self.ID]["controls"][0]}
        self.pub, app._mqtt_client = app._mqtt_client, None
        app._status_cache.pop(self.ID, None)

    def tearDown(self):
        app._mqtt_client = self.pub
        app.DEVICES.pop(self.ID, None)
        app._status_cache.pop(self.ID, None)

    def st(self):
        return app._status_cache[self.ID]

    def frame(self, value, retain=False):
        app._handle_mqtt_state(self.ID, "ctl", "power", value.encode(), retain)

    def avail(self, word, retain=False):
        app._handle_mqtt_state(self.ID, "avail", None, word.encode(), retain)

    def test_offline_then_back_waits_for_the_report_and_shows_it(self):
        self.frame("ON")
        self.assertTrue(self.st()["online"])
        self.assertIn("last_seen", self.st())
        self.avail("offline")
        self.assertFalse(self.st()["online"])
        self.assertTrue(self.st()["offline_since"])
        self.assertTrue(self.st()["values"]["power"])                  # the last value, kept as last
        self.avail("online")
        self.assertEqual((self.st()["online"], self.st()["waiting"]), (False, "return"))
        self.assertNotIn("offline_since", self.st())
        self.frame("OFF")
        self.assertTrue(self.st()["online"])
        self.assertNotIn("waiting", self.st())
        self.assertFalse(self.st()["values"]["power"])

    def test_back_with_the_same_value_still_ends_the_wait(self):
        self.frame("ON")
        self.avail("offline")
        self.avail("online")
        self.frame("ON")
        self.assertTrue(self.st()["online"])
        self.assertNotIn("waiting", self.st())

    def test_a_report_before_the_availability_brings_it_back(self):
        self.frame("ON")
        self.avail("offline")
        self.frame("OFF")
        self.assertTrue(self.st()["online"])
        self.avail("online")                                           # a no-op now
        self.assertTrue(self.st()["online"])
        self.assertNotIn("waiting", self.st())

    def test_a_start_waits_and_its_retained_availability_decides(self):
        app._status_cache[self.ID] = {"online": False, "waiting": "start", "values": {"power": True}, "raw": {}}
        self.frame("ON", retain=True)                                  # a retained state frame: still waiting
        self.assertEqual(self.st().get("waiting"), "start")
        self.avail("online", retain=True)
        self.assertTrue(self.st()["online"])
        self.assertNotIn("waiting", self.st())

    def test_a_start_with_a_retained_offline_stays_offline(self):
        app._status_cache[self.ID] = {"online": False, "waiting": "start", "values": {"power": True}, "raw": {}}
        self.avail("offline", retain=True)
        self.assertFalse(self.st()["online"])
        self.assertTrue(self.st()["offline_since"])
        self.assertNotIn("waiting", self.st())

    def test_the_cache_file_loads_as_waiting(self):
        import json
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({self.ID: {"online": True, "values": {"power": True}, "last_seen": 5},
                   "lf_gone": {"online": False, "offline_since": 7, "values": {}}}, f)
        f.close()
        keep = app.STATUS_CACHE_PATH
        app.STATUS_CACHE_PATH = app.Path(f.name)
        try:
            app._load_status_cache()
            self.assertEqual((self.st()["online"], self.st()["waiting"], self.st()["last_seen"]), (False, "start", 5))
            self.assertEqual(app._status_cache["lf_gone"]["offline_since"], 7)
            self.assertNotIn("waiting", app._status_cache["lf_gone"])
        finally:
            app.STATUS_CACHE_PATH = keep
            app._status_cache.pop("lf_gone", None)

    def test_after_the_start_grace_a_silent_device_is_live_and_a_returning_one_still_waits(self):
        app._mqtt_client = _Connected()
        app._status_cache[self.ID] = {"online": False, "waiting": "start", "values": {"power": True}, "raw": {}}
        app._status_cache["lf2"] = {"online": False, "waiting": "return", "values": {}, "raw": {}}
        try:
            app._liveness_tick(app._STARTED + app.START_GRACE_S - 1)
            self.assertEqual(self.st()["waiting"], "start")
            v0 = app._ws_version
            app._liveness_tick(app._STARTED + app.START_GRACE_S + 1)
            self.assertTrue(self.st()["online"])
            self.assertNotIn("waiting", self.st())
            self.assertGreater(app._ws_version, v0)                 # the screens hear it at once
            self.assertEqual(app._status_cache["lf2"]["waiting"], "return")
        finally:
            app._status_cache.pop("lf2", None)

    def test_a_command_says_nothing_about_life(self):
        app._status_cache[self.ID] = {"online": False, "waiting": "return", "values": {"power": False}, "raw": {}}
        app._optimistic(self.ID, "power", True)
        self.assertEqual((self.st()["online"], self.st()["waiting"]), (False, "return"))
        self.assertTrue(self.st()["values"]["power"])
        app._status_cache[self.ID] = {"online": False, "offline_since": 3, "values": {}, "raw": {}}
        app._optimistic(self.ID, "power", True)
        self.assertEqual(self.st()["offline_since"], 3)
        self.assertFalse(self.st()["online"])

    def test_a_poller_that_fails_goes_offline_after_its_grace(self):
        self.frame("ON")
        for _ in range(app.OFFLINE_AFTER_N_FAILS - 1):
            app._store_status(self.ID, {"online": False, "error": "x", "values": {}, "raw": {}})
            self.assertTrue(self.st()["online"])
        app._store_status(self.ID, {"online": False, "error": "x", "values": {}, "raw": {}})
        self.assertFalse(self.st()["online"])
        self.assertTrue(self.st()["offline_since"])

    def test_the_mirror_says_offline_while_waiting(self):
        sent = {}

        class Cli:
            def publish(self, t, p=None, retain=False, *a, **k):
                sent[t] = p
        app._mqtt_client = Cli()
        app._mqtt_last_pub.clear()
        self.frame("ON")
        self.assertEqual(sent[f"home/state/{self.ID}/online"], "true")
        self.avail("offline")
        self.avail("online")
        self.assertEqual(sent[f"home/state/{self.ID}/online"], "false")
        self.assertEqual(sent[f"home/state/{self.ID}/power"], "true")      # the last value stays


class SilenceRule(unittest.TestCase):
    """A periodic device (a heartbeat) silent past its limit is offline; event-only devices never."""

    def setUp(self):
        self.ids = ["sl1", "sl2", "sl3", "sl4"]
        for i in self.ids:
            app.DEVICES[i] = {"id": i, "name": i, "transport": "mqtt", "category": "sensor", "controls": [],
                              "_ctl": {}}
        app.DEVICES["sl3"]["availability_topic"] = "sl3/status"
        app.DEVICES["sl4"]["offline_after_s"] = 60
        self.pub, app._mqtt_client = app._mqtt_client, _Connected()

    def tearDown(self):
        app._mqtt_client = self.pub
        for i in self.ids:
            app.DEVICES.pop(i, None)
            app._status_cache.pop(i, None)
            app._report_times.pop(i, None)

    def beat(self, did, times):
        import unittest.mock as um
        for t in times:
            with um.patch.object(app.time, "time", return_value=t):
                app._store_status(did, {"online": True, "values": {}, "raw": {}})

    def test_learning_needs_a_regular_heartbeat(self):
        import random
        self.assertIsNone(app._periodic([0, 30], 60))
        self.assertIsNone(app._periodic([0, 10, 20], 30))                       # a door opened three times
        self.assertTrue(app._periodic([i * 30 + (i % 3) for i in range(25)], 800))   # 30 s, +-2 s
        self.assertTrue(app._periodic([t for i in range(25) for t in (i * 30, i * 30 + 0.3)], 800))  # two values at once
        self.assertFalse(app._periodic([i * 60 for i in range(6)] + [600 + i * 60 for i in range(6)], 1000))  # a gap
        rnd = random.Random(7)
        door, t = [], 0.0
        for _ in range(40):                                                    # a busy door: 40 events, 20 min
            t += rnd.uniform(2, 60)
            door.append(t)
        self.assertFalse(app._periodic(door, t + 1))

    def test_silence_takes_a_periodic_device_offline_and_its_next_report_back(self):
        t0 = app._STARTED + 1000
        self.beat("sl1", [t0 + i * 30 for i in range(25)])
        self.assertTrue(app._status_cache["sl1"]["periodic"])
        last = t0 + 24 * 30
        app._liveness_tick(last + app.DEVICE_SILENT_S - 5)
        self.assertTrue(app._status_cache["sl1"]["online"])
        app._liveness_tick(last + app.DEVICE_SILENT_S + 5)
        self.assertFalse(app._status_cache["sl1"]["online"])
        self.assertTrue(app._status_cache["sl1"]["offline_since"])
        self.beat("sl1", [last + 2000])
        self.assertTrue(app._status_cache["sl1"]["online"])
        self.assertNotIn("offline_since", app._status_cache["sl1"])

    def test_event_only_and_availability_devices_never_go_by_silence(self):
        t0 = app._STARTED + 1000
        self.beat("sl2", [t0, t0 + 10, t0 + 20])                               # events, not a heartbeat
        self.beat("sl3", [t0 + i * 30 for i in range(25)])                     # periodic, but has availability
        app._liveness_tick(t0 + 99999)
        self.assertTrue(app._status_cache["sl2"]["online"])
        self.assertTrue(app._status_cache["sl3"]["online"])

    def test_offline_after_s_is_an_opt_in_even_unlearned_and_irregular(self):
        t0 = app._STARTED + 1000
        self.beat("sl4", [t0, t0 + 5, t0 + 40])                               # never learned, irregular
        self.assertNotIn("periodic", app._status_cache["sl4"])
        app._liveness_tick(t0 + 40 + 50)
        self.assertTrue(app._status_cache["sl4"]["online"])
        app._liveness_tick(t0 + 40 + 70)
        self.assertFalse(app._status_cache["sl4"]["online"])

    def test_a_busy_door_never_greys(self):
        import random
        rnd = random.Random(3)
        t, times = app._STARTED + 1000, []
        for _ in range(40):
            t += rnd.uniform(2, 60)
            times.append(t)
        self.beat("sl2", times)
        self.assertNotIn("periodic", app._status_cache["sl2"])
        app._liveness_tick(t + 3 * app.DEVICE_SILENT_S)
        self.assertTrue(app._status_cache["sl2"]["online"])

    def test_a_learned_flag_clears_when_the_device_turns_irregular(self):
        t0 = app._STARTED + 1000
        self.beat("sl1", [t0 + i * 30 for i in range(25)])
        self.assertTrue(app._status_cache["sl1"]["periodic"])
        last = t0 + 24 * 30
        self.beat("sl1", [last + 3, last + 200])                                # it stops beating regularly
        self.assertNotIn("periodic", app._status_cache["sl1"])
        app._liveness_tick(last + 200 + 2 * app.DEVICE_SILENT_S)
        self.assertTrue(app._status_cache["sl1"]["online"])

    def test_the_silence_check_refuses_an_irregular_history_even_with_the_flag(self):
        t0 = app._STARTED + 1000
        times = [t0 + x for x in (0, 5, 70, 72, 140, 150, 260, 262, 300, 390, 395, 500)]
        self.beat("sl2", times)                                               # irregular, kept in memory
        app._status_cache["sl2"]["periodic"] = True                           # a flag from an older rule
        app._liveness_tick(times[-1] + 2 * app.DEVICE_SILENT_S)
        self.assertTrue(app._status_cache["sl2"]["online"])
        self.assertNotIn("periodic", app._status_cache["sl2"])

    def test_a_persisted_flag_lives_24_hours(self):
        import json
        import tempfile
        now = app.time.time()
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"sl1": {"online": True, "periodic": True, "periodic_since": now - 3600, "values": {}},
                   "sl2": {"online": True, "periodic": True, "periodic_since": now - 90000, "values": {}}}, f)
        f.close()
        keep = app.STATUS_CACHE_PATH
        app.STATUS_CACHE_PATH = app.Path(f.name)
        try:
            app._load_status_cache()
            self.assertTrue(app._status_cache["sl1"]["periodic"])
            self.assertNotIn("periodic", app._status_cache["sl2"])
        finally:
            app.STATUS_CACHE_PATH = keep

    def test_after_a_start_a_periodic_device_waits_for_its_report(self):
        app._status_cache["sl1"] = {"online": False, "waiting": "start", "periodic": True, "last_seen": 1, "values": {}}
        app._liveness_tick(app._STARTED + app.START_GRACE_S + 1)
        self.assertEqual(app._status_cache["sl1"]["waiting"], "start")          # not live from the cache
        app._liveness_tick(app._STARTED + app.DEVICE_SILENT_S + 1)
        self.assertFalse(app._status_cache["sl1"]["online"])
        self.assertNotIn("waiting", app._status_cache["sl1"])                   # silent since the start: offline

    def test_the_learned_flag_survives_a_store_that_does_not_carry_it(self):
        app._status_cache["sl2"] = {"online": True, "periodic": True, "values": {}}
        app._store_status("sl2", {"online": True, "values": {}, "raw": {}})
        self.assertTrue(app._status_cache["sl2"]["periodic"])


class DimmerCommandsBuildOnWhatWasSent(unittest.TestCase):
    def setUp(self):
        d = {"id": "pt1", "name": "P", "transport": "mqtt", "category": "light", "controls": [
            {"kind": "bright", "code": "brightness", "command_topic": "p/b", "state_topic": "p/bs", "min": 0, "max": 100},
            {"kind": "switch", "code": "state", "command_topic": "p/s", "state_topic": "p/ss"}]}
        app._mark_dimmers(d)
        d["_ctl"] = {c["code"]: c for c in d["controls"]}
        app.DEVICES["pt1"] = d
        self.pub, app._mqtt_client = app._mqtt_client, None
        app._status_cache["pt1"] = {"online": True, "values": {"brightness": 50, "state": True}, "raw": {}}

    def tearDown(self):
        app._mqtt_client = self.pub
        app.DEVICES.pop("pt1", None)
        app._status_cache.pop("pt1", None)
        for k in [k for k in app._cmd_sent if k[0] == "pt1"]:
            app._cmd_sent.pop(k, None)
            app._cmd_settle.pop(k, None)

    def test_two_quick_steps_add_up_and_the_report_is_the_base_again(self):
        code, v = app._bright_step("pt1", "brightness", "up")
        app._optimistic("pt1", code, v)
        self.assertEqual(app._status_cache["pt1"]["values"]["brightness"], 50)    # the cache waits
        self.assertEqual(app._bright_step("pt1", "brightness", "up"), ("brightness", v + 10))
        app._handle_mqtt_state("pt1", "ctl", "brightness", b"40")                # the device says 40
        import unittest.mock as um
        with um.patch.object(app.time, "time", return_value=app.time.time() + app.SETTLE_S + 0.1):
            self.assertEqual(app._bright_step("pt1", "brightness", "up"), ("brightness", 50))   # settled on 40

    def test_two_toggles_alternate(self):
        app._optimistic("pt1", "state", not app._value_now("pt1", "state", False))
        self.assertFalse(app._value_now("pt1", "state"))
        app._optimistic("pt1", "state", not app._value_now("pt1", "state", False))
        self.assertTrue(app._value_now("pt1", "state"))

    def test_the_sent_value_counts_only_for_a_while(self):
        import unittest.mock as um
        app._optimistic("pt1", "state", False)
        with um.patch.object(app.time, "time", return_value=app.time.time() + app.CONFIRM_WAIT_S + 1):
            self.assertTrue(app._value_now("pt1", "state"))                     # the report again

    def test_a_for_rule_owns_the_end_and_a_later_manual_on_gets_the_auto_off(self):
        armed = []
        keep = app._arm_inching
        app._arm_inching = lambda dev, code: armed.append((dev, code))
        try:
            app._mark_for_echo({"for": 30, "action": "on"}, "pt1", "state")
            app._inching_hook("pt1", "state", True)                           # the echo of the rule's ON
            self.assertEqual(armed, [])
            app._inching_hook("pt1", "state", True)                           # a manual ON later
            self.assertEqual(armed, [("pt1", "state")])
            app._mark_for_echo({"for": 30, "action": "on"}, "pt1", "state")
            app._inching_hook("pt1", "state", False)                          # OFF, then a manual ON
            app._inching_hook("pt1", "state", True)
            self.assertEqual(armed[-1], ("pt1", "state"))
            self.assertEqual(len(armed), 2)
            app._mark_for_echo({"for": 30, "action": "off"}, "pt1", "state")  # a revert to ON is not marked
            self.assertNotIn(("pt1", "state"), app._for_echo)
            app._bindings["ptfe"] = {"b": {"g": {"target": "pt1", "code": "state", "action": "on", "for": 30}}}
            app._mqtt_client = None                                            # the send fails
            with self.assertRaises(Exception):
                app._execute_binding("ptfe", "b", "g")
            self.assertNotIn(("pt1", "state"), app._for_echo)                  # no mark left behind
            app._bindings.pop("ptfe", None)
            app._for_echo[("pt1", "state")] = app.time.time() - 1             # an expired mark blocks nothing
            app._inching_hook("pt1", "state", True)
            self.assertEqual(len(armed), 3)
        finally:
            app._arm_inching = keep
            app._cancel_deferred("for|b|x|btn|single")
            app._for_echo.pop(("pt1", "state"), None)

    def test_each_relative_command_path_builds_on_what_was_sent(self):
        import asyncio
        got = []

        async def fake_control(dev_id, body):
            got.append(body.value)
            app._optimistic(dev_id, body.code, body.value)
            return {"ok": True}

        class Loop:
            pass
        keep = (app.control, app._MAIN_LOOP, app.asyncio.run_coroutine_threadsafe)
        app.control, app._MAIN_LOOP = fake_control, Loop()

        class Done:
            def __init__(self, coro):
                asyncio.run(coro)

            def add_done_callback(self, fn):
                pass
        app.asyncio.run_coroutine_threadsafe = lambda coro, loop: Done(coro)
        try:
            app.mqtt_command("pt1", "state", b"toggle")
            app.mqtt_command("pt1", "state", b"toggle")
            self.assertEqual(got, [False, True])                              # alternate, not False twice
        finally:
            app.control, app._MAIN_LOOP, app.asyncio.run_coroutine_threadsafe = keep
        sent = []

        class Cli:
            def is_connected(self):
                return True

            def publish(self, t, p=None, *a, **k):
                sent.append((t, p))

                class Info:
                    rc = 0
                return Info()
        app._mqtt_client = Cli()
        app._bindings["ptsrc"] = {"b": {"t": {"target": "pt1", "code": "state", "action": "toggle"},
                                        "u": {"target": "pt1", "code": "brightness", "action": "bright_up"}}}
        try:
            app._cmd_forget("pt1")
            app._execute_binding("ptsrc", "b", "t")
            app._execute_binding("ptsrc", "b", "t")
            self.assertEqual([p for t, p in sent if t == "p/s"], ["OFF", "ON"])
            app._execute_binding("ptsrc", "b", "u")
            app._execute_binding("ptsrc", "b", "u")
            self.assertEqual([p for t, p in sent if t == "p/b"], ["60", "70"])  # 50 -> 60 -> 70
        finally:
            app._bindings.pop("ptsrc", None)

    def test_a_step_on_a_gang_just_switched_off_switches_it_on(self):
        app._optimistic("pt1", "state", False)                               # sent, not reported yet
        self.assertEqual(app._bright_step("pt1", "brightness", "up"), ("state", True))

    def test_removing_a_device_drops_its_commands_and_the_tick_purges_old_ones(self):
        import json
        import tempfile
        app._optimistic("pt1", "state", False)
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump([{"id": "pt1", "name": "P", "controls": []}], f)
        f.close()
        keep = app.MQTT_DEVICES_PATH
        app.MQTT_DEVICES_PATH = app.Path(f.name)
        try:
            app.esphome_remove(app.MqttDeviceRemove(id="pt1"))
        finally:
            app.MQTT_DEVICES_PATH = keep
        self.assertFalse([k for k in app._cmd_sent if k[0] == "pt1"])
        self.setUp()
        app._optimistic("pt1", "state", False)
        app._liveness_tick(app.time.time() + app.CONFIRM_WAIT_S + 1)
        self.assertFalse([k for k in app._cmd_sent if k[0] == "pt1"])

    def test_a_lamp_pairs_its_only_switch_and_the_entry_stays_clean(self):
        entry = {"id": "lampq7", "name": "L", "category": "light", "controls": [
            {"kind": "switch", "code": "power", "command_topic": "l/p", "state_topic": "l/ps"},
            {"kind": "bright", "code": "brightness", "command_topic": "l/b", "state_topic": "l/bs"}]}
        try:
            d = app._register_mqtt_device(entry)
            self.assertEqual([c.get("confirm") for c in d["controls"]], [True, True])
            self.assertEqual([c.get("confirm") for c in entry["controls"]], [None, None])   # saved as written
        finally:
            app.DEVICES.pop("lampq7", None)
            app.BY_ID_NAME.pop("lampq7", None)
            app._status_cache.pop("lampq7", None)


class OfflineRefusedAndSkipped(unittest.TestCase):
    """D3 and D7 of docs/device-state.md: offline = offline_since; waiting is not offline."""

    def setUp(self):
        for i, tr in (("of1", "mqtt"), ("of2", "mqtt"), ("of3", "webos")):
            app.DEVICES[i] = {"id": i, "name": i.upper(), "transport": tr, "category": "switch", "controls": [
                {"kind": "switch", "code": "power", "command_topic": f"{i}/set", "state_topic": f"{i}/st"}]}
            app.DEVICES[i]["_ctl"] = {"power": app.DEVICES[i]["controls"][0]}
            app.BY_ID_NAME[i] = i.upper()
        self.sent = []
        test = self

        class Cli:
            def is_connected(self):
                return True

            def publish(self, t, p=None, *a, **k):
                test.sent.append((t, p))

                class Info:
                    rc = 0
                return Info()
        self.pub, app._mqtt_client = app._mqtt_client, Cli()
        app._status_cache["of1"] = {"online": False, "offline_since": app.time.time(), "values": {"power": True}, "raw": {}}
        app._status_cache["of2"] = {"online": False, "waiting": "start", "values": {"power": False}, "raw": {}}
        app._status_cache["of3"] = {"online": False, "offline_since": app.time.time(), "values": {"power": False}, "raw": {}}

    def tearDown(self):
        app._mqtt_client = self.pub
        for i in ("of1", "of2", "of3"):
            app.DEVICES.pop(i, None)
            app.BY_ID_NAME.pop(i, None)
            app._status_cache.pop(i, None)
        app._bindings.pop("ofsrc", None)
        app._events.pop("ofsrc", None)
        for t in ("for|x", "inch|of1/power", "sched|of1/power"):
            app._cancel_deferred(t)

    def test_a_command_to_an_offline_device_is_409_waiting_and_a_tv_are_not(self):
        import asyncio
        with self.assertRaises(app.HTTPException) as cm:
            asyncio.run(app.control("of1", app.ControlBody(code="power", value=False)))
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(self.sent, [])
        asyncio.run(app.control("of2", app.ControlBody(code="power", value=True)))        # waiting: it goes out
        self.assertIn(("of2/set", "ON"), self.sent)
        self.assertFalse(app._is_offline("of3"))                                         # a TV sleeps

    def test_a_rule_skips_an_offline_target_and_says_so_in_the_press_log(self):
        app._bindings["ofsrc"] = {"btn": {"single": {"target": "of1", "code": "power", "action": "off"}}}
        app._events["ofsrc"] = app.deque([{"code": "btn", "value": "single"}], maxlen=20)
        r = app._safe_execute_binding("ofsrc", "btn", "single")
        self.assertEqual(r["error"], "offline")
        self.assertEqual(self.sent, [])
        self.assertEqual(app._events["ofsrc"][-1]["fired"], "skipped: OF1 offline")

    def test_a_condition_on_a_device_that_is_not_live_skips_the_rule(self):
        app._bindings["ofsrc"] = {"btn": {"single": {"target": "of2", "code": "power", "action": "on",
                                                     "conditions": [{"device": "of2", "code": "power", "op": "off"}]}}}
        r = app._execute_binding("ofsrc", "btn", "single")
        self.assertEqual((r["error"], r["target"]), ("offline", "of2"))
        self.assertEqual(self.sent, [])

    def test_a_shot_due_while_offline_waits_for_its_device_then_runs(self):
        set_calls = []
        keep = app._set_switch
        app._set_switch = lambda *a, **k: set_calls.append(a)
        try:
            for tok in ("for|x", "inch|of1/power", "sched|of1/power"):
                app._schedule_deferred(tok, 3600, "of1", "power", False, "t")
                app._fire_deferred(tok)                                        # due now, the device away
                self.assertTrue(app._deferred_active(tok), tok)                # kept, retried
                self.assertEqual(set_calls, [])
            app._status_cache["of1"] = {"online": True, "values": {"power": True}, "raw": {}}   # back
            for tok in ("for|x", "inch|of1/power", "sched|of1/power"):
                app._fire_deferred(tok)
            self.assertEqual(len(set_calls), 3)
        finally:
            app._set_switch = keep

    def test_a_shot_waits_only_so_long(self):
        set_calls = []
        keep = app._set_switch
        app._set_switch = lambda *a, **k: set_calls.append(a)
        now = app.time.time()
        try:
            for tok, limit in (("inch|of1/power", 1800), ("for|x", 600), ("sched|of1/power", 600)):
                app._schedule_deferred(tok, 3600, "of1", "power", False, "t", due=now - limit - 1)
                app._fire_deferred(tok)
                self.assertFalse(app._deferred_active(tok), tok)
                app._schedule_deferred(tok, 3600, "of1", "power", False, "t", due=now - limit + 60)
                app._fire_deferred(tok)
                self.assertTrue(app._deferred_active(tok), tok)
                app._cancel_deferred(tok)
            self.assertEqual(set_calls, [])
            self.assertIn("schedules", {r["id"] for r in app._core_health()})  # the missed one shows
        finally:
            app._set_switch = keep
            app._missed_sched.clear()

    def test_a_schedule_due_while_offline_is_caught_up(self):
        import datetime as dt
        set_calls = []
        keep, keep_autos = app._set_switch, dict(app._autos)
        app._set_switch = lambda *a, **k: set_calls.append(a)
        app._autos.clear()
        app._autos["of1/power"] = {"schedule": [{"time": "07:00", "action": "off"}]}
        try:
            app._scheduler_tick(dt.datetime(2026, 1, 5, 7, 0))
            self.assertEqual(set_calls, [])
            self.assertTrue(app._deferred_active("sched|of1/power"))           # waits for the device
            app._status_cache["of1"] = {"online": True, "values": {"power": True}, "raw": {}}
            app._fire_deferred("sched|of1/power")
            self.assertEqual(set_calls, [("of1", "power", False)])
        finally:
            app._set_switch = keep
            app._autos.clear()
            app._autos.update(keep_autos)
            app._sched_fired.clear()

    def test_back_on_after_an_outage_gets_its_auto_off_again(self):
        armed = []
        keep, keep_autos = app._arm_inching, dict(app._autos)
        app._arm_inching = lambda dev, code: armed.append((dev, code))
        app._autos["of1/power"] = {"inching_min": 1, "schedule": []}
        try:
            app._store_status("of1", {"online": True, "values": {"power": True}, "raw": {}})
            self.assertEqual(armed, [("of1", "power")])
            app._store_status("of1", {"online": True, "values": {"power": True}, "raw": {}})
            self.assertEqual(armed, [("of1", "power")])                        # only on the return
        finally:
            app._arm_inching = keep
            app._autos.clear()
            app._autos.update(keep_autos)

    def test_health_lists_devices_offline_for_long(self):
        rows = {r["id"]: r for r in app._core_health()}
        self.assertNotIn("devices", rows)
        app._status_cache["of1"]["offline_since"] = app.time.time() - 700
        rows = {r["id"]: r for r in app._core_health()}
        self.assertEqual(rows["devices"]["state"], "warn")
        self.assertIn("OF1", str(rows["devices"]))


class LifeAfterReview(unittest.TestCase):
    """1afb94c review: the return wait, waiting is commandable, the tick, report=False."""
    ID = "lr1"

    def setUp(self):
        app.DEVICES[self.ID] = {"id": self.ID, "name": "R", "transport": "mqtt", "category": "switch",
                                "availability_topic": "lr/status", "controls": [
                                    {"kind": "switch", "code": "power", "state_topic": "lr/p", "command_topic": "lr/p/set"}]}
        app.DEVICES[self.ID]["_ctl"] = {"power": app.DEVICES[self.ID]["controls"][0]}
        self.cli = _Connected()
        self.pub, app._mqtt_client = app._mqtt_client, self.cli
        app._status_cache.pop(self.ID, None)

    def tearDown(self):
        app._mqtt_client = self.pub
        app.DEVICES.pop(self.ID, None)
        app._status_cache.pop(self.ID, None)

    def st(self):
        return app._status_cache[self.ID]

    def avail(self, word):
        app._handle_mqtt_state(self.ID, "avail", None, word.encode(), False)

    def test_a_second_online_word_does_not_end_the_return_wait(self):
        app._handle_mqtt_state(self.ID, "ctl", "power", b"ON", False)
        self.avail("offline")
        self.avail("online")
        self.avail("online")                                    # z2m, an ESPHome re-birth, a replay
        self.assertEqual((self.st()["online"], self.st()["waiting"]), (False, "return"))

    def test_an_event_only_device_ends_its_return_wait_like_the_start(self):
        app._handle_mqtt_state(self.ID, "ctl", "power", b"ON", False)
        self.avail("offline")
        self.avail("online")
        since = self.st()["waiting_since"]
        app._liveness_tick(since + app.START_GRACE_S - 1)
        self.assertEqual(self.st()["waiting"], "return")
        app._liveness_tick(since + app.START_GRACE_S + 1)
        self.assertTrue(self.st()["online"])
        self.assertNotIn("waiting", self.st())
        self.assertEqual(self.cli.sent[f"home/state/{self.ID}/online"], "true")    # the mirror follows

    def test_a_confirm_device_keeps_waiting_and_can_be_commanded(self):
        import asyncio
        app.DEVICES[self.ID]["controls"][0]["confirm"] = True
        app._handle_mqtt_state(self.ID, "ctl", "power", b"ON", False)
        self.avail("offline")
        self.avail("online")
        app._liveness_tick(self.st()["waiting_since"] + 999)
        self.assertEqual(self.st()["waiting"], "return")
        r = asyncio.run(app.control(self.ID, app.ControlBody(code="power", value=False)))
        self.assertTrue(r["ok"])

    def test_no_tick_while_the_broker_is_away(self):
        app._status_cache[self.ID] = {"online": False, "waiting": "start", "values": {}, "raw": {}}
        app._mqtt_client = None
        app._liveness_tick(app._STARTED + 9999)
        self.assertEqual(self.st()["waiting"], "start")

    def test_a_command_leaves_last_seen_and_waiting_alone(self):
        app._status_cache[self.ID] = {"online": True, "last_seen": 5.0, "values": {"power": False}, "raw": {}}
        app._optimistic(self.ID, "power", True)
        self.assertEqual(self.st()["last_seen"], 5.0)
        self.assertTrue(self.st()["values"]["power"])

    def test_offline_since_stays_the_first_one(self):
        self.avail("offline")
        first = self.st()["offline_since"]
        app.time.sleep(0.01)
        self.avail("offline")
        self.assertEqual(self.st()["offline_since"], first)

    def test_a_failed_poll_while_waiting_is_offline(self):
        app._status_cache[self.ID] = {"online": False, "waiting": "start", "values": {}, "raw": {}}
        app._store_status(self.ID, {"online": False, "error": "no route", "values": {}, "raw": {}})
        self.assertTrue(self.st()["offline_since"])
        self.assertNotIn("waiting", self.st())


class CommandsInFlight(unittest.TestCase):
    """One rule for the wait and the base of the next step: echoes clear their command and older ones,
    a value no command asked for settles after 1 s, everything else expires after CONFIRM_WAIT_S."""

    def setUp(self):
        d = {"id": "cf1", "name": "C", "transport": "mqtt", "category": "light", "controls": [
            {"kind": "bright", "code": "brightness", "command_topic": "c/b", "state_topic": "c/bs", "min": 0, "max": 255},
            {"kind": "switch", "code": "power", "command_topic": "c/p", "state_topic": "c/ps"}]}
        app._mark_dimmers(d)
        d["_ctl"] = {c["code"]: c for c in d["controls"]}
        app.DEVICES["cf1"] = d
        self.pub, app._mqtt_client = app._mqtt_client, None
        app._status_cache["cf1"] = {"online": True, "values": {"brightness": 100, "power": False}, "raw": {}}
        self.t = 1000.0

    def tearDown(self):
        app._mqtt_client = self.pub
        app.DEVICES.pop("cf1", None)
        app._status_cache.pop("cf1", None)
        for c in ("brightness", "power"):
            app._cmd_sent.pop(("cf1", c), None)
            app._cmd_settle.pop(("cf1", c), None)

    def at(self, dt):
        import unittest.mock as um
        self.t += dt
        return um.patch.object(app.time, "time", return_value=self.t)

    def send(self, v):
        with self.at(0):
            app._optimistic("cf1", "brightness", v)

    def report(self, v, dt=0.0):
        with self.at(dt):
            app._handle_mqtt_state("cf1", "ctl", "brightness", str(v).encode())

    def base(self, dt=0.0, way="", code="brightness"):
        with self.at(dt):
            return app._value_now("cf1", code, way=way)

    def test_a_command_sent_after_a_settle_began_survives_it(self):
        self.send(255)
        self.report(200, 0.2)                                  # answers none: settle until 1.2
        self.send_at(153, 1.1)                                 # a new absolute command
        self.assertEqual(self.base(0.25), 153)                 # 1.35: the settle ended, 153 stays

    def send_at(self, v, dt, code="brightness"):
        with self.at(dt):
            app._optimistic("cf1", code, v)

    def test_a_report_of_the_old_value_starts_the_settle_too(self):
        self.send_at(True, 0, "power")
        with self.at(0.3):
            app._handle_mqtt_state("cf1", "ctl", "power", b"OFF")   # refused: the old value again
        self.assertFalse(self.base(1.3, code="power"))          # settled on OFF: a toggle sends ON

    def test_another_channels_report_leaves_a_command_alone(self):
        self.send(200)
        with self.at(0.3):
            app._handle_mqtt_state("cf1", "ctl", "power", b"ON")
        self.assertEqual(self.base(1.5), 200)                   # no settle from the power report

    def test_while_settling_a_step_never_goes_against_the_report(self):
        self.send(255)
        self.report(200, 0.3)
        self.assertEqual(self.base(0.2, way="down"), 200)
        self.assertEqual(self.base(0, way="up"), 255)

    def test_a_command_sent_during_a_settle_is_the_base_as_it_is(self):
        self.send(255)
        self.report(200, 0.3)
        self.send_at(153, 0.2)                                 # an explicit 60 % after the report
        self.assertEqual(self.base(0.1, way="up"), 153)        # not max(153, 200)
        self.assertEqual(self.base(0, way="down"), 153)

    def test_a_device_frame_answers_only_the_channels_that_changed(self):
        app._cmd_settle.pop(("cf1", "brightness"), None)
        self.send(200)
        with self.at(0.3):
            app._store_status("cf1", {"online": True, "values": {"brightness": 100, "power": True}, "raw": {}},
                              codes=None)                     # unchanged brightness: not an answer
        self.assertNotIn(("cf1", "brightness"), app._cmd_settle)
        self.assertEqual(self.base(0.1), 200)

    def test_a_step_and_a_rule_step_pass_their_direction(self):
        app._status_cache["cf1"]["values"]["power"] = True        # an off gang would not step down
        self.send(255)
        self.report(200, 0.3)                                  # settling on 200 (78 %)
        with self.at(0.1):
            self.assertEqual(app._bright_step("cf1", "brightness", "down"), ("brightness", 70))
        sent = []

        class Cli:
            def is_connected(self):
                return True

            def publish(self, t, p=None, *a, **k):
                sent.append((t, p))

                class Info:
                    rc = 0
                return Info()
        keep = app._mqtt_client
        app._mqtt_client = Cli()
        app._bindings["cfsrc"] = {"b": {"d": {"target": "cf1", "code": "brightness", "action": "bright_down"}}}
        try:
            with self.at(0):
                app._execute_binding("cfsrc", "b", "d")
            self.assertEqual([p for t, p in sent if t == "c/b"], ["173"])    # 78 % - 10 = 68 % of 255
        finally:
            app._mqtt_client = keep
            app._bindings.pop("cfsrc", None)

    def test_a_device_level_json_frame_answers_only_what_changed(self):
        self.send(200)
        with self.at(0.3):
            app._handle_mqtt_state("cf1", "dev", None, b'{"brightness": 100, "power": "ON"}')
        self.assertNotIn(("cf1", "brightness"), app._cmd_settle)          # 100 again: no answer, no settle
        self.assertEqual(self.base(0.1), 200)

    def test_the_newest_eight_are_kept(self):
        for v in range(12):
            self.send_at(v * 10, 0.01)
        self.assertEqual([c[0] for c in app._cmd_sent[("cf1", "brightness")]], [v * 10 for v in range(4, 12)])
        self.assertEqual(app.CMD_KEEP, 8)

    def test_an_emptied_list_drops_its_settle(self):
        self.send(255)
        self.report(200, 0.3)                                  # a settle
        self.base(app.CONFIRM_WAIT_S + 1)                      # the command expired: list empty
        self.assertNotIn(("cf1", "brightness"), app._cmd_settle)

    def test_an_echo_ends_the_settle(self):
        self.send(255)
        self.report(200, 0.3)                                  # settle on 200
        self.send_at(230, 0.2)
        self.send_at(245, 0.05)
        self.report(230, 0.05)                                 # 230's echo: the settle is over, 245 waits
        self.assertEqual(self.base(0.1, way="down"), 245)      # not held at the old 200

    def test_the_newest_matching_command_and_all_older_ones_clear(self):
        for v in (100, 102, 104):
            self.send_at(v, 0.1)
        self.report(104, 0.1)
        self.assertNotIn(("cf1", "brightness"), app._cmd_sent)

    def test_a_switch_echo_must_be_equal(self):
        self.send_at(True, 0, "power")
        with self.at(0.1):
            app._handle_mqtt_state("cf1", "ctl", "power", b"OFF")
        self.assertIn(("cf1", "power"), app._cmd_sent)          # not an echo: it settles

    def test_at_most_eight_commands_are_kept(self):
        for v in range(20):
            self.send_at(v * 10, 0.01)
        self.assertEqual(len(app._cmd_sent[("cf1", "brightness")]), app.CMD_KEEP)

    def test_two_percent_and_not_more(self):
        self.send(200)
        self.report(206, 0.1)                                  # 6 off on 0..255: not an echo
        self.assertIn(("cf1", "brightness"), app._cmd_sent)

    def test_every_screen_gets_the_newest_wait_at_once(self):
        from fastapi.testclient import TestClient
        v0 = app._ws_version
        self.send(150)
        self.send_at(200, 0.1)
        self.assertGreater(app._ws_version, v0)                                 # a frame goes out now
        with self.at(0.1):
            self.assertEqual(app._ws_payload()["cf1"]["cmd_wait"], {"brightness": 200})   # the newest
        keep = app._token_ok
        app._token_ok = lambda *a, **k: True
        try:
            with self.at(0.1):
                r = TestClient(app.app).get("/api/devices/cf1/status")
            self.assertEqual(r.json()["cmd_wait"], {"brightness": 200})
        finally:
            app._token_ok = keep
        self.assertNotIn("cmd_wait", app._status_cache["cf1"])
        v1 = app._ws_version
        self.report(200, 0.1)
        self.assertGreater(app._ws_version, v1)

    def test_the_status_says_which_channel_waits(self):
        self.assertNotIn("cmd_wait", app._status_out("cf1"))
        self.send(200)
        with self.at(0.1):
            self.assertEqual(app._status_out("cf1")["cmd_wait"], {"brightness": 200})
        self.report(200, 0.1)
        self.assertNotIn("cmd_wait", app._status_out("cf1"))
        self.assertNotIn("cmd_wait", app._status_cache["cf1"])                  # never stored

    def test_a_command_counts_less_than_five_seconds(self):
        self.send(200)
        self.assertEqual(self.base(app.CONFIRM_WAIT_S), 100)

    def test_the_first_echo_after_the_second_press_keeps_the_second(self):
        self.send(130)
        with self.at(0.2):
            app._optimistic("cf1", "brightness", 160)
        self.report(130, 0.1)                                  # the first command's echo, late
        self.assertEqual(self.base(), 160)
        self.report(160, 0.1)
        self.assertEqual(self.base(), 160)
        self.assertNotIn(("cf1", "brightness"), app._cmd_sent)

    def test_a_clamp_settles_on_the_device_answer(self):
        self.send(255)
        self.report(200, 0.1)
        self.assertEqual(self.base(0.5), 255)                  # still settling
        self.assertEqual(self.base(0.6), 200)                  # 1 s without another report

    def test_a_ramp_ends_on_its_target_without_an_early_clear(self):
        self.send(250)
        for v in (30, 90, 160):
            self.report(v, 0.5)                                # every report restarts the settle
            self.assertEqual(self.base(), 250)
        self.report(250, 0.5)
        self.assertEqual(self.base(), 250)
        self.assertNotIn(("cf1", "brightness"), app._cmd_sent)

    def test_an_echo_within_two_percent_is_the_answer(self):
        self.send(200)
        self.report(197, 0.1)                                  # rounding in the device: 2 % of 0..255
        self.assertEqual(self.base(), 197)
        self.assertNotIn(("cf1", "brightness"), app._cmd_sent)

    def test_no_report_expires(self):
        self.send(200)
        self.assertEqual(self.base(app.CONFIRM_WAIT_S - 0.1), 200)
        self.assertEqual(self.base(0.2), 100)

    def test_a_change_from_elsewhere_during_the_wait_settles_on_it(self):
        self.send(200)
        self.report(10, 0.3)                                   # someone dimmed it on the wall
        self.assertEqual(self.base(1.1), 10)

    def test_a_rename_drops_the_commands_of_the_old_name(self):
        self.send(200)
        save, app._save_settings = app._save_settings, lambda: None
        try:
            app.migrate_device_key("cf1", "cf2")
        finally:
            app._save_settings = save
            app._status_cache.pop("cf2", None)
            app._status_cache["cf1"] = {"online": True, "values": {"brightness": 100}, "raw": {}}
        self.assertNotIn(("cf1", "brightness"), app._cmd_sent)


class AskOnReturn(unittest.TestCase):
    """A device waiting ASK_AFTER_S is asked once per wait (docs/device-state.md, D4)."""

    def setUp(self):
        self.cli = _Connected()
        self.pub, app._mqtt_client = app._mqtt_client, self.cli
        for i, extra in (("ak1", {"state_request": {"topic": "ak1/get", "payload": "state"}}), ("ak2", {}),
                         ("ak3", {"state_request": {"topic": "home/state/x", "payload": "1"}})):
            app.DEVICES[i] = {"id": i, "name": i, "transport": "mqtt", "category": "switch", "_ctl": {},
                              "controls": [], **extra}
            app._status_cache[i] = {"online": False, "waiting": "return", "waiting_since": 500.0, "values": {}}
            app._asked_wait.pop(i, None)

    def tearDown(self):
        app._mqtt_client = self.pub
        for i in ("ak1", "ak2", "ak3"):
            app.DEVICES.pop(i, None)
            app._status_cache.pop(i, None)
            app._asked_wait.pop(i, None)

    def test_asked_once_per_wait_only_with_a_state_request(self):
        app._liveness_tick(500.0 + app.ASK_AFTER_S - 1)
        self.assertNotIn("ak1/get", self.cli.sent)
        app._liveness_tick(500.0 + app.ASK_AFTER_S + 1)
        self.assertEqual(self.cli.sent.get("ak1/get"), "state")
        self.assertNotIn("home/state/x", self.cli.sent)                       # never into our own bus
        self.cli.sent.clear()
        app._liveness_tick(500.0 + app.ASK_AFTER_S + 2)
        self.assertEqual(self.cli.sent, {})                                     # once a wait
        app._status_cache["ak1"]["waiting_since"] = 900.0                     # a new wait: asked again
        app._liveness_tick(900.0 + app.ASK_AFTER_S + 1)
        self.assertIn("ak1/get", self.cli.sent)

    def test_a_state_request_is_held_to_the_discovery_rules_and_warned_once(self):
        bad = ["$CONTROL/dynamic-security/v1", "$SYS/x", "zigbee2mqtt/bridge/request/restart", "home/other/x",
               "home/", "a\nb", "x" * 300, "a/+/b", "a/#"]
        claimed = "zigbee2mqtt" not in app.MQTT_NAMESPACES
        app.MQTT_NAMESPACES.add("zigbee2mqtt")                                  # as the zigbee plug-in does
        self.addCleanup(lambda: claimed and app.MQTT_NAMESPACES.discard("zigbee2mqtt"))
        for t in bad:
            app.DEVICES["ak2"]["state_request"] = {"topic": t, "payload": "1"}
            app._bad_request_warned.discard("ak2")
            with self.assertLogs(app.log, "WARNING"):
                self.assertIsNone(app._state_request(app.DEVICES["ak2"]), t)
        with self.assertLogs(app.log, "WARNING") as cm:
            app._state_request(app.DEVICES["ak2"])
            app._state_request(app.DEVICES["ak2"])
            app.log.warning("end")
        self.assertEqual(len(cm.output), 1)                                    # once a device
        app.DEVICES["ak2"]["state_request"] = {"topic": "node/ak2/get", "payload": {"state": ""}}
        self.assertEqual(app._state_request(app.DEVICES["ak2"]), ("node/ak2/get", '{"state": ""}'))
        app.DEVICES["ak2"]["state_request"] = {"topic": "node/ak2/get", "payload": 5}
        app._bad_request_warned.discard("ak2")
        with self.assertLogs(app.log, "WARNING"):
            self.assertIsNone(app._state_request(app.DEVICES["ak2"]))

    def test_at_most_ten_asks_a_tick_and_a_start_wait_counts_from_the_connect(self):
        ids = [f"akm{i}" for i in range(15)]
        try:
            for i in ids:
                app.DEVICES[i] = {"id": i, "name": i, "transport": "mqtt", "_ctl": {}, "controls": [],
                                  "state_request": {"topic": f"n/{i}/get", "payload": ""}}
                app._status_cache[i] = {"online": False, "waiting": "start", "values": {}}
            app._liveness_tick(app._STARTED + app.ASK_AFTER_S - 1)
            self.assertFalse([t for t in self.cli.sent if t.startswith("n/akm")])  # a start wait: from the connect
            app._liveness_tick(app._STARTED + app.ASK_AFTER_S + 1)
            self.assertEqual(len([t for t in self.cli.sent if t.startswith("n/akm")]), app.ASK_PER_TICK)
            app._liveness_tick(app._STARTED + app.ASK_AFTER_S + 2)
            self.assertEqual(len([t for t in self.cli.sent if t.startswith("n/akm")]), 15)
        finally:
            for i in ids:
                app.DEVICES.pop(i, None)
                app._status_cache.pop(i, None)
                app._asked_wait.pop(i, None)

    def test_the_cap_counts_only_real_asks_and_payload_shapes(self):
        ids = [f"akn{i}" for i in range(12)] + [f"aky{i}" for i in range(3)]
        try:
            for i in ids:
                req = {"state_request": {"topic": f"n/{i}/get"}} if i.startswith("aky") else {}
                app.DEVICES[i] = {"id": i, "name": i, "transport": "mqtt", "_ctl": {}, "controls": [], **req}
                app._status_cache[i] = {"online": False, "waiting": "start", "values": {}}
            app._liveness_tick(app._STARTED + app.ASK_AFTER_S + 1)          # 12 that cannot be asked first
            self.assertEqual(sorted(t for t in self.cli.sent if t.startswith("n/aky")),
                             [f"n/aky{i}/get" for i in range(3)])
            self.assertEqual(self.cli.sent["n/aky0/get"], "")                 # no payload key: empty
            for i in ids:
                app._status_cache.pop(i, None)
            app._liveness_tick(app._STARTED + app.ASK_AFTER_S + 2)
            self.assertFalse([d for d in app._asked_wait if d in ids])        # waits over: purged
        finally:
            for i in ids:
                app.DEVICES.pop(i, None)
                app._status_cache.pop(i, None)
                app._asked_wait.pop(i, None)
        dev = {"id": "akz", "state_request": {"topic": "n/akz/get", "payload": ["a", 1]}}
        self.assertEqual(app._state_request(dev), ("n/akz/get", '["a", 1]'))
        app._bad_request_warned.discard("akq")
        with self.assertLogs(app.log, "WARNING") as cm:
            self.assertIsNone(app._state_request({"id": "akq", "state_request": "n/akq/get"}))
        self.assertIn("not an object", cm.output[0])

    def test_a_transport_asks_its_own_way(self):
        asked = []
        app.DEVICES["ak2"]["transport"] = "t_ask"
        app.STATE_ASK["t_ask"] = lambda did: asked.append(did) or True
        try:
            app._liveness_tick(500.0 + app.ASK_AFTER_S + 1)
            self.assertEqual(asked, ["ak2"])
        finally:
            app.STATE_ASK.pop("t_ask", None)


class OfflineEveryKind(unittest.TestCase):
    """d42c970 review: every channel kind is refused, the press log names why, a flood is one line."""

    def setUp(self):
        app.DEVICES["ok1"] = {"id": "ok1", "name": "Col", "transport": "mqtt", "category": "light", "controls": [
            {"kind": "color", "code": "rgb", "channels": {"r": "c/r", "g": "c/g", "b": "c/b"}},
            {"kind": "t_press", "code": "btn"}]}
        app.DEVICES["ok1"]["_ctl"] = {c["code"]: c for c in app.DEVICES["ok1"]["controls"]}
        app.CONTROL_KINDS["t_press"] = lambda cfg, ctl, val: {"pressed": True}
        app.BY_ID_NAME["ok1"] = "Col"
        app._status_cache["ok1"] = {"online": False, "offline_since": app.time.time(), "values": {}, "raw": {}}
        self.pub, app._mqtt_client = app._mqtt_client, _Connected()

    def tearDown(self):
        app._mqtt_client = self.pub
        app.DEVICES.pop("ok1", None)
        app.CONTROL_KINDS.pop("t_press", None)
        app.BY_ID_NAME.pop("ok1", None)
        app._status_cache.pop("ok1", None)

    def test_a_colour_and_a_press_are_refused_too(self):
        import asyncio
        for code, val in (("rgb", "#ff0000"), ("btn", True)):
            with self.assertRaises(app.HTTPException) as cm:
                asyncio.run(app.control("ok1", app.ControlBody(code=code, value=val)))
            self.assertEqual(cm.exception.status_code, 409, code)
        self.assertEqual(app._mqtt_client.sent, {})

    def test_the_press_log_says_waiting_or_gone(self):
        self.assertEqual(app._not_live_word("ok1"), "offline")
        app._status_cache["ok1"] = {"online": False, "waiting": "start", "values": {}}
        self.assertEqual(app._not_live_word("ok1"), "waiting")
        self.assertEqual(app._not_live_word("no_such_dev"), "gone")

    def test_a_flood_of_refused_commands_is_one_line_per_10_s(self):
        class F:
            def result(self):
                raise app.HTTPException(409, "Col is offline")
        app._cmd_refused.pop("home/cmd/ok1/rgb", None)
        with self.assertLogs(app.log, "WARNING") as cm:
            for _ in range(50):
                app._log_cmd_result(F(), "home/cmd/ok1/rgb", "x")
        self.assertEqual(len(cm.output), 1)

    def test_health_counts_after_10_min_and_never_lists_a_tv(self):
        now = app.time.time()
        app._status_cache["ok1"]["offline_since"] = now - 599
        self.assertNotIn("devices", {r["id"] for r in app._core_health()})
        app._status_cache["ok1"]["offline_since"] = now - 601
        rows = {r["id"]: r for r in app._core_health()}
        self.assertIn("Col", str(rows["devices"]["args"]))
        app.DEVICES["ok1"]["transport"] = "webos"
        self.assertNotIn("devices", {r["id"] for r in app._core_health()})


class LifeAfterReview2(unittest.TestCase):
    """771a861 review: the reconnect, a confirm device after a start, the kept times."""
    ID = "lq1"

    def setUp(self):
        app.DEVICES[self.ID] = {"id": self.ID, "name": "Q", "transport": "mqtt", "category": "light", "controls": [
            {"kind": "switch", "code": "power", "confirm": True, "state_topic": "lq/p", "command_topic": "lq/p/set"}]}
        app.DEVICES[self.ID]["_ctl"] = {"power": app.DEVICES[self.ID]["controls"][0]}
        self.cli = _Connected()
        self.pub, app._mqtt_client = app._mqtt_client, self.cli
        self.started = app._STARTED

    def tearDown(self):
        app._mqtt_client, app._STARTED = self.pub, self.started
        app.DEVICES.pop(self.ID, None)
        app._status_cache.pop(self.ID, None)

    def st(self):
        return app._status_cache[self.ID]

    def test_the_return_grace_counts_from_the_reconnect(self):
        app.DEVICES[self.ID]["controls"][0].pop("confirm")
        app._STARTED = 1000.0                                           # the broker came back at 1000
        app._status_cache[self.ID] = {"online": False, "waiting": "return", "waiting_since": 900.0, "values": {}}
        app._liveness_tick(1010.0)
        self.assertEqual(self.st()["waiting"], "return")
        app._liveness_tick(1000.0 + app.START_GRACE_S + 1)
        self.assertTrue(self.st()["online"])

    def test_a_connect_restarts_the_count(self):
        class Cli(_Connected):
            def subscribe(self, *a, **k):
                pass
        app._STARTED = 0.0
        app._mqtt_on_connect(Cli(), None, None, 0)
        self.assertGreater(app._STARTED, 0.0)

    def test_a_confirm_device_without_availability_is_live_after_a_start(self):
        app._status_cache[self.ID] = {"online": False, "waiting": "start", "values": {"power": True}}
        app._liveness_tick(app._STARTED + app.START_GRACE_S + 1)
        self.assertTrue(self.st()["online"])

    def test_a_confirm_device_with_availability_after_a_start_follows_the_grace(self):
        app.DEVICES[self.ID]["availability_topic"] = "lq/status"           # but nothing retained for it
        app._status_cache[self.ID] = {"online": False, "waiting": "start", "values": {"power": True}}
        app._liveness_tick(app._STARTED + app.START_GRACE_S + 1)
        self.assertTrue(self.st()["online"])

    def test_a_confirm_device_with_availability_back_from_an_outage_waits(self):
        app.DEVICES[self.ID]["availability_topic"] = "lq/status"
        app._status_cache[self.ID] = {"online": False, "waiting": "return", "waiting_since": app._STARTED,
                                      "values": {"power": True}}
        app._liveness_tick(app._STARTED + 999)
        self.assertEqual(self.st()["waiting"], "return")

    def test_the_times_are_kept_where_they_belong(self):
        app.DEVICES[self.ID]["availability_topic"] = "lq/status"
        app._handle_mqtt_state(self.ID, "ctl", "power", b"ON", False)
        seen = self.st()["last_seen"]
        app._handle_mqtt_state(self.ID, "avail", None, b"offline", False)
        app._handle_mqtt_state(self.ID, "avail", None, b"online", False)
        since = self.st()["waiting_since"]
        app._handle_mqtt_state(self.ID, "avail", None, b"online", False)   # again: the same wait
        self.assertEqual(self.st()["waiting_since"], since)
        app._handle_mqtt_state(self.ID, "ctl", "power", b"ON", True)       # a retained frame while it waits
        self.assertEqual(self.st()["last_seen"], seen)
        app._handle_mqtt_state(self.ID, "ctl", "power", b"OFF", False)
        self.assertNotIn("waiting_since", self.st())

    def test_a_start_wait_has_no_waiting_since(self):
        import json
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({self.ID: {"online": False, "waiting": "return", "waiting_since": 5.0, "values": {}}}, f)
        f.close()
        keep = app.STATUS_CACHE_PATH
        app.STATUS_CACHE_PATH = app.Path(f.name)
        try:
            app._load_status_cache()
        finally:
            app.STATUS_CACHE_PATH = keep
        self.assertEqual(self.st()["waiting"], "start")
        self.assertNotIn("waiting_since", self.st())


class ReviewF846264(unittest.TestCase):
    def tearDown(self):
        for k in ("rv1", "rv2"):
            app.DEVICES.pop(k, None)
            app._status_cache.pop(k, None)
            app.BY_ID_NAME.pop(k, None)
        app._bindings.pop("rvsrc", None)
        app._events.pop("rvsrc", None)

    def test_the_press_log_says_waiting(self):
        app.DEVICES["rv1"] = {"id": "rv1", "name": "Cond", "controls": [], "_ctl": {}}
        app.BY_ID_NAME["rv1"] = "Cond"
        app._status_cache["rv1"] = {"online": False, "waiting": "start", "values": {"power": False}}
        app._bindings["rvsrc"] = {"b": {"g": {"target": "rv1", "code": "power", "action": "on",
                                              "conditions": [{"device": "rv1", "code": "power", "op": "off"}]}}}
        app._events["rvsrc"] = app.deque([{"code": "b", "value": "g"}], maxlen=20)
        app._safe_execute_binding("rvsrc", "b", "g")
        self.assertEqual(app._events["rvsrc"][-1]["fired"], "skipped: Cond waiting")
        self.assertEqual(app._not_live_word("rv2"), "gone")
        app.DEVICES["rv2"] = {"id": "rv2", "controls": [], "_ctl": {}}
        self.assertEqual(app._not_live_word("rv2"), "waiting")                # registered, no status yet

    def test_the_flood_line_says_how_many_more(self):
        class F:
            def result(self):
                raise app.HTTPException(409, "x is offline")
        app._cmd_refused.pop("home/cmd/rv1/p", None)
        for _ in range(5):
            app._log_cmd_result(F(), "home/cmd/rv1/p", "1")
        app._cmd_refused["home/cmd/rv1/p"][0] -= 11
        with self.assertLogs(app.log, "WARNING") as cm:
            app._log_cmd_result(F(), "home/cmd/rv1/p", "1")
        self.assertIn("(and 4 more in 10 s)", cm.output[0])

    def test_health_names_eight_and_counts_the_rest(self):
        ids = [f"hq{i:02d}" for i in range(12)]
        try:
            for i in ids:
                app.DEVICES[i] = {"id": i, "name": i, "transport": "mqtt", "controls": [], "_ctl": {}}
                app._status_cache[i] = {"online": False, "offline_since": app.time.time() - 700, "values": {}}
            row = {r["id"]: r for r in app._core_health()}["devices"]
            self.assertTrue(str(row["args"][1]).endswith("+4"))
        finally:
            for i in ids:
                app.DEVICES.pop(i, None)
                app._status_cache.pop(i, None)

    def test_forget_and_the_tick_drop_every_trace(self):
        app._cmd_sent[("rv1", "p")] = [(True, app.time.time())]
        app._cmd_settle[("rv1", "p")] = (app.time.time() + 1, app.time.time(), False)
        app._for_echo[("rv1", "p")] = app.time.time() + 5
        app._for_echo[("rv2", "p")] = app.time.time() + 5
        app._cmd_sent[("rv2", "p")] = [(True, app.time.time())]
        app._cmd_forget("rv1")
        self.assertFalse([k for k in list(app._cmd_sent) + list(app._cmd_settle) + list(app._for_echo) if k[0] == "rv1"])
        self.assertIn(("rv2", "p"), app._cmd_sent)                              # another device's stay
        app._cmd_forget(now=app.time.time() + 1)
        self.assertIn(("rv2", "p"), app._cmd_sent)                              # live ones stay
        app._cmd_forget(now=app.time.time() + 6)
        self.assertNotIn(("rv2", "p"), app._cmd_sent)
        self.assertNotIn(("rv2", "p"), app._for_echo)


class TimersAfterReview(unittest.TestCase):
    """ce55a04 review: waits cost no disk writes, a start waits too, the rearm branches, the missed list."""

    def setUp(self):
        app.DEVICES["tw1"] = {"id": "tw1", "name": "T", "transport": "mqtt", "controls": [
            {"kind": "switch", "code": "power", "command_topic": "tw/set"}]}
        app.DEVICES["tw1"]["_ctl"] = {"power": app.DEVICES["tw1"]["controls"][0]}
        app._status_cache["tw1"] = {"online": False, "offline_since": app.time.time(), "values": {"power": True}}
        self.sets = []
        self.keep = (app._set_switch, app._arm_inching, dict(app._autos))
        app._set_switch = lambda *a, **k: self.sets.append(a)

    def tearDown(self):
        app._set_switch, app._arm_inching = self.keep[0], self.keep[1]
        app._autos.clear()
        app._autos.update(self.keep[2])
        for t in [t for t in list(app._pending) if "tw1" in t or t.startswith("for|tw")]:
            app._cancel_deferred(t)
        app.DEVICES.pop("tw1", None)
        app._status_cache.pop("tw1", None)
        app._missed_sched.clear()
        app._sched_fired.clear()

    def test_a_waiting_shot_writes_nothing_and_keeps_its_time(self):
        saves = []
        keep = app._pending_save
        try:
            app._schedule_deferred("inch|tw1/power", 3600, "tw1", "power", False, "t")
            run_at = app._pending["inch|tw1/power"]["run_at"]
            app._pending_save = lambda: saves.append(1)
            for _ in range(4):
                app._fire_deferred("inch|tw1/power")
            self.assertEqual(saves, [])                                           # no disk while waiting
            self.assertEqual(app._pending["inch|tw1/power"]["run_at"], run_at)    # the first time kept
        finally:
            app._pending_save = keep

    def test_two_retries_then_the_limit_still_counts_from_the_first_time(self):
        now = app.time.time()
        app._schedule_deferred("for|tw1x", 3600, "tw1", "power", False, "for", run_at=now - 590)
        app._fire_deferred("for|tw1x")
        app._fire_deferred("for|tw1x")
        self.assertTrue(app._deferred_active("for|tw1x"))
        import unittest.mock as um
        with um.patch.object(app.time, "time", return_value=now + 20):
            app._fire_deferred("for|tw1x")
        self.assertFalse(app._deferred_active("for|tw1x"))
        self.assertEqual(self.sets, [])

    def test_a_shot_at_a_start_waits_for_the_device_to_be_known(self):
        app._status_cache["tw1"] = {"online": False, "waiting": "start", "values": {"power": True}}
        app._schedule_deferred("inch|tw1/power", 3600, "tw1", "power", False, "t")
        app._fire_deferred("inch|tw1/power", app._REPLAY) if "inch|tw1/power" not in app._pending_handles else \
            app._fire_deferred("inch|tw1/power")
        self.assertEqual(self.sets, [])
        app._status_cache["tw1"] = {"online": True, "values": {"power": True}}
        app._fire_deferred("inch|tw1/power")
        self.assertEqual(self.sets, [("tw1", "power", False)])

    def test_the_return_rearms_only_a_steady_on_with_a_window_and_nothing_waiting(self):
        armed = []
        app._arm_inching = lambda d, c: armed.append(c)
        app._autos["tw1/power"] = {"inching_min": 1, "schedule": []}
        st = {"online": True, "values": {"power": True}}
        app._rearm_after_outage("tw1", st, {"power": False})                     # an edge: the hook arms it
        self.assertEqual(armed, [])
        app._rearm_after_outage("tw1", {"values": {"power": False}}, {"power": False})
        self.assertEqual(armed, [])
        app._schedule_deferred("inch|tw1/power", 3600, "tw1", "power", False, "t")
        app._rearm_after_outage("tw1", st, {"power": True})                      # an auto-off still waits
        self.assertEqual(armed, [])
        app._cancel_deferred("inch|tw1/power")
        app._schedule_deferred("for|tw1y", 3600, "tw1", "power", False, "for")
        app._rearm_after_outage("tw1", st, {"power": True})                      # a `for` owns it
        self.assertEqual(armed, [])
        app._cancel_deferred("for|tw1y")
        app._autos["tw1/power"] = {"inching_min": 0, "schedule": []}
        app._rearm_after_outage("tw1", st, {"power": True})                      # no window
        self.assertEqual(armed, [])
        app._autos["tw1/power"] = {"inching_min": 1, "schedule": []}
        app._rearm_after_outage("tw1", st, {"power": True})
        self.assertEqual(armed, ["power"])

    def test_the_missed_list_keeps_a_day_survives_a_restart_and_names_the_channel(self):
        import json
        import tempfile
        keep = app.MISSED_PATH
        app.MISSED_PATH = app.Path(tempfile.mkdtemp()) / "missed.json"
        try:
            app._sched_missed("tw1", "power", app.time.time() - 90000)           # a day old: pruned
            app._sched_missed("tw1", "power", app.time.time() - 60)
            self.assertEqual(len(json.loads(app.MISSED_PATH.read_text())), 1)
            app._missed_sched.clear()
            app._load_missed()
            self.assertEqual(len(app._missed_sched), 1)
            row = {r["id"]: r for r in app._core_health()}["schedules"]
            self.assertIn(".power", row["args"][1])
        finally:
            app.MISSED_PATH = keep

    def test_a_catch_up_from_another_day_is_missed_not_run(self):
        app._status_cache["tw1"] = {"online": True, "values": {"power": True}}
        yesterday = app.time.time() - 86400
        app._schedule_deferred("sched|tw1/power", 3600, "tw1", "power", True, "schedule", due=yesterday + 60)
        keep = app.MISSED_PATH
        import tempfile
        app.MISSED_PATH = app.Path(tempfile.mkdtemp()) / "m.json"
        try:
            app._OFFLINE_KEEP_S["sched"], old = 10 ** 9, app._OFFLINE_KEEP_S["sched"]
            app._fire_deferred("sched|tw1/power")
            self.assertEqual(self.sets, [])
            self.assertEqual(len(app._missed_sched), 1)
        finally:
            app._OFFLINE_KEEP_S["sched"] = old
            app.MISSED_PATH = keep

    def test_a_second_entry_replacing_a_waiting_catch_up_marks_it_missed(self):
        import datetime as dt
        import tempfile
        keep = app.MISSED_PATH
        app.MISSED_PATH = app.Path(tempfile.mkdtemp()) / "m.json"
        app._autos.clear()
        app._autos["tw1/power"] = {"schedule": [{"time": "07:00", "action": "on"}, {"time": "07:05", "action": "off"}]}
        try:
            app._scheduler_tick(dt.datetime(2026, 1, 5, 7, 0))
            app._scheduler_tick(dt.datetime(2026, 1, 5, 7, 5), dt.datetime(2026, 1, 5, 7, 4))
            self.assertEqual(len(app._missed_sched), 1)                          # the 07:00 one
            self.assertEqual(app._pending["sched|tw1/power"]["value"], False)    # the 07:05 one waits
        finally:
            app.MISSED_PATH = keep

    def test_a_timers_own_shot_does_not_void_a_waiting_schedule(self):
        app._status_cache["tw1"] = {"online": True, "values": {"power": True}}
        app._set_switch = self.keep[0]
        sent = []

        class Cli:
            def is_connected(self):
                return True

            def publish(self, t, p=None, *a, **k):
                sent.append(p)

                class Info:
                    rc = 0
                return Info()
        keep = app._mqtt_client
        app._mqtt_client = Cli()
        try:
            app._schedule_deferred("sched|tw1/power", 3600, "tw1", "power", True, "schedule", due=app.time.time())
            app._set_switch("tw1", "power", False)                       # an auto-off's OFF
            self.assertTrue(app._deferred_active("sched|tw1/power"))      # the ON catch-up still waits
            app._set_switch("tw1", "power", True)                        # it did what the schedule asked
            self.assertFalse(app._deferred_active("sched|tw1/power"))
            app._schedule_deferred("sched|tw1/power", 3600, "tw1", "power", True, "schedule", due=app.time.time())
            app._apply_value("tw1", "power", False, "switch")            # someone's command: it is void
            self.assertFalse(app._deferred_active("sched|tw1/power"))
        finally:
            app._mqtt_client = keep

    def test_a_rename_keeps_the_first_time(self):
        due = app.time.time() - 100
        app._schedule_deferred("sched|tw1/power", 3600, "tw1", "power", True, "schedule", due=due)
        save, app._save_settings = app._save_settings, lambda: None
        try:
            app.migrate_device_key("tw1", "tw2")
            self.assertEqual(app._pending["sched|tw2/power"]["due"], due)
        finally:
            app._save_settings = save
            app._cancel_deferred("sched|tw2/power")
            app._status_cache.pop("tw2", None)


class WsPush(unittest.TestCase):
    """The WebSocket: a frame at once on a change, at most one per WS_MIN_GAP_S, none repeated before the tick."""

    def setUp(self):
        from fastapi.testclient import TestClient
        self.keep = (app._token_ok, app._same_origin, dict(app.DEVICES), dict(app._status_cache))
        app._token_ok = lambda *a, **k: True
        app._same_origin = lambda h: True
        app.DEVICES["wsd"] = {"id": "wsd", "transport": "mqtt", "controls": [], "_ctl": {}}
        app._status_cache["wsd"] = {"online": True, "values": {"n": 0}, "last_seen": time.time()}
        self.c = TestClient(app.app)

    def tearDown(self):
        app._token_ok, app._same_origin = self.keep[0], self.keep[1]
        app.DEVICES.clear(); app.DEVICES.update(self.keep[2])
        app._status_cache.clear(); app._status_cache.update(self.keep[3])

    def frames(self, ws, changes, gap, span):
        """Change the status `changes` times `gap` s apart from a thread: (s since that change, n) of each
        frame seen in `span` s; frames still showing the state before the first change are left out."""
        at = {}

        def change():
            for i in range(changes):
                time.sleep(gap)
                with app._cache_lock:
                    app._status_cache["wsd"] = {**app._status_cache["wsd"], "values": {"n": i + 1}}
                at[i + 1] = time.monotonic()
                app._bump()
        got, t0 = [], time.monotonic()
        n0 = app._status_cache["wsd"]["values"]["n"]
        threading.Thread(target=change, daemon=True).start()
        while time.monotonic() - t0 < span:
            n = json.loads(ws.receive_text())["wsd"]["values"]["n"]
            if n != n0:
                got.append((time.monotonic() - at[n], n))
        return got

    def test_a_change_goes_out_at_once_and_a_burst_is_coalesced(self):
        with self.c.websocket_connect("/ws") as ws:
            ws.receive_text()
            time.sleep(0.5)
            got = self.frames(ws, 1, 0.3, 2.5)
            self.assertEqual(got[0][1], 1)
            self.assertLess(got[0][0], 2 * app.WS_MIN_GAP_S)               # not the 2 s tick
            got = self.frames(ws, 20, 0.05, 1.6)                           # 20 changes a second
            self.assertLessEqual(len(got), 1.6 / app.WS_MIN_GAP_S + 1)
            self.assertEqual(got[-1][1], 20)                               # the latest state arrives

    def test_a_bump_without_a_change_sends_nothing_before_the_tick(self):
        with self.c.websocket_connect("/ws") as ws:
            ws.receive_text()
            t0 = time.monotonic()
            for _ in range(5):
                app._bump()
                time.sleep(0.1)
            ws.receive_text()                                              # the heartbeat
            self.assertGreater(time.monotonic() - t0, app.WS_TICK - 0.3)

    def test_screens_with_one_key_share_a_build(self):
        calls = []
        keep = app._ws_payload
        app._ws_payload = lambda: calls.append(1) or keep()
        try:
            with self.c.websocket_connect("/ws?token=a") as w1, self.c.websocket_connect("/ws?token=a") as w2, \
                    self.c.websocket_connect("/ws?token=b") as w3:
                for w in (w1, w2, w3):
                    w.receive_text()
        finally:
            app._ws_payload = keep
        self.assertLessEqual(len(calls), 2)


class Review5a2Round(unittest.TestCase):
    """c79f425/cfe51fe/c2bfb06 review: a start never drops a shot, the wirings at start and on return."""

    def setUp(self):
        app.DEVICES["tr1"] = {"id": "tr1", "name": "R", "transport": "mqtt", "controls": [
            {"kind": "switch", "code": "power", "command_topic": "tr/set"}]}
        app.DEVICES["tr1"]["_ctl"] = {"power": app.DEVICES["tr1"]["controls"][0]}
        self.sets = []
        self.keep = (app._set_switch, app._arm_inching)
        app._set_switch = lambda *a, **k: self.sets.append(a)
        app._arm_inching = lambda *a, **k: None

    def tearDown(self):
        app._set_switch, app._arm_inching = self.keep
        for t in [t for t in list(app._pending) if "tr1" in t]:
            app._cancel_deferred(t)
        for k in ("tr1", "tr2"):
            app.DEVICES.pop(k, None)
            app._status_cache.pop(k, None)
        app._missed_sched.clear()
        app._bad_request_warned.discard("tr2")

    def test_an_auto_off_overdue_long_at_a_start_waits_and_then_goes_out(self):
        app._status_cache["tr1"] = {"online": False, "waiting": "start", "values": {"power": True}}
        for tok, late in (("inch|tr1/power", 2400), ("for|tr1x", 900)):
            app._schedule_deferred(tok, 3600, "tr1", "power", False, "t", due=app.time.time() - late)   # no timer fires
            app._fire_deferred(tok)
            self.assertTrue(app._deferred_active(tok))                  # over the limit, still not dropped
        self.assertEqual(self.sets, [])
        app._status_cache["tr1"] = {"online": True, "values": {"power": True}}
        app._fire_deferred("inch|tr1/power")
        app._fire_deferred("for|tr1x")
        self.assertEqual(self.sets, [("tr1", "power", False)] * 2)

    def test_a_new_wait_of_a_reused_token_is_logged_again(self):
        app._status_cache["tr1"] = {"online": False, "offline_since": app.time.time(), "values": {}}
        for _ in range(2):
            app._schedule_deferred("inch|tr1/power", 3600, "tr1", "power", False, "t")
            with self.assertLogs(app.log, "INFO") as cm:
                app._fire_deferred("inch|tr1/power")
            self.assertTrue(any("the shot waits for it" in m for m in cm.output))
            app._cancel_deferred("inch|tr1/power")
            self.assertNotIn("inch|tr1/power", app._waiting_shots)

    def test_an_echo_of_a_value_the_device_had_already_answers(self):
        app._cmd_sent[("tr1", "power")] = [(True, app.time.time())]
        try:
            app._cmd_report("tr1", {"power": True}, {"power": True}, codes=set())    # unchanged, so not in codes
            self.assertFalse(app._cmd_sent.get(("tr1", "power")))
            app._cmd_sent[("tr1", "power")] = [(True, app.time.time())]
            app._cmd_report("tr1", {"power": False}, {"power": False}, codes=set())  # not its value: waits on
            self.assertTrue(app._cmd_sent.get(("tr1", "power")))
        finally:
            app._cmd_sent.pop(("tr1", "power"), None)
            app._cmd_settle.pop(("tr1", "power"), None)

    def test_a_bad_state_request_is_named_at_start_and_at_each_add_with_its_cause(self):
        import inspect
        src = inspect.getsource(app._start_pollers)
        self.assertIn("_check_state_requests()", src)
        self.assertIn("_load_missed()", src)
        for req, cause in (("x", "not an object {topic, payload}"), ({"topic": "home/x"}, "its topic:"),
                           ({"topic": "dev/get", "payload": 5}, "its payload: text, an object or a list")):
            app._bad_request_warned.discard("tr2")
            app.DEVICES["tr2"] = {"id": "tr2", "transport": "mqtt", "controls": [], "state_request": req}
            with self.assertLogs(app.log, "WARNING") as cm:
                app._check_state_requests()
            self.assertTrue(any("tr2" in m and cause in m for m in cm.output), cm.output)
        entry = {"id": "tr2", "name": "Q", "state_topic": "q/state", "controls": [], "state_request": "x"}
        for _ in range(2):                                              # a re-add names it again
            with self.assertLogs(app.log, "WARNING") as cm:
                app._register_mqtt_device(dict(entry))
            self.assertTrue(any("state_request refused" in m for m in cm.output))

    def test_a_catch_up_dropped_at_boot_counts_as_missed(self):
        late = app.time.time() - 2000
        with app._pending_lock:
            app._pending["sched|tr1/power|x"] = {"run_at": late, "due": late, "device": "tr1", "code": "power",
                                                 "value": True, "label": "s"}
        keep = app._pending_save
        app._pending_save = lambda: None
        try:
            app._replay_pending_on_boot()
        finally:
            app._pending_save = keep
        self.assertIn(["tr1", "power", late], app._missed_sched)

    def test_the_return_feeds_the_values_before_the_outage(self):
        got = []
        keep = app._rearm_after_outage
        app._rearm_after_outage = lambda dev, st, before: got.append((dev, before))
        try:
            app._status_cache["tr1"] = {"online": False, "offline_since": app.time.time() - 60, "values": {"power": True}}
            app._store_status("tr1", {"online": True, "values": {"power": True}})
        finally:
            app._rearm_after_outage = keep
        self.assertEqual(got, [("tr1", {"power": True})])


class WsWho(unittest.TestCase):
    """17a6ed9 review: a shared frame never crosses callers, whatever carries the token."""
    HOUSE, PHONE = "h" * 40, "p" * 40

    def setUp(self):
        from fastapi.testclient import TestClient
        self.keep = (app._token_ok, app._same_origin, dict(app.DEVICES), dict(app._status_cache), list(app.WS_FILTERS))
        app._token_ok = lambda *a, **k: True
        app._same_origin = lambda h: True
        for d in ("wsa", "wsb"):
            app.DEVICES[d] = {"id": d, "transport": "mqtt", "controls": [], "_ctl": {}}
            app._status_cache[d] = {"online": True, "values": {}, "last_seen": time.time()}

        def trim(ws, payload):                   # a plug-in's trim: the phone sees wsa only, by any carrier
            h, q = ws.headers, ws.query_params
            auth = h.get("authorization", "")
            tok = (auth[7:] if auth.lower().startswith("bearer ") else h.get("x-token") or q.get("token", "")
                   or h.get("cookie", "").partition("tok=")[2] or h.get("sec-websocket-protocol", ""))
            return {k: v for k, v in payload.items() if k == "wsa"} if tok == WsWho.PHONE else payload
        app.WS_FILTERS[:] = [trim]
        self.c = TestClient(app.app)

    def tearDown(self):
        app._token_ok, app._same_origin = self.keep[0], self.keep[1]
        app.DEVICES.clear(); app.DEVICES.update(self.keep[2])
        app._status_cache.clear(); app._status_cache.update(self.keep[3])
        app.WS_FILTERS[:] = self.keep[4]

    @staticmethod
    def carry(tok, how):
        return {"query": ("/ws?token=" + tok, {}), "bearer": ("/ws", {"Authorization": "Bearer " + tok}),
                "x-token": ("/ws", {"X-Token": tok}), "cookie": ("/ws", {"Cookie": "tok=" + tok}),
                "protocol": ("/ws", {"Sec-WebSocket-Protocol": tok})}[how]

    def test_a_restricted_caller_never_gets_the_full_frame(self):
        for how in ("query", "bearer", "x-token", "cookie", "protocol"):
            for first, second in ((self.PHONE, self.HOUSE), (self.HOUSE, self.PHONE)):
                for _ in range(3):
                    app._bump()
                    u1, h1 = self.carry(first, how)
                    u2, h2 = self.carry(second, how)
                    with self.c.websocket_connect(u1, headers=h1) as w1, self.c.websocket_connect(u2, headers=h2) as w2:
                        got = {first: set(json.loads(w1.receive_text())), second: set(json.loads(w2.receive_text()))}
                    self.assertEqual(got[self.PHONE], {"wsa"}, how)
                    self.assertTrue({"wsa", "wsb"} <= got[self.HOUSE], how)

    def test_the_key_takes_every_header_but_the_nonce_and_the_query(self):
        class W:
            def __init__(self, h, q=""):
                self.headers, self.url = h, types.SimpleNamespace(query=q)
        base = {"sec-websocket-key": "a", "user-agent": "x"}
        self.assertEqual(app._ws_who(W(base)), app._ws_who(W({**base, "sec-websocket-key": "b"})))
        for extra in ({"x-token": "t"}, {"authorization": "Bearer t"}, {"cookie": "c=1"}, {"x-anything": "1"}):
            self.assertNotEqual(app._ws_who(W(base)), app._ws_who(W({**base, **extra})), extra)
        self.assertNotEqual(app._ws_who(W(base, "token=a")), app._ws_who(W(base, "token=b")))
        self.assertNotEqual(app._ws_who(W(base)), app._ws_who(W({**base, "user-agent": "y"})))   # every header counts
        self.assertEqual(app._ws_who(W({"a": "1", "b": "2"})), app._ws_who(W({"b": "2", "a": "1"})))   # not their order
        self.assertGreaterEqual(app.WS_IDLE_POLL_S, 0.1)

    def test_the_build_cache_stays_small(self):
        for i in range(300):
            app._ws_built[(i, "x")] = (0.0, "")
        class W:
            headers, query_params, url = {}, {}, types.SimpleNamespace(query="")
        app._ws_frame(W())
        self.assertLess(len(app._ws_built), 10)


class Review17a6(unittest.TestCase):
    """17a6ed9 review: the timers' survivors and the Health text."""

    def setUp(self):
        app.DEVICES["tq1"] = {"id": "tq1", "name": "Q", "transport": "mqtt", "controls": [
            {"kind": "switch", "code": "power", "command_topic": "tq/set"}]}
        app.DEVICES["tq1"]["_ctl"] = {"power": app.DEVICES["tq1"]["controls"][0]}
        self.keep = (app._set_switch,)
        app._set_switch = lambda *a, **k: None

    def tearDown(self):
        app._set_switch = self.keep[0]
        for t in [t for t in list(app._pending) if "tq1" in t]:
            app._cancel_deferred(t)
        app.DEVICES.pop("tq1", None)
        app._status_cache.pop("tq1", None)
        app._missed_sched.clear()

    def test_a_new_arm_forgets_the_old_wait(self):
        app._waiting_shots.add("inch|tq1/power")
        app._schedule_deferred("inch|tq1/power", 3600, "tq1", "power", False, "t")
        self.assertNotIn("inch|tq1/power", app._waiting_shots)

    def test_a_shot_at_a_start_looks_again_every_two_seconds(self):
        app._status_cache["tq1"] = {"online": False, "waiting": "start", "values": {}}
        app._schedule_deferred("inch|tq1/power", 3600, "tq1", "power", False, "t")
        app._fire_deferred("inch|tq1/power")
        self.assertEqual(app._pending_handles["inch|tq1/power"].interval, app.START_RETRY_S)
        self.assertLessEqual(app.START_RETRY_S, 3)
        app._status_cache["tq1"] = {"online": False, "offline_since": time.time(), "values": {}}
        app._fire_deferred("inch|tq1/power")
        self.assertEqual(app._pending_handles["inch|tq1/power"].interval, app.OFFLINE_RETRY_S)

    def test_a_catch_up_of_another_day_says_so(self):
        app._status_cache["tq1"] = {"online": True, "values": {}}
        y = time.time() - 86400 * 1.5
        app._schedule_deferred("sched|tq1/power|y", 3600, "tq1", "power", True, "s", due=y)
        with self.assertLogs(app.log, "WARNING") as cm:
            app._fire_deferred("sched|tq1/power|y")
        self.assertTrue(any("back only after its day" in m for m in cm.output), cm.output)
        self.assertIn(["tq1", "power", y], app._missed_sched)
        app._sched_missed("tq1", "power", time.time() - 60)              # the Health row keeps a day
        rows = [r for r in app._core_health() if r.get("id") == "schedules"]
        self.assertTrue(rows and "missed in a day, the device away at their time" in json.dumps(rows))
        i18n = (pathlib.Path(app.__file__).parent / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('"{0} missed in a day, the device away at their time: {1}": "', i18n)   # and its ru


class DashboardText(unittest.TestCase):
    """The c0b91ad dashboard fixes, read from the page itself (CI has no browser)."""

    @classmethod
    def setUpClass(cls):
        cls.page = _dashboard_page()

    def block(self, start, end):
        i = self.page.index(start)
        return self.page[i:self.page.index(end, i)]

    def test_an_offline_window_refuses_a_switch_by_click_and_key(self):
        onclick = self.block("""md.querySelectorAll('[data-act="toggle"],[data-act="set_bool"]')""", "sendControl(")
        self.assertIn("if(md.classList.contains('devoff')) return;", onclick)
        upd = self.block("function updateModal(", "for(const ctl of d.controls)")
        self.assertIn("e.setAttribute('aria-disabled', String(life === 'offline'));", upd)
        self.assertIn("e.tabIndex = life === 'offline' ? -1 : 0;", upd)

    def test_an_early_echo_ends_the_pulse_by_a_timer(self):
        self.assertIn("setTimeout(updateCards, 1550);", self.block("async function sendControl(", "updateCards();"))

    def test_cards_and_dots_take_the_keyboard(self):
        # the behaviour runs in tests/test_dashboard_js.py (node); here the wiring that node cannot see
        self.assertIn("cardKeys(c, d);", self.page)
        self.assertIn("cardKeys(f, d);", self.page)
        self.assertIn("dotKeys(wrap, d, s, i===0 ? dimB : null);", self.page)
        self.assertIn("dotKeys(dot, d, s, b);", self.block("function dimGroupRow(", "function dimStep("))
        self.assertIn("const base = favLevel(d, b), ui = dimStep(b, base, rel < 0.34 ? -1 : 1);", self.page)
        self.assertIn("flushKeySteps(d.id);\n    if(moved){", self.page)                       # the pointer sends a key step first
        self.assertIn("flushKeySteps(id);", self.block("function openModal(", "function releaseModal("))
        self.assertIn("if(e.repeat) return;", self.page)                                   # a held key on a card: once
        self.assertIn("e.target.classList.contains('card')", self.page)
        self.assertIn("markLife(card, d, life);", self.page)
        self.assertIn("_focusBeforeModal.id && document.getElementById(_focusBeforeModal.id)", self.page)
        self.assertIn(".card:focus-visible, .dotwrap:focus-visible, .dimpill:focus-visible{outline", self.page)

    def test_every_switch_path_builds_on_what_was_asked(self):
        # node runs the keys and the pill; these paths need the whole page, so their wiring is read here
        for path in ("()=> sendControl(f.device, f.code, !switchOn(f.device, f.code)),",          # favourite tile
                     "attachLongPress(c, ()=> sendControl(f.device, f.code, !switchOn(f.device, f.code)),",
                     "attachDimmerDot(dot, d, dimB, ()=> sendControl(d.id, s.code, !switchOn(d.id, s.code)));",
                     "          sendControl(d.id, s.code, !switchOn(d.id, s.code));",          # a plain dot
                     "const nv=!switchOn(d.id,s.code); sendControl(d.id,s.code,nv);",           # a card toggle
                     "      const on = switchOn(d.id, code);"):                                  # the device window
            self.assertIn(path, self.page)
        import re
        flips = re.findall(r"sendControl\([^;]*!isOn\(", self.page) + re.findall(r"!isOn\([^)]*\)\) sendControl", self.page)
        self.assertEqual(flips, [])                                                              # no decision on a stale report

    def test_the_meta_line_has_one_shade(self):
        import re
        meta = re.search(r"\n  \.meta\{([^}]*)\}", self.page).group(1)
        self.assertNotIn("opacity", meta)
        self.assertIn(".card.waiting > :not(.topbar):not(.info), .card.waiting .info > :not(.meta){opacity:.7}", self.page)


class P3SweepCore(unittest.TestCase):
    """The global review's P3 sweep, core part."""

    class Pub:
        def __init__(self):
            self.sent, self.down = [], False

        def publish(self, topic, payload=None, qos=0, retain=False):
            self.sent.append((topic, payload, retain))

        def disconnect(self):
            self.down = True

        def loop_stop(self):
            pass

    def setUp(self):
        self.keep = (app._mqtt_client, dict(app.DEVICES), dict(app._status_cache), app._MQTT_DEVICES_BROKEN)
        app._mqtt_client = self.pub = self.Pub()

    def tearDown(self):
        app._mqtt_client, app._MQTT_DEVICES_BROKEN = self.keep[0], self.keep[3]
        app.DEVICES.clear(); app.DEVICES.update(self.keep[1])
        app._status_cache.clear(); app._status_cache.update(self.keep[2])

    def test_a_window_from_equal_to_is_the_whole_day(self):
        from datetime import datetime
        self.assertTrue(app._in_time_window({"from": "07:00", "to": "07:00"}))
        today = (datetime.now(app._LOCAL_TZ) if app._LOCAL_TZ else datetime.now()).isoweekday()
        self.assertFalse(app._in_time_window({"from": "07:00", "to": "07:00", "days": [d for d in range(1, 8) if d != today]}))

    def test_a_rename_names_the_device_in_the_log_at_once(self):
        from fastapi.testclient import TestClient
        app.DEVICES["rn1"] = {"id": "rn1", "name": "Old", "transport": "mqtt", "controls": [], "_ctl": {}}
        app.BY_ID_NAME["rn1"] = "Old"
        keep = (app._token_ok, app._save_settings)
        app._token_ok, app._save_settings = (lambda *a, **k: True), (lambda: None)
        try:
            r = TestClient(app.app).post("/api/devices/rn1/rename", json={"name": "New"})
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            app._token_ok, app._save_settings = keep
            app._name_overrides.pop("rn1", None)
        self.assertEqual(app.BY_ID_NAME.pop("rn1"), "New")

    def test_the_cycle_publishes_only_what_changed(self):
        import inspect
        self.assertNotIn("_mqtt_last_pub.clear()", inspect.getsource(app._mqtt_refresh_loop))
        app.DEVICES["cy1"] = {"id": "cy1", "name": "C", "transport": "mqtt", "controls": [], "_ctl": {}}
        app._status_cache["cy1"] = {"online": True, "values": {"x": 1}}
        app._mqtt_publish_state("cy1")
        n = len(self.pub.sent)
        app._mqtt_publish_state("cy1")
        self.assertEqual(len(self.pub.sent), n)                     # unchanged: nothing again

    def test_a_channel_taken_out_leaves_the_cache_and_the_mirror(self):
        e = {"id": "fc1", "name": "F", "state_topic": "fc/state", "controls": [
            {"kind": "switch", "code": "power", "state_topic": "fc/p"}, {"kind": "sensor", "code": "temp", "state_topic": "fc/t"}]}
        app._register_mqtt_device(dict(e))
        app._status_cache["fc1"]["values"] = {"power": True, "temp": 21}
        app._status_cache["fc1"]["raw"] = {"power": "ON", "temp": "21"}
        app._mqtt_last_pub["home/state/fc1/temp"] = "21"
        app._register_mqtt_device(dict(e, controls=e["controls"][:1]))
        self.assertEqual(app._status_cache["fc1"]["values"], {"power": True})
        self.assertEqual(app._status_cache["fc1"]["raw"], {"power": "ON"})
        self.assertIn(("home/state/fc1/temp", None, True), self.pub.sent)
        self.assertIn(("home/state/fc1/temp_pct", None, True), self.pub.sent)
        self.assertNotIn("home/state/fc1/temp", app._mqtt_last_pub)          # the mirror sends it again if it returns
        self.assertNotIn(("home/state/fc1/power", None, True), self.pub.sent)

    def test_a_plug_in_update_drops_its_gone_channels_too(self):
        app.DEVICES["pu1"] = {"id": "pu1", "name": "P", "transport": "zz",
                              "_ctl": {"a": {"code": "a"}, "b": {"code": "b"}}}
        app._status_cache["pu1"] = {"online": True, "values": {"a": 1, "b": 2}, "raw": {}}
        keep = (app._owned, app._plugin_device)
        app._owned = lambda *a: None
        app._plugin_device = lambda dev: {**dev, "_ctl": {"a": {"code": "a"}}}
        try:
            app.update_device({"id": "pu1", "name": "P", "transport": "zz"})
        finally:
            app._owned, app._plugin_device = keep
        self.assertEqual(app._status_cache["pu1"]["values"], {"a": 1})

    def test_a_broken_device_file_keeps_their_mirror_and_their_state(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bad = app.Path(d) / "mqtt_devices.json"
            bad.write_text("[{broken")
            mds, why = app._read_mqtt_devices(bad)
            self.assertEqual(mds, [])
            self.assertTrue(why)
            bad.write_text('[{"id": "x"}]')
            self.assertEqual(app._read_mqtt_devices(bad), ([{"id": "x"}], ""))
            bad.write_text('{"id": "x"}')                                     # JSON, but not a list
            mds, why = app._read_mqtt_devices(bad)
            self.assertEqual(mds, [])
            self.assertIn("list", why)
        import inspect
        self.assertIn("_MQTT_DEVICES_BROKEN = _read_mqtt_devices(MQTT_DEVICES_PATH)", app.core_source())
        self.assertIn("_atomic_json_dump(STATUS_CACHE_PATH, _cache_snapshot()", inspect.getsource(app._status_cache_saver_loop))
        app._status_cache["gone1"] = {"online": True, "values": {}}
        app._MQTT_DEVICES_BROKEN = ""
        self.assertNotIn("gone1", app._cache_snapshot())                     # a removed device's state goes
        app._MQTT_DEVICES_BROKEN = "Expecting value"
        self.assertIn("gone1", app._cache_snapshot())                        # but not while the file is broken
        keep = app._paho
        app._paho = object()
        try:
            with self.assertLogs(app.log, "WARNING") as cm:
                self.assertEqual(app._prune_orphan_state_mirror(), 0)
        finally:
            app._paho = keep
        self.assertTrue(any("mqtt_devices.json did not load" in m for m in cm.output))

    def test_the_stop_runs_when_the_app_stops_and_reorder_is_gone(self):
        from fastapi.testclient import TestClient
        calls = []
        keep = (app._shutdown, app._token_ok, app._start_pollers)

        async def started():
            pass
        app._shutdown, app._token_ok, app._start_pollers = (lambda: calls.append(1)), (lambda *a, **k: True), started
        try:
            with TestClient(app.app) as c:
                self.assertEqual(calls, [])
                self.assertIn(c.post("/api/rooms/reorder", json={"order": []}).status_code, (404, 405))
            self.assertEqual(calls, [1])
        finally:
            app._shutdown, app._token_ok, app._start_pollers = keep

    def test_a_stop_keeps_the_states_of_a_broken_device_file(self):
        import tempfile
        keep = (app.STATUS_CACHE_PATH, app.REGISTRY_PENDING.copy())
        app.REGISTRY_PENDING.clear()
        with tempfile.TemporaryDirectory() as d:
            app.STATUS_CACHE_PATH = app.Path(d) / "status_cache.json"
            app._status_cache["gone2"] = {"online": True, "values": {"a": 1}}
            try:
                for broken, kept in (("", False), ("Expecting value", True)):
                    app._MQTT_DEVICES_BROKEN = broken
                    app._shutdown()
                    self.assertEqual("gone2" in json.loads(app.STATUS_CACHE_PATH.read_text()), kept)
            finally:
                app.STATUS_CACHE_PATH = keep[0]
                app.REGISTRY_PENDING.update(keep[1])

    def test_a_manual_run_of_a_rule_that_did_not_go_out_is_no_ok(self):
        from fastapi.testclient import TestClient
        keep = (app._token_ok, app._execute_binding, app._need_broker, app._rule_guard, dict(app._bindings))
        app._token_ok, app._need_broker, app._rule_guard = (lambda *a, **k: True), (lambda: None), (lambda *a: None)
        app._bindings["rr1"] = {"action": {"single": {"target": "zz", "code": "state", "action": "on"}}}
        try:
            for res, status, text in (({"target": "zz", "error": "offline"}, 409, "zz is offline: nothing was sent"),
                                      ({"target": "zz", "error": "publish failed"}, 502, "zz: publish failed"),
                                      ({"target": "zz", "value": 1}, 200, '"ok":true'),
                                      (None, 409, "zz is gone: nothing was sent")):
                app._execute_binding = lambda *a, r=res: dict(r) if r is not None else None
                r = TestClient(app.app).post("/api/rules/run", json={"id": "b|rr1|action|single"})
                self.assertEqual(r.status_code, status, r.text)
                self.assertIn(text, r.text)
        finally:
            app._token_ok, app._execute_binding, app._need_broker, app._rule_guard = keep[:4]
            app._bindings.clear(); app._bindings.update(keep[4])

    def test_a_manual_run_that_sent_nothing_says_why_and_404_is_only_for_no_rule(self):
        from fastapi.testclient import TestClient
        keep = (app._token_ok, app._need_broker, app._rule_guard, dict(app._bindings))
        app._token_ok, app._need_broker, app._rule_guard = (lambda *a, **k: True), (lambda: None), (lambda *a: None)
        app.DEVICES["rrd"] = {"id": "rrd", "name": "Lamp", "transport": "mqtt", "controls": [],
                              "_ctl": {"state": {"kind": "switch", "code": "state"}}}
        try:
            for tgt, code, action, text in (("nope", "state", "on", "nope is gone: nothing was sent"),
                                            ("rrd", "level", "on", "Lamp has no channel level: nothing was sent"),
                                            ("rrd", "state", "bright_up", "Lamp state is not a dimmer: nothing was sent")):
                app._bindings["rr2"] = {"action": {"single": {"target": tgt, "code": code, "action": action}}}
                r = TestClient(app.app).post("/api/rules/run", json={"id": "b|rr2|action|single"})
                self.assertEqual(r.status_code, 409, (action, r.text))
                self.assertIn(text, r.text)
            app._bindings.pop("rr2")
            r = TestClient(app.app).post("/api/rules/run", json={"id": "b|rr2|action|single"})
            self.assertEqual(r.status_code, 404, r.text)
        finally:
            app._token_ok, app._need_broker, app._rule_guard = keep[:3]
            app._bindings.clear(); app._bindings.update(keep[3])
            app.DEVICES.pop("rrd", None)

    def test_a_stop_writes_the_cache_and_leaves_the_broker(self):
        import tempfile
        keep = app.STATUS_CACHE_PATH
        with tempfile.TemporaryDirectory() as d:
            app.STATUS_CACHE_PATH = app.Path(d) / "status_cache.json"
            app._status_cache["sd1"] = {"online": True, "values": {"a": 1}}
            app.DEVICES["sd1"] = {"id": "sd1", "name": "S", "transport": "mqtt", "controls": [], "_ctl": {}}
            try:
                app._shutdown()
                self.assertEqual(json.loads(app.STATUS_CACHE_PATH.read_text())["sd1"]["values"], {"a": 1})
            finally:
                app.STATUS_CACHE_PATH = keep
        self.assertTrue(self.pub.down)


class CoreHelpers(unittest.TestCase):
    """_hooks_check, _atomic_json_dump and _mqtt_publish_state: what they refuse and how they fail."""

    def test_the_hooks_check_refuses_what_a_plug_in_may_not_touch(self):
        g = vars(app)
        keep = ({k: dict(g[k]) for k in app._HOOK_DICTS}, {k: list(g[k]) for k in app._HOOK_LISTS},
                dict(app.DEVICES), set(app.MQTT_NAMESPACES), dict(app._NS_OWNER))

        def restore():
            for k, v in keep[0].items():
                g[k].clear(); g[k].update(v)
            for k, v in keep[1].items():
                g[k][:] = v
            app.DEVICES.clear(); app.DEVICES.update(keep[2])
            app.MQTT_NAMESPACES.clear(); app.MQTT_NAMESPACES.update(keep[3])
            app._NS_OWNER.clear(); app._NS_OWNER.update(keep[4])

        app.TRANSPORTS["early_tr"] = lambda *a: None
        app.DEVICES["early_dev"] = {"id": "early_dev", "transport": "early_tr"}
        app.MQTT_SUBSCRIPTIONS.append("taken/#")
        app.MQTT_NAMESPACES.add("ownedns")
        cases = [
            (lambda: app.TRANSPORTS.__setitem__("early_tr", lambda *a: 1), "registered by an earlier plug-in"),
            (lambda: app.TRANSPORTS.pop("early_tr"), "registered by an earlier plug-in"),
            (lambda: app.MQTT_SUBSCRIPTIONS.insert(0, "x/#"), "append only"),
            (lambda: app.MQTT_SUBSCRIPTIONS.pop(), "append only"),
            (lambda: app.DEVICES.__setitem__("early_dev", {"id": "early_dev"}), "belongs to the core or an earlier"),
            (lambda: app.MQTT_NAMESPACES.discard("ownedns"), "add only"),
            (lambda: app.MQTT_NAMESPACES.add("a/b"), "one plain top-level topic"),
            (lambda: app.MQTT_NAMESPACES.add("home"), "one plain top-level topic"),
            (lambda: app.MQTT_NAMESPACES.add("$SYS"), "one plain top-level topic"),
            (lambda: app.MQTT_NAMESPACES.add("taken"), "an earlier plug-in already uses it"),
            (lambda: app.ASYNC_CONTROL.__setitem__("early_tr", lambda *a: 1), "already registered devices on it"),
        ]
        try:
            snap = app._hooks_snapshot()
            app._hooks_check("p_ok", snap)                                    # nothing changed: fine
            for change, why in cases:
                snap = app._hooks_snapshot()
                change()
                with self.assertRaises(ValueError) as cm:
                    app._hooks_check("p_bad", snap)
                self.assertIn(why, str(cm.exception))
                for k in ("TRANSPORTS", "ASYNC_CONTROL"):
                    g[k].clear(); g[k].update(snap["dicts"][k])
                app.MQTT_SUBSCRIPTIONS[:] = snap["lists"]["MQTT_SUBSCRIPTIONS"]
                app.DEVICES.clear(); app.DEVICES.update(snap["devices"])
                app.MQTT_NAMESPACES.clear(); app.MQTT_NAMESPACES.update(snap["ns"])
            snap = app._hooks_snapshot()
            app.TRANSPORTS["new_tr"] = lambda *a: None                         # new names: fine
            app.MQTT_SUBSCRIPTIONS.append("newns/#")
            app.MQTT_NAMESPACES.add("newns")
            app._hooks_check("p_new", snap)
            self.assertEqual(app._NS_OWNER.get("newns"), "p_new")
        finally:
            restore()

    def test_the_atomic_dump_writes_whole_files_or_nothing(self):
        import stat
        import tempfile
        import unittest.mock
        with tempfile.TemporaryDirectory() as d:
            p = app.Path(d) / "x.json"
            syncs = []
            real = os.fsync
            with unittest.mock.patch.object(app.os, "fsync", side_effect=lambda fd: syncs.append(fd) or real(fd)):
                app._atomic_json_dump(p, {"a": 1}, indent=1)
            self.assertEqual(json.loads(p.read_text()), {"a": 1})
            self.assertGreaterEqual(len(syncs), 2)     # the data, then the directory (other threads may sync too)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode) & 0o077, 0)        # a secret is never world-readable
            with self.assertRaises(TypeError):
                app._atomic_json_dump(p, {"a": object()})                       # not JSON: the old file stays
            self.assertEqual(json.loads(p.read_text()), {"a": 1})
            self.assertEqual(sorted(x.name for x in app.Path(d).iterdir()), ["x.json"])   # no temp left

    def test_the_mirror_publishes_each_value_once_and_retries_one_that_did_not_go(self):
        class Info:
            def __init__(self, rc):
                self.rc = rc

        class Cli:
            def __init__(self):
                self.sent, self.fail = [], None

            def publish(self, t, p, retain=False):
                if self.fail == "raise":
                    raise OSError("down")
                self.sent.append((t, p, retain))
                return Info(4 if self.fail == "rc" else 0)
        keep = (app._mqtt_client, dict(app.DEVICES), dict(app._status_cache), dict(app._mqtt_last_pub))
        app._mqtt_client = cli = Cli()
        ctl = [{"kind": "switch", "code": "power"}, {"kind": "bright", "code": "brightness", "min": 0, "max": 255}]
        app.DEVICES["mp1"] = {"id": "mp1", "name": "M", "transport": "mqtt", "controls": ctl,
                              "_ctl": {c["code"]: c for c in ctl}}
        app._status_cache["mp1"] = {"online": True, "values": {"power": False, "brightness": 128, "mode": "eco"}}
        try:
            app._mqtt_publish_state("mp1")
            got = {t: p for t, p, r in cli.sent}
            self.assertTrue(all(r for _t, _p, r in cli.sent))                  # retained
            self.assertEqual((got["home/state/mp1/online"], got["home/state/mp1/power"], got["home/state/mp1/mode"]),
                             ("true", "false", "eco"))
            self.assertEqual(got["home/state/mp1/brightness_pct"], "50")       # the level, also while off
            n = len(cli.sent)
            app._mqtt_publish_state("mp1")
            self.assertEqual(len(cli.sent), n)                                  # unchanged: nothing
            app._status_cache["mp1"]["values"].update(power=True, brightness=0)
            app._mqtt_publish_state("mp1")
            self.assertEqual({t: p for t, p, r in cli.sent[n:]}["home/state/mp1/brightness_pct"], "1")   # on, never 0 %
            for how in ("raise", "rc"):
                cli.fail = how
                app._status_cache["mp1"]["values"]["mode"] = "auto-" + how
                app._mqtt_publish_state("mp1")
                cli.fail = None
                n = len(cli.sent)
                app._mqtt_publish_state("mp1")
                self.assertIn(("home/state/mp1/mode", "auto-" + how, True), cli.sent[n:])   # tried again
        finally:
            app._mqtt_client = keep[0]
            app.DEVICES.clear(); app.DEVICES.update(keep[1])
            app._status_cache.clear(); app._status_cache.update(keep[2])
            app._mqtt_last_pub.clear(); app._mqtt_last_pub.update(keep[3])

