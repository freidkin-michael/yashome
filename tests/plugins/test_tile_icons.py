"""An Android tile may carry an icon from the known set; an unknown icon, or an icon on an ESP panel, is refused."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.panels"]
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(me, "_layouts", {"tab": {"hw": "android", "w": 1024, "h": 600, "widgets": []},
                                         "esp": {"hw": "waveshare7", "w": 800, "h": 480, "widgets": []}})
    monkeypatch.setattr(me, "_save_layouts", lambda: None)
    monkeypatch.setattr(me, "_publish", lambda name: None)
    yield TestClient(core.app)


def tile(icon):
    return {"widgets": [{"id": "t", "type": "label", "x": 0, "y": 0, "w": 100, "h": 60, "text": "Lamp", "icon": icon}]}


def test_a_known_icon_on_an_android_tile(c):
    assert c.put("/api/panels/tab/layout", headers=HOUSE, json=tile("bulb")).status_code == 200
    assert c.get("/api/panels/tab/layout", headers=HOUSE).json()["widgets"][0]["icon"] == "bulb"


def test_an_unknown_icon_or_one_on_an_esp_panel_is_refused(c):
    assert c.put("/api/panels/tab/layout", headers=HOUSE, json=tile("rocket")).status_code == 400
    assert c.put("/api/panels/esp/layout", headers=HOUSE, json=tile("bulb")).status_code == 400


def test_the_server_and_the_view_know_the_same_icons():
    import json
    import re
    src = open(os.path.join(os.path.dirname(me.__file__), "static", "view.html"), encoding="utf-8").read()
    block = re.search(r"const TILE_ICONS = (\{.*?\n *\});", src, re.S).group(1)
    assert set(json.loads(block)) == me._TILE_ICONS
