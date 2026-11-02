"""ESPHome firmware generator for rotary knob panels (profiles of kind: list).

A knob has no touch: its layout is an ordered list of up to 4 rows (widgets), each a
toggle, dimmer or climate bound by topics like the screen widgets. Turn = level of
the selected row (dimmer: down to 1 %, one more step down = off; climate: set
temperature), short press = next row, long press = on/off. The screen shows a ring
sector per row with a big number, the clock and the value; it changes only on the
device's own state. Holding the button shows the pairing countdown (factory reset).

Profile keys (kind list): label, kind, chip, native {w, h}, rotation, console,
lvgl_buffer, can (row types), board, display (body of the one display item, id
lcd), spi {clk, mosi}, backlight {pin, inverted}, encoder {a, b, resolution},
button {pin, inverted}. generate() has the same interface as gen.generate().
"""
import json
import math
import re
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import gen  # noqa: E402

KNOB_KEYS = {"label", "kind", "chip", "native", "rotation", "console", "lvgl_buffer", "can",
             "board", "display", "spi", "backlight", "encoder", "button"}
KNOB_NEED = KNOB_KEYS - {"rotation", "lvgl_buffer", "can"}
MAX_ROWS = 4
ROW_TYPES = ("dimmer", "toggle", "climate")
RING_D, RING_W = 236, 16          # ring diameter and thickness, px
SECTOR_GAP, TICK_GAP = 8, 2        # degrees between the rows' sectors and between ticks
BG, LIT_SEL, LIT, UNLIT, TEXT, MUTED = 0x1A1F29, 0x3CC2B8, 0x0F3D3A, 0x000000, 0xFFFFFF, 0x8A93A3
NUM_DIM = 0x2B3240                 # the inactive row's number: barely visible on BG
LOGO = "yash-knob.png"             # brand wordmark in icons/, centre of the screen
PAIR_HOLD_S = 10                   # holding the knob this long shows the pairing countdown
PAIR_GRACE_S = 5                   # ...which can still be cancelled by letting go
LONG_PRESS_MS = 700                # a long push toggles the row; a short one selects the next
PENDING_MS = 3000                  # no state within this after a command: show what is
ON_WORDS = '(x == "true" || x == "1" || x == "on" || x == "ON")'
TEMP_DEFAULTS = (16, 30, 1)        # min, max, step: a common AC range
CLOCK_TOPIC = "home/clock/time"

EN = {
    "knob_on": "on",
    "knob_off": "off",
    "knob_pair": "pairing",
    "knob_pair_hint": "let go to cancel",
}


def tr(lang, key):
    return gen.STRINGS.get(lang, {}).get(key, EN[key] if key in EN else gen.EN[key])


# the words a knob shows (its rows' names are the layout's): the house sends them in its language (gen.WORDS_TOPIC)
KNOB_WORDS = ("knob_on", "knob_off", "knob_pair", "knob_pair_hint", "net_wifi", "net_home", "starter_title", "starter_hint")


def words(lang):
    """A knob's part of gen.WORDS_TOPIC's payload in that language."""
    return {k: tr(lang, k) for k in KNOB_WORDS}


def _en(key):
    return EN[key] if key in EN else gen.EN[key]


def W(key):
    """C++: the house's word for key, else its English one."""
    return gen.wexpr(key, _en(key))


def check_profile(hw, prof):
    """Validate a kind: list profile; gen.load_profile hands these over."""
    missing = KNOB_NEED - set(prof)
    if missing:
        raise ValueError(f"profile {hw}: missing keys {sorted(missing)}")
    unknown = set(prof) - KNOB_KEYS
    if unknown:
        raise ValueError(f"profile {hw}: unknown keys {sorted(unknown)}")
    nat = prof["native"]
    if not (isinstance(nat, dict) and isinstance(nat.get("w"), int) and isinstance(nat.get("h"), int)):
        raise ValueError(f"profile {hw}: native must be {{w: <int>, h: <int>}}")
    if int(prof.get("rotation") or 0) % 360 not in (0, 90, 180, 270):
        raise ValueError(f"profile {hw}: rotation must be 0/90/180/270")
    can = prof.get("can") or []
    if not isinstance(can, list) or set(can) - set(ROW_TYPES):
        raise ValueError(f"profile {hw}: can must list row types out of {ROW_TYPES}")
    if not isinstance(prof["display"], str) or not re.search(r"^id: lcd$", prof["display"], re.M):
        raise ValueError(f"profile {hw}: display must be ESPHome text with id: lcd")
    for key, sub in (("spi", ("clk", "mosi")), ("backlight", ("pin",)),
                     ("encoder", ("a", "b")), ("button", ("pin",))):
        if not isinstance(prof[key], dict) or set(sub) - set(prof[key]):
            raise ValueError(f"profile {hw}: {key} needs {list(sub)}")
    return prof


def load_profile(hw):
    prof = gen.load_profile(hw)
    if prof.get("kind") != "list":
        raise ValueError(f"profile {hw}: kind {prof.get('kind')!r} is not a knob (needs list)")
    return prof


def ticks_per_row(n):
    """Ticks of one row's sector: 5 % each for 1-2 rows, 10 % each for 3-4."""
    return 20 if n <= 2 else 10


def sector_labels(n, radius=64):
    """(row, dx, dy) of each row's number: the middle of its sector, inside the ring."""
    span = 360.0 / n
    out = []
    for i in range(n):
        a = math.radians(270 + i * span + span / 2)
        out.append((i, round(radius * math.cos(a)), round(radius * math.sin(a))))
    return out


def sector_ticks(n):
    """(row, tick, start_deg, end_deg) of every tick; LVGL angles: 0 = 3 o'clock,
    clockwise. Row 0 starts at 12 o'clock, rows follow clockwise like the selection."""
    k, span = ticks_per_row(n), 360.0 / n
    out = []
    for i in range(n):
        a0 = 270 + i * span + SECTOR_GAP / 2
        step = (span - SECTOR_GAP) / k
        for j in range(k):
            s = a0 + j * step + TICK_GAP / 2
            out.append((i, j, round(s) % 360, round(s + step - TICK_GAP) % 360))
    return out


def _bad(wid, what):
    raise ValueError(f"row '{wid}': {what}")


def _topic(w, key, wid, required):
    t = w.get(key)
    if t is None or t == "":
        if required:
            _bad(wid, f"no {key}")
        return None
    if not isinstance(t, str) or any(ch in t for ch in '"\n+#'):
        _bad(wid, f"{key} must be a topic without quotes, newlines, + or #")
    return t


def _payload(w, key, wid, default):
    v = w.get(key)
    if v is None or v == "":
        return default
    v = str(v)
    if len(v) > 32 or any(ch in v for ch in '"\n'):
        _bad(wid, f"{key}: a short string without quotes")
    return v


def _int(w, key, wid, default, lo, hi):
    v = w.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        _bad(wid, f"{key} must be an integer {lo}..{hi}")
    return v


# an empty knob (starter or no rows) shows logo, clock and status only;
# this row exists for the C++ arrays alone and never publishes
EMPTY_ROW = {"id": "empty", "dim": False, "clim": False, "onoff_topic": "", "cmd_topic": "",
             "state_topic": None, "dim_topic": None, "state_max": 100, "cmd_max": 100,
             "temp_min": 0, "temp_max": 100, "temp_step": 1, "cmd_on": "on", "cmd_off": "off",
             "state_on": None}


def load_rows(name, layout, prof, starter):
    """Check the layout and return normalized rows (every key filled)."""
    widgets = [] if starter else layout.get("widgets") or []
    if not isinstance(widgets, list):
        raise ValueError("layout.widgets must be a list")
    if len(widgets) > MAX_ROWS:
        raise ValueError(f"a knob has up to {MAX_ROWS} rows, not {len(widgets)}")
    step = layout.get("step", 5)
    if isinstance(step, bool) or not isinstance(step, int) or not 1 <= step <= 50:
        raise ValueError("step must be an integer 1..50")
    can = prof.get("can") or ROW_TYPES
    rows, seen, checked = [], set(), []
    for i, w in enumerate(widgets):
        if not isinstance(w, dict):
            raise ValueError(f"row without a type: {w!r}")
        wid = w.get("id") or f"row{i}"
        if not isinstance(wid, str) or not re.fullmatch(r"[A-Za-z0-9_]+", wid):
            raise ValueError(f"widget id {wid!r}: use letters, digits and '_'")
        if wid in seen:
            raise ValueError(f"widget id '{wid}' is used twice")
        seen.add(wid)
        wtype = w.get("type")
        if wtype not in ROW_TYPES or wtype not in can:
            _bad(wid, f"type {wtype!r}; a knob row is one of {'/'.join(t for t in ROW_TYPES if t in can)}")
        text = str(w.get("text") or "")
        if any(ch in text for ch in '"\n') or len(text) > 24:
            _bad(wid, "text: up to 24 chars, no quotes or newlines")
        dim = wtype == "dimmer"
        clim = wtype == "climate"
        value_row = dim or clim          # a row that carries a number next to on/off
        own = _topic(w, "onoff_topic", wid, required=clim)
        onoff = own or _topic(w, "state_topic", wid, required=True)
        row = {
            "id": wid, "dim": dim, "clim": clim, "own_onoff": bool(own),
            "onoff_topic": onoff,
            "cmd_topic": _topic(w, "cmd_topic", wid, required=not (dim and not own)) or "",
            "state_topic": _topic(w, "state_topic", wid, required=True) if value_row else None,
            "dim_topic": _topic(w, "dim_topic", wid, required=True) if value_row else None,
            "state_max": _int(w, "state_max", wid, 100, 1, 65535) if dim else 100,
            "cmd_max": _int(w, "cmd_max", wid, 100, 1, 65535) if dim else 100,
            "temp_min": _int(w, "temp_min", wid, TEMP_DEFAULTS[0], -50, 100) if clim else 0,
            "temp_max": _int(w, "temp_max", wid, TEMP_DEFAULTS[1], -50, 100) if clim else 100,
            "temp_step": _int(w, "temp_step", wid, TEMP_DEFAULTS[2], -50, 100) if clim else 1,
            "cmd_on": _payload(w, "cmd_on", wid, "on"),
            "cmd_off": _payload(w, "cmd_off", wid, "off"),
            "state_on": _payload(w, "state_on", wid, None),
        }
        if clim and not (row["temp_min"] < row["temp_max"] and row["temp_step"] >= 1):
            _bad(wid, "needs temp_min < temp_max and temp_step >= 1")
        if w.get("cmd_payload"):
            warnings.warn(f"knob row '{wid}': cmd_payload is ignored, a press sends cmd_on/cmd_off")
        rows.append(row)
        checked.append(dict(row, type=wtype))
    gen.check_topics(name, checked)
    return rows or [EMPTY_ROW]


def _on_expr(row):
    return f'(x == {gen.js(row["state_on"])})' if row["state_on"] else ON_WORDS


def generate(name, layout, *, starter=False, service_key=None):
    """Files of one ESPHome project: {"main.yaml": bytes, "icons/<f>.png": bytes}; see gen.generate."""
    if service_key is not None and not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", str(service_key)):
        raise ValueError("service_key: 16-64 chars of A-Z a-z 0-9 _ -")
    if not isinstance(layout, dict):
        raise ValueError("layout must be a JSON object")
    layout = json.loads(json.dumps(layout))
    gen.check_layout(name, {"widgets": []})        # the node-name rule
    hw = layout.get("hw", "knob128")
    prof = load_profile(hw)
    rows = load_rows(name, layout, prof, bool(starter))
    text = _gen(name, layout, prof, hw, rows, bool(starter), service_key)
    p = gen.ICONS_DIR / LOGO
    if not p.is_file():
        raise ValueError(f"icon {LOGO} is missing from {gen.ICONS_DIR}")
    return {"main.yaml": text.encode("utf-8"), f"icons/{LOGO}": p.read_bytes()}


def _globals(A, n):
    na = max(n, 2)                 # ESPHome does not compile a one-element array global
    A("globals:")
    for gid in ("g_wifi_ok", "g_mqtt_ok"):
        A(f"  - {{ id: {gid}, type: bool, restore_value: no, initial_value: 'false' }}")
    A("  - { id: g_sel, type: int, restore_value: yes, initial_value: '0' }")
    for gid, typ, zero in (("g_lvl", "int", "0"), ("g_on", "bool", "false"), ("g_tgt", "int", "0"),
                           ("g_ton", "bool", "false"), ("g_pend", "uint32_t", "0"),
                           ("g_last", "int", "0")):
        init = "{" + ", ".join([zero] * na) + "}"
        A(f"  - {{ id: {gid}, type: '{typ}[{na}]', restore_value: no, initial_value: '{init}' }}")
    for line in gen.word_globals(KNOB_WORDS):
        A(line)
    A("")


class _Knob:
    """One knob main.yaml being written: the inputs, the shared words and tables, the output lines."""

    def __init__(self, name, layout, prof, hw, rows, starter, service_key):
        self.name, self.layout, self.prof, self.hw, self.rows = name, layout, prof, hw, rows
        self.starter, self.service_key = starter, service_key
        self.n = len(rows)
        self.empty = rows[0] is EMPTY_ROW
        self.w_on, self.w_off = W("knob_on"), W("knob_off")                  # C++ expressions: the house's words
        self.net = {k: W("net_" + k) for k in ("wifi", "home")}
        self.out = []
        self.A = self.out.append

    def T(self, key):
        """An English word of the firmware's own (an entity name, a fallback until the house sends its words)."""
        return _en(key)

    def tbl(self, key, fmt=str):
        return ", ".join(fmt(r[key]) for r in self.rows)

    def flags(self, test):
        return ", ".join("true" if test(r) else "false" for r in self.rows)


def _gen(name, layout, prof, hw, rows, starter, service_key):
    k = _Knob(name, layout, prof, hw, rows, starter, service_key)
    for part in (_k_head, _k_net, _k_mqtt, lambda k: _globals(k.A, k.n), _k_time, _k_buttons, _k_text_sensors,
                 _k_scripts, _k_turn, _k_toggle, _k_interval, _k_inputs, _k_hardware, _k_fonts, _k_lvgl):
        part(k)
    return "\n".join(k.out) + "\n"


def _k_head(k):
    A, prof = k.A, k.prof
    A(f"# AUTO-GENERATED from knob layout '{k.name}' ({prof['label']}) by gen_knob.py - do not hand-edit.")
    A("substitutions:")
    A(f"  name: {k.name}")
    A(f'  step: "{int(k.layout.get("step", 5))}"')
    A("")
    A("esphome:")
    A("  name: ${name}")
    A("  friendly_name: ${name}")
    if k.starter:
        A("  name_add_mac_suffix: true")
    A("  on_boot:")
    A("    priority: -100")
    A("    then:")
    A(f"      - lambda: 'if (id(g_sel) < 0 || id(g_sel) >= {k.n}) id(g_sel) = 0;'")
    A("      - script.execute: refresh_ui")
    A("      - script.execute: net_status")
    A("")
    A("esp32:")
    A(f"  board: {prof['board']}")
    A("  framework:")
    A("    type: esp-idf")
    A("")
    A("debug:")
    A("")
    A("logger: { level: INFO, hardware_uart: %s }" % prof["console"])
    A("")


def _k_net(k):
    A = k.A
    A("ota:")
    A("  - platform: esphome")
    A("    password: !secret ota_password")
    A("")
    A("wifi:")
    A("  ssid: !secret wifi_ssid")
    A("  password: !secret wifi_password")
    A("  ap:")
    A('    ssid: "${name}-setup"')
    A("    password: !secret ap_password")
    A("  power_save_mode: none")      # modem sleep made the node unreachable for OTA
    A("  on_connect:")
    A("    - lambda: 'id(g_wifi_ok) = true;'")
    A("    - script.execute: net_status")
    A("  on_disconnect:")
    A("    - lambda: 'id(g_wifi_ok) = false;'")
    A("    - script.execute: net_status")
    A("captive_portal:")
    A("safe_mode:")
    A("  reboot_timeout: 2min")
    A("")


def _row_msgs(i, r):
    """(topic, lambda) pairs a row listens to: its level and its on/off state."""
    scale = "" if r["clim"] else f" * 100.0f / {r['state_max']}"
    if r["dim"] and not r["own_onoff"]:      # a dimmer without a switch channel: on = a level above 0
        return [(r["state_topic"], f"'id(g_lvl)[{i}] = (int) lroundf(atof(x.c_str()){scale}); "
                                   f"id(g_on)[{i}] = id(g_lvl)[{i}] > 0; "
                                   f"if (id(g_lvl)[{i}] > 0) id(g_last)[{i}] = id(g_lvl)[{i}]; "
                                   f"id(settle).execute({i});'")]
    msgs = []
    if (r["dim"] or r["clim"]) and r["state_topic"] != r["onoff_topic"]:
        msgs.append((r["state_topic"],
                     f"'id(g_lvl)[{i}] = (int) lroundf(atof(x.c_str()){scale}); id(settle).execute({i});'"))
    msgs.append((r["onoff_topic"], f"'id(g_on)[{i}] = {_on_expr(r)}; id(settle).execute({i});'"))
    return msgs


def _k_mqtt(k):
    A, js = k.A, gen.js
    A("mqtt:")
    A("  id: mqtt_client")
    A("  broker: !secret mqtt_host")
    A("  port: !secret mqtt_port")
    A("  username: !secret mqtt_user")
    A("  password: !secret mqtt_password")
    A("  discovery: false")
    A("  topic_prefix: panel/${name}")
    A("  keepalive: 60s")
    A("  reboot_timeout: 10min")
    A("  on_connect:")
    A("    then:")
    A("      - lambda: 'lv_led_set_color(id(st_link), lv_color_hex(0x3CC2B8)); id(g_mqtt_ok) = true;'")
    A("      - script.execute: net_status")
    A("      - delay: 1s")       # publish() inside on_connect is dropped: not CONNECTED yet
    A("      - lambda: |-")
    A('          id(mqtt_client).publish(std::string("panel/${name}/fw"),')
    A('                                  std::string(__DATE__ " " __TIME__), 0, true);')
    A("      - lambda: |-")
    k.out.extend("          " + x for x in _words_apply(k))
    if k.starter:
        k.out.extend(gen.announce_lambda(k.hw, "new_code", "      "))
    A("  on_disconnect:")
    A("    then:")
    A("      - lambda: 'lv_led_set_color(id(st_link), lv_color_hex(0xFF6B6B)); id(g_mqtt_ok) = false;'")
    A("      - script.execute: net_status")
    msgs = [m for i, r in enumerate([] if k.empty else k.rows) for m in _row_msgs(i, r)]
    for what in ("restart", "safe_mode") if k.service_key and not k.starter else ():
        msgs.append((f"panel/{k.name}/{what}/command", f"'if (x == \"{k.service_key}\") id(btn_{what}).press();'"))
    A("  on_message:")
    for topic, body in msgs:
        A(f"    - topic: {js(topic)}")
        A("      then:")
        A(f"        - lambda: {body}")
    A(f"    - topic: {js(gen.WORDS_TOPIC)}")
    A("      then:")
    k.out.extend(gen.words_msg(KNOB_WORDS, _words_apply(k)))
    A("")


def _words_apply(k):
    """C++ lines: the labels that show one of the house's words, set again; the rows' texts redrawn."""
    out = [f"lv_label_set_text(id(pair_t), {W('knob_pair')});", f"lv_label_set_text(id(pair_h), {W('knob_pair_hint')});"]
    if k.starter:
        out += [f"lv_label_set_text(id(new_title), {W('starter_title')});", f"lv_label_set_text(id(new_hint), {W('starter_hint')});"]
    return out + ["id(refresh_ui).execute();"]


def _k_time(k):
    A, layout = k.A, k.layout
    A("time:")                         # the clock label prints the core's time topic
    A("  - platform: sntp")
    A("    id: sntp_time")
    if layout.get("ntp_servers"):
        A(f"    servers: {json.dumps([str(s) for s in layout['ntp_servers']][:3])}")
    A("    update_interval: 15min")
    if layout.get("timezone"):
        A(f"    timezone: {gen.js(str(layout['timezone']))}")
    A("")


def _k_buttons(k):
    A, T = k.A, k.T
    local = k.starter or k.service_key
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
    A("    internal: true")              # only the local long hold: no broker client may wipe a knob
    A("")


def _k_text_sensors(k):
    A = k.A
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
    A("      id: rst_reason")
    A(f'      name: "${{name}} {k.T("reset_reason")}"')
    A("      state_topic: panel/${name}/reset_reason")
    A("      retain: true")
    A("  - platform: mqtt_subscribe")
    A("    id: mqtt_time")
    A(f"    topic: {gen.js(CLOCK_TOPIC)}")
    A("    on_value:")
    A("      then:")
    A("        - lvgl.label.update:")
    A("            id: lbl_clock")
    A("            text: !lambda 'return x;'")
    A("")


def _row_cases(k, i, r):
    """The row's switch cases: how full its ring is (percent) and the text under it."""
    if r["dim"]:
        return (f"case {i}: return lvl;",
                f'case {i}: return on ? str_sprintf("%d%%", lvl) : std::string({k.w_off});')
    if r["clim"]:
        span = r["temp_max"] - r["temp_min"]
        return (f"case {i}: return (lvl - {r['temp_min']}) * 100 / {span};",
                f'case {i}: return on ? str_sprintf("%d{gen.DEG}", lvl)'
                f' : str_sprintf("%d{gen.DEG} ", lvl) + std::string({k.w_off});')
    return (f"case {i}: return 100;",
            f"case {i}: return std::string(on ? {k.w_on} : {k.w_off});")


def _k_scripts(k):
    A, n = k.A, k.n
    A("script:")
    A("  - id: net_status")
    A("    mode: restart")
    A("    then:")
    A("      - lambda: |-")
    A("          const char *t = nullptr;")
    A(f'          if (!id(g_wifi_ok)) t = {k.net["wifi"]};')
    A(f'          else if (!id(g_mqtt_ok)) t = {k.net["home"]};')
    A("          else if (millis() < 30000 && !id(rst_reason).state.empty())")
    A("            t = id(rst_reason).state.c_str();")
    A("          if (t) {")
    A("            lv_label_set_text(id(lbl_net), t);")
    A("            lv_obj_clear_flag(id(lbl_net), LV_OBJ_FLAG_HIDDEN);")
    A("          } else {")
    A("            lv_obj_add_flag(id(lbl_net), LV_OBJ_FLAG_HIDDEN);")
    A("          }")
    A("  - id: pair_hold")
    A("    mode: restart")
    A("    then:")
    A(f"      - delay: {PAIR_HOLD_S}s")
    A("      - lvgl.widget.show: pair_ov")
    for s in range(PAIR_GRACE_S, 0, -1):
        A(f"      - lvgl.label.update: {{ id: pair_n, text: \"{s}\" }}")
        A("      - delay: 1s")
    A("      - button.press: btn_factory_reset")
    ticks = ticks_per_row(n)
    seg_ids = ", ".join(f"id(s{r}_{j})" for r, j, _, _ in sector_ticks(n))
    cases = [_row_cases(k, i, r) for i, r in enumerate(k.rows)]
    A("  - id: refresh_ui")
    A("    then:")
    if k.empty:
        A("      - lambda: 'lv_label_set_text(id(lbl_val), \"\");'")
    else:
        A("      - lambda: |-")
        A(f"          lv_obj_t *seg[{n * ticks}] = {{{seg_ids}}};")
        A("          auto pct = [](int row, int lvl) -> int {")
        A("            switch (row) { " + " ".join(c[0] for c in cases) + " } return 0; };")
        A("          auto txt = [](int row, int lvl, bool on) -> std::string {")
        A("            switch (row) { " + " ".join(c[1] for c in cases) + ' } return std::string(""); };')
        A(f"          for (int r = 0; r < {n}; r++) {{")
        A("            int p = id(g_on)[r] ? pct(r, id(g_lvl)[r]) : 0;")
        A("            if (p < 0) p = 0;")
        A("            if (p > 100) p = 100;")
        A(f"            int lit = (p * {ticks} + 50) / 100;")
        A("            if (p > 0 && lit == 0) lit = 1;   // on at the floor still shows one tick")
        A(f"            uint32_t on_c = r == id(g_sel) ? 0x{LIT_SEL:06X} : 0x{LIT:06X};")
        A(f"            for (int j = 0; j < {ticks}; j++)")
        A(f"              lv_obj_set_style_arc_color(seg[r * {ticks} + j], lv_color_hex(j < lit ? on_c : 0x{UNLIT:06X}), LV_PART_MAIN);")
        A("          }")
        A("          int s = id(g_sel);")
        num_ids = ", ".join(f"id(num{i})" for i in range(n))
        A(f"          lv_obj_t *num[{n}] = {{{num_ids}}};")
        A(f"          for (int r = 0; r < {n}; r++)")
        A(f"            lv_obj_set_style_text_color(num[r], lv_color_hex(r == s ? 0x{TEXT:06X} : 0x{NUM_DIM:06X}), 0);")
        A("          lv_label_set_text(id(lbl_val), txt(s, id(g_lvl)[s], id(g_on)[s]).c_str());")
    A("  - id: settle")
    A("    mode: parallel")
    A("    parameters:")
    A("      row: int")
    A("    then:")
    A("      - lambda: |-")
    A("          // a state arrived: the target is reached (or the device chose otherwise) -> show what is")
    A("          if (id(g_pend)[row] && id(g_ton)[row] == id(g_on)[row] &&")
    A("              (!id(g_on)[row] || abs(id(g_tgt)[row] - id(g_lvl)[row]) <= 1))")
    A("            id(g_pend)[row] = 0;")
    A("      - script.execute: refresh_ui")


def _k_tables(k):
    """The per-row C tables turn and toggle_sel index by the selected row."""
    js = gen.js
    return {"dim": k.flags(lambda r: r["dim"]), "clim": k.flags(lambda r: r["clim"]),
            "cmd": ", ".join(js(r["dim_topic"] or "") for r in k.rows),
            "oncmd": k.tbl("cmd_topic", js), "onpl": k.tbl("cmd_on", js), "offpl": k.tbl("cmd_off", js),
            # a dimmer without a switch channel is switched by its level: 0 = off, the last level = on
            "lvlsw": k.flags(lambda r: r["dim"] and not r.get("own_onoff"))}


def _k_turn(k):
    A, n, t = k.A, k.n, _k_tables(k)
    A("  - id: turn")
    A("    parameters:")
    A("      delta: int")
    A("    then:")
    A("      - lambda: |-")
    A("          int s = id(g_sel);")
    A(f"          const bool dim[{n}] = {{{t['dim']}}};")
    A(f"          const bool clim[{n}] = {{{t['clim']}}};")
    A(f"          if ({'true || ' if k.empty else ''}(!dim[s] && !clim[s])) return;")
    A(f"          const char *cmd[{n}]   = {{{t['cmd']}}};")
    A("          // the command goes out, the screen waits for the device's own state;")
    A("          // a fast turn keeps counting from the last target, not from the old state")
    A("          int base = id(g_pend)[s] ? id(g_tgt)[s] : id(g_lvl)[s];")
    A("          bool was_on = id(g_pend)[s] ? id(g_ton)[s] : id(g_on)[s];")
    A("          if (clim[s]) {")
    A("            // set temperature: one step per detent inside the unit's range; the")
    A("            // unit powers on for any setting change, like its remote does")
    A(f"            const int tmin[{n}] = {{{k.tbl('temp_min')}}}, tmax[{n}] = {{{k.tbl('temp_max')}}},"
      f" tstep[{n}] = {{{k.tbl('temp_step')}}};")
    A("            int t = base + (delta > 0 ? tstep[s] : -tstep[s]);")
    A("            if (t < tmin[s]) t = tmin[s];")
    A("            if (t > tmax[s]) t = tmax[s];")
    A("            id(g_tgt)[s] = t; id(g_ton)[s] = true; id(g_pend)[s] = millis();")
    A("            id(mqtt_client).publish(cmd[s], to_string(t));")
    A("            return;")
    A("          }")
    A(f"          const int   cmax[{n}]  = {{{k.tbl('cmd_max')}}};")
    A(f"          const char *oncmd[{n}] = {{{t['oncmd']}}};")
    A(f"          const char *onpl[{n}]  = {{{t['onpl']}}};")
    A(f"          const char *offpl[{n}] = {{{t['offpl']}}};")
    A("          // the floor (1 %) is the dimmest allowed level: one more turn down")
    A("          // switches the channel off; turning down an off channel does nothing")
    A(f"          const bool lvlsw[{n}] = {{{t['lvlsw']}}};")
    A("          if (delta < 0 && (!was_on || base <= 1)) {")
    A("            if (was_on) {")
    A("              id(g_tgt)[s] = base; id(g_ton)[s] = false; id(g_pend)[s] = millis();")
    A("              if (lvlsw[s]) id(mqtt_client).publish(cmd[s], std::string(\"0\"));")
    A("              else id(mqtt_client).publish(oncmd[s], offpl[s]);")
    A("            }")
    A("            return;")
    A("          }")
    A("          int p = base + delta;")
    A("          if (p < 1) p = 1;")
    A("          if (p > 100) p = 100;")
    A("          id(g_tgt)[s] = p; id(g_ton)[s] = true; id(g_pend)[s] = millis();")
    A("          int raw = (int) lroundf(p * cmax[s] / 100.0f);")
    A("          if (raw < 1) raw = 1;")
    A("          id(mqtt_client).publish(cmd[s], to_string(raw));")
    A("          id(g_last)[s] = p;")
    A("          if (!was_on && !lvlsw[s]) id(mqtt_client).publish(oncmd[s], onpl[s]);")
    A("      - script.execute: refresh_ui")


def _k_toggle(k):
    A, n, t = k.A, k.n, _k_tables(k)
    A("  - id: toggle_sel")
    A("    then:")
    A("      - lambda: |-")
    A("          int s = id(g_sel);")
    if k.empty:
        A("          return;")
    A("          bool nx = !(id(g_pend)[s] ? id(g_ton)[s] : id(g_on)[s]);")
    A("          id(g_tgt)[s] = id(g_pend)[s] ? id(g_tgt)[s] : id(g_lvl)[s];")
    A("          id(g_ton)[s] = nx; id(g_pend)[s] = millis();")
    A(f"          const char *oncmd[{n}] = {{{t['oncmd']}}};")
    A(f"          const char *onpl[{n}]  = {{{t['onpl']}}};")
    A(f"          const char *offpl[{n}] = {{{t['offpl']}}};")
    A(f"          const bool lvlsw[{n}] = {{{t['lvlsw']}}};")
    A(f"          const char *cmd[{n}]   = {{{t['cmd']}}};")
    A(f"          const int   cmax[{n}]  = {{{k.tbl('cmd_max')}}};")
    A("          if (lvlsw[s]) {")
    A("            int p = id(g_last)[s] > 0 ? id(g_last)[s] : 100;")
    A("            id(mqtt_client).publish(cmd[s], nx ? to_string((int) lroundf(p * cmax[s] / 100.0f)) : std::string(\"0\"));")
    A("          } else {")
    A("            id(mqtt_client).publish(oncmd[s], nx ? onpl[s] : offpl[s]);")
    A("          }")
    A("      - script.execute: refresh_ui")
    A("")


def _k_interval(k):
    A = k.A
    A("interval:")
    A("  - interval: 500ms")
    A("    then:")
    A("      - lambda: |-")
    A("          bool changed = false;")
    A(f"          for (int r = 0; r < {k.n}; r++)")
    A(f"            if (id(g_pend)[r] && millis() - id(g_pend)[r] > {PENDING_MS}) {{ id(g_pend)[r] = 0; changed = true; }}")
    A("          if (changed) id(refresh_ui).execute();")
    A("  - interval: 10s")              # hides the reset reason after boot, catches missed link edges
    A("    then:")
    A("      - script.execute: net_status")
    A("")


def _k_inputs(k):
    A, enc, btn = k.A, k.prof["encoder"], k.prof["button"]
    A("sensor:")
    A("  - platform: rotary_encoder")
    A("    id: knob")
    A("    internal: true")
    for pin, num in (("pin_a", enc["a"]), ("pin_b", enc["b"])):
        A(f"    {pin}:")
        A(f"      number: {num}")
        A("      mode: { input: true, pullup: true }")
    A(f"    resolution: {enc.get('resolution', 1)}")
    for edge, sign in (("on_clockwise", ""), ("on_anticlockwise", "-")):
        A(f"    {edge}:")
        A("      - script.execute:")
        A("          id: turn")
        A(f"          delta: {sign}${{step}}")
    A("  - platform: uptime")
    A('    name: "${name} uptime"')
    A("    update_interval: 30s")
    A("    state_topic: panel/${name}/uptime")
    A("    accuracy_decimals: 0")
    A("  - platform: wifi_signal")
    A('    name: "${name} wifi rssi"')
    A("    update_interval: 20s")
    A("    state_topic: panel/${name}/rssi")
    A("    accuracy_decimals: 0")
    A("    on_value:")
    A("      then:")
    A("        - lambda: |-")
    A("            int q = (int) ((x + 100.0f) * 2.0f);")
    A("            if (q > 100) q = 100;")
    A("            if (q < 0) q = 0;")
    A("            char buf[16];")
    A('            snprintf(buf, sizeof(buf), "\\uF1EB %d%%", q);')
    A("            lv_label_set_text(id(st_wifi), buf);")
    A("            uint32_t c = q >= 60 ? 0x3CC2B8 : (q >= 35 ? 0xFFC34D : 0xFF6B6B);")
    A("            lv_obj_set_style_text_color(id(st_wifi), lv_color_hex(c), 0);")
    A("")
    A("binary_sensor:")
    A("  - platform: gpio")
    A("    id: knob_btn")
    A("    internal: true")
    A("    pin:")
    A(f"      number: {btn['pin']}")
    A("      mode: { input: true, pullup: true }")
    A(f"      inverted: {'true' if btn.get('inverted') else 'false'}")
    A("    filters:")
    A("      - delayed_on: 30ms")
    A("      - delayed_off: 30ms")
    A("    on_press:")                  # held PAIR_HOLD_S: countdown, then factory reset + setup AP
    A("      - script.execute: pair_hold")
    A("    on_release:")
    A("      - script.stop: pair_hold")
    A("      - lvgl.widget.hide: pair_ov")
    A("    on_click:")
    A("      - min_length: 30ms")
    A(f"        max_length: {LONG_PRESS_MS - 1}ms")
    A("        then:")
    if k.n > 1:
        A(f"          - lambda: 'id(g_sel) = (id(g_sel) + 1) % {k.n};'")
        A("          - script.execute: refresh_ui")
    else:
        A("          - script.execute: toggle_sel")
    A(f"      - min_length: {LONG_PRESS_MS}ms")
    A("        max_length: 10s")
    A("        then:")
    A("          - script.execute: toggle_sel")
    A("")


def _k_hardware(k):
    A, prof, bl = k.A, k.prof, k.prof["backlight"]
    A("spi:")
    A(f"  clk_pin: {prof['spi']['clk']}")
    A(f"  mosi_pin: {prof['spi']['mosi']}")
    A("")
    A("output:")
    A("  - platform: ledc")
    A("    id: bl_pwm")
    A(f"    pin: {bl['pin']}")
    A(f"    inverted: {'true' if bl.get('inverted') else 'false'}")
    A("    frequency: 5000Hz")
    A("")
    A("light:")
    A("  - platform: monochromatic")
    A("    id: backlight")
    A('    name: "${name} backlight"')
    A("    output: bl_pwm")
    A("    restore_mode: ALWAYS_ON")
    A("    default_transition_length: 0s")
    A("")
    A("display:")
    dl = prof["display"].rstrip("\n").split("\n")
    A("  - " + dl[0])
    for ln in dl[1:]:
        A("    " + ln if ln.strip() else ln)
    A("")


def _k_fonts(k):
    A = k.A
    cyr = gen.HOUSE_CYRILLIC or any(gen.has_cyrillic(json.dumps(r, ensure_ascii=False)) for r in k.rows)
    glyphsets = "[GF_Latin_Core, GF_Cyrillic_Core]" if cyr else "[GF_Latin_Core]"
    big = "0123456789%" + gen.DEG + " " + gen.LETTERS            # on / off in the house's language
    nums = "".join(str(i + 1) for i in range(k.n))
    A("font:")
    A(f'  - {{ file: "gfonts://Roboto@500", id: f_row, size: 18, bpp: 4, glyphsets: {glyphsets} }}')
    A(f'  - {{ file: "gfonts://Roboto@500", id: f_big, size: 42, bpp: 4, glyphs: {gen.js(big)} }}')
    A(f'  - {{ file: "gfonts://Roboto@700", id: f_num, size: 88, bpp: 4, glyphs: "{nums}" }}')
    A('  - { file: "gfonts://Roboto@500", id: f_clock, size: 30, bpp: 4, glyphs: "0123456789:-" }')
    A("")
    A("image:")
    A(f"  - {{ file: icons/{LOGO}, id: knob_logo, type: RGB565, transparency: alpha_channel }}")
    A("")


def _k_lvgl(k):
    A, T, js, prof = k.A, k.T, gen.js, k.prof
    rot = k.layout.get("rotation", prof.get("rotation", 0))
    try:
        rot = int(rot or 0) % 360
    except (TypeError, ValueError):
        raise ValueError(f"rotation must be 0/90/180/270, got {rot!r}") from None
    if rot not in (0, 90, 180, 270):
        raise ValueError(f"rotation must be 0/90/180/270, got {rot}")
    A("lvgl:")
    A("  displays: [lcd]")
    if rot:
        A(f"  rotation: {rot}")
    A(f"  buffer_size: {prof.get('lvgl_buffer', '25%')}")
    A("  color_depth: 16")
    A("  pages:")
    A("    - id: main_page")
    A(f"      bg_color: 0x{BG:06X}")
    A("      widgets:")
    for r, j, a0, a1 in ([] if k.empty else sector_ticks(k.n)):
        A(f"        - arc: {{ id: s{r}_{j}, align: CENTER, width: {RING_D}, height: {RING_D}, adjustable: false,"
          f" start_angle: {a0}, end_angle: {a1}, rotation: 0, value: 0, arc_width: {RING_W}, arc_rounded: false,"
          f" arc_color: 0x{UNLIT:06X}, indicator: {{ arc_opa: TRANSP }}, knob: {{ bg_opa: TRANSP }} }}")
    for i, dx, dy in ([] if k.empty else sector_labels(k.n)):
        A(f"        - label: {{ id: num{i}, align: CENTER, x: {dx}, y: {dy}, text: \"{i + 1}\","
          f" text_color: 0x{NUM_DIM:06X}, text_font: f_num }}")
    A("        - image: { id: logo, src: knob_logo, align: CENTER, y: 6 }")
    A(f"        - label: {{ id: lbl_clock, align: CENTER, y: -46, text: \"--:--\", text_color: 0x{TEXT:06X}, text_font: f_clock }}")
    A(f"        - label: {{ id: lbl_val, align: CENTER, y: 64, text: \"--\", text_color: 0x{TEXT:06X}, text_font: f_big }}")
    A("        - label:")
    A("            id: lbl_net")
    A("            align: CENTER")
    A("            y: 96")
    A("            width: 150")
    A("            text_align: CENTER")
    A("            long_mode: DOT")
    A('            text: ""')
    A("            text_font: f_row")
    A("            text_color: 0xFF6B6B")
    A("            hidden: true")
    A("        - label:")
    A("            id: st_wifi")
    A("            align: CENTER")
    A("            x: 8")
    A("            y: -80")
    A('            text: "\\uF1EB --"')
    A("            width: 64")
    A("            text_align: CENTER")
    A("            text_font: montserrat_14")
    A(f"            text_color: 0x{MUTED:06X}")
    A("        - led:")
    A("            id: st_link")
    A("            align_to: { id: st_wifi, align: OUT_LEFT_MID, x: -2 }")
    A("            width: 8")
    A("            height: 8")
    A("            color: 0xFF6B6B")
    A("            brightness: 100%")
    A("        - obj:")
    A("            id: pair_ov")
    A("            align: CENTER")
    A("            width: 210")
    A("            height: 210")
    A("            radius: 105")
    A(f"            bg_color: 0x{BG:06X}")
    A("            bg_opa: COVER")
    A("            border_width: 2")
    A(f"            border_color: 0x{LIT_SEL:06X}")
    A("            scrollbar_mode: \"OFF\"")
    A("            hidden: true")
    A("            widgets:")
    A(f"              - label: {{ id: pair_t, align: CENTER, y: -58, text: {js(T('knob_pair'))}, text_font: f_row, text_color: 0x{TEXT:06X} }}")
    A(f"              - label: {{ id: pair_n, align: CENTER, y: -4, text: \"{PAIR_GRACE_S}\", text_font: f_big, text_color: 0x{LIT_SEL:06X} }}")
    A(f"              - label: {{ id: pair_h, align: CENTER, y: 50, text: {js(T('knob_pair_hint'))}, text_font: f_row, text_color: 0x{MUTED:06X} }}")
    if k.starter:
        A("        - obj:")
        A("            id: new_ov")
        A("            align: CENTER")
        A("            width: 230")
        A("            height: 230")
        A("            radius: 115")
        A(f"            bg_color: 0x{BG:06X}")
        A("            bg_opa: COVER")
        A("            border_width: 0")
        A("            scrollbar_mode: \"OFF\"")
        A("            widgets:")
        A(f"              - label: {{ id: new_title, align: CENTER, y: -52, text: {js(T('starter_title'))}, text_font: f_row, text_color: 0x{TEXT:06X} }}")
        A(f"              - label: {{ id: new_code, align: CENTER, y: 0, text: \"\", text_font: f_big, text_color: 0x{LIT_SEL:06X} }}")
        A(f"              - label: {{ id: new_hint, align: CENTER, y: 52, width: 180, text_align: CENTER, text: {js(T('starter_hint'))},"
          f" text_font: f_row, text_color: 0x{MUTED:06X} }}")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Print the ESPHome main.yaml of one knob layout.")
    ap.add_argument("layout", help="JSON file: one layout, or a {name: layout} map")
    ap.add_argument("name", help="panel name, e.g. knob-1")
    ap.add_argument("--starter", action="store_true", help="starter firmware (no rows, announce)")
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
