"""Ask the house in the header (the sweep's house, a real page): Ask and a pill field (Search's class) right of the logo;
Enter hands the text to the plug-in that answers (voice) and clears it; a provider's examples drop under the empty
field and a click sends one; on a phone Ask first opens the field full width under the header."""
import asyncio
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
SPY = """(() => { window.__asked = []; const a = HomePlugins.ask[0]; window.__realAsk = a.open; a.open = t => { __asked.push(t); };
  a.examples = () => ['Is anyone home?', 'Which lights are on?']; return '{}'; })()"""
STATE = """JSON.stringify((() => { const v = e => !!e && e.getClientRects().length > 0, s = document.getElementById('askslot');
  const inp = s.querySelector('input'), b = s.querySelector('.askbtn'), logo = document.getElementById('brandtitle').getBoundingClientRect();
  return {field: v(inp), button: v(b), right: b.getBoundingClientRect().left >= logo.right - 1, asked: window.__asked || [], value: inp.value,
          pill: inp.classList.contains('pillfield') && getComputedStyle(inp).borderRadius === getComputedStyle(document.getElementById('navsearch')).borderRadius,
          wide: Math.round(inp.getBoundingClientRect().width), drop: [...s.querySelectorAll('.askdrop button')].map(x => x.textContent),
          modal: Home.modalId ? Home.modalId() : null}; })())"""


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

        await call("Page.addScriptToEvaluateOnNewDocument", source="localStorage.setItem('help.tip', '1')")
        await call("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
        await call("Page.navigate", url=url)
        await asyncio.sleep(6)
        await ev(SPY)
        await ev("(() => { document.querySelector('#askslot input').focus(); return '{}'; })()")
        await call("Input.insertText", text="Is anyone home?")
        await call("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r")
        await call("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)
        await asyncio.sleep(0.3)
        out["wide"] = await ev(STATE)
        await ev("(() => { document.querySelector('#askslot input').focus(); return '{}'; })()")
        await asyncio.sleep(0.2)
        out["drop"] = await ev(STATE)
        await ev("(() => { document.querySelector('#askslot .askdrop button').click(); return '{}'; })()")
        await asyncio.sleep(0.3)
        out["picked"] = await ev(STATE)
        await call("Emulation.setDeviceMetricsOverride", width=360, height=800, deviceScaleFactor=1, mobile=True)
        await asyncio.sleep(0.5)
        out["phone"] = await ev(STATE)
        await ev("(() => { document.querySelector('#askslot .askbtn').click(); return '{}'; })()")
        await asyncio.sleep(0.3)
        out["phone_open"] = await ev(STATE)
        await call("Input.insertText", text="Lock the door")
        await ev("(() => { document.querySelector('#askslot .askbtn').click(); return '{}'; })()")
        await asyncio.sleep(0.3)
        out["phone_sent"] = await ev(STATE)
        await call("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
        await ev("(() => { HomePlugins.ask[0].open = window.__realAsk; document.querySelector('#askslot .askbtn').click(); return '{}'; })()")
        await asyncio.sleep(1.5)
        out["real"] = await ev(STATE)
    return out


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_ask_the_house_sits_in_the_header(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    w, d = r["wide"], r["drop"]
    assert w["field"] and w["button"] and w["right"] and w["pill"] and w["asked"] == ["Is anyone home?"] and w["value"] == "", r
    assert d["drop"] == ["Is anyone home?", "Which lights are on?"] and r["picked"]["asked"][-1] == "Is anyone home?", r
    p, o, s = r["phone"], r["phone_open"], r["phone_sent"]
    assert p["button"] and not p["field"] and o["field"] and o["wide"] >= 320 and len(o["asked"]) == 2, r
    assert s["asked"][-1] == "Lock the door" and not s["field"], r
    assert r["real"]["modal"] == "__voice_ask__", r
