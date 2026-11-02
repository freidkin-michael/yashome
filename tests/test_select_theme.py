"""Every page that shows a drop-down list opens it dark: color-scheme dark on the select and the theme's colours on its options
(a select styled transparent once opened a white list with light words). The plug-ins' selects live in the dashboard's page."""
import re
import unittest
from pathlib import Path

from tools.webpage import page as _dashboard_page   # index.html with web/app.css and web/js/*.js inline

ROOT = Path(__file__).resolve().parents[1]


def _pages():
    yield "index.html", _dashboard_page()
    yield from ((str(p.relative_to(ROOT)), p.read_text(encoding="utf-8")) for p in sorted(ROOT.glob("plugins/*/*/static/*.html")))


class SelectsOpenDark(unittest.TestCase):
    def test_every_page_with_a_select_opens_it_dark(self):
        bad = []
        for name, s in _pages():
            if "<select" not in s and "createElement('select')" not in s:
                continue
            css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", s, re.S))
            if not re.search(r"select\s*\{[^}]*color-scheme:\s*dark", css) or not re.search(r"select option[^{]*\{[^}]*background:[^}]*color:", css):
                bad.append(name)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
