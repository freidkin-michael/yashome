"""A card says how the house reaches the device: an IR tile's card has the IR badge naming its blaster, the blaster's
own card has none (the sweep's house, a real page)."""
import asyncio
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
PROBE = """(async () => {
  const ds = Home.devices(), out = {tiles: [], blasters: []};
  for(const t of [...document.querySelectorAll('button.tab[data-tab]')].map(b => b.dataset.tab)){
    Home.openTab(t); await new Promise(r => setTimeout(r, 250));
    for(const d of ds){
      const c = document.getElementById('c_' + d.id); if(!c) continue;
      const b = [...c.querySelectorAll('.tbadge')].map(x => [x.dataset.t, x.title]);
      if(d.ir_appliance) out.tiles.push([d.id, b]);
      if(d.ir_blaster) out.blasters.push([d.id, b]);
    }
  }
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
        await call("Page.navigate", url=url)
        await asyncio.sleep(6)
        r = await call("Runtime.evaluate", expression=PROBE, awaitPromise=True, returnByValue=True)
        return json.loads((r.get("result") or {}).get("value") or "{}")


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_an_ir_tile_card_has_the_ir_badge_and_a_blaster_card_none(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    assert r["tiles"] and all(any(t == "ir" and title for t, title in b) for _, b in r["tiles"]), r
    assert all(b == [] for _, b in r["blasters"]), r
