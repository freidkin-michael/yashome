"""The core's room routes hand over to the map (ROOMS_OWNER): a room made on the dashboard is a room of the map, a rename renames its
outline, a delete takes the outline too; what still names a room is told before (GET .../refs, which changes nothing); the map is always on."""
import json
import sys

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.map"]
HOUSE = {"Authorization": "Bearer " + core.DASHBOARD_TOKEN}
SQUARE = [{"x": 0, "y": 0}, {"x": 0.5, "y": 0}, {"x": 0.5, "y": 1}, {"x": 0, "y": 1}]


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "_file", lambda: tmp_path / "map.json")
    monkeypatch.setattr(core, "plugin_data", lambda name: tmp_path)
    saved = list(core._rooms)
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    core._rooms[:] = []
    core.set_room_locked((), (), ())
    yield TestClient(core.app)
    core._rooms[:] = saved
    core.set_room_locked((), (), ())


def test_the_map_owns_the_rooms_and_is_always_on():
    assert core.ROOMS_OWNER.get("create") is me.create_room and core.ROOMS_OWNER.get("delete") is me.delete_room
    assert core.pluginlib.REQUIRED == ("map",)
    found, _ = core.pluginlib.discover([core.Path(core.__file__).parent / "plugins"])
    p = core.pluginlib.plan(found, {"ir"})
    assert "map" in p["on"] and p["why"]["map"] == "core" and "map" not in core.pluginlib.plan(found, {"ir"})["errors"]
    assert core.pluginlib.plan([q for q in found if q.name != "map"], {"ir"})["errors"]["map"].startswith("required by core but not found")


def test_a_room_made_on_the_dashboard_is_a_room_of_the_map(c, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "sw_x", {"id": "sw_x", "name": "x", "category": "switch", "transport": "z2m", "controls": [{"code": "state"}]})
    r = c.post("/api/rooms", headers=HOUSE, json={"name": "Den", "devices": ["sw_x"]})
    assert r.status_code == 200 and r.json()["room"]["devices"] == ["sw_x"]
    rid = r.json()["room"]["id"]
    assert [(u["name"], u["house_room"]) for u in me._load()["unplaced"]] == [("Den", rid)]
    assert c.post("/api/rooms", headers=HOUSE, json={"name": "den"}).status_code == 409
    assert c.put(f"/api/rooms/{rid}", headers=HOUSE, json={"name": "Study"}).json()["room"]["name"] == "Study"
    assert me._load()["unplaced"][0]["name"] == "Study"


def test_a_room_with_an_outline_is_deleted_on_the_dashboard_with_its_outline_and_told_what_names_it(c, monkeypatch):
    c.put("/api/map/zones", headers=HOUSE, json={"zones": [{"id": "z1", "name": "Hall", "poly": SQUARE}]})
    rid = me._load()["rooms"][0]["house_room"]
    assert rid in core.ROOM_ZONED
    panels, voice = sys.modules["yashome_plugins.panels"], sys.modules["yashome_plugins.voice"]
    monkeypatch.setitem(panels._layouts, "pnl_t", {"title": "Wall", "room": rid})
    monkeypatch.setitem(voice._cfg, "satellites", [{"id": "sat1", "name": "Kitchen sat", "room": "Hall"}])
    monkeypatch.setitem(core._tile_sizes, f"room:{rid}", {"sw": {"w": 2}})
    monkeypatch.setitem(core._tab_order, f"room:{rid}", ["sw"])
    before = (json.dumps(core._rooms), (me._file()).read_text())
    refs = c.get(f"/api/rooms/{rid}/refs", headers=HOUSE)
    assert refs.status_code == 200 and refs.json()["named_by"] == [{"kind": "panel", "name": "Wall"}, {"kind": "satellite", "name": "Kitchen sat"}]
    assert (json.dumps(core._rooms), (me._file()).read_text()) == before                       # asking changes nothing
    r = c.delete(f"/api/rooms/{rid}", headers=HOUSE)
    assert r.status_code == 200 and r.json()["removed"]["name"] == "Hall" and len(r.json()["named_by"]) == 2
    assert core._rooms == [] and me._load()["rooms"] == [] and f"room:{rid}" not in core._tile_sizes and f"room:{rid}" not in core._tab_order
    assert c.get(f"/api/rooms/{rid}/refs", headers=HOUSE).status_code == 404 and c.delete(f"/api/rooms/{rid}", headers=HOUSE).status_code == 404


def test_without_an_owner_the_core_keeps_the_rooms_itself(c, monkeypatch):
    monkeypatch.setattr(core, "ROOMS_OWNER", {})
    r = c.post("/api/rooms", headers=HOUSE, json={"name": "Core only"})
    assert r.status_code == 200 and me._load()["unplaced"] == [] and [x["name"] for x in core._rooms] == ["Core only"]
    assert c.delete(f"/api/rooms/{r.json()['room']['id']}", headers=HOUSE).json()["removed"]["name"] == "Core only"
