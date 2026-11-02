"""family: a phone token admits only that phone's rights, in REST and in /ws; only the dashboard pairs,
revokes, rings and sets rights; the app download is open; the bridge account never writes the core's bus."""
import hashlib
import json
import time
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.family"]
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
PHONE, FULL = "yfd_phone-token-for-tests-only-0000000000", "yfd_full-token-for-tests-only-00000000000"


def _wifi(monkeypatch, gateways=None, ssids=None):
    """The house's Wi-Fi as the phones taught it (home_wifi.json); nothing more is learned in the test."""
    w = me._home_wifi() if getattr(me._home_wifi, "_fake", False) else {"gateways": [], "ssids": []}
    w = {"gateways": sorted(gateways) if gateways is not None else w["gateways"], "ssids": sorted(ssids) if ssids is not None else w["ssids"]}
    fake = lambda: dict(w)
    fake._fake = True
    monkeypatch.setattr(me, "_home_wifi", fake)
    monkeypatch.setattr(me, "_learn_wifi", lambda gw, ssid: None)


def _phone(tok):
    return {"Authorization": "Bearer " + tok}


@pytest.fixture(autouse=True)
def phones(monkeypatch):
    devs = sorted(core.DEVICES)
    seen, ctl = devs[0], devs[1]
    me.DB.write_text(json.dumps({
        "kid": {"sha256": me._sha(PHONE), "name": "Kid", "rights": {seen: "view", ctl: "control"}},
        "adult": {"sha256": me._sha(FULL), "name": "Adult"}}))
    me._cache["mtime"] = None
    monkeypatch.setattr(me, "CLOUD", True)
    monkeypatch.setattr(me, "_net_save", lambda: None)                      # the tests never write the real network-report file
    me._net_home.clear(), me._tickled.clear(), me._tickle_open.clear(), me._pending_cmds.clear()
    calls = []
    monkeypatch.setattr(me, "_bridge", lambda m, p, b=None: calls.append((m, p, b)) or (
        [{"uid": "kid"}] if p == "/list" else {"png": "", "chars": 1}))
    yield {"seen": seen, "ctl": ctl, "hidden": devs[2], "calls": calls}
    me.DB.unlink(missing_ok=True)


def test_a_phone_sees_and_switches_only_its_rights(phones):
    c = TestClient(core.app)
    ids = {d["id"] for d in c.get("/api/devices", headers=_phone(PHONE)).json()}
    assert ids == {phones["seen"], phones["ctl"]}
    assert c.get(f"/api/devices/{phones['hidden']}/status", headers=_phone(PHONE)).status_code == 401
    r = c.post(f"/api/devices/{phones['seen']}/control", headers=_phone(PHONE), json={"code": "x", "value": 1})
    assert r.status_code == 401
    for path in ("/api/settings", "/openapi.json", "/docs", f"/api/devices/{phones['seen']}/rules"):
        assert c.get(path, headers=_phone(PHONE)).status_code == 401, path
    r = c.get("/api/devices", headers=_phone(PHONE))
    assert "content-security-policy" in {k.lower() for k in r.headers}
    assert c.get("/api/family/me", headers=_phone(PHONE)).json()["uid"] == "kid"
    assert c.get("/api/devices", headers=_phone("yfd_unknown")).status_code == 401


def test_only_the_dashboard_pairs_revokes_rings_and_sets_rights(phones, monkeypatch):
    c = TestClient(core.app)
    for path in ("/api/family/revoke", "/api/family/ring"):
        assert c.post(path, headers=_phone(FULL), json={"name": "kid"}).status_code == 401      # set-up: no use_route
    assert c.get("/api/family", headers=_phone(FULL)).status_code == 403                         # a GET: the route refuses itself
    assert c.put("/api/family/kid/rights", headers=_phone(FULL), json={"full": True}).status_code == 401
    monkeypatch.setattr(me, "server_url", lambda: "")
    assert c.post("/api/family/pair", headers=HOUSE, json={"name": "new1"}).status_code == 503
    monkeypatch.setattr(me, "server_url", lambda: "https://house.example")
    assert c.post("/api/family/pair", headers=HOUSE, json={"name": "new1"}).status_code == 200
    assert phones["calls"][-1][2]["link"] == "https://house.example/plugins/family/static/pair.html"
    assert c.post("/api/family/ring", headers=HOUSE, json={"name": "kid"}).status_code == 200
    assert me._load()["new1"]["rights"] == {}
    assert c.post("/api/family/pair", headers=HOUSE, json={"name": "new2", "full": True}).status_code == 200
    assert "rights" not in me._load()["new2"]
    for name, full in (("kid", True), ("kid", True), ("new1", True), ("new2", False), ("new2", False)):
        assert c.post("/api/family/pair", headers=HOUSE, json={"name": name, "full": full}).status_code == 200
    assert me._load()["kid"]["rights"] == {phones["seen"]: "view", phones["ctl"]: "control"}
    assert me._load()["new1"]["rights"] == {} and "rights" not in me._load()["new2"]
    for full in (False, False, True):
        assert c.post("/api/family/pair", headers=HOUSE, json={"name": "adult", "full": full}).status_code == 200
        assert "rights" not in me._load()["adult"] and me._load()["adult"]["name"] == "Adult"
    assert c.put("/api/family/new2/rights", headers=HOUSE, json={"full": False, "devices": {phones["seen"]: "view"}}).status_code == 200
    assert c.post("/api/family/pair", headers=HOUSE, json={"name": "new2", "full": True}).status_code == 200
    assert me._load()["new2"]["rights"] == {phones["seen"]: "view"}
    assert c.post("/api/family/revoke", headers=HOUSE, json={"name": "kid"}).status_code == 200
    assert c.post("/api/family/pair", headers=HOUSE, json={"name": "kid"}).status_code == 200
    assert me._load()["kid"]["rights"] == {}


def test_the_list_tells_waiting_connected_and_expired_and_keeps_local_phones(phones, monkeypatch):
    data = dict(me._load())
    data["kid"] = {"claim_sha": me._sha("yfc_x"), "name": "Kid", "created": int(__import__("time").time())}
    data["adult"] = {"claim_sha": me._sha("yfc_y"), "name": "Adult", "created": 0}
    data["solo"] = {"sha256": me._sha("yfd_solo"), "name": "Solo"}
    me._save(data)
    monkeypatch.setattr(me, "_bridge", lambda m, p, b=None: [{"uid": "kid"}, {"uid": "adult"}, {"uid": "gone"}])
    got = {p["uid"]: p["link"] for p in TestClient(core.app).get("/api/family", headers=HOUSE).json()}
    assert got == {"kid": "waiting", "adult": "qr expired", "gone": None, "solo": "connected"}


def test_ws_snapshot_is_trimmed_for_a_phone_by_header_or_query(phones):
    class Ws:
        def __init__(self, headers, query):
            self.headers, self.query_params = headers, query
    snap = {d: {} for d in core.DEVICES}
    assert set(me._ws_trim(Ws({}, {"token": PHONE}), snap)) == {phones["seen"], phones["ctl"]}
    assert set(me._ws_trim(Ws({"authorization": "Bearer " + PHONE}, {}), snap)) == {phones["seen"], phones["ctl"]}
    assert me._ws_trim(Ws({}, {"token": FULL}), snap) == snap
    assert me._ws_trim(Ws({}, {"token": os.environ["DASHBOARD_TOKEN"]}), snap) == snap
    me._save({"adult": me._load()["adult"]})
    assert me._ws_trim(Ws({}, {"token": PHONE}), snap) == {}


def _qr_code(c, name, full=False):
    import base64
    monkey = me.server_url
    me.server_url = lambda: "https://house.example"
    try:
        r = c.post("/api/family/pair", headers=HOUSE, json={"name": name, "full": full})
    finally:
        me.server_url = monkey
    assert r.status_code == 200, r.text
    if "text" in r.json():
        frag = r.json()["text"].split("#")[1]
        return json.loads(base64.urlsafe_b64decode(frag + "=" * (-len(frag) % 4)))["tok"]
    return None                                         # with the cloud the bridge draws it


def test_the_qr_code_works_once_and_for_an_hour_only(phones, monkeypatch):
    monkeypatch.setattr(me, "CLOUD", False)
    c = TestClient(core.app)
    code = _qr_code(c, "new1", full=True)
    assert code.startswith("yfc_") and me._uid_of(code) is None
    assert c.get("/api/devices", headers=_phone(code)).status_code == 401
    r = c.post("/api/family/claim", json={"code": code})
    assert r.status_code == 200 and r.json()["uid"] == "new1"
    tok = r.json()["tok"]
    assert tok.startswith("yfd_") and me._uid_of(tok) == "new1" and "claim_sha" not in me._load()["new1"]
    assert c.post("/api/family/claim", json={"code": code}).status_code == 401          # a photo of the QR
    code2 = _qr_code(c, "new1")
    assert me._uid_of(tok) is None                                                       # New QR: the old token dies
    data = dict(me._load())
    data["new1"] = {**data["new1"], "created": 0}
    me._save(data)
    assert c.post("/api/family/claim", json={"code": code2}).status_code == 401          # an hour old
    for bad in ("", "yfd_x", "yfc_nope", "x" * 64):
        assert c.post("/api/family/claim", json={"code": bad}).status_code == 401
    assert c.post("/api/family/claim", json={"code": "x" * 65}).status_code == 422
    legacy = dict(me._load())
    legacy["old"] = {"sha256": me._sha("yfd_legacy"), "claimed": False, "created": 0}
    me._save(legacy)
    assert me._uid_of("yfd_legacy") is None


def test_rights_follow_a_rename_and_a_phone_screen_keeps_to_them(phones):
    me._rights_rename(phones["seen"], "renamed_dev")
    assert "renamed_dev" in me._load()["kid"]["rights"] and phones["seen"] not in me._load()["kid"]["rights"]
    me._rights_rename("renamed_dev", phones["seen"])
    c = TestClient(core.app)
    items = [{"dev": phones["hidden"], "code": "x"}, {"dev": phones["ctl"], "code": "y"}]
    assert c.put("/api/family/me/screen", headers=_phone(PHONE), json={"items": items}).json()["items"] == [items[1]]
    big = [{"dev": "x" * 65, "code": "y"}]
    assert c.put("/api/family/me/screen", headers=_phone(PHONE), json={"items": big}).status_code == 422


def test_an_unreadable_phone_list_admits_nobody_and_is_never_overwritten(phones):
    me.DB.write_text("{broken")
    assert me._uid_of(FULL) is None
    with pytest.raises(core.HTTPException):
        me._save({})
    assert me.DB.read_text() == "{broken"


def test_the_app_download_is_open():
    c = TestClient(core.app)
    apk = core.plugin_data("family") / "yash.apk"
    apk.unlink(missing_ok=True)
    assert c.get("/plugins/family/yash.apk").status_code == 404
    apk.write_bytes(b"PK")
    try:
        r = c.get("/plugins/family/yash.apk")
        assert r.status_code == 200 and r.content == b"PK"
    finally:
        apk.unlink()


def test_the_bridge_account_reads_the_mirror_and_writes_only_its_bus(tmp_path):
    core_dir = pathlib.Path(core.__file__).parent
    (tmp_path / "tools").mkdir()
    for f in ("mkpasswd.py", "pluginlib.py"):
        shutil.copy(core_dir / "tools" / f, tmp_path / "tools")
    (tmp_path / "config" / "mosquitto").mkdir(parents=True)
    (tmp_path / "plugins" / "tools").mkdir(parents=True)
    shutil.copytree(pathlib.Path(me.__file__).parent, tmp_path / "plugins" / "tools" / "family")
    (tmp_path / ".env").write_text("PLUGINS=family\nMQTT_PASSWORD=p1\nMQTT_FAMILY_PASSWORD=p2\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MQTT_", "PLUGINS"))}
    run = lambda: subprocess.run([sys.executable, str(tmp_path / "tools" / "mkpasswd.py")], env=env,
                                 capture_output=True, text=True)
    assert run().returncode == 0
    dyn = json.loads((tmp_path / "config/mosquitto/dynamic-security.json").read_text())
    acls = {(a["acltype"], a["topic"]) for a in next(r for r in dyn["roles"] if r["rolename"] == "family-bridge")["acls"]}
    assert ("publishClientSend", "family/#") in acls and ("subscribePattern", "home/state/#") in acls
    assert ("publishClientSend", "home/state/#") not in acls
    b = tmp_path / "plugins/tools/family/setup/broker.json"
    for bad in ('"home/#"', '"$share/g/home/#"', '"#"'):
        b.write_text(b.read_text().replace('"family/#"', bad))
        r = run()
        assert r.returncode != 0 and "own bus only" in r.stderr, bad
        b.write_text(b.read_text().replace(bad, '"family/#"'))


def test_without_firebase_the_qr_holds_address_and_token_and_the_list_is_local(phones, monkeypatch):
    import base64
    monkeypatch.setattr(me, "CLOUD", False)
    monkeypatch.setattr(me, "server_url", lambda: "http://192.0.2.10:8765")
    c = TestClient(core.app)
    r = c.post("/api/family/pair", headers=HOUSE, json={"name": "new1"}).json()
    link, frag = r["text"].split("#")
    assert link == "http://192.0.2.10:8765/plugins/family/static/pair.html"
    data = json.loads(base64.urlsafe_b64decode(frag + "=" * (-len(frag) % 4)))
    assert data["srv"] == "http://192.0.2.10:8765" and data["tok"].startswith("yfc_") and "ct" not in data
    assert c.post("/api/family/claim", json={"code": data["tok"]}).json()["uid"] == "new1"
    got = {p["uid"]: p for p in c.get("/api/family", headers=HOUSE).json()}
    assert set(got) == {"kid", "adult", "new1"} and got["kid"]["live"] is False and got["kid"]["cloud"] is False
    assert c.get("/api/family/names", headers=HOUSE).json()[0] == {"uid": "adult", "name": "Adult"}
    assert c.get("/api/family/names", headers=_phone(FULL)).status_code == 403
    assert phones["calls"] == []
    r = c.post("/api/family/ring", headers=HOUSE, json={"name": "kid"})
    assert r.status_code == 409 and "not connected" in r.json()["detail"]
    assert c.post("/api/family/ring", headers=HOUSE, json={"name": "kid", "secs": 1000}).status_code == 422
    assert c.post("/api/family/revoke", headers=HOUSE, json={"name": "new1"}).status_code == 200
    assert phones["calls"] == [] and "new1" not in me._load()


def test_a_ring_goes_down_the_live_socket_and_the_ack_comes_back(phones, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(me, "CLOUD", False)
    monkeypatch.setattr(me, "LIVE_CHECK_S", 120)                # revoke itself must close it, not the re-check
    c = TestClient(core.app)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/api/family/live", headers=_phone("yfd_unknown")) as ws:
            ws.receive_json()
    with c.websocket_connect("/api/family/live", headers=_phone(PHONE)) as ws:
        for _ in range(50):
            if me._live.get("kid"):
                break
            __import__("time").sleep(0.05)
        assert c.get("/api/family", headers=HOUSE).json()[0]["live"] is True or me._live.get("kid")
        assert me._ring("kid", 1000) == {"ok": True, "via": ["live"]}
        assert ws.receive_json() == {"cmd": "ring", "secs": 300}
        assert c.post("/api/family/stop", headers=HOUSE, json={"name": "kid"}).json()["via"] == ["live"]
        assert ws.receive_json() == {"cmd": "stop"}
        ws.send_bytes(b"binary")                                  # ignored, the link stays
        ws.send_json({"ack": 1})
        for _ in range(50):
            if me._acks.get("kid"):
                break
            __import__("time").sleep(0.05)
        assert me._acks["kid"] > 0 and "ack" not in me._load()["kid"]
        assert c.get("/api/family", headers=HOUSE).json()[1]["ack"] == me._acks["kid"]
        assert c.post("/api/family/revoke", headers=HOUSE, json={"name": "kid"}).status_code == 200
        with pytest.raises(WebSocketDisconnect) as gone:
            ws.receive_json()
        assert gone.value.code == 4401                  # the app stops its link only on this code
    assert not me._live.get("kid")


def test_with_firebase_a_ring_is_a_push_too(phones):
    assert me._ring("kid", 30) == {"ok": True, "via": ["push"]}
    assert phones["calls"][-1] == ("POST", "/ring", {"uid": "kid", "secs": 30})


def test_the_ring_rule_action(phones, monkeypatch):
    act = core.BINDING_ACTIONS["ring"]
    assert act["validate"]({"target": "kid", "secs": 999}) == {"action": "ring", "target": "kid", "cmd": "ring", "secs": 300}
    assert act["validate"]({"target": "kid"})["secs"] == 60 and act["validate"]({"target": "kid", "secs": 0})["secs"] == 5
    assert act["validate"]({"target": "kid", "cmd": "stop"})["cmd"] == "stop"
    for bad in ({"target": "nobody"}, {"target": "kid", "secs": "x"}, {"target": "Kid Two"}):
        with pytest.raises(core.HTTPException):
            act["validate"](bad)
    assert act["describe"]({"target": "kid", "secs": 30})["type"] == "family-ring"
    me._rule_rings.clear()
    monkeypatch.setattr(me, "CLOUD", False)
    with pytest.raises(core.HTTPException):
        act["run"]({"target": "kid", "secs": 30})                               # not reachable: no minute starts
    assert "kid" not in me._rule_rings
    monkeypatch.setattr(me, "CLOUD", True)
    assert act["run"]({"target": "kid", "secs": 30})["via"] == ["push"]
    assert act["run"]({"target": "kid", "secs": 30})["skipped"]                 # a flapping rule rings once a minute
    assert act["run"]({"target": "kid", "secs": 30, "_forced": True})["via"] == ["push"]
    assert act["run"]({"target": "kid", "cmd": "stop"})["via"] == ["push"]      # a stop is never held back
    src = sorted(core.DEVICES)[0]
    c = TestClient(core.app)
    body = {"target": "kid", "action": "ring", "secs": 30}
    assert c.put(f"/api/bindings/{src}/probe/single", headers=_phone(FULL), json=body).status_code == 403
    assert c.put(f"/api/bindings/{src}/probe/single", headers=HOUSE, json=body).status_code == 200
    rid = f"b|{src}|probe|single"
    assert c.post("/api/rules/run", headers=_phone(FULL), json={"id": rid}).status_code == 403
    assert c.post("/api/rules/enable", headers=_phone(FULL), json={"id": rid, "enabled": False}).status_code == 403
    assert c.post("/api/rules/delete", headers=_phone(FULL), json={"id": rid}).status_code == 403
    assert c.post("/api/rules/run", headers=HOUSE, json={"id": rid}).status_code == 200
    assert c.post("/api/rules/run", headers=HOUSE, json={"id": rid}).json()["via"] == ["push"]
    assert c.post("/api/rules/delete", headers=HOUSE, json={"id": rid}).status_code == 200


def test_the_qr_library_is_served_and_ascii():
    r = TestClient(core.app).get("/plugins/family/static/qrcode.js")
    assert r.status_code == 200 and r.content.isascii() and b"Kazuhiko Arase" in r.content


def test_a_restricted_phone_opens_only_its_two_sockets():
    assert me._allowed({}, "WS", "/ws") and me._allowed({}, "WS", "/api/family/live")
    assert not me._allowed({}, "WS", "/api/family/live/x") and not me._allowed({}, "WS", "/api/other")


def test_a_new_qr_closes_the_old_link_and_sockets_are_capped(phones, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(me, "CLOUD", False)
    monkeypatch.setattr(me, "LIVE_CHECK_S", 120)
    c = TestClient(core.app)
    with c.websocket_connect("/api/family/live", headers=_phone(FULL)) as ws:
        for _ in range(50):
            if me._live.get("adult"):
                break
            __import__("time").sleep(0.05)
        _qr_code(c, "adult")
        with pytest.raises(WebSocketDisconnect) as gone:
            ws.receive_json()
        assert gone.value.code == 4401
    data = dict(me._load())
    data["adult"] = {"sha256": me._sha(FULL), "name": "Adult"}
    me._save(data)
    socks = [c.websocket_connect("/api/family/live", headers=_phone(FULL)) for _ in range(me.LIVE_MAX + 1)]
    for s in socks:
        s.__enter__()
    try:
        for _ in range(50):
            if len(me._live.get("adult", {})) == me.LIVE_MAX:
                break
            __import__("time").sleep(0.05)
        assert len(me._live["adult"]) == me.LIVE_MAX
    finally:
        for s in socks:
            try:
                s.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - the one closed as the oldest
                pass


def test_health_counts_paired_phones_only(phones, monkeypatch):
    monkeypatch.setattr(me, "CLOUD", False)
    data = dict(me._load())
    data["fresh"] = {"claim_sha": me._sha("yfc_z"), "name": "Fresh", "created": 0}
    me._save(data)
    row = me._health()[0]
    assert row["state"] == "warn" and row["args"] == ["0", "2"]
    me._save({})
    assert me._health()[0]["state"] == "ok"


def test_an_unreadable_phone_list_is_not_a_revoke_for_the_live_link(phones, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(me, "CLOUD", False)
    monkeypatch.setattr(me, "LIVE_CHECK_S", 0.2)
    c = TestClient(core.app)
    with c.websocket_connect("/api/family/live", headers=_phone(PHONE)) as ws:
        assert ws.receive_json() == {"cmd": "ping"}
        me.DB.write_text("{broken")
        with pytest.raises(WebSocketDisconnect) as gone:
            for _ in range(20):
                ws.receive_json()
        assert gone.value.code == 1011
    with pytest.raises(WebSocketDisconnect) as gone:
        with c.websocket_connect("/api/family/live", headers=_phone(PHONE)) as ws:
            ws.receive_json()
    assert gone.value.code == 1011


def test_with_firebase_the_sign_in_comes_with_the_claim_not_the_qr(phones, monkeypatch):
    sent = []

    def bridge(m, p, b=None, timeout=20):
        sent.append((p, b))
        return {"ct": "custom-token"} if p == "/custom_token" else {"png": "", "chars": 1}
    monkeypatch.setattr(me, "_bridge", bridge)
    monkeypatch.setattr(me, "server_url", lambda: "https://house.example")
    c = TestClient(core.app)
    assert c.post("/api/family/pair", headers=HOUSE, json={"name": "new1"}).status_code == 200
    code = sent[-1][1]["tok"]
    assert code.startswith("yfc_")
    r = c.post("/api/family/claim", json={"code": code}).json()
    assert r["ct"] == "custom-token" and sent[-1] == ("/custom_token", {"uid": "new1"})
    assert c.post("/api/family/claim", json={"code": code}).status_code == 401


def test_a_full_phone_cannot_overwrite_a_ring_rule_and_forced_is_never_stored(phones):
    c = TestClient(core.app)
    src = sorted(core.DEVICES)[0]
    body = {"target": "kid", "action": "ring", "secs": 30, "_forced": True, "cmd": "ring"}
    assert c.put(f"/api/bindings/{src}/probe2/single", headers=HOUSE, json=body).status_code == 200
    stored = core._bindings[src]["probe2"]["single"]
    assert "_forced" not in stored
    other = {"target": sorted(core.DEVICES)[1], "action": "on", "code": "x"}
    assert c.put(f"/api/bindings/{src}/probe2/single", headers=_phone(FULL), json=other).status_code == 403
    assert core._bindings[src]["probe2"]["single"]["action"] == "ring"
    assert c.post("/api/rules/delete", headers=HOUSE, json={"id": f"b|{src}|probe2|single"}).status_code == 200


def test_a_bad_file_admits_a_bogus_token_to_the_live_socket_only_and_its_removal_heals(phones):
    c = TestClient(core.app)
    me.DB.write_text("{broken")
    assert c.get("/api/devices", headers=_phone("yfd_bogus")).status_code == 401
    assert me._token_check("yfd_bogus", "WS", "/api/family/live") is True
    assert me._token_check("yfd_bogus", "WS", "/ws") is False
    me.DB.unlink()
    assert me._load() == {} and me._cache["bad"] is False
    assert me._token_check("yfd_bogus", "WS", "/api/family/live") is False
    me._save({"kid": {"sha256": me._sha(PHONE), "name": "Kid"}})
    assert me._uid_of(PHONE) == "kid"


def test_a_claim_with_a_broken_bridge_still_hands_over_the_token(phones, monkeypatch):
    import base64

    def bridge(m, p, b=None, timeout=20):
        if p == "/custom_token":
            raise ValueError("not JSON")
        return {"png": "", "chars": 1}
    monkeypatch.setattr(me, "_bridge", bridge)
    monkeypatch.setattr(me, "CLOUD", False)
    monkeypatch.setattr(me, "server_url", lambda: "https://house.example")
    c = TestClient(core.app)
    frag = c.post("/api/family/pair", headers=HOUSE, json={"name": "new1"}).json()["text"].split("#")[1]
    code = json.loads(base64.urlsafe_b64decode(frag + "=" * (-len(frag) % 4)))["tok"]
    monkeypatch.setattr(me, "CLOUD", True)
    r = c.post("/api/family/claim", json={"code": code})
    assert r.status_code == 200 and r.json()["no_cloud"] is True and r.json()["tok"].startswith("yfd_")


def _fake_builder(monkeypatch, states=None, fail_polls=0):
    """The build plug-in's job functions faked: AppBuild itself is the real one."""
    bm = sys.modules["yashome_plugins.build"]
    calls = {"submit": [], "polls": 0, "dropped": []}
    states = states or [{"state": "done"}]

    def job(jid):
        calls["polls"] += 1
        if calls["polls"] <= fail_polls:
            raise OSError("builder restarting")
        return states.pop(0) if len(states) > 1 else states[0]

    def submit_apk(name, project, password, keystore=None, google_services=None, app_id=None, alias="home"):
        calls["submit"].append((name, project, password, keystore, google_services, app_id, alias))
        return f"a{len(calls['submit'])}"
    monkeypatch.setattr(bm, "submit_apk", submit_apk)
    monkeypatch.setattr(bm, "job", job)
    monkeypatch.setattr(bm, "artifact", lambda jid, name: {"release.keystore": b"NEWKEY", "app.apk": b"PK-APK"}[name])
    monkeypatch.setattr(bm, "drop_artifact", lambda jid, name: calls["dropped"].append((jid, name)))
    monkeypatch.setattr(bm.AppBuild, "POLL_S", 0.01)
    return calls


def _settle(c, path, want):
    for _ in range(300):
        st = c.get(path, headers=HOUSE).json()
        if st.get("state") == want and not st.get("busy"):
            return st
        __import__("time").sleep(0.02)
    raise AssertionError(c.get(path, headers=HOUSE).json())


def test_the_family_app_is_built_with_the_house_key_made_once(phones, monkeypatch):
    calls = _fake_builder(monkeypatch)
    me._app_holder.clear()
    st_dir = core.plugin_state("family")
    for f in ("app.keystore", "app.keystore.pass", "yash.apk"):
        (st_dir / f).unlink(missing_ok=True)
    c = TestClient(core.app)
    assert c.post("/api/family/app/build", headers=_phone(FULL)).status_code == 401
    assert c.get("/api/family/app/build", headers=_phone(FULL)).status_code == 403
    assert c.post("/api/family/app/build", headers=HOUSE).json()["new_key"] is True
    st = _settle(c, "/api/family/app/build", "done")
    assert st["apk"] and st["key"] and calls["dropped"] == [("a1", "release.keystore")]
    assert (st_dir / "app.keystore").read_bytes() == b"NEWKEY"
    assert c.get("/plugins/family/yash.apk").content == b"PK-APK"
    name, project, password, key, gs, app_id, alias = calls["submit"][0]
    assert name == "yash-app" and project == me.APP_DIR and key is None and alias == "home"
    assert c.post("/api/family/app/build", headers=HOUSE).json()["new_key"] is False
    _settle(c, "/api/family/app/build", "done")
    assert calls["submit"][1][3] == b"NEWKEY" and calls["submit"][1][2] == password
    for f in ("app.keystore", "app.keystore.pass", "yash.apk"):
        (st_dir / f).unlink(missing_ok=True)


def test_without_the_build_plugin_the_app_build_says_so(monkeypatch):
    monkeypatch.delitem(sys.modules, "yashome_plugins.build", raising=False)
    me._app_holder.clear()
    r = TestClient(core.app).post("/api/family/app/build", headers=HOUSE)
    assert r.status_code == 503 and "build" in r.json()["detail"]
    me._app_holder.clear()


def test_widgets_get_their_channels_state_when_it_changes(phones, monkeypatch):
    c = TestClient(core.app)
    seen, hidden = phones["seen"], phones["hidden"]
    items = [{"dev": seen, "code": "state"}, {"dev": hidden, "code": "state"}, {"dev": "nope", "code": "x"},
             {"dev": seen, "code": "state"}]
    r = c.put("/api/family/me/widgets", headers=_phone(PHONE), json={"items": items})
    assert r.json()["items"] == [{"dev": seen, "code": "state"}]          # rights, existing devices, once
    assert c.put("/api/family/me/widgets", headers=HOUSE, json={"items": []}).status_code == 404
    assert c.put("/api/family/me/widgets", headers=_phone(PHONE), json={"items": [items[0]] * 41}).status_code == 422
    sent, tickles = [], []
    monkeypatch.setattr(me, "_send_live", lambda uid, msg: sent.append((uid, msg)) or True)
    monkeypatch.setattr(core, "_status_copy", lambda dev, default: {"online": True, "values": {"state": state["v"]}})
    state = {"v": True}
    me._widget_sent.clear()
    me._widget_tick()
    assert [(u, {k: v for k, v in m.items() if k != "t"}) for u, m in sent] == [
        ("kid", {"cmd": "state", "values": {seen: {"online": True, "state": True}}})]
    assert sent[0][1]["t"] > 0                                              # the house time orders the updates
    me._widget_tick()
    assert len(sent) == 1                                                   # nothing changed: nothing sent
    state["v"] = False
    me._widget_tick()
    assert sent[-1][1]["values"][seen]["state"] is False
    monkeypatch.setattr(me, "_send_live", lambda uid, msg: False)          # the app screen is not open
    monkeypatch.setattr(me, "_bridge", lambda m, p, b=None, timeout=20: tickles.append((p, b)) or {"id": "x"})
    monkeypatch.setitem(me._settings, "widget_push", False)
    state["v"] = True
    me._widget_tick()
    assert tickles == []                                                    # the house did not allow it
    monkeypatch.setitem(me._settings, "widget_push", True)
    monkeypatch.setattr(me, "WIDGET_INSTANT", False)                       # the old rule: only a lock is instant
    me._widget_tick()
    state["v"] = False
    me._widget_tick()                                                       # within the gap: held back
    assert tickles == [("/tickle", {"uid": "kid", "priority": "normal"})]   # a tickle carries no data at all
    monkeypatch.setattr(me, "_is_lock", lambda dev: True)                   # a lock's state is wanted at once
    state["v"] = True
    me._tickled["kid"] = time.time() - me.TICKLE_HIGH_GAP_S - 1
    me._widget_tick()
    assert tickles[-1] == ("/tickle", {"uid": "kid", "priority": "high"})
    monkeypatch.setattr(me, "CLOUD", False)
    me._tickled.clear(), me._tickle_open.clear()
    me._widget_tick()
    n = len(tickles)
    state["v"] = False
    me._widget_tick()
    assert len(tickles) == n                                                # no cloud, no tickle


def test_a_failed_tickle_is_retried_and_one_phone_never_blocks_another(phones, monkeypatch):
    seen = phones["seen"]
    monkeypatch.setattr(core, "_status_copy", lambda dev, default: {"online": True, "values": {"state": True}})
    data = dict(me._load())
    data["kid"] = {**data["kid"], "widgets": [{"dev": seen, "code": "state"}]}
    data["adult"] = {**data["adult"], "widgets": [{"dev": seen, "code": "state"}]}
    me._save(data)
    monkeypatch.setattr(me, "_send_live", lambda uid, msg: False)
    monkeypatch.setitem(me._settings, "widget_push", True)
    calls = []

    def bridge(m, p, b=None, timeout=20):
        calls.append(b["uid"])
        if len(calls) == 1:
            raise core.HTTPException(502, "no token yet")                  # the first phone fails ...
        return {"id": "x"}
    monkeypatch.setattr(me, "_bridge", bridge)
    me._widget_sent.clear()
    me._widget_tick()
    assert len(calls) == 2                                                  # ... the second still gets its tickle
    first = calls[0]
    assert not me._tickle_open.get(first) and me._tickle_open.get(calls[1])
    me._tickled[first] = 0
    me._widget_tick()
    assert calls[-1] == first and me._tickle_open.get(first)               # tried again after the gap


def test_a_phone_pulls_what_changed_once_over_https(phones, monkeypatch):
    c = TestClient(core.app)
    seen = phones["seen"]
    state = {"v": True}
    monkeypatch.setattr(core, "_status_copy", lambda dev, default: {"online": True, "values": {"state": state["v"]}})
    c.put("/api/family/me/widgets", headers=_phone(PHONE), json={"items": [{"dev": seen, "code": "state"}]})
    assert c.get("/api/family/me/pull", headers=HOUSE).status_code == 404                      # not a family phone
    me._tickle_open["kid"] = True
    r = c.get("/api/family/me/pull", headers=_phone(PHONE)).json()
    assert r["state"] == {seen: {"online": True, "state": True}} and r["cmds"] == [] and r["t"] > 0
    assert "kid" not in me._tickle_open                                                          # the tickle was answered
    assert c.get("/api/family/me/pull", headers=_phone(PHONE)).json()["state"] == r["state"]      # not acknowledged: the same change comes again
    assert c.get(f"/api/family/me/pull?ack={r['t']}", headers=_phone(PHONE)).json()["state"] == {}   # applied: nothing changed since
    state["v"] = False
    r = c.get("/api/family/me/pull", headers=_phone(PHONE)).json()
    assert r["state"][seen]["state"] is False
    assert c.get("/api/family/me/pull?all=1", headers=_phone(PHONE)).json()["state"][seen]["state"] is False       # the whole state on request
    me._pending_cmds["kid"] = [{"cmd": "update", "code": 99}]
    assert c.get("/api/family/me/pull", headers=_phone(PHONE)).json()["cmds"] == [{"cmd": "update", "code": 99}]
    assert c.get("/api/family/me/pull", headers=_phone(PHONE)).json()["cmds"] == []               # handed over once


def test_a_pull_names_the_address_for_the_phones_only_when_the_family_page_sets_one(phones, monkeypatch):
    c = TestClient(core.app)
    monkeypatch.setattr(me, "_settings", {})
    assert "srv" not in c.get("/api/family/me/pull", headers=_phone(PHONE)).json()              # the house's own LAN default: a typed VPN address stays
    monkeypatch.setattr(me, "_settings", {"server_url": "https://house.example/"})
    assert c.get("/api/family/me/pull", headers=_phone(PHONE)).json()["srv"] == "https://house.example"   # the app moves to it (0.32)


def test_an_update_waits_for_the_pull_when_no_live_link_is_open(phones, tmp_path, monkeypatch):
    monkeypatch.setattr(me, "_apk_file", lambda: _apk(tmp_path, 31))
    tickles = []
    monkeypatch.setattr(me, "_send_live", lambda uid, msg: False)
    monkeypatch.setattr(me, "_bridge", lambda m, p, b=None, timeout=20: tickles.append((p, b)) or {"id": "x"})
    meta = me._apk_meta()
    assert me._send_update("kid", meta) is True
    assert tickles == [("/tickle", {"uid": "kid", "priority": "high"})] and me._pending_cmds["kid"][0]["code"] == 31
    c = TestClient(core.app)
    cmds = c.get("/api/family/me/pull?v=30", headers=_phone(PHONE)).json()["cmds"]
    assert cmds[0]["cmd"] == "update" and cmds[0]["path"] == "/plugins/family/yash.apk" and me._versions["kid"] == 30
    monkeypatch.setattr(me, "CLOUD", False)
    assert me._send_update("kid", meta) is False                                                 # no cloud, no live link: nothing can be sent


def test_a_phone_note_goes_to_the_house_log_and_changes_nothing_else(phones, people, monkeypatch):
    lines = []
    monkeypatch.setattr(core.log, "info", lambda m, *a: lines.append(m))
    c = TestClient(core.app)
    monkeypatch.setattr(me, "_notes", {})
    assert c.post("/api/family/me/note", headers=HOUSE, json={"text": "x"}).status_code == 404
    assert c.post("/api/family/me/note", headers=_phone(FULL), json={"text": ""}).status_code == 422
    assert c.post("/api/family/me/note", headers=_phone(FULL), json={"text": "x" * 201}).status_code == 422
    assert c.post("/api/family/me/note", headers=_phone(PHONE), json={"text": "x"}).status_code in (401, 403)   # restricted
    assert c.post("/api/family/me/note", headers=_phone(FULL), json={"text": "auto-rotate\nON"}).json() == {"ok": True}
    assert "[family] note adult: auto-rotate ON" in lines and "adult" not in me._net_home
    for _ in range(me.NOTE_MAX - 1):
        assert c.post("/api/family/me/note", headers=_phone(FULL), json={"text": "x"}).status_code == 200
    assert c.post("/api/family/me/note", headers=_phone(FULL), json={"text": "x"}).status_code == 429   # per phone, per hour


def test_a_network_report_is_a_state_home_holds_until_a_report_says_otherwise(phones, people, monkeypatch):
    _wifi(monkeypatch, gateways={"192.0.2.1"})
    c = TestClient(core.app)
    assert c.post("/api/family/me/report", headers=HOUSE, json={"gw": "192.0.2.1"}).status_code == 404
    assert c.post("/api/family/me/report", headers=_phone(PHONE), json={"gw": "192.0.2.1", "ssid": "x", "v": 30}).json() == {"ok": True}
    assert me._net_home["kid"][0] is True and me._versions["kid"] == 30
    me._started = 0.0
    t = time.time()
    me._lan_seen.clear()
    me._presence_tick(t)
    assert core.get_status("person_kid")["values"]["home"] is True
    me._presence_tick(t + 5 * me.PRESENCE_GRACE_S)                                              # hours without a report: still home
    assert core.get_status("person_kid")["values"]["home"] is True
    c.post("/api/family/me/report", headers=_phone(PHONE), json={"gw": ""})                       # left the Wi-Fi: the network is gone
    assert me._net_home["kid"][0] is False
    me._presence_tick(t + 6 * me.PRESENCE_GRACE_S)
    me._presence_tick(t + 8 * me.PRESENCE_GRACE_S)
    assert core.get_status("person_kid")["values"]["home"] is False
    me._net_home["kid"] = [True, t]                                                               # a home report older than 24 h is not believed
    me._lan_seen.clear()
    me._presence_tick(t + me.NET_HOME_MAX_S + 10)
    assert core.get_status("person_kid")["values"]["home"] is False


def test_a_live_socket_from_a_home_address_does_not_undo_a_newer_away_report(phones, people, monkeypatch):
    _wifi(monkeypatch, gateways={"192.0.2.1"})
    monkeypatch.setattr(me, "_is_home_addr", lambda host: True)
    me._lan_seen.clear(), me._away_since.clear()
    c = TestClient(core.app)
    with c.websocket_connect("/api/family/live", headers=_phone(PHONE)) as ws:
        assert "kid" in me._lan_seen                                                              # the socket alone says home
        ws.send_json({"gw": ""})                                                                  # an away report over the same link
        ws.send_json({"ack": 1})                                                                  # a later frame must not flip it back
        ws.send_json({"ack": 1})
        assert "kid" in me._away_since and "kid" not in me._lan_seen
        c.get("/api/family/me/pull", headers={**_phone(PHONE), "X-Net-Gw": ""})                   # an away pull keeps it away
        assert "kid" not in me._lan_seen
        c.get("/api/family/me/pull", headers={**_phone(PHONE), "X-Net-Gw": "192.0.2.1"})          # a home pull says otherwise
        assert "kid" not in me._away_since and "kid" in me._lan_seen
    me._away_since["kid"] = 1.0
    me._lan_seen.clear()
    with c.websocket_connect("/api/family/live", headers=_phone(PHONE)):
        pass
    assert "kid" not in me._lan_seen                                                              # the connect and the close respect it too


def test_the_home_hold_is_24_hours_and_survives_a_restart(phones, people, monkeypatch, tmp_path):
    assert me.NET_HOME_MAX_S == 24 * 3600
    f = tmp_path / "net_home.json"
    monkeypatch.setattr(me, "_net_file", lambda: f)
    t = time.time()
    f.write_text(json.dumps({"kid": [True, t], "bad1": "x", "bad2": [1, t], "bad3": [True]}))
    me._net_load()                                                                               # what a restart reads back
    assert me._net_home == {"kid": [True, t]}                                                    # entries of another shape are not believed
    me._started = 0.0
    me._lan_seen.clear()
    me._presence_tick(t + me.NET_HOME_MAX_S - 10)
    assert core.get_status("person_kid")["values"]["home"] is True
    me._presence_tick(t + me.NET_HOME_MAX_S + 10 + me.PRESENCE_GRACE_S)                          # past the hour and the grace: away
    assert core.get_status("person_kid")["values"]["home"] is False
    f.write_text("[1, 2]")
    me._net_home.clear()
    me._net_load()                                                                               # a file of the wrong shape is ignored
    assert me._net_home == {}


def test_rights_cut_and_a_live_reconnect(phones, monkeypatch):
    seen, ctl = phones["seen"], phones["ctl"]
    monkeypatch.setattr(core, "_status_copy", lambda dev, default: {"online": True, "values": {"state": True}})
    data = dict(me._load())
    data["kid"] = {**data["kid"], "widgets": [{"dev": seen, "code": "state"}, {"dev": ctl, "code": "state"}]}
    me._save(data)
    sent = []
    monkeypatch.setattr(me, "_send_live", lambda uid, msg: sent.append((uid, msg)) or True)
    me._widget_sent.clear()
    me._widget_tick()
    assert set(sent[-1][1]["values"]) == {seen, ctl}
    data = dict(me._load())
    data["kid"] = {**data["kid"], "rights": {seen: "view"}}                  # Rights now hide ctl
    me._save(data)
    me._widget_sent.pop("kid")                                             # as a live reconnect does
    me._widget_tick()
    assert set(sent[-1][1]["values"]) == {seen}


def test_the_same_phone_paired_again_keeps_its_entry_and_its_old_token_dies(phones, monkeypatch):
    c = TestClient(core.app)
    monkeypatch.setattr(me, "CLOUD", False)
    dev = "and-0123456789abcdef"
    tok1 = c.post("/api/family/claim", json={"code": _qr_code(c, "phone_1", full=True), "device_id": dev}).json()["tok"]
    assert "rights" not in me._load()["phone_1"]                              # full
    r = c.post("/api/family/claim", json={"code": _qr_code(c, "phone_2"), "device_id": dev}).json()
    assert r["uid"] == "phone_1" and "phone_2" not in me._load()             # the QR's new name did not stay
    assert me._load()["phone_1"]["rights"] == {}                            # the restricted QR's rights, not the old full ones
    assert "fresh" not in me._load()["phone_1"]
    assert me._uid_of(r["tok"]) == "phone_1" and me._uid_of(tok1) is None
    assert me._load()["phone_1"]["device_id"] == dev
    other = c.post("/api/family/claim", json={"code": _qr_code(c, "phone_3"), "device_id": "and-fedcba9876543210"}).json()
    assert other["uid"] == "phone_3"                                           # another phone, its own entry
    assert c.post("/api/family/claim", json={"code": "yfc_x", "device_id": "not-an-id"}).status_code == 422
    _qr_code(c, "phone_4")
    code = _qr_code(c, "phone_4")                                            # a lost QR made again: still new
    r = c.post("/api/family/claim", json={"code": code, "device_id": dev}).json()
    assert r["uid"] == "phone_1"
    code = _qr_code(c, "phone_3")                                            # a listed phone's New QR
    assert c.post("/api/family/claim", json={"code": code, "device_id": dev}).status_code == 409
    assert "phone_3" in me._load() and me._load()["phone_1"]["sha256"] == me._sha(r["tok"])


def test_an_old_app_without_an_id_still_pairs_and_is_flagged(phones, monkeypatch):
    c = TestClient(core.app)
    monkeypatch.setattr(me, "CLOUD", False)
    assert c.post("/api/family/claim", json={"code": _qr_code(c, "old1")}).status_code == 200
    rows = {p["uid"]: p for p in c.get("/api/family", headers=HOUSE).json()}
    assert rows["old1"]["old_app"] is True and rows["kid"]["old_app"] is True


def test_delete_takes_the_rules_that_ring_it_and_a_firebase_only_row(phones, monkeypatch):
    c = TestClient(core.app)
    src = sorted(core.DEVICES)[0]
    assert c.put(f"/api/bindings/{src}/probe/single", headers=HOUSE, json={"target": "kid", "action": "ring"}).status_code == 200
    assert c.put(f"/api/bindings/{src}/probe/double", headers=HOUSE, json={"target": "adult", "action": "ring"}).status_code == 200
    assert c.post("/api/family/revoke", headers=_phone(FULL), json={"name": "kid"}).status_code == 401
    monkeypatch.setattr(core, "TOKEN_CHECKS", core.TOKEN_CHECKS + [lambda tok, m, path: tok == "another-key"])
    assert c.post("/api/family/revoke", headers=_phone("another-key"), json={"name": "kid"}).status_code == 403
    assert c.post("/api/family/revoke", headers=HOUSE, json={"name": "kid"}).json() == {"ok": True, "rules": 1}
    assert "single" not in core._bindings.get(src, {}).get("probe", {}) and "kid" not in me._load()
    assert core._bindings[src]["probe"]["double"]["target"] == "adult"            # another phone's rule stays
    assert ("POST", "/revoke", {"uid": "kid"}) in phones["calls"]
    assert c.post("/api/family/revoke", headers=HOUSE, json={"name": "ghost"}).status_code == 200
    assert ("POST", "/revoke", {"uid": "ghost"}) in phones["calls"]


def test_a_rename_keeps_the_phones_rights_screen_and_widgets(phones):
    data = me._load()
    data["kid"] = {**data["kid"], "rights": {"lamp_old": "view"}, "screen": [{"dev": "lamp_old", "code": "s"}],
                   "widgets": [{"dev": "lamp_old", "code": "s"}, {"dev": "x", "code": "y"}]}
    me._save(data)
    me._widget_sent["kid"] = {"lamp_old": {"s": 1}}
    me._rights_rename("lamp_old", "lamp_new")
    assert "kid" not in me._widget_sent                              # the whole state goes out again
    kid = me._load()["kid"]
    assert kid["rights"] == {"lamp_new": "view"} and kid["screen"][0]["dev"] == "lamp_new"
    assert [w["dev"] for w in kid["widgets"]] == ["lamp_new", "x"]


@pytest.fixture
def people():
    me._lan_seen.clear(), me._person_state.clear()
    yield
    me._presence_forget()


def test_a_phone_on_the_home_network_is_at_home_and_the_event_follows(phones, people):
    me._started = 0.0
    t = 1_000_000.0
    me._lan_seen["kid"] = t
    me._presence_tick(t)
    st = core.get_status("person_kid")
    assert st["values"]["home"] is True and "last_event" not in st          # the first reading is no arrival
    me._presence_tick(t + me.PRESENCE_GRACE_S - 1)
    assert core.get_status("person_kid")["values"]["home"] is True          # within the grace: still home
    me._presence_tick(t + me.PRESENCE_GRACE_S + 1)
    st = core.get_status("person_kid")
    assert st["values"]["home"] is False and st["last_event"]["value"] == "departure"
    me._lan_seen["kid"] = t + 2000
    me._presence_tick(t + 2001)
    assert core.get_status("person_kid")["last_event"]["value"] == "arrival"
    assert "person_adult" in core.DEVICES                                    # every paired phone has its device


def test_a_restart_does_not_fake_an_absence_or_an_arrival(phones, people):
    me._started = 1_000_000.0
    me._presence_tick(1_000_100.0)                  # just started, no link yet: nothing is published
    assert "home" not in core.get_status("person_kid").get("values", {})
    me._lan_seen["kid"] = 1_000_200.0
    me._presence_tick(1_000_201.0)
    assert core.get_status("person_kid")["values"]["home"] is True and "last_event" not in core.get_status("person_kid")


def test_a_revoked_phone_loses_its_presence_device(phones, people):
    me._started = 0.0
    me._presence_tick(2_000_000.0)
    assert "person_kid" in core.DEVICES
    me.DB.write_text(json.dumps({"adult": {"sha256": me._sha(FULL), "name": "Adult"}}))
    me._cache["mtime"] = None
    me._presence_tick(2_000_001.0)
    assert "person_kid" not in core.DEVICES and "person_adult" in core.DEVICES
    assert "kid" not in me._person_state


@pytest.mark.parametrize("host, home", [("192.0.2.9", True), ("::ffff:192.0.2.5", True), ("198.51.100.3", False),
                                         ("8.8.8.8", False), ("not-an-address", False), ("", False)])
def test_only_a_home_address_counts(host, home, monkeypatch):
    monkeypatch.setattr(me, "HOME_NETS", [me.ipaddress.ip_network("192.0.2.0/24")])
    assert me._is_home_addr(host) is home


def test_the_wifi_gateway_the_app_reports_counts_only_when_it_is_the_house_router(monkeypatch, people):
    _wifi(monkeypatch, gateways={"192.0.2.1"})
    assert me._note_gateway("kid", "192.0.2.1") is True and me._lan_seen["kid"] > 0
    me._lan_seen.clear()
    for bad in ("198.51.100.1", "", None, 5, "192.0.2.1x"):
        assert me._note_gateway("kid", bad) is False
    assert "kid" not in me._lan_seen


def test_the_wifi_name_must_match_too_when_the_house_sets_it(monkeypatch, people):
    _wifi(monkeypatch, gateways={"192.0.2.1"})
    _wifi(monkeypatch, ssids={"house-wifi"})
    monkeypatch.setattr(me, "_reported", {})
    assert me._note_gateway("kid", "192.0.2.1", "house-wifi") is True
    me._lan_seen.clear()
    for gw, ssid in (("192.0.2.1", "elsewhere"), ("192.0.2.1", ""), ("192.0.2.1", None), ("198.51.100.1", "house-wifi")):
        assert me._note_gateway("kid", gw, ssid) is False, (gw, ssid)         # the gateway alone is not enough
    assert "kid" not in me._lan_seen
    _wifi(monkeypatch, ssids=set())
    assert me._note_gateway("kid", "192.0.2.1", None) is True                  # no names configured: the gateway counts as before


def test_the_phones_own_wifi_address_counts_with_the_name_not_the_socket(monkeypatch, people):
    _wifi(monkeypatch, gateways=set())
    monkeypatch.setattr(me, "HOME_NETS", [me.ipaddress.ip_network("192.0.2.0/24")])
    _wifi(monkeypatch, ssids={"house-wifi"})
    monkeypatch.setattr(me, "_reported", {})
    assert me._note_gateway("kid", "192.0.2.1", "house-wifi", "192.0.2.77") is True        # its Wi-Fi address in the house subnet + the name
    me._lan_seen.clear()
    for gw, ssid, ip in (("192.0.2.1", "elsewhere", "192.0.2.77"),        # the same subnet in another place: the name differs
                         ("192.0.2.1", "", "192.0.2.77"),                 # no location permission: no name, no home
                         ("198.51.100.1", "house-wifi", "198.51.100.40"),  # the right name on a foreign subnet
                         ("", "house-wifi", ""),                          # off Wi-Fi
                         ("", "house-wifi", "203.0.113.7")):                 # a VPN's address is never a Wi-Fi address
        assert me._note_gateway("kid", gw, ssid, ip) is False, (gw, ssid, ip)
    assert "kid" not in me._lan_seen


def test_a_change_of_the_reported_network_is_logged_once(monkeypatch, people, caplog):
    import logging
    _wifi(monkeypatch, gateways={"192.0.2.1"})
    _wifi(monkeypatch, ssids=set())
    monkeypatch.setattr(me, "_reported", {})
    with caplog.at_level(logging.INFO):
        me._note_gateway("kid", "192.0.2.1", "w1")
        me._note_gateway("kid", "192.0.2.1", "w1")
        me._note_gateway("kid", "198.51.100.9", "w2")
    lines = [r.getMessage() for r in caplog.records if "reports Wi-Fi gateway" in r.getMessage()]
    assert len(lines) == 2 and "'w1'" in lines[0] and "not the house network" in lines[1]


def test_the_report_carries_the_ssid_to_the_check(monkeypatch, people):
    seen = []
    monkeypatch.setattr(me, "_note_gateway", lambda uid, gw, ssid=None: seen.append((uid, gw, ssid)))
    monkeypatch.setattr(me, "_note_gateway", lambda uid, gw, ssid=None, wifi_ip=None, ssid_known=True: seen.append((uid, gw, ssid, wifi_ip)))
    me._note_report("kid", {"gw": "192.0.2.1", "ssid": "house-wifi", "wifi_ip": "192.0.2.77", "v": 33})
    assert seen == [("kid", "192.0.2.1", "house-wifi", "192.0.2.77")]


def _axml(code, name):
    """A tiny binary AndroidManifest.xml: <manifest versionCode=code versionName=name>."""
    strs = ["manifest", "versionCode", "versionName", name]
    pool = b"".join(struct.pack("<H", len(x)) + x.encode("utf-16-le") + b"\0\0" for x in strs)
    offs, o = [], 0
    for x in strs:
        offs.append(o)
        o += 2 + 2 * len(x) + 2
    pool += b"\0" * (-len(pool) % 4)
    start = 28 + 4 * len(strs)
    sp = struct.pack("<HHIIIIII", 1, 28, start + len(pool), len(strs), 0, 0, start, 0) \
        + b"".join(struct.pack("<I", x) for x in offs) + pool
    attrs = struct.pack("<iiiHBBI", -1, 1, -1, 8, 0, 0x10, code) + struct.pack("<iiiHBBI", -1, 2, 3, 8, 0, 3, 3)
    st = struct.pack("<HHIII", 0x102, 16, 16 + 20 + len(attrs), 1, 0xFFFFFFFF) \
        + struct.pack("<iiHHHHHH", -1, 0, 20, 20, 2, 0, 0, 0) + attrs
    body = sp + st
    return struct.pack("<HHI", 3, 8, 8 + len(body)) + body


def _apk(tmp, code, name="1.2.3"):
    f = tmp / "yash.apk"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("AndroidManifest.xml", _axml(code, name))
    return f


def test_the_apk_version_is_read_from_its_manifest(tmp_path, monkeypatch):
    f = _apk(tmp_path, 31)
    monkeypatch.setattr(me, "_apk_file", lambda: f)
    meta = me._apk_meta()
    assert (meta["code"], meta["name"], meta["size"]) == (31, "1.2.3", f.stat().st_size)
    assert meta["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    f.write_bytes(b"not a zip")
    assert me._apk_meta() is None                                  # unreadable: no update goes out


def test_the_app_row_gets_the_built_version(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "_apk_file", lambda: None)
    c = TestClient(core.app)
    assert "apk_name" not in c.get("/api/family/app/build", headers=HOUSE).json()
    f = _apk(tmp_path, 31)
    monkeypatch.setattr(me, "_apk_file", lambda: f)
    r = c.get("/api/family/app/build", headers=HOUSE).json()
    assert (r["apk_name"], r["apk_code"]) == ("1.2.3", 31)


def test_only_the_dashboard_updates_and_only_an_older_phone_gets_the_command(phones, people, tmp_path, monkeypatch):
    monkeypatch.setattr(me, "_apk_file", lambda: _apk(tmp_path, 31))
    sent = []
    monkeypatch.setattr(me, "_send_live", lambda uid, msg: sent.append((uid, msg)) or True)
    me._versions.clear()
    me._versions.update(kid=30, adult=31)
    c = TestClient(core.app)
    assert c.post("/api/family/update", headers=_phone(FULL), json={}).status_code == 401
    r = c.post("/api/family/update", headers=HOUSE, json={}).json()
    assert r["sent"] == ["kid"] and r["skipped"] == ["adult"] and r["code"] == 31
    uid, msg = sent[0]
    assert uid == "kid" and msg["cmd"] == "update" and msg["code"] == 31 and msg["path"] == "/plugins/family/yash.apk"
    assert len(msg["sha256"]) == 64 and msg["size"] > 0
    assert c.post("/api/family/update", headers=HOUSE, json={"name": "nobody"}).status_code == 404
    me._versions.pop("kid")                                        # an app that never reported: nothing is sent
    assert c.post("/api/family/update", headers=HOUSE, json={"name": "kid"}).json()["sent"] == []


def test_an_automatic_update_is_rate_limited(phones, tmp_path, monkeypatch):
    """An older app is told of the built one by itself (the phone still asks before installing), at most once in UPDATE_GAP_S."""
    monkeypatch.setattr(me, "_apk_file", lambda: _apk(tmp_path, 31))
    calls = []
    monkeypatch.setattr(me, "_send_update", lambda uid, meta: calls.append(uid))
    me._update_sent.clear()
    assert me.AUTO_UPDATE is True
    me._note_report("kid", {"v": True})                           # a bool is no version
    me._note_report("kid", {"v": 31})                             # up to date
    assert calls == []
    me._note_report("kid", {"v": 30})
    import time as _t
    _t.sleep(0.2)
    assert calls == ["kid"]
    me._update_sent["kid"] = _t.time()
    me._note_report("kid", {"v": 30})
    _t.sleep(0.2)
    assert calls == ["kid"]                                         # within the gap: once


def test_the_family_list_says_who_is_home(phones, people):
    c = TestClient(core.app)
    assert c.get("/api/family", headers=HOUSE).json()[0]["home"] is None            # nothing published yet
    me._person_state["kid"], me._lan_seen["kid"] = True, 1_700_000_000.0
    row = [p for p in c.get("/api/family", headers=HOUSE).json() if p["uid"] == "kid"][0]
    assert row["home"] is True and row["home_ts"] == 1_700_000_000_000


def test_a_phone_without_a_live_link_borrows_a_presence_device(phones, people, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "presence", {"id": "presence", "name": "Phones", "transport": "presence"})
    c = TestClient(core.app)
    assert c.put("/api/family/kid/presence", headers=HOUSE, json={"device": "nope"}).status_code == 400
    assert c.put("/api/family/kid/presence", headers=_phone(FULL), json={"device": "presence"}).status_code in (401, 403)
    assert c.put("/api/family/kid/presence", headers=HOUSE, json={"device": "presence"}).json() == {"uid": "kid", "presence": "presence"}
    assert [p for p in c.get("/api/family", headers=HOUSE).json() if p["uid"] == "kid"][0]["presence"] == "presence"
    assert {"id": "presence", "name": "Phones"} in c.get("/api/family/presence/devices", headers=HOUSE).json()
    real = core.status_copy
    shown = {"home": True}
    monkeypatch.setattr(core, "status_copy",
                        lambda dev, default=None: {"values": dict(shown)} if dev == "presence" else real(dev, default))
    me._started = 0.0
    me._lan_seen.clear(), me._person_state.clear()
    me._presence_tick(3_000_000.0)
    assert me._person_state["kid"] is True                  # the device says home: no live link needed
    shown["home"] = False
    me._presence_tick(3_000_000.0 + me.PRESENCE_GRACE_S + 1)
    assert me._person_state["kid"] is False                 # and after the grace, away


def test_ask_the_house_and_push_to_talk_are_for_the_house_and_full_access_phones(phones, monkeypatch):
    vm = sys.modules["yashome_plugins.voice"]
    monkeypatch.setattr(vm, "_vad_post", lambda path, body, timeout=25.0: {"said": "ok"})
    c = TestClient(core.app)
    for path, body in (("/api/voice/ask", {"text": "light"}), ("/api/voice/ask/confirm", {"token": "t", "yes": False}),
                       ("/api/voice/chat", {"text": "hi"}), ("/api/voice/tts", {"text": "hi"}), ("/api/voice/say", {"text": "hi", "target": "x"})):
        assert c.post(path, headers=_phone(PHONE), json=body).status_code == 401, path          # rights of one device: refused by the core
    assert c.post("/api/voice/ask", headers=_phone(FULL), json={"text": "light"}).status_code == 200
    assert c.post("/api/voice/ask", headers=HOUSE, json={"text": "light"}).status_code == 200
    assert c.post("/api/voice/say", headers=_phone(FULL), json={"text": "hi", "target": "x"}).status_code == 401   # the speakers: the house only (no use_route)
    assert c.post("/api/voice/ptt?sat=x", headers=_phone(PHONE), content=b"\x00\x00").status_code == 401


def test_every_report_of_the_app_is_announced_for_the_voice_card(monkeypatch, people):
    seen = []
    monkeypatch.setattr(me, "_announce_seen", lambda uid: seen.append(uid))
    me._note_report("kid", {"gw": "198.51.100.1", "v": 34})
    assert seen == ["kid"]


def test_every_pull_is_a_network_report_too(phones, people, monkeypatch):
    _wifi(monkeypatch, gateways={"192.0.2.1"})
    _wifi(monkeypatch, ssids={"house-wifi"})
    c = TestClient(core.app)
    pull = lambda **h: c.get("/api/family/me/pull", headers={**_phone(PHONE), **h})      # noqa: E731
    pull(**{"X-Net-Gw": "192.0.2.1", "X-Net-Ssid": "house-wifi", "X-Net-Ssid-Known": "1"})
    assert me._net_home["kid"][0] is True
    pull(**{"X-Net-Gw": "192.0.2.1", "X-Net-Ssid": "other", "X-Net-Ssid-Known": "1"})            # the same address, another name: away
    assert me._net_home["kid"][0] is False
    pull(**{"X-Net-Gw": "192.0.2.1", "X-Net-Ssid": "", "X-Net-Ssid-Known": "0"})                  # no location access: the address alone
    assert me._net_home["kid"][0] is True
    pull(**{"X-Net-Gw": "", "X-Net-Ssid-Known": "0"})                                              # left the Wi-Fi: the next pull says so
    assert me._net_home["kid"][0] is False
    assert me.NET_HOME_MAX_S == 24 * 3600                                                          # only a day without contact drops home


def test_an_unanswered_tickle_backs_off_and_a_revoked_phone_forgets_its_home(phones, people, monkeypatch):
    monkeypatch.setattr(me, "WIDGET_INSTANT", False)                       # the lock rule (10 s, doubling) is the one without FAMILY_WIDGET_INSTANT
    seen = phones["seen"]
    monkeypatch.setattr(core, "_status_copy", lambda dev, default: {"online": True, "values": {"state": True, "locked": True}})
    monkeypatch.setattr(me, "_is_lock", lambda dev: True)
    data = dict(me._load())
    data["kid"] = {**data["kid"], "widgets": [{"dev": seen, "code": "state"}]}
    me._save(data)
    monkeypatch.setattr(me, "_send_live", lambda uid, msg: False)
    monkeypatch.setitem(me._settings, "widget_push", True)
    monkeypatch.setattr(me, "_bridge", lambda m, p, b=None, timeout=20: {"id": "x"})
    me._widget_sent.clear(), me._widget_pend.clear(), me._tickled.clear(), me._tickle_open.clear(), me._tickle_n.clear()
    me._widget_tick()
    assert me._tickle_n["kid"] == 1
    for n, (young, old) in enumerate([(5, 15), (15, 25), (35, 45)], start=1):          # a phone that never answers: the gap is 10, 20, 40 s ...
        me._tickled["kid"] = time.time() - young
        me._widget_tick()
        assert me._tickle_n["kid"] == n
        me._tickled["kid"] = time.time() - old
        me._widget_tick()
        assert me._tickle_n["kid"] == n + 1
    me._net_home["kid"] = [True, time.time()]
    me._presence_forget(keep=())
    assert "kid" not in me._net_home


@pytest.mark.parametrize("url,cloud,warns", [("https://1.1.1.1:8443", True, False), ("http://192.0.2.5", True, True), ("http://198.51.100.5:8765", True, True),
                                             ("https://house.example.org", True, False), ("http://nas.local", True, True), ("http://192.0.2.5", False, False)])
def test_a_firebase_house_with_a_lan_only_server_address_is_warned(monkeypatch, url, cloud, warns):
    monkeypatch.setattr(me, "server_url", lambda: url)
    monkeypatch.setattr(me, "CLOUD", cloud)
    assert bool(me._server_url_warning()) is warns


def test_a_home_phone_stays_home_in_doze_and_a_positive_away_report_drops_it_at_once(phones, people, monkeypatch):
    _wifi(monkeypatch, gateways={"192.0.2.1"})
    me._started = 0.0
    me._note_gateway("kid", "192.0.2.1")
    t = time.time()
    me._presence_tick(t)
    assert core.get_status("person_kid")["values"]["home"] is True
    me._presence_tick(t + 5 * 3600)                                                               # 5 h of Doze, no contact: still home
    assert core.get_status("person_kid")["values"]["home"] is True
    me._note_gateway("kid", "")                                                                   # an away pull: away with no grace
    me._presence_tick(time.time() + 1)
    assert core.get_status("person_kid")["values"]["home"] is False


def test_network_headers_are_clipped_and_stripped_before_use(phones, people, monkeypatch):
    _wifi(monkeypatch, gateways={"192.0.2.1"})
    logged = []
    monkeypatch.setattr(core.log, "info", lambda m, *a, **k: logged.append(str(m)))
    c = TestClient(core.app)
    c.get("/api/family/me/pull", headers={**_phone(PHONE), "X-Net-Gw": "192.0.2.1", "X-Net-Ssid": "ab%0d%0aINJECT%00" + "x" * 500})
    ssid = me._reported["kid"][1]
    assert len(ssid) <= 64 and ssid.startswith("abINJECT") and not any(ord(ch) < 32 for ch in ssid)
    assert not any("\n" in m or "\r" in m or len(m) > 400 for m in logged if "reports Wi-Fi" in m)


def test_with_instant_widgets_every_change_of_a_widget_channel_is_a_high_tickle_coalesced_per_phone(phones, monkeypatch):
    seen = phones["seen"]
    state = {"v": True}
    monkeypatch.setattr(core, "_status_copy", lambda dev, default: {"online": True, "values": {"state": state["v"]}})
    data = dict(me._load())
    data["kid"] = {**data["kid"], "widgets": [{"dev": seen, "code": "state"}]}
    me._save(data)
    tickles = []
    monkeypatch.setattr(me, "_send_live", lambda uid, msg: False)
    monkeypatch.setattr(me, "_bridge", lambda m, p, b=None, timeout=20: tickles.append(b) or {"id": "x"})
    monkeypatch.setitem(me._settings, "widget_push", True)
    monkeypatch.setattr(me, "_is_lock", lambda dev: False)                  # not a lock: it is instant all the same
    assert me.WIDGET_INSTANT is True and me.TICKLE_INSTANT_GAP_S == 5     # on by default
    me._widget_sent.clear(), me._widget_pend.clear(), me._tickled.clear(), me._tickle_open.clear(), me._tickle_n.clear()
    me._widget_tick()
    assert tickles == [{"uid": "kid", "priority": "high"}]
    me._tickle_open.clear(), me._tickle_n.clear()                           # the phone pulled
    state["v"] = False
    me._widget_tick()
    assert len(tickles) == 1                                                # a second change inside 5 s rides the next pull
    me._tickled["kid"] = time.time() - 6
    me._widget_tick()
    assert len(tickles) == 2 and tickles[-1]["priority"] == "high"


def test_a_new_qr_keeps_all_but_the_pairing(phones, monkeypatch):
    monkeypatch.setattr(me, "CLOUD", False)
    c = TestClient(core.app)
    code = _qr_code(c, "new1", full=True)
    assert c.post("/api/family/claim", json={"code": code, "device_id": "and-0123456789ab"}).status_code == 200
    data = dict(me._load())
    data["new1"] = {**data["new1"], "presence": "phone_mac", "widgets": [{"dev": "lamp"}], "screen": ["lamp"]}
    me._save(data)
    _qr_code(c, "new1")
    d = me._load()["new1"]
    assert (d["presence"], d["widgets"], d["screen"]) == ("phone_mac", [{"dev": "lamp"}], ["lamp"])
    assert "sha256" not in d and "device_id" not in d and d["claim_sha"]       # the claiming phone sets its own id


def test_a_member_is_named_in_any_language_its_id_is_made_and_a_rename_keeps_the_id(phones, monkeypatch):
    c = TestClient(core.app)
    monkeypatch.setattr(me, "CLOUD", False)
    monkeypatch.setattr(me, "server_url", lambda: "https://house.example")
    for name in ("Миша", "Маша", "Ann Lee"):   # i18n
        assert c.post("/api/family/pair", headers=HOUSE, json={"name": name}).status_code == 200
    db = me._load()
    assert (db["member"]["name"], db["member2"]["name"], db["annlee"]["name"]) == ("Миша", "Маша", "Ann Lee")   # i18n
    assert c.post("/api/family/pair", headers=HOUSE, json={"name": "миша"}).status_code == 409             # one name, one member   # i18n
    assert c.post("/api/family/pair", headers=HOUSE, json={"uid": "member"}).status_code == 200             # New QR: id and name stay
    assert me._load()["member"]["name"] == "Миша" and "member3" not in me._load()   # i18n
    assert c.put("/api/family/kid/name", headers=HOUSE, json={"name": "Малыш"}).json() == {"uid": "kid", "name": "Малыш"}   # i18n
    assert me._load()["kid"]["name"] == "Малыш" and me._load()["kid"]["rights"]                         # rights untouched   # i18n
    assert c.put("/api/family/kid/name", headers=HOUSE, json={"name": "Маша"}).status_code == 409   # i18n
    assert c.put("/api/family/nobody/name", headers=HOUSE, json={"name": "X"}).status_code == 404
    assert c.put("/api/family/kid/name", headers=_phone(PHONE), json={"name": "X"}).status_code in (401, 403)


def test_a_phone_asks_which_app_the_house_has_built(phones, people, tmp_path, monkeypatch):
    """The app's "Check for an update": the same command an update from the dashboard sends; a phone only."""
    c = TestClient(core.app)
    monkeypatch.setattr(me, "_apk_file", lambda: None)
    assert c.get("/api/family/me/app", headers=_phone(FULL)).status_code == 404       # nothing built yet
    monkeypatch.setattr(me, "_apk_file", lambda: _apk(tmp_path, 31))
    r = c.get("/api/family/me/app", headers=_phone(FULL)).json()
    assert (r["cmd"], r["code"], r["name"], r["path"]) == ("update", 31, "1.2.3", "/plugins/family/yash.apk") and len(r["sha256"]) == 64
    assert c.get("/api/family/me/app", headers=HOUSE).status_code == 404                # the dashboard is not a phone


def test_the_house_learns_its_wifi_from_a_phone_at_home(phones, tmp_path, monkeypatch):
    """Nothing to set: a report from the house's network teaches the gateway and the name; then a phone behind a VPN is home by its
    gateway, and a router elsewhere with the same address is not (another name)."""
    monkeypatch.setattr(me, "_wifi_file", lambda: tmp_path / "home_wifi.json")
    monkeypatch.setattr(me, "HOME_NETS", [me.ipaddress.ip_network("192.0.2.0/24")])
    assert me._home_wifi() == {"gateways": [], "ssids": []}
    assert me._note_gateway("kid", "192.0.2.1", "house-wifi", "192.0.2.77") is True
    assert me._home_wifi() == {"gateways": ["192.0.2.1"], "ssids": ["house-wifi"]}
    assert me._note_gateway("kid", "192.0.2.1", "house-wifi", "") is True                  # behind a VPN: the gateway says home
    assert me._note_gateway("kid", "192.0.2.1", "cafe", "") is False                     # the same address elsewhere: not home
    assert me._note_gateway("kid", "198.51.100.1", "cafe", "203.0.113.9") is False
    assert me._home_wifi() == {"gateways": ["192.0.2.1"], "ssids": ["house-wifi"]}       # nothing learned away from home
    assert me._note_gateway("kid", "192.0.2.1", "friend", "192.0.2.78") is False         # a friend's router, same subnet and address
    assert me._home_wifi() == {"gateways": ["192.0.2.1"], "ssids": ["house-wifi"]}       # teaches nothing: its name failed
    assert me._note_gateway("kid", "192.0.2.1", "friend", "") is False


def test_a_report_that_failed_the_name_teaches_no_gateway(phones, tmp_path, monkeypatch):
    monkeypatch.setattr(me, "_wifi_file", lambda: tmp_path / "home_wifi.json")
    monkeypatch.setattr(me, "HOME_NETS", [me.ipaddress.ip_network("192.0.2.0/24")])
    me._wifi_file().write_text(json.dumps({"gateways": [], "ssids": ["house-wifi"]}))
    assert me._note_gateway("kid", "192.0.2.1", "friend", "192.0.2.78") is False
    assert me._note_gateway("kid", "192.0.2.1", "", "192.0.2.78", ssid_known=False) is True   # no name to read: address alone
    assert me._home_wifi() == {"gateways": [], "ssids": ["house-wifi"]}                     # neither teaches the gateway
    assert me._note_gateway("kid", "192.0.2.1", "house-wifi", "192.0.2.78") is True
    assert me._home_wifi() == {"gateways": ["192.0.2.1"], "ssids": ["house-wifi"]}


def test_the_house_network_without_bind_address(phones, monkeypatch):
    monkeypatch.setattr(me, "HOME_NETS", [])
    monkeypatch.setattr(me, "_BIND", "")
    monkeypatch.setattr(me, "_settings", {})
    assert me._is_home_addr("198.51.100.20") is False                   # no source: no guessed range
    monkeypatch.setattr(me, "_settings", {"server_url": "http://192.0.2.2:8765"})
    assert me._is_home_addr("192.0.2.40") is True and me._is_home_addr("198.51.100.20") is False
    monkeypatch.setattr(me, "_settings", {"server_url": "https://home.example.org"})
    assert me._is_home_addr("192.0.2.40") is False


def test_the_address_for_the_phones_is_the_houses_own_unless_set(phones, monkeypatch):
    c = TestClient(core.app)
    monkeypatch.setattr(me, "_BIND", "192.0.2.10")
    monkeypatch.setattr(me, "_settings", {})
    monkeypatch.setattr(core, "save_settings", lambda: None)
    assert me.server_url() == "http://192.0.2.10:8765"
    r = c.put("/api/family/settings", headers=HOUSE, json={"server_url": "https://house.example.org/"}).json()
    assert me.server_url() == "https://house.example.org" and r["server_url_default"] == "http://192.0.2.10:8765"
    assert c.put("/api/family/settings", headers=HOUSE, json={"server_url": "ftp://x"}).status_code == 400
    assert c.put("/api/family/settings", headers=_phone(FULL), json={"server_url": ""}).status_code in (401, 403)
    c.put("/api/family/settings", headers=HOUSE, json={"server_url": ""})
    assert me.server_url() == "http://192.0.2.10:8765" and "server_url" not in me._settings


def test_a_full_phone_uses_the_house_but_does_not_set_it_up(phones):
    """Full access = use: it reads every GET and changes only core.use_route routes; set-up stays with the house token."""
    c = TestClient(core.app)
    dev = sorted(core.DEVICES)[0]
    assert c.get("/api/devices", headers=_phone(FULL)).status_code == 200
    assert c.get(f"/api/devices/{dev}/status", headers=_phone(FULL)).status_code == 200
    for method, path in (("POST", f"/api/devices/{dev}/control"), ("POST", "/api/rules/run"), ("POST", "/api/rules/enable"),
                         ("POST", "/api/rules/delete"), ("PUT", f"/api/bindings/{dev}/x/single"), ("POST", f"/api/automation/{dev}/x"),
                         ("PUT", f"/api/favorites/{dev}/x"), ("DELETE", f"/api/favorites/{dev}/x"), ("POST", "/api/favorites/reorder"),
                         ("POST", "/api/voice/ask"), ("POST", "/api/voice/ptt"), ("POST", "/api/voice/record/delete"),
                         ("POST", "/api/ir/remotes/r/buttons/b/send"), ("POST", "/api/player/p/play"), ("DELETE", "/api/player/recent"),
                         ("PUT", "/api/family/me/screen"), ("WS", "/ws"), ("WS", "/api/family/live")):
        assert me._allowed(None, method, path), (method, path)
    for method, path in (("POST", "/api/panels/pairing"), ("PUT", "/api/panels/p1/layout"), ("POST", "/api/zigbee/device/remove"),
                         ("POST", "/api/esphome/remove"), ("POST", "/api/devices/relink"), ("POST", f"/api/devices/{dev}/rename"),
                         ("POST", "/api/ir/remotes"), ("DELETE", "/api/ir/remotes/r"), ("PUT", "/api/rooms/r1"), ("POST", "/api/rooms"),
                         ("POST", "/api/computers"), ("POST", "/api/computers/c/roles/build"), ("PUT", "/api/voice/config"),
                         ("PUT", "/api/player/folders"), ("POST", "/api/connect/start"), ("DELETE", "/api/history"),
                         ("PUT", "/api/map"), ("POST", "/api/resync"), ("PUT", "/api/wiring"), ("WS", "/plugins/zigbee/ui/api")):
        assert not me._allowed(None, method, path), (method, path)
    # the same through the core: refused before the route runs, the house token goes on
    assert c.post("/api/panels/pairing", headers=_phone(FULL), json={"minutes": 1}).status_code == 401
    assert c.post("/api/panels/pairing", headers=HOUSE, json={"off": True}).status_code == 200
    assert c.post(f"/api/devices/{dev}/control", headers=_phone(FULL), json={"code": "nope", "value": True}).status_code != 401


def test_use_route_marks_only_a_route_of_the_plug_in_itself():
    import app as core_app
    route = next(r for r in core_app.app.router.routes if getattr(r, "path", "") == "/api/devices/{dev_id}/control")
    assert id(route) in core_app.USE_ROUTES
    assert core_app.is_use_route("POST", "/api/devices/x/control") and not core_app.is_use_route("POST", "/api/devices/x/rename")
    assert core_app.is_use_route("GET", "/api/anything") and core_app.is_use_route("WS", "/ws")


def test_the_qr_landing_page_is_served_without_a_token():
    """The QR's link opens /plugins/family/static/pair.html on a phone that has no key yet (it was a 404: .html was no public type)."""
    r = TestClient(core.app).get("/plugins/family/static/pair.html")
    assert r.status_code == 200 and "<html" in r.text.lower()
    assert TestClient(core.app).get("/plugins/family/__init__.py").status_code in (401, 404)   # the package's code stays closed
