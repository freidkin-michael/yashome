"""A panel in a floating window (the sweep's house, a real page): it opens over the dashboard with the live view inside, is
dragged by its head and resized by its corner, minimizes to a chip and comes back, moves with the arrows and minimizes on
Esc; a press on a toggle inside sends the device's control; a phone gets a bottom sheet."""
import asyncio
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
SETUP = """(async () => {
  const d = Home.devices().find(x => (x.controls || []).some(c => c.kind === 'switch')), code = d.controls.find(c => c.kind === 'switch').code;
  await Home.api('/api/panels/kitchen-esp/layout', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({hw: 'waveshare7',
    widgets: [{id: 't1', type: 'toggle', x: 20, y: 20, w: 400, h: 300, text: 'Lamp', state_topic: 'home/state/' + d.id + '/' + code, cmd_topic: 'home/cmd/' + d.id + '/' + code}]})});
  localStorage.removeItem('panels.float');
  await PanelFloat.open('kitchen-esp');
  return JSON.stringify({dev: d.id}); })()"""
BOX = """JSON.stringify((() => { const p = document.querySelector('.pf'), c = document.querySelector('.pf-chip');
  const r = p && !p.hidden ? p.getBoundingClientRect() : null, h = p && p.querySelector('.pf-head').getBoundingClientRect(), g = p && p.querySelector('.pf-grip').getBoundingClientRect();
  return {open: !!r, chip: !!c, sheet: !!p && p.classList.contains('sheet'), x: r && Math.round(r.x), y: r && Math.round(r.y), w: r && Math.round(r.width), h: r && Math.round(r.height),
          head: h && [h.x + h.width / 3, h.y + h.height / 2], grip: g && [g.x + g.width / 2, g.y + g.height / 2], label: p && p.getAttribute('aria-label'),
          saved: JSON.parse(localStorage.getItem('panels.float') || '{}')}; })())"""
SPY = """(async () => { const f = document.querySelector('.pf iframe'); for(let i = 0; i < 60 && !(f.contentDocument && f.contentDocument.querySelector('[data-wid="t1"]')); i++) await new Promise(r => setTimeout(r, 100));
  const w = f.contentWindow; w.__sent = []; const o = w.fetch; w.fetch = (u, opt) => { w.__sent.push(String(u)); return o(u, opt); };
  const el = f.contentDocument.querySelector('[data-wid="t1"]'), fr = f.getBoundingClientRect(), b = (el.closest('.w') || el).getBoundingClientRect();
  return JSON.stringify({at: [fr.x + b.x + b.width / 2, fr.y + b.y + b.height / 2]}); })()"""


async def _run(ws_url: str, url: str, safe: bool = False) -> dict:
    import websockets
    out = {}
    async with websockets.connect(ws_url, max_size=None) as ws:
        n = 0

        async def call(method, **params):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 20))          # a hung call fails the test, never the run
                if m.get("id") == n:
                    return m.get("result", {})

        async def ev(expr):
            r = await call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
            return json.loads((r.get("result") or {}).get("value") or "{}")

        async def drag(a, b):
            await call("Input.dispatchMouseEvent", type="mousePressed", x=a[0], y=a[1], button="left", clickCount=1)
            for k in range(1, 6):
                await call("Input.dispatchMouseEvent", type="mouseMoved", x=a[0] + (b[0] - a[0]) * k / 5, y=a[1] + (b[1] - a[1]) * k / 5, button="left", buttons=1)
            await call("Input.dispatchMouseEvent", type="mouseReleased", x=b[0], y=b[1], button="left", clickCount=1)
            await asyncio.sleep(0.3)

        async def key(k, code, vk):
            await call("Input.dispatchKeyEvent", type="rawKeyDown", key=k, code=code, windowsVirtualKeyCode=vk)
            await call("Input.dispatchKeyEvent", type="keyUp", key=k, code=code, windowsVirtualKeyCode=vk)
            await asyncio.sleep(0.2)

        await call("Page.addScriptToEvaluateOnNewDocument", source="localStorage.setItem('help.tip', '1')")
        await call("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
        await call("Page.navigate", url=url)
        await asyncio.sleep(6)
        out["setup"] = await ev(SETUP)
        await asyncio.sleep(2)
        out["opened"] = b = await ev(BOX)
        await drag(b["head"], [b["head"][0] - 300, b["head"][1] + 120])
        out["dragged"] = b2 = await ev(BOX)
        await drag(b2["grip"], [b2["grip"][0] + 120, b2["grip"][1] + 90])
        out["resized"] = await ev(BOX)
        await ev("(() => { document.querySelector('.pf [data-pf=min]').click(); return '{}'; })()")
        out["minimized"] = await ev(BOX)
        await ev("(() => { document.querySelector('.pf-chip').click(); return '{}'; })()")
        await asyncio.sleep(0.5)
        out["restored"] = b3 = await ev(BOX)
        await ev("(() => { document.querySelector('.pf-head').focus(); return '{}'; })()")
        await key("ArrowRight", "ArrowRight", 39)
        out["arrow"] = await ev(BOX)
        await asyncio.sleep(2)
        spy = await ev(SPY)
        await call("Input.dispatchMouseEvent", type="mousePressed", x=spy["at"][0], y=spy["at"][1], button="left", clickCount=1)
        await call("Input.dispatchMouseEvent", type="mouseReleased", x=spy["at"][0], y=spy["at"][1], button="left", clickCount=1)
        await asyncio.sleep(1.5)
        out["sent"] = await ev("JSON.stringify(document.querySelector('.pf iframe').contentWindow.__sent)")
        await ev("(() => { document.querySelector('.pf-head').focus(); return '{}'; })()")
        await key("Escape", "Escape", 27)
        out["esc"] = await ev(BOX)
        await call("Emulation.setDeviceMetricsOverride", width=360, height=800, deviceScaleFactor=1, mobile=True)
        await ev("(async () => { await PanelFloat.open('kitchen-esp'); return '{}'; })()")
        await asyncio.sleep(0.5)
        out["phone"] = await ev(BOX)
        out["b3"] = b3
    return out


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_a_panel_floats_over_the_dashboard_and_works(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    o, d, z = r["opened"], r["dragged"], r["resized"]
    assert o["open"] and o["w"] >= 220 and "Kitchen screen" in (o["label"] or ""), r
    assert (d["x"], d["y"]) == (o["x"] - 300, o["y"] + 120) and d["saved"]["x"] == d["x"], r
    assert (z["w"], z["h"]) == (d["w"] + 120, d["h"] + 90), r
    assert not r["minimized"]["open"] and r["minimized"]["chip"] and r["restored"]["open"] and not r["restored"]["chip"], r
    assert r["arrow"]["x"] == r["restored"]["x"] + 16, r
    assert any("/api/devices/" + r["setup"]["dev"] in u and u.endswith("/control") for u in r["sent"]), r
    assert not r["esc"]["open"] and r["esc"]["chip"], r
    assert r["phone"]["open"] and r["phone"]["sheet"] and r["phone"]["saved"]["w"] == r["esc"]["saved"]["w"], r      # the sheet keeps the desktop size
