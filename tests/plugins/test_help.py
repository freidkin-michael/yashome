"""The help list (HELP in index.html, a plug-in toolbar button's help): unique ids, a Russian text for every word of it, and an
entry for every toolbar button, every field of the core settings pages and every control of the device window. That every
entry's control shows on some screen is checked live by tests/ui_sweep.py; the tap and the drag by test_help_ui.py."""
import json
import re
from pathlib import Path
from tools.webpage import page as _dashboard_page   # index.html with web/app.css and web/js/*.js inline

ROOT = Path(__file__).resolve().parents[2]
HTML = _dashboard_page()
HELP = HTML[HTML.index("const HELP = ["):HTML.index("\n];", HTML.index("const HELP = ["))]


def _unescape(s: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s.replace("\\'", "'"))


def _field(name: str) -> list:
    return [_unescape(v) for v in re.findall(name + r": '((?:[^'\\]|\\.)*)'", HELP)]


def _sels() -> str:
    return "\n".join(_field("sel"))


def _block(start: str, end: str) -> str:
    i = HTML.index(start)
    return HTML[i:HTML.index(end, i)]


def _bracket(src: str, start: int) -> str:
    """The text of the [...] that opens at src[start]."""
    depth, i = 0, start
    while True:
        depth += {"[": 1, "]": -1}.get(src[i], 0)
        if depth == 0:
            return src[start + 1:i]
        i += 1


def _plugin_help() -> dict:
    """plug-in -> its register({help: [...]}) entries as {field: value} (single-quoted strings)."""
    out = {}
    for js in sorted(ROOT.glob("plugins/*/*/ui.js")):
        src = js.read_text(encoding="utf-8")
        for m in re.finditer(r"\bhelp: \[", src):
            block = _bracket(src, m.end() - 1)
            for e in re.split(r"\n\s*\{(?=id: ')", "\n" + block)[1:]:
                out.setdefault(js.parent.name, []).append(
                    {k: _unescape(v) for k, v in re.findall(r"(\w+): '((?:[^'\\]|\\.)*)'", e)})
    return out


def _settings_titles() -> list:
    core = re.findall(r"\{id: '[a-z]+',[^\n]*?title: '([^']+)'", _block("const ST_SUBJECTS = [", "\n];"))       # the subjects of the menu
    plug = [t for js in ROOT.glob("plugins/*/*/ui.js")                  # an entry naming no subject has a page of its own
            for b in re.finditer(r"settings: \[\{title: '([^']+)'", js.read_text(encoding="utf-8")) for t in [b.group(1)]]
    return core + plug


def test_the_ids_are_unique():
    ids = _field("id") + [e["id"] for es in _plugin_help().values() for e in es]
    assert len(_field("id")) > 20 and len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)
    assert all(re.fullmatch(r"[a-z]+(\.[a-z]+)+", i) for i in ids), ids


def test_a_plug_in_s_help_has_its_fields_and_a_russian_text():
    core = {json.loads(k) for k in re.findall(r'^\s*("(?:[^"\\]|\\.)*")\s*:', (ROOT / "i18n.js").read_text(encoding="utf-8"), re.M)}
    titles = set(_settings_titles())
    for plug, entries in _plugin_help().items():
        js = next(ROOT.glob(f"plugins/*/{plug}/ui.js"))
        ru = json.loads((js.parent / "i18n.json").read_text(encoding="utf-8"))["ru"]
        for e in entries:
            assert {"id", "screen", "sel", "title", "text"} <= set(e), (plug, e)
            words = [e[k] for k in ("title", "text", "more", "screenName") if e.get(k)]
            assert not [w for w in words if w not in ru and w not in core], (plug, e["id"])
            if e["screen"].startswith("settings:") and not re.fullmatch(r"settings:[a-z]+(/[a-z-]+)?", e["screen"]):
                assert e["screen"][9:] in titles, (plug, e["screen"])


def test_the_sweep_s_unreachable_entries_exist():
    """An entry the sweep's house cannot show is named in ui_sweep's UNREACHABLE; a name there that is no entry is stale."""
    sweep = (ROOT / "tests" / "ui_sweep.py").read_text(encoding="utf-8")
    block = sweep[sweep.index("const UNREACHABLE = new Set(["):sweep.index("]);", sweep.index("const UNREACHABLE = new Set(["))]
    ids = set(_field("id")) | {e["id"] for es in _plugin_help().values() for e in es}
    named = re.findall(r"'([a-z]+\.[a-z0-9]+)'", block)
    assert len(named) > 10 and not [i for i in named if i not in ids], [i for i in named if i not in ids]


def test_settings_pages_have_different_titles():
    titles = _settings_titles()
    assert len(titles) > 7 and len(titles) == len(set(titles)), titles


def test_every_word_of_the_help_has_a_russian_text():
    src = (ROOT / "i18n.js").read_text(encoding="utf-8")
    ru = {json.loads(k) for k in re.findall(r'^\s*("(?:[^"\\]|\\.)*")\s*:', src, re.M)}
    words = _field("title") + _field("text") + _field("more")
    assert not [w for w in words if w not in ru]


def test_every_plug_in_toolbar_button_has_help_in_both_languages():
    for js in ROOT.glob("plugins/*/*/ui.js"):
        src = js.read_text(encoding="utf-8")
        for m in re.finditer(r"toolbar: \[", src):
            depth, i = 0, m.end() - 1
            while True:
                depth += {"[": 1, "]": -1}.get(src[i], 0)
                if depth == 0:
                    break
                i += 1
            block = src[m.end():i]
            labels = re.findall(r"label: '([^']+)'", block)
            texts = [_unescape(t) for t in re.findall(r"help: \{text: '((?:[^'\\]|\\.)*)'\}", block)]
            assert len(texts) == len(labels), (js, labels)
            ru = json.loads((js.parent / "i18n.json").read_text(encoding="utf-8"))["ru"]
            assert not [t for t in texts if t not in ru], js


def test_every_field_of_the_core_settings_pages_has_an_entry():
    bodies = _block("  const bodies = {", "\n  };")
    ids = set(re.findall(r'<(?:button|select|input)[^>]*\bid="(st-[a-z-]+)"', bodies))
    ids |= set(re.findall(r'class="(?:toggle|enum)[^"]*"[^>]*\bid="(st-[a-z-]+)"', bodies))
    ids |= set(re.findall(r'<h3 id="(st-[a-z-]+)"', bodies))
    backups = _block("async function renderBackups", "\n}\n")
    ids |= set(re.findall(r'<button[^>]*\bid="(bk-[a-z-]+)"', backups))
    assert {"st-logout", "st-signin", "bk-now"} <= ids
    assert not sorted(i for i in ids if "#" + i not in _sels())


def test_every_control_of_the_device_window_has_an_entry():
    modal = _block("function buildModal(){", "\nlet _modalSects")
    ids = set(re.findall(r'<(?:button|select|input)[^>]*\bid="(m_[a-z_]+)"', modal))
    acts = set(re.findall(r'data-act="([a-z_]+)"', modal))
    heads = set(re.findall(r'data-h="([a-z]+)"', modal))
    assert {"m_ren_dev", "m_room", "m_fav", "m_modal_ok"} <= ids and {"toggle", "bright"} <= acts and "rules" in heads
    sels = _sels()
    assert not sorted(i for i in ids if "#" + i not in sels)
    acts -= {"set_bool", "set_enum", "set_int"}                       # the device's own settings: one entry for the section
    assert not sorted(a for a in acts if f'data-act="{a}"' not in sels and f".{a}" not in sels)
    assert not sorted(h for h in heads if f'data-h="{h}"' not in sels)


# a plug-in whose help is written: the pattern of its controls' ids and classes (one entry each), and the controls a
# helper of its own builds (class="${cls}": no pattern sees them)
COVERED = {"panels": (r"(?:pn|pe)-[a-z][a-z-]*", {"pn-wafter", "pn-wevery"}),
           # the map names its controls by data-m (the bar), data-u (the plan), data-b (a tool's bar), data-s (a sheet): "s:find"
           "map": (r"data-([mubs])=\"([a-z][a-z-]*)\"", {"u:opacity", "u:size", "u:rot", "s:kind-window", "s:kind-door",
                   "s:open-slide", "s:open-left", "s:open-right", "ctl", "unlink", "part"}),
           # IR: data-act (its tab and remote windows), data-f (the button form), data-ira (the tile window)
           "ir": (r"data-(act|f|ira|ira-sel)=\"([a-z][a-z-]*)\"", {"act:add-remote", "act:fw-press", "ir-test", "fw-slider", "fw-select", "ira-power"}),
           # climate: data-ac (the card and the AC window); its buttons are built in code, by data-code
           "climate": (r"data-(ac)=\"([a-z][a-z-]*)\"", {"ac:temp", "ac:power", "ac:more", "code:fan", "code:mode"}, 5),
           # voice: data-f (a satellite's settings), data-a (Ask the house, Voice recording), data-c and data-nw (Settings -> Voice)
           "voice": (r"data-(f|a|c)=\"([a-z][a-z_-]*)\"", {"c:llm", "c:confirm_all_off", "c:llm_timeout", "c:ask_ttl", "nw"}),
           # watchdog: the Health button in the header (built in code) and the report's close button
           "watchdog": (r"data-(w)=\"([a-z][a-z-]*)\"", {"w:badge"}, 1),
           # build: the fields and buttons of Settings -> Build server, by id
           # switchbot: its Add window by id, and a listed lock's Delete (data-del)
           "switchbot": (r"sb-[a-z][a-z-]*", {"del"}, 4),
           # touchmouse: its Add window by id, and a listed receiver's Delete (data-del)
           "touchmouse": (r"tm-[a-z][a-z-]*", {"del"}, 6),
           # history: its window by data-h (the erase choices too) and the header button built in code
           "history": (r"data-(h)=\"([a-z0-9][a-z0-9-]*)\"", {"h:open"}, 10),
           # pair: its dialog and the device window's way out, by id; a found network's button by its class
           "pair": (r"pr-[a-z][a-z-]*", set(), 5),
           # provision: the controls of a found network's row (classes) and the next step (id)
           "provision": (r"pv-[a-z][a-z-]*", set(), 4),
           # zigbee: the rows of the pairing window and the rename in a device window, by class
           "zigbee": (r"zb-[a-z][a-z-]*", set(), 2),
           # connect: its code box by id, a row's fields and buttons by data-; a field's name comes from the server (name, panel)
           "connect": ((r"cn-[a-z][a-z-]*", r"data-(f|rb)=\"([a-z][a-z-]*)\""), {"f:name", "f:panel", "go", "again", "other"}, 10),
           # family: its page, the phone's window, its rights and the rule action by id; the phone's buttons by data-, the header button
           "family": (r"(?:fam|fr|rb-fam)-[a-z][a-z-]*", {"fam:open", "rights", "repair", "update", "ring", "stop", "revoke", "all", "dev"}, 10)}


def _controls(src: str, pat: str) -> set:
    """Ids and classes matching pat on the controls of a ui.js: buttons, inputs, selects and toggles."""
    tag = r"<(?:button|input|select)[^>]*"
    if pat.startswith("data-"):                                                        # attribute-named controls: "m:fit"
        return {":".join(m) for t in re.findall(r"<(?:button|input|select|textarea)[^>]*>", src) for m in re.findall(pat, t)}
    found = set(re.findall(tag + r'\bid="(' + pat + r')"', src))
    found |= {c for cls in re.findall(tag + r'\bclass="([^"]*)"', src) for c in re.findall(r"(?<![\w-])(" + pat + r")(?![\w-])", cls)}
    found |= set(re.findall(r'class="toggle (' + pat + r')', src))
    return found


def test_every_control_of_a_covered_plug_in_has_an_entry():
    helps = _plugin_help()
    for plug, (pat, built, *least) in COVERED.items():
        src = next(ROOT.glob(f"plugins/*/{plug}/ui.js")).read_text(encoding="utf-8")
        sels = {s for e in helps.get(plug, []) for s in re.findall(r"[#.]([\w-]+)", e["sel"])}
        sels |= {f"{a}:{v}" for e in helps.get(plug, []) for a, v in re.findall(r'data-([a-z-]+)="([\w-]+)"', e["sel"])}
        sels |= {a for e in helps.get(plug, []) for a in re.findall(r"\[data-([a-z-]+)\]", e["sel"])}
        gone = [b for b in built if not any(f"'{x}'" in src for x in (b, b.split(":")[-1], b.split("-")[-1])) and f"data-{b}" not in src]
        assert not gone, (plug, gone)                                                  # each is still built there
        controls = set().union(*(_controls(src, p) for p in (pat if isinstance(pat, tuple) else (pat,)))) | built
        assert len(controls) > (least or [10])[0] and not sorted(controls - sels), (plug, sorted(controls - sels))
