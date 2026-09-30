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
        app._check_topic_name("Kitchen lamp 2")


class PluginActionNotJson(unittest.TestCase):
    def test_non_json_entry_refused_and_not_stored(self):
        app.BINDING_ACTIONS["t_nojson"] = {"validate": lambda b: {"action": "t_nojson", "target": "x",
                                                                  "when": object()}}
        app.DEVICES["pj"] = {"id": "pj", "name": "p", "transport": "mqtt",
                             "controls": [{"kind": "trigger_text", "code": "action"}]}
        app.DEVICES["pj"]["_ctl"] = {"action": app.DEVICES["pj"]["controls"][0]}
        try:
            with self.assertRaises(app.HTTPException) as cm:
                app.put_binding("pj", "action", "single", app.BindingBody(target="x", action="t_nojson"))
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
                                                                        threshold=thr, hyst=hyst))
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
        app.rule_enable(app.RuleIdBody(id=off_id, enabled=False))
        self.assertTrue(app._deferred_active("sched|sr/p"))          # the other row's retry stays
        app.rule_enable(app.RuleIdBody(id=on_id, enabled=False))
        self.assertFalse(app._deferred_active("sched|sr/p"))
        app._schedule_deferred("sched|sr/p", 60, "sr", "p", False, "schedule retry")
        app.rule_delete(app.RuleIdBody(id=off_id))
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
