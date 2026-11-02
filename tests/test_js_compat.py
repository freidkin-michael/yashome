"""Scripts an Android panel may load must parse on old WebViews (Chrome 77): no optional
chaining or nullish coalescing, which came with Chrome 80."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_WEBVIEW_SCRIPTS = ["i18n.js"]
MODERN = re.compile(r"\?\?|\?\.(?!\d)")


class OldWebViewSyntax(unittest.TestCase):
    def test_no_chrome80_operators(self):
        for name in OLD_WEBVIEW_SCRIPTS:
            for n, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
                with self.subTest(file=name, line=n):
                    self.assertIsNone(MODERN.search(line), f"{name}:{n}: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
