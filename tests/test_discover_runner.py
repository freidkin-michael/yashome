"""discover/runner.py: the core's discover container runs the enabled plug-ins' discovery modules, each apart."""
import importlib.util
import pathlib
import shutil
import tempfile
import threading
import time
import unittest

CORE = pathlib.Path(__file__).resolve().parent.parent


def _runner():
    spec = importlib.util.spec_from_file_location("yashome_discover_runner", CORE / "discover" / "runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _runner()

GOOD = """
def run(ctx):
    n = 0
    while not ctx.stop.is_set():
        n += 1
        (ctx.state / "beat").write_text(str(n))
        if ctx.wait(0.02):
            return
"""
BOOM = """
import pathlib
def run(ctx):
    f = ctx.state / "starts"
    f.write_text(str(int(f.read_text()) + 1 if f.exists() else 1))
    raise RuntimeError("boom")
"""


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Runner(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.base = self.d / "plugins"
        self.state = self.d / "state"
        self.state.mkdir()
        self._pause = (R.PAUSE_MIN, R.PAUSE_MAX)
        R.PAUSE_MIN, R.PAUSE_MAX = 0.02, 0.05

    def tearDown(self):
        R.PAUSE_MIN, R.PAUSE_MAX = self._pause
        shutil.rmtree(self.d, ignore_errors=True)

    def _plugin(self, path, module=None, requires=""):
        p = self.base / path
        p.mkdir(parents=True)
        (p / "__init__.py").write_text("")
        if module is not None:
            (p / "discover.py").write_text(module)
        if requires:
            (p / "requires").write_text(requires)

    def test_a_module_registers_by_its_file_and_only_when_its_plugin_is_on(self):
        self._plugin("tools/aa", GOOD)
        self._plugin("devices/bb", GOOD, requires="cc\n")
        self._plugin("cc", GOOD)
        self._plugin("dd")                                              # on, but no module
        self._plugin("map")                                             # always on, no module
        names = lambda plugins: [n for n, _ in R.modules(plugins, [self.base])]      # noqa: E731
        self.assertEqual(names("aa,dd"), ["aa"])
        self.assertEqual(names("bb"), ["cc", "bb"])                     # a requirement comes in, first
        self.assertEqual(names(""), [])                                 # none on: the runner idles
        self.assertEqual(names("*"), ["aa", "cc", "bb"])
        self.assertEqual(R.modules("aa", [self.base])[0][1], self.base / "tools" / "aa" / "discover.py")

    def test_a_crashing_module_is_restarted_and_does_not_stop_another(self):
        self._plugin("good", GOOD)
        self._plugin("bad", BOOM)
        stop = threading.Event()
        threads = R.start(R.modules("good,bad", [self.base]), stop, self.state)
        try:
            self.assertTrue(_wait(lambda: (self.state / "bad" / "starts").exists()
                                  and int((self.state / "bad" / "starts").read_text()) >= 3))
            beat = int((self.state / "good" / "beat").read_text())
            self.assertTrue(_wait(lambda: int((self.state / "good" / "beat").read_text()) > beat + 3))
        finally:
            stop.set()
        for t in threads:
            t.join(2)
            self.assertFalse(t.is_alive())

    def test_a_module_without_run_is_logged_and_retried(self):
        self._plugin("norun", "X = 1\n")
        with self.assertRaises(TypeError):
            R.load("norun", self.base / "norun" / "discover.py")
        stop = threading.Event()
        t = R.start(R.modules("norun", [self.base]), stop, self.state)[0]
        time.sleep(0.1)
        self.assertTrue(t.is_alive())
        stop.set()
        t.join(2)
        self.assertFalse(t.is_alive())


class Compose(unittest.TestCase):
    def test_discover_is_one_core_service_in_the_host_network_without_privileges(self):
        text = (CORE / "docker-compose.yml").read_text()
        block = text.split("\n  discover:\n", 1)[1].split("\n  mosquitto:\n", 1)[0]
        for want in ("network_mode: host", "read_only: true", "cap_drop: [ALL]", "/app/discover/runner.py",
                     "PLUGINS=${PLUGINS:-}", "./plugins:/app/plugins:ro"):
            self.assertIn(want, block)
        self.assertNotIn("docker.sock", block)
        self.assertNotIn("privileged", block)
        for f in CORE.glob("plugins/**/setup/compose.yml"):
            self.assertNotIn("network_mode: host", f.read_text(), f)  # a host-network part is a discovery module


if __name__ == "__main__":
    unittest.main()
