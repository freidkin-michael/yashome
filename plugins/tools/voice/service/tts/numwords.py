"""Numbers as words for the TTS (Silero does not read digits), Russian and English: integers up to 999 999 999, decimals ("27,5" is
"27 and 5"), a minus sign, and a % or a degree sign after a number (with the right plural form in Russian)."""
import re

RU_ONES = ["\u043d\u043e\u043b\u044c", "\u043e\u0434\u0438\u043d", "\u0434\u0432\u0430", "\u0442\u0440\u0438", "\u0447\u0435\u0442\u044b\u0440\u0435", "\u043f\u044f\u0442\u044c", "\u0448\u0435\u0441\u0442\u044c", "\u0441\u0435\u043c\u044c", "\u0432\u043e\u0441\u0435\u043c\u044c", "\u0434\u0435\u0432\u044f\u0442\u044c"]  # i18n
RU_FEM = {1: "\u043e\u0434\u043d\u0430", 2: "\u0434\u0432\u0435"}  # i18n
RU_TEENS = ["\u0434\u0435\u0441\u044f\u0442\u044c", "\u043e\u0434\u0438\u043d\u043d\u0430\u0434\u0446\u0430\u0442\u044c", "\u0434\u0432\u0435\u043d\u0430\u0434\u0446\u0430\u0442\u044c", "\u0442\u0440\u0438\u043d\u0430\u0434\u0446\u0430\u0442\u044c", "\u0447\u0435\u0442\u044b\u0440\u043d\u0430\u0434\u0446\u0430\u0442\u044c", "\u043f\u044f\u0442\u043d\u0430\u0434\u0446\u0430\u0442\u044c", "\u0448\u0435\u0441\u0442\u043d\u0430\u0434\u0446\u0430\u0442\u044c", "\u0441\u0435\u043c\u043d\u0430\u0434\u0446\u0430\u0442\u044c",  # i18n
            "\u0432\u043e\u0441\u0435\u043c\u043d\u0430\u0434\u0446\u0430\u0442\u044c", "\u0434\u0435\u0432\u044f\u0442\u043d\u0430\u0434\u0446\u0430\u0442\u044c"]  # i18n
RU_TENS = ["", "", "\u0434\u0432\u0430\u0434\u0446\u0430\u0442\u044c", "\u0442\u0440\u0438\u0434\u0446\u0430\u0442\u044c", "\u0441\u043e\u0440\u043e\u043a", "\u043f\u044f\u0442\u044c\u0434\u0435\u0441\u044f\u0442", "\u0448\u0435\u0441\u0442\u044c\u0434\u0435\u0441\u044f\u0442", "\u0441\u0435\u043c\u044c\u0434\u0435\u0441\u044f\u0442", "\u0432\u043e\u0441\u0435\u043c\u044c\u0434\u0435\u0441\u044f\u0442", "\u0434\u0435\u0432\u044f\u043d\u043e\u0441\u0442\u043e"]  # i18n
RU_HUNDREDS = ["", "\u0441\u0442\u043e", "\u0434\u0432\u0435\u0441\u0442\u0438", "\u0442\u0440\u0438\u0441\u0442\u0430", "\u0447\u0435\u0442\u044b\u0440\u0435\u0441\u0442\u0430", "\u043f\u044f\u0442\u044c\u0441\u043e\u0442", "\u0448\u0435\u0441\u0442\u044c\u0441\u043e\u0442", "\u0441\u0435\u043c\u044c\u0441\u043e\u0442", "\u0432\u043e\u0441\u0435\u043c\u044c\u0441\u043e\u0442", "\u0434\u0435\u0432\u044f\u0442\u044c\u0441\u043e\u0442"]  # i18n
EN_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
           "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def plural(n: int, one: str, few: str, many: str) -> str:
    """The Russian form after a number: one / few (2-4) / many (5-20, 25-30 ...)."""
    n = abs(n)
    last2, last = n % 100, n % 10
    return many if 11 <= last2 <= 14 else one if last == 1 else few if 2 <= last <= 4 else many


def _ru_below_1000(n: int, fem: bool) -> list:
    out = [RU_HUNDREDS[n // 100]] if n >= 100 else []
    n %= 100
    if n >= 20:
        out.append(RU_TENS[n // 10])
        n %= 10
        if n:
            out.append(RU_FEM.get(n, RU_ONES[n]) if fem else RU_ONES[n])
    elif n >= 10:
        out.append(RU_TEENS[n - 10])
    elif n:
        out.append(RU_FEM.get(n, RU_ONES[n]) if fem else RU_ONES[n])
    return [w for w in out if w]


def ru_int(n: int) -> str:
    if n < 0:
        return "\u043c\u0438\u043d\u0443\u0441 " + ru_int(-n)  # i18n
    if n == 0:
        return RU_ONES[0]
    parts = []
    for scale, forms, fem in ((10 ** 6, ("\u043c\u0438\u043b\u043b\u0438\u043e\u043d", "\u043c\u0438\u043b\u043b\u0438\u043e\u043d\u0430", "\u043c\u0438\u043b\u043b\u0438\u043e\u043d\u043e\u0432"), False), (1000, ("\u0442\u044b\u0441\u044f\u0447\u0430", "\u0442\u044b\u0441\u044f\u0447\u0438", "\u0442\u044b\u0441\u044f\u0447"), True)):  # i18n
        k = n // scale % 1000
        if k:
            parts += _ru_below_1000(k, fem) + [plural(k, *forms)]
    parts += _ru_below_1000(n % 1000, False)
    return " ".join(parts)


def en_int(n: int) -> str:
    if n < 0:
        return "minus " + en_int(-n)
    if n < 20:
        return EN_ONES[n]
    if n < 100:
        return EN_TENS[n // 10] + (" " + EN_ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return EN_ONES[n // 100] + " hundred" + (" " + en_int(n % 100) if n % 100 else "")
    for scale, name in ((10 ** 6, "million"), (1000, "thousand")):
        if n >= scale:
            return en_int(n // scale) + " " + name + (" " + en_int(n % scale) if n % scale else "")
    return str(n)


NUM_RE = re.compile(r"(?<![\w.,])(-?)(\d{1,9})(?:[.,](\d{1,6}))?(?!\d)(\s*(?:%|\u00b0\s*[Cc\u0421\u0441]?))?")  # i18n


def normalize(text: str, lang: str) -> str:
    """The text with every number (and a % or \u00b0 after it) written out in the language ("ru" or "en")."""  # i18n
    ru = lang == "ru"

    def words(n: int) -> str:
        return ru_int(n) if ru else en_int(n)

    def repl(m) -> str:
        sign, whole, frac, unit = m.group(1), int(m.group(2)), m.group(3), (m.group(4) or "").strip()
        out = ("\u043c\u0438\u043d\u0443\u0441 " if ru else "minus ") if sign else ""  # i18n
        out += words(whole)
        if frac:
            out += (" \u0438 " if ru else " point ") + (words(int(frac)) if ru else " ".join(en_int(int(d)) for d in frac))  # i18n
        if unit.startswith("%"):
            out += " " + (plural(whole, "\u043f\u0440\u043e\u0446\u0435\u043d\u0442", "\u043f\u0440\u043e\u0446\u0435\u043d\u0442\u0430", "\u043f\u0440\u043e\u0446\u0435\u043d\u0442\u043e\u0432") if ru and not frac else "\u043f\u0440\u043e\u0446\u0435\u043d\u0442\u0430" if ru else "percent")  # i18n
        elif unit.startswith("\u00b0"):  # i18n
            out += " " + (plural(whole, "\u0433\u0440\u0430\u0434\u0443\u0441", "\u0433\u0440\u0430\u0434\u0443\u0441\u0430", "\u0433\u0440\u0430\u0434\u0443\u0441\u043e\u0432") if ru and not frac else "\u0433\u0440\u0430\u0434\u0443\u0441\u0430" if ru else "degrees")  # i18n
        return out
    return NUM_RE.sub(repl, text)
