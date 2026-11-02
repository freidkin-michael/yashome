"""A zigbee friendly_name is checked as zigbee2mqtt allows it: Cyrillic and spaces pass; / # +, control characters, its
own words and an id another device has (in any case) do not; a rename may change only the case of its own name."""
import sys

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.zigbee"]


@pytest.fixture
def devs(monkeypatch):
    monkeypatch.setitem(core.DEVICES, "Кладовка", {"id": "Кладовка", "name": "Кладовка", "transport": "z2m", "controls": []})   # i18n
    monkeypatch.setitem(core.DEVICES, "Porch 3 Gang Switch", {"id": "Porch 3 Gang Switch", "name": "x", "transport": "z2m", "controls": []})


def refused(name, but=""):
    with pytest.raises(HTTPException) as e:
        me._check_key(name, but)
    return e.value.status_code


def test_what_zigbee2mqtt_allows_passes(devs):
    assert me._check_key("  прихожая движение ") == "прихожая движение"   # i18n
    assert me._check_key("Porch 3 Gang Switch 2") == "Porch 3 Gang Switch 2"
    assert me._check_key("КЛАДОВКА", but="Кладовка") == "КЛАДОВКА"                      # its own name in another case   # i18n


def test_what_it_does_not_is_refused(devs):
    assert refused("кладовка") == 409 and refused("porch 3 GANG switch") == 409   # i18n
    for bad in ("a/b", "a#", "a+b", "a|b", "a\tb", "  ", "Bridge", "set"):
        assert refused(bad) == 400, bad
