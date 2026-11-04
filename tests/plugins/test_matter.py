"""matter: Matter devices through python-matter-server (role "Matter controller" of plug-in computers). A fake controller
speaks the server's WebSocket API (server info first, {message_id, command, args} -> {message_id, result | error_code},
events {event, data}) with made-up nodes; the plug-in's session runs on a loop of its own, as on the backend's.
The Thread dataset and the pairing codes must never show in an answer, a health row or the log."""
import asyncio
import json
import logging
import sys
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

import app as core

me = sys.modules["yashome_plugins.matter"]
HOUSE = {"Authorization": "Bearer test-token-not-real"}
DS = "0e080000000000010000" + "0510" + "cd" * 16           # a made-up dataset (timestamp, network key)
MANUAL, QR = "3497-011-2332", "mt:y.k9042c00ka0648g00"      # the examples of the Matter specification
INFO = {"fabric_id": 1, "compressed_fabric_id": 1, "schema_version": 11, "min_supported_schema_version": 9, "sdk_version": "2025.1.0",
        "wifi_credentials_set": False, "thread_credentials_set": False, "bluetooth_enabled": True}


def lamp(available=True):
    """A dimmable color lamp on Thread: OnOff, Level and ColorControl (not shown yet) on endpoint 1."""
    return {"node_id": 1, "available": available, "is_bridge": False, "attributes": {
        "0/29/1": [29, 40, 49, 53], "0/40/1": "Acme", "0/40/3": "Bulb A19", "0/40/5": "", "0/49/65532": 2,
        "1/29/1": [3, 4, 6, 8, 29, 768], "1/6/0": True, "1/8/0": 127, "1/768/7": 250}}


def sensors():
    """A Wi-Fi multi-sensor: temperature, humidity, occupancy, contact on endpoints 1-4, a battery on endpoint 0."""
    return {"node_id": 2, "available": True, "is_bridge": False, "attributes": {
        "0/29/1": [29, 40, 47, 49], "0/40/1": "Acme", "0/40/3": "Sense 4", "0/40/5": "Hall sensor", "0/47/12": 180, "0/49/65532": 1,
        "1/29/1": [3, 1026], "1/1026/0": 2150, "2/29/1": [1029], "2/1029/0": 4512, "3/29/1": [1030], "3/1030/0": 1,
        "4/29/1": [69], "4/69/0": False}}


def plug2(available=False):
    """A two-socket plug: OnOff on endpoints 1 and 2."""
    return {"node_id": 3, "available": available, "is_bridge": False, "attributes": {
        "0/29/1": [29, 40, 49], "0/40/3": "Duo plug", "0/49/65532": 1, "1/29/1": [6], "1/6/0": False, "2/29/1": [6], "2/6/0": True}}


class Fake:
    """The controller: every call recorded; commission_with_code answers `new`; `fail` = {command: details} answers errors."""

    def __init__(self, nodes, bt=True):
        self.nodes, self.bt, self.calls, self.conns, self.fail = nodes, bt, [], [], {}
        self.new = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.srv = asyncio.run_coroutine_threadsafe(self._start(), self.loop).result(5)
        self.url = f"ws://127.0.0.1:{self.srv.sockets[0].getsockname()[1]}/ws"

    async def _start(self):
        return await serve(self.handler, "127.0.0.1", 0)

    async def handler(self, ws):
        self.conns.append(ws)
        await ws.send(json.dumps({**INFO, "bluetooth_enabled": self.bt}))
        async for raw in ws:
            m = json.loads(raw)
            cmd, args = m["command"], m.get("args") or {}
            self.calls.append((cmd, args))
            if cmd in self.fail:
                await ws.send(json.dumps({"message_id": m["message_id"], "error_code": 3, "details": self.fail[cmd]}))
                continue
            result = {"start_listening": self.nodes, "commission_with_code": self.new}.get(cmd)
            await ws.send(json.dumps({"message_id": m["message_id"], "result": result}))
            if cmd == "remove_node":
                await ws.send(json.dumps({"event": "node_removed", "data": args["node_id"]}))

    def push(self, event, data):
        for ws in self.conns:
            asyncio.run_coroutine_threadsafe(ws.send(json.dumps({"event": event, "data": data})), self.loop).result(5)

    def close(self):
        if self.loop.is_closed() or not self.loop.is_running():
            return                                                  # closed by the test already
        async def stop():
            self.srv.close()
            for ws in self.conns:
                await ws.close()
            await self.srv.wait_closed()
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)

    def of(self, cmd):
        return [a for c, a in self.calls if c == cmd]


def wait(pred, s=5.0):
    end = time.time() + s
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def ctl(monkeypatch, tmp_path):
    """The fake controller with the three nodes, the session connected, the house's Thread network kept."""
    monkeypatch.setattr(me, "CACHE", tmp_path / "nodes.json")
    monkeypatch.setattr(sys.modules["yashome_plugins.thread"], "dataset", lambda: DS, raising=False)
    monkeypatch.setitem(me._srv, "computer", "server-a")
    monkeypatch.setitem(me._srv, "checked", True)
    fake = Fake([lamp(), sensors(), plug2()])
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    task = asyncio.run_coroutine_threadsafe(me.session(fake.url), loop)
    assert wait(lambda: me._srv["up"] and "matter-3" in core.DEVICES)
    yield fake
    fake.close()
    try:
        task.result(5)
    except Exception:  # noqa: BLE001 - the connection ends with the fake
        pass
    loop.call_soon_threadsafe(loop.stop)
    for nid in list(me._nodes) + [1, 2, 3, 7]:
        me.on_event("node_removed", nid)


def client():
    return TestClient(core.app)


def test_every_node_is_a_device_with_a_channel_per_cluster(ctl):
    d = core.DEVICES["matter-1"]
    assert (d["transport"], d["category"], d["name"], d["vendor"], d["model"]) == ("matter", "switch", "Bulb A19", "Acme", "Bulb A19")
    assert [(c["kind"], c["code"]) for c in d["controls"]] == [("switch", "state"), ("bright", "brightness")]
    assert d["_not_shown"] == ["Color"]
    assert core.get_status("matter-1")["values"] == {"state": True, "brightness": 127}
    s = core.DEVICES["matter-2"]
    assert (s["category"], s["name"]) == ("sensor", "Hall sensor")
    assert {c["code"]: (c["kind"], c.get("unit"), c.get("vtype")) for c in s["controls"]} == {
        "battery": ("sensor", "%", "num"), "temperature": ("sensor", "\u00b0C", "num"), "humidity": ("sensor", "%", "num"),
        "occupancy": ("sensor", "", "bool"), "contact": ("sensor", "", "bool")}
    st = core.get_status("matter-2")
    assert st["online"] and st["values"] == {"battery": 90, "temperature": 21.5, "humidity": 45.12, "occupancy": True, "contact": False}
    two = core.DEVICES["matter-3"]
    assert [c["code"] for c in two["controls"]] == ["state_1", "state_2"] and two["_ep"] == {"state_1": [1, "onoff"], "state_2": [2, "onoff"]}
    assert core.get_status("matter-3")["online"] is False                  # not available: offline at once
    v = client().get("/api/matter", headers=HOUSE).json()
    assert [(n["node_id"], n["network"], n["available"]) for n in v["nodes"]] == [(1, "thread", True), (2, "wifi", True), (3, "wifi", False)]
    assert v["nodes"][0]["not_shown"] == ["Color"] and v["server"]["up"] and v["server"]["info"]["bluetooth_enabled"]
    assert json.loads(me.CACHE.read_text())[0]["id"] == "matter-1"        # the cards before the controller answers, next start


def test_an_attribute_report_updates_the_card(ctl):
    ctl.push("attribute_updated", [1, "1/6/0", False])
    ctl.push("attribute_updated", [2, "1/1026/0", 1990])
    assert wait(lambda: core.get_status("matter-1")["values"].get("state") is False)
    assert wait(lambda: core.get_status("matter-2")["values"].get("temperature") == 19.9)
    ctl.push("node_updated", plug2(available=True))
    assert wait(lambda: core.get_status("matter-3")["online"] is True)


def test_the_dataset_goes_to_the_controller_first_and_never_out(ctl, caplog):
    caplog.set_level(logging.DEBUG)
    names = [c for c, _ in ctl.calls]
    assert names[:2] == ["set_thread_dataset", "start_listening"] and ctl.of("set_thread_dataset") == [{"dataset": DS}]
    shown = json.dumps(client().get("/api/matter", headers=HOUSE).json()) + json.dumps(me.health())
    assert DS not in shown and "cd" * 16 not in shown
    ctl.fail["set_thread_dataset"] = "bad dataset " + DS        # even a controller that repeats it in its error
    me._given["hash"] = ""
    c, loop = me._client
    assert asyncio.run_coroutine_threadsafe(me.give_dataset(c), loop).result(5) is False
    assert DS not in caplog.text and "cd" * 16 not in caplog.text


@pytest.mark.parametrize("bt", [True, False])
def test_commissioning_by_either_code(ctl, caplog, bt):
    caplog.set_level(logging.DEBUG)
    ctl.bt = bt
    me._srv["info"]["bluetooth_enabled"] = bt
    new = {"node_id": 7, "available": True, "attributes": {"0/40/3": "Night light", "0/49/65532": 2, "1/29/1": [6], "1/6/0": False}}
    ctl.new = new
    c = client()
    assert c.post("/api/matter/commission", json={"code": "12345"}, headers=HOUSE).status_code == 400
    r = c.post("/api/matter/commission", json={"code": MANUAL}, headers=HOUSE)
    assert r.status_code == 200 and r.json()["state"] == "running" and r.json()["kind"] == "manual"
    assert wait(lambda: c.get("/api/matter/commission", headers=HOUSE).json()["state"] == "done")
    assert ctl.of("commission_with_code") == [{"code": "34970112332", "network_only": not bt}]    # Bluetooth and the network, or the network
    assert core.DEVICES["matter-7"]["name"] == "Night light" and c.get("/api/matter/commission", headers=HOUSE).json()["device"] == "matter-7"
    r = c.post("/api/matter/commission", json={"code": QR}, headers=HOUSE)
    assert r.json()["kind"] == "qr"
    assert wait(lambda: len(ctl.of("commission_with_code")) == 2 and c.get("/api/matter/commission", headers=HOUSE).json()["state"] == "done")
    assert ctl.of("commission_with_code")[1] == {"code": "MT:Y.K9042C00KA0648G00", "network_only": not bt}
    ctl.fail["commission_with_code"] = "Commission with code failed for node 8."
    c.post("/api/matter/commission", json={"code": MANUAL}, headers=HOUSE)
    assert wait(lambda: c.get("/api/matter/commission", headers=HOUSE).json()["state"] == "failed")
    assert "failed for node 8" in c.get("/api/matter/commission", headers=HOUSE).json()["error"]
    assert "34970112332" not in caplog.text and MANUAL not in caplog.text and "K9042C00" not in caplog.text.upper()


def test_a_command_from_the_card_and_from_a_rule_is_a_device_command(ctl):
    c = client()                                                    # the card sends percent: 50 % = level 127
    r = c.post("/api/devices/matter-1/control", json={"code": "brightness", "value": 50}, headers=HOUSE)
    assert r.status_code == 200, r.text
    r = c.post("/api/devices/matter-1/control", json={"code": "state", "value": False}, headers=HOUSE)
    assert r.status_code == 200, r.text
    me.control(core.DEVICES["matter-3"], "state_2", True, "switch")              # a rule: the worker thread's way
    assert ctl.of("device_command") == [
        {"node_id": 1, "endpoint_id": 1, "cluster_id": 8, "command_name": "MoveToLevelWithOnOff",
         "payload": {"level": 127, "transitionTime": 0, "optionsMask": 0, "optionsOverride": 0}},
        {"node_id": 1, "endpoint_id": 1, "cluster_id": 6, "command_name": "Off", "payload": {}},
        {"node_id": 3, "endpoint_id": 2, "cluster_id": 6, "command_name": "On", "payload": {}}]
    ctl.fail["device_command"] = "Node 1 is not (yet) available."
    with pytest.raises(HTTPException) as e:
        me.control(core.DEVICES["matter-1"], "state", True, "switch")
    assert e.value.status_code == 502
    with pytest.raises(HTTPException) as e:
        me.control(core.DEVICES["matter-2"], "temperature", 1, "sensor")
    assert e.value.status_code == 400


def test_unpair_removes_the_node_and_its_device(ctl):
    c = client()
    assert c.delete("/api/matter/nodes/99", headers=HOUSE).status_code == 404
    r = c.delete("/api/matter/nodes/3", headers=HOUSE)
    assert r.status_code == 200 and ctl.of("remove_node") == [{"node_id": 3}]
    assert "matter-3" not in core.DEVICES and [n["node_id"] for n in r.json()["nodes"]] == [1, 2]


def test_health_rows(ctl, monkeypatch):
    rows = {r["id"]: r for r in me.health()}
    assert rows["matter"]["state"] == "ok" and rows["matter:3"]["state"] == "warn" and "matter:1" not in rows
    ctl.close()
    assert wait(lambda: not me._srv["up"])
    assert [(r["id"], r["state"]) for r in me.health()] == [("matter", "bad")]
    assert core.get_status("matter-1")["online"] is False
    monkeypatch.setitem(me._srv, "computer", None)
    assert me.health()[0]["detail"] == "no controller"
    with pytest.raises(HTTPException) as e:
        me.control(core.DEVICES["matter-1"], "state", True, "switch")
    assert e.value.status_code == 503


def test_a_list_without_nodes_keeps_the_devices(ctl):
    me.sync_nodes([])
    assert "matter-1" in core.DEVICES
    me.sync_nodes([lamp(), sensors()])
    assert "matter-3" not in core.DEVICES and "matter-1" in core.DEVICES


def test_the_role_scripts_pin_the_image_and_keep_the_api_on_the_loopback():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    text = (root / "helper/roles/matter-install.sh").read_text()
    assert "python-matter-server@sha256:" in text and '"--listen-address", "127.0.0.1"' in text and "network_mode: host" in text
    assert "/run/dbus:/run/dbus:ro" in text
    helper = (root / "helper/helper.py").read_text()
    assert '("matter", "install"): ("helper/roles/matter-install.sh", ())' in helper and '"matter": 5580' in helper


def test_the_computers_role_is_one_per_house(monkeypatch):
    pcs = sys.modules["yashome_plugins.computers"]
    monkeypatch.setattr(pcs, "_computers", {"a": {"name": "server-a", "host": "192.0.2.10", "user": "someone"},
                                            "b": {"name": "server-b", "host": "192.0.2.11", "user": "someone"}})
    monkeypatch.setattr(pcs, "_status", {"a": {"ok": True, "roles": ["matter"]}, "b": {"ok": True, "roles": []}})
    assert pcs._roles(pcs._computers["a"], pcs._status["a"])["matter"] == {"installed": True, "active": False, "one": True}
    c = client()
    r = c.post("/api/computers/b/roles/matter", json={"action": "install"}, headers=HOUSE)
    assert r.status_code == 409 and "server-a" in r.json()["detail"]                # one per house
    assert c.post("/api/computers/a/roles/matter", json={"action": "use"}, headers=HOUSE).status_code == 400
