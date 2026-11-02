"""Weather for the house: the forecast from Open-Meteo (no key; several models agree, or one chosen), the weather NOW from the
nearest IMS station's last observation (public, no token) when one is close and fresh. GET /api/weather for the dashboard and
the Android panels, a compact retained copy on home/weather for the ESP panels. The place, the station and the model are set
in Settings -> Weather (plugin-data/weather/settings.json); the public IP only suggests the place on the first run."""
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from pydantic import Field

import app as core

PLUGIN = {"name": "Weather", "version": "2.0.0", "icon": "cloud"}

TOPIC = "home/weather"                    # the ESP panels read this retained topic
EVERY = 900
SETTINGS = core.plugin_data("weather") / "settings.json"
STATIONS = json.loads((Path(__file__).parent / "ims_stations.json").read_text(encoding="utf-8"))["stations"]
IMS_XML = "https://ims.gov.il/sites/default/files/ims_data/xml_files/imslasthour.xml"
IMS_TZ = ZoneInfo("Asia/Jerusalem")       # the IMS observation times are local
NEAR_KM, FRESH_S = 25.0, 3600             # a farther or older observation is not "now" here: the models say it
CONSENSUS = ("ecmwf_ifs025", "icon_seamless", "ukmo_seamless", "gfs_seamless")
MODELS = ("consensus", "best_match") + CONSENSUS + ("meteofrance_seamless",)
WET = 51                                  # a WMO code from here on is drizzle, rain, snow, showers or a storm
_weather: dict = {}
_lock = threading.Lock()
_owed = threading.Event()                 # a publish is owed: a fresh fetch, or one that failed
_now_fetch = threading.Event()            # the settings changed: fetch at once
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_CUR = "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m,cloud_cover"
_HR = "temperature_2m,weather_code,precipitation_probability,is_day"
_DY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"


def _get_json(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as r:      # noqa: S310 - fixed https/http URLs
        return json.loads(r.read().decode("utf-8"))


def _get_text(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "yashome-weather/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 - the fixed IMS address
        return r.read().decode("utf-8", "replace")


# ---- the settings: the place, the station, the model ----
def settings() -> dict:
    try:
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    return {"lat": None, "lon": None, "place": "", "guessed": False, "station": "auto", "model": "consensus", **s}


def _save(s: dict) -> None:
    keep = {k: s[k] for k in ("lat", "lon", "place", "guessed", "station", "model") if k in s}
    core.atomic_json_dump(SETTINGS, keep, ensure_ascii=False, indent=1)     # fsync, a unique temp: no torn file


def _locate() -> None:
    """No place set: the area of the public IP, saved as a suggestion the owner is asked to correct (Settings -> Weather)."""
    d = _get_json("https://ipwho.is/?fields=success,latitude,longitude,city", 10)
    if not d.get("success"):
        raise RuntimeError(f"ipwho.is: {d.get('message') or 'no answer'}")
    _save({**settings(), "lat": float(d["latitude"]), "lon": float(d["longitude"]), "place": str(d.get("city") or "")[:80], "guessed": True})
    core.log.info("[weather] no place set: located by the public IP (Settings -> Weather sets the exact one)")


def km(lat1, lon1, lat2, lon2) -> float:
    p, q = math.radians(lat1), math.radians(lat2)
    a = math.sin((q - p) / 2) ** 2 + math.cos(p) * math.cos(q) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def stations_near(lat, lon, temp=False) -> list:
    """The IMS stations by distance; temp: only those with a thermometer (the station of "now", not a radiation mast)."""
    return sorted(({**s, "km": round(km(lat, lon, s["lat"], s["lon"]), 1)} for s in STATIONS if s.get("temp") or not temp), key=lambda s: s["km"])


# ---- the models: one, or several that must agree on rain ----
def consensus_code(codes: list) -> int:
    """The WMO code the models agree on: rain (or snow, a storm) only when MORE than half say so, then the commonest of those
    (the milder on a tie); else the middle of the dry codes (how cloudy)."""
    codes = [int(c) for c in codes if c is not None]
    if not codes:
        return 0
    wet = [c for c in codes if c >= WET]
    if len(wet) * 2 > len(codes):
        return min(wet, key=lambda c: (-wet.count(c), c))
    dry = sorted(c for c in codes if c < WET)
    return dry[(len(dry) - 1) // 2]


def _median(vals):
    v = sorted(x for x in vals if x is not None)
    return None if not v else v[len(v) // 2] if len(v) % 2 else round((v[len(v) // 2 - 1] + v[len(v) // 2]) / 2, 1)


def _merge(d: dict) -> dict:
    """A several-models answer -> one forecast: codes by consensus_code, numbers by the median, the chance of rain = the share
    of the models that say rain. The current hour's codes stand for "now" (Open-Meteo's own current is one model only)."""
    def pick(block, var):
        return [block.get(f"{var}_{m}") or [] for m in CONSENSUS]

    def merged(block, var, how):
        cols = pick(block, var)
        n = max((len(c) for c in cols), default=0)
        return [how([c[i] if i < len(c) else None for c in cols]) for i in range(n)]
    wet_share = lambda vals: round(100 * sum(1 for c in vals if c is not None and c >= WET) / max(1, sum(1 for c in vals if c is not None)))  # noqa: E731
    h, dd = d.get("hourly") or {}, d.get("daily") or {}
    hourly = {"time": h.get("time") or [], "temperature_2m": merged(h, "temperature_2m", _median),
              "weather_code": merged(h, "weather_code", consensus_code), "precipitation_probability": merged(h, "weather_code", wet_share),
              "is_day": merged(h, "is_day", lambda v: max((x for x in v if x is not None), default=1))}
    daily = {"time": dd.get("time") or [], "weather_code": merged(dd, "weather_code", consensus_code),
             "temperature_2m_max": merged(dd, "temperature_2m_max", _median), "temperature_2m_min": merged(dd, "temperature_2m_min", _median),
             "precipitation_probability_max": merged(dd, "weather_code", wet_share),
             "sunrise": pick(dd, "sunrise")[0], "sunset": pick(dd, "sunset")[0]}
    cur = dict(d.get("current") or {})
    now = str(cur.get("time") or "")[:13]
    i = next((k for k, t in enumerate(hourly["time"]) if str(t)[:13] == now), 0)
    codes = [c[i] if i < len(c) else None for c in pick(h, "weather_code")]
    t = _median([c[i] if i < len(c) else None for c in pick(h, "temperature_2m")])
    cur.update(weather_code=consensus_code(codes), precipitation=cur.get("precipitation") if consensus_code(codes) >= WET else 0.0,
               temperature_2m=cur.get("temperature_2m") if t is None else t)
    return {"current": cur, "hourly": hourly, "daily": daily}


def _model_fetch(lat, lon, model) -> dict:
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&current=%s&hourly=%s&forecast_hours=24&daily=%s&forecast_days=7"
           "&timezone=auto&wind_speed_unit=ms&models=%s") % (lat, lon, _CUR, _HR, _DY, ",".join(CONSENSUS) if model == "consensus" else model)
    d = _get_json(url, 20)
    out = _merge(d) if model == "consensus" else {k: d.get(k) or {} for k in ("current", "hourly", "daily")}
    out.update(timezone=d.get("timezone"), lat=d.get("latitude"), lon=d.get("longitude"))
    return out


# ---- now: the nearest IMS station ----
def ims_last(xml_text: str) -> dict:
    """The IMS last-hour XML -> {station number: its latest observation} (numbers as floats, "time" as a unix time); a station
    that measures radiation keeps every 10-min sample of the hour too ("grads": [(time, W/m2)])."""
    out, grads = {}, {}
    for o in ET.fromstring(xml_text).iter("Observation"):
        try:
            num = int(o.findtext("stn_num") or "")
            t = datetime.fromisoformat(o.findtext("time_obs") or "").replace(tzinfo=IMS_TZ).timestamp()
        except ValueError:
            continue
        g = (o.findtext("Grad") or "").strip()
        if g:
            try:
                grads.setdefault(num, []).append((t, float(g)))
            except ValueError:
                pass
        if num in out and out[num]["time"] >= t:
            continue
        rec = {"time": t}
        for k in ("TD", "RH", "Rain", "WS", "Grad"):
            v = (o.findtext(k) or "").strip()
            if v:
                try:
                    rec[k] = float(v)
                except ValueError:
                    pass
        out[num] = rec
    for num, gs in grads.items():
        if num in out:
            out[num]["grads"] = sorted(gs)
    return out


def clear_sky(lat, lon, t) -> float:
    """The sun's power on a clear day here and now (W/m2, Haurwitz), 0 at night."""
    d = datetime.fromtimestamp(t, timezone.utc)
    decl = math.radians(23.44) * math.sin(math.radians(360 / 365 * (284 + d.timetuple().tm_yday)))
    ha = math.radians(15 * (d.hour + d.minute / 60 + lon / 15 - 12))
    cz = math.sin(math.radians(lat)) * math.sin(decl) + math.cos(math.radians(lat)) * math.cos(decl) * math.cos(ha)
    return 1098 * cz * math.exp(-0.057 / cz) if cz > 0.08 else 0.0


def sky_code(ratio: float) -> int:
    """The sun's share of a clear day -> clear 0, mainly clear 1, partly cloudy 2, overcast 3."""
    return 0 if ratio >= 0.8 else 1 if ratio >= 0.6 else 2 if ratio >= 0.3 else 3


def sky_ratio(obs: dict, lat, lon, now):
    """The sun's share of a clear day over the last hour at the nearest radiation mast within 25 km: the mean of its 10-min
    samples, each against the clear sky of its own time (one passing cloud is not "overcast"); None without one, or at night."""
    for r in stations_near(lat, lon):
        if not (r["rad"] and r["km"] <= NEAR_KM):
            continue
        rs = [g / cs for t, g in obs.get(r["num"], {}).get("grads", []) if now - FRESH_S <= t <= now and (cs := clear_sky(lat, lon, t)) > 50]
        if rs:
            return sum(rs) / len(rs)
    return None


def now_from_station(cur: dict, obs: dict, s: dict, lat, lon, now=None) -> dict:
    """The model's "now" with the station's truth laid over it: temperature, humidity, wind, rain in the last 10 min (wet or
    dry, how hard), the sky from the radiation of the nearest station that measures it (else the model's dry cloudiness)."""
    now = now or time.time()
    st = obs[s["num"]]
    out = dict(cur)
    out.update({k2: st[k1] for k1, k2 in (("TD", "temperature_2m"), ("RH", "relative_humidity_2m"), ("WS", "wind_speed_10m")) if k1 in st})
    rain = st.get("Rain")
    model = int(cur.get("weather_code") or 0)
    if rain is not None and rain > 0:
        out["weather_code"] = model if model >= 95 else 61 if rain < 0.5 else 63 if rain < 1.5 else 65
        out["precipitation"] = rain
    else:
        ratio = sky_ratio(obs, lat, lon, now)
        if ratio is not None:
            out["weather_code"] = sky_code(ratio)
        else:
            cc = cur.get("cloud_cover")
            out["weather_code"] = (model if model < WET else 3) if cc is None else 0 if cc < 20 else 1 if cc < 50 else 2 if cc < 85 else 3
        out["precipitation"] = 0.0
    return out


def _now(cur: dict, lat, lon, station, now=None) -> tuple:
    """(the current weather, where it came from): the chosen or nearest IMS station when within 25 km and under an hour old."""
    now = now or time.time()
    near = stations_near(lat, lon, temp=True)
    s = near[0] if station == "auto" else next((x for x in near if str(x["num"]) == str(station)), None)
    note = ""
    if s is None:                                         # a chosen station that is gone: the nearest, and it is said
        s, note = near[0], f"the station {station} was not found, the nearest is used"
    if s["km"] > NEAR_KM:                                 # a house outside Israel: the normal case, no warning, no download
        return cur, {"kind": "model", "reason": "far", "why": f"the nearest IMS station ({s['name']}) is {s['km']} km away"}
    try:
        obs = ims_last(_get_text(IMS_XML, 20))
    except Exception as e:                                # noqa: BLE001 - IMS away: the models say "now"
        return cur, {"kind": "model", "reason": "away", "why": f"the IMS stations are not reachable ({type(e).__name__})"}
    if s["num"] not in obs or now - obs[s["num"]]["time"] > FRESH_S:
        return cur, {"kind": "model", "reason": "stale", "why": f"the station {s['name']} has no observation in the last hour"}
    src = {"kind": "station", "name": s["name"], "num": s["num"], "km": s["km"], "age_min": round((now - obs[s["num"]]["time"]) / 60)}
    return now_from_station(cur, obs, s, lat, lon, now), ({**src, "why": note} if note else src)


def _fetch():
    s = settings()
    if s["lat"] is None:
        _locate()
        s = settings()
    d = _model_fetch(s["lat"], s["lon"], s["model"])
    cur, src = _now(d["current"], s["lat"], s["lon"], s["station"])
    with _lock:
        _weather.clear()
        _weather.update({"fetched_at": time.time(), "lat": d.get("lat"), "lon": d.get("lon"), "timezone": d.get("timezone"),
                         "current": cur, "hourly": d["hourly"], "daily": d["daily"], "now_source": src, "model": s["model"]})


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
    cli = cli or core.mqtt_client
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
    """Fetch every 15 minutes (2 minutes after a failure, at once after a settings change); publish after each fetch and
    every 30 s until a publish succeeds (reconnects publish in _on_connect)."""
    next_fetch = 0.0
    while True:
        now = time.time()
        if now >= next_fetch or _now_fetch.is_set():
            _now_fetch.clear()
            try:
                _fetch()
                next_fetch = now + EVERY
                _owed.set()
                core.log.info(f"[weather] {_weather['current'].get('temperature_2m')} C, code {_weather['current'].get('weather_code')}, "
                              f"now from {_weather['now_source'].get('kind')}")
            except Exception as e:
                core.log.warning(f"[weather] fetch failed: {e}")
                next_fetch = now + 120
        if _owed.is_set() and _publish():
            _owed.clear()
        _now_fetch.wait(30)


@core.app.get("/api/weather")
def weather():
    if settings()["lat"] is None:
        raise HTTPException(503, "the house's place is not known yet (Settings -> Weather, or the city by IP)")
    with _lock:
        if not _weather:
            raise HTTPException(503, "no forecast fetched yet")
        out = dict(_weather)
    out["age"] = round(time.time() - out["fetched_at"])
    return out


# ---- Settings -> Weather ----
class WeatherSettings(core.Body):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    place: str = Field(default="", max_length=80)
    station: str = Field(default="auto", max_length=8)
    model: str = Field(default="consensus", max_length=40)


@core.app.get("/api/weather/settings")
def get_settings(request: Request):
    s = settings()
    near = stations_near(s["lat"], s["lon"], temp=True) if s["lat"] is not None else []
    with _lock:
        src = dict(_weather.get("now_source") or {})
    return {**s, "models": list(MODELS), "stations": [{k: x[k] for k in ("num", "name", "km", "rad")} for x in near],
            "near_km": NEAR_KM, "now_source": src}


@core.app.put("/api/weather/settings")
def put_settings(body: WeatherSettings, request: Request):
    place = body.place.strip()
    if any(ord(ch) < 32 for ch in place):
        raise HTTPException(400, "the place's name has a control character")
    if body.model not in MODELS:
        raise HTTPException(400, f"model: one of {', '.join(MODELS)}")
    if body.station != "auto" and not any(str(s["num"]) == body.station and s.get("temp") for s in STATIONS):
        raise HTTPException(400, "station: auto or the number of an IMS station")
    _save({"lat": round(body.lat, 5), "lon": round(body.lon, 5), "place": place, "guessed": False, "station": body.station, "model": body.model})
    _now_fetch.set()
    return {"ok": True}


@core.app.get("/api/weather/geocode")
def geocode(q: str, request: Request):
    """City search for Settings -> Weather (Open-Meteo's geocoding, no key): up to 6 places."""
    q = q.strip()
    if not 2 <= len(q) <= 60 or any(ord(ch) < 32 for ch in q):
        raise HTTPException(400, "a place's name: 2 to 60 characters")
    lang = "ru" if re.search(r"[\u0410-\u044f\u0401\u0451]", q) else "en"   # i18n: a Russian letter asks in Russian
    try:
        d = _get_json("https://geocoding-api.open-meteo.com/v1/search?count=6&language=%s&name=%s" % (lang, urllib.parse.quote(q)), 10)
    except Exception as e:                                # noqa: BLE001
        raise HTTPException(503, f"the place search is not reachable ({type(e).__name__})") from e
    return [{"name": r.get("name"), "admin": r.get("admin1") or "", "country": r.get("country") or "",
             "lat": r.get("latitude"), "lon": r.get("longitude")} for r in (d.get("results") or [])]


core.STARTUP_TASKS.append(_loop)
core.ON_MQTT_CONNECT.append(_on_connect)


def _health():
    with _lock:
        at, src = _weather.get("fetched_at"), dict(_weather.get("now_source") or {})
    s = settings()
    if not at:
        return [core.health_row("weather", "warn", "Weather", "no forecast yet", "The house needs internet; its place is set on the Weather page", link="weather")]
    age = time.time() - at
    if age > 3600:
        return [core.health_row("weather", "warn", "Weather", "the forecast is {0} h old",
                                "Is the internet reachable?", (round(age / 3600, 1),), cmd="docker compose logs home | grep weather")]
    rows = [core.health_row("weather", "ok", "Weather")]
    if s.get("guessed"):
        rows.append(core.health_row("weather-place", "warn", "Weather place", "guessed from the internet address",
                                    "Find the city or enter the latitude and longitude", link="weather"))
    if src.get("kind") == "model" and src.get("reason") != "far":         # far from every station is how it is: no warning
        rows.append(core.health_row("weather-now", "warn", "Weather now", "from the forecast models: {0}",
                                    "Pick the station; the IMS stations are public, no key", (src.get("why"),), link="weather"))
    return rows


core.HEALTH_CHECKS.append(_health)
