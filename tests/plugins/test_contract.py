"""What every plug-in of this repo must satisfy against the pinned core."""
import pathlib
import shutil
import tempfile

import app
import conftest

PLUGINS = conftest.CORE / "plugins"
NAMES = sorted(p.name for p in PLUGINS.iterdir() if (p / "__init__.py").is_file() and not p.name.startswith("_"))


def test_every_plugin_loads():
    assert conftest.LOADED == NAMES
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
                if (PLUGINS / n / f).is_file():
                    assert app.plugin_asset(n, f).path == PLUGINS / n / f
    finally:
        app.PLUGINS_DIR = keep


def test_the_example_still_loads():
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        shutil.copytree(PLUGINS / "_example", d / "hello")
        assert app._load_plugins(d, top="yashome_example") == ["hello"]
    finally:
        shutil.rmtree(d, ignore_errors=True)
