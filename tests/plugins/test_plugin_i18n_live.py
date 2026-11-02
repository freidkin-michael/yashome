"""A plug-in's dictionary is the i18n.json as it is now: a text added after the start shows after a reload, no restart;
a file that does not read keeps the last good one."""
import json
import os
import shutil

from fastapi.testclient import TestClient

import app as core

HOUSE = {"Authorization": "Bearer " + core.DASHBOARD_TOKEN}


def _ru(c):
    return next(p for p in c.get("/api/plugins", headers=HOUSE).json()["loaded"] if p["id"] == "map")["i18n"]["ru"]


def test_a_text_added_after_the_start_is_served_and_a_broken_file_keeps_the_last_good_one(tmp_path, monkeypatch):
    real = core.PLUGIN_PATHS["map"]
    for f in real.iterdir():
        if f.is_file():
            shutil.copy2(f, tmp_path / f.name)
    monkeypatch.setitem(core.PLUGIN_PATHS, "map", tmp_path)
    monkeypatch.setattr(core, "_I18N_SEEN", {})
    c = TestClient(core.app)
    f = tmp_path / "i18n.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    assert "A word added later" not in _ru(c)
    d["ru"]["A word added later"] = "Слово, добавленное позже"   # i18n
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    t = f.stat()
    os.utime(f, ns=(t.st_atime_ns, t.st_mtime_ns + 10**9))
    assert _ru(c)["A word added later"] == "Слово, добавленное позже"                     # no restart   # i18n
    assert all("A word added later" not in (m.get("i18n") or {}).get("ru", {}) for m in core.PLUGINS)    # the start copy is not changed
    f.write_text("{broken", encoding="utf-8")
    os.utime(f, ns=(t.st_atime_ns, t.st_mtime_ns + 2 * 10**9))
    assert _ru(c)["A word added later"] == "Слово, добавленное позже"                     # the last good one stays   # i18n
