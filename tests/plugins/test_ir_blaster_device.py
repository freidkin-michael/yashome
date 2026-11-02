"""A blaster no device fronts is a device of its own, "blaster:<node>": listed, renamed through the device window (the
name is the blaster's, checked like any device name), its routes work with the id URL-encoded, online with its node,
gone with its blaster while its map icon stays, one map entry; an id another plug-in holds is a Health warning."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.ir"]
mp = sys.modules.get("yashome_plugins.map")
H = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


@pytest.fixture
def bl(monkeypatch):
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    monkeypatch.setattr(me, "_save", lambda: None)
    keep = list(me.BLASTERS)
    me.BLASTERS[:] = [{"node": "tb-1", "name": "Hall blaster", "rx": True}, {"node": "tb-2"}]
    me._node_status.clear()
    me._sync()
    yield TestClient(core.app)
    me.BLASTERS[:] = keep
    for k in ("tb-3", "blaster:tb-9"):
        core.DEVICES.pop(k, None)
    me._sync()


def test_a_bare_blaster_is_a_device_and_a_fronted_one_is_not(bl):
    d = core.DEVICES["blaster:tb-1"]
    assert (d["name"], d["ir_blaster"], d["transport"], d["category"]) == ("Hall blaster", "tb-1", "ir", "ir")
    assert core.DEVICES["blaster:tb-2"]["name"] == "tb-2"
    core.DEVICES["tb-3"] = {"id": "tb-3", "name": "Bedroom IR", "transport": "mqtt", "ir_blaster": "tb-3", "controls": [], "_ctl": {}}
    me.BLASTERS.append({"node": "tb-3"})
    me._sync()
    assert "blaster:tb-3" not in core.DEVICES and me._blaster_device("tb-3") == "tb-3"
    assert me._blaster_device("tb-1") is None                         # its own device fronts nothing


def test_its_routes_work_with_the_id_url_encoded(bl):
    c, enc = bl, "blaster%3Atb-1"
    assert c.get(f"/api/devices/{enc}/status", headers=H).status_code == 200
    assert c.get(f"/api/devices/{enc}/rules", headers=H).status_code == 200
    assert any(x["id"] == "blaster:tb-1" for x in c.get("/api/devices", headers=H).json())
    r = c.post(f"/api/devices/{enc}/rename", headers=H, json={"name": " Детская ИК "})   # i18n
    assert r.status_code == 200 and me._blaster("tb-1")["name"] == "Детская ИК" == core.DEVICES["blaster:tb-1"]["name"]   # i18n
    r = c.post("/api/devices/blaster%3Atb-2/rename", headers=H, json={"name": "детская ик"})   # i18n
    assert r.status_code == 409                                          # a device name, checked like any other
    assert core._clean_room_devices(["blaster:tb-1"]) == ["blaster:tb-1"]      # a room may hold it


def test_it_is_online_with_its_node(bl):
    me._node_status["tb-1"] = True
    me._blaster_status("blaster:tb-1")
    assert core.status_copy("blaster:tb-1", {}).get("online") is True
    me._node_status["tb-1"] = False
    me._blaster_status("blaster:tb-1")
    assert core.status_copy("blaster:tb-1", {}).get("online") is False


def test_it_goes_with_its_blaster_and_the_map_icon_stays(bl, monkeypatch):
    forgot = []
    if mp is not None:
        monkeypatch.setattr(mp, "forget_thing", lambda dev: forgot.append(dev) or 1)
    me.ir_blaster_del("tb-2")
    assert "blaster:tb-2" not in core.DEVICES and forgot == []


def test_an_id_another_plugin_holds_is_a_health_warning(bl):
    core.DEVICES["blaster:tb-9"] = {"id": "blaster:tb-9", "name": "x", "transport": "mqtt", "controls": [], "_ctl": {}}
    me.BLASTERS.append({"node": "tb-9"})
    me._sync()
    assert core.DEVICES["blaster:tb-9"]["transport"] == "mqtt"
    assert "ir:blasterid:blaster:tb-9" in [r["id"] for r in me._health()]


@pytest.mark.skipif(mp is None, reason="no map plug-in")
def test_the_map_lists_it_once_as_a_blaster(bl):
    got = [t for t in mp._things() if t["id"] == "blaster:tb-1"]
    assert len(got) == 1 and got[0]["category"] == "blaster" and got[0]["name"] == "Hall blaster"
