"""Every plug-in ui.js: no key twice in a HomePlugins.register({...}) object (a second onState silently replaced the
first in ir). Keys are read at the object's own indent, two spaces, as all plug-ins write them."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2] / "plugins"


def _dupes(src: str) -> list:
    out, seen, on = [], set(), False
    for line in src.splitlines():
        if "HomePlugins.register({" in line:
            on, seen = True, set()
        elif on and line.startswith("});"):
            on = False
        elif on and (m := re.match(r"  ([A-Za-z_$][\w$]*)\s*:", line)):
            if m.group(1) in seen:
                out.append(m.group(1))
            seen.add(m.group(1))
    return out


def test_the_scan_finds_a_repeated_key():
    assert _dupes("HomePlugins.register({\n  onState: [a],\n  tabs: [],\n  onState: [b],\n});") == ["onState"]


def test_no_plugin_registers_a_key_twice():
    hits = {str(p.relative_to(ROOT)): d for p in sorted(ROOT.rglob("ui.js")) if (d := _dupes(p.read_text(encoding="utf-8")))}
    assert hits == {}
