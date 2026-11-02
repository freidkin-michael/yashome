"""ir ui.js in headless Chrome (ir_ui/zones.html, a stub Home): a lamp's zone keys greyed while its feed is off come
back, with "sent: X" in the zone's title, the moment the feed is on; the title follows each later send."""
import html
import json
import pathlib
import re
import shutil
import subprocess

import pytest

CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
PAGE = pathlib.Path(__file__).parent / "ir_ui" / "zones.html"


@pytest.mark.skipif(not CHROME, reason="no Chrome")
def test_zone_keys_and_their_sent_title_come_back_when_the_feed_is_on():
    dom = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--allow-file-access-from-files",
                          "--dump-dom", PAGE.as_uri()], capture_output=True, text=True, timeout=60).stdout
    got = json.loads(html.unescape(re.search(r"<title>(.*)</title>", dom).group(1)))
    assert got["errors"] == []
    off, on, sent = got["steps"]
    assert off == "off: Fan | Light / power=1 / disabled=2"                   # no "sent" while the feed is off: that send did nothing
    assert on == "on: Fan · sent: Fan II | Light / power=0 / disabled=0"   # i18n
    assert sent == "sent: Fan · sent: Fan II | Light · sent: Light On / power=0 / disabled=0"   # i18n
    assert got["zones"] == ["tvk=0", "tvd=2"]                                  # "part of this remote": no zones; another device: zones
