"""The history: what is written (with who caused it), what is thinned, secrets never, the filters, the erasing, the retention, owner only."""
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

import app as core

HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
hist = sys.modules["yashome_plugins.history"]


@pytest.fixture(autouse=True)
def _api_names_resolve_again():
    yield
    for name in core.PLUGIN_API:
        vars(core).pop(name, None)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(hist, "_path", lambda: tmp_path / "history.db")
    old = hist._conn_w["c"]
    hist._conn_w["c"] = None
    for d in (hist._last, hist._seen, hist._rooms["of"], hist._log_seen):
        d.clear()
    hist._rooms["at"] = 0.0
    while not hist._q.empty():
        hist._q.get_nowait()
    monkeypatch.setitem(core.DEVICES, "lamp", {"id": "lamp", "name": "Lamp", "category": "switch", "transport": "z2m", "controls": [{"code": "state"}]})
    monkeypatch.setattr(core, "get_rooms", lambda: [{"id": "r1", "name": "Hall", "devices": ["lamp"]}])
    yield tmp_path / "history.db"
    if hist._conn_w["c"]:
        hist._conn_w["c"].close()
    hist._conn_w["c"] = old


def events(**params):
    return TestClient(core.app).get("/api/history", headers=HOUSE, params=params).json()


def test_a_state_change_names_who_caused_it_a_command_a_rule_or_the_device(db):
    now = time.time()
    core._sent_log.append(("lamp", "state", True, False, now - 2, "rule:radar/presence=true"))
    core._emit_changes("lamp", {"online": True, "values": {"state": False}}, {"online": True, "values": {"state": True}})
    core._emit_changes("lamp", {"online": True, "values": {"state": True}}, {"online": True, "values": {"state": False}})          # nobody sent that: a hand
    rows = events()["events"]
    assert [(r["kind"], r["new"], r["source"], r["name"], r["room"]) for r in rows] == [("state", False, "device", "Lamp", "Hall"), ("state", True, "rule:radar/presence=true", "Lamp", "Hall")]
    core._sent_log.clear()


def test_a_command_a_rule_firing_offline_and_online_are_written(db):
    core._emit({"kind": "command", "device": "lamp", "code": "state", "new": True, "old": False, "source": "app"})
    core._emit({"kind": "rule", "device": "radar", "code": "presence", "gesture": "true", "target": "lamp", "tcode": "state", "value": True, "source": "rule:radar/presence=true"})
    core._emit_changes("lamp", {"online": True, "values": {}}, {"online": False, "offline_since": time.time(), "values": {}})
    core._emit_changes("lamp", {"online": False, "offline_since": 1, "values": {}}, {"online": True, "values": {}})
    kinds = [r["kind"] for r in events()["events"]]
    assert kinds == ["online", "offline", "rule", "command"] and [r["cat"] for r in events()["events"]] == ["states", "states", "rules", "commands"]


def test_the_first_report_after_a_start_or_an_outage_is_no_change(db):
    core._emit_changes("lamp", {}, {"online": True, "values": {"state": True}})
    core._emit_changes("lamp", {"online": False, "waiting": "start", "values": {"state": False}}, {"online": True, "values": {"state": True}})
    assert events()["events"] == []


def test_noise_is_thinned_a_number_when_it_moved_or_once_an_hour_and_signals_never(db):
    ev = lambda code, old, new, t: hist.add({"kind": "state", "device": "lamp", "code": code, "old": old, "new": new, "source": "device", "ts": t})      # noqa: E731
    t = time.time()
    assert ev("temperature", 20.0, 20.2, t) is False and ev("temperature", 20.0, 20.6, t + 1) is True                                      # a step of 0.5
    assert ev("temperature", 20.6, 20.8, t + 60) is False and ev("temperature", 20.6, 20.8, t + 3700) is True                               # an hourly sample of a slow drift
    assert ev("linkquality", 100, 90, t + 2) is False and ev("g3_move_energy", 1, 90, t + 2) is False and ev("wifi_signal", -60, -70, t + 2) is False
    assert ev("state", False, True, t + 3) is True and ev("illuminance", 100, 110, t + 4) is False and ev("illuminance", 100, 400, t + 5) is True
    assert ev("state", False, True, t + 3.5) is False                                                                                          # the same thing twice inside 2 s is one


def test_the_filters_search_paging_and_the_newest_first(db):
    now = time.time()
    for i in range(7):
        hist.add({"kind": "command", "device": "lamp", "code": "state", "new": bool(i % 2), "source": "app", "ts": now - 100 + i * 10})
    hist.add({"kind": "rule", "device": "radar", "code": "presence", "target": "lamp", "source": "rule:radar/presence=true", "ts": now - 5})
    hist.add({"kind": "error", "level": "alert", "source": "log", "text": "a broker is gone", "ts": now - 1})
    r = events(limit=3)
    assert len(r["events"]) == 3 and r["more"] is True and r["events"][0]["kind"] == "error"
    older = events(limit=3, before=r["events"][-1]["id"])["events"]
    assert older[0]["id"] < r["events"][-1]["id"]
    assert [e["kind"] for e in events(kind="rules")["events"]] == ["rule"] and len(events(kind="commands")["events"]) == 7
    assert len(events(room="Hall")["events"]) == 7 and events(room="Nowhere")["events"] == []
    assert len(events(device="lamp")["events"]) == 7 and [e["kind"] for e in events(q="broker")["events"]] == ["error"]
    assert [e["kind"] for e in events(source="rule:")["events"]] == ["rule"]
    assert len(events(since=now - 65, until=now - 25)["events"]) == 3                                                                          # a time range
    assert TestClient(core.app).get("/api/history", headers=HOUSE, params={"kind": "nonsense"}).status_code == 400


def test_erasing_a_filter_a_range_or_everything_and_never_by_accident(db):
    now = time.time()
    for i in range(4):
        hist.add({"kind": "command", "device": "lamp", "code": "state", "new": bool(i % 2), "source": "app", "ts": now - 100 + i * 10})
    hist.add({"kind": "rule", "device": "radar", "code": "presence", "source": "rule:x", "ts": now})
    c = TestClient(core.app)
    assert c.delete("/api/history", headers=HOUSE).status_code == 400                                                                          # nothing named: refused
    assert c.delete("/api/history", headers=HOUSE, params={"kind": "commands", "until": now - 85}).json() == {"deleted": 2}                  # a range of a kind
    assert len(events()["events"]) == 3
    assert c.delete("/api/history", headers=HOUSE, params={"all": 1}).json() == {"deleted": 3} and events()["events"] == []
    assert c.get("/api/history/info", headers=HOUSE).json()["count"] == 0


def test_the_history_is_for_the_house_token_only(db):
    c = TestClient(core.app)
    for call in (lambda h: c.get("/api/history", headers=h), lambda h: c.delete("/api/history", headers=h, params={"all": 1}), lambda h: c.get("/api/history/info", headers=h)):
        assert call({}).status_code in (401, 403) and call({"Authorization": "Bearer nope"}).status_code in (401, 403)


def test_retention_drops_old_entries_and_caps_the_rows(db, monkeypatch):
    now = time.time()
    hist.add({"kind": "command", "device": "lamp", "code": "state", "new": True, "source": "app", "ts": now - 40 * 86400})
    hist.add({"kind": "command", "device": "lamp", "code": "state", "new": False, "source": "app", "ts": now - 86400})
    hist.flush()
    assert hist.prune() == 1 and len(events()["events"]) == 1
    monkeypatch.setattr(hist, "_settings", lambda: {"retention_days": 30, "max_rows": 2})
    for i in range(4):
        hist.add({"kind": "command", "device": "lamp", "code": "state", "new": f"v{i}", "source": "app", "ts": now - i})
    hist.flush()
    hist.prune()
    assert len(events()["events"]) == 2


def test_no_secret_reaches_the_history_through_the_log(db, monkeypatch):
    token = os.environ["DASHBOARD_TOKEN"]
    assert hist._redact("the house token " + token + " failed") is None                                                                       # a line with an env secret is dropped
    assert hist._redact("Authorization: Bearer abcdefgh12345678xyz sent") == "Authorization: Bearer *** sent"
    assert hist._redact("password=hunter2 and key: s3cr3t") == "password=*** and key=***"
    assert "0123456789abcdef0123456789abcdef" not in hist._redact("id 0123456789abcdef0123456789abcdef seen")
    core.log.warning("broker gone, token " + token)
    core.log.warning("a plain warning about lamp")
    core.log.error("Authorization: Bearer abcdefgh12345678xyz")
    texts = [e.get("text") for e in events(kind="errors")["events"]]
    assert "Authorization: Bearer ***" in texts
    assert not any(token in str(t) or "abcdefgh12345678xyz" in str(t) for t in texts) and "a plain warning about lamp" in texts


def test_the_watchdog_and_its_issues_are_in_the_timeline_with_their_words(db):
    wd = sys.modules["yashome_plugins.watchdog"]
    m = {"parts": [["no report for {0} min (more than {1} min is stale)", ["25", "20"]]], "text": "no report for 25 min (more than 20 min is stale)"}
    now = time.time()
    wd._sync("fresh", {"stale:lamp": ("warn", "lamp", m)}, now)
    wd._sync("fresh", {}, now + 90)
    rows = events()["events"]
    assert [r["kind"] for r in rows] == ["solved", "issue"] and rows[0]["seconds"] == 90 and rows[0]["parts"] == m["parts"] and rows[1]["level"] == "warn"
    assert rows[0]["device"] == "lamp" and rows[0]["cat"] == "errors"
    for d in (wd._issues, wd._closed):
        d.clear()
    wd._resolved.clear()


def test_a_merged_devices_history_reads_its_former_ids_too(db, monkeypatch):
    monkeypatch.setattr(core, "aliases", lambda dev: ["lamp_ir"] if dev == "lamp" else [])
    where, args = hist._where("", "lamp", "", "", "", "", "")
    assert "device IN (?,?)" in where and args[:2] == ["lamp", "lamp_ir"]
