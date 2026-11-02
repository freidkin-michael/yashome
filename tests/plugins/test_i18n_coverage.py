"""Every user-facing text of a plug-in goes through i18n: an English source with a Russian translation, none hard-coded in the code.

The plug-in's ui.js is scanned for text that bypasses i18n (a text between tags, a title/placeholder, a textContent), every i18n('key') it
calls must have a Russian entry in the plug-in's i18n.json, and so must every message of an HTTPException the plug-in raises (the dashboard
translates a server message through the same table, with {0} for the values in it)."""
import ast
import json
import re
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "plugins" / "tools"
PLUGINS = ("map", "voice", "family", "watchdog", "history", "connect")


def _ru(plug: str) -> dict:
    return json.loads((TOOLS / plug / "i18n.json").read_text(encoding="utf-8"))["ru"]


def _words(t: str) -> bool:                                                               # three letters in a row, Latin or Cyrillic
    return bool(re.search(r"[A-Za-z]{3,}", t)) or bool(re.search("[" + chr(0x400) + "-" + chr(0x4ff) + "]{3,}", t))


def _unescape(s: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s.replace("\\'", "'"))


@pytest.mark.parametrize("plug", PLUGINS)
def test_every_i18n_key_of_the_screen_has_a_russian_text(plug):
    js = (TOOLS / plug / "ui.js").read_text(encoding="utf-8")
    keys = {_unescape(k) for k in re.findall(r"i18n\('((?:[^'\\]|\\.)*)'\)", js)}
    ru = _ru(plug)
    assert not sorted(k for k in keys if k not in ru and k not in _core_words())


def _core_words() -> set:
    src = (TOOLS.parents[1] / "i18n.js").read_text(encoding="utf-8")
    return {_unescape(k) for k in re.findall(r'^\s*"((?:[^"\\]|\\.)*)"\s*:', src, re.M)}


@pytest.mark.parametrize("plug", ("map", "voice", "watchdog", "history"))
def test_no_user_facing_text_is_written_into_the_screen_code(plug):
    js = (TOOLS / plug / "ui.js").read_text(encoding="utf-8")
    flat = re.sub(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", "", js)                                  # what is left outside the ${...} of a template
    found = set()
    for m in re.finditer(r">([^<>`'\"\n]{2,})<", flat):                                       # a text between two tags
        t = m.group(1).strip()
        if _words(t) and not re.search(r"[;{}=()&|<>]", t):
            found.add(t)
    for m in re.finditer(r"""(?:title|placeholder|alt|aria-label)=["']([^"'$]{3,})["']""", js):
        found.add(m.group(1))                                                              # an attribute the user reads
    for m in re.finditer(r"""(?:textContent|innerText|\.title|\.placeholder|alert\(|confirm\()\s*=?\s*['"`]([^'"`$\n]{3,})['"`]""", js):
        found.add(m.group(1))
    found = {t for t in found if _words(t) and not re.fullmatch(r"[\\u0-9a-fA-F]+", t)}
    found -= {"lox - tol && x", "loy - tol && y"}                                           # code that looks like text between two comparison signs
    assert not sorted(found), "write these through i18n('...') with a Russian entry in i18n.json"


def _details(path: Path) -> set:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "HTTPException" and len(node.args) >= 2:
            a = node.args[1]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.add(a.value)
            elif isinstance(a, ast.JoinedStr):
                n, s = 0, ""
                for v in a.values:
                    if isinstance(v, ast.Constant):
                        s += v.value
                    else:
                        s, n = s + "{%d}" % n, n + 1
                out.add(s)
    return out


@pytest.mark.parametrize("plug", PLUGINS)
def test_every_message_a_plugin_raises_has_a_russian_text(plug):
    ru = _ru(plug)
    core = _core_words()
    got = set()
    for f in (TOOLS / plug).glob("*.py"):
        got |= _details(f)
    assert not sorted(d for d in got if d not in ru and d not in core)
