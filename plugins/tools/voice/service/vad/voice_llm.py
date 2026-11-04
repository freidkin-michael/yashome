"""LLM fallback of the voice path: a phrase the command map and the Vosk grammar did not understand goes to a small local model
(Ollama) that answers with ONE line of a strict DSL. The house parses that line against its own registry and acts only on what
validates. The model has no action rights: it never sees content (news, mail), only the phrase and the device registry.

DSL:  none | all_off [except=<name>,<name>] | <name> <code>=<value> [; <name> <code>=<value> ...]
<name> is a slug of the device id (ids have spaces); the registry maps it back."""
import json
import re
import threading
import time
import urllib.request

from voice_state import NOISE

HIDDEN_LABELS = ("-",)                      # a channel the house names "-" is not connected: nothing speaks of it
KINDS = ("switch", "bright", "setting_enum", "setting_int", "ir_press")     # what a phrase may drive
# settings (an enum, a number) are driven by voice only on these device categories: a radar's delay is not "a light"
SETTINGS_OK = ("climate",)
RISKY_RE = re.compile(r"lock|\u0437\u0430\u043c\u043e\u043a|gate|\u0432\u043e\u0440\u043e\u0442|door|\u0434\u0432\u0435\u0440|garage|\u0433\u0430\u0440\u0430\u0436|alarm|\u0441\u0438\u0433\u043d\u0430\u043b|security|\u043e\u0445\u0440\u0430\u043d|siren|\u0441\u0438\u0440\u0435\u043d", re.I)  # i18n
RISKY_CODES = {"locked", "lock", "open", "arm", "armed"}
CONFIRM_YES = ("\u0434\u0430", "\u0434\u0430\u0432\u0430\u0439", "\u0430\u0433\u0430", "\u0432\u0435\u0440\u043d\u043e", "\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u044e", "\u0432\u044b\u043f\u043e\u043b\u043d\u044f\u0439", "\u0434\u0435\u043b\u0430\u0439")  # i18n
CONFIRM_NO = ("\u043d\u0435\u0442", "\u043d\u0435", "\u043e\u0442\u043c\u0435\u043d\u0430", "\u043e\u0442\u0431\u043e\u0439", "\u0441\u0442\u043e\u043f")
SAY_NOT_UNDERSTOOD = "\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430."  # i18n
# the chat of the text box: the same model, a prompt with NO devices and no actions; its text is only shown, never parsed
CHAT_SYSTEM = ("\u041e\u0442\u0432\u0435\u0442\u044c \u043d\u0430 \u0432\u043e\u043f\u0440\u043e\u0441 \u043f\u043e \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443: \u0442\u043e\u043b\u044c\u043a\u043e \u0442\u043e, \u0447\u0442\u043e \u0441\u043f\u0440\u043e\u0441\u0438\u043b\u0438, \u043e\u0434\u043d\u043e-\u0434\u0432\u0430 \u043a\u043e\u0440\u043e\u0442\u043a\u0438\u0445 \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u044f, \u0431\u0435\u0437 \u0432\u0441\u0442\u0443\u043f\u043b\u0435\u043d\u0438\u0439 \u0438 \u0431\u0435\u0437 "  # i18n
               "\u0440\u0430\u0441\u0441\u0443\u0436\u0434\u0435\u043d\u0438\u0439. \u041e\u0442\u0432\u0435\u0447\u0430\u0439 \u043d\u0430 \u044f\u0437\u044b\u043a\u0435 \u0432\u043e\u043f\u0440\u043e\u0441\u0430. \u041e\u0431\u0449\u0438\u0435 \u0437\u043d\u0430\u043d\u0438\u044f (\u043d\u0430\u0443\u043a\u0430, \u044f\u0437\u044b\u043a\u0438, \u0438\u0441\u0442\u043e\u0440\u0438\u044f, \u0441\u043e\u0432\u0435\u0442\u044b) \u043e\u0442\u0432\u0435\u0447\u0430\u0439 \u0443\u0432\u0435\u0440\u0435\u043d\u043d\u043e \u0438 \u0442\u043e\u0447\u043d\u043e. "
               "\u0422\u044b \u043d\u0435 \u0432\u0438\u0434\u0438\u0448\u044c \u0434\u043e\u043c: \u043a\u0442\u043e \u0434\u043e\u043c\u0430, \u043a\u0430\u043a\u0430\u044f \u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0430, \u0433\u043e\u0440\u0438\u0442 \u043b\u0438 \u0441\u0432\u0435\u0442, \u043a\u0430\u043a\u0430\u044f \u043f\u043e\u0433\u043e\u0434\u0430 \u0438 \u0447\u0442\u043e \u0432 \u043d\u043e\u0432\u043e\u0441\u0442\u044f\u0445, \u0442\u044b \u043d\u0435 \u0437\u043d\u0430\u0435\u0448\u044c, "  # i18n
               "\u043e\u0431 \u044d\u0442\u043e\u043c \u0447\u0435\u0441\u0442\u043d\u043e \u0441\u043a\u0430\u0436\u0438 \u00ab\u043d\u0435 \u0437\u043d\u0430\u044e\u00bb. \u0422\u044b \u043d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0432\u043a\u043b\u044e\u0447\u0430\u0435\u0448\u044c, \u043d\u0435 \u0432\u044b\u043a\u043b\u044e\u0447\u0430\u0435\u0448\u044c \u0438 \u043d\u0435 \u043c\u0435\u043d\u044f\u0435\u0448\u044c. \u041d\u0435 \u043f\u0440\u0438\u0434\u0443\u043c\u044b\u0432\u0430\u0439.")
THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def strip_think(text):
    """The answer without the model's reasoning: a <think> block (closed or cut off) is never shown."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return re.sub(r"<think>.*", "", THINK_RE.sub("", text), flags=re.S).strip()
HOUSE_WORDS = re.compile(r"\u0434\u043e\u043c\b|\u0434\u043e\u043c\u0430|\u0434\u043e\u043c\u043e\u0439|\u043a\u0432\u0430\u0440\u0442\u0438\u0440|\u043a\u043e\u043c\u043d\u0430\u0442|\u0441\u0432\u0435\u0442|\u043b\u0430\u043c\u043f|\u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440|\u0433\u0440\u0430\u0434\u0443\u0441|\u0432\u043b\u0430\u0436\u043d|\u0432\u043a\u043b\u044e\u0447|\u0432\u044b\u043a\u043b\u044e\u0447|\u043e\u0442\u043a\u0440\u044b\u0442|\u0437\u0430\u043a\u0440\u044b\u0442|\u0437\u0430\u043f\u0435\u0440\u0442|\u0437\u0430\u043c\u043e\u043a|"  # i18n
                         r"\u0434\u0432\u0435\u0440|\u0431\u043e\u0439\u043b\u0435\u0440|\u043a\u043e\u043d\u0434\u0438\u0446|\u0442\u0435\u043b\u0435\u0432\u0438\u0437|\u0431\u0430\u0442\u0430\u0440\u0435|\u0434\u0430\u0442\u0447\u0438\u043a|\u043a\u0442\u043e |\u043a\u043e\u0433\u043e |\u043d\u0438\u043a\u043e\u0433\u043e|\u043f\u0440\u0438\u0441\u0443\u0442\u0441\u0442\u0432|\u0443\u0448[\u0435\u0451]\u043b|\u043f\u0440\u0438\u0448\u043b|\u043f\u0440\u0438\u0448\u0451\u043b|\u0441\u043f\u0430\u043b\u044c\u043d|\u0441\u0430\u043b\u043e\u043d|\u043a\u0443\u0445\u043d|"
                         r"\u043a\u043e\u0440\u0438\u0434\u043e\u0440|\u0433\u043e\u0441\u0442\u0438\u043d|\u0432\u0430\u043d\u043d", re.I)  # i18n
HOUSE_REFUSAL = "\u042d\u0442\u043e \u0432\u043e\u043f\u0440\u043e\u0441 \u043e \u0434\u043e\u043c\u0435: \u0441\u043f\u0440\u043e\u0441\u0438, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440, \u00ab\u043a\u0442\u043e \u0434\u043e\u043c\u0430\u00bb \u0438\u043b\u0438 \u00ab\u043a\u0430\u043a\u0430\u044f \u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0430 \u0432 \u0441\u043f\u0430\u043b\u044c\u043d\u0435\u00bb."
ACK_RE = re.compile(r"^(\u044f|\u043c\u044b|\u043e\u043d|\u043e\u043d\u0430|\u043e\u043d\u0438)\s+(\u0434\u043e\u043c\u0430|\u043f\u0440\u0438\u0448\u043b[\u0430\u0438]?|\u043f\u0440\u0438\u0448\u0451\u043b|\u043f\u0440\u0438\u0448\u0435\u043b|\u0443\u0448\u043b[\u0430\u0438]?|\u0443\u0448\u0451\u043b|\u0443\u0448\u0435\u043b|\u0432\u0435\u0440\u043d\u0443\u043b\w+|\u043b\u0451\u0433\w*|\u043b\u0435\u0433\w*|\u0441\u043f\u0430\u0442\u044c)\b", re.I)  # i18n


def chat_gate(text):
    """What the chat does with the user's words, before any model: None = chat on, else the fixed answer. A question or statement
    about the house, the people or the state of things is never the model's to answer: it would invent."""
    t = text.lower().strip(" ?!.")
    if ACK_RE.match(t):
        return "\u041f\u043e\u043d\u044f\u0442\u043d\u043e."  # i18n
    if HOUSE_WORDS.search(" " + t + " "):
        return HOUSE_REFUSAL
    return None


def chat_filter(answer):
    """A chat answer that talks about the house, the people or devices is replaced by the refusal (the caller logs it)."""
    return HOUSE_REFUSAL if HOUSE_WORDS.search(" " + answer.lower() + " ") else answer


def slug(text):
    return re.sub(r"[^a-z0-9\u0430-\u044f]+", "_", text.lower()).strip("_")  # i18n


def _stem_hit(word, phrase_words):
    """A phrase word that starts like `word` (4 leading letters; shorter words must match whole)."""
    n = min(4, len(word))
    return n >= 2 and any(w[:n] == word[:n] for w in phrase_words)


FACTS_MAX = 24                       # lines of the home map in the prompt: it stays small
GENERIC = ("\u0441\u0432\u0435\u0442", "\u043b\u0430\u043c\u043f\u0430", "\u043b\u0430\u043c\u043f\u043e\u0447\u043a\u0430", "\u0440\u043e\u0437\u0435\u0442\u043a\u0430", "\u043b\u0435\u043d\u0442\u0430", "light", "lamp", "socket", "strip")   # i18n: words of no one device: never a map synonym
_GENERIC4 = {g[:4] for g in GENERIC}


def map_words(m, known=()):
    """The words of the load names of the owner's map as extra words of the device that controls the load: [(6-letter stem, device id)].
    m: the map ({"items", "links"}); known: [(word, device id)] of the other names (command map, glossary, device names). A stem is taken only
    when its 4 leading letters (how a phrase word is matched) belong to this one device among all the names, and it is not a generic word."""
    owners = {}
    for w, dev in known:
        owners.setdefault(w[:4], set()).add(dev)
    cand = []
    for ln in m.get("links") or []:
        load = next((i for i in m.get("items") or [] if i.get("id") == ln.get("load")), None)
        for w in re.findall(r"[\u0430-\u044f\u0451]{4,}", (load or {}).get("name", "").lower()):  # i18n
            owners.setdefault(w[:4], set()).add(ln.get("dev"))
            cand.append((w, ln.get("dev")))
    return [(w[:6], dev) for w, dev in cand if w[:4] not in _GENERIC4 and owners[w[:4]] == {dev}]


def _fact_text(f):
    return str(f.get("text") or "") if isinstance(f, dict) else str(f)


def _fact_rank(f):
    """Controlled loads first, then the other loads, then the rest. A fact of the product's structured list says what it is;
    a plain line (an older product) is a controlled load when its text says "controlled by"."""
    if isinstance(f, dict):
        return 0 if f.get("load") and f.get("controlled") else 1 if f.get("load") else 2
    return 0 if "controlled by" in str(f) else 2


def facts_of(js):
    """The facts of the product's /map/facts answer {"lines": [...], "items": [{text, load, controlled}]}: the items when the product
    sends them, else the plain lines."""
    js = js if isinstance(js, dict) else {}
    items = js.get("items")
    if isinstance(items, list) and items:
        return [i for i in items if isinstance(i, dict) and i.get("text")]
    return [str(x) for x in js.get("lines") or []]


class Registry:
    """The house devices as the model sees them, built from the live /api/devices list."""

    def __init__(self, devices, zone_words=(), rooms=None, spoken=None, facts=None):
        """zone_words: [(word stem, device id)]: the command map and the glossary, the house's own synonyms.
        rooms: {device id: room name}; the device names and the channel labels are the custom ones the house shows."""
        self.dev = {}                 # id -> {"name","room","controls":{code: control}}
        self.names = {}               # slug -> id (a phrase may command these)
        self.slug = {}                # id -> slug
        self.qnames = {}              # slug -> id (a question may name these: sensors too)
        self.qlist = []               # (slug, id, label, room, [codes a question reads])
        for d in devices:
            if d.get("voice_role"):
                continue
            rc = [c["code"] for c in d.get("controls") or [] if c["code"] not in NOISE
                  and c.get("kind") in ("switch", "bright", "sensor", "sensor_text", "setting_int", "setting_enum")]
            if not rc:
                continue
            qs, k = slug(d["id"]), 2
            while qs in self.qnames:
                qs, k = slug(d["id"]) + "_%d" % k, k + 1
            self.qnames[qs] = d["id"]
            self.qlist.append((qs, d["id"], d.get("name") or d["id"], d.get("room") or (rooms or {}).get(d["id"], ""), rc,
                               [c["code"] for c in d.get("controls") or [] if c["code"] in rc and c["kind"] in ("sensor", "sensor_text")]))
        self.room_slugs = {slug(r): r for r in sorted({r for r in list((rooms or {}).values()) + [d.get("room") for d in devices if not d.get("voice_role")] if r})}
        for d in devices:
            ctl = {c["code"]: c for c in d.get("controls") or []
                   if c.get("kind") in KINDS and not c.get("ro") and c.get("label") not in HIDDEN_LABELS
                   and (not c["kind"].startswith("setting_") or d.get("category") in SETTINGS_OK)}
            if not ctl or d.get("voice_role"):
                continue
            s = next((q for q, i in self.qnames.items() if i == d["id"]), slug(d["id"]))
            self.names[s] = d["id"]
            self.slug[d["id"]] = s
            self.dev[d["id"]] = {"name": d.get("name") or d["id"], "room": d.get("room") or (rooms or {}).get(d["id"], ""), "controls": ctl}
        self.words = {}               # device id -> [synonym stems]
        for w, target in zone_words:
            if target in self.dev and w not in self.words.setdefault(target, []):
                self.words[target].append(w)
        self.spoken = spoken or {}      # glossary: {"<id>" or "<id>/<code>": {"acc": "...", "gen": "..."}}, how a question names a device
        lines = [t for _, t in sorted(((_fact_rank(f), _fact_text(f)[:110]) for f in (facts or [])), key=lambda x: x[0])]   # stable
        self.facts = lines[:FACTS_MAX]                                  # the owner's home map: where a load is and what controls it
        self.facts_dropped = len(lines) - len(self.facts)
        self.system = self._prompt()

    @staticmethod
    def _values(c):
        k, rng = c["kind"], c.get("range")
        if k == "switch":
            return "on|off"
        if k == "ir_press":
            return "press"
        if k == "bright":
            return "0-100"
        if k == "setting_int":
            return "%s-%s" % (c.get("min", "?"), c.get("max", "?"))
        if rng and all(str(x).lstrip("-").isdigit() for x in rng):
            return "%s-%s|+1|-1" % (min(map(int, rng)), max(map(int, rng)))
        return "|".join(str(x) for x in rng or []) or "text"

    @staticmethod
    def _chan(code, c):
        """The name the house gave a channel ("\u0421\u0430\u043b\u043e\u043d", "\u0414\u0432\u0435\u0440\u044c"): the model needs it to tell the channels of one switch apart."""  # i18n
        lab = (c.get("label") or "").strip()
        return "(%s)" % lab if code.startswith("state") and lab not in ("", "-") else ""

    def _prompt(self):
        lines = []
        for did, d in self.dev.items():
            ctl = " ".join("%s%s=%s" % (code, self._chan(code, c), self._values(c)) for code, c in d["controls"].items())
            lines.append("%s | %s, %s | %s" % (self.slug[did], d["name"], d["room"] or "-", ctl))
        reads = ["%s | %s, %s | %s" % (q, nm, rm or "-", " ".join(sensors)) for q, _i, nm, rm, _rc, sensors in self.qlist if sensors]
        rooms = " ".join("%s=%s" % (k, v) for k, v in self.room_slugs.items())
        words = "; ".join("%s=%s" % (",".join(w), self.slug[did]) for did, w in self.words.items() if w)
        ex_sw = next((did for did, d in self.dev.items() if any(c["kind"] == "switch" for c in d["controls"].values())), None)
        ex_code = next(code for code, c in self.dev[ex_sw]["controls"].items() if c["kind"] == "switch") if ex_sw else "state"
        ex = self.slug.get(ex_sw, "name")
        return ("Russian voice phrase -> one line command, nothing else.\nDevices (name | label, room | code=values):\n"
                + "\n".join(lines) + "\nWords (stem=name): " + words
                + "\nReadable (name | label, room | sensor codes):\n" + "\n".join(reads) + "\nRooms: " + rooms
                + ("\nPlaces (a load the owner marked on the home map: where it is and what controls it): " + "; ".join(self.facts) if self.facts else "")
                + "\nNumbers: \u0442\u0435\u043f\u043b\u0435\u0435/\u0432\u044b\u0448\u0435/\u0431\u043e\u043b\u044c\u0448\u0435 = +1, \u0445\u043e\u043b\u043e\u0434\u043d\u0435\u0435/\u043d\u0438\u0436\u0435/\u043c\u0435\u043d\u044c\u0448\u0435 = -1 (temp=+1); a mode only when a mode is named.\nAnswer forms:\n"  # i18n
                + "A question about a state (is it on, how warm, who is home, what is open) is ALWAYS an ask line, never a command:\n"
                + "ask <name> [code] | ask room=<room> [temp|humidity|on|off] | ask weather [now|today|tomorrow|evening|rain] | ask who_home | ask all lights|open|battery|online|on | ask clock time|date|weekday | ask why <name>\n"
                + "%s %s=on\n%s %s=off; %s %s=on\nall_off except=%s\nnone\n" % (ex, ex_code, ex, ex_code, ex, ex_code, ex)
                + "Examples:\n\u0432\u043a\u043b\u044e\u0447\u0438 \u0441\u0432\u0435\u0442 -> %s %s=on\n\u0432\u044b\u043a\u043b\u044e\u0447\u0438 \u0432\u0441\u0451 \u043a\u0440\u043e\u043c\u0435 \u044d\u0442\u043e\u0433\u043e -> all_off except=%s\n\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u0432\u0440\u0435\u043c\u0435\u043d\u0438 -> ask clock time\n\u043a\u0430\u043a\u043e\u0435 \u0441\u0435\u0433\u043e\u0434\u043d\u044f \u0447\u0438\u0441\u043b\u043e -> ask clock date\n\u043d\u0430\u043f\u043e\u043c\u043d\u0438 \u0437\u0430\u043a\u043e\u043d \u041e\u043c\u0430 -> none\n"  # i18n
                "\u043a\u0442\u043e \u0434\u043e\u043c\u0430 -> ask who_home\n\u0432\u043a\u043b\u044e\u0447\u0451\u043d \u043b\u0438 \u0441\u0432\u0435\u0442 -> ask %s\n\u043f\u043e\u0447\u0435\u043c\u0443 \u0432\u043a\u043b\u044e\u0447\u0438\u043b\u0441\u044f \u0441\u0432\u0435\u0442 -> ask why %s\n\u043a\u0430\u043a\u0430\u044f \u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0430 \u0432 \u0441\u043f\u0430\u043b\u044c\u043d\u0435 -> ask room=<room> temp\n\u043a\u0430\u043a\u0430\u044f \u043f\u043e\u0433\u043e\u0434\u0430 \u0437\u0430\u0432\u0442\u0440\u0430 -> ask weather tomorrow\n\u0447\u0442\u043e \u043e\u0442\u043a\u0440\u044b\u0442\u043e -> ask all open"
                % (ex, ex_code, ex, ex, ex))

    def label(self, did):
        return self.dev[did]["name"]

    def say(self, did, code=None, case="acc"):
        """How a question names a device or one channel: the glossary's spoken form in that case, else the house's own name; never the id."""
        for key in ((did + "/" + code) if code else None, did):
            f = (self.spoken.get(key) or {}) if key else {}
            if f.get(case) or f.get("acc"):
                return f.get(case) or f["acc"]
        return self.dev[did]["name"]

    def named_in(self, did, phrase_words):
        """True when the phrase itself names the device: a word of its label, room, id or a map synonym."""
        d = self.dev[did]
        labels = " ".join(c.get("label") or "" for c in d["controls"].values())
        cand = re.findall(r"[a-z\u0430-\u044f\u04510-9]+", (d["name"] + " " + d["room"] + " " + did + " " + labels).lower()) + self.words.get(did, [])  # i18n
        return any(_stem_hit(w, phrase_words) for w in cand if len(w) >= 3)

    def risky(self, did):
        d = self.dev[did]
        return bool(RISKY_RE.search(did + " " + d["name"])) or any(c in RISKY_CODES for c in d["controls"])


ASK_ALL = ("lights", "open", "battery", "online", "on")
ASK_CLOCK = ("time", "date", "weekday")
ASK_ASPECTS = ("temp", "humidity", "on", "off")
ASK_WEATHER = ("now", "today", "tomorrow", "evening", "rain")      # when a weather question asks                  # what of a room the question asks


def parse_dsl(line, names, qnames=None, room_slugs=None):
    """The one-line DSL -> {"action", "except", "intents"} (a question: {"action": "ask", "query"}); strict: any unknown name or
    malformed part = ValueError."""
    line = line.strip().splitlines()[0].strip() if line.strip() else ""
    if line == "none":
        return {"action": "none", "except": [], "intents": []}
    if line.startswith("ask"):
        q = {"action": "ask", "except": [], "intents": []}
        toks = re.sub(r"\([^)]*\)", "", line).split()[1:]                  # the model may copy a channel name or add a word
        if not toks:
            raise ValueError(line)
        first = toks[0]
        if first == "who_home" and len(toks) == 1:
            q["query"] = {"type": "who_home"}
        elif first == "why" and len(toks) == 2 and toks[1] in (qnames if qnames is not None else names):
            q["query"] = {"type": "why", "ids": [(qnames if qnames is not None else names)[toks[1]]]}
        elif first == "clock" and len(toks) == 2 and toks[1] in ASK_CLOCK:
            q["query"] = {"type": "clock", "what": toks[1]}
        elif first == "all" and len(toks) == 2 and toks[1] in ASK_ALL:
            q["query"] = {"type": "all", "kind": toks[1]}
        elif first == "weather" and len(toks) <= 2 and (len(toks) == 1 or toks[1] in ASK_WEATHER):
            q["query"] = {"type": "weather", "day": toks[1] if len(toks) == 2 and toks[1] != "rain" else None, "rain": len(toks) == 2 and toks[1] == "rain",
                          "temp": False}
        elif first.startswith("room=") and len(toks) <= 2 and first[5:] in (room_slugs or {}) and (len(toks) == 1 or toks[1] in ASK_ASPECTS):
            q["query"] = {"type": "room", "room": room_slugs[first[5:]]}
            if len(toks) == 2:
                q["query"]["aspect"] = toks[1]
        elif first in (qnames if qnames is not None else names):
            ids = (qnames if qnames is not None else names)[first]
            q["query"] = {"type": "device", "ids": [ids], "code": toks[1] if len(toks) > 1 and re.fullmatch(r"\w+", toks[1]) else None}
        else:
            raise ValueError(line)
        return q
    if line.startswith("all_off"):
        m = re.fullmatch(r"all_off(?: except=([\w,]*))?", line)
        if not m:
            raise ValueError(line)
        keep = [x for x in (m.group(1) or "").split(",") if x and x != "none"]       # "except=none" = nothing excepted
        if any(x not in names for x in keep):
            raise ValueError(line)
        return {"action": "all_off", "except": [names[x] for x in keep], "intents": []}
    intents = []
    for part in line.split(";"):
        m = re.fullmatch(r"\s*(\S+)\s+(\w+)(?:\([^)]*\))?=(\S+)\s*", part)       # the model may copy a channel name: state_l1(1)=on
        if not m or m.group(1) not in names:
            raise ValueError(part)
        intents.append({"device": names[m.group(1)], "code": m.group(2), "value": m.group(3)})
    return {"action": "set", "except": [], "intents": intents}


def check_value(c, value, current=None):
    """The value to send for control c, or None when it does not fit: on/off, 0-100, a listed option, an integer in range, +1/-1."""
    v = str(value).strip().lower()
    k, rng = c["kind"], c.get("range")
    if k == "switch":
        return v if v in ("on", "off") else None
    if k == "ir_press":
        return "press" if v == "press" else None
    if k == "bright":
        return int(v) if v.isdigit() and 0 <= int(v) <= 100 else None
    if k == "setting_int":
        n = int(v) if re.fullmatch(r"\d+", v) else None
        lo, hi = c.get("min"), c.get("max")
        return n if n is not None and (lo is None or n >= lo) and (hi is None or n <= hi) else None
    if rng:
        nums = [int(x) for x in rng if str(x).lstrip("-").isdigit()]
        if len(nums) == len(rng):
            if v in ("+1", "-1"):
                try:
                    n = int(float(current)) + int(v)
                except (TypeError, ValueError):
                    return None
                return str(min(max(n, min(nums)), max(nums)))
            return v if re.fullmatch(r"\d+", v) and int(v) in nums else None
        return next((str(x) for x in rng if str(x).lower() == v), None)
    return None


def plan(reg, parsed, phrase_words, state=lambda did, code: None, confirm_all_off=True):
    """Validate a parsed answer against the registry.
    -> {"actions": [(device, code, value)], "ask": [reasons], "dropped": [text]}; ask is not empty when the house must get a
    "yes" first: the model named something the phrase did not (a guess), a risky device, or a mass switch-off."""
    out = {"actions": [], "ask": [], "dropped": []}
    if parsed["action"] == "ask":                              # a question never acts and needs no question back
        out["query"] = parsed["query"]
        return out
    if parsed["action"] == "all_off":
        for did, d in reg.dev.items():
            if did in parsed["except"] or reg.risky(did):
                continue
            for code, c in d["controls"].items():
                if c["kind"] == "switch" and (code.startswith("state") or code == "power"):
                    out["actions"].append((did, code, "off"))
        for did in parsed["except"]:
            if not reg.named_in(did, phrase_words):
                out["ask"].append("guess:" + did)
        if confirm_all_off:
            out["ask"].append("all_off")
        if not out["actions"]:
            out["dropped"].append("all_off: nothing to switch off")
        return out
    for it in parsed["intents"]:
        did, code = it["device"], it["code"]
        c = reg.dev[did]["controls"].get(code)
        cur = state(did, code) if c and str(it["value"]) in ("+1", "-1") else None      # the live value only for a relative change
        val = check_value(c, it["value"], cur) if c else None
        if val is None:
            out["dropped"].append("%s %s=%s" % (did, code, it["value"]))
            continue
        out["actions"].append((did, code, val))
        if reg.risky(did):
            out["ask"].append("risky:" + did)
        elif not reg.named_in(did, phrase_words):
            out["ask"].append("guess:" + did)
    if out["dropped"]:
        out["actions"], out["ask"] = [], []         # one bad part spoils the line: the answer is not trusted
    return out


def ask_text(reg, p, parsed):
    """The question for the user in plain Russian."""
    if parsed["action"] == "all_off":
        keep = ", ".join(reg.say(d, case="gen") for d in parsed["except"])
        return "\u0412\u044b\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0432\u0441\u0451, \u043a\u0440\u043e\u043c\u0435 %s?" % keep if keep else "\u0412\u044b\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0432\u0441\u0451?"  # i18n
    parts = []
    for d, code, v in p["actions"]:
        c, name = reg.dev[d]["controls"][code], reg.say(d, code)
        if c["kind"] == "switch":
            lock = code in RISKY_CODES
            verb = ("\u0417\u0430\u043f\u0435\u0440\u0435\u0442\u044c" if v == "on" else "\u041e\u0442\u043a\u0440\u044b\u0442\u044c") if lock else ("\u0412\u043a\u043b\u044e\u0447\u0438\u0442\u044c" if v == "on" else "\u0412\u044b\u043a\u043b\u044e\u0447\u0438\u0442\u044c")  # i18n
            plain = code in ("state", "power", "locked") or (d + "/" + code) in reg.spoken      # a spoken channel name says it all
            parts.append("%s %s" % (verb, name) + ("" if plain else " (%s)" % (c.get("label") or code)))
        else:
            parts.append("%s: %s %s" % (name, c.get("label") or code, v))
    return "%s?" % ", ".join(parts)


def confirmation(text):
    """True for yes, False for no, None for anything else (which counts as no for the pending command)."""
    w = re.findall(r"[\u0430-\u044f\u0451]+", text.lower())  # i18n
    if any(x in CONFIRM_YES for x in w) and not any(x in CONFIRM_NO for x in w):
        return True
    return False if any(x in CONFIRM_NO for x in w) else None


WARM_TIMEOUT = 120.0              # a cold 14B model needs this long to load; a command keeps its short timeout


class Ollama:
    def __init__(self, url, model, threads=4, timeout=5.0, extra=None, keep_alive=-1):
        self.url, self.model, self.threads, self.timeout = url.rstrip("/"), model, threads, timeout
        self.extra, self.keep_alive = dict(extra or {}), keep_alive               # extra: request keys of this server ({"think": False})

    def ask(self, system, phrase, timeout=None):
        """The raw one-line answer; the system prompt stays byte-identical between calls so the cache is reused."""
        body = {"model": self.model, "stream": False, "keep_alive": self.keep_alive,
                "options": {"temperature": 0, "num_ctx": 4096, "num_thread": self.threads, "num_predict": 40, "seed": 1},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": phrase}], **self.extra}
        r = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=timeout or self.timeout) as resp:
            return (json.load(resp).get("message") or {}).get("content", "").strip()

    def chat_stream(self, messages, timeout=60.0, think=False):
        """The chat answer as it is produced (pieces of text). Same model, same context size (a different one would reload it);
        the system prompt differs, so the next command re-reads its own prompt once. think: the model reasons first (its
        thinking comes apart from the answer and is dropped; it needs room for it)."""
        body = {"model": self.model, "stream": True, "keep_alive": self.keep_alive,
                "options": {"temperature": 0.7, "num_ctx": 4096, "num_thread": self.threads, "num_predict": 2048 if think else 300},
                "messages": [{"role": "system", "content": CHAT_SYSTEM}] + messages[-6:], **self.extra}
        if think:
            body["think"] = True
        r = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            for line in resp:
                ev = json.loads(line)
                piece = (ev.get("message") or {}).get("content") or ""
                if piece:
                    yield piece
                if ev.get("done"):
                    return

    def warm_up(self, system):
        """One dummy phrase at service start: loads the model and caches the prompt (cold ~20 s, warm ~1.4 s)."""
        t0 = time.time()
        try:
            self.ask(system, "\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u0432\u0440\u0435\u043c\u0435\u043d\u0438", timeout=90)  # i18n
            return time.time() - t0
        except Exception:
            return None


class Brains:
    """Who answers the model's questions: the Dell's small model ALWAYS works; the big one (on another PC) is used only while its health
    says "available". The health is read by a background thread every poll_s (30 s), never on the command path. A cold big model is
    warmed in the background as soon as the health says available; a command or a chat goes to it only when it is loaded now, else to the
    small one at once. A failed or slow big answer is retried on the small one in the same call.
    `last` is the brain of the latest answer ("14b" | "4b"), for the debug log only."""

    CHAT_BIG_TIMEOUT = 60.0          # the big one thinks before it answers; a stuck one costs the chat this much, not more

    def __init__(self, small, big=None, health=None, poll_s=30.0, dsl_timeout=2.0, clock=time.time, warm=None, model=None, system=""):
        self.small, self.big, self.health, self.poll_s, self.dsl_timeout, self.clock = small, big, health, poll_s, dsl_timeout, clock
        self.model = model or getattr(big, "model", None)
        self.state, self.loaded, self.polled, self.last = "off", False, 0.0, "4b"
        self.system = system                                 # the latest prompt of the commands: what a background warm-up caches
        self._warm_fn, self._warming, self._warm_lock = warm, False, threading.Lock()
        self._stop = threading.Event()

    def refresh(self):
        """One health read (never raises, only the poller and the tests call it); an available but cold big model is warmed in the background."""
        if self.big is None or self.health is None:
            return
        self.polled = self.clock()
        try:
            h = self.health()
            loaded = h.get("loaded")                         # the health lists the models in memory
            self.state = str(h.get("state") or "off")
            self.loaded = (self.model in loaded) if isinstance(loaded, list) else bool(loaded)
        except Exception:                                    # noqa: BLE001  unreachable = off
            self.state, self.loaded = "off", False
        if self.state == "available" and not self.loaded:
            self._warm(self.system)

    def start(self):
        """The poller: reads the health now and then every poll_s, until stop()."""
        if self.big is None or self.health is None:
            return None

        def run():
            while True:
                self.refresh()
                if self._stop.wait(self.poll_s):
                    return
        th = threading.Thread(target=run, daemon=True, name="big-health")
        th.start()
        return th

    def stop(self):
        self._stop.set()

    def _fail(self):
        self.state = "off"                                   # until the next poll says otherwise

    def _warm(self, system):
        if self._warm_fn is None:
            return
        with self._warm_lock:                                # the poller and a command may both ask: one warm-up only
            if self._warming:
                return
            self._warming = True

        def run():
            try:
                self._warm_fn(system)
            except Exception:                                # noqa: BLE001  the next poll asks again
                pass
            finally:
                self._warming = False
        threading.Thread(target=run, daemon=True, name="warm-14b").start()

    def ask(self, system, phrase, timeout=None):
        """The one-line command answer -> (line, brain)."""
        self.system = system
        if self.big is not None and self.state == "available":
            if not self.loaded:                              # cold: the command must not wait for the load
                self._warm(system)
            else:
                try:
                    line = self.big.ask(system, phrase, timeout=self.dsl_timeout)
                    self.last = "14b"
                    return line, "14b"
                except Exception:                            # noqa: BLE001  slow or broken: the small one answers this very command
                    self._fail()
        line = self.small.ask(system, phrase, timeout=timeout)
        self.last = "4b"
        return line, "4b"

    def chat(self, messages):
        """The whole chat answer as text -> (text, brain); the big one only when it is loaded now, else the small one at once."""
        if self.big is not None and self.state == "available" and self.loaded:
            try:
                text = "".join(self.big.chat_stream(messages, timeout=self.CHAT_BIG_TIMEOUT, think=True))
                self.last = "14b"
                return strip_think(text), "14b"
            except Exception:                                # noqa: BLE001
                self._fail()
        text = "".join(self.small.chat_stream(messages))      # the small one has no thinking mode (an instruct model)
        self.last = "4b"
        return strip_think(text), "4b"


def learn(store, phrase, actions):
    """Remember a confirmed guess: this exact phrase means these actions. Only plain switch/enum actions; never a risky device."""
    store.setdefault("learned", {})[phrase] = [list(a) for a in actions]


def learned(store, reg, phrase):
    """The remembered actions of a phrase, validated against the registry as of now (a device or value that went away = forgotten)."""
    out = []
    for did, code, value in (store.get("learned") or {}).get(phrase, []):
        c = reg.dev.get(did, {}).get("controls", {}).get(code)
        v = check_value(c, value) if c and not reg.risky(did) else None
        if v is None:
            return None
        out.append((did, code, v))
    return out or None
