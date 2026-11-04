"""Vosk recognizer for voice_vad: model in a docker volume (sha-checked), a command grammar built
from the house command map, digits for spoken numbers.
Two passes (voice_vad): the grammar pass, then a free pass; whisper is not in the path."""
import hashlib
import json
import os
import re
import shutil
import urllib.request
import zipfile

MODEL_NAME = "vosk-model-small-ru-0.22"
MODEL_URL = "https://alphacephei.com/vosk/models/" + MODEL_NAME + ".zip"
MODEL_SHA256 = "961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4"   # of the zip
MODELS_DIR = os.environ.get("VA_VOSK_DIR", "/vosk")

# word forms are guessed as stem + ending and kept only when the model knows the word
ENDINGS = ["", "\u0430", "\u0443", "\u0435", "\u044b", "\u0438", "\u044f", "\u044e", "\u043e\u0439", "\u0430\u044f", "\u043e\u0435", "\u044b\u0435", "\u044b\u0439", "\u0438\u0439", "\u043e\u043c", "\u0435\u043c", "\u0430\u0445",  # i18n
           "\u044c", "\u044c\u044f", "\u044c\u0435", "\u044c\u044e", "\u0438\u0442\u044c", "\u0430\u0442\u044c", "\u0438\u043b", "\u0438\u043b\u0430", "\u0438\u043b\u043e", "\u0438\u043b\u0438", "\u0430\u043b", "\u0430\u043b\u0430", "\u0430\u043b\u0438", "\u0430\u0439", "\u0438\u0442\u0435", "\u0430\u0439\u0442\u0435",
           "\u0438\u0442", "\u0435\u0442", "\u0443\u0442", "\u044e\u0442", "\u0435\u043d\u043e", "\u0435\u043d\u0430", "\u0435\u043d", "\u043d\u044c", "\u043d\u044f", "\u043d\u044e", "\u043d\u0435", "\u043d\u0438", "\u043d\u0435\u0439", "\u043d\u043e\u0439", "\u043d\u043e\u0433\u043e", "\u043d\u043e\u043c",  # i18n
           "\u043d\u044b\u0445", "\u044b\u043c", "\u043e\u0433\u043e", "\u0435\u043c\u0443", "\u0435\u0435", "\u0438\u0435", "\u044b\u0445", "\u043e\u044e", "\u0435\u044e", "\u0438\u0441\u044c", "\u0442\u0438", "\u0442\u0438\u0442\u044c", "\u0442\u0438\u043b", "\u0442\u0438\u043b\u0430", "\u0442\u0438\u0442\u0435",
           "\u0435\u043c", "\u0435\u0433\u043e", "\u044e\u044e", "\u0430\u043c\u0438"]  # i18n

# words of the commands the code handles itself (TV, percent, temperature, filler)
FIXED = ["\u0434\u0436\u0430\u0440\u0432\u0438\u0441", "\u0432", "\u043d\u0430", "\u0438", "\u0441\u0434\u0435\u043b\u0430\u0439", "\u0443\u0441\u0442\u0430\u043d\u043e\u0432\u0438", "\u043f\u043e\u0441\u0442\u0430\u0432\u044c", "\u0441\u0432\u0435\u0442", "\u0443\u0433\u043e\u043b", "\u0433\u0440\u0430\u0434\u0443\u0441", "\u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440", "\u043f\u0440\u043e\u0446\u0435\u043d\u0442",  # i18n
         "\u044f\u0440\u043a\u043e\u0441\u0442", "\u0442\u0435\u043b\u0435\u0432\u0438\u0437\u043e\u0440", "\u0437\u0432\u0443\u043a", "\u0433\u0440\u043e\u043c\u0447", "\u0442\u0438\u0448", "\u043a\u043e\u043d\u0434\u0438\u0446\u0438\u043e\u043d\u0435\u0440", "\u0440\u0435\u0436\u0438\u043c", "\u0441\u0432\u0435\u0440\u0445\u0443", "\u0432\u0435\u0440\u0445\u043d", "\u0431\u043e\u0439\u043b\u0435\u0440", "\u043d\u043e\u043b\u044c",
         "\u043f\u0435\u0440\u0435\u0447\u0438\u0441\u043b\u0438", "\u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u0430", "\u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432", "\u043a\u0430\u043a\u0438\u0435", "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e", "\u0434\u043e\u043c\u0435",  # i18n
         "\u043f\u043e\u0433\u043e\u0434\u0430", "\u043f\u043e\u0433\u043e\u0434\u0443", "\u043f\u043e\u0433\u043e\u0434\u044b", "\u043f\u0440\u043e\u0433\u043d\u043e\u0437", "\u0437\u0430\u0432\u0442\u0440\u0430", "\u0441\u0435\u0433\u043e\u0434\u043d\u044f", "\u0432\u0435\u0447\u0435\u0440\u043e\u043c", "\u0434\u043e\u0436\u0434\u044c", "\u0434\u043e\u0436\u0434\u044f", "\u0443\u043b\u0438\u0446\u0435", "\u0437\u043e\u043d\u0442",
         "\u0432\u043b\u0430\u0436\u043d\u043e\u0441\u0442\u044c", "\u0432\u043b\u0430\u0436\u043d\u043e\u0441\u0442\u0438", "\u0432\u043b\u0430\u0436\u043d\u043e", "\u0441\u044b\u0440\u043e\u0441\u0442\u044c", "\u0442\u0435\u043f\u043b\u043e", "\u0445\u043e\u043b\u043e\u0434\u043d\u043e", "\u0436\u0430\u0440\u043a\u043e", "\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e", "\u0433\u043e\u0440\u0438\u0442", "\u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442"]  # i18n

ONES = {"\u043e\u0434\u0438\u043d": 1, "\u043e\u0434\u043d\u0430": 1, "\u043e\u0434\u043d\u0443": 1, "\u043e\u0434\u043d\u043e": 1, "\u0434\u0432\u0430": 2, "\u0434\u0432\u0435": 2, "\u0442\u0440\u0438": 3, "\u0447\u0435\u0442\u044b\u0440\u0435": 4, "\u043f\u044f\u0442\u044c": 5, "\u0448\u0435\u0441\u0442\u044c": 6,  # i18n
        "\u0441\u0435\u043c\u044c": 7, "\u0432\u043e\u0441\u0435\u043c\u044c": 8, "\u0434\u0435\u0432\u044f\u0442\u044c": 9}
TEENS = {"\u0434\u0435\u0441\u044f\u0442\u044c": 10, "\u043e\u0434\u0438\u043d\u043d\u0430\u0434\u0446\u0430\u0442\u044c": 11, "\u0434\u0432\u0435\u043d\u0430\u0434\u0446\u0430\u0442\u044c": 12, "\u0442\u0440\u0438\u043d\u0430\u0434\u0446\u0430\u0442\u044c": 13, "\u0447\u0435\u0442\u044b\u0440\u043d\u0430\u0434\u0446\u0430\u0442\u044c": 14, "\u043f\u044f\u0442\u043d\u0430\u0434\u0446\u0430\u0442\u044c": 15,  # i18n
         "\u0448\u0435\u0441\u0442\u043d\u0430\u0434\u0446\u0430\u0442\u044c": 16, "\u0441\u0435\u043c\u043d\u0430\u0434\u0446\u0430\u0442\u044c": 17, "\u0432\u043e\u0441\u0435\u043c\u043d\u0430\u0434\u0446\u0430\u0442\u044c": 18, "\u0434\u0435\u0432\u044f\u0442\u043d\u0430\u0434\u0446\u0430\u0442\u044c": 19}
TENS = {"\u0434\u0432\u0430\u0434\u0446\u0430\u0442\u044c": 20, "\u0442\u0440\u0438\u0434\u0446\u0430\u0442\u044c": 30, "\u0441\u043e\u0440\u043e\u043a": 40, "\u043f\u044f\u0442\u044c\u0434\u0435\u0441\u044f\u0442": 50, "\u0448\u0435\u0441\u0442\u044c\u0434\u0435\u0441\u044f\u0442": 60, "\u0441\u0435\u043c\u044c\u0434\u0435\u0441\u044f\u0442": 70,  # i18n
        "\u0432\u043e\u0441\u0435\u043c\u044c\u0434\u0435\u0441\u044f\u0442": 80, "\u0434\u0435\u0432\u044f\u043d\u043e\u0441\u0442\u043e": 90}
NUMBER_WORDS = ["\u043d\u043e\u043b\u044c", "\u0441\u0442\u043e"] + list(ONES) + list(TEENS) + list(TENS)  # i18n


def ensure_model():
    """Path of the model; downloaded once into the volume and checked against MODEL_SHA256."""
    path = os.path.join(MODELS_DIR, MODEL_NAME)
    marker = os.path.join(MODELS_DIR, "ok-" + MODEL_SHA256[:12])
    if os.path.isdir(path) and os.path.exists(marker):
        return path
    zp = os.path.join(MODELS_DIR, "download.zip")
    h = hashlib.sha256()
    with urllib.request.urlopen(MODEL_URL, timeout=120) as r, open(zp, "wb") as f:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != MODEL_SHA256:
        os.remove(zp)
        raise RuntimeError("vosk model sha256 mismatch: " + h.hexdigest())
    tmp = os.path.join(MODELS_DIR, "extract")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(path, ignore_errors=True)
    zipfile.ZipFile(zp).extractall(tmp)
    os.rename(os.path.join(tmp, MODEL_NAME), path)
    shutil.rmtree(tmp, ignore_errors=True)
    os.remove(zp)
    open(marker, "w").close()
    return path


def digits(text):
    """'\u0441\u0434\u0435\u043b\u0430\u0439 \u0434\u0432\u0430\u0434\u0446\u0430\u0442\u044c \u0447\u0435\u0442\u044b\u0440\u0435 \u0433\u0440\u0430\u0434\u0443\u0441\u0430' -> '\u0441\u0434\u0435\u043b\u0430\u0439 24 \u0433\u0440\u0430\u0434\u0443\u0441\u0430'; other words stay."""  # i18n
    out, toks, i = [], text.split(), 0
    while i < len(toks):
        t = toks[i]
        if t in TENS:
            v = TENS[t]
            if i + 1 < len(toks) and toks[i + 1] in ONES:
                v += ONES[toks[i + 1]]
                i += 1
            out.append(str(v))
        elif t in TEENS:
            out.append(str(TEENS[t]))
        elif t in ONES:
            out.append(str(ONES[t]))
        elif t == "\u0441\u0442\u043e":  # i18n
            out.append("100")
        elif t == "\u043d\u043e\u043b\u044c":  # i18n
            out.append("0")
        else:
            out.append(t)
        i += 1
    return " ".join(out)


def _stems(pattern):
    """Plain stems out of a command-map word; [ab] classes are spread, other regexes are skipped."""
    p = pattern.replace("\\b", "")
    m = re.search(r"\[([^\]]+)\]", p)
    if m:
        return [p[:m.start()] + c + p[m.end():] for c in m.group(1)]
    return [] if re.search(r"[\\^$.*+?(){}|]", p) else [p]


def agree(grammar_words, free_words):
    """Share of the grammar words the free recognition also heard (a word matches by 4 leading letters)."""
    pool = list(free_words)
    hit = 0
    for g in grammar_words:
        for i, f in enumerate(pool):
            n = min(4, len(g), len(f))
            if g[:n] == f[:n]:
                del pool[i]
                hit += 1
                break
    return hit / max(1, len(grammar_words))


class Vosk:
    def __init__(self):
        import vosk
        vosk.SetLogLevel(-1)
        self._vosk = vosk
        self.model = vosk.Model(ensure_model())
        self._grammar = ""

    def _forms(self, stem):
        stem = stem.strip().lower()
        if not stem:
            return []
        return [stem + e for e in ENDINGS if self.model.vosk_model_find_word(stem + e) >= 0]

    def build_grammar(self, patterns):
        """patterns: the words of the command map (stems or small regexes); sets the grammar and returns the word count."""
        words = set(NUMBER_WORDS)
        for p in list(patterns) + FIXED:
            for s in _stems(str(p)):
                words.update(self._forms(s))
        words = sorted(w for w in words if self.model.vosk_model_find_word(w) >= 0)
        self._grammar = json.dumps(words + ["[unk]"], ensure_ascii=False)
        return len(words)

    def recognize(self, pcm_i16, grammar=True):
        """(text, mean word confidence) of one utterance; grammar=False is the free pass."""
        rec = (self._vosk.KaldiRecognizer(self.model, 16000, self._grammar) if grammar
               else self._vosk.KaldiRecognizer(self.model, 16000))
        rec.SetWords(True)
        data = pcm_i16.tobytes()
        for i in range(0, len(data), 8000):
            rec.AcceptWaveform(data[i:i + 8000])
        res = json.loads(rec.FinalResult())
        confs = [w.get("conf", 1.0) for w in res.get("result", [])]
        return res.get("text", "").strip(), (sum(confs) / len(confs) if confs else 0.0)
