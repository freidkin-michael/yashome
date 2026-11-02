"""Background recording of a satellite's microphone: voice-vad saves the raw audio (no recognition, no action, owner-only files, see
docs/voice.md); this side starts and stops the sessions a person asked for, watches the TV for the "while the TV is on" one and shows the
state on the satellite's card. Two kinds: "quiet" (a given time from now) and "tv" (armed: it starts when the TV turns on, ends when it turns
off or after at most two hours, one time). Only one session records at a time (a TV one replaces a running quiet one)."""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import Field

import app as core

from . import _sat, _sats, _send, _set, _vad_post, _vad_raw, _vad_url, play, public_base, sound_url

TV_MAX_S = 7200
_lock = threading.Lock()
SESS: dict = {}                      # kind -> {"state": waiting | recording | done | cancelled | replaced, "sat", "since", "until", "summary"}


def _file():
    return core.plugin_state("voice") / "background.json"


_saved: dict = {}


def _persist() -> None:
    """The sessions survive a restart of the house (voice-vad keeps recording by itself; an armed TV session must not be forgotten)."""
    key = (str(_file()), json.dumps(SESS, sort_keys=True))
    if key == _saved.get("key"):
        return                                         # the watch calls this every 3 s: a file that would not change is not rewritten
    try:
        core.atomic_json_dump(_file(), SESS, indent=1)
        _saved["key"] = key
    except OSError as e:
        core.log.warning(f"[voice] background sessions not saved: {e}")


def _restore() -> None:
    try:
        SESS.update(json.loads(_file().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def _clock(sec: float) -> str:
    sec = int(sec)
    return "%d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60) if sec >= 3600 else "%d:%02d" % (sec // 60, sec % 60)


def _card_text() -> str:
    """What the card says: the TV session recording, else the quiet one recording, else a waiting TV one, else the last result."""
    now = time.time()
    for kind in ("tv", "quiet"):
        s = SESS.get(kind)
        if s and s["state"] == "recording":
            return "\u0437\u0430\u043f\u0438\u0441\u044c \u0444\u043e\u043d\u0430: \u0438\u0434\u0451\u0442 %s" % _clock(now - s["since"])  # i18n
    if SESS.get("tv", {}).get("state") == "waiting":
        return "\u0437\u0430\u043f\u0438\u0441\u044c \u0444\u043e\u043d\u0430: \u0436\u0434\u0451\u0442 \u0422\u0412"  # i18n
    done = [s for s in SESS.values() if s["state"] in ("done", "cancelled", "replaced")]
    if done:
        last = max(done, key=lambda s: s.get("ended", 0))
        return "\u0437\u0430\u043f\u0438\u0441\u044c \u0444\u043e\u043d\u0430: \u0433\u043e\u0442\u043e\u0432\u043e %s" % _clock(last.get("summary", {}).get("seconds", 0))  # i18n
    return ""


def _show(sat_id: str) -> None:
    _set(sat_id, "mic", bg_record=_card_text() or "-")


def _vad(ip: str, action: str, **kw) -> dict:
    return _vad_post("/record/bg", dict(kw, ip=ip, action=action), timeout=10)


def _start(kind: str, s: dict, max_s: float) -> None:
    ip, topic = s.get("peer") or "", f"{s['node']}/listen_once/command"
    if not ip:
        raise HTTPException(409, "the satellite has no audio address (peer)")
    for other, o in SESS.items():                          # one recorder per satellite: a running session of another kind ends
        if other != kind and o["state"] == "recording" and o["sat"] == s["id"]:
            o["summary"], o["state"], o["ended"] = _vad(ip, "stop"), "replaced", time.time()
    _vad(ip, "start", label=kind, topic=topic, payload="10", max_s=max_s)
    now = time.time()
    SESS[kind] = {**SESS.get(kind, {}), "state": "recording", "sat": s["id"], "since": now, "until": now + max_s, "summary": {}}
    core.log.info(f"[voice] background recording {kind} started on {s['id']} for {int(max_s)} s")


def _finish(kind: str, state: str, summary: dict) -> None:
    o = SESS[kind]
    o["state"], o["summary"], o["ended"] = state, summary, time.time()
    core.log.info(f"[voice] background recording {kind} {state}: {summary.get('files')} files, {summary.get('seconds')} s in {summary.get('dir')}")


def _tv_device(s: dict, asked: str = "") -> str:
    """The TV a tv session watches: the one asked for, else the TV of the satellite's room, else the house's only TV."""
    tvs = [d for d, x in list(core.DEVICES.items()) if x.get("category") == "tv"]
    if asked:
        return asked if asked in tvs else ""
    room = next((r for r in core.get_rooms() if s.get("room") in (r.get("id"), r.get("name"))), None)
    here = [d for d in tvs if room and d in (room.get("devices") or [])]
    return here[0] if len(here) == 1 else tvs[0] if len(tvs) == 1 else ""


def _tv_on(dev: str) -> bool:
    return bool(dev and ((core.status_copy(dev, {}) or {}).get("values") or {}).get("power"))


def tick() -> None:
    with _lock:
        for kind, o in list(SESS.items()):
            s = _sat(o["sat"])
            if s is None or not s.get("peer"):
                continue
            ip = s["peer"]
            try:
                if kind == "tv" and o["state"] == "waiting" and _tv_on(o.get("tv", "")):
                    _start("tv", s, TV_MAX_S)
                elif kind == "tv" and o["state"] == "recording" and not _tv_on(o.get("tv", "")):
                    _finish("tv", "done", _vad(ip, "stop"))
                elif o["state"] == "recording":
                    st = _vad(ip, "status")                # the session ended by its own timeout, or another one replaced it
                    if st.get("state") != "recording" or st.get("label") != kind:
                        _finish(kind, "done" if st.get("label") == kind else "replaced", st)
            except HTTPException as e:
                core.log.warning(f"[voice] background recording {kind}: {e.detail}")
        for sid in {o["sat"] for o in SESS.values()}:
            _show(sid)
        _persist()


def _watch() -> None:
    while True:
        try:
            tick()
        except Exception as e:                           # noqa: BLE001  the watch goes on
            core.log.warning(f"[voice] background recording watch: {e}")
        time.sleep(3)


class BgBody(core.Body):
    kind: Literal["quiet", "tv"]
    sat: str = Field(max_length=40)
    minutes: int = Field(default=60, ge=1, le=720)          # quiet: how long; a tv session lasts as long as the TV is on, at most two hours
    tv: str = Field(default="", max_length=80)              # tv: the TV to watch (empty: the satellite's room's, or the only one)


@core.app.post("/api/voice/background")
def start_background(body: BgBody, request: Request):
    """Start a quiet-room recording now, or arm a one-time recording of the room while the TV is on. The house token only."""
    core.house_token_only(request)
    s = _sat(body.sat)
    if s is None or not s.get("mic") or s.get("phone") or s.get("panel"):
        raise HTTPException(404, "a satellite with a microphone that streams on request (listen_once) is needed")
    tv = _tv_device(s, body.tv) if body.kind == "tv" else ""
    if body.kind == "tv" and not tv:
        raise HTTPException(400, "which TV: none in the satellite's room and not one in the house (name it: tv)")
    with _lock:
        if body.kind == "quiet":
            _start("quiet", s, body.minutes * 60.0)
        else:
            SESS["tv"] = {"state": "waiting", "sat": s["id"], "tv": tv, "since": time.time(), "until": 0, "summary": {}}
            core.log.info(f"[voice] background recording tv armed on {s['id']} (TV {tv})")
        _show(s["id"])
        _persist()
        return {k: {kk: vv for kk, vv in o.items() if kk != "summary"} | {"summary": o.get("summary")} for k, o in SESS.items()}


@core.app.get("/api/voice/background")
def background_state(request: Request):
    core.house_token_only(request)
    with _lock:
        return {k: dict(o, elapsed=round(time.time() - o["since"], 1) if o["state"] == "recording" else 0) for k, o in SESS.items()}


def cancel_all() -> dict:
    """Stop every background session and forget the armed one: one that cannot be stopped (the recorder does not answer) is still marked cancelled."""
    with _lock:
        for kind, o in list(SESS.items()):
            if o["state"] in ("recording", "waiting"):
                s = _sat(o["sat"])
                try:
                    summary = _vad(s["peer"], "stop") if o["state"] == "recording" and s and s.get("peer") else {}
                except HTTPException as e:
                    core.log.warning(f"[voice] background recording {kind}: the recorder did not stop ({e.detail})")
                    summary = {}
                _finish(kind, "cancelled", summary)
        for sid in {o["sat"] for o in SESS.values()}:
            _show(sid)
        _persist()
        return {k: o["state"] for k, o in SESS.items()}


@core.app.delete("/api/voice/background")
def cancel_background(request: Request):
    core.house_token_only(request)
    return cancel_all()


_restore()
core.STARTUP_TASKS.append(_watch)


# ---- wake-word samples: one short recording per press, positive (the phrase) or negative (anything else) ----
SAMPLE_TARGET = 100


def _vad_get(path: str, timeout: float = 10.0, raw: bool = False):
    key = os.environ.get("VOICE_TTS_KEY", "")
    if not key:
        raise HTTPException(503, "VOICE_TTS_KEY is not set")
    req = urllib.request.Request(_vad_url() + path, headers={"X-Voice-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return data if raw else json.loads(data)
    except urllib.error.HTTPError as e:
        raise HTTPException(404 if e.code == 404 else 502, f"the voice service answered {e.code}") from e
    except (OSError, ValueError) as e:
        raise HTTPException(503, f"the voice service is not reachable: {type(e).__name__}") from e


def _recorders() -> list:
    """The satellites that can record on request: a node that streams raw audio when asked (listen_once)."""
    return [{"id": s["id"], "name": s.get("name") or s["id"]} for s in _sats() if s.get("mic") and s.get("listen_once") and s.get("peer")]


@core.app.get("/api/voice/record/state")
def record_state():
    """{speakers: {slug: {name, positive, negative}}, devices: [satellites that record on request], target, background: bool}."""
    busy = any(o["state"] == "recording" for o in SESS.values())
    return {"speakers": _vad_get("/record/list"), "devices": _recorders(), "target": SAMPLE_TARGET, "background": busy}


_avail = {"t": 0.0, "ok": False}


@core.app.get("/api/voice/record/available")
def record_available():
    """Whether the recognizer service that keeps the samples answers (a house without it has no recording: the screens hide it). Asked at most once a minute."""
    if time.time() - _avail["t"] > 60:
        try:
            _vad_get("/record/list", timeout=3)
            ok = True
        except HTTPException:
            ok = False
        _avail.update(t=time.time(), ok=ok)
    return {"available": _avail["ok"]}


@core.app.get("/api/voice/record/phrases")
def record_phrases(n: int = 5):
    return _vad_get("/record/phrases?n=%d" % max(1, min(n, 20)))


class RecBody(core.Body):
    speaker: str = Field(min_length=1, max_length=40)
    label: Literal["positive", "negative"]
    device: str = Field(min_length=1, max_length=40)
    name: str = Field(default="", max_length=60)             # how a new speaker is written (the folder is a slug of the speaker)


@core.use_route
@core.app.post("/api/voice/record")
def record_on_device(body: RecBody):
    """Record one sample on a satellite: arm voice-vad, a short beep, the satellite streams 5 s of raw audio, the first 3 s are kept (nothing when nobody spoke).
    -> {state: saved, file, secs, peak, quiet, count}."""
    s = _sat(body.device)
    if s is None or body.device not in {r["id"] for r in _recorders()}:
        raise HTTPException(404, "no such satellite that records on request")
    if any(o["state"] == "recording" and o["sat"] == s["id"] for o in SESS.values()):
        raise HTTPException(409, "a background recording runs on this satellite: cancel it first")
    rid = _vad_post("/record/arm", {"ip": s["peer"], "speaker": body.speaker, "label": body.label, "sat": s["id"], "name": body.name}, timeout=10)["id"]
    if public_base():
        play(s, sound_url("listening", s), "record")
        time.sleep(0.8)
    _send(s, "listen_once/command", "5")
    end = time.time() + 9
    while time.time() < end:
        st = _vad_get("/record/status?id=" + urllib.parse.quote(rid))
        if st.get("state") in ("saved", "quiet", "error", "expired"):
            return st
        time.sleep(0.3)
    raise HTTPException(504, "nothing was recorded: is the satellite listening (not muted) and online?")


@core.use_route
@core.app.post("/api/voice/record/sample")
async def record_sample(request: Request, speaker: str, label: str, sat: str = "phone", name: str = ""):
    """A recording made on a phone or in the browser (16 kHz mono 16-bit little endian, as the body, 1-6 s): saved as it is."""
    data = await request.body()
    if not 8000 <= len(data) <= 200000:
        raise HTTPException(413, "a sample is 0.25 to 6 seconds of 16 kHz 16-bit mono")
    if label not in ("positive", "negative"):
        raise HTTPException(400, "label: positive | negative")
    from fastapi.concurrency import run_in_threadpool
    q = urllib.parse.urlencode({"speaker": speaker[:40], "label": label, "sat": sat[:30], "name": name[:60]})
    return await run_in_threadpool(_vad_raw, "/record/save?" + q, data)


class DelBody(core.Body):
    file: str = Field(default="", max_length=200)
    speaker: str = Field(default="", max_length=40)
    label: str = Field(default="", max_length=10)


def _sample_only(*names: str) -> None:
    """Room recordings live in folders that start with "_": these sample calls never reach them."""
    if any(part.startswith("_") for n in names for part in n.replace("\\", "/").split("/")):
        raise HTTPException(404, "no such sample")


@core.use_route
@core.app.post("/api/voice/record/delete")
def record_delete(body: DelBody):
    """Delete one sample (by file) or the newest of a speaker and label."""
    _sample_only(body.file, body.speaker)
    return _vad_post("/record/delete", body.model_dump(), timeout=10)


@core.app.get("/api/voice/record/file")
def record_file(f: str):
    """One sample as audio/wav, for playing it back (the house token or a full-access phone)."""
    _sample_only(f)
    return Response(_vad_get("/record/file?f=" + urllib.parse.quote(f), raw=True), media_type="audio/wav", headers={"Cache-Control": "no-store"})
