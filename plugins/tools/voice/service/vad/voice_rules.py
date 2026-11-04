"""Small decision rules of the voice path, kept pure so they can be tested on the exact strings of the live logs."""
import re

from voice_vosk import agree


def time_answer(text, now):
    """The answer to a time, date or weekday question from the clock (no model), else None; `now` is a time.struct_time.
    The same words and sentences as the text box (voice_state.clock): Cyrillic words pick the Russian voice."""
    import time
    import voice_state
    what = voice_state.clock_of(text)
    return voice_state.clock(what, voice_state.lang_of(text) == "en", time.mktime(now)) if what else None


def verb_stems(on_words, off_words):
    """The 4-letter stems of the command verbs of the map ("\u0432\u043a\u043b\u044e\u0447" -> "\u0432\u043a\u043b\u044e")."""  # i18n
    return [w[:4] for w in list(on_words) + list(off_words) if len(w) >= 4]


def is_verb(word, stems):
    return any(word.startswith(s) for s in stems)


def end_quiet_frames(command_ms, started_ms=300, wait=133, normal=20):
    """30 ms frames of silence that end a phrase. command_ms is the speech AFTER the wake word (the node sends the last 1.5 s before the wake with the
    stream: the wake word is no part of the command). Until the command has begun (under started_ms of speech) the end waits 4 s for the first word;
    once it has begun 0.6 s of silence ends it."""
    return wait if command_ms < started_ms else normal


def pin_room(query, words, satellite_room):
    """The model may never choose a room the phrase does not name: a question about a room whose name is not among the words is about the room of the
    satellite that heard it (a satellite in the living room: "what is the temperature" is about the living room). -> the query, with the room set."""
    if not isinstance(query, dict) or query.get("type") != "room":
        return query
    stem = str(query.get("room") or "").strip().lower()[:4]
    if stem and any(len(w) >= 3 and (w.startswith(stem) or stem.startswith(w[:4])) for w in words):
        return query                                                            # the phrase names it
    if satellite_room:
        return {**query, "room": satellite_room}
    return query


FILLERS = {"\u044d\u0439", "\u0435\u0439", "\u0445\u0435\u0439", "hey", "\u0430\u0439", "\u044d\u0445", "\u0430", "\u043d\u0443", "\u0437\u0434\u0435\u0441\u044c", "\u0437\u0434\u0435\u0441\u044c,", "\u0442\u0443\u0442", "\u044d\u0442\u0438", "\u0432\u0435\u0434\u044c", "\u0442\u044b"}      # i18n: the recogniser hears the filler "hey" (Russian "ey") as "here" (Russian "zdes")
# The wake word and how it is also heard (Settings -> Voice: "wake", "wake_variants", "wake_maxdist"); voice_vad.apply_config sets them.
# A variant ending in * is a stem (any ending), any other a whole word. These are the values before the first read of the page.
WAKE = "\u0434\u0436\u0430\u0440\u0432\u0438\u0441"  # i18n
WAKE_VARIANTS = ["\u0431\u0435\u0440\u043b\u043e\u0433\u0430", "\u0431\u0435\u0440\u043b\u043e\u0433*", "\u0432\u0435\u0440\u043b\u043e\u0433*", "\u0431\u0438\u0440\u043b\u043e\u0433*", "\u043c\u0435\u0440\u043b\u043e\u0433*", "\u0431\u0443\u0440\u043b\u043e\u0433*", "\u0434\u0436\u0430\u0440\u0432\u0438\u0441*", "\u0436\u0430\u0440\u0432\u0438\u0441*", "\u0447\u0430\u0440\u0432\u0438\u0441*", "jarvis", "\u043a\u0440\u0438\u0441", "\u043a\u0440\u0438\u0441\u0442\u0438"]
WAKE_MAXDIST = 3
THINKING_AFTER_S = 1.5                  # the "thinking" sound only when the answer takes longer than this (an LLM)


QUESTION_STEMS = ("\u043a\u0430\u043a", "\u0441\u043a\u043e\u043b", "\u043a\u0442\u043e", "\u0447\u0442\u043e", "\u0433\u0434\u0435", "\u043a\u043e\u0433\u0434", "\u043f\u043e\u0447", "\u0441\u043a\u0430", "\u043f\u043e\u043a\u0430\u0436", "\u0440\u0430\u0441\u0441\u043a", "\u0435\u0441\u0442\u044c")      # i18n: a phrase may also begin with a question word, not only with a verb


def _lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def wake_token(w):
    """The wake word or one of its variants as heard (a stem variant "\u0431\u0435\u0440\u043b\u043e\u0433*" takes any ending)."""  # i18n
    return w == WAKE or any(w.startswith(v[:-1]) if v.endswith("*") else w == v for v in WAKE_VARIANTS)


def close_to_wake(w):
    """A word the recogniser made of the wake word (a word a letter or two off it): within WAKE_MAXDIST edits of the wake word or of a whole-word
    variant at least as long, and long enough to be a word of its own. A shorter variant (4 letters) is too short to measure others by."""
    bases = [WAKE] + [v for v in WAKE_VARIANTS if not v.endswith("*") and len(v) >= len(WAKE)]
    return WAKE_MAXDIST > 0 and len(w) >= 5 and any(_lev(w, b) <= WAKE_MAXDIST for b in bases)


def norm_room_tokens(line, slugify):
    """The model may write the room as it reads it in the registry ("room=\u0421\u0430\u043b\u043e\u043d"): the parser knows the slug ("\u0441\u0430\u043b\u043e\u043d")."""  # i18n
    return re.sub(r"(\broom=)(\S+)", lambda m: m.group(1) + slugify(m.group(2)), line)


def strip_wake_phrase(words):
    """The wake phrase at the start of a transcript (the node sends the last second or so of audio before the wake, so "hey <wake word> turn on the light" comes in
    one piece) is removed: optional fillers ("hey" in both languages), the wake word or one of its variants or a word close to them, within the first four words;
    a transcript without a wake word is left as it is."""
    i, seen = 0, False
    while i < min(len(words), 4):
        w = words[i]
        if wake_token(w) or (i < 3 and close_to_wake(w)):
            seen = True
        elif w not in FILLERS:
            break
        i += 1
    return words[i:] if seen else words


def final_message(ok, reply, not_understood="\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430."):  # i18n
    """What the house says when a phrase is done -> (reply, quiet). An understood command was confirmed by a short beep at once: it is silent unless something
    went wrong (its reply then names the device that did not answer); a question is answered aloud; a phrase that was not understood is answered
    "Not understood" with no beep. quiet=True tells the satellite to add no chime of its own."""
    if reply:
        return reply, False
    if ok:
        return None, True
    return not_understood, False


def strip_lead(words, stems, max_drop=3):
    """A mis-heard wake word or a stray word in front of the verb (a short mis-heard word in front of "turn on the light") is dropped: the phrase
    starts at its first command verb when that comes within the first few words."""
    for i, w in enumerate(words[:max_drop + 1]):
        if is_verb(w, stems):
            return words[i:]
    return words


def accept_grammar(gw, fw, stems, threshold):
    """Does the grammar pass count? Yes when the free pass hears the same words; also when the free pass heard NO verb at all but did hear
    every other word of the grammar text ("turn off kitchen" vs a free pass that lost the verb: the zone is real, the verb the free pass lost); or when the grammar
    heard only a verb and the free pass heard no verb ("turn off" vs "drink" in Russian)."""
    if agree(gw, fw) >= threshold:
        return True
    verbs = [w for w in gw if is_verb(w, stems)]
    rest = [w for w in gw if not is_verb(w, stems)]
    if not verbs or any(is_verb(w, stems) for w in fw):
        return False
    return not rest or agree(rest, fw) >= 0.99                 # a clear verb of the grammar and none in the free pass ("turn off" vs "drink" in Russian): the verb stands, the zone is asked for


def answer_kind(text, confirmation):
    """What an utterance is while a question waits: "yes", "no", "empty" (nothing heard) or "command" (a phrase that is neither: the question is
    dropped and the phrase runs as a command of its own)."""
    if not text.strip():
        return "empty"
    c = confirmation(text)
    return "yes" if c is True else "no" if c is False else "command"


def repair_line(line, zones, reg):
    """A model line like "\u043a\u0443\u0445\u043d=on" (a zone stem and a value, no device): the zone map says which device that is. -> a parsed `set`, or None."""  # i18n
    m = re.fullmatch(r"\s*([a-z\u0430-\u044f\u0451_]+)=(on|off)\s*", line.lower())
    if not m:
        return None
    stem = m.group(1).replace("_", "")
    for word, entry in zones.items():
        if len(stem) >= 3 and (word.startswith(stem[:4]) or stem.startswith(word[:4])):
            did, code = entry[1], entry[2] or "state"
            if did in reg.dev and code in reg.dev[did]["controls"]:
                return {"action": "set", "except": [], "intents": [{"device": did, "code": code, "value": m.group(2)}]}
    return None


def guess_reply(parsed, plan, words, stems):
    """When the model GUESSED a device the phrase does not name, the house asks nothing about a device that was not said: it says what is
    missing ("where?") or that it did not understand. None = no guess (the plan stands)."""
    if not any(x.startswith("guess:") for x in plan.get("ask", [])):
        return None
    if parsed["action"] == "all_off":
        return "\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430, \u043a\u0440\u043e\u043c\u0435 \u0447\u0435\u0433\u043e \u0432\u044b\u043a\u043b\u044e\u0447\u0438\u0442\u044c."  # i18n
    if any(is_verb(w, stems) for w in words):
        return "\u0413\u0434\u0435? \u0421\u043a\u0430\u0436\u0438, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: \u0432\u043a\u043b\u044e\u0447\u0438 \u0441\u0432\u0435\u0442 \u043d\u0430 \u043a\u0443\u0445\u043d\u0435."  # i18n
    return "\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430."
