"""ESPHome firmware generator for LVGL wall panels.

generate(name, layout, starter=...) turns one panel layout into the files
of an ESPHome project: "main.yaml" plus the "icons/<file>.png" it references. A firmware has no language:
its labels are the layout's, the rest the house sends in its own (WORDS_TOPIC, the date on home/clock/date).
Widgets are static LVGL objects updated from MQTT, so a layout change means a
rebuild and an OTA flash. Everything board-specific lives in ../profiles/<hw>.yaml.

Profile format (YAML): label, kind (canvas; list = a knob, gen_knob.py), native {w, h}, rotation (default),
console (logger hardware_uart), lvgl_buffer, can (widget types; [] = all),
yaml (ESPHome text inserted verbatim; must define display `lcd` and light
`backlight`, must not use a key in GEN_KEYS), lists (section -> list items merged
into the generator's i2c/sensor/binary_sensor/interval), touch (body of the one
touchscreen item, id `touch`), options (layout flag -> more lists). Profile text
may use ${name} and ${t_<key>} (a translated word from EN/STRINGS).
"""
import json
import re
import sys
import warnings
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PROFILES_DIR = HERE.parent / "profiles"
ICONS_DIR = HERE / "icons"

DEG = "\u00b0"
DASH = "\u2014"
MIDDOT = "\u00b7"
WIFI_GLYPH = "\uf1eb"

EN = {
    "net_boot": "Connecting to Wi-Fi",
    "net_wifi": "No Wi-Fi",
    "net_home": "No link to home",
    "starter_title": "New panel",
    "starter_hint": "Dashboard: Add -> Panel",
    "ac_title": "Air conditioner",
    "ac_room": "in the room",
    "wx_feels": "feels like",
    "wx_ms": "m/s",
    "date_sample": "Sun 30 September",
    "restart": "restart",
    "safe_mode": "safe mode",
    "factory_reset": "factory reset",
    "reset_reason": "reset reason",
    "temperature": "temperature",
    "humidity": "humidity",
    "illuminance": "illuminance",
    "motion": "motion",
}
EN_WX = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Showers", 81: "Showers",
    82: "Heavy showers", 85: "Snowfall", 86: "Heavy snowfall", 95: "Thunderstorm", 96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}
EN_REMOTE = {
    "power": "power", "on": "on", "off": "off", "volume up": "vol_up", "volume down": "vol_down",
    "mute": "mute", "channel +": "ch_up", "channel -": "ch_down", "up": "up", "down": "down",
    "left": "left", "right": "right", "ok": "ok", "back": "back", "home": "home", "menu": "menu",
    "info": "info", "exit": "exit", "source": "source", "delete": "del", "previous": "prev",
    "next": "next", "rewind": "rew", "fast forward": "ff", "red": "red", "green": "green",
    "yellow": "yellow", "blue": "blue", "stop": "stop", "pause": "pause", "play": "play",
    "guide": "guide", "tools": "tools",
}
def _strings() -> dict:
    """The firmware's words in other languages: keys fw:<key>, fw:wx:<code>, fw:remote:<icon> (names
    joined by |) in the plug-in's i18n.json, next to the dashboard's words."""
    try:
        data = json.loads((HERE.parent / "i18n.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for lang, words in data.items():
        t = out.setdefault(lang, {"wx": {}, "remote": {}})
        for k, v in words.items():
            parts = k.split(":", 2)
            if parts[0] != "fw" or not isinstance(v, str):
                continue
            if len(parts) == 3 and parts[1] == "wx":
                t["wx"][int(parts[2])] = v
            elif len(parts) == 3 and parts[1] == "remote":
                t["remote"].update({name: parts[2] for name in v.split("|")})
            else:
                t[parts[1]] = v
    return {lang: t for lang, t in out.items() if len(t) > 2}


STRINGS = _strings()
LANGS = ("en",) + tuple(STRINGS)
ESP_WIDGETS = ("clock", "date", "label", "sensor", "temp", "toggle", "dimmer", "button", "weather", "ac", "remote")


def tr(lang, key):
    return STRINGS.get(lang, {}).get(key, EN[key])


# The words a screen shows that are not the layout's own (its labels are typed by hand): the house sends them in its
# language, retained on WORDS_TOPIC ({key: text, "wx": {code: text}}); the firmware keeps the last ones (flash) and has
# only the English ones of its own, until the first come. So a firmware never depends on a language.
WORDS_TOPIC = "home/clock/words"
WORD_LEN = 64
SCREEN_WORDS = ("net_boot", "net_wifi", "net_home", "starter_title", "starter_hint", "ac_title", "ac_room", "wx_feels", "wx_ms")


def words(lang):
    """WORDS_TOPIC's payload in that language: every word of a screen and a knob, and the weather's."""
    out = {k: tr(lang, k) for k in SCREEN_WORDS}
    out["wx"] = {str(c): t for c, t in wx_words(lang).items()}
    return out


def word_globals(keys):
    # std::array (an initial value of its own type: ESPHome's array constructor would match a bare {} too)
    return ["  - { id: g_w_%s, type: 'std::array<char, %d>', restore_value: yes, initial_value: 'std::array<char, %d>{}' }"
            % (k, WORD_LEN, WORD_LEN) for k in keys]


def wexpr(key, en):
    """C++: the house's word for key, else its English one."""
    return f"(id(g_w_{key})[0] ? (const char *) id(g_w_{key}).data() : {js(en)})"


def words_msg(keys, apply_lines=(), wx=False):
    """on_message body for WORDS_TOPIC: keep each word (and the weather's), then apply_lines (labels that show one)."""
    lines = ["        - lambda: |-",
             "            json::parse_json(x, [&](JsonObject r) -> bool {"]
    for k in keys:
        lines.append(f'              {{ const char *v = r[{js(k)}] | ""; if (*v) {{ strncpy(id(g_w_{k}).data(), v, {WORD_LEN - 1});'
                     f' id(g_w_{k})[{WORD_LEN - 1}] = 0; }} }}')
    if wx:
        lines += ['              JsonObject w = r["wx"];',
                  "              if (!w.isNull()) { id(g_wx).clear(); for (JsonPair kv : w) id(g_wx)[atoi(kv.key().c_str())] = kv.value().as<std::string>(); }"]
    lines += ["              return true;", "            });"]
    return lines + ["            " + a for a in apply_lines]


def wx_words(lang):
    return STRINGS.get(lang, {}).get("wx", EN_WX)


def has_cyrillic(s):   # i18n: a text that needs the Cyrillic glyphs
    return any("\u0400" <= c <= "\u04ff" for c in s)


# the house may speak any language it has words for: the fonts of what it sends carry all their glyphs
HOUSE_CYRILLIC = any(has_cyrillic(json.dumps(t, ensure_ascii=False)) for t in STRINGS.values())
LETTERS = "".join(chr(c) for c in range(0x41, 0x5B)) + "".join(chr(c) for c in range(0x61, 0x7B)) \
    + ("".join(chr(c) for c in range(0x410, 0x450)) + "\u0401\u0451" if HOUSE_CYRILLIC else "")   # i18n: the Cyrillic letters


RM_BY_NAME = dict(EN_REMOTE)    # labels are input data: match every language
for _t in STRINGS.values():
    RM_BY_NAME.update(_t.get("remote", {}))

RESET_BTN = (0, 0, 36, 36)      # hold the brand icon RESET_HOLD_S s -> factory reset
RESET_HOLD_S = 10
BRAND_PX = 30
RESET_ZONE = (RESET_BTN[0], RESET_BTN[0] + RESET_BTN[2],
              RESET_BTN[1], RESET_BTN[1] + RESET_BTN[3])

MAXPX = {"big": 240, "text": 80}
WFACTOR = 0.56


def desired_px(w):
    """Font size the widget wants, before the family cap."""
    pad = int(w.get("pad", 8))
    ih = max(6, w["h"] - 2 * pad)
    iw = max(6, w["w"] - 2 * pad)
    if not w.get("fit", w["type"] == "clock"):
        return max(8, int(w.get("size", 40)))
    txt = sample_text(w)
    return max(8, int(min(ih, iw / max(1, len(txt) * WFACTOR))))


def fam(w):
    """Family = glyph set, not typeface: a big size gets only the glyphs it uses."""
    return "big" if desired_px(w) > MAXPX["text"] else "text"


def sample_text(w):
    """Worst-case text of a widget, used to fit the font to the box."""
    t = w["type"]
    if t == "clock":
        return "00:00"
    if t == "date":
        return max((tr(lang, "date_sample") for lang in LANGS), key=len)     # the house may speak any of them
    if t in ("temp", "sensor"):
        on_t, off_t = w.get("text_on"), w.get("text_off")
        if on_t or off_t:
            word = max([str(on_t or ""), str(off_t or "")], key=len)
            return (w.get("text", "") + word).strip()
        return (w.get("text", "") + "-99.9" + w.get("suffix", "")).strip()
    if t == "dimmer":
        return (w.get("text", "") + " 100%").strip()
    return w.get("text", "Ag") or "Ag"


def climate_kind(w) -> str:
    """'temp' or 'hum' when the widget shows a measured temperature or a humidity (by its type or its topic), else ''."""
    t = str(w.get("state_topic") or "").lower()
    if "humid" in t:
        return "hum"
    return "temp" if w.get("type") == "temp" or "temperature" in t or "room_temp" in t else ""


def value_expr(w):
    """C++ value of a label: a temperature or a humidity as every screen shows it, a whole number held until the value is
    0.6 away; anything else the string the device sent ("decimals" of older layouts: ignored)."""
    k = climate_kind(w)
    if k:
        return ('[&]{ static int shown = -10000; float v = atof(x.c_str());'
                ' if (shown == -10000 || fabsf(v - shown) >= 0.6f) shown = (int) lroundf(v);'
                ' char b[16]; snprintf(b, sizeof(b), "%d", shown); return std::string(b); }()')
    return "std::string(x)"


def fit_px(w):
    return max(8, min(MAXPX[fam(w)], desired_px(w)))


def font_id(f, px):
    return f"f_{f}_{px}"


def js(s):
    """A string as a JSON literal: valid as a YAML double-quoted scalar and in C++."""
    return json.dumps(s, ensure_ascii=False)


DIM_DARK_TEXT = "0xFFFFFF"


def dim_apply(w, idx, level_expr):
    """C++ line redrawing a dimmer caption: a copy clipped to the fill and one
    clipped to the rest, so the colour edge follows the fill pixel for pixel."""
    wid = "w_" + w["id"]
    x0, bw = w["x"], max(1, w["w"])
    wtext_json = js(w.get("text", ""))
    return (f"int lv_ = {level_expr}; int fw_ = (id(g_dim_on{idx}) ? lv_ : 0) * {bw} / 100; "
            f"if (fw_ < 0) fw_ = 0; if (fw_ > {bw}) fw_ = {bw}; "
            f"lv_obj_set_width(id({wid}_clipa), fw_); "
            f"lv_obj_set_x(id({wid}_clipb), {x0} + fw_); lv_obj_set_width(id({wid}_clipb), {bw} - fw_); "
            f"lv_obj_set_x(id({wid}_lbl), -fw_); "
            f"std::string t_ = std::string({wtext_json}) + \" \" + to_string(lv_) + \"%\"; "
            f"lv_label_set_text(id({wid}_lbl), t_.c_str()); lv_label_set_text(id({wid}_lbl2), t_.c_str());")


WX_KINDS = ("sun", "moon", "psun", "pmoon", "cloud", "fog", "drizzle", "rain", "snow", "storm")
WX_ICON_PX = 96
WX_DIV = {"now": 7.0, "hours": 11.0, "days": 11.0, "both": 15.5}


def wx_mode(w):
    m = w.get("mode") or "auto"
    if m in WX_DIV:
        return m
    if w["h"] >= 260 and w["w"] >= 340:
        return "both"
    return "hours" if w["h"] >= 170 else "now"


def wx_plan(w):
    """All weather tile sizes in pixels, so fonts, objects and the lambda agree."""
    mode = wx_mode(w)
    fs = min(w["h"] / WX_DIV[mode], w["w"] / 16.0)

    def px(k):
        return max(8, int(round(fs * k)))
    p = {"mode": mode, "fs": fs, "padx": int(w["w"] * 0.04), "pady": int(w["h"] * 0.03),
         "ico": px(3.3), "big": min(MAXPX["big"], px(2.6)), "cond": min(MAXPX["text"], px(1.0)),
         "sub": min(MAXPX["text"], px(0.72)), "cell": min(MAXPX["text"], px(0.7)), "cico": px(1.4)}
    inner = w["w"] - 2 * p["padx"]
    p["nh"] = max(3, min(8, int(inner / (fs * 3.6)))) if mode in ("hours", "both") else 0
    p["nd"] = max(3, min(7, int(inner / (fs * 4.0)))) if mode in ("days", "both") else 0
    return p


def wx_fonts(w):
    p = wx_plan(w)
    out = {("big" if p["big"] > MAXPX["text"] else "text", p["big"])}
    for k in ("cond", "sub", "cell"):
        out.add(("text", p[k]))
    return out


def wx_emit(A, w, fg):
    p = wx_plan(w)
    wid = "w_" + w["id"]
    bg = hexcol(w.get("bg"), "0x232A36")
    fs, padx, pady = p["fs"], p["padx"], p["pady"]
    inner = w["w"] - 2 * padx
    f_big = font_id("big" if p["big"] > MAXPX["text"] else "text", p["big"])
    f_cond, f_sub, f_cell = font_id("text", p["cond"]), font_id("text", p["sub"]), font_id("text", p["cell"])
    A("        - obj:")
    A(f"            id: {wid}")
    A(f"            x: {w['x']}")
    A(f"            y: {w['y']}")
    A(f"            width: {w['w']}")
    A(f"            height: {w['h']}")
    A(f"            bg_color: {bg}")
    A("            bg_opa: COVER")
    A("            radius: 14")
    A("            border_width: 0")
    A("            pad_all: 0")
    A("            scrollable: false")
    A("            clickable: false")
    A("            widgets:")
    now_h = p["ico"]
    _strips = (1 if p["nh"] else 0) + (1 if p["nd"] else 0)
    _need = 2 * pady + now_h + _strips * (int(fs * 0.5) + int(fs * 0.45) + 3 * int(p["cell"] * 1.17) + p["cico"])
    spare0 = max(0, (w["h"] - _need) // (_strips + 2))
    A(f"              - image: {{ id: {wid}_ico, x: {padx}, y: {pady + spare0}, src: wx_cloud, scale: {p['ico'] / WX_ICON_PX:.3f} }}")
    tx = padx + p["ico"] + int(fs * 0.4)
    tw = int(p["big"] * 0.56 * 3.6)
    ty = pady + spare0 + max(0, (now_h - int(p["big"] * 1.17)) // 2)
    A(f"              - label: {{ id: {wid}_t, x: {tx}, y: {ty}, width: {tw}, text: \"--{DEG}\", text_font: {f_big}, text_color: {fg} }}")
    dx = tx + tw + int(fs * 0.4)
    dw = max(20, w["w"] - padx - dx)
    cy = pady + spare0 + now_h // 2
    if dw >= 8 * fs:
        A(f"              - label: {{ id: {wid}_cond, x: {dx}, y: {cy - int(p['cond'] * 1.17) - int(fs * 0.05)}, width: {dw},"
          f" long_mode: DOT, text: \"{DASH}\", text_font: {f_cond}, text_color: {fg} }}")
        A(f"              - label: {{ id: {wid}_sub, x: {dx}, y: {cy + int(fs * 0.1)}, width: {dw},"
          f" long_mode: DOT, text: \"\", text_font: {f_sub}, text_color: 0x8A93A3 }}")
    else:
        dy0 = pady + spare0 + now_h + int(fs * 0.2)
        A(f"              - label: {{ id: {wid}_cond, x: {padx}, y: {dy0}, width: {inner}, long_mode: DOT, text: \"{DASH}\", text_font: {f_cond}, text_color: {fg} }}")
        A(f"              - label: {{ id: {wid}_sub, x: {padx}, y: {dy0 + int(p['cond'] * 1.17)}, width: {inner}, long_mode: DOT, text: \"\", text_font: {f_sub}, text_color: 0x8A93A3 }}")
        now_h += int(fs * 0.2) + int(p['cond'] * 1.17) + int(p['sub'] * 1.17)
    inner = w["w"] - 2 * padx
    line_h = int(p["cell"] * 1.17)
    strips = (1 if p["nh"] else 0) + (1 if p["nd"] else 0)
    need = pady + now_h + strips * (int(fs * 0.5) + int(fs * 0.45) + 3 * line_h + p["cico"]) + pady
    spare = max(0, (w["h"] - need) // (strips + 2))
    y = pady + spare + now_h + int(fs * 0.5)
    for strip, n in (("h", p["nh"]), ("d", p["nd"])):
        if not n:
            continue
        A(f"              - obj: {{ x: {padx}, y: {y}, width: {inner}, height: 1, bg_color: 0x2A3240, bg_opa: COVER,"
          f" border_width: 0, radius: 0, pad_all: 0, scrollable: false, clickable: false }}")
        y += int(fs * 0.45)
        cw = inner / n
        for k in range(n):
            cx = padx + int(k * cw)
            cwi = int(cw)
            A(f"              - label: {{ id: {wid}_{strip}{k}_k, x: {cx}, y: {y}, width: {cwi}, text_align: CENTER,"
              f" text: \"\", text_font: {f_cell}, text_color: 0x8A93A3 }}")
            ix = cx + (cwi - p["cico"]) // 2
            A(f"              - image: {{ id: {wid}_{strip}{k}_i, x: {ix}, y: {y + line_h}, src: wx_cloud,"
              f" scale: {p['cico'] / WX_ICON_PX:.3f} }}")
            A(f"              - label: {{ id: {wid}_{strip}{k}_t, x: {cx}, y: {y + line_h + p['cico'] + int(fs * 0.1)}, width: {cwi},"
              f" text_align: CENTER, text: \"\", text_font: {f_cell}, text_color: {fg} }}")
            A(f"              - label: {{ id: {wid}_{strip}{k}_p, x: {cx}, y: {y + 2 * line_h + p['cico'] + int(fs * 0.1)}, width: {cwi},"
              f" text_align: CENTER, text: \"\", text_font: {f_cell}, text_color: 0x6FB5FF }}")
        y += 3 * line_h + p["cico"] + int(fs * 0.5) + spare


WX_KIND_CPP = r"""
            auto kind = [](int c, bool day) -> int {
              if (c == 0 || c == 1) return day ? 0 : 1;
              if (c == 2) return day ? 2 : 3;
              if (c == 3) return 4;
              if (c == 45 || c == 48) return 5;
              if (c >= 51 && c <= 57) return 6;
              if ((c >= 61 && c <= 67) || (c >= 80 && c <= 82)) return 7;
              if ((c >= 71 && c <= 77) || c == 85 || c == 86) return 8;
              if (c >= 95) return 9;
              return 4;
            };"""
WX_IMG_CPP = r"""
            esphome::image::Image* ic[] = {id(wx_sun), id(wx_moon), id(wx_psun), id(wx_pmoon), id(wx_cloud),
                                           id(wx_fog), id(wx_drizzle), id(wx_rain), id(wx_snow), id(wx_storm)};
            char b[160];
            json::parse_json(x, [&](JsonObject r) -> bool {"""


def wx_txt_cpp():
    """C++: a WMO weather code to the house's words for it (WORDS_TOPIC), else the English ones."""
    groups = {}
    for code, word in EN_WX.items():
        groups.setdefault(word, []).append(code)
    lines = ["            auto txt = [](int c) -> const char* {",
             "              auto it = id(g_wx).find(c); if (it != id(g_wx).end()) return it->second.c_str();",
             "              switch (c) {"]
    for word, codes in groups.items():
        cases = " ".join(f"case {c}:" for c in codes)
        lines.append(f"                {cases} return {js(word)};")
    lines += ["                default: return \"\";", "              }", "            };"]
    return "\n".join(lines)


def wx_msg(widgets):
    """on_message body for the weather JSON: one lambda updates every weather tile."""
    lines = ["        - lambda: |-", WX_KIND_CPP + "\n" + wx_txt_cpp() + WX_IMG_CPP]
    sub = js("%s %d" + DEG + " " + MIDDOT + " %d%% " + MIDDOT + " %d %s")
    feels, ms = wexpr("wx_feels", EN["wx_feels"]), wexpr("wx_ms", EN["wx_ms"])
    for w in widgets:
        p = wx_plan(w)
        wid = "w_" + w["id"]
        L = lines.append
        L(f'              snprintf(b, sizeof(b), "%d{DEG}", (int) r["t"]); lv_label_set_text(id({wid}_t), b);')
        L(f'              lv_label_set_text(id({wid}_cond), txt((int) r["c"]));')
        L(f'              snprintf(b, sizeof(b), {sub}, {feels}, (int) r["f"], (int) r["h"], (int) r["w"], {ms});')
        L(f'              lv_label_set_text(id({wid}_sub), b);')
        L(f'              lv_image_set_src(id({wid}_ico), ic[kind((int) r["c"], (int) r["d"] != 0)]->get_lv_image_dsc());')
        for strip, n, key in (("h", p["nh"], "hr"), ("d", p["nd"], "dy")):
            if not n:
                continue
            L(f'              {{ JsonArray a = r["{key}"];')
            for k in range(n):
                L(f'                if (a.size() > {k}) {{ JsonObject o = a[{k}];')
                L(f'                  lv_label_set_text(id({wid}_{strip}{k}_k), o["k"] | "");')
                if strip == "h":
                    L(f'                  snprintf(b, sizeof(b), "%d{DEG}", (int) o["t"]); lv_label_set_text(id({wid}_{strip}{k}_t), b);')
                    L(f'                  lv_image_set_src(id({wid}_{strip}{k}_i), ic[kind((int) o["c"], (int) o["d"] != 0)]->get_lv_image_dsc());')
                else:
                    L(f'                  snprintf(b, sizeof(b), "%d{DEG} / %d{DEG}", (int) o["hi"], (int) o["lo"]); lv_label_set_text(id({wid}_{strip}{k}_t), b);')
                    L(f'                  lv_image_set_src(id({wid}_{strip}{k}_i), ic[kind((int) o["c"], true)]->get_lv_image_dsc());')
                L('                  int pp = (int) o["p"]; if (pp >= 20) snprintf(b, sizeof(b), "%d%%", pp); else b[0] = 0;')
                L(f'                  lv_label_set_text(id({wid}_{strip}{k}_p), b); }}')
            L('              }')
    lines.append("              return true;")
    lines.append("            });")
    return lines


AC_DIV = 10.5    # AC buttons are not checkable: highlight comes only from state topics
AC_ICONS = ("cool", "heat", "dry", "fan", "auto", "low", "med", "max", "on", "off")
AC_ICON_PX = 64


def ac_plan(w):
    fs = min(w["h"] / AC_DIV, w["w"] / 14.0)

    def px(k):
        return max(8, int(round(fs * k)))
    p = {"fs": fs, "padx": int(w["w"] * 0.04), "pady": int(w["h"] * 0.04),
         "title": min(MAXPX["text"], px(0.9)), "big": min(MAXPX["big"], px(2.2)),
         "btn": min(MAXPX["text"], px(0.8)), "row_h": px(1.5), "pw_h": px(1.7), "gap": px(0.3)}
    p["modes"] = [m for m in (w.get("modes") or []) if isinstance(m, list) and len(m) == 2][:8]
    p["fans"] = [f for f in (w.get("fans") or []) if isinstance(f, list) and len(f) == 2][:8]
    return p


def ac_fonts(w):
    p = ac_plan(w)
    return {("big" if p["big"] > MAXPX["text"] else "text", p["big"]), ("text", p["title"]), ("text", p["btn"])}


def ac_button(A, wid, x, y, bw, bh, text, fid, fg, on_click, label_id=None, icon=None, outline=False):
    A("              - button:")
    A(f"                  id: {wid}")
    A(f"                  x: {x}")
    A(f"                  y: {y}")
    A(f"                  width: {bw}")
    A(f"                  height: {bh}")
    A("                  bg_color: 0x2E3746")
    if outline:                                         # off until the device says on: a grey outline
        A("                  bg_opa: TRANSP")
    A(f"                  text_color: {fg}")
    A("                  radius: 10")
    A("                  border_width: 2" if outline else "                  border_width: 0")
    if outline:
        A("                  border_color: 0x5A6578")
    A("                  shadow_width: 0")
    A("                  widgets:")
    lid = f"id: {label_id}, " if label_id else ""
    if icon:
        A(f"                    - image: {{ id: {wid}_i, align: CENTER, src: ic_ac_{icon}, scale: {bh * 0.6 / AC_ICON_PX:.3f},"
          f" image_recolor: {fg}, image_recolor_opa: COVER }}")
    else:
        A(f"                    - label: {{ {lid}align: CENTER, text: {js(text)}, text_font: {fid} }}")
    A("                  on_click:")
    A("                    - lambda: |-")
    for line in on_click:
        A("                        " + line)


def ac_emit(A, w, fg, idx):
    p = ac_plan(w)
    wid = "w_" + w["id"]
    bg = hexcol(w.get("bg"), "0x232A36")
    fs, padx, pady = p["fs"], p["padx"], p["pady"]
    f_big = font_id("big" if p["big"] > MAXPX["text"] else "text", p["big"])
    f_title, f_btn = font_id("text", p["title"]), font_id("text", p["btn"])
    inner = w["w"] - 2 * padx
    A("        - obj:")
    A(f"            id: {wid}")
    A(f"            x: {w['x']}")
    A(f"            y: {w['y']}")
    A(f"            width: {w['w']}")
    A(f"            height: {w['h']}")
    A(f"            bg_color: {bg}")
    A("            bg_opa: COVER")
    A("            radius: 14")
    A("            border_width: 0")
    A("            pad_all: 0")
    A("            scrollable: false")
    A("            widgets:")
    A(f"              - label: {{ id: {wid}_title, x: {padx}, y: {pady}, width: {inner * 3 // 5}, long_mode: DOT,"
      f" text: {js(w.get('text') or EN['ac_title'])}, text_font: {f_title}, text_color: {fg} }}")
    A(f"              - label: {{ id: {wid}_room, x: {padx + inner * 3 // 5}, y: {pady}, width: {inner - inner * 3 // 5},"
      f" text_align: RIGHT, text: \"\", text_font: {f_title}, text_color: 0x8A93A3 }}")
    rows = 3 + (1 if p["modes"] else 0) + (1 if p["fans"] else 0)
    need = 2 * pady + int(p["title"] * 1.17) + p["pw_h"] + max(p["row_h"], int(p["big"] * 1.17)) + (rows - 3) * p["row_h"] + rows * p["gap"]
    spare = max(0, (w["h"] - need) // rows)
    y = pady + int(p["title"] * 1.17) + p["gap"] + spare
    pon = js(w.get("power_on") or "ON")
    poff = js(w.get("power_off") or "OFF")
    pcmd = js(w["power_cmd"])
    pw = f"{wid}_pw"                                     # one power button: its state, a command on its way
    ac_button(A, pw, padx, y, p["pw_h"], p["pw_h"], "", f_btn, fg, [
        f"if (id(g_ac{idx}_w) && millis() < id(g_ac{idx}_w)) return;",
        f"bool on = id(g_ac{idx}_p) == 1;",
        f"id(mqtt_client).publish({pcmd}, std::string(on ? {poff} : {pon}));",
        f"id(g_ac{idx}_w) = millis() + 10000; lv_obj_set_style_opa(id({pw}), LV_OPA_50, 0);",
        f"lv_timer_t *t = lv_timer_create([](lv_timer_t *) {{ if (id(g_ac{idx}_w) && millis() >= id(g_ac{idx}_w)) {{"
        f" id(g_ac{idx}_w) = 0; lv_obj_set_style_opa(id({pw}), LV_OPA_COVER, 0); }} }}, 10050, nullptr);",
        "lv_timer_set_repeat_count(t, 1);"], icon="on", outline=True)
    y += p["pw_h"] + p["gap"] + spare
    row_big = max(p["row_h"], int(p["big"] * 1.17))
    pm_w = int(fs * 1.8)
    tmin, tmax = int(w.get("temp_min", 16)), int(w.get("temp_max", 30))
    step = max(1, int(w.get("temp_step", 1)))
    cmd = js(w.get("temp_cmd", ""))
    tx = padx
    tw = inner
    ac_button(A, f"{wid}_dn", tx, y + (row_big - p["row_h"]) // 2, pm_w, p["row_h"], "-", f_btn, fg, [
        f"int t = id(g_ac{idx}_t) - {step}; if (t < {tmin}) t = {tmin}; id(g_ac{idx}_t) = t;",
        f"char b[16]; snprintf(b, sizeof(b), \"%d{DEG}\", t); lv_label_set_text(id({wid}_t), b);",
        f"id(mqtt_client).publish({cmd}, to_string(t));"])
    ac_button(A, f"{wid}_up", tx + tw - pm_w, y + (row_big - p["row_h"]) // 2, pm_w, p["row_h"], "+", f_btn, fg, [
        f"int t = id(g_ac{idx}_t) + {step}; if (t > {tmax}) t = {tmax}; id(g_ac{idx}_t) = t;",
        f"char b[16]; snprintf(b, sizeof(b), \"%d{DEG}\", t); lv_label_set_text(id({wid}_t), b);",
        f"id(mqtt_client).publish({cmd}, to_string(t));"])
    A(f"              - label: {{ id: {wid}_t, x: {tx + pm_w}, y: {y + max(0, (row_big - int(p['big'] * 1.17)) // 2)},"
      f" width: {tw - 2 * pm_w}, text_align: CENTER, text: \"--{DEG}\", text_font: {f_big}, text_color: {fg} }}")
    y += row_big + p["gap"] + spare
    for key, items, topic in (("m", p["modes"], w.get("mode_cmd", "")), ("f", p["fans"], w.get("fan_cmd", ""))):
        if not items:
            continue
        n = len(items)
        cw = (inner - p["gap"] * (n - 1)) // n
        for k, (val, label) in enumerate(items):
            ac_button(A, f"{wid}_{key}{k}", padx + k * (cw + p["gap"]), y, cw, p["row_h"], label, f_btn, fg, [
                f"id(mqtt_client).publish({js(topic)}, std::string({js(val)}));"],
                icon=val if val in AC_ICONS else None)
        y += p["row_h"] + p["gap"] + spare


def ac_msgs(w, idx, fg_hex="0xFFFFFF"):
    """(topic, yaml lines) for on_message: power, setpoint, mode, fan, room."""
    wid = "w_" + w["id"]
    p = ac_plan(w)
    out = []
    pon = js(w.get("power_on") or "ON")
    if w.get("power_state"):
        out.append((w["power_state"], [
            "        - lambda: |-",
            f"            bool on = (x == {pon} || x == \"true\" || x == \"1\" || x == \"on\");",
            f"            id(g_ac{idx}_p) = on ? 1 : 0; id(g_ac{idx}_w) = 0; lv_obj_set_style_opa(id({wid}_pw), LV_OPA_COVER, 0);",
            f"            lv_obj_set_style_bg_color(id({wid}_pw), lv_color_hex(on ? 0x177A75 : 0x2E3746), LV_PART_MAIN);",
            f"            lv_obj_set_style_bg_opa(id({wid}_pw), on ? LV_OPA_COVER : LV_OPA_TRANSP, LV_PART_MAIN);",
            f"            lv_obj_set_style_border_color(id({wid}_pw), lv_color_hex(on ? 0x3CC2B8 : 0x5A6578), LV_PART_MAIN);",
            f"            lv_obj_set_style_image_recolor(id({wid}_pw_i), lv_color_hex(on ? 0xFFFFFF : 0x9AA3B2), 0);"]))
    if w.get("temp_state"):
        out.append((w["temp_state"], [
            "        - lambda: |-",
            f"            id(g_ac{idx}_t) = atoi(x.c_str()); char b[16]; snprintf(b, sizeof(b), \"%d{DEG}\", id(g_ac{idx}_t));",
            f"            lv_label_set_text(id({wid}_t), b);"]))
    for key, items, topic in (("m", p["modes"], w.get("mode_state")), ("f", p["fans"], w.get("fan_state"))):
        if not items or not topic:
            continue
        lines = ["        - lambda: |-"]
        for k, (val, _label) in enumerate(items):
            lines.append(f"            if (x == {js(val)}) lv_obj_add_state(id({wid}_{key}{k}), LV_STATE_CHECKED);"
                         f" else lv_obj_clear_state(id({wid}_{key}{k}), LV_STATE_CHECKED);")
            if val in AC_ICONS:
                lines.append(f"            lv_obj_set_style_image_recolor(id({wid}_{key}{k}_i), lv_color_hex(x == {js(val)} ? 0xFFFFFF : {fg_hex}), 0);")
        out.append((topic, lines))
    if w.get("room_state"):
        room = js("%s" + DEG + " %s")
        out.append((w["room_state"], [
            "        - lambda: |-",
            f"            char b[96]; snprintf(b, sizeof(b), {room}, {value_expr({'type': 'temp'})}.c_str(), {wexpr('ac_room', EN['ac_room'])});"
            f" lv_label_set_text(id({wid}_room), b);"]))
    return out


RM_ICONS = ("vol_up", "vol_down", "mute", "ch_up", "ch_down", "up", "down", "left", "right", "ok", "back", "home",
            "menu", "info", "exit", "source", "hdmi", "hdmi1", "hdmi2", "hdmi3", "hdmi4", "play", "pause", "stop",
            "prev", "next", "rew", "ff", "del", "tools", "guide", "dot")
RM_DOTS = {"red": "0xE5484D", "green": "0x46A758", "yellow": "0xFFC53D", "blue": "0x3E8DE3"}
RM_ACCENT = {"pon": "0x3CC2B8", "poff": "0xFF6B6B", "play": "0x3CC2B8", "stop": "0xFF6B6B"}
RM_KEYS = set(RM_ICONS) | set(RM_DOTS) | {"power", "on", "off"}
RM_MEDIA = ("prev", "rew", "play", "pause", "stop", "ff", "next")
RM_W = {"k": 2.6, "ok": 2.6, "wide": 5.6, "cap": 1.6, "blank": 2.6}
RM_H = {"k": 2.1, "ok": 2.6, "wide": 2.1, "cap": 2.1, "chip": 1.75}
RM_GAP, RM_SEC_GAP = 0.4, 0.5


def rm_key(bid, label):
    i = re.sub(r"^ir_", "", str(bid or ""))
    if i in RM_KEYS or re.fullmatch(r"d\d", i):
        return i
    lab = str(label or "").strip().lower()
    if lab in RM_BY_NAME:
        return RM_BY_NAME[lab]
    return "d" + lab if re.fullmatch(r"\d", lab) else ""


def rm_dropped(k):
    """Digit keys and delete are left off the tile on purpose."""
    return bool(re.fullmatch(r"d\d", k or "")) or k == "del"


def rm_icon(k):
    if k in ("power", "on"):
        return "ic_ac_on"
    if k == "off":
        return "ic_ac_off"
    if k in RM_DOTS:
        return "ic_ir_dot"
    return f"ic_ir_{k}" if k in RM_ICONS else ""


def rm_cell(kind, key="", btn=None, face="", cls=""):
    return {"kind": kind, "key": key, "btn": btn, "face": face, "cls": cls}


def rm_chip_w(cell):
    lab = cell["btn"].get("label") or cell["btn"]["id"]
    return 1.2 + 0.62 * WFACTOR * len(lab) + (1.7 if rm_icon(cell["key"]) else 0)


def rm_cell_w(c, kx=1.0):
    """Cell width in em; kx stretches keys on a tall tile."""
    if c["kind"] == "chip":
        return rm_chip_w(c)
    return RM_W[c["kind"]] * (kx if c["kind"] != "cap" else 1.0)


def rm_sections(buttons):
    """[{rows, sep, wrap}] by slot; None when too few keys are recognised."""
    by, rest = {}, []
    for b in buttons:
        k = rm_key(b.get("id"), b.get("label"))
        if rm_dropped(k):
            continue
        if k and k not in by:
            by[k] = b
        else:
            rest.append(b)
    if len([k for k in by if not re.fullmatch(r"d\d", k)]) < 4:
        return None

    def btn(k, kind="k", face="", cls=""):
        b = by.pop(k, None)
        return rm_cell(kind, k, b, face, cls) if b else rm_cell("blank")

    def has(*ks):
        return any(k in by for k in ks)
    secs = []
    if has("power", "on", "off", "source", "hdmi"):
        row = []
        if "on" in by or "off" in by:
            if "on" in by:
                row.append(btn("on", "wide", cls="pon"))
            if "off" in by:
                row.append(btn("off", "wide", cls="poff"))
        elif "power" in by:
            row.append(btn("power", "wide", cls="pon"))
        row += [btn(k) for k in ("source", "hdmi") if k in by]
        secs.append({"rows": [row]})
    if has("up", "down", "left", "right", "ok"):
        secs.append({"rows": [[btn("menu"), btn("up"), btn("home")], [btn("left"), btn("ok", "ok"), btn("right")],
                              [btn("back"), btn("down"), btn("exit" if "exit" in by else "info")]]})
    if has("vol_up", "vol_down", "mute"):
        secs.append({"rows": [[btn("vol_down"), btn("vol_up")] + ([btn("mute")] if "mute" in by else [])]})
    if has("ch_up", "ch_down"):
        secs.append({"rows": [[rm_cell("cap", face="CH"), btn("ch_down"), btn("ch_up")]]})
    if has(*RM_MEDIA):
        secs.append({"rows": [[btn(k) for k in RM_MEDIA if k in by]], "wrap": True})
    if has(*RM_DOTS):
        secs.append({"rows": [[btn(k) for k in RM_DOTS]]})
    chips = list(by.values()) + rest
    if chips:
        secs.append({"rows": [[rm_cell("chip", rm_key(b.get("id"), b.get("label")), b) for b in chips]], "sep": True, "wrap": True})
    return secs


def rm_wrap(secs, wem, kx=1.0):
    """Media and chip rows wrap at wem (em), like flex-wrap."""
    out = []
    for s in secs:
        if not s.get("wrap"):
            out.append(s)
            continue
        rows, cur, cw = [], [], 0.0
        for c in s["rows"][0]:
            w = rm_cell_w(c, kx)
            if cur and cw + RM_GAP + w > wem:
                rows.append(cur)
                cur, cw = [], 0.0
            cur.append(c)
            cw += (RM_GAP if cw else 0) + w
        if cur:
            rows.append(cur)
        out.append(dict(s, rows=rows))
    return out


def rm_row_h(row):
    return max((RM_H[c["kind"]] for c in row if c["kind"] != "blank"), default=RM_H["k"])


def rm_em(secs):
    """Layout height in em: sections, gaps between them, chip separator, margins."""
    em = 0.0
    for s in secs:
        em += sum(rm_row_h(r) for r in s["rows"]) + RM_GAP * (len(s["rows"]) - 1) + (RM_SEC_GAP if s.get("sep") else 0)
    return em + RM_SEC_GAP * max(0, len(secs) - 1) + 0.6


def rm_kx(secs, wem):
    """Key stretch (1..1.8): the longest non-wrapping row still fits one line."""
    kx = 1.8
    for s in secs:
        for row in s["rows"]:
            if any(c["kind"] == "chip" for c in row):
                continue
            units = sum(2 if c["kind"] == "wide" else 1 for c in row if c["kind"] != "cap")
            fixed = sum(RM_W["cap"] for c in row if c["kind"] == "cap") + RM_GAP * (len(row) - 1)
            if units:
                kx = min(kx, (wem * 0.97 - fixed - RM_GAP) / (units * 2.6))
    return min(1.8, max(1.0, kx))


def rm_buttons(w):
    return [{"id": str(b.get("id")), "label": b.get("label") or str(b.get("id"))}
            for b in (w.get("buttons") or []) if isinstance(b, dict) and b.get("id")]


def rm_plan(w):
    """px per em, wrapped sections and margins: width <= 9.5 em, height fits all."""
    bs = [b for b in rm_buttons(w) if not rm_dropped(rm_key(b["id"], b["label"]))]
    secs = rm_sections(bs) or [{"rows": [[rm_cell("chip", rm_key(b["id"], b["label"]), b) for b in bs]], "wrap": True}]
    fs = w["w"] / 9.5

    def kx(f):
        return rm_kx(secs, w["w"] * 0.94 / f)
    for _ in range(3):
        wrapped = rm_wrap(secs, w["w"] * 0.94 / fs, kx(fs))
        em = rm_em(wrapped) + (1.1 if w.get("text") else 0)
        fs = min(w["w"] / 9.5, w["h"] * 0.96 / em)
    fs = max(8.0, fs)

    def px(k):
        return max(8, min(MAXPX["text"], int(round(fs * k))))
    return {"fs": fs, "kx": kx(fs), "secs": rm_wrap(secs, w["w"] * 0.94 / fs, kx(fs)), "padx": int(w["w"] * 0.03), "pady": int(w["h"] * 0.02),
            "title": px(0.8), "face": px(0.72), "chip": px(0.62), "cap": px(0.55)}


def rm_fonts(w):
    p = rm_plan(w)
    cells = [c for s in p["secs"] for r in s["rows"] for c in r]
    out = set()
    if w.get("text"):
        out.add(("text", p["title"]))
    if any(c["kind"] not in ("blank", "chip", "cap") and not rm_icon(c["key"]) for c in cells):
        out.add(("text", p["face"]))
    if any(c["kind"] == "chip" for c in cells):
        out.add(("text", p["chip"]))
    if any(c["kind"] == "cap" for c in cells):
        out.add(("text", p["cap"]))
    return out


def rm_emit(A, w, fg):
    p = rm_plan(w)
    fs, wid = p["fs"], "w_" + w["id"]
    bg = hexcol(w.get("bg"), "0x232A36")
    inner = w["w"] - 2 * p["padx"]
    A("        - obj:")
    A(f"            id: {wid}")
    A(f"            x: {w['x']}")
    A(f"            y: {w['y']}")
    A(f"            width: {w['w']}")
    A(f"            height: {w['h']}")
    A(f"            bg_color: {bg}")
    A("            bg_opa: COVER")
    A("            radius: 14")
    A("            border_width: 0")
    A("            pad_all: 0")
    A("            scrollable: false")
    A("            widgets:")
    secs = p["secs"]
    title_h = int(p["title"] * 1.17) + int(round(0.2 * fs)) if w.get("text") else 0
    body = rm_em(secs) * fs
    spare = max(0.0, w["h"] - 2 * p["pady"] - title_h - body) / (len(secs) + 1)
    y = p["pady"] + spare
    if w.get("text"):
        A(f"              - label: {{ x: {p['padx']}, y: {int(y)}, width: {inner}, text_align: CENTER, long_mode: DOT,"
          f" text: {js(w['text'])}, text_font: {font_id('text', p['title'])}, text_color: {fg} }}")
        y += title_h
    y += 0.3 * fs
    n = 0
    for si, s in enumerate(secs):
        if si:
            y += RM_SEC_GAP * fs + spare
        if s.get("sep"):
            A(f"              - obj: {{ x: {p['padx'] + inner // 5}, y: {int(y)}, width: {inner * 3 // 5}, height: 1, bg_color: 0x333A47,"
              f" bg_opa: COVER, border_width: 0, radius: 0 }}")
            y += RM_SEC_GAP * fs
        for ri, row in enumerate(s["rows"]):
            if ri:
                y += RM_GAP * fs
            rh = rm_row_h(row)
            roww = sum(rm_cell_w(c, p["kx"]) for c in row) + RM_GAP * (len(row) - 1)
            x = p["padx"] + (inner - roww * fs) / 2
            for c in row:
                cw, ch = rm_cell_w(c, p["kx"]) * fs, RM_H.get(c["kind"], RM_H["k"])
                cy = y + (rh - ch) * fs / 2
                if c["kind"] == "cap":
                    A(f"              - label: {{ x: {int(x)}, y: {int(cy + (ch * fs - p['cap'] * 1.17) / 2)}, width: {int(cw)}, text_align: RIGHT,"
                      f" text: {json.dumps(c['face'])}, text_font: {font_id('text', p['cap'])}, text_color: 0x8A93A3 }}")
                elif c["kind"] != "blank":
                    n += 1
                    rm_button(A, f"{wid}_b{n}", int(x), int(cy), int(cw), int(ch * fs), c, p, fg, w["device"])
                x += cw + RM_GAP * fs
            y += rh * fs
    if n == 0:
        warnings.warn(f"remote widget '{w['id']}' has no buttons")


def rm_topic(device, bid):
    return f"panel/${{name}}/cmd/{device}/ir_{bid}"


def rm_button(A, bid, x, y, bw, bh, c, p, fg, device):
    k, b = c["key"], c["btn"]
    col = RM_DOTS.get(k) or RM_ACCENT.get(c["cls"]) or RM_ACCENT.get(k) or fg
    ico = rm_icon(k)
    fs = p["fs"]
    A("              - button:")
    A(f"                  id: {bid}")
    A(f"                  x: {x}")
    A(f"                  y: {y}")
    A(f"                  width: {bw}")
    A(f"                  height: {bh}")
    A("                  bg_color: 0x2E3746")
    A(f"                  text_color: {col}")
    A(f"                  radius: {bh // 2 if c['kind'] == 'ok' else int(round(0.5 * fs))}")
    A("                  border_width: 0")
    A("                  shadow_width: 0")
    A("                  pad_all: 0")
    A("                  widgets:")
    lab = b.get("label") or b["id"]
    if c["kind"] == "chip":
        if ico:
            A(f"                    - image: {{ align: LEFT_MID, x: {int(round(0.6 * fs))}, src: {ico}, scale: {1.4 * p['chip'] / AC_ICON_PX:.3f},"
              f" image_recolor: {col}, image_recolor_opa: COVER }}")
            A(f"                    - label: {{ align: LEFT_MID, x: {int(round(2.3 * fs))}, text: {js(lab)},"
              f" text_font: {font_id('text', p['chip'])} }}")
        else:
            A(f"                    - label: {{ align: CENTER, text: {js(lab)}, text_font: {font_id('text', p['chip'])} }}")
    elif ico:
        A(f"                    - image: {{ align: CENTER, src: {ico}, scale: {1.45 * fs / AC_ICON_PX:.3f}, image_recolor: {col}, image_recolor_opa: COVER }}")
    else:
        A(f"                    - label: {{ align: CENTER, text: {js(c['face'] or lab)}, text_font: {font_id('text', p['face'])} }}")
    A("                  on_click:")
    A("                    - lambda: |-")
    A(f"                        id(mqtt_client).publish({js(rm_topic(device, b['id']))}, std::string(\"true\"));")


def hexcol(s, default):
    if isinstance(s, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return "0x" + s[1:]
    return default


def announce_lambda(hw, label_id, indent):
    """Starter: on mqtt connect draw a random six-digit code and announce it retained
    with hw, mac, node name and IPv4 on panel/_new/<mac without colons>."""
    i = indent
    return [
        f"{i}- lambda: |-",
        f"{i}    static char code[8] = \"\";",
        f"{i}    if (!code[0]) snprintf(code, sizeof(code), \"%06u\", (unsigned) (esp_random() % 1000000));",
        f"{i}    lv_label_set_text(id({label_id}), code);",
        f"{i}    std::string mac = get_mac_address_pretty(), flat;",
        f"{i}    for (char c : mac) if (c != ':') flat += c;",
        f"{i}    std::string ip = wifi::global_wifi_component->get_ip_addresses()[0].str();",
        f"{i}    id(mqtt_client).publish(\"panel/_new/\" + flat,",
        f"{i}        std::string(\"{{\\\"hw\\\":\\\"{hw}\\\",\\\"code\\\":\\\"\") + code +",
        f"{i}        \"\\\",\\\"mac\\\":\\\"\" + mac + \"\\\",\\\"ip\\\":\\\"\" + ip +",
        f"{i}        \"\\\",\\\"node\\\":\\\"\" + App.get_name() + \"\\\"}}\", 0, true);",
        f"{i}    static bool listening = false;",          # starters share a node name: this topic is per MAC
        f"{i}    if (!listening) {{",
        f"{i}      listening = true;",
        f"{i}      id(mqtt_client).subscribe(\"panel/_newcmd/\" + flat + \"/safe_mode\",",
        f"{i}          [](const std::string &, const std::string &p) {{ if (p == code) id(btn_safe_mode).press(); }});",
        f"{i}    }}",
    ]


GEN_KEYS = {"substitutions", "esphome", "logger", "button", "script", "ota", "wifi", "captive_portal",
            "safe_mode", "globals", "debug", "sensor", "mqtt", "text_sensor", "time", "font", "image",
            "i2c", "binary_sensor", "lvgl", "interval", "touchscreen", "api"}
LIST_KEYS = ("i2c", "sensor", "binary_sensor", "interval")
PROFILE_KEYS = {"label", "kind", "chip", "native", "rotation", "console", "lvgl_buffer", "can",
                "yaml", "lists", "touch", "options"}


def list_profiles():
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def load_profile(hw):
    known = list_profiles()
    if not isinstance(hw, str) or hw not in known:
        raise ValueError(f"unknown hw profile {hw!r}; profiles present: {', '.join(known) or 'none'}")
    try:
        prof = yaml.safe_load((PROFILES_DIR / f"{hw}.yaml").read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"profile {hw}: not valid YAML: {e}") from None
    if not isinstance(prof, dict):
        raise ValueError(f"profile {hw}: expected a mapping")
    if prof.get("kind") == "list":
        return _knob().check_profile(hw, prof)
    missing = {"label", "kind", "native", "console", "yaml", "touch"} - set(prof)
    if missing:
        raise ValueError(f"profile {hw}: missing keys {sorted(missing)}")
    unknown = set(prof) - PROFILE_KEYS
    if unknown:
        raise ValueError(f"profile {hw}: unknown keys {sorted(unknown)}")
    if prof["kind"] != "canvas":
        raise ValueError(f"profile {hw}: kind {prof['kind']!r} is not supported by this generator (needs canvas)")
    nat = prof["native"]
    if not (isinstance(nat, dict) and isinstance(nat.get("w"), int) and isinstance(nat.get("h"), int)):
        raise ValueError(f"profile {hw}: native must be {{w: <int>, h: <int>}}")
    if int(prof.get("rotation") or 0) % 360 not in (0, 90, 180, 270):
        raise ValueError(f"profile {hw}: rotation must be 0/90/180/270")
    can = prof.get("can") or []
    if not isinstance(can, list) or set(can) - set(ESP_WIDGETS):
        raise ValueError(f"profile {hw}: can must list widget types out of {ESP_WIDGETS}")
    clash = set(re.findall(r"^([A-Za-z_]\w*):", prof["yaml"], re.M)) & GEN_KEYS
    if clash:
        raise ValueError(f"profile {hw}: yaml block uses keys the generator writes: {sorted(clash)}")
    for g in [prof.get("lists") or {}] + list((prof.get("options") or {}).values()):
        if not isinstance(g, dict) or set(g) - set(LIST_KEYS):
            raise ValueError(f"profile {hw}: lists/options may only hold {LIST_KEYS}")
    return prof


def _knob():
    """The generator of kind: list profiles (rotary knobs), next to this file."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import gen_knob
    return gen_knob


def profiles():
    """{hw: {label, kind, chip, w, h, can}} for every profile; w/h at the default rotation."""
    out = {}
    for hw in list_profiles():
        prof = load_profile(hw)
        nw, nh = prof["native"]["w"], prof["native"]["h"]
        if int(prof.get("rotation") or 0) % 180 == 90:
            nw, nh = nh, nw
        out[hw] = {"label": prof["label"], "kind": prof["kind"], "chip": prof.get("chip"), "w": nw, "h": nh,
                   "can": list(prof.get("can") or [])}
    return out


def profile_lists(prof, layout):
    """Section -> list-item text from the profile plus every enabled option."""
    out = {}
    groups = [prof.get("lists") or {}]
    groups += [g for k, g in (prof.get("options") or {}).items() if layout.get(k)]
    for g in groups:
        for key, text in g.items():
            out.setdefault(key, []).append(text.rstrip("\n"))
    return {k: "\n".join(v) for k, v in out.items()}


def indent(text, n):
    pad = " " * n
    return "\n".join(pad + ln if ln.strip() else ln for ln in text.rstrip("\n").split("\n"))


READ_KEYS = ("state_topic", "onoff_topic", "power_state", "temp_state", "mode_state", "fan_state", "room_state")
PUB_KEYS = ("cmd_topic", "dim_topic", "power_cmd", "temp_cmd", "mode_cmd", "fan_cmd")


# widgets greyed while their device is not live (docs/device-state.md); the panels plug-in lists the
# panel's devices that are not on panel/<name>/away (one topic: the MQTT inbound queue holds 31 events)
LIFE_TYPES = ("toggle", "dimmer", "sensor", "temp")
CONFIRM_WAIT_MS = 5000      # a dimmer waits this long for its report (the core's CONFIRM_WAIT_S)


def device_of(w):
    """The house device a widget shows: the <device> of its first home/state/<device>/<code>,
    home/cmd/<device>/<code> or panel/<name>/cmd/<device>/<code> topic (the build's own commands)."""
    for k in READ_KEYS + PUB_KEYS:
        lv = w.get(k).split("/") if isinstance(w.get(k), str) else []
        if len(lv) == 4 and lv[0] == "home" and lv[1] in ("state", "cmd") and lv[2] and lv[3]:
            return lv[2]
        if len(lv) == 5 and lv[0] == "panel" and lv[2] == "cmd" and lv[3] and lv[4]:
            return lv[3]
    return None


# read-only topics every panel shares: the house state, the weather plug-in's copy, the core's clock
SHARED_READ = ("home/state/", "home/weather", "home/clock/")


def check_topics(name, widgets):
    """The node may read home/state/ and its own panel/<name>/ and publish only under
    panel/<name>/; failing here beats a silent ACL denial on the broker."""
    own = (f"panel/{name}/", "panel/${name}/")
    for w in widgets:
        for keys, allowed in ((READ_KEYS, SHARED_READ + own), (PUB_KEYS, own)):
            for k in keys:
                t = w.get(k)
                if t in (None, ""):
                    continue
                if not isinstance(t, str) or not t.startswith(allowed) or any(c in t for c in "+\x23\0"):
                    raise ValueError(f"widget '{w['id']}': {k} {t!r} must start with "
                                     f"{' or '.join(allowed[:-1])} and hold no wildcard")
        if w["type"] == "remote":
            dev = w.get("device")
            if not dev or not isinstance(dev, str) or any(c in dev for c in "/+\x23\0"):
                raise ValueError(f"remote widget '{w['id']}' has no valid device (bind it in the builder)")


def check_layout(name, layout):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,22}[a-z0-9])?", name):
        raise ValueError(f"panel name {name!r}: use 1-24 chars of a-z, 0-9 or '-' inside (it is the node name); an older panel so named: delete it and make it again")
    if not isinstance(layout, dict):
        raise ValueError("layout must be a JSON object")
    widgets = layout.get("widgets", [])
    if not isinstance(widgets, list):
        raise ValueError("layout.widgets must be a list")
    seen = set()
    for w in widgets:
        if not isinstance(w, dict) or not isinstance(w.get("type"), str):
            raise ValueError(f"widget without a type: {w!r}")
        wid = w.get("id")
        if not isinstance(wid, str) or not re.fullmatch(r"[A-Za-z0-9_]+", wid):
            raise ValueError(f"widget id {wid!r}: use letters, digits and '_'")
        if wid in seen:
            raise ValueError(f"widget id '{wid}' is used twice")
        seen.add(wid)
        for k in ("x", "y", "w", "h"):
            if not isinstance(w.get(k), (int, float)) or isinstance(w.get(k), bool):
                raise ValueError(f"widget '{wid}': {k} must be a number")
            w[k] = int(w[k])
        if w["type"] == "ac" and not w.get("power_cmd"):
            raise ValueError(f"ac widget '{wid}' has no power_cmd")
        if w["type"] == "ac" and not w.get("power_state"):        # without it the one power button could only send ON
            raise ValueError(f"ac widget '{wid}' has no power_state: bind it to a device in the builder")


def generate(name, layout, *, starter=False, service_key=None):
    """Files of one ESPHome project: {"main.yaml": bytes, "icons/<f>.png": bytes}. service_key: restart
    and safe mode take only a message carrying it (no broker client can loop a screen with a retained one)."""
    if service_key is not None and not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", str(service_key)):
        raise ValueError("service_key: 16-64 chars of A-Z a-z 0-9 _ -")
    if not isinstance(layout, dict):
        raise ValueError("layout must be a JSON object")
    if load_profile(layout.get("hw", "waveshare7"))["kind"] == "list":
        return _knob().generate(name, layout, starter=starter, service_key=service_key)
    layout = json.loads(json.dumps(layout))
    check_layout(name, layout)
    hw = layout.get("hw", "waveshare7")
    prof = load_profile(hw)
    can = prof.get("can") or []
    if can and not starter:
        bad = sorted({w["type"] for w in layout.get("widgets", []) if w["type"] in ESP_WIDGETS} - set(can))
        if bad:
            raise ValueError(f"hw {hw} does not support widget types {bad}")
    text, icons = _gen(name, layout, prof, hw, bool(starter), service_key)
    files = {"main.yaml": text.encode("utf-8")}
    for f in icons:
        p = ICONS_DIR / f
        if not p.is_file():
            raise ValueError(f"icon {f} is missing from {ICONS_DIR}")
        files[f"icons/{f}"] = p.read_bytes()
    return files


class _Ctx:
    """One main.yaml being written: the inputs, what the widgets need, the output lines."""

    def __init__(self, name, layout, prof, hw, starter, service_key):
        self.name, self.layout, self.prof, self.hw = name, layout, prof, hw
        self.starter, self.service_key = starter, service_key
        self.out = []
        self.A = self.out.append
        self.icons = []

    def T(self, key):
        """An English word of the firmware's own (an entity name, a fallback until the house sends its words)."""
        return EN[key]

    def of(self, wtype):
        return [w for w in self.widgets if w["type"] == wtype]


def _gen(name, layout, prof, hw, starter, service_key=None):
    g = _Ctx(name, layout, prof, hw, starter, service_key)
    _prepare(g)
    for part in (_head, _scripts, _ota_wifi, _globals, _sensors, _mqtt, _text_sensors, _time, _fonts,
                 _images, _hw_lists, _lvgl, _touch):
        part(g)
    return "\n".join(g.out) + "\n", g.icons


def _prepare(g):
    """Check the layout and work out what the sections share: geometry, fonts, widget indexes."""
    layout, prof = g.layout, g.prof
    # camera/voice are Android-panel widgets: LVGL has no camera or microphone
    g.widgets = [] if g.starter else [w for w in layout.get("widgets", []) if w.get("type") not in ("camera", "voice")]
    check_topics(g.name, g.widgets)
    nat_w, nat_h = prof["native"]["w"], prof["native"]["h"]
    rot = layout.get("rotation", prof.get("rotation", 0))
    try:
        rot = int(rot or 0) % 360
    except (TypeError, ValueError):
        raise ValueError(f"rotation must be 0/90/180/270, got {rot!r}") from None
    if rot not in (0, 90, 180, 270):
        raise ValueError(f"rotation must be 0/90/180/270, got {rot}")
    g.rot = rot
    dw = nat_h if rot in (90, 270) else nat_w
    g.W = int(layout.get("w", dw))
    # LvglComponent::rotate_coordinates: touch zones get raw points, so rotate alike
    g.rot_xy = {
        90:  ("touch.y", f"{nat_w} - 1 - touch.x"),
        180: (f"{nat_w} - 1 - touch.x", f"{nat_h} - 1 - touch.y"),
        270: (f"{nat_h} - 1 - touch.y", "touch.x"),
    }.get(rot)
    g.plists = profile_lists(prof, layout)
    g.subst_keys = sorted(set(re.findall(r"\$\{t_(\w+)\}", prof["yaml"] + "\n".join(g.plists.values()) + prof["touch"])))
    for k in g.subst_keys:
        if k not in EN:
            raise ValueError(f"profile {g.hw}: unknown string ${{t_{k}}}")
    g.dimmers, g.wx, g.ac, g.rm = g.of("dimmer"), g.of("weather"), g.of("ac"), g.of("remote")
    g.dim_idx = {id(w): i + 1 for i, w in enumerate(g.dimmers)}
    g.life = [(w, device_of(w)) for w in g.widgets if w["type"] in LIFE_TYPES and device_of(w)]
    g.ac_idx = {id(w): i + 1 for i, w in enumerate(g.ac)}
    g.fonts, g.extra = {}, {}
    for w in g.wx:
        for key in wx_fonts(w):
            g.fonts[key] = font_id(*key)
    for w in g.ac:
        for key in ac_fonts(w):
            g.fonts[key] = font_id(*key)
    for w in g.rm:
        for key in rm_fonts(w):
            g.fonts[key] = font_id(*key)
    for w in g.widgets:
        if w["type"] in ("weather", "ac", "remote"):
            continue
        key = (fam(w), fit_px(w))
        g.fonts[key] = font_id(*key)
        g.extra.setdefault(key, set()).update(str(w.get("text") or "") + str(w.get("suffix") or ""))
        if w["type"] == "date":                                  # the house's date, in whatever language it speaks
            g.extra[key].update(LETTERS)
    g.net_txt = {k: wexpr("net_" + k, EN["net_" + k]) for k in ("boot", "wifi", "home")}     # C++: the house's words


def _head(g):
    A, T, prof = g.A, g.T, g.prof
    A(f"# AUTO-GENERATED from panel layout '{g.name}' ({prof['label']}) by gen.py - do not hand-edit.")
    A("substitutions:")
    A(f"  name: {g.name}")
    for k in g.subst_keys:
        A(f"  t_{k}: {js(T(k))}")
    A("")
    A("esphome:")
    A("  name: ${name}")
    A("  friendly_name: ${name}")
    if g.starter:
        A("  name_add_mac_suffix: true")
    A(prof["yaml"].rstrip("\n"))
    # INFO, not DEBUG: at DEBUG this LVGL build trips the task watchdog
    A("logger: { level: INFO, hardware_uart: %s }" % prof["console"])


def _scripts(g):
    A, T, net_txt = g.A, g.T, g.net_txt
    local = g.starter or g.service_key
    A("button:")
    A("  - platform: restart")
    A("    id: btn_restart")
    A(f'    name: "${{name}} {T("restart")}"')
    A("    internal: true" if local else "    command_topic: panel/${name}/restart/command")
    A("  - platform: safe_mode")
    A("    id: btn_safe_mode")
    A(f'    name: "${{name}} {T("safe_mode")}"')
    A("    internal: true" if local else "    command_topic: panel/${name}/safe_mode/command")
    A("  - platform: factory_reset")
    A("    id: btn_factory_reset")
    A(f'    name: "${{name}} {T("factory_reset")}"')
    A("    internal: true")              # only the local long press: no broker client may wipe a screen
    A("script:")
    A("  - id: net_status")
    A("    mode: restart")
    A("    then:")
    A("      - delay: 1s")
    A("      - lambda: |-")
    A("          const char *t = nullptr;")
    A(f'          if (!wifi::global_wifi_component->is_connected()) t = millis() < 60000 ? {net_txt["boot"]} : {net_txt["wifi"]};')
    A(f'          else if (!id(mqtt_client).is_connected()) t = {net_txt["home"]};')
    A("          if (t) {")
    A("            lv_label_set_text(id(net_txt), t);")
    A("            lv_obj_clear_flag(id(net_ban), LV_OBJ_FLAG_HIDDEN);")
    A("          } else {")
    A("            lv_obj_add_flag(id(net_ban), LV_OBJ_FLAG_HIDDEN);")
    A("          }")
    A("  - id: reset_hold")
    A("    mode: restart")
    A("    then:")
    A("      - delay: 2s")
    A("      - light.turn_off: backlight")
    A("      - delay: 300ms")
    A("      - light.turn_on: backlight")
    A("      - delay: %dms" % (RESET_HOLD_S * 1000 - 2300))
    A("      - button.press: btn_factory_reset")


def _ota_wifi(g):
    A = g.A
    A("ota:")
    A("  - platform: esphome")
    A("    password: !secret ota_password")
    A("    on_begin:")
    A("      then:")
    # Flash writes block the main loop, so LVGL never redraws on its own: show the
    # OTA page and force each redraw. MQTT is dropped, it only competes for buffers.
    A("        - lvgl.page.show: ota_page")
    A("        - light.turn_on: backlight")
    A("        - lambda: 'lv_refr_now(NULL);'")
    A("        - mqtt.disable:")
    A("    on_progress:")
    A("      then:")
    A("        - lambda: |-")
    A("            static int last = -5;")
    A("            int p = (int) x;")
    A("            if (p >= last + 5) {")
    A("              last = p;")
    A("              char buf[16];")
    A("              snprintf(buf, sizeof(buf), \"OTA %d%%\", p);")
    A("              lv_label_set_text(id(ota_txt), buf);")
    A("              lv_bar_set_value(id(ota_bar), p, LV_ANIM_OFF);")
    A("              lv_refr_now(NULL);")
    A("            }")
    A("    on_end:")
    A("      then:")
    A("        - lambda: |-")
    A("            lv_label_set_text(id(ota_txt), \"OTA 100%\");")
    A("            lv_bar_set_value(id(ota_bar), 100, LV_ANIM_OFF);")
    A("            lv_refr_now(NULL);")
    A("    on_error:")
    A("      then:")
    A("        - lambda: |-")
    A("            char buf[16];")
    A("            snprintf(buf, sizeof(buf), \"OTA E%d\", (int) x);")
    A("            lv_label_set_text(id(ota_txt), buf);")
    A("            lv_refr_now(NULL);")
    A("wifi:")
    A("  ssid: !secret wifi_ssid")
    A("  password: !secret wifi_password")
    A("  ap:")
    A('    ssid: "${name}-setup"')
    A("    password: !secret ap_password")
    A("  power_save_mode: none")      # modem sleep made the node unreachable for OTA
    A("  on_connect:")
    A("    - script.execute: net_status")
    A("  on_disconnect:")
    A("    - script.execute: net_status")
    A("captive_portal:")
    A("safe_mode:")
    A("  reboot_timeout: 2min")
    A("")


def _globals(g):
    lines = ["  - { id: g_ac%d_t, type: int, restore_value: no, initial_value: '%d' }"
             % (g.ac_idx[id(w)], int(w.get("temp_min", 16))) for w in g.ac]
    for w in g.ac:                                       # p: the power it reported (-1 unknown); w: until when a tap waits for it
        lines += ["  - { id: g_ac%d_p, type: int, restore_value: no, initial_value: '-1' }" % g.ac_idx[id(w)],
                  "  - { id: g_ac%d_w, type: uint32_t, restore_value: no, initial_value: '0' }" % g.ac_idx[id(w)]]
    for w in g.dimmers:
        i = g.dim_idx[id(w)]
        # on/lvl: what the device reported; t: when a tap's command went (0: none waits),
        # wl/wo: what it asked for (the next tap builds on it); off: the device is offline
        lines += ["  - { id: g_dim_on%d, type: bool, restore_value: no, initial_value: 'true' }" % i,
                  "  - { id: g_dim_lvl%d, type: int, restore_value: no, initial_value: '0' }" % i,
                  "  - { id: g_dim_t%d, type: uint32_t, restore_value: no, initial_value: '0' }" % i,
                  "  - { id: g_dim_wl%d, type: int, restore_value: no, initial_value: '0' }" % i,
                  "  - { id: g_dim_wo%d, type: bool, restore_value: no, initial_value: 'true' }" % i,
                  "  - { id: g_dim_off%d, type: bool, restore_value: no, initial_value: 'false' }" % i]
    lines += word_globals(SCREEN_WORDS)
    if g.wx:
        lines.append("  - { id: g_wx, type: 'std::map<int, std::string>', restore_value: no }")
    g.out.extend(["globals:", *lines, ""])


def _words_apply(g):
    """C++ lines: the labels that show one of the house's words, set again (after they came, and at each connect)."""
    out = []
    if g.starter:
        out += [f"lv_label_set_text(id(new_title), {wexpr('starter_title', EN['starter_title'])});",
                f"lv_label_set_text(id(new_hint), {wexpr('starter_hint', EN['starter_hint'])});"]
    for w in g.ac:
        if not w.get("text"):                                   # a title typed in the layout is the layout's
            out.append(f"lv_label_set_text(id(w_{w['id']}_title), {wexpr('ac_title', EN['ac_title'])});")
    return out


def _sensors(g):
    g.A("""debug:
  update_interval: 10s
sensor:
  - platform: wifi_signal
    id: s_rssi
    name: "${name} wifi rssi"
    update_interval: 20s
    state_topic: panel/${name}/rssi
    accuracy_decimals: 0
    on_value:
      then:
        - lambda: |-
            int q = (int) ((x + 100.0f) * 2.0f);
            if (q > 100) q = 100;
            if (q < 0) q = 0;
            char buf[16];
            snprintf(buf, sizeof(buf), "\uf1eb %d%%", q);
            lv_label_set_text(id(st_wifi), buf);
            uint32_t c = q >= 60 ? 0x3CC2B8 : (q >= 35 ? 0xFFC34D : 0xFF6B6B);
            lv_obj_set_style_text_color(id(st_wifi), lv_color_hex(c), 0);
  - platform: uptime
    name: "${name} uptime"
    update_interval: 30s
    state_topic: panel/${name}/uptime
    accuracy_decimals: 0
  - platform: debug
    free:
      name: "${name} heap free"
      state_topic: panel/${name}/heap_free
      accuracy_decimals: 0
    block:
      name: "${name} heap block"
      state_topic: panel/${name}/heap_block
      accuracy_decimals: 0
    loop_time:
      name: "${name} loop time"
      state_topic: panel/${name}/loop_time
      accuracy_decimals: 0
    psram:
      name: "${name} psram free"
      state_topic: panel/${name}/psram_free
      accuracy_decimals: 0""")
    if g.plists.get("sensor"):
        g.A(indent(g.plists["sensor"], 2))


def _msg_toggle(g, w, st, wid):
    if w.get("state_on"):
        cond = f'return x == {js(str(w["state_on"]))};'
    else:
        cond = 'return x == "true" || x == "1" || x == "on" || x == "ON";'
    return [(st, [
        '        - lvgl.widget.update:',
        f'            id: {wid}',
        '            state:',
        f'              checked: !lambda \'{cond}\'',
    ])]


def _msg_dimmer(g, w, st, wid):
    idx = g.dim_idx[id(w)]
    done = (f'        - lambda: \'id(g_dim_t{idx}) = 0;'
            f' lv_obj_set_style_bg_opa(id({wid}), LV_OPA_COVER, LV_PART_INDICATOR);\'')
    msgs = [(st, [
        done,
        f'        - lambda: \'id(g_dim_lvl{idx}) = atoi(x.c_str());\'',
        '        - lvgl.bar.update:',
        f'            id: {wid}',
        f'            value: !lambda'
        f' \'return id(g_dim_on{idx}) ? id(g_dim_lvl{idx}) : 0;\'',
        '        - lambda: |-',
        '            ' + dim_apply(w, idx, f'id(g_dim_lvl{idx})'),
    ])]
    if w.get("onoff_topic"):
        msgs.append((w["onoff_topic"], [
            done,
            '        - lambda: |-',
            f'            id(g_dim_on{idx}) ='
            f' (x == "true" || x == "1" || x == "on" || x == "ON");',
            '        - lvgl.bar.update:',
            f'            id: {wid}',
            f'            value: !lambda'
            f' \'return id(g_dim_on{idx}) ? id(g_dim_lvl{idx}) : 0;\'',
            '        - lambda: |-',
            '            ' + dim_apply(w, idx, f'id(g_dim_lvl{idx})'),
        ]))
    return msgs


def _msg_value(g, w, st, wid):
    pre = w.get("text", "") if w["type"] == "sensor" else ""
    pre_json = js((pre + " ") if pre else "")
    on_t, off_t = w.get("text_on"), w.get("text_off")
    if on_t or off_t:
        on_json = js(str(on_t or ""))
        off_json = js(str(off_t or ""))
        if w.get("state_on"):
            cond = f'x == {js(str(w["state_on"]))}'
        else:
            cond = 'x == "ON" || x == "on" || x == "1" || x == "true"'
        val = (f'(({cond}) ? std::string({on_json})'
               f' : std::string({off_json}))')
    else:
        suf_json = js(w.get("suffix", ""))
        val = f'{value_expr(w)} + {suf_json}'
    return [(st, [
        '        - lvgl.label.update:',
        f'            id: {wid}',
        '            text: !lambda |-',
        f'              return std::string({pre_json}) + {val};',
    ])]


def _msg_away(g):
    """panel/<name>/away is "/<device>#o/<device>#w/": offline, waiting for its report; such a widget is
    greyed or dimmed, an offline one takes no tap. A device id is an MQTT topic level: it holds no "/"
    and no "#", so "/<id>#" never matches inside another id (docs/device-state.md)."""
    body = ['        - lambda: |-',
            '            auto life_ = [&](const char *d) -> char {',
            '              std::string k_ = std::string("/") + d + "#";',
            '              size_t p_ = x.find(k_);',
            '              return p_ == std::string::npos || p_ + k_.size() >= x.size() ? \'l\' : x[p_ + k_.size()];',
            '            };']
    for w, dev in g.life:
        wid = "w_" + w["id"]
        body.append(f'            {{ char c_ = life_({js(dev)});')
        body.append("              lv_opa_t o_ = c_ == 'o' ? LV_OPA_40 : (c_ == 'w' ? LV_OPA_70 : LV_OPA_COVER);")
        for obj in ((wid, wid + "_clipa", wid + "_clipb") if w["type"] == "dimmer" else (wid,)):
            body.append(f"              lv_obj_set_style_opa(id({obj}), o_, 0);")
        if w["type"] == "dimmer":
            body.append(f"              id(g_dim_off{g.dim_idx[id(w)]}) = c_ == 'o';")
        if w["type"] == "toggle":
            body.append(f"              if (c_ == 'o') lv_obj_clear_flag(id({wid}), LV_OBJ_FLAG_CLICKABLE);"
                        f" else lv_obj_add_flag(id({wid}), LV_OBJ_FLAG_CLICKABLE);")
        body.append('            }')
    return body


# what a widget's own state_topic does to it on the screen (one signature for all: not every one uses g)
_MSGS = {"toggle": _msg_toggle, "dimmer": _msg_dimmer, "sensor": _msg_value, "temp": _msg_value}


def _mqtt(g):
    A = g.A
    A("mqtt:")
    A("  broker: !secret mqtt_host")
    A("  port: !secret mqtt_port")
    A("  username: !secret mqtt_user")
    A("  password: !secret mqtt_password")
    A("  discovery: false")
    A("  on_connect:")
    A("    then:")
    A("      - lambda: 'lv_led_set_color(id(st_link), lv_color_hex(0x3CC2B8));'")
    A("      - delay: 1s")       # publish() inside on_connect is dropped: not CONNECTED yet
    A("      - lambda: |-")
    A("          id(mqtt_client).publish(std::string(\"panel/${name}/fw\"),")
    A("                                  std::string(__DATE__ \" \" __TIME__), 0, true);")
    A("      - lambda: 'lv_obj_add_flag(id(net_ban), LV_OBJ_FLAG_HIDDEN);'")
    if _words_apply(g):
        A("      - lambda: |-")
        g.out.extend("          " + x for x in _words_apply(g))
    if g.starter:
        g.out.extend(announce_lambda(g.hw, "new_code", "      "))
    A("  on_disconnect:")
    A("    then:")
    A("      - lambda: 'lv_led_set_color(id(st_link), lv_color_hex(0xFF6B6B));'")
    A("      - script.execute: net_status")
    A("  id: mqtt_client")
    A("  topic_prefix: panel/${name}")
    A("  keepalive: 60s")            # 15 s dropped the session on one lost packet at weak RSSI
    msgs = []
    for w in g.widgets:
        if w.get("state_topic") and w["type"] in _MSGS:
            msgs += _MSGS[w["type"]](g, w, w["state_topic"], "w_" + w["id"])
    msgs.append(("panel/${name}/motion/state", [
        '        - if:',
        '            condition:',
        '              lambda: \'return x == "ON" || x == "on" || x == "1"'
        ' || x == "true";\'',
        '            then:',
        '              - lvgl.widget.show: w_motion_dot',
        '            else:',
        '              - lvgl.widget.hide: w_motion_dot',
    ]))
    msgs.append((WORDS_TOPIC, words_msg(SCREEN_WORDS, _words_apply(g), wx=bool(g.wx))))
    if g.wx:
        wx_topic = next((w["state_topic"] for w in g.wx if w.get("state_topic")), "home/weather")
        msgs.append((wx_topic, wx_msg(g.wx)))
    for w in g.ac:
        msgs.extend(ac_msgs(w, g.ac_idx[id(w)], hexcol(w.get("fg"), "0xFFFFFF")))
    if g.life:
        msgs.append(("panel/${name}/away", _msg_away(g)))
    for what in ("restart", "safe_mode") if g.service_key and not g.starter else ():
        msgs.append((f"panel/{g.name}/{what}/command", [f"        - lambda: 'if (x == \"{g.service_key}\") id(btn_{what}).press();'"]))
    A("  on_message:")
    by_topic = {}                   # one subscription a topic: each costs a SUBACK and a retained value
    for st, body in msgs:
        by_topic.setdefault(st, []).extend(body)
    for st, body in by_topic.items():
        A(f"    - topic: {js(st)}")
        A("      then:")
        g.out.extend(body)
    A("")


def _text_sensors(g):
    A = g.A
    A("text_sensor:")
    A("  - platform: wifi_info")
    A("    ip_address:")
    A("      id: ip_addr")
    A("      state_topic: panel/${name}/ip")
    A("      retain: true")
    A("    mac_address:")
    A("      id: mac_addr")
    A("      state_topic: panel/${name}/mac")
    A("      retain: true")
    A("  - platform: debug")
    A("    reset_reason:")
    A(f'      name: "${{name}} {g.T("reset_reason")}"')
    A("      state_topic: panel/${name}/reset_reason")
    A("      retain: true")
    for wtype, sid, default in (("clock", "mqtt_time", "home/clock/time"),
                                ("date", "mqtt_date", "home/clock/date")):
        w = next(iter(g.of(wtype)), None)
        if not w:
            continue
        A("  - platform: mqtt_subscribe")
        A(f"    id: {sid}")
        A(f"    topic: {js(w.get('state_topic') or default)}")
        A("    on_value:")
        A("      then:")
        A("        - lvgl.label.update:")
        A(f"            id: w_{w['id']}")
        A("            text: !lambda 'return x;'")
    A("")


def _time(g):
    A, layout = g.A, g.layout
    # clock and date labels print what their topic says; sntp only keeps device time
    A("time:")
    A("  - platform: sntp")
    A("    id: sntp_time")
    if layout.get("ntp_servers"):
        A(f"    servers: {json.dumps([str(s) for s in layout['ntp_servers']][:3])}")
    A("    update_interval: 15min")
    if layout.get("timezone"):
        A(f"    timezone: {js(str(layout['timezone']))}")
    A("")


def _fonts(g):
    A, extra = g.A, g.extra
    cyr = HOUSE_CYRILLIC or any(has_cyrillic("".join(s)) for s in extra.values()) \
        or any(has_cyrillic(json.dumps(w, ensure_ascii=False)) for w in g.rm + g.ac)
    glyphsets = "[GF_Latin_Core, GF_Cyrillic_Core]" if cyr else "[GF_Latin_Core]"
    A("font:")
    for (f, px), fid in sorted(g.fonts.items()):
        if f == "big":
            base = "0123456789:" + DEG + "-. "
            add = "".join(sorted(c for c in extra.get((f, px), set())
                                 if c not in base and c.isprintable()))
            A(f'  - {{ file: "gfonts://Roboto@500", id: {fid}, size: {px}, bpp: 4,'
              f' glyphs: {js(base + add)} }}')
        else:
            A(f'  - {{ file: "gfonts://Roboto@500", id: {fid}, size: {px}, bpp: 4, glyphsets: {glyphsets}, glyphs: ["{DEG}"] }}')
    A('  - { file: "gfonts://Roboto@500", id: f_ota, size: 64, bpp: 4, glyphs: %s }' % js(sorted(set(" .%0123456789AEOT"))))
    A(f'  - {{ file: "gfonts://Roboto@500", id: f_net, size: 28, bpp: 4, glyphsets: {glyphsets} }}')     # the house's words
    A("")


def _images(g):
    A, icons = g.A, g.icons
    icons.append("brand.png")
    A("image:")
    A(f"  - {{ file: icons/brand.png, id: ic_brand, resize: {BRAND_PX}x{BRAND_PX}, type: RGB565, transparency: alpha_channel }}")
    for k in (WX_KINDS if g.wx else ()):
        icons.append(f"wx_{k}.png")
        A(f"  - {{ file: icons/wx_{k}.png, id: wx_{k}, type: RGB565, transparency: alpha_channel }}")
    for k in (AC_ICONS if g.ac else (("on", "off") if g.rm else ())):
        icons.append(f"ac_{k}.png")
        A(f"  - {{ file: icons/ac_{k}.png, id: ic_ac_{k}, type: RGB565, transparency: alpha_channel }}")
    for k in (RM_ICONS if g.rm else ()):
        icons.append(f"ir_{k}.png")
        A(f"  - {{ file: icons/ir_{k}.png, id: ic_ir_{k}, type: RGB565, transparency: alpha_channel }}")
    A("")


def _hw_lists(g):
    if g.plists.get("i2c"):
        g.A("i2c:")
        g.A(indent(g.plists["i2c"], 2))
    if g.plists.get("binary_sensor"):
        g.A("binary_sensor:")
        g.A(indent(g.plists["binary_sensor"], 2))
    g.A("")


def _lv_text(g, w, wid, fid, fg, align, px):
    """clock, date, temp, label, sensor: one label whose text a topic sets (or a fixed one)."""
    A, t = g.A, w["type"]
    _pre = w.get("text", "")
    init = {"clock": "00:00", "date": "", "temp": "--" + w.get("suffix", ""),
            "label": w.get("text", ""),
            "sensor": ((_pre + " ") if _pre else "") + DASH}[t]
    y_lbl = w["y"] + max(0, (w["h"] - int(px * 1.17)) // 2)
    A("        - label:")
    A(f"            id: {wid}")
    A(f"            x: {w['x']}")
    A(f"            y: {y_lbl}")
    A(f"            width: {w['w']}")
    A(f"            text_align: {('CENTER' if align == 'CENTER' else ('LEFT' if align == 'LEFT_MID' else 'RIGHT'))}")
    A(f"            text: {js(init)}")
    A(f"            text_font: {fid}")
    A(f"            text_color: {fg}")


def _lv_toggle(g, w, wid, fid, fg, align, px):
    A = g.A
    bg = hexcol(w.get("bg"), "0x232A36")
    A("        - button:")
    A(f"            id: {wid}")
    A(f"            x: {w['x']}")
    A(f"            y: {w['y']}")
    A(f"            width: {w['w']}")
    A(f"            height: {w['h']}")
    A("            checkable: true")
    A(f"            bg_color: {bg}")
    A(f"            text_color: {fg}")    # on the button so the theme's checked colour wins
    A("            radius: 14")
    A("            border_width: 0")
    A("            shadow_width: 0")
    A("            widgets:")
    A(f"              - label: {{ align: CENTER, text: {js(w.get('text', ''))}, text_font: {fid} }}")
    if w.get("cmd_topic"):
        A("            on_click:")
        if w.get("cmd_on") or w.get("cmd_off"):
            _on = js(w.get("cmd_on", "ON"))
            _off = js(w.get("cmd_off", "OFF"))
            A("              - lambda: |-")
            A(f"                  bool on = lv_obj_has_state(id({wid}), LV_STATE_CHECKED);")
            A(f"                  id(mqtt_client).publish({js(w['cmd_topic'])}, on ? {_on} : {_off});")
        else:
            A(f"              - mqtt.publish: {{ topic: {js(w['cmd_topic'])}, payload: {json.dumps(w.get('cmd_payload', 'toggle'))} }}")


def _lv_button(g, w, wid, fid, fg, align, px):
    A = g.A
    bg = hexcol(w.get("bg"), "0x232A36")
    A("        - button:")
    A(f"            id: {wid}")
    A(f"            x: {w['x']}")
    A(f"            y: {w['y']}")
    A(f"            width: {w['w']}")
    A(f"            height: {w['h']}")
    A(f"            bg_color: {bg}")
    A("            radius: 14")
    A("            border_width: 0")
    A("            shadow_width: 0")
    A("            widgets:")
    A(f"              - label: {{ align: CENTER, text: {js(w.get('text', ''))}, text_font: {fid}, text_color: {fg} }}")
    if w.get("cmd_topic"):
        A("            on_click:")
        A(f"              - mqtt.publish: {{ topic: {js(w['cmd_topic'])}, payload: {js(w.get('cmd_payload', ''))} }}")


def _lv_dimmer(g, w, wid, fid, fg, align, px):
    A = g.A
    bg = hexcol(w.get("bg"), "0x232A36")
    acc = hexcol(w.get("accent"), "0x177A75")
    A("        - bar:")
    A(f"            id: {wid}")
    A(f"            x: {w['x']}")
    A(f"            y: {w['y']}")
    A(f"            width: {w['w']}")
    A(f"            height: {w['h']}")
    A("            min_value: 0")
    A("            max_value: 100")
    A("            value: 0")
    A("            radius: 14")
    A(f"            bg_color: {bg}")
    A("            bg_opa: COVER")
    A(f"            indicator: {{ bg_color: {acc}, bg_opa: COVER, radius: 14 }}")
    init_txt = js((w.get('text', '') + ' 0%').strip())
    for suffix, lbl, col, cw in (("clipa", "lbl2", DIM_DARK_TEXT, 0), ("clipb", "lbl", fg, w['w'])):
        A("        - obj:")
        A(f"            id: {wid}_{suffix}")
        A(f"            x: {w['x']}")
        A(f"            y: {w['y']}")
        A(f"            width: {cw}")
        A(f"            height: {w['h']}")
        A("            bg_opa: TRANSP")
        A("            border_width: 0")
        A("            pad_all: 0")
        A("            radius: 0")
        A("            scrollable: false")
        A("            clickable: false")
        A("            widgets:")
        A("              - label:")
        A(f"                  id: {wid}_{lbl}")
        A("                  align: LEFT_MID")
        A("                  x: 0")
        A(f"                  width: {w['w']}")
        A("                  text_align: CENTER")
        A(f"                  text: {init_txt}")
        A(f"                  text_font: {fid}")
        A(f"                  text_color: {col}")
        A("                  clickable: false")


# each widget type's LVGL objects on the main page; weather, ac and remote draw with fonts of their own.
# One signature for all: an emitter ignores what it does not need (align, px)
_LVGL = {**dict.fromkeys(("clock", "date", "temp", "label", "sensor"), _lv_text),
         "toggle": _lv_toggle, "button": _lv_button, "dimmer": _lv_dimmer,
         "weather": lambda g, w, wid, fid, fg, align, px: wx_emit(g.A, w, fg),
         "ac": lambda g, w, wid, fid, fg, align, px: ac_emit(g.A, w, fg, g.ac_idx[id(w)]),
         "remote": lambda g, w, wid, fid, fg, align, px: rm_emit(g.A, w, fg)}


def _lvgl(g):
    A, T = g.A, g.T
    A("lvgl:")
    A("  displays: [lcd]")
    A("  touchscreens: [touch]")
    if g.rot:
        A(f"  rotation: {g.rot}")
    A(f"  buffer_size: {g.prof.get('lvgl_buffer', '10%')}")
    A("  bg_color: 0x0E1116")
    A("  theme:")
    A("    button:")
    A("      radius: 14")
    A("      checked: { bg_color: 0x177A75, text_color: 0xFFFFFF, border_color: 0x3CC2B8, border_width: 2 }")
    A("  top_layer:")
    A("    widgets:")
    A("      - obj:")
    A("          id: net_ban")
    A("          align: TOP_MID")
    A("          y: 44")
    A("          width: SIZE_CONTENT")
    A("          height: SIZE_CONTENT")
    A("          pad_all: 12")
    A("          radius: 12")
    A("          bg_color: 0x5A1E22")
    A("          bg_opa: COVER")
    A("          border_width: 2")
    A("          border_color: 0xFF6B6B")
    A("          clickable: false")
    A("          scrollbar_mode: \"OFF\"")
    A("          widgets:")
    A("            - label:")
    A("                id: net_txt")
    A("                align: CENTER")
    A(f'                text: {js(EN["net_boot"])}')
    A("                text_font: f_net")
    A("                text_color: 0xFFFFFF")
    if g.starter:
        A("      - obj:")
        A("          id: new_ov")
        A("          align: CENTER")
        A("          width: 100%")
        A("          height: 100%")
        A("          bg_color: 0x0E1116")
        A("          bg_opa: COVER")
        A("          border_width: 0")
        A("          radius: 0")
        A("          clickable: false")
        A("          scrollbar_mode: \"OFF\"")
        A("          widgets:")
        A(f"            - label: {{ id: new_title, align: CENTER, y: -70, text: {js(T('starter_title'))}, text_font: f_net, text_color: 0xE6EAF0 }}")
        A("            - label: { id: new_code, align: CENTER, y: 0, text: \"\", text_font: f_ota, text_color: 0x3CC2B8 }")
        A(f"            - label: {{ id: new_hint, align: CENTER, y: 70, text: {js(T('starter_hint'))}, text_font: f_net, text_color: 0x8A93A3 }}")
    A("  pages:")
    A("    - id: main_page")
    A("      bg_color: 0x0E1116")
    A("      widgets:")
    A("        - obj:")
    A("            id: w_motion_dot")
    A(f"            x: {g.W - 26}")
    A("            y: 10")
    A("            width: 16")
    A("            height: 16")
    A("            radius: 8")
    A("            bg_color: 0xE5484D")
    A("            bg_opa: COVER")
    A("            border_width: 0")
    A("            shadow_width: 0")
    A("            hidden: true")
    for w in g.widgets:
        px = fit_px(w) if w["type"] not in ("weather", "ac", "remote") else 0
        fid = font_id(fam(w), px) if px else ""
        align = {"left": "LEFT_MID", "right": "RIGHT_MID"}.get(w.get("align"), "CENTER")
        emit = _LVGL.get(w["type"])
        if emit:
            emit(g, w, "w_" + w["id"], fid, hexcol(w.get("fg"), "0xFFFFFF"), align, px)
    A("        - label:")
    A("            id: st_wifi")
    A("            align: TOP_RIGHT")
    A("            x: -12")
    A("            y: 6")
    A(f'            text: "{WIFI_GLYPH} --"')
    A("            width: 118")
    A("            text_align: RIGHT")
    A("            text_font: montserrat_20")
    A("            text_color: 0x8A93A3")
    A("            clickable: false")
    A("        - led:")
    A("            id: st_link")
    A("            align_to: { id: st_wifi, align: OUT_LEFT_MID, x: -6 }")
    A("            width: 10")
    A("            height: 10")
    A("            color: 0xFF6B6B")
    A("            brightness: 100%")
    A("        - image:")
    A("            id: brand_ic")
    A("            src: ic_brand")
    A("            x: 3")
    A("            y: 3")
    A("")
    A("    - id: ota_page")
    A("      bg_color: 0x0E1116")
    A("      widgets:")
    A("        - label:")
    A("            id: ota_txt")
    A("            align: CENTER")
    A("            y: -40")
    A('            text: "OTA 0%"')
    A("            text_font: f_ota")
    A("            text_color: 0xE6EAF0")
    A("        - bar:")
    A("            id: ota_bar")
    A("            align: CENTER")
    A("            y: 60")
    A("            width: %d" % min(560, int(g.W * 0.7)))
    A("            height: 24")
    A("            min_value: 0")
    A("            max_value: 100")
    A("            value: 0")
    A("            bg_color: 0x232A36")
    A("            indicator: { bg_color: 0x3CC2B8 }")
    A("")
    if g.dimmers or g.plists.get("interval"):
        A("interval:")
    if g.dimmers:
        A("  - interval: 250ms")
        A("    then:")
        A("      - lambda: |-")
        A("          uint32_t now_ = millis();")
        for w in g.dimmers:
            i = g.dim_idx[id(w)]
            A(f"          if (id(g_dim_t{i}) && now_ - id(g_dim_t{i}) > {CONFIRM_WAIT_MS}) {{ id(g_dim_t{i}) = 0;"
              f" lv_obj_set_style_bg_opa(id(w_{w['id']}), LV_OPA_COVER, LV_PART_INDICATOR); }}")
    if g.plists.get("interval"):
        A(indent(g.plists["interval"], 2))
    if g.dimmers or g.plists.get("interval"):
        A("")


def _touch_dimmer(g, w):
    """A tap on a dimmer: the middle third switches it, the sides step the level by a tenth. The bar
    stays: its fill dims until the device reports (5 s at most); a tap meanwhile builds on the last
    one's value. An offline device's dimmer takes no tap (docs/device-state.md)."""
    A = g.A
    idx = g.dim_idx[id(w)]
    wid = "w_" + w["id"]
    x0, y0 = w["x"], w["y"]
    x1, y1 = w["x"] + w["w"], w["y"] + w["h"]
    ww = max(1, w["w"])
    cmd = js(w["cmd_topic"]) if w.get("cmd_topic") else ""
    dim = js(w["dim_topic"]) if w.get("dim_topic") else ""
    A("          if (tx >= %d && tx <= %d && ty >= %d && ty <= %d) {" % (x0, x1, y0, y1))
    A("            if (id(g_dim_off%d)) return;" % idx)
    A("            bool wt = id(g_dim_t%d) != 0;" % idx)
    A("            int cur = wt ? id(g_dim_wl%d) : id(g_dim_lvl%d);" % (idx, idx))   # the level, not the bar
    A("            bool on = wt ? id(g_dim_wo%d) : id(g_dim_on%d);" % (idx, idx))
    A("            auto sent = [&](int l, bool o) {")
    A("              id(g_dim_wl%d) = l; id(g_dim_wo%d) = o; id(g_dim_t%d) = millis() | 1;" % (idx, idx, idx))
    A("              lv_obj_set_style_bg_opa(id(%s), LV_OPA_40, LV_PART_INDICATOR);" % wid)
    A("            };")
    A("            double rel = (double)(tx - %d) / %d.0;" % (x0, ww))
    if cmd:
        A('            if (rel >= 0.34 && rel <= 0.66) {')
        A('              id(mqtt_client).publish(%s, std::string(!on ? "on" : "off"));' % cmd)
        A('              sent(cur, !on);')
        A('              return;')
        A('            }')
    A("            int k = (cur + 5) / 10;")   # nearest tenth: a lamp echoing 29 for 30 must not stall
    if cmd:
        A("            if (rel < 0.34 && (k <= 0 || !on)) {")
        A('              if (on) { id(mqtt_client).publish(%s, std::string("off")); sent(cur, false); }' % cmd)
        A("              return;")
        A("            }")
    A("            int p = (rel < 0.34) ? (k - 1) * 10 : (k + 1) * 10;")
    A("            if (p < 1) p = 1;")
    A("            if (p > 100) p = 100;")
    if dim:
        A('            id(mqtt_client).publish(%s, to_string(p));' % dim)
    if cmd:
        A('            id(mqtt_client).publish(%s, std::string("on"));' % cmd)
    A("            sent(p, %s);" % ("true" if cmd else "on"))
    A("            return;")
    A("          }")


def _touch(g):
    A = g.A
    A("touchscreen:")
    tl = g.prof["touch"].rstrip("\n").split("\n")
    A("  - " + tl[0])
    for ln in tl[1:]:
        A("    " + ln if ln.strip() else ln)
    A("    on_release:")
    A("      - script.stop: reset_hold")
    A("    on_touch:")
    A("      - lambda: |-")
    if g.rot:
        A("          int rx = %s, ry = %s;" % g.rot_xy)
        A("          if (rx >= %d && rx <= %d && ry >= %d && ry <= %d) {" % RESET_ZONE)
    else:
        A("          if (touch.x >= %d && touch.x <= %d && touch.y >= %d && touch.y <= %d) {"
          % RESET_ZONE)
    A("            id(reset_hold).execute();")
    A("          } else {")
    A("            id(reset_hold).stop();")
    A("          }")
    if not g.dimmers:
        return
    A("      - lambda: |-")
    if g.rot:
        A("          int tx = %s, ty = %s;" % g.rot_xy)
    else:
        A("          int tx = touch.x, ty = touch.y;")
    for w in g.dimmers:
        _touch_dimmer(g, w)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Print the ESPHome main.yaml of one panel layout.")
    ap.add_argument("layout", help="JSON file: one layout, or a {name: layout} map")
    ap.add_argument("name", help="panel name, e.g. panel_1")
    ap.add_argument("--starter", action="store_true", help="starter firmware (empty layout, announce)")
    a = ap.parse_args(argv)
    with open(a.layout, encoding="utf-8") as f:
        data = json.load(f)
    layout = data if "widgets" in data or "hw" in data else data.get(a.name)
    if layout is None:
        sys.exit(f"panel '{a.name}' not in {a.layout}")
    try:
        files = generate(a.name, layout, starter=a.starter)
    except ValueError as e:
        sys.exit(f"error: {e}")
    sys.stdout.write(files["main.yaml"].decode("utf-8"))


if __name__ == "__main__":
    main()
