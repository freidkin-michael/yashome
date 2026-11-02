"""The global review's P3 sweep, plug-in part: IR press values, webOS values, a failed zigbee binding,
the build pairing code, the family file and its routes."""
import inspect
import json
import sys

import pytest
from fastapi import HTTPException

import app as core


def mod(name):
    return sys.modules[f"yashome_plugins.{name}"]


def test_an_ir_button_is_pressed_only_with_a_press(monkeypatch):
    ir = mod("ir")
    sent = []
    monkeypatch.setattr(ir, "_send", lambda *a: sent.append(a) or {"ok": True})
    for v in (False, 0, "off", "OFF", "false", "0"):
        with pytest.raises(HTTPException) as e:
            ir._press({"id": "tv"}, {"remote": "r", "button": "b"}, v)
        assert e.value.status_code == 400
    for v in (True, None, "on", 1, "press"):
        ir._press({"id": "tv"}, {"remote": "r", "button": "b"}, v)
    assert len(sent) == 5


def test_webos_values_are_checked_before_the_tv(monkeypatch):
    w = mod("webos")
    cfg = {"id": "tv", "_webos_inputs": {"Kino": "HDMI_2"}, "_webos_apps": {}}
    assert w._webos_value(cfg, "input", "Kino") == "Kino" and w._webos_value(cfg, "input", "HDMI_1") == "HDMI_1"
    assert w._webos_value(cfg, "volume", "15") == 15 and w._webos_value(cfg, "volume", 0) == 0
    assert w._webos_value(cfg, "mute", "off") is False and w._webos_value(cfg, "mute", True) is True
    assert w._webos_value(cfg, "media", "play") == "play"
    assert w._webos_value(cfg, "volume", "50.0") == 50 and w._webos_value(cfg, "volume", 50.0) == 50
    for code, bad in (("input", "a b"), ("input", True), ("app", "x" * 70), ("volume", 101), ("volume", "1.5"),
                      ("volume", 1.5), ("volume", 10 ** 400), ("input", "-x"),
                      ("volume", True), ("volume", "loud"), ("mute", "maybe"), ("media", "rewind_all")):
        with pytest.raises(HTTPException) as e:
            w._webos_value(cfg, code, bad)
        assert e.value.status_code == 400, (code, bad)
    assert "asyncio.to_thread(keep)" in inspect.getsource(w.webos_pair)        # the fsync is off the loop


def test_a_bad_webos_value_never_reaches_the_tv(monkeypatch):
    import asyncio
    w = mod("webos")
    sent = []

    async def command(*a, **k):
        sent.append(a)
    monkeypatch.setattr(w.webos, "command", command)
    monkeypatch.setattr(w, "_webos_keys", {"tv9": "k"})
    monkeypatch.setitem(core.status_cache, "tv9", {"online": True, "values": {"power": True}})
    cfg = {"id": "tv9", "name": "TV", "transport": "webos", "host": "192.0.2.9"}
    with pytest.raises(HTTPException) as e:
        asyncio.run(w._webos_control(cfg, "volume", 101))
    assert e.value.status_code == 400 and sent == []
    monkeypatch.setitem(core.status_cache, "tv9", {"online": True, "values": {"power": False}})
    with pytest.raises(HTTPException) as e:                          # a bad value: 400 before the 409 of an off TV
        asyncio.run(w._webos_control(cfg, "volume", 101))
    assert e.value.status_code == 400


def test_a_failed_zigbee_binding_is_no_fired_and_arms_nothing(monkeypatch):
    z = mod("zigbee")
    monkeypatch.setitem(core.DEVICES, "zl", {"id": "zl", "transport": "zigbee", "controls": [], "_ctl": {}})
    monkeypatch.setattr(z, "_cli", lambda: object())
    monkeypatch.setattr(core, "mqtt_pub", lambda *a, **k: (_ for _ in ()).throw(OSError("broker gone")))
    res = z.binding("btn", "action", "single", {"target": "zl", "code": "state", "action": "on"})
    assert res["error"] == "publish failed"
    armed = []
    monkeypatch.setattr(core, "_apply_for_timer", lambda *a, **k: armed.append(a) or True)
    monkeypatch.setattr(core, "_arm_inching", lambda *a, **k: armed.append(a))
    monkeypatch.setitem(core.TRANSPORT_BINDINGS, "zigbee", lambda *a: dict(res))
    monkeypatch.setitem(core.DEVICES, "btn", {"id": "btn", "transport": "mqtt", "controls": [], "_ctl": {}})
    rule = {"target": "zl", "code": "state", "action": "on", "for": 60}
    monkeypatch.setitem(core._bindings, "btn", {"action": {"single": rule}})
    monkeypatch.setattr(core, "_rule_enabled", lambda rid: True)
    with core._events_lock:
        core._events["btn"] = [{"code": "action", "value": "single"}]
    try:
        assert core._safe_execute_binding("btn", "action", "single")["error"] == "publish failed"
        assert armed == [] and ("zl", "state") not in core._for_echo
        assert core._events["btn"][-1]["fired"].startswith("failed: ")
    finally:
        core._events.pop("btn", None)


def test_the_family_file_is_written_once_with_fsync_and_off_the_loop(tmp_path, monkeypatch):
    f = mod("family")
    seen = []
    monkeypatch.setattr(core, "atomic_json_dump", lambda p, d, **k: seen.append((p, k)) or p.write_text(json.dumps(d)))
    monkeypatch.setattr(f, "DB", tmp_path / "family_devices.json")
    monkeypatch.setattr(f, "_load", lambda: {})
    monkeypatch.setitem(f._cache, "bad", False)
    f._save({"a": {"name": "A"}})
    assert seen and seen[0][0] == tmp_path / "family_devices.json" and not list(tmp_path.glob("*.tmp"))
    for name in ("family_rights_put", "family_me_screen", "family_screen_put", "family_me_widgets"):
        assert not inspect.iscoroutinefunction(getattr(f, name)), name            # FastAPI runs it in its pool
    assert "await run_in_threadpool(keep)" in inspect.getsource(f.family_pair)
    assert "await run_in_threadpool(drop)" in inspect.getsource(f.family_revoke)


def test_a_binding_that_raised_leaves_no_echo_mark(monkeypatch):
    monkeypatch.setitem(core.DEVICES, "zr", {"id": "zr", "transport": "zr_tr", "controls": [], "_ctl": {}})
    monkeypatch.setitem(core.TRANSPORT_BINDINGS, "zr_tr", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setitem(core._bindings, "btn2", {"action": {"single": {"target": "zr", "code": "state", "action": "on"}}})
    monkeypatch.setattr(core, "_rule_enabled", lambda rid: True)
    with pytest.raises(RuntimeError):
        core._execute_binding("btn2", "action", "single")
    assert ("zr", "state") not in core._for_echo


def _press_of(monkeypatch, run):
    """One press of btn/action/single whose rule runs `run`; its result and the press log's `fired`."""
    monkeypatch.setattr(core, "_execute_binding", run)
    with core._events_lock:
        core._events["btn"] = [{"code": "action", "value": "single"}]
    try:
        return core._safe_execute_binding("btn", "action", "single"), core._events["btn"][-1].get("fired")
    finally:
        core._events.pop("btn", None)


def test_a_rule_action_that_raises_is_failed_in_the_press_log_and_returned(monkeypatch):
    def tv_off(*a):
        raise HTTPException(409, "TV is off")
    res, fired = _press_of(monkeypatch, tv_off)
    assert "TV is off" in res["error"] and fired == "failed: " + res["error"]
    res, fired = _press_of(monkeypatch, lambda *a: (_ for _ in ()).throw(KeyError()))
    assert res["error"] == "KeyError" and fired == "failed: KeyError"


def test_a_failed_press_names_the_target_or_the_action_never_none(monkeypatch):
    res, fired = _press_of(monkeypatch, lambda *a: {"error": "boom", "action": "play"})
    assert fired == "failed: play boom"
    res, fired = _press_of(monkeypatch, lambda *a: {"error": "boom"})
    assert fired == "failed: boom"


def test_an_action_that_raises_offline_is_a_failure_not_a_skip(monkeypatch):
    def raises(*a):
        raise RuntimeError("offline")
    res, fired = _press_of(monkeypatch, raises)
    assert res == {"error": "offline", "raised": True} and fired == "failed: offline"


def test_a_rule_off_on_an_ir_button_fails_in_the_press_log(monkeypatch):
    ir = mod("ir")
    monkeypatch.setattr(ir, "_send", lambda *a: pytest.fail("a button was sent"))
    cfg = {"id": "tvir", "name": "TV"}
    res, fired = _press_of(monkeypatch, lambda *a: ir._press(cfg, {"remote": "r", "button": "b"}, False))
    assert res["raised"] and "pressed with true" in res["error"] and fired.startswith("failed: ")
