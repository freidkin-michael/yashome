"""map: the Opening tool driven by the browser's own input (CDP Input.*), a mouse at 1280 px and a finger at 360 px (the plan turned):
a stroke drawn beside a free wall lands on it, Window or door is chosen by a real press, a tap on it opens its sheet again."""
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

websockets = pytest.importorskip("websockets")
HERE = pathlib.Path(__file__).resolve().parent
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _drive(ws_url: str, url: str, width: int, touch: bool, scenario: str = "opening") -> dict:
    async with websockets.connect(ws_url, max_size=None) as ws:
        n = 0

        async def call(method, **params):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == n:
                    return r.get("result", {})

        async def js(expr):
            r = await call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
            return (r.get("result") or {}).get("value")
        await call("Emulation.setDeviceMetricsOverride", width=width, height=800 if width > 700 else 760, deviceScaleFactor=1, mobile=False)      # the harness has no meta viewport: a mobile layout would be 980 px wide
        if touch:
            await call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=2)
        await call("Page.navigate", url=url)
        for _ in range(60):
            if "IDLE" in (await js("document.getElementById('out').textContent") or ""):
                break
            await asyncio.sleep(0.2)

        async def tap(x, y):
            if touch:
                await call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
                await call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            else:
                for t in ("mousePressed", "mouseReleased"):
                    await call("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
            await asyncio.sleep(0.15)

        async def press(sel):
            r = await js(f"(() => {{ const e = document.querySelector('{sel}'); if(!e) return null; const q = e.getBoundingClientRect(); return [q.left + q.width / 2, q.top + q.height / 2]; }})()")
            assert r, sel
            await tap(*r)

        async def stroke(a, b):
            steps = [(a[0] + (b[0] - a[0]) * k / 6, a[1] + (b[1] - a[1]) * k / 6) for k in range(7)]
            if touch:
                await call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": a[0], "y": a[1]}])
                for x, y in steps[1:]:
                    await call("Input.dispatchTouchEvent", type="touchMove", touchPoints=[{"x": x, "y": y}])
                    await asyncio.sleep(0.02)
                await call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            else:
                await call("Input.dispatchMouseEvent", type="mousePressed", x=a[0], y=a[1], button="left", clickCount=1)
                for x, y in steps[1:]:
                    await call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, button="left", buttons=1)
                    await asyncio.sleep(0.02)
                await call("Input.dispatchMouseEvent", type="mouseReleased", x=b[0], y=b[1], button="left", clickCount=1)
            await asyncio.sleep(0.2)
        if scenario == "move":                                            # "Move it": a drag moves the plan, Done (a shaky press too) saves, Esc puts it back
            async def to_move():
                if await js("!!document.querySelector('.mapbar.narrow') && !document.querySelector('.mapbar.open')"):
                    await press('[data-m="more"]')
                await press('[data-m="underlay"]')
                await press('[data-m="sheet"] [data-u="move"]')
            v = await js("(() => { const q = document.querySelector('[data-m=\"view\"]').getBoundingClientRect(); return [(q.left + q.right) / 2, (Math.max(q.top, 0) + Math.min(q.bottom, innerHeight)) / 2]; })()")
            x0 = await js("__map().underlay.x")
            await to_move()
            await stroke(v, [v[0] + 40, v[1] + 30])
            out = {"cover": await js("!!document.querySelector('[data-m=\"ulmove\"]')")}
            r = await js("(() => { const q = document.querySelector('[data-b=\"uldone\"]').getBoundingClientRect(); return [q.left + q.width / 2, q.top + q.height / 2]; })()")
            await js("window.__failPatch = true")                              # the house refuses: the cover stays and says so
            await tap(*r)
            await asyncio.sleep(0.3)
            out["refused"] = await js("!!document.querySelector('[data-m=\"ulmove\"]') && /refused/.test(document.querySelector('[data-m=\"ulmove\"] span').textContent)")
            await js("window.__failPatch = false")
            await stroke(r, [r[0] + 4, r[1] + 3])                             # a shaking hand: the press wanders 5 px on the button
            await asyncio.sleep(0.3)
            out["done"] = await js("!document.querySelector('[data-m=\"ulmove\"]')")
            x1 = await js("__map().underlay.x")
            out["moved"] = abs(x1 - x0) > 1
            await to_move()
            await stroke(v, [v[0] - 50, v[1]])
            await call("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
            await call("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27)
            await asyncio.sleep(0.3)
            out["esc"] = await js("!document.querySelector('[data-m=\"ulmove\"]')")
            out["kept"] = abs(await js("__map().underlay.x") - x1) < 1e-6
            return out
        if scenario == "spot":                                            # "What is here?": New load right under the search, in sight
            w = await js("__wall")
            await tap(*(await js(f"__atNow({w[0] - 0.12}, {(w[1] + w[2]) / 2})")))
            geo = "(() => { const s = document.querySelector('[data-m=\"sheet\"]'), b = s.querySelector('[data-s=\"new\"]'), l = s.querySelector('[data-s=\"list\"]'); if(!b) return null; const q = b.getBoundingClientRect(), v = s.getBoundingClientRect(); return {above: q.bottom <= l.getBoundingClientRect().top + 1, inside: q.top >= v.top - 1 && q.bottom <= v.bottom + 1}; })()"
            out = {"open": await js(geo)}
            await js("(() => { const f = document.querySelector('[data-m=\"sheet\"] [data-s=\"find\"]'); f.value = 'zzzz'; f.dispatchEvent(new Event('input')); })()")
            await js("document.querySelector('[data-m=\"sheet\"]').scrollTop = 1e6")
            out["searched"] = await js(geo)
            await press('[data-m="sheet"] [data-s="new"]')
            out["new"] = await js("!!document.querySelector('[data-m=\"sheet\"] [data-s=\"type\"]')")
            return out
        if await js("!!document.querySelector('.mapbar.narrow') && !document.querySelector('.mapbar.open')"):
            await press('[data-m="more"]')
        await press('[data-m="optool"]')
        w = await js("__wall")
        a, b = await js(f"__atNow({w[0]}, {w[1] + 0.02})"), await js(f"__atNow({w[0]}, {w[2] - 0.02})")
        side = [6, 0] if a[0] == b[0] else [0, 6]                         # beside the wall line, not on it
        await stroke([a[0] + side[0], a[1] + side[1]], [b[0] + side[0], b[1] + side[1]])
        out = {"sheet": await js("!!document.querySelector('[data-m=\"sheet\"] [data-s=\"kind-door\"]')"),
               "hint": await js("(document.querySelector('[data-m=\"hint\"]') || {}).textContent"), "ab": [a, b]}
        assert out["sheet"], out
        await press('[data-m="sheet"] [data-s="kind-door"]')
        await press('[data-m="sheet"] [data-s="open-slide"]')
        await press('[data-m="sheet"] [data-s="close"]')
        await asyncio.sleep(1.2)                                          # the save
        out["ops"] = await js("JSON.stringify(__map().openings || [])")
        mid = await js(f"__atNow({w[0]}, {(w[1] + w[2]) / 2})")
        await tap(*mid)
        out["again"] = await js("!!document.querySelector('[data-m=\"sheet\"] [data-s=\"del\"]')")
        out["drawn"] = await js("document.querySelectorAll('svg [data-op]').length")
        out["wall"] = w
        return out


def _run(width, touch, query, scenario) -> dict:
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(HERE / "map_ui" / "harness.html", d)
        shutil.copy(HERE.parent.parent / "plugins" / "tools" / "map" / "ui.js", d)
        port = _port()
        prof = tempfile.mkdtemp(prefix="opreal")
        p = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                              "--allow-file-access-from-files", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(60):
                try:
                    pages = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list").read())
                    break
                except OSError:
                    time.sleep(0.25)
            ws_url = next(x for x in pages if x["type"] == "page")["webSocketDebuggerUrl"]
            out = asyncio.run(asyncio.wait_for(_drive(ws_url, f"file://{d}/harness.html{query}", width, touch, scenario), 90))
        finally:
            p.terminate()
            p.wait(10)
            shutil.rmtree(prof, ignore_errors=True)
    return out


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
@pytest.mark.parametrize("width,touch,query", [(1280, False, "?idle=1&underlay=1"), (1280, True, "?idle=1&underlay=1"),
                                               (360, False, "?idle=1&underlay=1&portrait=1"), (360, True, "?idle=1&underlay=1&portrait=1")])
def test_the_plan_is_moved_and_done_ends_it_with_a_mouse_or_a_finger(width, touch, query):
    out = _run(width, touch, query, "move")
    assert out == {"cover": True, "refused": True, "done": True, "moved": True, "esc": True, "kept": True}, out


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_new_load_stands_under_the_search_and_stays_in_sight_at_360_with_a_real_finger():
    out = _run(360, True, "?idle=1&portrait=1", "spot")
    assert out["open"] == {"above": True, "inside": True}, out                  # before the first room group
    assert out["searched"] == {"above": True, "inside": True}, out              # a search typed and the list scrolled: still there
    assert out["new"] is True, out


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
@pytest.mark.parametrize("width,touch,query", [(1280, False, "?idle=1"), (360, True, "?idle=1&portrait=1")])
def test_an_opening_is_drawn_with_a_real_mouse_and_a_real_finger(width, touch, query):
    out = _run(width, touch, query, "opening")
    ops = json.loads(out["ops"])
    assert out["sheet"] is True and len(ops) == 1, out
    o = ops[0]
    w = out["wall"]
    assert (o["kind"], o["open"]) == ("door", "slide") and abs(o["a"][0] - w[0]) < 1e-4 and abs(o["b"][0] - w[0]) < 1e-4, (o, w)   # on the wall's line
    assert w[1] < o["a"][1] < o["b"][1] < w[2], (o, w)
    assert out["again"] is True and out["drawn"] == 1, out
