"""The script of a request (to read English words of a typed phrase) and the fixed phrases of the house: a reply is in the house language
(Settings -> General, voice_vad.HOUSE_LANG); a language with no phrases here answers in Russian. Names, rooms, answers: voice_state."""
import re

CYR = re.compile(r"[\u0410-\u042f\u0430-\u044f\u0401\u0451]")  # i18n
LAT = re.compile(r"[A-Za-z]")

FIXED = {                                                    # Russian -> English
    "\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430.": "Sorry, I did not understand.",  # i18n
    "\u041f\u043e\u043d\u044f\u0442\u043d\u043e.": "Got it.",
    "\u0421\u0434\u0435\u043b\u0430\u043d\u043e.": "Done.",  # i18n
    "\u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.": "Cancelled.",
    "\u0412\u043e\u043f\u0440\u043e\u0441 \u0443\u0441\u0442\u0430\u0440\u0435\u043b.": "That question has expired.",  # i18n
    "\u041d\u0435 \u0440\u0430\u0441\u0441\u043b\u044b\u0448\u0430\u043b\u0430.": "I did not catch that.",
    "\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430, \u043a\u0440\u043e\u043c\u0435 \u0447\u0435\u0433\u043e \u0432\u044b\u043a\u043b\u044e\u0447\u0438\u0442\u044c.": "Sorry, I did not understand what to keep on.",  # i18n
    "\u0413\u0434\u0435? \u0421\u043a\u0430\u0436\u0438, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: \u0432\u043a\u043b\u044e\u0447\u0438 \u0441\u0432\u0435\u0442 \u043d\u0430 \u043a\u0443\u0445\u043d\u0435.": "Where? Say, for example: turn on the kitchen light.",
}
PREFIX = (("\u0421\u0434\u0435\u043b\u0430\u043d\u043e: ", "Done: "), ("\u041d\u0435 \u0432\u044b\u0448\u043b\u043e: ", "Failed: "))  # i18n

# English words of a command or a question -> the Russian stems the house knows them by (rooms, common devices)
EN_STEMS = {"kitchen": "\u043a\u0443\u0445\u043d", "bedroom": "\u0441\u043f\u0430\u043b\u044c\u043d", "salon": "\u0441\u0430\u043b\u043e\u043d", "lounge": "\u0441\u0430\u043b\u043e\u043d", "living": "\u0441\u0430\u043b\u043e\u043d", "bathroom": "\u0432\u0430\u043d\u043d", "bath": "\u0432\u0430\u043d\u043d",  # i18n
            "hall": "\u043a\u043e\u0440\u0438\u0434\u043e\u0440", "hallway": "\u043a\u043e\u0440\u0438\u0434\u043e\u0440", "corridor": "\u043a\u043e\u0440\u0438\u0434\u043e\u0440", "yard": "\u0434\u0432\u043e\u0440", "courtyard": "\u0434\u0432\u043e\u0440", "garden": "\u0441\u0430\u0434",
            "light": "\u0441\u0432\u0435\u0442", "lights": "\u0441\u0432\u0435\u0442", "lamp": "\u043b\u0430\u043c\u043f", "boiler": "\u0431\u043e\u0439\u043b\u0435\u0440", "heater": "\u0431\u043e\u0439\u043b\u0435\u0440", "tv": "\u0442\u0435\u043b\u0435\u0432\u0438\u0437\u043e\u0440",  # i18n
            "television": "\u0442\u0435\u043b\u0435\u0432\u0438\u0437\u043e\u0440", "conditioner": "\u043a\u043e\u043d\u0434\u0438\u0446\u0438\u043e\u043d\u0435\u0440", "ac": "\u043a\u043e\u043d\u0434\u0438\u0446\u0438\u043e\u043d\u0435\u0440", "shredder": "\u0438\u0437\u043c\u0435\u043b\u044c\u0447", "lock": "\u0437\u0430\u043c\u043e\u043a",
            "door": "\u0434\u0432\u0435\u0440"}  # i18n


def lang_of(text: str) -> str:
    """"en" when the Latin letters outnumber the Cyrillic ones (and there are at least three), else "ru"."""
    lat, cyr = len(LAT.findall(text)), len(CYR.findall(text))
    return "en" if lat > cyr and lat >= 3 else "ru"


def tr(text, lang: str):
    """A fixed Russian phrase of the house in `lang`; anything else is returned as it is."""
    if lang != "en" or not text:
        return text
    if text in FIXED:
        return FIXED[text]
    for ru, en in PREFIX:
        if text.startswith(ru):
            return en + text[len(ru):]
    return text


def en_stems(text: str) -> list:
    """The Russian stems of the English room/device words in a phrase ("turn on the kitchen light" -> \u043a\u0443\u0445\u043d, \u0441\u0432\u0435\u0442)."""  # i18n
    return [EN_STEMS[w] for w in re.findall(r"[a-z]+", text.lower()) if w in EN_STEMS]
