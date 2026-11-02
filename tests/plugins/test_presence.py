"""presence: the host piece's messages become the device `presence` (home, stale, unseen_days), an
arrival or departure is an event the rules get (a retained replay is not), silence is "no link"."""
import asyncio
import sys

import app as core

me = sys.modules["yashome_plugins.presence"]


def msg(leaf, text, retain=False):
    me._on_message(["presence", leaf], text.encode(), retain)


def test_state_and_stale_from_the_host_piece():
    msg("state", "ON")
    msg("unseen_days", "0")
    v = core.get_status("presence")["values"]
    assert core.get_status("presence")["online"] and v["home"] is True and v["stale"] is False
    msg("state", "OFF")
    msg("unseen_days", "5")
    v = core.get_status("presence")["values"]
    assert v["home"] is False and v["stale"] is True and v["unseen_days"] == 5
    msg("state", "maybe")
    msg("unseen_days", "-1")
    assert core.get_status("presence")["values"]["unseen_days"] == 5


def test_events_reach_the_rules_once(monkeypatch):
    fired = []
    monkeypatch.setattr(core._bg, "submit", lambda fn, *a: fired.append(a))
    msg("event", "arrival")
    msg("event", "departure", retain=True)
    msg("event", "party")
    assert fired == [("presence", "event", "arrival")]
    assert core.get_status("presence")["last_event"] == {"code": "event", "value": "arrival"}


def test_silence_is_no_link(monkeypatch):
    msg("state", "ON")
    monkeypatch.setattr(me, "SILENT_S", -1)

    async def one_round():
        real = asyncio.sleep
        calls = []

        async def fake_sleep(s):
            calls.append(s)
            if len(calls) > core.OFFLINE_AFTER_N_FAILS + 1:
                raise asyncio.CancelledError
            await real(0)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        try:
            await me._watch_link()
        except asyncio.CancelledError:
            pass
    asyncio.run(one_round())
    assert core.get_status("presence")["online"] is False


def test_the_broker_account_writes_only_its_bus():
    import json
    import pathlib
    b = json.loads((pathlib.Path(me.__file__).parent / "setup/broker.json").read_text())
    assert b["accounts"][0]["topics"] == ["presence/#"]


def test_sensor_rules_see_home_and_stale(monkeypatch):
    seen = []
    monkeypatch.setattr(core, "_fire_sensor_triggers", lambda name, cfg, values: seen.append((name, values)))
    msg("state", "ON")
    msg("unseen_days", "6")
    assert seen == [("presence", {"home": True}), ("presence", {"unseen_days": 6, "stale": True})]
