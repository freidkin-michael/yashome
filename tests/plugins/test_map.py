"""The home map: owner only, the whole map saved with a revision, the house's own scheme, what may be placed, the links the house knows, the voice facts."""
import json
import sys

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.map"]
HOUSE = {"Authorization": "Bearer " + core.DASHBOARD_TOKEN}


@pytest.fixture
def mp(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "_file", lambda: tmp_path / "map.json")
    monkeypatch.setattr(core, "plugin_data", lambda name: tmp_path)
    return TestClient(core.app)


def body(c, **kw):
    cur = c.get("/api/map", headers=HOUSE).json()
    d = {"rev": cur["rev"], "rooms": cur["rooms"], "items": [], "links": []}
    d.update(kw)
    return d


def test_only_the_owner_reaches_the_map(mp):
    for method, path in (("get", "/api/map"), ("get", "/api/map/auto"), ("get", "/api/map/facts"), ("get", "/api/map/candidates"), ("get", "/api/map/scheme")):
        assert getattr(mp, method)(path).status_code == 401, path
        assert getattr(mp, method)(path, headers={"Authorization": "Bearer nope"}).status_code in (401, 403), path
    assert mp.put("/api/map", json={"rev": 0, "rooms": [], "items": [], "links": []}).status_code == 401


def test_the_map_is_saved_whole_and_a_stale_revision_is_refused(mp):
    first = mp.get("/api/map", headers=HOUSE).json()
    assert first["rev"] == 0 and first["items"] == [] and "lamp" in first["load_types"] and "led" in first["load_types"] and "fan" in first["load_types"]
    items = [{"id": "a1", "kind": "load", "name": "Chandelier", "type": "lamp", "x": 0.4, "y": 0.5},
             {"id": "d1", "kind": "device", "dev": me._things()[0]["id"], "x": 0.2, "y": 0.3}]
    links = [{"load": "a1", "dev": items[1]["dev"], "code": "state"}]
    r = mp.put("/api/map", headers=HOUSE, json=body(mp, items=items, links=links))
    assert r.status_code == 200 and r.json()["rev"] == 1
    got = mp.get("/api/map", headers=HOUSE).json()
    assert got["rev"] == 1 and [i["id"] for i in got["items"]] == ["a1", "d1"] and got["links"][0]["load"] == "a1"
    assert mp.put("/api/map", headers=HOUSE, json={"rev": 0, "rooms": [], "items": [], "links": []}).status_code == 409        # a second tab with an old copy


def test_a_bad_map_is_refused(mp):
    dev = me._things()[0]["id"]
    def put(**kw):
        return mp.put("/api/map", headers=HOUSE, json=body(mp, **kw)).status_code
    load = {"id": "a1", "kind": "load", "name": "Lamp", "type": "lamp", "x": 0.1, "y": 0.1}
    assert put(items=[load, dict(load)]) == 400                                                        # an id twice
    assert put(items=[{"id": "a", "kind": "device", "dev": dev, "x": 0, "y": 0}, {"id": "b", "kind": "device", "dev": dev, "x": 1, "y": 1}]) == 400   # a device twice
    assert put(items=[dict(load, name=" ")]) == 400                                                    # a load needs a name
    assert put(items=[dict(load, type="spaceship")]) == 400
    assert put(items=[dict(load, x=1.5)]) == 422                                                       # outside the plan
    assert put(items=[load], links=[{"load": "zzz", "dev": dev, "code": ""}]) == 400                   # a link from nothing
    assert put(items=[load]) == 200


def test_what_may_be_placed(mp, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "vc_x", {"id": "vc_x", "name": "A voice card", "category": "voice", "controls": []})
    out = mp.get("/api/map/candidates", headers=HOUSE).json()
    ids = {c["id"] for c in out}
    assert "vc_x" not in ids and me._things()[0]["id"] in ids
    monkeypatch.setattr(me, "_panels", lambda: {"panel7": {"widgets": []}})
    got = {c["id"]: c for c in mp.get("/api/map/candidates", headers=HOUSE).json()}
    assert got["panel:panel7"]["category"] == "panel" and got["panel:panel7"]["kind"] == "panel"


def test_the_links_the_house_knows_are_computed_not_stored(mp, monkeypatch):
    a, b = list(core.DEVICES)[:2]
    monkeypatch.setattr(core, "bindings", {a: {"state": {"single": {"target": b, "code": "state", "action": "on"}}}, "ghost": {"x": {"single": {"target": "nobody"}}}})
    monkeypatch.setattr(me, "_panels", lambda: {"p1": {"widgets": [{"cmd_topic": f"panel/p1/cmd/{b}/state"}, {"cmd_topic": "home/cmd/nobody/state"}]}})
    out = mp.get("/api/map/auto", headers=HOUSE).json()
    assert {"from": a, "to": b, "via": "rule", "note": "state single"} in out
    assert {"from": "panel:p1", "to": b, "via": "panel", "note": "state"} in out
    assert all(x["to"] != "nobody" for x in out)
    assert mp.get("/api/map", headers=HOUSE).json()["links"] == []


def test_the_voice_gets_plain_facts_and_a_room_is_found_by_its_outline(mp):
    dev = me._things()[0]["id"]
    name = core.DEVICES[dev].get("name") or dev
    room = {"id": "r1", "name": "Room 1", "house_room": "", "poly": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 0.5}, {"x": 0, "y": 0.5}]}
    items = [{"id": "a1", "kind": "load", "name": "lamp_1", "type": "lamp", "x": 0.5, "y": 0.25},
             {"id": "a2", "kind": "load", "name": "outdoor lamp", "type": "lamp", "x": 0.5, "y": 0.9},
             {"id": "d1", "kind": "device", "dev": dev, "x": 0.1, "y": 0.1}]
    assert mp.put("/api/map/zones", headers=HOUSE, json={"zones": [{k: v for k, v in room.items() if k not in ("house_room",)}]}).status_code == 200
    mp.put("/api/map", headers=HOUSE, json=body(mp, items=items, links=[{"load": "a1", "dev": dev, "code": ""}]))
    lines = mp.get("/api/map/facts", headers=HOUSE).json()["lines"]
    assert f"lamp_1 (Room 1, lamp): controlled by {name}" in lines
    assert "outdoor lamp (lamp)" in lines                                                              # outside every outline: no room
    assert f"{name} is in Room 1" in lines


def strip(**kw):
    d = {"id": "s1", "kind": "load", "name": "long strip", "type": "led", "x": 0.5, "y": 0.5,
         "path": [{"x": 0.1, "y": 0.1}, {"x": 0.3, "y": 0.2}, {"x": 0.6, "y": 0.2}, {"x": 0.8, "y": 0.4}]}
    d.update(kw)
    return d


def test_an_led_strip_is_a_line_of_points_with_parts_linked_to_channels(mp):
    dev = me._things()[0]["id"]
    def put(items, links=()):
        return mp.put("/api/map", headers=HOUSE, json=body(mp, items=items, links=list(links)))
    assert put([strip(path=[{"x": 0.1, "y": 0.1}])]).status_code == 400                                 # one point is not a line
    assert put([strip(type="lamp")]).status_code == 400                                                 # only a strip has a path
    assert put([strip()], [{"load": "s1", "dev": dev, "code": "a", "seg": [2, 1]}]).status_code == 400  # a part in order
    assert put([strip()], [{"load": "s1", "dev": dev, "code": "a", "seg": [0, 4]}]).status_code == 400  # inside the path
    assert put([strip()], [{"load": "s1", "dev": dev, "code": "a", "seg": [0, 1]}, {"load": "s1", "dev": dev, "code": "b", "seg": [1, 3]}]).status_code == 200
    got = mp.get("/api/map", headers=HOUSE).json()
    assert len(got["items"][0]["path"]) == 4 and got["items"][0]["smooth"] is False
    assert (got["items"][0]["x"], got["items"][0]["y"]) == (0.6, 0.2)                                    # the handle is the middle of the path
    assert [ln["seg"] for ln in got["links"]] == [[0, 1], [1, 3]]


def test_the_voice_hears_of_a_strip_and_its_parts(mp):
    dev = me._things()[0]["id"]
    name = core.DEVICES[dev].get("name") or dev
    mp.put("/api/map", headers=HOUSE, json=body(mp, items=[strip()], links=[{"load": "s1", "dev": dev, "code": ""}, {"load": "s1", "dev": dev, "code": "", "seg": [0, 1]}]))
    lines = mp.get("/api/map/facts", headers=HOUSE).json()["lines"]
    assert f"LED strip long strip: controlled by {name}; {name} (part 1-2 of the strip)" in lines


FULL = "yfd_full-token-for-tests-only-00000000000"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 860 530"><rect width="860" height="530" fill="#000"/><text x="1" y="2">Room 1</text></svg>'


def test_a_full_access_phone_is_not_the_owner(mp, monkeypatch, tmp_path):
    fam = sys.modules["yashome_plugins.family"]
    monkeypatch.setattr(fam, "DB", tmp_path / "family_devices.json")
    fam.DB.write_text(__import__("json").dumps({"adult": {"sha256": fam._sha(FULL), "name": "Adult"}}))
    fam._cache["mtime"] = None
    phone = {"Authorization": "Bearer " + FULL}
    for method, path in (("get", "/api/map"), ("get", "/api/map/auto"), ("get", "/api/map/facts"), ("get", "/api/map/candidates"), ("get", "/api/map/scheme")):
        assert getattr(mp, method)(path, headers=phone).status_code in (401, 403), path
    assert mp.put("/api/map", headers=phone, json={"rev": 0, "rooms": [], "items": [], "links": []}).status_code in (401, 403)
    assert mp.get("/api/map", headers=HOUSE).status_code == 200                                      # and the house token still is


@pytest.mark.parametrize("svg,ok", [
    (SVG, True),
    (SVG.replace(b"<rect", b"<defs><filter id='f'><feDropShadow dx='0' dy='1' stdDeviation='1'/></filter></defs><g filter='url(#f)'><rect").replace(b"/><text", b"/></g><text"), True),
    (SVG.replace(b"<rect", b"<x:script xmlns:x='http://www.w3.org/2000/svg'>1</x:script><rect"), False),                  # a prefix is no way round the list
    (SVG.replace(b"<rect", b"<animateMotion/><rect"), False),
    (SVG.replace(b"<rect", b"<animateTransform/><rect"), False),
    (SVG.replace(b"<rect", b"<feImage href='http://x/y'/><rect"), False),
    (SVG.replace(b"<rect", b"<rect style='fill:\\75rl(http://x)'"), False),
    (SVG.replace(b"<rect", b"<rect fill='&#117;rl(http://x)'"), False),
    (SVG.replace(b"<rect", b"<rect fill='url(http://x/y.svg#a)'"), False),
    (SVG.replace(b"<rect", b"<rect filter='url(#f)'"), True),
    (SVG.replace(b"<rect", b"<rect data-snap='1'"), True),                                                         # a shape the strips stick to
    (SVG.replace(b"<rect", b"<script>1</script><rect"), False),
    (SVG.replace(b"<rect", b"<rect onclick='x'"), False),
    (SVG.replace(b"<rect", b"<foreignObject/><rect"), False),
    (SVG.replace(b"<rect", b"<image href='http://x/y.png'/><rect"), False),
    (SVG.replace(b"<rect", b"<use href='#a'/><rect"), False),
    (SVG.replace(b"fill=\"#000\"", b"style='fill:url(http://x)'"), False),
    (b"<!DOCTYPE svg [<!ENTITY a 'b'>]>" + SVG, False),
    (b"not an svg", False),
])
def test_only_a_static_drawing_is_used_as_the_scheme(mp, tmp_path, svg, ok):
    (tmp_path / me.SCHEME_FILE).write_bytes(svg)
    got = mp.get("/api/map", headers=HOUSE).json()
    assert got["scheme_img"] is ok
    if not ok:                                                    # no drawing to show: a blank frame, the map still works
        assert got["scheme"] == {"x": 0, "y": 0, "w": 1000, "h": 700}
    r = mp.get("/api/map/scheme", headers=HOUSE)
    assert (r.status_code == 200) is ok
    if ok:
        assert (got["scheme"]["w"], got["scheme"]["h"]) == (860, 530)
        assert r.headers["content-type"] == "image/svg+xml" and "sandbox" in r.headers["content-security-policy"]
    assert mp.post("/api/map/scheme", headers=HOUSE, content=SVG).status_code in (404, 405)                    # nothing uploads a drawing


def test_the_page_and_the_map_read_the_same_build_of_the_screen(mp):
    import pathlib
    ui = pathlib.Path(me.__file__).parent / "ui.js"
    listed = next(p for p in mp.get("/api/plugins", headers=HOUSE).json()["loaded"] if p["id"] == "map")
    assert listed["ui_v"] == mp.get("/api/map", headers=HOUSE).json()["ui_v"] == int(ui.stat().st_mtime)             # no banner "older screen" on a fresh load
    assert all("ui_v" not in m for m in core.PLUGINS)                                                              # never kept from the start of the house
    import os
    t = ui.stat()
    os.utime(ui, (t.st_atime, t.st_mtime + 100))
    try:
        listed = next(p for p in mp.get("/api/plugins", headers=HOUSE).json()["loaded"] if p["id"] == "map")
        assert listed["ui_v"] == int(t.st_mtime) + 100 == mp.get("/api/map", headers=HOUSE).json()["ui_v"]          # a changed file is a new address at once
    finally:
        os.utime(ui, (t.st_atime, t.st_mtime))


def test_the_keys_of_an_older_file_are_dropped(mp, tmp_path):
    (tmp_path / "map.json").write_text('{"rev": 3, "plan": "elec", "plans": {"fur": {}}, "frame": "vector", "rooms": [], "items": [], "links": []}')
    got = mp.get("/api/map", headers=HOUSE).json()
    assert got["rev"] == 3 and not {"plan", "plans", "frame", "plan_urls"} & set(got)


def test_only_physical_things_can_be_placed_and_a_tv_is_one_thing(mp, monkeypatch, tmp_path):
    import json
    for did, d in {"tv_a": {"name": "TV A", "category": "tv", "transport": "webos"}, "remote_a": {"name": "TV remote", "category": "ir", "transport": "ir"},
                   "tag_a": {"name": "NFC", "category": "button", "transport": "nfc"}, "who_a": {"name": "Who", "category": "indicator", "transport": "presence"},
                   "sw_a": {"name": "Wall switch", "category": "switch", "transport": "z2m", "controls": [{"code": "state"}]},
                   "plug_a": {"name": "Plug", "category": "switch", "transport": "z2m", "model": "TS011F_plug_3", "controls": [{"code": "state"}, {"code": "power"}]}}.items():
        monkeypatch.setitem(core.DEVICES, did, {"id": did, "controls": [], **d})
    (tmp_path / me.THINGS_FILE).write_text(json.dumps({"things": [{"dev": "tv_a", "name": "TV (room 1)", "kind": "tv", "members": ["remote_a"]}]}))
    ir = sys.modules["yashome_plugins.ir"]
    monkeypatch.setattr(ir, "BLASTERS", [{"node": "blaster-9", "name": "IR room 2"}])
    monkeypatch.setattr(ir, "_node_online", lambda node: False)
    got = {c["id"]: c for c in mp.get("/api/map/candidates", headers=HOUSE).json()}
    assert got["tv_a"]["name"] == "TV A" and got["tv_a"]["category"] == "tv" and got["tv_a"]["members"] == ["remote_a"]       # one TV, its remote is a part of it
    assert got["sw_a"]["category"] == "switch" and got["plug_a"]["category"] == "socket"
    assert got["blaster:blaster-9"]["name"] == "IR room 2" and got["blaster:blaster-9"]["category"] == "blaster" and got["blaster:blaster-9"]["online"] is False                          # listed, marked offline
    assert not {"remote_a", "tag_a", "who_a"} & set(got)                                                                             # nothing stands there
    assert mp.get("/api/map/parts", headers=HOUSE).json() == {"tv_a": ["remote_a"]}                                                  # the remote is a part of the TV
    def put(dev, items=()):
        return mp.put("/api/map", headers=HOUSE, json=body(mp, items=[{"id": "a1", "kind": "device", "dev": dev, "x": 0.1, "y": 0.1}, *items])).status_code
    assert put("remote_a") == 400 and put("tag_a") == 400 and put("who_a") == 400
    assert put("tv_a") == 200
    (tmp_path / me.THINGS_FILE).unlink()
    assert put("tv_a") == 200                                                                                  # what is already placed stays, whatever the list says


def _square(x0, x1):
    return [{"x": x0, "y": 0}, {"x": x1, "y": 0}, {"x": x1, "y": 1}, {"x": x0, "y": 1}]


def _wall_switches(monkeypatch, n):
    ids = [f"sw_t{i}" for i in range(n)]
    for i in ids:
        monkeypatch.setitem(core.DEVICES, i, {"id": i, "name": i, "category": "switch", "transport": "z2m", "controls": [{"code": "state"}]})
    return ids


@pytest.fixture
def rooms(monkeypatch):
    saved = list(core._rooms)
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    core.ROOM_LOCKED.clear()
    yield core._rooms
    core._rooms[:] = saved
    core.ROOM_LOCKED.clear()


def test_the_zones_of_the_map_are_the_dashboard_rooms(mp, rooms, monkeypatch):
    a, b, c = _wall_switches(monkeypatch, 3)
    core._rooms[:] = [{"id": "r_room1", "name": "Room 1", "devices": [b, c]}, {"id": "r_old", "name": "Old", "devices": [a]}]
    items = [{"id": "d1", "kind": "device", "dev": a, "x": 0.25, "y": 0.5}, {"id": "d2", "kind": "device", "dev": b, "x": 0.75, "y": 0.5}]
    zones = [{"id": "z1", "name": "Room 1 ", "poly": _square(0, 0.5)}, {"id": "z2", "name": "Room 2", "poly": _square(0.5, 1)}]
    def put(**kw):
        return _put_map(mp, **kw)
    assert put(items=items).status_code == 200 and [r["id"] for r in core._rooms] == ["r_room1", "r_old"]          # no zone drawn: the rooms are left alone
    r = put(items=items, rooms=zones)
    assert r.status_code == 200 and [(z["id"], z["house_room"]) for z in r.json()["zones"]][0] == ("z1", "r_room1")        # the same name keeps the room's id
    by = {x["name"]: x for x in core._rooms}
    assert set(by) == {"Room 1", "Room 2"} and by["Room 1"]["devices"] == [c, a] and by["Room 2"]["devices"] == [b]     # one room each; "Old" lost its last device: gone
    assert c in by["Room 1"]["devices"]                                                                              # a device that is not placed keeps its room
    assert core.ROOM_LOCKED == {a, b}
    assert mp.get("/api/rooms/locked").status_code in (200, 401, 403)
    c2 = TestClient(core.app)
    assert c2.put(f"/api/rooms/{by['Room 1']['id']}", headers=HOUSE, json={"devices": [c]}).status_code == 409                       # a placed device cannot be moved by hand
    assert c2.put(f"/api/rooms/{by['Room 1']['id']}", headers=HOUSE, json={"devices": [c, a]}).status_code == 200                    # the others can
    items[0] = {**items[0], "x": 0.8}                                                                              # the icon moves to the other zone
    zones[0]["house_room"], zones[1]["house_room"] = by["Room 1"]["id"], by["Room 2"]["id"]
    assert put(items=items, rooms=zones).status_code == 200
    by = {x["name"]: x for x in core._rooms}
    assert a in by["Room 2"]["devices"] and a not in by["Room 1"]["devices"]
    zones[0]["name"] = "Hall"                                                                                       # a rename renames the room, its id stays
    assert put(items=items, rooms=zones).status_code == 200
    assert {x["id"] for x in core._rooms if x["name"] == "Hall"} == {by["Room 1"]["id"]}
    assert put(items=items, rooms=[]).status_code == 200 and {x["name"] for x in core._rooms} == {"Hall", "Room 2"}   # zones deleted: the rooms stay
    assert core.ROOM_LOCKED == set()


def test_a_thing_that_is_no_device_stays_out_of_the_rooms_and_a_room_change_from_a_device_window_works(mp, rooms, monkeypatch):
    a, b = _wall_switches(monkeypatch, 2)
    core._rooms[:] = [{"id": "r_bed", "name": "Bed", "devices": [a, "blaster:old-1"]}]     # what an older sync left there
    orig = me._things
    monkeypatch.setattr(me, "_things", lambda: orig() + [{"id": f"blaster:{n}", "name": n, "category": "ir"} for n in ("bl-1", "old-1")])
    items = [{"id": "d1", "kind": "device", "dev": a, "x": 0.25, "y": 0.5},
             {"id": "t1", "kind": "device", "dev": "blaster:bl-1", "x": 0.3, "y": 0.5},
             {"id": "t2", "kind": "device", "dev": "blaster:old-1", "x": 0.35, "y": 0.5}]
    zones = [{"id": "z1", "name": "Bed", "poly": _square(0, 1)}]
    r = _put_map(mp, items=items, rooms=zones)
    assert r.status_code == 200, r.text
    assert core._rooms[0]["devices"] == [a]                               # devices only; the old thing id is gone too
    core._rooms[0]["devices"].append("blaster:old-2")                     # a stale id still there: a device-window change must not fail on it
    c = TestClient(core.app)
    r = c.put("/api/rooms/r_bed", headers=HOUSE, json={"devices": [a, "blaster:old-2", b]})
    assert r.status_code == 200, r.text
    assert c.put("/api/rooms/r_bed", headers=HOUSE, json={"devices": [a, b, "nothing"]}).status_code == 400      # a NEW unknown id is refused


def test_a_room_a_rule_names_does_not_disappear_and_a_clash_is_refused(mp, rooms, monkeypatch):
    a, b = _wall_switches(monkeypatch, 2)
    core._rooms[:] = [{"id": "r_old", "name": "Old", "devices": [a]}, {"id": "r_two", "name": "Two", "devices": [b]}]
    monkeypatch.setattr(core, "cfg_section", lambda k, d: [{"if": {"room": "r_old"}}] if k == "automations" else d)
    items = [{"id": "d1", "kind": "device", "dev": a, "x": 0.25, "y": 0.5}]
    r = _put_map(mp, items=items, rooms=[{"id": "z1", "name": "New", "poly": _square(0, 0.5)}])
    assert r.status_code == 200 and r.json()["kept"] == [{"room": "Old", "why": "a rule, a favourite or a satellite names it"}]
    assert {x["name"] for x in core._rooms} == {"Old", "New", "Two"} and core.ROOM_HIDDEN == {"r_old"}              # kept, said why, and no tab
    zone = [{"id": "z1", "name": "Two", "house_room": r.json()["zones"][0]["house_room"], "poly": _square(0, 0.5)}]
    assert _put_map(mp, items=items, rooms=zone).status_code == 409           # another room has that name


def _put_map(mp, rooms=None, **kw):
    """Zones go in through the owner's import (PUT /api/map/zones), the rest through the PUT of the map with the zones as they are."""
    if rooms is not None:
        r = mp.put("/api/map/zones", headers=HOUSE, json={"zones": [{k: v for k, v in z.items() if k in ("id", "name", "poly")} for z in rooms]})
        if r.status_code != 200:
            return r
    return mp.put("/api/map", headers=HOUSE, json=body(mp, **kw))


def test_no_zone_leaves_the_rooms_byte_identical(mp, rooms, monkeypatch):
    import json
    a, b = _wall_switches(monkeypatch, 2)
    core._rooms[:] = [{"id": "r1", "name": "Room 1", "devices": [a, b]}, {"id": "r2", "name": "Empty", "devices": []}]
    before = json.dumps(core._rooms, sort_keys=True)
    assert _put_map(mp, items=[{"id": "d1", "kind": "device", "dev": a, "x": 0.2, "y": 0.2}]).status_code == 200
    assert json.dumps(core._rooms, sort_keys=True) == before and core.ROOM_LOCKED == set() and core.ROOM_ZONED == set()


def test_a_refused_save_leaves_the_rooms_as_they_were(mp, rooms, monkeypatch):
    import json
    a, b = _wall_switches(monkeypatch, 2)
    core._rooms[:] = [{"id": "r_one", "name": "One", "devices": [a]}, {"id": "r_two", "name": "Two", "devices": [b]}]
    items = [{"id": "d1", "kind": "device", "dev": a, "x": 0.25, "y": 0.5}]
    assert _put_map(mp, items=items, rooms=[{"id": "z1", "name": "One", "poly": _square(0, 0.5)}]).status_code == 200      # the zone adopts the room One
    before, saved = json.dumps(core._rooms, sort_keys=True), mp.get("/api/map", headers=HOUSE).json()
    zones = [{**saved["rooms"][0], "name": "Two"}]                                                                 # renamed to a name the room Two has
    assert mp.put("/api/map", headers=HOUSE, json={"rev": saved["rev"], "rooms": zones, "items": items, "links": []}).status_code == 409
    assert json.dumps(core._rooms, sort_keys=True) == before and mp.get("/api/map", headers=HOUSE).json()["rev"] == saved["rev"]      # nothing changed, not the map either


def test_two_zones_can_swap_their_names_in_one_save(mp, rooms):
    core._rooms[:] = [{"id": "r_a", "name": "Alpha", "devices": []}, {"id": "r_b", "name": "Beta", "devices": []}]
    assert _put_map(mp, rooms=[{"id": "z1", "name": "Alpha", "poly": _square(0, 0.4)}, {"id": "z2", "name": "Beta", "poly": _square(0.5, 1)}]).status_code == 200
    saved = mp.get("/api/map", headers=HOUSE).json()
    zones = [{**saved["rooms"][0], "name": "Beta"}, {**saved["rooms"][1], "name": "Alpha"}]
    assert mp.put("/api/map", headers=HOUSE, json={"rev": saved["rev"], "rooms": zones, "items": [], "links": []}).status_code == 200
    assert {r["id"]: r["name"] for r in core._rooms} == {"r_a": "Beta", "r_b": "Alpha"}                            # the ids stay, the names went round


def test_a_room_without_zone_and_device_goes_or_is_hidden_while_a_rule_names_it(mp, rooms, monkeypatch):
    a, b = _wall_switches(monkeypatch, 2)
    core._rooms[:] = [{"id": "r_a", "name": "A", "devices": [a]}, {"id": "r_dining", "name": "Dining", "devices": []}, {"id": "r_store", "name": "Store", "devices": []},
                      {"id": "r_left", "name": "Left", "devices": [b]}]
    monkeypatch.setattr(core, "cfg_section", lambda k, d: [{"if": {"room": "r_store"}}] if k == "automations" else d)
    items = [{"id": "d1", "kind": "device", "dev": a, "x": 0.25, "y": 0.5}]
    assert _put_map(mp, items=items, rooms=[{"id": "z1", "name": "A", "poly": _square(0, 0.5)}]).status_code == 200
    assert {x["name"] for x in core._rooms} == {"A", "Store", "Left"} and core.ROOM_HIDDEN == {"r_store"}      # that one has no tab; a room that holds a device stays
    assert mp.get("/api/rooms/locked", headers=HOUSE).json()["hidden"] == ["r_store"]


def test_many_zones_make_short_room_ids(mp, rooms):
    zones = [{"id": f"z{i}", "name": f"Room {i}", "poly": _square(i / 100, i / 100 + 0.005)} for i in range(55)]
    r = _put_map(mp, rooms=zones)
    assert r.status_code == 200 and len({z["house_room"] for z in r.json()["zones"]}) == 55 and max(len(z["house_room"]) for z in r.json()["zones"]) <= 24


def test_a_room_that_goes_is_reported_and_a_room_a_satellite_names_stays(mp, rooms, monkeypatch, tmp_path):
    a, b = _wall_switches(monkeypatch, 2)
    core._rooms[:] = [{"id": "r_a", "name": "A", "devices": [a]}, {"id": "r_group", "name": "Group", "devices": [a]}, {"id": "r_sat", "name": "Sat", "devices": [b]}]
    voice = sys.modules["yashome_plugins.voice"]
    monkeypatch.setattr(voice, "_sats", lambda: [{"id": "s1", "room": "r_sat"}])
    items = [{"id": "d1", "kind": "device", "dev": a, "x": 0.25, "y": 0.5}, {"id": "d2", "kind": "device", "dev": b, "x": 0.75, "y": 0.5}]
    zones = [{"id": "z1", "name": "A", "poly": _square(0, 0.5)}, {"id": "z2", "name": "Elsewhere", "poly": _square(0.5, 1)}]
    r = _put_map(mp, items=items, rooms=zones).json()
    assert r["removed"] == ["Group"] and r["kept"][0]["room"] == "Sat"                                              # told, not silent; the satellite's room stays
    assert [x["name"] for x in core._rooms if x["id"] in ("r_group", "r_sat")] == ["Sat"]
    import json
    snap = json.loads((tmp_path / "rooms.json").read_text(encoding="utf-8"))                                       # what the map did is on the map's own file (committed with it)
    assert {x["name"] for x in snap} == {"A", "Sat", "Elsewhere"}


def test_a_room_of_the_map_deleted_by_hand_takes_its_zone_and_a_voice_card_cannot_be_placed(mp, rooms, monkeypatch):
    a, b = _wall_switches(monkeypatch, 2)
    monkeypatch.setitem(core.DEVICES, "vc_t", {"id": "vc_t", "name": "Voice", "category": "voice", "transport": "voice", "voice_role": "mic", "controls": []})
    core._rooms[:] = [{"id": "r_a", "name": "A", "devices": []}, {"id": "r_free", "name": "Free", "devices": [b]}]
    zones = [{"id": "z1", "name": "A", "poly": _square(0, 0.5)}]
    assert _put_map(mp, items=[{"id": "d9", "kind": "device", "dev": "vc_t", "x": 0.1, "y": 0.1}], rooms=zones).status_code == 400        # a voice card has no place
    assert _put_map(mp, items=[{"id": "d1", "kind": "device", "dev": a, "x": 0.25, "y": 0.5}], rooms=zones).status_code == 200
    assert mp.put("/api/rooms/r_a", headers=HOUSE, json={"devices": []}).status_code == 409                       # its placed device is not taken out by hand
    assert mp.get("/api/rooms/locked", headers=HOUSE).json() == {"devices": [a], "rooms": ["r_a"], "hidden": []}
    assert mp.delete("/api/rooms/r_free", headers=HOUSE).status_code == 200                                        # any other room goes as before
    assert mp.delete("/api/rooms/r_a", headers=HOUSE).status_code == 200                                           # a zone is this room: it goes with it
    assert mp.get("/api/map", headers=HOUSE).json()["rooms"] == [] and mp.get("/api/rooms/locked", headers=HOUSE).json() == {"devices": [], "rooms": [], "hidden": []}


def test_the_outline_of_a_zone_can_be_edited_while_it_stays_a_simple_polygon(mp, rooms):
    assert mp.put("/api/map/zones", headers=HOUSE, json={"zones": [{"id": "z1", "name": "One", "poly": _square(0, 0.5)}, {"id": "z2", "name": "Two", "poly": _square(0.5, 1)}]}).status_code == 200
    cur = mp.get("/api/map", headers=HOUSE).json()

    def put(zones, rev=None):
        return mp.put("/api/map", headers=HOUSE, json={"rev": cur["rev"] if rev is None else rev, "rooms": zones, "items": [], "links": []})
    z1, z2 = cur["rooms"]
    moved = {**z1, "poly": [{"x": 0, "y": 0}, {"x": 0.4, "y": 0}, {"x": 0.6, "y": 0.5}, {"x": 0, "y": 0.5}, {"x": 0, "y": 0.2}]}               # a corner moved, one added
    assert put([moved, z2]).status_code == 200 and mp.get("/api/map", headers=HOUSE).json()["rooms"][0]["poly"][2] == {"x": 0.6, "y": 0.5}
    cur = mp.get("/api/map", headers=HOUSE).json()
    bow = {**cur["rooms"][0], "poly": [{"x": 0, "y": 0}, {"x": 0.5, "y": 0.5}, {"x": 0.5, "y": 0}, {"x": 0, "y": 0.5}]}                              # a bow tie crosses itself
    assert put([bow, cur["rooms"][1]]).status_code == 400
    fold = {**cur["rooms"][0], "poly": [{"x": 0, "y": 0}, {"x": 0.5, "y": 0}, {"x": 0.2, "y": 0}, {"x": 0.3, "y": 0.4}]}                              # an edge folds back over another
    assert put([fold, cur["rooms"][1]]).status_code == 400
    flat = {**cur["rooms"][0], "poly": [{"x": 0, "y": 0}, {"x": 0.2, "y": 0}, {"x": 0.4, "y": 0}]}                                                     # no area
    assert put([flat, cur["rooms"][1]]).status_code == 400
    two = {**cur["rooms"][0], "poly": [{"x": 0, "y": 0}, {"x": 0.4, "y": 0}]}
    assert put([two, cur["rooms"][1]]).status_code == 422                                                                                             # 3 points at least
    assert put(cur["rooms"][:1]).status_code == 200                                                                                                   # a room deleted on the screen
    cur = mp.get("/api/map", headers=HOUSE).json()
    assert [z["id"] for z in cur["rooms"]] == ["z1"] and "Two" not in {r["name"] for r in core._rooms}                            # its empty dashboard room goes with it
    assert put(cur["rooms"] + [{"id": "z9", "name": "Nine", "house_room": "", "poly": _square(0.6, 0.7)}], rev=cur["rev"] - 1).status_code == 409    # the revision check is kept
    assert put(cur["rooms"] + [{"id": "z9", "name": "Nine", "house_room": "", "poly": _square(0.6, 0.7)}]).status_code == 200                           # a new room
    assert mp.put("/api/map/zones", json={"zones": []}).status_code in (401, 403)                                                                       # the import is the owner's


def test_an_ir_fixture_on_a_wall_switch_is_a_lamp_of_the_map_and_its_feed_is_no_auto_link(mp, monkeypatch):
    ir = sys.modules["yashome_plugins.ir"]
    monkeypatch.setitem(core.DEVICES, "wall1", {"id": "wall1", "name": "Switch 1", "category": "switch", "transport": "z2m", "controls": [{"code": "state_left"}]})
    monkeypatch.setitem(core.DEVICES, "fix1", {"id": "fix1", "name": "Lamp with fan", "category": "ir", "transport": "ir", "ir_appliance": True,
                                               "ir_power": {"device": "wall1", "code": "state_left"}, "controls": []})
    monkeypatch.setitem(core.DEVICES, "tile1", {"id": "tile1", "name": "TV remote", "category": "ir", "transport": "ir", "ir_appliance": True, "controls": []})
    monkeypatch.setattr(ir, "APPLIANCES", [{"id": "fix1", "name": "Lamp with fan", "remote": "r", "power": {"device": "wall1", "code": "state_left"}},
                                           {"id": "tile1", "name": "TV remote", "remote": "r"}])
    got = {c["id"]: c for c in mp.get("/api/map/candidates", headers=HOUSE).json()}
    assert got["fix1"]["category"] == "lamp" and "tile1" not in got           # a fixture stands somewhere; a remote does not
    auto = mp.get("/api/map/auto", headers=HOUSE).json()
    assert not [x for x in auto if x["via"] == "power"]                         # the feed is a wire now: the Wires layer draws it



def test_a_zone_edge_is_a_wall_or_open_and_the_marks_follow_its_points(mp):
    sq = [{"x": 0.1, "y": 0.1}, {"x": 0.4, "y": 0.1}, {"x": 0.4, "y": 0.4}, {"x": 0.1, "y": 0.4}]
    r = mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[{"id": "z1", "name": "Kitchen", "poly": sq, "walls": [True, False]}]))
    assert r.status_code == 400 and "one wall mark per edge" in r.json()["detail"]
    assert mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[{"id": "z1", "name": "Kitchen", "poly": sq, "walls": [True, False, True, True]}])).status_code == 200
    assert mp.get("/api/map", headers=HOUSE).json()["rooms"][0]["walls"] == [True, False, True, True]
    assert mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[{"id": "z1", "name": "Kitchen", "poly": sq}])).status_code == 200
    assert "walls" not in mp.get("/api/map", headers=HOUSE).json()["rooms"][0]                        # not marked yet: none stored


WALLED = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 860 530"><g data-walls="1"><rect x="100" y="100" width="300" height="6"/>'
          b'<rect x="100" y="100" width="6" height="200"/></g></svg>')


def test_the_plan_of_the_flat_is_uploaded_placed_and_removed(mp, tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    assert mp.put("/api/map/underlay?w=2000&h=1000", headers=HOUSE, content=b"<html>no</html>").status_code == 400
    assert mp.put("/api/map/underlay", headers=HOUSE, content=png).status_code == 400                  # its size is needed
    r = mp.put("/api/map/underlay?w=2000&h=1000", headers=HOUSE, content=png).json()
    u = r["underlay"]
    assert (u["kind"], u["w"], u["h"], u["x"], u["y"], u["show"]) == ("png", 1000, 500, 0, 100, True)    # fitted into the blank 1000x700 frame, centred
    g = mp.get("/api/map", headers=HOUSE).json()
    assert g["scheme_img"] is True and g["scheme"]["w"] == 1000 and mp.get("/api/map/underlay", headers=HOUSE).content == png
    assert mp.patch("/api/map/underlay", headers=HOUSE, json={"opacity": 0.3, "rot": 12, "show": False}).json()["underlay"]["rot"] == 12
    assert mp.patch("/api/map/underlay", headers=HOUSE, json={"opacity": 3}).status_code == 422
    assert mp.get("/api/map", headers=HOUSE).json()["scheme_img"] is False
    assert mp.delete("/api/map/underlay", headers=HOUSE).status_code == 200 and not (tmp_path / "underlay.png").exists()
    assert mp.get("/api/map/underlay", headers=HOUSE).status_code == 404


def test_a_straight_wall_has_no_step_at_a_joint_and_near_axis_edges_are_straightened(mp):
    pt = lambda x, y: {"x": x / 1000, "y": y / 700}
    a = [pt(100, 100), pt(250, 104), pt(250, 300), pt(100, 300)]                         # its top slants 1.5 degrees
    b = [pt(250, 108), pt(400, 108), pt(400, 300), pt(250, 300)]                         # the same wall continued, 6 units lower
    assert mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[{"id": "a", "name": "A", "poly": a}, {"id": "b", "name": "B", "poly": b}])).status_code == 200
    mp.post("/api/map/zones/tidy", headers=HOUSE)
    r = mp.get("/api/map", headers=HOUSE).json()["rooms"]
    tops = {round(r[0]["poly"][0]["y"] * 700, 2), round(r[0]["poly"][1]["y"] * 700, 2), round(r[1]["poly"][0]["y"] * 700, 2), round(r[1]["poly"][1]["y"] * 700, 2)}
    assert len(tops) == 1                                                                 # one straight line, no step, no slant


def test_the_scheme_moves_under_the_zones_once_and_nothing_is_read_off_it(mp, tmp_path):
    (tmp_path / me.SCHEME_FILE).write_bytes(WALLED)
    sq = [{"x": 100 / 860, "y": 100 / 530}, {"x": 400 / 860, "y": 100 / 530}, {"x": 400 / 860, "y": 300 / 530}, {"x": 100 / 860, "y": 300 / 530}]
    assert mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[{"id": "z1", "name": "Hall", "poly": sq}])).status_code == 200
    me._migrate_scheme()
    d = mp.get("/api/map", headers=HOUSE).json()
    assert d["canvas"] == {"x": 0, "y": 0, "w": 860, "h": 530} and d["underlay"]["file"] == me.SCHEME_FILE and d["underlay"]["show"] is False
    assert "walls" not in d["rooms"][0] and d["scheme_img"] is False                     # the owner marks the walls; nothing is guessed
    rev = d["rev"]
    me._migrate_scheme()                                                                  # once only
    assert mp.get("/api/map", headers=HOUSE).json()["rev"] == rev
    for path in ("/api/map/walls/from_underlay", "/api/map/underlay/candidates"):
        assert mp.post(path, headers=HOUSE).status_code in (404, 405)
    assert mp.delete("/api/map/underlay", headers=HOUSE).status_code == 200 and (tmp_path / me.SCHEME_FILE).exists()   # the house's own file stays
    d = mp.get("/api/map", headers=HOUSE).json()
    assert d["underlay"] is None and d["scheme_img"] is False                             # removed stays removed: the scheme does not come back
    me._migrate_scheme()
    assert mp.get("/api/map", headers=HOUSE).json()["scheme_img"] is False
    assert mp.patch("/api/map/underlay", headers=HOUSE, json={"show": True}).status_code == 404


def test_a_new_house_takes_geometry_4_at_once_so_zones_drawn_later_are_never_moved(mp):
    me._migrate_geometry()
    assert mp.get("/api/map", headers=HOUSE).json()["geo"] == 4
    pt = lambda x, y: {"x": x / 1000, "y": y / 700}
    a = [pt(100, 100), pt(250, 104), pt(250, 300), pt(100, 300)]
    assert mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[{"id": "a", "name": "A", "poly": a, "walls": [True, False, True, True]}])).status_code == 200
    me._migrate_geometry()
    r = mp.get("/api/map", headers=HOUSE).json()["rooms"][0]
    assert round(r["poly"][1]["y"] * 700) == 104 and r["walls"] == [True, False, True, True]


def test_doors_and_windows_are_stored_by_their_ends_and_survive_a_zones_import(mp):
    pt = lambda x, y: {"x": x / 1000, "y": y / 700}
    room = {"id": "z1", "name": "Hall", "poly": [pt(100, 100), pt(400, 100), pt(400, 300), pt(100, 300)], "walls": [True, False, True, True]}
    w = {"id": "w1", "a": [0.3, 0.3], "b": [0.3, 0.5]}
    for bad in ({"id": "o1", "a": [0.1, 0.1], "b": [0.1, 0.1], "kind": "door"}, {"id": "o1", "a": [0.1, 0.1], "b": [1.2, 0.1], "kind": "door"}):
        assert mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[room], walls=[w], openings=[bad])).status_code == 400
    assert mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[room], walls=[w], openings=[{"id": "o1", "a": [0.1, 0.1], "b": [0.2, 0.1], "kind": "door", "open": "up"}])).status_code == 422
    ok = [{"id": "o1", "a": [0.15, 100 / 700], "b": [0.2, 100 / 700], "kind": "door", "open": "right", "side": True},
          {"id": "o2", "a": [0.3, 0.35], "b": [0.3, 0.4], "kind": "window"}, {"id": "o3", "a": [0.25, 300 / 700], "b": [0.3, 300 / 700], "kind": "door", "open": "slide"}]
    assert mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[room], walls=[w], openings=ok)).status_code == 200
    d = mp.get("/api/map", headers=HOUSE).json()
    assert [(o["id"], o["open"], o["side"]) for o in d["openings"]] == [("o1", "right", True), ("o2", "left", False), ("o3", "slide", False)]
    cur = mp.get("/api/map", headers=HOUSE).json()                                       # a page that knows no walls keeps them
    assert mp.put("/api/map", headers=HOUSE, json={"rev": cur["rev"], "rooms": cur["rooms"], "items": [], "links": []}).status_code == 200
    assert len(mp.get("/api/map", headers=HOUSE).json()["walls"]) == 1
    # the zones come again, renumbered and one more point on the top edge: the openings stay where they were, the known edges keep their marks
    z = {"id": "z9", "name": "Hall", "poly": [pt(100, 300), pt(100, 100), pt(250, 100), pt(400, 100), pt(400, 300)]}
    assert mp.put("/api/map/zones", headers=HOUSE, json={"zones": [z]}).status_code == 200
    d = mp.get("/api/map", headers=HOUSE).json()
    assert [o["id"] for o in d["openings"]] == ["o1", "o2", "o3"] and d["openings"][0]["a"] == ok[0]["a"]
    assert d["rooms"][0]["walls"] == [True, True, True, False, True]                    # 100,300->100,100 wall; the new halves: walls; the right edge stays open


def test_an_opening_stored_by_zone_and_edge_index_is_dropped_on_load(mp):
    d = me._load()
    d["openings"] = [{"id": "o1", "zone": "z1", "edge": 0, "t0": 0.1, "t1": 0.2, "kind": "door"}, {"id": "o2", "a": [0.1, 0.1], "b": [0.2, 0.1], "kind": "window"}]
    me._save(d)
    assert [o["id"] for o in mp.get("/api/map", headers=HOUSE).json()["openings"]] == ["o2"]


def test_an_svg_plan_takes_its_size_from_the_viewbox_or_width_and_height_and_is_bounded(mp, tmp_path):
    svg = lambda attrs: f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}><rect x="1" y="1" width="5" height="5"/></svg>'.encode()
    assert mp.put("/api/map/underlay", headers=HOUSE, content=svg('viewBox="0 0 860 530px"')).json()["underlay"]["vw"] == 860   # a unit on it is read, not a 500
    r = mp.put("/api/map/underlay", headers=HOUSE, content=svg('width="800px" height="400"'))
    assert r.status_code == 200 and r.json()["underlay"]["vw"] == 800
    assert mp.put("/api/map/underlay", headers=HOUSE, content=svg('viewBox="0 0 1.2.3 5"')).status_code == 400
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    for q in ("w=inf&h=5", "w=nan&h=5", "w=1e9&h=5"):
        assert mp.put(f"/api/map/underlay?{q}", headers=HOUSE, content=png).status_code in (400, 422), q
    big = svg('viewBox="0 0 10 10"').replace(b"</svg>", b"<desc>" + b"x" * (2 * 1024 * 1024) + b"</desc></svg>")
    assert mp.put("/api/map/underlay", headers=HOUSE, content=big).status_code == 200            # an SVG plan up to 8 MB, as the sheet says
    (tmp_path / me.SCHEME_FILE).write_bytes(svg('viewBox="0 0 1.2.3 5"'))
    assert me._scheme() is None                                                              # a bad scheme is not used, no error


def test_names_of_things_json_move_to_their_devices_once_and_leave_the_file(mp, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "_name_overrides", {})
    monkeypatch.setitem(core.DEVICES, "plug_9", {"id": "plug_9", "name": "plug_9", "transport": "mqtt", "controls": []})
    monkeypatch.setitem(core.DEVICES, "lamp_9", {"id": "lamp_9", "name": "Own name", "transport": "mqtt", "controls": []})
    (tmp_path / me.THINGS_FILE).write_text(json.dumps({"things": [{"dev": "plug_9", "name": "Kettle socket", "kind": "socket"},
                                                                  {"dev": "lamp_9", "name": "Other"}, {"dev": "panel:hall", "label": "Hall"}]}))
    (tmp_path / me.THINGS_FILE).write_text(json.dumps({"things": json.loads((tmp_path / me.THINGS_FILE).read_text())["things"] + [{"dev": "zb_late", "name": "Later"}]}))
    me._migrate_thing_names()
    assert core.DEVICES["plug_9"]["name"] == "Kettle socket" and core.DEVICES["lamp_9"]["name"] == "Own name"     # a device's own name wins
    left = json.loads((tmp_path / me.THINGS_FILE).read_text())["things"]
    assert left == [{"dev": "plug_9", "kind": "socket"}, {"dev": "lamp_9"}, {"dev": "panel:hall", "label": "Hall"}, {"dev": "zb_late", "name": "Later"}]   # no device yet: kept
    monkeypatch.setitem(core.DEVICES, "zb_late", {"id": "zb_late", "name": "zb_late", "transport": "z2m", "controls": []})
    me._migrate_thing_names()                                                             # its device came: the name moves, then leaves
    assert core.DEVICES["zb_late"]["name"] == "Later" and json.loads((tmp_path / me.THINGS_FILE).read_text())["things"][-1] == {"dev": "zb_late"}


def test_a_thing_that_is_gone_leaves_the_map_with_its_links(mp):
    items = [{"id": "b1", "kind": "device", "dev": "blaster:t-9", "x": 0.5, "y": 0.5}, {"id": "l1", "kind": "load", "name": "Lamp", "type": "lamp", "x": 0.2, "y": 0.2}]
    d = me._load()
    d["items"] = items
    d["links"] = [{"load": "l1", "dev": "blaster:t-9", "code": ""}]
    me._save(d)
    assert me.forget_thing("blaster:t-9") == 1
    d = mp.get("/api/map", headers=HOUSE).json()
    assert [i["id"] for i in d["items"]] == ["l1"] and d["links"] == []


def test_an_imported_edge_on_an_open_edge_stays_open_and_openings_off_the_walls_go(mp):
    pt = lambda x, y: {"x": x / 1000, "y": y / 700}
    room = {"id": "z1", "name": "Hall", "poly": [pt(100, 100), pt(400, 100), pt(400, 300), pt(100, 300)], "walls": [True, False, True, True]}
    ops = [{"id": "o1", "a": [0.2, 100 / 700], "b": [0.15, 100 / 700], "kind": "window"}, {"id": "o2", "a": [0.3, 0.3], "b": [0.35, 0.3], "kind": "window"}]
    r = mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[room], openings=ops)).json()
    d = mp.get("/api/map", headers=HOUSE).json()
    assert r["openings_dropped"] == 1 and [o["id"] for o in d["openings"]] == ["o1"]                     # o2 lies on no wall
    assert d["openings"][0]["a"][0] < d["openings"][0]["b"][0]                                          # a left of b, whatever was sent
    dup = [{"id": "o1", "a": [0.15, 100 / 700], "b": [0.2, 100 / 700], "kind": "window"}] * 2
    assert mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=d["rooms"], openings=dup)).status_code == 400
    z = {"id": "z1", "name": "Hall", "poly": [pt(100, 100), pt(400, 100), pt(400, 200), pt(400, 300), pt(100, 300)]}     # the open right edge split
    mp.put("/api/map/zones", headers=HOUSE, json={"zones": [z]})
    assert mp.get("/api/map", headers=HOUSE).json()["rooms"][0]["walls"] == [True, False, False, True, True]          # its halves stay open
    z = {"id": "z1", "name": "Hall", "poly": [pt(100, 100), pt(400, 100), pt(450, 300), pt(100, 300)]}                 # moved: no known edge under it
    mp.put("/api/map/zones", headers=HOUSE, json={"zones": [z]})
    assert mp.get("/api/map", headers=HOUSE).json()["rooms"][0]["walls"][1] is False


def test_an_svg_too_deep_or_too_big_in_elements_is_refused(mp):
    deep = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">' + b"<g>" * 70 + b"</g>" * 70 + b"</svg>"
    assert mp.put("/api/map/underlay", headers=HOUSE, content=deep).status_code == 400
    many = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">' + b"<g/>" * (me.SVG_MAX_ELEMENTS + 1) + b"</svg>"
    assert mp.put("/api/map/underlay", headers=HOUSE, content=many).status_code == 400


def test_a_swapped_door_keeps_its_hinge_and_leaf_and_tidy_moves_openings_with_their_edge(mp):
    pt = lambda x, y: {"x": x / 1000, "y": y / 700}
    a = {"id": "a", "name": "A", "poly": [pt(100, 100), pt(250, 100), pt(250, 300), pt(100, 300)], "walls": [True, True, True, True]}
    b = {"id": "b", "name": "B", "poly": [pt(260, 100), pt(400, 100), pt(400, 300), pt(260, 300)], "walls": [True, True, True, True]}
    door = {"id": "o1", "a": [0.25, 250 / 700], "b": [0.25, 200 / 700], "kind": "door", "open": "left", "side": False}    # sent bottom to top
    mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[a, b], openings=[door]))
    o = mp.get("/api/map", headers=HOUSE).json()["openings"][0]
    assert o["a"][1] < o["b"][1] and (o["open"], o["side"]) == ("right", True)                # the same door, its ends in order
    r = mp.post("/api/map/zones/tidy", headers=HOUSE).json()
    o = mp.get("/api/map", headers=HOUSE).json()["openings"][0]
    assert r["openings_dropped"] == 0 and round(o["a"][0] * 1000, 1) == round(o["b"][0] * 1000, 1) == 255.0     # it moved with its wall


def test_a_split_open_edge_inside_a_wall_of_another_zone_stays_open(mp):
    pt = lambda x, y: {"x": x / 1000, "y": y / 700}
    big = {"id": "z2", "name": "Salon", "poly": [pt(100, 100), pt(400, 100), pt(400, 300), pt(100, 300)], "walls": [True, True, True, True]}
    small = {"id": "z8", "name": "Hall", "poly": [pt(400, 150), pt(500, 150), pt(500, 250), pt(400, 250)], "walls": [True, True, True, False]}
    mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[big, small]))
    hall = {"id": "z8", "name": "Hall", "poly": [pt(400, 150), pt(500, 150), pt(500, 250), pt(400, 250), pt(400, 200)]}   # its open edge split
    r = mp.put("/api/map/zones", headers=HOUSE, json={"zones": [{"id": "z2", "name": "Salon", "poly": big["poly"]}, hall]}).json()
    assert "openings_dropped" in r
    walls = {z["id"]: z["walls"] for z in mp.get("/api/map", headers=HOUSE).json()["rooms"]}
    assert walls["z8"][3:] == [False, False]                                                # the doorway stays open (its own zone first)


def test_tidy_never_squeezes_an_opening_onto_a_wall_that_does_not_cover_it(mp):
    pt = lambda x, y: {"x": x / 1000, "y": y / 700}
    w4 = [True, True, True, True]
    a = {"id": "a", "name": "A", "poly": [pt(100, 100), pt(250, 100), pt(250, 300), pt(100, 300)], "walls": w4}
    b = {"id": "b", "name": "B", "poly": [pt(260, 100), pt(400, 100), pt(400, 300), pt(260, 300)], "walls": w4}
    c = {"id": "c", "name": "C", "poly": [pt(100, 330), pt(250, 330), pt(250, 500), pt(100, 500)], "walls": w4}
    door = {"id": "o1", "a": [0.25, 150 / 700], "b": [0.25, 200 / 700], "kind": "door"}
    mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[a, b, c], openings=[door]))
    mp.post("/api/map/zones/tidy", headers=HOUSE)
    d = mp.get("/api/map", headers=HOUSE).json()
    for o in d["openings"]:
        assert o["a"] != o["b"] and 150 / 700 - 0.01 < o["a"][1] < o["b"][1] < 200 / 700 + 0.01, o      # on its own (moved) wall, not on C's
    assert mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=d["rooms"], openings=d["openings"])).status_code == 200   # the map still saves


@pytest.mark.parametrize("hall_y", [300, 200])     # 200: the live shape, one split piece IS the Hall's open edge
def test_a_split_edge_of_the_zone_keeps_its_own_mark_over_another_zones_identical_edge(mp, hall_y):
    pt = lambda x, y: {"x": x / 1000, "y": y / 700}
    salon = {"id": "z2", "name": "Salon", "poly": [pt(100, 100), pt(400, 100), pt(400, 300), pt(100, 300)], "walls": [True, True, True, True]}
    hall = {"id": "z8", "name": "Hall", "poly": [pt(400, 100), pt(500, 100), pt(500, hall_y), pt(400, hall_y)], "walls": [True, True, True, False]}
    mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[salon, hall]))
    s2 = {"id": "z2", "name": "Salon", "poly": [pt(100, 100), pt(400, 100), pt(400, 200), pt(400, 300), pt(100, 300)]}     # Salon's wall split
    mp.put("/api/map/zones", headers=HOUSE, json={"zones": [s2, {"id": "z8", "name": "Hall", "poly": hall["poly"]}]})
    walls = {z["id"]: z["walls"] for z in mp.get("/api/map", headers=HOUSE).json()["rooms"]}
    assert walls["z2"][1:3] == [True, True] and walls["z8"][3] is False                     # its own wall stays a wall; Hall's side stays open


def test_tidy_keeps_a_doors_swing_when_its_wall_runs_the_other_way(mp):
    pt = lambda x, y: {"x": x / 1000, "y": y / 700}
    w4 = [True, True, True, True]
    a = {"id": "a", "name": "A", "poly": [pt(100, 100), pt(300, 100), pt(300, 250), pt(100, 250)], "walls": w4}    # its bottom edge runs right to left
    b = {"id": "b", "name": "B", "poly": [pt(100, 260), pt(300, 260), pt(300, 400), pt(100, 400)], "walls": w4}
    door = {"id": "o1", "a": [0.15, 250 / 700], "b": [0.2, 250 / 700], "kind": "door", "open": "left", "side": False}
    mp.put("/api/map", headers=HOUSE, json=body(mp, rooms=[a, b], openings=[door]))
    mp.post("/api/map/zones/tidy", headers=HOUSE)
    o = mp.get("/api/map", headers=HOUSE).json()["openings"][0]
    assert (o["open"], o["side"]) == ("left", False) and o["a"][0] < o["b"][0] and round(o["a"][1] * 700) == 255   # moved, not mirrored


def test_an_ir_only_appliance_gets_its_dashed_line_from_the_blaster_card_and_a_rule_gets_none_drawn(mp, monkeypatch):
    ir = sys.modules["yashome_plugins.ir"]
    monkeypatch.setitem(core.DEVICES, "fan_ir", {"id": "fan_ir", "name": "Fan", "category": "ir", "transport": "ir", "controls": []})
    monkeypatch.setitem(core.DEVICES, "blaster:node-1", {"id": "blaster:node-1", "name": "Blaster", "category": "ir", "transport": "ir", "controls": []})
    monkeypatch.setattr(ir, "APPLIANCES", [{"id": "fan_ir", "name": "Fan", "blaster": "node-1"}])
    out = mp.get("/api/map/auto", headers=HOUSE).json()
    assert {"from": "blaster:node-1", "to": "fan_ir", "via": "ir", "note": "Fan"} in out



def test_the_map_json_links_move_into_the_wiring_and_the_file_keeps_none(mp, monkeypatch, tmp_path):
    d = me._load()
    d["links"] = [{"load": "l1", "dev": "sw1", "code": "state", "note": "", "seg": []}, {"load": "l2", "dev": "sw2", "code": "", "note": "", "seg": []}]
    saved, wires = [], [{"id": "a1", "switch": {"dev": "sw1", "code": "state"}, "target": {"load": "l1"}, "note": ""}]
    monkeypatch.setattr(me, "_load", lambda: d)
    monkeypatch.setattr(me, "_save", lambda x: saved.append(x))
    monkeypatch.setattr(core, "wiring_owns", lambda name: True)
    monkeypatch.setattr(core, "wiring", lambda: list(wires))
    monkeypatch.setattr(core, "wiring_edit", lambda change, check=True: wires.__setitem__(slice(None), change(list(wires))))
    assert me._move_stale_links() == 1                               # sw2 -> l2 was only in map.json: moved, not lost
    assert [(e["switch"]["dev"], e["target"]["load"]) for e in wires] == [("sw1", "l1"), ("sw2", "l2")]
    assert saved and saved[-1]["links"] == []
    assert me._move_stale_links() == 0                               # once: the file has none now



def test_a_rename_or_a_merge_moves_the_icons_and_the_things_one_thing_keeps_one_icon():
    mp = sys.modules["yashome_plugins.map"]
    d = {"items": [{"id": "i1", "dev": "tv_ir"}, {"id": "i2", "dev": "tv"}, {"id": "i3", "dev": "lamp"}]}
    things = {"things": [{"dev": "tv", "members": ["tv_ir"]}, {"dev": "tv_ir", "kind": "tv"}]}
    n = mp._rename_refs(d, things, "tv_ir", "tv")
    assert [i["dev"] for i in d["items"]] == ["tv", "lamp"] and n == {"icons": 1, "things": 2}
    assert things["things"] == [{"dev": "tv", "members": []}, {"dev": "tv", "kind": "tv"}]
    d2 = {"items": [{"id": "i1", "dev": "old"}]}
    assert mp._rename_refs(d2, {}, "old", "new") == {"icons": 1, "things": 0} and d2["items"][0]["dev"] == "new"
