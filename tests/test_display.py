"""display.js: every screen shows a temperature and a humidity the same way (whole numbers held until the value is 0.6 away),
and no screen formats them on its own."""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
CASES = """
const out = {};
out.round = [YDisp.temp(21.4), YDisp.temp(21.5), YDisp.temp(-0.4), YDisp.hum(48.6), YDisp.temp(null), YDisp.temp('22.7')];
const seq = [22.4, 22.6, 22.9, 23.1, 22.5, 22.4, 22.39, 21.9];          // one sensor's reports
out.held = seq.map(v => YDisp.temp(v, 'dev/temperature'));
out.other = YDisp.temp(22.6, 'dev2/temperature');                        // another sensor holds its own
out.kind = ['temperature', 'room_temp', 'humidity', 'temp', 'battery', 'local_temperature'].map(YDisp.kind);
out.reading = [YDisp.reading('battery', 77.7, 'x'), YDisp.reading('humidity', 55.5, 'h1')];
document.body.textContent = JSON.stringify(out);
"""


@pytest.mark.skipif(not CHROME, reason="no Chrome or Chromium")
def test_the_formatter_rounds_and_holds_each_reading():
    with tempfile.TemporaryDirectory() as tmp:
        page = pathlib.Path(tmp) / "t.html"
        page.write_text("<html><body><script>" + (ROOT / "display.js").read_text(encoding="utf-8")
                        + "</script><script>" + CASES + "</script></body></html>", encoding="utf-8")
        dom = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={tmp}/u", "--dump-dom",
                              page.as_uri()], capture_output=True, text=True, timeout=60).stdout
    got = json.loads(re.search(r"<body>(.*)</body>", dom, re.S).group(1))
    assert got["round"] == ["21", "22", "0", "49", "", "23"]
    assert got["held"] == ["22", "23", "23", "23", "23", "22", "22", "22"]    # 22.6: 0.6 from 22, moves; 22.5 stays 23; 22.4 moves back
    assert got["other"] == "23"
    assert got["kind"] == ["temp", "temp", "hum", "", "", "temp"]           # an AC's set point ("temp") is no reading
    assert got["reading"] == [None, "56"]


SURFACES = ["index.html", "web/app.css", *sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("web/js/*.js")), "plugins/tools/map/ui.js", "plugins/devices/climate/ui.js", "plugins/devices/panels/static/widgets.js",
            "plugins/devices/panels/static/view.html", "plugins/devices/panels/static/builder.html", "plugins/sources/weather/ui.js"]
SELF = re.compile(r"toFixed\(1\)|\* ?10\) ?/ ?10|Math\.round\(")
CLIMATE = re.compile(r"temperature|room_temp|humid|\\u00b0|&deg;", re.I)
SETPOINT = re.compile(r"sl\.value|v\.temp\b|temp_min|temp_max|setpoint|\.temp\)|lat|lon|font|\bpx\b")


def test_no_screen_formats_a_temperature_or_a_humidity_on_its_own():
    bad = []
    for rel in SURFACES:
        for n, ln in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if CLIMATE.search(ln) and SELF.search(ln) and "YDisp" not in ln and not SETPOINT.search(ln):
                bad.append(f"{rel}:{n}")
    assert bad == []
    for page in ("index.html", "plugins/devices/panels/static/view.html", "plugins/devices/panels/static/builder.html"):
        assert '<script src="/display.js"></script>' in (ROOT / page).read_text(encoding="utf-8"), page
