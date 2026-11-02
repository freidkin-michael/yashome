"""tizen: a fake Samsung TV in-process (REST + UPnP over HTTP, the remote channel over plain ws) - pairing keeps the token
(600, never logged), each control reaches the TV as the right key or call, the state is debounced, power-on is WoL then the
IR fallback, the settings are checked. No real device is ever addressed (192.0.2.x, 00:11:22:33:44:55)."""
import asyncio
import http.server
import json
import logging
import os
import socket
import sys
import threading

import pytest
import websockets
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.tizen"]
tv = me.tv
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
TOKEN = "fake-token-77"


def _free():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeTV:
    """What a 2018 Tizen TV answers: /api/v2/ (no PowerState), the applications POST, RenderingControl, the channel."""
    def __init__(self):
        self.on, self.volume, self.mute = True, 20, False
        self.keys, self.apps, self.tokens = [], [], set()
        self.allow = True
        self.http_port, self.ws_port = _free(), _free()
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b""):
                self.send_response(code)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not fake.on:
                    return self._send(503)
                self._send(200, json.dumps({"device": {"OS": "Tizen", "name": "Fake TV", "TokenAuthSupport": "true", "wifiMac": "00:11:22:33:44:55"}}).encode())

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode()
                if not fake.on:
                    return self._send(503)
                if self.path.startswith("/api/v2/applications/"):
                    fake.apps.append(self.path.rsplit("/", 1)[1])
                    return self._send(200, b"{}")
                act = self.headers.get("SOAPACTION", "").split("#")[-1].strip('"')
                if act == "GetVolume":
                    return self._send(200, f"<CurrentVolume>{fake.volume}</CurrentVolume>".encode())
                if act == "GetMute":
                    return self._send(200, f"<CurrentMute>{int(fake.mute)}</CurrentMute>".encode())
                if act == "SetVolume":
                    fake.volume = int(body.split("<DesiredVolume>")[1].split("<")[0])
                    return self._send(200, b"<ok/>")
                if act == "SetMute":
                    fake.mute = body.split("<DesiredMute>")[1].split("<")[0] == "1"
                    return self._send(200, b"<ok/>")
                self._send(404)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.http_port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def handler(ws):
            q = ws.request.path.split("?", 1)[1] if "?" in ws.request.path else ""
            tok = dict(p.split("=", 1) for p in q.split("&") if "=" in p).get("token", "")
            if tok and tok not in self.tokens:
                await ws.send(json.dumps({"event": "ms.channel.unauthorized"}))
                return
            if not tok:
                if not self.allow:
                    await ws.send(json.dumps({"event": "ms.channel.timeOut"}))
                    return
                self.tokens.add(TOKEN)
                tok = TOKEN
            await ws.send(json.dumps({"event": "ms.channel.connect", "data": {"token": tok}}))
            async for msg in ws:
                m = json.loads(msg)
                if m.get("method") == "ms.channel.emit" and m["params"].get("event") == "ed.installedApp.get":
                    await ws.send(json.dumps({"event": "ed.installedApp.get", "data": {"data": [
                        {"appId": "3201907018807", "name": "Netflix"}, {"appId": "bad id!", "name": "x"}, {"appId": "111", "name": "Kino"}]}}))
                    continue
                k = m["params"]["DataOfCmd"]
                self.keys.append(k)
                if k == "KEY_POWER":
                    self.on = False

        async def serve():
            quiet = logging.getLogger("fake-tv")
            quiet.setLevel(logging.WARNING)                       # the fake's own request log is not the plug-in's
            async with websockets.serve(handler, "127.0.0.1", self.ws_port, logger=quiet):
                ready.set()
                await asyncio.Future()
        threading.Thread(target=lambda: self.loop.run_until_complete(serve()), daemon=True).start()
        ready.wait(5)

    def close(self):
        self.httpd.shutdown()                                     # the ws thread is a daemon: it ends with the run


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeTV()
    monkeypatch.setattr(tv, "REST_PORT", f.http_port)
    monkeypatch.setattr(tv, "UPNP_PORT", f.http_port)
    monkeypatch.setattr(tv, "WS_PORT", f.ws_port)
    monkeypatch.setattr(tv, "WS_SCHEME", "ws")
    monkeypatch.setattr(me, "TOKENS_PATH", tmp_path / "tokens.json")
    monkeypatch.setattr(me, "_tokens", {})
    monkeypatch.setattr(me, "_tokens_broken", False)
    monkeypatch.setattr(me, "TVS", [])
    monkeypatch.setattr(me, "_seen", {})
    monkeypatch.setattr(me, "_expect", {})
    monkeypatch.setattr(me, "_last_off", {})
    monkeypatch.setattr(me, "_last_on", {})
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    woke = []
    monkeypatch.setattr(core, "wake_on_lan", woke.append)
    f.woke = woke
    yield f
    me.TVS[:] = []
    me._sync()
    f.close()


def _add(c, **kw):
    body = {"id": "tv_t", "name": "Tizen bench TV", "host": "127.0.0.1", "mac": "00:11:22:33:44:55",
            "inputs": {"HDMI 1": "KEY_HDMI1"}, "apps": {"YouTube": "111299001912"}, **kw}
    return c.put(f"/api/tizen/tvs/{body['id']}", headers=HOUSE, json=body)


def test_pairing_keeps_the_token_private_and_never_logs_it(fake, caplog):
    c = TestClient(core.app)
    assert _add(c).status_code == 200
    with caplog.at_level(logging.DEBUG):
        r = c.post("/api/tizen/tvs/tv_t/pair", headers=HOUSE)
    assert r.status_code == 200, r.text
    assert json.loads(me.TOKENS_PATH.read_text()) == {"tv_t": TOKEN} and oct(me.TOKENS_PATH.stat().st_mode & 0o777) == "0o600"
    assert TOKEN not in caplog.text and TOKEN not in json.dumps(c.get("/api/tizen", headers=HOUSE).json())
    fake.allow = False                                           # the owner did not press Allow
    me._tokens.clear()
    assert c.post("/api/tizen/tvs/tv_t/pair", headers=HOUSE).status_code == 409


def test_each_control_reaches_the_tv(fake, caplog):
    caplog.set_level(logging.DEBUG)
    c = TestClient(core.app)
    _add(c)
    me._tokens["tv_t"] = TOKEN
    fake.tokens.add(TOKEN)
    me._store("tv_t", True, {})
    for code, val in (("media", "pause"), ("input", "HDMI 1"), ("volume", 35), ("mute", True), ("app", "YouTube")):
        r = c.post("/api/devices/tv_t/control", headers=HOUSE, json={"code": code, "value": val})
        assert r.status_code == 200, (code, r.text)
    assert fake.keys == ["KEY_PAUSE", "KEY_HDMI1"] and (fake.volume, fake.mute) == (35, True) and fake.apps == ["111299001912"]
    assert TOKEN not in caplog.text                                 # the URL with it is never logged, not even at DEBUG
    for code, val in (("app", "Netflix"), ("input", "HDMI 9"), ("media", "eject")):        # the core clamps a volume itself
        assert c.post("/api/devices/tv_t/control", headers=HOUSE, json={"code": code, "value": val}).status_code == 400


def test_power_off_is_one_key_only_while_on_and_a_bad_token_says_pair_again(fake):
    c = TestClient(core.app)
    _add(c)
    me._tokens["tv_t"] = "stale"
    me._store("tv_t", True, {})
    r = c.post("/api/devices/tv_t/control", headers=HOUSE, json={"code": "power", "value": False})
    assert r.status_code == 409 and "pair it again" in r.json()["detail"]
    me._tokens["tv_t"] = TOKEN
    fake.tokens.add(TOKEN)
    assert c.post("/api/devices/tv_t/control", headers=HOUSE, json={"code": "power", "value": False}).status_code == 200
    assert fake.keys == ["KEY_POWER"]
    me._store("tv_t", False, {})
    me._store("tv_t", False, {})
    assert c.post("/api/devices/tv_t/control", headers=HOUSE, json={"code": "power", "value": False}).status_code == 200
    assert fake.keys == ["KEY_POWER"]                            # already off: KEY_POWER would switch it ON


@pytest.fixture
def wake(fake, monkeypatch):
    """Fast timings: WoL repeats at 0.3 and 0.6 s, our off settles 0.5 s; IR presses recorded; no IR on key unless a test sets one."""
    monkeypatch.setitem(core.DEVICES, "ir_tv", {"id": "ir_tv", "name": "IR TV", "controls": [], "_ctl": {}})
    pressed = []

    async def control(dev, body):
        pressed.append((dev, body.code, body.value))
    monkeypatch.setattr(core, "control", control)
    monkeypatch.setattr(me, "WOL_REPEATS", (0.3, 0.6))
    monkeypatch.setattr(me, "OFF_SETTLE_S", 0.5)
    monkeypatch.setattr(me, "_waking", {})
    monkeypatch.setattr(me, "_last_on", {})
    monkeypatch.setattr(me, "_last_off", {})
    c = TestClient(core.app)
    assert _add(c).status_code == 200
    me._tokens["tv_t"] = TOKEN
    fake.tokens.add(TOKEN)
    fake.pressed = pressed
    return fake


def _off():
    me._store("tv_t", False, {})
    me._store("tv_t", False, {})


def test_without_an_ir_on_key_a_power_on_is_wol_repeated(wake):
    async def go(answers):
        _off()
        await me._control(core.DEVICES["tv_t"], "power", True)
        if answers:
            await asyncio.sleep(0.05)
            me._store("tv_t", True, {})                       # UPnP answers soon after the first WoL
        await asyncio.sleep(0.8)
    asyncio.run(go(answers=False))
    assert wake.woke == ["00:11:22:33:44:55"] * 3 and wake.pressed == [] and wake.keys == []
    asyncio.run(go(answers=True))                                 # it answered: the repeats go on (the screen)
    assert len(wake.woke) == 6 and wake.keys == []


def test_with_an_ir_on_key_a_power_on_is_that_key_and_one_wol_at_once_even_right_after_our_off(wake, monkeypatch):
    me._tv("tv_t")["ir_on"] = {"device": "ir_tv", "code": "ir_power_on"}

    async def off_then_on():
        me._store("tv_t", True, {})
        await me._control(core.DEVICES["tv_t"], "power", False)
        _off()
        me._store("tv_t", True, {})                               # the dark-network phase: no matter
        await me._control(core.DEVICES["tv_t"], "power", True)
        await asyncio.sleep(0.8)
    asyncio.run(off_then_on())
    assert wake.keys == ["KEY_POWER"] and wake.pressed == [("ir_tv", "ir_power_on", True)]   # the key only for the off
    assert wake.woke == ["00:11:22:33:44:55"] and "tv_t" not in me._waking                    # one WoL, no series

    async def broken(dev, body):
        raise me.HTTPException(409, "the blaster is offline")
    monkeypatch.setattr(core, "control", broken)
    asyncio.run(off_then_on())                                    # the IR key fails: the WoL still goes
    assert len(wake.woke) == 2


def test_the_repeats_stop_at_a_power_off_or_when_the_remote_switches_it_off(wake):
    async def by_command():
        _off()
        await me._control(core.DEVICES["tv_t"], "power", True)
        await asyncio.sleep(0.05)
        me._store("tv_t", True, {})
        await me._control(core.DEVICES["tv_t"], "power", False)     # KEY_POWER: the repeats end
        await asyncio.sleep(0.8)
    asyncio.run(by_command())
    assert wake.woke == ["00:11:22:33:44:55"] and wake.keys == ["KEY_POWER"]

    async def by_remote():
        me._last_off.clear()
        _off()
        await me._control(core.DEVICES["tv_t"], "power", True)
        await asyncio.sleep(0.05)
        me._store("tv_t", True, {})
        _off()                                                    # seen on, then silent twice: switched off by hand
        await asyncio.sleep(0.8)
    asyncio.run(by_remote())
    assert len(wake.woke) == 2 and wake.pressed == []


def test_a_power_on_right_after_our_power_off_waits_for_the_tv_to_settle(wake):
    me._tv("tv_t")["hold_off_s"] = 0                              # no hold: the plain WoL path

    async def go():
        me._store("tv_t", True, {})
        await me._control(core.DEVICES["tv_t"], "power", False)
        _off()
        t0 = asyncio.get_running_loop().time()
        await me._control(core.DEVICES["tv_t"], "power", True)
        while not wake.woke:
            await asyncio.sleep(0.02)
        return asyncio.get_running_loop().time() - t0
    assert asyncio.run(go()) >= 0.4                              # OFF_SETTLE_S 0.5 after the KEY_POWER


def test_the_state_is_debounced_and_a_command_s_power_is_kept_until_seen(fake):
    c = TestClient(core.app)
    _add(c)
    pw = lambda: core.status_copy("tv_t", {}).get("values", {}).get("power")   # noqa: E731
    me._store("tv_t", True, {"volume": 20})
    me._store("tv_t", None, {})
    assert pw() is True                                           # one miss: still on
    me._store("tv_t", None, {})
    assert pw() is False and core.status_copy("tv_t", {})["online"] is True       # off, never offline
    me._expect["tv_t"] = True
    me._store("tv_t", None, {})
    assert pw() is False and "tv_t" in me._expect                  # waiting for on: no other value written
    me._store("tv_t", True, {})
    assert pw() is True and "tv_t" not in me._expect


def test_the_poller_reads_upnp_backs_off_when_long_off_and_forgets_a_removed_tv(fake, monkeypatch):
    c = TestClient(core.app)
    _add(c)
    asyncio.run(me._poll_one(me._tv("tv_t")))
    st = core.status_copy("tv_t", {})["values"]
    assert (st["power"], st["volume"], st["mute"]) == (True, 20, False)
    fake.on = False
    asyncio.run(me._poll_one(me._tv("tv_t")))
    asyncio.run(me._poll_one(me._tv("tv_t")))
    assert core.status_copy("tv_t", {})["values"]["power"] is False
    me._seen["tv_t"]["off_since"] -= me.IDLE_AFTER_S + 1
    me._seen["tv_t"]["next"] = 0
    polled = []

    async def one(t):
        polled.append(t["id"])

    async def stop(_):
        raise asyncio.CancelledError
    monkeypatch.setattr(me, "_poll_one", one)
    monkeypatch.setattr(me.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(me._poller())
    assert polled == ["tv_t"] and me._seen["tv_t"]["next"] - me.time.monotonic() > me.POLL_S
    assert c.delete("/api/tizen/tvs/tv_t", headers=HOUSE).status_code == 200
    assert "tv_t" not in core.DEVICES and "tv_t" not in me._seen


def test_settings_are_checked_and_a_kept_pairing_is_adopted(fake):
    c = TestClient(core.app)
    for bad in ({"id": "Bad Id"}, {"host": "a b"}, {"mac": "nope"}, {"inputs": {"HDMI 1": "hdmi1"}}, {"apps": {"X": "a/b"}},
                {"ir_on": {"device": "nothing", "code": "x"}}):
        assert _add(c, **bad).status_code == 400, bad
    assert _add(c, id=next(iter(d for d in core.DEVICES if d != "tv_t"))).status_code == 409   # an id another device has
    assert _add(c).status_code == 200 and core.DEVICES["tv_t"]["wakes"] is True
    me._tokens["pending-pair"] = TOKEN
    g = c.get("/api/tizen", headers=HOUSE).json()
    assert g["unassigned"] == ["pending-pair"] and g["tvs"][0]["paired"] is False
    assert c.post("/api/tizen/tvs/tv_t/adopt", headers=HOUSE, json={"key": "pending-pair"}).status_code == 200
    assert me._tokens == {"tv_t": TOKEN} and c.get("/api/tizen", headers=HOUSE).json()["tvs"][0]["paired"] is True


def test_a_tv_that_accepts_and_never_answers_never_stalls_the_house(fake, monkeypatch):
    """Every call to the TV runs off the event loop or is bounded: a silent TV (TCP accepted, nothing said) on every port."""
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(16)
    port = silent.getsockname()[1]
    for k in ("REST_PORT", "UPNP_PORT", "WS_PORT"):
        monkeypatch.setattr(tv, k, port)
    c = TestClient(core.app)
    _add(c)
    me._tokens["tv_t"] = TOKEN
    me._store("tv_t", True, {})

    async def go():
        lag, stop = [0.0], asyncio.Event()

        async def beat():                                         # the loop's own heartbeat while the TV is silent
            while not stop.is_set():
                t0 = asyncio.get_running_loop().time()
                await asyncio.sleep(0.05)
                lag[0] = max(lag[0], asyncio.get_running_loop().time() - t0 - 0.05)
        b = asyncio.ensure_future(beat())
        calls = [me._poll_one(me._tv("tv_t")), me._control(core.DEVICES["tv_t"], "media", "pause"),
                 me._control(core.DEVICES["tv_t"], "volume", 30)]
        await asyncio.gather(*(asyncio.wait_for(x, 20) for x in calls), return_exceptions=True)
        stop.set()
        await b
        return lag[0]
    assert asyncio.run(go()) < 0.5
    silent.close()


def test_after_our_off_a_network_back_counts_only_after_hold_confirm_s_without_a_gap(wake, monkeypatch):
    pw = lambda: core.status_copy("tv_t", {}).get("values", {}).get("power")   # noqa: E731
    me._tv("tv_t")["hold_confirm_s"] = 0.3
    me._store("tv_t", True, {})
    asyncio.run(asyncio.sleep(0.35))                            # on for longer than hold_confirm_s before the off
    me._store("tv_t", True, {})

    async def off():
        await me._control(core.DEVICES["tv_t"], "power", False)
    asyncio.run(off())
    me._expect.pop("tv_t", None)
    me._store("tv_t", True, {"volume": 70})                     # UPnP still up for a moment after the key: still held
    assert "tv_t" in me._last_off
    _off()
    assert pw() is False
    for _ in range(4):                                          # the dark-network phase: UPnP answers, still held
        me._store("tv_t", True, {"volume": 70})
    assert pw() is False
    _off()                                                      # a gap starts the count again
    me._store("tv_t", True, {})
    asyncio.run(asyncio.sleep(0.2))
    me._store("tv_t", True, {})
    assert pw() is False
    asyncio.run(asyncio.sleep(0.15))
    me._store("tv_t", True, {})
    assert pw() is True and "tv_t" not in me._last_off          # 0.35 s without a gap: on (the remote, or it stayed on)
    me._last_off["tv_t"] = me.time.monotonic() - 10_000           # the hold is over (hold_off_s): one answer counts again
    _off()
    me._store("tv_t", True, {})
    assert pw() is True
    assert core.DEVICES["tv_t"]["no_enforce"] == ["power"]        # the watchdog never fights a TV that woke


def test_a_tv_name_is_a_device_name_and_one_name(fake, monkeypatch):
    c = TestClient(core.app)
    monkeypatch.setitem(core.DEVICES, "nm_lamp", {"id": "nm_lamp", "name": "Desk lamp", "controls": [], "_ctl": {}})
    assert _add(c, name="desk LAMP").status_code == 409
    assert _add(c).status_code == 200
    names = []
    monkeypatch.setattr(core, "set_device_name", lambda did, name: names.append((did, name)))
    assert _add(c, name=" Bedroom TV ").status_code == 200 and names == [("tv_t", "Bedroom TV")]



def test_the_ir_on_key_follows_a_rename_or_a_merge(monkeypatch):
    tz = sys.modules["yashome_plugins.tizen"]
    monkeypatch.setattr(tz, "TVS", [{"id": "s1", "ir_on": {"device": "tv_ir", "code": "ir_power_on"}}, {"id": "s2"}])
    monkeypatch.setattr(core, "save_settings", lambda: None)
    assert tz._merge_plan("tv_ir", "s1") == {"samsung: IR on key": 1}
    tz._on_rename("tv_ir", "s1")
    assert tz.TVS[0]["ir_on"] == {"device": "s1", "code": "ir_power_on"} and tz._merge_plan("tv_ir", "s1") == {}


def test_pairing_learns_the_mac_and_edit_offers_the_tv_s_own_apps(fake):
    c = TestClient(core.app)
    assert _add(c, mac="").status_code == 200
    r = c.get("/api/tizen/tvs/tv_t/apps", headers=HOUSE).json()
    assert not r["from_tv"] and r["why"] == "not paired" and {"id": "111299001912", "name": "YouTube"} in r["apps"]
    assert c.post("/api/tizen/tvs/tv_t/pair", headers=HOUSE).status_code == 200
    assert me._tv("tv_t")["mac"] == "00:11:22:33:44:55"                # read from the TV, nobody typed it
    r = c.get("/api/tizen/tvs/tv_t/apps", headers=HOUSE).json()
    ids = [a["id"] for a in r["apps"]]
    assert r["from_tv"] and ids[:2] == ["111", "3201907018807"] and "bad id!" not in ids and ids.count("3201907018807") == 1
