"""The rooms are the map's: a room with no outline yet (unplaced) is made, renamed and deleted by the map, always shows as a tab, and is
adopted by the outline drawn for it later; the dashboard's rooms the map had no zone for are taken over once, after a backup."""
import json
import sys

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.map"]
HOUSE = {"Authorization": "Bearer " + core.DASHBOARD_TOKEN}
SQUARE = [{"x": 0, "y": 0}, {"x": 0.5, "y": 0}, {"x": 0.5, "y": 1}, {"x": 0, "y": 1}]


@pytest.fixture
def mp(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "_file", lambda: tmp_path / "map.json")
    monkeypatch.setattr(core, "plugin_data", lambda name: tmp_path)
    monkeypatch.setattr(core, "plugin_state", lambda name: tmp_path / "state")
    saved = list(core._rooms)
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    core.set_room_locked((), (), ())
    yield TestClient(core.app)
    core._rooms[:] = saved
    core.set_room_locked((), (), ())


def _map(c):
    return c.get("/api/map", headers=HOUSE).json()


def test_a_new_room_has_no_outline_yet_and_shows_as_a_tab(mp):
    core._rooms[:] = []
    assert mp.post("/api/map/rooms", json={"name": "Den"}).status_code == 401                      # the owner only
    r = mp.post("/api/map/rooms", headers=HOUSE, json={"name": " Den "})
    assert r.status_code == 200 and r.json()["room"]["name"] == "Den"
    rid = r.json()["room"]["id"]
    m = _map(mp)
    assert m["rooms"] == [] and [(u["name"], u["house_room"]) for u in m["unplaced"]] == [("Den", rid)]
    assert [x["id"] for x in core._rooms] == [rid]
    assert rid not in core.ROOM_ZONED and rid not in core.ROOM_HIDDEN                              # empty, yet a tab: it can be found
    assert mp.post("/api/map/rooms", headers=HOUSE, json={"name": "den"}).status_code == 409


def test_the_outline_drawn_for_it_adopts_the_room_and_its_id(mp):
    core._rooms[:] = []
    rid = mp.post("/api/map/rooms", headers=HOUSE, json={"name": "Den"}).json()["room"]["id"]
    r = mp.put("/api/map/zones", headers=HOUSE, json={"zones": [{"id": "z1", "name": "Den", "poly": SQUARE}]})
    assert r.status_code == 200
    m = _map(mp)
    assert m["unplaced"] == [] and m["rooms"][0]["house_room"] == rid and [x["id"] for x in core._rooms] == [rid]
    assert rid in core.ROOM_ZONED


def test_the_map_saved_whole_keeps_the_rooms_with_no_outline(mp):
    core._rooms[:] = []
    rid = mp.post("/api/map/rooms", headers=HOUSE, json={"name": "Den"}).json()["room"]["id"]
    mp.put("/api/map/zones", headers=HOUSE, json={"zones": [{"id": "z1", "name": "Hall", "poly": SQUARE}]})
    m = _map(mp)
    r = mp.put("/api/map", headers=HOUSE, json={"rev": m["rev"], "rooms": m["rooms"], "items": [], "links": []})
    assert r.status_code == 200
    m = _map(mp)
    assert [u["house_room"] for u in m["unplaced"]] == [rid] and {x["name"] for x in core._rooms} == {"Den", "Hall"}
    assert rid not in core.ROOM_HIDDEN and rid not in core.ROOM_ZONED


def test_a_room_is_renamed_and_deleted_with_its_outline(mp):
    core._rooms[:] = []
    den = mp.post("/api/map/rooms", headers=HOUSE, json={"name": "Den"}).json()["room"]["id"]
    mp.put("/api/map/zones", headers=HOUSE, json={"zones": [{"id": "z1", "name": "Hall", "poly": SQUARE}]})
    hall = _map(mp)["rooms"][0]["house_room"]
    assert mp.put(f"/api/map/rooms/{den}", headers=HOUSE, json={"name": "Study"}).json()["room"] == {"id": den, "name": "Study", "devices": []}
    assert mp.put(f"/api/map/rooms/{den}", headers=HOUSE, json={"name": "Hall"}).status_code == 409
    assert mp.put(f"/api/map/rooms/{den}", headers=HOUSE, json={"name": " hALL "}).status_code == 409             # any case, any spaces
    assert mp.put(f"/api/map/rooms/{hall}", headers=HOUSE, json={"name": "study"}).status_code == 409
    assert mp.put(f"/api/map/rooms/{hall}", headers=HOUSE, json={"name": "Lobby"}).status_code == 200
    m = _map(mp)
    assert m["rooms"][0]["name"] == "Lobby" and m["unplaced"][0]["name"] == "Study"
    assert mp.delete(f"/api/map/rooms/{hall}", headers=HOUSE).status_code == 200                   # the outline goes with the room
    m = _map(mp)
    assert m["rooms"] == [] and [x["id"] for x in core._rooms] == [den]
    assert mp.delete(f"/api/map/rooms/{den}", headers=HOUSE).status_code == 200
    assert _map(mp)["unplaced"] == [] and core._rooms == []
    assert mp.delete(f"/api/map/rooms/{den}", headers=HOUSE).status_code == 404


def test_the_dashboard_rooms_are_taken_over_once_after_a_backup(mp, tmp_path):
    core._rooms[:] = [{"id": "r_a", "name": "Kitchen", "devices": []}, {"id": "r_b", "name": "Hall", "devices": []}]
    (tmp_path / "map.json").write_text(json.dumps({**me._empty(), "rooms": [{"id": "z1", "name": "Hall", "house_room": "r_b", "poly": SQUARE}]}))
    me._migrate_rooms()
    m = me._load()
    assert [(u["name"], u["house_room"]) for u in m["unplaced"]] == [("Kitchen", "r_a")] and m["rev"] == 1
    mark = json.loads((tmp_path / ".rooms-migrated").read_text())
    assert mark["rooms"] == ["Kitchen"]
    backup = tmp_path / "state" / mark["backup"].rsplit("/", 1)[-1]
    assert json.loads((backup / "rooms.json").read_text()) == core._rooms and "map.json" in (backup / "manifest.txt").read_text()
    core._rooms.append({"id": "r_c", "name": "Later", "devices": []})
    me._migrate_rooms()                                                                            # never again
    assert len(me._load()["unplaced"]) == 1


def test_nothing_to_take_over_writes_only_the_mark(mp, tmp_path):
    core._rooms[:] = []
    me._migrate_rooms()
    assert not (tmp_path / "map.json").exists() and not (tmp_path / "state").exists() and (tmp_path / ".rooms-migrated").is_file()


def test_a_door_on_a_wall_the_neighbour_shares_stays_when_one_room_goes(mp):
    core._rooms[:] = []
    sq = lambda x0, x1: [{"x": x0, "y": 0}, {"x": x1, "y": 0}, {"x": x1, "y": 1}, {"x": x0, "y": 1}]
    zones = [{"id": "z1", "name": "Left", "poly": sq(0, 0.5), "walls": [True] * 4}, {"id": "z2", "name": "Right", "poly": sq(0.5, 1), "walls": [True] * 4}]
    doors = [{"id": "o1", "a": [0.5, 0.4], "b": [0.5, 0.5], "kind": "door"}, {"id": "o2", "a": [0, 0.4], "b": [0, 0.5], "kind": "door"}]
    cur = _map(mp)
    r = mp.put("/api/map", headers=HOUSE, json={"rev": cur["rev"], "rooms": zones, "items": [], "links": [], "openings": doors})
    assert r.status_code == 200 and len(_map(mp)["openings"]) == 2
    left = next(z["house_room"] for z in r.json()["zones"] if z["id"] == "z1")
    assert mp.delete(f"/api/map/rooms/{left}", headers=HOUSE).status_code == 200
    m = _map(mp)
    assert [z["name"] for z in m["rooms"]] == ["Right"] and [o["id"] for o in m["openings"]] == ["o1"]      # the shared wall's door stays, the outer one goes


def test_a_zone_name_is_checked_and_trimmed(mp):
    core._rooms[:] = []
    m = _map(mp)
    put = lambda name: mp.put("/api/map", headers=HOUSE, json={"rev": m["rev"], "rooms": [{"id": "z1", "name": name, "poly": SQUARE}],   # noqa: E731
                                                              "items": [], "links": []})
    assert put("   ").json()["detail"] == "name is empty"
    assert put("A\tB").json()["detail"] == "name has a control character"
    r = put(" Hall ")
    assert r.status_code == 200 and [z["name"] for z in _map(mp)["rooms"]] == ["Hall"]


def test_a_lamp_name_is_checked_when_new_or_changed(mp):
    core._rooms[:] = []
    m = _map(mp)
    lamp = lambda i, n: {"id": i, "kind": "load", "name": n, "type": "lamp", "x": 0.1, "y": 0.1}   # noqa: E731
    put = lambda items, rev: mp.put("/api/map", headers=HOUSE, json={"rev": rev, "rooms": [], "items": items, "links": []})   # noqa: E731
    assert put([lamp("l1", "Lamp"), lamp("l2", " lamp ")], m["rev"]).status_code == 409
    r = put([lamp("l1", "Lamp"), lamp("l2", " Lamp 2 ")], m["rev"])
    assert r.status_code == 200 and sorted(i["name"] for i in _map(mp)["items"]) == ["Lamp", "Lamp 2"]
    assert put([lamp("l1", "Lamp"), lamp("l2", "Lamp\x01")], r.json()["rev"]).status_code == 400
