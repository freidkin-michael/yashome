"""The health page: the core's and the plug-ins' rows, worst first; a hung or broken check is a row of its
own and never holds the page; only the dashboard's own token reads it."""
import os
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app as core

HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


def test_the_report_has_rows_of_core_and_plug_ins_worst_first():
    r = TestClient(core.app).get("/api/health/report", headers=HOUSE)
    assert r.status_code == 200
    rows = r.json()["rows"]
    ids = {x["id"] for x in rows}
    assert {"broker", "disk", "backup", "zigbee", "build", "presence", "weather", "panels-discover"} <= ids
    order = {"bad": 0, "warn": 1, "ok": 2}
    assert [order[x["state"]] for x in rows] == sorted(order[x["state"]] for x in rows)
    assert r.json()["worst"] == rows[0]["state"]
    assert all(set(x) == {"id", "state", "title", "detail", "fix", "args", "link", "cmd"} for x in rows)


def test_a_hung_check_holds_one_thread_and_never_the_page(monkeypatch):
    stop = threading.Event()

    def hang():
        stop.wait(30)
        return []
    monkeypatch.setattr(core, "HEALTH_CHECKS", core.HEALTH_CHECKS + [hang])
    monkeypatch.setattr(core, "HEALTH_TIMEOUT_S", 0.5)
    c = TestClient(core.app)
    try:
        for _ in range(5):
            t0 = time.monotonic()
            rows = c.get("/api/health/report", headers=HOUSE).json()["rows"]
            assert time.monotonic() - t0 < 1.5
            assert any(x["detail"] == "did not answer for {1} s" for x in rows)
        assert len([t for t in threading.enumerate() if t.name.startswith("health-")]) <= 1   # other tests' threads come and go
        assert all(t.daemon for t in threading.enumerate() if t.name.startswith("health-"))
    finally:
        stop.set()


def test_broken_checks_and_rows_are_rows(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(core, "HEALTH_CHECKS", core.HEALTH_CHECKS + [
        boom, lambda: [{"title": "x"}], lambda: [{"title": "x", "state": "weird"}], lambda: "nope"])
    r = TestClient(core.app).get("/api/health/report", headers=HOUSE)
    assert r.status_code == 200
    bad = [x for x in r.json()["rows"] if x["id"].startswith("check:")]
    assert any(x["detail"] == "failed: {1}" and "RuntimeError" in x["args"][1] for x in bad)
    assert sum(x["detail"] == "gave a row the page cannot show" for x in bad) == 2
    assert any(x["detail"] == "gave no list of rows" for x in bad)


def test_checks_are_named_by_their_plug_in():
    esp = sys.modules["yashome_plugins.panels._esp"]
    assert core._check_name(esp._health) == "panels" and core._check_name(core._core_health) == "core"


def test_only_the_house_token_reads_it(monkeypatch):
    c = TestClient(core.app)
    assert c.get("/api/health/report").status_code == 401
    monkeypatch.setattr(core, "TOKEN_CHECKS", core.TOKEN_CHECKS + [lambda tok, m, p: tok == "a-full-phone"])
    assert c.get("/api/health/report", headers={"Authorization": "Bearer a-full-phone"}).status_code == 403


@pytest.mark.parametrize("free, state", [(5e9, "ok"), (5e8, "warn"), (1e8, "bad")])
def test_disk_thresholds(monkeypatch, free, state):
    monkeypatch.setattr(core.shutil, "disk_usage", lambda p: type("U", (), {"free": free})())
    row = next(x for x in core._core_health() if x["id"] == "disk")
    assert row["state"] == state


def test_bad_args_and_too_many_rows_are_rows(monkeypatch):
    many = [core.health_row(f"r{i}", "ok", "x") for i in range(120)]
    monkeypatch.setattr(core, "HEALTH_CHECKS", core.HEALTH_CHECKS + [
        lambda: [{"title": "x", "state": "ok", "args": 5}], lambda: many])
    rows = TestClient(core.app).get("/api/health/report", headers=HOUSE).json()["rows"]
    assert any(x["detail"] == "gave a row the page cannot show" for x in rows)
    more = [x for x in rows if x["detail"] == "{1} more rows not shown"]
    assert more and more[0]["args"][1] == "70"


def test_a_slow_check_answers_on_the_next_report_and_a_hung_one_costs_no_wait(monkeypatch):
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.8)
        return [core.health_row("slow", "ok", "Slow")]
    stop = threading.Event()
    monkeypatch.setattr(core, "HEALTH_CHECKS", core.HEALTH_CHECKS + [slow, lambda: stop.wait(30) or []])
    monkeypatch.setattr(core, "HEALTH_TIMEOUT_S", 0.5)
    c = TestClient(core.app)
    try:
        c.get("/api/health/report", headers=HOUSE)
        time.sleep(1.0)
        t0 = time.monotonic()
        rows = c.get("/api/health/report", headers=HOUSE).json()["rows"]
        assert time.monotonic() - t0 < 0.4                    # the hung one is old: no waiting for it
        assert any(x["id"] == "slow" for x in rows) and len(calls) == 1
    finally:
        stop.set()


def test_the_clock_row(monkeypatch):
    def row():
        return next((x for x in core._core_health() if x["id"] == "clock"), None)
    monkeypatch.setitem(core._clock_last, "ts", None)
    monkeypatch.setitem(core._clock_last, "error", None)
    assert row() is None
    monkeypatch.setitem(core._clock_last, "ts", core.time.time())
    assert row()["state"] == "ok"
    monkeypatch.setitem(core._clock_last, "ts", core.time.time() - 600)
    monkeypatch.setitem(core._clock_last, "error", "OSError: x")
    r = row()
    assert r["state"] == "bad" and r["args"] == ["10", "OSError: x"]


def test_the_clock_loop_survives_an_error(monkeypatch):
    class Cli:
        def is_connected(self):
            return True

        def publish(self, *a, **k):
            raise OSError("broker gone")

    class Stop(BaseException):
        pass

    def sleep(_s):
        raise Stop
    monkeypatch.setattr(core, "_mqtt_client", Cli())
    monkeypatch.setattr(core.time, "sleep", sleep)
    with pytest.raises(Stop):
        core._clock_loop()
    assert core._clock_last["error"] == "OSError: broker gone"


def test_a_container_left_with_no_network_is_red_and_points_at_restart(monkeypatch):
    monkeypatch.setenv("HELPER_KEY", "k")
    seen = []

    def fake(method, path, body=None, timeout=15):
        seen.append((method, path))
        return {"containers": [{"name": "yashome-zigbee2mqtt-1", "service": "zigbee2mqtt", "networks": [], "netless": True},
                               {"name": "yashome-home-1", "service": "home", "networks": ["yashome_default"], "netless": False}]}
    monkeypatch.setattr(core, "_helper", fake)
    rows = core._containers_health()
    assert seen == [("GET", "/v1/containers")]
    assert [(r["id"], r["state"], r["link"], r["args"]) for r in rows] == \
        [("containers:yashome-zigbee2mqtt-1", "bad", "restart", ["yashome-zigbee2mqtt-1", "zigbee2mqtt"])]
    monkeypatch.setattr(core, "_helper", lambda *a, **k: {"containers": [{"name": "a", "networks": ["n"], "netless": False}]})
    assert [(r["state"], r["args"]) for r in core._containers_health()] == [("ok", ["1"])]

    def down(*a, **k):
        raise core.HTTPException(503, "the helper does not answer")
    monkeypatch.setattr(core, "_helper", down)
    assert [r["state"] for r in core._containers_health()] == ["warn"]

    def old(*a, **k):
        raise core.HTTPException(404, "helper: not found")
    monkeypatch.setattr(core, "_helper", old)
    assert core._containers_health() == []
    monkeypatch.delenv("HELPER_KEY")
    assert core._containers_health() == []                  # no helper at all: nothing to check with


def test_the_demo_host_is_watched_only_when_set_and_is_yellow_when_it_does_not_answer(monkeypatch):
    import socket
    old = dict(core._house)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    c = TestClient(core.app)
    try:
        core._house.pop("demo_host", None)
        assert core._demo_health() == []
        assert c.put("/api/house", headers=HOUSE, json={"demo_host": "bad host;x"}).status_code == 400
        assert c.put("/api/house", headers=HOUSE, json={"demo_host": f"127.0.0.1:{port}"}).json()["demo_host"] == f"127.0.0.1:{port}"
        assert [(r["id"], r["state"]) for r in core._demo_health()] == [("demo", "ok")]
        srv.close()
        r = core._demo_health()[0]
        assert (r["state"], r["args"][0]) == ("warn", f"127.0.0.1:{port}")
        assert core.demo_host() == ("127.0.0.1", port)
        core._house["demo_host"] = "demo.example.org"
        assert core.demo_host() == ("demo.example.org", 443)
        assert c.put("/api/house", headers=HOUSE, json={"demo_host": ""}).status_code == 200
        assert core._demo_health() == []
    finally:
        srv.close()
        core._house.clear()
        core._house.update(old)
