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
