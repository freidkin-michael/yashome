"""A tile's height on a wide screen (h) and on a narrow one (hn, <= 640 px) live apart: each width shows its own, a turn across
640 px switches them, a double-click (auto size) forgets the current one only, and a screen opened earlier never writes its stale
copy of the other one back (the sweep's house, real pages; the page script is one closure, so DOM and HTTP only)."""
import asyncio
import json
import pathlib
import shutil
import sys
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")

FIND = """(async () => {                          // the first tab with a device grid: its scope is the tab's id
  for(const t of document.querySelectorAll('[data-tab]')){ t.click(); await new Promise(r => setTimeout(r, 400));
    const cd = document.querySelector('.grid.packed > .card[data-dev]');
    if(cd) return JSON.stringify({tab: t.dataset.tab, dev: cd.dataset.dev}); }
  return 'no tile'; })()"""
MINH = "__cd.style.minHeight"


def OPEN(tab, dev):
    return f"""(async () => {{ document.querySelector('[data-tab="{tab}"]').click(); await new Promise(r => setTimeout(r, 600));
  window.__cd = document.querySelector('.grid.packed > .card[data-dev="{dev}"]'); return __cd ? __cd.style.minHeight : 'no tile'; }})()"""


def RESET(dev):
    return f"""(async () => {{ document.querySelector('[data-tb="order"]').click(); await new Promise(r => setTimeout(r, 600));   // the handles
  window.__cd = document.querySelector('.grid.packed > .card[data-dev="{dev}"]');
  const rz = __cd.querySelector('.resizer'); if(!rz) return 'no resizer';
  rz.dispatchEvent(new MouseEvent('dblclick', {{bubbles: true}})); await new Promise(r => setTimeout(r, 900));
  return __cd.style.minHeight; }})()"""


class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get("id") == self.n:
                return m.get("result", {})

    async def ev(self, js):
        r = await self.call("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
        if r.get("exceptionDetails"):
            return "error: " + str(r["exceptionDetails"].get("exception", {}).get("description"))[:300]
        return (r.get("result") or {}).get("value")

    async def width(self, w):
        await self.call("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=w < 640)
        await asyncio.sleep(0.6)

    async def load(self, url, w):
        await self.width(w)
        await self.call("Page.navigate", url=url)
        await asyncio.sleep(6)


async def _run(ws_url: str, url: str, safe: bool = False) -> dict:
    import websockets
    u = urlparse(url)
    tok = parse_qs(u.query)["token"][0]

    def http(method, path, body=None):                    # "another screen": the house's API itself
        req = urllib.request.Request(f"{u.scheme}://{u.netloc}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    out = {}
    async with websockets.connect(ws_url, max_size=None) as ws:
        a = Page(ws)
        await a.load(url, 1280)
        found = json.loads(await a.ev(FIND))
        tab, dev = found["tab"], found["dev"]

        def stored():
            return http("GET", "/api/tile_sizes").get(tab, {}).get(dev)

        def put(**kw):
            http("PUT", f"/api/tile_sizes/{tab}", {"device": dev, **kw})

        put(w=1, h=300, col=-1, hn=150)                   # one page, both widths
        await a.load(url, 1280)
        out["wide"] = await a.ev(OPEN(tab, dev))
        await a.width(360)
        out["narrow"] = await a.ev(MINH)
        out["narrow_reset"] = [await a.ev(RESET(dev)), stored()]
        await a.width(1280)
        out["wide_again"] = await a.ev(MINH)

        put(w=1, h=300, col=-1, hn=150)                   # two pages opened before another screen changes the entry
        await a.load(url, 1280)
        await a.ev(OPEN(tab, dev))
        tid = (await a.call("Target.createTarget", url="about:blank"))["targetId"]
        async with websockets.connect(ws_url.rsplit("/", 1)[0] + "/" + tid, max_size=None) as ws2:
            b = Page(ws2)
            await b.load(url, 360)
            await b.ev(OPEN(tab, dev))
            put(w=1, h=300, col=-1, hn=90)                # the phone's narrow height changed after page A read it
            await a.ev(RESET(dev))                        # a wide reset on A must not send its hn 150
            out["after_wide_reset"] = stored()
            put(w=1, h=250, col=-1)                       # the desktop's wide height changed after page B read it
            await b.ev(RESET(dev))                        # a narrow reset on B must not send its h 300
            out["after_narrow_reset"] = stored()
    return out


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_wide_and_narrow_heights_live_apart(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    assert (r["wide"], r["narrow"], r["wide_again"]) == ("300px", "150px", "300px"), r
    assert r["narrow_reset"] == ["", {"w": 1, "h": 300, "col": -1}], r          # the narrow reset forgets hn only
    assert r["after_wide_reset"] == {"w": 0, "h": 0, "col": -1, "hn": 90}, r     # the other screen's hn kept
    assert r["after_narrow_reset"] == {"w": 1, "h": 250, "col": -1}, r           # the other screen's h kept
