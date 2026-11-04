"""Weather questions, Russian and English: the answer is a template over the house's own forecast (GET /api/weather: Open-Meteo, fetched
by the weather plug-in), so nothing is made up; the model only classifies a question (ask weather <when>). No data -> it says so."""
import re

from voice_state import plural

DAYS = ("now", "today", "tomorrow", "evening")            # what the question names; "rain" and "temp" are flags of the question
# every WMO code with its intensity: 80 is a slight shower ("light rain in places"), not a downpour
CODE_RU = {0: "\u044f\u0441\u043d\u043e", 1: "\u043c\u0430\u043b\u043e\u043e\u0431\u043b\u0430\u0447\u043d\u043e", 2: "\u043f\u0435\u0440\u0435\u043c\u0435\u043d\u043d\u0430\u044f \u043e\u0431\u043b\u0430\u0447\u043d\u043e\u0441\u0442\u044c", 3: "\u043f\u0430\u0441\u043c\u0443\u0440\u043d\u043e", 45: "\u0442\u0443\u043c\u0430\u043d", 48: "\u0442\u0443\u043c\u0430\u043d \u0441 \u0438\u0437\u043c\u043e\u0440\u043e\u0437\u044c\u044e",  # i18n
           51: "\u0441\u043b\u0430\u0431\u0430\u044f \u043c\u043e\u0440\u043e\u0441\u044c", 53: "\u043c\u043e\u0440\u043e\u0441\u044c", 55: "\u0441\u0438\u043b\u044c\u043d\u0430\u044f \u043c\u043e\u0440\u043e\u0441\u044c", 56: "\u0441\u043b\u0430\u0431\u0430\u044f \u043b\u0435\u0434\u044f\u043d\u0430\u044f \u043c\u043e\u0440\u043e\u0441\u044c", 57: "\u043b\u0435\u0434\u044f\u043d\u0430\u044f \u043c\u043e\u0440\u043e\u0441\u044c",
           61: "\u043d\u0435\u0431\u043e\u043b\u044c\u0448\u043e\u0439 \u0434\u043e\u0436\u0434\u044c", 63: "\u0434\u043e\u0436\u0434\u044c", 65: "\u0441\u0438\u043b\u044c\u043d\u044b\u0439 \u0434\u043e\u0436\u0434\u044c", 66: "\u043d\u0435\u0431\u043e\u043b\u044c\u0448\u043e\u0439 \u043b\u0435\u0434\u044f\u043d\u043e\u0439 \u0434\u043e\u0436\u0434\u044c", 67: "\u043b\u0435\u0434\u044f\u043d\u043e\u0439 \u0434\u043e\u0436\u0434\u044c",  # i18n
           71: "\u043d\u0435\u0431\u043e\u043b\u044c\u0448\u043e\u0439 \u0441\u043d\u0435\u0433", 73: "\u0441\u043d\u0435\u0433", 75: "\u0441\u0438\u043b\u044c\u043d\u044b\u0439 \u0441\u043d\u0435\u0433", 77: "\u0441\u043d\u0435\u0436\u043d\u0430\u044f \u043a\u0440\u0443\u043f\u0430", 80: "\u043d\u0435\u0431\u043e\u043b\u044c\u0448\u043e\u0439 \u0434\u043e\u0436\u0434\u044c \u043c\u0435\u0441\u0442\u0430\u043c\u0438", 81: "\u043b\u0438\u0432\u043d\u0435\u0432\u044b\u0439 \u0434\u043e\u0436\u0434\u044c",
           82: "\u0441\u0438\u043b\u044c\u043d\u044b\u0439 \u043b\u0438\u0432\u0435\u043d\u044c", 85: "\u043d\u0435\u0431\u043e\u043b\u044c\u0448\u043e\u0439 \u0441\u043d\u0435\u0433\u043e\u043f\u0430\u0434", 86: "\u0441\u0438\u043b\u044c\u043d\u044b\u0439 \u0441\u043d\u0435\u0433\u043e\u043f\u0430\u0434", 95: "\u0433\u0440\u043e\u0437\u0430", 96: "\u0433\u0440\u043e\u0437\u0430 \u0441 \u0433\u0440\u0430\u0434\u043e\u043c", 99: "\u0441\u0438\u043b\u044c\u043d\u0430\u044f \u0433\u0440\u043e\u0437\u0430 \u0441 \u0433\u0440\u0430\u0434\u043e\u043c"}  # i18n
CODE_EN = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
           55: "dense drizzle", 56: "light freezing drizzle", 57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
           66: "light freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
           80: "light showers", 81: "rain showers", 82: "violent showers", 85: "light snow showers", 86: "heavy snow showers",
           95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail"}
SNOW = {71, 73, 75, 77, 85, 86}

WEATHER_RE = re.compile(r"\u043f\u043e\u0433\u043e\u0434|\u043f\u0440\u043e\u0433\u043d\u043e\u0437|\u0431\u0443\u0434\u0435\u0442 (\u043b\u0438 )?(\u0434\u043e\u0436\u0434\u044c|\u043b\u0438\u0432\u0435\u043d\u044c|\u0441\u043d\u0435\u0433|\u0433\u0440\u043e\u0437\u0430)|\b\u0434\u043e\u0436\u0434|\b\u0441\u043d\u0435\u0433|\u0437\u043e\u043d\u0442|\bweather\b|\bforecast\b|"  # i18n
                        r"\b(rain|raining|snow|snowing|umbrella)\b|\bwill it (rain|snow)\b")
# a place word alone is no weather question ("is the light on outside"): it counts only next to a temperature word
PLACE_RE = re.compile(r"\u043d\u0430 \u0443\u043b\u0438\u0446\u0435|\u043d\u0430 \u0434\u0432\u043e\u0440\u0435|\u043d\u0430 \u043e\u0442\u043a\u0440\u044b\u0442\u043e\u043c \u0432\u043e\u0437\u0434\u0443\u0445\u0435|\b(outside|outdoors?)\b")  # i18n
TEMP_RE = re.compile(r"\u0433\u0440\u0430\u0434\u0443\u0441|\u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440|\u0445\u043e\u043b\u043e\u0434\u043d\u043e|\u0442\u0435\u043f\u043b\u043e|\u0436\u0430\u0440\u043a\u043e|\b(degrees|temperature|hot|cold|warm|freezing)\b")


# words of a weather question that name no device: what is left is checked against the house's devices ("did the rain sensor trigger")
FILLER_RE = re.compile(r"\u043f\u043e\u0433\u043e\u0434|\u043f\u0440\u043e\u0433\u043d\u043e\u0437|\u0434\u043e\u0436\u0434|\u0441\u043d\u0435\u0433|\u0437\u043e\u043d\u0442|\u043b\u0438\u0432\u0435\u043d|\u0433\u0440\u043e\u0437|\u0443\u043b\u0438\u0446|\u0434\u0432\u043e\u0440|\u0432\u043e\u0437\u0434\u0443\u0445|\u0433\u0440\u0430\u0434\u0443\u0441|\u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440|\u0445\u043e\u043b\u043e\u0434\u043d\u043e|\u0442\u0435\u043f\u043b\u043e|\u0436\u0430\u0440\u043a\u043e|\u043a\u0430\u043a\u0430\u044f|\u043a\u0430\u043a\u043e\u0439|\u0431\u0443\u0434\u0435\u0442|\u0441\u043a\u043e\u043b\u044c\u043a\u043e|"  # i18n
                       r"\u0441\u0435\u0433\u043e\u0434\u043d\u044f|\u0437\u0430\u0432\u0442\u0440\u0430|\u0441\u0435\u0439\u0447\u0430\u0441|\u0432\u0435\u0447\u0435\u0440|\b(weather|forecast|rain\w*|snow\w*|umbrella|outside|outdoors?|degrees|temperature|hot|cold|warm|"
                       r"freezing|what\w*|will|how|many|the|is|it|today|tomorrow|now|tonight|evening|right)\b")


def device_words(text: str) -> list:
    """The words of the question that are not weather words: a device named by one of them makes it a device question."""
    return [w for w in re.findall(r"[a-z\u0430-\u044f0-9]+", text.lower().replace("\u0451", "\u0435")) if len(w) >= 3 and not FILLER_RE.search(w)]  # i18n


def is_weather(text: str) -> bool:
    t = text.lower().replace("\u0451", "\u0435")  # i18n
    return bool(WEATHER_RE.search(t) or (PLACE_RE.search(t) and TEMP_RE.search(t)))


def flags_of(text: str) -> dict:
    """{"day": now|today|tomorrow|evening|None, "rain": bool, "temp": bool} from the words of the question."""
    t = text.lower().replace("\u0451", "\u0435")  # i18n
    day = ("tomorrow" if re.search(r"\u0437\u0430\u0432\u0442\u0440\u0430|tomorrow", t) else "evening" if re.search(r"\u0432\u0435\u0447\u0435\u0440|tonight|evening", t)
           else "today" if re.search(r"\u0441\u0435\u0433\u043e\u0434\u043d\u044f|today", t) else "now" if re.search(r"\u0441\u0435\u0439\u0447\u0430\u0441|\bnow\b|right now", t) else None)  # i18n
    rain = bool(re.search(r"\u0434\u043e\u0436\u0434|\u043b\u0438\u0432\u0435\u043d\u044c|\u0441\u043d\u0435\u0433|\u0437\u043e\u043d\u0442|\u0433\u0440\u043e\u0437\u0430|\brain|\bsnow|umbrella|thunder", t))
    temp = bool(re.search(r"\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u0433\u0440\u0430\u0434\u0443\u0441|\u043a\u0430\u043a\u0430\u044f \u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0430|how (hot|cold|warm)|what('s| is) the temperature|how many degrees", t)) and not rain  # i18n
    return {"day": day, "rain": rain, "temp": temp}


def _num(v) -> int:
    return int(round(float(v)))


def _rain_ru(prob: int, code: int, snow: bool = False) -> str:
    noun = "\u0441\u043d\u0435\u0433" if snow or code in SNOW else "\u0434\u043e\u0436\u0434\u044c"  # i18n
    return "\u0431\u0435\u0437 %s" % ("\u0441\u043d\u0435\u0433\u0430" if noun == "\u0441\u043d\u0435\u0433" else "\u0434\u043e\u0436\u0434\u044f") if prob < 30 else ("\u0432\u043e\u0437\u043c\u043e\u0436\u0435\u043d %s" % noun) if prob < 60 else noun


def _rain_en(prob: int, code: int, snow: bool = False) -> str:
    noun = "snow" if snow or code in SNOW else "rain"
    return "no %s" % noun if prob < 30 else ("%s is possible" % noun) if prob < 60 else "%s expected" % noun


def _day(w: dict, i: int):
    d = w.get("daily") or {}
    try:
        return {"code": int((d.get("weather_code") or [])[i] or 0), "hi": _num(d["temperature_2m_max"][i]), "lo": _num(d["temperature_2m_min"][i]),
                "p": _num((d.get("precipitation_probability_max") or [0] * 7)[i] or 0)}
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _evening(w: dict):
    """The hours 17-23 of the first day in the hourly list: (min t, max t, max precipitation probability, worst code), else None."""
    h = w.get("hourly") or {}
    rows = []
    for i, tm in enumerate(h.get("time") or []):
        try:
            hour = int(str(tm)[11:13])
            rows.append((hour, _num(h["temperature_2m"][i]), _num((h.get("precipitation_probability") or [0] * 99)[i] or 0),
                         int((h.get("weather_code") or [0] * 99)[i] or 0)))
        except (IndexError, KeyError, TypeError, ValueError):
            continue
    if not rows:
        return None
    first_day = str((h.get("time") or [""])[0])[:10]
    ev = [r for r, tm in zip(rows, h["time"]) if 17 <= r[0] <= 23 and str(tm)[:10] == first_day]
    if not ev:
        return None
    return min(r[1] for r in ev), max(r[1] for r in ev), max(r[2] for r in ev), max((r[3] for r in ev), key=lambda c: (c in CODE_RU and c >= 51, c))


def answer(w, flags: dict, lang: str = "ru"):
    """(full text, short text to read aloud) for the question flags over the forecast dict `w` (GET /api/weather)."""
    en = lang == "en"
    if not w or not w.get("current"):
        text = "I have no weather data right now" if en else "\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445 \u043e \u043f\u043e\u0433\u043e\u0434\u0435"  # i18n
        return text, text
    cur = w["current"]
    code = int(cur.get("weather_code") or 0)
    names = CODE_EN if en else CODE_RU
    now_t = _num(cur.get("temperature_2m"))
    raining_now = float(cur.get("precipitation") or 0) > 0
    day = flags.get("day")
    deg = "%d degrees" % now_t if en else "%d %s" % (now_t, plural(now_t, "\u0433\u0440\u0430\u0434\u0443\u0441", "\u0433\u0440\u0430\u0434\u0443\u0441\u0430", "\u0433\u0440\u0430\u0434\u0443\u0441\u043e\u0432"))  # i18n
    if flags.get("temp"):
        return ("It is %d\u00b0 outside" if en else "\u041d\u0430 \u0443\u043b\u0438\u0446\u0435 %d\u00b0") % now_t, ("It is %s outside" if en else "\u041d\u0430 \u0443\u043b\u0438\u0446\u0435 %s") % deg  # i18n
    full, spoken = [], []

    def now_part():                                         # only what was asked: the temperature, the sky, rain or not
        what = names.get(code, "")
        rain = "" if code >= 51 else ("no rain" if en else "\u0431\u0435\u0437 \u0434\u043e\u0436\u0434\u044f")  # i18n
        tail = ", ".join(x for x in (what, rain) if x)
        full.append(("Now %d\u00b0, %s" if en else "\u0421\u0435\u0439\u0447\u0430\u0441 %d\u00b0, %s") % (now_t, tail))  # i18n
        spoken.append(("Now %s, %s" if en else "\u0421\u0435\u0439\u0447\u0430\u0441 %s, %s") % (deg, tail))

    def day_part(i, word_ru, word_en):
        d = _day(w, i)
        if d is None:
            full.append("No forecast for %s" % word_en if en else "\u041d\u0435\u0442 \u043f\u0440\u043e\u0433\u043d\u043e\u0437\u0430: %s" % word_ru)  # i18n
            spoken.append(full[-1])
            return
        rain = (_rain_en if en else _rain_ru)(d["p"], d["code"])
        label = word_en if en else word_ru
        sky = "" if d["code"] >= 51 else names.get(d["code"], "")                 # a rain code says it all: no "rain, rain expected"
        rng = ("%d to %d" if en else "\u043e\u0442 %d \u0434\u043e %d") % (d["lo"], d["hi"])  # i18n
        full.append("%s: %s (%d%%)" % (label, ", ".join(x for x in (rng, sky, rain) if x), d["p"]))
        spoken.append(("%s up to %d, %s" if en else "%s \u0434\u043e %d, %s") % (label, d["hi"], rain))  # i18n

    if flags.get("rain"):
        parts_full, parts_spoken = [], []
        if raining_now:
            parts_full.append("It is raining now" if en else "\u0421\u0435\u0439\u0447\u0430\u0441 \u0438\u0434\u0451\u0442 \u0434\u043e\u0436\u0434\u044c")  # i18n
        for i, ru, e in ((0, "\u0421\u0435\u0433\u043e\u0434\u043d\u044f", "Today"), (1, "\u0417\u0430\u0432\u0442\u0440\u0430", "Tomorrow")):
            if day in ("today", "now", "evening") and i == 1 or day == "tomorrow" and i == 0:
                continue
            d = _day(w, i)
            if d:
                r = (_rain_en if en else _rain_ru)(d["p"], d["code"])
                parts_full.append("%s: %s (%d%%)" % (e if en else ru, r, d["p"]))
                parts_spoken.append("%s: %s" % (e if en else ru, r))
        text = (". ".join(parts_full) or ("No forecast" if en else "\u041d\u0435\u0442 \u043f\u0440\u043e\u0433\u043d\u043e\u0437\u0430"))  # i18n
        return text, ". ".join(parts_spoken) or text
    if day == "tomorrow":
        day_part(1, "\u0417\u0430\u0432\u0442\u0440\u0430", "Tomorrow")  # i18n
    elif day == "evening":
        ev = _evening(w)
        if ev is None:
            text = "No forecast for the evening" if en else "\u041d\u0435\u0442 \u043f\u0440\u043e\u0433\u043d\u043e\u0437\u0430 \u043d\u0430 \u0432\u0435\u0447\u0435\u0440"  # i18n
            return text, text
        lo, hi, p, c = ev
        rain = (_rain_en if en else _rain_ru)(p, c)
        t = ("%d" % lo) if lo == hi else ("%d to %d" % (lo, hi) if en else "\u043e\u0442 %d \u0434\u043e %d" % (lo, hi))  # i18n
        full.append(("This evening: %s, %s (%d%%)" if en else "\u0412\u0435\u0447\u0435\u0440\u043e\u043c: %s, %s (%d%%)") % (t, rain, p))
        spoken.append(("This evening %s, %s" if en else "\u0412\u0435\u0447\u0435\u0440\u043e\u043c %s, %s") % (("around %d" % hi) if en and lo == hi else ("\u043e\u043a\u043e\u043b\u043e %d" % hi) if lo == hi else t, rain))  # i18n
    elif day == "today":
        day_part(0, "\u0421\u0435\u0433\u043e\u0434\u043d\u044f", "Today")  # i18n
    else:                                                   # now, or no day named: the weather now
        now_part()
    return ". ".join(full), ". ".join(spoken)
