"""A device whose room the map sets has a grey Room field that says so and "Show on the map" beside it, which
opens the map tab with that device to focus (the sweep's house, a real page; every device is made map-set)."""
import asyncio
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
LOCK_ALL = """(() => { const f = window.fetch;
  window.fetch = async (u, o) => {
    if(String(u).includes('/api/rooms/locked')){
      const all = await (await f('/api/devices', o)).json();
      return new Response(JSON.stringify({devices: all.map(d => d.id), rooms: [], hidden: []}), {headers: {'Content-Type': 'application/json'}});
    }
    return f(u, o); }; })()"""
PROBE = """(async () => {
  const wait = async sel => { for(let i = 0; i < 40 && !document.querySelector(sel); i++) await new Promise(r => setTimeout(r, 100)); return document.querySelector(sel); };
  const d = Home.devices()[0]; if(!d) return '{}';
  Home.openDevice(d.id);
  const b = await wait('#m_room_map'); if(!b) return JSON.stringify({id: d.id, button: false});
  const out = {id: d.id, button: true, line: document.getElementById('m_room').title.trim(), same: document.getElementById('m_room').closest('.row') === b.closest('.row'),
               grey: document.getElementById('m_room').disabled, room: Home.roomOf ? (Home.roomOf(d.id) || '') : ''};
  b.click();
  await new Promise(r => setTimeout(r, 400));
  out.focus = sessionStorage.getItem('map.focus');
  const act = document.querySelector('button.tab.active[data-tab]');
  out.tab = act ? act.dataset.tab : '';
  out.modal = !!document.querySelector('#bd.show');
  return JSON.stringify(out); })()"""


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
        await call("Page.enable")
        await call("Page.addScriptToEvaluateOnNewDocument", source=LOCK_ALL)
        await call("Emulation.setDeviceMetricsOverride", width=360, height=800, deviceScaleFactor=1, mobile=True)
        await call("Page.navigate", url=url)
        await asyncio.sleep(6)
        r = await call("Runtime.evaluate", expression=PROBE, awaitPromise=True, returnByValue=True)
        return json.loads((r.get("result") or {}).get("value") or "{}")


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_a_map_set_room_names_its_zone_and_opens_the_map_at_the_device(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    assert r.get("button") and r["grey"], r
    assert r["line"] and r["same"], r                                         # the grey field says why; the button in the Room row
    assert r["focus"] in (r["id"], None) and r["tab"] == "p:map" and not r["modal"], r   # None: the map took it already
