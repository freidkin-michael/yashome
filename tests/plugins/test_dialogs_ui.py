"""Every dialog of the dashboard (the sweep's house, a real page): no corner x, a footer with the primary button at the
bottom right, and the content as wide as the dialog (no empty band beside it), at 1280 and 360. Shots: DIALOG_SHOTS=<dir>."""
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ui_sweep  # noqa: E402

PROBE = """JSON.stringify((() => { const md = document.getElementById('md'), cs = getComputedStyle(md);
  const seen = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const xs = [...md.querySelectorAll('button')].filter(b => seen(b) && /^(\\u00d7|x|\\u2715)$/i.test(b.textContent.trim()));
  const ok = md.querySelector(':scope > .mfoot .dlg-btn'), inner = md.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const kids = [...md.children].filter(e => !e.classList.contains('mfoot') && seen(e));
  const sel = [...md.querySelectorAll('select')].filter(seen).map(e => [Math.round(e.getBoundingClientRect().height), getComputedStyle(e).backgroundColor]);
  return {open: document.getElementById('bd').classList.contains('show'), xs: xs.length, ok: !!(ok && seen(ok)), okText: ok ? ok.textContent : '',
          primary: !!(ok && ok.classList.contains('primary')), sel,
          okRight: ok ? Math.round(md.getBoundingClientRect().right - ok.getBoundingClientRect().right) : null,
          dead: Math.max(0, ...kids.map(e => Math.round(inner - e.getBoundingClientRect().width))), focus: document.activeElement === ok}; })())"""
OPENERS = {"device": "Home.openDevice(Home.devices().find(d => (d.controls || []).length).id)",
           "health": "document.querySelector('[data-w=badge]').click()",
           "ask": "HomePlugins.ask[0].open('')"}


async def _run(ws_url, url, safe=False):
    import websockets
    out, shots = {}, os.environ.get("DIALOG_SHOTS")
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

        async def ev(expr):
            r = await call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
            return (r.get("result") or {}).get("value")
        for w, h in ((1280, 800), (360, 760)):
            await call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
            await call("Page.navigate", url=url)
            await asyncio.sleep(3)
            await ev("localStorage.setItem('help.tip', '1'); 1")
            for name, js in OPENERS.items():
                await ev("Home.closeModal(); 1")
                await asyncio.sleep(0.3)
                await ev(f"(() => {{ try {{ {js}; }} catch(e) {{}} }})(); 1")
                await asyncio.sleep(1.2)
                out[f"{name}@{w}"] = json.loads(await ev(PROBE))
                if shots:
                    d = await call("Page.captureScreenshot", format="png")
                    Path(shots, f"dialog-{name}-{w}.png").write_bytes(base64.b64decode(d["data"]))
            await ev("Home.closeModal(); 1")
            await ev("document.getElementById('bd').classList.contains('show') ? 0 : 1")
    return out


@pytest.mark.skipif(not ui_sweep.CHROME, reason="no Chrome")
def test_every_dialog_has_a_footer_button_no_corner_x_and_no_empty_band(monkeypatch):
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    got = ui_sweep.sweep()
    bad = {k: v for k, v in got.items() if v["open"] and (v["xs"] or not v["ok"] or v["dead"] > 2 or v["okRight"] is None or v["okRight"] > 40
                                                       or (v["okText"], v["primary"]) not in (("Close", False), ("OK", True))
                                                       or (not k.startswith("health") and v["okText"] != "Close") or any(h < 40 for h, _bg in v["sel"]))}
    # the device window and Ask have actions of their own: a secondary Close; Health with nothing to do here: a primary OK
    assert all(v["open"] for v in got.values()), got                         # every dialog did open
    assert bad == {}, bad
