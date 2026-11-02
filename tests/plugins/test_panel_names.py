"""Panel names: a new panel's name is id-like (its topics, an ESP node's name) and new in any case, a renamed one too;
an existing panel with an older name still saves; a changed title is checked (40, unique) and never cut."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.panels"]
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(me, "_layouts", {})
    monkeypatch.setattr(me, "_publish", lambda name: None)
    monkeypatch.setattr(me, "_clear_retained", lambda name: 0)
    monkeypatch.setattr(me, "_save_layouts", lambda: None)
    yield TestClient(core.app)


def put(c, name, **body):
    return c.put(f"/api/panels/{name}/layout", headers=HOUSE, json={"widgets": [], "hw": "waveshare7", **body})


def test_a_new_panel_name_is_id_like_and_new_in_any_case(c):
    assert put(c, "Hall").status_code == 200
    assert put(c, "hall").status_code == 409
    assert put(c, "Hall 2").json()["detail"] == "panel name: 1-32 latin letters, digits, - or _"
    assert put(c, "Hall").status_code == 200                                  # the same panel saves again


def test_an_older_odd_name_still_saves(c):
    me._layouts["Old one"] = {"hw": "waveshare7", "w": 800, "h": 480, "widgets": [], "title": "Old"}
    assert put(c, "Old one", title="Old").status_code == 200


def test_a_changed_title_is_checked_not_cut(c):
    put(c, "a", title="Kitchen")
    assert put(c, "b", title=" kitchen ").status_code == 409
    assert put(c, "b", title="x" * 41).json()["detail"] == "name is too long: at most 40 characters"
    assert put(c, "b", title=" Hall ").status_code == 200 and me._layouts["b"]["title"] == "Hall"


def test_a_rename_is_new_in_any_case(c):
    for n in ("tab1", "Entry"):                                              # Android panels come from bound tablets
        me._layouts[n] = {"hw": "android", "w": 1024, "h": 600, "widgets": [], "title": n}
    assert c.post("/api/panels/tab1/rename", headers=HOUSE, json={"to": "entry"}).status_code == 409
