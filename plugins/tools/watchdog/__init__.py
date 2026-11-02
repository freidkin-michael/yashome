"""watchdog: keeps the house consistent without anybody babysitting it. One loop every 45 s runs checks; a data fix (a retained ghost, a dangling
reference, a stale mark) is made and logged at once, an actuation fix only for a state a RULE set or a command that was not confirmed, rate limited, and never
for a lock, an air conditioner, the boiler or anything marked sensitive; everything else is reported. Owner only: GET /api/watchdog (the house token).

Checks: rules (a channel a rule set while its source says otherwise for 2 minutes: the opposite rule runs again), fresh (a sensor silent beyond its interval:
its values are marked stale), commands (a command whose channel still reports the old value: sent again once, then an alert), ghosts (retained home/state of
ids that are no device), refs (a gone device's data leftovers are listed, forgotten by themselves after forget_days while its system runs; a rule
naming it is reported), presence (the phones and the router disagree). An issue closes by itself when its cause is gone and stays in `resolved` with its duration; one that comes back within the hour is the
same entry with a count.

State: plugin-state/watchdog/state.json (the rule origins, the fixes, the digest). Settings, all optional: plugin-data/watchdog/settings.json
{"never_actuate": {"ids": [...], "categories": [...]}, "checks": {"rules": true, ...}, "fresh": {"mains_s": 1200, "battery_s": 21600}, "forget_days": 30}."""
import asyncio
import collections
import datetime
import json
import threading
import time
from typing import Optional

from fastapi import HTTPException, Request
from pydantic import Field

import app as core

PLUGIN = {"name": "Home watchdog", "version": "1.0.0", "icon": "shield"}

LOOP_S = 45
GRACE_S = 120                  # a source that selects the opposite rule this long before the rule is run again
RETRY_S = 120                  # one re-run per target in this time; two tries, then an alert
GHOST_EVERY_S = 600
DANGLING_LOOPS = 4             # a device missing this many loops in a row (about 3 minutes) before a rule naming it is reported
FORGET_DAYS_MAX = 3650
HISTORY_S = 7 * 86400          # fixes and solved issues are kept this long
FLAP_S = 3600                  # an issue that comes back within this is the same entry, counted
CONFIRM_AFTER_S = 15           # the shortest wait for a command's answer (a channel learns its own, see confirm_window)
CONFIRM_MAX_S = 180            # ... and the longest
CONFIRM_KEEP, CONFIRM_LEARNED = 20, 5                       # answers kept per channel; answers before they replace the start value
CONFIRM_FOR_S = 600            # ... and is not older than this
PRESENCE_S = 1800              # the phones and the router disagree this long
NO_FRESH_CATEGORIES = ("climate", "ir", "button", "voice", "indicator", "lock", "tv", "camera")      # they answer when asked or tell on their own occasion: silence says nothing
NO_FRESH_CODES = ("battery", "linkquality", "wifi_signal", "uptime", "button", "rssi", "action", "event")
DEFAULTS = {"never_actuate": {"ids": ["water heater"], "categories": ["lock", "climate"]},
            "checks": {"rules": True, "fresh": True, "commands": True, "ghosts": True, "refs": True, "presence": True},
            "fresh": {"mains_s": 1200, "battery_s": 21600}, "config_quiet": [], "forget_days": 30,
            "confirm_start": {"lock": 60, "tv": 90}, "confirm": {}}       # the start wait by kind; "device/code" -> a wait set by hand

# The words an issue or a fix is made of (English; the dashboard translates each through i18n.json with {n} for the values).
RULE = "{0} has been {1} for {2} s and {3} is still {4} from the {5} rule"
RULE_RAN, RULE_NEVER, RULE_OFFLINE = ": ran the {0} rule", " (the watchdog never switches it)", " (it is offline)"
RULE_FIXED = '{0}: "{1}" by its rule did not take at first; the house ran the rule once more and it worked'
RULE_WAIT, RULE_ALERT, RULE_FAIL = ": waiting to run it again", ": the {0} rule was run {1} times and did not help", ": the {0} rule failed ({1})"
GHOST = "cleared {0} retained topics of ids that are no device"
REF_FIX, REF_LEFT = "forgot {0}: gone for {1} days", "'{0}' does not exist but is named by: {1}"
FORGOT, FORGOT_HELD = "{0} gone devices still have data stored", "; {0} of them are older than {1} days but their system is down"
HINT_FORGOT = "Settings, All devices, Forgotten: forget what is not coming back."
FRESH = "{0}: no report for {1} min (more than {2} min is stale)"
HINT_FRESH = "Check that it is powered and in range; its values are shown dimmed until it reports again."
CMD_RETRY = '{0}: "{1}" did not work the first time; the house sent it again and waits for the answer'
CMD_FIXED = '{0}: "{1}" did not work the first time; the house sent it again and it worked'
HINT_CMD = "Look at the device: it may be stuck, out of range or wired differently from its card."
CMD_ALERT = '{0}: "{1}" did not work, not even when the house sent it again'
CMD_NEVER = '{0}: "{1}" did not work; the house does not send it again by itself'
CMD_TOGGLE = '{0}: "{1}" did not work; it only flips, so the house does not send it again by itself'
SLOW = '{0}: "{1}" worked, but slowly: in {2} s'
_UNCONFIRMED = ("{0}: {1} was set to {2} and the device still reports {3}: sent again",         # rows of older versions that claimed a fix
                "{0} has been {1} for {2} s and {3} is still {4} from the {5} rule")            # nobody confirmed: dropped when loaded
PRESENCE = "the phones say {0}, the router says {1}, for {2} min"
HINT_PRESENCE = "Check the phone MAC on the router and that the Yash! app is allowed to run in the background."
HINT_RULE = "Look at the light or device and at its rule; the house keeps trying only twice."
HINT_REF = "Open the rule or panel named here and point it at an existing device."

_lock = threading.RLock()
_issues: dict = {}             # key -> {key, check, subject, parts, text, level (warn | retry | alert), since, last, opened}
_closed: dict = {}             # key -> (closed at, opened count): to fold a flapping issue
_resolved = collections.deque(maxlen=50)
_fixes = collections.deque(maxlen=100)
_pending: dict = {}            # issue key -> (check, subject): what the house did, listed as fixed once the issue is gone
_slow = collections.deque(maxlen=50)                        # answers that came after the window, before any retry
_lat: dict = {}                # "device/code" -> the last answer times (s) of its commands
_lat_seen: dict = {}           # (device, code) -> the command whose answer was taken
_lat_dirty = [False]
_missing: dict = {}            # device key -> loops it has been missing
_gone_since: dict = {}         # device key -> when it was first seen missing (kept: the days survive a restart)
_seen: dict = {}               # device key -> [owner plug-in, transport]: what a gone device is judged by
_tries: dict = {}              # (target, code) -> [last try, tries]
_cmd_seen: dict = {}           # (device, code) -> [time of the command last judged, tries]
_pres = {"since": 0.0}
_marked: set = set()           # the devices whose values this watchdog marked stale
_last = {"ghosts": 0.0, "digest_day": "", "saved": ""}
_notes = collections.deque(maxlen=20)   # what a helper observed and posted (POST /api/watchdog/note): the card shows it
_digest = {"ts": 0, "open": 0, "fixes": 0, "solved": 0, "items": []}


def _nm(dev: str) -> str:
    """A device as people name it: its display name, with its room when the name does not say it already ("Bath light (Bath)")."""
    d = core.DEVICES.get(dev) or {}
    name = str(d.get("name") or dev)
    room = next((r.get("name") or "" for r in core.get_rooms() if dev in (r.get("devices") or [])), "")
    return f"{name} ({room})" if room and room.lower() not in name.lower() else name


def _msg(*parts, hint=None):
    """[(template, args), ...] -> {"parts": [[template, [args]]...], "text": the English sentence}."""
    out = {"parts": [[t, [str(a) for a in args]] for t, *args in parts], "text": "".join(t.format(*[str(a) for a in args]) for t, *args in parts)}
    if hint:
        out["hint"] = [hint, []]                                                  # what to do about it
    return out


def _state_file():
    return core.plugin_state_file("watchdog", "state.json")


def _settings() -> dict:
    try:
        raw = json.loads((core.plugin_data("watchdog") / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULTS.items()}
    if isinstance(raw, dict):
        na, ck, fr = raw.get("never_actuate"), raw.get("checks"), raw.get("fresh")
        cq, fd = raw.get("config_quiet"), raw.get("forget_days")
        if isinstance(fd, (int, float)) and not isinstance(fd, bool) and 0 <= fd <= FORGET_DAYS_MAX:
            out["forget_days"] = fd
        for k in ("confirm_start", "confirm"):
            if isinstance(raw.get(k), dict):
                out[k].update({str(a): float(b) for a, b in raw[k].items() if isinstance(b, (int, float)) and 0 < b <= 3600})
        if isinstance(cq, list):
            out["config_quiet"] = [{"device": str(c["device"]), "prefix": str(c["prefix"]).rstrip("/") + "/", "seconds": float(c.get("seconds") or 30)}
                                   for c in cq if isinstance(c, dict) and c.get("device") and c.get("prefix")]
        if isinstance(na, dict):
            out["never_actuate"] = {"ids": [str(x) for x in na.get("ids", DEFAULTS["never_actuate"]["ids"])],
                                    "categories": [str(x) for x in na.get("categories", DEFAULTS["never_actuate"]["categories"])]}
        if isinstance(ck, dict):
            out["checks"].update({k: bool(v) for k, v in ck.items() if k in out["checks"]})
        if isinstance(fr, dict):
            out["fresh"].update({k: float(v) for k, v in fr.items() if k in out["fresh"] and isinstance(v, (int, float))})
    return out


def never_actuate(dev_id: str, code: str = "") -> bool:
    """A lock (by its category or its channel), an air conditioner, the boiler, anything marked sensitive, anything the house
    lists: the watchdog never switches it."""
    d = core.DEVICES.get(dev_id) or {}
    na = _settings()["never_actuate"]
    lock = "lock" in na["categories"] and bool(code) and _kind(dev_id, code) == "lock"
    return d.get("category") in na["categories"] or lock or dev_id in na["ids"] or bool(d.get("sensitive"))


def no_feedback(dev_id: str) -> bool:
    """An IR remote says nothing back: whether it did what it was told cannot be known, so nothing is enforced on it."""
    d = core.DEVICES.get(dev_id) or {}
    return d.get("transport") == "ir" or d.get("category") == "ir"


def _device_of(subject: str) -> str:
    dev = subject.split(".")[0]
    return dev if dev in core.DEVICES else ""


def _kind(dev: str, code: str) -> str:
    """The channel's kind: a lock by its category or by a "lock" channel (an MQTT lock is a switch device), else the category."""
    cat = (core.DEVICES.get(dev) or {}).get("category") or ""
    return "lock" if cat == "lock" or "lock" in code else cat


def _verb(dev: str, code: str, val) -> str:
    """What a command asked for, in the device's own verb: lock / unlock, turn on / turn off, else the value itself."""
    v = str(val).lower()
    on, off = val is True or v in ("on", "true", "lock", "locked"), val is False or v in ("off", "false", "unlock", "unlocked")
    if _kind(dev, code) == "lock":
        return "lock" if on else "unlock" if off else str(val)
    return "turn on" if on else "turn off" if off else str(val)


_pending_words: dict = {}


def _pend(key: str, check: str, subject: str, m: dict) -> None:
    """The house acted on an issue: it becomes a fix once the issue is gone, nothing if it turns into an alert."""
    with _lock:
        _pending[key] = (check, subject)
        _pending_words[key] = m


def _lat_file():
    return core.plugin_data("watchdog") / "latency.json"


def _p90(xs: list) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.9 * len(xs)))]


def confirm_window(dev: str, code: str) -> float:
    """How long a command to this channel waits for its answer: a wait set by hand; else learned, max(15 s, 1.5 x p90) up to
    180 s, once 5 answers are known; else the start value of the device's kind."""
    s = _settings()
    if f"{dev}/{code}" in s["confirm"]:
        return s["confirm"][f"{dev}/{code}"]
    xs = _lat.get(f"{dev}/{code}") or []
    if len(xs) >= CONFIRM_LEARNED:
        return round(min(CONFIRM_MAX_S, max(CONFIRM_AFTER_S, 1.5 * _p90(xs))), 1)
    return s["confirm_start"].get(_kind(dev, code), CONFIRM_AFTER_S)


def _toggle(dev: str, code: str) -> bool:
    """A channel whose command only flips (a TV's power key, an IR toggle): sending it again could undo it."""
    return any(c.get("code") == code and c.get("toggle") for c in ((core.DEVICES.get(dev) or {}).get("controls") or []))


def _newest(dev: str, code: str):
    """The newest command a person, a rule or a timer sent to the channel (the watchdog's own retry is not a new command)."""
    return next(((v, o, t) for d, c, v, o, t, cause in reversed(core.sent_commands) if d == dev and c == code and cause != "watchdog"), None)


def _on_report(dev: str, values: dict, ts: float) -> None:
    """A report that carries a channel's commanded value answers its newest command: the time is learned, once per command;
    after the window and before any retry it is a slow answer. After a retry the answer may be the retry's: not learned."""
    for code, v in values.items():
        cmd = _newest(dev, code)
        if not cmd or v != cmd[0] or ts < cmd[2] or _lat_seen.get((dev, code)) == cmd[2] or ts - cmd[2] > CONFIRM_FOR_S:
            continue
        took, win = ts - cmd[2], confirm_window(dev, code)
        _lat_seen[(dev, code)] = cmd[2]
        seen = _cmd_seen.get((dev, code))
        if seen and seen[0] == cmd[2]:
            continue
        with _lock:
            xs = _lat.setdefault(f"{dev}/{code}", [])
            xs.append(round(took, 1))
            del xs[:-CONFIRM_KEEP]
            _lat_dirty[0] = True
            if took > win:
                _slow.appendleft({"ts": ts, "check": "commands", "subject": f"{dev}.{code}", **_msg((SLOW, _nm(dev), _verb(dev, code, cmd[0]), int(took)))})


core.ON_REPORT.append(_on_report)


def _fix(check: str, subject: str, m: dict) -> None:
    with _lock:
        _fixes.appendleft({"ts": time.time(), "check": check, "subject": subject, **m})
    core.log.info(f"[watchdog] {check} {subject}: {m['text']}")
    core.emit_event({"kind": "watchdog", "device": _device_of(subject), "subject": subject, "check": check, "parts": m["parts"], "source": "watchdog"})


def _sync(check: str, found: dict, now: float) -> None:
    """The issues of this check become `found` {key: (level, subject, msg)}: new ones open, gone ones close (kept in `resolved`), the rest stay."""
    with _lock:
        done = set()
        for key in [k for k, p in _pending.items() if p[0] == check]:
            if key not in found:                                                 # what the house did took: now it is a fix
                done.add(key)
                _fix(check, _pending.pop(key)[1], _pending_words.pop(key))
            elif found[key][0] == "alert":                                       # it did not: the alert says so
                _pending.pop(key)
                _pending_words.pop(key, None)
        for key, (level, subject, m) in found.items():
            cur = _issues.get(key)
            if cur:
                cur.update(level=level, parts=m["parts"], text=m["text"], hint=m.get("hint"), resend=m.get("resend"), link=m.get("link"), last=now)
                continue
            prev = _closed.pop(key, None)
            opened = prev[1] + 1 if prev and now - prev[0] < FLAP_S else 1
            _issues[key] = {"key": key, "check": check, "subject": subject, "parts": m["parts"], "text": m["text"], "hint": m.get("hint"), "resend": m.get("resend"),
                            "link": m.get("link"), "level": level, "since": now, "last": now, "opened": opened}
            if level == "info":
                continue                                                         # a note to look at when convenient: no event, no colour
            core.emit_event({"kind": "issue", "device": _device_of(subject), "subject": subject, "check": check, "parts": m["parts"], "level": "alert" if level == "alert" else "warn",
                             "opened": opened, "source": "watchdog"})
        for key in [k for k, v in _issues.items() if v["check"] == check and k not in found]:
            gone = _issues.pop(key)
            _closed[key] = (now, gone["opened"])
            if key in done or gone["level"] == "info":
                continue                                                         # listed as fixed, or a note: not as solved by itself
            _resolved.appendleft({"key": key, "check": check, "subject": gone["subject"], "parts": gone["parts"], "text": gone["text"], "since": gone["since"], "until": now,
                                  "seconds": int(now - gone["since"]), "opened": gone["opened"]})
            core.log.info(f"[watchdog] {check} {gone['subject']}: solved by itself after {int(now - gone['since'])} s")
            core.emit_event({"kind": "solved", "device": _device_of(gone["subject"]), "subject": gone["subject"], "check": check, "parts": gone["parts"], "seconds": int(now - gone["since"]),
                             "opened": gone["opened"], "source": "watchdog"})


# ---- rules: a channel a rule set, while its source says otherwise ----
def _plain(b: dict) -> bool:
    return b.get("action") in ("on", "off") and not b.get("for") and b.get("action") not in core.BINDING_ACTIONS


def check_rules(now: float) -> dict:
    """For every channel a rule switched (core.bind_origin): when the rule's source has said the opposite for GRACE_S and a plain rule for that opposite
    exists for the same channel, the channel is still in the old rule's state, and it is not a never-actuate or IR device: that rule runs again (conditions
    apply), once per RETRY_S, twice at most, then an alert. A channel nobody's rule switched (a hand, the app, voice) is never in this list."""
    found = {}
    for k in [k for k in _tries if k not in core.bind_origin]:
        _tries.pop(k)                                                            # the channel is nobody's rule's now
    with core.bindings_lock:
        binds = {s: {c: {g: dict(b) for g, b in gs.items()} for c, gs in cs.items()} for s, cs in core.bindings.items()}
    for (tgt, tcode), o in list(core.bind_origin.items()):
        src, scode, g0 = o["src"], o["code"], o["g"]
        live, since = core.sensor_last.get(f"{src}/{scode}"), core.sensor_since.get(f"{src}/{scode}")
        if live is None or since is None:
            continue
        g_now = "true" if live else "false"
        if g_now == g0 or now - since < GRACE_S:
            continue
        b = binds.get(src, {}).get(scode, {}).get(g_now)
        if not b or not _plain(b) or b.get("target") != tgt or b.get("code") != tcode:
            continue                                                             # no opposite rule for this channel: nothing to enforce
        have = (core.status_copy(tgt, {}).get("values") or {}).get(tcode)
        if have is None:
            continue
        if bool(have) == (b["action"] == "on"):
            core.bind_origin.pop((tgt, tcode), None)                             # in step
            _tries.pop((tgt, tcode), None)
            continue
        key, subject = f"rule:{tgt}/{tcode}", f"{tgt}.{tcode}"
        base = (RULE, _nm(src), g_now, int(now - since), _nm(tgt), "on" if have else "off", g0)
        if never_actuate(tgt, tcode) or no_feedback(tgt):
            found[key] = ("warn", subject, _msg(base, (RULE_NEVER,), hint=HINT_RULE))
            continue
        if core.is_offline(tgt):
            found[key] = ("warn", subject, _msg(base, (RULE_OFFLINE,), hint=HINT_RULE))
            continue
        t = _tries.setdefault((tgt, tcode), [0.0, 0])
        if t[1] >= 2:
            found[key] = ("alert", subject, _msg(base, (RULE_ALERT, g_now, t[1]), hint=HINT_RULE))
            continue
        if now - t[0] < RETRY_S:
            found[key] = ("retry", subject, _msg(base, (RULE_WAIT,)))
            continue
        t[0], t[1] = now, t[1] + 1
        res = core.safe_execute_binding(src, scode, g_now)
        if isinstance(res, dict) and res.get("error"):
            found[key] = ("retry" if t[1] < 2 else "alert", subject, _msg(base, (RULE_FAIL, g_now, res["error"])))
            continue
        _pend(key, "rules", subject, _msg((RULE_FIXED, _nm(tgt), _verb(tgt, tcode, b["action"] == "on"))))
        found[key] = ("retry", subject, _msg(base, (RULE_RAN, g_now)))
    return found


# ---- fresh: a sensor silent beyond its interval has stale values ----
def _numeric_sensor(d: dict) -> bool:
    return any(c.get("kind") == "sensor" and c.get("code") not in NO_FRESH_CODES and c.get("vtype") in (None, "num") for c in d.get("controls") or [])


def check_fresh(now: float) -> dict:
    """A device that is online by its own word but has reported nothing for longer than its kind reports (mains 20 min, battery 6 h) has its values marked
    stale: the dashboard dims them, a room's temperature and the voice answers never use them. The next report clears the mark."""
    found, fr = {}, _settings()["fresh"]
    for did, d in list(core.DEVICES.items()):
        if not _numeric_sensor(d) or no_feedback(did) or d.get("category") in NO_FRESH_CATEGORIES or d.get("ir_via"):
            continue
        st = core.status_copy(did, {})
        if not st.get("online") or st.get("waiting") or not st.get("last_seen"):
            continue
        battery = any(c.get("code") == "battery" for c in d.get("controls") or [])
        if not battery and not st.get("periodic"):
            continue                                                             # never seen reporting regularly: its silence is not judged
        limit = fr["battery_s"] if battery else fr["mains_s"]
        age = now - st["last_seen"]
        if age <= limit:
            continue
        if not st.get("stale"):
            st["stale"] = True
            core.store_status(did, st, report=False)
        _marked.add(did)
        found[f"stale:{did}"] = ("warn", did, _msg((FRESH, _nm(did), int(age / 60), int(limit / 60)), hint=HINT_FRESH))
    for did in [d for d in _marked if f"stale:{d}" not in found]:                 # a mark of ours on a device that is fresh now or is no longer judged goes
        _marked.discard(did)
        st = core.status_copy(did, {})
        if st.get("stale"):
            st.pop("stale", None)
            core.store_status(did, st, report=False)
    return found


# ---- commands: the channel still reports the old value ----
def check_commands(now: float) -> dict:
    """The newest command to each channel (core.sent_commands, not the watchdog's own retry): when the device has reported since and, past the channel's
    confirm window, still says the value from before, the command did not take: it is sent again once (not a never-actuate, IR or toggle channel: those
    are reported, with "Send again"), and when it still does not take, an alert."""
    found, newest = {}, {}
    for dev, code, val, old, ts, cause in list(core.sent_commands):
        if cause != "watchdog":
            newest[(dev, code)] = (val, old, ts)
    for (dev, code), (val, old, ts) in newest.items():
        if code in ((core.DEVICES.get(dev) or {}).get("no_enforce") or ()):    # its state may change by itself (a TV that wakes)
            _cmd_seen.pop((dev, code), None)
            continue
        age = now - ts
        st = core.status_copy(dev, {})
        have = (st.get("values") or {}).get(code)
        seen = _cmd_seen.get((dev, code))
        if age > CONFIRM_FOR_S or dev not in core.DEVICES:
            _cmd_seen.pop((dev, code), None)
            continue
        if age < confirm_window(dev, code) or have is None or old is None or have == val or have != old or (st.get("last_seen") or 0) <= ts + 1:
            continue                                                             # too soon, taken, or the device said something else (a hand)
        key, base = f"cmd:{dev}/{code}", (_nm(dev), _verb(dev, code, val))
        if never_actuate(dev, code) or no_feedback(dev) or core.is_offline(dev) or _toggle(dev, code):
            m = _msg((CMD_TOGGLE if _toggle(dev, code) else CMD_NEVER, *base), hint=HINT_CMD)
            if not no_feedback(dev) and not core.is_offline(dev):
                m["resend"] = {"device": dev, "code": code}                        # the owner may send it again by hand
            found[key] = ("warn", f"{dev}.{code}", m)
            continue
        tries = seen[1] if seen and seen[0] == ts else 0
        if tries >= 1:
            found[key] = ("alert", f"{dev}.{code}", _msg((CMD_ALERT, *base), hint=HINT_CMD))
            continue
        _cmd_seen[(dev, code)] = [ts, tries + 1]
        keep = core.bind_origin.get((dev, code))
        try:
            asyncio.run_coroutine_threadsafe(core.control(dev, core.ControlBody(code=code, value=val, source="watchdog")), core.main_loop).result(10)
        except Exception as e:                                                  # noqa: BLE001
            found[key] = ("alert", f"{dev}.{code}", _msg((CMD_ALERT, *base), hint=HINT_CMD))
            core.log.warning(f"[watchdog] commands {dev}.{code}: sending again failed: {e}")
            continue
        finally:
            if keep:
                core.bind_origin[(dev, code)] = keep                             # the rule still owns the channel
        _pend(key, "commands", f"{dev}.{code}", _msg((CMD_FIXED, *base)))
        found[key] = ("retry", f"{dev}.{code}", _msg((CMD_RETRY, *base)))
    for k in [k for k in _cmd_seen if f"cmd:{k[0]}/{k[1]}" not in found]:
        if now - _cmd_seen[k][0] > CONFIRM_FOR_S:
            _cmd_seen.pop(k)
    return found


# ---- ghosts: retained state of ids that are no device ----
def check_ghosts(now: float) -> dict:
    if now - _last["ghosts"] < GHOST_EVERY_S or not core.registry_known():
        return {}
    _last["ghosts"] = now
    n = core.prune_state_mirror()
    if n:
        _fix("ghosts", "home/state", _msg((GHOST, n)))
    return {}


# ---- refs: references to devices that do not exist ----
def _system_up(dev: str) -> bool:
    """The system a gone device came from runs: its plug-in is loaded and another device of its transport is there now.
    A device never seen (gone before the watchdog knew it) has no system: it is only listed."""
    owner, transport = _seen.get(dev) or ("", "")
    if not transport or (owner and owner not in {p.get("id") for p in core.plugins}):
        return False
    return any(d.get("transport") == transport for d in list(core.DEVICES.values()))


def check_refs(now: float) -> dict:
    found = {}
    if not core.registry_known():
        return found
    owners = core.device_owner
    for dev, d in list(core.DEVICES.items()):
        _seen[dev] = [str(owners.get(dev) or ""), str(d.get("transport") or "")]
    days = _settings()["forget_days"]
    orphans = {k: v for k, v in core.orphan_refs().items() if not k.startswith(core.VIRTUAL_PREFIXES)}
    for d in (_missing, _gone_since):
        for k in [k for k in d if k not in orphans]:
            d.pop(k)
    for k in [k for k in _seen if k not in core.DEVICES and k not in orphans]:
        _seen.pop(k)
    held = 0
    for key in orphans:
        _missing[key] = _missing.get(key, 0) + 1
        since = _gone_since.setdefault(key, now)
        if days and now - since >= days * 86400 and key in _seen:
            if not _system_up(key):
                held += 1                                                   # its system is down: it may come back, nothing goes
            elif gone := core.prune_data_refs({key}):
                _fix("refs", key, _msg((REF_FIX, ", ".join(f"{kind} {where}" for kind, _k, where in gone[:6]) + (" ..." if len(gone) > 6 else ""), int(days))))
        if _missing[key] < DANGLING_LOOPS:
            continue
        left = [p for p in (core.orphan_refs().get(key) or []) if p.split(":", 1)[0] not in core.data_ref_kinds]        # a rule, an automation: reported, never touched
        if left:
            found[f"ref:{key}"] = ("warn", key, _msg((REF_LEFT, key, ", ".join(left[:6])), hint=HINT_REF))
    listed = [k for k, v in core.orphan_refs().items() if not k.startswith(core.VIRTUAL_PREFIXES) and any(p.split(":", 1)[0] in core.data_ref_kinds for p in v)]
    if listed:                                                                  # one row, however many: an outage must not flood the report
        m = _msg((FORGOT, len(listed)), *([(FORGOT_HELD, held, int(days))] if held else []), hint=HINT_FORGOT)
        found["forgotten"] = ("warn" if held else "info", "forgotten", {**m, "link": "devices/forgotten"})
    return found


# ---- presence: the phones and the router disagree ----
def check_presence(now: float) -> dict:
    router = (core.status_copy("presence", {}).get("values") or {}).get("home")
    phones = [bool((core.status_copy(i, {}).get("values") or {}).get("home")) for i, d in list(core.DEVICES.items()) if d.get("transport") == "family"]
    if router is None or not phones or bool(router) == any(phones):
        _pres["since"] = 0.0
        return {}
    _pres["since"] = _pres["since"] or now
    if now - _pres["since"] < PRESENCE_S:
        return {}
    word = lambda v: "home" if v else "away"                                      # noqa: E731
    return {"presence": ("warn", "presence", _msg((PRESENCE, word(any(phones)), word(router), int((now - _pres["since"]) / 60)), hint=HINT_PRESENCE))}


CHECKS = (("rules", check_rules), ("fresh", check_fresh), ("commands", check_commands), ("ghosts", check_ghosts), ("refs", check_refs), ("presence", check_presence))


def status(now: float | None = None) -> str:
    """green: nothing open (the history never colours it); yellow: a warning; orange: a retry waiting for its answer; red: an alert."""
    now = now or time.time()
    with _lock:
        levels = {v["level"] for v in _issues.values()}
    return "red" if "alert" in levels else "orange" if "retry" in levels else "yellow" if "warn" in levels else "green"


def run_once(now: float | None = None) -> None:
    now = now or time.time()
    cfg = _settings()["checks"]
    for name, fn in CHECKS:
        if not cfg.get(name):
            with _lock:
                for k in [k for k, p in _pending.items() if p[0] == name]:
                    _pending.pop(k)
                    _pending_words.pop(k, None)
            _sync(name, {}, now)
            continue
        try:
            _sync(name, fn(now), now)
        except Exception as e:                                                  # noqa: BLE001  one check must not stop the others
            core.log.warning(f"[watchdog] check {name} failed: {type(e).__name__}: {e}")
    cut = now - HISTORY_S
    with _lock:
        for q, at in ((_fixes, "ts"), (_resolved, "until"), (_slow, "ts")):
            keep = [x for x in q if x.get(at, 0) >= cut]
            q.clear()
            q.extend(keep)
    _digest_if_due(now)
    _save()
    if _lat_dirty[0]:
        with _lock:
            snap, _lat_dirty[0] = {k: list(v) for k, v in _lat.items()}, False
        try:
            core.atomic_json_dump(_lat_file(), snap, indent=1)
        except OSError as e:
            core.log.warning(f"[watchdog] answer times not saved: {e}")


def _digest_if_due(now: float) -> None:
    t = datetime.datetime.fromtimestamp(now)
    day = t.strftime("%Y%m%d")
    if t.hour < 8 or _last["digest_day"] == day:
        return
    _last["digest_day"] = day
    since = now - 86400
    with _lock:
        fixes = [f for f in _fixes if f["ts"] >= since]
        solved = [r for r in _resolved if r["until"] >= since]
        issues = list(_issues.values())
    _digest.update(ts=now, open=len(issues), fixes=len(fixes), solved=len(solved),
                   items=[{"kind": "open", "subject": i["subject"], "parts": i["parts"]} for i in issues[:10]] + [{"kind": "fixed", "subject": f["subject"], "parts": f["parts"]} for f in fixes[:10]])
    core.log.info(f"[watchdog] digest: {len(issues)} open issues, {len(fixes)} fixes, {len(solved)} solved by themselves in the last 24 h")


def _save() -> None:
    """The rule origins and the fixes survive a restart; written only when they changed."""
    with _lock:
        snap = {"origin": [[t, c, o] for (t, c), o in core.bind_origin.items()], "fixes": list(_fixes), "digest": dict(_digest), "digest_day": _last["digest_day"], "notes": list(_notes),
                "slow": list(_slow), "gone_since": dict(_gone_since), "seen": dict(_seen)}
    text = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    if text == _last["saved"]:
        return
    try:
        core.atomic_json_dump(_state_file(), snap, indent=1, ensure_ascii=False)
        _last["saved"] = text
    except OSError as e:
        core.log.warning(f"[watchdog] state not saved: {e}")


def _load() -> None:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for t, c, o in d.get("origin") or []:
        if isinstance(o, dict) and {"src", "code", "g", "ts"} <= set(o):
            core.bind_origin.setdefault((t, c), o)
    _fixes.extend(f for f in d.get("fixes") or [] if isinstance(f, dict) and f.get("parts") and f["parts"][0][0] not in _UNCONFIRMED
                  and f.get("ts", 0) >= time.time() - HISTORY_S)
    _digest.update(d.get("digest") or {})
    _notes.extend(n for n in d.get("notes") or [] if isinstance(n, dict) and {"ts", "title", "text"} <= set(n))
    _slow.extend(x for x in d.get("slow") or [] if isinstance(x, dict) and x.get("parts") and x.get("ts", 0) >= time.time() - HISTORY_S)
    _last["digest_day"] = d.get("digest_day") or ""
    _gone_since.update({str(k): float(v) for k, v in (d.get("gone_since") or {}).items() if isinstance(v, (int, float))})
    _seen.update({str(k): [str(x) for x in v] for k, v in (d.get("seen") or {}).items() if isinstance(v, list) and len(v) == 2})
    try:
        lat = json.loads(_lat_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lat = {}
    _lat.update({str(k): [float(x) for x in v][-CONFIRM_KEEP:] for k, v in (lat.items() if isinstance(lat, dict) else ()) if isinstance(v, list)})


async def _loop():
    await asyncio.sleep(LOOP_S)                                                 # the first pass when the house has settled
    while True:
        await asyncio.to_thread(run_once)
        await asyncio.sleep(LOOP_S)


@core.app.get("/api/watchdog")
def api_watchdog(request: Request):
    core.house_token_only(request)
    now = time.time()
    with _lock:
        return {"status": status(now), "count": sum(i["level"] != "info" for i in _issues.values()), "issues": sorted(_issues.values(), key=lambda i: (i["level"] != "alert", -i["since"])),
                "fixes": list(_fixes)[:30], "resolved": list(_resolved)[:30], "digest": dict(_digest), "notes": list(_notes)[::-1][:10],
                "slow": list(_slow)[:30]}


class ResendBody(core.Body):
    key: str = Field(max_length=200)


@core.app.post("/api/watchdog/resend")
async def api_resend(body: ResendBody, request: Request):
    """The owner sends an open issue's command again ("Send again"): the newest command of that channel, as it was."""
    core.house_token_only(request)
    with _lock:
        r = (_issues.get(body.key) or {}).get("resend")
    cmd = r and _newest(r["device"], r["code"])
    if not cmd:
        raise HTTPException(404, "nothing to send again")
    await core.control(r["device"], core.ControlBody(code=r["code"], value=cmd[0], source="app"))
    return {"ok": True}


def _answers(dev: str) -> list:
    s = _settings()["confirm"]
    out = []
    for c in (core.DEVICES.get(dev) or {}).get("controls") or []:
        if str(c.get("kind") or "").startswith("sensor") or not c.get("code"):
            continue
        xs = list(_lat.get(f"{dev}/{c['code']}") or [])
        out.append({"code": c["code"], "label": c.get("label") or c["code"], "n": len(xs), "typical": sorted(xs)[len(xs) // 2] if xs else None,
                    "wait": confirm_window(dev, c["code"]), "set": s.get(f"{dev}/{c['code']}"), "toggle": bool(c.get("toggle"))})
    return out


@core.app.get("/api/watchdog/answers/{dev}")
def api_answers(dev: str, request: Request):
    """How fast each channel of a device answers its commands and how long the house waits (the device window)."""
    core.house_token_only(request)
    if dev not in core.DEVICES:
        raise HTTPException(404, "no such device")
    return {"channels": _answers(dev)}


class WaitBody(core.Body):
    code: str = Field(max_length=80)
    seconds: Optional[float] = Field(default=None, gt=0, le=3600)      # None: back to the learned wait


@core.app.put("/api/watchdog/answers/{dev}")
def api_wait(dev: str, body: WaitBody, request: Request):
    core.house_token_only(request)
    if dev not in core.DEVICES:
        raise HTTPException(404, "no such device")
    p = core.plugin_data("watchdog") / "settings.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    if not isinstance(raw.get("confirm"), dict):
        raw["confirm"] = {}
    conf = raw["confirm"]
    if body.seconds is None:
        conf.pop(f"{dev}/{body.code}", None)
    else:
        conf[f"{dev}/{body.code}"] = body.seconds
    p.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_json_dump(p, raw, indent=1)
    return {"channels": _answers(dev)}


class NoteBody(core.Body):
    title: str = Field(max_length=120)
    text: str = Field(max_length=4000)


@core.app.post("/api/watchdog/note")
def api_note(body: NoteBody, request: Request):
    """A helper of the house (the shower capture) posts what it found: shown on the card, kept (the last 20)."""
    core.house_token_only(request)
    with _lock:
        _notes.append({"ts": time.time(), "title": body.title, "text": body.text})
    core.log.info(f"[watchdog] note: {body.title}")
    return {"ok": True}


def _health() -> list:
    st = status()
    with _lock:
        n, fg = sum(i["level"] != "info" for i in _issues.values()), _issues.get("forgotten")
    rows = [core.health_row("watchdog", {"green": "ok", "yellow": "warn", "orange": "warn", "red": "bad"}[st], "Home watchdog",
                            "" if st == "green" else "{0} open issues", args=[n])]
    if fg:
        rows.append(core.health_row("forgotten", "warn" if fg["level"] == "warn" else "ok", "Forgotten devices", fg["parts"][0][0], HINT_FORGOT,
                                    args=fg["parts"][0][1], link="devices/forgotten"))
    return rows


class ForgetBody(core.Body):
    days: float = Field(ge=0, le=FORGET_DAYS_MAX)       # 0: never by itself


@core.app.get("/api/watchdog/settings")
def api_settings(request: Request):
    core.house_token_only(request)
    return {"forget_days": _settings()["forget_days"]}


@core.app.put("/api/watchdog/settings")
def api_put_settings(body: ForgetBody, request: Request):
    """Settings -> Watchdog: after how many days of absence a gone device is forgotten by itself (its system running)."""
    core.house_token_only(request)
    p = core.plugin_data("watchdog") / "settings.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    raw["forget_days"] = body.days
    p.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_json_dump(p, raw, indent=1)
    return {"forget_days": _settings()["forget_days"]}


# ---- a write to a node's settings: its sensors may say things that are not so for a while ----
def _config_watch(device: str, prefix: str, seconds: float):
    """A command to a number / switch / select of the node (anybody's: the dashboard, a tool, the watchdog's own), or its engineering-mode switch changing, makes
    its binary sensors untrusted for `seconds`: an LD2410 sees a phantom target for 10-20 s after any settings write."""
    def handler(parts, payload, retain):
        if retain or len(parts) < 4:
            return
        kind, obj, last = parts[-3], parts[-2], parts[-1]
        if (last == "command" and kind in ("number", "switch", "select")) or (last == "state" and kind == "switch" and "engineering" in obj):
            core.quiet_device(device, seconds, f"{kind}/{obj}/{last}")
    return handler


for _c in _settings()["config_quiet"]:
    core.MQTT_SUBSCRIPTIONS.extend(_c["prefix"] + k for k in ("number/+/command", "switch/+/command", "select/+/command", "switch/+/state"))
    core.MQTT_HANDLERS.append((tuple(_c["prefix"].strip("/").split("/")), _config_watch(_c["device"], _c["prefix"], _c["seconds"])))

_load()
core.STARTUP_TASKS.append(_loop)
core.HEALTH_CHECKS.append(_health)
