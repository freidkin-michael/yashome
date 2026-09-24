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
