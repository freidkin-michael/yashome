"""An IR tile's "Extends the card" offers only devices with a face of their own (a TV, an AC), never a blaster card or a switch
(the sweep's house, a real page: its IR tile window)."""
import asyncio
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
PROBE = """(async () => {
  const wait = async sel => { for(let i = 0; i < 40 && !document.querySelector(sel); i++) await new Promise(r => setTimeout(r, 100)); return document.querySelector(sel); };
  Home.openTab('p:tech'); const c = await wait('#app .card[data-dev="sweep_tile"]'); if(!c) return 'no tile'; c.click();
  const s = await wait('#md [data-ira-sel="device"]'); if(!s) return 'no select';
  const by = Object.fromEntries(Home.devices().map(d => [d.id, d]));
  const opts = [...s.options].map(o => o.value).filter(Boolean);
  return JSON.stringify({opts: opts.map(v => [v, (by[v] || {}).category, !!(by[v] || {}).ir_blaster]),
                         blasters: Home.devices().filter(d => d.ir_blaster).map(d => d.id)}); })()"""


async def _run(ws_url: str, url: str, safe: bool = False) -> dict:
    import websockets
    async with websockets.connect(ws_url, max_size=None) as ws:
        n = 0

        async def call(method, **params):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == n:
                    return m.get("result", {})
        await call("Emulation.setDeviceMetricsOverride", width=360, height=800, deviceScaleFactor=1, mobile=True)
        await call("Page.navigate", url=url)
        await asyncio.sleep(6)
        r = await call("Runtime.evaluate", expression=PROBE, awaitPromise=True, returnByValue=True)
        return json.loads((r.get("result") or {}).get("value") or "{}")


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_a_tile_extends_only_a_device_with_a_face_never_a_blaster(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    assert r["blasters"], r                                                   # the house has a blaster card to leave out
    assert all(cat in ("tv", "climate") and not bl for _, cat, bl in r["opts"]), r
