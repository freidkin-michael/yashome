"""climate: room_temp_from mirrors a live source only, is kept in its settings section, the route checks input."""
import asyncio
import sys

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.climate"]


@pytest.fixture
def ac(monkeypatch):
    core.DEVICES["t_ac"] = {"id": "t_ac", "name": "AC", "transport": "mqtt", "category": "climate", "controls": [], "_ctl": {}}
    core.DEVICES["t_sens"] = {"id": "t_sens", "name": "S", "transport": "mqtt", "category": "sensor", "controls": [], "_ctl": {}}
    saved = []
    monkeypatch.setattr(core, "_save_settings", lambda: saved.append(dict(me._section())))
    yield saved
    for k in ("t_ac", "t_sens"):
        core.DEVICES.pop(k, None)
    me._from.pop("t_ac", None)


def call(src):
    return asyncio.run(me.set_room_temp_from("t_ac", me.RoomTempFromBody(source=src)))


def test_live_source_is_mirrored_and_saved(ac):
    core._store_status("t_sens", {"online": True, "values": {"temperature": 23.5}})
    assert call("t_sens") == {"ok": True, "room_temp_from": "t_sens"}
    assert core._status_copy("t_ac", {})["values"]["room_temp"] == 23.5
    assert ac[-1]["room_temp_from"]["t_ac"] == "t_sens" and core.DEVICES["t_ac"]["room_temp_from"] == "t_sens"
    assert "room_temp_from" in core.DEVICE_FIELDS


def test_dead_source_drops_the_reading(ac):
    core._store_status("t_sens", {"online": True, "values": {"temperature": 20}})
    call("t_sens")
    core._store_status_exact("t_sens", {"online": False, "values": {"temperature": 20}})
    me._mirror("t_ac")
    assert "room_temp" not in core._status_copy("t_ac", {}).get("values", {})


def test_clear_and_refusals(ac):
    core._store_status("t_sens", {"online": True, "values": {"temperature": 21}})
    call("t_sens")
    call("")
    assert "t_ac" not in me._from and "room_temp" not in core._status_copy("t_ac", {}).get("values", {})
    for bad in ("t_ac", "nope"):
        with pytest.raises(HTTPException):
            call(bad)
    with pytest.raises(HTTPException):
        asyncio.run(me.set_room_temp_from("t_sens", me.RoomTempFromBody(source="")))


def test_section_is_registered():
    assert core.SETTINGS_SECTIONS[me.SECTION] is me._section


def test_a_saved_empty_choice_is_not_taken_over_again():
    devs = {"t_ac": {"room_temp_from": "t_sens"}}
    assert me._initial({"room_temp_from": {}}, devs) == {}
    assert me._initial({}, devs) == {"t_ac": "t_sens"}
    assert me._initial({"room_temp_from": {"t_ac": "x"}}, devs) == {"t_ac": "x"}


def test_a_failed_save_leaves_the_old_choice_live(ac, monkeypatch):
    core._store_status("t_sens", {"online": True, "values": {"temperature": 22}})
    call("t_sens")

    def boom():
        raise HTTPException(503, "settings.json was unreadable")
    monkeypatch.setattr(core, "_save_settings", boom)
    with pytest.raises(HTTPException):
        call("")
    assert me._from["t_ac"] == "t_sens" and core.DEVICES["t_ac"]["room_temp_from"] == "t_sens"


def test_the_poller_waits_a_period_before_its_first_pass(ac, monkeypatch):
    me._from["t_ac"] = "t_sens"
    events = []

    async def sleep(s):
        events.append(s)
        if len(events) > 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(me, "_mirror", events.append)
    monkeypatch.setattr(me.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(me._poller())
    assert events == [me.POLL_S, "t_ac", me.POLL_S]


def test_a_re_added_card_gets_its_source_back_on_the_next_pass(ac, monkeypatch):
    me._from["t_ac"] = "t_sens"
    core.DEVICES["t_ac"].pop("room_temp_from", None)          # removed and added again

    async def sleep(s):
        if "room_temp_from" in core.DEVICES["t_ac"]:
            raise asyncio.CancelledError
    monkeypatch.setattr(me.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(me._poller())
    assert core.DEVICES["t_ac"]["room_temp_from"] == "t_sens"
