#!/usr/bin/env python3
"""The house TTS: Silero voices (CC BY-NC-SA 4.0 models, fetched once into a volume), answers as WAV PCM 16 kHz 16-bit STEREO served
under random names (docs/voice.md).

POST /say {text, lang?, voice?, lead_in?} (header X-Voice-Key) -> {"url": "/a/<name>.wav", ...}; GET /a/<name>.wav, GET /s/<sound>.wav (and <sound>_lead.wav),
GET /health. The names are the secret of a file (it lives 10 minutes), the key guards the synthesis. VOICE_TTS_OPEN=1 instead of a key:
a service whose port only its own loopback has (a computer of the house: the house reaches it through ssh).
"""
import array
import hmac
import io
import itertools
import json
import math
import os
import random
import re
import secrets
import shutil
import struct
import sys
import threading
import time
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numwords

PORT = int(os.environ.get("VOICE_TTS_PORT", "8092"))
KEY = os.environ.get("VOICE_TTS_KEY", "")
OPEN = not KEY and os.environ.get("VOICE_TTS_OPEN") == "1"
DATA = Path(os.environ.get("VOICE_TTS_DATA", "/data"))
MODELS = Path(os.environ.get("VOICE_TTS_MODELS", "/models"))
MODEL_URLS = {"ru": "https://models.silero.ai/models/tts/ru/v5_ru.pt", "en": "https://models.silero.ai/models/tts/en/v3_en.pt"}
DEFAULT_VOICES = {"ru": "ru-xenia", "en": "en-en_99"}
VOICE_RE = re.compile(r"^(ru|en)-[A-Za-z0-9_]+$")
SPEAKERS = {"ru": {"aidar", "baya", "kseniya", "xenia", "eugene", "random"},                 # Silero v5_ru
            "en": {f"en_{i}" for i in range(118)} | {"random"}}                                 # Silero v3_en


def known_voice(v) -> bool:
    """<language>-<Silero speaker> of a language with a model here."""
    return isinstance(v, str) and bool(VOICE_RE.match(v)) and v[:2] in MODEL_URLS and v[3:] in SPEAKERS[v[:2]]


VOICES = list(DEFAULT_VOICES.values())        # warmed at start: each language's default; any known voice may be asked for
ALL_VOICES = VOICES + sorted(f"{lang}-{sp}" for lang in MODEL_URLS for sp in SPEAKERS[lang]
                             if f"{lang}-{sp}" not in VOICES and sp != "random")       # what the Voice page offers a satellite
WARM = {"running": True}        # until the first load of every voice has been tried
SYNTH_RATE = 48000              # Silero's best rate; the area-average resampler brings it to RATE
THREADS = int(os.environ.get("VOICE_TTS_THREADS", "2"))
RATE = 16000                    # the one format the satellites get: 16 kHz, 16-bit, stereo (their native I2S port)
LEAD_IN_MS = 400                # noise before every sound (see _lead_in)
LEAD_IN_AMP = 100
KEEP_S = 600                    # a spoken file is deleted after this
MAX_CHARS = 500
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def pick_voice(text: str, lang: str = None, voice: str = None) -> str:
    """The voice for a phrase: the one named (any Silero speaker of a language here), else the language's default (given, or
    guessed from the letters)."""
    if known_voice(voice):
        return voice
    if lang not in ("ru", "en"):
        letters = [c for c in text if c.isalpha()]
        cyr = sum(1 for c in letters if 0x400 <= ord(c) <= 0x4ff)       # the Cyrillic block
        lang = "ru" if letters and cyr / len(letters) >= 0.3 else "en"
    return next((v for v in VOICES if v.startswith(lang + "-")), VOICES[0] if VOICES else "")


def resample(pcm: bytes, rate: int) -> bytes:
    """16-bit mono PCM from `rate` to RATE by area averaging (a box filter: enough for speech, no library, no aliasing to speak of)."""
    if rate == RATE:
        return pcm
    a = array.array("h")
    a.frombytes(pcm)
    if sys.byteorder == "big":
        a.byteswap()
    cum = [0] + list(itertools.accumulate(a))
    ratio = rate / RATE

    def area(x: float) -> float:                      # the integral of the samples up to position x
        k = int(x)
        return cum[k] + (a[k] * (x - k) if k < len(a) else 0.0)
    out = array.array("h", (int((area(min((i + 1) * ratio, len(a))) - area(i * ratio)) / ratio) for i in range(len(a) * RATE // rate)))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def _lead_in() -> array.array:
    """LEAD_IN_MS of faint uniform noise (+-LEAD_IN_AMP counts, fixed seed). The satellite's codec path mutes on digital zeros and opens
    60-250 ms after the first non-zero sample, which ate the first syllable or beep; a quiet noise opens it before the sound starts."""
    rnd = random.Random(1)
    return array.array("h", (int(rnd.uniform(-LEAD_IN_AMP, LEAD_IN_AMP)) for _ in range(RATE * LEAD_IN_MS // 1000)))


def to_wav16(pcm: bytes, rate: int, lead_in: bool = False) -> bytes:
    """16-bit MONO PCM at `rate` -> a WAV file in the house format: RATE, 16-bit, stereo (the channel is repeated); `lead_in`: the noise first."""
    mono = _lead_in() if lead_in else array.array("h")
    mono.frombytes(resample(pcm, rate))
    stereo = array.array("h", bytes(4 * len(mono)))
    stereo[0::2] = mono
    stereo[1::2] = mono
    if sys.byteorder == "big":
        stereo.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(stereo.tobytes())
    return out.getvalue()


def tone(freq: float, ms: int, gain: float = 0.35) -> bytes:
    """A sine with a short fade in and out (no clicks), 16-bit mono at RATE."""
    n = RATE * ms // 1000
    fade = max(1, RATE // 100)
    out = bytearray()
    for i in range(n):
        env = min(1.0, i / fade, (n - 1 - i) / fade)
        out += struct.pack("<h", int(32767 * gain * env * math.sin(2 * math.pi * freq * i / RATE)))
    return bytes(out)


SOUNDS = {                      # the confirmations: the house plays them through the same say/url
    "listening": [(660, 80), (880, 110)],
    "done": [(880, 90), (1175, 130)],
    "error": [(220, 150), (0, 70), (220, 190)],
    "thinking": [(523, 70), (0, 60), (523, 70)],               # a slow answer is on the way
}


def make_sounds(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, notes in SOUNDS.items():
        pcm = b"".join(tone(f, ms) if f else b"\0\0" * (RATE * ms // 1000) for f, ms in notes)
        (directory / f"{name}.wav").write_bytes(to_wav16(pcm, RATE))
        (directory / f"{name}_lead.wav").write_bytes(to_wav16(pcm, RATE, lead_in=True))


class Voices:
    """Silero models, one per language, loaded on first use (or by warm()) and kept; a missing model file is downloaded once into MODELS."""

    def __init__(self):
        self.loaded = {}
        self.lock = threading.Lock()

    def model(self, lang: str):
        with self.lock:
            if lang not in self.loaded:
                path = MODELS / f"silero_{lang}.pt"
                if not path.is_file():
                    part = path.with_suffix(".part")
                    with urllib.request.urlopen(MODEL_URLS[lang], timeout=60) as r, open(part, "wb") as f:      # a stalled download ends in 60 s, not never
                        shutil.copyfileobj(r, f)
                    part.replace(path)
                import torch                                        # imported here: the tests run without it
                torch.set_num_threads(THREADS)
                m = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
                m.to("cpu")
                self.loaded[lang] = m
            return self.loaded[lang]

    def say(self, text: str, name: str, lead_in: bool = False) -> bytes:
        lang, speaker = name.split("-", 1)
        model = self.model(lang)
        text = numwords.normalize(text, lang)
        extra = {"put_accent": True, "put_yo": True} if lang == "ru" else {}
        with self.lock:                                             # one synthesis at a time: simple and fast enough (RTF ~0.03)
            try:
                audio = model.apply_tts(text=text, speaker=speaker, sample_rate=SYNTH_RATE, **extra)
            except Exception:                                       # noqa: BLE001  a sign the model cannot read: keep letters, digits, punctuation
                clean = re.sub(r"[^\w\s.,!?:;-]", " ", text)
                audio = model.apply_tts(text=clean, speaker=speaker, sample_rate=SYNTH_RATE, **extra)
        pcm = array.array("h", (max(-32768, min(32767, int(x * 32767))) for x in audio.tolist()))
        if sys.byteorder == "big":
            pcm.byteswap()
        return to_wav16(pcm.tobytes(), SYNTH_RATE, lead_in)

    def warm(self) -> None:
        """Load every language of VOICES and speak once, so the first answer is as fast as the rest."""
        for name in VOICES:
            try:
                t0 = time.perf_counter()
                self.say("\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430." if name.startswith("ru-") else "Check.", name)  # i18n
                print(f"[tts] {name} ready in {time.perf_counter() - t0:.1f} s", flush=True)
            except Exception as e:                                  # noqa: BLE001  the first request tries again and reports it
                print(f"[tts] {name} not ready: {type(e).__name__}: {e}", flush=True)
        WARM["running"] = False


def store(wav: bytes) -> str:
    """The file's random name (128 bits); old files go on every call."""
    directory = DATA / "a"
    directory.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - KEEP_S
    for f in directory.glob("*.wav"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
    name = secrets.token_urlsafe(16)
    (directory / f"{name}.wav").write_bytes(wav)
    return name


VOICE_SET = Voices()


class Handler(BaseHTTPRequestHandler):
    server_version = "yash-tts"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj).encode())

    def do_GET(self):
        m = re.fullmatch(r"/([as])/([A-Za-z0-9_-]{4,64})\.wav", self.path)
        if self.path == "/health":
            return self._json(200, {"ok": bool(VOICE_SET.loaded) or WARM["running"], "voices": ALL_VOICES, "loaded": sorted(VOICE_SET.loaded)})
        if not m:
            return self._json(404, {"detail": "not found"})
        f = DATA / m.group(1) / f"{m.group(2)}.wav"
        if not f.is_file() or (m.group(1) == "a" and f.stat().st_mtime < time.time() - KEEP_S):
            return self._json(404, {"detail": "gone"})
        self._send(200, f.read_bytes(), "audio/wav")

    def do_POST(self):
        if self.path != "/say":
            return self._json(404, {"detail": "not found"})
        if not KEY and not OPEN:
            return self._json(503, {"detail": "no VOICE_TTS_KEY set: the service refuses to speak"})
        if not OPEN and not hmac.compare_digest(self.headers.get("X-Voice-Key", ""), KEY):
            return self._json(403, {"detail": "wrong key"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(min(n, 8192)) or b"{}")
            text = str(req.get("text") or "").strip()
        except (ValueError, TypeError):
            return self._json(400, {"detail": "bad request"})
        if not text or len(text) > MAX_CHARS:
            return self._json(400, {"detail": f"text: 1..{MAX_CHARS} characters"})
        name = pick_voice(text, req.get("lang"), req.get("voice"))
        t0 = time.perf_counter()
        try:
            wav = VOICE_SET.say(text, name, bool(req.get("lead_in")))
        except Exception as e:                                       # noqa: BLE001  a missing model or no network
            return self._json(503, {"detail": f"voice {name} is not ready: {type(e).__name__}"})
        file = store(wav)
        self._json(200, {"url": f"/a/{file}.wav", "voice": name, "bytes": len(wav), "seconds": round((len(wav) - 44) / (4 * RATE), 2),
                         "ms": round(1000 * (time.perf_counter() - t0))})


def main():
    make_sounds(DATA / "s")
    print(f"[tts] voices {VOICES}, port {PORT}", flush=True)
    threading.Thread(target=VOICE_SET.warm, daemon=True, name="tts-warm").start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
