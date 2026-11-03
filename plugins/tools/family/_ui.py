"""The app's screen, made by the house for one phone: GET /api/family/me/ui. The phone's token says who it is; its rights
say what it sees; the house says how: tabs, cards and the items on them, labels in the house's language. The app draws the
items natively and knows nothing of device kinds. An item the app does not know is drawn as its label and a button.

Items (t = type; dev + code = the channel: its live value is state[dev].values[code], a change goes to its control):
  switch  {label}                         on/off
  slider  {label, min, max, pct}          pct: the value is sent in percent and min..max is the raw scale of the state
  slider  {label, values: [v ...], unit}  a numeric list (a temperature): the knob stops at these values and sends them
  select  {label, options: [[value, label]]}
  value   {label, unit}                   read only
  remote  {keys: [{slot, dev, code, value?, label}]}   slot: the place on the remote (up, ok, vol_up ...), "" = a chip
ro: true on an item = the phone may only look (its rights); confirm_off: true = ask before switching it off (a lock opens).
A card of the phone's own tab carries "my": {dev, code} (code "*ir" / "*ac" = the whole remote / device), the item it shows.
"""
import json
import re
import sys

import app as core

V = 1
# the places on the app's remote (Remote.kt): a key with one of these sits there, any other is a chip below
SLOTS = {"power", "on", "off", "source", "hdmi", "up", "down", "left", "right", "ok", "menu", "home", "back", "exit", "info",
         "vol_up", "vol_down", "mute", "ch_up", "ch_down", "prev", "rew", "play", "pause", "stop", "ff", "next", "playpause",
         "red", "green", "yellow", "blue"} | {f"d{i}" for i in range(10)}
_ALIAS = {"power_on": "on", "poweron": "on", "power_off": "off", "poweroff": "off"}
_words: dict = {}


def _table(lang: str) -> dict:
    """The dashboard's words in this language (i18n.js) and the plug-ins' (their i18n.json), read once."""
    if lang in _words:
        return _words[lang]
    t = {}
    try:
        s = (core.CHECKOUT / "i18n.js").read_text(encoding="utf-8")
        body = s[s.index("const I18N = ") + len("const I18N = "):s.index("\nconst I18N_NAMES")]
        body = re.sub(r"^(\s*)([a-z]{2}):", r'\1"\2":', body, flags=re.M)
        body = re.sub(r"^\s*//.*$", "", body, flags=re.M)
        body = re.sub(r",(\s*[}\]])", r"\1", body)
        t.update(json.JSONDecoder().raw_decode(body)[0].get(lang) or {})
    except (OSError, ValueError) as e:
        core.log.warning(f"[family] the app's screen: no words from i18n.js ({e})")
    for m in core.plugins:
        t.update((m.get("i18n") or {}).get(lang) or {})
    _words[lang] = t
    return t


def tr(s: str, lang: str) -> str:
    """As the dashboard's i18nLabel: the text, else "a \u00b7 b" part by part, else its longest known first words."""
    if not s or not isinstance(s, str):
        return s
    t = _table(lang)
    if s in t:
        return t[s]
    if " \u00b7 " in s:
        return " \u00b7 ".join(tr(x, lang) for x in s.split(" \u00b7 "))
    w = s.split(" ")
    for n in range(len(w) - 1, 0, -1):
        head = " ".join(w[:n])
        if head in t:
            return t[head] + s[len(head):]
    return s


def slot(code: str, icon=None) -> str:
    """The place of an IR / remote key: its icon when that is a place, else its id."""
    if icon in SLOTS:
        return icon
    k = re.sub(r"^(ir_|key_)", "", str(code or ""))
    k = _ALIAS.get(k, k)
    return k if k in SLOTS else ""


def _tv():
    return sys.modules.get(f"{__package__.rsplit('.', 1)[0]}.tv")


def _visible(d: dict) -> bool:
    if d.get("unsupported") or d.get("ir_blaster"):
        return False
    return not (d.get("ir_appliance") and d.get("ir_card") in core.DEVICES)      # its keys are on the card it extends


def _items(d: dict, lang: str, codes=None) -> list:
    """The items of a device (or of these codes of it): its controls in order, its keys as one remote."""
    out, keys = [], []
    tvp = _tv() if d.get("category") == "tv" else None
    remote = tvp.remote(d, core.DEVICES) if tvp else None
    for c in d.get("controls") or []:
        k, code = c.get("kind"), c.get("code")
        if codes is not None and code not in codes:
            continue
        label = tr(c.get("label") or code, lang)
        if c.get("label") == "-":                                   # a channel the house hides
            continue
        base = {"dev": d["id"], "code": code, "label": label}
        if k in ("ir_press", "tv_key"):
            if remote is None:
                keys.append({"slot": slot(code, c.get("icon") or c.get("key")), "dev": d["id"], "code": code, "label": label})
            continue
        if remote is not None and code == "media":
            continue                                                # its keys are on the remote
        if k == "switch":
            out.append({"t": "value" if c.get("ro") else "switch", **base, **({"confirm_off": True} if c.get("payload_on") == "LOCKED" else {})})
        elif k == "setting_bool":
            out.append({"t": "switch", **base})
        elif k == "bright":
            out.append({"t": "slider", **base, "min": c.get("min", 0), "max": c.get("max", 100), "pct": True})
        elif k == "setting_int":
            out.append({"t": "slider", **base, "min": c.get("min", 0), "max": c.get("max", 100), "pct": False})
        elif k == "setting_enum" and isinstance(c.get("range"), list) and len(c["range"]) >= 5 \
                and all(re.fullmatch(r"-?\d+(\.\d+)?", str(v)) for v in c["range"]):
            out.append({"t": "slider", **base, "values": c["range"],
                        "unit": c.get("unit") or ("\u00b0C" if str(code).endswith("temp") else "")})
        elif k == "setting_enum" and isinstance(c.get("range"), list):
            out.append({"t": "select", **base, "options": [[v, tr(str((c.get("labels") or {}).get(v, v)), lang)] for v in c["range"]]})
        elif k in ("sensor", "sensor_text"):
            out.append({"t": "value", **base, "unit": c.get("unit") or ""})
    if remote:
        keys = [{**x, "label": tr(x["label"], lang)} for x in remote]
    if keys:
        out.append({"t": "remote", "keys": keys})
    return out


def _card(d: dict, lang: str, room_of: dict, ro: bool, codes=None, title=None) -> dict:
    items = _items(d, lang, None if codes and str(codes[0]).startswith("*") else codes)
    if ro:
        items = [{**i, "ro": True} for i in items]
    return {"id": d["id"], "title": title or d.get("name") or d["id"], "sub": room_of.get(d["id"], ""), "items": items}


def screen(rights, my_items: list, lang: str = "") -> dict:
    """The whole screen of a phone with these rights (None = all) and its own items ([{dev, code}]), in the language the
    app asks for (its own; else the house's)."""
    lang = lang if re.fullmatch(r"[a-z]{2}", lang or "") else core.house_lang()
    seen = lambda dev: dev in core.DEVICES and _visible(core.DEVICES[dev]) and (rights is None or dev in rights)
    ro = lambda dev: rights is not None and rights.get(dev) != "control"
    served = {r["id"]: core.served_devices(r["id"]) for r in core.get_rooms()}       # the room a device serves, not where it stands
    rooms = [{**r, "devices": served[r["id"]]} for r in core.get_rooms() if any(seen(x) for x in served[r["id"]])]
    room_of = {x: r.get("name", "") for r in rooms for x in r["devices"]}
    card = lambda dev, codes=None, title=None: _card(core.DEVICES[dev], lang, room_of, ro(dev), codes, title)
    tabs = []
    tabs.append({"id": "my", "title": tr("My screen", lang),
                 "cards": [{**card(i["dev"], [i["code"]] if i.get("code") else None), "my": {"dev": i["dev"], "code": i.get("code") or ""}}
                           for i in my_items if isinstance(i, dict) and seen(i.get("dev"))]})
    sec = [x for x in core.DEVICES if seen(x) and _security(core.DEVICES[x])]
    if sec:
        tabs.append({"id": "sec", "title": tr("Security", lang), "cards": [card(x) for x in sec]})
    favs = [card(f["device"], [f["code"]] if f.get("code") else None, f.get("alias"))
            for f in core.favorites if isinstance(f, dict) and seen(f.get("device"))]
    if favs:
        tabs.append({"id": "fav", "title": tr("Favourites", lang), "cards": favs})
    for r in rooms:
        tabs.append({"id": "room:" + r["id"], "title": r.get("name") or r["id"], "cards": [card(x) for x in r["devices"] if seen(x)]})
    tabs.append({"id": "all", "title": tr("All devices", lang), "cards": [card(x) for x in sorted(core.DEVICES, key=lambda k: str(core.DEVICES[k].get("name") or k).lower()) if seen(x)]})
    return {"v": V, "lang": lang, "tabs": [t for t in tabs if t["cards"] or t["id"] == "my"]}


def _security(d: dict) -> bool:
    """Locks and doors (the app's Security tab): a lock switch or a door / contact channel."""
    return any(c.get("payload_on") == "LOCKED" or c.get("code") in ("locked", "lock", "door", "contact") for c in d.get("controls") or [])
