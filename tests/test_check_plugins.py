"""tools/check_plugins.py on small trees: what it refuses and what it lets through."""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

CORE = pathlib.Path(__file__).resolve().parent.parent
RU = "".join(chr(c) for c in (0x43F, 0x440, 0x438, 0x432, 0x435, 0x442))   # a Russian word, built: no Cyrillic here


def run(files: dict, words: str = ""):
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        (d / "tools").mkdir()
        for f in ("check_plugins.py", "pluginlib.py"):
            shutil.copy(CORE / "tools" / f, d / "tools")
        for rel, body in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(body, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}   # a git hook's GIT_DIR: not here
        env["HOUSE_WORDS"] = words
        r = subprocess.run([sys.executable, "tools/check_plugins.py"], cwd=d, capture_output=True, text=True, env=env)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


OK = {"plugins/devices/fine/__init__.py": "x = 1\n"}


class CheckPlugins(unittest.TestCase):
    def test_a_clean_tree_passes(self):
        self.assertEqual(run(OK)[0], 0)

    def test_russian_only_in_i18n_or_marked_lines(self):
        rc, out = run({**OK, "plugins/devices/fine/ui.js": f"const a = '{RU}';\n"})
        self.assertEqual(rc, 1)
        self.assertIn("Russian outside i18n", out)
        self.assertEqual(run({**OK, "plugins/devices/fine/i18n.json": '{"ru": {"Hi": "' + RU + '"}}'})[0], 0)
        self.assertEqual(run({**OK, "tests/t.py": f"# i18n: a Russian name\nname = '{RU}'\n"})[0], 0)
        self.assertEqual(run({**OK, "tests/t.py": f"my_i18n_name = '{RU}'\n"})[0], 1)
        self.assertEqual(run({**OK, "tests/t.py": f"name = '{RU}'   # i18n: same line\n"})[0], 0)
        self.assertEqual(run({**OK, "plugins/devices/fine/ui.js": "const a = '" + "\\" + "u043f';   // i18n\n"})[0], 0)
        self.assertEqual(run({**OK, "plugins/devices/fine/sub/__init__.py": ""})[0], 0)          # not a marker
        self.assertEqual(run({**OK, "tests/t.py": "name = '" + "\\" + "u043f'\n"})[0], 1)        # escapes count

    def test_ascii_in_plug_ins_and_their_tests(self):
        rc, out = run({**OK, "plugins/devices/fine/ui.js": "const a = 'x\u2026';\n"})
        self.assertEqual(rc, 1)
        self.assertIn("non-ASCII", out)
        self.assertEqual(run({**OK, "plugins/devices/fine/ui.js": f"const a = '{RU}';   // i18n\n"})[0], 1)   # code: ASCII only
        self.assertEqual(run({**OK, "tests/plugins/t.py": f"say = '{RU}'   # i18n: a phrase\n"})[0], 0)      # a test: marked is fine
        self.assertEqual(run({**OK, "tests/plugins/t.py": f"# i18n: a phrase\nsay = '{RU}'\n"})[0], 0)
        self.assertEqual(run({**OK, "tests/plugins/t.py": "say = 'x\u2026'\n"})[0], 1)                     # unmarked: refused

    def test_group_folders_requires_and_names(self):
        rc, out = run({"plugins/widgets/w/__init__.py": ""})
        self.assertIn("is not one of", out)
        rc, out = run({**OK, "plugins/devices/fine/requires": "nothere\n"})
        self.assertEqual(rc, 1)
        self.assertIn("nothere", out)
        rc, out = run({**OK, "plugins/sources/fine/__init__.py": ""})
        self.assertIn("unique", out)

    def test_house_data(self):
        self.assertEqual(run({**OK, "plugins/devices/fine/x.py": "HOST = '" + ".".join(("10", "1", "2", "3")) + "'\n"})[0], 1)
        self.assertEqual(run({**OK, "plugins/devices/fine/x.py": "NAME = 'sample word'\n"}, words="sample word")[0], 1)
        self.assertEqual(run({**OK, "plugins/devices/fine/x.py": "HOST = '192.0.2.5'\n"})[0], 0)


if __name__ == "__main__":
    unittest.main()
