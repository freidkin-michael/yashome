"""switchbot: a lock is a generic MQTT device on switchbot/<id>/; only the house token adds one, and only
a SwitchBot device is replaced; the health rows; the bridge's settings, commands and state."""
import importlib.util
import os
import pathlib
import sys
import types

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.switchbot"]
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
HOST = pathlib.Path(me.__file__).parent / "host"


@pytest.fixture(autouse=True)
def devices(monkeypatch):
    saved = []
    monkeypatch.setattr(core, "DEVICES", dict(core.DEVICES))
    monkeypatch.setattr(core, "_mqtt_devices_upsert", saved.append)
    monkeypatch.setattr(core, "_mqtt_resubscribe_generic", lambda: 0)
    return saved


def test_a_running_bridge_is_found_so_its_id_is_never_typed(devices, monkeypatch):
    """The Add window offers the bridges the broker holds an availability of; a lock in the house already is not offered."""
    seen = ["switchbot/front_lock/availability", "switchbot/yard/availability", "switchbot/Bad Id/availability", "switchbot/x/y/availability"]
    monkeypatch.setattr(core, "_retained_topics", lambda filters, listen=2.5: list(seen))
    c = TestClient(core.app)
    assert c.get("/api/switchbot/found", headers=HOUSE).json() == {"found": ["front_lock", "yard"]}
    assert c.put("/api/switchbot/locks/yard", headers=HOUSE, json={"name": "Yard", "model": "lock"}).status_code == 200
    assert c.get("/api/switchbot/found", headers=HOUSE).json() == {"found": ["front_lock"]}


def test_a_lock_is_added_as_a_generic_device(devices, monkeypatch):
    c = TestClient(core.app)
    r = c.put("/api/switchbot/locks/front_lock", headers=HOUSE, json={"name": "Yard", "model": "lock_ultra"})
    assert r.status_code == 200 and devices[-1]["id"] == "front_lock"
    d = core.DEVICES["front_lock"]
    lock = d["_ctl"]["locked"]
    assert lock["payload_on"] == "LOCKED" and lock["command_topic"] == "switchbot/front_lock/set"
    assert d["availability_topic"] == "switchbot/front_lock/availability" and d["switchbot"] == {"model": "lock_ultra"}
    assert [k["id"] for k in c.get("/api/switchbot", headers=HOUSE).json()] == ["front_lock"]
    assert c.put("/api/switchbot/locks/front_lock", json={"name": "x"}).status_code == 401
    phone = "yfd_" + "p" * 40
    monkeypatch.setattr(core, "TOKEN_CHECKS", [lambda tok, method, path: tok == phone])
    assert c.put("/api/switchbot/locks/front_lock", headers={"Authorization": "Bearer " + phone},
                 json={"name": "x"}).status_code == 403


class Broker:
    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    def publish(self, topic, payload=None, retain=False):
        self.sent.append((topic, payload))


def test_a_lock_command_never_shows_locked_before_the_lock_says_so(devices, monkeypatch):
    c = TestClient(core.app)
    broker = Broker()
    monkeypatch.setattr(core, "_mqtt_client", broker)
    assert c.put("/api/switchbot/locks/front_lock", headers=HOUSE, json={}).status_code == 200
    real = {"online": False, "offline_since": 1.0, "values": {"locked": False}}
    monkeypatch.setattr(core, "_status_cache", {"front_lock": real})
    r = c.post("/api/devices/front_lock/control", headers=HOUSE, json={"code": "locked", "value": True})
    sent = lambda: [m for m in broker.sent if m[0].startswith("switchbot/")]
    assert r.status_code == 409 and not sent()
    real["online"] = True
    real.pop("offline_since")
    r = c.post("/api/devices/front_lock/control", headers=HOUSE, json={"code": "locked", "value": True})
    assert r.status_code == 200 and sent() == [("switchbot/front_lock/set", "LOCKED")]
    assert core.get_status("front_lock") == {"online": True, "values": {"locked": False}}


def test_a_rule_on_the_lock_never_shows_locked_before_the_lock_says_so(devices, monkeypatch):
    broker = Broker()
    monkeypatch.setattr(core, "_mqtt_client", broker)
    core._register_mqtt_device(me.entry("front_lock", "Front", "lock"))
    monkeypatch.setattr(core, "_status_cache", {"front_lock": {"online": True, "values": {"locked": False}}})
    monkeypatch.setitem(core._bindings, "nfc", {"card": {"lock": {"target": "front_lock", "code": "locked", "action": "on"}}})
    core._execute_binding("nfc", "card", "lock")
    assert ("switchbot/front_lock/set", "LOCKED") in broker.sent
    assert core.get_status("front_lock")["values"] == {"locked": False}


def test_an_offline_rule_is_logged_as_skipped_not_fired(devices, monkeypatch):
    monkeypatch.setattr(core, "_execute_binding", lambda *a: {"target": "front_lock", "code": "locked",
                                                             "value": True, "error": "offline"})
    monkeypatch.setitem(core._events, "nfc", [{"code": "card", "value": "lock"}])
    monkeypatch.setitem(core.BY_ID_NAME, "front_lock", "Front")
    assert core._safe_execute_binding("nfc", "card", "lock")["error"] == "offline"
    assert core._events["nfc"][-1]["fired"] == "skipped: Front offline"


def test_a_rule_on_an_offline_lock_is_not_sent(devices, monkeypatch):
    broker = Broker()
    monkeypatch.setattr(core, "_mqtt_client", broker)
    core._register_mqtt_device(me.entry("front_lock", "Front", "lock"))
    monkeypatch.setattr(core, "_status_cache", {"front_lock": {"online": False, "offline_since": 1.0,
                                                               "values": {"locked": False}}})
    monkeypatch.setitem(core._bindings, "nfc", {"card": {"lock": {"target": "front_lock", "code": "locked", "action": "on"}}})
    assert core._execute_binding("nfc", "card", "lock")["error"] == "offline"
    assert not [m for m in broker.sent if m[0].startswith("switchbot/")]


def test_a_device_with_availability_is_not_made_online_by_a_command(devices, monkeypatch):
    core.DEVICES["plug"] = {**me.entry("plug", "plug", "lock"), "switchbot": None}
    core.DEVICES["plug"]["_ctl"] = {"locked": {**core.DEVICES["plug"]["controls"][0], "confirm": False}}
    monkeypatch.setattr(core, "_status_cache", {"plug": {"online": False, "values": {}}})
    core._optimistic("plug", "locked", True)
    assert core.get_status("plug")["values"]["locked"] is True and core.get_status("plug")["online"] is False


def test_only_a_switchbot_device_is_replaced(devices):     # cutover-compat: the hand-made old entry
    c = TestClient(core.app)
    core.DEVICES["old_lock"] = {"id": "old_lock", "transport": "mqtt", "model": "SwitchBot Lock Ultra", "controls": []}
    core.DEVICES["lamp"] = {"id": "lamp", "transport": "mqtt", "model": "Sonoff", "controls": []}
    assert c.put("/api/switchbot/locks/old_lock", headers=HOUSE, json={}).status_code == 200
    assert core.DEVICES["old_lock"]["_ctl"]["locked"]["state_topic"] == "switchbot/old_lock/state"
    assert c.put("/api/switchbot/locks/lamp", headers=HOUSE, json={}).status_code == 409


@pytest.mark.parametrize("lid, body", [("Bad Id", {}), ("x" * 33, {}), ("ok", {"model": "door"})])
def test_bad_locks_are_refused(lid, body):
    assert TestClient(core.app).put(f"/api/switchbot/locks/{lid}", headers=HOUSE, json=body).status_code == 400


def test_health_rows(monkeypatch):
    rows = {"a": {"online": False}, "b": {"online": True, "values": {"battery": "12"}},
            "c": {"online": True, "values": {"battery": 80}}}
    for lid in rows:
        core.DEVICES[lid] = me.entry(lid, lid, "lock")
    monkeypatch.setattr(core, "get_status", lambda lid: rows.get(lid, {}))
    got = {r["id"]: r["state"] for r in me._health()}
    assert got == {"switchbot-a": "bad", "switchbot-b": "warn", "switchbot-c": "ok"}


@pytest.fixture
def bridge(monkeypatch):
    """The host bridge with its BLE and MQTT libraries stubbed out."""
    model = types.SimpleNamespace(LOCK="lock", LOCK_PRO="pro", LOCK_ULTRA="ultra")
    stubs = {"paho": types.ModuleType("paho"), "paho.mqtt": types.ModuleType("paho.mqtt"),
             "paho.mqtt.client": types.ModuleType("paho.mqtt.client"), "bleak": types.ModuleType("bleak"),
             "switchbot": types.ModuleType("switchbot"), "switchbot.const": types.ModuleType("switchbot.const"),
             "switchbot.devices": types.ModuleType("switchbot.devices")}
    stubs["bleak"].BleakScanner = object
    stubs["switchbot"].parse_advertisement_data = None
    stubs["switchbot.const"].SwitchbotModel = model
    stubs["switchbot.devices"].lock = None
    for k, v in stubs.items():
        monkeypatch.setitem(sys.modules, k, v)
    spec = importlib.util.spec_from_file_location("switchbot_lock_host", HOST / "switchbot-lock.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GOOD = {"SWITCHBOT_LOCK_ID": "front_lock", "SWITCHBOT_LOCK_MAC": "aa:bb:cc:dd:ee:ff",
        "SWITCHBOT_LOCK_KEY_ID": "ff", "SWITCHBOT_LOCK_ENC_KEY": "0" * 32}


def test_bridge_settings(bridge):
    cfg = bridge.config(GOOD)
    assert cfg["base"] == "switchbot/front_lock" and cfg["mac"] == "AA:BB:CC:DD:EE:FF" and cfg["model"] == "ultra"
    for bad in ({"SWITCHBOT_LOCK_ENC_KEY": ""}, {"SWITCHBOT_LOCK_ID": "a/b"}, {"SWITCHBOT_LOCK_ID": "Front.Lock"},
                {"SWITCHBOT_LOCK_MAC": "aa:bb"},
                {"SWITCHBOT_LOCK_ENC_KEY": "zz"}, {"SWITCHBOT_LOCK_MODEL": "door"}):
        with pytest.raises(SystemExit) as e:
            bridge.config({**GOOD, **bad})
        assert e.value.code == 2


def test_bridge_commands_and_state(bridge):
    assert [bridge.command(p) for p in ("LOCKED", " unlock\n", "on", "open", "")] == ["lock", "unlock", "lock", None, None]
    assert bridge.state_of({"status": "LockStatus.LOCKED", "battery": 90, "door_open": True}) == \
        {"state": "LOCKED", "battery": 90, "door": "open", "calibrated": None}
    assert bridge.state_of({"status": "UNLOCKED"})["door"] is None


def test_a_retained_command_never_moves_the_bolt(bridge):
    queued = []
    b = types.SimpleNamespace(loop=types.SimpleNamespace(call_soon_threadsafe=lambda f, x: f(x)),
                              cmd_q=types.SimpleNamespace(put_nowait=queued.append))
    msg = lambda retain: types.SimpleNamespace(retain=retain, payload=b"unlock")
    bridge.Bridge._on_mq_message(b, None, None, msg(True))
    bridge.Bridge._on_mq_message(b, None, None, msg(False))
    assert queued == ["unlock"]


def test_a_lock_name_is_a_device_name(devices, monkeypatch):
    c = TestClient(core.app)
    core.DEVICES["nm_gate"] = {"id": "nm_gate", "name": "Gate", "transport": "mqtt", "controls": []}
    assert c.put("/api/switchbot/locks/front_lock", headers=HOUSE, json={"name": " gate ", "model": "lock_ultra"}).status_code == 409
    assert c.put("/api/switchbot/locks/front_lock", headers=HOUSE, json={"name": "Yard\x07", "model": "lock_ultra"}).status_code == 400
