"""The plug-in contract (docs/plugins.md), exercised with examples/plugins/hello and a broken one."""
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import test_pure  # noqa: E402,F401  (sets HOME_STACK_ROOT to a private copy of the fixtures)
import app  # noqa: E402

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples" / "plugins"


class Plugins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = app._load_plugins(EXAMPLES, top="t_examples")

    def test_example_loads_and_registers(self):
        self.assertIn("hello", self.loaded)
        self.assertIn("hello_lamp", app.DEVICES)
        self.assertIn("hello", app.TRANSPORTS)
        m = next(p for p in app.PLUGINS if p["id"] == "hello")
        self.assertEqual(m["ui"], "/plugins/hello/ui.js")
        self.assertIn("ru", m["i18n"])

    def test_transport_actuates(self):
        app._apply_value("hello_lamp", "power", True, "switch")
        self.assertTrue(app._status_cache["hello_lamp"]["values"]["power"])

    def test_plugin_rule_action(self):
        app.DEVICES["btn"] = {"id": "btn", "name": "b", "transport": "mqtt",
                              "controls": [{"kind": "trigger_text", "code": "action"}]}
        app.DEVICES["btn"]["_ctl"] = {"action": app.DEVICES["btn"]["controls"][0]}
        try:
            app.put_binding("btn", "action", "single", app.BindingBody(target="x", action="greet", text="hi"))
            rule = next(r for r in app._binding_rules() if r["id"] == "b|btn|action|single")
            self.assertEqual(rule["then"][0]["type"], "hello")
            self.assertEqual(app._execute_binding("btn", "action", "single", force=True)["text"], "hi")
        finally:
            app._bindings.pop("btn", None)
            app.DEVICES.pop("btn", None)

    def test_settings_section_saved_and_foreign_section_kept(self):
        app._cfg["someone_elses"] = {"keep": True}
        app._save_settings()
        data = json.loads(app.SETTINGS_PATH.read_text())
        self.assertIn("hello", data)
        self.assertEqual(data["someone_elses"], {"keep": True})

    def test_broken_plugin_is_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "broken").mkdir()
            (pathlib.Path(d) / "broken" / "__init__.py").write_text("raise SystemExit('bad plug-in')\n")
            (pathlib.Path(d) / "Bad-Name").mkdir()
            (pathlib.Path(d) / "Bad-Name" / "__init__.py").write_text("")
            self.assertEqual(app._load_plugins(pathlib.Path(d), top="t_broken"), [])
        self.assertIn("broken", app._PLUGIN_ERRORS)
        self.assertIn("Bad-Name", app._PLUGIN_ERRORS)

    def test_assets_only_of_loaded_plugins(self):
        app.PLUGINS_DIR, keep = EXAMPLES, app.PLUGINS_DIR
        try:
            self.assertEqual(app.plugin_asset("hello", "ui.js").path, EXAMPLES / "hello" / "ui.js")
            for name, f in (("hello", "__init__.py"), ("hello", "../x.js"), ("nope", "ui.js")):
                with self.assertRaises(app.HTTPException):
                    app.plugin_asset(name, f)
        finally:
            app.PLUGINS_DIR = keep


def _pkg(root, name, code, **files):
    d = pathlib.Path(root) / name
    d.mkdir(parents=True)
    (d / "__init__.py").write_text(code)
    for fn, body in files.items():
        p = d / fn.replace("__", "/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return d


class Isolation(unittest.TestCase):
    def load(self, **pkgs):
        d = tempfile.mkdtemp()
        for name, code in pkgs.items():
            _pkg(d, name, code)
        return app._load_plugins(pathlib.Path(d), top="t_" + "_".join(pkgs))

    def test_failed_import_takes_its_hooks_back(self):
        self.load(half="import app as core\ncore.OPEN_PATHS.add('/api/devices')\n"
                       "core.TRANSPORTS['half'] = print\nraise RuntimeError('boom')\n")
        self.assertNotIn("/api/devices", app.OPEN_PATHS)
        self.assertNotIn("half", app.TRANSPORTS)
        self.assertIn("half", app._PLUGIN_ERRORS)

    def test_reserved_names_refused(self):
        for name, code in (("r1", "import app as core\ncore.TRANSPORTS['mqtt'] = print\n"),
                           ("r2", "import app as core\ncore.BINDING_ACTIONS['on'] = {}\n"),
                           ("r3", "import app as core\ncore.MQTT_HANDLERS.append((('home', 'cmd'), print))\n"),
                           ("r4", "import app as core\ncore.MQTT_SUBSCRIPTIONS.append('vendor/#/x')\n"),
                           ("r5", "import app as core\ncore.OPEN_PATHS.add('/ws')\n"),
                           ("r6", "import app as core\ncore.DEVICE_FIELDS.append('name')\n"),
                           ("r7", "PLUGIN = 'not a dict'\n"),
                           ("r8", "import app as core\ncore.MQTT_SUBSCRIPTIONS.append('homeassistant/#')\n"),
                           ("r9", "import app as core\ncore.MQTT_SUBSCRIPTIONS.append('$SYS/#')\n"),
                           ("r10", "import app as core\ncore.SETTINGS_SECTIONS['names'] = dict\n"),
                           ("r11", "import app as core\ncore.MQTT_SUBSCRIPTIONS.append('vendor/a+b')\n"),
                           ("r12", "import app as core\n\n\ndef f():\n    return {}\n\n\ncore.app.get('/plugins/hello/i18n.json')(f)\n")):
            self.assertEqual(self.load(**{name: code}), [], name)
            self.assertIn(name, app._PLUGIN_ERRORS)

    def test_an_earlier_plugins_entries_are_not_taken_over(self):
        self.assertEqual(self.load(own1="import app as core\ncore.TRANSPORTS['own1'] = print\n"), ["own1"])
        mine = app.TRANSPORTS["own1"]
        self.assertEqual(self.load(own2="import app as core\ncore.TRANSPORTS['own1'] = repr\n"), [])
        self.assertIs(app.TRANSPORTS["own1"], mine)
        self.assertIn("own2", app._PLUGIN_ERRORS)

    def test_bad_i18n_is_a_load_error_not_a_crash(self):
        d = tempfile.mkdtemp()
        _pkg(d, "badi18n", "", **{"i18n.json": "[1, 2]"})
        self.assertEqual(app._load_plugins(pathlib.Path(d), top="t_badi18n"), [])
        self.assertIn("badi18n", app._PLUGIN_ERRORS)

    def test_unstorable_section_keeps_the_house_saving(self):
        app.SETTINGS_SECTIONS["bad_set"] = lambda: {1, 2}          # a set is not JSON
        try:
            app._save_settings()                                   # must not raise
        finally:
            app.SETTINGS_SECTIONS.pop("bad_set", None)

    def test_register_device_refuses_a_taken_id(self):
        app.DEVICES["taken"] = {"id": "taken", "transport": "mqtt", "controls": [], "_ctl": {}}
        try:
            with self.assertRaises(ValueError):
                app.register_device({"id": "taken", "transport": "other", "controls": []})
        finally:
            app.DEVICES.pop("taken", None)


class Assets(unittest.TestCase):
    def test_only_public_files(self):
        d = tempfile.mkdtemp()
        pk = _pkg(d, "pub", "", **{"ui.js": "//", "config.json": "{}", ".secret.json": "{}",
                                  "static__icon.svg": "<svg/>"})
        (pk / "static" / "leak.json").symlink_to("/etc/passwd")
        keep = app.PLUGINS_DIR
        app.PLUGINS_DIR = pathlib.Path(d)
        try:
            app._load_plugins(pathlib.Path(d), top="t_pub")
            self.assertTrue(app.plugin_asset("pub", "ui.js"))
            self.assertTrue(app.plugin_asset("pub", "static/icon.svg"))
            for f in ("config.json", ".secret.json", "static/leak.json", "__init__.py", "static/../config.json"):
                with self.assertRaises(app.HTTPException, msg=f):
                    app.plugin_asset("pub", f)
        finally:
            app.PLUGINS_DIR = keep

    def test_plugin_routes_under_plugins_need_the_token(self):
        self.assertIsNone(app._OPEN_ASSET.match("/plugins/ir/learn"))
        self.assertTrue(app._OPEN_ASSET.match("/plugins/ir/ui.js"))


class StartTasks(unittest.TestCase):
    def test_exit_and_errors_do_not_stop_the_core(self):
        import asyncio
        seen = []

        def bye():
            raise SystemExit("plug-in exits")

        async def poller():
            seen.append("ran")
            raise RuntimeError("poller died")
        app.STARTUP_TASKS[:] = [bye, poller]
        try:
            async def go():
                await app._run_startup_tasks()
                await asyncio.sleep(0.05)
            with self.assertLogs("home", level="ERROR") as cm:
                asyncio.run(go())
            self.assertEqual(seen, ["ran"])
            self.assertTrue(any("poller died" in m for m in cm.output))
        finally:
            app.STARTUP_TASKS.clear()

    def test_async_exit_and_a_blocking_task_do_not_stop_the_core(self):
        import asyncio
        import threading
        import time
        gate = threading.Event()

        async def bye():
            raise SystemExit("plug-in exits")
        app.STARTUP_TASKS[:] = [gate.wait, bye]         # gate.wait blocks until the test ends
        try:
            async def go():
                t0 = time.monotonic()
                await app._run_startup_tasks()
                await asyncio.sleep(0.05)
                return time.monotonic() - t0
            with self.assertLogs("home", level="ERROR"):
                self.assertLess(asyncio.run(go()), 1.0)
        finally:
            gate.set()
            app.STARTUP_TASKS.clear()


class PluginRules(unittest.TestCase):
    def setUp(self):
        app._bindings["gone_src"] = {"action": {"single": {"action": "vanished", "target": "t1"}}}

    def tearDown(self):
        app._bindings.pop("gone_src", None)
        app.BINDING_ACTIONS.pop("raises", None)

    def test_rule_of_an_absent_plugin_is_not_a_device_rule(self):
        rule = next(r for r in app._binding_rules() if r["id"] == "b|gone_src|action|single")
        self.assertEqual(rule["then"][0]["type"], "plugin-absent")
        self.assertEqual(rule["then"][0]["action"], "vanished")

    def test_audit_leaves_an_absent_plugins_target_alone(self):
        self.assertNotIn("t1", app._audit_orphan_refs())

    def test_rules_run_404_409_502(self):
        import asyncio

        def boom(entry):
            raise RuntimeError("down")
        app.BINDING_ACTIONS["raises"] = {"validate": dict, "run": boom, "describe": dict}
        app._bindings["gone_src"]["action"]["double"] = {"action": "raises", "target": "t1"}
        for rid, code in (("b|gone_src|action|nope", 404), ("b|gone_src|action|double", 502),
                          ("b|gone_src|action|single", 409)):
            with self.assertRaises(app.HTTPException) as cm:
                asyncio.run(app.rule_run(app.RuleIdBody(id=rid)))
            self.assertEqual(cm.exception.status_code, code, rid)
