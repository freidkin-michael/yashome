"""State questions of the voice path: the house answers from LIVE state in human words (never acts, needs no confirmation), in the language
of the question (Russian or English). Questions come from the fast phrase templates below or from the model's `ask` line (voice_llm); every
answer is a template over the registry's devices, so the model only chooses WHAT to ask, never what the answer says."""
import json
import os
import re
import time

from voice_lang import lang_of

NOISE = {"wifi_signal", "uptime", "button", "baseline", "rise", "light_indicator_level", "last_wake", "action", "card", "event",
         "stale", "unseen_days", "now", "current", "voltage", "energy", "linkquality", "update", "stale_disp", "phone", "state_text"}
LABELS = {"temperature": "\u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0430", "humidity": "\u0432\u043b\u0430\u0436\u043d\u043e\u0441\u0442\u044c", "illuminance": "\u043e\u0441\u0432\u0435\u0449\u0451\u043d\u043d\u043e\u0441\u0442\u044c", "battery": "\u0431\u0430\u0442\u0430\u0440\u0435\u044f",  # i18n
          "room_temp": "\u0432 \u043a\u043e\u043c\u043d\u0430\u0442\u0435", "volume": "\u0433\u0440\u043e\u043c\u043a\u043e\u0441\u0442\u044c", "distance": "\u0440\u0430\u0441\u0441\u0442\u043e\u044f\u043d\u0438\u0435", "temp": "\u0437\u0430\u0434\u0430\u043d\u043e", "mode": "\u0440\u0435\u0436\u0438\u043c", "fan": "\u0432\u0435\u043d\u0442\u0438\u043b\u044f\u0442\u043e\u0440",
          "input": "\u0432\u0445\u043e\u0434", "app": "\u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435", "media": "\u0441\u0435\u0439\u0447\u0430\u0441", "door": "\u0434\u0432\u0435\u0440\u044c", "state": "\u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u0435"}  # i18n
LABELS_EN = {"temperature": "temperature", "humidity": "humidity", "illuminance": "light level", "battery": "battery",
             "room_temp": "room", "volume": "volume", "distance": "distance", "temp": "set to", "mode": "mode", "fan": "fan",
             "input": "input", "app": "app", "media": "now playing", "door": "door", "state": "state"}
ENUM_RU = {"cool": "\u043e\u0445\u043b\u0430\u0436\u0434\u0435\u043d\u0438\u0435", "heat": "\u043e\u0431\u043e\u0433\u0440\u0435\u0432", "dry": "\u0441\u0443\u0448\u043a\u0430", "fan": "\u0432\u0435\u043d\u0442\u0438\u043b\u044f\u0442\u043e\u0440", "auto": "\u0430\u0432\u0442\u043e", "low": "\u0441\u043b\u0430\u0431\u043e", "med": "\u0441\u0440\u0435\u0434\u043d\u0435",  # i18n
           "max": "\u0441\u0438\u043b\u044c\u043d\u043e", "play": "\u0438\u0433\u0440\u0430\u0435\u0442", "pause": "\u043f\u0430\u0443\u0437\u0430", "stop": "\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e"}
ENUM_EN = {"cool": "cooling", "heat": "heating", "dry": "drying", "fan": "fan", "auto": "auto", "low": "low", "med": "medium",
           "max": "high", "play": "playing", "pause": "paused", "stop": "stopped"}
NOT_LIGHT_RE = re.compile(r"\u0440\u043e\u0437\u0435\u0442\u043a|\u0431\u043e\u0439\u043b\u0435\u0440|\u0432\u043e\u0434\u043e\u043d\u0430\u0433\u0440\u0435\u0432|\u0438\u0437\u043c\u0435\u043b\u044c\u0447|\u043a\u043e\u043d\u0434\u0438\u0446|\u0442\u0435\u043b\u0435\u0432\u0438\u0437|socket|heater|sredder|boiler|shredder", re.I)  # i18n
LIGHT_RE = re.compile(r"light|\u0441\u0432\u0435\u0442|\u043b\u0430\u043c\u043f\u0430|\u043f\u043e\u0434\u0441\u0432\u0435\u0442\u043a|\u043b\u0430\u043c\u043f|\u0443\u0433\u043e\u043b|\u043a\u0443\u0445\u043d\u044f|\u043a\u043e\u0440\u0438\u0434\u043e\u0440|\u0432\u0435\u0440\u0445|salon|bedroom|hall|corridor|garden|bath", re.I)
STALE_S = 6 * 3600
ON_PLACES = ("\u043a\u0443\u0445\u043d", "\u0431\u0430\u043b\u043a\u043e\u043d", "\u0442\u0435\u0440\u0440\u0430\u0441", "\u0432\u0435\u0440\u0430\u043d\u0434", "\u0443\u043b\u0438\u0446", "\u043b\u0435\u0441\u0442\u043d\u0438\u0446")           # i18n: the kitchen takes "na" in Russian ("on the kitchen"), the rest "v ..."
ASPECTS = ("temp", "humidity", "on", "off")
SPOKEN_ITEMS = 6                                                               # a list read aloud: this many, then "and N more"

ROOM_EN = {"\u0441\u043f\u0430\u043b\u044c\u043d": "bedroom", "\u043a\u0443\u0445\u043d": "kitchen", "\u0441\u0430\u043b\u043e\u043d": "living room", "\u0432\u0430\u043d\u043d": "bathroom", "\u043a\u043e\u0440\u0438\u0434\u043e\u0440": "hallway", "\u0434\u0432\u043e\u0440": "yard",  # i18n
           "\u0433\u043e\u0441\u0442\u0438\u043d": "living room", "\u0434\u0435\u0442\u0441\u043a": "kids room", "\u043a\u0430\u0431\u0438\u043d\u0435\u0442": "study", "\u043f\u0440\u0438\u0445\u043e\u0436": "entrance hall", "\u0431\u0430\u043b\u043a\u043e\u043d": "balcony",
           "\u0442\u0443\u0430\u043b\u0435\u0442": "toilet", "\u0441\u0430\u043d\u0443\u0437\u0435\u043b": "bathroom", "\u0437\u0430\u043b": "hall"}  # i18n
ROOM_EN_WORDS = (("bedroom", "\u0441\u043f\u0430\u043b\u044c\u043d"), ("kitchen", "\u043a\u0443\u0445\u043d"), ("living room", "\u0441\u0430\u043b\u043e\u043d"), ("lounge", "\u0441\u0430\u043b\u043e\u043d"), ("salon", "\u0441\u0430\u043b\u043e\u043d"),
                 ("bathroom", "\u0432\u0430\u043d\u043d"), ("bath", "\u0432\u0430\u043d\u043d"), ("hallway", "\u043a\u043e\u0440\u0438\u0434\u043e\u0440"), ("hall", "\u043a\u043e\u0440\u0438\u0434\u043e\u0440"), ("corridor", "\u043a\u043e\u0440\u0438\u0434\u043e\u0440"),  # i18n
                 ("yard", "\u0434\u0432\u043e\u0440"), ("courtyard", "\u0434\u0432\u043e\u0440"), ("balcony", "\u0431\u0430\u043b\u043a\u043e\u043d"), ("toilet", "\u0442\u0443\u0430\u043b\u0435\u0442"), ("study", "\u043a\u0430\u0431\u0438\u043d\u0435\u0442"))


def num(v, whole=False):
    """A number as people say it: one decimal at most (a decimal comma), whole for percents and lux."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(round(f))) if whole or abs(f - round(f)) < 0.05 else ("%.1f" % f).replace(".", ",")


def plural(n, one, few, many):
    """The Russian form after a number: 1 \u0433\u0440\u0430\u0434\u0443\u0441, 2 \u0433\u0440\u0430\u0434\u0443\u0441\u0430, 5 \u0433\u0440\u0430\u0434\u0443\u0441\u043e\u0432; a fraction takes the 'few' form (27,5 \u0433\u0440\u0430\u0434\u0443\u0441\u0430)."""  # i18n
    if abs(n - round(n)) >= 0.05:
        return few
    n = int(round(abs(n)))
    last2, last = n % 100, n % 10
    return many if 11 <= last2 <= 14 else one if last == 1 else few if 2 <= last <= 4 else many


_WHERE = {"mtime": None, "forms": {"\u0432\u0430\u043d\u043d\u0430": "\u0432 \u0432\u0430\u043d\u043d\u043e\u0439"}}          # i18n: a room named as people say it ("the bath" is the bathroom): the house's glossary "rooms" adds more


def _where_forms():
    path = os.environ.get("VA_GLOSSARY", "/house/voice_glossary.json")
    try:
        mt = os.path.getmtime(path)
        if mt != _WHERE["mtime"]:
            with open(path, encoding="utf-8") as fh:
                rooms = (json.load(fh) or {}).get("rooms") or {}
            _WHERE["forms"] = {"\u0432\u0430\u043d\u043d\u0430": "\u0432 \u0432\u0430\u043d\u043d\u043e\u0439", **{str(k).strip().lower(): str(v).strip() for k, v in rooms.items() if str(v).strip()}}  # i18n
            _WHERE["mtime"] = mt
    except (OSError, ValueError):
        pass
    return _WHERE["forms"]


def where(room):
    """'\u0432 \u0441\u043f\u0430\u043b\u044c\u043d\u0435', '\u043d\u0430 \u043a\u0443\u0445\u043d\u0435': the place of a room in the prepositional case; a name it cannot decline becomes '\u0432 \u043a\u043e\u043c\u043d\u0430\u0442\u0435 \u00abX\u00bb'."""  # i18n
    low = room.strip().lower()
    if low in _where_forms():
        return _where_forms()[low]
    if not re.fullmatch(r"[\u0430-\u044f\u0451]+", low):  # i18n
        return "\u0432 \u043a\u043e\u043c\u043d\u0430\u0442\u0435 \u00ab%s\u00bb" % room
    prep = "\u043d\u0430" if low.startswith(ON_PLACES) else "\u0432\u043e" if low.startswith(("\u0434\u0432\u043e\u0440", "\u0432\u0442", "\u0432\u0445")) else "\u0432"  # i18n
    if low.endswith("\u0430\u044f"):
        form = low[:-2] + ("\u0435\u0439" if low[-3:-2] in "\u0436\u0448\u0449\u0447" else "\u043e\u0439")  # i18n
    elif low.endswith("\u044f\u044f"):
        form = low[:-2] + "\u0435\u0439"  # i18n
    elif low.endswith(("\u0430", "\u044f")):
        form = low[:-1] + "\u0435"  # i18n
    elif low[-1] in "\u043e\u0435\u0438\u0443\u044b\u044d\u044e\u0451\u044c\u0439":
        return "\u0432 \u043a\u043e\u043c\u043d\u0430\u0442\u0435 \u00ab%s\u00bb" % room  # i18n
    else:
        form = low + "\u0435"  # i18n
    return "%s %s" % (prep, form)


def room_en(room):
    """The English name of a room ('bedroom'): by its Russian stem, else the name itself."""
    low = room.strip().lower()
    return next((en for stem, en in ROOM_EN.items() if low.startswith(stem)), room.strip())


def where_en(room):
    en = room_en(room)
    return "in the " + en if en.lower() != room.strip().lower() else "in " + en


def latin(text):
    return bool(text) and not re.search(r"[\u0410-\u042f\u0430-\u044f\u0401\u0451]", text)  # i18n


def ago(sec, en=False):
    sec = max(0, int(sec))
    if sec < 90:
        return "less than a minute" if en else "\u043c\u0435\u043d\u044c\u0448\u0435 \u043c\u0438\u043d\u0443\u0442\u044b"  # i18n
    if sec < 3600:
        return "%d min" % (sec // 60) if en else "%d \u043c\u0438\u043d" % (sec // 60)  # i18n
    if sec < 86400:
        return "%d h" % (sec // 3600) if en else "%d \u0447" % (sec // 3600)  # i18n
    return "%d d" % (sec // 86400) if en else "%d \u0434\u043d" % (sec // 86400)


def cut(items, en=False, n=SPOKEN_ITEMS):
    """A list read aloud: the first n, then '\u0438 \u0435\u0449\u0451 N' / 'and N more'."""  # i18n
    shown = ", ".join(items[:n])
    rest = len(items) - n
    return shown if rest <= 0 else "%s %s %d%s" % (shown, "and" if en else "\u0438 \u0435\u0449\u0451", rest, " more" if en else "")  # i18n


class State:
    def __init__(self, devices, room_of, status, name=None, now=None, chan=None, lang="ru", sensors=None):
        """devices: the dashboard's list; room_of: {device id: room name}; status(did) -> {"online", "values", "last_seen"} or None;
        name(did) -> how a device is called aloud (glossary), else its own name. lang: "ru" | "en" (English: Latin names, no glossary).
        sensors: {room name lower: {"temperature" | "humidity": {"order": ["dev/code"]}}}, the house's order of a room's reading.
        A temperature is said in whole degrees, as every screen shows it."""
        self.devs = {d["id"]: d for d in devices}
        self.sensors = sensors or {}
        self._chan = chan                                    # chan(did, code) -> the spoken name of one channel, or None
        self.room_of, self._status, self._name, self.now = room_of, status, name, now or time.time
        self.en = lang == "en"
        self._cache = {}

    def st(self, did):
        if did not in self._cache:
            try:
                self._cache[did] = self._status(did) or {}
            except Exception:                                # noqa: BLE001  an unreachable device is "no data", not an error
                self._cache[did] = {}
        return self._cache[did]

    def chan_name(self, did, code, label=None):
        """A channel of a multi-gang device as people name it: its spoken name when the glossary has one, else 'device (channel label)'."""
        if self.en:
            return self.name(did) + (" (%s)" % label if code.startswith("state_") and latin(label) and label not in ("", "-") else "")
        n = self._chan(did, code) if self._chan else None
        if n:
            return n
        return self.name(did) + (" (%s)" % label if code.startswith("state_") and label not in (None, "", "-") else "")

    def prefetch(self, ids=None, workers=8):
        """Read the status of many devices at once (a question about a room or about everything would otherwise ask them one by one)."""
        from concurrent.futures import ThreadPoolExecutor
        todo = [i for i in (ids if ids is not None else self.devs) if i not in self._cache]
        with ThreadPoolExecutor(workers) as ex:
            for i, st in zip(todo, ex.map(lambda d: self._fetch(d), todo)):
                self._cache[i] = st

    def _fetch(self, did):
        try:
            return self._status(did) or {}
        except Exception:                                    # noqa: BLE001
            return {}

    def name(self, did):
        if self.en:                                          # English: the registry's own name when it is Latin, else the device id
            n = self.devs[did].get("name")
            return n if latin(n) else did
        n = self._name(did) if self._name else None
        return n or self.devs[did].get("name") or did

    def room_name(self, room):
        return room_en(room) if self.en else room

    def fresh(self, did):
        """'' when the data is live; else the reason in words (offline, or silent for long)."""
        s = self.st(did)
        seen = s.get("last_seen")
        if s.get("online") is False:
            if self.en:
                return "no connection%s" % (", last data %s ago" % ago(self.now() - seen, True) if seen else "")
            return "\u043d\u0435\u0442 \u0441\u0432\u044f\u0437\u0438%s" % (", \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 %s \u043d\u0430\u0437\u0430\u0434" % ago(self.now() - seen) if seen else "")  # i18n
        if not s.get("values"):
            return "no data" if self.en else "\u043d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445"  # i18n
        if seen and self.now() - seen > STALE_S and self.devs[did].get("category") not in ("button",):
            return ("no data for %s" % ago(self.now() - seen, True)) if self.en else "\u043d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445 %s" % ago(self.now() - seen)  # i18n
        return ""

    # ---- one control ----
    def part(self, did, c, v):
        k, code = c["kind"], c["code"]
        en = self.en
        if v is None:
            return ""
        if k == "switch":
            if code in ("locked", "lock"):
                return ("locked" if v else "unlocked") if en else ("\u0437\u0430\u043f\u0435\u0440\u0442" if v else "\u043e\u0442\u043a\u0440\u044b\u0442")  # i18n
            if code == "mute":
                return ("muted" if v else "") if en else ("\u0437\u0432\u0443\u043a \u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d" if v else "")  # i18n
            return ("on" if v else "off") if en else ("\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e" if v else "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e")
        if k == "bright":
            return "%s %s%%" % (("volume" if en else "\u0433\u0440\u043e\u043c\u043a\u043e\u0441\u0442\u044c") if code == "volume" else ("brightness" if en else "\u044f\u0440\u043a\u043e\u0441\u0442\u044c"), num(v, True))  # i18n
        if k in ("sensor", "sensor_text", "setting_int", "setting_enum"):
            unit = c.get("unit") or ""
            if code in ("presence", "occupancy", "home", "active"):
                on = v in (True, 1, "1", "true", "True", "on", "ON", "home")
                if en:
                    return {"home": "home" if on else "away", "active": "active" if on else "no", "presence": "someone is there" if on else "nobody",
                            "occupancy": "motion" if on else "quiet"}[code]
                return {"home": "\u0434\u043e\u043c\u0430" if on else "\u043d\u0435\u0442 \u0434\u043e\u043c\u0430", "active": "\u0438\u0434\u0451\u0442" if on else "\u043d\u0435\u0442", "presence": "\u0435\u0441\u0442\u044c \u043a\u0442\u043e-\u0442\u043e" if on else "\u043d\u0438\u043a\u043e\u0433\u043e",  # i18n
                        "occupancy": "\u0435\u0441\u0442\u044c \u0434\u0432\u0438\u0436\u0435\u043d\u0438\u0435" if on else "\u0442\u0438\u0445\u043e"}[code]
            if code == "door":
                op = str(v).lower() in ("open", "opened", "true", "1")
                return ("door open" if op else "door closed") if en else "\u0434\u0432\u0435\u0440\u044c %s" % ("\u043e\u0442\u043a\u0440\u044b\u0442\u0430" if op else "\u0437\u0430\u043a\u0440\u044b\u0442\u0430")  # i18n
            labels, enum = (LABELS_EN, ENUM_EN) if en else (LABELS, ENUM_RU)
            label = labels.get(code) or (c.get("label") or code)
            val = enum.get(str(v), str(v)) if k in ("sensor_text", "setting_enum") and not str(v).replace(".", "").isdigit() else num(v)
            if code in ("temperature", "room_temp", "temp"):
                unit = "\u00b0"
            if code in ("temperature", "room_temp"):
                val = num(v, True)
            if code == "temp":                           # the air conditioner's setting, not a measurement ("room_temp" is that)
                return ("set to %s\u00b0" % val) if en else "\u0437\u0430\u0434\u0430\u043d\u0430 \u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0430 %s\u00b0" % val  # i18n
            if unit in ("%", "lx") or code in ("humidity", "battery", "illuminance"):
                val = num(v, True)
            return "%s %s%s" % (label, val, (" " + unit if unit and unit not in ("\u00b0", "%") else unit))
        return ""

    def readable(self, did):
        return [c for c in self.devs[did].get("controls") or []
                if c["code"] not in NOISE and c.get("label") != "-" and c["kind"] in ("switch", "bright", "sensor", "sensor_text", "setting_int", "setting_enum")
                and not (c["kind"] == "setting_int" and (self.devs[did].get("category") != "climate"))
                and not (c["kind"] == "setting_enum" and self.devs[did].get("category") not in ("climate", "tv"))]

    # ---- the questions ----
    def device(self, did, code=None):
        """'<Name>: parts' of one device (or one control code of it)."""
        if did not in self.devs:
            return ""
        why = self.fresh(did)
        if why:
            return "%s: %s" % (self.name(did), why)
        vals = self.st(did).get("values") or {}
        cs = [c for c in self.readable(did) if code in (None, c["code"])]
        parts = [p for p in (self.part(did, c, vals.get(c["code"])) for c in cs) if p]
        if not cs:
            return "%s: %s" % (self.name(did), "no state known, it only takes commands" if self.en else "\u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u044f \u043d\u0435 \u0437\u043d\u0430\u044e, \u044d\u0442\u043e \u0442\u043e\u043b\u044c\u043a\u043e \u043a\u043e\u043c\u0430\u043d\u0434\u044b")  # i18n
        return "%s: %s" % (self.name(did), ", ".join(parts) if parts else ("no data" if self.en else "\u043d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445"))

    def _room_ids(self, room):
        return [d for d, r in self.room_of.items() if r.lower() == room.lower() and d in self.devs]

    def _switches(self, ids, want):
        """The names of the switches of these devices that are on (want True) or off (want False); stale or offline data is skipped."""
        out = []
        for did in ids:
            if self.devs[did].get("category") == "voice" or self.devs[did].get("voice_role") or self.fresh(did):
                continue
            vals = self.st(did).get("values") or {}
            for c in self.readable(did):
                v = vals.get(c["code"])
                if c["kind"] == "switch" and v is not None and c["code"] not in ("locked", "lock", "mute") and bool(v) == want:
                    out.append(self.chan_name(did, c["code"], c.get("label")))
        return list(dict.fromkeys(out))

    def room(self, room, aspect=None):
        """What a room reports. aspect: "temp" | "humidity" | "on" | "off" answers only that; none = a short summary of all three."""
        ids = self._room_ids(room)
        t, h = self._reading(room, "temperature"), self._reading(room, "humidity")     # one reading answers, never an average of sensors
        temps, hums = ([t] if t is not None else []), ([h] if h is not None else [])
        if self.en:
            return self._room_en(room, ids, aspect, temps, hums)
        if not ids:
            return "\u0412 \u043a\u043e\u043c\u043d\u0430\u0442\u0435 \u00ab%s\u00bb \u043d\u0435\u0442 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432" % room  # i18n
        place = where(room)[:1].upper() + where(room)[1:]
        deg = self._deg(temps)
        hum = self._hum(hums)
        if aspect in ("on", "off"):
            items = self._switches(ids, aspect == "on")
            if not items:
                return "%s \u043d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 %s" % (place, "\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e" if aspect == "on" else "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e")  # i18n
            return "%s %s: %s" % (place, "\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e" if aspect == "on" else "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e", ", ".join(items))
        if aspect == "temp":
            return "%s %s" % (place, deg) if deg else "%s \u043d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445 \u043e \u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0435" % place  # i18n
        if aspect == "humidity":
            return "%s %s" % (place, hum) if hum else "%s \u043d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445 \u043e \u0432\u043b\u0430\u0436\u043d\u043e\u0441\u0442\u0438" % place  # i18n
        items = self._switches(ids, True)
        say_on = "\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e: " + ", ".join(items) if items else "\u043d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e"  # i18n
        head = ", ".join(x for x in (deg, hum) if x)
        return "%s %s; %s" % (place, head, say_on) if head else "%s %s" % (place, say_on)

    def _reading(self, room, kind):
        """The first live source of the house's order for the room (the one its tab shows), or None."""
        for src in ((self.sensors.get(room.lower()) or {}).get(kind) or {}).get("order") or []:
            did, _, code = src.rpartition("/")
            if did not in self.devs or self.fresh(did) or self.st(did).get("stale"):
                continue
            v = (self.st(did).get("values") or {}).get(code)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return None

    def _room_en(self, room, ids, aspect, temps, hums):
        if not ids:
            return "There are no devices in the %s" % room_en(room)
        where_ = where_en(room)
        deg = self._deg(temps)
        hum = self._hum(hums)
        if aspect in ("on", "off"):
            items = self._switches(ids, aspect == "on")
            if not items:
                return "Nothing is %s %s" % (aspect, where_)
            return "%s: %s" % ("On " + where_ if aspect == "on" else "Off " + where_, ", ".join(items))
        if aspect == "temp":
            return "It is %s %s" % (deg, where_) if deg else "No temperature data %s" % where_
        if aspect == "humidity":
            return "Humidity %s is %s" % (where_, hum) if hum else "No humidity data %s" % where_
        items = self._switches(ids, True)
        say_on = "on: " + ", ".join(items) if items else "nothing is on"
        head = ", ".join(x for x in (deg, ("humidity " + hum) if hum else "") if x)
        return "%s: %s; %s" % (room_en(room).capitalize(), head, say_on) if head else "%s: %s" % (room_en(room).capitalize(), say_on)

    def _deg(self, vals):
        """'27 \u0433\u0440\u0430\u0434\u0443\u0441\u043e\u0432' | '27 \u0438 26,5 \u0433\u0440\u0430\u0434\u0443\u0441\u0430' | '27 degrees': the distinct readings, one unit word after the last."""  # i18n
        uniq = list(dict.fromkeys(num(v, True) for v in vals))
        if not uniq:
            return ""
        if self.en:
            return "%s degrees" % " and ".join(uniq)
        last = float(uniq[-1].replace(",", "."))
        return "%s %s" % (" \u0438 ".join(uniq), plural(last, "\u0433\u0440\u0430\u0434\u0443\u0441", "\u0433\u0440\u0430\u0434\u0443\u0441\u0430", "\u0433\u0440\u0430\u0434\u0443\u0441\u043e\u0432"))  # i18n

    def _hum(self, vals):
        uniq = list(dict.fromkeys(num(v, True) for v in vals))
        if not uniq:
            return ""
        if self.en:
            return "%s percent" % " and ".join(uniq)
        return "\u0432\u043b\u0430\u0436\u043d\u043e\u0441\u0442\u044c %s %s" % (" \u0438 ".join(uniq), plural(float(uniq[-1]), "\u043f\u0440\u043e\u0446\u0435\u043d\u0442", "\u043f\u0440\u043e\u0446\u0435\u043d\u0442\u0430", "\u043f\u0440\u043e\u0446\u0435\u043d\u0442\u043e\u0432"))  # i18n

    def who_home(self):
        people = [d for d in self.devs.values() if d["id"].startswith("person_")]
        if not people:
            return "No phones with a home marker" if self.en else "\u041d\u0435 \u0437\u043d\u0430\u044e: \u043d\u0435\u0442 \u0442\u0435\u043b\u0435\u0444\u043e\u043d\u043e\u0432 \u0441 \u043e\u0442\u043c\u0435\u0442\u043a\u043e\u0439 \u00ab\u0434\u043e\u043c\u0430\u00bb"  # i18n
        home, away, unknown = [], [], []
        for d in people:
            v = (self.st(d["id"]).get("values") or {}).get("home")
            if self.en:
                who = d["id"].replace("person_", "").title()
            else:
                who = self.name(d["id"]).replace("person_", "").title() if not self._name or not self._name(d["id"]) else self.name(d["id"])
            (home if v else away if v is not None else unknown).append(who)
        if home:
            return ("Home: " if self.en else "\u0414\u043e\u043c\u0430: ") + ", ".join(home)  # i18n
        if away and not unknown:
            return "Nobody is home" if self.en else "\u041d\u0438\u043a\u043e\u0433\u043e \u043d\u0435\u0442 \u0434\u043e\u043c\u0430"  # i18n
        return "No data on who is home" if self.en else "\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445, \u043a\u0442\u043e \u0434\u043e\u043c\u0430"

    def all(self, kind):
        en = self.en
        names = []
        if kind == "battery":
            for did in self.devs:
                v = (self.st(did).get("values") or {}).get("battery") if any(c["code"] == "battery" for c in self.devs[did].get("controls") or []) else None
                if v is not None:
                    names.append((float(v), "%s %s%%" % (self.name(did), num(v, True))))
            names.sort()
            if names:
                return ("Batteries: " if en else "\u0411\u0430\u0442\u0430\u0440\u0435\u0439\u043a\u0438: ") + ", ".join(t for _v, t in names)  # i18n
            return "No battery data" if en else "\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445 \u043e \u0431\u0430\u0442\u0430\u0440\u0435\u0439\u043a\u0430\u0445"
        if kind == "online":
            off = [self.name(d) for d in self.devs if self.devs[d].get("category") != "voice" and self.st(d).get("online") is False]
            if off:
                return ("Offline: " if en else "\u041d\u0435 \u0432 \u0441\u0435\u0442\u0438: ") + ", ".join(off)  # i18n
            return "Everything is online" if en else "\u0412\u0441\u0435 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u0430 \u0432 \u0441\u0435\u0442\u0438"
        if kind in ("lights", "on", "open"):
            for did, d in self.devs.items():
                if d.get("category") == "voice" or self.fresh(did):
                    continue
                vals = self.st(did).get("values") or {}
                for c in d.get("controls") or []:
                    if c["kind"] != "switch" or not vals.get(c["code"]):
                        continue
                    lock = c["code"] in ("locked", "lock")
                    if kind == "open" and lock:
                        continue
                    if kind == "open" and not lock:
                        continue
                    if kind == "lights" and (lock or c["code"] in ("mute", "power") and d.get("category") in ("tv", "climate")):
                        continue
                    if kind == "on" and lock:
                        continue
                    shown = self.chan_name(did, c["code"], c.get("label"))
                    if kind == "lights" and NOT_LIGHT_RE.search(shown):         # a socket, the boiler, the shredder are not lights
                        continue
                    names.append(shown)
            if kind == "open":
                opened = [self.name(d) for d, x in self.devs.items()
                          if any(c["code"] in ("locked", "lock") for c in x.get("controls") or [])
                          and (self.st(d).get("values") or {}).get("locked") is False]
                if opened:
                    return ("Open: " if en else "\u041e\u0442\u043a\u0440\u044b\u0442\u043e: ") + ", ".join(opened)  # i18n
                return "Everything is closed" if en else "\u0412\u0441\u0451 \u0437\u0430\u043a\u0440\u044b\u0442\u043e"
            word = {"lights": "Lights on" if en else "\u0421\u0432\u0435\u0442 \u0432\u043a\u043b\u044e\u0447\u0451\u043d", "on": "On" if en else "\u0412\u043a\u043b\u044e\u0447\u0435\u043d\u043e"}[kind]  # i18n
            if names:
                return "%s: %s" % (word, ", ".join(dict.fromkeys(names)))
            return ("Lights are off everywhere" if kind == "lights" else "Nothing is on") if en else (
                "\u0421\u0432\u0435\u0442 \u0432\u0435\u0437\u0434\u0435 \u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d" if kind == "lights" else "\u041d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e")  # i18n
        return ""

    # ---- a state of the whole house, and the list of devices: (full text, the short text read aloud) ----
    def _groups(self, pick):
        """{room name: [items]} of the devices that have items (pick(did) -> list of strings); no room -> the last group."""
        groups = {}
        for did in self.devs:
            if self.devs[did].get("category") == "voice" or self.devs[did].get("voice_role") or did.startswith("person_"):
                continue
            items = pick(did)
            if items:
                groups.setdefault(self.room_of.get(did, ""), []).extend(items)
        order = sorted(r for r in groups if r) + ([""] if "" in groups else [])
        return [(r, list(dict.fromkeys(groups[r]))) for r in order]

    def _grouped(self, head, groups):
        """The full text 'Head: Room - a, b; Room - c' and the flat spoken one."""
        flat = [x for _r, items in groups for x in items]
        none_room = "no room" if self.en else "\u0431\u0435\u0437 \u043a\u043e\u043c\u043d\u0430\u0442\u044b"  # i18n
        full = "%s: %s" % (head, "; ".join("%s - %s" % (self.room_name(r) if r else none_room, ", ".join(items)) for r, items in groups))
        return full, "%s: %s" % (head, cut(flat, self.en))

    def house_state(self, state):
        """Everything that is on / off in the house, by room."""
        want = state == "on"
        groups = self._groups(lambda did: self._switches([did], want))
        if not groups:
            word = ("on" if want else "off") if self.en else ("\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e" if want else "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e")  # i18n
            text = ("Nothing is %s" % word) if self.en else "\u041d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 %s" % word
            return text, text
        head = ("On" if want else "Off") if self.en else ("\u0412\u043a\u043b\u044e\u0447\u0435\u043d\u043e" if want else "\u0412\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e")  # i18n
        return self._grouped(head, groups)

    def list_devices(self, room=None):
        """The devices of the house (or of one room) with a short state: by room for the house."""
        def short(did):
            why = self.fresh(did)
            if why:
                return why
            vals = self.st(did).get("values") or {}
            for c in self.readable(did):
                p = self.part(did, c, vals.get(c["code"]))
                if p:
                    return p
            return "no state" if self.en else "\u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u044f \u043d\u0435\u0442"  # i18n
        ids = set(self._room_ids(room)) if room else None
        groups = self._groups(lambda did: ["%s: %s" % (self.name(did), short(did))] if ids is None or did in ids else [])
        if not groups:
            if room:
                text = "There are no devices in the %s" % room_en(room) if self.en else "\u0412 \u043a\u043e\u043c\u043d\u0430\u0442\u0435 \u00ab%s\u00bb \u043d\u0435\u0442 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432" % room  # i18n
            else:
                text = "No devices" if self.en else "\u0423\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432 \u043d\u0435\u0442"  # i18n
            return text, text
        if room:
            items = groups[0][1]
            head = ("Devices %s" % where_en(room)) if self.en else "\u0423\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u0430 %s" % where(room)  # i18n
            return "%s: %s" % (head, ", ".join(items)), "%s: %s" % (head, cut([i.split(": ")[0] for i in items], self.en))
        head = "Devices" if self.en else "\u0423\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u0430"  # i18n
        full, _ = self._grouped(head, groups)
        names = [i.split(": ")[0] for _r, items in groups for i in items]
        return full, "%s: %s" % (head, cut(names, self.en))


# ---- the fast phrases: a question is recognized by its words, never by a command word it may contain ----
QUESTION_RE = re.compile(r"(^|\s)(\u043a\u0442\u043e|\u0447\u0442\u043e|\u043a\u0430\u043a\u0430\u044f|\u043a\u0430\u043a\u043e\u0439|\u043a\u0430\u043a\u043e\u0435|\u043a\u0430\u043a\u0438\u0435|\u0441\u043a\u043e\u043b\u044c\u043a\u043e|\u0433\u0434\u0435|\u043a\u043e\u0433\u0434\u0430|\u043f\u043e\u0447\u0435\u043c\u0443|\u0435\u0441\u0442\u044c \u043b\u0438|\u0432\u0441\u0435 \u043b\u0438)\s|\b\u043b\u0438\b|\?")  # i18n
QUESTION_EN = re.compile(r"^(what|what's|whats|which|who|who's|whose|how|is|are|do|does|any|anyone|where|when|why|can|will)\b|\?")

STATUS_RE = re.compile(r"^(\u0431\u0430\u0442\u0430\u0440\u0435\u0439\u043a|\u0437\u0430\u0440\u044f\u0434|\u0447\u0442\u043e (\u0441\u0435\u0439\u0447\u0430\u0441 )?(\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e|\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e|\u0433\u043e\u0440\u0438\u0442|\u043e\u0442\u043a\u0440\u044b\u0442\u043e|\u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442)|\u043a\u0442\u043e \u0434\u043e\u043c\u0430|\u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440|\u0432\u043b\u0430\u0436\u043d\u043e\u0441\u0442|\u0441\u0442\u0430\u0442\u0443\u0441|\u043f\u0435\u0440\u0435\u0447\u0438\u0441\u043b|\u0441\u043f\u0438\u0441\u043e\u043a \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432|\u043f\u043e\u043a\u0430\u0436\u0438 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u0430|(\u043a\u0430\u043a\u0430\u044f |\u0447\u0442\u043e \u0437\u0430 )?\u043f\u043e\u0433\u043e\u0434|\u0431\u0443\u0434\u0435\u0442 (\u043b\u0438 )?(\u0434\u043e\u0436\u0434\u044c|\u0441\u043d\u0435\u0433|\u0433\u0440\u043e\u0437\u0430)|\u043f\u0440\u043e\u0433\u043d\u043e\u0437|weather|forecast)")  # i18n
LIST_EN = re.compile(r"^(list|show)( me)?( all)?( the| my)? devices\b")


# how a room feels, said without a question word ("it is cold in the bedroom"): asked of the room, never a command ("make it warmer" is one)
FEEL_RE = re.compile(r"(^|\s)(\u0445\u043e\u043b\u043e\u0434\u043d\u043e|\u0442\u0435\u043f\u043b\u043e|\u0436\u0430\u0440\u043a\u043e|\u043f\u0440\u043e\u0445\u043b\u0430\u0434\u043d\u043e|\u0434\u0443\u0448\u043d\u043e|\u0441\u044b\u0440\u043e|\u0432\u043b\u0430\u0436\u043d\u043e)(\s|$)")  # i18n


def is_query(text):
    """A question, or a short request for a state ("\u0431\u0430\u0442\u0430\u0440\u0435\u0439\u043a\u0438", "\u0447\u0442\u043e \u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e", "list the devices"): both are answered, never acted on."""  # i18n
    t = text.lower().strip()  # i18n: the next line
    return is_question(text) or bool(STATUS_RE.match(t)) or bool(LIST_EN.match(t)) or bool(FEEL_RE.search(t.replace("\u0451", "\u0435"))) \
        or clock_of(text) is not None


MONTHS_RU = ("\u044f\u043d\u0432\u0430\u0440\u044f", "\u0444\u0435\u0432\u0440\u0430\u043b\u044f", "\u043c\u0430\u0440\u0442\u0430", "\u0430\u043f\u0440\u0435\u043b\u044f", "\u043c\u0430\u044f", "\u0438\u044e\u043d\u044f", "\u0438\u044e\u043b\u044f", "\u0430\u0432\u0433\u0443\u0441\u0442\u0430", "\u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044f", "\u043e\u043a\u0442\u044f\u0431\u0440\u044f", "\u043d\u043e\u044f\u0431\u0440\u044f", "\u0434\u0435\u043a\u0430\u0431\u0440\u044f")  # i18n
DAYS_RU = ("\u043f\u043e\u043d\u0435\u0434\u0435\u043b\u044c\u043d\u0438\u043a", "\u0432\u0442\u043e\u0440\u043d\u0438\u043a", "\u0441\u0440\u0435\u0434\u0430", "\u0447\u0435\u0442\u0432\u0435\u0440\u0433", "\u043f\u044f\u0442\u043d\u0438\u0446\u0430", "\u0441\u0443\u0431\u0431\u043e\u0442\u0430", "\u0432\u043e\u0441\u043a\u0440\u0435\u0441\u0435\u043d\u044c\u0435")
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
DAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def clock_of(text):
    """"time" | "date" | "weekday" when the words ask the house clock, else None (Russian and English)."""
    t = " " + text.lower().replace("\u0451", "\u0435").strip(" ?") + " "  # i18n
    if re.search(r"\u0441\u043a\u043e\u043b\u044c\u043a\u043e (\u0441\u0435\u0439\u0447\u0430\u0441 )?\u0432\u0440\u0435\u043c\u0435\u043d\u0438|\u043a\u043e\u0442\u043e\u0440\u044b\u0439 (\u0441\u0435\u0439\u0447\u0430\u0441 )?\u0447\u0430\u0441|\bwhat time\b|\bthe time\b", t):
        return "time"
    if re.search(r"\u043a\u0430\u043a\u043e\u0439 (\u0441\u0435\u0433\u043e\u0434\u043d\u044f |\u0441\u0435\u0439\u0447\u0430\u0441 )?\u0434\u0435\u043d\u044c( \u043d\u0435\u0434\u0435\u043b\u0438)?|\bwhat day\b|\bday of the week\b", t):  # i18n
        return "weekday"
    if re.search(r"\u043a\u0430\u043a\u043e[\u0435\u044f] (\u0441\u0435\u0433\u043e\u0434\u043d\u044f |\u0441\u0435\u0439\u0447\u0430\u0441 )?(\u0447\u0438\u0441\u043b\u043e|\u0434\u0430\u0442\u0430)|\u043a\u0430\u043a\u043e\u0435 \u0441\u0435\u0433\u043e\u0434\u043d\u044f|\bwhat('s| is) (the )?date\b|\bwhat date\b|\btoday'?s date\b", t):  # i18n
        return "date"
    return None


def why_of(text):
    """The way a "why" question asks: "on" (\u0432\u043a\u043b\u044e\u0447\u0438\u043b\u0441\u044f, \u0437\u0430\u0433\u043e\u0440\u0435\u043b\u0441\u044f), "off" (\u0432\u044b\u043a\u043b\u044e\u0447\u0438\u043b\u0441\u044f, \u043f\u043e\u0433\u0430\u0441) or None."""  # i18n
    t = text.lower()
    if re.search(r"\u0432\u044b\u043a\u043b\u044e\u0447|\u043f\u043e\u0433\u0430\u0441|\u043e\u0442\u043a\u043b\u044e\u0447|\b(off|stopped)\b", t):  # i18n
        return "off"
    return "on" if re.search(r"\u0432\u043a\u043b\u044e\u0447|\u0437\u0430\u0433\u043e\u0440|\u0437\u0430\u0440\u0430\u0431\u043e\u0442|\bon\b|\bstarted\b", t) else None  # i18n


def _trigger(e, en=False):
    """What set a rule off, in words: a sensor that saw someone or nobody, a door, a button; else that the device fired."""
    code, g = str(e.get("code") or ""), str(e.get("gesture") or "").lower()
    nm = str(e.get("name") or e.get("device") or "")
    low, room, yes = nm[:1].lower() + nm[1:], str(e.get("room") or ""), g in ("true", "on", "1", "detected")
    if code in ("presence", "occupancy", "motion"):
        if en:
            return "%s saw someone" % low if yes else ("nobody was left %s" % where_en(room) if room else "%s saw nobody" % low)
        return "%s \u0437\u0430\u043c\u0435\u0442\u0438\u043b \u0447\u0435\u043b\u043e\u0432\u0435\u043a\u0430" % low if yes else ("%s \u043d\u0438\u043a\u043e\u0433\u043e \u043d\u0435 \u0441\u0442\u0430\u043b\u043e" % where(room) if room else "%s \u043f\u0435\u0440\u0435\u0441\u0442\u0430\u043b \u0432\u0438\u0434\u0435\u0442\u044c \u043b\u044e\u0434\u0435\u0439" % low)  # i18n
    if code == "contact":                                    # zigbee: contact true = closed
        return ("%s was %s" % (low, "closed" if yes else "opened")) if en else ("%s: %s" % (nm, "\u0437\u0430\u043a\u0440\u044b\u0442\u043e" if yes else "\u043e\u0442\u043a\u0440\u044b\u0442\u043e"))  # i18n
    if g in ("single", "double", "long", "hold", "press"):
        return ("someone pressed %s" % low) if en else ("\u043d\u0430\u0436\u0430\u043b\u0438 \u043a\u043d\u043e\u043f\u043a\u0443 \u00ab%s\u00bb" % nm)  # i18n
    return ("%s fired" % low) if en else ("\u0441\u0440\u0430\u0431\u043e\u0442\u0430\u043b \u0434\u0430\u0442\u0447\u0438\u043a \u00ab%s\u00bb" % nm)


def _went(value, want):
    vals = [str(v).lower() for v in (value.values() if isinstance(value, dict) else [value])]
    return any(v in (("on", "true", "1") if want == "on" else ("off", "false", "0")) for v in vals)


def why_answer(events, ids, name, en=False, want=None):
    """Why a device changed: the latest rule (of the rule log, newest first) that acted on it (that way, when the question says),
    in one sentence; else that none did."""
    for e in events:
        if e.get("target") in ids and (want is None or _went(e.get("value"), want)):
            tm = time.localtime(e.get("ts") or 0)  # i18n: the next line
            did = ("turned on" if en else "\u0432\u043a\u043b\u044e\u0447\u0438\u043b\u043e") if _went(e.get("value"), "on") else ("turned off" if en else "\u0432\u044b\u043a\u043b\u044e\u0447\u0438\u043b\u043e") \
                if _went(e.get("value"), "off") else ("changed" if en else "\u0438\u0437\u043c\u0435\u043d\u0438\u043b\u043e")  # i18n
            return ("At %d:%02d %s, and a rule %s %s." if en else "\u0412 %d:%02d %s, \u0438 \u043f\u0440\u0430\u0432\u0438\u043b\u043e %s %s.") % (tm.tm_hour, tm.tm_min, _trigger(e, en), did, name)
    return ("No rule changed %s lately; it was switched by hand or by its own button." % name if en
            else "\u041f\u0440\u0430\u0432\u0438\u043b\u0430 %s \u0432 \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0435 \u0432\u0440\u0435\u043c\u044f \u043d\u0435 \u0442\u0440\u043e\u0433\u0430\u043b\u0438: \u0432\u043a\u043b\u044e\u0447\u0430\u043b\u0438 \u0432\u0440\u0443\u0447\u043d\u0443\u044e \u0438\u043b\u0438 \u043a\u043d\u043e\u043f\u043a\u043e\u0439." % name)  # i18n


def clock(what, en=False, now=None):
    """One short sentence of the house clock (the container's local time, TZ of the house)."""
    tm = time.localtime(now if now is not None else time.time())
    if what == "time":
        return ("It is %d:%02d." if en else "\u0421\u0435\u0439\u0447\u0430\u0441 %d:%02d.") % (tm.tm_hour, tm.tm_min)     # i18n: "9:05": TTS reads a leading 0 aloud
    day = "%d %s" % (tm.tm_mday, MONTHS_EN[tm.tm_mon - 1] if en else MONTHS_RU[tm.tm_mon - 1])
    if what == "weekday":
        return ("Today is %s, %s." % (DAYS_EN[tm.tm_wday], day)) if en else ("\u0421\u0435\u0433\u043e\u0434\u043d\u044f %s, %s." % (DAYS_RU[tm.tm_wday], day))  # i18n
    return ("Today is %s." % day) if en else ("\u0421\u0435\u0433\u043e\u0434\u043d\u044f %s." % day)


def is_question(text):
    """True for a question ("\u0432\u043a\u043b\u044e\u0447\u0451\u043d \u043b\u0438 \u0441\u0432\u0435\u0442", "is the light on"): it must never reach a command rule, whose stems it may share."""  # i18n
    t = text.lower().strip()
    if lang_of(text) == "en":
        return bool(QUESTION_EN.search(t))
    return bool(QUESTION_RE.search(" " + t + " ")) or t.startswith(("\u043a\u0442\u043e ", "\u0447\u0442\u043e ", "\u043a\u0430\u043a\u0430\u044f ", "\u043a\u0430\u043a\u043e\u0439 "))  # i18n


def aspect_of(text):
    """What of a room the words ask: "humidity" | "temp" | "on" | "off" | None. Only words that were HEARD count (never a model's guess)."""
    t = text.lower().replace("\u0451", "\u0435")  # i18n
    if re.search(r"\u0432\u043b\u0430\u0436\u043d|\u0441\u044b\u0440\u043e\u0441\u0442|\u0441\u044b\u0440\u043e\b|\bhumid|\bdamp", t):
        return "humidity"
    if re.search(r"\u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440|\u0433\u0440\u0430\u0434\u0443\u0441|\u0442\u0435\u043f\u043b|\u0445\u043e\u043b\u043e\u0434\u043d|\u0436\u0430\u0440\u043a|\btemperature|\bdegrees|\bhot\b|\bcold\b|\bwarm|\bcool\b", t):  # i18n
        return "temp"
    if re.search(r"\u0447\u0442\u043e (\u0441\u0435\u0439\u0447\u0430\u0441 )?(\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e|\u043d\u0435 \u0433\u043e\u0440\u0438\u0442)|\bwhat('s| is)( \w+)? (off|turned off)|\bwhich .* (are |is )?off\b", t):  # i18n
        return "off"
    if re.search(r"\u0447\u0442\u043e (\u0441\u0435\u0439\u0447\u0430\u0441 )?(\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e|\u0433\u043e\u0440\u0438\u0442|\u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442)|\bwhat('s| is)( \w+)? (on|turned on|running)|\bwhich .* (are |is )?on\b", t):  # i18n
        return "on"
    return None


def fix_aspect(q, text):
    """A room query from the model keeps an aspect only when its word is in the phrase; else it is the short summary of the room."""
    if q.get("type") == "room":
        q.pop("aspect", None)
        a = aspect_of(text)
        if a:
            q["aspect"] = a
    return q


def find_room(t, rooms):
    """The room a phrase names (Russian by the first four letters, English by its words), else None."""
    r = next((r for r in rooms if r.lower().replace("\u0451", "\u0435")[:4] in t), None)  # i18n
    if r:
        return r
    for word, stem in ROOM_EN_WORDS:
        if re.search(r"\b%s\b" % re.escape(word), t):
            hit = next((r for r in rooms if r.lower().replace("\u0451", "\u0435").startswith(stem[:4])), None)  # i18n
            if hit:
                return hit
    return None


def quick(text, rooms, find_devices):
    """The query of a fast phrase, or None: {"type": "who_home"} | {"type": "room", "room", "aspect"} | {"type": "device", "ids", "code"} |
    {"type": "all", "kind"} | {"type": "state", "state": "on"|"off"} | {"type": "list", "room"?}.
    rooms: [room name]; find_devices(words) -> [device ids] the words name. Russian and English."""
    t = " " + text.lower().replace("\u0451", "\u0435").strip(" ?") + " "  # i18n
    c = clock_of(text)
    if c:
        return {"type": "clock", "what": c}
    if re.search(r"\b(\u043a\u0442\u043e|\u043d\u0438\u043a\u0442\u043e|\u0435\u0441\u0442\u044c \u043b\u0438 \u043a\u0442\u043e)\b.*\b(\u0434\u043e\u043c\u0430|\u0432 \u0434\u043e\u043c\u0435)\b|\b\u043a\u0442\u043e \u0434\u043e\u043c\u0430\b|\b\u0434\u043e\u043c\u0430 \u043b\u0438\b|\bwho('s| is| are)?( at)? home\b|\b(anyone|anybody|someone) (at )?home\b", t):  # i18n
        return {"type": "who_home"}
    if re.search(r"\u0431\u0430\u0442\u0430\u0440\u0435|\u0437\u0430\u0440\u044f\u0434|\bbatter", t):  # i18n
        return {"type": "all", "kind": "battery"}
    import voice_weather                                      # the weather outside: before "how many degrees" is taken for a room
    if voice_weather.is_weather(t):
        rest = voice_weather.device_words(t)
        if not (rest and find_devices(rest)):                 # "did the rain sensor trigger" names a device: not the forecast
            return dict(voice_weather.flags_of(t), type="weather")
    room = find_room(t, rooms)
    if re.search(r"\u043f\u0435\u0440\u0435\u0447\u0438\u0441\u043b|\u0441\u043f\u0438\u0441\u043e?\u043a? \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432|\u043a\u0430\u043a\u0438\u0435 (\u0435\u0441\u0442\u044c )?\u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u0430|\u043f\u043e\u043a\u0430\u0436\u0438 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u0430|\b(list|show)( me)?( all)?( the| my)? devices\b|\b(which|what) devices\b", t):  # i18n
        return {"type": "list", "room": room} if room else {"type": "list"}
    a = aspect_of(t)
    if a in ("on", "off") and re.search(r"\u0447\u0442\u043e (\u0441\u0435\u0439\u0447\u0430\u0441 )?(\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e|\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e|\u0433\u043e\u0440\u0438\u0442|\u043d\u0435 \u0433\u043e\u0440\u0438\u0442|\u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442)|\bwhat('s| is)\b|\bwhich\b", t):  # i18n
        if room:
            return {"type": "room", "room": room, "aspect": a}
        return {"type": "state", "state": a}  # i18n: the next line
    if re.search(r"\u0432\u0435\u0437\u0434\u0435 (\u043b\u0438 )?(\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d|\u0433\u043e\u0440\u0438\u0442)|\u0432\u0435\u0441\u044c \u0441\u0432\u0435\u0442|\u043a\u0430\u043a\u043e\u0439 \u0441\u0432\u0435\u0442|\u0433\u0434\u0435 \u0433\u043e\u0440\u0438\u0442|\u0433\u043e\u0440\u0438\u0442 \u043b\u0438 \u0441\u0432\u0435\u0442|\b(any|which|what) lights?( are| is)? on\b", t) \
            and not re.search(r"(\u0432|\u043d\u0430) \w+", t.replace("\u0432 \u0434\u043e\u043c\u0435", "")) and not room:  # i18n
        return {"type": "all", "kind": "lights"}
    if re.search(r"\u0447\u0442\u043e (\u043d\u0435 \u0432 \u0441\u0435\u0442\u0438|\u043e\u0444\u043b\u0430\u0439\u043d|\u043d\u0435 \u043e\u0442\u0432\u0435\u0447\u0430\u0435\u0442)|\u0432\u0441\u0435 \u043b\u0438 .*\u0432 \u0441\u0435\u0442\u0438|\boffline\b|\bnot responding\b", t):  # i18n
        return {"type": "all", "kind": "online"}
    if re.search(r"\u0447\u0442\u043e (\u043e\u0442\u043a\u0440\u044b\u0442\u043e|\u043d\u0435 \u0437\u0430\u043a\u0440\u044b\u0442\u043e)|\u0432\u0441\u0435 \u043b\u0438 \u0437\u0430\u043a\u0440\u044b\u0442\u043e|\u043e\u0442\u043a\u0440\u044b\u0442\u043e \u043b\u0438|\bwhat('s| is) open\b|\banything open\b", t) and not find_devices(t.split()):  # i18n
        return {"type": "all", "kind": "open"}
    if a in ("humidity", "temp") and room:
        return {"type": "room", "room": room, "aspect": a}
    if re.search(r"\b\u043b\u0438\b|^ *is (the )?\w|^ *are (the )?\w", t):  # i18n
        ids = find_devices(t.split())
        if ids:
            return {"type": "device", "ids": ids[:3], "code": None}
    return None


def answer_pair(state, q):
    """(the full text, the short text to read aloud) for a query dict (from quick() or the model's `ask` line)."""
    t = q.get("type")
    if t == "state":
        return state.house_state(q["state"])
    if t == "list":
        return state.list_devices(q.get("room"))
    text = answer(state, q)
    return screen(text), text


def screen(text):
    """The shown form of an answer: "27,9\u00b0" for "27,9 \u0433\u0440\u0430\u0434\u0443\u0441\u0430" (the spoken one keeps the word, the voice reads it)."""  # i18n
    return re.sub(r"(\d+(?:[,.]\d+)?) (?:\u0433\u0440\u0430\u0434\u0443\u0441(?:\u0430|\u043e\u0432)?|degrees?)\b", "\\1\u00b0", text)


def answer(state, q):
    """The words for a query dict."""
    t = q.get("type")
    if t == "clock":
        return clock(q.get("what"), state.en, state.now())
    if t == "who_home":
        return state.who_home()
    if t == "room":
        return state.room(q["room"], q.get("aspect"))
    if t == "all":
        return state.all(q["kind"]) or ("I do not know how to answer" if state.en else "\u041d\u0435 \u0437\u043d\u0430\u044e, \u043a\u0430\u043a \u043e\u0442\u0432\u0435\u0442\u0438\u0442\u044c")  # i18n
    if t == "device":
        return "; ".join(x for x in (state.device(i, q.get("code")) for i in q["ids"]) if x) or ("No such device" if state.en else "\u041d\u0435\u0442 \u0442\u0430\u043a\u043e\u0433\u043e \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u0430")  # i18n
    if t in ("state", "list"):
        return answer_pair(state, q)[0]
    return ""
