"""Every character the dashboard shows is one the fonts it runs on have: the page names no font of its own (the system stack),
so a sign outside the common sets (a clock, a warning, a check mark, a play triangle) shows as an empty box on some screens.
Such a sign is drawn instead (icon() in index.html). The allowed set was checked with fc-list on a plain Linux host."""
import html
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = (set(range(0xA0, 0x100)) | set(range(0x400, 0x500)) | set(range(0x2010, 0x2027))
           | {0x202F, 0x2030, 0x2039, 0x203A, 0x2116, 0x2190, 0x2191, 0x2192, 0x2193, 0x2212, 0x2260, 0x2264, 0x2265, 0x25CB, 0x25CF})
VENDOR = {"qrcode.js"}                      # a QR encoder's own tables, never shown


def _shown(text: str) -> str:
    """The text without comments, with \\uXXXX escapes and HTML entities as the characters they are."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)(^|[^:'\"\\])//.*$", r"\1", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\\u\{([0-9a-fA-F]{1,6})\}", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    return html.unescape(text)                   # &#9681; and &rarr; are signs too


def _files():
    yield from (*[ROOT / "index.html", ROOT / "web" / "app.css", *sorted(ROOT.glob("web/js/*.js"))], ROOT / "i18n.js")
    for pat in ("plugins/*/*/ui.js", "plugins/*/*/static/*.js", "plugins/*/*/i18n.json"):
        yield from (f for f in sorted(ROOT.glob(pat)) if f.name not in VENDOR)


class Glyphs(unittest.TestCase):
    def test_no_sign_the_fonts_may_lack(self):
        bad = {}
        for f in _files():
            text = f.read_text(encoding="utf-8")
            if f.name == "i18n.json":            # fw:remote:<icon>: names a learned button may have, matched, never shown
                data = json.loads(text)
                text = json.dumps({lang: {k: v for k, v in words.items() if not k.startswith("fw:remote:")}
                                   for lang, words in data.items()}, ensure_ascii=False)
            for c in set(_shown(text)):
                if ord(c) > 0x7F and ord(c) not in ALLOWED:
                    bad.setdefault(f"U+{ord(c):04X} {c}", []).append(str(f.relative_to(ROOT)))
        self.assertEqual(bad, {}, "draw it with icon() or say it in words")


if __name__ == "__main__":
    unittest.main()
