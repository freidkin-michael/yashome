"""voice: the house answers by voice (docs/voice.md). The TTS service client, the satellites (a microphone and/or a player each),
their cards, say/play/stop/volume (API, MQTT home/cmd/<id>/say|play|stop|volume, the stateless channels), the stop and the listening
chime on a wake and the reply to the satellite that heard (voice/heard from the voice service)."""
import io
import json
import logging
import os
import re
import tarfile
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from fastapi import Body, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import Field, ValidationError

import app as core

PLUGIN = {"name": "Voice", "version": "0.2.0", "icon": "mic", "menu_group": "devices"}
TRANSPORT = "voice"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,27}$")
_cfg: dict = dict(core.cfg_section("voice", {}))
_cfg.setdefault("satellites", [])
_config_lock = threading.RLock()     # the satellites and the voice service's settings change under it (a seed walks the satellites)
core.SETTINGS_SECTIONS["voice"] = lambda: _cfg
_stats: dict = {}                    # satellite id -> {wakes, commands, errors, last_wake, playing, what}
_subscribed: set = set()


def _tts_cfg() -> dict:
    """A TTS service on another computer of the house (settings.json voice.tts {host, url}, set by the plug-in computers): the
    house reaches it through the helper's ssh tunnel (url, no key: it listens on that computer's loopback only). None = the
    house's own `tts` container on the compose network (VOICE_TTS_KEY). Either way the satellites get its files from the
    dashboard's port (tts_files)."""
    t = _cfg.get("tts")
    return t if isinstance(t, dict) and t.get("url") and t.get("host") else {}


def _url() -> str:
    return (_tts_cfg().get("url") or "http://tts:8092").rstrip("/")


def public_base() -> str:
    """The address the satellites fetch spoken files from: the dashboard's own port (BIND_ADDRESS:DASHBOARD_PORT), which hands
    them on from the TTS service wherever it runs (tts_files); empty without BIND_ADDRESS. The TTS publishes no port of its own."""
    bind = (os.environ.get("BIND_ADDRESS") or "").strip()
    if not bind or bind in ("0.0.0.0", "::"):
        return ""
    return f"http://{bind}:{os.environ.get('DASHBOARD_PORT') or 8765}/plugins/voice/tts"


def _tts_key() -> str:
    return "" if _tts_cfg() else os.environ.get("VOICE_TTS_KEY", "")


def use_tts(host: str = "", url: str = "") -> None:
    """The TTS service the house speaks with: another computer's (its host, the tunnel's url), or "" = its own again."""
    with _config_lock:
        if host:
            _cfg["tts"] = {"host": host, "url": url.rstrip("/")}
        else:
            _cfg.pop("tts", None)
        core.save_settings()
    core.log.info(f"[voice] TTS service: {host or 'this house'}")


def tts_in_use() -> str:
    """The computer whose TTS service is in use ("" = the house's own)."""
    return _tts_cfg().get("host", "")


def tts_files(kind: str, name: str):
    """GET /plugins/voice/tts/{a|s}/<name>.wav: a spoken file of the TTS service (the house's own container, or another computer's
    on its loopback), for the satellites: the only way they reach it. Open like the service's own: a file's random name is its
    secret, it lives 10 min."""
    if kind not in ("a", "s") or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise HTTPException(404, "not found")
    try:
        with urllib.request.urlopen(f"{_url()}/{kind}/{name}.wav", timeout=10) as r:
            return Response(r.read(), media_type="audio/wav", headers={"Cache-Control": "no-store"})
    except urllib.error.HTTPError as e:
        raise HTTPException(404 if e.code == 404 else 502, "gone" if e.code == 404 else "tts")
    except OSError:
        raise HTTPException(503, "the TTS service is not reachable")


core.app.add_api_route("/plugins/voice/tts/{kind}/{name}.wav", tts_files, methods=["GET"])
core.open_route(tts_files)


def tts_health(timeout: float = 3.0) -> dict:
    try:
        with urllib.request.urlopen(_url() + "/health", timeout=timeout) as r:
            return json.loads(r.read())
    except (OSError, ValueError) as e:
        return {"ok": False, "error": type(e).__name__}


def say_url(text: str, lang: str = None, voice: str = None, timeout: float = 20.0, lead_in: bool = False) -> dict:
    """Synthesize `text`; the answer has `url`, the full address a satellite fetches the WAV from. Raises RuntimeError with a reason."""
    key = _tts_key()
    if not key and not _tts_cfg():
        raise RuntimeError("VOICE_TTS_KEY is not set (make install generates it)")
    if not public_base():
        raise RuntimeError("BIND_ADDRESS is not set: the satellites do not know where to fetch the sound")
    body = json.dumps({"text": text, "lang": lang, "voice": voice, "lead_in": lead_in}).encode()
    req = urllib.request.Request(_url() + "/say", data=body, method="POST", headers={"X-Voice-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"tts answered {e.code}") from e
    except (OSError, ValueError) as e:
        raise RuntimeError(f"tts is not reachable: {type(e).__name__}") from e
    out["url"] = public_base() + out["url"]
    return out


def sound_url(name: str, s: dict = None) -> str:
    """A confirmation sound of the service (listening, done, error, thinking); the variant with the lead-in noise for a satellite that needs it."""
    if name not in ("listening", "done", "error", "thinking"):
        raise ValueError("no such sound")
    return f"{public_base()}/s/{name}{'_lead' if s and s.get('lead_in') else ''}.wav"


# ---- the satellites: one record each, a card per role ----
RESERVED = {"home", "homeassistant", "zigbee2mqtt", "panel", "provision", "voice"}


def _taken(node: str) -> str:
    """Why this node name may not be a satellite's: a namespace of the house or of a plug-in, the first topic level of a device the
    house already has, or another satellite's; '' when it is free (a satellite's own topics do not count against itself)."""
    mine = {s["node"] for s in _sats()}
    names = set(RESERVED) | set(core.MQTT_NAMESPACES) | set(core.NODE_SUBTOPICS)
    names |= {t.split("/", 1)[0] for t in core.MQTT_SUBSCRIPTIONS} | {str(p[0]) for p, _fn in core.MQTT_HANDLERS if p}
    if node in mine:
        return f"{node!r} is another satellite's"
    if node in names:
        return f"{node!r} is a topic of the house or of a plug-in"
    for d in list(core.DEVICES.values()):
        tops = [d.get("availability_topic")] + [c.get(k) for c in d.get("controls") or [] if isinstance(c, dict) for k in ("state_topic", "command_topic")]
        if d.get("transport", "mqtt") == "mqtt" and (d.get("esphome_node") == node or any(isinstance(t, str) and t.split("/", 1)[0] == node for t in tops)):
            return f"{node!r} is the name of a device the house already has"
    return ""


def _sats() -> list:
    return [s for s in _cfg["satellites"] if isinstance(s, dict) and ID_RE.match(str(s.get("id", "")))]


def _map_room(s: dict) -> dict:
    """The room one of the satellite's cards or its tablet serves (its Serves pick, else where it stands on the map), else {}."""
    ids = {s["id"] + "_mic", s["id"] + "_spk"} | ({s["panel"]} if s.get("panel") else set())
    return next((r for r in core.get_rooms() if ids & set(core.served_devices(r["id"]))), {})


def _room(s: dict) -> str:
    """Where the satellite stands: the map's room id, else the room it was saved with."""
    return _map_room(s).get("id") or s.get("room") or ""


def _room_name(s: dict) -> str:
    """The name of the satellite's room, for the dashboard."""
    rid = _room(s)
    return next((r.get("name") or rid for r in core.get_rooms() if rid in (r.get("id"), r.get("name"))), rid)


def _room_keys(s: dict) -> set:
    r = _map_room(s)
    return {k for k in (r.get("id"), r.get("name"), s.get("room")) if k}


def _room_refs(rid: str) -> list:
    """The satellites that stand in the room (by its id or its name)."""
    keys = {rid} | {r.get("name") for r in core.get_rooms() if r.get("id") == rid}
    return [{"kind": "satellite", "name": s.get("name") or s["id"]} for s in _sats() if _room_keys(s) & keys]


core.ROOM_REFS.append(_room_refs)


def _sat(sid: str):
    return next((s for s in _sats() if s["id"] == sid or sid in (s["id"] + "_mic", s["id"] + "_spk")), None)


def _stat(sid: str) -> dict:
    return _stats.setdefault(sid, {"wakes": 0, "commands": 0, "errors": 0, "last_wake": None, "playing": False, "what": ""})


def _pub(topic: str, payload) -> None:
    try:
        core.mqtt_pub(core.mqtt_client, topic, payload)
    except Exception as e:                                  # noqa: BLE001  no broker: the caller says 503
        raise HTTPException(503, f"MQTT is not available: {e}")


def _send(s: dict, suffix: str, payload) -> None:
    """A command for a satellite: an ESP node gets <node>/<suffix>; a tablet (its `panel` is the device id) gets the same as a JSON line
    on panel/<id>/voice/cmd, which the panels plug-in hands to the tablet's long-poll."""
    if s.get("panel"):
        _pub(f"panel/{s['panel']}/voice/cmd", json.dumps({"t": suffix, "p": str(payload)}))
    else:
        _pub(f"{s['node']}/{suffix}", payload)


def panel_node(devid: str) -> str:
    """The node name of a tablet satellite; the panels plug-in publishes the tablet's events under the same name."""
    return "panel-" + re.sub(r"[^a-z0-9_-]+", "-", devid.lower()).strip("-")


SOUND_MAX_S = 10.0               # a sound the node never reported finished is taken as finished after this
LOW = ("listening", "thinking")  # sounds that never wait and never cut: they are dropped while anything plays or waits


def _send_now(s: dict, st: dict, url: str) -> None:
    st.update(sound_active=True, sent_at=time.time(), heard_playing=False)
    _send(s, "say/url", url)


def _enqueue(s: dict, url: str, kind: str = "reply") -> None:
    """A sound of the voice pipeline for a satellite, one at a time: it never cuts a running reply. A "listening" / "thinking" sound is dropped
    when something plays or waits; a reply or a result chime queues and goes out when the node reports idle."""
    st = _stat(s["id"])
    if st.get("sound_active") and time.time() - st.get("sent_at", 0) > SOUND_MAX_S:
        st["sound_active"] = False
    if st.get("sound_active"):
        if kind not in LOW:
            st.setdefault("queue", []).append(url)
        return
    if kind in LOW and st.get("queue"):
        return
    _send_now(s, st, url)


def _next_sound(s: dict, st: dict) -> None:
    """The node reported idle after a sound: the next queued one goes out."""
    if st.get("queue"):
        _send_now(s, st, st["queue"].pop(0))
    else:
        st["sound_active"] = False


def _devices(s: dict) -> list:
    """The cards of a satellite: `<id>_mic` for the microphone, `<id>_spk` for the player."""
    out = []
    if s.get("phone"):                                                       # one card: when it was last talked to
        return [{"id": s["id"] + "_mic", "name": (s.get("name") or s["id"]) + " (mic)", "category": "voice", "transport": TRANSPORT,
                 "room": s.get("room"), "voice_sat": s["id"], "voice_role": "mic",
                 "controls": [{"kind": "sensor_text", "code": "last_wake", "label": "Last wake", "ro": True}]}]
    if s.get("mic"):
        out.append({"id": s["id"] + "_mic", "name": (s.get("name") or s["id"]) + " (mic)", "category": "voice", "transport": TRANSPORT,
                    "room": s.get("room"), "voice_sat": s["id"], "voice_role": "mic",
                    "controls": [{"kind": "switch", "code": "listen", "label": "Listening", "text_on": "On", "text_off": "Muted"},
                                 {"kind": "sensor_text", "code": "last_wake", "label": "Last wake", "ro": True},
                                 {"kind": "sensor_text", "code": "bg_record", "label": "Background recording", "ro": True},
                                 {"kind": "voice_press", "code": "bg_cancel", "label": "Cancel background recording", "advanced": True}]})
    if s.get("player"):
        out.append({"id": s["id"] + "_spk", "name": (s.get("name") or s["id"]) + " (speaker)", "category": "voice", "transport": TRANSPORT,
                    "room": s.get("room"), "voice_sat": s["id"], "voice_role": "spk",
                    "controls": [{"kind": "sensor_text", "code": "now", "label": "Now", "ro": True},
                                 {"kind": "bright", "code": "volume", "label": "Volume", "min": 0, "max": 100},
                                 {"kind": "voice_press", "code": "stop", "label": "Stop"},
                                 {"kind": "voice_press", "code": "test", "label": "Test sound"},
                                 {"kind": "voice_say", "code": "say", "label": "Say", "advanced": True},
                                 {"kind": "voice_play", "code": "play", "label": "Play", "advanced": True}]})
    return out


def _sync_devices() -> None:
    want = {d["id"]: d for s in _sats() for d in _devices(s)}
    for did, d in want.items():
        (core.update_device if did in core.DEVICES else core.register_device)(d)
    for did in [k for k, d in list(core.DEVICES.items()) if d.get("transport") == TRANSPORT and k not in want]:
        core.remove_device(did, TRANSPORT)
    _subscribe()


def _topics(s: dict) -> list:
    n = s["node"]
    return [f"{n}/status", f"{n}/wake", f"{n}/player/state", f"{n}/listen/state", f"{n}/mute/state",
            s.get("volume_state") or f"{n}/volume/state"]


def _subscribe() -> None:
    for s in [x for x in _sats() if not x.get("phone")]:
        for t in _topics(s):
            if t not in _subscribed:
                _subscribed.add(t)
                if t not in core.MQTT_SUBSCRIPTIONS:
                    core.MQTT_SUBSCRIPTIONS.append(t)
                if core.mqtt_client is not None:
                    try:
                        core.mqtt_client.subscribe(t)
                    except Exception:                       # noqa: BLE001  the next connect subscribes it
                        pass
        if (s["node"],) not in [p for p, _ in core.MQTT_HANDLERS]:
            core.MQTT_HANDLERS.append(((s["node"],), _on_node))


def _set(sat_id: str, role: str, report: bool = False, **vals) -> None:
    """Store a value of a satellite card. report=True: the node itself said something, so the core ends an offline / waiting state; otherwise it is a
    word about the node (its status line), an explicit availability that cannot end a waiting state by itself."""
    did = f"{sat_id}_{role}"
    if did not in core.DEVICES:
        return
    st = core.status_copy(did, {"online": False, "values": {}, "raw": {}})
    online = vals.pop("online", None)
    if online is not None:
        st["online"] = online
    st["values"].update({k: v for k, v in vals.items() if v is not None})
    st["last_seen"] = time.time()
    if report:
        core.store_status(did, st)
    else:
        core.store_status_exact(did, st)                      # exact: an offline satellite stays offline


def _now_text(sid: str) -> str:
    st = _stat(sid)
    return ("playing: " + st["what"]).strip(": ") if st["playing"] else "idle"


ROOM_WINDOW_S = 1.5
_room_wake: dict = {}            # room -> (time, satellite id) of the first wake of a burst


def _room_first(s: dict):
    """The id of the satellite of this room that woke within ROOM_WINDOW_S before this one (this one is a duplicate), else None
    (this one is the first: it is remembered). A satellite without a room is its own room."""
    room, now = _room(s), time.time()
    if not room:
        return None
    t, who = _room_wake.get(room, (0.0, ""))
    if who and who != s["id"] and now - t < ROOM_WINDOW_S:
        return who
    _room_wake[room] = (now, s["id"])
    return None


def _on_node(parts, payload, retain) -> None:
    """The topics of a registered satellite: its status, wake, player, listen, mute and volume."""
    s = next((x for x in _sats() if x["node"] == parts[0]), None)
    if s is None:
        return
    topic = "/".join(parts)
    text = payload.decode("utf-8", "replace").strip() if isinstance(payload, (bytes, bytearray)) else str(payload).strip()
    st = _stat(s["id"])
    if not retain and topic != f"{s['node']}/status":          # a node that talks is on: its cards do not wait for the status line (a
        for role in ("mic", "spk"):                            # house restarted while the node reconnected may never see "online")
            _set(s["id"], role, online=True, report=True)
    if topic == f"{s['node']}/status":
        for role in ("mic", "spk"):                            # "online" is the node speaking: a report; "offline" is a word about it
            _set(s["id"], role, online=text == "online", report=text == "online")
    elif topic == f"{s['node']}/wake" and not retain:
        st["wakes"] += 1
        st["last_wake"] = time.strftime("%H:%M")
        _set(s["id"], "mic", last_wake=st["last_wake"])
        first = _room_first(s)
        if first is not None:
            st["dups"] = st.get("dups", 0) + 1
            core.log.info(f"[voice] wake of {s['id']} ignored: {first} of room {s.get('room')!r} woke first")
        elif s.get("player"):                                  # the house, not the node, stops what plays and says "I listen"
            try:
                st["queue"] = []                               # a new wake: what waited for the last phrase is void
                if st["playing"]:                              # a stop only when something plays: no needless work for the node's audio bus
                    st["sound_active"] = False
                    _send(s, "stop/command", "PRESS")
                elif s.get("wake_chime") and s.get("chime", True) and public_base():          # no sound on a wake by default: the command follows at once
                    st["sound_active"] = False
                    _enqueue(s, sound_url("listening", s), "listening")
            except HTTPException as e:
                core.log.warning(f"[voice] wake of {s['id']}: {e.detail}")
    elif topic == f"{s['node']}/player/state":
        st["playing"] = text == "playing"
        _set(s["id"], "spk", now=_now_text(s["id"]))
        _question_progress(s, st, st["playing"])
        if text == "playing":
            st["heard_playing"] = True
        elif st.get("heard_playing"):                          # a sound ended: the next waiting one goes out
            st["heard_playing"] = False
            _next_sound(s, st)
    elif topic == f"{s['node']}/listen/state":
        _set(s["id"], "mic", listen=text.upper() == "ON")
    elif topic == (s.get("volume_state") or f"{s['node']}/volume/state"):
        try:
            _set(s["id"], "spk", volume=round(float(text)))
        except ValueError:
            pass


# ---- the phone satellite is "on" while its app reports (a pull, a network report) or talks (push-to-talk), for an hour after the last sign ----
PHONE_SILENT_S = 3600


def on_phone_seen(parts, payload, retain) -> None:
    """voice/phone/<uid>/seen (from the family plug-in): the app of that phone is alive."""
    if retain or len(parts) != 4 or parts[3] != "seen":
        return
    s = _sat("phone_" + parts[2])
    if s is not None and s.get("phone"):
        _stat(s["id"])["phone_seen"] = time.time()
        _set(s["id"], "mic", online=True, report=True)


def _phone_check(now: float = None) -> None:
    """A phone satellite whose app has been silent for PHONE_SILENT_S is offline."""
    now = now or time.time()
    for s in _sats():
        st = _stat(s["id"])
        if s.get("phone") and st.get("phone_seen") and now - st["phone_seen"] > PHONE_SILENT_S:
            st["phone_seen"] = 0
            _set(s["id"], "mic", online=False)


def _phone_watch() -> None:
    while True:
        time.sleep(30)
        try:
            _phone_check()
        except Exception as e:                                  # noqa: BLE001  the watch goes on
            core.log.warning(f"[voice] phone watch: {e}")


core.MQTT_SUBSCRIPTIONS.append("voice/phone/+/seen")
core.MQTT_HANDLERS.append((("voice", "phone"), on_phone_seen))
threading.Thread(target=_phone_watch, daemon=True, name="voice-phone-watch").start()


# ---- a question of the house (an ask-back) must be HEARD: played, then the mic opens once; else it is cancelled ----
QUESTION_PLAY_S = 6.0            # the node must start playing the question within this time
LISTEN_ONCE_S = 6                # seconds the mic stays open for the answer


def _question_progress(s: dict, st: dict, playing: bool) -> None:
    """The player state of a satellite that was just asked to speak a question: playing = it was heard to start; when it ends, the mic opens once."""
    if not st.get("q_at") or time.time() - st["q_at"] > 60:
        return
    if playing:
        st["q_played"] = True
    elif st.get("q_played"):
        st["q_at"] = 0
        if s.get("listen_once") and not s.get("panel") and not s.get("phone"):
            try:
                _send(s, "listen_once/command", str(LISTEN_ONCE_S))
            except HTTPException as e:
                core.log.warning(f"[voice] listen_once of {s['id']}: {e.detail}")


def _question_check(sid: str, ip: str) -> None:
    st = _stat(sid)
    if st.get("q_at") and not st.get("q_played"):
        st["q_at"] = 0
        core.log.warning(f"[voice] {sid} did not play the question: it is cancelled")
        try:
            _pub("voice/ask_failed", json.dumps({"ip": ip}))
        except HTTPException:
            pass


def _track_question(s: dict, ip: str) -> None:
    st = _stat(s["id"])
    st.update(q_at=time.time(), q_played=False)
    t = threading.Timer(QUESTION_PLAY_S, _question_check, (s["id"], ip))
    t.daemon = True
    t.start()


# ---- what a satellite is told ----
def _players(target: str) -> list:
    """Players named by `target`: a satellite id, a room, or `all`."""
    ps = [s for s in _sats() if s.get("player")]
    if target in ("all", "*"):
        return ps
    return [s for s in ps if s["id"] == target or target in _room_keys(s)]


def play(s: dict, url: str, what: str = "") -> None:
    st = _stat(s["id"])
    st["what"] = what[:40]
    st["queue"] = []                                           # an explicit play (the API, a test) cuts and replaces what was waiting
    _send_now(s, st, url)


def speak(s: dict, text: str, lang: str = None, voice: str = None, queued: bool = False) -> dict:
    try:
        out = say_url(text, lang, voice or s.get("voice"), lead_in=bool(s.get("lead_in")))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    if queued:                                                 # a reply of the voice pipeline waits for what plays, it never cuts it
        _stat(s["id"])["what"] = text[:40]
        _enqueue(s, out["url"], "reply")
    else:
        play(s, out["url"], text)
    return out


def stop(s: dict) -> None:
    st = _stat(s["id"])
    st["queue"], st["sound_active"] = [], False
    _send(s, "stop/command", "PRESS")


def set_volume(s: dict, value: int) -> None:
    _send(s, "volume/set", str(max(0, min(100, int(value)))))


def _sat_of(cfg: dict) -> dict:
    s = _sat(cfg.get("voice_sat") or "")
    if s is None:
        raise HTTPException(404, "no such satellite")
    return s


def _press(cfg: dict, ctl: dict, val) -> dict:
    if val is False or val == 0 or (isinstance(val, str) and val.strip().lower() in ("off", "false", "0")):
        raise HTTPException(400, "a button is pressed with true")
    s = _sat_of(cfg)
    if ctl["code"] == "bg_cancel":
        _record.cancel_all()
    elif ctl["code"] == "stop":
        stop(s)
    elif public_base():
        play(s, sound_url("done", s), "test")
    else:
        raise HTTPException(503, "BIND_ADDRESS is not set: the satellites do not know where to fetch the sound")
    return {"ok": True}


def _say(cfg: dict, ctl: dict, val) -> dict:
    text = str(val or "").strip()
    if not text or len(text) > 500:
        raise HTTPException(400, "say: 1..500 characters")
    return {"url": speak(_sat_of(cfg), text)["url"]}


def _playurl(cfg: dict, ctl: dict, val) -> dict:
    url = str(val or "").strip()
    if not re.match(r"^https?://[^\s]{1,400}$", url):
        raise HTTPException(400, "play: an http(s) URL")
    play(_sat_of(cfg), url, "audio")
    return {"ok": True}


def _transport(cfg: dict, code: str, val, kind=None):
    s = _sat_of(cfg)
    if code == "volume":
        set_volume(s, int(float(val)))
    elif code == "listen":
        on = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "on")
        _send(s, "listen/command", "ON" if on else "OFF")
    else:
        raise HTTPException(400, f"no channel {code!r}")


core.TRANSPORTS[TRANSPORT] = _transport
core.DEVICE_FIELDS.extend(["voice_sat", "voice_role"])               # the dashboard needs them for the window section
core.CONTROL_KINDS["voice_press"] = _press
core.CONTROL_KINDS["voice_say"] = _say
core.CONTROL_KINDS["voice_play"] = _playurl


# ---- the reply: the voice service says who heard what and how it went ----
def on_heard(parts, payload, retain) -> None:
    """voice/heard {"ip": peer address, "ok": bool, "reply": text?, "sound": name?, "ask": bool?}: the satellite of that address gets the reply or a chime.
    "ask": the reply is a question: it must be heard (played), the mic opens once after it (listen_once), else voice/ask_failed cancels it.
    "sound" alone (thinking) only plays it, nothing is counted; a reply is spoken whether the command worked or not ("I did not understand"),
    unless the satellite is quiet_fail: then a command not understood (no question) plays nothing."""
    if retain:
        return
    try:
        msg = json.loads(payload)
        ip = str(msg.get("ip") or "")
    except (ValueError, AttributeError):
        return
    s = next((x for x in _sats() if x.get("peer") == ip), None)
    if s is None:
        return
    st = _stat(s["id"])
    sound = str(msg.get("sound") or "")
    if not sound:
        st["commands" if msg.get("ok") else "errors"] += 1
    if not s.get("player") or not public_base():
        return
    reply = str(msg.get("reply") or "").strip()
    if not sound and not msg.get("ok") and not msg.get("ask") and s.get("quiet_fail"):
        return                                                             # "Silent when not understood": a false wake (a TV) says nothing
    try:
        if sound:
            if s.get("chime", True):
                _enqueue(s, sound_url(sound, s), sound)
        elif reply:
            speak(s, reply, queued=True)
            if msg.get("ask"):
                _track_question(s, ip)
        elif msg.get("quiet"):
            pass                                                           # a confirm beep already said "understood": no second sound
        else:
            _enqueue(s, sound_url("done" if msg.get("ok") else "error", s), "chime")
    except (HTTPException, ValueError) as e:
        core.log.warning(f"[voice] reply to {s['id']}: {getattr(e, 'detail', e)}")


core.MQTT_SUBSCRIPTIONS.append("voice/heard")
core.MQTT_HANDLERS.append((("voice", "heard"), on_heard))


# ---- the API ----
class SatBody(core.Body):
    wake_chime: bool = False                                                   # play "listening" on a wake (off by default: the command follows the wake word at once)
    chime: bool = True                                                         # play "thinking" / "done" / "error" (off: the node cannot play while it streams)
    lead_in: bool = False                                                      # a faint noise before every sound: the node's codec path mutes on digital zeros (docs/voice.md)
    listen_once: bool = False                                                  # the node takes respeaker-style <node>/listen_once/command (an answer without a wake word)
    quiet_fail: bool = False                                                   # nothing is played when a command was not understood (no reply, no chime)
    need_wake: Optional[bool] = None                                           # acts only on a phrase that holds the wake word (a tablet that wakes by mistake); absent = as it was
    phone: bool = False                                                        # a phone with push-to-talk: no node, no topics
    name: str = Field(default="", max_length=40)
    node: str = Field(default="", pattern=r"^([a-z0-9][a-z0-9_-]{0,31})?$")      # a tablet's node is made from its `panel`
    panel: str = Field(default="", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$|^$")   # the tablet's device id (panels plug-in)
    mic: bool = False
    player: bool = True
    room: str = Field(default="", max_length=32)
    peer: str = Field(default="", max_length=45)
    voice: str = Field(default="", max_length=60)
    volume_state: str = Field(default="", max_length=120)


@core.app.get("/api/voice/status")
def voice_status(request: Request):
    core.house_token_only(request)
    return {"tts": tts_health(), "public_url": public_base() or None, "key": bool(os.environ.get("VOICE_TTS_KEY"))}


@core.app.get("/api/voice/satellites")
def satellites(request: Request):
    core.house_token_only(request)
    cards = lambda s: {r: core.DEVICES[s["id"] + "_" + r].get("name") for r in ("mic", "spk") if s["id"] + "_" + r in core.DEVICES}
    return [{**s, "stats": _stat(s["id"]), "room_name": _room_name(s), "cards": cards(s)} for s in _sats()]   # shown names: renamed cards, the map's room


@core.app.put("/api/voice/satellites/{sid}")
def put_satellite(sid: str, body: SatBody, request: Request):
    core.house_token_only(request)
    if not ID_RE.match(sid):
        raise HTTPException(400, "id: a lowercase letter, then letters, digits, - or _ (28 at most)")
    if not (body.mic or body.player):
        raise HTTPException(400, "a satellite is a microphone, a player or both")
    if (sid + "_mic" in core.DEVICES or sid + "_spk" in core.DEVICES or sid in core.DEVICES) and not _sat(sid):
        raise HTTPException(409, "the id is taken by a device")
    if (body.name or "").strip():
        body.name = core.check_name(body.name, "satellite", [s.get("name") for s in _sats() if s.get("id") != sid], most=40)
    if body.phone:
        body.node, body.mic, body.player = "phone-" + sid.replace("_", "-"), True, False
    elif body.panel:
        body.node = panel_node(body.panel)
    elif not body.node:
        raise HTTPException(400, "node: the satellite's topic name (or `panel`: a tablet's device id)")
    with _config_lock:
        prev = _sat(sid)
        if body.need_wake is None:                                             # left out: kept; a new satellite at an address the env named needs it
            body.need_wake = bool(prev.get("need_wake")) if prev else body.peer in (_cfg["config"].get("need_wake_env") or ())
        if not prev or prev["node"] != body.node:
            why = _taken(body.node)
            if why:
                raise HTTPException(409, why)
        rec = {"id": sid, **{k: v for k, v in body.model_dump().items() if v not in ("", None)}}
        _cfg["satellites"] = [s for s in _cfg["satellites"] if not (isinstance(s, dict) and s.get("id") == sid)] + [rec]
        _sync_devices()
        core.save_settings()
    return rec


@core.app.delete("/api/voice/satellites/{sid}")
def delete_satellite(sid: str, request: Request):
    core.house_token_only(request)
    with _config_lock:
        if _sat(sid) is None:
            raise HTTPException(404, "no such satellite")
        _cfg["satellites"] = [s for s in _cfg["satellites"] if not (isinstance(s, dict) and s.get("id") == sid)]
        _sync_devices()
        core.save_settings()
    return {"ok": True}


# ---- the voice service's settings (Settings -> Voice): voice-vad reads GET /api/voice/config every 10 s. A field never set
# is absent and voice-vad keeps its own value; its first read seeds them all (PUT ?seed=1), so the page shows what runs. ----
_cfg.setdefault("config", {})
MODEL_RE = r"^[A-Za-z0-9._:/-]{0,80}$"
LETTER = "a-zA-Z\u0430-\u044f\u0410-\u042f\u0451\u0401"                     # i18n: Latin and Cyrillic letters, what voice-vad matches, on both sides
WORD_RE = f"^[{LETTER}]+$"
VARIANT_RE = f"[{LETTER}]{{3,20}}\\*|[{LETTER}]{{2,20}}"


def max_wake_dist(word: str) -> int:
    """How many letters a heard word may be off the wake word: more on a short word would wake on almost anything (7 letters -> 3)."""
    return max(0, min(3, (len(word) - 1) // 2))


class ConfigBody(core.Body):
    wake: Optional[str] = Field(default=None, min_length=2, max_length=40, pattern=WORD_RE)   # the wake word: one word
    wake_maxdist: Optional[int] = Field(default=None, ge=0, le=3)       # letters a heard word may differ by (0 = exact), at most max_wake_dist
    wake_prefix: Optional[str] = Field(default=None, max_length=20, pattern=f"^([{LETTER}]{{3,}})?$")   # a heard word starting so wakes ("" = off)
    wake_variants: Optional[list[str]] = Field(default=None, max_length=40)   # how the recogniser also hears it; "comput*" = any ending
    llm: Optional[bool] = None                                           # smart answers: the model takes what no phrase rule understood
    llm_model: Optional[str] = Field(default=None, pattern=MODEL_RE)
    big_model: Optional[str] = Field(default=None, pattern=MODEL_RE)
    llm_timeout: Optional[float] = Field(default=None, ge=1, le=60)
    confirm_all_off: Optional[bool] = None                               # "turn everything off" from the model asks first
    ask_ttl: Optional[float] = Field(default=None, ge=3, le=120)         # seconds a question waits for yes / no
    need_wake_peers: Optional[list[str]] = None                          # seed only: the addresses that need the wake word


def _voice_config() -> dict:
    return {**{k: v for k, v in _cfg["config"].items() if k != "need_wake_env"}, "lang": core.house_lang(),
            "need_wake": sorted(s["peer"] for s in _sats() if s.get("need_wake") and s.get("peer"))}


CONFIG_KEYS = set(ConfigBody.model_fields) - {"need_wake_peers"} | {"seeded", "need_wake_env"}
for _k in set(_cfg["config"]) - CONFIG_KEYS:                                  # a field an older version stored (big_timeout): gone
    _cfg["config"].pop(_k)


def _check_wake(conf: dict, seed: bool) -> None:
    """The variants are words (a stem ends in *); how exact fits the wake word's length. A seed is fitted, a page value refused."""
    if seed and "wake_variants" in conf:                                       # a seed without them keeps voice-vad's own list
        conf["wake_variants"] = [v for v in conf["wake_variants"] if re.fullmatch(VARIANT_RE, v)]
    for v in conf.get("wake_variants") or ():
        if not re.fullmatch(VARIANT_RE, v):                                    # a stem needs 3 letters before its *: 2 would wake on many words
            raise HTTPException(400, f"wake_variants: {v!r} is not a word (a stem ends in *)")
    top = max_wake_dist(conf.get("wake") or "")
    if conf.get("wake") and conf.get("wake_maxdist", 0) > top:
        if not seed:
            raise HTTPException(400, f"wake_maxdist: at most {top} for a word of {len(conf['wake'])} letters")
        conf["wake_maxdist"] = top


@core.app.get("/api/voice/config")
def get_voice_config(request: Request):
    core.house_token_only(request)
    return _voice_config()


def _seed_body(raw: dict) -> ConfigBody:
    """A seed is never refused (the page would stay locked): each field is checked on its own, one that does not fit is dropped and logged."""
    ok, bad = {}, []
    for k, v in raw.items():
        if k not in ConfigBody.model_fields:
            continue
        try:
            ConfigBody.model_validate({k: v})
            ok[k] = v
        except ValidationError:
            if k == "wake_variants" and isinstance(v, list):                  # the variants one by one
                keep = [x for x in v if isinstance(x, str) and _variant_ok(x)]
                ok[k] = keep[:40]
                bad.append(f"wake_variants: {len(v) - len(ok[k])} dropped")
            else:
                bad.append(f"{k}={str(v)[:40]!r} dropped")
    if bad:
        core.log.warning(f"[voice] the seed of the voice service did not fit the page: {'; '.join(bad)}")
    return ConfigBody.model_validate(ok)


def _variant_ok(v: str) -> bool:
    try:
        return bool(re.fullmatch(VARIANT_RE, v)) and ConfigBody.model_validate({"wake_variants": [v]}) is not None
    except ValidationError:
        return False


@core.app.put("/api/voice/config")
def put_voice_config(request: Request, raw: dict = Body(...), seed: bool = False):
    """A change from the page; seed=1 (voice-vad's first read) only fills fields never set, and only once."""
    core.house_token_only(request)
    if seed:
        body = _seed_body(raw)
    else:
        try:
            body = ConfigBody.model_validate(raw)
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False, include_context=False, include_input=False))
    vals = {k: v for k, v in body.model_dump().items() if v is not None and k != "need_wake_peers"}
    if "wake_variants" in vals:
        vals["wake_variants"] = list(dict.fromkeys(v.strip().lower() for v in vals["wake_variants"] if v.strip()))
    with _config_lock:
        conf = _cfg["config"]
        if seed:
            if conf.get("seeded"):
                return _voice_config()
            peers = sorted({str(p) for p in body.need_wake_peers or ()})
            for s in _sats():
                if s.get("peer") in peers:
                    s["need_wake"] = True
            vals = {**vals, **conf, "seeded": True, "need_wake_env": peers}
        elif not conf.get("seeded"):
            raise HTTPException(409, "the voice service has not reported its settings yet: wait for it, then save")
        new = {**conf, **vals}
        _check_wake(new, seed)
        conf.clear()
        conf.update(new)
        core.save_settings()
        return _voice_config()


class _QuietConfigPoll(logging.Filter):
    """voice-vad reads its settings every 10 s: a GET that went fine is no access-log line (8.6k a day otherwise)."""

    def filter(self, record):
        a = record.args if isinstance(record.args, tuple) else ()
        return not (len(a) >= 5 and a[1] == "GET" and str(a[2]).startswith("/api/voice/config") and a[4] == 200)


if not any(type(f).__name__ == "_QuietConfigPoll" for f in logging.getLogger("uvicorn.access").filters):
    logging.getLogger("uvicorn.access").addFilter(_QuietConfigPoll())


@core.app.post("/api/voice/models")
def voice_models(request: Request):
    """The models the voice service's Ollama hosts have: {"small": [...], "big": [...]} (empty when one is off)."""
    core.house_token_only(request)
    return _vad_post("/models", {}, timeout=8.0)


class SayBody(core.Body):
    text: str = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=40)
    lang: str = None
    voice: str = None


class PlayBody(core.Body):
    url: str = Field(pattern=r"^https?://[^\s]{1,400}$")
    target: str = Field(min_length=1, max_length=40)


class TargetBody(core.Body):
    target: str = Field(min_length=1, max_length=40)


class VolumeBody(TargetBody):
    value: int = Field(ge=0, le=100)


def _need(target: str) -> list:
    ps = _players(target)
    if not ps:
        raise HTTPException(404, f"no player named {target!r} (a satellite id, a room or all)")
    return ps


@core.app.post("/api/voice/say")
def api_say(body: SayBody, request: Request):
    core.house_token_only(request)
    ps = _need(body.target)
    out = say_url_checked(body)
    for s in ps:
        play(s, out["url"], body.text)
    return {"ok": True, "players": [s["id"] for s in ps], "voice": out.get("voice"), "seconds": out.get("seconds"), "ms": out.get("ms")}


def say_url_checked(body: SayBody) -> dict:
    try:
        return say_url(body.text, body.lang, body.voice)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@core.app.post("/api/voice/play")
def api_play(body: PlayBody, request: Request):
    core.house_token_only(request)
    ps = _need(body.target)
    for s in ps:
        play(s, body.url, "audio")
    return {"ok": True, "players": [s["id"] for s in ps]}


@core.app.post("/api/voice/stop")
def api_stop(body: TargetBody, request: Request):
    core.house_token_only(request)
    ps = _need(body.target)
    for s in ps:
        stop(s)
    return {"ok": True, "players": [s["id"] for s in ps]}


@core.app.post("/api/voice/volume")
def api_volume(body: VolumeBody, request: Request):
    core.house_token_only(request)
    ps = _need(body.target)
    for s in ps:
        set_volume(s, body.value)
    return {"ok": True, "players": [s["id"] for s in ps]}


# ---- "Ask the house": a typed phrase takes the same road as a spoken one after recognition (voice-vad: phrase map -> LLM -> checks) ----
def _vad_url() -> str:
    return "http://voice-vad:8093"


def _vad_post(path: str, body: dict, timeout: float = 25.0) -> dict:
    key = os.environ.get("VOICE_TTS_KEY", "")
    if not key:
        raise HTTPException(503, "VOICE_TTS_KEY is not set")
    req = urllib.request.Request(_vad_url() + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"X-Voice-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"the voice service answered {e.code}") from e
    except (OSError, ValueError) as e:
        raise HTTPException(503, f"the voice service is not reachable: {type(e).__name__}") from e


class AskBody(core.Body):
    text: str = Field(min_length=1, max_length=300)
    speak: str = Field(default="", max_length=40)             # a satellite or room that also says the answer


class AskConfirmBody(core.Body):
    token: str = Field(min_length=1, max_length=40)
    yes: bool
    speak: str = Field(default="", max_length=40)


def _say_answer(res: dict, target: str) -> None:
    text = str(res.get("spoken") or res.get("said") or "").strip()
    if target and text:
        out = say_url(text)
        for s in _need(target):
            play(s, out["url"], text)


@core.use_route
@core.app.post("/api/voice/ask")
def api_ask(body: AskBody, request: Request):
    res = _vad_post("/ask", {"text": body.text})
    try:
        _say_answer(res, body.speak)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return res


@core.use_route
@core.app.post("/api/voice/ask/confirm")
def api_ask_confirm(body: AskConfirmBody, request: Request):
    res = _vad_post("/confirm", {"token": body.token, "yes": body.yes})
    try:
        _say_answer(res, body.speak)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return res


class ChatBody(core.Body):
    text: str = Field(min_length=1, max_length=300)
    history: list = Field(default_factory=list, max_length=6)       # [{"role": "user"|"assistant", "content": "..."}], kept by the page


def _vad_open(path: str, body: dict, timeout: float = 60.0):
    key = os.environ.get("VOICE_TTS_KEY", "")
    if not key:
        raise HTTPException(503, "VOICE_TTS_KEY is not set")
    req = urllib.request.Request(_vad_url() + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"X-Voice-Key": key, "Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"the voice service answered {e.code}") from e
    except OSError as e:
        raise HTTPException(503, f"the voice service is not reachable: {type(e).__name__}") from e


@core.use_route
@core.app.post("/api/voice/chat")
def api_chat(body: ChatBody, request: Request):
    """A chat answer of the house model, streamed as plain text. It has no devices and no actions: the text is only shown.
    Who may call the ask, chat and push-to-talk paths is the core's token check: the house token and a full-access phone; a phone
    with per-device rights and a panel key are refused before this runs."""
    history = [{"role": m.get("role"), "content": str(m.get("content") or "")[:600]} for m in body.history if isinstance(m, dict)]
    resp = _vad_open("/chat", {"text": body.text, "history": history}, timeout=130.0)   # the big model thinks up to 60 s, then the small one

    def pieces():
        with resp:
            while True:
                chunk = resp.read1(512) if hasattr(resp, "read1") else resp.read(512)
                if not chunk:
                    return
                yield chunk
    return StreamingResponse(pieces(), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


# ---- push-to-talk of a phone: hold, speak, release; the recording goes up, the answer comes back ----
def _vad_raw(path: str, data: bytes, timeout: float = 40.0) -> dict:
    key = os.environ.get("VOICE_TTS_KEY", "")
    if not key:
        raise HTTPException(503, "VOICE_TTS_KEY is not set")
    req = urllib.request.Request(_vad_url() + path, data=data, method="POST",
                                 headers={"X-Voice-Key": key, "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"the voice service answered {e.code}") from e
    except (OSError, ValueError) as e:
        raise HTTPException(503, f"the voice service is not reachable: {type(e).__name__}") from e


def _audio_of(res: dict, s: dict) -> str:
    """What the phone plays after a phrase: the spoken reply or question, else the done / error chime; nothing for a chat
    (the phone asks for the speech of the streamed text itself). '' when the speech service is not there."""
    try:
        if res.get("chat"):
            return ""
        text = str(res.get("spoken") or "").strip() or (str(res.get("reply") or "").strip() if not res.get("ok") or res.get("ask") else "")
        if text:
            return say_url(text, None, s.get("voice"))["url"]
        return sound_url("done" if res.get("ok") else "error")
    except (RuntimeError, ValueError):
        return ""


@core.use_route
@core.app.post("/api/voice/ptt")
async def api_ptt(request: Request, sat: str):
    """A phrase recorded on the phone (16 kHz mono, 16-bit, little endian, as the body): the same road as a spoken one, no wake word.
    -> {text, engine, ok, said, ask, token?, chat?, audio}; Yes / No of a question go to /api/voice/ask/confirm."""
    s = _sat(sat)
    if s is None and re.fullmatch(r"phone_[a-z0-9_]{1,22}", sat) and ID_RE.match(sat):      # the first talk of a phone makes its satellite
        if sat + "_mic" in core.DEVICES or sat in core.DEVICES:
            raise HTTPException(409, "the id is taken by a device")
        s = {"id": sat, "name": sat[len("phone_"):].replace("_", " ").title(), "node": "phone-" + sat.replace("_", "-"), "phone": True,
             "mic": True, "player": False}
        with _config_lock:
            _cfg["satellites"] = [x for x in _cfg["satellites"] if not (isinstance(x, dict) and x.get("id") == sat)] + [s]
            _sync_devices()
            core.save_settings()
    if s is None or not s.get("phone"):
        raise HTTPException(404, "no such phone satellite")
    data = await request.body()
    if len(data) > 700000:
        raise HTTPException(413, "a phrase is at most 20 seconds")
    from fastapi.concurrency import run_in_threadpool
    res = await run_in_threadpool(_vad_raw, "/utter", data)
    st = _stat(s["id"])
    st["wakes"] += 1
    st["last_wake"] = time.strftime("%H:%M")
    st["commands" if res.get("ok") else "errors"] += 1
    st["phone_seen"] = time.time()
    _set(s["id"], "mic", online=True, report=True, last_wake=st["last_wake"])
    res["audio"] = await run_in_threadpool(_audio_of, res, s)
    return res


class TtsBody(core.Body):
    text: str = Field(min_length=1, max_length=600)


@core.use_route
@core.app.post("/api/voice/tts")
def api_tts(body: TtsBody):
    """The speech of a text, for a phone that reads out a chat answer: {url}. The house token or a full-access phone (use_route)."""
    try:
        return {"url": say_url(body.text)["url"]}
    except RuntimeError as e:
        raise HTTPException(503, str(e))


# ---- the TTS service on another computer of the house (plug-in computers installs it): its code from here ----
_HERE = os.path.dirname(os.path.abspath(__file__))


def tts_service_files():
    """The TTS service (its Dockerfile, server.py, numwords.py) as a tar.gz, the same code as here; no key, no model in it."""
    def plain(t):
        t.uid = t.gid = 0
        t.uname = t.gname = ""
        t.mode = 0o644
        return t
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("server.py", "numwords.py"):
            tar.add(os.path.join(_HERE, "service", "tts", name), arcname=name, filter=plain)
        with open(os.path.join(_HERE, "service", "tts", "Dockerfile"), encoding="utf-8") as f:     # its context is that folder there
            df = f.read().replace("COPY service/tts/server.py service/tts/numwords.py", "COPY server.py numwords.py").encode()
        ti = plain(tarfile.TarInfo("Dockerfile"))
        ti.size = len(df)
        tar.addfile(ti, io.BytesIO(df))
    return Response(buf.getvalue(), media_type="application/gzip")


core.app.add_api_route("/plugins/voice/tts.tar.gz", tts_service_files, methods=["GET"])
core.open_route(tts_service_files)


from . import _record  # noqa: E402,F401  background recording (the sessions, the TV trigger)


_sync_devices()
