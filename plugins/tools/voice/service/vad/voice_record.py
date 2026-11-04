"""Wake-word sample recording (the "Voice recording" screen): a stream armed for recording is saved as it is (16 kHz mono, 16-bit, the raw microphone), with
no recognition and no action; a phone's recording arrives as a whole. Files: <root>/<speaker>/<positive|negative>/<ts>_<satellite>.wav.
Nothing here listens by itself: a sample exists only when a person pressed "record" for this device a moment ago."""
import errno
import json
import math
import os
import random
import re
import shutil
import threading
import time
import wave

PUB = None                           # set by voice-vad: fn(topic, payload) publishing to the broker
OWNER = int(os.environ.get("VA_SAMPLES_UID", "1000"))

ROOT = os.environ.get("VA_SAMPLES_DIR", "/house/wake-samples")
PHRASES = os.environ.get("VA_WAKE_PHRASES", "/house/wake_phrases.json")
RATE = 16000
LABELS = ("positive", "negative")
SECS = 3.0
ARM_TTL = 15.0
MIN_PEAK = 800                       # below this nobody spoke: a sample is not kept, the answer says so
RESERVED = {"background"}            # the name of no speaker (the background folder is _background)
_lock = threading.Lock()
_armed = {}                          # satellite address -> the open request
_done = {}                           # request id -> its result (kept a few minutes)
TR = dict(zip("\u0430\u0431\u0432\u0433\u0434\u0435\u0451\u0436\u0437\u0438\u0439\u043a\u043b\u043c\u043d\u043e\u043f\u0440\u0441\u0442\u0443\u0444\u0445\u0446\u0447\u0448\u0449\u044a\u044b\u044c\u044d\u044e\u044f", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",  # i18n
                                                    "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slug(name: str) -> str:
    """A safe folder name for a speaker: Latin letters, digits, - and _ (Cyrillic transliterated)."""
    s = "".join(TR.get(c, c) for c in name.strip().lower())
    s = re.sub(r"[^a-z0-9_-]+", "-", s).strip("-_")
    return s[:32] or "speaker"


def _unique(path: str) -> str:
    """The path, or path-1, path-2 ... when the name is taken (two files in the same millisecond)."""
    base, ext = os.path.splitext(path)
    n, out = 0, path
    while os.path.exists(out):
        n += 1
        out = "%s-%d%s" % (base, n, ext)
    return out


def _dir(speaker: str, label: str) -> str:
    if label not in LABELS:
        raise ValueError("label: positive | negative")
    if speaker.strip().startswith("_") or slug(speaker) in RESERVED:
        raise ValueError("speaker: this name is reserved")
    return os.path.join(ROOT, slug(speaker), label)


def _wav(path: str, pcm: bytes) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def peak_of(pcm: bytes) -> int:
    import array
    a = array.array("h")
    a.frombytes(pcm[:len(pcm) // 2 * 2])
    return max((abs(x) for x in a), default=0)


def save_pcm(pcm: bytes, speaker: str, label: str, sat: str, display: str = "") -> dict:
    """One sample as a WAV file; -> {file, secs, peak, count}. `file` is relative to the samples folder."""
    d = _dir(speaker, label)
    os.makedirs(d, mode=0o755, exist_ok=True)
    for p in (os.path.dirname(d), d):
        _own(p, 0o755)
    now = time.time()
    name = "%s-%03d_%s.wav" % (time.strftime("%Y%m%d-%H%M%S", time.localtime(now)), int(now * 1000) % 1000, re.sub(r"[^a-z0-9_-]", "", sat.lower()) or "sat")
    path = _unique(os.path.join(d, name))
    _wav(path, pcm)
    _own(path, 0o644)
    meta = os.path.join(ROOT, slug(speaker), "speaker.txt")
    if display and not os.path.exists(meta):
        with open(meta, "w", encoding="utf-8") as f:
            f.write(display.strip()[:60] + "\n")
        _own(meta, 0o644)
    return {"file": os.path.relpath(path, ROOT), "secs": round(len(pcm) / 2 / RATE, 2), "peak": peak_of(pcm), "count": len(os.listdir(d))}


def arm(ip: str, speaker: str, label: str, sat: str, secs: float = SECS, display: str = "") -> str:
    """The next stream from this address is a sample (SECS seconds, taken from its first byte); -> a request id."""
    _dir(speaker, label)
    rid = "r%x" % random.getrandbits(40)
    with _lock:
        _armed[ip] = {"id": rid, "speaker": speaker, "label": label, "sat": sat, "display": display, "need": int(secs * RATE) * 2, "buf": bytearray(),
                      "until": time.time() + ARM_TTL}
        _done[rid] = {"state": "armed"}
    return rid


def feed(ip: str, chunk: bytes) -> bool:
    """True when this chunk went into a sample (the caller then does nothing else with it)."""
    if ip not in _armed:
        return False
    with _lock:
        req = _armed.get(ip)
        if req is None:
            return False
        if time.time() > req["until"]:
            _armed.pop(ip, None)
            _done[req["id"]] = {"state": "expired"}
            return False
        req["buf"] += chunk[:req["need"] - len(req["buf"])]
        if len(req["buf"]) < req["need"]:
            return True
        _armed.pop(ip, None)
        pcm = bytes(req["buf"])
    try:
        if peak_of(pcm) < MIN_PEAK:                          # nobody spoke: nothing is kept
            _done[req["id"]] = {"state": "quiet", "quiet": True}
            return True
        out = save_pcm(pcm, req["speaker"], req["label"], req["sat"], req.get("display", ""))
        _done[req["id"]] = dict(out, state="saved", quiet=False)
    except (OSError, ValueError) as e:
        _done[req["id"]] = {"state": "error", "error": str(e)}
    return True


def status(rid: str) -> dict:
    with _lock:
        for ip, req in list(_armed.items()):
            if req["until"] < time.time():
                _armed.pop(ip, None)
                _done[req["id"]] = {"state": "expired"}
        for k in list(_done)[:-50]:
            _done.pop(k, None)
        return dict(_done.get(rid) or {"state": "unknown"})


def listing() -> dict:
    """{speaker slug: {"name", "positive", "negative"}} of what is on disk."""
    out = {}
    if not os.path.isdir(ROOT):
        return out
    for sp in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, sp)
        if sp.startswith("_") or not os.path.isdir(p):                  # _background is not a speaker
            continue
        try:
            name = open(os.path.join(p, "speaker.txt"), encoding="utf-8").read().strip() or sp
        except OSError:
            name = sp
        out[sp] = {"name": name, **{lb: len([f for f in os.listdir(os.path.join(p, lb))]) if os.path.isdir(os.path.join(p, lb)) else 0 for lb in LABELS}}
    return out


def _safe(rel: str) -> str:
    path = os.path.realpath(os.path.join(ROOT, rel))
    if not path.startswith(os.path.realpath(ROOT) + os.sep) or not path.endswith(".wav"):
        raise ValueError("not a sample")
    return path


def read_file(rel: str) -> bytes:
    with open(_safe(rel), "rb") as f:
        return f.read()


def delete(rel: str = "", speaker: str = "", label: str = "") -> dict:
    """Delete one sample by its relative path, or the newest of speaker/label."""
    if not rel:
        d = _dir(speaker, label)
        files = sorted(f for f in os.listdir(d) if f.endswith(".wav")) if os.path.isdir(d) else []
        if not files:
            return {"deleted": None}
        rel = os.path.relpath(os.path.join(d, files[-1]), ROOT)
    os.remove(_safe(rel))
    return {"deleted": rel}


def phrases(n: int = 5) -> list:
    """Random phrases to read for negative samples (house data: wake_phrases.json {"negative": [...]})."""
    try:
        with open(PHRASES, encoding="utf-8") as f:
            pool = [str(x) for x in (json.load(f).get("negative") or [])]
    except (OSError, ValueError):
        pool = []
    random.shuffle(pool)
    return pool[:max(1, min(n, 20))]


# ---- background sessions: one per satellite address ----
_bg = {}                             # ip -> the running session
_bg_done = {}                        # ip -> the summary of the last finished one
MIN_FREE_BYTES = 2 * 1024 ** 3        # less free space on the recordings volume: no background session starts, a running one stops (disk_low)
MAX_SESSION_S = 12 * 3600.0           # the longest background session a request may ask for
KICK_GAP_S = 2.5                     # no audio for this long while a session runs: ask again


def _own(path: str, mode: int) -> None:
    os.chmod(path, mode)
    try:
        os.chown(path, OWNER, OWNER)
    except OSError:                                # not root: the owner is who we are
        pass


def disk_low() -> bool:
    """True when the volume of the recordings has less than MIN_FREE_BYTES free."""
    p = ROOT
    while p and not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    try:
        return shutil.disk_usage(p).free < MIN_FREE_BYTES
    except OSError:
        return False


def _summary(s: dict, state: str) -> dict:
    return {"state": state, "label": s["label"], "dir": os.path.relpath(s["dir"], ROOT), "files": s["files"], "seconds": round(s["bytes"] / 2 / RATE, 1),
            "elapsed": round(time.time() - s["started"], 1)}


def bg_start(ip: str, label: str, topic: str, payload: str = "10", max_s: float = 7200.0) -> dict:
    if not re.fullmatch(r"[a-z0-9_-]{1,20}", label):
        raise ValueError("label")
    if not math.isfinite(max_s) or not 0 < max_s <= MAX_SESSION_S:     # nan/inf/negative would never time out
        raise ValueError("max_s")
    if disk_low():
        _bg_done[ip] = {"state": "disk_low", "label": label, "dir": "", "files": 0, "seconds": 0.0, "elapsed": 0.0}
        return _bg_done[ip]
    d = os.path.join(ROOT, "_background", time.strftime("%Y-%m-%d"), label)
    os.makedirs(d, mode=0o700, exist_ok=True)
    for p in (os.path.join(ROOT, "_background"), os.path.dirname(d), d):
        _own(p, 0o700)
    with _lock:
        old = _bg.pop(ip, None)
    if old:
        _bg_close(old)
        _bg_done[ip] = _summary(old, "replaced")
    s = {"label": label, "dir": d, "topic": topic, "payload": payload, "started": time.time(), "until": time.time() + max_s, "last": 0.0, "kick": 0.0,
         "files": 0, "bytes": 0, "w": None, "path": None}
    with _lock:
        _bg[ip] = s
    bg_kick(ip, force=True)
    return _summary(s, "recording")


def bg_kick(ip: str, force: bool = False) -> None:
    s = _bg.get(ip)
    if s and PUB and (force or time.time() - s["kick"] > 1.0):
        s["kick"] = time.time()
        PUB(s["topic"], s["payload"])


def _bg_close(s: dict) -> None:
    w, path = s.get("w"), s.get("path")
    if w is not None:
        try:
            w.close()
        except (OSError, wave.Error):
            pass
        if path:
            _own(path, 0o600)
    s["w"], s["path"] = None, None


def bg_feed(ip: str, chunk: bytes) -> bool:
    """True when a background session took this chunk. Under the lock: a stop between two chunks drops the next one."""
    with _lock:
        s = _bg.get(ip)
        if s is None:
            return False
        now = time.time()
        if s["w"] is None or now - s["last"] > 1.5:         # a new stream: a new file
            _bg_close(s)
            if disk_low():
                _bg.pop(ip, None)
                _bg_done[ip] = _summary(s, "disk_low")
                return False
            s["path"] = _unique(os.path.join(s["dir"], "%s-%03d.wav" % (time.strftime("%H%M%S", time.localtime(now)), int(now * 1000) % 1000)))
            s["w"] = wave.open(s["path"], "wb")
            s["w"].setnchannels(1)
            s["w"].setsampwidth(2)
            s["w"].setframerate(RATE)
            s["files"] += 1
        s["w"].writeframes(chunk[:len(chunk) // 2 * 2])
        s["bytes"] += len(chunk) // 2 * 2
        s["last"] = now
    return True


def bg_conn_closed(ip: str) -> None:
    """The satellite's stream ended: close the file and ask for the next stream at once."""
    with _lock:
        s = _bg.get(ip)
        if s:
            _bg_close(s)
    if s:
        bg_kick(ip, force=True)


def bg_stop(ip: str, state: str = "stopped") -> dict:
    with _lock:
        s = _bg.pop(ip, None)
    if s is None:
        return _bg_done.get(ip) or {"state": "none"}
    _bg_close(s)
    _bg_done[ip] = _summary(s, state)
    return _bg_done[ip]


def bg_status(ip: str) -> dict:
    s = _bg.get(ip)
    return _summary(s, "recording") if s else (_bg_done.get(ip) or {"state": "none"})


def bg_tick() -> None:
    """Once a second: end a session at its timeout, ask again when a stream is overdue."""
    for ip, s in list(_bg.items()):
        now = time.time()
        if now > s["until"]:
            bg_stop(ip, "done")
        elif disk_low():
            bg_stop(ip, "disk_low")
        elif now - max(s["last"], s["started"]) > KICK_GAP_S and now - s["kick"] > KICK_GAP_S:
            with _lock:
                _bg_close(s)
            bg_kick(ip, force=True)


# ---- HTTP side of /record/*: a bad request is 400 (the phone drops it), never 500 (the phone retries it forever) ----
def _post(path: str, body: dict, raw: bytes) -> dict:
    from urllib.parse import parse_qs, urlparse
    u = urlparse(path)
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    if u.path in ("/record/arm", "/record/bg") and not str(body.get("ip") or "").strip():
        raise ValueError("ip")
    if u.path == "/record/arm":
        return {"id": arm(str(body["ip"]), str(body["speaker"]), str(body["label"]), str(body.get("sat") or "sat"), display=str(body.get("name") or ""))}
    if u.path == "/record/save":                               # a recording made on the phone: the body is raw 16 kHz mono s16le
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if peak_of(raw) < MIN_PEAK:                            # no speech: nothing is kept
            return {"state": "quiet", "quiet": True}
        return dict(save_pcm(raw, q.get("speaker", ""), q.get("label", ""), q.get("sat", "phone"), q.get("name", "")), state="saved", quiet=False)
    if u.path == "/record/delete":
        return delete(str(body.get("file") or ""), str(body.get("speaker") or ""), str(body.get("label") or ""))
    if u.path == "/record/bg":
        ip = str(body.get("ip") or "")
        if body.get("action") == "start":
            return bg_start(ip, str(body.get("label") or ""), str(body.get("topic") or ""), str(body.get("payload") or "10"), _num(body.get("max_s"), 7200.0))
        if body.get("action") == "stop":
            return bg_stop(ip)
        return bg_status(ip)
    raise LookupError(u.path)


def _num(v, default: float) -> float:
    try:
        return default if v is None else float(v)
    except TypeError:                                          # a list or an object is a bad request, not a crash
        raise ValueError("max_s")


def http_post(path: str, body: dict, raw: bytes = b"") -> tuple:
    """-> (status code, answer): ValueError or a missing field = 400, an unknown path or file = 404, a full disk = 507."""
    try:
        return 200, _post(path, body, raw)
    except (ValueError, KeyError) as e:
        return 400, {"error": str(e)}
    except FileNotFoundError:
        return 404, {"error": "file"}
    except OSError as e:
        if e.errno in (errno.ENOSPC, errno.EDQUOT):
            return 507, {"error": "disk full"}
        raise
    except LookupError:
        return 404, {"error": "path"}


def http_get(path: str, q: dict) -> tuple:
    """-> (status code, body bytes, content type): ValueError = 400, a missing file or path = 404."""
    try:
        if path == "/record/file":
            return 200, read_file(q.get("f", "")), "audio/wav"
        if path == "/record/status":
            out = status(q.get("id", ""))
        elif path == "/record/list":
            out = listing()
        elif path == "/record/phrases":
            out = {"phrases": phrases(int(q.get("n") or 5))}
        elif path == "/record/bg":
            out = bg_status(q.get("ip", ""))
        else:
            raise LookupError(path)
        return 200, json.dumps(out, ensure_ascii=False).encode(), "application/json; charset=utf-8"
    except ValueError as e:
        return 400, json.dumps({"error": str(e)}).encode(), "application/json"
    except (OSError, LookupError) as e:
        return 404, json.dumps({"error": str(e)}).encode(), "application/json"
