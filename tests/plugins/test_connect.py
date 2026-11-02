"""connect: one Add / bind window opens every pairing window at once and closes them all; a code finds the waiting
tablet or ESP screen that shows it; a source that fails or is off does not stop the others; house token only."""
import os
import sys

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.connect"]
pn = sys.modules["yashome_plugins.panels"]
zb = sys.modules["yashome_plugins.zigbee"]
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


@pytest.fixture
def c(monkeypatch):
    for f in (pn.PAIR_DB, pn.PAIR_FLAG):
        f.unlink(missing_ok=True)
    monkeypatch.setattr(pn._esp, "_new", {})
    yield TestClient(core.app)
    for f in (pn.PAIR_DB, pn.PAIR_FLAG):
        f.unlink(missing_ok=True)


def test_start_opens_every_window_and_a_failing_source_is_named(c, monkeypatch):
    calls = []
    monkeypatch.setattr(me, "_owners", {})
    monkeypatch.setattr(me, "_ours", set())
    monkeypatch.setattr(zb, "pair_start", lambda: (_ for _ in ()).throw(HTTPException(503, "zigbee2mqtt is offline")))
    monkeypatch.setattr(zb, "pair_stop", lambda: calls.append("zb-stop"))
    assert c.post("/api/connect/start").status_code in (401, 403)                 # the house token only
    assert c.post("/api/connect/start", headers=HOUSE, json={}).status_code == 400     # no window id: an older page
    r = c.post("/api/connect/start", headers=HOUSE, json={"window": "A"}).json()
    assert r["opened"] == ["tablet"] and r["errors"] == {"zigbee": "zigbee2mqtt is offline"} and r["started"] == ["tablet"]
    assert pn._pair_load(pn.PAIR_FLAG, {})["until"] > 0                            # tablets are invited
    assert c.post("/api/connect/start", headers=HOUSE, json={"window": "A"}).json()["started"] == []     # renewed: not started again
    r = c.post("/api/connect/stop", headers=HOUSE, json={"window": "A"}).json()
    assert r["closed"] == ["tablet"] and calls == [] and pn._pair_load(pn.PAIR_FLAG, {})["until"] == 0


def test_a_way_stays_open_while_another_window_or_another_page_keeps_it(c, monkeypatch):
    calls = []
    monkeypatch.setattr(me, "_owners", {})
    monkeypatch.setattr(me, "_ours", set())
    monkeypatch.setitem(zb._pairing, "active", True)
    monkeypatch.setitem(zb._pairing, "until", 9e12)                                 # the Zigbee page opened pairing before
    monkeypatch.setattr(zb, "pair_start", lambda: calls.append("zb-start"))
    monkeypatch.setattr(zb, "pair_stop", lambda: calls.append("zb-stop"))
    assert c.post("/api/connect/start", headers=HOUSE, json={"window": "A"}).json()["started"] == ["tablet"]
    c.post("/api/connect/start", headers=HOUSE, json={"window": "B"})              # a second window
    assert c.post("/api/connect/stop", headers=HOUSE, json={"window": "A"}).json()["closed"] == []      # B still keeps the invite
    assert pn._pair_load(pn.PAIR_FLAG, {})["until"] > 0
    assert c.post("/api/connect/stop", headers=HOUSE, json={"window": "B"}).json()["closed"] == ["tablet"]
    assert "zb-stop" not in calls                                                  # Zigbee was the Zigbee page's: it stays open


def test_a_renewed_zigbee_window_keeps_the_devices_that_joined(monkeypatch):
    monkeypatch.setattr(zb, "ENABLED", True)
    monkeypatch.setattr(zb, "_bridge_online", True)
    sent = []

    class Cli:
        def publish(self, t, p):
            sent.append(t)
            return type("R", (), {"rc": 0})()

        def is_connected(self):
            return True
    monkeypatch.setattr(zb, "_cli", lambda: Cli())
    monkeypatch.setitem(zb._pairing, "active", False)
    zb.pair_start()
    zb._pairing_found["0x1"] = {"id": "0x1"}
    zb.pair_start()                                                                # renewed while open
    assert "0x1" in zb._pairing_found
    zb.pair_stop()
    zb.pair_start()                                                                # a new window
    assert "0x1" not in zb._pairing_found


def test_a_code_finds_the_tablet_or_the_screen_that_shows_it(c, monkeypatch):
    c.post("/api/panels/pairing", headers=HOUSE, json={"minutes": 5})
    assert c.post("/api/panels/register", json={"id": "tab9", "code": "482913", "name": "Kitchen", "model": "SM-T500"}).status_code == 200
    monkeypatch.setitem(pn._esp._new, "aa:bb:cc:dd:ee:ff", {"mac": "aa:bb:cc:dd:ee:ff", "hw": "panel7", "code": "771204", "ip": "192.0.2.9",
                                                            "seen": __import__("time").time()})
    m = c.post("/api/connect/code", headers=HOUSE, json={"code": "482913"}).json()["matches"]
    assert m == [{"kind": "tablet", "id": "tab9", "model": "SM-T500", "name": "Kitchen", "again": False, "panel": ""}]
    m = c.post("/api/connect/code", headers=HOUSE, json={"code": " 771204 "}).json()["matches"]
    assert [(x["kind"], x["id"], x["model"]) for x in m] == [("esp_screen", "aa:bb:cc:dd:ee:ff", "panel7")]
    assert c.post("/api/connect/code", headers=HOUSE, json={"code": "000000"}).json()["matches"] == []
    assert c.post("/api/connect/code", headers=HOUSE, json={"code": " "}).status_code == 400
    assert c.post("/api/connect/code", json={"code": "482913"}).status_code in (401, 403)


def test_a_bound_tablet_showing_its_code_is_bound_again_without_a_new_invitation(c, monkeypatch):
    monkeypatch.setattr(pn, "_layouts", {})
    monkeypatch.setattr(pn, "_save_layouts", lambda: None)
    c.post("/api/panels/pairing", headers=HOUSE, json={"minutes": 5})
    c.post("/api/panels/register", json={"id": "tab7", "code": "936039", "name": "P7", "model": "PRITOM P7"})
    assert c.post("/api/panels/bind", headers=HOUSE, json={"id": "tab7", "code": "936039", "panel": "hall"}).status_code == 200
    key = c.get("/api/panels/register/tab7").json()["token"]
    m = c.post("/api/connect/code", headers=HOUSE, json={"code": "936039"}).json()["matches"]
    assert m == [{"kind": "tablet", "id": "tab7", "model": "PRITOM P7", "name": "P7", "bound": True, "panel": "hall"}]
    before = pn._pair_load(pn.PAIR_DB, {})["tab7"]["key_sha256"]
    r = c.post("/api/panels/bind", headers=HOUSE, json={"id": "tab7", "code": "936039", "panel": "kitchen", "keep_key": True})
    assert r.status_code == 200 and pn._pair_load(pn.PAIR_DB, {})["tab7"]["key_sha256"] == before        # its key works on
    beat = c.post("/api/panels/heartbeat", headers={"Authorization": "Bearer " + key}, json={"id": "tab7"})
    assert beat.status_code == 200 and beat.json()["panel"] == "kitchen"                                # it learns the panel
    again = c.get("/api/panels/register/tab7").json()                                                   # its pairing screen polls
    assert again["token"].startswith("ypk_") and again["token"] != key
    assert c.get("/api/panels/register/tab7").json().get("token") is None                               # once


def test_a_window_not_heard_of_for_its_lease_lets_go(c, monkeypatch):
    calls = []
    monkeypatch.setattr(me, "_owners", {})
    monkeypatch.setattr(me, "_ours", set())
    monkeypatch.setattr(zb, "pair_start", lambda: calls.append("zb-start"))
    monkeypatch.setattr(zb, "pair_stop", lambda: calls.append("zb-stop"))
    monkeypatch.setitem(zb._pairing, "active", False)
    c.post("/api/connect/start", headers=HOUSE, json={"window": "DEAD"})            # a tab that dies: no stop ever comes
    c.post("/api/connect/start", headers=HOUSE, json={"window": "LIVE"})
    for k in list(me._owners):
        me._owners[k]["DEAD"] -= me.LEASE_S + 1                                    # its lease ran out
    assert c.post("/api/connect/stop", headers=HOUSE, json={"window": "LIVE"}).json()["closed"] == ["zigbee", "tablet"]
    assert me._owners == {} and "zb-stop" in calls


def test_a_window_back_after_its_lease_or_a_failed_renewal_keeps_its_hold(c, monkeypatch):
    calls, fail = [], []
    monkeypatch.setattr(me, "_owners", {})
    monkeypatch.setattr(me, "_ours", set())
    monkeypatch.setattr(zb, "pair_stop", lambda: calls.append("zb-stop"))
    def start():
        if fail:
            raise HTTPException(503, "z2m away")
    monkeypatch.setattr(zb, "pair_start", start)
    monkeypatch.setitem(zb._pairing, "active", False)
    c.post("/api/connect/start", headers=HOUSE, json={"window": "PHONE"})
    me._owners["zigbee"]["PHONE"] -= me.LEASE_S + 1                                # the phone slept longer than its lease
    c.post("/api/connect/start", headers=HOUSE, json={"window": "PHONE"})
    assert "zb-stop" not in calls and "PHONE" in me._owners["zigbee"]              # not closed under it: the joins it found stay
    me._owners["zigbee"]["PHONE"] -= me.LEASE_S - 1
    fail.append(1)
    c.post("/api/connect/start", headers=HOUSE, json={"window": "PHONE"})           # a renewal whose start failed
    me._owners["zigbee"]["PHONE"] -= 2
    c.post("/api/connect/start", headers=HOUSE, json={"window": "PHONE"})
    assert "zb-stop" not in calls
