"""One stacking scale: every layer above the page names a variable of :root (page < side panels < floating tablet < bars <
header pop-ups < dialogs < the help < toasts), so a pop-up of the header is never under a plug-in's panel; a plain number is only a
small local order (under 100) inside one component."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
Z = re.compile(r"z-index\s*:\s*([^;}'\"`]+)")


def _files():
    return [ROOT / "index.html", ROOT / "web" / "app.css", *sorted(ROOT.glob("web/js/*.js"))] + sorted(ROOT.glob("plugins/*/*/ui.js")) + sorted(ROOT.glob("plugins/*/*/static/*.html"))


def test_the_scale_is_in_order():
    root = re.search(r":root\{(.*?)\}", (ROOT / "web" / "app.css").read_text(encoding="utf-8"), re.S).group(1)
    v = {k: int(n) for k, n in re.findall(r"--z-(\w+):(\d+)", root)}
    assert v["side"] < v["float"] < v["bar"] < v["pop"] < v["dialog"] < v["help"] < v["toast"]


def test_every_high_layer_names_the_scale():
    bad = []
    for f in _files():
        for m in Z.finditer(f.read_text(encoding="utf-8")):
            val = m.group(1).replace("!important", "").strip()
            if "var(--z-" in val or "${" in val:
                continue
            if not val.isdigit() or int(val) >= 100:
                bad.append(f"{f.relative_to(ROOT)}: z-index:{val}")
    assert bad == []
