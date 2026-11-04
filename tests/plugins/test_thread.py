"""thread: the house's Thread network over its border routers (role "Thread border router" of plug-in computers). Fake OTBR
REST servers stand behind the tunnel slots, a fake helper answers ctl.sh; the role's install script is rendered with stub
commands. The dataset is the house's: it must never show in an answer, a health row or the log."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.thread"]
REPO = Path(__file__).resolve().parents[2]
HOUSE = {"Authorization": "Bearer test-token-not-real"}
# made-up datasets: TLV 0 channel 15, 2 extended PAN id, 3 name, 5 network key
NET_A = "000300000f" + "0208" + "1111111111111111" + "0307" + b"Yashome".hex() + "0510" + "aa" * 16
NET_B = "000300000f" + "0208" + "2222222222222222" + "0307" + b"Yashome".hex() + "0510" + "bb" * 16
KEY_A = "aa" * 16
# made-up extended addresses: the two border routers and two ESP32-C6 routers (bench, kitchen)
EXT_DELL, EXT_BED, EXT_BENCH, EXT_KITCHEN = "0011223344550001", "0011223344550002", "0011223344550003", "0011223344550004"


class Otbr:
    """One fake border router: the REST paths the plug-in uses in the shapes the live OTBR answers, every request recorded.
    Its device collection holds only itself (as at its start) until an updateDeviceCollectionTask copies `network` in;
    a list of tasks with a network diagnostic of the router itself fails as a whole, as on the live one."""

    def __init__(self, ext, dataset="", state="disabled"):
        self.ext, self.dataset, self.state, self.up, self.rcp = ext, dataset, state, True, True
        self.calls, self.actions, self.diags = [], {}, []
        self.devices = [{"id": ext, "type": "threadBorderRouter", "attributes": {
            "extAddress": ext, "mlEidIid": "0000000000000000", "omrIpv6Address": "::", "hostname": "", "role": "", "state": "disabled",
            "rloc16": "0x0000", "extPanId": "1111111111111111", "baState": "enabled"}}]
        self.network, self.diag_of = None, {}        # what a device collection finds; {ext: networkDiagnostics attributes}
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None, ctype="application/json"):
                data = b"" if body is None else (body if isinstance(body, str) else json.dumps(body)).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _do(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode() if n else ""
                fake.calls.append((method, self.path, self.headers.get("Content-Type"), body))
                if not fake.up:
                    return self._send(502)
                p = self.path
                if (method, p) == ("GET", "/node"):
                    return self._send(200, {"baId": "00112233445566770011223344556677", "baState": "", "state": fake.state, "routerCount": 4,
                                            "rlocAddress": "fd00:db8::ff:fe00:e000", "extAddress": fake.ext, "networkName": "Yashome" if fake.dataset else "",
                                            "rloc16": "0xe000", "routerId": 56, "leaderData": {"partitionId": 1, "weighting": 255, "dataVersion": 1,
                                                                                              "stableDataVersion": 1, "leaderRouterId": 56},
                                            "extPanId": "1111111111111111"})
                if (method, p) == ("GET", "/node/coprocessor/version"):
                    return self._send(200, json.dumps("OPENTHREAD/RCP 2.5.3")) if fake.rcp else self._send(500)
                if p == "/node/dataset/active":
                    if method == "GET":
                        return self._send(200, fake.dataset, "text/plain") if fake.dataset else self._send(204)
                    if fake.dataset and fake.state not in ("disabled", "detached"):
                        return self._send(409)
                    fake.dataset = NET_A if body.startswith("{") else body.strip()
                    return self._send(201)
                if (method, p) == ("PUT", "/node/state"):
                    fake.state = "leader" if json.loads(body) == "enable" else "disabled"
                    return self._send(200)
                if p == "/api/actions":
                    if method == "DELETE":
                        fake.actions.clear()
                        return self._send(204)
                    out, batch = [], json.loads(body)["data"]
                    broken = any(t["type"] == "getNetworkDiagnosticTask" and t["attributes"]["destination"] == fake.ext for t in batch)
                    for t in batch:
                        tid = f"t{len(fake.actions) + 1}"
                        fake.actions[tid] = {**t, "id": tid, "attributes": {**t["attributes"], "status": "pending"}}
                        out.append(json.loads(json.dumps(fake.actions[tid])))
                        fake.actions[tid]["attributes"]["status"] = fake._run(t, broken)
                    return self._send(200, {"data": out})
                m = re.fullmatch(r"/api/actions/(\w+)", p)
                if m and m.group(1) in fake.actions:
                    return self._send(200, {"data": fake.actions[m.group(1)]})
                if p == "/api/devices":
                    return self._send(200, {"data": fake.devices})
                if p == "/api/diagnostics":
                    if method == "DELETE":
                        fake.diags.clear()
                        return self._send(204)
                    return self._send(200, {"data": fake.diags})
                return self._send(404)

            def do_GET(self):
                self._do("GET")

            def do_PUT(self):
                self._do("PUT")

            def do_POST(self):
                self._do("POST")

            def do_DELETE(self):
                self._do("DELETE")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def _run(self, t, broken):
        if t["type"] == "addThreadDeviceTask":
            return "pending"
        if broken:
            return "failed" if t["type"] == "getNetworkDiagnosticTask" else "stopped"
        if t["type"] == "updateDeviceCollectionTask":
            if self.network is not None:
                self.devices = json.loads(json.dumps(self.network))
            return "completed"
        a = self.diag_of.get(t["attributes"]["destination"])
        if a is None:
            return "failed"
        self.diags.append({"id": f"d{len(self.diags) + 1}", "type": "networkDiagnostics", "attributes": a})
        return "completed"

    def puts(self, path):
        return [c for c in self.calls if c[0] in ("PUT", "POST") and c[1] == path]


PCS = [{"id": "dell", "name": "Dell", "host": "192.0.2.10", "user": "tester", "wsl": ""},
       {"id": "bedroom", "name": "Bedroom", "host": "192.0.2.33", "user": "tester", "wsl": ""}]


@pytest.fixture
def net(monkeypatch, tmp_path):
    """Two routers (slot 1 = the Dell, 2 = the bedroom) behind fake REST servers; the helper's ctl.sh status per host."""
    otbrs = {1: Otbr(EXT_DELL), 2: Otbr(EXT_BED)}
    status = {"192.0.2.10": {"running": "true", "state": "leader", "leaderweight": "255", "trel_peers": "1"},
              "192.0.2.33": {"running": "true", "state": "router", "leaderweight": "64", "trel_peers": "1"}}
    calls = []

    def helper(method, path, body=None, timeout=15):
        calls.append((path, body))
        if path == "/v1/tunnels":
            return {"tunnels": {}}
        if path == "/v1/hosts/thread":
            if body["cmd"] == "status":
                return {"rc": 0, "out": dict(status[body["host"]])}
            if body["cmd"] == "leaderweight":
                status[body["host"]]["leaderweight"] = str(body["weight"])
            return {"rc": 0, "out": {}}
        return {}
    pcs = sys.modules["yashome_plugins.computers"]
    monkeypatch.setattr(core, "_helper", helper)
    monkeypatch.setattr(pcs, "with_role", lambda role: [dict(c) for c in PCS] if role == "thread" else [])
    monkeypatch.setattr(me, "_base", lambda slot: otbrs[slot].url)
    monkeypatch.setattr(me, "DATASET", tmp_path / "state" / "dataset.hex")
    monkeypatch.setattr(core, "save_settings", lambda: None)
    monkeypatch.setattr(me, "_cfg", json.loads(json.dumps(me.DEFAULTS)))
    monkeypatch.setattr(me, "_routers", {})
    monkeypatch.setattr(me, "_pair", {})
    monkeypatch.setattr(me, "_nodes", {"list": [], "ts": None, "asked": 0.0})
    c = TestClient(core.app)
    c.auto_pair = me._auto_pair
    monkeypatch.setattr(me, "_auto_pair", lambda started: None)     # no pair requests of its own in the tests
    c.otbrs, c.status, c.calls = otbrs, status, calls
    yield c
    for o in otbrs.values():
        o.srv.shutdown()


def _all_text(client, caplog) -> str:
    return json.dumps(client.get("/api/thread", headers=HOUSE).json()) + json.dumps(me._health()) + caplog.text


def test_the_first_router_forms_the_network_and_the_second_gets_it(net, caplog):
    caplog.set_level("INFO")
    me.tick()
    put = net.otbrs[1].puts("/node/dataset/active")
    assert json.loads(put[0][3]) == {"NetworkName": "Yashome", "Channel": 15}
    assert me.DATASET.read_text().strip() == NET_A and stat.S_IMODE(me.DATASET.stat().st_mode) == 0o600
    given = net.otbrs[2].puts("/node/dataset/active")
    assert given[0][2] == "text/plain" and given[0][3] == NET_A and net.otbrs[2].puts("/node/state")
    v = net.get("/api/thread", headers=HOUSE).json()
    assert v["network"] == {"kept": True, "name": "Yashome", "channel": 15, "extpanid": "1111111111111111", "want": {"name": "Yashome", "channel": 15}}
    assert [r["net"] for r in v["routers"]] == ["house", "house"] and v["main"] == "dell"
    assert NET_A not in _all_text(net, caplog) and KEY_A not in _all_text(net, caplog)
    assert net.get("/api/thread").status_code in (401, 403)


def test_the_house_keeps_the_network_its_main_router_already_has(net, caplog):
    net.otbrs[1].dataset, net.otbrs[1].state = NET_A, "leader"
    me.tick()
    assert me._dataset() == NET_A and not net.otbrs[1].puts("/node/dataset/active")
    assert KEY_A not in _all_text(net, caplog)


def test_a_router_on_another_network_is_reported_never_overwritten(net):
    net.otbrs[1].dataset, net.otbrs[1].state = NET_A, "leader"
    net.otbrs[2].dataset, net.otbrs[2].state = NET_B, "leader"
    me.tick()
    assert not net.otbrs[2].puts("/node/dataset/active") and net.otbrs[2].dataset == NET_B
    row = next(r for r in me._health() if r["id"] == "thread:bedroom")
    assert row["state"] == "bad" and row["detail"] == "on another Thread network"


def test_the_main_router_gets_leader_weight_255_and_the_others_64(net):
    net.status["192.0.2.10"]["leaderweight"] = "65"
    net.status["192.0.2.33"]["leaderweight"] = "255"
    me.tick()
    sent = {b["host"]: b["weight"] for p, b in net.calls if p == "/v1/hosts/thread" and b["cmd"] == "leaderweight"}
    assert sent == {"192.0.2.10": 255, "192.0.2.33": 64}
    net.calls.clear()
    me._cfg["main"] = "bedroom"                                   # the owner picks the other one as main
    net.status["192.0.2.10"]["leaderweight"], net.status["192.0.2.33"]["leaderweight"] = "255", "64"
    for r in me._routers.values():
        r["status_ts"] = 0
    me.tick()
    sent = {b["host"]: b["weight"] for p, b in net.calls if p == "/v1/hosts/thread" and b["cmd"] == "leaderweight"}
    assert sent == {"192.0.2.10": 64, "192.0.2.33": 255}


def test_each_router_has_its_own_tunnel_slot(net):
    me.tick()
    posts = [b for p, b in net.calls if p == "/v1/tunnels" and b]
    assert {(b["role"], b["host"]) for b in posts} == {("thread1", "192.0.2.10"), ("thread2", "192.0.2.33")}


def test_a_stuck_router_is_restarted_once_and_the_restart_is_reported(net):
    net.otbrs[1].dataset, net.otbrs[1].state = NET_A, "leader"
    me.tick()
    net.otbrs[2].up = False                                        # its agent hangs, its container runs
    for _ in range(me.STUCK_CHECKS):
        me.tick()
    restarts = [b["host"] for p, b in net.calls if p == "/v1/hosts/thread" and b["cmd"] == "restart"]
    assert restarts == ["192.0.2.33"]
    for _ in range(me.STUCK_CHECKS):
        me.tick()
    assert [b["host"] for p, b in net.calls if p == "/v1/hosts/thread" and b["cmd"] == "restart"] == ["192.0.2.33"]   # not again soon
    net.otbrs[2].up = True
    me.tick()
    row = next(r for r in me._health() if r["id"] == "thread:bedroom")
    assert row["state"] == "warn" and row["detail"].startswith("restarted by the house at") and "its border router does not answer" in row["args"]


def test_health_rows_trel_and_the_leader(net):
    net.otbrs[1].dataset, net.otbrs[1].state = NET_A, "leader"
    net.otbrs[2].dataset, net.otbrs[2].state = NET_A, "router"
    me.tick()
    rows = {r["id"]: r for r in me._health()}
    assert rows["thread:dell"]["state"] == rows["thread:bedroom"]["state"] == rows["thread:leader"]["state"] == "ok"
    net.status["192.0.2.33"]["trel_peers"] = "0"
    net.otbrs[1].state, net.otbrs[2].state = "router", "leader"
    for r in me._routers.values():
        r["status_ts"] = 0
    me.tick()
    rows = {r["id"]: r for r in me._health()}
    assert rows["thread:bedroom"]["state"] == "warn" and "TREL" in rows["thread:bedroom"]["detail"]
    assert rows["thread:leader"]["state"] == "warn" and rows["thread:leader"]["args"] == ["Bedroom", "Dell"]


def test_no_border_router_is_a_warning_with_the_way_to_one(net, monkeypatch):
    monkeypatch.setattr(sys.modules["yashome_plugins.computers"], "with_role", lambda role: [])
    rows = me._health()
    assert rows[0]["id"] == "thread" and rows[0]["state"] == "warn" and rows[0]["link"] == "computers"


def test_pairing_by_eui_follows_the_task_to_joined(net, caplog):
    caplog.set_level("INFO")
    net.otbrs[1].dataset, net.otbrs[1].state = NET_A, "leader"
    me.tick()
    assert net.post("/api/thread/pair", headers=HOUSE, json={"pskd": "io12345"}).status_code == 400        # I and O are not PSKd letters
    assert net.post("/api/thread/pair", headers=HOUSE, json={"pskd": "PSKD0AAA11", "eui": "nothex"}).status_code == 400
    r = net.post("/api/thread/pair", headers=HOUSE, json={"pskd": "pskd0aaa11", "eui": "00:11:22:33:44:55:66:77"})
    assert r.status_code == 200 and r.json()["state"] == "open" and r.json()["left"] > 170
    task = json.loads(net.otbrs[1].puts("/api/actions")[-1][3])["data"][0]
    assert task == {"type": "addThreadDeviceTask", "attributes": {"eui": "0011223344556677", "pskd": "PSKD0AAA11", "timeout": 180}}
    assert net.post("/api/thread/pair", headers=HOUSE, json={"pskd": "PSKD0AAA11"}).status_code == 409
    tid = next(k for k, t in net.otbrs[1].actions.items() if t["type"] == "addThreadDeviceTask")
    net.otbrs[1].actions[tid]["attributes"]["status"] = "completed"
    assert net.get("/api/thread/pair", headers=HOUSE).json()["state"] == "joined"
    assert "PSKD0AAA11" not in caplog.text and "PSKD0AAA11" not in json.dumps(net.get("/api/thread", headers=HOUSE).json())


def test_pairing_without_eui_opens_the_commissioner_and_sees_the_new_node(net):
    net.otbrs[1].dataset, net.otbrs[1].state = NET_A, "leader"
    me.tick()
    r = net.post("/api/thread/pair", headers=HOUSE, json={"pskd": "PSKD0BBB22"})
    assert r.status_code == 200
    j = next(b for p, b in net.calls if p == "/v1/hosts/thread" and b["cmd"] == "joiner")
    assert j == {"host": "192.0.2.10", "user": "tester", "cmd": "joiner", "pskd": "PSKD0BBB22", "eui": "*"}
    assert net.get("/api/thread/pair", headers=HOUSE).json()["state"] == "open"
    net.otbrs[1].devices = [{"id": "0011223344556688", "type": "threadDevice", "attributes": {"extAddress": "0011223344556688", "hostname": "kitchen-c6.local", "role": "router"}}]
    me.tick()
    p = net.get("/api/thread/pair", headers=HOUSE).json()
    assert p["state"] == "joined" and p["node"] == "kitchen-c6"



def test_one_button_pairing_uses_the_public_code_and_pairs_the_new_yash_node_over_thread(net, monkeypatch):
    """Let it in with no code: the commissioner opens for * with the public code; each SRP host that is not on the broker
    and not a name the house has gets the pair request once, through ctl.sh pair on the main router; a node that does
    not answer it is left alone (no card, no line)."""
    import types
    net.otbrs[1].dataset, net.otbrs[1].state = NET_A, "leader"
    me.tick()
    srp = ["node-on fd00:db8:1:1::4", "node-old fd00:db8:1:1::5", "taken fd00:db8:1:1::6"]   # node-a: no SRP name (an old key holds it)
    with me._lock:
        me._nodes["list"] = [{"ext": "0011223344550003", "omr": "fd00:db8:1:1::3", "border_router": False}]
    sent, made, probes = [], [], []
    real = core._helper

    def helper(method, path, body=None, timeout=15):
        if path == "/v1/hosts/thread" and body["cmd"] == "status":
            out = real(method, path, body, timeout)
            return {**out, "out": {**out["out"], "srp": srp if body["host"] == "192.0.2.10" else []}}
        if path == "/v1/hosts/thread" and body["cmd"] == "pair" and not body["form"]:     # the probe: only node-a waits for the house
            probes.append(body["addr"])
            if body["addr"].endswith("::3") and probes.count(body["addr"]) > 1:   # it answers from the second round on
                return {"rc": 0, "out": {"pair_status": 400, "pair_reply": {"ok": False, "error": "bad or missing field", "node": "node-a-0a0b0c"}}}
            if body["addr"].endswith("::6"):                                    # a node the house has: refused by its name
                return {"rc": 0, "out": {"pair_status": 400, "pair_reply": {"ok": False, "node": "taken"}}}
            return {"rc": 0, "out": {"pair_status": 0}}
        if path == "/v1/hosts/thread" and body["cmd"] == "pair":
            sent.append((body["host"], body["addr"], body["form"]))
            if body["addr"].endswith("::3"):
                return {"rc": 0, "out": {"pair_status": 200, "pair_reply": {"ok": True, "node": "node-a-0a0b0c", "hash": "0123456789ab"}}}
            return {"rc": 0, "out": {"pair_status": 0, "pair_reply": {"ok": False, "error": "connection refused"}}}
        return real(method, path, body, timeout)

    def pair_with(node, deliver, house_id="yashome", reads=(), kind="device", via="wifi"):
        assert via == "thread"
        made.append(node)
        out = deliver({"house_id": house_id, "mqtt_user": f"dev-{node}", "mqtt_password": "pw not real", "ota_password": "ota not real"})
        if out.get("ok") is not True:
            raise me.HTTPException(409, out.get("error"))
        return {"ok": True, "node": out["reply"]["node"]}

    def undelivered(node, deliver, **kw):
        try:
            return pair_with(node, deliver, **kw)
        except RuntimeError as e:
            raise me.HTTPException(502, str(e))
    pm = types.SimpleNamespace(NODE_RE=re.compile(r"[a-z][a-z0-9_-]{0,27}"), _taken=lambda n: "a device of the house" if n == "taken" else "",
                               thread_form=lambda rec, host, port: {"house_id": rec["house_id"], "mqtt_host": host, "mqtt_port": str(port),
                                                                   "mqtt_user": rec["mqtt_user"], "mqtt_password": rec["mqtt_password"],
                                                                   "ota_password": rec["ota_password"]},
                               pair_with=undelivered)
    monkeypatch.setattr(core, "_helper", helper)
    monkeypatch.setitem(sys.modules, "yashome_plugins.pair", pm)
    monkeypatch.setenv("BIND_ADDRESS", "192.0.2.10")
    monkeypatch.setattr(core, "_retained_values", lambda topics, timeout=3.0: {t: "online" for t in topics if t == "node-on/status"})
    r = net.post("/api/thread/pair", headers=HOUSE, json={})
    assert r.status_code == 200
    j = next(b for p, b in net.calls if p == "/v1/hosts/thread" and b["cmd"] == "joiner")
    assert (j["pskd"], j["eui"]) == (me.PUBLIC_PSKD, "*")
    with me._lock:
        me._pair["until"] = me.time.time() - me.AUTO_PAIR_AFTER_S + 0.3            # a few passes, then the loop ends
    monkeypatch.setattr(me.time, "sleep", lambda s: None)
    net.auto_pair(me._pair["started"])                                      # here, not in its own thread
    assert made == ["node-a-0a0b0c"]                     # not the one online, not a taken name, not one that never answers
    assert [(h, a) for h, a, _ in sent] == [("192.0.2.10", "fd00:db8:1:1::3")]
    assert probes.count("fd00:db8:1:1::3") == 2 and probes.count("fd00:db8:1:1::5") >= 2   # no answer: asked again later
    assert "fd00:db8:1:1::4" not in probes and probes.count("fd00:db8:1:1::6") == 1        # online: never asked; taken: once
    assert sent[0][2]["mqtt_host"] == "192.0.2.10" and "ssid" not in sent[0][2] and "code" not in sent[0][2]
    assert net.get("/api/thread/pair", headers=HOUSE).json()["paired"] == [{"node": "node-a-0a0b0c", "ok": True}]

def _dev(ext, kind="threadDevice", omr="::", **a):
    """A /api/devices entry as the live OTBR gives it (hostname empty: the nodes registered with the other router's SRP)."""
    return {"id": ext, "type": kind, "attributes": {"extAddress": ext, "mlEidIid": "0011223344556677", "omrIpv6Address": omr, "hostname": "",
                                                    "role": "router", "mode": {"fullThreadDevice": True, "rxOnWhenIdle": True, "fullNetworkData": True}, **a}}


def _nb(ext, rloc, margin, rssi):
    return {"supportsErrorRate": True, "rloc16": rloc, "extAddress": ext, "threadVersion": 5, "linkAge": 400, "linkMargin": margin,
            "averageRssi": rssi, "lastRssi": rssi - 1, "frameErrorRate": 0, "messageErrorRate": 0}


def test_nodes_from_the_live_shapes_with_links_and_srp_names(net, monkeypatch):
    """The main router's device collection (asked alone), then the diagnostics of every other router; names from the SRP
    hosts the OTHER router serves; the main router's links from its neighbours' tables."""
    net.otbrs[1].dataset, net.otbrs[1].state = NET_A, "leader"
    net.otbrs[2].dataset, net.otbrs[2].state = NET_A, "router"
    net.status["192.0.2.33"]["srp"] = ["thread-node-b fd00:db8:1:1::4", "thread-node-a fd00:db8:1:1::3"]
    monkeypatch.setitem(core.DEVICES, "thread_node_a", {"id": "thread_node_a", "name": "Bench", "transport": "mqtt", "controls": [],
                                                       "availability_topic": "home/thread-node-a/status"})
    net.otbrs[1].network = [
        _dev(EXT_KITCHEN, omr="fd00:db8:1:1::4"), _dev(EXT_BED, omr="fd00:db8:1:1::2"), _dev(EXT_BENCH, omr="fd00:db8:1:1:0:0:0:3"),
        _dev(EXT_DELL, "threadBorderRouter", "fd00:db8:1:1::1", state="leader", rloc16="0xe000", baState="active")]
    net.otbrs[1].diag_of = {
        EXT_BENCH: {"extAddress": EXT_BENCH, "rloc16": "0xec00", "routerId": 59, "childTable": [],
                    "routerNeighbors": [_nb(EXT_BED, "0xdc00", 74, -46), _nb(EXT_DELL, "0xe000", 37, -83)]},
        EXT_BED: {"extAddress": EXT_BED, "rloc16": "0xdc00", "routerId": 55, "childTable": [],
                  "routerNeighbors": [_nb(EXT_DELL, "0xe000", 60, -36), _nb(EXT_BENCH, "0xec00", 70, -30)]}}
    me.tick()
    assert [(n["ext"], n["role"]) for n in me._nodes["list"]] == [(EXT_DELL, "disabled")]      # what the OTBR has at its start
    me.tick()
    me.tick()
    batches = [[t["type"] for t in json.loads(b)["data"]] for _, _, _, b in net.otbrs[1].puts("/api/actions")]
    assert ["updateDeviceCollectionTask"] in batches and all(len(b) == 1 for b in batches if "updateDeviceCollectionTask" in b)
    diags = [t["attributes"]["destination"] for b in net.otbrs[1].puts("/api/actions") for t in json.loads(b[3])["data"]
             if t["type"] == "getNetworkDiagnosticTask"]
    assert sorted(diags) == sorted([EXT_KITCHEN, EXT_BED, EXT_BENCH])                       # never the main router itself
    nodes = {n["name"]: n for n in net.get("/api/thread", headers=HOUSE).json()["nodes"]}
    assert list(nodes) == ["Bedroom", "Dell", "thread-node-a", "thread-node-b"]
    d = nodes["Dell"]
    assert (d["role"], d["border_router"], d["router"], d["device"]) == ("leader", True, True, None)
    assert d["links"] == [{"node": "Bedroom", "rssi": -36, "lq": 3}, {"node": "thread-node-a", "rssi": -83, "lq": 3}]
    b = nodes["thread-node-a"]
    assert (b["role"], b["border_router"], b["rssi"], b["lq"], b["device"], b["parent"]) == ("router", False, -46, 3, "thread_node_a", None)
    assert [k["node"] for k in b["links"]] == ["Bedroom", "Dell"]
    assert nodes["Bedroom"]["border_router"] and nodes["Bedroom"]["role"] == "router"
    k = nodes["thread-node-b"]
    assert (k["role"], k["links"], k["rssi"], k["device"]) == ("router", [], None, None)    # no diagnostics answered
    assert net.get("/api/thread/marks", headers=HOUSE).json() == {"thread_node_a": "router"}
    assert net.post("/api/thread/node", headers=HOUSE, json={"node": EXT_KITCHEN, "device": "thread_node_a"}).status_code == 200
    assert next(n for n in me._nodes["list"] if n["ext"] == EXT_KITCHEN)["device"] == "thread_node_a"



def test_an_address_with_an_old_and_a_new_srp_name_takes_the_house_device_s(monkeypatch):
    """A re-flashed node registers under its new name while its old lease stays: the name of a house device wins."""
    monkeypatch.setattr(me, "_routers", {"a": {"status": {"srp": ["node-b fd00:db8:1:1::4", "node-a-0a0b0c fd00:db8:1:1::3"]}},
                                         "b": {"status": {"srp": ["node-a fd00:db8:1:1::3"]}}})
    monkeypatch.setitem(core.DEVICES, "node-a-0a0b0c", {"id": "node-a-0a0b0c", "transport": "mqtt", "controls": []})
    monkeypatch.setattr(me, "_cfg", {**me._cfg, "names": {}})
    assert me._srp_names() == {"fd00:db8:1:1::4": "node-b", "fd00:db8:1:1::3": "node-a-0a0b0c"}
    monkeypatch.setattr(me, "_routers", {"b": {"status": {"srp": ["node-a fd00:db8:1:1::3"]}},
                                         "a": {"status": {"srp": ["node-a-0a0b0c fd00:db8:1:1::3"]}}})
    assert me._srp_names()["fd00:db8:1:1::3"] == "node-a-0a0b0c"                  # in either order

def test_a_child_gets_its_parent_and_link_from_the_parent_child_table():
    devs = {"data": [_dev(EXT_BED, rloc16="0xdc00"), {"id": EXT_KITCHEN, "type": "threadDevice", "attributes": {
        "extAddress": EXT_KITCHEN, "role": "child", "rloc16": "0xdc01", "omrIpv6Address": "fd00:db8:1:1::4"}}]}
    diags = {"data": [{"type": "networkDiagnostics", "attributes": {"extAddress": EXT_BED, "rloc16": "0xdc00",
                                                                    "childTable": [{"childId": 1, "linkQuality": 2, "timeout": 240}], "routerNeighbors": []}}]}
    n = {x["ext"]: x for x in me.nodes_from(devs, diags, {"fd00:db8:1:1::4": "thread-node-b"}, {EXT_BED: "Bedroom"})}
    assert (n[EXT_KITCHEN]["name"], n[EXT_KITCHEN]["parent"], n[EXT_KITCHEN]["lq"], n[EXT_KITCHEN]["router"]) == ("thread-node-b", "Bedroom", 2, False)


def test_a_router_status_from_before_it_got_the_network_is_read_again(net):
    """ctl.sh status said "disabled" at the install; the REST says router now: status is read again at once, not in 5 min."""
    net.otbrs[1].dataset, net.otbrs[1].state = NET_A, "leader"
    net.status["192.0.2.33"].update(state="disabled", leaderweight="65")
    me.tick()
    assert me._routers["bedroom"]["status"]["state"] == "disabled"
    net.status["192.0.2.33"]["state"] = "router"
    net.otbrs[2].state = "router"
    me.tick()
    assert me._routers["bedroom"]["status"]["state"] == "router"
    assert {b["host"]: b["weight"] for p, b in net.calls if p == "/v1/hosts/thread" and b["cmd"] == "leaderweight"}.get("192.0.2.33") == 64
    n = len([c for c in net.calls if c[0] == "/v1/hosts/thread" and c[1]["cmd"] == "status"])
    me.tick()
    assert len([c for c in net.calls if c[0] == "/v1/hosts/thread" and c[1]["cmd"] == "status"]) == n        # same state: cached

# ---- the role's install script, rendered with stub commands ----
STUBS = {"ip": "echo 'default via 192.0.2.1 dev eno1 proto static'",
         "docker": 'case "$1" in ps) [ -n "${FAKE_OTHER:-}" ] && echo "$FAKE_OTHER openthread/otbr@sha256:00";; compose) echo "compose $*" >> "$LOG";; esac; exit 0',
         "modprobe": 'echo "modprobe $*" >> "$LOG"', "curl": "exit 0", "sleep": "exit 0"}


def _render(tmp_path, radio="usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_0011-if00-port0", baud="460800", **env):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        (bin_ / name).write_text("#!/bin/sh\n" + body + "\n")
        (bin_ / name).chmod(0o755)
    byid = tmp_path / "by-id"
    byid.mkdir(exist_ok=True)
    (byid / radio).touch()
    text = (REPO / "helper" / "roles" / "thread-install.sh").read_text()
    text = text.replace("/opt/yashome-thread", str(tmp_path / "opt")).replace("/etc/modules-load.d", str(tmp_path))
    text = text.replace('"/dev/serial/by-id/$RADIO" ]', f'"{byid}/$RADIO" ]')
    text = text.replace("[ -S /run/systemd/journal/dev-log ]", "true")
    script = tmp_path / "install.sh"
    script.write_text(f"set -- '{radio}' '{baud}'\n" + text)
    r = subprocess.run(["sh", str(script)], capture_output=True, text=True,
                       env={"PATH": f"{bin_}:/usr/bin:/bin", "LOG": str(tmp_path / "log"), **env})
    return r


def test_the_role_script_parses_and_passes_shellcheck():
    for name in ("thread-install.sh", "thread-remove.sh"):
        assert subprocess.run(["sh", "-n", str(REPO / "helper" / "roles" / name)]).returncode == 0
        if shutil.which("shellcheck"):
            r = subprocess.run(["shellcheck", "-s", "sh", str(REPO / "helper" / "roles" / name)], capture_output=True, text=True)
            assert r.returncode == 0, r.stdout


def test_the_role_writes_the_pinned_otbr_on_the_chosen_radio_with_trel_on_the_lan(tmp_path):
    r = _render(tmp_path)
    assert r.returncode == 0, r.stderr
    c = yaml.safe_load((tmp_path / "opt" / "compose.yml").read_text())["services"]["otbr"]
    assert re.fullmatch(r"openthread/otbr@sha256:[0-9a-f]{64}", c["image"])
    assert (c["network_mode"], c["privileged"], c["tmpfs"], c["restart"]) == ("host", True, ["/run"], "unless-stopped")
    assert c["environment"] == {"DEBUG_LEVEL": "5", "FIREWALL": "0", "HTTP_HOST": "127.0.0.1", "HTTP_PORT": "8089", "REST_HOST": "127.0.0.1"}
    assert c["command"] == ["--radio-url", "spinel+hdlc+uart:///dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_0011-if00-port0?uart-baudrate=460800",
                            "--trel-url", "trel://eno1", "--backbone-interface", "eno1"]
    assert "flow-control" not in json.dumps(c) and "./data:/var/lib/thread" in c["volumes"]
    assert c["volumes"][:4] == ["/dev:/dev", "./rsyslog.conf:/etc/rsyslog.conf:ro",
                                "/run/systemd/journal/dev-log:/run/systemd/journal/dev-log", "./syslog:/var/log/syslog"]
    assert "imuxsock" not in (tmp_path / "opt" / "rsyslog.conf").read_text()           # the host keeps its /dev/log
    assert (tmp_path / "opt" / "syslog").read_text() == ""
    assert (tmp_path / "yashome-thread.conf").read_text() == "iptable_nat\niptable_filter\n"
    log = (tmp_path / "log").read_text()
    assert "modprobe iptable_nat" in log and "compose compose up -d" in log
    assert os.access(tmp_path / "opt" / "ctl.sh", os.X_OK) and subprocess.run(["sh", "-n", str(tmp_path / "opt" / "ctl.sh")]).returncode == 0


def test_the_role_refuses_a_second_otbr_and_a_path_for_a_radio(tmp_path):
    r = _render(tmp_path, FAKE_OTHER="otbr")
    assert r.returncode != 0 and "another border router runs here (container otbr)" in r.stderr
    assert not (tmp_path / "opt" / "compose.yml").exists()
    other = tmp_path / "other"
    other.mkdir()
    r = _render(other, radio="..")
    assert r.returncode != 0 and "not a name in /dev/serial/by-id" in r.stderr
    again = tmp_path / "again"
    again.mkdir()
    assert _render(again, FAKE_OTHER="yashome-otbr-routes").returncode == 0           # its own routes service is no other router


# what the live routers had: the other router's RA route (NetworkManager, metric 105) to the shared OMR prefix
RA_ROUTES = """default via fe80::1 metric 1024 pref medium
fd00:db8:1:1::/64 via fe80::2 metric 105 pref medium
fd00:db8:1:1::/64 via fe80::2 metric 1024 expires 1797sec pref medium
fd00:db8:9:1::/64 via fe80::2 metric 105 pref low
fd00:db8:5:5::/64 metric 100 pref medium
"""
WPAN = """fd00:db8:1:1::/64 proto kernel metric 256 pref medium
fd00:db8:2:2::/64 proto kernel metric 256 pref medium
fe80::/64 proto kernel metric 256 pref medium
"""
FAKE_IP = """#!/bin/sh
case "$*" in
"-6 route show dev wpan0") cat "$W" ;;
"-6 route show dev eno1 proto ra") cat "$RA" ;;
"-6 route add "*) echo "$*" >> "$LOG"; echo "$4 proto static metric ${10} pref medium" >> "$W" ;;
*) echo "unexpected: $*" >> "$LOG"; exit 1 ;;
esac
"""


def test_routes_pins_the_own_thread_prefixes_over_the_other_router_ra_route(tmp_path):
    """The loop between two border routers: the sysctl goes back to 0 and the prefix both wpan0 and an RA route have is
    pinned to wpan0 (metric 10) once; the default route, on-link prefixes and prefixes not on this mesh stay as they are."""
    assert _render(tmp_path).returncode == 0
    routes = tmp_path / "opt" / "routes.sh"
    assert os.access(routes, os.X_OK) and routes.read_text().startswith("#!/bin/sh\nBB=eno1\n")
    services = yaml.safe_load((tmp_path / "opt" / "compose.yml").read_text())["services"]
    svc = services["otbr"]                                   # one container: the guard runs beside the router
    assert list(services) == ["otbr"] and "./routes.sh:/routes.sh:ro" in svc["volumes"]
    assert svc["entrypoint"] == ["/bin/sh", "-c", 'sh /routes.sh loop & exec /app/etc/docker/test/docker_entrypoint.sh "$$@"', "otbr"]
    b = tmp_path / "rbin"
    b.mkdir()
    (b / "ip").write_text(FAKE_IP)
    (b / "ip").chmod(0o755)
    conf = tmp_path / "conf" / "eno1"
    conf.mkdir(parents=True)
    (conf / "accept_ra_rt_info_max_plen").write_text("64\n")
    (tmp_path / "ra").write_text(RA_ROUTES)
    (tmp_path / "wpan").write_text(WPAN)
    env = {"PATH": f"{b}:/usr/bin:/bin", "ROUTES_CONF": str(tmp_path / "conf"), "W": str(tmp_path / "wpan"), "RA": str(tmp_path / "ra"),
           "LOG": str(tmp_path / "iplog")}
    r = subprocess.run(["sh", str(routes)], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.splitlines() == ["routes: eno1 accept_ra_rt_info_max_plen -> 0",
                                                           "routes: fd00:db8:1:1::/64 pinned to wpan0 (an RA route to it via eno1)"]
    assert (conf / "accept_ra_rt_info_max_plen").read_text().strip() == "0"
    assert (tmp_path / "iplog").read_text() == "-6 route add fd00:db8:1:1::/64 dev wpan0 proto static metric 10\n"
    r = subprocess.run(["sh", str(routes)], capture_output=True, text=True, env=env)
    assert (r.returncode, r.stdout) == (0, "")                                                # nothing to change: silent
    assert (tmp_path / "iplog").read_text().count("\n") == 1


# `ot-ctl srp server host` as the live router prints it (a deleted host is left out)
SRP_HOSTS = """thread-node-b.default.service.arpa.\r
    deleted: false\r
    addresses: [fd00:db8:1:1::4]\r
    lease: 7200\r
thread-node-a.default.service.arpa.\r
    deleted: false\r
    addresses: [fd00:db8:1:1::3, fd00:db8:1:1::33]\r
old-node.default.service.arpa.\r
    deleted: true\r
    addresses: [fd00:db8:1:1::9]\r
Done\r
"""


def test_ctl_status_lists_the_srp_hosts_of_the_router(tmp_path):
    assert _render(tmp_path).returncode == 0
    b = tmp_path / "cbin"
    b.mkdir()
    (tmp_path / "srp").write_text(SRP_HOSTS)
    (b / "docker").write_text('#!/bin/sh\ncase "$*" in\ninspect*) echo true ;;\n*"srp server host") cat "$SRP" ;;\n*"trel peers") echo "| 0 | x |" ;;\n'
                              '*) printf "router\\r\\nDone\\r\\n" ;;\nesac\n')
    (b / "docker").chmod(0o755)
    (b / "ip").write_text("#!/bin/sh\nexit 0\n")
    (b / "ip").chmod(0o755)
    r = subprocess.run(["sh", str(tmp_path / "opt" / "ctl.sh"), "status"], capture_output=True, text=True,
                       env={"PATH": f"{b}:/usr/bin:/bin", "SRP": str(tmp_path / "srp")})
    assert r.returncode == 0 and r.stdout.splitlines() == [
        "routes_fixed=0", "running=true", "state=router", "leaderweight=router", "trel_peers=1",
        "srp=thread-node-b fd00:db8:1:1::4", "srp=thread-node-a fd00:db8:1:1::3", "srp=thread-node-a fd00:db8:1:1::33"]


# iptables on a docker host: the chain DOCKER-USER is there, no rule of ours yet (-C fails until -I added it)
FAKE_IPT = """#!/bin/sh
echo "$*" >> "$IPT"
case "$1" in
-S) exit 0 ;;
-C) shift; grep -qxF -- "-I $*" "$IPT" ;;
esac
"""


def test_ctl_status_lets_the_nodes_mqtt_through_docker_forward_drop_once(tmp_path):
    """Docker's FORWARD DROP ate the NAT64 packets of the nodes to the broker: status adds the two rules (MQTT from
    wpan0 into the backbone's LAN, the answers back) once, says so, and does not count them as fixed routes."""
    assert _render(tmp_path).returncode == 0
    b = tmp_path / "fbin"
    b.mkdir()
    (b / "docker").write_text("#!/bin/sh\necho false\n")
    (b / "ip").write_text('#!/bin/sh\ncase "$*" in\n"-4 route show dev eno1 scope link") echo "192.0.2.0/24 proto kernel src 192.0.2.5" ;;\nesac\nexit 0\n')
    (b / "iptables").write_text(FAKE_IPT)
    for f in b.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{b}:/usr/bin:/bin", "IPT": str(tmp_path / "ipt")}
    r = subprocess.run(["sh", str(tmp_path / "opt" / "ctl.sh"), "status"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.splitlines() == ["routes_fixed=0", "running=false"]
    added = [x for x in (tmp_path / "ipt").read_text().splitlines() if x.startswith("-I")]
    assert added == ["-I DOCKER-USER -i wpan0 -o eno1 -d 192.0.2.0/24 -p tcp --dport 1883 -j ACCEPT",
                     "-I DOCKER-USER -i eno1 -o wpan0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"]
    subprocess.run(["sh", str(tmp_path / "opt" / "ctl.sh"), "status"], capture_output=True, text=True, env=env)
    assert sum(x.startswith("-I") for x in (tmp_path / "ipt").read_text().splitlines()) == 2      # there already: not again
    r = subprocess.run(["sh", str(tmp_path / "opt" / "ctl.sh"), "unforward"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and sum(x.startswith("-D") for x in (tmp_path / "ipt").read_text().splitlines()) == 2


def test_ctl_pair_posts_the_form_from_stdin_to_the_node_over_ipv6(tmp_path):
    """ctl.sh pair <IPv6>: the house's form (stdin, never argv) to the node's /yash/pair; its status and answer come back;
    a node that does not answer is status 0; anything but an IPv6 address is refused."""
    import socket
    assert _render(tmp_path).returncode == 0
    got = {}

    class Node(BaseHTTPRequestHandler):
        def do_POST(self):
            got["path"], got["ctype"] = self.path, self.headers.get("Content-Type")
            got["body"] = self.rfile.read(int(self.headers["Content-Length"])).decode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true,"node":"node-a-0a0b0c","hash":"0123456789ab"}')

        def log_message(self, *a):
            pass

    class V6(ThreadingHTTPServer):
        address_family = socket.AF_INET6
    srv = V6(("::1", 0), Node)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ctl = tmp_path / "opt" / "ctl.sh"
    ctl.write_text(ctl.read_text().replace("]/yash/pair", f"]:{srv.server_address[1]}/yash/pair"))
    r = subprocess.run(["sh", str(ctl), "pair", "::1"], input="house_id=yashome&mqtt_user=dev-node-a", capture_output=True, text=True)
    srv.shutdown()
    srv.server_close()
    assert r.returncode == 0 and r.stdout.splitlines() == ["pair_status=200", 'pair_reply={"ok":true,"node":"node-a-0a0b0c","hash":"0123456789ab"}']
    assert got == {"path": "/yash/pair", "ctype": "application/x-www-form-urlencoded", "body": "house_id=yashome&mqtt_user=dev-node-a"}
    r = subprocess.run(["sh", str(ctl), "pair", "::1"], input="x=1", capture_output=True, text=True)       # nobody listens now
    assert r.stdout.splitlines()[0] == "pair_status=0"
    r = subprocess.run(["sh", str(ctl), "pair", "1.2.3.4;id"], input="", capture_output=True, text=True)
    assert r.returncode != 0 and "pair_status" not in r.stdout


# ---- the helper's side: role arguments, the fixed ctl.sh commands, a tunnel slot to the router's REST ----
def _helper():
    import importlib.util
    spec = importlib.util.spec_from_file_location("yashome_helper_thread", REPO / "helper" / "helper.py")
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    return h


def test_the_helper_takes_only_a_by_id_name_and_a_known_baud():
    h = _helper()
    assert h.ROLE_SCRIPTS[("thread", "install")] == ("helper/roles/thread-install.sh", ("radio", "baud"))
    ok = h.ROLE_ARG_RE["radio"]
    assert ok.match("usb-Nordic_Semiconductor_ASA_Yashome_Thread_RCP_0011-if00")
    for bad in ("../ttyUSB0", "/dev/ttyUSB0", "a b", "x;y", "-x", ".hidden"):
        assert not ok.match(bad), bad
    assert h.ROLE_ARG_RE["baud"].match("460800") and not h.ROLE_ARG_RE["baud"].match("9600")


def test_the_helper_runs_only_the_fixed_ctl_commands(monkeypatch, tmp_path):
    import types
    h = _helper()
    cfg = types.SimpleNamespace(dir=str(tmp_path), house=None, ssh_dir=None, git_ssh="")
    (tmp_path / ".helper").mkdir()
    (tmp_path / ".helper" / "hosts_known_hosts").write_text("")
    seen = []
    monkeypatch.setattr(h, "run", lambda cmd, cfg, timeout=10, input=None: seen.append((cmd, input)) or
                        subprocess.CompletedProcess(cmd, 0, "routes_fixed=0\nrunning=true\nstate=leader\nleaderweight=255\ntrel_peers=1\nbad line\n"
                                                    "srp=thread-node-a fd00:db8:1:1::3\nsrp=thread-node-b fd00:db8:1:1::4\nsrp=a;b fd00::1\nsrp=x 1.2.3.4\n", ""))
    st, out = h.post_hosts_thread(cfg, {"host": "192.0.2.10", "user": "tester", "cmd": "status"})
    assert out == {"rc": 0, "out": {"routes_fixed": "0", "running": "true", "state": "leader", "leaderweight": "255", "trel_peers": "1",
                                    "srp": ["thread-node-a fd00:db8:1:1::3", "thread-node-b fd00:db8:1:1::4"]}}
    assert seen[-1][0][-1] == "sudo -n /opt/yashome-thread/ctl.sh status"
    h.post_hosts_thread(cfg, {"host": "192.0.2.10", "user": "tester", "cmd": "leaderweight", "weight": 255})
    assert seen[-1][0][-1] == "sudo -n /opt/yashome-thread/ctl.sh leaderweight 255"
    h.post_hosts_thread(cfg, {"host": "192.0.2.10", "user": "tester", "cmd": "joiner", "pskd": "PSKD0AAA11", "eui": "*"})
    assert seen[-1][0][-1] == "sudo -n /opt/yashome-thread/ctl.sh joiner" and seen[-1][1] == "* PSKD0AAA11\n"   # never on a command line
    monkeypatch.setattr(h, "run", lambda cmd, cfg, timeout=10, input=None: seen.append((cmd, input)) or
                        subprocess.CompletedProcess(cmd, 0, 'pair_status=200\npair_reply={"ok":true,"node":"node-a-0a0b0c","hash":"0123456789ab","x":{"y":1}}\n', ""))
    form = {"house_id": "yashome", "mqtt_host": "192.0.2.10", "mqtt_port": "1883", "mqtt_user": "dev-node-a", "mqtt_password": "pw not real",
            "ota_password": "ota not real"}
    st, out = h.post_hosts_thread(cfg, {"host": "192.0.2.10", "user": "tester", "cmd": "pair", "addr": "fd00:db8:1:1::3", "form": form})
    assert out["out"] == {"pair_status": 200, "pair_reply": {"ok": True, "node": "node-a-0a0b0c", "hash": "0123456789ab"}}   # plain fields only
    assert seen[-1][0][-1] == "sudo -n /opt/yashome-thread/ctl.sh pair fd00:db8:1:1::3" and "pw+not+real" in seen[-1][1]    # the form on stdin
    for bad in ({"cmd": "pair", "addr": "fd00::1;id", "form": form}, {"cmd": "pair", "addr": "fd00::1", "form": {**form, "ssid": "x"}},
                {"cmd": "pair", "addr": "fd00::1", "form": {**form, "mqtt_port": 1883}}, {"cmd": "pair", "addr": "fd00::1", "form": "a=b"}):
        with pytest.raises(h.HttpError):
            h.post_hosts_thread(cfg, {"host": "192.0.2.10", "user": "tester", **bad})
    for bad in ({"cmd": "shell"}, {"cmd": "leaderweight", "weight": 256}, {"cmd": "leaderweight", "weight": "1;x"},
                {"cmd": "joiner", "pskd": "a b", "eui": "*"}, {"cmd": "joiner", "pskd": "PSKD0AAA11", "eui": "-x"}):
        with pytest.raises(h.HttpError):
            h.post_hosts_thread(cfg, {"host": "192.0.2.10", "user": "tester", **bad})


def test_a_thread_tunnel_slot_brings_the_router_rest_to_its_own_port():
    import types
    h = _helper()
    cfg = types.SimpleNamespace(dir="/tmp")
    assert h.TUNNEL_PORTS["thread1"] == 8181 and h.TUNNEL_PORTS["thread8"] == 8188
    cmd = h._tunnel_cmd(cfg, {"host": "192.0.2.10", "user": "u", "wsl": ""}, 8182, h.TUNNEL_REMOTE["thread2"])
    assert "0.0.0.0:8182:127.0.0.1:8081" in cmd


def test_the_probe_lists_the_serial_radios(monkeypatch):
    import types
    h = _helper()
    cfg = types.SimpleNamespace(dir="/tmp")
    out = "host=x\nserial=usb-Itead_Sonoff_0011-if00-port0\nserial=bad name\nserial=../x\nrole=thread\nsudo=yes\n"
    monkeypatch.setattr(h, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    monkeypatch.setattr(h.os.path, "isfile", lambda p: True)
    r = h.check_host(cfg, "192.0.2.33", "fraddy")
    assert r["serials"] == ["usb-Itead_Sonoff_0011-if00-port0"] and r["roles"] == ["thread"]


def test_the_computer_card_installs_the_role_on_a_radio_the_probe_found(monkeypatch, tmp_path):
    pcs = sys.modules["yashome_plugins.computers"]
    sent = []
    serials = ["usb-Logitech_Receiver-if00", "usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_0011-if00-port0",
               "usb-Nordic_Semiconductor_ASA_Yashome_Thread_RCP_0011-if00"]

    def helper(method, path, body=None, timeout=15):
        sent.append((path, body))
        return {"run": {"state": "running"}} if path == "/v1/hosts/run" else {"runs": {}} if path == "/v1/hosts/runs" else {}
    monkeypatch.setattr(core, "_helper", helper)
    monkeypatch.setattr(pcs, "DATA", tmp_path / "computers.json")
    monkeypatch.setattr(pcs, "_computers", {"bedroom": {"name": "Bedroom", "host": "192.0.2.33", "user": "tester"}})
    monkeypatch.setattr(pcs, "_status", {"bedroom": {"ok": True, "roles": [], "serials": serials}})
    c = TestClient(core.app)
    v = c.get("/api/computers", headers=HOUSE).json()["computers"][0]
    assert [r["name"] for r in v["radios"]][-1] == "usb-Logitech_Receiver-if00" and v["roles"]["thread"]["many"]
    r = c.post("/api/computers/bedroom/roles/thread", headers=HOUSE, json={"action": "install"})
    assert r.status_code == 409                                               # two likely radios: the owner picks one
    r = c.post("/api/computers/bedroom/roles/thread", headers=HOUSE, json={"action": "install", "radio": serials[2]})
    assert r.status_code == 200 and sent[-1][1]["args"] == {"radio": serials[2], "baud": "1000000"}
    c.post("/api/computers/bedroom/roles/thread", headers=HOUSE, json={"action": "install", "radio": serials[1], "baud": 115200})
    assert sent[-1][1]["args"] == {"radio": serials[1], "baud": "115200"}
    assert c.post("/api/computers/bedroom/roles/thread", headers=HOUSE, json={"action": "install", "radio": "../x"}).status_code == 409
    assert c.post("/api/computers/bedroom/roles/thread", headers=HOUSE, json={"action": "install", "radio": serials[1], "baud": 9600}).status_code == 400


def test_the_role_runs_here_while_its_router_answers_on_the_network(monkeypatch):
    """The computers card lights the role green from role_active: the router answers and is on the network."""
    monkeypatch.setattr(me, "_routers", {"dell": {"rest": True, "node": {"state": "leader"}}, "bed": {"rest": False, "node": {"state": "router"}},
                                         "pc": {"rest": True, "node": {"state": "detached"}}})
    assert [me.role_active({"id": i}) for i in ("dell", "bed", "pc", "none")] == [True, False, False, False]
    monkeypatch.setitem(me._routers, "dell", {"host": "192.0.2.10", "rest": True, "node": {"state": "router"}})
    assert me.role_active({"host": "192.0.2.10"}) is True                        # the computers card passes no id: by host


def test_a_node_that_left_is_not_listed_and_a_rejoined_one_has_the_name_it_told(monkeypatch):
    """A node that forgot its network rejoins with a new extended address: the router keeps the old entry until it
    expires (its "updated" stamp stops); that one is dropped, the new one gets the name the node told at pairing."""
    devices = {"data": [
        {"id": "a", "type": "threadDevice", "attributes": {"extAddress": "0011223344550003", "role": "router", "updated": "2026-11-04T07:50:04+00:00",
                                                          "omrIpv6Address": "fd00:db8:1:1::30"}},
        {"id": "b", "type": "threadDevice", "attributes": {"extAddress": "0011223344550009", "role": "router", "updated": "2026-11-04T08:46:00+00:00",
                                                          "omrIpv6Address": "fd00:db8:1:1::3"}},
        {"id": "c", "type": "threadDevice", "attributes": {"extAddress": "0011223344550004", "role": "router", "updated": "2026-11-04T08:45:10+00:00",
                                                          "omrIpv6Address": "fd00:db8:1:1::4", "hostname": "node-b.local"}}]}
    monkeypatch.setattr(me, "_routers", {})
    monkeypatch.setattr(me, "_cfg", {**me._cfg, "names": {}, "told": {}})
    monkeypatch.setattr(core, "save_settings", lambda: None)
    me._remember("fd00:db8:1:1::3", "node-a-0a0b0c")
    nodes = {n["ext"]: n for n in me.nodes_from(devices, {"data": []}, me._srp_names(), {})}
    assert sorted(nodes) == ["0011223344550004", "0011223344550009"]                    # the old entry is gone
    assert nodes["0011223344550009"]["name"] == "node-a-0a0b0c" and nodes["0011223344550004"]["name"] == "node-b"



def test_the_told_names_survive_a_restart(monkeypatch):
    monkeypatch.setattr(core, "cfg_section", lambda name, default=None: {"told": {"fd00:db8:1:1::3": "node-a-0a0b0c"}} if name == "thread" else default)
    keep = dict(me._cfg)
    try:
        me._load_cfg()
        assert me._cfg["told"] == {"fd00:db8:1:1::3": "node-a-0a0b0c"}
    finally:
        me._cfg.clear()
        me._cfg.update(keep)
