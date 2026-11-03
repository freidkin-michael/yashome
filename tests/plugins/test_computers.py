"""computers: a computer of the house is one the helper's check passes (key login, sudo); the list, the TV its screen is,
a search that offers the others and moves a listed one to its new address. The helper is a fake here."""
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.computers"]
HOUSE = {"Authorization": "Bearer test-token-not-real"}
SAM = {"connector": "HDMI-A-2", "vendor": "SAM", "name": "SAMSUNG", "port": 1}
LG = {"connector": "HDMI-A-1", "vendor": "GSM", "name": "LG TV", "port": 3}
TVS = {"tv_bed": {"id": "tv_bed", "name": "Bedroom TV", "category": "tv", "transport": "tizen",
                  "controls": [{"code": "input", "range": ["HDMI 1", "HDMI 2"]}]},
       "tv_main": {"id": "tv_main", "name": "Main TV", "category": "tv", "transport": "webos",
                   "controls": [{"code": "input", "range": ["PC", "HDMI 2", "HDMI 3"]}]}}
HOSTS = {"192.0.2.33": {"ok": True, "ssh": True, "sudo": True, "hostname": "bedroom", "os": "Ubuntu", "mac": "00:11:22:33:44:55", "screens": [SAM]},
         "192.0.2.61": {"ok": True, "ssh": True, "sudo": True, "hostname": "living-pc", "os": "Ubuntu", "screens": [LG]},
         "192.0.2.10": {"ok": True, "ssh": True, "sudo": True, "hostname": "dell", "os": "Ubuntu", "screens": []},
         "192.0.2.40": {"ok": False, "ssh": False, "sudo": False, "error": "the key is refused"},
         "192.0.2.41": {"ok": False, "ssh": True, "sudo": False, "hostname": "kid", "error": "sudo asks for a password"}}


@pytest.fixture
def house(monkeypatch, tmp_path):
    calls = []

    def helper(method, path, body=None, timeout=15):
        calls.append((path, body))
        if path == "/v1/hosts/check":
            return {"hosts": [{"host": h["host"], "user": h["user"], **HOSTS.get(h["host"], {"ok": False, "error": "not reachable"})}
                              for h in body["hosts"]]}
        if path == "/v1/hosts/scan":
            return {"net": body["net"], "hosts": [{"host": h, **r} for h, r in HOSTS.items()]}
        return {}
    monkeypatch.setattr(core, "_helper", helper)
    monkeypatch.setattr(me, "DATA", tmp_path / "computers.json")
    monkeypatch.setattr(me, "_computers", {})
    monkeypatch.setattr(me, "_status", {})
    monkeypatch.setattr(me, "_found", [])
    for k, v in TVS.items():
        monkeypatch.setitem(core.DEVICES, k, v)
    for k in [k for k, d in core.DEVICES.items() if d.get("category") == "tv" and k not in TVS]:
        monkeypatch.delitem(core.DEVICES, k)
    c = TestClient(core.app)
    c.calls = calls
    return c


def test_only_a_computer_the_house_logs_in_to_with_sudo_is_added(house):
    v = house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.33"}).json()
    c = v["computers"][0]
    assert (c["name"], c["hostname"], c["user"], c["tv"], c["input"]) == ("bedroom", "bedroom", "tester", "tv_bed", "HDMI 1")
    assert c["status"]["ok"] and house.calls[0] == ("/v1/hosts/check", {"hosts": [{"host": "192.0.2.33", "user": "tester", "wsl": ""}]})
    for host, why in (("192.0.2.40", "the key is refused"), ("192.0.2.41", "sudo asks for a password")):
        r = house.post("/api/computers", headers=HOUSE, json={"host": host})
        assert r.status_code == 409 and why in r.json()["detail"]
    assert house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.33"}).status_code == 409
    assert house.post("/api/computers", json={"host": "192.0.2.61"}).status_code in (401, 403)


def test_a_renamed_tv_input_is_not_guessed_and_is_picked_by_hand(house):
    TVS["tv_main"]["controls"][0]["range"] = ["PC", "HDMI 2", "HDMI 4"]           # its HDMI 3 renamed
    try:
        c = house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.61", "name": "Living room PC"}).json()["computers"][0]
    finally:
        TVS["tv_main"]["controls"][0]["range"] = ["PC", "HDMI 2", "HDMI 3"]
    assert (c["name"], c["tv"], c["input"]) == ("Living room PC", "tv_main", None)
    r = house.patch(f"/api/computers/{c['id']}", headers=HOUSE, json={"input": "PC"}).json()
    assert r["computers"][0]["input"] == "PC"
    assert house.patch(f"/api/computers/{c['id']}", headers=HOUSE, json={"input": "HDMI 9"}).status_code == 400
    assert house.patch(f"/api/computers/{c['id']}", headers=HOUSE, json={"tv": "nope"}).status_code == 400
    r = house.patch(f"/api/computers/{c['id']}", headers=HOUSE, json={"tv": "", "name": " Salon "}).json()["computers"][0]
    assert (r["tv"], r["input"], r["name"]) == (None, None, "Salon")


def test_the_search_offers_the_others_and_says_why_one_is_not_a_computer(house):
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.33"})
    v = house.post("/api/computers/scan", headers=HOUSE).json()
    assert house.calls[-1] == ("/v1/hosts/scan", {"net": "192.0.2.0/24", "user": "tester"})
    found = {f["host"]: f for f in v["found"]}
    assert set(found) == {"192.0.2.61", "192.0.2.10", "192.0.2.40", "192.0.2.41"}             # the listed one is not offered
    assert found["192.0.2.61"]["ok"] and found["192.0.2.61"]["guess"] == {"tv": "tv_main", "input": "HDMI 3", "port": 3}
    assert not found["192.0.2.41"]["ok"] and found["192.0.2.41"]["error"] == "sudo asks for a password"
    v = house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.10"}).json()
    dell = next(c for c in v["computers"] if c["host"] == "192.0.2.10")
    assert dell["self"] and v["computers"][0]["self"] and "192.0.2.10" not in {f["host"] for f in v["found"]}
    assert house.delete(f"/api/computers/{dell['id']}", headers=HOUSE).status_code == 409   # the house's own


def test_a_listed_computer_that_moved_is_found_at_its_new_address(house, monkeypatch):
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.33"})
    cid = next(iter(me._computers))
    me._computers[cid]["host"] = "192.0.2.99"                                               # its old lease
    me._status[cid]["ok"] = False
    v = house.post("/api/computers/scan", headers=HOUSE).json()
    c = next(c for c in v["computers"] if c["id"] == cid)
    assert c["host"] == "192.0.2.33" and c["status"]["ok"] and "192.0.2.33" not in {f["host"] for f in v["found"]}


def test_remove_forgets_its_host_key_and_the_checks_keep_status(house):
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.33"})
    cid = next(iter(me._computers))
    HOSTS["192.0.2.33"] = {**HOSTS["192.0.2.33"], "ok": False, "ssh": False, "error": "not reachable"}
    try:
        v = house.post("/api/computers/check", headers=HOUSE).json()
    finally:
        HOSTS["192.0.2.33"] = {**HOSTS["192.0.2.33"], "ok": True, "ssh": True, "error": None}
    st = v["computers"][0]["status"]
    assert st["ok"] is False and st["error"] == "not reachable" and st["screens"] == [SAM] and st["seen"]   # its last screens kept
    v = house.delete(f"/api/computers/{cid}", headers=HOUSE).json()
    assert not v["computers"] and house.calls[-1] == ("/v1/hosts/forget", {"host": "192.0.2.33"})
    assert house.delete(f"/api/computers/{cid}", headers=HOUSE).status_code == 404


def test_the_list_is_kept_in_plugin_data(house):
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.33"})
    assert me._load()[next(iter(me._computers))]["host"] == "192.0.2.33"
    assert me.DATA.read_text().startswith("{")


def test_without_a_user_nothing_is_searched(house, monkeypatch):
    monkeypatch.setattr(me, "USER", "")
    assert house.post("/api/computers/scan", headers=HOUSE).status_code == 503
    with pytest.raises(HTTPException):
        me._need_user()


class FakeBuild:
    def __init__(self):
        self.remote = ""

    def in_use(self):
        return self.remote

    def use_remote(self, host=""):
        self.remote = host


@pytest.fixture
def build(house, monkeypatch):
    """A build plug-in and a helper that takes runs and tunnels (it keeps the tunnels it was given)."""
    b = FakeBuild()
    monkeypatch.setitem(sys.modules, "yashome_plugins.build", b)
    orig = core._helper
    tunnels = {}

    def helper(method, path, body=None, timeout=15):
        if path == "/v1/hosts/run":
            house.calls.append((path, body))
            return {"run": {"host": body["host"], "role": body["role"], "action": body["action"], "state": "running", "log": []}}
        if path == "/v1/hosts/runs":
            return {"runs": {}}
        if path == "/v1/tunnels":
            if method == "POST":
                house.calls.append((path, body))
                if body.get("host"):
                    tunnels[body["role"]] = {"host": body["host"], "user": body["user"], "wsl": body["wsl"], "state": "up"}
                else:
                    tunnels.pop(body["role"], None)
            return {"tunnels": dict(tunnels)}
        return orig(method, path, body, timeout)
    monkeypatch.setattr(core, "_helper", helper)
    b.tunnels = tunnels
    return b


def test_the_build_server_is_installed_on_a_computer_with_no_code_or_key(house, build, monkeypatch):
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.61"})
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.10"})
    v = house.get("/api/computers", headers=HOUSE).json()
    i3 = next(c for c in v["computers"] if c["host"] == "192.0.2.61")
    me_ = next(c for c in v["computers"] if c["self"])
    assert i3["roles"]["build"] == {"installed": False, "active": False} and not me_["roles"]["build"]["installed"] and v["roles"]["build"]["on"]
    r = house.post(f"/api/computers/{i3['id']}/roles/build", headers=HOUSE, json={"action": "install"})
    assert r.status_code == 200
    assert house.calls[-1] == ("/v1/hosts/run", {"host": "192.0.2.61", "user": "tester", "wsl": "", "role": "build", "action": "install",
                                                 "args": {"house": "http://192.0.2.10:8765"}})
    assert build.remote == ""                                                     # installed is not in use: Use does that
    assert house.post(f"/api/computers/{i3['id']}/roles/build", headers=HOUSE, json={"action": "use"}).status_code == 409  # not there yet
    monkeypatch.setattr(me, "SELF", "")                                         # no BIND_ADDRESS: said, never "http://:8765"
    r = house.post(f"/api/computers/{i3['id']}/roles/build", headers=HOUSE, json={"action": "install"})
    assert r.status_code == 503 and "BIND_ADDRESS" in r.json()["detail"]


def test_use_points_the_tunnel_at_one_computer_and_the_one_in_use_is_not_removed(house, build):
    for h in ("192.0.2.61", "192.0.2.33", "192.0.2.10"):
        house.post("/api/computers", headers=HOUSE, json={"host": h})
    cid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.61")
    other = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.33")
    selfid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.10")
    me._status[cid]["roles"] = ["build"]
    me._status[other]["roles"] = ["build"]
    v = house.post(f"/api/computers/{cid}/roles/build", headers=HOUSE, json={"action": "use"}).json()
    assert build.remote == "192.0.2.61" and build.tunnels["build"]["host"] == "192.0.2.61"
    pc = {c["id"]: c for c in v["computers"]}
    assert pc[cid]["roles"]["build"] == {"installed": True, "active": True} and not pc[other]["roles"]["build"]["active"]
    assert not pc[selfid]["roles"]["build"]["active"] and v["tunnels"]["build"]["state"] == "up"
    v = house.post(f"/api/computers/{other}/roles/build", headers=HOUSE, json={"action": "use"}).json()   # one computer per server
    assert build.remote == "192.0.2.33" and build.tunnels["build"]["host"] == "192.0.2.33"
    assert [c["id"] for c in v["computers"] if c["roles"]["build"]["active"]] == [other]
    assert house.post(f"/api/computers/{other}/roles/build", headers=HOUSE, json={"action": "remove"}).status_code == 409
    assert house.post(f"/api/computers/{selfid}/roles/tts", headers=HOUSE, json={"action": "install"}).status_code == 409
    assert house.post(f"/api/computers/{selfid}/roles/build", headers=HOUSE, json={"action": "use"}).status_code == 409  # none there
    house.post(f"/api/computers/{cid}/roles/build", headers=HOUSE, json={"action": "use"})
    r = house.post(f"/api/computers/{other}/roles/build", headers=HOUSE, json={"action": "remove"})
    assert r.status_code == 200 and house.calls[-1][1]["action"] == "remove" and house.calls[-1][1]["args"] == {}
    assert house.post(f"/api/computers/{cid}/roles/nope", headers=HOUSE, json={"action": "install"}).status_code == 400


def test_a_build_server_in_use_follows_its_computer_to_a_new_address(house, build):
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.33"})
    cid = next(iter(me._computers))
    me._status[cid]["roles"] = ["build"]
    house.post(f"/api/computers/{cid}/roles/build", headers=HOUSE, json={"action": "use"})
    assert build.remote == "192.0.2.33"
    me._computers[cid]["host"] = "192.0.2.99"                    # the house knew it by its old lease
    build.remote = "192.0.2.99"
    me._status[cid]["ok"] = False
    house.post("/api/computers/scan", headers=HOUSE)
    assert me._computers[cid]["host"] == "192.0.2.33" and build.remote == "192.0.2.33"
    assert build.tunnels["build"]["host"] == "192.0.2.33"


def test_the_app_builder_goes_beside_a_build_server(house, build):
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.61"})
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.10"})
    cid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.61")
    assert house.post(f"/api/computers/{cid}/roles/apk", headers=HOUSE, json={"action": "install"}).status_code == 409
    me._status[cid]["roles"] = ["build"]
    r = house.post(f"/api/computers/{cid}/roles/apk", headers=HOUSE, json={"action": "install"})
    assert r.status_code == 200 and house.calls[-1][1]["role"] == "apk" and house.calls[-1][1]["args"] == {"house": "http://192.0.2.10:8765"}
    assert house.post(f"/api/computers/{cid}/roles/apk", headers=HOUSE, json={"action": "use"}).status_code == 400
    me._status[cid]["roles"] = ["build", "apk"]
    v = house.get("/api/computers", headers=HOUSE).json()
    assert next(c for c in v["computers"] if c["id"] == cid)["roles"]["apk"]["installed"]


def test_the_tunnels_follow_the_servers_in_use_after_a_helper_restart(house, build, monkeypatch):
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.61"})
    v = FakeVoice()
    monkeypatch.setitem(sys.modules, "yashome_plugins.voice", v)
    build.remote = "192.0.2.61"                                       # in use, but the helper lost its tunnels
    me._sync_tunnels()
    assert build.tunnels["build"]["host"] == "192.0.2.61" and "tts" not in build.tunnels
    n = len(house.calls)
    me._sync_tunnels()
    assert len(house.calls) == n                                      # nothing to change: nothing sent


def test_the_houses_own_computer_takes_a_build_server_like_any_other(house, build):
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.10"})
    selfid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.10")
    v = house.get("/api/computers", headers=HOUSE).json()
    assert next(c for c in v["computers"] if c["self"])["roles"]["build"] == {"installed": False, "active": False}   # no .env key decides
    r = house.post(f"/api/computers/{selfid}/roles/build", headers=HOUSE, json={"action": "install"})
    assert r.status_code == 200 and house.calls[-1] == ("/v1/hosts/run", {"host": "192.0.2.10", "user": "tester", "wsl": "", "role": "build",
                                                                          "action": "install", "args": {"house": "http://192.0.2.10:8765"}})
    me._status[selfid]["roles"] = ["build", "apk"]
    v = house.post(f"/api/computers/{selfid}/roles/build", headers=HOUSE, json={"action": "use"}).json()
    assert build.remote == "192.0.2.10" and build.tunnels["build"]["host"] == "192.0.2.10"
    assert next(c for c in v["computers"] if c["self"])["roles"]["build"] == {"installed": True, "active": True}
    assert house.post(f"/api/computers/{selfid}/roles/build", headers=HOUSE, json={"action": "remove"}).status_code == 409   # in use
    assert house.post(f"/api/computers/{selfid}/roles/tts", headers=HOUSE, json={"action": "remove"}).status_code == 409    # its own: shown only


class FakeVoice:
    def __init__(self):
        self.host, self.url = "", ""

    def tts_in_use(self):
        return self.host

    def use_tts(self, host="", url=""):
        self.host, self.url = host, url


def test_the_tts_service_goes_to_a_computer_with_no_key_and_the_house_hands_its_files_on(house, build, monkeypatch):
    v = FakeVoice()
    monkeypatch.setitem(sys.modules, "yashome_plugins.voice", v)
    monkeypatch.setenv("COMPOSE_PROFILES", "build,voice")
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.61"})
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.10"})
    cid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.61")
    selfid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.10")
    r = house.post(f"/api/computers/{cid}/roles/tts", headers=HOUSE, json={"action": "install"})
    assert r.status_code == 200 and "keys" not in me._computers[cid]
    assert house.calls[-1][1]["args"] == {"house": "http://192.0.2.10:8765"} and v.host == ""
    assert house.post(f"/api/computers/{cid}/roles/tts", headers=HOUSE, json={"action": "use"}).status_code == 409   # not there yet
    me._status[cid]["roles"] = ["tts"]
    view = house.post(f"/api/computers/{cid}/roles/tts", headers=HOUSE, json={"action": "use"}).json()
    assert (v.host, v.url) == ("192.0.2.61", "http://helper:8092")
    assert build.tunnels["tts"]["host"] == "192.0.2.61"
    assert next(c for c in view["computers"] if c["id"] == cid)["roles"]["tts"] == {"installed": True, "active": True}
    assert house.post(f"/api/computers/{cid}/roles/tts", headers=HOUSE, json={"action": "remove"}).status_code == 409
    house.post(f"/api/computers/{selfid}/roles/tts", headers=HOUSE, json={"action": "use"})
    assert v.host == "" and "tts" not in build.tunnels                                     # back to the house's own


def test_the_big_model_runs_on_a_computer_through_the_house_tunnel(house, build, monkeypatch, tmp_path):
    v = FakeVoice()
    v._cfg = {"config": {"big_model": "qwen3:14b"}}
    monkeypatch.setitem(sys.modules, "yashome_plugins.voice", v)
    monkeypatch.setattr(me, "LLM_TARGET", tmp_path / "llm.json")
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.61"})
    house.post("/api/computers", headers=HOUSE, json={"host": "192.0.2.10"})
    cid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.61")
    selfid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.10")
    assert house.post(f"/api/computers/{cid}/roles/llm", headers=HOUSE, json={"action": "use"}).status_code == 409   # no Ollama there
    r = house.post(f"/api/computers/{cid}/roles/llm", headers=HOUSE, json={"action": "install"})
    assert r.status_code == 200 and house.calls[-1][1]["role"] == "llm" and house.calls[-1][1]["args"] == {"model": "qwen3:14b"}
    me._status[cid].update(roles=["llm"], llm={"serve": "/home/someone/ollama/serve.sh", "models": ["qwen3:14b"]})
    me._computers[cid]["wsl"] = "Ubuntu/u"
    view = house.post(f"/api/computers/{cid}/roles/llm", headers=HOUSE, json={"action": "use"}).json()
    assert json.loads(me.LLM_TARGET.read_text()) == {"host": "192.0.2.61", "user": "tester", "wsl": "Ubuntu/u", "serve": "/home/someone/ollama/serve.sh"}
    pc = next(c for c in view["computers"] if c["id"] == cid)["roles"]["llm"]
    assert pc == {"installed": True, "active": True, "models": ["qwen3:14b"]}
    assert house.post(f"/api/computers/{cid}/roles/llm", headers=HOUSE, json={"action": "remove"}).status_code == 409
    me._status[selfid]["roles"] = ["llm"]
    house.post(f"/api/computers/{selfid}/roles/llm", headers=HOUSE, json={"action": "use"})
    assert me.LLM_TARGET.read_text() == ""                                               # no big model: the tunnel waits
    assert house.post(f"/api/computers/{selfid}/roles/llm", headers=HOUSE, json={"action": "install"}).status_code == 409


def test_yashgen_moves_to_a_computer_and_back_through_the_host_piece(house, build, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "yashome_plugins.fwgen", type(sys)("fwgen"))
    monkeypatch.setattr(me, "YASHGEN_TARGET", tmp_path / "yashgen.json")
    monkeypatch.setenv("YASHGEN_SRC", "/home/someone/fwgen")
    for h in ("192.0.2.61", "192.0.2.10", "192.0.2.33"):
        house.post("/api/computers", headers=HOUSE, json={"host": h})
    cid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.61")
    selfid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.10")
    wslid = next(k for k, c in me._computers.items() if c["host"] == "192.0.2.33")
    me._computers[wslid]["wsl"] = "Ubuntu/u"
    assert house.post(f"/api/computers/{wslid}/roles/yashgen", headers=HOUSE, json={"action": "install"}).status_code == 409
    assert house.post(f"/api/computers/{cid}/roles/yashgen", headers=HOUSE, json={"action": "use"}).status_code == 409   # not there
    r = house.post(f"/api/computers/{cid}/roles/yashgen", headers=HOUSE, json={"action": "install"})
    assert r.status_code == 200 and house.calls[-1][1]["role"] == "yashgen" and house.calls[-1][1]["args"] == {}
    view = house.get("/api/computers", headers=HOUSE).json()
    assert next(c for c in view["computers"] if c["id"] == selfid)["roles"]["yashgen"] == {"installed": True, "active": True, "local": True}
    me._status[cid]["roles"] = ["yashgen"]
    view = house.post(f"/api/computers/{cid}/roles/yashgen", headers=HOUSE, json={"action": "use"}).json()
    assert json.loads(me.YASHGEN_TARGET.read_text()) == {"host": "192.0.2.61", "user": "tester"}
    assert next(c for c in view["computers"] if c["id"] == cid)["roles"]["yashgen"] == {"installed": True, "active": True}
    assert house.post(f"/api/computers/{cid}/roles/yashgen", headers=HOUSE, json={"action": "remove"}).status_code == 409
    house.post(f"/api/computers/{selfid}/roles/yashgen", headers=HOUSE, json={"action": "use"})
    assert me.YASHGEN_TARGET.read_text() == ""                                         # this host's own builder again
    assert house.post(f"/api/computers/{cid}/roles/yashgen", headers=HOUSE, json={"action": "remove"}).status_code == 200


HOST = Path(__file__).resolve().parents[2] / "plugins" / "tools" / "computers" / "host"


def _fake_bin(d):
    """ssh, rsync and systemctl that log their arguments (and ssh its stdin) to calls.log."""
    d.mkdir()
    for name in ("ssh", "rsync", "systemctl"):
        (d / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "$CALLS"\n'
                              + ('case "$*" in *start.sh*) cat >> "$CALLS.in";; esac\n' if name == "ssh" else ""))
        (d / name).chmod(0o755)


def test_the_host_piece_moves_yashgen_there_and_tunnels_it_to_the_front_doors_address(tmp_path):
    import os
    import subprocess
    house, src, bin_ = tmp_path / "house", tmp_path / "fwgen", tmp_path / "bin"
    (house / "plugin-data" / "computers").mkdir(parents=True)
    (house / "esphome").mkdir()
    src.mkdir()
    (src / "fwgen.py").write_text('ESPHOME_PIN = "2026.9.1"\n')
    (src / ".builder-token").write_text("tok123\n")
    (src / "projects").mkdir()
    (tmp_path / "house-secrets.yaml").write_text("wifi_password: not-real\n")
    _fake_bin(bin_)
    calls = tmp_path / "calls.log"
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "CALLS": str(calls), "HOME": str(tmp_path), "HOUSE_DIR": str(house),
           "YASHOME_HOSTS_KNOWN": str(tmp_path / "known"), "YASHGEN_SRC": str(src), "YASHGEN_LISTEN": "192.0.2.1:8090",
           "YASHGEN_LOCAL_UNIT": "fwgen-builder.service", "YASHGEN_HOUSE_SECRETS": str(tmp_path / "house-secrets.yaml")}
    (tmp_path / ".config" / "yashome").mkdir(parents=True)
    (house / "plugin-data" / "computers" / "yashgen.json").write_text('{"host": "192.0.2.61", "user": "tester"}')
    r = subprocess.run(["sh", str(HOST / "yashgen-tunnel.sh")], env=env, capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    log = calls.read_text().splitlines()
    assert log[0] == "systemctl --user stop fwgen-builder.service"                 # this host's own builder makes way
    code = next(x for x in log if x.startswith("rsync") and "/fwgen/" in x.split()[-1])
    assert "--exclude=/tests/" in code and "--exclude=/secrets.yaml" in code and "--exclude=/.*-token" in code
    assert any(x.startswith("rsync") and "/projects " in x + " " for x in log)    # its data goes along
    nodes = next(x for x in log if x.endswith("/house/esphome/"))
    assert "--exclude=secrets.yaml" in nodes and "--exclude=.esphome/" in nodes
    assert any("sudo -n sh /opt/yashome-yashgen/start.sh 2026.9.1" in x for x in log)
    assert log[-1].startswith("ssh ") and "-N" in log[-1] and "-L 192.0.2.1:8090:127.0.0.1:8090" in log[-1] and "StrictHostKeyChecking=yes" in log[-1]
    assert (tmp_path / "calls.log.in").read_text() == "tok123\n\nwifi_password: not-real\n"   # token and secrets: stdin only
    assert not any("tok123" in x or "not-real" in x for x in log)
    assert (tmp_path / ".config" / "yashome" / "yashgen.current").read_text() == "tester 192.0.2.61\n"
    calls.unlink()
    r = subprocess.run(["sh", str(HOST / "yashgen-stop.sh")], env=env, capture_output=True, text=True, timeout=20)
    log = calls.read_text().splitlines()
    assert any(x.startswith("rsync") and x.endswith(f"192.0.2.61:/opt/yashome-yashgen/data/projects {src}/") for x in log)
    assert log[-1].endswith("192.0.2.61 sudo -n sh /opt/yashome-yashgen/stop.sh")
    assert not (tmp_path / ".config" / "yashome" / "yashgen.current").exists()


def test_yashgen_data_that_did_not_come_back_is_asked_again_and_never_dropped(tmp_path):
    import os
    import subprocess
    bin_, src, conf = tmp_path / "bin", tmp_path / "fwgen", tmp_path / ".config" / "yashome"
    _fake_bin(bin_)
    (bin_ / "rsync").write_text('#!/bin/sh\necho "rsync $*" >> "$CALLS"\n[ -z "$RSYNC_FAIL" ]\n')
    src.mkdir()
    conf.mkdir(parents=True)
    (conf / "yashgen.current").write_text("tester 192.0.2.61\n")
    calls = tmp_path / "calls.log"
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "CALLS": str(calls), "HOME": str(tmp_path),
           "YASHOME_HOSTS_KNOWN": str(tmp_path / "known"), "YASHGEN_SRC": str(src), "RSYNC_FAIL": "1"}
    stop = ["sh", str(HOST / "yashgen-stop.sh")]
    r = subprocess.run(stop, env=env, capture_output=True, text=True, timeout=20)
    assert r.returncode == 1 and "did not come back" in r.stderr
    assert (conf / "yashgen.pending").read_text() == "tester 192.0.2.61\n" and not (conf / "yashgen.current").exists()
    assert not any(".pulled-back" in x for x in calls.read_text().splitlines())     # the remote copy stays the only one
    calls.unlink()
    env["RSYNC_FAIL"] = ""
    r = subprocess.run(stop + ["pending"], env=env, capture_output=True, text=True, timeout=20)
    log = calls.read_text().splitlines()
    assert r.returncode == 0 and not (conf / "yashgen.pending").exists()
    assert any(x.endswith("192.0.2.61 sudo -n touch /opt/yashome-yashgen/data/.pulled-back") for x in log)
    assert not any("stop.sh" in x for x in log)                                     # only the pull, the builder there untouched


def test_yashgen_remove_refuses_while_the_house_lacks_the_data():
    body = (HOST.parents[3] / "helper" / "roles" / "yashgen-remove.sh").read_text()
    assert body.index(".pulled-back") < body.index("docker compose down") and "exit 1" in body


def test_discovered_reads_the_companions_file_and_ignores_a_stale_one(tmp_path, monkeypatch):
    import time
    me = sys.modules["yashome_plugins.computers"]
    f = tmp_path / "discovered.json"
    monkeypatch.setattr(me, "DISCOVERED", f)
    assert me.discovered() == []
    host = {"host": "192.0.2.61", "name": "living-pc", "servers": ["touchmouse"], "ports": {}, "seen": time.time()}
    f.write_text(json.dumps({"hosts": [host, {"host": "192.0.2.9"}, "junk"], "updated": time.time()}))
    assert me.discovered() == [host]
    f.write_text(json.dumps({"hosts": [host], "updated": time.time() - 3600}))
    assert me.discovered() == []


def _load_file(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_discover_companion_takes_only_well_formed_answers():
    d = _load_file("computers_discoverd", Path(__file__).resolve().parents[2] / "plugins/tools/computers/discover/discoverd.py")
    ok = d.parse(json.dumps({"yashome": 1, "name": "bedroom", "servers": ["player", "touchmouse", "Bad One", 5],
                             "ports": {"touchmouse": 7709, "x": 70000}}).encode())
    assert ok == {"name": "bedroom", "servers": ["player", "touchmouse"], "ports": {"touchmouse": 7709}}
    for bad in (b"junk", b"[]", json.dumps({"yashome": 2, "name": "a", "servers": []}).encode(),
                json.dumps({"yashome": 1, "name": "a b", "servers": []}).encode()):
        assert d.parse(bad) is None


def test_the_hello_responder_answers_the_broadcast_with_its_servers(tmp_path, monkeypatch):
    """The responder that hello-install.sh writes on a computer: "? yashome 1" -> its name and servers; anything else
    gets no answer."""
    import socket
    import threading
    text = (Path(__file__).resolve().parents[2] / "helper/roles/hello-install.sh").read_text()
    src = text.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    (tmp_path / "hello.py").write_text(src)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("YASHOME_HELLO_PORT", str(port))
    h = _load_file("yashome_hello", tmp_path / "hello.py")
    monkeypatch.setattr(h, "servers", lambda: ["player", "touchmouse"])
    threading.Thread(target=h.main, daemon=True).start()
    import time
    time.sleep(0.3)                       # the responder binds its port
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(2)
        s.sendto(b"hello?", ("127.0.0.1", port))
        s.sendto(b"? yashome 1", ("127.0.0.1", port))
        got = json.loads(s.recv(2048))
        assert got["yashome"] == 1 and got["servers"] == ["player", "touchmouse"] and got["ports"] == {"touchmouse": 7709}
        s.settimeout(0.3)
        with pytest.raises(socket.timeout):
            s.recv(2048)


def _llm_health(monkeypatch, tmp_path):
    import importlib.util
    monkeypatch.setenv("HOUSE_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("llm_health_t", HOST / "llm-health.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_llm_health_restarts_only_the_model_s_own_wsl_and_reads_n_a(monkeypatch, tmp_path):
    import types
    m = _llm_health(monkeypatch, tmp_path)
    ran = []
    t = {"host": "192.0.2.7", "user": "u", "wsl": "Ubuntu-Train/yash"}
    monkeypatch.setattr(m, "_target", lambda: t)
    monkeypatch.setattr(m, "_unload", lambda loaded: None)
    api = {"/api/version": {"version": "0.9"}, "/api/ps": {"models": [{"name": "q", "size_vram": 0}]}}
    monkeypatch.setattr(m, "_get", lambda path, body=None, timeout=3: api[path])
    out = {"nvidia": "[N/A], 500, 8000"}
    def ssh(t_, cmd, timeout=10):
        ran.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout=out["nvidia"] if cmd.startswith("nvidia-smi") else "")
    monkeypatch.setattr(m, "_ssh", ssh)
    assert m._gpu(t) == (0, 500, 8000)
    r = m.check()
    assert r["reason"] == "cpu_fallback, WSL restarted" and "wsl --terminate Ubuntu-Train" in ran and not any("--shutdown" in c for c in ran)
    ran.clear()
    m._heal["at"] = 0.0
    out["nvidia"] = "90, 6000, 8000"                    # the owner works on the card: no restart under her
    assert m.check()["reason"] == "cpu_fallback" and not any(c.startswith("wsl") for c in ran)
