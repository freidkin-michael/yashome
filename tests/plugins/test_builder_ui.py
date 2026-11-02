"""The panel builder: its palette offers every widget kind of the registry (but legacy names); a deployment can add
layout checks and limit what the builder offers; with touch a widget is added and dragged; on a phone the builder says
it is best on a wider screen until "Try anyway" (the sweep's house, a real page with touch emulation)."""
import asyncio
import json
import os
import pathlib
import re
import shutil
import sys

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as core

ROOT = pathlib.Path(__file__).resolve().parents[2]
me = sys.modules["yashome_plugins.panels"]
HOUSE = {"Authorization": "Bearer " + os.environ["DASHBOARD_TOKEN"]}
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


def test_the_palette_offers_every_kind_of_the_registry():
    src = (ROOT / "plugins/devices/panels/static/builder.html").read_text(encoding="utf-8")
    block = src[src.index("const TYPES = ["):src.index("];", src.index("const TYPES = ["))]
    offered = set(re.findall(r"\['([a-z]+)',", block))
    assert offered == me._WIDGET_TYPES - me._LEGACY_TYPES and not offered & me._LEGACY_TYPES


def test_a_layout_check_and_the_limits(monkeypatch):
    monkeypatch.setattr(me, "_layouts", {})
    monkeypatch.setattr(me, "_save_layouts", lambda: None)
    monkeypatch.setattr(me, "_publish", lambda name: None)

    def only_play(name, body):
        if name != "play":
            raise HTTPException(403, "only the playground")
    monkeypatch.setattr(me, "LAYOUT_CHECKS", [only_play])
    monkeypatch.setattr(me, "BUILDER_LIMITS", {"only": "play", "hide": ["flash"]})
    c = TestClient(core.app)
    body = {"hw": "waveshare7", "widgets": []}
    assert c.put("/api/panels/other/layout", headers=HOUSE, json=body).status_code == 403
    assert c.put("/api/panels/play/layout", headers=HOUSE, json=body).status_code == 200
    assert c.get("/api/panels/limits", headers=HOUSE).json() == {"only": "play", "hide": ["flash"]}


PROBE = """(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  for(let i = 0; i < 50 && !(typeof layout !== 'undefined' && document.querySelector('#panelSel option')); i++) await sleep(100);
  const r = el => { const b = el.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; };
  return JSON.stringify({n: layout.length, narrow: !document.getElementById('narrow').hidden,
                         add: r(document.getElementById('addWidget')), go: r(document.getElementById('narrowGo')), w: innerWidth}); })()"""
LAST = """(() => { const w = layout[layout.length - 1], el = document.querySelector('#stage [data-id="' + w.id + '"]') || [...document.querySelectorAll('#stage > *')].pop();
  const b = el.getBoundingClientRect(); return JSON.stringify({n: layout.length, id: w.id, x: w.x, y: w.y, at: [b.x + 10, b.y + 10]}); })()"""


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

        async def tap(x, y):
            for t in ("touchStart", "touchEnd"):
                await call("Input.dispatchTouchEvent", type=t, touchPoints=[{"x": x, "y": y}] if t == "touchStart" else [])
            await asyncio.sleep(0.3)

        async def swipe(x, y, dx, dy):
            await call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
            for k in range(1, 6):
                await call("Input.dispatchTouchEvent", type="touchMove", touchPoints=[{"x": x + dx * k / 5, "y": y + dy * k / 5}])
            await call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            await asyncio.sleep(0.3)
        await call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=2)
        await call("Page.navigate", url=url)                                  # the dashboard keeps the token for the builder
        await asyncio.sleep(5)
        for width in (1280, 360):
            await ev("(() => { try { dirty = false; } catch(e) {} return '{}'; })()")   # unsaved test edits: no leave-page prompt
            await call("Emulation.setDeviceMetricsOverride", width=width, height=800, deviceScaleFactor=1, mobile=width < 700)
            await call("Page.navigate", url=url.split("?")[0].rstrip("/") + "/panel-builder")
            await asyncio.sleep(3)
            s = await ev(PROBE)
            out[f"narrow{width}"] = s["narrow"]
            if s["narrow"]:
                await tap(*s["go"])
                s = await ev(PROBE)
                out[f"after_go{width}"] = s["narrow"]
            await tap(*s["add"])
            a = await ev(LAST)
            out[f"added{width}"] = a["n"] - s["n"]
            await swipe(*a["at"], 80, 40)
            b = await ev(LAST)
            out[f"moved{width}"] = (b["x"], b["y"]) != (a["x"], a["y"]) and b["id"] == a["id"]
    return out


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_touch_adds_and_drags_and_a_phone_hears_it_is_cramped(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    assert r["narrow1280"] is False and r["narrow360"] is True and r["after_go360"] is False, r
    assert r["added1280"] == 1 and r["added360"] == 1, r
    assert r["moved1280"] and r["moved360"], r
