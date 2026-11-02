"""One physical thing, one device: a part's channels go to its transport with its cfg (also while the device is
offline), a shared code falls back to a part that maps it, the merge plan writes nothing, a merge moves every
reference after a backup and keeps the old id as an alias."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = "test-token-parts-0123456789"
H = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def house(monkeypatch):
    monkeypatch.setattr(app, "DASHBOARD_TOKEN", TOKEN)
    monkeypatch.setattr(app, "_save_settings", lambda: None)
    sent, pressed = [], []
    monkeypatch.setitem(app.TRANSPORTS, "nettv", lambda cfg, code, val, kind: sent.append((cfg["id"], code, val)))
    monkeypatch.setitem(app.CONTROL_KINDS, "test_press", lambda cfg, ctl, val: pressed.append((cfg.get("blaster"), ctl["code"])) or {"ok": True})
    for did in ("tv1", "tv1_ir"):
        monkeypatch.setitem(app.DEVICES, did, None)
    tv = {"id": "tv1", "name": "TV", "transport": "nettv", "category": "tv",
          "controls": [{"kind": "switch", "code": "power"}, {"kind": "bright", "code": "volume", "min": 0, "max": 100}]}
    app.DEVICES["tv1"] = app._plugin_device(tv)
    app.DEVICES["tv1_ir"] = app._plugin_device({"id": "tv1_ir", "name": "TV IR", "transport": "nettv", "category": "ir",
                                                 "controls": [{"kind": "test_press", "code": "ir_menu"}]})
    app._PLUGIN_DEVICES.update({"tv1", "tv1_ir"})
    monkeypatch.setattr(app, "PARTS", {})
    monkeypatch.setattr(app, "_aliases", {})
    monkeypatch.setattr(app, "_favorites", [{"device": "tv1_ir", "code": "ir_menu"}])
    yield {"sent": sent, "pressed": pressed, "tv": tv}
    for did in ("tv1", "tv1_ir"):
        app.DEVICES.pop(did, None)
        app._PLUGIN_DEVICES.discard(did)
        app._status_cache.pop(did, None)


def test_a_parts_channels_join_the_device_and_go_to_the_parts_transport_even_while_it_is_offline(house, monkeypatch):
    app.extend_device("tv1", "ir", [{"kind": "test_press", "code": "ir_menu"}, {"kind": "test_press", "code": "ir_on"}],
                      "irtest", info={"remote": "tv_remote"}, cfg={"blaster": "b1"}, map={"power": {"true": "ir_on"}})
    d = app.DEVICES["tv1"]
    assert [c["code"] for c in d["controls"]] == ["power", "volume", "ir_menu", "ir_on"]
    assert d["parts"] == {"ir": {"transport": "irtest", "remote": "tv_remote"}} and d["_ctl"]["ir_menu"]["part"] == "ir"
    app.DEVICES["tv1"] = app._plugin_device(house["tv"])                          # a new registration keeps its parts
    assert "ir_menu" in app.DEVICES["tv1"]["_ctl"]
    monkeypatch.setattr(app, "_is_offline", lambda did: True)
    c = TestClient(app.app)
    assert c.post("/api/devices/tv1/control", headers=H, json={"code": "ir_menu", "value": True}).status_code == 200
    assert house["pressed"] == [("b1", "ir_menu")]                               # with the part's cfg, the TV asleep
    r = c.post("/api/devices/tv1/control", headers=H, json={"code": "power", "value": True}).json()
    assert r["via"] == "ir" and house["pressed"][-1] == ("b1", "ir_on") and house["sent"] == []     # the fallback
    assert c.post("/api/devices/tv1/control", headers=H, json={"code": "volume", "value": 10}).status_code == 409   # nothing maps it
    app.drop_part("tv1", "ir")
    assert "ir_menu" not in app.DEVICES["tv1"]["_ctl"]


def test_the_merge_plan_writes_nothing_and_the_merge_moves_everything_after_a_backup(house, monkeypatch):
    c = TestClient(app.app)
    before = copy.deepcopy(app._favorites)
    plan = c.get("/api/devices/merge-plan", params={"old": "tv1_ir", "new": "tv1"}, headers=H).json()
    assert plan["moves"].get("favorites") == 1 and plan["codes_only_old"] == ["ir_menu"] and app._favorites == before
    assert c.get("/api/devices/merge-plan", params={"old": "tv1", "new": "tv1"}, headers=H).status_code == 400
    assert c.post("/api/devices/merge", json={"old": "tv1_ir", "new": "tv1"}).status_code in (401, 403)
    monkeypatch.setattr(app, "backup_now", lambda light=False: {"ok": False, "error": "disk full"})
    assert c.post("/api/devices/merge", headers=H, json={"old": "tv1_ir", "new": "tv1"}).status_code == 503       # no backup, no merge
    assert "tv1_ir" in app.DEVICES
    light = []
    monkeypatch.setattr(app, "backup_now", lambda light_=False, **k: light.append(k.get("light", light_)) or {"ok": True, "name": "yashome-backup-x.tar.gz"})
    told = []
    monkeypatch.setattr(app, "DEVICE_MERGED", [lambda old, new: told.append((old, new))])
    r = c.post("/api/devices/merge", headers=H, json={"old": "tv1_ir", "new": "tv1"}).json()
    assert r["backup"] == "yashome-backup-x.tar.gz" and told == [("tv1_ir", "tv1")]
    assert app._favorites == [{"device": "tv1", "code": "ir_menu"}] and "tv1_ir" not in app.DEVICES
    assert app.aliases("tv1") == ["tv1_ir"] and app.merged("tv1_ir") == "tv1" and light == [True]       # a light backup
    assert next(d for d in c.get("/api/devices", headers=H).json() if d["id"] == "tv1")["aliases"] == ["tv1_ir"]
