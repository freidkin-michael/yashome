"""Every dialog, sheet, settings page and tab at 360, 600 and 1280 px: no label under its field, no squeezed or clipped label (tests/ui_sweep.py)."""
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_no_label_is_covered_squeezed_or_clipped_anywhere():
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    hits = [h for h in ui_sweep.sweep() if h["kind"] != "stat"]                  # "stat": numbers to read, not faults
    assert hits == [], "\n".join(f"{h['width']} {h['kind']} {h['where']}: {h['text']!r} <- {h['ctl']!r}" for h in hits)
