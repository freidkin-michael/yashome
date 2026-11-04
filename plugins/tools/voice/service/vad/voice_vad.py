#!/usr/bin/env python3
# The voice service of the house: webrtcvad segments each utterance from the satellites' PCM streams (peak-normalised), Vosk recognizes it
# (the grammar of the command map first, checked by a free pass), the house acts on it (phrase map, the state questions, the LLM fallback with
# the house's checks) and tells the satellite how it went (voice/heard). Design: docs/voice.md and the README of the voice plug-in (this folder is its service/vad).
import socket
import math
import os
import time
import wave
import re
import collections
import select
import secrets
import threading
try:                                                  # a test host may lack the audio and HTTP libraries; the service image has them
    import numpy as np
    import requests
except ImportError:                                   # pragma: no cover
    np = requests = None

# --- intent -> home MQTT --------------------------------------------------
# The zone map + verb lists are DATA, editable live from the dashboard
# (the house's voice_commands.json). We hot-reload the JSON by
# mtime so edits apply with NO container restart. The hardcoded values below are
# only the fallback used if the JSON is missing or invalid.
#   zone entry: (backend, target, on_off_code, bright_code_or_None)
#   backend "z2m":  publish zigbee2mqtt/<target>/set/<code>  ON|OFF
#   backend "mqtt": publish home/cmd/<target>/<code>         on|off  (+ bright for %)
import json as _json

_DEFAULT_DEVICES = {}                 # no built-in zones: the house's voice_commands.json has them
_DEFAULT_ON  = ["\u0432\u043a\u043b\u044e\u0447", "\u0432\u0440\u0443\u0431", "\u0437\u0430\u0436\u0433", "\u0437\u0430\u043f\u0443\u0441"]  # i18n
_DEFAULT_OFF = ["\u0432\u044b\u043a\u043b\u044e\u0447", "\u0432\u044b\u0440\u0443\u0431", "\u043f\u043e\u0433\u0430\u0441", "\u043e\u0442\u043a\u043b\u044e\u0447", "\u0442\u0443\u0448\u0438", "\u043f\u043e\u0442\u0443\u0448\u0438"]

CMD_PATH = os.environ.get("VA_COMMANDS", "/house/voice_commands.json")
DEVICES = dict(_DEFAULT_DEVICES)
ON_WORDS = list(_DEFAULT_ON)
OFF_WORDS = list(_DEFAULT_OFF)
_cmd_mtime = 0.0


# Appliances with complex commands (air conditioners: modes, temperature) come from the "appliances" section
# of voice_commands.json; without it there are none.
APPLIANCES_FALLBACK = []
APPLIANCES = APPLIANCES_FALLBACK


_tl = threading.local()


def _note_done(did, code, value):
    """Remember one executed control with the device's state before it (for the spoken result of a voice command)."""
    if getattr(_tl, "done", None) is not None:
        _tl.done.append({"did": did, "code": code, "value": value, "before": _state(did, code)})


def act_print(msg, **kw):
    """print() that also keeps the [ACT] line of the current thread (the text box shows what a command did)."""
    print(msg, **kw)
    if getattr(_tl, "lines", None) is not None:
        _tl.lines.append(msg)


def load_commands(force=False):
    """(Re)load DEVICES/ON_WORDS/OFF_WORDS from voice_commands.json when it changes.
    JSON: {"on_words":[...],"off_words":[...],
           "zones":[{"words":[...],"backend":"z2m|mqtt","target":"...","code":"state","bright":null}],
           "appliances":[{"match":[...],"rooms":[{"words":[...],"target":"ac"}],
                          "intents":[{"kind":"set|number|delta|power","code":"mode","value":"heat",
                                      "words":[...],"delta":1}]}]}
    Any error -> keep the last-good tables (built-in defaults on first load)."""
    global DEVICES, ON_WORDS, OFF_WORDS, APPLIANCES, _cmd_mtime
    try:
        m = os.path.getmtime(CMD_PATH)
    except OSError:
        return  # no file -> keep whatever we have
    if not force and m == _cmd_mtime:
        return
    try:
        with open(CMD_PATH, encoding="utf-8") as f:
            cfg = _json.load(f)
        dev = {}
        for z in cfg.get("zones", []):
            backend = (z.get("backend") or "z2m").strip()
            target = z["target"]
            code = (z.get("code") or "state").strip()
            bright = z.get("bright") or None
            for w in z.get("words", []):
                w = str(w).strip().lower()
                if w:
                    dev[w] = (backend, target, code, bright)
        on = [str(x).strip().lower() for x in cfg.get("on_words", []) if str(x).strip()]
        off = [str(x).strip().lower() for x in cfg.get("off_words", []) if str(x).strip()]
        if not dev or not on or not off:
            raise ValueError("empty zones/verbs")
        # appliances with complex commands: an old file may have no such section, then the fallback stays
        aps = cfg.get("appliances")
        aps = [a for a in aps if isinstance(a, dict) and a.get("match")] \
            if isinstance(aps, list) else []
        DEVICES, ON_WORDS, OFF_WORDS = dev, on, off
        APPLIANCES = aps or APPLIANCES_FALLBACK
        _cmd_mtime = m
        print("[cfg] loaded %d zone-words, %d on / %d off verbs, %d appliances from %s"
              % (len(dev), len(on), len(off), len(APPLIANCES), CMD_PATH), flush=True)
    except Exception as e:
        print("[cfg] load failed (%s) -> keeping current tables" % e, flush=True)



# spoken-percent parsing: digits first, then a small RU word map (round values)
RU_NUM = {
    "\u043d\u043e\u043b\u044c": 0, "\u043d\u043e\u043b": 0,  # i18n
    "\u0434\u0435\u0441\u044f\u0442": 10, "\u0434\u0432\u0430\u0434\u0446\u0430\u0442": 20, "\u0442\u0440\u0438\u0434\u0446\u0430\u0442": 30, "\u0441\u043e\u0440\u043e\u043a": 40, "\u043f\u044f\u0442\u044c\u0434\u0435\u0441\u044f\u0442": 50,
    "\u0448\u0435\u0441\u0442\u044c\u0434\u0435\u0441\u044f\u0442": 60, "\u0441\u0435\u043c\u044c\u0434\u0435\u0441\u044f\u0442": 70, "\u0432\u043e\u0441\u0435\u043c\u044c\u0434\u0435\u0441\u044f\u0442": 80, "\u0434\u0435\u0432\u044f\u043d\u043e\u0441\u0442": 90, "\u0441\u0442\u043e": 100,  # i18n
    "\u043f\u043e\u043b\u043e\u0432\u0438\u043d": 50, "\u043c\u0430\u043a\u0441\u0438\u043c": 100, "\u043f\u043e\u043b\u043d": 100, "\u043c\u0438\u043d\u0438\u043c\u0443\u043c": 1, "\u043c\u0438\u043d\u0438\u043c\u0430\u043b": 1,
}

# extra mistranscriptions of the wake word to accept verbatim (comma-list env): added to voice_rules.WAKE_VARIANTS, the seed of the page
WAKE_VARIANTS = [x.strip().lower() for x in os.environ.get("VA_WAKE_VARIANTS", "").split(",") if x.strip()]


def _lev(a, b):
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# Generic wake matcher: exact / small edit-distance / leading-stem prefix, all
# relative to the configured WAKE word (WAKE/WAKE_MAXDIST/WAKE_PREFIX below).
# Firing wake on a stray word is harmless: without a valid verb+zone nothing acts.
def find_wake(words):
    import voice_rules
    for i, w in enumerate(words[:3]):
        if _lev(w, WAKE) <= WAKE_MAXDIST or (WAKE_PREFIX and w.startswith(WAKE_PREFIX)) or voice_rules.wake_token(w):
            return i
    return -1


def parse_percent(cmd):
    # only meaningful when the phrase mentions percent / brightness
    if not re.search(r"\u043f\u0440\u043e\u0446\u0435\u043d\u0442|\u044f\u0440\u043a|\u044f\u0440\u043a\u043e\u0441\u0442", cmd):  # i18n
        return None
    raw = None
    m = re.search(r"(\d+)", cmd)
    if m:
        raw = int(m.group(1))
    else:
        for stem, val in RU_NUM.items():
            if stem in cmd:
                raw = val
                break
    if raw is None:
        return None
    return max(1, min(100, raw))   # led strip floor = 1% (0 would be "off")


# MQTT publish via paho (broker reachable as MQTT_HOST on the compose network).
# Was `docker exec mosquitto mosquitto_pub` when host-side; a real client is the
# right call now that this runs inside the compose stack.
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
_mqc = None                                           # made by _mqtt_start() when the service starts, not on import


def _on_ask_failed(client, userdata, msg):
    """voice/ask_failed {ip}: the satellite did not play our question: it must not wait for an answer nobody heard."""
    try:
        ip = _json.loads(msg.payload).get("ip", "")
    except Exception:
        return
    if _pending.pop(ip, None) is not None:
        print("[LLM] question for %s cancelled: the satellite did not play it" % ip, flush=True)


def _mqtt_start():
    global _mqc
    import paho.mqtt.client as mqtt
    _mqc = mqtt.Client()
    if os.environ.get("MQTT_USER"):
        _mqc.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASSWORD", ""))
    try:
        _mqc.connect(MQTT_HOST, MQTT_PORT, 60)
    except Exception as e:
        print("[mqtt] connect deferred (%s), retrying in background" % e, flush=True)
        try:
            _mqc.connect_async(MQTT_HOST, MQTT_PORT, 60)
        except Exception:
            pass
    _mqc.on_message = _on_ask_failed
    _mqc.on_connect = lambda c, u, f, rc: c.subscribe("voice/ask_failed")
    _mqc.loop_start()


def pub(topic, val):
    """Publish a command and say so when it did NOT go out. paho does not queue a
    QoS-0 publish while the broker link is down, it just returns rc != 0 -- which
    this used to discard, so act() logged commands the house never received."""
    if _mqc is None:
        print("[mqtt] DROPPED %s=%s (no client yet)" % (topic, val), flush=True)
        return False
    info = _mqc.publish(topic, str(val), qos=0, retain=False)
    rc = getattr(info, "rc", 0)
    if rc != 0:
        print("[mqtt] DROPPED %s=%s (rc=%s, broker link down)" % (topic, val, rc),
              flush=True)
        return False
    return True


# --- AC (Samsung via BroadLink RM Pro, driven through the dashboard HTTP API) --
HOME_URL = os.environ.get("VA_HOME_URL", "http://home:8765").rstrip("/")     # the core on the compose network
# the dashboard API is bearer-token gated; token comes from .env via compose
_AUTH = {"Authorization": "Bearer " + os.environ.get("DASHBOARD_TOKEN", "")}
AC_API = HOME_URL + "/api/devices"   # per-room base built below
AC_TMIN, AC_TMAX = 16, 30




def _ap_targets(ap, cmd):
    """\u041a\u043e\u043c\u043d\u0430\u0442\u044b, \u043d\u0430\u0437\u0432\u0430\u043d\u043d\u044b\u0435 \u0432 \u043a\u043e\u043c\u0430\u043d\u0434\u0435; \u0435\u0441\u043b\u0438 \u043d\u0438 \u043e\u0434\u043d\u0430 \u2014 \u043f\u0440\u0438\u0431\u043e\u0440 \u043f\u043e \u0443\u043c\u043e\u043b\u0447\u0430\u043d\u0438\u044e."""  # i18n
    t = []
    for room in ap.get("rooms") or []:
        if any(re.search(w, cmd) for w in room.get("words") or []):
            if room.get("target") and room["target"] not in t:
                t.append(room["target"])
    if t:
        return t
    dflt = ap.get("default_target")
    return [dflt] if dflt else []


def _ac_get_temp(base):
    try:
        r = requests.get(base + "/status", headers=_AUTH, timeout=4)
        return int(r.json().get("values", {}).get("temp") or 20)
    except Exception:
        return 20


def _ac_ctl(base, code, val):
    dev = base.rsplit("/", 1)[-1]
    _note_done(dev, code, val)
    try:
        requests.post(base + "/control", json={"code": code, "value": val}, headers=_AUTH, timeout=8)
        act_print("[ACT] AC %s %s=%s" % (dev, code, val), flush=True)
        return True
    except Exception as e:
        act_print("[ACT err] AC %s %s=%s: %s" % (dev, code, val, e), flush=True)
        return False


def _ac_clamp(t):
    return max(AC_TMIN, min(AC_TMAX, t))


def try_ac(cmd):
    """Appliances with complex commands (air conditioners), by the rules of voice_commands.json.

    True when the command was about such an appliance: the plain zones then leave it alone (else "turn off the AC in the
    bedroom" would also turn off the light there). The intents go in the order of the file, mode and temperature on
    purpose BEFORE power: any change implies turning on."""
    for ap in APPLIANCES:
        if not any(re.search(w, cmd) for w in ap.get("match") or []):
            continue
        targets = _ap_targets(ap, cmd)
        if not targets:
            act_print("[ACT] %s: \u043d\u0435 \u043f\u043e\u043d\u044f\u043b \u043a\u043e\u043c\u043d\u0430\u0442\u0443: %s" % (ap.get("id"), cmd), flush=True)  # i18n
            return "unclear"
        bases = [AC_API + "/" + dev for dev in targets]
        lo = int(ap.get("min", AC_TMIN))
        hi = int(ap.get("max", AC_TMAX))
        clamp = lambda v: max(lo, min(hi, v))          # noqa: E731

        for it in ap.get("intents") or []:
            kind = (it.get("kind") or "set").strip()
            words = it.get("words") or []
            hit = any(re.search(w, cmd) for w in words) if words else False
            code = it.get("code") or "power"
            if kind == "set" and hit:
                for b in bases:
                    _ac_ctl(b, code, it.get("value"))
                return True
            if kind == "number" and hit:
                m = re.search(r"(\d{2})", cmd)
                if m:
                    val = clamp(int(m.group(1)))
                    for b in bases:
                        _ac_ctl(b, code, val)
                    return True
                continue                # no number named: the next intents decide
            if kind == "delta" and hit:
                d = int(it.get("delta") or 0)
                for b in bases:
                    _ac_ctl(b, code, clamp(_ac_get_temp(b) + d))
                return True
            if kind == "power":
                if any(w in cmd for w in ON_WORDS):
                    for b in bases:
                        _ac_ctl(b, code, True)
                    return True
                if any(w in cmd for w in OFF_WORDS):
                    for b in bases:
                        _ac_ctl(b, code, False)
                    return True
        act_print("[ACT] %s: \u043d\u0435 \u043f\u043e\u043d\u044f\u043b, \u0447\u0442\u043e \u0441\u0434\u0435\u043b\u0430\u0442\u044c: %s" % (ap.get("id"), cmd), flush=True)  # i18n
        return "unclear"
    return False


# --- TV (Settings -> Voice "tv_device", driven through the same dashboard HTTP API as the AC) ------
# the recognizer often drops the start of the Russian word for a TV and hears its tail, so the
# keyword match is deliberately broad.
TV_DEVICE = ""                                        # the TV the TV commands drive; "" = no TV commands


def _tv_base():
    return "%s/%s" % (AC_API, TV_DEVICE)


def _tv_get_volume():
    try:
        r = requests.get(_tv_base() + "/status", headers=_AUTH, timeout=4)
        return int(r.json().get("values", {}).get("volume") or 10)
    except Exception:
        return 10


def _tv_ctl(code, val):
    _note_done(TV_DEVICE, code, val)
    try:
        requests.post(_tv_base() + "/control", json={"code": code, "value": val}, headers=_AUTH, timeout=10)
        act_print("[ACT] TV %s=%s" % (code, val), flush=True)
        return True
    except Exception as e:
        act_print("[ACT err] TV %s=%s: %s" % (code, val, e), flush=True)
        return False


def try_tv(cmd):
    """Handle TV commands (the TV of Settings -> Voice). Return True if this was a TV cmd.
    Order: mute/volume BEFORE power (a volume/mute change must not power-toggle)."""
    if not TV_DEVICE or not re.search(r"\u0442\u0435\u043b\u0435\u0432\u0438\u0437|\u043b\u0435\u0432\u0438\u0437\u043e\u0440|\u0442\u0435\u043b\u0435\u0432\u0438\u0437\u0435\u0440|\u0432\u0438\u0437\u043e\u0440|\u0442\u0435\u043b\u0435\u043a|\u0442\u0435\u043b\u0438\u043a|\b\u0442\u0432\b|\u044f\u0449\u0438\u043a", cmd):  # i18n
        return False
    # mute / unmute: "sound" + off/on, or explicit mute words
    if re.search(r"\u0437\u0430\u0433\u043b\u0443\u0448|\u043c\u044c\u044e\u0442|\u0431\u0435\u0437\s*\u0437\u0432\u0443\u043a", cmd) or (re.search(r"\u0437\u0432\u0443\u043a", cmd) and any(w in cmd for w in OFF_WORDS)):  # i18n
        _tv_ctl("mute", True); return True
    if re.search(r"\u0440\u0430\u0437\u043c\u044c\u044e\u0442|\u0432\u0435\u0440\u043d\u0438\s*\u0437\u0432\u0443\u043a", cmd) or (re.search(r"\u0437\u0432\u0443\u043a", cmd) and any(w in cmd for w in ON_WORDS)):  # i18n
        _tv_ctl("mute", False); return True
    # relative volume +/- 3
    if re.search(r"\u0433\u0440\u043e\u043c\u0447\u0435|\u043f\u043e\u0433\u0440\u043e\u043c\u0447|\u043f\u0440\u0438\u0431\u0430\u0432|\u043f\u043e\u0432\u044b\u0441", cmd):  # i18n
        _tv_ctl("volume", _tv_get_volume() + 3); return True
    if re.search(r"\u0442\u0438\u0448\u0435|\u043f\u043e\u0442\u0438\u0448\u0435|\u0443\u0431\u0430\u0432|\u043f\u043e\u043d\u0438\u0437", cmd):  # i18n
        _tv_ctl("volume", max(0, _tv_get_volume() - 3)); return True
    # power
    if any(w in cmd for w in ON_WORDS):
        _tv_ctl("power", True); return True
    if any(w in cmd for w in OFF_WORDS):
        _tv_ctl("power", False); return True
    act_print("[ACT] TV no sub-intent: %s" % cmd, flush=True)
    return True


def act(cmd):
    """True when the command did something (an appliance, a TV, a zone), False when nothing matched."""
    load_commands()   # hot-reload zone/verb edits from the dashboard (mtime-gated, cheap)
    ac = try_ac(cmd)
    if ac == "unclear":              # an appliance was named, nothing understood: not handled, and the zones must not act on it
        return False
    if ac:
        return True
    if try_tv(cmd):
        return True
    # a command may name several zones ("in the bedroom and the living room") -> actuate ALL
    matched = [(k, v) for k, v in DEVICES.items() if k in cmd]
    if not matched:
        act_print("[ACT] no zone match: %s" % cmd, flush=True)
        return False
    state = "ON" if any(w in cmd for w in ON_WORDS) else ("OFF" if any(w in cmd for w in OFF_WORDS) else None)
    fired = 0
    for k, (backend, name, code, bright) in matched:
        pct = parse_percent(cmd) if bright else None
        try:
            if pct is not None:
                _note_done(name, bright, pct)
                if backend == "mqtt":
                    pub("home/cmd/%s/%s" % (name, bright), str(pct))
                else:
                    pub("zigbee2mqtt/%s/set/%s" % (name, bright), str(pct))
                act_print("[ACT] %s -> %d%%" % (name, pct), flush=True)
                fired += 1
                continue
            if not state:
                continue
            _note_done(name, code, state)
            if backend == "mqtt":
                pub("home/cmd/%s/%s" % (name, code), "on" if state == "ON" else "off")
            else:
                pub("zigbee2mqtt/%s/set/%s" % (name, code), state)
            act_print("[ACT] %s -> %s" % (name, state), flush=True)
            fired += 1
        except Exception as e:
            act_print("[ACT err] %s: %s" % (name, e), flush=True)
    if not fired:
        act_print("[ACT] no verb/percent (zones=%s): %s" % ([m[1][1] for m in matched], cmd), flush=True)
    return fired > 0


PORT     = int(os.environ.get("PCM_PORT", "8091"))
VAD_GAIN = float(os.environ.get("VA_VAD_GAIN", "12"))   # gain for VAD decision only
NORM_PEAK = float(os.environ.get("VA_NORM_PEAK", "29000"))  # normalise utterance to this peak
RATE     = 16000
FRAME    = 480                  # 30 ms
FBYTES   = FRAME * 2
WAKE       = os.environ.get("VA_WAKE", "\u0434\u0436\u0430\u0440\u0432\u0438\u0441").lower()  # i18n
WAKE_MAXDIST = int(os.environ.get("VA_WAKE_MAXDIST", "3"))          # Levenshtein tolerance
WAKE_PREFIX  = os.environ.get("VA_WAKE_PREFIX", "\u0434\u0436\u0430\u0440\u0432").lower()     # i18n: accepted leading stem ("" disables)
# Satellites that gate the wake word ON-DEVICE (e.g. the Android app via openWakeWord):
# for these peers, don't re-require the wake word in the transcript - act on any
# utterance. Set to "*" to trust ALL :8091 peers (safe now that no dumb continuous mic
# streams here).
# Otherwise a comma-list of specific IPs; other peers still need the wake word.
NOWAKE_IPS = set(x.strip() for x in os.environ.get("VA_NOWAKE_IPS", "").split(",") if x.strip())
WAKE_ON_DEVICE = "*" in NOWAKE_IPS                    # Settings -> Voice "wake_on_device": every satellite checks the wake word itself
AGGR     = int(os.environ.get("VA_AGGR", "2"))
PAD_MS   = int(os.environ.get("VA_PAD_MS", "500"))   # pre-roll before VAD trigger; bigger = keeps the wake-word onset
MAX_MS   = int(os.environ.get("VA_MAX_MS", "6000"))
PREROLL_BYTES = int(os.environ.get("VA_PREROLL_MS", "1500")) * 32         # 16 kHz s16le: what a node sends first, before its stream reaches the live audio
NEEDWAKE_IPS = set(x.strip() for x in os.environ.get("VA_NEEDWAKE_IPS", "").split(",") if x.strip())   # trusted peers whose transcript must still hold a wake phrase (a tablet that wakes by mistake)
# Reap a connection that has delivered no data for this long. The ESP satellite
# streams continuously, so a live mic connection is never silent this long; a
# half-open one (ESP WiFi-drop / reboot / reconnect leaves the old TCP ESTAB
# with no FIN/RST) goes silent forever and would otherwise accumulate until we
# hit the fd / select FD_SETSIZE limit and crash. See socket loop below.
IDLE_TO  = int(os.environ.get("VA_IDLE_TIMEOUT", "30"))


def wake_checked_on_device(ip):
    """True when the satellite of ip checked the wake word itself: its phrase needs none (a ticked one still does)."""
    return (WAKE_ON_DEVICE or "*" in NOWAKE_IPS or ip in NOWAKE_IPS) and ip not in NEEDWAKE_IPS

vad = None                                            # webrtcvad.Vad, made by main()
pad_frames = max(1, PAD_MS // 30)


def vad_frame(raw):
    a = np.frombuffer(raw, dtype=np.int16)
    g = np.clip(a.astype(np.int32) * VAD_GAIN, -32768, 32767).astype(np.int16)
    return g.tobytes()


def normalize(raw):
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    if peak > 0:
        a = a * (NORM_PEAK / peak)
    return np.clip(a, -32768, 32767).astype(np.int16)


def norm(t):
    return re.sub(r"[^\w\u0430-\u044f\u0451 ]", "", t.lower()).strip()  # i18n


# Debug captures are OFF by default: every VAD segment (noise included) used to
# be written here with no cap or retention, and 13392 files / 1.4 GB had piled up
# on carbon. Set VA_CAPTURES=1 to record while debugging; KEEP bounds the dir.
CAPTURES      = os.environ.get("VA_CAPTURES", "0") not in ("0", "", "no", "false")
CAPTURES_DIR  = os.environ.get("VA_CAPTURES_DIR", "/house/captures")
CAPTURES_KEEP = int(os.environ.get("VA_CAPTURES_KEEP", "200"))


def _save_capture(pcm):
    """Write the utterance for offline debugging and prune the oldest ones.
    Returns a short tag for the log line ('-' when capturing is off)."""
    if not CAPTURES:
        return "-"
    try:
        os.makedirs(CAPTURES_DIR, exist_ok=True)
        p = os.path.join(CAPTURES_DIR, "utt-%d.wav" % int(time.time()))
        wf = wave.open(p, "wb")
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(RATE)
        wf.writeframes(pcm.tobytes()); wf.close()
        names = sorted(n for n in os.listdir(CAPTURES_DIR) if n.endswith(".wav"))
        for old in names[:max(0, len(names) - CAPTURES_KEEP)]:
            try:
                os.remove(os.path.join(CAPTURES_DIR, old))
            except OSError:
                pass
        return p
    except Exception as e:
        print("[capture] failed: %s" % e, flush=True)
        return "?"


_rooms = {"at": 0.0, "by_ip": {}}
_gate = None


def _room_of(ip):
    """The room of the satellite at this address (the voice plug-in's registry, read at most every 30 s); '' when unknown."""
    if time.time() - _rooms["at"] > 30:
        _rooms["at"] = time.time()
        try:
            r = requests.get(AC_API.rsplit("/", 1)[0] + "/voice/satellites", headers=_AUTH, timeout=3).json()
            _rooms["by_ip"] = {s["peer"]: s.get("room") or "" for s in r if s.get("peer")}
        except Exception:
            pass
    return _rooms["by_ip"].get(ip, "")


def _admit(ip):
    """False when another satellite of this room started the same phrase a moment ago (logged); its stream is then dropped."""
    global _gate
    import voice_room
    if _gate is None:
        _gate = voice_room.RoomGate(float(os.environ.get("VA_ROOM_WINDOW", "1.5")))
    room = _room_of(ip)
    ok = _gate.admit(room, ip, time.time())
    if not ok:
        print("[vad] stream of %s dropped: another satellite of room %s was first" % (ip, room), flush=True)
    return ok


def new_state():
    return {"buf": bytearray(),
            "ring": collections.deque(maxlen=pad_frames),
            "triggered": False,
            "voiced": bytearray(),
            "rx": 0,                  # bytes of the stream seen so far: the first PREROLL_MS of it are the node's preroll (the wake word)
            "cmd_ms": 0,              # speech after the preroll: the command itself
            "ip": ""}


# Recognizer: Vosk, the grammar of the command map first (rebuilt when voice_commands.json changes), then a free pass.
VOSK_MINCONF = float(os.environ.get("VA_VOSK_MINCONF", "0.5"))
VOSK_GUARD = os.environ.get("VA_VOSK_GUARD", "1") not in ("0", "", "no", "false")   # check the grammar pass against the free pass
VOSK_AGREE = float(os.environ.get("VA_VOSK_AGREE", "0.7"))   # share of grammar words the free pass confirms
_vosk = None
_grammar_mtime = None


def _vosk_grammar():
    global _vosk, _grammar_mtime
    if _vosk is None:
        import voice_vosk
        _vosk = voice_vosk.Vosk()
    load_commands()
    if _grammar_mtime != _cmd_mtime:
        pats = list(DEVICES) + ON_WORDS + OFF_WORDS
        for ap in APPLIANCES:
            pats += list(ap.get("match") or [])
            for room in ap.get("rooms") or []:
                pats += list(room.get("words") or [])
            for it in ap.get("intents") or []:
                pats += list(it.get("words") or [])
        n = _vosk.build_grammar(pats)
        _grammar_mtime = _cmd_mtime
        print("[vosk] grammar: %d words" % n, flush=True)
    return _vosk


def _strip_wake(words):
    i = find_wake(words)
    return words[i + 1:] if i >= 0 else words


# --- LLM fallback (voice_llm.py): a phrase no rule understood; the house validates the answer strictly ---------------
LLM_URL = os.environ.get("VA_LLM_URL", "")            # empty = off
LLM_MODEL = os.environ.get("VA_LLM_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
LLM_TIMEOUT = float(os.environ.get("VA_LLM_TIMEOUT", "5"))
LLM_CONFIRM_ALL_OFF = os.environ.get("VA_LLM_CONFIRM_ALL_OFF", "1") not in ("0", "", "no", "false")
ASK_TTL = float(os.environ.get("VA_ASK_TTL", "12"))   # seconds a question waits for the answer
NOT_UNDERSTOOD = "\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430."  # i18n
_llm_lock = threading.Lock()
_llm = {"ollama": None, "reg": None, "at": 0.0, "brains": None}
BIG_URL = os.environ.get("VA_BIG_URL", "")                 # the big model on another PC (empty = only the Dell's)
BIG_HEALTH = os.environ.get("VA_BIG_HEALTH", "")
BIG_MODEL = os.environ.get("VA_BIG_MODEL", "qwen3:14b")

# --- Settings -> Voice of the dashboard (GET /api/voice/config every 10 s): a value set there wins over the env above; the first read of a
# house that never set them seeds them from what runs now, so the page shows it. The answers are in the house language (Settings -> General).
HOUSE_LANG = (os.environ.get("CLOCK_LANG") or "ru").strip().lower()      # until the first read
LLM_ON = True                                                             # "Smart answers" (the model also needs VA_LLM_URL)
CONFIG_POLL_S = 10.0


def max_wake_dist(word):
    """How many edits a heard word may be off the wake word: a third of a short word would match almost anything (7 letters -> 3)."""
    return max(0, min(3, (len(word) - 1) // 2))


WORD = re.compile(r"[a-z\u0430-\u044f\u0451]+")                                  # i18n: plain Latin or Cyrillic letters: what every regex engine calls a letter
VARIANT = re.compile(r"[a-z\u0430-\u044f\u0451]{3,20}\*|[a-z\u0430-\u044f\u0451]{2,20}")         # a stem needs 3 letters before its *, as the page asks
MODEL = re.compile(r"[A-Za-z0-9._:/-]{0,80}")
URL = re.compile(r"(https?://[A-Za-z0-9._:/\[\]-]{1,200})?")          # as the page asks; "" = off
ID = re.compile(r"[A-Za-z0-9._-]{0,64}")


def _env_config():
    """What the service runs with now, in the page's fields and fitted to the page's bounds (a value the page would refuse would refuse
    the whole seed, and the page would stay locked): the seed of a house that never set them. What was fitted is logged."""
    import voice_rules
    fit = []

    def bound(name, v, lo, hi):
        w = min(max(v, lo), hi) if math.isfinite(v) else hi                    # nan / inf: the page's top
        if w != v:
            fit.append("%s %s -> %s" % (name, v, w))
        return w
    wake = WAKE if WORD.fullmatch(WAKE) and 2 <= len(WAKE) <= 40 else "\u0434\u0436\u0430\u0440\u0432\u0438\u0441"  # i18n
    variants = []
    for v in dict.fromkeys(voice_rules.WAKE_VARIANTS + WAKE_VARIANTS):
        (variants.append(v) if VARIANT.fullmatch(v) else fit.append("variant %r dropped" % v))
    prefix = WAKE_PREFIX if len(WAKE_PREFIX) >= 3 and WORD.fullmatch(WAKE_PREFIX) and len(WAKE_PREFIX) <= 20 else ""
    if wake != WAKE or prefix != WAKE_PREFIX:
        fit.append("wake %r prefix %r -> %r %r" % (WAKE, WAKE_PREFIX, wake, prefix))
    if len(variants) > 40:
        fit.append("variants cut from %d to 40" % len(variants))
    out = {"wake": wake, "wake_maxdist": bound("maxdist", WAKE_MAXDIST, 0, max_wake_dist(wake)), "wake_prefix": prefix, "wake_variants": variants[:40],
           "llm": bool(LLM_URL), "llm_timeout": bound("llm_timeout", LLM_TIMEOUT, 1, 60), "confirm_all_off": LLM_CONFIRM_ALL_OFF,
           "ask_ttl": bound("ask_ttl", ASK_TTL, 3, 120), "need_wake_peers": sorted(NEEDWAKE_IPS), "wake_on_device": WAKE_ON_DEVICE}
    for k, u in (("llm_url", LLM_URL), ("big_url", BIG_URL), ("big_health", BIG_HEALTH)):
        (out.__setitem__(k, u) if URL.fullmatch(u) else fit.append("%s dropped" % k))
    if ID.fullmatch(TV_DEVICE):
        out["tv_device"] = TV_DEVICE
    for k, m in (("llm_model", LLM_MODEL), ("big_model", BIG_MODEL)):
        (out.__setitem__(k, m) if MODEL.fullmatch(m) else fit.append("%s %r dropped" % (k, m)))
    if fit:
        print("[vad] settings seed fitted to the page: %s" % "; ".join(fit), flush=True)
    return out


def apply_config(c):
    """Take the page's values (a field that is absent keeps the current one). A changed model reaches the live clients and is warmed at once;
    smart answers turned off stop the big model's poller (the other PC is left alone), turned on start the models again."""
    global WAKE, WAKE_MAXDIST, WAKE_PREFIX, LLM_ON, LLM_MODEL, BIG_MODEL, LLM_TIMEOUT, LLM_CONFIRM_ALL_OFF, ASK_TTL, NEEDWAKE_IPS, HOUSE_LANG
    global LLM_URL, BIG_URL, BIG_HEALTH, WAKE_ON_DEVICE, TV_DEVICE
    import voice_rules
    was_on, was_model, was_urls = LLM_ON, LLM_MODEL, (LLM_URL, BIG_URL, BIG_HEALTH)
    LLM_URL, BIG_URL, BIG_HEALTH = (str(c.get(k, v) or "") for k, v in (("llm_url", LLM_URL), ("big_url", BIG_URL), ("big_health", BIG_HEALTH)))
    WAKE_ON_DEVICE = bool(c.get("wake_on_device", WAKE_ON_DEVICE))
    TV_DEVICE = str(c.get("tv_device", TV_DEVICE) or "")
    WAKE = str(c.get("wake") or WAKE).lower()
    WAKE_MAXDIST = min(int(c.get("wake_maxdist", WAKE_MAXDIST)), max_wake_dist(WAKE))
    WAKE_PREFIX = str(c.get("wake_prefix", WAKE_PREFIX)).lower()
    if len(WAKE_PREFIX) < 3:                                                   # a 2-letter start would wake on many words: a stored short one counts as none
        WAKE_PREFIX = ""
    if isinstance(c.get("wake_variants"), list):                              # a short stem stored before the 3-letter rule is not used
        voice_rules.WAKE_VARIANTS = [str(v).lower() for v in c["wake_variants"] if VARIANT.fullmatch(str(v).lower())]
    voice_rules.WAKE, voice_rules.WAKE_MAXDIST = WAKE, WAKE_MAXDIST
    LLM_ON = bool(c.get("llm", LLM_ON))
    LLM_MODEL, BIG_MODEL = c.get("llm_model") or LLM_MODEL, c.get("big_model") or BIG_MODEL
    LLM_TIMEOUT, ASK_TTL = float(c.get("llm_timeout", LLM_TIMEOUT)), float(c.get("ask_ttl", ASK_TTL))
    LLM_CONFIRM_ALL_OFF = bool(c.get("confirm_all_off", LLM_CONFIRM_ALL_OFF))
    if c.get("seeded") and isinstance(c.get("need_wake"), list):          # before the seed no satellite carries the mark yet
        NEEDWAKE_IPS = set(map(str, c["need_wake"]))
    HOUSE_LANG = str(c.get("lang") or HOUSE_LANG)
    if (LLM_URL, BIG_URL, BIG_HEALTH) != was_urls:                         # another model host: the clients are made again
        with _brains_lock:
            if _llm["brains"] is not None:
                _llm["brains"].stop()
            _llm["brains"] = _llm["ollama"] = None
        if LLM_URL and LLM_ON:
            threading.Thread(target=_llm_start, daemon=True, name="llm-start").start()
        return
    small, brains = _llm["ollama"], _llm["brains"]
    if small is not None:
        small.model, small.timeout = LLM_MODEL, LLM_TIMEOUT
    if brains is not None and brains.big is not None and brains.model != BIG_MODEL:
        brains.big.model = brains.model = BIG_MODEL
        brains.loaded = False                                                  # its poller warms the new one when the other PC is on
    if not LLM_URL or LLM_ON == was_on and (small is None or LLM_MODEL == was_model):
        return
    if not LLM_ON:
        with _brains_lock:
            if _llm["brains"] is not None:
                _llm["brains"].stop()
            _llm["brains"] = None
    else:                                                                       # on again, or a new small model: loaded now, not on the next phrase
        threading.Thread(target=_llm_start, daemon=True, name="llm-start").start()


def config_tick():
    """One read of the page's values (the seed first when the house has none) -> the config applied."""
    base = AC_API.rsplit("/", 1)[0]
    c = requests.get(base + "/voice/config", headers=_AUTH, timeout=4).json()
    if isinstance(c, dict) and "lang" in c and not c.get("seeded"):
        c = requests.put(base + "/voice/config", params={"seed": 1}, headers=_AUTH, json=_env_config(), timeout=4).json()
    if not (isinstance(c, dict) and "lang" in c):                         # an error (the plug-in is older or off, a refused seed): nothing changes
        raise ValueError("no voice config: %s" % str(c)[:200])
    apply_config(c)
    return c


def _config_loop():
    last, err = None, None
    while True:
        try:
            c = config_tick()
            err = None
            if c != last:
                print("[vad] settings: wake=%r maxdist=%d prefix=%r variants=%d llm=%s model=%s big=%s ask_ttl=%.0f lang=%s need_wake=%s" % (
                    WAKE, WAKE_MAXDIST, WAKE_PREFIX, len(c.get("wake_variants") or ()), LLM_ON, LLM_MODEL, BIG_MODEL, ASK_TTL, HOUSE_LANG,
                    sorted(NEEDWAKE_IPS)), flush=True)
            last = c
        except Exception as e:                                                 # noqa: BLE001  the dashboard is restarting: keep the values
            why = "%s: %s" % (type(e).__name__, e)
            if why != err:                                                     # the first failure and every new reason, not every 10 s
                print("[vad] settings not read (%s): the current ones stay" % why[:300], flush=True)
            err = why
        time.sleep(CONFIG_POLL_S)


def ollama_models():
    """The models the Ollama of each brain has: {"small": [...], "big": [...]} (empty when one is off or down)."""
    import urllib.request
    out = {}
    for k, url in (("small", LLM_URL), ("big", BIG_URL)):
        out[k] = []
        if url:
            try:
                with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=3) as r:
                    out[k] = sorted(m["name"] for m in _json.load(r).get("models") or [] if m.get("name"))
            except Exception:                                                  # noqa: BLE001
                pass
    return out


_brains_lock = threading.Lock()


def _brains():
    """The router of the model's questions: the Dell's small model always, the big one while its health says available."""
    with _brains_lock:                                    # two callers must not start two health pollers
        return _brains_locked()


def _brains_locked():
    import urllib.request

    import voice_llm
    if _llm["brains"] is None:
        if _llm["ollama"] is None:
            _llm["ollama"] = voice_llm.Ollama(LLM_URL, LLM_MODEL, int(os.environ.get("VA_LLM_THREADS", "4")), LLM_TIMEOUT)
        big = health = warm = None
        if BIG_URL and BIG_HEALTH:
            big = voice_llm.Ollama(BIG_URL, BIG_MODEL, 4, 30.0, extra={"think": False}, keep_alive="30m")      # stays loaded while it is available

            def health():
                with urllib.request.urlopen(BIG_HEALTH, timeout=1.0) as r:
                    return _json.load(r)

            def warm(system):
                big.ask(system or "Answer in one line.", "\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u0432\u0440\u0435\u043c\u0435\u043d\u0438", timeout=voice_llm.WARM_TIMEOUT)  # i18n
        reg = _llm["reg"]                                 # the first warm-up caches the real prompt, not a dummy one
        _llm["brains"] = voice_llm.Brains(_llm["ollama"], big, health, poll_s=float(os.environ.get("VA_BIG_POLL", "30")), warm=warm, model=BIG_MODEL,
                                          system=reg.system if reg is not None else "")
        _llm["brains"].start()                            # the health is read here, every 30 s, never on a command
    return _llm["brains"]

_pending = {}                                         # satellite ip or text-box token -> the plan waiting for "yes"


def _api_get(path):
    return requests.get(AC_API + path, headers=_AUTH, timeout=4).json()


def _llm_registry():
    """The registry of the live device list; rebuilt at most once a minute, the old one kept when the text is the same."""
    import voice_llm
    from voice_vosk import _stems
    with _llm_lock:
        if _llm["reg"] is not None and time.time() - _llm["at"] < 60:
            return _llm["reg"]
        load_commands()
        devs = _api_get("")
        devs = devs if isinstance(devs, list) else devs.get("devices", [])
        words = [(w, t[1]) for w, t in DEVICES.items()]
        for ap in APPLIANCES:
            for room in ap.get("rooms") or []:
                words += [(st, room["target"]) for w in room.get("words") or [] for st in _stems(w) if room.get("target")]
        try:
            rooms = requests.get(AC_API.rsplit("/", 1)[0] + "/rooms", headers=_AUTH, timeout=4).json()
        except Exception:
            rooms = []
        rooms = rooms if isinstance(rooms, list) else rooms.get("rooms", [])
        room_of = _served_by(rooms)
        gl = _glossary()
        words += [(w, did) for did, ws in (gl.get("aliases") or {}).items() for w in ws]
        names = [(w, d["id"]) for d in devs if isinstance(d, dict) and d.get("id") for w in re.findall(r"[\u0430-\u044f\u0451]{4,}", str(d.get("name") or "").lower())]  # i18n
        facts, load_words = _map_facts(words + names)
        words += load_words
        reg = voice_llm.Registry(devs, words, room_of, gl.get("names"), facts=facts)
        if reg.facts_dropped and reg.facts_dropped != _llm.get("dropped"):
            print("[vad] map facts: %d lines dropped over the cap of %d" % (reg.facts_dropped, voice_llm.FACTS_MAX), flush=True)
        _llm["dropped"] = reg.facts_dropped
        if _llm["reg"] is None or _llm["reg"].system != reg.system:
            _llm["reg"] = reg
            if _llm["brains"] is not None:
                _llm["brains"].system = reg.system        # what the background warm-up of the big model caches
        _llm["at"] = time.time()
        return _llm["reg"]


def _map_facts(known=()):
    """The owner's home map (the dashboard's map plug-in; empty when it is off or unreachable): lines for the model's prompt and the names of the
    loads as extra words of the devices that control them ("the central chandelier" -> the switch). -> (lines, [(stem, device id)]).
    known: [(word, device id)] of the other names, a map word that is also one of those is not taken (voice_llm.map_words)."""
    import voice_llm
    base = AC_API.rsplit("/", 1)[0]
    try:
        lines = voice_llm.facts_of(requests.get(base + "/map/facts", headers=_AUTH, timeout=3).json())
        m = requests.get(base + "/map", headers=_AUTH, timeout=3).json()
    except Exception:
        return [], []
    return lines, voice_llm.map_words(m, known)


GLOSSARY = os.environ.get("VA_GLOSSARY", "/house/voice_glossary.json")


def _rekey(g: dict, devs: list) -> dict:
    """The glossary with a merged device's former ids read as the device it is now (the device list's "aliases"): its
    names ("dev" and "dev/code"), spoken stems and learned commands; an entry of the new id wins over an old one."""
    now = {a: d["id"] for d in devs or [] if isinstance(d, dict) for a in d.get("aliases") or []}
    if not now:
        return g
    key = lambda x: (lambda dev, sep, code: now.get(dev, dev) + sep + code)(*str(x).partition("/"))     # noqa: E731
    for sec in ("names", "aliases"):
        if isinstance(g.get(sec), dict):
            old = {key(k): v for k, v in g[sec].items() if key(k) != k}
            g[sec] = {**old, **{k: v for k, v in g[sec].items() if key(k) == k}}
    if isinstance(g.get("learned"), dict):
        g["learned"] = {ph: [[now.get(a[0], a[0]), *a[1:]] if isinstance(a, list) and a else a for a in acts] if isinstance(acts, list) else acts
                        for ph, acts in g["learned"].items()}
    return g


def _glossary():
    """House data: {"aliases": {device id: [spoken stems]}, "learned": {phrase: [[device, code, value]]}}; a missing file = empty.
    A merged device's former ids read as the device it is now (_rekey)."""
    try:
        with open(GLOSSARY, encoding="utf-8") as f:
            g = _json.load(f)
    except (OSError, ValueError):
        return {}
    try:
        devs = _qcache["devs"] or _qdata()[0]
    except Exception:                                     # noqa: BLE001  no house answer: the file as it is
        devs = []
    return _rekey(g, devs)


def _glossary_save(g):
    tmp = GLOSSARY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(g, f, ensure_ascii=False, indent=1)
    os.replace(tmp, GLOSSARY)


# ---- state questions (voice_state.py): the house answers from live state; never an action, never a question back ----
def _served_by(rooms):
    """{device id: room name} by the room each device SERVES (the dashboard's "Serves"), not where it stands;
    an older dashboard without `serves` gives where it stands."""
    return {d: r["name"] for r in rooms for d in r.get("serves", r.get("devices", []))}


_qcache = {"at": 0.0, "devs": [], "room_of": {}}


def _qdata():
    """The device list and the rooms of the house, read at most every 30 s."""
    if time.time() - _qcache["at"] > 30:
        devs = _api_get("")
        _qcache["devs"] = devs if isinstance(devs, list) else devs.get("devices", [])
        try:
            rooms = requests.get(AC_API.rsplit("/", 1)[0] + "/rooms", headers=_AUTH, timeout=4).json()
            rooms = rooms if isinstance(rooms, list) else rooms.get("rooms", [])
            _qcache["room_of"] = _served_by(rooms)
            _qcache["sensors"] = {r["name"].lower(): r.get("sensors") or {} for r in rooms}     # the house's order of each room's reading
        except Exception:
            pass
        _qcache["at"] = time.time()
    return _qcache["devs"], _qcache["room_of"]


def _spoken(did):
    n = (_glossary().get("names") or {}).get(did) or {}
    return n.get("nom") or n.get("acc") or None


def _spoken_chan(did, code):
    n = (_glossary().get("names") or {}).get("%s/%s" % (did, code)) or {}
    return n.get("nom") or n.get("acc") or None


def _find_devices(words):
    """The devices the words of a question name: a word of the device's name, its room, a map synonym or a glossary alias."""
    devs, room_of = _qdata()
    syn = {}
    for w, t in DEVICES.items():
        syn.setdefault(t[1], []).append(w)
    for did, ws in (_glossary().get("aliases") or {}).items():
        syn.setdefault(did, []).extend(ws)
    best, hits = 0, []
    for d in devs:
        if d.get("voice_role"):
            continue
        toks = re.findall(r"[a-z\u0430-\u044f\u04510-9]+", ((d.get("name") or "") + " " + d["id"]).lower()) + syn.get(d["id"], [])  # i18n
        score = sum(1 for tk in toks if len(tk) >= 3 and any(w.startswith(tk[:4]) or tk.startswith(w[:4]) for w in words if len(w) >= 3))
        room = room_of.get(d["id"], "").lower()
        if score and room and any(w.startswith(room[:4]) for w in words if len(w) >= 3):
            score += 1
        if score:
            hits.append((score, d["id"]))
            best = max(best, score)
    return [i for sc, i in sorted(hits, reverse=True) if sc == best][:3]


_wcache = {"at": 0.0, "data": None}


def _weather_data():
    """The forecast of the house (the weather plug-in's GET /api/weather), read at most once a minute; None when there is none."""
    if time.time() - _wcache["at"] > 60:
        try:
            r = requests.get(AC_API.rsplit("/", 1)[0] + "/weather", headers=_AUTH, timeout=4)
            _wcache["data"] = r.json() if r.status_code == 200 else None
        except Exception:
            _wcache["data"] = None
        _wcache["at"] = time.time()
    return _wcache["data"]


def answer_query(q, lang="ru"):
    """(the full text, the short text to read aloud) for a query dict, from the LIVE state of the house, in `lang`."""
    import voice_state
    if q["type"] == "clock":                                 # the house clock: no device to read
        t = voice_state.clock(q.get("what"), lang == "en")
        return t, t
    if q["type"] == "why":                                   # the rule log of the house, newest first
        try:
            ev = requests.get(AC_API.rsplit("/", 1)[0] + "/history", params={"kind": "rules", "limit": 200}, headers=_AUTH, timeout=4).json().get("events") or []
        except Exception:
            ev = []
        ids = q.get("ids") or []
        devs, _room = _qdata()
        g = ((_glossary().get("names") or {}).get(ids[0]) or {}) if ids else {}
        name = (g.get("acc") or _spoken(ids[0]) or next((d.get("name") for d in devs if d["id"] == ids[0]), ids[0])) if ids else ""   # "turned on the light in the bathroom"
        t = voice_state.why_answer(ev, ids, name, lang == "en", q.get("want"))
        return t, t
    if q["type"] == "weather":
        import voice_weather
        return voice_weather.answer(_weather_data(), q, lang)
    devs, room_of = _qdata()
    st = voice_state.State(devs, room_of, lambda did: _api_get("/%s/status" % did), _spoken, chan=_spoken_chan, lang=lang,
                           sensors=_qcache.get("sensors"))
    if q["type"] == "device":
        st.prefetch(q["ids"])
    elif q["type"] == "room":
        st.prefetch([d for d, r in room_of.items() if r.lower() == q["room"].lower()])
    elif q["type"] == "who_home":
        st.prefetch([d["id"] for d in devs if d["id"].startswith("person_")])
    elif q["type"] == "list" and q.get("room"):
        st.prefetch([d for d, r in room_of.items() if r.lower() == q["room"].lower()])
    else:
        st.prefetch()
    return voice_state.answer_pair(st, q)


def quick_answer(cmd):
    """(full text, spoken text) of a question the fast templates know ("\u043a\u0442\u043e \u0434\u043e\u043c\u0430", "what is on in the bedroom", "\u0432\u043a\u043b\u044e\u0447\u0451\u043d \u043b\u0438 X"), else None."""  # i18n
    import voice_state
    try:
        _devs, room_of = _qdata()
        q = voice_state.quick(cmd, sorted(set(room_of.values())), _find_devices)
        text = answer_query(q, HOUSE_LANG) if q else None
        if q:
            print("[QUERY] %r -> %s -> %r" % (cmd, q, text), flush=True)
        return text
    except Exception as e:
        print("[QUERY] %r failed: %s" % (cmd, e), flush=True)
        return None


def _state(did, code):
    try:
        return _api_get("/%s/status" % did).get("values", {}).get(code)
    except Exception:
        return None


def _execute(actions):
    done = 0
    for did, code, value in actions:
        _note_done(did, code, value)
        try:
            r = requests.post("%s/%s/control" % (AC_API, did), json={"code": code, "value": value}, headers=_AUTH, timeout=8)
            good = r.status_code == 200
        except Exception as e:
            good = False
            print("[LLM] control %s %s=%s failed: %s" % (did, code, value, e), flush=True)
        print("[LLM] do %s %s=%s -> %s" % (did, code, value, "ok" if good else "FAILED"), flush=True)
        done += good
    return done


def _result_reply(lang="ru", problems_only=False):
    """The spoken result of the commands collected in this thread: wait up to 3 s for each device's own report, then a short phrase."""
    import voice_result
    items = list(getattr(_tl, "done", None) or [])
    _tl.done = None
    if not items:
        return None
    voice_result.wait_confirm(items, _state, 3.0)

    def forms(did, code):
        n = _glossary().get("names") or {}
        return n.get("%s/%s" % (did, code)) or n.get(did) or {}

    def name(did, code, case):
        f = forms(did, code)
        if f.get(case) or f.get("nom"):
            return f.get(case) or f["nom"]
        d = next((x for x in _qdata()[0] if x["id"] == did), None)
        return (d or {}).get("name") or did

    if lang == "en":
        name = lambda did, code, case: _name_en(did)                                  # noqa: E731
    text = voice_result.phrase(items, name, lambda did, code: forms(did, code).get("g", "n"), lang, problems_only)
    print("[RESULT] %s <- %s" % (text, [(i["did"], i["code"], i["value"], i["before"], i["ok"]) for i in items]), flush=True)
    return text or None


_last_said: dict = {}            # ip -> (the words of the last reply said to that satellite, when)


def _is_echo(ip, said):
    """The satellite's mic heard the house's own reply ("\u043d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430" after "\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430."): the words of the transcript are all words of what was just said."""  # i18n
    last = _last_said.get(ip)
    words = set(norm(said).split())
    return bool(last and words and time.time() - last[1] < 20 and words <= last[0])


def _beep(ip):
    """A command was understood: a short confirm beep at once (before it is carried out and its result is waited for)."""
    pub("voice/heard", _json.dumps({"ip": ip, "ok": True, "text": "", "engine": "beep", "sound": "done"}))


def _heard(ip, ok, text, engine, reply=None, sound=None, ask=False, quiet=False):
    m = {"ip": ip, "ok": bool(ok), "text": text, "engine": engine}
    if quiet:
        m["quiet"] = True                                 # the beep already said it: the satellite adds no chime of its own
    if ask:
        m["ask"] = True                                   # the reply is a question: the house must see it played, then opens the mic once
    if reply:
        m["reply"] = reply
        _last_said[ip] = (set(norm(reply).split()), time.time())
    if sound:
        m["sound"] = sound
    pub("voice/heard", _json.dumps(m, ensure_ascii=False))


def _name_en(did):
    """A device as English speech has it: the registry's own name when it is Latin, else its id."""
    d = next((x for x in _qdata()[0] if x["id"] == did), None)
    n = (d or {}).get("name") or ""
    return n if n and not re.search(r"[\u0410-\u042f\u0430-\u044f\u0401\u0451]", n) else did  # i18n


def _plog(**fields):
    """One line of the phrase log (voice_phraselog); a missing or broken log never touches the phrase."""
    try:
        import voice_phraselog
        voice_phraselog.record(**fields)
    except Exception as e:                                # noqa: BLE001
        print("[plog] %s" % e, flush=True)


def _ask_en(reg, p, parsed):
    """The question for the user in plain English."""
    import voice_llm
    if parsed["action"] == "all_off":
        keep = ", ".join(_name_en(d) for d in parsed["except"])
        return "Turn everything off except %s?" % keep if keep else "Turn everything off?"
    parts = []
    for d, code, v in p["actions"]:
        if reg.dev[d]["controls"][code]["kind"] == "switch":
            verb = ("Lock" if v == "on" else "Unlock") if code in voice_llm.RISKY_CODES else ("Turn on" if v == "on" else "Turn off")
            parts.append("%s %s" % (verb, _name_en(d)))
        else:
            parts.append("Set %s %s to %s" % (_name_en(d), code, v))
    return "%s?" % ", ".join(parts)


NOT_SURE = "\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430, \u043e \u0447\u0451\u043c \u0432\u044b. \u041c\u043e\u0436\u043d\u043e \u0442\u0430\u043a: \u00ab\u043a\u0442\u043e \u0434\u043e\u043c\u0430\u00bb, \u00ab\u043a\u0430\u043a\u0430\u044f \u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0430 \u0432 \u0441\u043f\u0430\u043b\u044c\u043d\u0435\u00bb, \u00ab\u0447\u0442\u043e \u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e\u00bb, \u00ab\u0432\u044b\u043a\u043b\u044e\u0447\u0438 \u0441\u0432\u0435\u0442 \u043d\u0430 \u043a\u0443\u0445\u043d\u0435\u00bb."  # i18n


def model_failed(e):
    """What to say when the model did not answer (still loading after a start, busy, or down), else None (a real "not understood")."""
    if isinstance(e, (TimeoutError, socket.timeout)) or "timed out" in str(e):
        return "\u041c\u043e\u0434\u0435\u043b\u044c \u0435\u0449\u0451 \u0437\u0430\u0433\u0440\u0443\u0436\u0430\u0435\u0442\u0441\u044f \u0438\u043b\u0438 \u0437\u0430\u043d\u044f\u0442\u0430: \u0441\u043f\u0440\u043e\u0441\u0438 \u0447\u0435\u0440\u0435\u0437 \u043c\u0438\u043d\u0443\u0442\u0443."  # i18n
    if isinstance(e, (ConnectionError, OSError)):
        return "\u041c\u043e\u0434\u0435\u043b\u044c \u0441\u0435\u0439\u0447\u0430\u0441 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430."  # i18n
    return None


def llm_handle(phrase, key, source):
    """-> {"ok", "reply"?, "said", "ask", "engine": "llm"}; `key` (satellite ip / text token) is where a question waits.
    The reply is in the house language."""
    import voice_lang
    import voice_llm
    lang = HOUSE_LANG
    res = {"engine": "llm", "ok": False, "reply": NOT_UNDERSTOOD, "said": NOT_UNDERSTOOD, "ask": False}
    if voice_llm.ACK_RE.match(phrase.lower().strip(" ?!.")):          # "I am home": a statement, not a question and not a command
        return {"engine": "ack", "ok": True, "reply": "\u041f\u043e\u043d\u044f\u0442\u043d\u043e.", "said": "\u041f\u043e\u043d\u044f\u0442\u043d\u043e.", "ask": False, "line": "", "decision": "ack"}  # i18n
    if _llm.get("ready") is False:                       # still starting: said at once, not after a timeout
        busy = model_failed(TimeoutError())
        return {"engine": "llm", "ok": False, "reply": busy, "said": busy, "ask": False, "line": "", "decision": "loading"}
    import voice_rules
    t0 = time.time()
    line, decision = "", "not-understood"
    think = None
    try:
        reg = _llm_registry()
        brains = _brains()
        known = voice_llm.learned(_glossary(), reg, phrase)
        if known:                                       # a guess the user confirmed once: no model needed
            if source == "voice":
                _beep(key)
            n = _execute(known)
            what = "; ".join("%s: %s=%s" % (reg.label(d), c, v) for d, c, v in known)
            print("[LLM] learned phrase %r -> %d/%d" % (phrase, n, len(known)), flush=True)
            return {"engine": "learned", "ok": n == len(known), "reply": None, "said": "\u0421\u0434\u0435\u043b\u0430\u043d\u043e: " + what, "ask": False,  # i18n
                    "line": "", "decision": "learned"}
        if source == "voice":                               # the "thinking" sound only when the model takes longer than 1.5 s
            think = threading.Timer(voice_rules.THINKING_AFTER_S, _heard, (key, True, phrase, "llm"), {"sound": "thinking"})
            think.daemon = True
            think.start()
        line, res["brain"] = brains.ask(reg.system, phrase)
        line = voice_rules.norm_room_tokens(line, voice_llm.slug)
        if think is not None:
            think.cancel()
        import voice_rules
        try:
            parsed = voice_llm.parse_dsl(line, reg.names, reg.qnames, reg.room_slugs)
        except ValueError:
            parsed = voice_rules.repair_line(line, DEVICES, reg)          # "<zone stem>=on": the zone map says which device
            if parsed is None:
                raise
            print("[LLM] line %r repaired through the zone map" % line, flush=True)
        words = re.findall(r"[\u0430-\u044f\u04510-9]+", phrase.lower()) + (voice_lang.en_stems(phrase) if voice_lang.lang_of(phrase) == "en" else [])  # i18n
        p = voice_llm.plan(reg, parsed, words, _state, LLM_CONFIRM_ALL_OFF)
        guess = voice_rules.guess_reply(parsed, p, words, voice_rules.verb_stems(ON_WORDS, OFF_WORDS))
        if guess:                                          # a device the phrase does not name: no question about it
            guess = voice_lang.tr(guess, lang)
            res.update(ok=False, reply=guess, said=guess, ask=False)
            decision = "guess-rejected(%s)" % ",".join(p["ask"])
        elif p.get("query"):                                 # a question: answered from live state, nothing to confirm
            import voice_state
            if p["query"].get("type") == "weather":                # the model only says "weather": the day and rain/temperature come from the words
                import voice_weather
                p["query"] = dict(voice_weather.flags_of(phrase), type="weather")
            p["query"] = voice_rules.pin_room(p["query"], words, _room_of(key))
            if p["query"].get("type") == "why":                  # turned on or off: the words say which rule is meant
                p["query"]["want"] = voice_state.why_of(phrase)
            full, spoken = answer_query(voice_state.fix_aspect(p["query"], phrase), lang)
            res.update(ok=True, reply=spoken, said=full, spoken=spoken, ask=False, engine="llm")
            decision = "query(%s)" % p["query"].get("type")
        elif p["actions"]:
            if p["ask"]:
                q = _ask_en(reg, p, parsed) if lang == "en" else voice_llm.ask_text(reg, p, parsed)
                learnable = parsed["action"] == "set" and all(a.startswith("guess:") for a in p["ask"])
                _pending[key] = {"actions": p["actions"], "until": time.time() + ASK_TTL, "q": q, "learn": phrase if learnable else None, "lang": lang}
                res.update(reply=q, said=q, ask=True, ok=True)
                decision = "ask(%s)" % ",".join(p["ask"])
            else:
                _tl.done = [] if source == "voice" else None
                if source == "voice":
                    _beep(key)
                n = _execute(p["actions"])
                spoken = _result_reply(lang, problems_only=True) if source == "voice" else None
                what = "; ".join("%s: %s=%s" % (_name_en(d) if lang == "en" else reg.label(d), c, v) for d, c, v in p["actions"])
                res.update(ok=n == len(p["actions"]), reply=spoken, said=voice_lang.tr(("\u0421\u0434\u0435\u043b\u0430\u043d\u043e: " if n else "\u041d\u0435 \u0432\u044b\u0448\u043b\u043e: ") + what, lang))  # i18n
                decision = "do(%d/%d)" % (n, len(p["actions"]))
        elif parsed["action"] == "none":
            decision = "none"
            res["chat"] = source in ("text", "ptt")     # the model says it is no command: the text box or the phone may chat
    except Exception as e:
        decision = "error: %s" % e
        if think is not None:
            think.cancel()
        late = model_failed(e) or (NOT_SURE if isinstance(e, ValueError) else None)    # an unreadable line: ask back, never guess
        if late:
            res.update(reply=late, said=late)
    print("[LLM %.1fs] phrase=%r line=%r decision=%s brain=%s" % (time.time() - t0, phrase, line, decision, res.get("brain", "-")), flush=True)
    res["line"], res["decision"] = line, decision
    res["reply"], res["said"] = voice_lang.tr(res.get("reply"), lang), voice_lang.tr(res.get("said"), lang)
    return res


def confirm_pending(key, text, voice=False):
    """The answer to a question: yes runs the waiting plan, anything else drops it."""
    import voice_lang
    import voice_llm
    pend = _pending.pop(key, None)
    if pend is None or pend["until"] < time.time():
        return {"engine": "confirm", "ok": False, "reply": None, "said": "\u0412\u043e\u043f\u0440\u043e\u0441 \u0443\u0441\u0442\u0430\u0440\u0435\u043b.", "ask": False}  # i18n
    lang = pend.get("lang", "ru")
    if voice_llm.confirmation(text) is True:
        _tl.done = [] if voice else None
        n = _execute(pend["actions"])
        spoken = _result_reply(lang, problems_only=True) if voice else None
        if pend.get("learn") and n == len(pend["actions"]):          # a guess that was right: remembered for this exact phrase
            g = _glossary()
            voice_llm.learn(g, pend["learn"], pend["actions"])
            try:
                _glossary_save(g)
            except OSError as e:
                print("[LLM] glossary not saved: %s" % e, flush=True)
        print("[LLM] confirmed %r -> %d/%d" % (pend["q"], n, len(pend["actions"])), flush=True)
        return {"engine": "confirm", "ok": n == len(pend["actions"]), "reply": spoken, "said": voice_lang.tr("\u0421\u0434\u0435\u043b\u0430\u043d\u043e.", lang), "ask": False}  # i18n
    print("[LLM] not confirmed %r (%r)" % (pend["q"], text), flush=True)
    return {"engine": "confirm", "ok": True, "reply": voice_lang.tr("\u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.", lang), "said": voice_lang.tr("\u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.", lang), "ask": False}  # i18n


def ask_text(text):
    res = _ask_text(text)
    _plog(src="text", free=text, engine=res.get("engine"), ok=res.get("ok"), reply=res.get("said"), llm_line=res.get("line"),
          llm_decision=res.get("decision"), brain=res.get("brain"))
    return res


def _ask_text(text):
    """The text box: the same pipeline after recognition (phrase map -> LLM -> strict validation); engine=text in the log."""
    import voice_lang
    import voice_vosk
    phrase = voice_vosk.digits(" ".join(norm(text).split()))
    lang = HOUSE_LANG
    if not phrase:
        return {"engine": "text", "ok": False, "said": NOT_UNDERSTOOD, "ask": False}
    if voice_state_is_question(phrase):                    # a question never acts
        text = quick_answer(phrase)
        if text is not None:
            return {"engine": "query", "ok": True, "said": text[0], "spoken": text[1], "ask": False}
        ok, lines = False, []
    else:
        _tl.lines = []
        try:
            ok = act(phrase)
            lines = list(_tl.lines)
        finally:
            _tl.lines = None
    if ok:
        print("[TEXT engine=map ok=True] %s -> %s" % (phrase, lines), flush=True)
        return {"engine": "map", "ok": True, "said": "\u0421\u0434\u0435\u043b\u0430\u043d\u043e: " + "; ".join(l.replace("[ACT] ", "") for l in lines), "ask": False}  # i18n
    if not (LLM_URL and LLM_ON):
        return {"engine": "none", "ok": False, "said": voice_lang.tr(NOT_UNDERSTOOD, lang), "ask": False}
    token = "t" + secrets.token_hex(6)
    res = llm_handle(phrase, token, "text")
    res["token"] = token if res["ask"] else None
    print("[TEXT engine=llm ok=%s ask=%s] %s" % (res["ok"], res["ask"], phrase), flush=True)
    return res


def chat_reply(msgs):
    """The text of a chat answer: a house question gets the fixed refusal, else the model's answer (checked; the big one
    thinks); a model that is down or late says so in the question's language, never an empty bubble."""
    import voice_lang
    import voice_llm
    t0, text = time.time(), msgs[-1]["content"]
    en = voice_lang.lang_of(text) == "en"
    fixed = voice_llm.chat_gate(text)
    if fixed:                                            # about the house or the people: not the model's to answer
        print("[TEXT engine=chat GATED] %r" % text, flush=True)
        return fixed
    try:
        full, brain = _brains().chat(msgs)
    except (TimeoutError, socket.timeout):
        print("[TEXT engine=chat TIMEOUT] %r" % text, flush=True)
        return "The model did not answer in time." if en else "\u041c\u043e\u0434\u0435\u043b\u044c \u043d\u0435 \u0443\u0441\u043f\u0435\u043b\u0430 \u043e\u0442\u0432\u0435\u0442\u0438\u0442\u044c."  # i18n
    except OSError:
        print("[TEXT engine=chat DOWN] %r" % text, flush=True)
        return "The model is not available now." if en else "\u041c\u043e\u0434\u0435\u043b\u044c \u0441\u0435\u0439\u0447\u0430\u0441 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430."  # i18n
    out = voice_llm.chat_filter(full)                    # held until checked: a streamed lie cannot be taken back
    if out != full:
        print("[TEXT engine=chat FILTERED] %r -> %r" % (full[:120], out), flush=True)
    print("[TEXT engine=chat brain=%s] %d chars in %.1fs" % (brain, len(out), time.time() - t0), flush=True)
    return out or ("The model did not answer." if en else "\u041c\u043e\u0434\u0435\u043b\u044c \u043d\u0435 \u043e\u0442\u0432\u0435\u0442\u0438\u043b\u0430.")  # i18n


def _serve_http():
    """POST /ask {text} and POST /confirm {token, yes} for the dashboard plug-in (X-Voice-Key); one process, one pipeline."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    key = os.environ.get("VOICE_TTS_KEY", "")

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 700000:                                     # 20 s of audio is the most a phrase can be
                    self.send_response(413)
                    self.end_headers()
                    return
                raw = self.rfile.read(n)
                try:
                    body = {} if self.path == "/utter" or self.path.startswith("/record/save") else _json.loads(raw or b"{}")
                except ValueError:                                 # a body that is not JSON is the client's fault, not a 500
                    body = None
                if not key or self.headers.get("X-Voice-Key") != key:
                    code, out = 401, {"error": "key"}
                elif body is None:
                    code, out = 400, {"error": "json"}
                elif self.path.startswith("/record/"):
                    import voice_record
                    code, out = voice_record.http_post(self.path, body, raw)
                elif self.path == "/utter":
                    code, out = 200, utter_pcm(raw)
                elif self.path == "/ask":
                    code, out = 200, ask_text(str(body.get("text") or ""))
                elif self.path == "/chat":
                    return self._chat(body)
                elif self.path == "/models":
                    code, out = 200, ollama_models()
                elif self.path == "/confirm":
                    code, out = 200, confirm_pending(str(body.get("token") or ""), "\u0434\u0430" if body.get("yes") else "\u043d\u0435\u0442")  # i18n
                else:
                    code, out = 404, {"error": "path"}
            except Exception as e:
                code, out = 500, {"error": str(e)}
            data = _json.dumps(out, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            from urllib.parse import parse_qs, urlparse
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not key or self.headers.get("X-Voice-Key") != key:
                self.send_response(401)
                self.end_headers()
                return
            import voice_record
            code, data, ctype = voice_record.http_get(u.path, q)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _chat(self, body):
            """Streamed text of a chat answer; plain text, no actions, nothing of it is ever parsed as a command."""
            if (_llm["ollama"] is None and not LLM_URL) or not LLM_ON:
                self.send_response(503)
                self.end_headers()
                return
            msgs = [{"role": m["role"], "content": str(m["content"])[:600]} for m in (body.get("history") or [])[-4:]
                    if m.get("role") in ("user", "assistant") and m.get("content")]
            msgs.append({"role": "user", "content": str(body.get("text") or "")[:600]})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(chat_reply(msgs).encode())

        def log_message(self, *a):
            pass

    if key:
        threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("VA_HTTP_PORT", "8093"))), H).serve_forever, daemon=True).start()


def _llm_start():
    """Warm-up at service start, and when smart answers come on or the model changes: the model is loaded and the prompt cached before
    the first phrase (cold ~20 s, warm ~1.4 s)."""
    import voice_llm
    if not LLM_ON:
        return
    _llm["ready"] = False
    for _ in range(60):                                   # the house may come up after the service: wait for it, 5 min at most
        try:
            _llm_registry()
            break
        except Exception as e:                            # noqa: BLE001
            print("[llm] the house is not up yet (%s): again in 5 s" % type(e).__name__, flush=True)
            time.sleep(5)
    try:
        reg = _llm_registry()
        if not LLM_ON:                                    # turned off while the registry was read: no model, no poller
            return
        _llm["ollama"] = voice_llm.Ollama(LLM_URL, LLM_MODEL, int(os.environ.get("VA_LLM_THREADS", "4")), LLM_TIMEOUT)
        with _brains_lock:
            if _llm["brains"] is not None:
                _llm["brains"].stop()                     # one health poller only
            _llm["brains"] = None
        _brains()
        t = _llm["ollama"].warm_up(reg.system)
        _llm["ready"] = True                               # a later question goes to a loaded model
        print("[llm] %s: %d devices, warm-up %s" % (LLM_MODEL, len(reg.dev), "%.1fs" % t if t else "FAILED (Ollama unreachable?)"), flush=True)
    except Exception as e:
        print("[llm] start failed: %s" % e, flush=True)


def voice_state_is_question(cmd):
    import voice_state
    return voice_state.is_query(cmd)


def _decide(pcm, trusted, key, source):
    """Recognize one utterance and act on it: the grammar pass (checked against the free pass), the free pass, then the LLM.
    -> {"cmd", "ok", "engine", "reply", "said", "conf", "llm"}; `key` is where a question of the LLM waits, `source` voice | ptt."""
    import voice_rules
    import voice_vosk
    v = _vosk_grammar()
    cmd, ok, engine, reply, llm, full = "", False, "none", None, None, None
    _tl.done = [] if source == "voice" else None
    # pass 1: the grammar; it can invent a command out of any speech, so it counts only when the
    # free pass hears the same words. Otherwise the free pass is the answer (and must find the command itself).
    gtext, gconf = v.recognize(pcm, True)
    gw = norm(gtext).split()
    free = []                                   # (text, conf), filled when the free pass runs

    def free_pass():
        if not free:
            free.append(v.recognize(pcm, False))
        return free[0]

    grammar_ok = bool(gw) and "[unk]" not in gtext and gconf >= VOSK_MINCONF
    stems = voice_rules.verb_stems(ON_WORDS, OFF_WORDS)
    if grammar_ok and VOSK_GUARD:
        grammar_ok = voice_rules.accept_grammar(gw, norm(free_pass()[0]).split(), stems, VOSK_AGREE)
    said, conf = gtext, gconf
    for engine in ("grammar", "free"):
        if engine == "grammar":
            if not grammar_ok:
                continue
            words = gw
        else:
            said, conf = free_pass()
            words = norm(said).split()
        if not words or (not trusted and find_wake(words[:3]) < 0):
            continue
        words = voice_rules.strip_lead(voice_rules.strip_wake_phrase(_strip_wake(words) if find_wake(words[:3]) >= 0 else words), stems + list(voice_rules.QUESTION_STEMS))
        cmd = voice_vosk.digits(" ".join(words))
        clock = voice_rules.time_answer(cmd, time.localtime()) if cmd else None
        if clock:
            ok, engine, reply, full = True, "query", clock, clock
            break
        if cmd and voice_state_is_question(cmd):          # "is the light on" shares its stem with "turn the light on" in Russian: never an action
            text = quick_answer(cmd)
            if text is not None:
                ok, engine, reply, full = True, "query", text[1], text[0]
                break
            cmd = ""
            continue
        if cmd and act(cmd):
            ok = True
            if source == "voice":
                _beep(key)
            reply = _result_reply(problems_only=True) if source == "voice" else None
            break
    _tl.done = None
    if free:
        said = free[0][0] or said
    if not ok and LLM_URL and LLM_ON:            # no rule matched: the LLM gets the words the free pass heard
        words = norm(said).split()
        words = voice_rules.strip_lead(voice_rules.strip_wake_phrase(_strip_wake(words) if find_wake(words[:3]) >= 0 else words), stems + list(voice_rules.QUESTION_STEMS))
        if words and (trusted or find_wake(norm(said).split()[:3]) >= 0):
            cmd = " ".join(words)
            llm = llm_handle(cmd, key, source)
            ok, engine, reply, full = llm["ok"], "llm", llm["reply"], llm.get("said")
    return {"cmd": cmd, "ok": ok, "engine": engine, "reply": reply, "said": said, "conf": conf, "llm": llm, "gtext": gtext, "full": full}


def _finish_vosk(st, pcm, p):
    import voice_rules
    ip = st.get("ip", "")
    dur = (len(st["voiced"]) // 2) / RATE
    trusted = wake_checked_on_device(ip)
    t0 = time.time()
    v = _vosk_grammar()
    pend = _pending.get(ip)
    if pend is not None and pend["until"] < time.time():      # nobody answered: the question is void, this is a new phrase
        _pending.pop(ip, None)
        pend = None
    if pend is not None:                         # the house asked this satellite a question: this utterance is the answer
        import voice_llm
        import voice_rules
        text, _ = v.recognize(pcm, False)
        kind = voice_rules.answer_kind(text, voice_llm.confirmation)
        if kind == "command":                    # neither yes nor no: a command of its own; the question is dropped, the command runs
            _pending.pop(ip, None)
            print("[LLM] question %r dropped: heard a new phrase %r" % (pend["q"], text), flush=True)
        else:
            res = confirm_pending(ip, text, voice=True)
            print("[UTT %.1fs %.0fms engine=confirm ok=%s ip=%s %s] heard=%r (%s) -> %s"
                  % (dur, (time.time() - t0) * 1000, res["ok"], ip, p, text, kind, res["said"]), flush=True)
            _heard(ip, res["ok"], text, "confirm", res["reply"])
            _plog(src="voice", ip=ip, engine="confirm", heard=text, kind=kind, ok=res["ok"], said=res["said"])
            return
    d = _decide(pcm, trusted, ip, "voice")
    cmd, ok, engine, reply, said = d["cmd"], d["ok"], d["engine"], d["reply"], d["said"]
    ms = (time.time() - t0) * 1000
    if not said:
        print("[UTT %.1fs %.0fms junk ip=%s %s]" % (dur, ms, ip, p), flush=True)
        return
    if not ok and (not voice_rules.strip_wake_phrase(norm(said).split()) or dur < 0.6 or d["conf"] < 0.5 or _is_echo(ip, said)):
        print("[UTT %.1fs %.0fms ignored (only the wake phrase, a stray word or low confidence %.2f) ip=%s] free=%r" % (dur, ms, d["conf"], ip, said), flush=True)
        return                                                                    # no "not understood" for what was no phrase
    print("[UTT %.1fs %.0fms engine=%s ok=%s conf=%.2f ip=%s %s] free=%r grammar=%r -> %s"
          % (dur, ms, engine if ok or engine == "llm" else "none", ok, d["conf"], ip, p, said, d["gtext"], cmd or "(no command)"), flush=True)
    _plog(src="voice", ip=ip, room=_room_of(ip), dur=dur, ms=ms, free=said, grammar=d["gtext"], conf=d["conf"], engine=engine, ok=ok, cmd=cmd,
          reply=reply, llm_line=(d["llm"] or {}).get("line"), llm_decision=(d["llm"] or {}).get("decision"), brain=(d["llm"] or {}).get("brain"))
    if cmd or trusted or find_wake(norm(said).split()[:3]) >= 0:        # a satellite that must say the wake phrase is answered once it did
        asking = bool(d["llm"] and d["llm"].get("ask"))
        quiet = False
        if not asking:
            reply, quiet = voice_rules.final_message(ok, reply, NOT_UNDERSTOOD)
        _heard(ip, ok, cmd or said, engine if ok or engine == "llm" else "", reply, ask=asking, quiet=quiet)


def utter_pcm(raw):
    """A phrase recorded on a phone (push-to-talk): 16 kHz mono s16le, no wake word. The same decision as a spoken phrase; a question
    waits under a token (Yes / No come as buttons), the log says engine=ptt."""
    a = np.frombuffer(raw[:len(raw) // 2 * 2], dtype=np.int16)
    if len(a) < RATE * 3 // 10:                  # under 0.3 s: a tap, not a phrase
        return {"engine": "none", "ok": False, "said": "\u041d\u0435 \u0440\u0430\u0441\u0441\u043b\u044b\u0448\u0430\u043b\u0430.", "text": "", "ask": False}  # i18n
    t0 = time.time()
    pcm = normalize(a.tobytes())
    token = "t" + secrets.token_hex(6)
    _tl.lines = []
    try:
        d = _decide(pcm, True, token, "ptt")
        lines = list(_tl.lines)
    finally:
        _tl.lines = None
    llm = d["llm"]
    if llm is not None:
        out = dict(llm)
        out["token"] = token if llm.get("ask") else None
    elif d["ok"] and d["engine"] == "query":
        out = {"engine": "query", "ok": True, "ask": False, "said": d.get("full") or d["reply"], "reply": d["reply"], "spoken": d["reply"]}
    elif d["ok"]:
        out = {"engine": d["engine"], "ok": True, "ask": False,
               "said": "\u0421\u0434\u0435\u043b\u0430\u043d\u043e: " + "; ".join(l.replace("[ACT] ", "") for l in lines)}  # i18n
    else:
        out = {"engine": "none", "ok": False, "ask": False, "said": NOT_UNDERSTOOD if d["said"] else "\u041d\u0435 \u0440\u0430\u0441\u0441\u043b\u044b\u0448\u0430\u043b\u0430."}  # i18n
    out["text"] = d["cmd"] or d["said"]
    print("[PTT %.1fs %.0fms engine=%s ok=%s ask=%s] %s" % (len(a) / RATE, (time.time() - t0) * 1000, out["engine"], out["ok"],
                                                          out.get("ask"), out["text"]), flush=True)
    _plog(src="ptt", dur=len(a) / RATE, ms=(time.time() - t0) * 1000, free=d["said"], grammar=d["gtext"], conf=d["conf"], engine=out["engine"],
          ok=out["ok"], cmd=d["cmd"], reply=out.get("said"), llm_line=(llm or {}).get("line"), llm_decision=(llm or {}).get("decision"))
    return out


def _finish(st):
    if st.get("dup"):                                     # the same phrase was heard first by another satellite of the room
        st["dup"] = False
        return
    pcm = normalize(bytes(st["voiced"]))
    p = _save_capture(pcm)
    return _finish_vosk(st, pcm, p)


def process(chunk, st):
    import voice_record
    if voice_record.bg_feed(st.get("ip", ""), chunk) or voice_record.feed(st.get("ip", ""), chunk):
        return                                             # a recording: saved as it is, never recognised, never acted on
    st["buf"] += chunk
    while len(st["buf"]) >= FBYTES:
        raw = bytes(st["buf"][:FBYTES]); del st["buf"][:FBYTES]
        st["rx"] = st.get("rx", 0) + FBYTES
        is_sp = vad.is_speech(vad_frame(raw), RATE)
        if not st["triggered"]:
            st["ring"].append((raw, is_sp))
            if sum(1 for _, s in st["ring"] if s) > 0.9 * st["ring"].maxlen:
                st["triggered"] = True
                st["dup"] = not _admit(st.get("ip", ""))
                st["voiced"] = bytearray()
                st["quiet"] = 0
                st["cmd_ms"] = 0
                for f, _ in st["ring"]:
                    st["voiced"] += f
                st["ring"].clear()
        else:
            st["voiced"] += raw
            st["ring"].append((raw, is_sp))
            over = (len(st["voiced"]) // 2) * 1000 // RATE >= MAX_MS
            st["quiet"] = 0 if is_sp else st.get("quiet", 0) + 1
            import voice_rules
            if is_sp and st["rx"] > PREROLL_BYTES:                     # speech after the node's preroll: the command has begun
                st["cmd_ms"] = st.get("cmd_ms", 0) + 30
            if st["quiet"] >= voice_rules.end_quiet_frames(st.get("cmd_ms", 0)) or over:
                st["done"] = True                                       # the node ends its own stream: closing it here left the ReSpeaker mic on the I2S bus (no reply)
                try:
                    _finish(st)
                except Exception as e:                                  # noqa: BLE001  a failed phrase is dropped, never decoded again frame after frame
                    print("[UTT] phrase failed: %s: %s" % (type(e).__name__, e), flush=True)
                finally:
                    st["triggered"] = False
                    st["voiced"] = bytearray()
                    st["quiet"] = 0
                    st["cmd_ms"] = 0
                    st["ring"].clear()


def main():
    """The service: the satellites' PCM on one port, the helper threads, the HTTP side."""
    global vad
    load_commands(force=True)
    _mqtt_start()
    import webrtcvad
    vad = webrtcvad.Vad(AGGR)
    import voice_record
    voice_record.PUB = pub
    threading.Thread(target=lambda: [(voice_record.bg_tick(), time.sleep(1.0)) for _ in iter(int, 1)], daemon=True, name="record-tick").start()
    _vosk_grammar()              # load the model and build the grammar now, not on the first utterance
    try:
        config_tick()                # the page's values before the models start: smart answers off = no model is loaded
    except Exception as e:           # noqa: BLE001  the loop below says why, and retries
        print("[vad] settings: first read failed (%s), the env values run" % type(e).__name__, flush=True)
    if LLM_URL and LLM_ON:
        threading.Thread(target=_llm_start, daemon=True).start()
    threading.Thread(target=_config_loop, daemon=True, name="config").start()
    _serve_http()
    print("[vad] wake='%s' vad_gain=%.0f norm_peak=%.0f aggr=%d recognizer=vosk" % (WAKE, VAD_GAIN, NORM_PEAK, AGGR), flush=True)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(4)
    srv.setblocking(False)
    print("[vad] waiting on :%d (select multi-conn)" % PORT, flush=True)

    conns  = {}    # fd -> socket
    st_by  = {}    # fd -> per-connection VAD state
    last_by = {}   # fd -> monotonic time of last data (idle-reap)
    addr_by = {}   # fd -> peer addr (drop stale conns from the same satellite IP)


    def _drop(fd, why):
        # Flush a triggered-but-unfinished utterance before dropping. A satellite that
        # gates on-device (tablet) closes its socket right after the command, often
        # before our VAD sees enough trailing silence to finalize - without this the
        # utterance would be lost on close.
        st = st_by.get(fd)
        if st:
            import voice_record
            voice_record.bg_conn_closed(st.get("ip", ""))
        if st and st.get("triggered") and (len(st.get("voiced", b"")) // 2) / RATE >= 0.3:
            try:
                _finish(st)
            except Exception as e:
                print("[vad] flush-on-drop err: %s" % e, flush=True)
        c = conns.get(fd)
        try:
            if c:
                c.close()
        except Exception:
            pass
        conns.pop(fd, None)
        st_by.pop(fd, None)
        last_by.pop(fd, None)
        addr_by.pop(fd, None)
        print("[vad] %s fd %d (%d active)" % (why, fd, len(conns)), flush=True)


    while True:
        try:
            r, _, _ = select.select([srv] + list(conns.values()), [], [], 1.0)
        except Exception as e:
            print("[vad] select err: %s" % e, flush=True)
            time.sleep(0.2)
            continue
        now = time.time()
        for s in r:
            if s is srv:
                try:
                    c, addr = srv.accept()
                except Exception:
                    continue
                c.setblocking(False)
                # There is one mic satellite per IP: drop any earlier connection from
                # the same peer so ESP reconnect churn can't accumulate half-open TCP.
                for ofd in [fd for fd, a in addr_by.items() if a[0] == addr[0]]:
                    _drop(ofd, "dropped stale conn from %s," % addr[0])
                # Let the OS detect a vanished peer too (belt-and-braces vs idle-reap).
                try:
                    c.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    c.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 20)
                    c.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                    c.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                except Exception:
                    pass
                fd = c.fileno()
                conns[fd] = c
                st_by[fd] = new_state()
                st_by[fd]["ip"] = addr[0]
                last_by[fd] = now
                addr_by[fd] = addr
                print("[vad] conn from %s (fd %d, %d active)" % (str(addr), fd, len(conns)), flush=True)
                continue
            fd = s.fileno()
            try:
                d = s.recv(4096)
            except BlockingIOError:
                continue
            except Exception:
                d = b""
            if not d:
                _drop(fd, "closed")
                continue
            last_by[fd] = now
            try:
                process(d, st_by[fd])
            except Exception as e:
                print("[vad] proc err: %s" % e, flush=True)
        # idle sweep: reap half-open connections that went silent (dead ESP)
        for fd, ts in [(f, t) for f, t in last_by.items() if now - t > IDLE_TO]:
            _drop(fd, "reaped idle (%.0fs)," % (now - ts))


if __name__ == "__main__":
    main()
