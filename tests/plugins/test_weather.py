"""weather: the forecast reaches /api/weather and the compact panel format; published retained."""
import sys

import pytest
from fastapi import HTTPException

import app as core

me = sys.modules["yashome_plugins.weather"]

SAMPLE = {"latitude": 51.5, "longitude": -0.1, "timezone": "Europe/London",
          "current": {"temperature_2m": 24.6, "weather_code": 2, "is_day": 1, "apparent_temperature": 25.2,
                      "relative_humidity_2m": 61, "wind_speed_10m": 3.4},
          "hourly": {"time": [f"2026-09-24T{h:02d}:00" for h in range(24)], "temperature_2m": [20 + h / 4 for h in range(24)],
                     "weather_code": [1] * 24, "precipitation_probability": [5] * 24, "is_day": [1] * 24},
          "daily": {"time": ["2026-09-24", "2026-09-25"], "weather_code": [2, 61], "temperature_2m_max": [29.4, 27],
                    "temperature_2m_min": [21, 20.5], "precipitation_probability_max": [0, 70]}}


@pytest.fixture
def fed(monkeypatch):
    monkeypatch.setattr(me, "_get_json", lambda url, timeout: SAMPLE)
    monkeypatch.setattr(me, "_lat", "51.5")
    monkeypatch.setattr(me, "_lon", "-0.1")
    me._fetch()


def test_api_and_compact(fed):
    out = me.weather()
    assert out["current"]["temperature_2m"] == 24.6 and out["age"] >= 0
    c = me.compact()
    assert (c["t"], c["f"], c["h"], c["w"]) == (25, 25, 61, 3)
    assert len(c["hr"]) == 8 and c["hr"][1]["k"] == "03:00"
    assert [d["k"] for d in c["dy"]] == ["today", "Fri"] and c["dy"][1]["p"] == 70


def test_published_retained(fed, monkeypatch):
    sent = []

    class Cli:
        def is_connected(self):
            return True

        def publish(self, topic, payload, retain=False):
            sent.append((topic, retain))
    monkeypatch.setattr(core, "_mqtt_client", Cli())
    assert me._publish() and sent == [("home/weather", True)]


def test_no_coordinates_is_503(monkeypatch):
    monkeypatch.setattr(me, "_lat", "")
    with pytest.raises(HTTPException) as e:
        me.weather()
    assert e.value.status_code == 503


def test_failed_publish_is_not_counted(fed, monkeypatch):
    class Cli:
        def is_connected(self):
            return True

        def publish(self, topic, payload, retain=False):
            class R:
                rc = 4                                   # MQTT_ERR_NO_CONN
            return R()
    monkeypatch.setattr(core, "_mqtt_client", Cli())
    assert me._publish() is False


def test_locate_uses_https(monkeypatch):
    seen = []
    monkeypatch.setattr(me, "_lat", "")
    monkeypatch.setattr(me, "_lon", "")
    monkeypatch.setattr(me, "_get_json", lambda url, timeout: seen.append(url) or
                        {"success": True, "latitude": 51.5, "longitude": -0.1})
    me._locate()
    assert seen[0].startswith("https://") and (me._lat, me._lon) == ("51.5", "-0.1")


def test_every_reconnect_republishes_and_a_failed_one_is_owed(fed, monkeypatch):
    sent = []

    class Cli:
        rc = 0

        def is_connected(self):
            return True

        def publish(self, topic, payload, retain=False):
            sent.append(topic)
            return self
    monkeypatch.setattr(core, "_mqtt_client", None)
    assert me._on_connect in core.ON_MQTT_CONNECT
    me._owed.clear()
    me._on_connect(Cli())
    assert sent == ["home/weather"] and not me._owed.is_set()
    bad = Cli()
    bad.rc = 4
    me._on_connect(bad)
    assert me._owed.is_set()
    me._owed.clear()
