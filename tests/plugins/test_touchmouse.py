"""touchmouse: the receivers come from devices and the settings section; only the dashboard edits
them; the host receiver turns datagrams into uinput events, a step capped."""
import importlib.util
import os
import sys

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.touchmouse"]
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(me, "_receivers", [])
    monkeypatch.setitem(core.SETTINGS_SECTIONS, "touchmouse", lambda: me._receivers)
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    dev = sorted(core.DEVICES)[0]
    monkeypatch.setitem(core.DEVICES, dev, {**core.DEVICES[dev], "touchmouse": {"port": 7710, "host": "192.0.2.40"}})
    yield dev


def test_devices_and_hosts_are_listed(clean):
    c = TestClient(core.app)
    body = {"id": "media-pc", "name": "Media PC", "host": "192.0.2.30", "port": 7709, "os": "linux"}
    assert c.put("/api/touchmouse/receivers/media-pc", headers=HOUSE, json=body).status_code == 200
    got = {r["id"]: r for r in c.get("/api/touchmouse", headers=HOUSE).json()}
    assert got[clean] == {"id": clean, "name": core.DEVICES[clean].get("name") or clean, "host": "192.0.2.40",
                          "port": 7710, "os": "android", "device": True}
    assert got["media-pc"]["host"] == "192.0.2.30" and got["media-pc"]["device"] is False
    assert c.delete("/api/touchmouse/receivers/media-pc", headers=HOUSE).status_code == 200
    assert c.delete("/api/touchmouse/receivers/media-pc", headers=HOUSE).status_code == 404


@pytest.mark.parametrize("bad, status", [({"id": "Bad Id"}, 400), ({"host": "a b"}, 400), ({"os": "dos"}, 400),
                                         ({"port": 70000}, 422), ({"id": "x" * 33}, 422)])
def test_bad_receivers_are_refused(bad, status):
    body = {"id": "pc", "host": "192.0.2.30", "port": 7709, "os": "linux", **bad}
    r = TestClient(core.app).put(f"/api/touchmouse/receivers/{body['id']}", headers=HOUSE, json=body)
    assert r.status_code == status and me._receivers == []


def test_a_device_id_is_not_a_receiver_id_and_paths_must_match(clean):
    c = TestClient(core.app)
    body = {"id": clean, "host": "192.0.2.30"}
    assert c.put(f"/api/touchmouse/receivers/{clean}", headers=HOUSE, json=body).status_code == 409
    assert c.put("/api/touchmouse/receivers/other", headers=HOUSE, json={"id": "pc", "host": "h"}).status_code == 400


def test_only_the_dashboard_edits(monkeypatch):
    c = TestClient(core.app)
    monkeypatch.setattr(core, "TOKEN_CHECKS", core.TOKEN_CHECKS + [lambda tok, m, p: tok == "a-full-phone"])
    phone = {"Authorization": "Bearer a-full-phone"}
    assert c.get("/api/touchmouse", headers=phone).status_code == 200
    assert c.put("/api/touchmouse/receivers/pc", headers=phone, json={"id": "pc", "host": "h"}).status_code == 403
    me._receivers.append({"id": "pc", "host": "h"})
    assert c.delete("/api/touchmouse/receivers/pc", headers=phone).status_code == 403 and me._receivers
    assert c.get("/api/touchmouse").status_code == 401


def test_the_receiver_turns_datagrams_into_events(monkeypatch):
    spec = importlib.util.spec_from_file_location("touchmouse_rx", os.path.join(
        os.path.dirname(me.__file__), "host", "touchmouse-rx.py"))
    rx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rx)
    got = []
    monkeypatch.setattr(rx, "emit", lambda fd, *ev: got.append(ev))
    for words in (["m", "3", "-4"], ["m", "9999", "0"], ["b", "r", "1"], ["k", "home"], ["k", "power"], ["b", "x", "1"],
                  ["w", "3"], ["w", "-99"], ["w", "0"], ["b", "m", "1"], ["k", "kbd"]):
        rx.handle(None, words)
    assert got == [((rx.EV_REL, rx.REL_X, 3), (rx.EV_REL, rx.REL_Y, -4)),
                   ((rx.EV_REL, rx.REL_X, 500), (rx.EV_REL, rx.REL_Y, 0)),
                   ((rx.EV_KEY, rx.BTN_RIGHT, 1),),
                   ((rx.EV_KEY, 172, 1),), ((rx.EV_KEY, 172, 0),),
                   ((rx.EV_REL, rx.REL_WHEEL, 3),), ((rx.EV_REL, rx.REL_WHEEL, -20),),
                   ((rx.EV_KEY, rx.BTN_MIDDLE, 1),), ((rx.EV_KEY, 148, 1),), ((rx.EV_KEY, 148, 0),)]
    with pytest.raises(ValueError):
        rx.handle(None, ["m", "x", "1"])
    with pytest.raises(ValueError):
        rx.handle(None, ["w", "x"])


def test_a_bad_hand_edited_entry_or_a_taken_id_is_skipped(clean):
    me._receivers.extend([{"id": "pc", "host": "h", "port": "x"}, {"id": clean, "host": "h"}, {"id": "ok", "host": "h"}])
    ids = [r["id"] for r in me.targets()]
    assert ids.count(clean) == 1 and "pc" not in ids and "ok" in ids


def test_the_receivers_are_the_settings_section():
    me._receivers.append({"id": "pc", "host": "192.0.2.30", "port": 7709, "os": "linux"})
    assert core.SETTINGS_SECTIONS["touchmouse"]() == me._receivers


def test_a_held_button_is_let_go(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("touchmouse_rx2", os.path.join(
        os.path.dirname(me.__file__), "host", "touchmouse-rx.py"))
    rx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rx)
    got = []
    monkeypatch.setattr(rx, "emit", lambda fd, *ev: got.append(ev))
    rx.handle(None, ["b", "l", "1"])
    rx.release_all(None)
    rx.release_all(None)
    assert got == [((rx.EV_KEY, rx.BTN_LEFT, 1),), ((rx.EV_KEY, rx.BTN_LEFT, 0),)]


def _rx():
    spec = importlib.util.spec_from_file_location("touchmouse_rx2", os.path.join(os.path.dirname(me.__file__), "host", "touchmouse-rx.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_arrows_and_enter_are_keys():
    rx = _rx()
    got = []
    rx.emit = lambda fd, *ev: got.append(ev)
    for k in ("up", "down", "left", "right", "enter", "back", "forward", "home"):
        rx.handle(None, ["k", k])
    assert [e[0][1] for e in got[::2]] == [103, 108, 105, 106, 28, 158, 159, 172]
    assert all(e[0][2] == 1 for e in got[::2]) and all(e[0][2] == 0 for e in got[1::2])


def test_play_pause_and_the_volume_are_the_media_keys():
    rx = _rx()
    got = []
    rx.emit = lambda fd, *ev: got.append(ev)
    for k in ("playpause", "voldown", "mute", "volup", "nope"):
        rx.handle(None, ["k", k])
    assert [e[0][1] for e in got[::2]] == [164, 114, 113, 115]          # KEY_PLAYPAUSE, VOLUMEDOWN, MUTE, VOLUMEUP; unknown: nothing


def test_text_goes_to_the_typing_helper_only_when_valid_and_short():
    rx = _rx()
    sent = []
    out = type("Out", (), {"sendto": lambda self, raw, addr: sent.append((raw, addr))})()
    assert rx.forward_text(out, "\u041f\u0440\u0438\u0432\u0435\u0442, world 1".encode("utf-8")) and sent == [("\u041f\u0440\u0438\u0432\u0435\u0442, world 1".encode("utf-8"), ("127.0.0.1", rx.TYPE_PORT))]          # i18n: a Cyrillic sample
    assert not rx.forward_text(out, b"") and not rx.forward_text(out, b"x" * 201) and not rx.forward_text(out, b"\xff\xfe")
    assert rx.forward_text(out, b"x" * 200) and len(sent) == 2


def test_the_typing_helper_quotes_for_gdbus_and_drops_controls(monkeypatch):
    spec = importlib.util.spec_from_file_location("touchmouse_type", os.path.join(os.path.dirname(me.__file__), "host", "touchmouse-type.py"))
    ty = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ty)
    assert ty.clean("a\x00b\x07c\nd\x7f".encode()) == "abc\nd"
    assert ty.quote("it's a \\ b\nc") == "'it\\'s a \\\\ b\\nc'"
    assert ty.quote("-x") == "'-x'"                                   # never an option of gdbus
    calls = []
    monkeypatch.setattr(ty.subprocess, "run", lambda args, **kw: calls.append(args))
    ty.type_text("\u041f\u0440\u0438\u0432\u0435\u0442")          # i18n: a Cyrillic sample
    assert calls[0][:3] == ["gdbus", "call", "--session"] and calls[0][-1] == "'\u041f\u0440\u0438\u0432\u0435\u0442'" and "org.yashome.Osk.Type" in calls[0]          # i18n: a Cyrillic sample


def test_a_receiver_name_is_unique_among_receivers(clean):
    c = TestClient(core.app)
    body = {"id": "pc1", "name": "Media PC", "host": "192.0.2.30", "port": 7709, "os": "linux"}
    assert c.put("/api/touchmouse/receivers/pc1", headers=HOUSE, json=body).status_code == 200
    assert c.put("/api/touchmouse/receivers/pc2", headers=HOUSE, json={**body, "id": "pc2", "name": "media pc"}).status_code == 409
    assert c.put("/api/touchmouse/receivers/pc1", headers=HOUSE, json={**body, "name": "MEDIA PC"}).status_code == 200


def test_receivers_found_by_the_broadcast_are_listed_by_themselves(clean, monkeypatch):
    """The computers plug-in's one broadcast found a receiver: it is listed with its computer's name and TV; one
    already listed by hand keeps its entry; one that is not a computer of the house keeps its own name."""
    pcs = sys.modules["yashome_plugins.computers"]
    monkeypatch.setattr(pcs, "discovered", lambda: [
        {"host": "192.0.2.61", "name": "i3server", "servers": ["build", "touchmouse"], "ports": {"touchmouse": 7709}},
        {"host": "192.0.2.62", "name": "box", "servers": ["touchmouse"], "ports": {}},
        {"host": "192.0.2.30", "name": "media", "servers": ["touchmouse"], "ports": {}},
        {"host": "192.0.2.63", "name": "nas", "servers": ["build"], "ports": {}}])
    monkeypatch.setattr(pcs, "by_host", lambda: {"192.0.2.61": {"id": "i3server", "name": "i3server", "tv": "LG"}})
    me._receivers.append({"id": "media-pc", "name": "Media PC", "host": "192.0.2.30", "port": 7709, "os": "linux"})
    got = {r["host"]: r for r in TestClient(core.app).get("/api/touchmouse", headers=HOUSE).json()}
    assert got["192.0.2.61"] == {"id": "i3server", "name": "i3server (LG)", "host": "192.0.2.61", "port": 7709,
                                 "os": "linux", "device": False, "found": True}
    assert got["192.0.2.62"]["name"] == "box" and got["192.0.2.62"]["id"] == "tm-192-0-2-62"
    assert got["192.0.2.30"]["id"] == "media-pc" and "found" not in got["192.0.2.30"]
    assert "192.0.2.63" not in got
