"""tools/envlib.sh: which plug-in directories install/update/secrets act on (group folders, requires)."""
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

CORE = pathlib.Path(__file__).resolve().parent.parent


def _tree(d, layout):
    for path, req in layout.items():
        p = d / path
        p.mkdir(parents=True)
        (p / "__init__.py").write_text("")
        if req:
            (p / "requires").write_text(req)


def _dirs(d, plugins, fn="plugin_dirs"):
    (d / ".env").write_text(f"PLUGINS={plugins}\n")
    out = subprocess.run(["sh", "-c", f". tools/envlib.sh; {fn}"], cwd=d, capture_output=True, text=True, check=True)
    return out.stdout.split()


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class PluginDirs(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        (self.d / "tools").mkdir()
        shutil.copy(CORE / "tools" / "envlib.sh", self.d / "tools")
        shutil.copy(CORE / "tools" / "pluginlib.py", self.d / "tools")
        _tree(self.d, {"plugins/transports/tx": "", "plugins/devices/dev": "tx  # its transport\n",
                       "plugins/devices/top": "dev\n", "plugins/sources/off": "", "plugins/_example": "",
                       "plugins/devices/dev/sub": "", "plugins-extra/mine": "tx\n"})

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_every_plugin_flat_or_grouped(self):
        got = {os.path.basename(p) for p in _dirs(self.d, "", "plugin_all")}
        self.assertEqual(got, {"tx", "dev", "top", "off", "mine"})       # not _example, not a sub-package

    def test_requires_are_switched_on_transitively(self):
        self.assertEqual(sorted(os.path.basename(p) for p in _dirs(self.d, "top")), ["dev", "top", "tx"])
        self.assertEqual(sorted(os.path.basename(p) for p in _dirs(self.d, "mine")), ["mine", "tx"])
        self.assertEqual(_dirs(self.d, ""), [])

    def test_star_is_everything(self):
        self.assertEqual(len(_dirs(self.d, "*")), 5)


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class ShellAgreesWithBackend(unittest.TestCase):
    """install/update (envlib.sh, with and without python3) switch on exactly what the backend loads."""

    def test_same_set(self):
        import sys
        sys.path.insert(0, str(CORE / "tests"))
        import test_pure  # noqa: F401  (a private HOME_STACK_ROOT before app is imported)
        import app
        d = pathlib.Path(tempfile.mkdtemp())
        shutil.copytree(CORE / "tools", d / "tools")
        _tree(d, {"plugins/transports/tx": "", "plugins/devices/crlf": "tx\r\n# comment\r\n",
                  "plugins/devices/twoword": "tx ty\n", "plugins/devices/self": "self\ntx\n",
                  "plugins/devices/needsbad": "twoword\n", "plugins/devices/needsnone": "nothere\n",
                  "plugins/devices/longreq": "a" * 40 + "\n", "plugins/devices/ca": "cb\n", "plugins/devices/cb": "cc\n",
                  "plugins/devices/cc": "ca\n", "plugins/sources/off": "", "plugins/sources/nonl": "tx",
                  "plugins/_hidden/inner": "", "_x/mine": "crlf\n", "_x/grp/inner2": ""})
        (d / "plugins/devices/bin").mkdir()
        (d / "plugins/devices/bin/__init__.py").write_text("")
        (d / "plugins/devices/bin/requires").write_bytes(b"\xff\xfe")
        cases = ("mine", "twoword,self", "bin,off", "nonl", "needsbad,needsnone,longreq", "ca", "*")
        for plugins in cases:
            (d / ".env").write_text(f"PLUGINS={plugins}\nPLUGINS_EXTRA_DIR=./_x\n")
            got = {}
            for fn in ("plugin_dirs",):
                out = subprocess.run(["sh", "-c", f". tools/envlib.sh; {fn}"], cwd=d, capture_output=True,
                                     text=True, check=True).stdout.split()
                got[fn] = sorted(os.path.basename(x) for x in out)
            keep = app.PLUGINS_DIR, app.PLUGINS_EXTRA, os.environ.get("PLUGINS")
            app.PLUGINS_DIR, app.PLUGINS_EXTRA, os.environ["PLUGINS"] = d / "plugins", d / "_x", plugins
            try:
                loaded = sorted(app._load_plugins(top="t_parity_" + re.sub(r"\W", "_", plugins)))
            finally:
                app.PLUGINS_DIR, app.PLUGINS_EXTRA = keep[0], keep[1]
                os.environ.pop("PLUGINS") if keep[2] is None else os.environ.__setitem__("PLUGINS", keep[2])
            self.assertEqual(got["plugin_dirs"], loaded, plugins)
        self.assertIn("circular", app._PLUGIN_ERRORS["cb"])
        shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class HouseDir(unittest.TestCase):
    def test_default_here_and_no_odd_characters(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "tools").mkdir()
        shutil.copy(CORE / "tools" / "envlib.sh", d / "tools")
        for val, want in (("", "."), ("../my-house", "../my-house"), ("'../q'", "../q"), ("a b", None), ("x:y", None),
                          ("~/h", None)):
            (d / ".env").write_text(f"HOUSE_DIR={val}\n")
            r = subprocess.run(["sh", "-c", ". tools/envlib.sh; house_dir"], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.stdout.strip() if r.returncode == 0 else None, want, val)
        shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class MoveHouse(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        (self.d / "tools").mkdir()
        (self.d / "examples").mkdir()
        for f in ("envlib.sh", "move_house.sh"):
            shutil.copy(CORE / "tools" / f, self.d / "tools")
        shutil.copy(CORE / "examples" / "house.gitignore", self.d / "examples")
        for f in ("settings.json", "mqtt_devices.json", "status_cache.json", "plugin-data/ir/settings.json",
                  "plugin-data/zigbee/zigbee2mqtt/database.db", "plugin-state/nfc/nfc_counters.json"):
            (self.d / f).parent.mkdir(parents=True, exist_ok=True)
            (self.d / f).write_text("{}")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def sh(self, cmd, docker="exit 0"):
        b = self.d / "bin"
        b.mkdir(exist_ok=True)
        (b / "docker").write_text(f"#!/bin/sh\n{docker}\n")       # a stand-in: the backend is stopped
        (b / "docker").chmod(0o755)
        env = {**os.environ, "PATH": f"{b}:{os.environ['PATH']}"}
        return subprocess.run(["sh", "-c", cmd], cwd=self.d, capture_output=True, text=True, env=env)

    def test_move_house_refuses_when_docker_cannot_tell(self):
        (self.d / ".env").write_text("HOUSE_DIR=./h\n")
        r = self.sh("sh tools/move_house.sh", docker="exit 1")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.d / "settings.json").is_file())

    def test_an_empty_house_dir_over_a_house_is_refused(self):
        (self.d / ".env").write_text("HOUSE_DIR=../h2\n")
        self.assertNotEqual(self.sh(". tools/envlib.sh; house_check").returncode, 0)
        (self.d / ".env").write_text("HOUSE_DIR=\n")
        self.assertEqual(self.sh(". tools/envlib.sh; house_check").returncode, 0)

    def test_an_existing_house_keeps_its_broker_rights(self):
        (self.d / ".env").write_text("MQTT_USER=home\n")
        self.assertEqual(self.sh(". tools/envlib.sh; backend_only_keep").returncode, 0)
        self.assertIn("MQTT_BACKEND_ONLY=\n", (self.d / ".env").read_text())
        for kept in ("MQTT_BACKEND_ONLY=x/#\n", "export MQTT_BACKEND_ONLY=x/#\n", "  MQTT_BACKEND_ONLY = x/#\n"):
            (self.d / ".env").write_text(kept)
            self.sh(". tools/envlib.sh; backend_only_keep")
            self.assertEqual((self.d / ".env").read_text(), kept)
        (self.d / ".env").write_text("MQTT_PASSWORD=pw1")                   # no newline at the end
        self.sh(". tools/envlib.sh; backend_only_keep")
        self.assertEqual(self.sh(". tools/envlib.sh; get MQTT_PASSWORD").stdout.strip(), "pw1")
        self.assertEqual(self.sh(". tools/envlib.sh; get MQTT_BACKEND_ONLY").stdout.strip(), "")

    def test_a_new_install_has_backend_only_commands(self):
        self.assertIn("\nMQTT_BACKEND_ONLY=home/cmd/#\n", (CORE / "secrets.env.example").read_text())

    def test_an_extra_plugins_dir_is_written_out(self):
        for bad in ("~/x", "a b", "a:b", "$HOME/x"):
            (self.d / ".env").write_text(f"PLUGINS_EXTRA_DIR={bad}\n")
            self.assertNotEqual(self.sh(". tools/envlib.sh; plugin_args").returncode, 0, bad)
        (self.d / ".env").write_text("PLUGINS_EXTRA_DIR=/srv/mine\n")
        self.assertEqual(self.sh(". tools/envlib.sh; plugin_args").stdout.split(), ["./plugins", "/srv/mine"])

    def test_move_house_moves_all_but_the_zigbee_network(self):
        (self.d / ".env").write_text("HOUSE_DIR=./h\nZ2M_DATA_DIR=./plugin-data/zigbee/zigbee2mqtt\n")
        r = self.sh("sh tools/move_house.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = self.d / "h"
        for f in ("settings.json", "mqtt_devices.json", "status_cache.json", "plugin-data/ir/settings.json",
                  "plugin-state/nfc/nfc_counters.json", ".gitignore"):
            self.assertTrue((h / f).is_file(), f)
        self.assertTrue((self.d / "plugin-data/zigbee/zigbee2mqtt/database.db").is_file())
        self.assertFalse((self.d / "settings.json").exists())
        self.assertEqual(self.sh(". tools/envlib.sh; house_check").returncode, 0)

    def test_an_empty_z2m_data_dir_means_the_default_place_and_stays(self):
        (self.d / ".env").write_text("HOUSE_DIR=./h\n")
        self.assertEqual(self.sh("sh tools/move_house.sh").returncode, 0)
        self.assertTrue((self.d / "plugin-data/zigbee/zigbee2mqtt/database.db").is_file())
        self.assertTrue((self.d / "h/settings.json").is_file())

    def test_the_checkout_under_another_name_is_here(self):
        for val in ("./", str(self.d)):
            (self.d / ".env").write_text(f"HOUSE_DIR={val}\n")
            self.assertEqual(self.sh(". tools/envlib.sh; house_check").returncode, 0, val)

    def test_move_house_into_a_house_a_backend_left_empty_folders_in(self):
        (self.d / ".env").write_text("HOUSE_DIR=./h\n")
        for d in ("h/plugin-data/ir", "h/plugin-state/nfc"):
            (self.d / d).mkdir(parents=True)
        r = self.sh("sh tools/move_house.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.d / "h/plugin-state/nfc/nfc_counters.json").is_file())

    def test_a_populated_house_dir_is_fine_and_move_house_needs_one(self):
        (self.d / ".env").write_text("HOUSE_DIR=./h\n")
        (self.d / "h").mkdir()
        (self.d / "h/settings.json").write_text("{}")
        self.assertEqual(self.sh(". tools/envlib.sh; house_check").returncode, 0)
        (self.d / ".env").write_text("HOUSE_DIR=\n")
        self.assertNotEqual(self.sh("sh tools/move_house.sh").returncode, 0)

    def test_move_house_refuses_when_the_stop_fails_and_moves_the_aside_copies(self):
        (self.d / ".env").write_text("HOUSE_DIR=./h\n")
        r = self.sh("sh tools/move_house.sh", docker='case "$*" in *stop*) exit 1;; esac; exit 0')
        self.assertNotEqual(r.returncode, 0)
        r = self.sh("sh tools/move_house.sh", docker='case "$*" in *running*) echo abc;; esac; exit 0')
        self.assertNotEqual(r.returncode, 0)
        (self.d / "settings.json.pre-split-1").write_text("{}")
        self.assertEqual(self.sh("sh tools/move_house.sh").returncode, 0)
        self.assertTrue((self.d / "h/settings.json.pre-split-1").is_file())

    def test_a_house_with_only_devices_is_a_house(self):
        (self.d / "settings.json").unlink()
        (self.d / ".env").write_text("HOUSE_DIR=../elsewhere\n")
        self.assertNotEqual(self.sh(". tools/envlib.sh; house_check").returncode, 0)

    def test_install_and_update_check_the_house_before_compose_mounts_it(self):
        for f in ("install.sh", "secrets.sh"):
            text = (CORE / "tools" / f).read_text()
            self.assertIn("house_check || exit 1", text, f)
            self.assertLess(text.index("house_check || exit 1"), text.index("docker compose"), f)

    def test_move_house_overwrites_nothing(self):
        (self.d / ".env").write_text("HOUSE_DIR=./h\n")
        (self.d / "h").mkdir()
        (self.d / "h/settings.json").write_text('{"keep": 1}')
        self.assertNotEqual(self.sh("sh tools/move_house.sh").returncode, 0)
        self.assertEqual((self.d / "h/settings.json").read_text(), '{"keep": 1}')
        self.assertTrue((self.d / "settings.json").is_file())



class EnvKeysDocumented(unittest.TestCase):
    """Scans *.py, *.sh, *.yml, *.service, helper/ and yashctl; Makefile, Dockerfiles and *.kts are not read."""
    # read through a variable or a built name, which the patterns cannot see
    INDIRECT = {"PANELS_OTA_PASSWORD", "PANELS_AP_PASSWORD", "NFC_SDM_META_KEY", "NFC_SDM_FILE_KEY",
                "HOUSE_WIFI_SSID", "HOUSE_WIFI_PASSWORD", "MQTT_DEVICE_USERS"}
    # GIT_TERMINAL_PROMPT: set for git (helper.py), never read; the env[...] pattern sees assignments too
    SYSTEM = {"APP_ID", "GIT_TERMINAL_PROMPT", "HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "RUNTIME_DIRECTORY", "STATE_DIRECTORY",
              "SUDO_USER"}

    def test_every_key_the_code_reads_is_named_in_the_docs(self):
        import re
        import subprocess as sp
        ls = lambda *pats: sp.run(["git", "ls-files", *pats], cwd=CORE, capture_output=True, text=True).stdout.split()
        pats = [r'(?:environ\.get|env\.get|getenv|\benv)\(\s*["\']([A-Z][A-Z0-9_]+)["\']', r'(?:environ|\benv)\[\s*["\']([A-Z][A-Z0-9_]+)["\']\s*\]',
                r'\$\{([A-Z][A-Z0-9_]+)[:}-]', r'\bget ([A-Z][A-Z0-9_]+)\b']
        keys = set()
        for f in ls("*.py", "*.sh", "*.yml", "*.service", "helper/*", "tools/yashctl"):
            if not f.startswith("tests/"):
                t = (CORE / f).read_text(errors="replace")
                keys |= {k for p in pats for k in re.findall(p, t)}
        self.assertGreater(len(keys), 100, "too few keys found: not a git checkout?")
        keys |= self.INDIRECT
        docs = "".join((CORE / f).read_text(errors="replace") for f in ls("*.example", "*.md"))
        missing = sorted(k for k in keys - self.SYSTEM if not re.search(r"\b" + k + r"\b", docs))
        self.assertEqual(missing, [], "read by the code, named in no documentation")


if __name__ == "__main__":
    unittest.main()

