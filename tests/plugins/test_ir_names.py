"""IR names are checked like every name: a remote's among remotes, a button's on its remote, a group's, a tile's and a
blaster's among the devices (both are device names); and a blaster's own device is its card in the list."""
import sys

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.ir"]


@pytest.fixture
def ir(monkeypatch):
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    monkeypatch.setattr(me, "_save", lambda: None)
    keep = (list(me.BLASTERS), list(me.REMOTES), list(me.APPLIANCES))
    me.BLASTERS[:] = [{"node": "tn-1", "name": "Hub", "rx": True}]
    me.REMOTES[:] = [{"id": "tv", "name": "TV", "buttons": [{"id": "pw", "name": "Power"}, {"id": "up", "name": "Up"}]},
                     {"id": "fan", "name": "Fan", "buttons": []}]
    me.APPLIANCES[:] = []
    me._sync()
    core.DEVICES["tn_lamp"] = {"id": "tn_lamp", "name": "Desk lamp", "transport": "mqtt", "controls": [], "_ctl": {}}
    yield
    core.DEVICES.pop("tn_lamp", None)
    me.BLASTERS[:], me.REMOTES[:], me.APPLIANCES[:] = keep
    me._sync()


def refused(code, fn, *a):
    with pytest.raises(HTTPException) as e:
        fn(*a)
    assert e.value.status_code == code, e.value.detail
    return e.value.detail


def test_remote_names(ir):
    assert refused(409, me.ir_remote_add, me.RemoteBody(name=" tv ")) == "'tv' is already taken by another remote"
    assert refused(400, me.ir_remote_add, me.RemoteBody(name="x" * 41)).startswith("name is too long")
    assert refused(409, me.ir_remote_rename, "fan", me.NameBody(name="TV"))
    assert me.ir_remote_rename("fan", me.NameBody(name="FAN 2"))
    assert me._remote("fan")["name"] == "FAN 2"


def test_button_and_group_names(ir):
    assert refused(409, me.ir_button_rename, "tv", "up", me.NameBody(name="power")) == "'power' is already taken by another button"
    assert refused(400, me.ir_button_rename, "tv", "up", me.NameBody(group="a\x01")) == "name has a control character"
    me.ir_button_rename("tv", "up", me.NameBody(name=" UP ", group=" Nav "))
    b = next(x for x in me._remote("tv")["buttons"] if x["id"] == "up")
    assert (b["name"], b["group"]) == ("UP", "Nav")


def test_tile_and_blaster_names_are_device_names(ir):
    assert refused(409, me.ir_appliance_add, me.ApplianceBody(name="desk LAMP", remote="tv", blaster="tn-1"))
    a = me.ir_appliance_add(me.ApplianceBody(name="TV box", remote="tv", blaster="tn-1"))
    assert refused(409, me.ir_appliance_patch, a["id"], me.AppliancePatch(name="Desk lamp"))
    assert refused(409, me.ir_blaster_patch, "tn-1", me.BlasterBody(name="tv BOX"))
    assert me.ir_blaster_patch("tn-1", me.BlasterBody(name="HUB"))["name"] == "HUB"      # its own name in another case


def test_a_blasters_own_device_is_its_card_in_the_list(ir):
    got = {b["node"]: b["device"] for b in me.ir_remotes()["blasters"]}
    assert got["tn-1"] == "blaster:tn-1"
