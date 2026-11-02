"""The room's thermometer window in a real page (the sweep's house): the room's menu offers it, it lists Auto and the room's
thermometer, a press stores the choice (the house's order is that one alone) and Auto puts it back."""
import asyncio
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
WIN = """JSON.stringify((() => { const md = document.getElementById('md');
  return {modal: Home.modalId ? Home.modalId() : null, rows: [...md.querySelectorAll('[data-k="temperature"]')].map(b => [b.dataset.src, b.classList.contains('active')]),
          hum: md.querySelectorAll('[data-k="humidity"]').length}; })())"""


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
                m = json.loads(await asyncio.wait_for(ws.recv(), 20))
                if m.get("id") == n:
                    return m.get("result", {})

        async def ev(expr):
            r = await call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
            return json.loads((r.get("result") or {}).get("value") or "{}")

        await call("Page.addScriptToEvaluateOnNewDocument", source="localStorage.setItem('help.tip', '1')")
        await call("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
        await call("Page.navigate", url=url)
        await asyncio.sleep(6)
        out["room"] = await ev("Home.api('/api/rooms', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: 'Thermo room', devices: ['sensor_1']})})"
                               ".then(r => JSON.stringify({id: r.room.id}))")
        rid = out["room"]["id"]
        await ev(f"Home.refreshTabs().then(() => {{ document.querySelector('.tab[data-tab=\"room:{rid}\"]').click(); return '{{}}'; }})")
        await asyncio.sleep(1)
        await ev("(() => { document.querySelector('[data-tb=\"more-room\"]').click(); return '{}'; })()")
        out["menu"] = await ev("JSON.stringify([...document.querySelectorAll('.roommenu [role=menuitem]')].map(b => b.dataset.mi))")
        await ev("(() => { document.querySelector('.roommenu [data-mi=\"Thermometer\\u2026\"]').click(); return '{}'; })()")
        await asyncio.sleep(1)
        out["open"] = await ev(WIN)
        await ev("(() => { document.querySelector('#md [data-k=\"temperature\"][data-src=\"sensor_1/temperature\"]').click(); return '{}'; })()")
        await asyncio.sleep(1.5)
        out["picked"] = await ev(WIN)
        out["house"] = await ev(f"Home.api('/api/rooms').then(R => JSON.stringify(R.find(r => r.id === '{rid}').sensors.temperature))")
        await ev("(() => { document.querySelector('#md [data-k=\"temperature\"][data-src=\"auto\"]').click(); return '{}'; })()")
        await asyncio.sleep(1.5)
        out["auto"] = await ev(f"Home.api('/api/rooms').then(R => JSON.stringify(R.find(r => r.id === '{rid}').sensors.temperature))")
    return out


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_the_rooms_thermometer_is_chosen_in_its_window(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    assert "Thermometer…" in r["menu"], r                                         # findable without guessing: the room's menu   # i18n
    assert r["open"]["modal"] == "__room_sensors__" and r["open"]["rows"] == [["auto", True], ["sensor_1/temperature", False]], r
    assert r["open"]["hum"] == 2, r                                                  # the hygrometer the same way
    assert r["picked"]["rows"] == [["auto", False], ["sensor_1/temperature", True]], r
    assert r["house"] == {"pick": "sensor_1/temperature", "order": ["sensor_1/temperature"]}, r
    assert r["auto"]["pick"] == "auto", r
