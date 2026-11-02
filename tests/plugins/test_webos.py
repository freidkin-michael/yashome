"""webos: the TVs of devices.json become devices; commands reach SSAP; the poller keeps a
just-commanded power value until the TV confirms it."""
import asyncio
import sys

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.webos"]


@pytest.fixture
def ssap(monkeypatch):
    calls = []

    async def command(ip, key, code, value=None):
        calls.append((ip, key, code, value))

    state = {"volume": 12, "mute": False}

    async def status(ip, key):
        if state.get("down"):
            raise OSError("no SSAP")
        return dict(state)
    monkeypatch.setattr(me.webos, "command", command)
    monkeypatch.setattr(me.webos, "status", status)
    monkeypatch.setattr(me.webos, "wol", lambda mac: calls.append(("wol", mac)))
    monkeypatch.setattr(me, "_webos_kick", lambda *a, **k: None)
    return calls, state


def test_tvs_of_devices_json_are_devices():
    assert core.DEVICES["tv_test"]["transport"] == "webos"
    assert core.DEVICES["tv_test"]["_ctl"]["power"]["kind"] == "switch"
    assert {"webos"} <= set(core.TRANSPORTS) and "webos" in core.ASYNC_CONTROL


def test_commands_reach_the_tv(ssap):
    calls, _ = ssap
    tv = core.DEVICES["tv_test"]
    asyncio.run(me._webos_control(tv, "input", "HDMI 1"))
    asyncio.run(me._webos_control(tv, "power", True))
    assert calls == [("192.0.2.10", "test-client-key", "input", "HDMI_1"), ("wol", "00:11:22:33:44:55")]


def test_the_remote_s_keys_are_tv_key_presses_that_reach_the_pointer_socket(ssap, monkeypatch):
    pressed = []

    async def button(ip, key, name):
        pressed.append((ip, name))
    monkeypatch.setattr(me.webos, "button", button)
    tv = core.DEVICES["tv_test"]
    keys = {x["code"]: x for x in tv["controls"] if x["kind"] == "tv_key"}
    assert {"key_up", "key_ok", "key_back", "key_home"} <= set(keys) and "key_source" not in keys     # inputs: the input list
    old = {"id": "x", "controls": [{"kind": "setting_enum", "code": "media", "range": ["play"]}]}      # a devices.json of before
    assert me._with_keys(old)["controls"][0]["range"] == ["play", "pause", "stop", "rewind", "fast_forward"]
    assert old["controls"][0]["range"] == ["play"]                                                       # the entry is not touched
    asyncio.run(me._webos_control(tv, "key_ok", True))             # the core's press (CONTROL_KINDS tv_key): test_tv
    assert pressed == [("192.0.2.10", "ENTER")]
    with pytest.raises(HTTPException) as e:
        asyncio.run(me._webos_control(tv, "key_eject", True))
    assert e.value.status_code == 400


def test_a_button_goes_through_the_pointer_socket_the_tv_hands_out(monkeypatch):
    import json
    import websockets
    got = []

    async def go():
        async def pointer(ws):
            got.append(await ws.recv())

        async def main(ws):
            async for msg in ws:
                m = json.loads(msg)
                if m["type"] == "register":
                    await ws.send(json.dumps({"type": "registered", "payload": {"client-key": "k"}}))
                elif m["uri"].endswith("getPointerInputSocket"):
                    await ws.send(json.dumps({"type": "response", "payload": {"socketPath": f"ws://127.0.0.1:{pp}/x"}}))
        async with websockets.serve(pointer, "127.0.0.1", 0) as ps, websockets.serve(main, "127.0.0.1", 0) as ms:
            pp = ps.sockets[0].getsockname()[1]
            monkeypatch.setattr(me.webos, "PORT", ms.sockets[0].getsockname()[1])
            await me.webos.button("127.0.0.1", "k", "ENTER")
            for _ in range(50):
                if got:
                    break
                await asyncio.sleep(0.02)
    asyncio.run(go())
    assert got == ["type:button\nname:ENTER\n\n"]
    with pytest.raises(ValueError):
        asyncio.run(me.webos.button("127.0.0.1", "k", "rm -rf"))


def test_unpaired_and_unknown_are_refused(ssap):
    with pytest.raises(HTTPException) as e:
        asyncio.run(me._webos_control(core.DEVICES["tv_unpaired"], "power", False))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        asyncio.run(me._webos_control(core.DEVICES["tv_test"], "nope", 1))
    assert e.value.status_code == 400


def test_poll_keeps_commanded_power_until_confirmed(ssap):
    _, state = ssap
    tv = core.DEVICES["tv_test"]
    assert asyncio.run(me._webos_poll_one(tv)) is True
    assert core._status_copy("tv_test", {})["values"] == {"power": True, "volume": 12, "mute": False}
    asyncio.run(me._webos_poll_one(tv, expect=False))      # still answering after power_off
    assert core._status_copy("tv_test", {})["values"]["power"] is True
    state["down"] = True
    asyncio.run(me._webos_poll_one(tv, expect=False))
    st = core._status_copy("tv_test", {})
    assert st["values"]["power"] is False and st["online"] is True


def test_a_silent_tv_cannot_hang_the_call(monkeypatch):
    """A TV that accepts the socket and then says nothing (the reported hang)."""
    import time
    import websockets

    async def run():
        async def mute(ws):
            async for _ in ws:                     # reads the register request, never answers
                pass
        async with websockets.serve(mute, "127.0.0.1", 0) as srv:
            monkeypatch.setattr(me.webos, "PORT", srv.sockets[0].getsockname()[1])
            t0 = time.monotonic()
            with pytest.raises(asyncio.TimeoutError):
                await me.webos._open("127.0.0.1", "k", timeout=0.5)
            return time.monotonic() - t0
    assert asyncio.run(run()) < 2


def test_status_is_bounded():
    import inspect
    src = inspect.getsource(me.webos)
    assert "@bounded(20)\nasync def status" in src and "@bounded(20)\nasync def command" in src
    assert "proxy=None" in src


def test_unreadable_keys_file_blocks_pairing(monkeypatch):
    monkeypatch.setattr(me, "_keys_broken", True)
    with pytest.raises(HTTPException) as e:
        asyncio.run(me.webos_pair("tv_test"))
    assert e.value.status_code == 503


def test_bad_devices_json_entry_is_skipped(monkeypatch):
    monkeypatch.setattr(me, "_read_json", lambda path, default: [
        {"id": "bad/id", "transport": "webos"}, {"id": "tv_extra", "transport": "webos", "controls": []}])
    try:
        got = [d["id"] for d in me._register_tvs()]
        assert got == ["tv_extra"]
    finally:
        core.DEVICES.pop("tv_extra", None)


def test_poll_keeps_changes_made_while_it_waited(ssap, monkeypatch):
    real = me.webos.status

    async def slow_status(ip, key):
        core._store_status("tv_test", {"online": True, "values": {"power": True, "input": "HDMI_2"}})
        return await real(ip, key)
    monkeypatch.setattr(me.webos, "status", slow_status)
    asyncio.run(me._webos_poll_one(core.DEVICES["tv_test"]))
    assert core._status_copy("tv_test", {})["values"]["input"] == "HDMI_2"


def test_the_poller_keeps_the_power_a_catch_up_series_waits_for(ssap, monkeypatch):
    core._store_status("tv_test", {"online": True, "values": {"power": False}})
    me._webos_expect["tv_test"] = False                    # power_off sent, SSAP still answers

    async def sleep(s):
        raise asyncio.CancelledError
    monkeypatch.setattr(me.asyncio, "sleep", sleep)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(me._webos_poller())
    finally:
        me._webos_expect.pop("tv_test", None)
    assert core._status_copy("tv_test", {})["values"]["power"] is False


def test_a_new_series_keeps_its_entry_when_the_old_one_is_cancelled(monkeypatch):
    monkeypatch.setattr(me, "_WEBOS_KICK_DELAYS", (100,))

    async def run():
        monkeypatch.setattr(core, "_MAIN_LOOP", asyncio.get_running_loop())
        me._webos_kick("tv_test", True)
        await asyncio.sleep(0)
        me._webos_kick("tv_test", False)
        for _ in range(3):
            await asyncio.sleep(0)
        task = me._webos_kicks.get("tv_test")
        assert task is not None and not task.done() and me._webos_expect["tv_test"] is False
        task.cancel()
        await asyncio.sleep(0)
    asyncio.run(run())
    assert "tv_test" not in me._webos_kicks and "tv_test" not in me._webos_expect


def test_the_client_keys_live_in_plugin_state():
    assert me.WEBOS_KEYS_PATH.parent.name == "webos" and me.WEBOS_KEYS_PATH.parent.parent.name == "plugin-state"


def test_the_device_lists_hold_while_the_mqtt_thread_adds_and_drops_devices():
    import threading
    import time
    panels = sys.modules["yashome_plugins.panels"]
    stop, errors = threading.Event(), []

    def churn():
        i = 0
        while not stop.is_set():
            core.DEVICES[f"churn{i % 50}"] = {"id": "x", "transport": "mqtt", "controls": []}
            core.DEVICES.pop(f"churn{(i + 25) % 50}", None)
            i += 1
    t = threading.Thread(target=churn, daemon=True)
    t.start()
    try:
        end = time.time() + 1.0
        while time.time() < end:
            for fn in (me._tvs, panels._command_topics):
                try:
                    fn()
                except RuntimeError as e:
                    errors.append(e)
    finally:
        stop.set()
        t.join()
        for i in range(50):
            core.DEVICES.pop(f"churn{i}", None)
    assert errors == []


def _relay():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("wol_relay2", pathlib.Path(me.__file__).parent / "host" / "wol-relay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_wol_relay_answers_only_a_wol_datagram():
    relay = _relay()
    pkt = relay.packet(b"WOL 00:11:22:33:44:55")
    assert len(pkt) == 102 and pkt[:6] == b"\xff" * 6 and pkt[6:12] == bytes.fromhex("001122334455")
    for bad in (b"WOL zz:11:22:33:44:55", b"RESOLVE 00:11:22:33:44:55", b"WOL", b"\xff\xfe",
                b"WOL 00:11:22", b"WOL 00:11:22:33:44:55:66", b"WOL 00:11-22:33:44:55"):
        assert relay.packet(bad) is None, bad
    assert relay.main() == 2                                    # not through its socket unit: it says so


def test_the_relay_is_rate_limited_and_keeps_sending_after_a_failure():
    relay = _relay()
    out, logs = [], []

    def sendto(pkt, dest):
        if dest[0] == "10.0.0.0":
            raise OSError("down")
        out.append(dest)
    r = relay.Relay(["10.0.0.0", "172.16.0.0"], sendto, logs.append)
    ok = b"WOL 00:11:22:33:44:55"
    assert r.handle(ok, 0.0) == 2 and out == [("172.16.0.0", 9), ("172.16.0.0", 7)]
    assert "failed" in logs[-1]
    assert r.handle(b"junk", 0.5) == 0
    for i in range(9):
        r.handle(ok, 1.0 + i / 100)
    assert r.handle(ok, 2.0) == 0                               # the 11th in 10 s
    assert r.handle(ok, 10.5) == 2                              # the window slides


def test_wol_goes_to_the_host_socket_first_and_a_failing_direct_send_still_relays(monkeypatch, tmp_path):
    import socket as _s
    ssap = sys.modules[me.__name__ + "._ssap"] if (me.__name__ + "._ssap") in sys.modules else me.webos
    hs = ssap._wol._hostsock()
    monkeypatch.setattr(hs, "DIR", str(tmp_path))
    sent = []

    class S:
        def sendto(self, data, dest):
            sent.append(dest)
            raise OSError("no broadcast here")
    ssap._wol_send(S(), b"x", "00:11:22:33:44:55", "255.255.255.255")         # no relay here: the broadcast only
    assert sent == [("255.255.255.255", 9), ("255.255.255.255", 7)]
    srv = _s.socket(_s.AF_UNIX, _s.SOCK_DGRAM)
    srv.bind(str(tmp_path / "wol.sock"))
    try:
        ssap._wol_send(S(), b"x", "00:11:22:33:44:55", "255.255.255.255")
        assert srv.recv(64) == b"WOL 00:11:22:33:44:55" and len(sent) == 4
    finally:
        srv.close()
    (tmp_path / "wol.sock").unlink()
    srv = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)                  # a socket that does not take it: logged, the broadcast goes
    srv.bind(str(tmp_path / "wol.sock"))
    try:
        ssap._wol_send(S(), b"x", "00:11:22:33:44:55", "255.255.255.255")
        assert len(sent) == 6
    finally:
        srv.close()


def test_power_on_needs_a_valid_mac(monkeypatch):
    import asyncio
    monkeypatch.setitem(me._webos_keys, "tvm", "key")
    woke = []
    monkeypatch.setattr(me.webos, "wol", woke.append)
    for mac in (None, "", "zz:11:22:33:44:55", "00:11:22:33:44", "00::11:22:33:44:55", "0011:2233:4455",
                "001:122:334:455", "00:11-22:33:44:55"):
        with pytest.raises(core.HTTPException) as e:
            asyncio.run(me._webos_control({"id": "tvm", "name": "TV", "mac": mac, "host": "192.0.2.40"}, "power", True))
        assert e.value.status_code == 400
    monkeypatch.setattr(me, "_webos_kick", lambda *a, **k: None)
    asyncio.run(me._webos_control({"id": "tvm", "name": "TV", "mac": "00-11-22-33-44-55", "host": "192.0.2.40"}, "power", True))
    asyncio.run(me._webos_control({"id": "tvm", "name": "TV", "mac": " 001122334455\n", "host": "192.0.2.40"}, "power", True))
    assert woke == ["00:11:22:33:44:55"] * 2


def test_a_tv_that_is_off_says_so_at_once_and_power_on_still_goes_out(ssap, monkeypatch):
    import asyncio
    calls, _state = ssap
    monkeypatch.setitem(me._webos_keys, "tvo", "key")
    cfg = {"id": "tvo", "name": "TV", "mac": "00:11:22:33:44:55", "host": "192.0.2.40"}
    monkeypatch.setitem(core._status_cache, "tvo", {"online": True, "values": {"power": False}})
    with pytest.raises(core.HTTPException) as e:
        asyncio.run(me._webos_control(cfg, "volume", 10))
    assert e.value.status_code == 409 and "off or unreachable" in e.value.detail and calls == []
    asyncio.run(me._webos_control(cfg, "power", True))
    assert calls == [("wol", "00:11:22:33:44:55")]

    async def down(*a, **k):
        raise OSError("connect call failed")
    monkeypatch.setattr(me.webos, "command", down)
    core._status_cache["tvo"] = {"online": True, "values": {"power": True}}       # the poll thought it on
    with pytest.raises(core.HTTPException) as e:
        asyncio.run(me._webos_control(cfg, "volume", 10))
    assert e.value.status_code == 409


def test_the_webos_wake_on_lan_is_the_core_one():
    assert me.webos.wol is core.wake_on_lan                                  # tools/wol.py, loaded once


def test_the_lg_page_lists_a_tv_and_an_edit_writes_its_entry_and_the_card_lists(monkeypatch, tmp_path):
    import json
    import os
    from fastapi.testclient import TestClient
    house = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
    tv = next(d for d in core.DEVICES.values() if d.get("transport") == "webos")
    entry = {"id": tv["id"], "name": tv["name"], "transport": "webos", "category": "tv", "host": "192.0.2.42", "mac": "aa:bb:cc:dd:ee:ff",
             "_webos_inputs": {"PC": "HDMI_1"}, "_webos_apps": {"YouTube": "youtube.leanback.v4"},
             "controls": [{k: v for k, v in x.items() if not k.startswith("_")} for x in tv["controls"]]}
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([entry]))
    monkeypatch.setattr(me, "DEVICES_PATH", path)
    c = TestClient(core.app)
    assert c.get("/api/webos/tvs").status_code in (401, 403)
    assert any(t["id"] == tv["id"] for t in c.get("/api/webos/tvs", headers=house).json()["tvs"])
    body = {"name": "Салон TV", "host": "192.0.2.43", "mac": "aa:bb:cc:dd:ee:ff", "inputs": {"PC": "HDMI_1", "PS5": "HDMI_3"}, "apps": {}}   # i18n
    assert c.put(f"/api/webos/tvs/{tv['id']}", headers=house, json={**body, "mac": "nope"}).status_code == 400
    try:
        got = c.put(f"/api/webos/tvs/{tv['id']}", headers=house, json=body).json()["tv"]
        assert got["inputs"] == {"PC": "HDMI_1", "PS5": "HDMI_3"} and got["host"] == "192.0.2.43" and got["mac"] == "aa:bb:cc:dd:ee:ff"
        saved = json.loads(path.read_text())[0]
        assert saved["name"] == "Салон TV" and {x["code"]: x.get("range") for x in saved["controls"]}.get("input", ["PC", "PS5"]) == ["PC", "PS5"]   # i18n
        assert all(x["range"] == ["PC", "PS5"] for x in core.DEVICES[tv["id"]]["controls"] if x["code"] == "input")
    finally:
        c.put(f"/api/webos/tvs/{tv['id']}", headers=house, json={"name": tv["name"], "host": "192.0.2.42", "mac": "", "inputs": {"PC": "HDMI_1"}, "apps": {"YouTube": "youtube.leanback.v4"}})


def test_removing_an_lg_tv_drops_its_entry_and_key_and_a_wrong_id_is_404(monkeypatch, tmp_path):
    import json
    import os
    from fastapi.testclient import TestClient
    house = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([{"id": "lg-gone", "transport": "webos", "name": "x"}, {"id": "other", "transport": "zigbee"}]))
    monkeypatch.setattr(me, "DEVICES_PATH", path)
    monkeypatch.setattr(me, "WEBOS_KEYS_PATH", tmp_path / "keys.json")
    monkeypatch.setattr(me, "_webos_keys", {"lg-gone": "k", "tv-two": "k2"})
    c = TestClient(core.app)
    assert c.delete("/api/webos/tvs/lg-gone").status_code in (401, 403)
    assert c.delete("/api/webos/tvs/nobody", headers=house).status_code == 404
    assert c.delete("/api/webos/tvs/lg-gone", headers=house).json() == {"ok": True}
    assert json.loads(path.read_text()) == [{"id": "other", "transport": "zigbee"}] and json.loads((tmp_path / "keys.json").read_text()) == {"tv-two": "k2"}
