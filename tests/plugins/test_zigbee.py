"""zigbee: bridge/devices -> core devices (transport z2m) through the core's device API, state and
availability, bridge state, actuation and rule shortcuts, the periodic /get, pairing, the key rename
and the setup files (secret.yaml, broker account). Ported from the tests of the earlier core that had Zigbee built in."""
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.zigbee"]
ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "transports" / "zigbee"

SWITCH = {"type": "switch", "features": [{"type": "binary", "property": "state", "access": 7}]}


def z(name, exposes=(SWITCH,), **kw):
    return {"friendly_name": name, "ieee_address": "0x" + name.encode().hex()[:16].ljust(16, "0"), "type": "Router",
            "definition": {"model": "M1", "exposes": list(exposes)}, **kw}


@pytest.fixture
def bridge(monkeypatch):
    """A clean zigbee state: no z2m devices, bridge online, a fake broker client that records."""
    sent = []
    cli = types.SimpleNamespace(publish=lambda t, p=None, **k: sent.append((t, p)) or types.SimpleNamespace(rc=0),
                                is_connected=lambda: True)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    monkeypatch.setattr(core, "_mqtt_pub", lambda c, t, p=None, **k: sent.append((t, p)))
    monkeypatch.setattr(me, "_bridge_online", True)
    yield sent
    me.sync_devices([])
    me._pairing_found.clear()


def test_registered_as_a_transport_plugin():
    assert core.TRANSPORTS["z2m"] is me.publish_set and core.TRANSPORT_BINDINGS["z2m"] is me.binding
    assert "zigbee2mqtt" in core.MQTT_NAMESPACES and core._NS_OWNER["zigbee2mqtt"] == "zigbee"
    subs = [t for t in core.MQTT_SUBSCRIPTIONS if t.startswith("zigbee2mqtt/")]
    assert subs.index("zigbee2mqtt/bridge/devices") < subs.index("zigbee2mqtt/+")
    assert me.get_all in core.RESYNC_HOOKS and me.on_rename in core.ON_RENAME


def test_two_gang_dimmer_and_meta():
    exposes = [
        {"type": "light", "features": [
            {"type": "binary", "property": "state_l1", "access": 7},
            {"type": "numeric", "property": "brightness_l1", "value_max": 1000, "access": 7},
            {"type": "binary", "property": "state_l2", "access": 7},
            {"type": "numeric", "property": "brightness_l2", "value_max": 1000, "access": 7},
            {"type": "numeric", "property": "min_brightness_l1", "access": 7},
        ]},
        {"type": "numeric", "property": "linkquality", "access": 1},
        {"type": "numeric", "property": "battery", "access": 1},
        {"type": "enum", "property": "action", "values": ["single", "double"]},
    ]
    ctls = me.controls_from_exposes(exposes)
    kinds = [(c["kind"], c["code"]) for c in ctls]
    assert ("switch", "state_l1") in kinds and ("bright", "brightness_l2") in kinds
    assert ("bright", "min_brightness_l1") not in kinds
    assert not any(c["code"] == "linkquality" for c in ctls)
    assert next(c for c in ctls if c["code"] == "brightness_l1")["max"] == 1000
    assert next(c for c in ctls if c["code"] == "battery")["kind"] == "sensor"
    assert next(c for c in ctls if c["code"] == "action")["kind"] == "trigger_text"


def test_sync_adds_updates_and_drops_through_the_core_api(bridge):
    assert me.sync_devices([z("zs_a"), z("zs_b"), {"friendly_name": "Coordinator", "type": "Coordinator"}]) == 2
    assert core.DEVICES["zs_a"]["transport"] == "z2m" and "zs_a" in core._PLUGIN_DEVICES
    assert core._DEVICE_OWNER["zs_a"] == "zigbee"
    core._store_status("zs_a", {"online": True, "values": {"state": True}, "raw": {}})
    me.sync_devices([z("zs_a", model="M2"), z("zs_b")])
    assert core._status_cache["zs_a"]["values"] == {"state": True}         # an update keeps the state
    me.sync_devices([z("zs_a")])
    assert "zs_b" not in core.DEVICES and "zs_a" in core.DEVICES


def test_an_id_of_another_transport_and_a_bar_are_skipped(bridge):
    core.DEVICES["zs_taken"] = {"id": "zs_taken", "name": "t", "transport": "mqtt", "controls": [], "_ctl": {}}
    try:
        assert me.sync_devices([z("zs_taken"), z("a|b"), z("a/b")]) == 0
        assert core.DEVICES["zs_taken"]["transport"] == "mqtt" and "a|b" not in core.DEVICES
    finally:
        core.DEVICES.pop("zs_taken", None)


def test_unsupported_and_ef00_devices(bridge):
    tuya = z("zs_tuya", endpoints={"1": {"clusters": {"input": ["manuSpecificTuya"]}}})
    me.sync_devices([z("zs_blank", exposes=[{"type": "numeric", "property": "linkquality", "access": 1}]), tuya])
    assert core.DEVICES["zs_blank"]["unsupported"] and core.DEVICES["zs_blank"]["controls"][0]["code"] == "linkquality"
    assert core.DEVICES["zs_tuya"]["_skip_get"] is True
    assert me.binding("s", "action", "x", {"target": "zs_tuya", "code": "state", "action": "toggle"}) is None


def test_registry_update_keeps_offline(bridge):
    core._status_cache["zs_off"] = {"online": False, "availability": "offline", "values": {}, "raw": {}}
    me.sync_devices([z("zs_off")])
    assert core._status_cache["zs_off"]["online"] is False


def test_bridge_devices_marks_the_registry_ready(bridge, monkeypatch):
    monkeypatch.setattr(me, "_loaded", False)
    core.registry_pending("zigbee")
    me.on_message(["zigbee2mqtt", "bridge", "devices"], json.dumps([z("zs_r")]).encode(), True)
    assert "zigbee" not in core.REGISTRY_PENDING and me._loaded and "zs_r" in core.DEVICES


def test_availability_offline_is_immediate(bridge):
    me.sync_devices([z("zs_av")])
    core._status_cache["zs_av"] = {"online": True, "values": {"state": False}, "raw": {}}
    me.on_message(["zigbee2mqtt", "zs_av", "availability"], b'{"state":"offline"}', True)
    assert core._status_cache["zs_av"]["online"] is False
    assert not core._conditions_pass([{"device": "zs_av", "code": "state", "op": "off"}])


def test_bridge_state_parsed_and_takes_its_devices_along(bridge):
    me.sync_devices([z("zs_br")])
    core._status_cache["zs_br"] = {"online": True, "values": {"state": True}, "raw": {}}
    me.on_bridge(["zigbee2mqtt", "bridge", "state"], b'{"state":"offline"}')
    assert me._bridge_online is False and core._status_cache["zs_br"]["online"] is False
    assert not core._conditions_pass([{"device": "zs_br", "code": "state", "op": "on"}])
    me.on_bridge(["zigbee2mqtt", "bridge", "state"], b"online")
    st = core._status_cache["zs_br"]               # back after an outage: waits for its own report
    assert me._bridge_online is True and st["online"] is False and st["waiting"] == "return"
    me.on_message(["zigbee2mqtt", "zs_br"], b'{"state":"OFF"}', False)
    st = core._status_cache["zs_br"]
    assert st["online"] is True and "waiting" not in st and st["values"]["state"] is False


def test_a_device_waiting_since_the_start_is_live_only_on_its_own_word(bridge):
    core._status_cache["zs_st"] = {"online": False, "waiting": "start", "values": {"state": True}, "raw": {}}
    me.sync_devices([z("zs_st")])                   # registering it says nothing about it
    assert core._status_cache["zs_st"]["online"] is False and core._status_cache["zs_st"]["waiting"] == "start"
    me.on_message(["zigbee2mqtt", "zs_st", "availability"], b'{"state":"online"}', True)
    assert core._status_cache["zs_st"]["online"] is True and "waiting" not in core._status_cache["zs_st"]


def test_a_bridge_down_takes_devices_waiting_since_the_start_offline(bridge):
    me.sync_devices([z("zs_wb")])
    core._status_cache["zs_wb"] = {"online": False, "waiting": "start", "values": {"state": True}, "raw": {}}
    me.on_bridge(["zigbee2mqtt", "bridge", "state"], b'{"state":"offline"}')
    assert core._status_cache["zs_wb"]["offline_since"] and "waiting" not in core._status_cache["zs_wb"]
    me.on_bridge(["zigbee2mqtt", "bridge", "state"], b'{"state":"online"}')


def test_a_rule_on_an_offline_zigbee_device_sends_nothing(bridge, monkeypatch):
    me.sync_devices([z("zs_off")])
    sent = []
    monkeypatch.setattr(core, "_mqtt_client", type("C", (), {"is_connected": lambda s: True,
                                                             "publish": lambda s, t, p=None, *a, **k: sent.append(t)})())
    core._status_cache["zs_off"] = {"online": False, "offline_since": time.time(), "values": {"state": False}}
    monkeypatch.setitem(core._bindings, "zsrc", {"b": {"s": {"target": "zs_off", "code": "state", "action": "on"}}})
    r = core._execute_binding("zsrc", "b", "s")
    assert r["error"] == "offline" and not [t for t in sent if t.startswith("zigbee2mqtt/zs_off")]


def test_a_device_back_from_an_outage_is_asked_once(bridge, monkeypatch):
    me.sync_devices([z("zs_ask")])
    sent = []
    monkeypatch.setattr(core, "_mqtt_client", type("C", (), {"is_connected": lambda s: True,
                                                             "publish": lambda s, t, p=None, *a, **k: sent.append((t, p))})())
    core._status_cache["zs_ask"] = {"online": False, "waiting": "return", "waiting_since": 700.0, "values": {}}
    core._asked_wait.pop("zs_ask", None)
    core._liveness_tick(700.0 + core.ASK_AFTER_S + 1)
    core._liveness_tick(700.0 + core.ASK_AFTER_S + 2)
    assert [t for t, _p in sent if t == "zigbee2mqtt/zs_ask/get"] == ["zigbee2mqtt/zs_ask/get"]


def test_an_unreadable_zigbee_device_is_not_asked(bridge, monkeypatch):
    me.sync_devices([z("zs_ef")])
    core.DEVICES["zs_ef"]["_skip_get"] = True
    sent = []
    monkeypatch.setattr(core, "_mqtt_client", type("C", (), {"is_connected": lambda s: True,
                                                             "publish": lambda s, t, p=None, *a, **k: sent.append(t)})())
    assert me.ask("zs_ef") is False and sent == []


def test_devices_registered_while_the_bridge_is_down_are_offline(bridge, monkeypatch):
    monkeypatch.setattr(me, "_bridge_online", False)
    core._status_cache["zs_bd"] = {"online": False, "waiting": "start", "values": {"state": True}, "raw": {}}
    me.sync_devices([z("zs_bd")])
    assert core._status_cache["zs_bd"]["offline_since"] and "waiting" not in core._status_cache["zs_bd"]


def test_a_bridge_down_wins_over_a_devices_own_online_in_either_order(bridge, monkeypatch):
    for first_bridge in (True, False):
        did = f"zs_o{int(first_bridge)}"
        me.sync_devices([z(did)])
        core._status_cache[did] = {"online": False, "waiting": "start", "values": {}, "raw": {}}
        frames = [lambda: me.on_bridge(["zigbee2mqtt", "bridge", "state"], b'{"state":"offline"}'),
                  lambda: me.on_availability(did, b'{"state":"online"}')]
        for f in (frames if first_bridge else frames[::-1]):
            f()
        st = core._status_cache[did]
        assert st["online"] is False and st.get("offline_since"), (first_bridge, st)
        me.on_bridge(["zigbee2mqtt", "bridge", "state"], b'{"state":"online"}')


def test_an_empty_availability_payload_says_nothing(bridge):
    me.sync_devices([z("zs_e")])
    core._status_cache["zs_e"] = {"online": True, "values": {}, "raw": {}}
    me.on_availability("zs_e", b"")
    assert core._status_cache["zs_e"]["online"] is True


def test_a_frame_answers_only_the_channels_that_changed(bridge, monkeypatch):
    me.sync_devices([z("zs_ch")])
    seen = []
    monkeypatch.setattr(core, "_store_status", lambda did, st, report=True, codes=None: seen.append(codes))
    core._status_cache["zs_ch"] = {"online": True, "values": {"state": True}, "raw": {}}
    me.on_message(["zigbee2mqtt", "zs_ch"], b'{"state":"ON"}', False)
    me.on_message(["zigbee2mqtt", "zs_ch"], b'{"state":"OFF"}', False)
    assert seen == [set(), {"state"}]


def test_registering_a_waiting_device_tells_the_mirror(bridge, monkeypatch):
    seen = []
    monkeypatch.setattr(core, "_mqtt_publish_state", lambda did: seen.append(did))
    core._status_cache["zs_wm"] = {"online": False, "waiting": "start", "values": {}, "raw": {}}
    me.sync_devices([z("zs_wm")])
    assert "zs_wm" in seen


def test_retained_frame_while_bridge_down_stays_offline(bridge, monkeypatch):
    me.sync_devices([z("zs_rt")])
    core._status_cache["zs_rt"] = {"online": False, "values": {}, "raw": {}}
    monkeypatch.setattr(me, "_bridge_online", False)
    me.on_message(["zigbee2mqtt", "zs_rt"], b'{"state":"ON"}', True)
    assert core._status_cache["zs_rt"]["online"] is False


def test_state_frame_and_a_press_reach_the_rule_engine(bridge, monkeypatch):
    fired = []
    monkeypatch.setattr(core._bg, "submit", lambda fn, *a: fired.append(a))
    me.sync_devices([z("zs_btn", exposes=[{"type": "enum", "property": "action", "values": ["single"]}]), z("zs_sw")])
    me.on_message(["zigbee2mqtt", "zs_sw"], b'{"state":"ON"}', False)
    assert core._status_cache["zs_sw"]["values"]["state"] is True
    me.on_message(["zigbee2mqtt", "zs_btn"], b'{"action":"single"}', False)
    me.on_message(["zigbee2mqtt", "zs_btn"], b'{"action":"single"}', False)
    me.on_message(["zigbee2mqtt", "zs_btn"], b'{"action":"single"}', True)
    assert fired == [("zs_btn", "action", "single")]
    me.on_message(["zigbee2mqtt", "nobody"], b'{"action":"single"}', False)
    assert "nobody" not in core.DEVICES


def test_control_and_the_bright_step_keep_the_gang(bridge):
    me.sync_devices([z("zs_dim")])
    me.publish_set(core.DEVICES["zs_dim"], "state", True, "switch")
    assert bridge[-1] == ("zigbee2mqtt/zs_dim/set", '{"state": "ON"}')
    for code, key in (("brightness_l2", "brightness_step_onoff_l2"), ("state_l2", "brightness_step_onoff_l2"),
                      ("brightness", "brightness_step_onoff")):
        r = me.binding("s", "action", "x", {"target": "zs_dim", "code": code, "action": "bright_down"})
        assert (bridge[-1][0], json.loads(bridge[-1][1])) == ("zigbee2mqtt/zs_dim/set", {key: -25})
        assert r["code"] == code


def test_control_without_broker_is_503(bridge, monkeypatch):
    me.sync_devices([z("zs_nb")])
    monkeypatch.setattr(core, "_mqtt_client", None)
    with pytest.raises(HTTPException) as e:
        me.publish_set(core.DEVICES["zs_nb"], "state", True, "switch")
    assert e.value.status_code == 503


def test_get_all_skips_fresh_asked_and_sleeping(bridge, monkeypatch):
    cli = core._mqtt_client
    me.sync_devices([z("zs_g1"), z("zs_g2"), z("zs_rem", exposes=[{"type": "enum", "property": "action", "values": []}])])
    monkeypatch.setattr(me, "_seen", {"zs_g2": me.time.monotonic()})
    monkeypatch.setattr(me, "_asked", {})
    assert me.get_all(cli) == 1 and bridge[-1][0] == "zigbee2mqtt/zs_g1/get"
    assert me.get_all(cli) == 0                       # zs_g1 asked and silent: backoff
    assert me.get_all(cli, force=True) == 2
    assert core._run_resync_hooks(cli, True) >= 2


def test_wait_devices_wakes_and_returns_at_once_when_down(bridge, monkeypatch):
    flag = {"done": False}

    def later():
        flag["done"] = True
        with me._devices_cv:
            me._devices_cv.notify_all()
    threading.Timer(0.2, later).start()
    t0 = dt.datetime.now()
    assert me.wait_devices(lambda: flag["done"], timeout=5)
    assert (dt.datetime.now() - t0).total_seconds() < 2
    monkeypatch.setattr(me, "_bridge_online", False)
    t0 = dt.datetime.now()
    assert not me.wait_devices(lambda: False, timeout=5)
    assert (dt.datetime.now() - t0).total_seconds() < 0.5


def test_pairing_refused_while_down_and_bad_names_refused(bridge, monkeypatch):
    monkeypatch.setattr(me, "_bridge_online", False)
    with pytest.raises(HTTPException) as e:
        me.pair_start()
    assert e.value.status_code == 503
    monkeypatch.setattr(me, "_bridge_online", True)
    core.DEVICES["zs_taken2"] = {"id": "zs_taken2", "name": "t", "transport": "mqtt", "controls": [], "_ctl": {}}
    try:
        for name in ("a/b", "a#b", "zs_taken2"):
            with pytest.raises(HTTPException):
                me.pair_add(me.PairAdd(ieee="0x1", friendly_name="0x1", name=name))
        assert not [t for t, _ in bridge if "rename" in t]
    finally:
        core.DEVICES.pop("zs_taken2", None)


def test_pairing_lists_joins_and_orphans(bridge):
    me.pair_start()
    assert bridge[-1][0] == "zigbee2mqtt/bridge/request/permit_join"
    me.on_bridge(["zigbee2mqtt", "bridge", "event"], json.dumps(
        {"type": "device_interview", "data": {"ieee_address": "0xabc", "friendly_name": "0xabc", "status": "successful",
                                              "definition": {"model": "M9", "vendor": "V"}}}).encode())
    me.sync_devices([z("zs_or", exposes=[])])
    s = me.pair_status()
    assert s["active"] and s["found"][0]["interview"] == "done" and s["orphans"][0]["id"] == "zs_or"


def test_a_joined_device_is_named_by_what_it_is_not_its_model(bridge):
    """The pairing list offers "Wireless button", "Light"... from the exposes; nothing known: no kind (the screen says so)."""
    assert me.kind_name([{"type": "enum", "property": "action"}, {"type": "numeric", "property": "battery"}]) == "Wireless button"
    assert me.kind_name([{"type": "light", "features": []}, {"type": "numeric", "property": "linkquality"}]) == "Light"
    assert me.kind_name([{"type": "binary", "property": "contact"}]) == "Door sensor"
    assert me.kind_name([{"type": "numeric", "property": "linkquality"}]) == "" and me.kind_name(None) == ""
    me.pair_start()
    me.on_bridge(["zigbee2mqtt", "bridge", "event"], json.dumps(
        {"type": "device_interview", "data": {"ieee_address": "0xb1", "friendly_name": "0xb1", "status": "successful",
                                              "definition": {"model": "TS0043", "exposes": [{"type": "enum", "property": "action"}]}}}).encode())
    assert next(f for f in me.pair_status()["found"] if f["ieee"] == "0xb1")["kind"] == "Wireless button"


def test_rename_moves_references_caches_and_state(bridge, monkeypatch):
    me.sync_devices([z("zs_old")])
    core._store_status("zs_old", {"online": True, "values": {"state": True}, "raw": {}})
    core._favorites.append({"device": "zs_old", "code": "state"})
    me._seen["zs_old"] = 1.0

    def renamed(old, new):
        me.sync_devices([z(new)])
        core._status_cache.pop(new, None)
    monkeypatch.setattr(me, "_ask_rename", renamed)
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    try:
        r = me.device_rename(me.RenameBody(id="zs_old", to="zs_new"))
        assert r["ok"] and r["migrated"]["favorites"] == 1 and "pending" not in r
        assert {"device": "zs_new", "code": "state"} in core._favorites and me._seen.get("zs_new") == 1.0
        assert core._status_cache["zs_new"]["values"] == {"state": True}
    finally:
        core._favorites[:] = [f for f in core._favorites if f.get("device") not in ("zs_old", "zs_new")]
    with pytest.raises(HTTPException) as e:
        me.device_rename(me.RenameBody(id="zs_new", to="zs_new"))
    assert e.value.status_code == 400


def test_setup_writes_secret_yaml_and_the_broker_account(tmp_path):
    """The core's tools/mkpasswd.py in a scratch checkout: the z2m account from broker.json, devices
    may not publish to zigbee2mqtt/#, and secrets.py writes secret.yaml + configuration.yaml."""
    core_dir = pathlib.Path(core.__file__).parent
    (tmp_path / "tools").mkdir()
    shutil.copy(core_dir / "tools" / "mkpasswd.py", tmp_path / "tools")
    shutil.copy(core_dir / "tools" / "pluginlib.py", tmp_path / "tools")
    (tmp_path / "config" / "mosquitto").mkdir(parents=True)
    (tmp_path / "plugins" / "transports").mkdir(parents=True)
    shutil.copytree(PLUGIN, tmp_path / "plugins" / "transports" / "zigbee")
    (tmp_path / ".env").write_text("PLUGINS=zigbee\nMQTT_PASSWORD=p1\nMQTT_Z2M_PASSWORD='p#2\"x'\nZ2M_AUTH_TOKEN=t3\n"
                                   "Z2M_DATA_DIR=./plugin-data/zigbee/zigbee2mqtt\nZ2M_SERIAL=/dev/serial/by-id/usb-Example_Zigbee_stick\nMQTT_DEVICE_USERS=esp\nMQTT_ESP_PASSWORD=e\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MQTT_", "Z2M_"))}
    out = subprocess.run([sys.executable, str(tmp_path / "tools" / "mkpasswd.py")], env=env,
                         capture_output=True, text=True, check=True).stdout
    assert "z2m (zigbee2mqtt)" in out
    dyn = json.loads((tmp_path / "config/mosquitto/dynamic-security.json").read_text())
    roles = {r["rolename"]: r for r in dyn["roles"]}
    assert {a["topic"] for a in roles["zigbee2mqtt"]["acls"]} == {"zigbee2mqtt/#"}
    assert any(a["topic"] == "zigbee2mqtt/#" and not a["allow"] for a in roles["devices"]["acls"])
    data = tmp_path / "plugin-data/zigbee/zigbee2mqtt"
    assert 'mqtt_password: "p#2\\"x"' in (data / "secret.yaml").read_text()
    assert (data / "configuration.yaml").read_text().startswith("# Template")


def test_a_required_transport_gets_its_broker_account(tmp_path):
    """PLUGINS names only a plug-in that requires zigbee: mkpasswd.py still creates the z2m account."""
    core_dir = pathlib.Path(core.__file__).parent
    (tmp_path / "tools").mkdir()
    shutil.copy(core_dir / "tools" / "mkpasswd.py", tmp_path / "tools")
    shutil.copy(core_dir / "tools" / "pluginlib.py", tmp_path / "tools")
    (tmp_path / "config" / "mosquitto").mkdir(parents=True)
    (tmp_path / "plugins" / "transports").mkdir(parents=True)
    shutil.copytree(PLUGIN, tmp_path / "plugins" / "transports" / "zigbee")
    dev = tmp_path / "plugins" / "devices" / "needs_zb"
    dev.mkdir(parents=True)
    (dev / "__init__.py").write_text("")
    (dev / "requires").write_text("zigbee\n")
    (tmp_path / ".env").write_text("PLUGINS=needs_zb\nMQTT_PASSWORD=p1\nMQTT_Z2M_PASSWORD=p2\nZ2M_AUTH_TOKEN=t3\n"
                                   "Z2M_DATA_DIR=./plugin-data/zigbee/zigbee2mqtt\nZ2M_SERIAL=none\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MQTT_", "Z2M_", "PLUGINS"))}
    out = subprocess.run([sys.executable, str(tmp_path / "tools" / "mkpasswd.py")], env=env,
                         capture_output=True, text=True, check=True).stdout
    assert "z2m (zigbee2mqtt)" in out
    (tmp_path / ".env").write_text((tmp_path / ".env").read_text().replace("PLUGINS=needs_zb", "PLUGINS="))
    out = subprocess.run([sys.executable, str(tmp_path / "tools" / "mkpasswd.py")], env=env,
                         capture_output=True, text=True, check=True).stdout
    assert "z2m" not in out                       # a plug-in that is off gets no account


def test_setup_scripts_are_valid_sh():
    for f in (PLUGIN / "setup").glob("*.sh"):
        subprocess.run(["sh", "-n", str(f)], check=True)
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(PLUGIN / "setup", pathlib.Path(d) / "setup")
        assert json.loads((pathlib.Path(d) / "setup" / "broker.json").read_text())["device_readonly"] == ["zigbee2mqtt/#"]


def test_parity_with_the_core_that_had_zigbee_built_in():
    """A made-up house device list (make_fixture.py: vendor Example, test addresses) and the same frames through the
    core + this plug-in give what the earlier core with Zigbee built in gave (its output, frozen)."""
    here = ROOT / "tests" / "plugins" / "zigbee_parity"
    out = subprocess.run([sys.executable, str(here / "run.py"), str(pathlib.Path(core.__file__).parent),
                          str(ROOT / "plugins"), str(here / "bridge_devices.json")],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == json.loads((here / "expected.json").read_text())


def test_a_long_gone_name_starts_clean_but_a_quick_rejoin_keeps_its_state(bridge, monkeypatch):
    me.sync_devices([z("zs_back")])
    core._store_status("zs_back", {"online": True, "values": {"state": True}, "raw": {}})
    me.sync_devices([])
    me.sync_devices([z("zs_back")])
    assert core._status_cache["zs_back"]["values"] == {"state": True}
    me.sync_devices([])
    me._gone["zs_back"] -= me.GONE_KEEP_SEC + 1
    me.sync_devices([z("zs_back")])
    assert core._status_cache["zs_back"].get("values") in ({}, None)


def test_a_device_gone_for_good_is_told_once_but_an_empty_or_half_list_tells_nobody(bridge, monkeypatch):
    seen = []
    monkeypatch.setattr(core, "DEVICE_REMOVED", [lambda d: seen.append(d["id"])])
    monkeypatch.setattr(me, "_gone", {})
    monkeypatch.setattr(me, "_gone_dev", {})
    me.sync_devices([z("zg_a"), z("zg_b"), z("zg_c")])
    me.sync_devices([z("zg_a"), z("zg_b")])                                           # one left: a normal leave
    assert me._sweep_gone(time.monotonic() + me.GONE_KEEP_SEC + 1) == ["zg_c"] and seen == ["zg_c"]
    assert me._sweep_gone(time.monotonic() + me.GONE_KEEP_SEC + 1) == []             # once
    me.sync_devices([])                                                                # an empty list (a wrong volume, a restore)
    assert "zg_a" not in core.DEVICES and me._sweep_gone(time.monotonic() + me.GONE_KEEP_SEC + 1) == [] and seen == ["zg_c"]
    me.sync_devices([z("zg_a"), z("zg_b"), z("zg_c"), z("zg_d")])
    me.sync_devices([z("zg_a")])                                                       # three of four gone at once: suspect too
    assert me._sweep_gone(time.monotonic() + me.GONE_KEEP_SEC + 1) == []


def test_the_sweep_waits_for_a_running_sync(bridge, monkeypatch):
    seen = []
    monkeypatch.setattr(core, "DEVICE_REMOVED", [lambda d: seen.append(d["id"])])
    monkeypatch.setattr(me, "_gone", {"zg_x": 0.0})
    monkeypatch.setattr(me, "_gone_dev", {"zg_x": {"dev": {"id": "zg_x"}, "left": 0.0}})
    monkeypatch.setattr(me, "_list_suspect", False)
    with me._sync_lock:                                                                # a bridge/devices sync is running
        t = threading.Thread(target=me._sweep_gone, args=(me.GONE_KEEP_SEC + 1,))
        t.start()
        t.join(0.2)
        assert t.is_alive() and seen == []
    t.join(2)
    assert seen == ["zg_x"]


def test_a_gone_record_outlives_a_restart(bridge, monkeypatch):
    seen = []
    monkeypatch.setattr(core, "DEVICE_REMOVED", [lambda d: seen.append(d["id"])])
    monkeypatch.setattr(me, "_gone", {})
    monkeypatch.setattr(me, "_gone_dev", {})
    monkeypatch.setattr(me, "_gone_saved", None)
    me.sync_devices([z("zg_a"), z("zg_b"), z("zg_c")])
    me.sync_devices([z("zg_a"), z("zg_b")])                                           # zg_c left
    assert set(json.loads(me.GONE_PATH.read_text())) == {"zg_c"}
    me._gone.clear(); me._gone_dev.clear()                                             # a restart
    me._load_gone()
    assert me._sweep_gone() == [] and seen == []                                       # not yet: its time counts on
    assert me._sweep_gone(time.monotonic() + me.GONE_KEEP_SEC + 1) == ["zg_c"] and seen == ["zg_c"]
    assert json.loads(me.GONE_PATH.read_text()) == {}


@pytest.fixture
def gone(bridge, monkeypatch):
    seen = []
    monkeypatch.setattr(core, "DEVICE_REMOVED", [lambda d: seen.append(d["id"])])
    monkeypatch.setattr(me, "_gone", {})
    monkeypatch.setattr(me, "_gone_dev", {})
    monkeypatch.setattr(me, "_gone_saved", None)
    monkeypatch.setattr(me, "_list_suspect", False)
    return seen


LATER = lambda: time.monotonic() + me.GONE_KEEP_SEC + 1


def test_a_rejoin_drops_the_gone_record_so_a_later_broken_list_tells_nobody(gone):
    me.sync_devices([z("zg_a"), z("zg_b"), z("zg_c")])
    me.sync_devices([z("zg_a"), z("zg_b")])                                           # zg_c leaves ...
    me.sync_devices([z("zg_a"), z("zg_b"), z("zg_c")])                                # ... and rejoins in time
    assert me._gone_dev == {} and json.loads(me.GONE_PATH.read_text()) == {}
    me.sync_devices([])                                                                # later a broken list
    assert me._sweep_gone(LATER()) == [] and gone == []


def test_a_restart_long_after_a_leave_with_an_empty_first_list_tells_nobody(gone):
    me.sync_devices([z("zg_a"), z("zg_b"), z("zg_c")])
    me.sync_devices([z("zg_a"), z("zg_b")])                                           # zg_c left
    me._gone.clear(); me._gone_dev.clear()                                             # a restart (zg_a, zg_b stay registered)
    me._load_gone()
    me.sync_devices([])                                                                # the first list is empty
    assert me._sweep_gone(LATER()) == [] and gone == []
    me.sync_devices([z("zg_a"), z("zg_b")])                                           # a sound list: now it is told
    assert me._sweep_gone(LATER()) == ["zg_c"] and gone == ["zg_c"]


def test_a_restart_with_the_device_back_keeps_its_values_and_tells_nobody(gone):
    me.sync_devices([z("zg_a"), z("zg_b"), z("zg_c")])
    core.store_status_exact("zg_c", {"values": {"temperature": 21}, "raw": {}, "online": True})
    me.sync_devices([z("zg_a"), z("zg_b")])                                           # zg_c left, its record saved
    me._gone.clear(); me._gone_dev.clear()                                             # a restart
    me._load_gone()
    for k in me._gone_dev:
        me._gone_dev[k]["left"] -= me.GONE_KEEP_SEC + 1                               # it left long before the restart
    me.sync_devices([z("zg_a"), z("zg_b"), z("zg_c")])                                # and is in the first list
    assert core.status_copy("zg_c", {}).get("values", {}).get("temperature") == 21
    assert me._sweep_gone(LATER()) == [] and gone == [] and me._gone_dev == {}


@pytest.mark.parametrize("text", ["[1]", '"x"', "null", "{]"])
def test_a_gone_file_that_is_not_an_object_is_ignored(gone, text):
    me.GONE_PATH.write_text(text)
    me._load_gone()
    assert me._gone_dev == {}
    me.GONE_PATH.unlink()


def _run_secrets_py(root, env):
    """setup/secrets.py as mkpasswd.py runs it: globals env (the parsed .env) and ROOT."""
    code = (PLUGIN / "setup" / "secrets.py").read_text()
    g = {"env": env, "ROOT": root, "__file__": str(PLUGIN / "setup" / "secrets.py"), "__name__": "secrets_py"}
    exec(compile(code, "secrets.py", "exec"), g)


def test_the_serial_block_follows_z2m_serial_stick_and_network(tmp_path):
    """A network coordinator gets its tcp:// port and socket settings, a stick the passed-in device; the rest of
    configuration.yaml stays, the adapter of the network stays when Z2M_ADAPTER is empty, and a change is marked
    for update.sh (a restart) only when something really changed - key order or quotes as z2m writes are no change."""
    base = {"Z2M_DATA_DIR": "z2m", "MQTT_Z2M_PASSWORD": "p", "Z2M_AUTH_TOKEN": "t"}
    _run_secrets_py(tmp_path, {**base, "Z2M_SERIAL": "/dev/serial/by-id/usb-Example_stick"})
    conf, mark = tmp_path / "z2m" / "configuration.yaml", tmp_path / "z2m" / ".serial-changed"
    assert "  port: /dev/ttyUSB0" in conf.read_text() and not mark.exists()
    conf.write_text(conf.read_text().replace("adapter: ember", "adapter: zstack"))
    _run_secrets_py(tmp_path, {**base, "Z2M_SERIAL": "tcp://192.0.2.7:6638"})
    text = conf.read_text()
    assert "serial:\n  port: tcp://192.0.2.7:6638\n  adapter: zstack\n  baudrate: 115200\n  rtscts: false\n" in text
    assert "frontend:" in text and "network_key: GENERATE" in text and mark.exists()
    mark.unlink()
    conf.write_text(text.replace("serial:\n  port: tcp://192.0.2.7:6638\n  adapter: zstack\n  baudrate: 115200\n  rtscts: false\n",
                                 "serial:\n  adapter: zstack\n  baudrate: 115200\n  port: 'tcp://192.0.2.7:6638'\n  rtscts: false\n"))
    _run_secrets_py(tmp_path, {**base, "Z2M_SERIAL": "tcp://192.0.2.7:6638"})
    assert not mark.exists()                                  # z2m's own order and quotes: nothing to do
    _run_secrets_py(tmp_path, {**base, "Z2M_SERIAL": "/dev/serial/by-id/usb-Example_stick", "Z2M_ADAPTER": "ember"})
    text = conf.read_text()
    assert "serial:\n  port: /dev/ttyUSB0\n  adapter: ember\n" in text and "baudrate" not in text and mark.exists()


def _scratch_checkout(tmp_path, env_text):
    (tmp_path / "tools").mkdir()
    shutil.copy(ROOT / "tools" / "envlib.sh", tmp_path / "tools")
    (tmp_path / ".env").write_text(env_text)
    return tmp_path


def test_secrets_sh_passes_the_stick_or_dev_null_for_a_network_coordinator(tmp_path):
    root = _scratch_checkout(tmp_path, "COMPOSE_PROFILES=voice\nZ2M_SERIAL=tcp://192.0.2.7:6638\n")

    def run():
        subprocess.run(["sh", str(PLUGIN / "setup" / "secrets.sh")], cwd=root, check=True, capture_output=True, text=True)

    run()
    env = (root / ".env").read_text()
    assert "Z2M_DEVICE=/dev/null" in env and "COMPOSE_PROFILES=voice,zigbee" in env
    (root / ".env").write_text(env.replace("tcp://192.0.2.7:6638", "/dev/serial/by-id/usb-Example_stick"))
    run()
    assert "Z2M_DEVICE=/dev/serial/by-id/usb-Example_stick" in (root / ".env").read_text()
    assert "${Z2M_DEVICE:-${Z2M_SERIAL:-/dev/null}}:/dev/ttyUSB0" in (PLUGIN / "setup" / "compose.yml").read_text()


def test_discover_reads_the_mdns_announcements(tmp_path):
    """avahi-browse -p lines of an SLZB (ezsp) and a TI one (znp), IPv6 and duplicates skipped."""
    fake = tmp_path / "bin"
    fake.mkdir()
    lines = ['+;eth0;IPv4;SLZB-06MU;_slzb-06._tcp;local',
             '=;eth0;IPv4;SLZB-06MU;_slzb-06._tcp;local;SLZB-06MU.local;192.0.2.7;6638;"radio_type=ezsp" "baud_rate=115200"',
             '=;eth0;IPv6;SLZB-06MU;_slzb-06._tcp;local;SLZB-06MU.local;fe80::1;6638;"radio_type=ezsp"',
             '=;eth0;IPv4;SLZB-06P7;_slzb-06._tcp;local;SLZB-06P7.local;192.0.2.8;6638;"radio_type=znp"']
    (fake / "avahi-browse").write_text("#!/bin/sh\n[ \"$2\" = _slzb-06._tcp ] || exit 0\ncat <<'X'\n"
                                       + "\n".join(lines + lines[1:2]) + "\nX\n")
    (fake / "avahi-browse").chmod(0o755)
    out = subprocess.run(["sh", str(PLUGIN / "setup" / "discover.sh")], check=True, capture_output=True, text=True,
                         env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}).stdout
    assert out.splitlines() == ["tcp://192.0.2.7:6638 ember", "tcp://192.0.2.8:6638 zstack"]


def test_zigbee2mqtts_page_goes_through_the_dashboard_with_a_pass(monkeypatch):
    """No port of its own: GET /plugins/zigbee/ui/... with a pass (from POST /api/zigbee/ui, the house token) is handed on to
    zigbee2mqtt; without one, or with a path out of it, nothing goes; the plug-in's own ui.js is not taken."""
    from fastapi.testclient import TestClient
    house = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
    monkeypatch.setattr(me, "_ui_passes", {})
    seen = []

    class R:
        status, headers = 200, {"Content-Type": "text/html", "Set-Cookie": "x=1"}

        def read(self):
            return b"<html>z2m</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(me._ui_opener, "open", lambda req, timeout=15: seen.append(req.full_url) or R())
    c = TestClient(core.app)
    assert c.get("/plugins/zigbee/ui/").status_code == 401 and not seen
    assert c.post("/api/zigbee/ui").status_code == 401
    r = c.post("/api/zigbee/ui", headers=house)
    assert r.json() == {"url": "/plugins/zigbee/ui/"}
    ck = r.headers["set-cookie"]
    assert "Path=/plugins/zigbee/ui" in ck and "HttpOnly" in ck and "SameSite=strict" in ck.replace("Strict", "strict")
    r = c.get("/plugins/zigbee/ui/assets/a.js?v=1")
    assert r.status_code == 200 and r.content == b"<html>z2m</html>" and "set-cookie" not in r.headers
    assert seen == ["http://zigbee2mqtt:8080/plugins/zigbee/ui/assets/a.js?v=1"] and "https:" in r.headers["content-security-policy"]
    assert c.get("/plugins/zigbee/ui/a/../../../api/state").status_code in (401, 404) and len(seen) == 1
    assert c.get("/plugins/zigbee/ui.js").status_code == 200 and len(seen) == 1
    me._ui_passes.clear()
    assert c.get("/plugins/zigbee/ui/").status_code == 401               # a pass ends (or the house restarted)


def test_zigbee2mqtts_live_link_goes_through_with_its_token_filled_in(monkeypatch):
    import websockets.asyncio.client as wsc
    from fastapi.testclient import TestClient
    monkeypatch.setattr(me, "_ui_passes", {"p1": time.time() + 60})
    monkeypatch.setenv("Z2M_AUTH_TOKEN", "z2m-tok")
    got = {}

    class Up:
        def __init__(self):
            self.msgs = ['{"topic":"bridge/state"}']

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.msgs:
                return self.msgs.pop(0)
            await __import__("asyncio").sleep(3600)

        async def send(self, m):
            got.setdefault("sent", []).append(m)

        async def close(self):
            got["closed"] = True

    async def connect(url, **kw):
        got["url"] = url
        return Up()
    monkeypatch.setattr(wsc, "connect", connect)
    c = TestClient(core.app)
    with pytest.raises(Exception):
        with c.websocket_connect("/plugins/zigbee/ui/api") as ws:          # no pass
            ws.receive_text()
    c.cookies.set("z2m_pass", "p1", path="/plugins/zigbee/ui")
    with c.websocket_connect("/plugins/zigbee/ui/api") as ws:
        assert ws.receive_text() == '{"topic":"bridge/state"}'
        ws.send_text('{"topic":"bridge/request/health_check"}')
        time.sleep(0.2)
    assert got["url"] == "ws://zigbee2mqtt:8080/plugins/zigbee/ui/api?token=z2m-tok"
    assert got["sent"] == ['{"topic":"bridge/request/health_check"}']
