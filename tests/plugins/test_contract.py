"""What every plug-in of this repo must satisfy against the pinned core."""
import json
import pathlib
import shutil
import tempfile

import app
import conftest

PLUGINS = conftest.CORE / "plugins"
# plugins/<group>/<name>/ (a group folder has no __init__.py) or plugins/<name>/
DIRS = {q.name: q for r in app._plugin_roots(PLUGINS) for q in r.iterdir()
        if (q / "__init__.py").is_file() and not q.name.startswith("_")}
NAMES = sorted(DIRS)


def test_every_plugin_loads():
    assert sorted(conftest.LOADED) == NAMES
    for m in app.PLUGINS:                       # a requirement is loaded before what requires it
        if m["id"] in conftest.LOADED:
            assert all(conftest.LOADED.index(d) < conftest.LOADED.index(m["id"]) for d in m["requires"]), m["id"]
    assert not {n: e for n, e in app._PLUGIN_ERRORS.items() if n in NAMES}


def test_every_plugin_says_what_it_is():
    for n in NAMES:
        meta = next(m for m in app.PLUGINS if m["id"] == n)
        assert meta.get("name") and meta.get("version"), n


def test_public_files_are_served():
    keep = app.PLUGINS_DIR
    app.PLUGINS_DIR = PLUGINS
    try:
        for n in NAMES:
            for f in ("ui.js", "i18n.json"):
                if (DIRS[n] / f).is_file():
                    assert app.plugin_asset(n, f).path == DIRS[n] / f
    finally:
        app.PLUGINS_DIR = keep


def test_every_plugin_sits_in_a_group():
    assert all(q.parent != PLUGINS for q in DIRS.values()), "plugins/<group>/<name>/: transports, devices, sources, tools"


def test_the_example_still_loads():
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        shutil.copytree(PLUGINS / "_example", d / "hello")
        assert app._load_plugins(d, top="yashome_example") == ["hello"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_settings_example_shows_every_section_of_the_plugin():
    for n in NAMES:
        owned = sorted(k for k, owner in app._SECTION_FILE.items() if owner == n)
        ex = DIRS[n] / "setup" / "settings.example.json"
        if owned:
            assert ex.is_file(), f"{n}: sections {owned} but no setup/settings.example.json"
        if ex.is_file():
            assert sorted(json.loads(ex.read_text(encoding="utf-8"))) == owned, n


def test_every_example_file_parses():
    for q in DIRS.values():
        for f in sorted((q / "setup").glob("*.example.json")):
            json.loads(f.read_text(encoding="utf-8"))


EXAMPLES_RUN = """
import json, os, pathlib, sys
sys.path.insert(0, os.environ["CORE"])
import app
loaded = app._load_plugins(pathlib.Path(os.environ["CORE"]) / "plugins", top="t_examples_boot")
ir = sys.modules["t_examples_boot.ir"]
refs = []
for a in ir.APPLIANCES:
    refs.append(("appliance", a["id"], ir._remote(a["remote"]) is not None and ir._blaster(a["blaster"]) is not None))
for t in ir.TOGGLES:
    r = ir._remote(t["power"]["remote"])
    refs.append(("toggle", t["id"], bool(r) and any(b["id"] == t["power"]["button"] for b in r["buttons"])
                 and ir._blaster(t["power"]["via"]) is not None))
for a in ir.APPLIANCES:
    refs.append(("appliance card", a["id"], a.get("device") in app.DEVICES))
for t in ir.TOGGLES:
    refs.append(("probe port", t["id"], isinstance(t["probe"].get("port"), int)))
known = {"switch", "bright", "sensor", "sensor_text", "setting_enum", "setting_int", "setting_bool",
         "trigger_text"} | set(app.CONTROL_KINDS)
refs += [("control kind", f"{d['id']}/{c['code']}", c.get("kind") in known) for d in app.DEVICES.values()
         for c in d.get("controls", [])]
cam = sys.modules["t_examples_boot.cameras"]
web = sys.modules["t_examples_boot.webos"]
print(json.dumps({"loaded": loaded, "errors": app._PLUGIN_ERRORS, "devices": sorted(app.DEVICES), "refs": refs,
                  "cameras": cam.list_cameras(), "camera_1": cam.CAMERAS.get("camera_1"),
                  "tv_host": web._webos_host(app.DEVICES["tv_1"]), "climate": sys.modules["t_examples_boot.climate"]._from}))
"""


def test_the_examples_load_through_their_plugins(tmp_path):
    """Every setup/*.example.json copied into a house as the file it shows: the plug-ins load it, the
    devices appear and every reference between the examples resolves."""
    import json
    import os
    import subprocess
    import sys
    shutil.copytree(conftest.CORE / "tests" / "fixtures", tmp_path, dirs_exist_ok=True)
    for name, q in DIRS.items():
        for f in sorted((q / "setup").glob("*.example.json")):
            dst = tmp_path / "plugin-data" / name / f.name.replace(".example", "")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(f, dst)
    env = {**os.environ, "HOME_STACK_ROOT": str(tmp_path), "CORE": str(conftest.CORE), "PLUGINS": "",
           "CAMERA_1_PASS": "from-env"}
    env.pop("HOUSE_ROOT", None)
    out = subprocess.run([sys.executable, "-c", EXAMPLES_RUN], env=env, capture_output=True, text=True, check=True)
    r = json.loads(out.stdout.splitlines()[-1])
    assert sorted(r["loaded"]) == NAMES and not r["errors"]
    assert {"tv_1", "tv_1_ir", "box_1"} <= set(r["devices"])
    assert r["refs"] and all(ok for _, _, ok in r["refs"]), r["refs"]
    assert [c["id"] for c in r["cameras"]] == ["camera_1"]
    assert r["camera_1"]["pass"] == "from-env" and r["camera_1"]["snapshot"] and r["camera_1"]["stream"]
    assert r["tv_host"] == "192.0.2.41"                     # webos_hosts.json wins over the configured host
    assert r["climate"] == {"ac_1": "sensor_1"}
