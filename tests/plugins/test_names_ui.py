"""A refused rename reads in the dashboard's language: the Russian page shows the server's English refusal in Russian
(the sweep's house, a real page; nothing is renamed: the name is another device's)."""
import asyncio
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
PROBE = """(async () => {
  const ds = Home.devices().filter(d => d.name); if(ds.length < 2) return '{}';
  const out = {lang: window.I18N_LANG};
  try { await Home.api(`/api/devices/${encodeURIComponent(ds[0].id)}/rename`, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                                                  body: JSON.stringify({name: ds[1].name.toUpperCase()})}); out.msg = 'renamed'; }
  catch(e){ out.msg = e.message; out.detail = e.detail; }
  try { await Home.api(`/api/devices/${encodeURIComponent(ds[0].id)}/rename`, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                                                  body: JSON.stringify({name: 'x'.repeat(61)})}); }
  catch(e){ out.long = e.message; }
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
        await call("Page.enable")
        await call("Page.addScriptToEvaluateOnNewDocument", source="localStorage.setItem('lang', 'ru')")
        await call("Page.navigate", url=url)
        await asyncio.sleep(6)
        r = await call("Runtime.evaluate", expression=PROBE, awaitPromise=True, returnByValue=True)
        return json.loads((r.get("result") or {}).get("value") or "{}")


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_a_refused_rename_reads_in_russian(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    import ui_sweep
    monkeypatch.setattr(ui_sweep, "_cdp", _run)
    r = ui_sweep.sweep()
    assert r.get("lang") == "ru", r
    assert "уже занято другим устройством" in r["msg"] and "already" in r["detail"], r   # i18n
    assert r["long"] == "имя слишком длинное: не больше 60 символов", r   # i18n
