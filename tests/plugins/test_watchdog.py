"""The watchdog: a state a rule set is put right when its source says otherwise; a hand's state, a lock, an air conditioner and an IR remote are never touched;
a gone device's data is listed and forgotten only after days of absence while its system runs, rule references reported; an issue closes by itself and a flapping one is counted."""
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

import app as core

HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
wd = sys.modules["yashome_plugins.watchdog"]
M = {"parts": [["named by a rule", []]], "text": "named by a rule"}
RULES = {"radar": {"presence": {"true": {"target": "light", "code": "state", "action": "on"},
                                "false": {"target": "light", "code": "state", "action": "off"}}}}


@pytest.fixture(autouse=True)
def _api_names_resolve_again():
    """monkeypatch leaves the value it read behind on the module: a name of core.PLUGIN_API must go back to resolving when it is read."""
    yield
    for name in core.PLUGIN_API:
        vars(core).pop(name, None)


@pytest.fixture
def house(monkeypatch, tmp_path):
    for d in (wd._issues, wd._closed, wd._missing, wd._tries, core.bind_origin, core.sensor_last, core.sensor_since):
        d.clear()
    wd._resolved.clear()
    wd._fixes.clear()
    wd._pending.clear()
    wd._pending_words.clear()
    for d in (wd._lat, wd._lat_seen, wd._cmd_seen):
        d.clear()
    wd._slow.clear()
    monkeypatch.setattr(wd, "_lat_file", lambda: tmp_path / "latency.json")
    calls = []
    monkeypatch.setitem(core.DEVICES, "light", {"id": "light", "name": "light", "category": "switch", "transport": "z2m", "controls": [{"code": "state"}]})
    monkeypatch.setattr(core, "bindings", RULES)
    monkeypatch.setattr(core, "is_offline", lambda d: False)
    monkeypatch.setattr(core, "safe_execute_binding", lambda *a: calls.append(a))
    monkeypatch.setattr(core, "registry_known", lambda: True)
    monkeypatch.setattr(core, "orphan_refs", lambda: {})                                     # the fixtures of the suite name devices that are not there
    monkeypatch.setattr(wd, "_save", lambda: None)
    monkeypatch.setitem(wd._last, "ghosts", time.time() + 1e9)                                # the real pruner waits for the registry: only its own test runs it
    yield calls
    for d in (wd._issues, wd._closed, wd._missing, wd._tries, wd._gone_since, wd._seen, core.bind_origin, core.sensor_last, core.sensor_since):
        d.clear()
    for k in ("light", "door", "aircon", "water heater", "remote"):
        core.status_cache.pop(k, None)


def rule_set_it_on(now, light=True, presence=False, since_ago=300, target="light"):
    core.bind_origin[(target, "state")] = {"src": "radar", "code": "presence", "g": "true", "ts": now - 600}
    core.sensor_last["radar/presence"] = presence
    core.sensor_since["radar/presence"] = now - since_ago
    core.status_cache[target] = {"online": True, "values": {"state": light}}


def test_a_light_its_rule_switched_on_goes_off_when_the_presence_has_been_false_long_enough(house):
    now = time.time()
    rule_set_it_on(now)
    wd.run_once(now)
    assert house == [("radar", "presence", "false")]
    assert not wd._fixes and wd._issues["rule:light/state"]["level"] == "retry"             # a fix only once the light says so
    assert wd.status(now) == "orange"                                                       # the retry waits for its answer


def test_a_stale_cached_presence_switches_nothing(house):
    now = time.time()
    core.status_cache["radar"] = {"online": True, "values": {"presence": True}}                  # yesterday's value restored from the cache: not the sensor's word
    core.status_cache["light"] = {"online": True, "values": {"state": False}}
    wd.run_once(now)
    core.status_cache.pop("radar", None)
    assert house == [] and not wd._issues


def test_a_light_nobodys_rule_switched_is_never_touched(house):
    now = time.time()
    rule_set_it_on(now)
    core.bind_origin.clear()                                                                # a hand, the app or voice: no origin
    wd.run_once(now)
    assert house == [] and not wd._issues


def test_not_before_the_grace_time_and_not_while_somebody_is_there(house):
    now = time.time()
    rule_set_it_on(now, since_ago=30)
    wd.run_once(now)
    rule_set_it_on(now, presence=True)
    wd.run_once(now)
    assert house == []


def test_a_lock_an_air_conditioner_and_the_listed_boiler_are_never_switched_but_reported(house, monkeypatch):
    binds = {}
    for n, (cat, did) in enumerate((("lock", "door"), ("climate", "aircon"), ("switch", "water heater"))):
        monkeypatch.setitem(core.DEVICES, did, {"id": did, "name": did, "category": cat, "transport": "z2m", "controls": [{"code": "state"}]})
        binds[f"radar{n}"] = {"presence": {"true": {"target": did, "code": "state", "action": "on"}, "false": {"target": did, "code": "state", "action": "off"}}}
        now = time.time()
        core.bind_origin[(did, "state")] = {"src": f"radar{n}", "code": "presence", "g": "true", "ts": now - 600}
        core.sensor_last[f"radar{n}/presence"], core.sensor_since[f"radar{n}/presence"] = False, now - 300
        core.status_cache[did] = {"online": True, "values": {"state": True}}
    monkeypatch.setattr(core, "bindings", binds)
    wd.run_once(time.time())
    assert house == []
    assert {i["subject"] for i in wd._issues.values()} == {"door.state", "aircon.state", "water heater.state"} and all(i["level"] == "warn" for i in wd._issues.values())
    monkeypatch.setitem(core.DEVICES, "secret", {"id": "secret", "category": "switch", "sensitive": True, "controls": []})
    assert wd.never_actuate("secret") and not wd.never_actuate("light")                     # a device marked sensitive; the bath light is on no list


def test_an_ir_remote_has_no_feedback_and_is_skipped(house, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "remote", {"id": "remote", "name": "r", "category": "ir", "transport": "ir", "controls": [{"code": "state"}]})
    monkeypatch.setattr(core, "bindings", {"radar": {"presence": {"false": {"target": "remote", "code": "state", "action": "off"}}}})
    now = time.time()
    rule_set_it_on(now, target="remote")
    wd.run_once(now)
    assert house == [] and wd.no_feedback("remote")


def test_an_offline_target_is_reported_not_commanded(house, monkeypatch):
    monkeypatch.setattr(core, "is_offline", lambda d: True)
    now = time.time()
    rule_set_it_on(now)
    wd.run_once(now)
    assert house == [] and "offline" in wd._issues["rule:light/state"]["text"]


def test_one_try_a_while_two_tries_then_an_alert(house):
    now = time.time()
    rule_set_it_on(now)
    wd.run_once(now)
    wd.run_once(now + 30)                                                                    # inside the retry gap: nothing more
    assert len(house) == 1 and wd._issues["rule:light/state"]["level"] == "retry"
    rule_set_it_on(now + 200)
    wd.run_once(now + 200)                                                                   # the second try
    assert len(house) == 2
    rule_set_it_on(now + 400)
    wd.run_once(now + 400)
    assert len(house) == 2 and wd._issues["rule:light/state"]["level"] == "alert" and wd.status(now + 400) == "red"


def test_an_issue_the_house_acted_on_is_listed_as_fixed_once_the_light_is_in_step(house):
    now = time.time()
    rule_set_it_on(now)
    wd.run_once(now)
    core.status_cache["light"] = {"online": True, "values": {"state": False}}
    wd.run_once(now + 60)
    assert not wd._issues and (("light", "state") not in core.bind_origin)
    f = wd._fixes[0]
    assert f["check"] == "rules" and f["subject"] == "light.state" and f["text"] == 'light: "turn off" by its rule did not take at first; the house ran the rule once more and it worked'
    assert not wd._resolved                                                                  # fixed, not solved by itself
    assert wd.status(now + 60) == "green"                                                    # the history never colours the dot


def test_an_issue_that_comes_back_within_the_hour_is_the_same_entry_counted(house):
    now = time.time()
    for k in range(3):
        wd._sync("refs", {"ref:x": ("warn", "x", M)}, now + k * 100)
        wd._sync("refs", {}, now + k * 100 + 50)
    wd._sync("refs", {"ref:x": ("warn", "x", M)}, now + 1000)
    assert wd._issues["ref:x"]["opened"] == 4
    wd._sync("refs", {}, now + 1050)
    wd._sync("refs", {"ref:x": ("warn", "x", M)}, now + 1050 + wd.FLAP_S + 1)
    assert wd._issues["ref:x"]["opened"] == 1                                                # long ago: a new entry


def test_ghosts_are_cleared_at_most_every_ten_minutes(house, monkeypatch):
    got = []
    monkeypatch.setattr(core, "prune_state_mirror", lambda: got.append(1) or 3)
    monkeypatch.setitem(wd._last, "ghosts", 0.0)
    now = time.time()
    wd.run_once(now)
    wd.run_once(now + 60)
    assert len(got) == 1 and wd._fixes[0]["check"] == "ghosts" and "3 retained" in wd._fixes[0]["text"]


def _gone(monkeypatch, orphans):
    """The core reports these orphans {key: ["kind:where"]}; a prune removes the data ones of the keys it is given. -> the pruned keys."""
    removed = []

    def prune(keys):
        removed.extend(keys)
        return [("room", k, "Bath") for k in keys]
    monkeypatch.setattr(core, "orphan_refs", lambda: {k: [p for p in v if not (k in removed and p.split(":")[0] in core.data_ref_kinds)] for k, v in orphans.items()})
    monkeypatch.setattr(core, "prune_data_refs", prune)
    return removed


def test_a_gone_device_is_listed_in_one_row_and_never_forgotten_at_once_a_rule_reference_is_reported(house, monkeypatch):
    removed = _gone(monkeypatch, {"shower": ["room:Bath", "name-override:shower"], "lamp2": ["tab-order:switch", "binding:radar/presence"],
                                  "blaster:ir-2": ["room:Bedroom"]})
    wd._seen.update({"shower": ["", "z2m"], "lamp2": ["", "z2m"]})
    now = time.time()
    for k in range(wd.DANGLING_LOOPS + 2):
        wd.run_once(now + k * 45)
    assert removed == []                                                                     # z2m late at boot, a node offline: nothing goes
    row = wd._issues["forgotten"]
    assert row["level"] == "info" and row["link"] == "devices/forgotten" and row["parts"][0][1] == ["2"]      # the map's blaster is no device
    assert "binding:radar/presence" in wd._issues["ref:lamp2"]["text"] and wd._issues["ref:lamp2"]["level"] == "warn"
    del wd._issues["ref:lamp2"]
    assert wd.status() == "green"                                                            # a note colours nothing


@pytest.mark.parametrize("seen, pruned, level", [
    (["", "z2m"], True, None),                       # its system runs (another z2m device is here): forgotten after the days
    (["", "zigbee-down"], False, "warn"),            # no device of its transport is here: the transport is down, held
    (["switched-off", "z2m"], False, "warn"),        # its plug-in is not loaded: held
    (None, False, "info"),                           # never seen by the watchdog: listed only, never by itself
])
def test_a_gone_device_is_forgotten_after_the_days_only_while_its_system_runs(house, monkeypatch, seen, pruned, level):
    removed = _gone(monkeypatch, {"shower": ["room:Bath"]})
    now = time.time()
    if seen:
        wd._seen["shower"] = seen
    wd._gone_since["shower"] = now - 29 * 86400
    wd.run_once(now)
    assert removed == []                                                                     # 29 days: not yet
    wd._gone_since["shower"] = now - 31 * 86400
    wd.run_once(now + 45)
    assert (removed == ["shower"]) is pruned
    if pruned:
        assert "forgotten" not in wd._issues and "gone for 30 days" in wd._fixes[0]["text"]
    else:
        assert wd._issues["forgotten"]["level"] == level


def test_zero_days_never_forgets_and_the_days_are_a_setting(house, monkeypatch, tmp_path):
    removed = _gone(monkeypatch, {"shower": ["room:Bath"]})
    monkeypatch.setattr(core, "plugin_data", lambda name: tmp_path)
    c = TestClient(core.app)
    assert c.put("/api/watchdog/settings", json={"days": 0}).status_code in (401, 403)
    assert c.put("/api/watchdog/settings", headers=HOUSE, json={"days": 0}).json() == {"forget_days": 0}
    assert c.put("/api/watchdog/settings", headers=HOUSE, json={"days": -1}).status_code == 422
    wd._seen["shower"], wd._gone_since["shower"] = ["", "z2m"], time.time() - 400 * 86400
    wd.run_once()
    assert removed == [] and wd._issues["forgotten"]["level"] == "info"


def test_the_checks_wait_for_the_device_lists_registry_known_not_registry_ready(house, monkeypatch):
    _gone(monkeypatch, {"shower": ["room:Bath"]})
    monkeypatch.setattr(core, "registry_known", core._registry_known)
    monkeypatch.setattr(core, "REGISTRY_PENDING", {"zigbee"})
    assert wd.check_refs(time.time()) == {}                                                  # a list not in yet: nothing is judged
    monkeypatch.setattr(core, "REGISTRY_PENDING", set())
    assert "forgotten" in wd.check_refs(time.time())


def test_forget_removes_every_leftover_of_a_gone_device_for_the_owner_only(monkeypatch):
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    monkeypatch.setattr(core, "_name_overrides", {"ghost": "Old shower"})
    monkeypatch.setattr(core, "_favorites", [{"device": "ghost", "code": "state"}])
    monkeypatch.setattr(core, "_room_sensors", {"r1": {"temperature": "ghost/temperature", "humidity": "light/humidity"}, "r2": {"temperature": "ghost/t"}})
    monkeypatch.setattr(core, "_bindings", {"ghost": {"b": {"single": {"target": "light", "code": "state", "action": "on"}}}})
    monkeypatch.setitem(core.DEVICES, "light", {"id": "light", "controls": []})
    c = TestClient(core.app)
    assert c.get("/api/devices/forgotten").status_code in (401, 403)
    ghost = next(f for f in c.get("/api/devices/forgotten", headers=HOUSE).json()["items"] if f["id"] == "ghost")
    assert ghost["name"] == "Old shower" and {"name-override:ghost", "favorite:state", "room-sensor:r1"} <= set(ghost["data"]) and ghost["kept"]
    assert c.delete("/api/devices/ghost/leftovers").status_code in (401, 403)
    assert c.delete("/api/devices/light/leftovers", headers=HOUSE).status_code == 400            # a device that is here
    r = c.delete("/api/devices/ghost/leftovers", headers=HOUSE).json()
    assert {"name-override:ghost", "favorite:state", "room-sensor:r1", "room-sensor:r2"} <= set(r["removed"]) and r["kept"]
    assert core._room_sensors == {"r1": {"humidity": "light/humidity"}} and core._name_overrides == {} and "ghost" in core._bindings
    assert c.delete("/api/devices/nobody/leftovers", headers=HOUSE).status_code == 404


def test_the_core_removes_only_data_references_of_a_missing_device(monkeypatch):
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    monkeypatch.setattr(core, "_favorites", [{"device": "ghost", "code": "state"}, {"device": "light", "code": "state"}])
    monkeypatch.setattr(core, "_tab_order", {"switch": ["ghost", "light"]})
    monkeypatch.setattr(core, "_tile_sizes", {"switch": {"ghost": "2x1", "light": "1x1"}})
    monkeypatch.setattr(core, "_name_overrides", {"ghost": "x"})
    monkeypatch.setattr(core, "_bindings", {"ghost": {"b": {"single": {"target": "light", "code": "state", "action": "on"}}}})
    monkeypatch.setitem(core.DEVICES, "light", {"id": "light", "controls": []})
    gone = core.prune_data_refs({"ghost", "light"})                                          # "light" exists: never touched
    assert {k for _k, k, _w in gone} == {"ghost"} and core._favorites == [{"device": "light", "code": "state"}]
    assert core._tab_order == {"switch": ["light"]} and core._tile_sizes == {"switch": {"light": "1x1"}} and core._name_overrides == {}
    assert "ghost" in core._bindings                                                          # a rule is reported, never removed


def test_the_report_is_for_the_house_token_only_and_the_badge_colour_follows_the_worst_issue(house):
    c = TestClient(core.app)
    assert c.get("/api/watchdog").status_code in (401, 403) and c.get("/api/watchdog", headers={"Authorization": "Bearer nope"}).status_code in (401, 403)
    r = c.get("/api/watchdog", headers=HOUSE).json()
    assert r["status"] == "green" and r["count"] == 0
    wd._sync("refs", {"ref:x": ("warn", "x", M)}, time.time())
    assert c.get("/api/watchdog", headers=HOUSE).json()["status"] == "yellow"
    wd._sync("rules", {"rule:y": ("alert", "y", M)}, time.time())
    assert c.get("/api/watchdog", headers=HOUSE).json()["status"] == "red"


# ---- fresh ----
def _sensor(monkeypatch, did, battery, age, now):
    ctl = [{"kind": "sensor", "code": "temperature", "vtype": "num"}] + ([{"kind": "sensor", "code": "battery"}] if battery else [])
    monkeypatch.setitem(core.DEVICES, did, {"id": did, "name": did, "category": "sensor", "transport": "z2m", "controls": ctl})
    core.status_cache[did] = {"online": True, "values": {"temperature": 21.0}, "last_seen": now - age, "periodic": not battery}      # a mains sensor that reports regularly


def test_a_sensor_silent_beyond_its_kind_is_marked_stale_a_battery_one_later_and_an_event_device_never(house, monkeypatch):
    now, marked = time.time(), []
    _sensor(monkeypatch, "mains", False, 25 * 60, now)
    _sensor(monkeypatch, "battery_old", True, 7 * 3600, now)
    _sensor(monkeypatch, "battery_ok", True, 2 * 3600, now)
    _sensor(monkeypatch, "mains_ok", False, 5 * 60, now)
    monkeypatch.setitem(core.DEVICES, "button", {"id": "button", "category": "button", "transport": "z2m", "controls": [{"kind": "sensor", "code": "action", "vtype": "str"}]})
    core.status_cache["button"] = {"online": True, "values": {}, "last_seen": now - 86400}
    monkeypatch.setattr(core, "store_status", lambda did, st, report=True, codes=None: marked.append((did, st.get("stale"), report)))
    wd.run_once(now)
    assert sorted(m[0] for m in marked) == ["battery_old", "mains"] and all(m[1] is True and m[2] is False for m in marked)     # a mark, not a report
    assert {i["subject"] for i in wd._issues.values()} == {"mains", "battery_old"}
    for k in ("mains", "battery_old", "battery_ok", "mains_ok", "button"):
        core.status_cache.pop(k, None)


# ---- commands ----
def _sent(monkeypatch, dev, cat="switch", val=True, old=False, have=False, age=60, seen_after=True, transport="z2m"):
    now = time.time()
    monkeypatch.setitem(core.DEVICES, dev, {"id": dev, "name": dev, "category": cat, "transport": transport, "controls": [{"code": "state"}]})
    core.status_cache[dev] = {"online": True, "values": {"state": have}, "last_seen": now - age + 5 if seen_after else now - age - 5}
    core.sent_commands.clear()
    core.sent_commands.append((dev, "state", val, old, now - age, "app"))
    return now


def test_a_channel_the_device_marks_no_enforce_is_neither_resent_nor_reported(house, monkeypatch):
    sent = []
    monkeypatch.setattr(wd.asyncio, "run_coroutine_threadsafe", lambda coro, loop: (coro.close(), sent.append(1))[1])
    now = _sent(monkeypatch, "tv")
    core.DEVICES["tv"]["no_enforce"] = ["state"]                          # a TV that wakes by itself: the house accepts it
    wd.run_once(now)
    assert sent == [] and "cmd:tv/state" not in wd._issues
    core.status_cache.pop("tv", None)
    core.sent_commands.clear()


def test_a_command_the_channel_still_contradicts_is_sent_again_once_then_alerted(house, monkeypatch):
    sent = []
    monkeypatch.setattr(wd.asyncio, "run_coroutine_threadsafe", lambda coro, loop: (coro.close(), sent.append(1), type("F", (), {"result": lambda s, t=None: None})())[2])
    now = _sent(monkeypatch, "lamp")
    wd.run_once(now)
    i = wd._issues["cmd:lamp/state"]
    assert len(sent) == 1 and not wd._fixes and i["level"] == "retry" and i["text"].startswith('lamp: "turn on" did not work the first time')
    wd.run_once(now + 50)                                                                    # still wrong after the second try: an alert, no third
    assert len(sent) == 1 and wd._issues["cmd:lamp/state"]["level"] == "alert" and wd.status(now + 50) == "red" and not wd._fixes
    core.status_cache.pop("lamp", None)
    core.sent_commands.clear()


def test_a_command_that_took_or_was_overruled_by_a_hand_or_is_too_young_is_left_alone(house, monkeypatch):
    sent = []
    monkeypatch.setattr(wd.asyncio, "run_coroutine_threadsafe", lambda *a: sent.append(1))
    for kw in ({"have": True}, {"have": "other"}, {"age": 5}, {"seen_after": False}):
        now = _sent(monkeypatch, "lamp", **kw)
        wd.run_once(now)
    core.status_cache.pop("lamp", None)
    core.sent_commands.clear()
    assert sent == [] and not wd._issues


def test_a_lock_and_an_ir_remote_are_never_sent_again(house, monkeypatch):
    sent = []
    monkeypatch.setattr(wd.asyncio, "run_coroutine_threadsafe", lambda *a: sent.append(1))
    now = _sent(monkeypatch, "door", cat="lock")
    wd.run_once(now)
    assert sent == [] and wd._issues["cmd:door/state"]["level"] == "warn"
    now = _sent(monkeypatch, "tvir", cat="ir", transport="ir")
    wd.run_once(now)
    assert sent == []
    for k in ("door", "tvir"):
        core.status_cache.pop(k, None)
    core.sent_commands.clear()


# ---- presence ----
def test_the_phones_and_the_router_disagreeing_for_long_is_reported_and_closes_by_itself(house, monkeypatch):
    monkeypatch.setitem(core.DEVICES, "presence", {"id": "presence", "category": "indicator", "transport": "presence", "controls": []})
    monkeypatch.setitem(core.DEVICES, "person_a", {"id": "person_a", "category": "indicator", "transport": "family", "controls": []})
    core.status_cache["presence"] = {"online": True, "values": {"home": False}}
    core.status_cache["person_a"] = {"online": True, "values": {"home": True}}
    now = time.time()
    wd.run_once(now)
    wd.run_once(now + wd.PRESENCE_S - 60)
    assert not wd._issues                                                                     # not for long yet
    wd.run_once(now + wd.PRESENCE_S + 60)
    assert wd._issues["presence"]["level"] == "warn" and "phones say home" in wd._issues["presence"]["text"]
    core.status_cache["presence"] = {"online": True, "values": {"home": True}}
    wd.run_once(now + wd.PRESENCE_S + 120)
    assert not wd._issues and wd._resolved[0]["subject"] == "presence"
    for k in ("presence", "person_a"):
        core.status_cache.pop(k, None)


def test_every_word_of_an_issue_has_a_russian_text():
    import json
    import pathlib
    ru = json.loads((pathlib.Path(wd.__file__).parent / "i18n.json").read_text(encoding="utf-8"))["ru"]
    words = [getattr(wd, n) for n in dir(wd) if n.isupper() and isinstance(getattr(wd, n), str) and ("{" in getattr(wd, n) or n.startswith(("RULE", "GHOST", "REF", "FRESH", "CMD", "PRESENCE", "HINT")))]
    assert len(words) >= 19 and [w for w in words if w not in ru] == []


# ---- a write to a node's settings ----
def test_a_settings_write_to_the_radar_or_its_engineering_mode_changing_makes_its_presence_untrusted(house, monkeypatch):
    got = []
    monkeypatch.setattr(core, "quiet_device", lambda dev, secs, why: got.append((dev, secs, why)))
    h = wd._config_watch("radar_1", "radar-1/", 30.0)
    h(["radar-1", "number", "radar_1_timeout", "command"], b"15", False)
    h(["radar-1", "switch", "radar_1_engineering_mode", "state"], b"ON", False)
    h(["radar-1", "select", "radar_1_x", "command"], b"a", False)
    h(["radar-1", "number", "radar_1_timeout", "command"], b"15", True)                     # a retained replay is no write
    h(["radar-1", "sensor", "radar_1_moving_energy", "state"], b"48", False)               # a reading is none either
    h(["radar-1", "switch", "radar_1_bluetooth", "state"], b"OFF", False)
    assert got == [("radar_1", 30.0, "number/radar_1_timeout/command"), ("radar_1", 30.0, "switch/radar_1_engineering_mode/state"), ("radar_1", 30.0, "select/radar_1_x/command")]


def test_the_quiet_settings_of_the_house_are_read(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text('{"config_quiet": [{"device": "radar_1", "prefix": "radar-1", "seconds": 20}, {"nonsense": 1}]}')
    monkeypatch.setattr(core, "plugin_data", lambda name: tmp_path)
    assert wd._settings()["config_quiet"] == [{"device": "radar_1", "prefix": "radar-1/", "seconds": 20.0}]


def test_a_device_that_reports_only_when_asked_or_never_regularly_is_not_judged_and_an_old_mark_goes(house, monkeypatch):
    now, marked = time.time(), []
    monkeypatch.setitem(core.DEVICES, "ac2", {"id": "ac2", "name": "Aircon (bedroom)", "category": "climate", "transport": "mqtt", "controls": [{"kind": "sensor", "code": "room_temp", "vtype": "num"}]})
    core.status_cache["ac2"] = {"online": True, "values": {"room_temp": 21.0}, "last_seen": now - 3600, "periodic": True}              # IR driven: it answers when commanded
    _sensor(monkeypatch, "odd", False, 3600, now)
    core.status_cache["odd"]["periodic"] = False                                                                                          # never seen reporting regularly
    monkeypatch.setattr(core, "store_status", lambda did, st, report=True, codes=None: marked.append((did, st.get("stale"))))
    wd._marked.add("ac2")                                                                                                                  # marked stale by an earlier version
    core.status_cache["ac2"]["stale"] = True
    wd.run_once(now)
    assert not wd._issues and marked == [("ac2", None)] and "ac2" not in wd._marked                                                        # not judged; the old mark is taken off
    for k in ("ac2", "odd"):
        core.status_cache.pop(k, None)


def test_an_issue_names_the_device_with_its_room_and_says_what_to_do(house, monkeypatch):
    now = time.time()
    monkeypatch.setattr(core, "get_rooms", lambda: [{"id": "r", "name": "Bath", "devices": ["mains"]}])
    _sensor(monkeypatch, "mains", False, 25 * 60, now)
    monkeypatch.setattr(core, "store_status", lambda did, st, report=True, codes=None: None)
    wd.run_once(now)
    i = wd._issues["stale:mains"]
    assert i["text"].startswith("mains (Bath): no report for 25 min") and i["hint"][0] == wd.HINT_FRESH
    core.status_cache.pop("mains", None)



def test_a_retry_the_device_confirms_is_a_fix_and_the_dot_goes_green(house, monkeypatch):
    sent = []
    monkeypatch.setattr(wd.asyncio, "run_coroutine_threadsafe", lambda coro, loop: (coro.close(), sent.append(1), type("F", (), {"result": lambda s, t=None: None})())[2])
    now = _sent(monkeypatch, "lamp")
    wd.run_once(now)
    assert wd.status(now) == "orange" and not wd._fixes
    core.status_cache["lamp"] = {"online": True, "values": {"state": True}, "last_seen": now + 30}
    wd.run_once(now + 50)
    assert not wd._issues and not wd._resolved and wd.status(now + 50) == "green"
    assert wd._fixes[0]["text"] == 'lamp: "turn on" did not work the first time; the house sent it again and it worked'
    core.status_cache.pop("lamp", None)
    core.sent_commands.clear()


def test_a_command_s_row_speaks_the_device_s_verb_never_a_field_or_true_false(house, monkeypatch):
    import json
    import pathlib
    ru = json.loads((pathlib.Path(wd.__file__).parent / "i18n.json").read_text(encoding="utf-8"))["ru"]
    monkeypatch.setitem(core.DEVICES, "yard", {"id": "yard", "name": "Yard lock", "category": "lock", "controls": [{"code": "locked"}]})
    assert [wd._verb("yard", "locked", v) for v in (False, True, "UNLOCK")] == ["unlock", "lock", "unlock"]
    assert [wd._verb("light", "state", v) for v in (True, "OFF", 40)] == ["turn on", "turn off", "40"]
    assert all(ru[v] for v in ("lock", "unlock", "turn on", "turn off"))
    now = _sent(monkeypatch, "door", cat="lock", val=False, old=True, have=True)          # a lock: reported, never sent again
    wd.run_once(now)
    t = wd._issues["cmd:door/state"]["text"]
    assert t == 'door: "unlock" did not work; the house does not send it again by itself' and "True" not in t and "False" not in t
    core.status_cache.pop("door", None)
    core.sent_commands.clear()


def test_the_history_keeps_seven_days_and_old_unconfirmed_claims_are_dropped(house, monkeypatch, tmp_path):
    import json
    now = time.time()
    wd._fixes.extend([{"ts": now - 3600, "check": "ghosts", "subject": "x", "parts": [["a", []]], "text": "a"},
                      {"ts": now - 8 * 86400, "check": "ghosts", "subject": "y", "parts": [["b", []]], "text": "b"}])
    wd.run_once(now)
    assert [f["subject"] for f in wd._fixes] == ["x"]
    wd._fixes.clear()
    f = tmp_path / "state.json"
    f.write_text(json.dumps({"fixes": [{"ts": now, "parts": [[wd._UNCONFIRMED[0], ["a", "state", "True", "False"]]], "text": "old"},
                                       {"ts": now, "parts": [[wd.GHOST, ["2"]]], "text": "cleared"}]}), encoding="utf-8")
    monkeypatch.setattr(wd, "_state_file", lambda: f)
    wd._load()
    assert [x["text"] for x in wd._fixes] == ["cleared"]



# ---- the confirm window: learned per channel, a start value by kind, a wait set by hand ----
def _answers(dev, code, secs, now):
    for i, t in enumerate(secs):
        ts = now - 10000 + i * 100
        core.sent_commands.append((dev, code, True, False, ts, "app"))
        wd._on_report(dev, {code: True}, ts + t)


def test_the_wait_is_learned_from_the_channel_s_own_answers(house, monkeypatch):
    now = time.time()
    monkeypatch.setitem(core.DEVICES, "lamp", {"id": "lamp", "name": "lamp", "category": "switch", "controls": [{"code": "state"}]})
    monkeypatch.setitem(core.DEVICES, "yard", {"id": "yard", "name": "yard", "category": "lock", "controls": [{"code": "locked"}]})
    monkeypatch.setitem(core.DEVICES, "tv", {"id": "tv", "name": "tv", "category": "tv", "controls": [{"code": "power"}]})
    assert (wd.confirm_window("lamp", "state"), wd.confirm_window("yard", "locked"), wd.confirm_window("tv", "power")) == (15, 60, 90)
    _answers("yard", "locked", [2, 3, 4, 30], now)
    assert wd.confirm_window("yard", "locked") == 60                      # four answers: still the start value
    _answers("yard", "locked", [40], now)
    assert wd.confirm_window("yard", "locked") == 60                      # 1.5 x p90 (40 s)
    _answers("lamp", "state", [1, 1, 1, 1, 1], now)
    assert wd.confirm_window("lamp", "state") == 15                       # never under the floor
    _answers("tv", "power", [150] * 5, now)
    assert wd.confirm_window("tv", "power") == 180                        # never over the cap
    assert len(wd._lat["yard/locked"]) == 5
    monkeypatch.setitem(core.DEVICES, "mqlock", {"id": "mqlock", "name": "m", "category": "switch", "controls": [{"code": "locked"}]})
    assert wd.confirm_window("mqlock", "locked") == 60                    # an MQTT lock is a switch device: its lock channel says it
    core.sent_commands.clear()


def test_a_wait_set_in_the_device_window_wins_and_auto_gives_it_back(house, monkeypatch, tmp_path):
    monkeypatch.setitem(core.DEVICES, "yard", {"id": "yard", "name": "yard", "category": "lock", "controls": [{"code": "locked", "kind": "lock", "label": "Lock"}]})
    monkeypatch.setattr(core, "plugin_data", lambda name: tmp_path)
    c = TestClient(core.app)
    r = c.put("/api/watchdog/answers/yard", headers=HOUSE, json={"code": "locked", "seconds": 120}).json()
    assert r["channels"][0]["wait"] == 120 and r["channels"][0]["set"] == 120 and wd.confirm_window("yard", "locked") == 120
    r = c.put("/api/watchdog/answers/yard", headers=HOUSE, json={"code": "locked", "seconds": None}).json()
    assert r["channels"][0]["wait"] == 60 and r["channels"][0]["set"] is None
    assert c.get("/api/watchdog/answers/yard").status_code in (401, 403)


def test_a_command_is_judged_only_after_its_channel_s_window(house, monkeypatch):
    sent = []
    monkeypatch.setattr(wd.asyncio, "run_coroutine_threadsafe", lambda coro, loop: (coro.close(), sent.append(1), type("F", (), {"result": lambda s, t=None: None})())[2])
    now = _sent(monkeypatch, "lamp", age=60)
    monkeypatch.setitem(wd.DEFAULTS["confirm_start"], "switch", 90)        # a slow kind: 60 s is not yet late
    wd.run_once(now)
    assert sent == [] and not wd._issues
    core.status_cache.pop("lamp", None)
    core.sent_commands.clear()


# ---- a channel that only flips is never sent again by the house ----
def test_a_toggle_channel_is_an_open_issue_with_send_again_never_resent(house, monkeypatch):
    sent, ctl = [], []
    monkeypatch.setattr(wd.asyncio, "run_coroutine_threadsafe", lambda *a: sent.append(1))
    now = _sent(monkeypatch, "tvk")
    core.DEVICES["tvk"]["controls"] = [{"code": "state", "toggle": True}]
    wd.run_once(now)
    i = wd._issues["cmd:tvk/state"]
    assert sent == [] and i["level"] == "warn" and i["resend"] == {"device": "tvk", "code": "state"} and "only flips" in i["text"]

    async def control(dev, body):
        ctl.append((dev, body.code, body.value, body.source))
    monkeypatch.setattr(core, "control", control)
    r = TestClient(core.app).post("/api/watchdog/resend", headers=HOUSE, json={"key": "cmd:tvk/state"})
    assert r.status_code == 200 and ctl == [("tvk", "state", True, "app")]
    assert TestClient(core.app).post("/api/watchdog/resend", headers=HOUSE, json={"key": "cmd:nothing"}).status_code == 404
    core.status_cache.pop("tvk", None)
    core.sent_commands.clear()


# ---- the watchdog's own retry is not a new command ----
def test_the_retry_is_never_taken_for_a_new_command(house, monkeypatch):
    sent = []

    def run(coro, loop):                                   # the real control logs the retry as a command of the watchdog
        coro.close()
        sent.append(1)
        core.sent_commands.append(("lamp", "state", True, False, time.time(), "watchdog"))
        return type("F", (), {"result": lambda s, t=None: None})()
    monkeypatch.setattr(wd.asyncio, "run_coroutine_threadsafe", run)
    now = _sent(monkeypatch, "lamp")
    for k in range(3):                                     # three loops, the device never takes it
        core.status_cache["lamp"]["last_seen"] = now + 50 * k + 5
        wd.run_once(now + 50 * k)
    assert len(sent) == 1 and wd._issues["cmd:lamp/state"]["level"] == "alert"
    core.status_cache.pop("lamp", None)
    core.sent_commands.clear()


# ---- an answer after the window, before any retry: slow, not a problem ----
def test_a_late_answer_before_any_retry_is_a_slow_answer(house, monkeypatch):
    now = time.time()
    monkeypatch.setitem(core.DEVICES, "lamp", {"id": "lamp", "name": "lamp", "category": "switch", "controls": [{"code": "state"}]})
    core.sent_commands.clear()
    core.sent_commands.append(("lamp", "state", True, False, now - 100, "app"))
    wd._on_report("lamp", {"state": False}, now - 90)                      # the old value: no answer
    wd._on_report("lamp", {"state": True}, now - 60)                       # 40 s: after the 15 s window
    wd._on_report("lamp", {"state": True}, now - 50)                       # the same command: counted once
    assert wd._lat["lamp/state"] == [40.0] and wd._slow[0]["text"] == 'lamp: "turn on" worked, but slowly: in 40 s'
    core.sent_commands.append(("lamp", "state", False, True, now - 20, "app"))
    wd._on_report("lamp", {"state": False}, now - 18)                      # in time: learned, not slow
    assert wd._lat["lamp/state"] == [40.0, 2.0] and len(wd._slow) == 1
    core.sent_commands.append(("lamp", "state", True, False, now - 15, "app"))
    wd._cmd_seen[("lamp", "state")] = [now - 15, 1]                         # the watchdog sent it again
    wd._on_report("lamp", {"state": True}, now - 1)                        # the retry may have answered: no sample
    assert wd._lat["lamp/state"] == [40.0, 2.0] and len(wd._slow) == 1
    core.sent_commands.clear()


def test_the_core_tells_on_report_listeners_of_a_device_s_own_report_not_a_command_echo(house, monkeypatch):
    got = []
    monkeypatch.setattr(core, "ON_REPORT", core.ON_REPORT + [lambda dev, values, ts: got.append((dev, values))])
    core.store_status("light", {"online": True, "values": {"state": True, "brightness": 40}}, codes=["state"])
    core.store_status("light", {"online": True, "values": {"state": False}}, report=False)
    assert got == [("light", {"state": True})]



def test_an_mqtt_lock_known_by_its_channel_is_never_sent_again_only_reported(house, monkeypatch):
    sent = []
    monkeypatch.setattr(wd.asyncio, "run_coroutine_threadsafe", lambda *a: sent.append(1))
    now = time.time()
    monkeypatch.setitem(core.DEVICES, "yard", {"id": "yard", "name": "Yard lock", "category": "switch", "transport": "mqtt", "controls": [{"code": "locked", "kind": "switch"}]})
    core.status_cache["yard"] = {"online": True, "values": {"locked": True}, "last_seen": now - 10}
    core.sent_commands.clear()
    core.sent_commands.append(("yard", "locked", False, True, now - 120, "app"))      # past its 60 s window
    wd.run_once(now)
    i = wd._issues["cmd:yard/locked"]
    assert sent == [] and i["level"] == "warn" and i["resend"] == {"device": "yard", "code": "locked"} and '"unlock"' in i["text"]
    assert wd.never_actuate("yard", "locked") and not wd.never_actuate("yard")
    core.status_cache.pop("yard", None)
    core.sent_commands.clear()
