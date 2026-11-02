"""provision host pieces: the helper's AP pick (MAC pin, open "-setup" refusal), the AP key going to the
gate on stdin and never in argv, and the root gate's passwd-file and its check of `con up id NAME`."""
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import stat
import sys

import pytest

HOST = pathlib.Path(sys.modules["yashome_plugins.provision"].__file__).parent / "host"
OUI = "24:0A:C4"                     # an Espressif prefix; the rest built, not a real MAC
MAC1, MAC2 = OUI + ":00:00:01", OUI + ":00:00:02"
KEY = "ap-key-not-real-123"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


helper = load("esp_provision_helper", HOST / "esp-provision.py")
gate = load("esp_provision_gate", HOST / "esp-provision-nmcli")


def ap(ssid="lamp-setup", bssid=MAC1, open_=False):
    return {"ssid": ssid, "bssid": bssid, "signal": 70, "open": open_, "setup": ssid.endswith("-setup")}


def test_pick_follows_the_pinned_mac():
    aps = [ap(bssid=MAC1), ap(bssid=MAC2)]
    assert helper.pick(aps, "lamp-setup", MAC2, KEY)["bssid"] == MAC2
    assert helper.pick(aps, "lamp-setup", "", KEY)[0] == 409
    assert helper.pick([ap(bssid=MAC1)], "lamp-setup", MAC2, KEY)[0] == 400


def test_pick_refuses_an_open_setup_ap_unless_the_core_allowed_it():
    aps = [ap(open_=True)]
    assert helper.pick(aps, "lamp-setup", "", "") == (409, helper.OPEN_HOUSE)
    assert helper.pick(aps, "lamp-setup", "", "", allow_open_setup=True)["ssid"] == "lamp-setup"
    assert helper.pick([ap("blank-device", open_=True)], "blank-device", "", "")["ssid"] == "blank-device"
    assert helper.pick([ap(open_=False)], "lamp-setup", "", "")[0] == 400


def test_the_key_goes_to_the_gate_on_stdin(monkeypatch):
    calls = []

    def run(*args, timeout=45, stdin=None):
        calls.append((args, stdin))
        return 0, "", ""
    monkeypatch.setattr(helper, "run", run)
    monkeypatch.setattr(helper, "active_connection", lambda dev: None)
    monkeypatch.setattr(helper, "portal_save", lambda *a: True)
    monkeypatch.setattr(helper.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"x"))
    out = helper.provision("wlan9", "lamp-setup", MAC1, "house-net", "house-pass-not-real",
                           dict(helper.DEFAULT_PORTAL), KEY)
    assert out["ok"]
    connect = [c for c in calls if "connect" in c[0]][0]
    assert "secured" in connect[0] and connect[1] == KEY + "\n"
    assert all(KEY not in " ".join(args) for args, _in in calls)
    assert all(KEY not in s for s in out["steps"])


def gate_nm(monkeypatch, outputs, seen):
    def nm(*args, check_out=False):
        seen.append(args)
        if "passwd-file" in args:
            path = args[args.index("passwd-file") + 1]
            seen.append(("file", stat.S_IMODE(os.stat(path).st_mode), pathlib.Path(path).read_text(), path))
        return outputs.get(args, "") if check_out else 0
    monkeypatch.setattr(gate, "nm", nm)


def test_gate_hands_the_key_in_a_0600_passwd_file(monkeypatch):
    seen = []
    gate_nm(monkeypatch, {}, seen)
    monkeypatch.setattr(gate, "wifi_if", lambda dev: dev)
    monkeypatch.setattr(gate, "saved_names", lambda: set())
    rc = gate.connect(["lamp-setup", "bssid", MAC1, "secured", "ifname", "wlan9", "name", gate.CON],
                      stdin=io.StringIO(KEY + "\n"))
    assert rc == 0
    add = [a for a in seen if a[:2] == ("con", "add")][0]
    assert "autoconnect" in add and add[add.index("autoconnect") + 1] == "no"
    assert add[add.index("ipv4.never-default") + 1] == "yes"            # the host's default route never goes through a setup AP
    assert all(KEY not in " ".join(a) for a in seen if a[0] != "file")
    f = [a for a in seen if a[0] == "file"][0]
    assert f[1] == 0o600 and f[2] == f"802-11-wireless-security.psk:{KEY}\n"
    assert not os.path.exists(f[3])
    with pytest.raises(SystemExit):
        gate.connect(["lamp-setup", "secured", "ifname", "wlan9", "name", gate.CON], stdin=io.StringIO("short\n"))
    with pytest.raises(SystemExit):
        gate.connect(["lamp-setup", "password", KEY, "ifname", "wlan9", "name", gate.CON])
    for bad in ("a\\" + KEY, KEY + " "):                     # the passwd-file would change it
        with pytest.raises(SystemExit):
            gate.connect(["lamp-setup", "secured", "ifname", "wlan9", "name", gate.CON], stdin=io.StringIO(bad + "\n"))


def show(name, typ, iface):
    args = ("-t", "-f", "connection.type,connection.interface-name", "con", "show", "id", name)
    return args, f"connection.type:{typ}\nconnection.interface-name:{iface}\n"


def test_gate_restores_only_a_wifi_profile_of_the_pinned_card(monkeypatch):
    outputs = dict([show("home", "802-11-wireless", "wlan9"), show("any", "802-11-wireless", ""),
                    show("other-card", "802-11-wireless", "wlan0"), show("wired", "802-3-ethernet", "eth9"),
                    show("vpn", "vpn", "")])
    gate_nm(monkeypatch, outputs, [])
    monkeypatch.setattr(gate, "pinned_if", lambda: "wlan9")
    assert gate.restorable("home") and gate.restorable("any")
    for bad in ("other-card", "wired", "vpn", "unknown", "-x", ""):
        assert not gate.restorable(bad)
    with pytest.raises(SystemExit):
        gate.main(["con", "up", "id", "wired"])
    assert gate.main(["con", "up", "id", "home"]) == 0
    monkeypatch.setattr(gate, "pinned_if", lambda: "")
    monkeypatch.setattr(gate, "wifi_devs", lambda: ["wlan0", "wlan9"])
    assert gate.restorable("other-card") and not gate.restorable("wired")


def test_a_pair_form_is_checked_and_posted_urlencoded_with_the_answer_handed_back(monkeypatch):
    good = {"code": "ABCD2345", "house_id": "h1", "ssid": "net", "wifi_password": "pw-not-real", "mqtt_host": "192.0.2.5", "mqtt_port": "1883",
            "mqtt_user": "dev-x", "mqtt_password": "m", "ota_password": "o"}
    assert helper.form_ok(good)
    for bad in ({}, {"a b": "x"}, {"a": 1}, {"a": "x" * 129}, {f"f{i}": "v" for i in range(17)}, {"a": "x" * 128, "b": "x" * 128, **{f"k{i}": "y" * 120 for i in range(15)}}, "x"):
        assert not helper.form_ok(bad)
    seen = {}

    class Reply:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "node": "air", "hash": "3f9a1c07be52"}'

    def fake_urlopen(req, timeout=0):
        seen.update(url=req.full_url, body=req.data, ctype=req.get_header("Content-type"), method=req.get_method())
        return Reply()
    monkeypatch.setattr(helper.urllib.request, "urlopen", fake_urlopen)
    steps = []
    out = helper.portal_pair(good, helper.DEFAULT_PORTAL, steps)
    assert out == {"ok": True, "node": "air", "hash": "3f9a1c07be52"}
    assert seen["url"] == "http://192.168.4.1/yash/pair" and seen["method"] == "POST" and seen["ctype"] == "application/x-www-form-urlencoded"
    assert b"code=ABCD2345" in seen["body"] and b"ota_password=o" in seen["body"]
    assert not any("pw-not-real" in x or "ABCD2345" in x for x in steps)               # no password or code in what is logged


def test_a_portal_that_answers_with_a_404_is_up_and_one_that_never_answers_is_not(monkeypatch):
    import urllib.error
    calls = []

    def not_found(url, timeout=0):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    monkeypatch.setattr(helper.urllib.request, "urlopen", not_found)
    assert helper.portal_answers("192.168.4.1", attempts=3, pause=0) == 1 and len(calls) == 1

    def down(url, timeout=0):
        raise OSError("no route")
    monkeypatch.setattr(helper.urllib.request, "urlopen", down)
    assert helper.portal_answers("192.168.4.1", attempts=3, pause=0) == 0


def test_the_relay_answers_on_its_socket_file_with_no_token(monkeypatch, tmp_path):
    """The house's own client (tools/hostsock.py) against the relay's server on a socket file: no token, no port."""
    import socket as so
    import threading as th
    hs = load("yashome_hostsock_t", pathlib.Path(__file__).resolve().parents[2] / "tools" / "hostsock.py")
    monkeypatch.setattr(hs, "DIR", str(tmp_path))
    monkeypatch.setattr(helper, "wifi_if", lambda: "wlan9")
    monkeypatch.setattr(helper, "nm_read", lambda *a, **k: (0, "wlan9:disconnected", ""))
    s = so.socket(so.AF_UNIX, so.SOCK_STREAM)
    s.bind(hs.path("provision"))
    s.listen(4)
    srv = helper.Server(s)
    th.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert hs.http("provision", "GET", "/health", timeout=5) == (200, {"ok": True, "wifi": "wlan9", "state": "disconnected"})
        assert hs.http("provision", "GET", "/nosuch", timeout=5)[0] == 404
        assert hs.http("provision", "POST", "/provision", {"ap": "x"}, timeout=5)[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()
