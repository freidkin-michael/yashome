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
    me.BLASTERS[:] = [{"node": "t-hub", "name": "Hub", "rx": True}, {"node": "t-bed", "name": "Bedroom"}]
    me.REMOTES[:] = []
    me.APPLIANCES[:] = []
    me._node_status.clear()
    yield sent
    me.APPLIANCES[:] = []
    me._sync()
    me.REMOTES[:] = []
    me.BLASTERS[:] = []


def learn(rid, name, frame="9060,-4520,550", group=None):
    def answer():
        while me._learn["node"] is None:
            time.sleep(0.01)
        me._handle(["t-hub", "rx", "ir_raw"], frame.encode())
    threading.Thread(target=answer, daemon=True).start()
    return asyncio.run(me.ir_learn(rid, me.LearnBody(name=name, group=group, timeout=5)))


def test_learn_press_and_the_appliance_device(ir):
    r = me.ir_remote_add(me.RemoteBody(name="\u0412\u0435\u043d\u0442\u0438\u043b\u044f\u0442\u043e\u0440 \u043a\u0443\u0445\u043d\u044f"))
    assert r["id"] == "ventilyator_kuhnya"
    out = learn(r["id"], "Power", group="Fan")
    assert ("t-hub/learn/command", "ON") in ir and ("t-hub/learn/command", "OFF") in ir
    assert out["button"]["n"] == 3 and "code" not in out["button"]
    a = me.ir_appliance_add(me.ApplianceBody(name="Fan", remote=r["id"], blaster="t-bed"))
    d = core.DEVICES[a["id"]]
    assert d["transport"] == "ir" and d["category"] == "ir" and list(d["_ctl"]) == ["ir_power"]
    core.CONTROL_KINDS["ir_press"](d, d["_ctl"]["ir_power"], True)
    assert ir[-1] == ("t-bed/tx/ir_raw", "9060,-4520,550")
    learn(r["id"], "Speed")
    assert sorted(core.DEVICES[a["id"]]["_ctl"]) == ["ir_power", "ir_speed"]     # updated at run time
    me.ir_appliance_patch(a["id"], me.AppliancePatch(name="Fan 2"))
    assert core.DEVICES[a["id"]]["name"] == "Fan 2"
    me.ir_remote_del(r["id"])
    assert a["id"] not in core.DEVICES and not me.APPLIANCES


def test_no_frame_times_out_and_a_stale_frame_is_ignored(ir):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    me._handle(["t-hub", "rx", "ir_raw"], b"1,2,3")                       # nobody is learning
    with pytest.raises(HTTPException) as e:
        asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="x", timeout=5)))  # clamps to >= 5 s
    assert e.value.status_code == 408 and me._learn["node"] is None


def test_too_long_is_422(ir):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))

    def answer():
        while me._learn["node"] is None:
            time.sleep(0.01)
        me._handle(["t-hub", "rx", "error"], b"too_long")
    threading.Thread(target=answer, daemon=True).start()
    with pytest.raises(HTTPException) as e:
        asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="x", timeout=5)))
    assert e.value.status_code == 422


def test_tile_follows_its_blaster(ir):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    learn(r["id"], "Power")
    a = me.ir_appliance_add(me.ApplianceBody(name="TV bed", remote=r["id"], blaster="t-bed"))
    assert core._status_copy(a["id"], {}).get("online") is False
    me._handle(["t-bed", "status"], b"online")
    assert core._status_copy(a["id"], {})["online"] is True


def test_refusals(ir):
    r = me.ir_remote_add(me.RemoteBody(name="TV"))
    for body in (me.ApplianceBody(name="x", remote="nope", blaster="t-bed"),
                 me.ApplianceBody(name="x", remote=r["id"], blaster="nope"),
                 me.ApplianceBody(name="", remote=r["id"], blaster="t-bed")):
        with pytest.raises(HTTPException):
            me.ir_appliance_add(body)
    with pytest.raises(HTTPException):
        asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="x", proto="uv")))


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


def test_ir_toggle_sends_only_when_the_state_differs_and_probes(ir):
    import socket as so
    r = me.ir_remote_add(me.RemoteBody(name="Box"))
    learn(r["id"], "power")
    srv = so.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    me.TOGGLES[:] = [{"id": "t_box", "name": "Box", "power": {"remote": r["id"], "button": "power", "via": "t-bed"},
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
        assert ir[-1] == ("t-bed/tx/ir_raw", "9060,-4520,550")
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
        me.APPLIANCES.append({"id": "t_other", "name": "TV", "remote": r["id"], "blaster": "t-bed"})
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
        me._learn.update(node="t-hub", proto="ir", until=time.time() + 30, sid=me._learn["sid"] + 1)
        ir.append(("t-hub/learn/command", "ON"))
        return True
    monkeypatch.setattr(me, "run_in_threadpool", waited)
    with pytest.raises(HTTPException) as e:
        asyncio.run(me.ir_learn(r["id"], me.LearnBody(name="a", timeout=5)))
    assert e.value.status_code == 409 and me._learn["node"] == "t-hub" and me._learn["until"] > time.time()
    assert ir[-1] == ("t-hub/learn/command", "ON")
    me._learn.update(node=None, proto=None, until=0.0)
