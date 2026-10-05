"""The plug-in contract (docs/plugins.md), exercised with plugins/_example and a broken one."""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import test_pure  # noqa: E402,F401  (sets HOME_STACK_ROOT to a private copy of the fixtures)
import app  # noqa: E402

EXAMPLES = pathlib.Path(tempfile.mkdtemp())      # plugins/_example, under the name it is written for
shutil.copytree(pathlib.Path(__file__).resolve().parent.parent / "plugins" / "_example", EXAMPLES / "hello")


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
        own = json.loads((app.PLUGIN_DATA / "hello" / "settings.json").read_text())
        self.assertIn("hello", own)
        self.assertNotIn("hello", data)                     # a plug-in's section lives in its own file
        self.assertEqual(data["someone_elses"], {"keep": True})
        self.assertIn("names", data)

    def test_house_root_holds_the_house_files(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            code = ("import app; print(app.HOUSE, app.SETTINGS_PATH.parent, app.MQTT_DEVICES_PATH.parent,"
                    " app.PLUGIN_DATA.parent, app._PENDING_PATH.parent, app.STATUS_CACHE_PATH.parent,"
                    " app.PLUGIN_STATE.parent, app.PLUGINS_DIR.parent)")
            out = subprocess.run([sys.executable, "-c", code], cwd=pathlib.Path(__file__).parent.parent,
                                 env={**os.environ, "HOUSE_ROOT": d, "PLUGINS": ""},
                                 capture_output=True, text=True, check=True).stdout.split()
            self.assertEqual(out[:7], [d] * 7)
            self.assertNotEqual(out[7], d)                  # the code and its plug-ins stay in the checkout

    def test_plugin_state_file_moves_an_old_copy_once(self):
        old = app.PLUGIN_DATA / "hello" / "keys.json"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text('{"k": 1}')
        new = app.plugin_state_file("hello", "keys.json")
        self.assertEqual(new, app.PLUGIN_STATE / "hello" / "keys.json")
        self.assertEqual(new.read_text(), '{"k": 1}')
        self.assertFalse(old.exists())
        shutil.rmtree(app.PLUGIN_STATE / "hello")      # plugin-state/ is lost (a house restored from git)
        old.write_text('{"k": 0}')                     # and an old copy turns up: it is NOT taken back
        self.assertFalse(app.plugin_state_file("hello", "keys.json").exists())

    def test_plugin_state_file_with_both_copies_merges_and_puts_the_old_aside(self):
        old = app.PLUGIN_DATA / "hello" / "ctr.json"
        old.parent.mkdir(parents=True, exist_ok=True)
        (app.PLUGIN_STATE / "hello").mkdir(parents=True, exist_ok=True)
        (app.PLUGIN_STATE / "hello" / "ctr.json").write_text("new")
        old.write_text("old")
        new = app.plugin_state_file("hello", "ctr.json", merge=lambda a, b: a + b"+" + b)
        self.assertEqual(new.read_text(), "new+old")
        self.assertFalse(old.exists())
        self.assertEqual(len(list(old.parent.glob("ctr.json.migrated-*"))), 1)

    def test_plugin_settings_files_are_read_at_boot(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(pathlib.Path(__file__).parent / "fixtures", d, dirs_exist_ok=True)
            for name, body in (("zeta", '{"zeta_sec": [1, 2], "names": {"x": "core"}}'), ("Bad-Dir", '{"bad": 1}'),
                               ("broken", "{"), ("listy", "[1, 2]")):
                (pathlib.Path(d) / "plugin-data" / name).mkdir(parents=True)
                (pathlib.Path(d) / "plugin-data" / name / "settings.json").write_text(body)
            code = ("import app, json; print(json.dumps([app._cfg_section('zeta_sec', []), app._SECTION_FILE,"
                    " 'x' in app._cfg_section('names', {}), sorted(app._load_failed)]))")
            out = subprocess.run([sys.executable, "-c", code], cwd=pathlib.Path(__file__).parent.parent,
                                 env={**os.environ, "HOME_STACK_ROOT": d, "PLUGINS": ""},
                                 capture_output=True, text=True, check=True).stdout.splitlines()[-1]
        sec, owners, core_taken, failed = json.loads(out)
        self.assertEqual(sec, [1, 2])
        self.assertEqual(owners, {"zeta_sec": "zeta"})      # not the core's "names", not a bad dir name
        self.assertFalse(core_taken)
        self.assertEqual(failed, ["settings"])              # an unreadable plug-in file stops saves, like settings.json

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
                           ("r12", "import app as core\n\n\ndef f():\n    return {}\n\n\ncore.app.get('/plugins/r12/i18n.json')(f)\n"),
                           ("r13", "import app as core\n\n\ndef f(p: str):\n    return {}\n\n\n"
                                   "core.app.get('/plugins/hello/{p:path}')(f)\n")):
            self.assertEqual(self.load(**{name: code}), [], name)
            self.assertIn(name, app._PLUGIN_ERRORS)

    def test_open_route_only_for_own_routes(self):
        code = ("import app as core\n\n\n@core.open_route\n@core.app.post('/api/pairtest/register')\n"
                "def reg():\n    return {'ok': True}\n")
        self.assertEqual(self.load(pairok=code), ["pairok"])
        scope = {"type": "http", "method": "POST", "path": "/api/pairtest/register", "root_path": ""}
        self.assertIn(id(app._matched_route(scope)), app.OPEN_ROUTES)
        core_scope = {"type": "http", "method": "GET", "path": "/api/devices", "root_path": ""}
        self.assertNotIn(id(app._matched_route(core_scope)), app.OPEN_ROUTES)
        grab = "import app as core\ncore.open_route(core.get_devices if hasattr(core, 'get_devices') else core.health)\n"
        self.assertEqual(self.load(pairbad=grab), [])
        self.assertIn("pairbad", app._PLUGIN_ERRORS)

    def test_own_topics_under_home_are_allowed(self):
        code = ("import app as core\ncore.MQTT_SUBSCRIPTIONS.append('home/hprov/#')\n"
                "core.MQTT_HANDLERS.append((('home', 'hprov'), print))\n")
        self.assertEqual(self.load(hprov=code), ["hprov"])
        for name, t in (("h1", "home/cmd/x"), ("h2", "home/state/#"), ("h3", "home/#"), ("h4", "home/+/x"), ("h5", "home"),
                        ("h6", "home/door_lock/#"), ("h7", "$share/g/home/cmd/#"), ("h8", "$queue/zigbee2mqtt/#")):
            self.assertEqual(self.load(**{name: f"import app as core\ncore.MQTT_SUBSCRIPTIONS.append({t!r})\n"}), [], t)
        dev = "import app as core\ncore.MQTT_HANDLERS.append((('home', 'door_lock'), print))\n"
        self.assertEqual(self.load(h9=dev), [])

    def test_reserved_names_do_not_open_the_core_bus(self):
        app.DEVICES["gate"] = {"id": "gate", "name": "gate", "transport": "mqtt", "controls": []}
        try:
            for name in ("cmd", "state", "gate"):
                code = f"import app as core\ncore.MQTT_SUBSCRIPTIONS.append('home/{name}/#')\n"
                self.assertEqual(self.load(**{name: code}), [], name)
        finally:
            app.DEVICES.pop("gate", None)

    def test_plugin_data_is_its_own_ignored_directory(self):
        d = app.plugin_data("hello")
        self.assertEqual(d, app.ROOT / "plugin-data" / "hello")
        self.assertTrue(d.is_dir())
        for bad in ("../x", "", "Hello", "a/b"):
            with self.assertRaises(ValueError):
                app.plugin_data(bad)
        ignore = (pathlib.Path(__file__).resolve().parent.parent / ".gitignore").read_text()
        self.assertIn("/plugin-data/", ignore.splitlines())

    def test_a_later_device_cannot_take_a_plugins_name(self):
        app.PLUGINS.append({"id": "pgate"})
        try:
            with self.assertRaises(app.HTTPException) as cm:
                app._register_mqtt_device({"id": "pgate", "name": "g", "controls": []})
            self.assertEqual(cm.exception.status_code, 409)
            self.assertNotIn("pgate", app.DEVICES)
        finally:
            app.PLUGINS.remove({"id": "pgate"})

    def test_open_route_opens_one_route_not_its_function(self):
        two = ("import app as core\n\n\ndef f():\n    return {}\n\n\n"
               "core.app.get('/api/pt2/a')(f)\ncore.open_route(core.app.get('/api/pt2/b')(f))\n")
        self.assertEqual(self.load(pt2=two), ["pt2"])
        scope = {"type": "http", "method": "GET", "root_path": ""}
        self.assertNotIn(id(app._matched_route({**scope, "path": "/api/pt2/a"})), app.OPEN_ROUTES)
        self.assertIn(id(app._matched_route({**scope, "path": "/api/pt2/b"})), app.OPEN_ROUTES)
        reuse = ("import app as core\nf = core._matched_endpoint({'type': 'http', 'method': 'GET', "
                 "'path': '/api/devices', 'root_path': ''})\ncore.open_route(core.app.get('/api/pt3/d')(f))\n")
        self.assertEqual(self.load(pt3=reuse), ["pt3"])
        self.assertNotIn(id(app._matched_route({**scope, "path": "/api/devices"})), app.OPEN_ROUTES)
        self.assertIn(id(app._matched_route({**scope, "path": "/api/pt3/d"})), app.OPEN_ROUTES)

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

    def test_token_checks_admit_only_an_exact_true(self):
        app.TOKEN_CHECKS[:] = [lambda t, m, p: (_ for _ in ()).throw(RuntimeError("boom")), lambda t, m, p: "yes",
                               lambda t, m, p: t == "panelkey" and p == "/api/devices"]
        master, app.DASHBOARD_TOKEN = app.DASHBOARD_TOKEN, "master-token"
        try:
            self.assertTrue(app._token_ok({"authorization": "Bearer panelkey"}, None, "GET", "/api/devices"))
            self.assertFalse(app._token_ok({"authorization": "Bearer panelkey"}, None, "POST", "/api/panels/bind"))
            self.assertFalse(app._token_ok({"authorization": "Bearer other"}))
            self.assertFalse(app._token_ok({"authorization": "Bearer "}))
            self.assertTrue(app._token_ok({"authorization": f"Bearer {app.DASHBOARD_TOKEN}"}))
            app.TOKEN_CHECKS[:] = [lambda t, m, p: "yes"]
            self.assertFalse(app._token_ok({"x-token": "anything"}))
        finally:
            app.TOKEN_CHECKS[:] = []
            app.DASHBOARD_TOKEN = master

    def test_on_mqtt_connect_callbacks_run_and_a_failing_one_is_contained(self):
        seen = []
        app.ON_MQTT_CONNECT[:] = [lambda cli: (_ for _ in ()).throw(RuntimeError("boom")), lambda cli: seen.append(cli)]
        try:
            class Cli:
                def subscribe(self, *a, **k):
                    pass

                def publish(self, *a, **k):
                    pass
            c = Cli()
            app._mqtt_on_connect(c, None, None, 0)
            self.assertEqual(seen, [c])
        finally:
            app.ON_MQTT_CONNECT[:] = []

    def test_update_and_remove_only_own_devices(self):
        d = app.register_device({"id": "upd1", "name": "A", "transport": "t_upd", "controls": [{"kind": "switch", "code": "power"}]})
        try:
            app._store_status("upd1", {"online": True, "values": {"power": True}})
            u = app.update_device({"id": "upd1", "name": "B", "transport": "t_upd", "category": "ir",
                                   "controls": [{"kind": "switch", "code": "power"}, {"kind": "sensor", "code": "t"}]})
            self.assertEqual((u["name"], u["category"], sorted(u["_ctl"])), ("B", "ir", ["power", "t"]))
            self.assertTrue(app._status_copy("upd1", {})["values"]["power"])
            for bad in ({"id": "upd1", "transport": "other"}, {"id": "nope", "transport": "t_upd"}):
                with self.assertRaises(ValueError):
                    app.update_device(bad)
            app.DEVICES["core1"] = {"id": "core1", "name": "c", "transport": "t_upd", "controls": []}   # same transport, not registered
            with self.assertRaises(ValueError):
                app.remove_device("core1", "t_upd")
            app.DEVICES.pop("core1")
            app.remove_device("upd1", "t_upd")
            self.assertNotIn("upd1", app.DEVICES)
            self.assertEqual(app._status_copy("upd1", None), None)
        finally:
            app.DEVICES.pop("upd1", None)
            app._PLUGIN_DEVICES.discard(d["id"])

    def test_a_plugin_touches_only_the_devices_it_registered(self):
        dev = {"id": "own1", "name": "A", "transport": "t_own", "controls": []}
        as_a = {"__name__": "yashome_plugins.alpha", "core": app, "dev": dev}
        as_b = {"__name__": "yashome_plugins.beta.sub", "core": app, "dev": dev}
        try:
            exec("core.register_device(dev)", as_a)
            self.assertEqual(app._DEVICE_OWNER["own1"], "alpha")
            for call in ("core.update_device(dev)", "core.remove_device('own1', 't_own')"):
                with self.assertRaises(ValueError):
                    exec(call, as_b)
            exec("core.update_device(dev)", as_a)
            exec("core.remove_device('own1', 't_own')", as_a)
            self.assertNotIn("own1", app.DEVICES)
            self.assertNotIn("own1", app._DEVICE_OWNER)
        finally:
            app.DEVICES.pop("own1", None)
            app._PLUGIN_DEVICES.discard("own1")
            app._DEVICE_OWNER.pop("own1", None)

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

    def test_a_parametrised_plugin_route_is_not_opened(self):
        d = tempfile.mkdtemp()
        _pkg(d, "rt", "import app as core\n\n\ndef f(f: str):\n    return {}\n\n\n"
                      "def p(p: str):\n    return {}\n\n\n"
                      "core.app.get('/plugins/rt/static/{f}')(f)\ncore.app.get('/plugins/rt/{p:path}')(p)\n",
             **{"ui.js": "//"})
        self.assertEqual(app._load_plugins(pathlib.Path(d), top="t_rt"), ["rt"])
        routes = app.app.router.routes
        asset = next(r for r in routes if getattr(r, "endpoint", None) is app.plugin_asset)
        keep = list(routes)
        routes.remove(asset); routes.append(asset)           # as in production: after the plug-ins
        try:
            for path in ("/plugins/rt/static/data.json", "/plugins/rt/ui.js"):
                scope = {"type": "http", "method": "GET", "path": path, "root_path": ""}
                self.assertFalse(app._routes_to_asset(scope), path)
            scope = {"type": "http", "method": "GET", "path": "/plugins/hello/ui.js", "root_path": ""}
            self.assertTrue(app._routes_to_asset(scope))
        finally:
            routes[:] = keep

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


class TransportHooks(unittest.TestCase):
    """Core 2.0: what a transport plug-in (zigbee) needs from the core."""

    def load(self, **pkgs):
        d = tempfile.mkdtemp()
        for name, code in pkgs.items():
            _pkg(d, name, code)
        return app._load_plugins(pathlib.Path(d), top="t_" + "_".join(pkgs))

    def test_namespace_is_its_owners_only(self):
        own = ("import app as core\ncore.MQTT_NAMESPACES.add('bus7')\n"
               "core.MQTT_SUBSCRIPTIONS.extend(['bus7/bridge/devices', 'bus7/+'])\n"
               "core.MQTT_HANDLERS.append((('bus7',), print))\n")
        self.assertEqual(self.load(nsown=own), ["nsown"])
        self.assertEqual(app._NS_OWNER.get("bus7"), "nsown")
        for name, code in (("nsother", "import app as core\ncore.MQTT_SUBSCRIPTIONS.append('bus7/#')\n"),
                           ("nsclaim", "import app as core\ncore.MQTT_NAMESPACES.add('home')\n"),
                           ("nsslash", "import app as core\ncore.MQTT_NAMESPACES.add('a/b')\n"),
                           ("nsdrop", "import app as core\ncore.MQTT_NAMESPACES.discard('bus7')\n")):
            self.assertEqual(self.load(**{name: code}), [], name)
        self.assertIn("bus7", app.MQTT_NAMESPACES)
        self.assertFalse(app._node_topic_ok("bus7/x/set"))

    def test_failed_import_gives_its_namespace_and_registry_back(self):
        self.load(nshalf="import app as core\ncore.MQTT_NAMESPACES.add('bus8')\ncore.registry_pending('nshalf')\n"
                         "raise RuntimeError('boom')\n")
        self.assertNotIn("bus8", app.MQTT_NAMESPACES)
        self.assertNotIn("bus8", app._NS_OWNER)
        self.assertNotIn("nshalf", app.REGISTRY_PENDING)

    def test_registry_barrier(self):
        app.registry_pending("slowlist")
        try:
            self.assertFalse(app._registry_known())
            self.assertFalse(app._wait_registry(0.05))
            import threading
            threading.Timer(0.1, app.registry_ready, args=("slowlist",)).start()
            self.assertTrue(app._wait_registry(3))
        finally:
            app.registry_ready("slowlist")

    def test_resync_hooks_are_summed_and_contained(self):
        calls = []

        def good(cli, force):
            calls.append(force)
            return 3

        def bad(cli, force):
            raise RuntimeError("down")
        saved = list(app.RESYNC_HOOKS)
        app.RESYNC_HOOKS[:] = [good, bad, good]
        try:
            self.assertEqual(app._run_resync_hooks(object(), True), 6)
            self.assertEqual(calls, [True, True])
        finally:
            app.RESYNC_HOOKS[:] = saved

    def test_transport_binding_or_the_generic_path(self):
        sent, applied = [], []
        app.DEVICES["tb_lamp"] = {"id": "tb_lamp", "name": "l", "transport": "tbx", "_ctl": {"state": {"kind": "switch", "code": "state"}},
                                  "controls": [{"kind": "switch", "code": "state"}]}
        rule = {"target": "tb_lamp", "code": "state", "action": "on"}
        keep = (dict(app._bindings), dict(app.TRANSPORT_BINDINGS), dict(app.TRANSPORTS))
        app._bindings["tb_btn"] = {"action": {"single": rule}}
        app.TRANSPORTS["tbx"] = lambda cfg, code, val, kind: applied.append((code, val))
        try:
            app.TRANSPORT_BINDINGS["tbx"] = lambda s, c, g, b: sent.append(b["action"]) or {"target": "tb_lamp", "code": "state"}
            self.assertEqual(app._execute_binding("tb_btn", "action", "single", force=True)["code"], "state")
            self.assertEqual((sent, applied), (["on"], []))
            app.TRANSPORT_BINDINGS["tbx"] = lambda s, c, g, b: None
            self.assertIs(app._execute_binding("tb_btn", "action", "single", force=True)["value"], True)
            self.assertEqual(applied, [("state", True)])
        finally:
            app._bindings.clear(); app._bindings.update(keep[0])
            app.TRANSPORT_BINDINGS.clear(); app.TRANSPORT_BINDINGS.update(keep[1])
            app.TRANSPORTS.clear(); app.TRANSPORTS.update(keep[2])
            app.DEVICES.pop("tb_lamp", None); app._status_cache.pop("tb_lamp", None)
            app._cancel_inching("tb_lamp/state")

    def test_a_transport_is_its_registrants_and_a_namespace_keeps_generic_topics_out(self):
        self.addCleanup(app.TRANSPORTS.pop, "trx", None)
        self.assertEqual(self.load(trown="import app as core\ncore.TRANSPORTS['trx'] = print\n"), ["trown"])
        grab = ("import app as core\ncore.register_device({'id': 'tr_grab', 'transport': 'trx', 'controls': []})\n")
        self.assertEqual(self.load(trgrab=grab), [])
        self.assertIn("belongs to plug-in 'trown'", app._PLUGIN_ERRORS["trgrab"])
        app.MQTT_NAMESPACES.add("bus9")
        app.DEVICES["ns_gen"] = {"id": "ns_gen", "transport": "mqtt", "state_topic": "bus9/x", "controls": []}
        try:
            self.assertNotIn("bus9/x", app._mqtt_topic_index())
        finally:
            app.MQTT_NAMESPACES.discard("bus9"); app.DEVICES.pop("ns_gen", None)

    def test_plugin_ids_are_topic_levels(self):
        self.assertTrue(app._plugin_id_ok("Lamp 1") and app._plugin_id_ok("Lamp \u00e9 1"))
        for bad in ("a/b", "a+b", "a#b", "a|b", "", "a\nb", None, "x" * 257):
            self.assertFalse(app._plugin_id_ok(bad), bad)

    def test_migrate_calls_on_rename_and_z2m_is_a_plugin_transport(self):
        seen = []
        app.ON_RENAME.append(lambda o, n: seen.append((o, n)))
        try:
            app.migrate_device_key("mk_old", "mk_new")
            self.assertEqual(seen, [("mk_old", "mk_new")])
        finally:
            app.ON_RENAME.pop()
        self.assertNotIn("z2m", app._CORE_TRANSPORTS)
        self.assertEqual(self.load(ztr="import app as core\ncore.TRANSPORTS['z2m'] = print\n"), ["ztr"])
        app.TRANSPORTS.pop("z2m", None)


SECTIONS_RUN = """
import json, os, pathlib, sys
sys.path.insert(0, os.environ["CORE"])
import app
d = pathlib.Path(os.environ["PDIR"])
app._load_plugins(d, top="t_sections")
if os.environ.get("START"):                     # the real start path, network parts stubbed
    import asyncio
    for fn in ("_load_status_cache", "_load_pending", "_mqtt_start"):
        setattr(app, fn, lambda: None)
    app.threading.Thread = lambda *a, **k: type("T", (), {"start": lambda self: None})()
    async def _quiet():
        return None
    app._after_broker = _quiet
    asyncio.run(app._start_pollers())
    moved = None
else:
    moved = app._migrate_settings_at_start()
if os.environ.get("SAVE"):
    app._cfg_section("x", 0)
    app._save_settings()
h = pathlib.Path(os.environ["HOME_STACK_ROOT"])
files = {str(f.relative_to(h)): json.loads(f.read_text()) for f in sorted(h.glob("plugin-data/*/settings.json"))}
print(json.dumps({"moved": moved, "settings": json.loads((h / "settings.json").read_text()), "files": files,
                  "bad": app._settings_bad() if "settings" in app._load_failed else "",
                  "aside": sorted(str(p.relative_to(h)).split(".pre-split-")[0] for p in h.rglob("*.pre-split-*"))}))
"""


class SettingsSplit(unittest.TestCase):
    """Plug-in sections in plugin-data/<name>/settings.json: migration, conflicts, duplicates, owner change."""

    def run_house(self, settings, plugin_files, owner="zz", save=False, start=False):
        import subprocess
        h, pd = pathlib.Path(tempfile.mkdtemp()), pathlib.Path(tempfile.mkdtemp())
        _pkg(pd, owner, "import app as core\n"
                         "core.SETTINGS_SECTIONS['zsec'] = lambda: core._cfg_section('zsec', {})\n")
        for name, body in plugin_files.items():
            (h / "plugin-data" / name).mkdir(parents=True)
            (h / "plugin-data" / name / "settings.json").write_text(json.dumps(body))
        (h / "settings.json").write_text(json.dumps({"names": {"a": "b"}, **settings}))
        env = {**os.environ, "HOME_STACK_ROOT": str(h), "PDIR": str(pd), "PLUGINS": "", "SAVE": "1" if save else "",
               "START": "1" if start else "",
               "CORE": str(pathlib.Path(__file__).resolve().parent.parent)}
        env.pop("HOUSE_ROOT", None)
        out = subprocess.run([sys.executable, "-c", SECTIONS_RUN], env=env, capture_output=True, text=True, check=True)
        return json.loads(out.stdout.splitlines()[-1])

    def test_a_section_left_in_settings_json_moves_at_start(self):
        r = self.run_house({"zsec": {"v": 1}}, {})
        self.assertTrue(r["moved"])
        self.assertEqual(r["aside"], ["settings.json"])        # the old settings.json, one copy back for an older core
        self.assertNotIn("zsec", r["settings"])
        self.assertEqual(r["settings"]["names"], {"a": "b"})
        self.assertEqual(r["files"], {"plugin-data/zz/settings.json": {"zsec": {"v": 1}}})

    def test_the_start_moves_it(self):
        r = self.run_house({"zsec": {"v": 1}}, {}, start=True)
        self.assertNotIn("zsec", r["settings"])
        self.assertEqual(r["files"], {"plugin-data/zz/settings.json": {"zsec": {"v": 1}}})

    def test_one_directory_under_two_names_is_not_an_empty_house(self):
        import subprocess
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            pathlib.Path(root, "mqtt_devices.json").write_text("[]")
            alias = pathlib.Path(other, "house")
            alias.symlink_to(root)                      # like /app and /house in the container without HOUSE_DIR
            code = "import app; print(sorted(app._load_failed), app._migrate_settings_at_start())"
            out = subprocess.run([sys.executable, "-c", code], cwd=pathlib.Path(__file__).parent.parent,
                                 env={**os.environ, "HOME_STACK_ROOT": root, "HOUSE_ROOT": str(alias), "PLUGINS": ""},
                                 capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
            self.assertEqual(out, "[] True")
            self.assertTrue(pathlib.Path(root, "settings.json").is_file())

    def test_an_empty_house_dir_over_a_house_in_the_checkout_saves_nothing(self):
        for keep, body in (("settings.json", '{"names": {}}'), ("mqtt_devices.json", "[]")):
            with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as house:
                pathlib.Path(root, keep).write_text(body)
                self.empty_house_case(root, house)

    def empty_house_case(self, root, house):
        import subprocess
        code = ("import app; print(app._settings_bad() if 'settings' in app._load_failed else '-',"
                " app._migrate_settings_at_start())")
        out = subprocess.run([sys.executable, "-c", code], cwd=pathlib.Path(__file__).parent.parent,
                             env={**os.environ, "HOME_STACK_ROOT": root, "HOUSE_ROOT": house, "PLUGINS": ""},
                             capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
        self.assertIn("make move-house", out)
        self.assertTrue(out.endswith("False"))
        self.assertEqual(list(pathlib.Path(house).iterdir()), [])

    def test_nothing_to_move_writes_nothing(self):
        r = self.run_house({}, {"zz": {"zsec": {"v": 1}}})
        self.assertFalse(r["moved"])

    def test_a_different_copy_in_settings_json_loses_and_both_are_kept(self):
        r = self.run_house({"zsec": {"v": "edited"}}, {"zz": {"zsec": {"v": "current"}}})
        self.assertEqual(r["files"]["plugin-data/zz/settings.json"], {"zsec": {"v": "current"}})
        self.assertEqual(r["aside"], ["plugin-data/zz/settings.json", "settings.json"])

    def test_an_older_cores_default_never_replaces_the_data(self):
        r = self.run_house({"zsec": {"room_temp_from": {}}}, {"zz": {"zsec": {"room_temp_from": {"ac_1": "sensor_1"}}}})
        self.assertEqual(r["files"]["plugin-data/zz/settings.json"], {"zsec": {"room_temp_from": {"ac_1": "sensor_1"}}})

    def test_a_section_taken_over_keeps_the_old_owners_file_aside(self):
        r = self.run_house({"zsec": {"v": "settings"}}, {"gone": {"zsec": {"v": "real"}}}, owner="newer")
        self.assertEqual(r["files"]["plugin-data/newer/settings.json"], {"zsec": {"v": "real"}})
        self.assertEqual(r["aside"], ["plugin-data/gone/settings.json", "settings.json"])

    def test_a_section_in_two_plugin_files_stops_saves_and_names_both(self):
        r = self.run_house({}, {"aa": {"zsec": 1}, "zz": {"zsec": 2}})
        self.assertIn("plugin-data/aa/settings.json", r["bad"])
        self.assertIn("plugin-data/zz/settings.json", r["bad"])

    def test_a_section_taken_over_removes_an_old_file_that_held_only_it(self):
        r = self.run_house({}, {"gone": {"zsec": {"v": "kept"}}}, owner="newer", save=True)
        self.assertEqual(r["files"], {"plugin-data/newer/settings.json": {"zsec": {"v": "kept"}}})

    def test_a_section_taken_over_leaves_the_old_owners_file(self):
        r = self.run_house({}, {"gone": {"zsec": {"v": "kept"}, "other": 1}}, owner="newer", save=True)
        self.assertEqual(r["files"]["plugin-data/newer/settings.json"], {"zsec": {"v": "kept"}})
        self.assertEqual(r["files"]["plugin-data/gone/settings.json"], {"other": 1})


class GroupsAndRequires(unittest.TestCase):
    """plugins/<group>/<name>/ and the `requires` file: what PLUGINS names brings its requirements."""

    def _load(self, layout, plugins):
        d = pathlib.Path(tempfile.mkdtemp())
        for path, files in layout.items():
            _pkg(d, path, files.pop("__init__.py", ""), **files)
        keep = app.PLUGINS_DIR, app.PLUGINS_EXTRA, os.environ.get("PLUGINS")
        app.PLUGINS_DIR, app.PLUGINS_EXTRA = d, d / "none"
        os.environ["PLUGINS"] = plugins
        try:
            return app._load_plugins(top="t_grp_" + next(iter(layout)).replace("/", "_"))
        finally:
            app.PLUGINS_DIR, app.PLUGINS_EXTRA = keep[0], keep[1]
            os.environ.pop("PLUGINS") if keep[2] is None else os.environ.__setitem__("PLUGINS", keep[2])

    def test_required_transport_comes_first_and_on(self):
        got = self._load({"transports/gtx": {}, "devices/gdev": {"requires": "gtx  # its transport\n"},
                          "devices/goff": {}}, "gdev")
        self.assertEqual(got, ["gtx", "gdev"])
        m = next(p for p in app.PLUGINS if p["id"] == "gdev")
        self.assertEqual((m["group"], m["requires"]), ("devices", ["gtx"]))
        self.assertIn("goff", app._PLUGIN_OFF)

    def test_missing_failing_and_circular_requirements(self):
        got = self._load({"devices/gm": {"requires": "nothere\n"},
                          "devices/gf": {"requires": "gbad\n"}, "transports/gbad": {"__init__.py": "raise ValueError('x')\n"},
                          "devices/ca": {"requires": "cb\n"}, "devices/cb": {"requires": "ca\n"}}, "gm,gf,ca")
        self.assertEqual(got, [])
        self.assertIn("nothere (not found)", app._PLUGIN_ERRORS["gm"])
        self.assertIn("gbad (failed to load)", app._PLUGIN_ERRORS["gf"])
        self.assertIn("circular", app._PLUGIN_ERRORS["ca"])
        self.assertIn("circular", app._PLUGIN_ERRORS["cb"])

    def test_every_member_of_a_tangled_circle_is_circular(self):
        self._load({"devices/ta": {"requires": "tb\ntc\n"}, "devices/tb": {"requires": "ta\n"},
                    "devices/tc": {"requires": "tb\n"}}, "ta")
        for n in ("ta", "tb", "tc"):
            self.assertIn("circular", app._PLUGIN_ERRORS[n], n)

    def test_a_bad_requires_keeps_only_that_plugin_off(self):
        got = self._load({"devices/gw": {"requires": "a b\n"}, "devices/gok": {"requires": "gtr\r\n"},
                          "transports/gtr": {}}, "gw,gok")
        self.assertEqual(got, ["gtr", "gok"])
        self.assertIn("not one plug-in name per line", app._PLUGIN_ERRORS["gw"])

    def test_an_unreadable_requires_does_not_stop_the_backend(self):
        d = pathlib.Path(tempfile.mkdtemp())
        _pkg(d, "devices/gbin", "")
        (d / "devices/gbin/requires").write_bytes(b"\xff\xfe\x00")
        self.assertEqual(app._load_plugins(d, top="t_grp_bin"), [])
        self.assertIn("requires", app._PLUGIN_ERRORS["gbin"])

    def test_a_missing_requirement_says_who_wanted_it(self):
        self._load({"devices/gwho": {"requires": "gnone\n"}}, "gwho")
        self.assertIn("required by gwho", app._PLUGIN_ERRORS["gnone"])

    def test_alphabetical_groups_with_underscore_and_inner_packages_skipped(self):
        got = self._load({"devices/zz_last": {}, "transports/aa_first": {}, "_hidden/nope": {},
                          "flat": {}, "flat/inner": {}}, "*")
        self.assertEqual(got, ["aa_first", "flat", "zz_last"])
        flat = next(m for m in app.PLUGINS if m["id"] == "flat")
        self.assertEqual((flat["group"], flat["required_by"]), (None, None))

    def test_resolver_details(self):
        lib = app.pluginlib
        d = pathlib.Path(tempfile.mkdtemp())
        for path, req in (("a/plugins/devices/dup", ""), ("a/extra/dup", ""), ("a/plugins/devices/me", "me\nme\ntx\ntx\n"),
                          ("a/plugins/transports/tx", ""), ("a/plugins/devices/Bad-Name", ""),
                          ("a/plugins/devices/needs_bad", "broken\n"), ("a/plugins/devices/broken", "x y\n")):
            _pkg(d, path, "", **({"requires": req} if req else {}))
        found, dup = lib.discover([d / "a/plugins", d / "a/extra"])
        self.assertEqual([q.name for q in found].count("dup"), 1)
        self.assertEqual([str(q.parent.name) for q, _ in dup], ["extra"])
        self.assertEqual(lib.read_requires(d / "a/plugins/devices/me"), ["tx"])
        p = lib.plan(found, {"me", "needs_bad"})
        self.assertIn("me", p["on"])
        self.assertIn("broken (off)", p["errors"]["needs_bad"])

    def test_required_by_and_the_marker_after_a_merge(self):
        self._load({"transports/rtx": {}, "devices/rdev": {"requires": "rtx\n"}}, "rdev")
        self.assertEqual(next(m for m in app.PLUGINS if m["id"] == "rtx")["required_by"], "rdev")
        old = app.PLUGIN_DATA / "hello" / "m.json"
        old.parent.mkdir(parents=True, exist_ok=True)
        (app.PLUGIN_STATE / "hello").mkdir(parents=True, exist_ok=True)
        (app.PLUGIN_STATE / "hello" / "m.json").write_text("a")
        old.write_text("b")
        app.plugin_state_file("hello", "m.json", merge=lambda x, y: x)
        self.assertTrue((old.parent / ".moved-m.json").exists())

    def test_a_name_is_taken_once_across_groups(self):
        got = self._load({"devices/gdup": {}, "transports/gdup": {}}, "gdup")
        self.assertEqual(got, ["gdup"])
        self.assertIn("gdup@extra", app._PLUGIN_ERRORS)


if __name__ == "__main__":
    unittest.main()
