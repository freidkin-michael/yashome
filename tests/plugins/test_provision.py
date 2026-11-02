"""provision: house token only, portal and secured-AP guards, the shared setup-AP key, open/secured and
MAC conflicts, open "-setup" APs, online nodes, relay discovery and order, busy relays and attempt ids, the
confirmation, the host relay's token, credentials and errors, and no house Wi-Fi password or key on MQTT."""
import json
import sys
import time

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.provision"]
NODE_BACK = me._node_back
STATUS = me._node_status
HOUSE_PW = "house-pass-not-real"
AP_KEY = "ap-key-not-real-123"
OUI = "24:0A:C4"                     # an Espressif prefix; the rest built, not a real MAC
MAC = OUI + ":00:00:01"


class Req:
    def __init__(self, token=""):
        self.headers = {"x-token": token} if token else {}


HOUSE = Req(core.DASHBOARD_TOKEN)


class Cli:
    """Records publishes; a `do` is answered by `reply(node, payload)` like a relay node back home."""
    def __init__(self, reply=None):
        self.sent, self.reply = [], reply

    def is_connected(self):
        return True

    def publish(self, topic, payload=None, retain=False):
        self.sent.append((topic, payload))
        if self.reply and topic.endswith("/do"):
            self.reply(topic.split("/")[1], payload)


def result(node, payload, ok=True, **extra):
    """What a relay node publishes when back: the command's AP and attempt id echoed."""
    f = payload.split("|")
    me._on_message(["provision", node, "result"], json.dumps(
        {"node": node, "ap": f[0], "id": f[7], "ok": ok, "detail": "ok", **extra}).encode(), False)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for d in (me._relays, me._results, me._busy):
        d.clear()
    me._last_scan["ts"] = time.time()
    me._host_seen.update(aps=[], ts=0.0, error=None)
    monkeypatch.setenv("HOUSE_WIFI_SSID", "house-net")
    monkeypatch.setenv("HOUSE_WIFI_PASSWORD", HOUSE_PW)
    monkeypatch.setattr(me, "_host_there", lambda: False)
    monkeypatch.delenv("PANELS_AP_PASSWORD", raising=False)
    monkeypatch.setattr(me, "RELAY_WAIT_S", 0.3)
    for k in ("SCAN_POLL_S", "RELAY_POLL_S", "BACK_POLL_S"):    # waits look again often: no test sits idle
        monkeypatch.setattr(me, k, 0.01)
    monkeypatch.setattr(me, "_node_back", lambda node, since, **k: True)
    monkeypatch.setattr(me, "_node_status", lambda node, timeout=0: {})
    monkeypatch.setattr(core, "_mqtt_client", Cli())


def seen(node, aps, retain=False):
    me._on_message(["provision", "seen", node], json.dumps({"node": node, "aps": aps}).encode(), retain)


def ap(ssid="lamp-setup", rssi=-60, open_=True, bssid=MAC):
    return {"ssid": ssid, "bssid": bssid, "rssi": rssi, "ch": 6, "open": open_, "setup": ssid.endswith("-setup")}


def status(fn):
    with pytest.raises(HTTPException) as e:
        fn()
    return e.value.status_code


def test_routes_take_only_the_house_token():
    assert status(lambda: me.ap_scan(Req(), me.ApScan())) == 403
    assert status(lambda: me.ap_scan(Req("a-panel-key"), me.ApScan())) == 403
    assert status(lambda: me.ap_provision(me.ApProvision(ap="lamp-setup"), Req())) == 403


def test_a_third_concurrent_run_gets_429_at_once():
    assert me._slots.acquire(blocking=False) and me._slots.acquire(blocking=False)
    try:
        t0 = time.time()
        assert status(lambda: me.ap_scan(HOUSE, me.ApScan())) == 429
        assert status(lambda: me.ap_provision(me.ApProvision(ap="lamp-setup"), HOUSE)) == 429
        assert time.time() - t0 < 1.0                                   # refused, not queued
    finally:
        me._slots.release()
        me._slots.release()


def test_portal_outside_the_setup_net_is_refused():
    for host in ("192.0.2.10", "10.0.0.0", "192.168.4.1:0", "example.com", "192.168.4.1.example.com"):
        body = me.ApProvision(ap="lamp-setup", custom={"host": host})
        assert status(lambda: me.ap_provision(body, HOUSE)) == 400
    assert me._portal_spec("esphome", {"host": "192.168.4.1:8080"})["host"] == "192.168.4.1:8080"
    for bad in ({"path": "/a b"}, {"path": "/x\r\nHost: y"}, {"ssid_field": "a.b"}, {"psk_field": "p|q"}):
        assert status(lambda: me._portal_spec("esphome", bad)) == 400


def test_secured_ap_needs_its_password():
    seen("node-a", [ap("panel-setup", open_=False)])
    assert status(lambda: me.ap_provision(me.ApProvision(ap="panel-setup", via="node-a"), HOUSE)) == 400


def test_a_secured_ap_gets_the_house_setup_key(monkeypatch):
    monkeypatch.setenv("PANELS_AP_PASSWORD", AP_KEY)
    cli = Cli(result)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    seen("node-a", [ap("panel-setup", open_=False)])
    r = me.ap_provision(me.ApProvision(ap="panel-setup", via="node-a"), HOUSE)
    assert r["ok"]
    do = [p for t, p in cli.sent if t == "provision/node-a/do"][0].split("|")
    assert len(do) == 9 and do[6] == MAC and do[8] == ""                # the node uses its own key
    assert do[:6] == ["panel-setup", "192.168.4.1", "POST", "/wifisave", "ssid", "psk"]


def test_open_and_secured_under_one_name_is_refused():
    # one MAC and no house key: neither the MAC check nor the open "-setup" rule can refuse it instead
    seen("node-a", [ap("panel-setup", open_=False)])
    seen("node-b", [ap("panel-setup", open_=True)])
    for pw in ("", "typed-pass-123"):
        body = me.ApProvision(ap="panel-setup", via="node-a", ap_password=pw)
        assert status(lambda: me.ap_provision(body, HOUSE)) == 409       # never "open wins, drop the key"
    r = me._report(0.0, False, None)["aps"][0]
    assert r["conflict"] and not r["secured"]


def test_two_macs_or_a_key_for_an_open_ap_are_refused():
    seen("node-a", [ap(open_=False)])
    seen("node-b", [ap(open_=False, bssid=OUI + ":00:00:09")])
    assert status(lambda: me.ap_provision(me.ApProvision(ap="lamp-setup", ap_password="typed-pass-123"), HOUSE)) == 409
    me._relays.clear()
    seen("node-a", [ap(open_=True)])
    assert status(lambda: me.ap_provision(me.ApProvision(ap="lamp-setup", ap_password="typed-pass-123"), HOUSE)) == 409


def test_the_mac_is_shown_and_checked_against_espressif():
    seen("node-a", [ap(), ap("fake-setup", bssid="aa:bb:cc:dd:ee:ff"), ap("old-setup", bssid=None)])
    aps = {a["ssid"]: a for a in me._report(0.0, False, None)["aps"]}
    assert aps["lamp-setup"]["espressif"] == {MAC: True}
    assert aps["fake-setup"]["espressif"] == {"AA:BB:CC:DD:EE:FF": False}
    assert aps["old-setup"]["bssids"] == []


def test_neighbours_are_discovered_from_their_reports():
    seen("node-a", [ap(rssi=-70)])
    seen("node-b", [], retain=True)
    for bad in ("Bad/Name", "server", "auto"):
        me._on_message(["provision", "seen", bad], b"{}", False)
    assert set(me._relays) == {"node-a", "node-b"}
    assert me._relays["node-a"]["aps"][0]["ssid"] == "lamp-setup"
    assert me._relays["node-b"]["ts"] == 0.0
    me._on_message(["provision", "seen", "node-b"], b"", True)
    assert "node-b" not in me._relays
    assert status(lambda: me.ap_provision(me.ApProvision(ap="lamp-setup", via="node-x"), HOUSE)) == 400


def test_a_retained_report_is_not_fresh_scan_data():
    seen("node-a", [ap()], retain=True)
    assert me._relays["node-a"]["ts"] == 0.0 and me._observations(0.0) == {}
    r = me.ap_provision(me.ApProvision(ap="lamp-setup"), HOUSE)
    assert not r["ok"] and r["attempts"] == []                          # nobody "hears" it: no relay


def test_a_node_that_missed_three_scans_is_not_waited_for(monkeypatch):
    class Answers(Cli):
        def publish(self, topic, payload=None, retain=False):
            super().publish(topic, payload, retain)
            if topic == "provision/scan":
                seen("node-b", [])
    monkeypatch.setattr(core, "_mqtt_client", Answers())
    seen("node-a", [], retain=True)
    for n in range(3):
        t0 = time.time()
        r = me._scan(0.2)
        assert time.time() - t0 >= 0.15                                 # waited for node-a
    assert r["relays"][0]["gone"] and r["relays"][0]["missed"] == 3
    t0 = time.time()
    me._scan(3.0)
    assert time.time() - t0 < 1.0                                       # not any more


def test_auto_order_is_loudest_node_first_then_the_host(monkeypatch):
    monkeypatch.setattr(me, "_host_there", lambda: True)
    seen("node-a", [ap(rssi=-75)])
    seen("node-b", [ap(rssi=-45)])
    seen("node-c", [ap("other-setup", rssi=-30)])
    seen("node-d", [ap(rssi=-80)])
    assert me._order("lamp-setup", "auto", 0.0) == ["node-b", "node-a", "server"]    # at most two nodes
    tried = []
    monkeypatch.setattr(me, "_via_node", lambda n, *a: tried.append(n) or {"ok": False, "detail": "x"})
    monkeypatch.setattr(me, "_via_host", lambda *a: tried.append("server") or {"ok": True, "detail": "y"})
    r = me.ap_provision(me.ApProvision(ap="lamp-setup"), HOUSE)
    assert r["ok"] and r["via"] == "server" and tried == ["node-b", "node-a", "server"]


def test_a_busy_node_is_skipped_and_the_next_one_tried(monkeypatch):
    def reply(node, payload):
        result(node, payload, ok=False, busy=True) if node == "node-b" else result(node, payload)
    cli = Cli(reply)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    seen("node-a", [ap(rssi=-75)])
    seen("node-b", [ap(rssi=-45)])
    r = me.ap_provision(me.ApProvision(ap="lamp-setup"), HOUSE)
    assert r["ok"] and r["via"] == "node-a"
    assert r["attempts"][0]["via"] == "node-b" and r["attempts"][0]["busy"]


def test_a_late_result_of_an_older_attempt_is_ignored(monkeypatch):
    def late(node, payload):
        f = payload.split("|")
        old = "|".join(f[:7] + ["0ld1d000"] + f[8:])
        result(node, old)                                               # the previous attempt, ok
    monkeypatch.setattr(core, "_mqtt_client", Cli(late))
    seen("node-a", [ap()])
    r = me.ap_provision(me.ApProvision(ap="lamp-setup", via="node-a"), HOUSE)
    assert not r["ok"] and "did not report" in r["attempts"][0]["detail"]


def test_a_relay_ok_counts_only_once_the_node_shows_up(monkeypatch):
    monkeypatch.setattr(core, "_mqtt_client", Cli(result))
    monkeypatch.setattr(me, "_node_back", lambda node, since, **k: False)
    seen("node-a", [ap()])
    r = me.ap_provision(me.ApProvision(ap="lamp-setup", via="node-a"), HOUSE)
    assert not r["ok"] and r["pending"] and r["node"] == "lamp"
    lwt = {"lamp/status": "offline"}
    monkeypatch.setattr(core, "_retained_values", lambda topics, timeout=0: dict(lwt))
    monkeypatch.setattr(me, "_node_status", STATUS)
    assert not NODE_BACK("lamp", time.time(), timeout=0, before={"lamp/status": "offline"})
    lwt["lamp/status"] = "online"
    assert NODE_BACK("lamp", time.time(), timeout=0, before={"lamp/status": "offline"})
    assert not NODE_BACK("lamp", time.time(), timeout=0, before={"lamp/status": "online"})   # no change
    lwt.clear()
    t0 = time.time()
    seen("lamp", [])                                                    # a relay node itself: its live report
    assert NODE_BACK("lamp", t0, timeout=0)


def test_host_relay_gets_the_house_wifi(monkeypatch):
    monkeypatch.setattr(me, "_host_there", lambda: True)
    calls = []

    def http(url, body, timeout):
        calls.append((url, body))
        return {"aps": [{"ssid": "lamp-setup", "signal": 70, "open": True}]} if url.endswith("/scan") else {"ok": True}
    monkeypatch.setattr(me, "_http", http)
    r = me.ap_provision(me.ApProvision(ap="lamp-setup", via="server"), HOUSE)
    assert r["ok"] and r["via"] == "server"
    url, body = calls[-1]
    assert url == "/provision"
    assert (body["ap"], body["ssid"], body["password"]) == ("lamp-setup", "house-net", HOUSE_PW)
    assert body["portal"]["host"] == "192.168.4.1" and "ap_password" not in body


def test_ap_password_and_mac_reach_the_host(monkeypatch):
    monkeypatch.setattr(me, "_host_there", lambda: True)
    monkeypatch.setenv("PANELS_AP_PASSWORD", AP_KEY)
    monkeypatch.setattr(me, "FRESH_S", 0)
    monkeypatch.setattr(me, "SCAN_WAIT_S", 0.1)          # no node answers: the scan waits it out
    calls = []

    def http(url, body, timeout):
        calls.append((url, body))
        if url.endswith("/scan"):
            return {"aps": [{"ssid": "panel-setup", "bssid": MAC.lower(), "signal": 70, "open": False}]}
        return {"ok": True, "restore_failed": True}
    monkeypatch.setattr(me, "_http", http)
    r = me.ap_provision(me.ApProvision(ap="panel-setup", via="server"), HOUSE)
    body = calls[-1][1]
    assert calls[-1][0].endswith("/provision") and body["ap_password"] == AP_KEY and body["bssid"] == MAC
    assert r["ok"] and r["restore_failed"]


def test_the_host_helpers_own_error_is_passed_through(monkeypatch):
    monkeypatch.setattr(me, "_host_there", lambda: True)
    monkeypatch.setattr(me, "_http", lambda *a: {"ok": False, "error": "no Wi-Fi interface (set PROVISION_WIFI_IF)"})
    r = me._scan(0.3)
    assert not r["server"]["ok"] and r["server"]["error"].startswith("no Wi-Fi interface")
    row = {x["id"]: x for x in me._health()}["provision:host"]
    assert row["state"] == "bad" and "no Wi-Fi interface" in json.dumps(row)


def test_no_house_password_on_mqtt(monkeypatch):
    """Not on provision/scan, not in any do - also through a busy node, a retry and the setup-AP key."""
    monkeypatch.setenv("PANELS_AP_PASSWORD", AP_KEY)

    def reply(node, payload):
        result(node, payload, ok=False, busy=True) if node == "node-b" else result(node, payload)
    cli = Cli(reply)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    monkeypatch.setattr(me, "FRESH_S", 0)

    def scan(wait=7.0):
        start = time.time()
        cli.publish("provision/scan", "1")
        seen("node-a", [ap("panel-setup", rssi=-70, open_=False)])
        seen("node-b", [ap("panel-setup", rssi=-40, open_=False)])
        return {"scan_at": start}
    monkeypatch.setattr(me, "_scan", scan)
    r = me.ap_provision(me.ApProvision(ap="panel-setup"), HOUSE)
    assert r["ok"] and r["via"] == "node-a"
    dos = [p for t, p in cli.sent if t.endswith("/do")]
    assert len(dos) == 2 and all(p.endswith("|") and AP_KEY not in p for p in dos)
    assert ("provision/scan", "1") in cli.sent
    assert all(HOUSE_PW not in str(p) and "house-net" not in str(p) for _t, p in cli.sent)


def test_provision_counts_from_its_own_scan_not_a_later_one(monkeypatch):
    monkeypatch.setattr(core, "_mqtt_client", Cli(result))
    monkeypatch.setattr(me, "FRESH_S", 0)

    def scan(wait=7.0):
        start = time.time()
        seen("node-a", [ap()])
        me._last_scan["ts"] = time.time() + 5                           # a concurrent scan started later
        return {"scan_at": start}
    monkeypatch.setattr(me, "_scan", scan)
    r = me.ap_provision(me.ApProvision(ap="lamp-setup"), HOUSE)
    assert r["ok"] and r["via"] == "node-a"


def test_health_rows(monkeypatch):
    rows = {r["id"]: r for r in me._health()}
    assert rows["provision:host"]["detail"] == "not installed" and rows["provision:nodes"]["state"] == "warn"
    monkeypatch.setattr(me, "_host_there", lambda: True)
    monkeypatch.setattr(me, "_http", lambda *a: (_ for _ in ()).throw(OSError("refused")))
    seen("node-a", [])
    rows = {r["id"]: r for r in me._health()}
    assert rows["provision:host"]["state"] == "bad" and rows["provision:nodes"]["state"] == "ok"


def test_a_retained_result_is_ignored():
    me._on_message(["provision", "node-a", "result"], json.dumps({"ap": "lamp-setup", "id": "1", "ok": True}).encode(), True)
    assert me._results == {}
    me._on_message(["provision", "node-a", "result"], json.dumps({"ap": "lamp-setup", "id": "1", "ok": True}).encode(), False)
    assert me._results["node-a"]["ok"]


def test_a_result_for_another_ap_is_ignored(monkeypatch):
    def other(node, payload):
        result(node, "|".join(["other-setup"] + payload.split("|")[1:]))  # this attempt's id, another AP
    monkeypatch.setattr(core, "_mqtt_client", Cli(other))
    seen("node-a", [ap()])
    r = me.ap_provision(me.ApProvision(ap="lamp-setup", via="node-a"), HOUSE)
    assert not r["ok"] and "did not report" in r["attempts"][0]["detail"]


def test_a_relay_busy_here_is_skipped_without_a_command(monkeypatch):
    cli = Cli(result)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    seen("node-a", [ap(rssi=-75)])
    seen("node-b", [ap(rssi=-45)])
    me._busy.add("node-b")                                              # relaying for another dialog
    r = me.ap_provision(me.ApProvision(ap="lamp-setup"), HOUSE)
    assert r["ok"] and r["via"] == "node-a"
    assert r["attempts"][0] == {"via": "node-b", "ok": False, "busy": True, "detail": "busy with another AP"}
    assert [t for t, _p in cli.sent if t.endswith("/do")] == ["provision/node-a/do"]
    assert me._busy == {"node-b"}                                       # node-a released, node-b not touched


def test_an_open_setup_ap_is_refused_with_the_house_key(monkeypatch):
    monkeypatch.setenv("PANELS_AP_PASSWORD", AP_KEY)
    cli = Cli(result)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    seen("node-a", [ap("stranger-setup")])
    e = pytest.raises(HTTPException, me.ap_provision, me.ApProvision(ap="stranger-setup", via="node-a"), HOUSE)
    assert e.value.status_code == 409 and e.value.detail == me.OPEN_HOUSE
    assert me._report(0.0, False, None)["aps"][0]["house_open"]
    assert not [t for t, _p in cli.sent if t.endswith("/do")]


def test_an_open_ap_named_after_a_known_node_is_refused(monkeypatch):
    seen("node-a", [ap("node-c-setup"), ap("node-c")])
    seen("node-c", [], retain=True)                                     # the house knows node-c
    for name in ("node-c-setup", "node-c"):
        assert status(lambda: me.ap_provision(me.ApProvision(ap=name, via="node-a"), HOUSE)) == 409
    monkeypatch.setitem(core.DEVICES, "lamp2", {"id": "lamp2", "esphome_node": "lamp2"})
    seen("node-a", [ap("lamp2-setup")])
    assert status(lambda: me.ap_provision(me.ApProvision(ap="lamp2-setup", via="node-a"), HOUSE)) == 409


def test_an_open_ap_of_a_stranger_stays_possible_with_an_empty_key(monkeypatch):
    cli = Cli(result)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    seen("node-a", [ap("blank-device"), ap("lamp-setup")])
    for name in ("blank-device", "lamp-setup"):                         # no house key set: both allowed
        assert me.ap_provision(me.ApProvision(ap=name, via="node-a"), HOUSE)["ok"]
    assert all(p.split("|")[8] == "" for t, p in cli.sent if t.endswith("/do"))


def test_a_typed_key_goes_to_the_node_the_house_key_does_not(monkeypatch):
    monkeypatch.setenv("PANELS_AP_PASSWORD", AP_KEY)
    cli = Cli(result)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    seen("node-a", [ap("panel-setup", open_=False), ap("cam-ap", open_=False)])
    for name, pw in (("panel-setup", AP_KEY), ("panel-setup", ""), ("cam-ap", "typed-pass-123")):
        me.ap_provision(me.ApProvision(ap=name, via="node-a", ap_password=pw), HOUSE)
    keys = [p.split("|")[8] for t, p in cli.sent if t.endswith("/do")]
    assert keys == ["", "", "typed-pass-123"]
    # the house key fills in only for "*-setup"
    assert status(lambda: me.ap_provision(me.ApProvision(ap="cam-ap", via="node-a"), HOUSE)) == 400


def test_the_host_gets_the_house_key_and_the_open_permission(monkeypatch):
    monkeypatch.setattr(me, "_host_there", lambda: True)
    bodies = []
    monkeypatch.setattr(me, "_http", lambda url, body, t: bodies.append(body) or {"ok": True})
    seen("node-x", [ap("lamp-setup", open_=True)])
    assert me.ap_provision(me.ApProvision(ap="lamp-setup", via="server"), HOUSE)["ok"]
    assert bodies[-1]["allow_open_setup"] is True and "ap_password" not in bodies[-1]
    monkeypatch.setenv("PANELS_AP_PASSWORD", AP_KEY)
    me._relays.clear()
    seen("node-x", [ap("panel-setup", open_=False)])
    assert me.ap_provision(me.ApProvision(ap="panel-setup", via="server"), HOUSE)["ok"]
    assert bodies[-1]["ap_password"] == AP_KEY and "allow_open_setup" not in bodies[-1]


def test_an_online_node_is_refused_before_any_relay(monkeypatch):
    cli = Cli(result)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    monkeypatch.setattr(me, "_node_status", lambda node, timeout=0: {"lamp/status": "online"})
    seen("node-a", [ap()])
    e = pytest.raises(HTTPException, me.ap_provision, me.ApProvision(ap="lamp-setup", via="node-a"), HOUSE)
    assert e.value.status_code == 409 and "the node lamp is online" in e.value.detail
    assert not [t for t, _p in cli.sent if t.endswith("/do")]
    monkeypatch.setattr(me, "_node_status", lambda node, timeout=0: {})
    monkeypatch.setenv("PANELS_AP_PASSWORD", AP_KEY)                    # secured: the open rule stays out
    seen("node-a", [ap(open_=False)])
    seen("lamp", [])                                                    # its own live scan report
    e = pytest.raises(HTTPException, me.ap_provision, me.ApProvision(ap="lamp-setup", via="node-a"), HOUSE)
    assert e.value.status_code == 409 and "the node lamp is online" in e.value.detail


def test_force_goes_past_a_stale_online(monkeypatch):
    cli = Cli(result)
    monkeypatch.setattr(core, "_mqtt_client", cli)
    monkeypatch.setattr(me, "_node_back", lambda node, since, before=None, **k: before == {"lamp/status": "offline"})
    monkeypatch.setattr(me, "_node_status", lambda node, timeout=0: {"lamp/status": "online"})
    seen("node-a", [ap()])
    assert me.ap_provision(me.ApProvision(ap="lamp-setup", via="node-a", force=True), HOUSE)["ok"]
    assert ("lamp/status", "offline") in cli.sent and [t for t, _p in cli.sent if t.endswith("/do")]


def test_success_needs_the_status_to_change_after_the_start(monkeypatch):
    monkeypatch.setattr(core, "_mqtt_client", Cli(result))
    monkeypatch.setattr(me, "_node_back", NODE_BACK)
    monkeypatch.setattr(me, "CONFIRM_S", 0.0)
    states = iter([{"lamp/status": "offline"}, {"lamp/status": "offline"}])
    monkeypatch.setattr(me, "_node_status", lambda node, timeout=0: next(states, {"lamp/status": "offline"}))
    seen("node-a", [ap()])
    r = me.ap_provision(me.ApProvision(ap="lamp-setup", via="node-a"), HOUSE)
    assert not r["ok"] and r["pending"]
    states = iter([{"lamp/status": "offline"}, {"lamp/status": "online"}])
    r = me.ap_provision(me.ApProvision(ap="lamp-setup", via="node-a"), HOUSE)
    assert r["ok"] and r["node"] == "lamp"
