"""The header's "?" (the sweep's house, a real page): the first visit shows how it is used; a tap and Enter open the help
sheet (a dialog); dragged with a mouse or a finger onto the gear it turns green, the gear's title shows on the screen, and
let go it shows the gear's hint; let go over nothing, it shows nothing."""
import asyncio
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
AT = """JSON.stringify((() => { const c = id => { const b = document.getElementById(id).getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; };
  const h = document.querySelector('header'), hb = h.getBoundingClientRect(), y = hb.y + 6;     // the header's own background: no control there
  let x = hb.x + hb.width * 0.4; while(x < hb.right && document.elementFromPoint(x, y) !== h) x += 4;
  return {help: c('helpbtn'), gear: c('gearbtn'), empty: [x, y]}; })())"""
STATE = """JSON.stringify({sheet: !!document.getElementById('hqsheet'), role: (document.getElementById('hqsheet') || {getAttribute: () => ''}).getAttribute('role'),
  pop: (document.querySelector('.hq-pop') || {}).dataset ? document.querySelector('.hq-pop').dataset.hq : null,
  ghost: !!document.querySelector('.hq-drag'), green: !!document.querySelector('.hq-drag.on'),
  name: (document.querySelector('.hq-name') || {}).textContent || '', target: !!document.querySelector('#gearbtn.hq-target'),
  nameIn: (n => !!n && n.getBoundingClientRect().top >= 0 && n.getBoundingClientRect().left >= 0 && n.getBoundingClientRect().right <= innerWidth)(document.querySelector('.hq-name:not([hidden])')),
  wiggle: document.getElementById('helpbtn').classList.contains('hq-wiggle')})"""


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

        async def reset():
            await ev("(() => { document.querySelectorAll('.hq-pop,#hqsheet').forEach(e => e.remove()); return '{}'; })()")

        async def mouse(path, mid=None):
            await call("Input.dispatchMouseEvent", type="mousePressed", x=path[0][0], y=path[0][1], button="left", clickCount=1)
            for x, y in path[1:]:
                await call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, button="left", buttons=1)
            if mid is not None:
                mid.update(await ev(STATE))
            await call("Input.dispatchMouseEvent", type="mouseReleased", x=path[-1][0], y=path[-1][1], button="left", clickCount=1)
            await asyncio.sleep(0.3)

        async def touch(path, mid=None):
            await call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": path[0][0], "y": path[0][1]}])
            for x, y in path[1:]:
                await call("Input.dispatchTouchEvent", type="touchMove", touchPoints=[{"x": x, "y": y}])
            if mid is not None:
                mid.update(await ev(STATE))
            await call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            await asyncio.sleep(0.3)

        def line(a, b, k=8):
            return [(a[0] + (b[0] - a[0]) * i / k, a[1] + (b[1] - a[1]) * i / k) for i in range(k + 1)]

        await call("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
        await call("Page.navigate", url=url)
        await asyncio.sleep(6)
        out["first"] = await ev(STATE)                                       # the tip of the first visit
        await reset()
        at = await ev(AT)
        await mouse([at["help"], at["help"]])
        out["tap"] = await ev(STATE)
        await reset()
        await ev("(() => { document.getElementById('helpbtn').focus(); return '{}'; })()")
        await call("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r")
        await call("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)
        await asyncio.sleep(0.3)
        out["enter"] = await ev(STATE)
        await reset()
        mid = {}
        await mouse(line(at["help"], at["gear"]), mid)
        out["mouse_mid"], out["mouse"] = mid, await ev(STATE)
        await reset()
        await mouse(line(at["help"], at["empty"]))
        out["mouse_empty"] = await ev(STATE)
        await reset()
        await call("Emulation.setDeviceMetricsOverride", width=360, height=800, deviceScaleFactor=1, mobile=True)
        await call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=2)
        await asyncio.sleep(1)
        at = await ev(AT)
        mid = {}
        await touch(line(at["help"], (at["gear"][0], at["gear"][1] + 56)), mid)          # the "?" rides 56 px above the finger
        out["touch_mid"], out["touch"] = mid, await ev(STATE)
    return out


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_the_help_button_opens_the_sheet_and_drags_onto_a_control(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    assert r["first"]["pop"] == "help.tip" and r["first"]["wiggle"], r
    assert r["tap"]["sheet"] and r["tap"]["role"] == "dialog" and not r["tap"]["ghost"], r
    assert r["enter"]["sheet"], r
    m = r["mouse_mid"]
    assert m["ghost"] and m["green"] and m["target"] and m["name"] == "Settings" and m["nameIn"], r
    assert r["mouse"]["pop"] == "header.settings" and not r["mouse"]["sheet"] and not r["mouse"]["ghost"], r
    assert r["mouse_empty"]["pop"] is None and not r["mouse_empty"]["sheet"], r
    assert r["touch_mid"]["green"] and r["touch_mid"]["name"] == "Settings" and r["touch_mid"]["nameIn"], r
    assert r["touch"]["pop"] == "header.settings" and not r["touch"]["target"], r
