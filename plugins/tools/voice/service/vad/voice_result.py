"""The spoken result of a voice command: a SHORT phrase, said only after the device itself reports the new state (a few seconds at most), in human names.
Pure functions: the callers hand in how to read a state and how a device is called."""
import time

from voice_state import ENUM_RU

SWITCH = {"m": ("\u0432\u043a\u043b\u044e\u0447\u0451\u043d", "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d"), "f": ("\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u0430", "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u0430"), "n": ("\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u043e", "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e"), "p": ("\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u044b", "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u044b")}  # i18n
NO_ANSWER = {"m": "\u043d\u0435 \u043e\u0442\u0432\u0435\u0442\u0438\u043b", "f": "\u043d\u0435 \u043e\u0442\u0432\u0435\u0442\u0438\u043b\u0430", "n": "\u043d\u0435 \u043e\u0442\u0432\u0435\u0442\u0438\u043b\u043e", "p": "\u043d\u0435 \u043e\u0442\u0432\u0435\u0442\u0438\u043b\u0438"}
MAX_NAMES = 3                    # more targets than this: a count, not a list (a phrase stays under about 1.5 s)


def expected(value):
    """The value a control should report: a bool for on/off, a number, or a lower-case word."""
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ("on", "true", "1"):
        return True
    if v in ("off", "false", "0"):
        return False
    try:
        return float(v)
    except ValueError:
        return v


def matches(exp, got, code=""):
    if exp is None or got is None:
        return False
    if isinstance(exp, bool):
        g = got if isinstance(got, bool) else str(got).strip().lower() in ("on", "true", "1")
        return g == exp
    if isinstance(exp, float):
        try:
            return abs(float(got) - exp) <= (0.6 if code in ("temp", "temperature") else 8)
        except (TypeError, ValueError):
            return False
    return str(got).strip().lower() == exp


def wait_confirm(items, read, timeout=3.0, step=0.25, now=time.time, sleep=time.sleep):
    """Fill item["ok"]: did the device report the expected value within `timeout` seconds? read(did, code) -> the device's current value."""
    end = now() + timeout
    for it in items:
        it["exp"] = expected(it["value"])
        it["ok"] = False
    pending = list(items)
    while pending:
        for it in list(pending):
            if matches(it["exp"], read(it["did"], it["code"]), it["code"]):
                it["ok"] = True
                pending.remove(it)
        if not pending or now() >= end:
            break
        sleep(step)
    return items


def deg(n):
    n = int(round(n))
    last2, last = n % 100, n % 10
    return "%d %s" % (n, "\u0433\u0440\u0430\u0434\u0443\u0441\u043e\u0432" if 11 <= last2 <= 14 else "\u0433\u0440\u0430\u0434\u0443\u0441" if last == 1 else "\u0433\u0440\u0430\u0434\u0443\u0441\u0430" if 2 <= last <= 4 else "\u0433\u0440\u0430\u0434\u0443\u0441\u043e\u0432")  # i18n


def cap(t):
    return t[:1].upper() + t[1:]


def _join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " \u0438 " + names[-1]  # i18n


def phrase_en(items, name, problems_only=False):
    """The English sentences for the same items ("Turned on the kitchen light")."""
    changed, already, failed, settings = {}, [], [], []
    for it in items:
        exp = it.get("exp", expected(it["value"]))
        if not it.get("ok"):
            failed.append(it)
        elif isinstance(exp, bool):
            if matches(exp, it.get("before"), it["code"]):
                already.append(it)
            else:
                lock = it["code"] in ("locked", "lock")
                changed.setdefault(("Locked" if exp else "Unlocked") if lock else ("Turned on" if exp else "Turned off"), []).append(it)
        else:
            settings.append(it)
    out = []
    for verb, group in changed.items():
        if len(group) > MAX_NAMES:
            out.append("%s %d devices" % (verb, len(group)))
        else:
            out.append("%s %s" % (verb, " and ".join(name(g["did"], g["code"], "acc") for g in group)))
    for it in already[:MAX_NAMES]:
        out.append("%s is already %s" % (name(it["did"], it["code"], "nom"), "on" if expected(it["value"]) else "off"))
    for it in settings:
        v = expected(it["value"])
        what = ("%d degrees" % int(round(v)) if it["code"] in ("temp", "temperature") else "volume %d" % v if it["code"] == "volume" and isinstance(v, float)
                else "mode %s" % it["value"] if it["code"] == "mode" else str(it["value"]))
        out.append("%s: %s" % (name(it["did"], it["code"], "nom"), what))
    failed_out = ["%s did not respond" % name(it["did"], it["code"], "nom") for it in failed[:MAX_NAMES]]
    return ". ".join(failed_out if problems_only else out + failed_out)


def phrase(items, name, gender, lang="ru", problems_only=False):
    if lang == "en":
        return phrase_en(items, name, problems_only)
    """items: dicts did, code, value, before (the state before the command), ok (confirmed). name(did, code, case) -> text, case "nom" | "acc";
    gender(did, code) -> m | f | n | p. -> the sentence(s) to say."""
    changed, already, failed, settings = {}, [], [], []          # changed: verb -> the items it names
    for it in items:
        exp = it.get("exp", expected(it["value"]))
        if not it.get("ok"):
            failed.append(it)
        elif isinstance(exp, bool):
            if matches(exp, it.get("before"), it["code"]):
                already.append(it)
            else:
                lock = it["code"] in ("locked", "lock")
                changed.setdefault(("\u0417\u0430\u043f\u0435\u0440\u043b\u0430" if exp else "\u041e\u0442\u043a\u0440\u044b\u043b\u0430") if lock else ("\u0412\u043a\u043b\u044e\u0447\u0438\u043b\u0430" if exp else "\u0412\u044b\u043a\u043b\u044e\u0447\u0438\u043b\u0430"), []).append(it)  # i18n
        else:
            settings.append(it)
    out = []
    for verb, group in changed.items():
        if len(group) > MAX_NAMES:
            out.append("%s %d \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432%s" % (verb, len(group), "\u0430" if len(group) < 5 else ""))  # i18n
        else:
            out.append("%s %s" % (verb, _join([name(g["did"], g["code"], "acc") for g in group])))
    for it in already[:MAX_NAMES]:
        g = gender(it["did"], it["code"])
        out.append("%s \u0443\u0436\u0435 %s" % (cap(name(it["did"], it["code"], "nom")), SWITCH[g][0 if expected(it["value"]) else 1]))  # i18n
    for it in settings:
        v = expected(it["value"])
        what = deg(v) if it["code"] in ("temp", "temperature") else ("\u0433\u0440\u043e\u043c\u043a\u043e\u0441\u0442\u044c %d" % v if it["code"] == "volume" and isinstance(v, float)  # i18n
                                                                      else "\u0440\u0435\u0436\u0438\u043c %s" % ENUM_RU.get(str(it["value"]), it["value"]) if it["code"] == "mode" else str(it["value"]))
        out.append("%s: %s" % (cap(name(it["did"], it["code"], "nom")), what))
    failed_out = ["%s %s" % (cap(name(it["did"], it["code"], "nom")), NO_ANSWER[gender(it["did"], it["code"])]) for it in failed[:MAX_NAMES]]
    return ". ".join(failed_out if problems_only else out + failed_out)         # problems_only: a command that worked is silent (its beep said so)
