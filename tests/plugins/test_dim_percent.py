"""A dimmer tile on the house's "_pct" mirror shows the percent of the light's own scale (a 0..254 zigbee light at 180
is 71 %), the widget's own state_max wins, a plain value stays (widgets.js PW.dimPercent, in headless Chrome)."""
import asyncio
import json
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
CASES = """JSON.stringify([
  PW.dimPercent({state_topic: 'home/state/L/brightness_pct'}, 180, [{id: 'L', controls: [{code: 'brightness', min: 0, max: 254}]}]),
  PW.dimPercent({state_topic: 'home/state/L/brightness_pct', state_max: 255}, 255, []),
  PW.dimPercent({state_topic: 'home/state/L/level'}, 42, []),
  PW.dimPercent({state_topic: 'home/state/L/brightness_pct'}, 'x', []),
  PW.dimPercent({state_topic: 'home/state/L/brightness_pct'}, 300, [{id: 'L', controls: [{code: 'brightness', min: 0, max: 254}]}])])"""


async def _run(ws_url: str):
    import websockets
    async with websockets.connect(ws_url, max_size=None) as ws:
        n = 0

        async def call(method, **params):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 20))
                if m.get("id") == n:
                    return m.get("result", {})
        src = (ROOT / "plugins/devices/panels/static/widgets.js").read_text(encoding="utf-8")
        await call("Runtime.evaluate", expression=src)
        r = await call("Runtime.evaluate", expression=CASES, returnByValue=True)
        return json.loads(r["result"]["value"])


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_a_zigbee_light_at_180_of_254_is_71_percent():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); dbg = s.getsockname()[1]; s.close()
    tmp = tempfile.mkdtemp()
    chrome = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={dbg}",
                               f"--user-data-dir={tmp}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                pages = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{dbg}/json/list", timeout=1).read()); break
            except Exception:
                time.sleep(0.2)
        got = asyncio.run(_run(next(p for p in pages if p.get("type") == "page")["webSocketDebuggerUrl"]))
    finally:
        chrome.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    assert got == [71, 100, 42, None, 100]
