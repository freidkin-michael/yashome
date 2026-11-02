"""The "+ Room" chip of the tab row is there only while the house has no room; the map's bar has "New room", which opens
the new-room sheet; a room tab keeps its own actions in a menu (Esc closes it); a device window makes no room (360 px, real page)."""
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
CHIP = "!!document.getElementById('addroom-chip')"
MENU = """(async rid => { document.querySelector('[data-tab="room:' + rid + '"]').click(); await new Promise(r => setTimeout(r, 500));
  const b = document.querySelector('[data-tb="more-room"]'); if(!b) return 'no button';
  const bar = b.closest('div'), shown = [...bar.querySelectorAll(':scope > button, :scope > span > button')].length;
  b.click(); await new Promise(r => setTimeout(r, 200));
  const m = document.querySelector('.roommenu[role="menu"]'); if(!m) return 'no menu';
  const items = [...m.querySelectorAll('[role="menuitem"]')].map(x => x.dataset.mi + (x.classList.contains('danger') ? '!' : ''));
  const exp = b.getAttribute('aria-expanded'), pop = b.getAttribute('aria-haspopup'), focused = document.activeElement === m.querySelector('[role="menuitem"]');
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  await new Promise(r => setTimeout(r, 100));
  return JSON.stringify({shown, items, exp, pop, focused, closed: !document.querySelector('.roommenu'), back: document.activeElement === b}); })"""
NOSELECT = """(async () => { const d = Home.devices().find(x => x.category === "switch") || Home.devices()[0]; Home.openDevice(d.id);
  for(let i = 0; i < 30 && !document.getElementById('m_room'); i++) await new Promise(r => setTimeout(r, 100));
  const s = document.getElementById('m_room'); if(!s) return 'no select';
  const v = [...s.options].map(o => o.value); closeModal(); return JSON.stringify(v); })()"""
NEWROOM = """(async () => { Home.openTab('p:map'); for(let i = 0; i < 40 && !document.querySelector('[data-m="newroom"]'); i++)
    await new Promise(r => setTimeout(r, 100));
  const b = document.querySelector('[data-m="newroom"]'); if(!b) return 'no button';
  b.click(); await new Promise(r => setTimeout(r, 300));
  return !!document.querySelector('[data-m="sheet"] [data-s="rname"]'); })()"""


async def _run(ws_url: str, url: str, safe: bool = False) -> dict:
    import websockets
    u = urlparse(url)
    tok = parse_qs(u.query)["token"][0]

    def http(method, path, body=None):
        req = urllib.request.Request(f"{u.scheme}://{u.netloc}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"null")

    out = {}
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

        async def ev(js):
            r = await call("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
            return (r.get("result") or {}).get("value")

        await call("Emulation.setDeviceMetricsOverride", width=360, height=800, deviceScaleFactor=1, mobile=True)
        await call("Page.navigate", url=url)
        await asyncio.sleep(6)
        out["rooms_before"] = len(http("GET", "/api/rooms"))
        out["chip_without_rooms"] = await ev(CHIP)
        http("POST", "/api/rooms", {"name": "Test room"})
        await call("Page.navigate", url=url)
        await asyncio.sleep(6)
        out["chip_with_rooms"] = await ev(CHIP)
        out["map_newroom"] = await ev(NEWROOM)
        rid = [r for r in http("GET", "/api/rooms") if r["name"] == "Test room"][0]["id"]
        out["menu"] = json.loads(await ev(MENU + f"({json.dumps(rid)})"))
        out["select"] = json.loads(await ev(NOSELECT))
    return out


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_the_room_chip_only_in_a_house_without_rooms_and_the_map_makes_new_ones(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    assert r["rooms_before"] == 0 and r["chip_without_rooms"] is True, r      # a fresh house: the first room from the chip
    assert r["chip_with_rooms"] is False, r                                   # then: the map or a device window
    assert r["map_newroom"] is True, r
    m = r["menu"]                                                             # an empty room: no Order, only the menu button
    assert m["shown"] == 1 and m["items"] == ["Devices of the room", "Thermometer\u2026", "Rename room", "Edit on the map", "Delete room!"], m
    assert (m["pop"], m["exp"], m["focused"], m["closed"], m["back"]) == ("menu", "true", True, True, True), m
    assert "-new" not in r["select"] and "" in r["select"], r["select"]      # the device window makes no room
