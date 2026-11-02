"""panels ui.js in headless Chrome (panels_ui/apk.html, a stub Home): the launcher row names the published version and
draws a QR of its download with the address under it; a tablet behind it shows "old -> new" with its update error,
also for a build that ended while the tab was open (its line: "update available", the versions under Advanced); each tablet's
watchdog row shows on/off, its minutes, and its last raise or that the house cannot restart it."""
import html
import json
import pathlib
import re
import shutil
import subprocess
import time

import pytest

CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
PAGE = pathlib.Path(__file__).parent / "panels_ui" / "apk.html"


@pytest.mark.skipif(not CHROME, reason="no Chrome")
def test_the_launcher_row_names_its_version_and_a_tablet_behind_it_shows_the_step():
    dom = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--allow-file-access-from-files", "--virtual-time-budget=10000",
                          "--dump-dom", PAGE.as_uri()], capture_output=True, text=True, timeout=60).stdout
    got = json.loads(html.unescape(re.search(r"<title>(.*)</title>", dom).group(1)))
    assert got["errors"] == [] and "error" not in got, got
    assert got["row"] == "panel.apk · 3.33 (37) · WHEN"              # the build that ended while the tab was open   # i18n
    assert got["qrHidden"] is False and got["qr"] == [True, True] and got["qrToggled"] is True
    assert got["tablets"] == ["update available · the update failed", "update available"]   # i18n; the versions are under Advanced
    assert got["more"] == ["launcher 3.28 → 3.33 · update failed: no space", "launcher 3.32 → 3.33"]   # i18n
    at = time.strftime("%H:%M", time.localtime(1790000000))
    assert got["watch"] == [[True, f"Raise the launcher if it fell after min at most once in min last raise: {at} · failed: unreachable", "2", "10"],   # i18n
                            [False, "Raise the launcher if it fell after min at most once in min the house cannot restart it: turn on network debugging on the tablet", "3", "15"]]
