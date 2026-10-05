"""Weather for the panels: Open-Meteo (no key) every 15 minutes, GET /api/weather for the
dashboard and Android panels, a compact retained copy on home/weather for the ESP panels."""
import json
import os
import threading
import time
import urllib.request
from datetime import datetime

from fastapi import HTTPException

import app as core

PLUGIN = {"name": "Weather", "version": "1.0.0"}

TOPIC = "home/weather"                    # the ESP panels read this retained topic
EVERY = 900
_lat = os.environ.get("HOME_LAT", "").strip()
_lon = os.environ.get("HOME_LON", "").strip()
_weather: dict = {}
_lock = threading.Lock()
_owed = threading.Event()                 # a publish is owed: a fresh fetch, or one that failed
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _get_json(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as r:      # noqa: S310 - fixed https/http URLs
        return json.loads(r.read().decode("utf-8"))


def _fetch():
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
           "precipitation,weather_code,wind_speed_10m"
           "&hourly=temperature_2m,weather_code,precipitation_probability,is_day&forecast_hours=24"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
           "sunrise,sunset&forecast_days=7&timezone=auto&wind_speed_unit=ms") % (_lat, _lon)
    d = _get_json(url, 20)
    with _lock:
        _weather.clear()
        _weather.update({"fetched_at": time.time(), "lat": d.get("latitude"), "lon": d.get("longitude"),
                         "timezone": d.get("timezone"), "current": d.get("current") or {},
                         "hourly": d.get("hourly") or {}, "daily": d.get("daily") or {}})


def _locate():
    """No HOME_LAT/HOME_LON: the area of the public IP (district accuracy is enough), over HTTPS."""
    global _lat, _lon
    d = _get_json("https://ipwho.is/?fields=success,latitude,longitude", 10)
    if not d.get("success"):
        raise RuntimeError(f"ipwho.is: {d.get('message') or 'no answer'}")
    _lat, _lon = str(d["latitude"]), str(d["longitude"])
    core.log.info("[weather] HOME_LAT/HOME_LON not set: located by IP (set them in .env to skip this)")


def compact() -> dict:
    """Compact JSON for the ESP panels: current, 8 points 3 hours apart, 7 days."""
    with _lock:
        c, h, d = (dict(_weather.get(k) or {}) for k in ("current", "hourly", "daily"))
    r = lambda v: int(round(float(v))) if v is not None else 0
    out = {"t": r(c.get("temperature_2m")), "c": int(c.get("weather_code") or 0), "d": int(c.get("is_day", 1) or 0),
           "f": r(c.get("apparent_temperature")), "h": r(c.get("relative_humidity_2m")), "w": r(c.get("wind_speed_10m")),
           "hr": [], "dy": []}
    times = h.get("time") or []
    for i in range(0, min(len(times), 24), 3):
        out["hr"].append({"k": str(times[i])[11:16], "c": int((h.get("weather_code") or [0] * 24)[i] or 0),
                          "d": int((h.get("is_day") or [1] * 24)[i] or 0), "t": r((h.get("temperature_2m") or [0] * 24)[i]),
                          "p": r((h.get("precipitation_probability") or [0] * 24)[i])})
    for i, day in enumerate((d.get("time") or [])[:7]):
        try:
            wd = datetime.strptime(str(day), "%Y-%m-%d").weekday()
        except ValueError:
            wd = -1
        out["dy"].append({"k": "today" if i == 0 else (_DOW[wd] if wd >= 0 else ""), "wd": wd,
                          "c": int((d.get("weather_code") or [0] * 7)[i] or 0),
                          "hi": r((d.get("temperature_2m_max") or [0] * 7)[i]), "lo": r((d.get("temperature_2m_min") or [0] * 7)[i]),
                          "p": r((d.get("precipitation_probability_max") or [0] * 7)[i])})
    return out


def _publish(cli=None) -> bool:
    """True only when the broker took it (a failed publish is retried on the next tick)."""
    cli = cli or core._mqtt_client
    if cli is None or not cli.is_connected() or not _weather:
        return False
    try:
        info = cli.publish(TOPIC, json.dumps(compact(), ensure_ascii=False, separators=(",", ":")), retain=True)
    except Exception as e:
        core.log.warning(f"[weather] publish failed: {e}")
        return False
    return getattr(info, "rc", 0) == 0


def _on_connect(cli):
    """Every broker (re)connect, however short the outage: the retained copy at once."""
    if not _publish(cli):
        _owed.set()


def _loop():
    """Fetch every 15 minutes (2 minutes after a failure); publish after each fetch and every
    30 s until a publish succeeds (reconnects publish in _on_connect)."""
    next_fetch = 0.0
    while True:
        now = time.time()
        if now >= next_fetch:
            try:
                if not (_lat and _lon):
                    _locate()
                _fetch()
                next_fetch = now + EVERY
                _owed.set()
                core.log.info(f"[weather] {_weather['current'].get('temperature_2m')} C, "
                              f"code {_weather['current'].get('weather_code')}")
            except Exception as e:
                core.log.warning(f"[weather] fetch failed: {e}")
                next_fetch = now + 120
        if _owed.is_set() and _publish():
            _owed.clear()
        time.sleep(30)


@core.app.get("/api/weather")
def weather():
    if not (_lat and _lon):
        raise HTTPException(503, "home coordinates unknown yet (HOME_LAT/HOME_LON in .env, or the city by IP)")
    with _lock:
        if not _weather:
            raise HTTPException(503, "no forecast fetched yet")
        out = dict(_weather)
    out["age"] = round(time.time() - out["fetched_at"])
    return out


core.STARTUP_TASKS.append(_loop)
core.ON_MQTT_CONNECT.append(_on_connect)
