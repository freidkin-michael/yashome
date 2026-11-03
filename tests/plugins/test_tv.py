"""tv: the plug-in loads and ships its ui.js; tv_key presses; Add a TV (the LAN search, the probe of one address)."""
import json
import os
import sys

from fastapi.testclient import TestClient

import app as core


def test_loaded_with_ui():
    m = next(p for p in core.PLUGINS if p["id"] == "tv")
    assert m["ui"] == "/plugins/tv/ui.js" and "ru" in m["i18n"]


def test_tv_key_is_a_stateless_kind_that_goes_to_the_tv_s_own_transport(monkeypatch):
    me = next(m for n, m in __import__("sys").modules.items() if n.endswith(".tv") and hasattr(m, "key_controls"))
    assert [c["code"] for c in me.key_controls(["ok", "up", "nope"])] == ["key_up", "key_ok"]       # the order of a remote
    sent = []
    monkeypatch.setitem(core.TRANSPORTS, "fake_tv", lambda cfg, code, val, kind: sent.append((cfg["id"], code, val, kind)))
    ctl = me.key_controls(["back"])[0]
    assert core.CONTROL_KINDS["tv_key"]({"id": "t", "transport": "fake_tv"}, ctl, None) == {"key": "back"}
    assert sent == [("t", "key_back", True, "tv_key")]


# ---- Add a TV ----

tvmod = sys.modules["yashome_plugins.tv"]
probe_mod = tvmod._probe
CHECKS = list(probe_mod.CHECKS)                  # the real ones: each fake LAN swaps the SSAP check of these
HOUSE = lambda: {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


def _fake_lan(monkeypatch, ports=(), urls=None, ssap=()):
    """A LAN where only these ports are open, these URLs answer and these hosts speak SSAP (no packet leaves the test)."""
    urls = urls or {}

    def answer(host):
        if host not in ssap:
            raise OSError("no SSAP")
        return {"platform": "webos", "maker": "LG"}
    monkeypatch.setattr(probe_mod, "CHECKS", [answer if c is probe_mod._ssap else c for c in CHECKS])

    def get(url, limit=20000):
        if url not in urls:
            raise OSError("refused")
        return urls[url]
    monkeypatch.setattr(probe_mod, "_port", lambda host, port: port in ports)
    monkeypatch.setattr(probe_mod, "_get", get)


def test_the_probe_names_the_platform_from_what_answers(monkeypatch):
    _fake_lan(monkeypatch, urls={"http://192.0.2.5:8001/api/v2/": json.dumps(
        {"device": {"name": "[TV] Bedroom", "modelName": "UE43", "wifiMac": "00:11:22:33:44:55"}})})
    assert probe_mod.probe("192.0.2.5") == {"host": "192.0.2.5", "platform": "tizen", "maker": "Samsung", "name": "[TV] Bedroom",
                                            "model": "UE43", "mac": "00:11:22:33:44:55"}
    _fake_lan(monkeypatch, ports=(3000,))
    assert probe_mod.probe("192.0.2.6")["platform"] == ""                       # an open 3000 alone: a Gitea, not a TV
    _fake_lan(monkeypatch, ports=(3000,), ssap=("192.0.2.6",))                  # it answered a request before pairing
    assert probe_mod.probe("192.0.2.6")["platform"] == "webos"
    _fake_lan(monkeypatch, ports=(6466, 8008))
    assert probe_mod.probe("192.0.2.7")["platform"] == "androidtv"
    _fake_lan(monkeypatch, urls={"http://192.0.2.8:8060/query/device-info": "<device-info><vendor-name>TCL</vendor-name>"
                                 "<friendly-device-name>Salon</friendly-device-name></device-info>"})
    assert probe_mod.probe("192.0.2.8")["maker"] == "TCL"
    _fake_lan(monkeypatch)
    assert probe_mod.probe("192.0.2.9")["platform"] == ""                       # off, or not a TV
    assert probe_mod.probe("bad host;x")["platform"] == ""


def test_the_sweep_takes_the_house_s_24s_and_probes_only_what_has_a_tv_port(monkeypatch):
    import ipaddress
    assert probe_mod.nets(["192.0.2.206", "8.8.8.8", "127.0.0.1", None, "nope", "198.51.100.3", "192.0.2.9"]) == [
        ipaddress.ip_network("192.0.2.0/24"), ipaddress.ip_network("198.51.100.0/24")]          # private IPv4 only, each /24 once
    knocked = []

    def knock(pair):
        knocked.append(pair)
        return pair in {("192.0.2.5", 8001), ("192.0.2.6", 3000), ("192.0.2.6", 3001), ("192.0.2.7", 1883)}
    monkeypatch.setattr(probe_mod, "_knock", knock)
    monkeypatch.setattr(probe_mod, "probe", lambda h: {"host": h, "platform": {"192.0.2.5": "tizen", "192.0.2.6": "webos"}.get(h, "")})
    got = probe_mod.sweep(probe_mod.nets(["192.0.2.1"]))
    assert [r["host"] for r in got] == ["192.0.2.5", "192.0.2.6"]
    assert len(knocked) == 254 * len(probe_mod.SWEEP_PORTS) and ("192.0.2.7", 1883) not in knocked   # TV ports only


def test_search_and_probe_mark_known_and_supported_and_need_the_house_token(monkeypatch):
    monkeypatch.setattr(tvmod, "KNOWN_HOSTS", [lambda: {"192.0.2.6": "TV main"}, lambda: 1 / 0])          # a broken plug-in is skipped
    swept = []

    def sweep(networks):
        swept.append([str(n) for n in networks])
        return [{"host": "192.0.2.6", "platform": "webos", "maker": "LG", "name": "", "model": "", "mac": ""},
                {"host": "192.0.2.5", "platform": "tizen", "maker": "Samsung", "name": "Bedroom", "model": "", "mac": ""}]
    monkeypatch.setattr(probe_mod, "sweep", sweep)
    monkeypatch.setenv("BIND_ADDRESS", "192.0.2.206")
    c = TestClient(core.app)
    assert c.post("/api/tv/search").status_code in (401, 403)
    r = c.post("/api/tv/search", headers=HOUSE()).json()
    assert "192.0.2.0/24" in r["networks"] and swept[0][0] == "192.0.2.0/24"
    assert [(t["host"], t["known"], t["house_name"]) for t in r["tvs"]] == [("192.0.2.5", False, ""), ("192.0.2.6", True, "TV main")]   # new first
    assert r["tvs"][1]["supported"] is ("webos" in core.TRANSPORTS)
    monkeypatch.setattr(probe_mod, "probe", lambda h: {"host": h, "platform": "webos", "maker": "LG", "name": "", "model": "", "mac": ""})
    one = c.post("/api/tv/probe", headers=HOUSE(), json={"host": "192.0.2.6"}).json()
    assert one["platform"] == "webos" and one["known"]
    assert c.post("/api/tv/probe", headers=HOUSE(), json={"host": "a b"}).status_code == 400
