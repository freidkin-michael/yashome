"""The device window's "What it is": the kind in plain words and how a device talks (the page's own functions,
run by node), and the fields GET /api/devices gives them (model, vendor, description, an ESPHome node)."""
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if "app" not in sys.modules:                 # alone: the core on a private copy of the fixtures, as test_pure does
    _FIX = tempfile.mkdtemp(prefix="yashome-test-")
    atexit.register(shutil.rmtree, _FIX, ignore_errors=True)
    shutil.copytree(os.path.join(os.path.dirname(__file__), "fixtures"), _FIX, dirs_exist_ok=True)
    os.environ["HOME_STACK_ROOT"] = _FIX
    os.environ.pop("HOUSE_ROOT", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
from tools.webpage import page as _dashboard_page   # index.html with web/app.css and web/js/*.js inline

PAGE = _dashboard_page()
CASES = [   # controls (code, kind), extra fields -> kind, via
    ([("state", "switch"), ("brightness", "bright")], {"transport": "z2m"}, "Dimmable light", "Zigbee"),
    ([("state", "switch"), ("power", "sensor")], {"transport": "z2m"}, "Smart plug with power metering", "Zigbee"),
    ([("state_l1", "switch"), ("state_l2", "switch")], {"transport": "z2m"}, "Wall switch with 2 channels", "Zigbee"),
    ([("temperature", "sensor"), ("humidity", "sensor")], {"transport": "z2m"}, "Temperature and humidity sensor", "Zigbee"),
    ([("presence", "sensor"), ("distance", "sensor")], {"transport": "mqtt", "esphome": True}, "mmWave presence radar", "Wi-Fi"),
    ([("contact", "sensor")], {"transport": "z2m"}, "Door or window contact", "Zigbee"),
    ([("action", "trigger_text")], {"transport": "z2m"}, "Wireless button", "Zigbee"),
    ([("power", "switch")], {"transport": "mqtt", "category": "climate"}, "Air conditioner", "Wi-Fi"),
    ([("state", "switch")], {"transport": "z2m", "description": "Zigbee router"}, "Zigbee repeater", "Zigbee"),
    ([("state", "switch"), ("energy", "sensor")], {"transport": "z2m", "description": "Smart plug (EU)"},
     "Smart plug with power metering", "Zigbee"),
    ([("state", "switch")], {"transport": "z2m", "description": "Smart plug (EU)"}, "Smart plug", "Zigbee"),
    ([("temperature", "sensor")], {"transport": "own", "category": "boiler"}, "boiler", ""),   # a plug-in's kind: its word; its way: none said
]


class WhatIs(unittest.TestCase):
    def test_kind_and_way_in_plain_words(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("no node")
        funcs = "\n".join(re.search(r, PAGE, re.S | re.M).group(0)     # deviceKind with the tables it reads
                          for r in (r"^const GUESS_KINDS = .*?^function deviceKind\(.*?^}", r"^function deviceVia\(.*?^}"))
        stub = ("const i18n = (k, ...a) => k.replace(/\\{(\\d)\\}/g, (_, i) => a[i]); const BY_ID = {};"
                "const catLabel = c => c || 'device';\n")
        devs = [{"controls": [{"code": c, "kind": k} for c, k in ctl], **extra} for ctl, extra, _k, _v in CASES]
        js = stub + funcs + f"\nconsole.log(JSON.stringify({json.dumps(devs)}.map(d => [deviceKind(d), deviceVia(d)])));"
        out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, [[k, v] for _c, _e, k, v in CASES])

    def test_the_device_list_carries_what_the_window_shows(self):
        app.DEVICES["whatis_probe"] = {"id": "whatis_probe", "name": "Probe", "transport": "mqtt", "category": "sensor",
                                       "controls": [], "model": "ESP32-C3", "availability_topic": "probe-node/status"}
        try:
            d = next(x for x in app.list_devices() if x["id"] == "whatis_probe")
        finally:
            app.DEVICES.pop("whatis_probe", None)
        self.assertEqual((d["model"], d["vendor"], d["description"], d["esphome"]), ("ESP32-C3", None, None, True))

    def test_a_kind_reads_in_english_with_no_table(self):
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not chrome:
            self.skipTest("no Chrome")
        funcs = re.search(r"^const CAT_EN = .*?^function catLabel\(.*?$", PAGE, re.S | re.M).group(0)
        js = ("const i18n = k => k;\n" + funcs + "\ndocument.getElementById('o').textContent = "
              "JSON.stringify(['switch', 'tv', 'ir', 'zigbee_thing', ''].map(catLabel));")      # English, or restricted mode: no table at all
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.html").write_text(f"<pre id=o></pre><script>{js}</script>")
            out = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={d}/c", "--dump-dom",
                                  f"file://{d}/p.html"], capture_output=True, text=True, timeout=60).stdout
        self.assertIn('["switch","TV","IR remote","device",""]', out.replace("&quot;", '"'))


if __name__ == "__main__":
    unittest.main()
