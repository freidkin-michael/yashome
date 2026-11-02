# weather - the weather now and the forecast

The forecast from Open-Meteo (`api.open-meteo.com`, no account, no key), fetched every 15 minutes (2 minutes after a failure,
at once after a settings change). The weather NOW comes from the nearest station of the Israel Meteorological Service (its
public last-hour file, no key) when one is near and fresh, else from the models. For the dashboard and the Android panels
(`GET /api/weather`) and the ESP panels (the retained topic `home/weather`).

| file | what |
|---|---|
| `__init__.py` | the fetch loop, the models' agreement, the station, `/api/weather`, the settings API, `home/weather`, the health rows |
| `ui.js` | Settings -> House -> Weather |
| `ims_stations.json` | the IMS stations (number, name, place, what they measure), with its source and date in its header |
| `setup/ims_stations.py` | rebuilds `ims_stations.json` from the IMS public station list |
| `i18n.json` | the Russian words |

## Settings -> House -> Weather (`plugin-data/weather/settings.json`)

- **Place of the house**: a city search (Open-Meteo's geocoding) or the latitude and longitude, "On the map" to check it. With
  no place set the house is located once by its public IP (`ipwho.is` over HTTPS) and that guess is saved as a suggestion: the
  page and the health page ask for the exact place until it is saved.
- **Station of the weather now**: the nearest IMS station with a thermometer, or one chosen by name (40 nearest listed).
- **Forecast**: several models (ECMWF IFS, DWD ICON, UK Met Office, NOAA GFS) or one of them, or Open-Meteo's own best match.

## The models' agreement

With several models, an hour or a day is wet (drizzle, rain, snow, showers, a storm) only when MORE than half of them say so,
then their commonest wet code (the milder on a tie); else the middle of their dry codes (how cloudy). Numbers are their median;
the chance of rain is the share of the models that say rain. Open-Meteo's own `current` is one model only, so "now" is the
models' agreement on the current hour.

## The weather now

The station's last observation (every 10 minutes, about 30 minutes late) when the station is within 25 km and the observation
is under an hour old: its temperature, humidity and wind; rain in its last 10 minutes makes it rain now (61 under 0.5 mm, 63
under 1.5 mm, else 65; a storm of the models stays a storm); dry, the sky comes from the radiation of the nearest station that
measures it (its share of a clear day: 0 clear, 1 mainly clear, 2 partly cloudy, 3 overcast), else from the models' cloud cover.
Otherwise the models say it, and the health page says why. `now_source` in `/api/weather` tells which.

## `GET /api/weather` (with a token)

`{fetched_at, lat, lon, timezone, current, hourly, daily, now_source, model, age}`: `current` (temperature, humidity,
feels-like, is_day, precipitation, weather code, wind in m/s, cloud cover), `hourly` for 24 h (temperature, weather code,
precipitation probability, is_day) and `daily` for 7 days (weather code, max / min, precipitation probability, sunrise,
sunset), in the place's own time zone; `now_source` `{kind: "station", name, num, km, age_min}` or `{kind: "model", why}`;
`age` in seconds. 503 while the place is unknown, or before the first forecast.

`GET /api/weather/settings`, `PUT /api/weather/settings` `{lat, lon, place, station: "auto" | its number, model}`,
`GET /api/weather/geocode?q=` (2 to 60 characters, up to 6 places).

## `home/weather` (retained)

A compact copy for the ESP panels, published after each fetch, again every 30 s until the broker takes
it, and at every broker (re)connect: `{t, c, d, f, h, w, hr, dy}` - temperature, weather code, day (1/0),
feels-like, humidity, wind (m/s); `hr`: 8 points 3 hours apart `{k: "HH:MM", c, d, t, p}`; `dy`: 7 days
`{k: "today" or Mon..Sun, wd, c, hi, lo, p}` (`wd` = weekday, 0 = Monday, -1 when the date does not
parse; `p` = precipitation probability, %).

The health page warns when there is no forecast yet or it is older than an hour, while the place is only guessed, and while
the weather now comes from the models (with the reason).
