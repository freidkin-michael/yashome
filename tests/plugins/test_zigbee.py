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
import types

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.zigbee"]
ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "zigbee"

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
    assert me._bridge_online is True and core._status_cache["zs_br"]["online"] is True


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
    (tmp_path / "config" / "mosquitto").mkdir(parents=True)
    (tmp_path / "plugins").mkdir()
    shutil.copytree(PLUGIN, tmp_path / "plugins" / "zigbee")
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


def test_setup_scripts_are_valid_sh():
    for f in (PLUGIN / "setup").glob("*.sh"):
        subprocess.run(["sh", "-n", str(f)], check=True)
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(PLUGIN / "setup", pathlib.Path(d) / "setup")
        assert json.loads((pathlib.Path(d) / "setup" / "broker.json").read_text())["device_readonly"] == ["zigbee2mqtt/#"]


def test_parity_with_the_core_that_had_zigbee_built_in():
    """A real house's device list (names and addresses replaced) and the same frames through the
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
