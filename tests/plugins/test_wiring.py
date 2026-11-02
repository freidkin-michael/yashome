"""The wiring list (core wiring.json): migrated once from the map's links and the IR appliances' power, behind a backup with a
manifest; a marker keeps a lost or broken file from bringing the old copies back; a taken-over plug-in reads the wiring only and
its old fields stay as they were (a rollback reads them); the dashboard's PUT checks a new entry, a migration keeps every entry."""
import hashlib
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

import app as core

HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
mp = sys.modules["yashome_plugins.map"]
ir = sys.modules["yashome_plugins.ir"]
WALL = {"id": "wall", "name": "Switch 1", "category": "switch", "transport": "z2m", "controls": [],
        "_ctl": {"state_left": {"code": "state_left", "kind": "switch"}, "state_right": {"code": "state_right", "kind": "switch"}}}


IR_M = {"name": "ir", "files": [], "entries": ir._wiring_old}


def MAP_M(tmp_path):
    return {"name": "map", "files": [tmp_path / "map.json"], "entries": mp._wiring_old}


@pytest.fixture
def house(tmp_path, monkeypatch):
    """A house of its own: wiring.json, the marker, backups, map.json and IR appliances in tmp_path; the old state is put back."""
    monkeypatch.setattr(core, "WIRING_PATH", tmp_path / "wiring.json")
    monkeypatch.setattr(core, "WIRING_MARK", tmp_path / ".wiring-migrated")
    monkeypatch.setattr(core, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(mp, "_file", lambda: tmp_path / "map.json")
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    monkeypatch.setitem(core.DEVICES, "wall", WALL)
    monkeypatch.setitem(core.DEVICES, "fix1", {"id": "fix1", "name": "Fan lamp", "transport": "ir", "controls": [], "_ctl": {}})
    monkeypatch.setattr(ir, "APPLIANCES", [{"id": "fix1", "name": "Fan lamp", "remote": "r", "power": {"device": "wall", "code": "state_left"}},
                                           {"id": "fix2", "name": "Old lamp", "remote": "r", "power": {"device": "ghost", "code": "state"}}])
    monkeypatch.setattr(ir, "_sync_locked", lambda: None)
    mp._save({**mp._empty(), "rev": 3, "items": [{"id": "l1", "kind": "load", "dev": "", "name": "Ceiling", "type": "lamp", "x": 0.2, "y": 0.2, "room": ""}],
              "links": [{"load": "l1", "dev": "wall", "code": "state_right", "note": "", "seg": []}]})
    monkeypatch.setattr(core, "WIRING_MIGRATORS", [MAP_M(tmp_path), IR_M])
    monkeypatch.setattr(core, "_wiring", [])
    monkeypatch.setattr(core, "_wiring_state", {"state": "warn", "detail": "", "args": []})
    monkeypatch.setattr(core, "_wiring_postponed", [])
    monkeypatch.setattr(core, "_wiring_taken", set())
    monkeypatch.setattr(ir, "_FEEDS_SEEN", {})
    return tmp_path


FROZEN_POWER = {"device": "wall", "code": "state_left"}


def _feed(aid):
    return ir._power({"id": aid})


def _health():
    return [r for r in core._wiring_health() if r["id"] == "wiring"][0]


def test_the_migration_keeps_every_entry_behind_a_backup_and_runs_once(house):
    core._wiring_boot()
    got = {(e["switch"]["dev"], e["switch"]["code"], json.dumps(e["target"], sort_keys=True)) for e in core.wiring()}
    assert got == {("wall", "state_right", '{"load": "l1"}'), ("wall", "state_left", '{"dev": "fix1"}'), ("ghost", "state", '{"dev": "fix2"}')}
    mark = json.loads((house / ".wiring-migrated").read_text())["migrators"]
    assert (mark["map"]["count"], mark["ir"]["count"]) == (1, 2) and _health()["state"] == "ok"
    bk = next(p for p in (house / "backups").iterdir() if p.name.endswith("-map"))
    man = (bk / "manifest.txt").read_text().split()
    assert man[0] == hashlib.sha256((house / "map.json").read_bytes()).hexdigest() and man[1] == "map.json"
    assert (bk / "map.json").read_bytes() == (house / "map.json").read_bytes()
    core._wiring_boot()                                                       # a second start: read, not migrated again
    assert len(core.wiring()) == 3 and len(list((house / "backups").iterdir())) == 2


def test_a_lost_or_broken_file_after_the_migration_is_never_migrated_again(house):
    core._wiring_boot()
    (house / "wiring.json").unlink()
    core._wiring_boot()
    assert core.wiring() == [] and _health()["state"] == "bad" and "missing" in _health()["detail"]
    with pytest.raises(core.WiringBroken):
        core.wiring_put([])
    assert mp._load()["links"] and ir.APPLIANCES[0]["power"]                    # the old copies are left alone
    (house / "wiring.json").write_text("{not json")
    core._wiring_boot()
    assert core.wiring() == [] and _health()["state"] == "bad"
    assert not (house / "wiring.json").exists() and [p.name for p in house.glob("wiring.json.bad-*")]
    assert len(list((house / "backups").iterdir())) == 2                      # no second migration


def test_a_change_lives_in_the_wiring_only_and_the_old_fields_stay(house):
    core._wiring_boot()
    frozen = (house / "map.json").read_bytes()
    c = TestClient(core.app)
    ws = core.wiring()
    feed = next(e for e in ws if e["target"].get("dev") == "fix1")
    feed["switch"]["code"] = "state_right"                                     # the dashboard moves the fixture to the other key
    ws = [e for e in ws if e["target"].get("load") != "l1"]                     # and unlinks the ceiling lamp
    r = c.put("/api/wiring", headers=HOUSE, json={"entries": ws})
    assert r.status_code == 200, r.text
    assert _feed("fix1") == {"device": "wall", "code": "state_right"} and ir._public_appliance(ir.APPLIANCES[0])["power"] == _feed("fix1")
    assert c.get("/api/map", headers=HOUSE).json()["links"] == [] and ir.APPLIANCES[0]["power"] == FROZEN_POWER
    assert (house / "map.json").read_bytes() == frozen
    cur = c.get("/api/map", headers=HOUSE).json()                             # the map's own save goes into the wiring only
    link = {"load": "l1", "dev": "wall", "code": "state_left", "note": "", "seg": []}
    assert c.put("/api/map", headers=HOUSE, json={"rev": cur["rev"], "rooms": [], "items": cur["items"], "links": [link]}).status_code == 200
    assert c.get("/api/map", headers=HOUSE).json()["links"] == [link] and mp._load()["links"] == [dict(link, code="state_right")]
    first = [e for e in core.wiring() if e["target"].get("load") == "l1"]
    assert [(e["switch"]["code"], e["target"]) for e in first] == [("state_left", {"load": "l1"})]
    cur = c.get("/api/map", headers=HOUSE).json()
    c.put("/api/map", headers=HOUSE, json={"rev": cur["rev"], "rooms": [], "items": cur["items"], "links": [link]})
    assert [e["id"] for e in core.wiring() if e["target"].get("load") == "l1"] == [first[0]["id"]]      # kept, not made again
    assert any(e["switch"]["dev"] == "ghost" for e in core.wiring())        # an unknown device kept as it was


def test_the_dashboard_s_put_checks_only_what_it_adds(house):
    core._wiring_boot()
    c = TestClient(core.app)
    ws = core.wiring()
    assert c.put("/api/wiring", json={"entries": ws}).status_code == 401
    assert c.put("/api/wiring", headers=HOUSE, json={"entries": ws}).status_code == 200      # the ghost entry is not looked up again
    for bad in ({"switch": {"dev": "nobody", "code": ""}, "target": {"dev": "fix1"}},
                {"switch": {"dev": "wall", "code": "state_up"}, "target": {"dev": "fix1"}},
                {"switch": {"dev": "wall", "code": "state_left"}, "target": {"dev": "nothing"}},
                {"switch": {"dev": "wall", "code": "state_left"}, "target": {"dev": "fix1", "load": "l1"}},
                {"switch": {"dev": "wall"}, "target": {"load": "l1", "seg": [1]}}):
        r = c.put("/api/wiring", headers=HOUSE, json={"entries": ws + [bad]})
        assert r.status_code == 400, (bad, r.text)
    r = c.put("/api/wiring", headers=HOUSE, json={"entries": ws + [dict(ws[0], id="x1")]})
    assert r.status_code == 400 and "twice" in r.json()["detail"]
    assert c.get("/api/wiring", headers=HOUSE).json()["state"] == "ok"


def test_a_house_with_nothing_to_take_over_gets_no_file_until_its_first_change(house):
    house.joinpath("map.json").unlink()
    ir.APPLIANCES[:] = []
    core._wiring_boot()
    assert not (house / "wiring.json").exists() and not (house / ".wiring-migrated").exists() and not (house / "backups").exists()
    assert _health()["state"] == "ok"
    core.wiring_put([{"switch": {"dev": "wall", "code": "state_left"}, "target": {"dev": "fix1"}}])
    assert (house / ".wiring-migrated").exists() and len(json.loads((house / "wiring.json").read_text())) == 1


def test_a_plug_in_off_at_the_first_start_is_taken_over_when_it_is_on_and_its_links_survive(house, monkeypatch):
    links = mp._load()["links"]
    monkeypatch.setattr(core, "WIRING_MIGRATORS", [IR_M])                     # the map off (or failed) at this start
    core._wiring_boot()
    core._wiring_notify()
    assert {e["target"].get("dev") for e in core.wiring()} == {"fix1", "fix2"} and mp._load()["links"] == links
    core.wiring_edit(lambda ws: ws, check=False)                              # a change meanwhile: the map's copy is not touched
    assert mp._load()["links"] == links
    monkeypatch.setattr(core, "WIRING_MIGRATORS", [MAP_M(house), IR_M])     # the next start, the map on
    core._wiring_boot()
    core._wiring_notify()
    assert [e["target"] for e in core.wiring() if e["target"].get("load")] == [{"load": "l1"}]
    assert mp._load()["links"] == links and set(json.loads((house / ".wiring-migrated").read_text())["migrators"]) == {"ir", "map"}


def test_an_ir_change_and_a_map_save_at_once_both_survive_everywhere(house):
    import threading
    core._wiring_boot()
    c = TestClient(core.app)
    errors = []

    def ir_side():
        for i in range(15):
            try:
                core.wiring_edit(lambda ws, i=i: [e for e in ws if e["target"].get("dev") != "fix1"]
                                 + [{"switch": {"dev": "wall", "code": ("state_left", "state_right")[i % 2]}, "target": {"dev": "fix1"}}])
            except Exception as e:                                            # noqa: BLE001
                errors.append(repr(e))

    def map_side():
        for i in range(15):
            cur = c.get("/api/map", headers=HOUSE).json()
            link = {"load": "l1", "dev": "wall", "code": ("state_left", "state_right")[i % 2], "note": "", "seg": []}
            r = c.put("/api/map", headers=HOUSE, json={"rev": cur["rev"], "rooms": [], "items": cur["items"], "links": [link]})
            if r.status_code != 200:
                errors.append(r.text)
    ts = [threading.Thread(target=ir_side), threading.Thread(target=map_side)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not any(t.is_alive() for t in ts) and errors == []                 # no deadlock, nothing refused
    w = core.wiring()
    feed = [e["switch"]["code"] for e in w if e["target"].get("dev") == "fix1"]
    lamp = [e["switch"]["code"] for e in w if e["target"].get("load") == "l1"]
    assert feed == ["state_left"] and lamp == ["state_left"]                  # the last of each side (i = 14)
    assert _feed("fix1") == {"device": "wall", "code": "state_left"}
    assert [(ln["dev"], ln["code"]) for ln in c.get("/api/map", headers=HOUSE).json()["links"]] == [("wall", "state_left")]
    assert json.loads((house / "wiring.json").read_text()) == w


def _read_only(path):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o500)


def test_a_read_only_backups_dir_backs_up_into_plugin_state(house, monkeypatch):
    _read_only(house / "backups")
    monkeypatch.setattr(core, "PLUGIN_STATE", house / "plugin-state")
    core._wiring_boot()
    assert _health()["state"] == "ok" and len(core.wiring()) == 3
    kept = sorted(p.name for p in (house / "plugin-state").iterdir())
    assert len(kept) == 2 and all(n.startswith("wiring-migration-") for n in kept)
    assert (house / "plugin-state" / kept[0] / "manifest.txt").is_file()
    (house / "backups").chmod(0o700)


def test_nowhere_to_back_up_postpones_the_migration_and_the_old_copies_stay_in_charge(house, monkeypatch):
    _read_only(house / "backups")
    _read_only(house / "plugin-state")
    monkeypatch.setattr(core, "PLUGIN_STATE", house / "plugin-state")
    links = mp._load()["links"]
    core._wiring_boot()
    assert _health()["state"] == "warn" and "postponed" in _health()["detail"] and core.wiring_postponed()
    assert not (house / "wiring.json").exists() and not (house / ".wiring-migrated").exists()
    with pytest.raises(core.WiringBroken):
        core.wiring_put([])
    c = TestClient(core.app)                                                 # the map saves its links itself, as before
    cur = c.get("/api/map", headers=HOUSE).json()
    link = {"load": "l1", "dev": "wall", "code": "state_left", "note": "", "seg": []}
    assert c.put("/api/map", headers=HOUSE, json={"rev": cur["rev"], "rooms": [], "items": cur["items"], "links": [link]}).status_code == 200
    assert mp._load()["links"] == [link] and links != [link]
    ir.ir_appliance_patch("fix1", ir.AppliancePatch(power={"device": "wall", "code": "state_right"}))   # IR writes its own power
    assert ir.APPLIANCES[0]["power"] == {"device": "wall", "code": "state_right"} and not (house / "wiring.json").exists()
    for d in ("backups", "plugin-state"):
        (house / d).chmod(0o700)


def test_a_device_key_change_moves_its_wiring_entries_and_leaves_the_old_fields(house, monkeypatch):
    core._wiring_boot()
    frozen = (house / "map.json").read_bytes()
    monkeypatch.setitem(core.DEVICES, "wall2", dict(WALL, id="wall2"))
    core.wiring_put(core.wiring() + [{"switch": {"dev": "wall2", "code": "state_right"}, "target": {"load": "l1"}}], check=False)
    touched = core.migrate_device_key("wall", "wall2")                       # zigbee rename / relink: the key itself changes
    assert touched["wiring"] == 2 and not any(e["switch"]["dev"] == "wall" for e in core.wiring())
    assert [e["target"] for e in core.wiring() if e["target"].get("load")] == [{"load": "l1"}]      # the same entry twice: kept once
    assert _feed("fix1") == {"device": "wall2", "code": "state_left"} and ir.APPLIANCES[0]["power"] == FROZEN_POWER
    assert (house / "map.json").read_bytes() == frozen
    assert core.migrate_device_key("nobody", "wall2")["wiring"] == 0


def test_a_removed_feed_device_keeps_its_wiring_entry(house):
    core._wiring_boot()
    ir._wiring_changed()
    core.store_status_exact("fix1", {"online": True, "values": {"power": True}, "raw": {}})
    before = core.wiring()
    ir._on_removed(WALL)                                                     # removed, or swept by its system: not an owner's act
    assert core.wiring() == before and _feed("fix1") == {"device": "wall", "code": "state_left"}
    assert "power" not in (core.status_copy("fix1", {}).get("values") or {})   # the gone feed's last word is forgotten


def test_a_rollback_reads_the_frozen_old_fields(house, monkeypatch):
    core._wiring_boot()
    frozen_links = mp._load()["links"]
    core.wiring_put([], check=False)                                         # every change after the switch is in the wiring only
    assert _feed("fix1") is None and TestClient(core.app).get("/api/map", headers=HOUSE).json()["links"] == []
    monkeypatch.setattr(core, "_wiring_taken", set())                       # an older build: the old copies are in charge
    assert mp._load()["links"] == frozen_links and mp._wiring_old() and len(ir._wiring_old()) == 2
    assert _feed("fix1") == FROZEN_POWER and TestClient(core.app).get("/api/map", headers=HOUSE).json()["links"] == frozen_links
    assert ir._public_appliance(ir.APPLIANCES[0])["power"] == FROZEN_POWER
