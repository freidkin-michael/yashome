"""ir: remotes, appliances as devices (added, changed, dropped at run time), press, learning, status."""
import asyncio
import sys
import threading
import time

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.ir"]


@pytest.fixture
def ir(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "_mqtt_pub", lambda cli, topic, payload=None, retain=False: sent.append((topic, payload)))
    monkeypatch.setattr(core, "_mqtt_client", object())
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    monkeypatch.setattr(me, "REMOTES_PATH", me.DATA / "t_ir_remotes.json")
    me.BLASTERS[:] = [{"node": "t-1", "name": "Hub", "rx": True}, {"node": "t-2", "name": "Blaster 2"}]
    me.REMOTES[:] = []
    me.APPLIANCES[:] = []
    me._node_status.clear()
    yield sent
    me.APPLIANCES[:] = []
    me._sync()
    me.REMOTES[:] = []
    me.BLASTERS[:] = []
    me._sync()                                         # their devices go too


def learn(rid, name, frame="9060,-4520,550", group=None):
    def answer():
        while me._learn["node"] is None:
            time.sleep(0.01)
        me._handle(["t-1", "rx", "ir_raw"], frame.encode())
    threading.Thread(target=answer, daemon=True).start()
    return asyncio.run(me.ir_learn(rid, me.LearnBody(name=name, group=group, timeout=5)))


def test_learn_press_and_the_appliance_device(ir):
    # i18n: a Russian name becomes an ascii id
    r = me.ir_remote_add(me.RemoteBody(name="\u041f\u0440\u0438\u043c\u0435\u0440 1"))
    assert r["id"] == "primer_1"
    out = learn(r["id"], "Power", group="Fan")
    assert ("t-1/learn/command", "ON") in ir and ("t-1/learn/command", "OFF") in ir
    assert out["button"]["n"] == 3 and "code" not in out["button"]
    a = me.ir_appliance_add(me.ApplianceBody(name="Fan", remote=r["id"], blaster="t-2"))
    d = core.DEVICES[a["id"]]
    assert d["transport"] == "ir" and d["category"] == "ir" and list(d["_ctl"]) == ["ir_power"]
    core.CONTROL_KINDS["ir_press"](d, d["_ctl"]["ir_power"], True)
    assert ir[-1] == ("t-2/tx/ir_raw", "9060,-4520,550")
    learn(r["id"], "Speed")
    assert sorted(core.DEVICES[a["id"]]["_ctl"]) == ["ir_power", "ir_speed"]     # updated at run time
    me.ir_appliance_patch(a["id"], me.AppliancePatch(name="Fan 2"))
    assert core.DEVICES[a["id"]]["name"] == "Fan 2"
    me.ir_remote_del(r["id"])
    assert a["id"] not in core.DEVICES and not me.APPLIANCES


def test_no_frame_times_out_and_a_stale_frame_is_ignored(ir, monkeypatch):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    me._handle(["t-1", "rx", "ir_raw"], b"1,2,3")                       # nobody is learning
    assert (me.LEARN_MIN_S, me.LEARN_MAX_S) == (5, 120)
    monkeypatch.setattr(me, "LEARN_MIN_S", 0.2)
    monkeypatch.setattr(me, "LEARN_MAX_S", 0.2)                         # timeout=5 clamps to the window
    with pytest.raises(HTTPException) as e:
        asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="x", timeout=5)))
    assert e.value.status_code == 408 and me._learn["node"] is None


def test_the_learning_window_is_clamped_and_one_at_a_time(ir, monkeypatch):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    waited = []

    class Ev:                                    # records the window instead of sitting it out
        def clear(self):
            pass

        def wait(self, t):
            waited.append(t)
            return False
    monkeypatch.setattr(me, "_learn_ev", Ev())
    monkeypatch.setattr(me, "LEARN_MIN_S", 3)
    monkeypatch.setattr(me, "LEARN_MAX_S", 50)
    for asked, want in ((1, 3), (9999, 50), (20, 20)):
        with pytest.raises(HTTPException) as e:
            asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="x", timeout=asked)))
        assert e.value.status_code == 408 and waited[-1] == want, (asked, waited)
    me._learn["until"] = time.time() + 10           # another learning is on
    try:
        with pytest.raises(HTTPException) as e:
            asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="x", timeout=5)))
        assert e.value.status_code == 409
    finally:
        me._learn["until"] = 0.0


def test_too_long_is_422(ir):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))

    def answer():
        while me._learn["node"] is None:
            time.sleep(0.01)
        me._handle(["t-1", "rx", "error"], b"too_long")
    threading.Thread(target=answer, daemon=True).start()
    with pytest.raises(HTTPException) as e:
        asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="x", timeout=5)))
    assert e.value.status_code == 422


def test_tile_follows_its_blaster(ir):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    learn(r["id"], "Power")
    a = me.ir_appliance_add(me.ApplianceBody(name="TV bed", remote=r["id"], blaster="t-2"))
    assert core._status_copy(a["id"], {}).get("online") is False
    me._handle(["t-2", "status"], b"online")
    assert core._status_copy(a["id"], {})["online"] is True
    me._handle(["t-2", "status"], b"offline")
    assert core._status_copy(a["id"], {}).get("offline_since")
    me._handle(["t-2", "status"], b"online")            # it never reports: back with its blaster at once
    st = core._status_copy(a["id"], {})
    assert st["online"] is True and "waiting" not in st


def test_refusals(ir):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    for body in (me.ApplianceBody(name="x", remote="nope", blaster="t-2"),
                 me.ApplianceBody(name="x", remote=r["id"], blaster="nope"),
                 me.ApplianceBody(name="", remote=r["id"], blaster="t-2")):
        with pytest.raises(HTTPException):
            me.ir_appliance_add(body)
    with pytest.raises(HTTPException):
        asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="x", proto="uv")))


def test_a_button_keeps_the_icon_picked_for_it(ir):
    r = me.ir_remote_add(me.RemoteBody(name="Tv"))
    learn(r["id"], "Louder")
    a = me.ir_appliance_add(me.ApplianceBody(name="Tv", remote=r["id"], blaster="t-2"))
    out = me.ir_button_rename(r["id"], "louder", me.NameBody(icon="vol_up"))
    assert out["buttons"][0]["icon"] == "vol_up"
    assert core.DEVICES[a["id"]]["controls"][0]["icon"] == "vol_up"
    me.ir_button_rename(r["id"], "louder", me.NameBody(icon="../x"))      # not a key of the set: dropped, never stored
    assert me._remote(r["id"])["buttons"][0].get("icon") is None


def test_the_library_file_keeps_groups(ir):
    r = me.ir_remote_add(me.RemoteBody(name="Light"))
    learn(r["id"], "On", group="Lamp")
    import json
    disk = json.loads(me.REMOTES_PATH.read_text())
    rec = next(x for x in disk if x["id"] == r["id"])
    assert rec["groups"][0]["name"] == "Lamp" and rec["groups"][0]["buttons"][0]["code"] == "9060,-4520,550"
    assert me._flatten(rec)["buttons"][0]["group"] == "Lamp"


def test_loaded_with_ui():
    m = next(p for p in core.PLUGINS if p["id"] == "ir")
    assert m["ui"] == "/plugins/ir/ui.js" and "ru" in m["i18n"]


def test_an_ir_toggle_is_a_touchmouse_target_at_its_probe_address():      # cutover-compat
    t = {"id": "t_box", "power": {}, "probe": {"host": "192.0.2.30"}, "touchmouse": {"port": 7709, "os": "android"}}
    assert me._toggle_device(t)["touchmouse"] == {"port": 7709, "os": "android", "host": "192.0.2.30"}
    assert "touchmouse" not in me._toggle_device({"id": "t_box"})


def test_ir_toggle_sends_only_when_the_state_differs_and_probes(ir):
    import socket as so
    r = me.ir_remote_add(me.RemoteBody(name="Box"))
    learn(r["id"], "power")
    srv = so.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    me.TOGGLES[:] = [{"id": "t_box", "name": "Box", "power": {"remote": r["id"], "button": "power", "via": "t-2"},
                      "probe": {"host": "127.0.0.1", "port": srv.getsockname()[1]}}]
    try:
        me._sync()
        d = core.DEVICES["t_box"]
        assert d["transport"] == "ir" and list(d["_ctl"]) == ["power"]
        assert me._probe("127.0.0.1", srv.getsockname()[1]) is True
        core._store_status_exact("t_box", {"online": True, "values": {"power": True}})
        n = len(ir)
        asyncio.run(core.ASYNC_CONTROL["ir"](d, "power", True))
        assert len(ir) == n                                   # already on: no frame, it would switch it off
        core.TRANSPORTS["ir"](d, "power", False)
        assert ir[-1] == ("t-2/tx/ir_raw", "9060,-4520,550")
        with pytest.raises(HTTPException):
            core.TRANSPORTS["ir"](d, "volume", 3)
    finally:
        srv.close()
        me.TOGGLES[:] = []
        me._sync()
    assert "t_box" not in core.DEVICES


def test_a_library_that_failed_to_load_refuses_changes(ir, monkeypatch):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    learn(r["id"], "Power")
    monkeypatch.setattr(me, "_load_failed", True)
    for call in (lambda: me.ir_remote_add(me.RemoteBody(name="x")), lambda: me.ir_remote_del(r["id"]),
                 lambda: me.ir_remote_rename(r["id"], me.NameBody(name="y")),
                 lambda: me.ir_button_del(r["id"], "power"),
                 lambda: asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="x", timeout=5)))):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 503
    assert me._remote(r["id"])["name"] == "TV" and me._learn["node"] is None


def test_an_appliance_id_taken_by_another_device_is_skipped(ir):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    learn(r["id"], "Power")
    core.DEVICES["t_other"] = {"id": "t_other", "name": "Other", "transport": "mqtt", "controls": [], "_ctl": {}}
    core._store_status_exact("t_other", {"online": True, "values": {}})
    try:
        me.APPLIANCES.append({"id": "t_other", "name": "TV", "remote": r["id"], "blaster": "t-2"})
        me._sync()
        me._status("t_other")
        assert core.DEVICES["t_other"]["transport"] == "mqtt" and core._status_copy("t_other", {})["online"] is True
    finally:
        me.APPLIANCES[:] = []
        core.DEVICES.pop("t_other", None)


def test_a_cancelled_learn_does_not_end_the_next_one(ir, monkeypatch):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))

    async def waited(fn, timeout):                    # while the first request waits: cancel, a new learn arms
        me.ir_learn_cancel()
        me._learn.update(node="t-1", proto="ir", until=time.time() + 30, sid=me._learn["sid"] + 1)
        ir.append(("t-1/learn/command", "ON"))
        return True
    monkeypatch.setattr(me, "run_in_threadpool", waited)
    with pytest.raises(HTTPException) as e:
        asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="a", timeout=5)))
    assert e.value.status_code == 409 and me._learn["node"] == "t-1" and me._learn["until"] > time.time()
    assert ir[-1] == ("t-1/learn/command", "ON")
    me._learn.update(node=None, proto=None, until=0.0)


def test_an_appliance_follows_its_renamed_host_card(monkeypatch):
    monkeypatch.setattr(me, "_save", lambda: None)
    monkeypatch.setattr(me, "_sync", lambda: None)
    monkeypatch.setattr(me, "APPLIANCES", [{"id": "tv_ir", "device": "tv_old"}, {"id": "x", "device": "other"}])
    me._on_rename("tv_old", "tv_new")
    assert [a["device"] for a in me.APPLIANCES] == ["tv_new", "other"]


def test_a_remote_hides_groups_of_keys_and_the_device_says_so(ir):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    rid = r["id"]
    learn(rid, "Power")
    assert me.ir_remote_rename(rid, me.NameBody(hide=["media", "other", "media"]))["hide"] == ["media", "other"]
    me.APPLIANCES.append({"id": "tv1", "name": "TV", "remote": rid, "blaster": "t-2"})
    assert me._device_of(me.APPLIANCES[0])["ir_hide"] == ["media", "other"]
    with pytest.raises(HTTPException) as e:
        me.ir_remote_rename(rid, me.NameBody(hide=["lamps"]))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        me.ir_remote_rename(rid, me.NameBody())
    assert e.value.status_code == 400
    assert me.ir_remote_rename(rid, me.NameBody(hide=[]))["hide"] == []


def test_one_name_per_appliance_and_blaster(ir, monkeypatch):
    monkeypatch.setattr(core, "_name_overrides", {"lamp": "Ceiling lamp", "other": "Kept"})
    r = me.ir_remote_add(me.RemoteBody(name="Lamp"))
    me.APPLIANCES.append({"id": "lamp", "name": "Lamp", "remote": r["id"], "blaster": "t-1"})
    monkeypatch.setitem(core.DEVICES, "hub_card", {"id": "hub_card", "name": "Hub card", "ir_blaster": "t-1", "controls": []})
    me._one_name_each()                                     # at start: the saved rename moves into the record
    me._sync()
    assert me._appliance("lamp")["name"] == "Ceiling lamp" and core._name_overrides == {"other": "Kept"}
    assert "name" not in me._blaster("t-1") and me._blaster("t-2")["name"] == "Blaster 2"
    assert me._blaster_name("t-1") == "Hub card" and me.ir_remotes()["blasters"][0]["name"] == "Hub card"
    core.set_device_name("lamp", "Salon lamp")              # the card's pencil: the record, no override
    assert me._appliance("lamp")["name"] == "Salon lamp" and core.DEVICES["lamp"]["name"] == "Salon lamp"
    assert "lamp" not in core._name_overrides and core.BY_ID_NAME["lamp"] == "Salon lamp"
    me.ir_appliance_patch("lamp", me.AppliancePatch(name="Lamp 2"))     # the tile's pencil: the same name everywhere
    assert core.DEVICES["lamp"]["name"] == "Lamp 2" and me.ir_appliances()["appliances"][0]["name"] == "Lamp 2"
    core.set_device_name("other", "Not ours")              # a device no plug-in names keeps its override
    assert core._name_overrides["other"] == "Not ours"


def test_blasters_are_renamed_and_deleted_only_when_nothing_sends_through_them(ir, monkeypatch):
    monkeypatch.setattr(core, "_name_overrides", {})
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    me.BLASTERS.append({"node": "t-3", "name": "Spare"})
    me.ir_appliance_add(me.ApplianceBody(name="TV tile", remote=r["id"], blaster="t-2"))
    monkeypatch.setitem(core.DEVICES, "hub_card", {"id": "hub_card", "name": "Hub card", "ir_blaster": "t-1", "transport": "mqtt",
                                                   "availability_topic": "t-1/status", "controls": []})
    monkeypatch.setitem(core.DEVICES, "ac_x", {"id": "ac_x", "name": "AC", "transport": "mqtt", "controls": [{"code": "mode", "command_topic": "t-1/ac/mode/set"}]})
    users = {b["node"]: b["users"] for b in me.ir_remotes()["blasters"]}
    assert users == {"t-1": ["AC"], "t-2": ["TV tile"], "t-3": []}
    assert me._rx_node() == "t-1"
    for node, who in (("t-1", "learns"), ("t-2", "TV tile")):                       # the one that learns goes only after another learns
        with pytest.raises(HTTPException) as e:
            me.ir_blaster_del(node)
        assert e.value.status_code == 409 and who in e.value.detail
    assert me.ir_blaster_patch("t-3", me.BlasterBody(name="Hall blaster"))["name"] == "Hall blaster" and me._blaster("t-3")["name"] == "Hall blaster"
    me.ir_blaster_patch("t-1", me.BlasterBody(name="Salon blaster"))         # fronted by a device: the device's name, nothing in ir_blasters
    assert core.DEVICES["hub_card"]["name"] == "Salon blaster" and core._name_overrides["hub_card"] == "Salon blaster"
    assert me._blaster_name("t-1") == "Salon blaster" and "name" not in me._blaster("t-1")
    assert me.ir_blaster_del("t-3")["removed"] == "t-3" and me._blaster("t-3") is None
    with pytest.raises(HTTPException) as e:
        me.ir_blaster_del("t-3")
    assert e.value.status_code == 404


def test_a_power_feed_refuses_presses_while_off_and_clears_what_was_sent(ir, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "wall", {"id": "wall", "name": "Switch 1", "transport": "z2m", "controls": [],
                                               "_ctl": {"state_left": {"code": "state_left", "label": "Left", "kind": "switch"},
                                                        "battery": {"code": "battery", "kind": "sensor"}}})
    r = me.ir_remote_add(me.RemoteBody(name="Ceiling"))
    learn(r["id"], "On/Off", frame="1,2,3", group="Light")
    learn(r["id"], "II", frame="4,5,6", group="Fan")
    learn(r["id"], "Off", frame="7,8,9", group="Fan")
    a = me.ir_appliance_add(me.ApplianceBody(name="Lamp with fan", remote=r["id"], blaster="t-2"))
    for bad in ({"device": "wall", "code": "nope"}, {"device": "wall", "code": "battery"}, {"device": ["wall"], "code": "state_left"}):
        with pytest.raises(HTTPException) as e:
            me.ir_appliance_patch(a["id"], me.AppliancePatch(power=bad))
        assert e.value.status_code in (400, 404), bad
    me.ir_appliance_patch(a["id"], me.AppliancePatch(power={"device": "wall", "code": "state_left"}))
    d = core.DEVICES[a["id"]]
    assert d["ir_power"] == {"device": "wall", "code": "state_left"}
    press = lambda code: core.CONTROL_KINDS["ir_press"](d, d["_ctl"][code], True)
    monkeypatch.setitem(core._status_cache, "wall", {"online": True, "values": {"state_left": False}, "raw": {}})
    n = len(ir)
    with pytest.raises(HTTPException) as e:                          # the switch is off: no frame to a dead fixture
        press("ir_ii")
    assert e.value.status_code == 409 and "Switch 1 (Left)" in e.value.detail and len(ir) == n
    monkeypatch.setitem(core._status_cache, "wall", {"online": True, "values": {"state_left": True}, "raw": {}})
    press("ir_on_off")                                                 # a toggle alone: nothing to remember
    press("ir_ii")
    vals = core.status_copy(a["id"], {})["values"]
    assert ir[-1] == ("t-2/tx/ir_raw", "4,5,6") and vals["sent_Fan"] == "II" and "sent_Light" not in vals
    me._on_event({"kind": "state", "device": "wall", "code": "state_left", "old": True, "new": False})
    vals = core.status_copy(a["id"], {})["values"]
    assert "sent_Fan" not in vals and vals["power"] is False              # power gone: nothing is "sent" any more
    monkeypatch.setitem(core._status_cache, "wall", {"online": False, "values": {"state_left": False}, "raw": {}})
    press("ir_on_off")                                                 # unknown (offline): the press goes
    me.ir_appliance_patch(a["id"], me.AppliancePatch(power={}))
    assert "power" not in me._appliance(a["id"])


def test_a_blaster_is_added_from_the_dashboard_and_one_blaster_learns(ir, monkeypatch):
    subs = []
    monkeypatch.setattr(core, "_mqtt_client", type("C", (), {"subscribe": lambda self, t: subs.append(t)})())
    handlers = list(core.MQTT_HANDLERS)
    try:
        for bad in ("", "Bad Name", "home"):
            with pytest.raises(HTTPException):
                me.ir_blaster_add(me.BlasterBody(node=bad))
        with pytest.raises(HTTPException) as e:
            me.ir_blaster_add(me.BlasterBody(node="t-2"))
        assert e.value.status_code == 409
        out = me.ir_blaster_add(me.BlasterBody(node="kitchen-ir", name="Kitchen IR"))
        assert out == {"node": "kitchen-ir", "name": "Kitchen IR", "rx": False} and me._rx_node() == "t-1"
        assert subs == ["kitchen-ir/status", "kitchen-ir/rx/+"] and (("kitchen-ir",), me._handle) in core.MQTT_HANDLERS   # it works at once
        me._handle(["kitchen-ir", "status"], b"online")
        assert me._node_online("kitchen-ir")
        assert me.ir_blaster_patch("kitchen-ir", me.BlasterBody(rx=True))["rx"] is True
        assert me._rx_node() == "kitchen-ir" and [b["node"] for b in me.BLASTERS if b.get("rx")] == ["kitchen-ir"]   # one learns
        with pytest.raises(HTTPException):
            me.ir_blaster_patch("kitchen-ir", me.BlasterBody(rx=False))           # move it instead
    finally:
        core.MQTT_HANDLERS[:] = handlers
        core.MQTT_SUBSCRIPTIONS[:] = [t for t in core.MQTT_SUBSCRIPTIONS if not t.startswith("kitchen-ir/")]


def test_a_removed_feed_device_keeps_its_wiring_and_a_removed_blaster_leaves_nothing_behind(ir, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "wall", {"id": "wall", "name": "Switch 1", "transport": "z2m", "controls": [],
                                               "_ctl": {"state_left": {"code": "state_left", "kind": "switch"}}})
    r = me.ir_remote_add(me.RemoteBody(name="Ceiling"))
    a = me.ir_appliance_add(me.ApplianceBody(name="Lamp", remote=r["id"], blaster="t-2"))
    me.ir_appliance_patch(a["id"], me.AppliancePatch(power={"device": "wall", "code": "state_left"}))
    me._set_values(a["id"], {"power": True})
    for fn in core.DEVICE_REMOVED:
        fn(core.DEVICES["wall"])
    assert me._power(me._appliance(a["id"])) == {"device": "wall", "code": "state_left"}      # a device may come back: its owner removes it
    assert "power" not in (core.status_copy(a["id"], {}).get("values") or {})              # the gone feed's last word is forgotten
    assert [e["target"] for e in core.wiring() if e["switch"]["dev"] == "wall"] == [{"dev": a["id"]}]
    me.ir_appliance_del(a["id"])
    assert not [e for e in core.wiring() if e["target"].get("dev") == a["id"]]               # the tile deleted: its feed goes too
    me.BLASTERS.append({"node": "t-9"})
    me._listen("t-9")
    me._node_status["t-9"] = True
    forgot = []
    mp = sys.modules["yashome_plugins.map"]
    monkeypatch.setattr(mp, "forget_thing", lambda dev: forgot.append(dev) or 1)
    assert me.ir_blaster_del("t-9")["removed"] == "t-9"
    assert "t-9/status" not in core.MQTT_SUBSCRIPTIONS and ("t-9",) not in [tuple(p) for p, _ in core.MQTT_HANDLERS]
    assert "t-9" not in me._node_status and "blaster:t-9" not in core.DEVICES
    assert forgot == []                                                  # its map icon stays, marked as gone


def test_a_typed_name_of_a_fronted_blaster_is_its_devices_name(ir, monkeypatch):
    monkeypatch.setattr(core, "_name_overrides", {})
    monkeypatch.setitem(core.DEVICES, "box_card", {"id": "box_card", "name": "box_card", "ir_blaster": "t-7", "transport": "mqtt", "controls": []})
    out = me.ir_blaster_add(me.BlasterBody(node="t-7", name="Bedroom blaster"))
    assert out["name"] == "Bedroom blaster" and core.DEVICES["box_card"]["name"] == "Bedroom blaster" and "name" not in me._blaster("t-7")
    me.BLASTERS.remove(me._blaster("t-7"))
    me._unlisten("t-7")


def test_a_blaster_topic_the_card_also_reads_stays_subscribed_and_learning_blocks_a_delete(ir, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "card9", {"id": "card9", "name": "Card", "transport": "mqtt", "availability_topic": "t-8/status", "ir_blaster": "t-8", "controls": []})
    me.BLASTERS.append({"node": "t-8"})
    me._listen("t-8")
    me._unlisten("t-8")
    assert "t-8/status" in core.MQTT_SUBSCRIPTIONS and "t-8/rx/+" not in core.MQTT_SUBSCRIPTIONS     # the card's availability still comes
    assert ("t-8",) not in [tuple(p) for p, _ in core.MQTT_HANDLERS]
    monkeypatch.setitem(me._learn, "until", time.time() + 30)
    monkeypatch.setitem(me._learn, "node", "t-8")
    with pytest.raises(HTTPException) as e:
        me.ir_blaster_del("t-8")
    assert e.value.status_code == 409 and "learning" in e.value.detail
    monkeypatch.setitem(me._learn, "until", 0)
    me.ir_blaster_del("t-8")


def test_a_probe_only_toggle_is_no_user_of_the_blaster_that_learns(ir, monkeypatch):
    monkeypatch.setattr(me, "TOGGLES", [{"id": "box", "name": "Box", "probe": {"host": "192.0.2.4", "port": 80}}])
    assert "Box" not in me._blaster_users(me._rx_node())


def test_any_removed_plugin_device_fires_device_removed_but_a_relink_does_not(ir, monkeypatch):
    seen = []
    monkeypatch.setattr(core, "DEVICE_REMOVED", [lambda d: seen.append(d["id"])])
    monkeypatch.setattr(core, "_owned", lambda *a: None)                         # as if the owning plug-in removed them
    for did in ("zb_gone", "zb_relink"):
        monkeypatch.setitem(core.DEVICES, did, {"id": did, "name": did, "transport": "z2m", "controls": []})
    core.remove_device("zb_relink", "z2m", keep_state=True)
    core.remove_device("zb_gone", "z2m")
    assert seen == ["zb_gone"]


def test_blaster_topics_are_subscribed_once_and_kept_while_any_device_reads_under_them(ir, monkeypatch):
    me.BLASTERS.append({"node": "rv"})
    for _ in range(3):
        me._listen("rv")
        me._unlisten("rv") if _ < 2 else None
    assert core.MQTT_SUBSCRIPTIONS.count("rv/status") == 1
    monkeypatch.setitem(core.DEVICES, "rxdev", {"id": "rxdev", "name": "Rx", "transport": "mqtt", "state_topic": "rv/rx/x", "controls": []})
    me._unlisten("rv")
    assert "rv/rx/+" in core.MQTT_SUBSCRIPTIONS and "rv/status" not in core.MQTT_SUBSCRIPTIONS      # rv/rx/+ still delivers rv/rx/x
    me.BLASTERS.remove(me._blaster("rv"))
    assert me._matches("a/+/c", "a/b/c") and me._matches("a/#", "a/b/c") and not me._matches("a/+", "a/b/c")


def test_the_cards_rebuilt_after_a_removal_wait_for_the_lock(ir, monkeypatch):
    ran = []
    monkeypatch.setattr(me, "_sync", lambda: ran.append(1))
    with me._lock:                                                                     # an edit is running
        t = threading.Thread(target=me._sync_locked)
        t.start()
        t.join(0.2)
        assert t.is_alive() and ran == []
    t.join(2)
    assert ran == [1]


def test_a_group_that_is_part_of_the_remote_stays_one_group_kind_and_does_not_make_zones(ir):
    rid = me.ir_remote_add(me.RemoteBody(name="Media"))["id"]
    learn(rid, "1", group="Digits")
    learn(rid, "Power")
    rec = me._remote(rid)
    patch = lambda bid, **kw: me.ir_button_rename(rid, bid, me.NameBody(**kw))
    d1 = next(b["id"] for b in rec["buttons"] if b["name"] == "1")
    pw = next(b["id"] for b in rec["buttons"] if b["name"] == "Power")
    assert [b["part"] for b in patch(d1, part=True)["buttons"]] == [True, False]
    learn(rid, "2", group="Digits")                                       # a new key of a keypad joins it
    d2 = next(b["id"] for b in rec["buttons"] if b["name"] == "2")
    assert {b["name"]: b["part"] for b in me._public_remote(rec)["buttons"]} == {"1": True, "Power": False, "2": True}
    patch(pw, group="Digits")                                             # moved in: it takes the group's kind
    assert next(b for b in rec["buttons"] if b["id"] == pw)["part"] is True
    patch(pw, group="")                                                   # moved out to no group: no kind
    assert "part" not in next(b for b in rec["buttons"] if b["id"] == pw)
    assert next(b for b in patch(pw, part=True)["buttons"] if b["id"] == pw)["part"] is False     # no group: never part
    key = lambda bs: sorted(bs, key=lambda b: b["id"])
    assert key(me._flatten(me._nest(rec))["buttons"]) == key(rec["buttons"])      # the flag survives the file (grouped order)
    ctl = {c["button"]: c["part"] for c in me._device_of({"id": "box", "remote": rid, "blaster": "t-2"})["controls"]}
    assert ctl == {d1: True, pw: False, d2: True}                          # the card reads it


def test_a_mixed_group_of_old_data_is_set_whole_by_its_switch(ir):
    rid = me.ir_remote_add(me.RemoteBody(name="Old"))["id"]
    learn(rid, "1", group="Digits")
    learn(rid, "2", group="Digits")
    rec = me._remote(rid)
    rec["buttons"][0]["part"] = True                                      # mixed, as an older file may have it
    assert me._group_part(rec, "Digits") is False
    for b in list(rec["buttons"]):
        me.ir_button_rename(rid, b["id"], me.NameBody(part=True))       # what the switch sends for each button
    assert all(b["part"] for b in rec["buttons"]) and me._group_part(rec, "Digits") is True


def test_a_tile_that_extends_a_device_without_a_face_works_and_health_says_so(monkeypatch):
    monkeypatch.setitem(core.DEVICES, "bl", {"id": "bl", "name": "Blaster", "category": "ir", "ir_blaster": "bl", "controls": []})
    monkeypatch.setitem(core.DEVICES, "tvn", {"id": "tvn", "name": "Network TV", "category": "tv", "controls": []})
    monkeypatch.setattr(me, "APPLIANCES", [{"id": "t1", "name": "Old TV", "remote": "r", "device": "bl"},
                                           {"id": "t2", "name": "TV keys", "remote": "r", "device": "tvn"}, {"id": "t3", "name": "Free", "remote": "r"}])
    rows = me._health()
    assert [r["id"] for r in rows] == ["ir:host:t1"] and rows[0]["state"] == "warn"


def test_an_appliance_merged_into_its_device_is_that_devices_ir_part(ir, monkeypatch):
    r = me.ir_remote_add(me.RemoteBody(name="TV remote"))
    learn(r["id"], "Power on")
    learn(r["id"], "Menu")
    a = me.ir_appliance_add(me.ApplianceBody(name="TV IR", remote=r["id"], blaster="t-2"))
    monkeypatch.setitem(core.DEVICES, "tvhost", core._plugin_device({"id": "tvhost", "name": "TV", "transport": "nettv", "category": "tv",
                                                                    "controls": [{"kind": "switch", "code": "power"}]}))
    monkeypatch.setattr(core, "merged", lambda did: "tvhost" if did == a["id"] else None)
    try:
        me._sync()
        host = core.DEVICES["tvhost"]
        assert a["id"] not in core.DEVICES                                       # no device of its own
        assert {"ir_power_on", "ir_menu"} <= set(host["_ctl"]) and host["_ctl"]["ir_menu"]["part"] == "ir"
        assert host["parts"]["ir"]["remote_name"] == "TV remote" and host["_ctl"]["power"]["_fallback"] == ["ir"]
        ctl = host["_ctl"]["ir_menu"]
        core.CONTROL_KINDS["ir_press"](core._part_cfg("tvhost", host, ctl), ctl, True)
        assert ir[-1] == ("t-2/tx/ir_raw", "9060,-4520,550")                     # through its blaster
    finally:
        core.PARTS.pop("tvhost", None)
