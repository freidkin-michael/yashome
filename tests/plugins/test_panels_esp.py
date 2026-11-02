"""panels, ESP side: layouts go onto the house bus, a panel's commands reach only its own devices, new
screens bind by their code, builds go through safe mode before the upload."""
import json
import os
import re
import sys
import time

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.panels"]
esp = sys.modules["yashome_plugins.panels._esp"]
PORT = esp._port
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
_REAL_BROKER = esp._broker


class FakeBuild:
    """The build plug-in's client as the server behaves: its job names, a held job waits in "ready"."""
    NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

    def __init__(self):
        self.jobs, self.released, self.aborted, self.end = [], [], [], "done"
        self.calls = {}

    def submit(self, name, files, main="main.yaml", ota=None):
        if not self.NAME_RE.fullmatch(name):
            raise core.HTTPException(400, "build server: name: a-z 0-9 -, 1-40 chars")
        self.jobs.append({"name": name, "files": files, "ota": ota})
        return f"job{len(self.jobs)}"

    def job(self, jid):
        n = self.calls[jid] = self.calls.get(jid, 0) + 1
        if jid not in self.released:
            return {"state": "compiling" if n == 1 else "ready", "progress": 50, "log": []}
        return {"state": self.end, "progress": 100, "log": []}

    def release(self, jid, abort=False):
        assert self.job(jid)["state"] == "ready", "released before the job was ready"
        (self.aborted if abort else self.released).append(jid)


class Cli:
    def __init__(self):
        self.sent = []

    def publish(self, t, p=None, retain=False, qos=0):
        self.sent.append((t, p, retain))


@pytest.fixture(autouse=True)
def house(monkeypatch):
    monkeypatch.setattr(me, "_layouts", {})
    monkeypatch.setattr(me, "_save_layouts", lambda: None)
    fb, cli, broker, persisted, house_state = FakeBuild(), Cli(), [], [], {"broker_up": True}
    monkeypatch.setattr(esp, "_build", lambda: fb)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    for k, v in (("HOUSE_WIFI_SSID", "net"), ("HOUSE_WIFI_PASSWORD", "pw-for-tests"),
                 ("MQTT_PANELS_PASSWORD", "mq-for-tests"), ("PANELS_OTA_PASSWORD", "ota-for-tests"),
                 ("PANELS_AP_PASSWORD", "ap-for-tests"), ("BIND_ADDRESS", "192.0.2.1")):
        monkeypatch.setenv(k, v)
    arp({"192.0.2.21": "a1b2c3d4e5f6", "192.0.2.22": "0a0b0c0d0e0f"})
    esp._new.clear()
    esp._ips.clear()
    esp._jobs.clear()
    esp.KEYS.unlink(missing_ok=True)
    ports = {"closes": True, "opens": True}
    monkeypatch.setattr(esp, "_port", lambda ip, port, want_open, wait: ports["opens" if want_open else "closes"])
    monkeypatch.setattr(esp, "_broker", lambda cmds: broker.append(cmds) or house_state["broker_up"])
    monkeypatch.setattr(esp, "_persist", lambda: persisted.append(1))
    esp.ACCOUNTS.unlink(missing_ok=True)
    monkeypatch.setattr(esp.threading, "Thread", lambda target, args=(), daemon=None, name=None: type("T", (), {
        "start": lambda self: target(*args)})())
    monkeypatch.setattr(esp.time, "sleep", lambda s: None)
    devs = sorted(core.DEVICES)
    yield {"fb": fb, "cli": cli, "on": devs[0], "off": devs[1], "ports": ports, "broker": broker,
           "state": house_state, "persisted": persisted}
    esp.ACCOUNTS.unlink(missing_ok=True)


def arp(table, age=0):
    (core.plugin_state("panels") / "arp.json").write_text(json.dumps({"updated": time.time() - age, "arp": table}))


def _w(**kw):
    return {"id": "w1", "type": "toggle", "x": 40, "y": 40, "w": 200, "h": 80, **kw}


def test_widget_topics_go_onto_the_house_bus(house, monkeypatch):
    on = "lamp_t"
    monkeypatch.setitem(core.DEVICES, on, {"id": on, "controls": [], "_ctl": {"state": {"kind": "switch"}}})
    monkeypatch.setitem(core.DEVICES, "thermo", {"id": "thermo", "controls": [
        {"code": "temperature", "state_topic": "zigbee2mqtt/thermo"}], "_ctl": {"temperature": {"kind": "sensor"}}})
    lay = {"hw": "waveshare7", "widgets": [
        _w(state_topic=f"home/state/{on}/state", cmd_topic=f"home/cmd/{on}/state", state_on="ON"),
        _w(id="w2", type="sensor", state_topic="zigbee2mqtt/thermo")]}
    out = esp.bus_layout("p1", lay)["widgets"]
    assert out[0]["cmd_topic"] == f"panel/p1/cmd/{on}/state" and out[0]["state_topic"] == f"home/state/{on}/state"
    assert (out[0]["state_on"], out[0]["cmd_on"], out[0]["cmd_off"]) == ("true", "true", "false")
    assert out[1]["state_topic"] == "home/state/thermo/temperature"
    monkeypatch.setitem(core.DEVICES, "door", {"id": "door", "controls": [], "_ctl": {
        "contact": {"kind": "sensor_text"}, "mode": {"kind": "setting_enum"}, "level": {"kind": "bright"}}})
    out = esp.bus_layout("p1", {"widgets": [
        _w(id="a", type="sensor", state_topic="home/state/door/contact", state_on="open"),
        _w(id="b", type="button", cmd_topic="home/cmd/door/mode", cmd_payload="cool"),
        _w(id="c", type="dimmer", state_topic="home/state/door/level", dim_topic="home/cmd/door/level",
           state_max=255, cmd_max=255)]})["widgets"]
    assert out[0]["state_on"] == "open" and out[1]["cmd_payload"] == "cool"
    monkeypatch.setitem(core.DEVICES, "vol", {"id": "vol", "controls": [], "_ctl": {"level": {"kind": "setting_enum"}}})
    btn = lambda pay: esp.bus_layout("p1", {"widgets": [_w(type="button", cmd_topic="home/cmd/lamp_t/state",
                                                         cmd_payload=pay)]})["widgets"][0]["cmd_payload"]
    assert [btn(x) for x in ("OFF", "0", "on", "1", "toggle")] == ["false", "false", "true", "true", "toggle"]
    vol = esp.bus_layout("p1", {"widgets": [_w(type="dimmer", state_topic="home/state/vol/level", state_max=30)]})
    assert vol["widgets"][0]["state_max"] == 30
    assert out[2]["state_topic"] == "home/state/door/level_pct" and (out[2]["state_max"], out[2]["cmd_max"]) == (100, 100)
    with pytest.raises(core.HTTPException):
        esp.bus_layout("p1", {"widgets": [_w(state_topic="somewhere/else")]})


def test_a_panel_command_reaches_only_what_its_widgets_send(house, monkeypatch):
    on, off = house["on"], house["off"]
    me._layouts["p1"] = {"hw": "waveshare7", "widgets": [
        _w(cmd_topic=f"home/cmd/{on}/state", state_topic=f"home/state/{on}/state"),
        _w(id="w2", type="sensor", state_topic=f"home/state/{off}/battery"),
        {"id": "w3", "type": "remote", "x": 0, "y": 0, "w": 9, "h": 9, "device": off, "buttons": [{"id": "power"}]}]}
    me._layouts["tab"] = {"hw": "android", "widgets": [_w(cmd_topic=f"home/cmd/{on}/state")]}
    seen = []
    monkeypatch.setattr(core, "mqtt_command", lambda d, c, p, t="": seen.append((d, c, p)))
    for parts, retain in ((["panel", "p1", "cmd", on, "state"], False), (["panel", "p1", "cmd", on, "other"], False),
                          (["panel", "p1", "cmd", off, "state"], False), (["panel", "p1", "cmd", off, "ir_power"], False),
                          (["panel", "p1", "cmd", on, "state"], True), (["panel", "tab", "cmd", on, "state"], False),
                          (["panel", "nope", "cmd", on, "state"], False)):
        esp._on_mqtt(parts, b"true", retain)
    assert seen == [(on, "state", b"true"), (off, "ir_power", b"true")]


def test_a_new_screen_binds_by_its_code_and_gets_its_firmware(house):
    c = TestClient(core.app)
    ann = {"hw": "waveshare7", "code": "123456", "mac": "AA:BB", "node": "panel-new", "ip": "192.0.2.21"}
    esp._on_mqtt(["panel", "_new", "A1B2C3D4E5F6"], json.dumps(ann).encode(), True)
    esp._on_mqtt(["panel", "_new", "A1B2C3D4E5F6"], json.dumps({**ann, "ip": "203.0.113.9"}).encode(), True)
    esp._on_mqtt(["panel", "_new", "A1B2C3D4E5F6"], b"", True)
    esp._on_mqtt(["panel", "_new", "A1B2C3D4E5F6"], json.dumps({**ann, "hw": "onx35"}).encode(), True)
    lst = c.get("/api/panels/discovered", headers=HOUSE).json()["esp"]
    assert lst[0]["hw"] == "waveshare7"
    assert [e["mac"] for e in lst] == ["a1b2c3d4e5f6"] and "code" not in lst[0]
    bind = lambda **kw: c.post("/api/panels/bind_esp", headers=HOUSE, json={"mac": "a1b2c3d4e5f6", "code": "123456",
                                                                           "panel": "hall-1", **kw})
    assert bind(code="000000").status_code == 400
    arp({"192.0.2.21": "ffffffffffff"})
    assert bind().status_code == 409
    arp({"192.0.2.21": "a1b2c3d4e5f6"}, age=120)
    assert bind().status_code == 503
    arp({"192.0.2.21": "a1b2c3d4e5f6"})
    esp._on_mqtt(["panel", "_new", "0A0B0C0D0E0F"], json.dumps({**ann, "ip": "192.0.2.22"}).encode(), True)
    assert bind().status_code == 409
    esp._new.pop("0a0b0c0d0e0f")
    for bad in ("Hall 1", "hall_1"):
        assert bind(panel=bad).status_code == 400 and bad not in me._layouts
    me._layouts["other"] = {"hw": "waveshare7", "widgets": [], "mac": "a1b2c3d4e5f6"}
    assert bind().status_code == 409                                     # this screen is panel "other" already
    me._layouts.pop("other")
    house["fb"].end = "error"
    assert bind().status_code == 200 and [e["mac"] for e in esp.new_list()] == ["a1b2c3d4e5f6"]
    house["fb"].end = "done"
    assert c.post("/api/panels/bind_esp", json={"mac": "a1b2c3d4e5f6", "code": "123456", "panel": "x"}).status_code == 401
    r = bind()
    assert r.status_code == 200, r.text
    job = house["fb"].jobs[-1]
    assert job["ota"] == {"address": "192.0.2.21", "hold": True} and b"panel/${name}" in job["files"]["main.yaml"]
    assert ("panel/_newcmd/A1B2C3D4E5F6/safe_mode", "123456", False) in house["cli"].sent and esp._ips["hall-1"] == "192.0.2.21"
    assert "ota-for-tests" in job["files"]["secrets.yaml"] and "panel-hall-1" in job["files"]["secrets.yaml"]   # its own account
    assert any(k.startswith("icons/") and isinstance(v, bytes) for k, v in job["files"].items())
    assert me._layouts["hall-1"]["hw"] == "waveshare7" and (me._layouts["hall-1"]["w"], me._layouts["hall-1"]["h"]) == (800, 480)
    assert ("panel/_new/A1B2C3D4E5F6", None, True) in house["cli"].sent and esp.new_list() == []
    assert "ap-for-tests" in job["files"]["secrets.yaml"] and me._layouts["hall-1"]["mac"] == "a1b2c3d4e5f6"



def test_a_build_flashes_only_a_confirmed_mac_through_safe_mode(house):
    c = TestClient(core.app)
    me._layouts["p1"] = {"hw": "waveshare7", "w": 800, "h": 480, "widgets": []}
    flash = lambda: c.post("/api/panels/p1/build", headers=HOUSE, json={"ota": True})
    row = lambda: next(p for p in c.get("/api/panels", headers=HOUSE).json() if p["name"] == "p1")
    esp._on_mqtt(["panel", "p1", "ip"], b"192.0.2.22", True)
    assert flash().status_code == 409 and "mac" not in me._layouts["p1"]
    assert row()["candidate"] == {"ip": "192.0.2.22", "mac": "0a0b0c0d0e0f"}
    assert c.put("/api/panels/p1/mac", json={"mac": "0a0b0c0d0e0f"}).status_code == 401
    assert c.put("/api/panels/p1/mac", headers=HOUSE, json={"mac": "nope"}).status_code == 400
    assert c.put("/api/panels/p1/mac", headers=HOUSE, json={"mac": "0A0B0C0D0E0F"}).status_code == 200
    assert row()["mac"] == "0a0b0c0d0e0f" and row()["candidate"] is None
    house["ports"]["closes"] = False
    r = flash()
    assert r.status_code == 200 and house["fb"].jobs[-1]["ota"] == {"address": "192.0.2.22", "hold": True}
    assert house["fb"].released == [] and house["fb"].aborted == [r.json()["job"]]
    assert ("panel/p1/safe_mode/command", "PRESS", False) in house["cli"].sent
    st = c.get("/api/panels/p1/build", headers=HOUSE).json()
    assert st["phase"] == "error" and "restart" in st["error"]
    house["ports"].update(closes=True, opens=False)
    flash()
    assert house["fb"].released == [] and "safe mode" in esp._jobs["p1"]["error"]
    me._layouts["p1"]["mac"] = "0A0B0C0D0E0F"
    arp({"192.0.2.30": "0a0b0c0d0e0f"})
    house["ports"]["opens"] = True
    r = flash()
    assert house["fb"].jobs[-1]["ota"]["address"] == "192.0.2.30" and house["fb"].released == [r.json()["job"]]
    assert esp._jobs["p1"]["phase"] == "done" and json.loads(esp.JOBS.read_text())["p1"]["id"] == r.json()["job"]
    me._layouts["tab"] = {"hw": "android", "widgets": []}
    assert c.post("/api/panels/tab/build", headers=HOUSE, json={}).status_code == 400


def test_the_address_is_checked_again_before_the_upload_and_one_flash_at_a_time(house, monkeypatch):
    c = TestClient(core.app)
    me._layouts["p1"] = {"hw": "waveshare7", "w": 800, "h": 480, "widgets": [], "mac": "0a0b0c0d0e0f"}
    monkeypatch.setattr(esp, "_port", lambda ip, port, want_open, wait: (want_open or arp({"192.0.2.22": "ffffffffffff"})
                                                                          or True))
    assert c.post("/api/panels/p1/build", headers=HOUSE, json={"ota": True}).status_code == 200
    assert house["fb"].released == [] and "MAC" in esp._jobs["p1"]["error"]
    esp._jobs["p1"].update(phase="compiling", started=time.time())
    arp({"192.0.2.22": "0a0b0c0d0e0f"})
    assert c.post("/api/panels/p1/build", headers=HOUSE, json={"ota": True}).status_code == 409


def test_a_bad_or_stale_arp_file_is_no_view_of_the_network(house):
    f = core.plugin_state("panels") / "arp.json"
    for bad in ('[]', '{"updated": "x", "arp": {}}', '{"updated": 1e400, "arp": {"192.0.2.1": "a"}}',
                '{"updated": %d, "arp": []}' % time.time(), 'nope'):
        f.write_text(bad)
        assert esp._arp() == {}, bad


def test_new_screens_expire_and_are_capped(house, monkeypatch):
    for i in range(esp.NEW_MAX + 5):
        esp._on_mqtt(["panel", "_new", f"{i:012x}"], json.dumps({"hw": "waveshare7", "code": "1"}).encode(), True)
    assert len(esp.new_list()) == esp.NEW_MAX
    monkeypatch.setattr(esp.time, "time", lambda: 10 ** 10)
    assert esp.new_list() == []
    esp._on_mqtt(["panel", "nope", "ip"], b"192.0.2.5", True)
    assert "nope" not in esp._ips


def test_missing_secrets_are_named(house, monkeypatch):
    c = TestClient(core.app)
    monkeypatch.delenv("HOUSE_WIFI_SSID")
    r = c.post("/api/panels/starter/waveshare7", headers=HOUSE)
    assert r.status_code == 400 and "wifi_ssid" in r.json()["detail"]
    monkeypatch.setenv("HOUSE_WIFI_SSID", "net")
    assert c.post("/api/panels/starter/nope", headers=HOUSE).status_code == 404
    r = c.post("/api/panels/starter/waveshare7", headers=HOUSE)
    assert r.status_code == 200 and house["fb"].jobs[-1]["name"] == "starter-waveshare7"
    assert "panel/_new/" in house["fb"].jobs[-1]["files"]["main.yaml"].decode()


def test_the_hardware_list_comes_from_the_profiles():
    hw = {h["hw"]: h for h in TestClient(core.app).get("/api/panels/hw", headers=HOUSE).json()}
    assert {"waveshare7", "onx35"} <= set(hw) and (hw["onx35"]["w"], hw["onx35"]["h"]) == (480, 320)



def test_the_screens_account_writes_its_bus_but_never_reads_new_screens(tmp_path):
    import pathlib
    import shutil
    import subprocess
    root = pathlib.Path(core.__file__).parent
    (tmp_path / "tools").mkdir()
    for f in ("mkpasswd.py", "pluginlib.py"):
        shutil.copy(root / "tools" / f, tmp_path / "tools")
    (tmp_path / "config" / "mosquitto").mkdir(parents=True)
    (tmp_path / "plugins" / "devices").mkdir(parents=True)
    shutil.copytree(pathlib.Path(me.__file__).parent, tmp_path / "plugins" / "devices" / "panels")
    (tmp_path / ".env").write_text("PLUGINS=panels\nMQTT_PASSWORD=p1\nMQTT_PANELS_PASSWORD=p2\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MQTT_", "PLUGINS"))}
    r = subprocess.run([sys.executable, str(tmp_path / "tools" / "mkpasswd.py")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    dyn = json.loads((tmp_path / "config/mosquitto/dynamic-security.json").read_text())
    acls = {(a["acltype"], a["topic"], a["allow"]): a["priority"]
            for a in next(x for x in dyn["roles"] if x["rolename"] == "panels")["acls"]}
    assert ("publishClientSend", "panel/#", True) in acls and ("subscribePattern", "home/state/#", True) in acls
    assert acls[("publishClientReceive", "panel/_new/#", False)] > acls[("publishClientReceive", "panel/#", True)]
    assert ("publishClientSend", "home/state/#", True) not in acls
    dev = {(a["acltype"], a["topic"], a["allow"]) for a in next(x for x in dyn["roles"] if x["rolename"] == "devices")["acls"]}
    assert ("publishClientReceive", "panel/_new/#", False) in dev


def test_a_closed_port_takes_two_failed_connects_in_a_row(monkeypatch):
    seq = iter([OSError(), None, OSError(), OSError(), OSError()])

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def connect(addr, timeout):
        e = next(seq)
        if e:
            raise e
        return Conn()
    monkeypatch.setattr(esp.socket, "create_connection", connect)
    monkeypatch.setattr(esp.time, "sleep", lambda s: None)
    assert PORT("192.0.2.9", 3232, False, 60)
    assert len(list(seq)) == 1          # closed, open, closed, closed: decided; the last read is not taken


def test_names_jobs_after_a_restart_and_a_deleted_panel(house, monkeypatch):
    for good, ok in (("hall-1", True), ("hall-", False), ("-hall", False), ("hall_1", False), ("a" * 24, True)):
        assert bool(esp.ESP_NAME.fullmatch(good)) is ok, good
    with pytest.raises(ValueError, match="delete it and make it again"):
        esp.gen.generate("hall-", {"hw": "waveshare7", "widgets": []})
    esp.JOBS.write_text(json.dumps({"p1": {"id": "j1", "phase": "safe_mode"}, "p2": {"id": "j2", "phase": "done"}}))
    jobs = esp._load_jobs()
    assert jobs["p1"]["phase"] == "error" and "restart" in jobs["p1"]["error"] and jobs["p2"]["phase"] == "done"
    me._layouts["p2"] = {"hw": "waveshare7", "widgets": []}
    esp._jobs.update(jobs)
    assert TestClient(core.app).delete("/api/panels/p2", headers=HOUSE).status_code == 200 and "p2" not in esp._jobs


def test_service_keys_follow_the_firmware_and_only_the_dashboard_flashes(house, monkeypatch):
    c = TestClient(core.app)
    me._layouts["p1"] = {"hw": "waveshare7", "w": 800, "h": 480, "widgets": [], "mac": "0a0b0c0d0e0f"}
    flash = lambda h=HOUSE: c.post("/api/panels/p1/build", headers=h, json={"ota": True})
    assert flash().status_code == 200
    first = esp._keys()["p1"]["live"]
    assert ("panel/p1/safe_mode/command", "PRESS", False) in house["cli"].sent
    assert first.encode() in house["fb"].jobs[-1]["files"]["main.yaml"]
    assert flash().status_code == 200
    assert ("panel/p1/safe_mode/command", first, False) in house["cli"].sent and esp._keys()["p1"]["live"] != first
    house["fb"].end = "error"
    flash()
    assert esp._keys()["p1"]["live"] != esp._keys()["p1"]["next"]
    monkeypatch.setattr(core, "TOKEN_CHECKS", core.TOKEN_CHECKS + [lambda tok, m, path: tok == "a-full-phone"])
    phone = {"Authorization": "Bearer a-full-phone"}
    assert flash(phone).status_code == 403
    for path, body in (("/api/panels/p1/mac", {"mac": "0a0b0c0d0e0f"}), ("/api/panels/bind_esp", {"mac": "x", "code": "1", "panel": "p"}),
                       ("/api/panels/starter/waveshare7", {})):
        assert c.request("PUT" if path.endswith("/mac") else "POST", path, headers=phone, json=body).status_code == 403, path


def test_a_compile_error_frees_the_panel(house):
    c = TestClient(core.app)
    me._layouts["p1"] = {"hw": "waveshare7", "w": 800, "h": 480, "widgets": [], "mac": "0a0b0c0d0e0f"}
    house["fb"].job = lambda jid: {"state": "error", "error": "compile"}
    assert c.post("/api/panels/p1/build", headers=HOUSE, json={"ota": True}).status_code == 200
    assert esp._jobs["p1"]["phase"] == "error"
    assert c.post("/api/panels/p1/build", headers=HOUSE, json={"ota": False}).status_code == 200
    assert not esp._busy("p1")


def test_a_failure_after_the_compile_calls_the_held_job_off(house, monkeypatch):
    me._layouts["p1"] = {"hw": "waveshare7", "w": 800, "h": 480, "widgets": [], "mac": "0a0b0c0d0e0f"}

    def boom(*a, **kw):
        raise OSError("broker gone")
    monkeypatch.setattr(house["cli"], "publish", boom)
    r = TestClient(core.app).post("/api/panels/p1/build", headers=HOUSE, json={"ota": True})
    assert house["fb"].aborted == [r.json()["job"]] and "broker gone" in esp._jobs["p1"]["error"]


def test_a_knob_is_built_and_flashed_like_a_screen(house, monkeypatch):
    lamp = "lamp_k"
    monkeypatch.setitem(core.DEVICES, lamp, {"id": lamp, "controls": [], "_ctl": {"state": {"kind": "switch"},
                                                                                "brightness": {"kind": "bright"}}})
    me._layouts["knob-1"] = {"hw": "knob128", "w": 240, "h": 240, "mac": "0a0b0c0d0e0f", "widgets": [
        {"id": "r1", "type": "dimmer", "x": 0, "y": 0, "w": 1, "h": 1, "text": "Lamp",
         "state_topic": f"home/state/{lamp}/brightness", "dim_topic": f"home/cmd/{lamp}/brightness",
         "onoff_topic": f"home/state/{lamp}/state", "cmd_topic": f"home/cmd/{lamp}/state"}]}
    r = TestClient(core.app).post("/api/panels/knob-1/build", headers=HOUSE, json={"ota": True})
    assert r.status_code == 200, r.text
    text = house["fb"].jobs[-1]["files"]["main.yaml"].decode()
    assert "esp32-c3" in text and "panel/knob-1/cmd/lamp_k/brightness" in text and "home/state/lamp_k/brightness_pct" in text
    assert (lamp, "state") in esp.allowed_commands("knob-1") and (lamp, "brightness") in esp.allowed_commands("knob-1")


def test_a_builder_shaped_ac_row_reads_and_sends_true_false(house, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "ac_t", {"id": "ac_t", "controls": [], "_ctl": {
        "power": {"kind": "switch"}, "setpoint": {"kind": "sensor"}}})
    out = esp.bus_layout("k", {"widgets": [{"id": "r1", "type": "climate", "x": 0, "y": 0, "w": 1, "h": 1,
        "state_topic": "home/state/ac_t/setpoint", "onoff_topic": "home/state/ac_t/power",
        "cmd_topic": "home/cmd/ac_t/power", "dim_topic": "home/cmd/ac_t/setpoint",
        "state_on": "ON", "cmd_on": "ON", "cmd_off": "OFF"}]})["widgets"][0]
    assert (out["state_on"], out["cmd_on"], out["cmd_off"]) == ("true", "true", "false")


def test_an_ac_tile_power_command_is_a_switch_too(house, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "ac_p", {"id": "ac_p", "controls": [], "_ctl": {"power": {"kind": "switch"}}})
    out = esp.bus_layout("p", {"widgets": [{"id": "a", "type": "ac", "x": 0, "y": 0, "w": 1, "h": 1,
        "power_cmd": "home/cmd/ac_p/power", "cmd_on": "ON", "cmd_off": "OFF"}]})["widgets"][0]
    assert (out["cmd_on"], out["cmd_off"]) == ("true", "false")


def test_a_knob_with_a_builder_made_switchless_dimmer_builds(house, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "strip", {"id": "strip", "controls": [], "_ctl": {"level": {"kind": "bright"}}})
    me._layouts["knob-2"] = {"hw": "knob128", "w": 240, "h": 240, "mac": "0a0b0c0d0e0f", "widgets": [
        {"id": "r1", "type": "dimmer", "x": 0, "y": 0, "w": 1, "h": 1, "text": "Strip",
         "state_topic": "home/state/strip/level", "dim_topic": "home/cmd/strip/level"}]}
    r = TestClient(core.app).post("/api/panels/knob-2/build", headers=HOUSE, json={"ota": True})
    assert r.status_code == 200, r.text
    assert ("strip", "level") in esp.allowed_commands("knob-2")


def test_the_screens_get_the_host_port_and_never_a_loopback_broker(house, monkeypatch):
    monkeypatch.setenv("MQTT_HOST_PORT", "18830")
    c = TestClient(core.app)
    assert c.post("/api/panels/starter/waveshare7", headers=HOUSE).status_code == 200
    assert "mqtt_port: 18830" in house["fb"].jobs[-1]["files"]["secrets.yaml"]
    monkeypatch.delenv("BIND_ADDRESS")
    for base in ("http://127.0.0.1:8765", "http://0.0.0.0:8765", "http://127.1:8765", "http://home.example.org"):
        r = TestClient(core.app, base_url=base).post("/api/panels/starter/waveshare7", headers=HOUSE)
        assert r.status_code == 400 and "BIND_ADDRESS" in r.json()["detail"], base
    r = TestClient(core.app, base_url="http://192.0.2.9:8765").post("/api/panels/starter/waveshare7", headers=HOUSE)
    assert r.status_code == 200 and 'mqtt_host: "192.0.2.9"' in house["fb"].jobs[-1]["files"]["secrets.yaml"]


def test_the_old_clock_topics_are_cleared_once_connected():
    sent = []
    cli = type("Cli", (), {"publish": lambda self, t, p, retain=False: sent.append((t, p, retain))})()
    esp._old_clock_dropped.clear()
    esp._drop_old_clock(cli)
    esp._drop_old_clock(cli)
    assert sent == [("panel/_clock/time", None, True), ("panel/_clock/date", None, True)]


def test_one_screen_one_panel():
    c = TestClient(core.app)
    me._layouts.update(p1={"hw": "waveshare7", "widgets": [], "mac": "a1b2c3d4e5f6"}, p2={"hw": "waveshare7", "widgets": []})
    try:
        assert c.put("/api/panels/p2/mac", headers=HOUSE, json={"mac": "A1B2C3D4E5F6"}).status_code == 409
        assert c.put("/api/panels/p1/mac", headers=HOUSE, json={"mac": "a1b2c3d4e5f6"}).status_code == 200
    finally:
        me._layouts.pop("p1", None); me._layouts.pop("p2", None)


def test_a_hung_build_never_writes_into_the_job_that_replaced_it(monkeypatch):
    monkeypatch.setattr(esp, "_save_jobs", lambda: None)
    old, new = {"id": "j1", "phase": "compiling"}, {"id": "j2", "phase": "compiling"}
    monkeypatch.setitem(esp._jobs, "p9", new)
    esp._set("p9", old, phase="error", error="late")
    assert esp._jobs["p9"] == {"id": "j2", "phase": "compiling"}


def test_one_unmapped_widget_leaves_the_others_commands_and_is_refused_at_save(monkeypatch):
    lamp = "lamp_u"
    monkeypatch.setitem(core.DEVICES, lamp, {"id": lamp, "controls": [], "_ctl": {"state": {"kind": "switch"}}})
    me._layouts["k9"] = {"hw": "waveshare7", "widgets": [
        {"id": "a", "type": "toggle", "x": 0, "y": 0, "w": 1, "h": 1, "cmd_topic": f"home/cmd/{lamp}/state"},
        {"id": "b", "type": "sensor", "x": 0, "y": 0, "w": 1, "h": 1, "state_topic": "nobody/owns/this"}]}
    try:
        assert (lamp, "state") in esp.allowed_commands("k9")
        c = TestClient(core.app)
        old = me._layouts["k9"]["widgets"]
        assert c.put("/api/panels/k9/layout", headers=HOUSE, json={"w": 800, "h": 480, "widgets": old}).status_code == 200
        new = old + [{"id": "c", "type": "sensor", "x": 0, "y": 0, "w": 1, "h": 1, "state_topic": "nor/this"}]
        r = c.put("/api/panels/k9/layout", headers=HOUSE, json={"w": 800, "h": 480, "widgets": new})
        assert r.status_code == 400 and "belongs to no device" in r.text      # only a new or changed widget
    finally:
        me._layouts.pop("k9", None)


class _Builder:
    def __init__(self, states):
        self.states, self.released = list(states), []

    def job(self, jid):
        return {"state": self.states.pop(0) if len(self.states) > 1 else self.states[0]}

    def release(self, jid, abort=False):
        self.released.append((jid, abort))


def test_a_watch_gives_up_at_its_deadline_and_never_releases_a_replaced_job(monkeypatch):
    monkeypatch.setattr(esp, "_save_jobs", lambda: None)
    monkeypatch.setattr(esp.time, "sleep", lambda s: None)
    clock = iter(range(0, 10 ** 6, 1000))
    monkeypatch.setattr(esp.time, "time", lambda: next(clock))
    rec = {"id": "j1", "phase": "compiling"}
    monkeypatch.setitem(esp._jobs, "p8", rec)
    b = _Builder(["compiling"])
    esp._watch("p8", rec, "192.0.2.9", b, "panel/p8/safe_mode/command")
    assert rec["phase"] == "error" and "in time" in rec["error"] and b.released == [("j1", True)]
    monkeypatch.setattr(esp.time, "time", lambda: 0)
    rec2 = {"id": "j2", "phase": "compiling"}
    monkeypatch.setitem(esp._jobs, "p8", rec2)
    monkeypatch.setattr(esp, "_port", lambda *a: esp._jobs.__setitem__("p8", {"id": "j3"}) or True)
    b2 = _Builder(["ready"])
    esp._watch("p8", rec2, "192.0.2.9", b2, "panel/p8/safe_mode/command")
    assert b2.released == [("j2", True)]                                         # replaced meanwhile: aborted


def test_an_esp_panel_gets_its_own_broker_account_and_loses_it_with_the_panel(house):
    me._layouts["p5"] = {"hw": "waveshare7", "w": 800, "h": 480, "mac": "a1b2c3d4e5f6", "widgets": []}
    c = TestClient(core.app)
    r = c.post("/api/panels/p5/build", headers=HOUSE, json={"ota": False})
    assert r.status_code == 200, r.text
    text = house["fb"].jobs[-1]["files"]["secrets.yaml"]
    acct = json.loads(esp.ACCOUNTS.read_text())
    assert [a["user"] for a in acct] == ["panel-p5"] and acct[0]["topics"] == ["panel/p5/#"]
    assert "mqtt_user: \"panel-p5\"" in text and acct[0]["password"] in text and oct(esp.ACCOUNTS.stat().st_mode & 0o777) == "0o600"
    assert [x["command"] for x in house["broker"][-1]] == ["deleteClient", "deleteRole", "createRole", "createClient"]
    c.post("/api/panels/p5/build", headers=HOUSE, json={"ota": False})
    assert len(json.loads(esp.ACCOUNTS.read_text())) == 1 and len(house["broker"]) == 1      # made once
    assert c.delete("/api/panels/p5", headers=HOUSE).status_code == 200
    assert json.loads(esp.ACCOUNTS.read_text()) == [] and house["broker"][-1][0]["command"] == "deleteClient"


def test_an_account_the_broker_did_not_take_fails_the_build_and_is_sent_again(house):
    me._layouts["p6"] = {"hw": "waveshare7", "w": 800, "h": 480, "mac": "a1b2c3d4e5f7", "widgets": []}
    c = TestClient(core.app)
    house["state"]["broker_up"] = False
    r = c.post("/api/panels/p6/build", headers=HOUSE, json={"ota": False})
    assert r.status_code == 503 and "docker compose restart mosquitto" in r.text and house["persisted"]
    pw = json.loads(esp.ACCOUNTS.read_text())[0]
    assert pw["synced"] is False
    house["state"]["broker_up"] = True
    assert c.post("/api/panels/p6/build", headers=HOUSE, json={"ota": False}).status_code == 200
    acct = json.loads(esp.ACCOUNTS.read_text())[0]
    assert "synced" not in acct and acct["password"] == pw["password"] and len(house["broker"]) == 2


def test_a_starter_firmware_keeps_the_shared_account(house):
    r = TestClient(core.app).post("/api/panels/starter/waveshare7", headers=HOUSE, json={})
    assert r.status_code == 200, r.text
    assert "panel-" not in house["fb"].jobs[-1]["files"]["secrets.yaml"] and not house["broker"]
    with pytest.raises(core.HTTPException):
        esp._account("Kitchen")


class _FakeDynsec:
    """A paho stand-in that answers $CONTROL commands with mosquitto 2.1.2's own words: a client or role
    keeps existing between calls (``state``), a duplicate add is an "Internal error"."""
    state, foreign, silent = set(), None, False

    def __init__(self, *a, **k):
        self.on_message = None

    def connect(self, *a, **k):
        pass

    def subscribe(self, *a, **k):
        pass

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def _one(self, c):
        st, cmd = _FakeDynsec.state, c["command"]
        kind = "Client" if "Client" in cmd else "Role"
        name = ("c:" if kind == "Client" else "r:") + (c.get("username") if kind == "Client" else c.get("rolename"))
        if cmd.startswith("create"):
            if name in st:
                return f"{kind} already exists"
            st.add(name)
        elif cmd.startswith("delete"):
            if name not in st:
                return f"{kind} not found"
            st.discard(name)
        elif cmd.startswith("add"):
            return "Internal error"
        return None

    def publish(self, topic, payload, qos=0):
        cmds = json.loads(payload)["commands"]
        msg = lambda resp: type("M", (), {"payload": json.dumps({"responses": resp}).encode()})()
        if _FakeDynsec.foreign is not None:           # another admin's answers first: never ours
            self.on_message(None, None, msg([{"command": c["command"], **_FakeDynsec.foreign} for c in cmds]))
        if _FakeDynsec.silent:
            return
        resp = []
        for c in cmds:
            err = self._one(c)
            resp.append({"command": c["command"], "correlationData": c["correlationData"], **({"error": err} if err else {})})
        self.on_message(None, None, msg(resp))


@pytest.fixture
def dynsec(monkeypatch):
    fake = type("Paho", (), {"Client": _FakeDynsec, "CallbackAPIVersion": type("V", (), {"VERSION2": 2})})
    monkeypatch.setattr(core, "_paho", fake)
    monkeypatch.setattr(core, "_mqtt_auth", lambda cli: cli)
    monkeypatch.setattr(esp.time, "sleep", lambda s: None)
    monkeypatch.setattr(_FakeDynsec, "state", set())
    monkeypatch.setattr(_FakeDynsec, "foreign", None)
    monkeypatch.setattr(_FakeDynsec, "silent", False)
    return _FakeDynsec


def test_a_panel_account_is_replaced_as_a_whole_on_the_real_wording(dynsec, monkeypatch):
    monkeypatch.setattr(esp, "_broker", _REAL_BROKER)
    acct = lambda: [{"command": "deleteClient", "username": "panel-x"}, {"command": "deleteRole", "rolename": "panel-x"},
                    {"command": "createRole", "rolename": "panel-x", "acls": []},
                    {"command": "createClient", "username": "panel-x", "password": "p", "roles": []}]
    assert _REAL_BROKER(acct(), wait=0.2) is True                         # fresh: the deletes find nothing
    assert _REAL_BROKER(acct(), wait=0.2) is True                         # a rebuild: replaced, no "exists"
    assert _REAL_BROKER([{"command": "createRole", "rolename": "panel-x"}], wait=0.2) is False
    assert _REAL_BROKER([{"command": "addClientRole", "username": "panel-x", "rolename": "panel-x"}], wait=0.2) is False


def test_only_our_answers_count(dynsec):
    dynsec.silent = True
    dynsec.foreign = {}                                                   # another admin's SUCCESS, ours missing
    assert _REAL_BROKER([{"command": "createRole", "rolename": "r"}], wait=0.2) is False
    dynsec.silent, dynsec.foreign = False, {"error": "Not authorised"}
    assert _REAL_BROKER([{"command": "createRole", "rolename": "r2"}], wait=0.2) is True


def test_a_delete_that_fails_otherwise_than_not_found_fails(dynsec, monkeypatch):
    orig = dynsec._one
    monkeypatch.setattr(dynsec, "_one", lambda self, c: "Internal error" if c["command"] == "deleteRole" else orig(self, c))
    assert _REAL_BROKER([{"command": "deleteRole", "rolename": "panel-x"}], wait=0.2) is False


def test_the_one_panel_handler_serves_both_the_esp_panels_and_the_tablets(house, monkeypatch):
    """A second handler of the prefix "panel" shadowed the ESP relay for a day (the core takes the first one): no button worked."""
    handlers = [fn for pre, fn in core.MQTT_HANDLERS if tuple(pre) == ("panel",)]
    assert len(handlers) == 1
    on = house["on"]
    me._layouts["p1"] = {"hw": "waveshare7", "widgets": [_w(cmd_topic=f"home/cmd/{on}/state", state_topic=f"home/state/{on}/state")]}
    seen = []
    monkeypatch.setattr(core, "mqtt_command", lambda d, c, p, t="": seen.append((d, c, p)))
    handlers[0](["panel", "p1", "cmd", on, "state"], b"true", False)                      # an ESP panel's button
    handlers[0](["panel", "p1", "voice", "cmd"], b'{"t": "stop/command"}', False)         # a tablet's voice command
    assert seen == [(on, "state", b"true")] and me._VQ.get("p1")


def test_the_house_sends_the_screens_words_in_its_language_once_per_change(house, monkeypatch):
    cli = Cli()
    monkeypatch.setattr(core, "_mqtt_client", cli)
    monkeypatch.setitem(core._house, "lang", "en")
    esp._words_sent["txt"] = None
    esp.words_tick()
    esp.words_tick()
    assert len(cli.sent) == 1 and cli.sent[0][0] == esp.gen.WORDS_TOPIC and cli.sent[0][2] is True     # retained, once
    sent = json.loads(cli.sent[0][1])
    assert sent["net_wifi"] == "No Wi-Fi" and sent["knob_off"] == "off" and sent["wx"]["0"] == "Clear"
    monkeypatch.setitem(core._house, "lang", "ru")
    esp.words_tick()
    assert len(cli.sent) == 2 and json.loads(cli.sent[1][1])["net_wifi"] != "No Wi-Fi"                # the language changed
