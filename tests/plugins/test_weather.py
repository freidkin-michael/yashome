"""weather: the forecast reaches /api/weather and the compact panel format, published retained; several models must agree on
rain; "now" is the nearest IMS station's last observation (rain, sky from radiation) when it is near and fresh, else the models
with the reason in Health; Settings -> Weather keeps the place, the station and the model. Fixtures only, no network."""
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.weather"]
FX = Path(__file__).parent / "weather_fixtures"
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
XML = (FX / "ims_lasthour.xml").read_text(encoding="utf-8")
CONS = json.loads((FX / "openmeteo_consensus.json").read_text(encoding="utf-8"))
OBS_AT = me.ims_last(XML)[54]["time"]                     # Bet Dagan's latest observation in the fixture
BET_DAGAN = (32.009, 34.814)

SAMPLE = {"latitude": 51.5, "longitude": -0.1, "timezone": "Europe/London",
          "current": {"temperature_2m": 24.6, "weather_code": 2, "is_day": 1, "apparent_temperature": 25.2,
                      "relative_humidity_2m": 61, "wind_speed_10m": 3.4},
          "hourly": {"time": [f"2026-09-24T{h:02d}:00" for h in range(24)], "temperature_2m": [20 + h / 4 for h in range(24)],
                     "weather_code": [1] * 24, "precipitation_probability": [5] * 24, "is_day": [1] * 24},
          "daily": {"time": ["2026-09-24", "2026-09-25"], "weather_code": [2, 61], "temperature_2m_max": [29.4, 27],
                    "temperature_2m_min": [21, 20.5], "precipitation_probability_max": [0, 70]}}


@pytest.fixture
def place(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.delenv("HOME_LON", raising=False)
    me._save({"lat": 51.5, "lon": -0.1, "place": "London", "station": "auto", "model": "best_match"})
    return tmp_path


@pytest.fixture
def fed(place, monkeypatch):
    monkeypatch.setattr(me, "_get_json", lambda url, timeout: SAMPLE)
    monkeypatch.setattr(me, "_get_text", lambda url, timeout: XML)
    me._fetch()


def test_api_and_compact(fed):
    out = me.weather()
    assert out["current"]["temperature_2m"] == 24.6 and out["age"] >= 0
    assert out["now_source"]["kind"] == "model" and "km away" in out["now_source"]["why"]     # London: no IMS station near
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


def test_no_place_is_503(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "SETTINGS", tmp_path / "none.json")
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


def test_the_ip_only_suggests_the_place(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(me, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(me, "_get_json", lambda url, timeout: seen.append(url) or {"success": True, "latitude": 51.5, "longitude": -0.1, "city": "London"})
    me._locate()
    s = me.settings()
    assert seen[0].startswith("https://") and (s["lat"], s["lon"], s["guessed"]) == (51.5, -0.1, True)


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


# ---- several models ----
def test_rain_only_when_most_models_say_so():
    assert me.consensus_code([53, 2, 2, 2]) == 2                  # 27.10 10:30: ECMWF drizzle alone is not rain
    assert me.consensus_code([53, 80, 2, 3]) == 2                 # two of four: still dry
    assert me.consensus_code([61, 61, 63, 2]) == 61
    assert me.consensus_code([80, 81, 2]) == 80                   # a tie: the milder
    assert me.consensus_code([0, 1, 3, 3]) == 1
    assert me.consensus_code([]) == 0


def test_the_models_merge_into_one_forecast():
    m = me._merge(CONS)
    assert len(m["hourly"]["time"]) == 24 and len(m["hourly"]["weather_code"]) == 24 and len(m["daily"]["weather_code"]) == 7
    i = 0
    codes = [CONS["hourly"][f"weather_code_{k}"][i] for k in me.CONSENSUS]
    assert m["current"]["weather_code"] == me.consensus_code(codes)
    assert all(0 <= p <= 100 for p in m["hourly"]["precipitation_probability"])


def test_a_merged_zero_degrees_now_stays_zero():
    d = json.loads(json.dumps(CONS))
    for k in me.CONSENSUS:
        d["hourly"][f"temperature_2m_{k}"] = [0.0] * len(d["hourly"][f"temperature_2m_{k}"])
    d["current"] = {**d.get("current", {}), "time": d["hourly"]["time"][0], "temperature_2m": 7.5}
    assert me._merge(d)["current"]["temperature_2m"] == 0.0               # not the single model's 7.5


# ---- now: the station ----
def test_the_ims_xml_gives_each_station_its_latest():
    obs = me.ims_last(XML)
    assert {54, 85, 178, 78, 64} <= set(obs) and "TD" in obs[54] and "Grad" in obs[85]
    assert obs[54]["time"] == max(me.ims_last(XML)[54]["time"] for _ in [0])


def test_a_dry_station_with_the_sun_at_a_half_reads_partly_cloudy():
    obs = me.ims_last(XML)
    obs[54]["Rain"] = 0.0
    s = next(x for x in me.stations_near(*BET_DAGAN) if x["num"] == 54)
    now = obs[85]["time"]
    obs[85]["Grad"] = me.clear_sky(*BET_DAGAN, now) * 0.5
    obs[85]["grads"] = [(t, me.clear_sky(*BET_DAGAN, t) * 0.5) for t, _ in obs[85]["grads"]]
    out = me.now_from_station({"weather_code": 80, "precipitation": 0.1, "temperature_2m": 25.6}, obs, s, *BET_DAGAN, now=now)
    assert out["weather_code"] == 2 and out["precipitation"] == 0.0 and out["temperature_2m"] == obs[54]["TD"]


def test_one_passing_cloud_is_not_overcast():
    obs = me.ims_last(XML)
    assert len(obs[85]["grads"]) >= 4                                      # the hour's 10-min samples are kept
    now = obs[85]["time"]
    obs[85]["grads"] = [(t, me.clear_sky(*BET_DAGAN, t) * (0.1 if t == now else 0.95)) for t, _ in obs[85]["grads"]]
    assert me.sky_code(me.sky_ratio(obs, *BET_DAGAN, now)) in (0, 1)        # one dark sample among bright ones
    obs[85]["grads"] = [(t, me.clear_sky(*BET_DAGAN, t) * 0.1) for t, _ in obs[85]["grads"]]
    assert me.sky_code(me.sky_ratio(obs, *BET_DAGAN, now)) == 3


def test_rain_at_the_station_is_rain_now():
    obs = me.ims_last(XML)
    obs[54]["Rain"] = 0.3
    s = next(x for x in me.stations_near(*BET_DAGAN) if x["num"] == 54)
    out = me.now_from_station({"weather_code": 2}, obs, s, *BET_DAGAN, now=obs[54]["time"])
    assert (out["weather_code"], out["precipitation"]) == (61, 0.3)
    obs[54]["Rain"] = 2.0
    assert me.now_from_station({"weather_code": 2}, obs, s, *BET_DAGAN, now=obs[54]["time"])["weather_code"] == 65


def test_far_or_old_falls_back_to_the_models(monkeypatch):
    monkeypatch.setattr(me, "_get_text", lambda url, timeout: XML)
    cur, src = me._now({"weather_code": 3}, *BET_DAGAN, "auto", now=OBS_AT + 60)
    assert src["kind"] == "station" and src["num"] == 54 and src["km"] < 1
    cur, src = me._now({"weather_code": 3}, *BET_DAGAN, "auto", now=OBS_AT + 2 * 3600)
    assert src["kind"] == "model" and "last hour" in src["why"]
    fetched = []
    monkeypatch.setattr(me, "_get_text", lambda url, timeout: fetched.append(url) or XML)
    cur, src = me._now({"weather_code": 3}, 51.5, -0.1, "auto", now=OBS_AT + 60)
    assert src["kind"] == "model" and src["reason"] == "far" and "km away" in src["why"]
    assert fetched == []                                                  # far: the IMS file is not downloaded
    cur, src = me._now({"weather_code": 3}, *BET_DAGAN, "99999", now=OBS_AT + 60)
    assert src["kind"] == "station" and src["num"] == 54 and "99999 was not found" in src["why"]

    def down(url, timeout):
        raise OSError("down")
    monkeypatch.setattr(me, "_get_text", down)
    assert me._now({"weather_code": 3}, *BET_DAGAN, "auto")[1]["kind"] == "model"


def test_the_bundled_station_table_says_where_it_comes_from():
    head = json.loads((Path(me.__file__).parent / "ims_stations.json").read_text(encoding="utf-8"))
    assert head["source"].startswith("https://ims.gov.il/") and head["fetched"] and len(head["stations"]) > 50
    assert all({"num", "name", "lat", "lon", "temp", "rad"} <= set(s) for s in head["stations"])
    assert next(s for s in head["stations"] if s["num"] == 85)["at_of"] == 54                      # the mast stands at Bet Dagan


# ---- Settings -> Weather ----
def test_the_settings_are_checked_and_kept(place):
    c = TestClient(core.app)
    ok = {"lat": 32.009, "lon": 34.814, "place": "Bet Dagan", "station": "auto", "model": "consensus"}
    assert c.put("/api/weather/settings", headers=HOUSE, json={**ok, "model": "magic"}).status_code == 400
    assert c.put("/api/weather/settings", headers=HOUSE, json={**ok, "station": "99999"}).status_code == 400
    assert c.put("/api/weather/settings", headers=HOUSE, json={**ok, "station": "85"}).status_code == 400          # a radiation mast: no thermometer
    assert c.put("/api/weather/settings", headers=HOUSE, json={**ok, "lat": 120}).status_code == 422
    assert c.put("/api/weather/settings", headers=HOUSE, json={**ok, "place": "a\x01b"}).status_code == 400
    assert c.put("/api/weather/settings", headers=HOUSE, json={**ok, "station": "54"}).status_code == 200
    g = c.get("/api/weather/settings", headers=HOUSE).json()
    assert (g["place"], g["station"], g["model"], g["guessed"]) == ("Bet Dagan", "54", "consensus", False)
    assert g["stations"][0]["num"] == 54 and g["stations"][0]["km"] < 1


def test_the_city_search(place, monkeypatch):
    seen = []
    monkeypatch.setattr(me, "_get_json", lambda url, timeout: seen.append(url) or {"results": [
        {"name": "Ришон-ле-Цион", "admin1": "Центральный округ", "country": "Израиль", "latitude": 31.97, "longitude": 34.79}]})   # i18n
    c = TestClient(core.app)
    assert c.get("/api/weather/geocode", params={"q": "x"}, headers=HOUSE).status_code == 400
    r = c.get("/api/weather/geocode", params={"q": "Ришон"}, headers=HOUSE).json()   # i18n
    assert r == [{"name": "Ришон-ле-Цион", "admin": "Центральный округ", "country": "Израиль", "lat": 31.97, "lon": 34.79}]   # i18n
    assert "language=ru" in seen[0] and seen[0].startswith("https://")


def test_health_says_a_guessed_place_and_a_now_from_the_models_only_when_it_is_wrong(fed, monkeypatch):
    me._save({**me.settings(), "guessed": True})
    rows = me._health()
    text = json.dumps(rows, ensure_ascii=False)
    assert len(rows) == 2 and "guessed from the internet address" in text and "forecast models" not in text, text    # London: far, no warning
    me._weather["now_source"] = {"kind": "model", "reason": "stale", "why": "the station X has no observation in the last hour"}
    assert "from the forecast models" in json.dumps(me._health(), ensure_ascii=False)
