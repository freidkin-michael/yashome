"""androidtv: a fake androidtvremote2 client in-process - the plug-in loads without the package and says what is
missing; Add a TV takes the MAC from the certificate; the pairing code makes the TV trusted and connected; each
control is the right key; the TV's pushes are the state; no connection = Wake-on-LAN for power-on, 409 for the rest.
No real device is ever addressed (192.0.2.x, 00:11:22:33:44:55)."""
import asyncio
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.androidtv"]
atv = me.atv
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


class FakeRemote:
    """What the package's AndroidTVRemote does, kept in memory: keys sent, apps launched, the pairing code."""
    made: list = []
    code = "A1B2C3"

    def __init__(self, host):
        self.host, self.keys, self.apps, self.cbs = host, [], [], {}
        self.is_on, self.volume_info = True, {"level": 4, "max": 20, "muted": False}
        FakeRemote.made.append(self)

    async def async_generate_cert_if_missing(self):
        for f in (me.CERT, me.KEY):
            f.write_text("test")
        return True

    async def async_get_name_and_mac(self):
        return "Fake TV", "00:11:22:33:44:55"

    async def async_start_pairing(self):
        self.pairing = True

    async def async_finish_pairing(self, code):
        if code != FakeRemote.code:
            raise atv.InvalidAuth("wrong code")

    async def async_connect(self):
        pass

    def add_is_on_updated_callback(self, f):
        self.cbs["on"] = f

    def add_volume_info_updated_callback(self, f):
        self.cbs["volume"] = f

    def add_is_available_updated_callback(self, f):
        self.cbs["available"] = f

    def keep_reconnecting(self, invalid_auth_callback=None):
        self.unpaired = invalid_auth_callback

    def disconnect(self):
        pass

    def send_key_command(self, key):
        self.keys.append(key)

    def send_launch_app_command(self, link):
        self.apps.append(link)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeRemote.made = []
    monkeypatch.setattr(atv, "MISSING", "")
    monkeypatch.setattr(atv, "remote", lambda host, cert, key: FakeRemote(host))
    monkeypatch.setattr(me, "CERT", tmp_path / "client.pem")
    monkeypatch.setattr(me, "KEY", tmp_path / "client.key")
    monkeypatch.setattr(me, "PAIRED_PATH", tmp_path / "paired.json")
    monkeypatch.setattr(me, "_paired", set())
    monkeypatch.setattr(me, "_conns", {})
    monkeypatch.setattr(me, "_pairing", {})
    c = TestClient(core.app)
    tv = c.post("/api/androidtv/tvs", headers=HOUSE, json={"name": "Salon TV", "host": "192.0.2.60"}).json()
    yield c, tv
    c.delete(f"/api/androidtv/tvs/{tv['id']}", headers=HOUSE)
    assert tv["id"] not in core.DEVICES


def test_without_the_package_it_loads_and_says_what_to_add(monkeypatch):
    monkeypatch.setattr(atv, "MISSING", "No module named 'androidtvremote2'")
    monkeypatch.setattr(me, "TVS", [{"id": "atv_x", "name": "X", "host": "192.0.2.61"}])
    rows = {r["id"]: r for r in me._health()}
    assert rows["androidtv"]["state"] == "bad" and atv.PACKAGES in rows["androidtv"]["args"]
    assert rows["androidtv:atv_x"]["state"] == "warn"
    with pytest.raises(RuntimeError, match="EXTRA_PIP"):
        monkeypatch.setattr(atv, "AndroidTVRemote", None)
        atv.remote("192.0.2.61", "c", "k")


def test_add_takes_the_mac_from_the_certificate_and_a_second_add_of_the_address_is_refused(fake):
    c, tv = fake
    assert tv["id"] == "atv_salon_tv_60" and tv["mac"] == "00:11:22:33:44:55" and not tv["paired"]
    assert core.DEVICES[tv["id"]]["transport"] == "androidtv" and core.DEVICES[tv["id"]]["category"] == "tv"
    assert c.post("/api/androidtv/tvs", json={"name": "Y", "host": "192.0.2.62"}).status_code in (401, 403)
    assert c.post("/api/androidtv/tvs", headers=HOUSE, json={"name": "Again", "host": "192.0.2.60"}).status_code == 409
    assert me.tv_plugin._known().get("192.0.2.60") == "Salon TV"


def test_the_code_on_the_tv_pairs_it_and_connects_and_a_wrong_code_is_refused(fake):
    c, tv = fake
    p = f"/api/androidtv/tvs/{tv['id']}/pair"
    assert c.post(p + "/code", headers=HOUSE, json={"code": "A1B2C3"}).status_code == 409    # Pairing first
    assert c.post(p, headers=HOUSE).json()["code_on_tv"]
    assert c.post(p + "/code", headers=HOUSE, json={"code": "ZZZZZZ"}).status_code == 409
    assert c.post(p, headers=HOUSE).status_code == 200
    assert c.post(p + "/code", headers=HOUSE, json={"code": "a1b2c3"}).json()["paired"]       # typed in lower case
    assert json.loads(me.PAIRED_PATH.read_text()) == [tv["id"]] and tv["id"] in me._conns
    vals = core.status_copy(tv["id"], {})["values"]
    assert vals["power"] is True and vals["volume"] == 20 and vals["mute"] is False          # level 4 of 20 = 20 %
    me._conns[tv["id"]].cbs["on"](False)
    assert core.status_copy(tv["id"], {})["values"]["power"] is False                        # the TV's push


def test_each_control_is_the_right_key(fake):
    c, tv = fake
    c.post(f"/api/androidtv/tvs/{tv['id']}/pair", headers=HOUSE)
    c.post(f"/api/androidtv/tvs/{tv['id']}/pair/code", headers=HOUSE, json={"code": FakeRemote.code})
    r = me._conns[tv["id"]]
    cfg = core.DEVICES[tv["id"]]
    for code, val in (("power", True), ("power", False), ("mute", True), ("input", "HDMI 2"), ("media", "pause"), ("key_ok", True)):
        asyncio.run(me._control(cfg, code, val))
    assert r.keys == ["WAKEUP", "SLEEP", "VOLUME_MUTE", "TV_INPUT_HDMI_2", "MEDIA_PAUSE", "DPAD_CENTER"]
    r.keys.clear()
    asyncio.run(me._control(cfg, "volume", 50))                                              # 4 of 20 -> 10 of 20
    assert r.keys == ["VOLUME_UP"] * 6
    asyncio.run(me._control(cfg, "app", "Netflix"))
    assert r.apps == ["com.netflix.ninja"]
    with pytest.raises(core.HTTPException):
        asyncio.run(me._control(cfg, "input", "HDMI 9"))


def test_without_a_connection_power_on_is_wake_on_lan_and_the_rest_says_off(fake, monkeypatch):
    c, tv = fake
    woke = []
    monkeypatch.setattr(core, "wake_on_lan", woke.append)
    cfg = core.DEVICES[tv["id"]]
    asyncio.run(me._control(cfg, "power", True))
    assert woke == ["00:11:22:33:44:55"]
    with pytest.raises(core.HTTPException) as e:
        asyncio.run(me._control(cfg, "volume", 10))
    assert e.value.status_code == 409 and "not paired" in e.value.detail


def test_edit_checks_the_lists_and_rewrites_the_device(fake):
    c, tv = fake
    u = f"/api/androidtv/tvs/{tv['id']}"
    body = {"name": "Salon TV", "host": "192.0.2.60", "mac": "00:11:22:33:44:55",
            "inputs": {"PS5": "TV_INPUT_HDMI_3"}, "apps": {"Plex": "com.plexapp.android", "YouTube": "https://www.youtube.com"}}
    assert c.put(u, headers=HOUSE, json={**body, "inputs": {"X": "HDMI_9"}}).status_code == 400
    assert c.put(u, headers=HOUSE, json={**body, "apps": {"Bad": "rm -rf"}}).status_code == 400
    got = c.put(u, headers=HOUSE, json=body).json()
    assert got["inputs"] == {"PS5": "TV_INPUT_HDMI_3"}
    ranges = {x["code"]: x.get("range") for x in core.DEVICES[tv["id"]]["controls"]}
    assert ranges["input"] == ["PS5"] and ranges["app"] == ["Plex", "YouTube"]
