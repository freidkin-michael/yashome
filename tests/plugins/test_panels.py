"""panels: a tablet gets a key of its own once, after a human bound it, and the key opens only a panel's
needs; layouts are checked (topics, hardware, widgets) and merged; rename, delete and device renames."""
import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.panels"]
CLEAR = me._clear_retained
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


def _key(k):
    return {"Authorization": "Bearer " + k}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(me, "_layouts", {})
    monkeypatch.setattr(me, "_BEATS", {})
    monkeypatch.setattr(me, "_publish", lambda name: None)
    monkeypatch.setattr(me, "_clear_retained", lambda name: 0)
    for f in (me.PAIR_DB, me.PAIR_FLAG, me.LAYOUTS):
        f.unlink(missing_ok=True)
    yield
    for f in (me.PAIR_DB, me.PAIR_FLAG, me.LAYOUTS):
        f.unlink(missing_ok=True)


def _paired(c, dev="tab1", panel="hall", code="123456"):
    """A tablet registered and bound; returns its key."""
    assert c.post("/api/panels/pairing", headers=HOUSE, json={"minutes": 5}).status_code == 200
    r = c.post("/api/panels/register", json={"id": dev, "code": code, "name": "Tab",
                                             "caps": {"screen": {"w": 600, "h": 1024}, "battery": 50}})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/panels/register/{dev}").json() == {"status": "pending"}
    r = c.post("/api/panels/bind", headers=HOUSE, json={"id": dev, "code": code, "panel": panel})
    assert r.status_code == 200 and (r.json()["w"], r.json()["h"]) == (1024, 600)
    return c.get(f"/api/panels/register/{dev}").json()["token"]


def test_the_key_goes_out_once_and_only_after_a_bind():
    c = TestClient(core.app)
    key = _paired(c)
    assert key.startswith("ypk_") and key != os.environ["DASHBOARD_TOKEN"]
    again = c.get("/api/panels/register/tab1").json()
    assert again["status"] == "bound" and "token" not in again
    assert key not in me.PAIR_DB.read_text()
    assert "key_sha256" not in json.dumps(c.get("/api/panels/discovered", headers=HOUSE).json())


def test_a_bind_with_the_wrong_code_or_another_address_gives_no_key(monkeypatch):
    c = TestClient(core.app)
    c.post("/api/panels/pairing", headers=HOUSE, json={"minutes": 5})
    c.post("/api/panels/register", json={"id": "tab2", "code": "111111"})
    assert c.post("/api/panels/bind", headers=HOUSE, json={"id": "tab2", "code": "222222", "panel": "p"}).status_code == 400
    assert c.post("/api/panels/bind", headers=HOUSE, json={"id": "tab2", "code": "111111", "panel": "p"}).status_code == 200
    monkeypatch.setattr(me, "_ip", lambda request: "192.0.2.99")
    assert "token" not in c.get("/api/panels/register/tab2").json()


def test_a_tablet_paired_again_keeps_its_record_and_its_old_key_dies():
    c = TestClient(core.app)
    key1 = _paired(c, dev="and-0123456789abcdef")
    assert c.post("/api/panels/register", json={"id": "and-0123456789abcdef", "code": "999999"}).status_code == 200
    assert c.get("/api/panels/register/and-0123456789abcdef").json() == {"status": "pending"}
    reg = c.get("/api/panels/discovered", headers=HOUSE).json()["registered"]
    assert [(r["id"], r["status"], r["repairs"]) for r in reg] == [("and-0123456789abcdef", "pending", "hall")]
    assert "999999" not in str(reg)
    beat = {"id": "and-0123456789abcdef"}
    assert c.post("/api/panels/heartbeat", headers=_key(key1), json=beat).status_code == 200   # until a human checks
    assert c.post("/api/panels/bind", headers=HOUSE, json={**beat, "code": "123456", "panel": "hall"}).status_code == 400
    assert c.post("/api/panels/bind", headers=HOUSE, json={**beat, "code": "999999", "panel": "hall"}).status_code == 200
    key2 = c.get("/api/panels/register/and-0123456789abcdef").json()["token"]
    assert c.post("/api/panels/heartbeat", headers=_key(key1), json=beat).status_code == 401
    assert c.post("/api/panels/heartbeat", headers=_key(key2), json=beat).json()["panel"] == "hall"
    assert list(json.loads(me.PAIR_DB.read_text())) == ["and-0123456789abcdef"]


def test_an_old_short_id_takes_the_full_android_id_in_place():
    c = TestClient(core.app)
    key = _paired(c, dev="and-01234567")
    me._layouts["hall"]["widgets"] = [{"type": "camera", "cam_id": "and-01234567-front"}]
    other = _paired(c, dev="tab9", panel="porch", code="555555")
    full = {"id": "and-0123456789abcdef"}
    assert c.post("/api/panels/heartbeat", headers=_key(other), json=full).status_code in (403, 404)
    assert "and-01234567" in json.loads(me.PAIR_DB.read_text())               # another panel's key renames nothing
    assert c.post("/api/panels/heartbeat", headers=_key(key), json=full).json()["panel"] == "hall"
    assert sorted(json.loads(me.PAIR_DB.read_text())) == ["and-0123456789abcdef", "tab9"]
    assert me._layouts["hall"]["device"] == "and-0123456789abcdef"
    assert me._layouts["hall"]["widgets"][0]["cam_id"] == "and-0123456789abcdef-front"


def test_the_bound_tablets_own_register_before_its_poll_changes_nothing():
    c = TestClient(core.app)
    assert c.post("/api/panels/pairing", headers=HOUSE, json={"minutes": 5}).status_code == 200
    c.post("/api/panels/register", json={"id": "tabx", "code": "424242"})
    assert c.post("/api/panels/bind", headers=HOUSE, json={"id": "tabx", "code": "424242", "panel": "hall"}).status_code == 200
    assert c.post("/api/panels/register", json={"id": "tabx", "code": "424242"}).json() == {"status": "bound"}
    assert "token" in c.get("/api/panels/register/tabx").json()


def test_pair_again_with_the_same_code_after_the_key_went_out_parks_a_repair():
    c = TestClient(core.app)
    key = _paired(c)
    assert c.post("/api/panels/register", json={"id": "tab1", "code": "123456"}).json() == {"status": "pending"}
    assert c.get("/api/panels/register/tab1").json() == {"status": "pending"}
    assert c.post("/api/panels/bind", headers=HOUSE, json={"id": "tab1", "code": "123456", "panel": "Bad Name"}).status_code == 400
    assert me._layouts["hall"]["device"] == "tab1" and "repair" in json.loads(me.PAIR_DB.read_text())["tab1"]
    assert c.post("/api/panels/bind", headers=HOUSE, json={"id": "tab1", "code": "123456", "panel": "hall"}).status_code == 200
    assert "token" in c.get("/api/panels/register/tab1").json()
    assert c.post("/api/panels/heartbeat", headers=_key(key), json={"id": "tab1"}).status_code == 401


def test_a_tablet_bound_to_another_panel_leaves_the_first_and_an_old_record_goes():
    c = TestClient(core.app)
    _paired(c, dev="and-01234567")
    c.post("/api/panels/register", json={"id": "and-0123456789abcdef", "code": "313131"})
    assert c.post("/api/panels/bind", headers=HOUSE, json={"id": "and-0123456789abcdef", "code": "313131",
                                                           "panel": "kitchen"}).status_code == 200
    assert list(json.loads(me.PAIR_DB.read_text())) == ["and-0123456789abcdef"]   # the reinstall from an old launcher
    assert me._layouts["kitchen"]["device"] == "and-0123456789abcdef" and "device" not in me._layouts["hall"]


def test_only_the_house_token_binds_unbinds_and_deletes(monkeypatch):
    c = TestClient(core.app)
    _paired(c)
    monkeypatch.setattr(core, "TOKEN_CHECKS", core.TOKEN_CHECKS + [lambda tok, m, path: tok == "a-full-phone"])
    phone = {"Authorization": "Bearer a-full-phone"}
    assert c.delete("/api/panels/bind/tab1", headers=phone).status_code == 403
    assert c.delete("/api/panels/hall", headers=phone).status_code == 403
    assert c.post("/api/panels/bind", headers=phone, json={"id": "tab1", "code": "123456", "panel": "x"}).status_code == 403


def test_registering_needs_the_pairing_window():
    c = TestClient(core.app)
    assert c.post("/api/panels/register", json={"id": "x", "code": "1"}).status_code == 403
    c.post("/api/panels/pairing", headers=HOUSE, json={"off": True})
    assert c.post("/api/panels/register", json={"id": "x", "code": "1"}).status_code == 403
    assert c.post("/api/panels/pairing", json={"minutes": 5}).status_code == 401


class _Ws:
    def __init__(self, key):
        self.headers, self.query_params = {}, {"token": key}


def test_the_key_opens_only_its_panel_and_the_devices_on_it():
    c = TestClient(core.app)
    key = _paired(c)
    on, off = sorted(core.DEVICES)[:2]
    me._layouts["hall"]["widgets"] = [_w(cmd_topic=f"home/cmd/{on}/state", state_topic=f"home/state/{on}/state")]
    me._layouts["other"] = {"hw": "waveshare7", "w": 800, "h": 480, "widgets": []}
    assert c.get(f"/api/devices/{on}/status", headers=_key(key)).status_code == 200
    assert c.get(f"/api/devices/{off}/status", headers=_key(key)).status_code == 401
    assert c.post(f"/api/devices/{off}/control", headers=_key(key), json={"code": "x", "value": 1}).status_code == 401
    assert c.post(f"/api/devices/{on}/status", headers=_key(key), json={}).status_code in (401, 405)
    assert [d["id"] for d in c.get("/api/devices", headers=_key(key)).json()] == [on]
    assert len(c.get("/api/devices", headers=HOUSE).json()) > 1
    assert set(me._ws_trim(_Ws(key), {on: 1, off: 2})) == {on}
    assert c.get("/api/panels/hall/layout", headers=_key(key)).status_code == 200
    assert c.get("/api/panels/other/layout", headers=_key(key)).status_code == 401
    assert c.get("/api/camera/camera_x/snapshot", headers=_key(key)).status_code == 401
    for m, path in (("GET", "/api/settings"), ("GET", "/api/panels"), ("PUT", "/api/panels/hall/layout"),
                    ("POST", "/api/panels/bind"), ("POST", "/api/panels/pairing"), ("GET", f"/api/devices/{on}/rules"),
                    ("POST", "/api/signin/start"), ("GET", "/api/backups"), ("GET", "/api/rooms"),
                    ("GET", "/api/panels/discovered"), ("DELETE", "/api/panels/hall")):
        assert c.request(m, path, headers=_key(key), json={}).status_code == 401, path
    assert c.post("/api/panels/heartbeat", headers=_key(key), json={"id": "tab1"}).json()["panel"] == "hall"
    assert c.get("/api/devices", headers=_key("ypk_not-a-key")).status_code == 401
    c.delete("/api/panels/bind/tab1", headers=HOUSE)
    assert me._ws_trim(_Ws(key), {on: 1, off: 2}) == {}


def test_only_register_and_its_poll_are_open():
    c = TestClient(core.app)
    me._layouts["p"] = {"hw": "android", "widgets": []}
    opened = set()
    for r in core.app.router.routes:
        path, methods = getattr(r, "path", ""), getattr(r, "methods", None) or set()
        if not (path.startswith("/api/panels") and getattr(r, "endpoint", None).__module__ == me.__name__):
            continue
        url = path.replace("{name}", "p").replace("{dev_id}", "x")
        for m in methods - {"HEAD"}:
            if c.request(m, url, json={}).status_code != 401:
                opened.add((m, path))
    assert opened == {("POST", "/api/panels/register"), ("GET", "/api/panels/register/{dev_id}")}


def test_the_key_window_closes_after_ten_minutes():
    c = TestClient(core.app)
    c.post("/api/panels/pairing", headers=HOUSE, json={"minutes": 5})
    c.post("/api/panels/register", json={"id": "late", "code": "1"})
    c.post("/api/panels/bind", headers=HOUSE, json={"id": "late", "code": "1", "panel": "p"})
    db = json.loads(me.PAIR_DB.read_text())
    db["late"]["bound_at"] -= me.KEY_WINDOW_S + 1
    me.PAIR_DB.write_text(json.dumps(db))
    assert "token" not in c.get("/api/panels/register/late").json()


def test_register_limits(monkeypatch):
    c = TestClient(core.app)
    c.post("/api/panels/pairing", headers=HOUSE, json={"minutes": 5})
    assert c.post("/api/panels/register", json={"id": "big", "code": "1", "caps": {"cameras": "y" * 5000}}).status_code == 413
    assert c.post("/api/panels/register", json={"id": "big", "code": "1", "cams": ["y" * 5000]}).status_code == 413
    assert c.post("/api/panels/register", json={"id": "n", "code": "1", "name": "z" * 100}).status_code == 422
    assert c.post("/api/panels/register", json={"id": "k", "code": "1", "caps": {"junk": 1, "mic": True}}).status_code == 200
    assert json.loads(me.PAIR_DB.read_text())["k"]["caps"] == {"mic": True}
    for i in range(me.PENDING_PER_IP - 1):
        assert c.post("/api/panels/register", json={"id": f"t{i}", "code": "1"}).status_code == 200
    assert c.post("/api/panels/register", json={"id": "one-too-many", "code": "1"}).status_code == 429
    assert c.post("/api/panels/register", json={"id": "t0", "code": "1"}).status_code == 200
    monkeypatch.setattr(me, "_ip", lambda request: "192.0.2.7")
    assert c.post("/api/panels/register", json={"id": "t0", "code": "1"}).status_code == 409


def test_many_addresses_never_push_out_a_tablet_that_just_registered(monkeypatch):
    c = TestClient(core.app)
    c.post("/api/panels/pairing", headers=HOUSE, json={"minutes": 5})
    ips = iter(f"192.0.2.{i}" for i in range(1, 200))
    monkeypatch.setattr(me, "_ip", lambda request: next(ips))
    codes = [c.post("/api/panels/register", json={"id": f"t{i}", "code": "1"}).status_code for i in range(me.MAX_PENDING + 3)]
    assert codes.count(200) == me.MAX_PENDING and set(codes[me.MAX_PENDING:]) == {429}
    db = json.loads(me.PAIR_DB.read_text())
    for d in db.values():
        d["seen"] -= 120
    me.PAIR_DB.write_text(json.dumps(db))
    assert c.post("/api/panels/register", json={"id": "late", "code": "1"}).status_code == 200
    assert len(json.loads(me.PAIR_DB.read_text())) == me.MAX_PENDING


def test_a_tile_reaches_a_device_by_any_topic_it_declares_and_its_own_camera(monkeypatch):
    c = TestClient(core.app)
    key = _paired(c)
    monkeypatch.setitem(core.DEVICES, "thermo", {"id": "thermo", "name": "t", "controls": [
        {"code": "temperature", "state_topic": "zigbee2mqtt/thermo"}], "_ctl": {}})
    me._layouts["hall"]["widgets"] = [_w(type="sensor", state_topic="zigbee2mqtt/thermo"), _w(id="w2", type="camera")]
    assert c.get("/api/devices/thermo/status", headers=_key(key)).status_code != 401
    assert me._refs("hall")[1] == {"tab1-front"}


def test_a_key_cannot_heartbeat_for_another_tablet():
    c = TestClient(core.app)
    key = _paired(c)
    _paired(c, dev="tab3", panel="kitchen", code="654321")
    assert c.post("/api/panels/heartbeat", headers=_key(key), json={"id": "tab3"}).status_code == 403
    assert c.post("/api/panels/heartbeat", headers=HOUSE, json={"id": "tab3"}).status_code == 200


def test_unbind_rebind_and_delete_kill_the_key():
    c = TestClient(core.app)
    key = _paired(c)
    assert c.delete("/api/panels/bind/tab1", headers=HOUSE).status_code == 200
    assert c.get("/api/devices", headers=_key(key)).status_code == 401
    key2 = _paired(c)
    key3 = _paired(c, dev="tab1b", code="777777")
    assert c.post("/api/panels/bind", headers=HOUSE, json={"id": "tab1", "code": "123456", "panel": "hall"}).status_code == 200
    assert c.get("/api/devices", headers=_key(key2)).status_code == 401
    assert c.delete("/api/panels/hall", headers=HOUSE).json()["unpaired"] == ["tab1", "tab1b"]
    assert c.get("/api/devices", headers=_key(key3)).status_code == 401


def test_rename_moves_the_tablet_and_old_names_still_answer():
    c = TestClient(core.app)
    key = _paired(c)
    assert c.post("/api/panels/hall/rename", headers=HOUSE, json={"to": "entry"}).json()["devices"] == ["tab1"]
    assert c.get("/api/panels/hall/layout", headers=_key(key)).status_code == 200
    assert c.get("/api/panels/entry/layout", headers=_key(key)).status_code == 200
    me._layouts["esp"] = {"hw": "waveshare7", "widgets": []}
    assert c.post("/api/panels/esp/rename", headers=HOUSE, json={"to": "esp2"}).status_code == 400
    assert c.post("/api/panels/entry/rename", headers=HOUSE, json={"to": "esp"}).status_code == 409


def _w(**kw):
    return {"id": "w1", "type": "toggle", "x": 0, "y": 0, "w": 100, "h": 50, **kw}


def test_layouts_are_checked_and_merged():
    c = TestClient(core.app)
    put = lambda name, **body: c.put(f"/api/panels/{name}/layout", headers=HOUSE, json=body)
    assert put("p1", widgets=[]).status_code == 400
    assert put("p1", hw="nope", widgets=[]).status_code == 400
    dev = sorted(core.DEVICES)[0]
    assert put("p1", hw="waveshare7", widgets=[_w(cmd_topic=f"home/cmd/{dev}/state")]).status_code == 200
    me._layouts["p1"]["i2c_sensors"] = True
    assert put("p1", widgets=[_w(cmd_topic="zigbee2mqtt/lamp/set")]).status_code == 400
    for bad in (f"home/state/{dev}/state", "home/cmd/#", "home/cmd/+/state", "home/cmd/no_such_device/state", f"home/cmd/{dev}"):
        assert put("p1", widgets=[_w(cmd_topic=bad)]).status_code == 400, bad
    assert put("p1", widgets=[_w(power_cmd=f"home/state/{dev}/y")]).status_code == 400
    assert put("p1", widgets=[_w(type="camera")]).status_code == 400
    assert put("p1", widgets=[_w(), _w()]).status_code == 400
    assert put("p1", widgets=[_w(bg="#12345")]).status_code == 400
    assert put("p1", hw="knob128", widgets=[]).status_code == 400
    assert put("p1", widgets=[_w(type="climate")]).status_code == 400
    assert put("p1", rotation=45, widgets=[]).status_code == 400
    assert put("p1", title="Hall", rotation=90, widgets=[_w(type="clock")]).status_code == 200
    lay = c.get("/api/panels/p1/layout", headers=HOUSE).json()
    assert lay["i2c_sensors"] is True and lay["title"] == "Hall" and lay["hw"] == "waveshare7"
    assert json.loads(me.LAYOUTS.read_text())["p1"]["rotation"] == 90
    assert put("k1", hw="knob128", widgets=[_w(id=str(i)) for i in range(5)]).status_code == 400
    assert put("k1", hw="knob128", widgets=[_w(type="climate")]).status_code == 200
    assert put("bad/name", hw="waveshare7", widgets=[]).status_code in (400, 404)


def test_the_status_bar_holds_up_to_four_touchmouse_buttons():
    c = TestClient(core.app)
    put = lambda **body: c.put("/api/panels/tab/layout", headers=HOUSE, json={"widgets": [], **body})
    tm = {"type": "touchmouse", "receiver": "tv-box"}
    _paired(c, dev="tbar", panel="tab", code="654321")             # an Android panel comes from a bound tablet
    assert put(bar=[tm]).status_code == 200
    assert c.get("/api/panels/tab/layout", headers=HOUSE).json()["bar"] == [tm]
    assert put(bar=[{"type": "airmouse", "device": "pc_1"}]).status_code == 200      # cutover-compat
    assert c.get("/api/panels/tab/layout", headers=HOUSE).json()["bar"] == [{"type": "touchmouse", "receiver": "pc_1"}]
    for bad in ([{"type": "lamp", "receiver": "x"}], [{"type": "touchmouse", "receiver": "a b"}],
                [{"type": "touchmouse"}], [tm] * 5):
        assert 400 <= put(bar=bad).status_code < 500, bad
    assert put(widgets=[]).status_code == 200 and me._layouts["tab"]["bar"] == [{"type": "touchmouse", "receiver": "pc_1"}]
    assert put(bar=[]).status_code == 200 and me._layouts["tab"]["bar"] == []
    assert c.put("/api/panels/esp/layout", headers=HOUSE, json={"hw": "waveshare7", "widgets": [], "bar": [tm]}).status_code == 400


def test_a_panel_key_reads_the_touchmouse_receivers_but_cannot_change_them():
    c = TestClient(core.app)
    key = _paired(c)
    assert c.get("/api/touchmouse", headers=_key(key)).status_code == 200
    r = c.put("/api/touchmouse/receivers/x", headers=_key(key), json={"name": "TV", "host": "192.0.2.5", "port": 7709})
    assert r.status_code in (401, 403)


def test_a_device_rename_follows_into_widgets_and_the_charger():
    me._layouts["p"] = {"hw": "waveshare7", "widgets": [_w(cmd_topic="home/cmd/lamp/state", state_topic="home/state/lamp/state"),
                                                       _w(id="w2", type="remote", device="lamp")],
                        "charger": {"device": "lamp", "code": "state", "min": 40, "max": 85}}
    me._on_rename("lamp", "hall_lamp")
    w = me._layouts["p"]["widgets"][0]
    assert w["cmd_topic"] == "home/cmd/hall_lamp/state" and w["state_topic"] == "home/state/hall_lamp/state"
    assert me._layouts["p"]["charger"]["device"] == "hall_lamp"
    assert me._layouts["p"]["widgets"][1]["device"] == "hall_lamp"


def test_an_offline_charger_plug_is_not_told_done_and_said_once(monkeypatch, caplog):
    monkeypatch.setitem(core.DEVICES, "plug_c", {"id": "plug_c", "controls": [], "_ctl": {}})
    monkeypatch.setitem(core._status_cache, "plug_c", {"online": False, "offline_since": 1.0, "values": {"state": False}})
    monkeypatch.setitem(me._layouts, "pc", {"charger": {"device": "plug_c", "code": "state", "min": 40, "max": 85}})
    monkeypatch.setattr(core, "_MAIN_LOOP", object())
    me._charger_away.discard("plug_c")
    with caplog.at_level("WARNING"):
        assert me._charge_guard("pc", {"battery": 20}) is None
        assert me._charge_guard("pc", {"battery": 19}) is None
    assert sum("charger plug_c is offline" in r.message for r in caplog.records) == 1


def test_the_charge_window():
    assert me._charge_decision(90, True, 40, 85) is False
    assert me._charge_decision(30, False, 40, 85) is True
    assert me._charge_decision(60, None, 40, 85) is None
    assert me._charge_decision(90, False, 40, 85) is None
    assert me._charge_decision(True, None, 40, 85) is None


def test_the_kiosk_pages_open_without_the_token_and_the_layout_does_not():
    c = TestClient(core.app)
    for path in ("/panel-view.html", "/panel-view", "/panel-builder", "/plugins/panels/static/widgets.js"):
        assert c.get(path).status_code == 200, path
    me._layouts["p"] = {"hw": "waveshare7", "widgets": []}
    assert c.get("/api/panels/p/layout").status_code == 401
    assert c.get("/api/panels").status_code == 401


def test_a_bound_tablets_cameras_are_cameras_of_the_house(monkeypatch):
    c = TestClient(core.app)
    ip = {"v": "192.0.2.50"}
    monkeypatch.setattr(me, "_ip", lambda request: ip["v"])
    cams = lambda: {x["id"] for x in c.get("/api/cameras", headers=HOUSE).json()}
    caps = {"cameras": [{"facing": "front"}, {"facing": "x"}], "cam_port": 8081, "ip": "192.0.2.99"}
    c.post("/api/panels/pairing", headers=HOUSE, json={"minutes": 5})
    c.post("/api/panels/register", json={"id": "camtab", "code": "1", "caps": caps})
    assert "camtab-front" not in cams()
    assert c.post("/api/panels/bind", headers=HOUSE, json={"id": "camtab", "code": "1", "panel": "bed room!"}).status_code == 400
    c.post("/api/panels/bind", headers=HOUSE, json={"id": "camtab", "code": "1", "panel": "door"})
    assert "camtab-front" not in cams()
    beat = c.post("/api/panels/heartbeat", headers=HOUSE, json={"id": "camtab"}).json()
    assert "camtab-front" in cams() and "camtab-x" not in cams()
    assert me._panel_cameras()["camtab-front"]["snapshot"].startswith("http://192.0.2.50:8081/")
    key = beat["cam_key"]
    assert len(key) >= 24 and me._panel_cameras()["camtab-front"]["headers"] == {"X-Cam-Key": key}
    assert c.post("/api/panels/heartbeat", headers=HOUSE, json={"id": "camtab"}).json()["cam_key"] == key   # stays
    assert key not in c.get("/api/cameras", headers=HOUSE).text                     # never shown to a client
    cammod = sys.modules["yashome_plugins.cameras"]
    sent = {}

    class Fake:
        def open(self, req, timeout):
            sent.update(dict(req.header_items()))
            raise OSError("no tablet here")
    monkeypatch.setattr(cammod, "_opener", Fake())
    with pytest.raises(core.HTTPException):
        cammod._open(me._panel_cameras()["camtab-front"], "http://192.0.2.50:8081/snapshot?cam=front", 2)
    assert sent.get("X-cam-key") == key
    for bad in ("127.0.0.1", "169.254.1.1", "0.0.0.0", "::ffff:127.0.0.1"):
        ip["v"] = bad
        c.post("/api/panels/heartbeat", headers=HOUSE, json={"id": "camtab"})
        assert "camtab-front" not in cams(), bad
    ip["v"] = "192.0.2.50"
    c.post("/api/panels/heartbeat", headers=HOUSE, json={"id": "camtab", "caps": {**caps, "cam_port": 1883}})
    assert "camtab-front" not in cams()
    c.delete("/api/panels/bind/camtab", headers=HOUSE)
    assert not {"device", "caps", "can"} & set(me._layouts["door"])

def test_the_retained_scan_stops_even_when_messages_keep_coming(monkeypatch):
    import threading
    import time as _t

    class Msg:
        retain, payload = True, b"x"

        def __init__(self, i):
            self.topic = f"panel/p/t{i}"

    class Cli:
        def __init__(self, *a, **kw):
            self.run = True

        def connect(self, *a, **kw):
            pass

        def subscribe(self, *a):
            pass

        def loop_start(self):
            def spam():
                i = 0
                while self.run:
                    self.on_message(self, None, Msg(i % 5))
                    i += 1
                    _t.sleep(0.01)
            threading.Thread(target=spam, daemon=True).start()

        def loop_stop(self):
            self.run = False

        def disconnect(self):
            pass

    class Paho:
        Client = Cli

        class CallbackAPIVersion:
            VERSION2 = 2
    sent = []
    monkeypatch.setattr(core, "_paho", Paho)
    monkeypatch.setattr(core, "_mqtt_auth", lambda cli: cli)
    monkeypatch.setattr(core, "_mqtt_client", type("M", (), {"publish": lambda self, t, p, retain: sent.append(t)})())
    out = []
    th = threading.Thread(target=lambda: out.append(CLEAR("p", timeout=0.5)), daemon=True)
    th.start()
    th.join(3)
    assert out == [6]


def test_a_tablet_is_online_only_by_its_heartbeat_and_offers_the_tablet_tiles():
    c = TestClient(core.app)
    key = _paired(c)
    row = lambda: next(p for p in c.get('/api/panels', headers=HOUSE).json() if p['name'] == 'hall')
    assert row()['seen'] is None and {'weather', 'ac', 'remote'} <= set(row()['can'])
    c.post('/api/panels/heartbeat', headers=_key(key), json={'id': 'tab1'})
    assert row()['seen']


def test_the_usb_flasher_is_the_unchanged_esptool_js_bundle():
    import hashlib
    r = TestClient(core.app).get("/plugins/panels/static/esptool.mjs")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert hashlib.sha256(r.content).hexdigest() == "00ebe6fb0b202976d3e2a3c6b9a6bd438c26a81d2af6739c95ac276a64c1a84e"


def test_only_web_files_of_static_are_served():
    c = TestClient(core.app)
    assert c.get("/plugins/panels/static/esptool-LICENSE.txt").status_code == 404
    hw = {h["hw"]: h for h in c.get("/api/panels/hw", headers={"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}).json()}
    assert hw["waveshare7"]["chip"] == "ESP32-S3" and hw["onx35"]["chip"] == "ESP32-S3"



def test_the_launcher_is_built_and_served_open(monkeypatch):
    bm = sys.modules["yashome_plugins.build"]
    monkeypatch.setattr(bm, "submit_apk", lambda name, project, *a, **k: seen.append((name, project, k.get("app_id"))) or "a1")
    monkeypatch.setattr(bm, "job", lambda jid: {"state": "done"})
    monkeypatch.setattr(bm, "artifact", lambda jid, name: {"release.keystore": b"K", "app.apk": b"PK-PANEL"}[name])
    monkeypatch.setattr(bm, "drop_artifact", lambda jid, name: None)
    seen = []
    me._app_holder.clear()
    st_dir = core.plugin_state("panels")
    for f in ("app.keystore", "app.keystore.pass", "panel.apk"):
        (st_dir / f).unlink(missing_ok=True)
    c = TestClient(core.app)
    assert c.get("/plugins/panels/panel.apk").status_code == 404
    assert c.post("/api/panels/launcher", headers=HOUSE).status_code == 200
    for _ in range(300):
        if c.get("/api/panels/launcher", headers=HOUSE).json().get("state") == "done":
            break
        __import__("time").sleep(0.02)
    r = c.get("/plugins/panels/panel.apk")
    assert r.status_code == 200 and r.content == b"PK-PANEL"
    assert seen[0][0] == "home-panel" and seen[0][1].name == "launcher" and seen[0][2] is None
    for f in ("app.keystore", "app.keystore.pass", "panel.apk"):
        (st_dir / f).unlink(missing_ok=True)
    me._app_holder.clear()



def test_a_panel_key_sees_only_the_receivers_of_its_bar(monkeypatch):
    tm = sys.modules["yashome_plugins.touchmouse"]
    monkeypatch.setattr(tm, "_receivers", [{"id": "tv-box", "host": "192.0.2.30", "port": 7709, "os": "linux"},
                                            {"id": "office", "host": "192.0.2.31", "port": 7709, "os": "linux"}])
    c = TestClient(core.app)
    key = _paired(c, dev="tbar2", panel="tab2", code="246810")
    assert c.put("/api/panels/tab2/layout", headers=HOUSE,
                 json={"widgets": [], "bar": [{"type": "touchmouse", "receiver": "tv-box"}]}).status_code == 200
    assert [r["id"] for r in c.get("/api/touchmouse", headers=_key(key)).json()] == ["tv-box"]
    assert {r["id"] for r in c.get("/api/touchmouse", headers=HOUSE).json()} >= {"tv-box", "office"}


def test_an_unreadable_paired_file_is_never_written_over(monkeypatch):
    c = TestClient(core.app)
    _paired(c)
    me.PAIR_DB.write_text('{"tab1": {"status": "bou')                    # truncated
    try:
        assert c.post("/api/panels/register", json={"id": "tab9", "code": "1"}).status_code == 503
        assert me.PAIR_DB.read_text() == '{"tab1": {"status": "bou'
        esp = sys.modules["yashome_plugins.panels._esp"]
        monkeypatch.setattr(esp, "_arp", lambda: {"192.0.2.1": "a"})
        assert any(r["id"] == "panels-file:paired.json" and r["state"] == "bad" for r in esp._health())
        me.PAIR_DB.unlink()                                               # removed by hand: it heals
        assert c.post("/api/panels/register", json={"id": "tab9", "code": "1"}).status_code == 200
    finally:
        me._BROKEN.clear()


def test_an_unreadable_layouts_or_keys_file_is_never_written_over(monkeypatch):
    esp = sys.modules["yashome_plugins.panels._esp"]
    monkeypatch.setitem(me._BROKEN, str(me.LAYOUTS), "bad json")
    with pytest.raises(core.HTTPException) as e:
        me._save_layouts()
    assert e.value.status_code == 503
    esp.KEYS.write_text("{oops")
    try:
        with pytest.raises(core.HTTPException):
            esp._key_next("p1")
        assert esp.KEYS.read_text() == "{oops"
    finally:
        esp.KEYS.unlink(missing_ok=True)
        me._BROKEN.pop(str(esp.KEYS), None)


def test_a_widget_that_only_shows_a_device_gives_no_control():
    c = TestClient(core.app)
    key = _paired(c)
    shown, pressed = sorted(core.DEVICES)[:2]
    me._layouts["hall"]["widgets"] = [_w(id="s", type="sensor", state_topic=f"home/state/{shown}/state"),
                                      _w(id="t", cmd_topic=f"home/cmd/{pressed}/state")]
    assert c.get(f"/api/devices/{shown}/status", headers=_key(key)).status_code == 200
    assert c.post(f"/api/devices/{shown}/control", headers=_key(key), json={"code": "state", "value": True}).status_code == 401
    assert c.post(f"/api/devices/{pressed}/control", headers=_key(key), json={"code": "x", "value": 1}).status_code != 401


def test_a_layout_with_nan_or_infinity_anywhere_is_refused_and_an_old_one_is_cleaned(monkeypatch):
    c = TestClient(core.app)
    me._layouts["nanp"] = {"hw": "android", "w": 800, "h": 480, "widgets": []}
    put = lambda w: c.put("/api/panels/nanp/layout", headers={**HOUSE, "Content-Type": "application/json"},
                          content=json.dumps({"w": 800, "h": 480, "widgets": [w]}, allow_nan=True))
    for bad in (_w(x=float("nan")), _w(size=float("nan")), _w(radius=float("inf")), _w(x=1e300)):
        assert put(bad).status_code == 400, bad
    assert me._layouts["nanp"]["widgets"] == [] and put(_w()).status_code == 200
    me.LAYOUTS.write_text('{"old": {"hw": "android", "widgets": [{"id": "a", "x": NaN}, {"id": "b", "x": 1}]}}')
    assert [w["id"] for w in me._load_layouts()["old"]["widgets"]] == ["b"]


def test_a_heartbeat_a_minute_does_not_write_the_file_each_time():
    c = TestClient(core.app)
    key = _paired(c)
    c.post("/api/panels/heartbeat", headers=_key(key), json={"id": "tab1", "caps": {"battery": 50}})
    before = me.PAIR_DB.stat().st_mtime_ns, me.PAIR_DB.read_text()
    r = c.post("/api/panels/heartbeat", headers=_key(key), json={"id": "tab1", "caps": {"battery": 49}})
    assert r.status_code == 200 and (me.PAIR_DB.stat().st_mtime_ns, me.PAIR_DB.read_text()) == before
    row = next(p for p in c.get("/api/panels", headers=HOUSE).json() if p["name"] == "hall")
    assert row["seen"] and row["caps"]["battery"] == 49


def test_pairing_through_a_reverse_proxy_is_refused():
    c = TestClient(core.app)
    c.post("/api/panels/pairing", headers=HOUSE, json={"minutes": 5})
    fwd = {"X-Forwarded-For": "192.0.2.50"}
    assert c.post("/api/panels/register", headers=fwd, json={"id": "px", "code": "1"}).status_code == 403
    assert c.get("/api/panels/register/px", headers=fwd).status_code == 403


def test_the_heartbeat_writes_on_a_new_address_a_new_passport_or_an_old_beat(monkeypatch):
    c = TestClient(core.app)
    key = _paired(c)
    beat = lambda **caps: c.post("/api/panels/heartbeat", headers=_key(key), json={"id": "tab1", "caps": caps})
    beat(battery=50)
    written = lambda: json.loads(me.PAIR_DB.read_text())["tab1"]
    monkeypatch.setattr(me, "_ip", lambda request: "192.0.2.77")
    beat(battery=49)
    assert written()["ip"] == "192.0.2.77"                                      # a new address
    beat(battery=48, sdk=33)
    assert written()["caps"].get("sdk") == 33                                   # a new passport
    rec = json.loads(me.PAIR_DB.read_text())
    rec["tab1"]["beat"] = 1
    me.PAIR_DB.write_text(json.dumps(rec))
    beat(battery=47)
    assert written()["beat"] > 1                                                # an old beat in the file
    assert c.delete("/api/panels/bind/tab1", headers=HOUSE).status_code == 200
    assert "tab1" not in me._BEATS


def test_a_showing_widget_with_a_leftover_command_gives_no_control():
    c = TestClient(core.app)
    key = _paired(c)
    dev = sorted(core.DEVICES)[0]
    for t in ("sensor", "label", "camera"):
        me._layouts["hall"]["widgets"] = [_w(type=t, cmd_topic=f"home/cmd/{dev}/state", device=dev,
                                             state_topic=f"home/state/{dev}/state")]
        assert c.get(f"/api/devices/{dev}/status", headers=_key(key)).status_code == 200, t
        assert c.post(f"/api/devices/{dev}/control", headers=_key(key), json={"code": "state", "value": True}).status_code == 401, t


def test_an_esp_panel_hears_which_of_its_devices_are_away_once_per_change(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "_mqtt_client", type("M", (), {"publish": lambda self, t, p, retain: sent.append((t, p, retain))})())
    monkeypatch.setattr(me, "_AWAY", {})
    w = [{"id": "a", "type": "toggle", "state_topic": "home/state/lamp_a/state"},
         {"id": "b", "type": "sensor", "state_topic": "home/state/s_b/temperature"}]
    monkeypatch.setitem(me._layouts, "esp1", {"hw": "onx35", "widgets": w})
    monkeypatch.setitem(me._layouts, "tab", {"hw": "android", "widgets": w})
    monkeypatch.setitem(core._status_cache, "lamp_a", {"online": False, "offline_since": 5.0, "values": {}})
    monkeypatch.setitem(core._status_cache, "s_b", {"online": False, "waiting": "start", "values": {}})
    monkeypatch.setitem(core._status_cache, "other_c", {"online": False, "offline_since": 5.0, "values": {}})
    me._away_tick()
    me._away_tick()
    assert sent == [("panel/esp1/away", "/lamp_a#o/s_b#w/", True)]        # the tablet reads its status itself
    core._status_cache["s_b"] = {"online": True, "values": {}}
    me._away_tick()
    assert sent[-1] == ("panel/esp1/away", "/lamp_a#o/", True) and len(sent) == 2
    assert me._away_reset in core.ON_MQTT_CONNECT
    me._away_reset(None)
    me._away_tick()
    assert len(sent) == 3                                       # a new connection: published again
    with TestClient(core.app) as c:
        assert c.delete("/api/panels/esp1", headers=HOUSE).status_code == 200
    assert "esp1" not in me._AWAY


def test_the_tablets_life_classes_are_not_classes_the_widgets_toggle():
    import re
    static = os.path.join(os.path.dirname(me.__file__), "static")
    view = open(os.path.join(static, "view.html"), encoding="utf-8").read()
    widgets = open(os.path.join(static, "widgets.js"), encoding="utf-8").read()
    life = set(re.findall(r"'(dev-[a-z]+)'", view))
    own = set(re.findall(r"classList\.(?:toggle|add)\('([\w-]+)'", widgets)) | {"w", "keep", "tap", "ac", "rm", "wx"}
    for names in re.findall(r"(?:className\s*=|setAttribute\('class',)\s*['\"`]([^'\"`]*)", widgets + view):
        own |= set(names.split())                                    # classes set whole, not toggled
    assert life == {"dev-off", "dev-wait"} and not life & own        # an AC switched off is 'off', not offline
    assert view.count("setTimeout(poll") == 1                     # one quick timer: soon()


def test_the_away_task_runs_every_second_and_a_layout_save_reads_the_devices_again(monkeypatch):
    assert me._away_loop in core.STARTUP_TASKS and me.AWAY_TICK_S == 1
    monkeypatch.setattr(me, "_AWAY_DEVS", {})
    monkeypatch.setitem(me._layouts, "esp2", {"hw": "onx35", "widgets": [{"id": "a", "type": "toggle",
                                                                         "state_topic": "home/state/d1/state"}]})
    calls = []
    real = me._refs
    monkeypatch.setattr(me, "_refs", lambda n: calls.append(n) or real(n))
    for _ in range(3):
        me._away_of("esp2")
    assert calls == ["esp2"]                                    # cached: the tick reads only the status
    assert me.AWAY_DEVS_S <= 10
    me._layouts["esp2"]["widgets"].append({"id": "b", "type": "toggle", "state_topic": "home/state/d2/state"})
    me._save_layouts()
    assert me._away_devs("esp2") == ["d1", "d2"] and len(calls) == 2


def test_a_device_added_or_renamed_on_the_house_side_reads_the_panels_devices_again(monkeypatch):
    monkeypatch.setattr(me, "_AWAY_DEVS", {})
    monkeypatch.setitem(me._layouts, "esp3", {"hw": "onx35", "widgets": [
        {"id": "a", "type": "toggle", "cmd_topic": "z2m/plug/set"}]})
    assert me._away_devs("esp3") == []                       # the topic is nobody's yet
    monkeypatch.setitem(core.DEVICES, "plug", {"id": "plug", "controls": [{"code": "state", "command_topic": "z2m/plug/set"}]})
    assert me._away_devs("esp3") == ["plug"]                 # a device added: read again at once
    me._AWAY_DEVS["esp3"] = (me._AWAY_DEVS["esp3"][0], len(core.DEVICES), ["stale"])
    for fn in core.ON_RENAME:
        if getattr(fn, "__module__", "") == me.__name__:
            fn("plug", "plug")
    assert me._away_devs("esp3") == ["plug"]


def test_an_offline_desk_lamp_leaves_lamp_out_of_the_away_list(monkeypatch):
    monkeypatch.setattr(me, "_AWAY_DEVS", {})
    monkeypatch.setitem(me._layouts, "esp4", {"hw": "onx35", "widgets": [
        {"id": "a", "type": "toggle", "state_topic": "home/state/lamp/state"},
        {"id": "b", "type": "toggle", "state_topic": "home/state/desk lamp/state"}]})
    monkeypatch.setitem(core._status_cache, "lamp", {"online": True, "values": {}})
    monkeypatch.setitem(core._status_cache, "desk lamp", {"online": False, "offline_since": 1.0, "values": {}})
    txt = me._away_of("esp4")
    assert txt == "/desk lamp#o/" and "/lamp#" not in txt          # the firmware's key for lamp


def _apk_with_manifest(tmp_path):
    """A launcher APK reduced to its binary manifest (a real one, versionCode 18, versionName 3.14)."""
    import zipfile
    f = tmp_path / "panel.apk"
    with zipfile.ZipFile(f, "w") as z:
        z.write(os.path.join(os.path.dirname(__file__), "..", "fixtures", "launcher-manifest.axml"), "AndroidManifest.xml")
    return f


def test_the_published_launcher_names_its_version(tmp_path, monkeypatch):
    st = tmp_path / "state"
    st.mkdir()
    monkeypatch.setattr(core, "plugin_state", lambda name: st)
    monkeypatch.setattr(core, "plugin_data", lambda name: tmp_path / "data")
    c = TestClient(core.app)
    assert c.get("/plugins/panels/app").status_code == 404           # nothing published yet
    apk = _apk_with_manifest(tmp_path)
    (st / "panel.apk").write_bytes(apk.read_bytes())
    r = c.get("/plugins/panels/app")                                 # open: a panel reads it without a key
    import hashlib
    assert r.status_code == 200, r.text
    assert r.json() == {"code": 18, "name": "3.14", "package": "dev.freidkin.panel", "size": apk.stat().st_size,
                        "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(), "path": "/plugins/panels/panel.apk"}


def test_the_launcher_row_gets_the_published_version(tmp_path, monkeypatch):
    st = tmp_path / "state"
    st.mkdir()
    monkeypatch.setattr(core, "plugin_state", lambda name: st)
    monkeypatch.setattr(core, "plugin_data", lambda name: tmp_path / "data")
    me._app_holder.clear()
    c = TestClient(core.app)
    assert "apk_name" not in c.get("/api/panels/launcher", headers=HOUSE).json()     # nothing published yet
    (st / "panel.apk").write_bytes(_apk_with_manifest(tmp_path).read_bytes())
    r = c.get("/api/panels/launcher", headers=HOUSE).json()
    assert r["apk"] is True and (r["apk_name"], r["apk_code"]) == ("3.14", 18)
    me._app_holder.clear()


def test_a_broken_launcher_file_is_a_500_not_a_crash(tmp_path, monkeypatch):
    st = tmp_path / "state"
    st.mkdir()
    (st / "panel.apk").write_bytes(b"not a zip")
    monkeypatch.setattr(core, "plugin_state", lambda name: st)
    monkeypatch.setattr(core, "plugin_data", lambda name: tmp_path / "data")
    assert TestClient(core.app).get("/plugins/panels/app").status_code == 500


def test_the_house_launcher_project_wins_over_the_shipped_one(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "plugin_data", lambda name: tmp_path)
    assert me._launcher_dir() == me.LAUNCHER_DIR
    (tmp_path / "launcher" / "app").mkdir(parents=True)
    (tmp_path / "launcher" / "app" / "build.gradle.kts").write_text("")
    assert me._launcher_dir() == tmp_path / "launcher"


def test_the_panel_page_may_load_the_launchers_own_camera_stream():
    c = TestClient(core.app)
    csp = c.get("/panel-view.html?panel=hall").headers["content-security-policy"]
    assert "img-src 'self' data: blob: http://127.0.0.1:8081;" in csp and "connect-src 'self'" in csp
    assert "127.0.0.1" not in c.get("/panel-builder").headers["content-security-policy"]   # the others keep the core's


def test_a_touchmouse_widget_is_android_only_and_names_a_receiver(monkeypatch):
    tm = sys.modules["yashome_plugins.touchmouse"]
    monkeypatch.setattr(tm, "_receivers", [{"id": "tv-box", "host": "192.0.2.30", "port": 7709, "os": "linux"},
                                            {"id": "office", "host": "192.0.2.31", "port": 7709, "os": "linux"}])
    c = TestClient(core.app)
    key = _paired(c, dev="tbar3", panel="tab3", code="135790")
    put = lambda name, **body: c.put(f"/api/panels/{name}/layout", headers=HOUSE, json=body)
    ok = _w(id="m1", type="touchmouse", receiver="tv-box")
    assert put("tab3", widgets=[ok]).status_code == 200
    for bad in (_w(id="m2", type="touchmouse"), _w(id="m3", type="touchmouse", receiver="a b"), _w(id="m4", type="touchmouse", receiver="")):
        assert put("tab3", widgets=[bad]).status_code == 400, bad
    assert put("esp", hw="waveshare7", widgets=[ok]).status_code == 400                  # no bridge on an ESP screen
    assert [r["id"] for r in c.get("/api/touchmouse", headers=_key(key)).json()] == ["tv-box"]   # the key sees the widget's receiver only


def test_the_adb_and_dnd_state_of_a_heartbeat_is_kept_but_not_written_as_durable():
    caps = {"app": "3.18", "adb": {"enabled": True, "wifi": False, "port": 5555, "rooted": True}, "dnd": "alarms", "evil": 1}
    assert me._small(caps) == {"app": "3.18", "adb": {"enabled": True, "wifi": False, "port": 5555, "rooted": True}, "dnd": "alarms"}
    assert "adb" not in me._durable(caps) and "dnd" not in me._durable(caps)


def test_a_panel_that_says_another_app_is_in_front_is_a_health_warning(monkeypatch):
    esp = sys.modules["yashome_plugins.panels._esp"]
    now = time.time()
    monkeypatch.setitem(me._layouts, "tabf", {"hw": "android", "device": "and-front"})
    live = {"beat": now, "caps": {"front": False}}
    monkeypatch.setattr(me, "_live", lambda dev, rec: dict(live))
    monkeypatch.setattr(me, "_db", lambda: {})
    assert any(r["id"] == "panel-front:tabf" and r["state"] == "warn" for r in esp._health())
    live["caps"] = {"front": True}
    assert not any(r["id"] == "panel-front:tabf" for r in esp._health())
    assert me._small({"front": False, "evil": 1}) == {"front": False} and "front" not in me._durable({"front": False})


def test_a_tablet_takes_its_voice_commands_by_long_poll_and_reports_as_a_satellite(monkeypatch):
    c = TestClient(core.app)
    key = _paired(c)
    _paired(c, dev="tab3", panel="kitchen", code="654321")
    sent = []
    monkeypatch.setattr(me, "_voice_publish", lambda devid, sub, value, retain=False: sent.append((me.voice_node(devid) + "/" + sub, value, retain)))
    monkeypatch.setattr(me, "_VQ", {})
    monkeypatch.setattr(me, "_VSTATUS", {})
    me._voice_cmd(["panel", "tab1", "voice", "cmd"], json.dumps({"t": "say/url", "p": "http://192.0.2.7:8092/s/done.wav"}).encode(), False)
    me._voice_cmd(["panel", "tab1", "voice", "cmd"], json.dumps({"t": "stop/command", "p": "PRESS"}).encode(), False)
    me._voice_cmd(["panel", "tab1", "voice", "cmd"], b"not json", False)
    me._voice_cmd(["panel", "tab1", "voice", "cmd"], json.dumps({"t": "say/url"}).encode(), True)           # retained: ignored
    r = c.get("/api/panels/voice/next?id=tab1&wait=0", headers=_key(key))
    assert r.json() == {"cmds": [{"t": "say/url", "p": "http://192.0.2.7:8092/s/done.wav"}, {"t": "stop/command", "p": "PRESS"}]}
    assert c.get("/api/panels/voice/next?id=tab1&wait=0", headers=_key(key)).json() == {"cmds": []}        # taken once
    assert ("panel-tab1/status", "online", True) in sent                                                    # polling = online
    assert c.get("/api/panels/voice/next?id=tab3&wait=0", headers=_key(key)).status_code == 403            # another tablet's queue
    assert c.get("/api/panels/voice/next?id=nope&wait=0", headers=HOUSE).status_code == 404
    sent.clear()
    for e, v in (("wake", ""), ("player", "playing"), ("listen", "OFF"), ("volume", "40")):
        assert c.post("/api/panels/voice/event", headers=_key(key), json={"id": "tab1", "e": e, "v": v}).status_code == 200
    assert sent == [("panel-tab1/wake", "wake", False), ("panel-tab1/player/state", "playing", False),
                    ("panel-tab1/listen/state", "OFF", True), ("panel-tab1/volume/state", "40", True)]
    assert c.post("/api/panels/voice/event", headers=_key(key), json={"id": "tab1", "e": "bogus"}).status_code == 422


def test_an_old_voice_command_is_dropped(monkeypatch):
    c = TestClient(core.app)
    key = _paired(c)
    monkeypatch.setattr(me, "_VQ", {"tab1": [(time.time() - me.VOICE_TTL_S - 5, {"t": "say/url", "p": "x"})]})
    monkeypatch.setattr(me, "_voice_publish", lambda *a, **k: None)
    assert c.get("/api/panels/voice/next?id=tab1&wait=0", headers=_key(key)).json() == {"cmds": []}


def test_a_heartbeat_with_another_launcher_than_the_published_one_is_told_to_update(monkeypatch):
    c = TestClient(core.app)
    key = _paired(c, dev="and-0123456789abcdef")
    beat = lambda caps: c.post("/api/panels/heartbeat", headers=_key(key), json={"id": "and-0123456789abcdef", "caps": caps}).json()   # noqa: E731
    monkeypatch.setattr(me, "_published", lambda: None)
    assert "update" not in beat({"app": "3.25", "app_code": 29})                       # nothing published: nothing to fetch
    monkeypatch.setattr(me, "_published", lambda: {"code": 31, "name": "3.27"})
    assert beat({"app": "3.25", "app_code": 29})["update"] is True
    assert beat({"app": "3.25"})["update"] is True                                      # an older launcher sends only its name
    assert "update" not in beat({"app": "3.27", "app_code": 31}) and "update" not in beat({"app": "3.30", "app_code": 34})   # newer: put on by hand
    assert "update" not in beat({"app": "3.27"}) and "update" not in beat({}) and "update" not in beat({"app": 3.25})
    monkeypatch.setattr(me, "_published", lambda: {"code": 31, "name": "3.27", "package": "yash.home.panel"})
    assert beat({"app": "3.25", "app_code": 29, "pkg": "yash.home.panel"})["update"] is True
    assert "update" not in beat({"app": "3.25", "app_code": 29, "pkg": "dev.example.old"})   # another package moves, never updates


def test_a_launcher_of_another_package_is_asked_to_hand_the_tablet_over_and_health_watches_it(monkeypatch):
    c = TestClient(core.app)
    key = _paired(c, dev="and-0123456789abcdef")
    beat = lambda caps: c.post("/api/panels/heartbeat", headers=_key(key), json={"id": "and-0123456789abcdef", "caps": caps}).json()   # noqa: E731
    monkeypatch.setattr(me, "_MOVES", {})
    monkeypatch.setattr(me, "_ODD_PKG", {})
    monkeypatch.setattr(me, "MIGRATE_FROM", {"dev.example.old"})
    monkeypatch.setattr(me, "APP_ID", None)
    assert "transfer_owner" not in beat({"app": "3.34", "pkg": "dev.example.old"})      # no house package: nothing to move
    monkeypatch.setattr(me, "APP_ID", "yash.home.panel")
    assert "transfer_owner" not in beat({"app": "3.34", "pkg": "yash.home.panel"})      # already the house's
    assert "transfer_owner" not in beat({"app": "3.34"})                                # no pkg: not known, not asked
    assert "transfer_owner" not in beat({"app": "3.34", "pkg": "dev.example.other"})    # not listed: never asked
    assert [r["id"] for r in me._move_health()] == ["panels-pkg-and-0123456789abcdef"]
    assert beat({"app": "3.34", "pkg": "dev.example.old"})["transfer_owner"] == "yash.home.panel"
    assert me._move_health() == []
    assert "transfer_owner" not in beat({"app": "3.34", "pkg": "dev.example.old"})      # asked again only after MOVE_EVERY_S
    at = me._MOVES["and-0123456789abcdef"]
    assert me._move_health() == []                                                      # the old one still beats: nothing moved
    monkeypatch.setattr(me.time, "time", lambda: at + me.MOVE_CONFIRM_S + 1)
    me._BEATS["and-0123456789abcdef"]["beat"] = at                                      # quiet since the ask
    assert [r["state"] for r in me._move_health()] == ["warn"]
    monkeypatch.setattr(me.time, "time", lambda: at + me.MOVE_EVERY_S + 1)
    assert beat({"app": "3.34", "pkg": "dev.example.old"})["transfer_owner"] == "yash.home.panel"
    assert "transfer_owner" not in beat({"app": "3.34", "pkg": "yash.home.panel"}) and me._MOVES == {}
    assert me._move_health() == []
    monkeypatch.setattr(me, "APP_ID", "dev.example.old")                                # switched back: never a move back
    assert "transfer_owner" not in beat({"app": "3.34", "pkg": "yash.home.panel"})


def test_the_published_launcher_is_read_once_per_file_change(monkeypatch, tmp_path):
    f = tmp_path / "panel.apk"
    f.write_bytes(b"x")
    monkeypatch.setattr(core, "plugin_state", lambda name: tmp_path)
    reads = []
    monkeypatch.setattr(me._apkinfo, "describe", lambda p: reads.append(p) or {"code": 1, "name": "1"})
    me._published_cache.clear()
    assert me._published() == {"code": 1, "name": "1"} and me._published() == {"code": 1, "name": "1"} and len(reads) == 1
    f.write_bytes(b"xy")
    assert me._published()["code"] == 1 and len(reads) == 2
    me._published_cache.clear()


def test_the_home_screen_is_pinned_by_default_and_released_from_settings():
    c = TestClient(core.app)
    key = _paired(c, dev="and-0123456789abcdef")
    beat = lambda: c.post("/api/panels/heartbeat", headers=_key(key), json={"id": "and-0123456789abcdef"}).json()     # noqa: E731
    assert beat()["home_pin"] is True
    assert c.patch("/api/panels/bind/and-0123456789abcdef", json={"home_pin": False}).status_code in (401, 403)
    assert c.patch("/api/panels/bind/and-0123456789abcdef", headers=HOUSE, json={"home_pin": False}).json()["home_pin"] is False
    assert beat()["home_pin"] is False
    seq = beat()["home_pin_seq"]
    c.patch("/api/panels/bind/and-0123456789abcdef", headers=HOUSE, json={"home_pin": False})
    assert beat()["home_pin_seq"] == seq + 1                                        # pressed again: a new seq, the tablet takes it again
    assert c.patch("/api/panels/bind/nope", headers=HOUSE, json={"home_pin": True}).status_code == 404


def _watched(c, monkeypatch, answer):
    """A bound tablet last heard at t=1000 (the plug-in up long before); start_launcher stubbed: no device is touched."""
    _paired(c, dev="wd1", panel="hall", code="135790")
    monkeypatch.setattr(me, "_WATCH_SINCE", 0.0)
    monkeypatch.setitem(me._BEATS, "wd1", {"beat": 1000.0, "seen": 1000.0, "ip": "192.0.2.40"})
    me._WATCH.clear()
    calls = []
    monkeypatch.setattr(me, "start_launcher", lambda did, rec: calls.append(did) or dict(answer))
    return calls


def test_the_watchdog_starts_a_silent_launcher_once_per_its_minutes(monkeypatch):
    c = TestClient(core.app)
    calls = _watched(c, monkeypatch, {"ok": True})
    assert me._watch_tick(1000 + 119) == [] and calls == []                 # 2 min by default: not yet
    assert me._watch_tick(1000 + 121) == ["wd1"] and calls == ["wd1"]
    assert me._watch_tick(1000 + 121 + 599) == [] and len(calls) == 1      # at most once in 10 min
    assert me._watch_tick(1000 + 121 + 601) == ["wd1"] and len(calls) == 2
    last = [r for r in c.get("/api/panels/discovered", headers=HOUSE).json()["registered"] if r["id"] == "wd1"][0]
    assert last["watch"] == {"on": True, "after_min": 2, "every_min": 10} and last["watch_last"]["ok"] is True
    me._BEATS["wd1"]["beat"] = 1000 + 2000                                   # it beats again: nothing to do
    assert me._watch_tick(1000 + 2001) == [] and len(calls) == 2


def test_no_adb_means_no_try_and_the_row_says_so(monkeypatch):
    c = TestClient(core.app)
    calls = _watched(c, monkeypatch, {"noadb": True, "why": "no way"})
    assert me._watch_tick(1000 + 600) == [] and calls == ["wd1"]           # asked, nothing tried
    assert me._WATCH["wd1"] == {"asked": 1600, "noadb": True}                 # no "at": no try
    assert me._watch_tick(1000 + 650) == [] and calls == ["wd1"]           # asked again only after every_min
    me._BEATS["wd1"]["beat"] = 1000 + 700
    me._watch_tick(1000 + 701)
    assert "noadb" not in me._WATCH["wd1"]                                  # alive again: the hint goes


def test_the_watchdog_switched_off_or_set_from_the_dashboard(monkeypatch):
    c = TestClient(core.app)
    calls = _watched(c, monkeypatch, {"ok": False, "why": "unreachable"})
    url = "/api/panels/bind/wd1/watch"
    assert c.patch(url, json={"on": False}).status_code == 401
    assert c.patch(url, headers=HOUSE, json={"after_min": 0}).status_code == 422
    assert c.patch(url, headers=HOUSE, json={"every_min": 241}).status_code == 422
    assert c.patch("/api/panels/bind/nobody/watch", headers=HOUSE, json={"on": False}).status_code == 404
    assert c.patch(url, headers=HOUSE, json={"on": False}).json()["watch"] == {"on": False, "after_min": 2, "every_min": 10}
    assert me._watch_tick(1000 + 3600) == [] and calls == []                # off: no try
    assert c.patch(url, headers=HOUSE, json={"on": True, "after_min": 5, "every_min": 1}).status_code == 200
    assert me._watch_tick(1000 + 299) == [] and me._watch_tick(1000 + 301) == ["wd1"]
    assert me._WATCH["wd1"]["ok"] is False and me._WATCH["wd1"]["why"] == "unreachable"
    assert me._watch_tick(1000 + 301 + 61) == ["wd1"]                       # every_min 1


def test_after_a_restart_silence_counts_from_the_start(monkeypatch):
    c = TestClient(core.app)
    calls = _watched(c, monkeypatch, {"ok": True})
    monkeypatch.setattr(me, "_WATCH_SINCE", 50_000.0)                        # the beats in memory were lost
    assert me._watch_tick(50_000 + 60) == [] and calls == []
    assert me._watch_tick(50_000 + 121) == ["wd1"]


def test_one_tablet_per_tick_the_one_asked_longest_ago_first(monkeypatch):
    c = TestClient(core.app)
    calls = _watched(c, monkeypatch, {"ok": True})
    _paired(c, dev="wd2", panel="hall", code="246802")
    me._BEATS["wd2"] = {"beat": 1000.0, "seen": 1000.0, "ip": "192.0.2.41"}
    first = me._watch_tick(1000 + 200)
    second = me._watch_tick(1000 + 230)
    assert len(first) == 1 and len(second) == 1 and set(first + second) == {"wd1", "wd2"} and len(calls) == 2


def _helper(monkeypatch, tmp_path, answer):
    """A stand-in for the host's panel-start on its socket file: it records the line and gives one answer."""
    import socket as so
    import threading as th
    monkeypatch.setattr(core.hostsock, "DIR", str(tmp_path))
    sock = tmp_path / "panel-start.sock"
    if sock.exists():
        sock.unlink()
    srv = so.socket(so.AF_UNIX, so.SOCK_STREAM)
    srv.bind(str(sock))
    srv.listen(1)
    got = []

    def serve():
        conn, _ = srv.accept()
        got.append(conn.makefile("rb").readline().decode())
        conn.sendall((json.dumps(answer) + "\n").encode())
        conn.close()
        srv.close()
    th.Thread(target=serve, daemon=True).start()
    return got


def test_start_launcher_sends_only_the_id_to_the_helper(monkeypatch, tmp_path):
    got = _helper(monkeypatch, tmp_path, {"ok": True})
    assert me.start_launcher("wd1", {"ip": "192.0.2.40"}) == {"ok": True} and got == ["start-launcher wd1\n"]
    _helper(monkeypatch, tmp_path, {"refused": True, "why": "unknown tablet"})
    assert me.start_launcher("wd1", {}) == {"ok": False, "why": "refused: unknown tablet"}


def test_start_launcher_without_the_helper_is_no_way_in(monkeypatch, tmp_path):
    import socket as so
    monkeypatch.setattr(core.hostsock, "DIR", str(tmp_path))
    r = me.start_launcher("wd1", {})
    assert r["noadb"] is True and "not installed" in r["why"]
    s = so.socket(so.AF_UNIX, so.SOCK_STREAM)
    s.bind(str(tmp_path / "panel-start.sock"))
    s.close()                                                                # the file, nobody behind it
    assert me.start_launcher("wd1", {}) == {"noadb": True, "why": "panel-start helper: ConnectionRefusedError"}


def test_a_tablet_is_renamed_in_the_house():
    c = TestClient(core.app)
    _paired(c, dev="and-0123456789abcdef")
    _paired(c, dev="and-fedcba9876543210", panel="hall2", code="654321")
    p = lambda body, dev="and-0123456789abcdef": c.patch(f"/api/panels/bind/{dev}", headers=HOUSE, json=body)   # noqa: E731
    assert p({"name": " Kitchen tab "}).json()["name"] == "Kitchen tab"
    assert p({"name": "kitchen TAB"}, "and-fedcba9876543210").status_code == 409
    assert p({}).status_code == 400
    assert me._pair_load(me.PAIR_DB, {})["and-0123456789abcdef"]["name_set"] is True
