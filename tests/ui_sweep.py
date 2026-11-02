"""Layout sweep: every dialog, sheet, settings page and tab of the dashboard and its plug-ins, at 360, 600 and 1280 px, in headless Chrome.
A hit is a label's text that overlaps a field or button, or a text box that clips its text. Run: python tests/ui_sweep.py [house dir]
(a copy of tests/fixtures by default). The app runs alone: its own port, no broker (MQTT_HOST is a test address), no real .env, and no
plug-in that reaches devices by itself (webos, cameras). Prints the hits; exit 1 when there is any."""
import asyncio
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import websockets

ROOT = pathlib.Path(os.environ.get("SWEEP_ROOT") or pathlib.Path(__file__).resolve().parents[1])     # a copy of the code may be swept
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
TOKEN = "sweep-token-not-real"


def _redact(v):
    """A hit is printed: no house key in it, whatever text carries one (a URL with ?token=)."""
    return re.sub(r"(token=)[^&\s'\"]+", r"\1***", v) if isinstance(v, str) else v
WIDTHS = (360, 600, 1280)
SKIP_PLUGINS = {"webos", "cameras"}

CHECK_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms)), DEBUG = !!window.__sweepDebug;
  // the body of a closed <details> is not seen, though Chrome still lays it out (content-visibility: hidden)
  const folded = e => { const d = e.closest('details:not([open])'); return !!d && !e.closest('summary'); };
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !folded(e); };
  const CTL = 'input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select,textarea,button';
  function scan(where, only){
    const stx = document.getElementById('stx');
    const root = only || (document.getElementById('bd').classList.contains('show') ? document.getElementById('md')
      : stx && !stx.hidden ? stx : document.getElementById('app') || document.body);
    if(!root) return [];
    root.querySelectorAll('details:not([open])').forEach(d => { d.open = true; });     // Advanced too: its fields are checked open
    const out = [], ctl = [...root.querySelectorAll(CTL)].filter(vis).map(e => [e, e.getBoundingClientRect()]);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for(let n = walker.nextNode(); n; n = walker.nextNode()){
      const t = n.nodeValue.trim(), p = n.parentElement;
      const ps = p && getComputedStyle(p);                      // a label squeezed to no width is the very case: its box is not asked to be wide
      if(t.length < 2 || !p || ps.visibility === 'hidden' || ps.display === 'none' || !p.getClientRects().length || p.closest(CTL) || p.closest('svg,option,[data-m="view"],pre,.code') || folded(p)) continue;
      if(p.clientWidth === 0 && p.scrollWidth > 0 && ps.display !== 'inline') out.push({where, kind: 'squeezed', text: t.slice(0, 40), ctl: ''});
      const rg = document.createRange(); rg.selectNodeContents(n);
      const clip = (() => { for(let a = p; a && a !== document.body; a = a.parentElement){ const o = getComputedStyle(a).overflowY; if(o === 'auto' || o === 'scroll' || o === 'hidden') return a.getBoundingClientRect(); } return null; })();
      for(const tr of rg.getClientRects()){
        if(tr.width < 2) continue;
        if(clip && (tr.bottom <= clip.top + 1 || tr.top >= clip.bottom - 1)) continue;      // scrolled out of its box: not seen, overlaps nothing
        for(const [c, cr] of ctl){
          if(c.contains(p)) continue;
          const w = Math.min(tr.right, cr.right) - Math.max(tr.left, cr.left), h = Math.min(tr.bottom, cr.bottom) - Math.max(tr.top, cr.top);
          if(w > 2 && h > 2){ out.push({where, kind: 'overlap', text: t.slice(0, 40), ctl: (c.tagName + ' ' + (c.textContent || c.value || c.placeholder || '').trim()).slice(0, 30)}); break; }
        }
      }
      const s = getComputedStyle(p);
      if(p.scrollWidth > p.clientWidth + 1 && /hidden|clip/.test(s.overflowX) && s.textOverflow !== 'ellipsis' && p.clientWidth > 0)
        out.push({where, kind: 'clipped', text: t.slice(0, 40), ctl: ''});
    }
    const seen = new Set();                                     // one hit per text and control
    return out.filter(h => { const k = h.kind + h.text + h.ctl; if(seen.has(k)) return false; seen.add(k); return true; });
  }
  const hits = [], close = () => { try { closeModal(); } catch(e){}
    if(location.hash.startsWith('#settings')){ history.replaceState(null, '', location.pathname + location.search); dispatchEvent(new HashChangeEvent('hashchange')); } };
  const hqSeen = new Set(), hqCount = () => Home.help.marks().forEach(id => hqSeen.add(id));     // the entries whose control showed
  for(const e of document.querySelectorAll('header button, header #conn, header #brandtitle')){     // the header fits the screen: nothing past its right edge
    const r = e.getBoundingClientRect();
    if(r.width && r.right > innerWidth + 1) hits.push({where: 'header', kind: 'header-cut', text: (e.textContent || e.id || '').trim().slice(0, 30), ctl: String(Math.round(r.right))});
  }
  const steps = [];                                             // only window's names: Home, HomePlugins, openSettings, closeModal
  openSettings(); await sleep(200);
  const pages = [];                                              // a group on the left, its subjects on top of its page
  for(const g of [...document.querySelectorAll('#stx .stx-item[data-pg]')].map(e => e.dataset.pg)){
    openSettings(g); await sleep(60);
    const sub = [...document.querySelectorAll('#stx .stx-stab[data-pg]')].map(e => e.dataset.pg);
    for(const p of sub.length ? sub : [g]) if(!pages.includes(p)) pages.push(p);
  }
  steps.push(['settings', () => openSettings()]);
  for(const p of pages){                                         // every page, and every tab of it
    openSettings(p); await sleep(60);
    const tabs = [...document.querySelectorAll('#stx .stx-own .stx-tab')].map(t => t.dataset.t);
    if(tabs.length < 2) steps.push(['settings ' + p, () => openSettings(p)]);
    else for(const t of tabs) steps.push(['settings ' + p + '/' + t, () => openSettings(p, t)]);
  }
  if(!window.__sweepSafe){                                      // a live house: no Add window (it opens pairing and scans for real)
    steps.push(['add', () => Home.openAdd()]);
    HomePlugins.addMenu.forEach(e => steps.push(['add: ' + e.label, () => e.onClick()]));
  }
  for(const d of Home.devices().slice(0, 80)) steps.push(['device ' + d.id, () => Home.openDevice(d.id)]);
  if(!window.__sweepSafe && document.querySelector('button.tab[data-tab="p:panels"]'))   // the Tablets window opens pairing: not on a live house
    steps.push(['panels: tablets', async () => {
      Home.openTab('p:panels'); await sleep(400);
      const b = document.getElementById('pn-tabs') || document.getElementById('pn-pair'); if(b) b.click();
      for(let i = 0; i < 40 && !document.querySelector('#pn-list .pn-won'); i++) await sleep(100);
      if(!document.querySelector('#pn-list .pn-won')) hits.push({where: 'panels: tablets', kind: 'error', text: 'no watchdog row in the Tablets window', ctl: ''});
    }]);
  const waitFor = async (sel, n = 40) => { for(let i = 0; i < n && !document.querySelector(sel); i++) await sleep(100); return document.querySelector(sel); };
  if(!window.__sweepSafe && document.querySelector('button.tab[data-tab="p:panels"]'))   // the panel cards, under Manage panels
    steps.push(['panels: cards', async () => {
      Home.openTab('p:panels'); await sleep(400);
      const det = document.querySelector('#app details'); if(det) det.open = true;
      if(!await waitFor('#app .pn-edit')) hits.push({where: 'panels: cards', kind: 'error', text: 'no panel card', ctl: ''});
    }]);
  if(!window.__sweepSafe && document.querySelector('button.tab[data-tab="p:ir"]')){       // an IR remote's window, its button form, a tile's window
    steps.push(['ir: remote window', async () => {
      Home.openTab('p:ir'); await sleep(400);
      const t = await waitFor('#app [data-ir]'); if(t) t.click();
      if(!await waitFor('#md [data-act="ren-btn"]')) hits.push({where: 'ir: remote window', kind: 'error', text: 'no remote window', ctl: ''});
    }]);
    steps.push(['ir: button form', async () => {
      Home.openTab('p:ir'); await sleep(400);
      const t = await waitFor('#app [data-ir]'); if(t) t.click();
      const r = await waitFor('#md [data-act="ren-btn"]'); if(r) r.click();
      const f = await waitFor('.askbd [data-f="name"]');
      if(!f){ hits.push({where: 'ir: button form', kind: 'error', text: 'no button form', ctl: ''}); return; }
      await sleep(350); hits.push(...scan('ir: button form', f.closest('.askbd'))); hqCount('ir: button form');
      f.closest('.askbd').querySelector('[data-f="cancel"]').click();
    }]);
    steps.push(['ir: tile window', async () => {
      Home.openTab('p:tech'); await sleep(400);
      const c = await waitFor('#app .card[data-dev="sweep_tile"]'); if(c) c.click();
      if(!await waitFor('#md [data-ira-sel="remote"]')) hits.push({where: 'ir: tile window', kind: 'error', text: 'no tile window', ctl: ''});
    }]);
  }
  steps.push(['rooms: + Room', async () => {                     // only in a house with no room: then scrolled to, it is in view
    Home.openTab('fav'); await sleep(200);
    const chip = document.getElementById('addroom-chip'), rooms = (await Home.api('/api/rooms')).length;
    if(!!chip === !!rooms) hits.push({where: 'rooms: + Room', kind: 'error', text: chip ? 'a + Room chip in a house with rooms' : 'no + Room chip in a house with no room', ctl: ''});
    if(!chip) return;
    chip.scrollIntoView({inline: 'end', block: 'nearest'}); await sleep(150);
    const r = chip.getBoundingClientRect();
    if(r.left < 0 || r.right > window.innerWidth + 1) hits.push({where: 'rooms: + Room', kind: 'error', text: 'out of view after scrolling to it', ctl: ''});
  }]);
  const tabs = [...document.querySelectorAll('button.tab[data-tab]')].map(b => b.dataset.tab);
  for(const t of tabs) steps.push(['tab ' + t, () => document.querySelector(`button.tab[data-tab="${CSS.escape(t)}"]`).click()]);
  // the map: a tap on every icon opens its sheet; a switch's "What it turns on" too
  const mapTab = tabs.find(t => t === 'p:map');
  if(DEBUG) hits.push({where: 'tabs', kind: 'info', text: tabs.join(','), ctl: ''});
  if(mapTab){
    steps.push(['tab map', () => document.querySelector('button.tab[data-tab="p:map"]').click()]);
    steps.push(['map sheets', async () => {
      const view = document.querySelector('[data-m="view"]'); if(!view){ hits.push({where: 'map', kind: 'error', text: 'no map view', ctl: ''}); return; }
      let n = 0, nd = 0;
      for(const ic of [...document.querySelectorAll('[data-m="icons"] [data-id]')].slice(0, 40)){
        ic.scrollIntoView({block: 'center'}); await sleep(30);
        const r = ic.getBoundingClientRect(); if(!r.width) continue;
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        for(const t of ['pointerdown', 'pointerup']) view.dispatchEvent(new PointerEvent(t, {pointerId: 1, clientX: x, clientY: y, bubbles: true}));
        await sleep(120);
        hits.push(...scan('map sheet ' + ic.dataset.id, document.querySelector('[data-m="sheet"]')));
        const dr = document.querySelector('[data-m="sheet"] [data-s="drives"]');
        n++;
        if(dr){ nd++; dr.click(); await sleep(80); hits.push(...scan('map drives ' + ic.dataset.id, document.querySelector('[data-m="sheet"]'))); }
        const cl = document.querySelector('[data-m="sheet"] [data-s="close"]'); if(cl) cl.click();
      }
      if(DEBUG) hits.push({where: 'map', kind: 'info', text: `icons tapped ${n}, switch sheets ${nd}`, ctl: ''});
    }]);
    // the Opening tool: its bar, and the sheet of a stroke along a wall (a new opening is kept only once Window or Door is pressed: nothing is saved)
    steps.push(['map opening tool', async () => {
      const view = document.querySelector('[data-m="view"]'), more = document.querySelector('[data-m="more"]');
      if(!view || !document.querySelector('[data-m="optool"]')) return;
      if(more && document.querySelector('.mapbar.narrow') && !document.querySelector('.mapbar.open')){ more.click(); await sleep(60); }
      document.querySelector('[data-m="optool"]').click(); await sleep(80);
      hits.push(...scan('map opening bar', document.querySelector('[data-m="hint"]')));
      const wl = [...document.querySelectorAll('svg line[data-w]')].map(l => l.getBoundingClientRect()).find(r => r.width + r.height > 30 && r.top > 0 && r.bottom < innerHeight);
      if(wl){
        const vert = wl.height > wl.width, a = vert ? [wl.left + wl.width / 2 + 5, wl.top + wl.height * 0.3] : [wl.left + wl.width * 0.3, wl.top + wl.height / 2 + 5];
        const b = vert ? [a[0], wl.top + wl.height * 0.6] : [wl.left + wl.width * 0.6, a[1]];
        view.dispatchEvent(new PointerEvent('pointerdown', {pointerId: 1, clientX: a[0], clientY: a[1], bubbles: true}));
        for(let k = 1; k <= 4; k++) view.dispatchEvent(new PointerEvent('pointermove', {pointerId: 1, clientX: a[0] + (b[0] - a[0]) * k / 4, clientY: a[1] + (b[1] - a[1]) * k / 4, bubbles: true}));
        view.dispatchEvent(new PointerEvent('pointerup', {pointerId: 1, clientX: b[0], clientY: b[1], bubbles: true})); await sleep(120);
        hits.push(...scan('map opening sheet', document.querySelector('[data-m="sheet"]')));
        const cl = document.querySelector('[data-m="sheet"] [data-s="close"]'); if(cl) cl.click();
      }
      const done = document.querySelector('[data-m="hint"] [data-b="wdone"]'); if(done) done.click();
    }]);
  }
  const probe = document.createElement('div');                       // the check finds a known overlap, else it proves nothing
  probe.innerHTML = '<div style="position:relative;width:200px"><span>Overlapping label text</span><select style="position:absolute;left:0;top:0;width:180px"><option>x</option></select></div>';
  document.body.appendChild(probe);
  const self = scan('self-test', probe).length;
  probe.remove();
  if(!self) hits.push({where: 'self-test', kind: 'error', text: 'the overlap check found nothing in a known overlap', ctl: ''});
  const canary = document.createElement('div');                      // the shared row CSS itself: a label beside a full-width field in a narrow sheet
  canary.style.cssText = 'width:328px';
  canary.innerHTML = '<div class="row"><span class="rlabel">Which key</span><select class="askin"><option>Middle key</option></select></div>'
    + '<div class="row"><span class="rlabel">Name</span><input class="askin" value="lamp 1"></div>';
  document.body.appendChild(canary);
  hits.push(...scan('row CSS (a narrow sheet)', canary));
  canary.remove();
  for(const [where, open] of steps){
    close();
    try { const r = open(); if(r && r.then) await Promise.race([r, sleep(20000)]); } catch(e){ hits.push({where, kind: 'error', text: String(e).slice(0, 80), ctl: ''}); continue; }
    await sleep(350);
    hits.push(...scan(where));
    hqCount(where);
  }
  close();
  // help: the sheet and an entry's popover lie on top of a window
  const dev = Home.devices().find(d => (d.controls || []).length);
  if(dev){
    for(const [name, open] of [['device window', () => Home.openDevice(dev.id)], ['settings', () => openSettings('lang')]]){
      close(); open(); await sleep(400);
      openHelp(); await sleep(100);
      const sh = document.getElementById('hqsheet'), r = sh.getBoundingClientRect(), top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if(!top || !top.closest('#hqsheet')) hits.push({where: 'help ' + name, kind: 'hq-stack', text: 'the sheet is under ' + (top ? top.tagName + '#' + top.id : 'nothing'), ctl: ''});
      sh.remove();
      const id = Home.help.marks().find(i => /^(device|settings)\./.test(i));
      if(id && Home.help.pop(id)){
        await sleep(60);
        const p = document.querySelector('.hq-pop'), pr = p && p.getBoundingClientRect(), pt = p && document.elementFromPoint(pr.left + pr.width / 2, pr.top + pr.height / 2);
        if(!pt || !pt.closest('.hq-pop')) hits.push({where: 'help ' + name, kind: 'hq-stack', text: 'the popover is ' + (p ? 'under ' + (pt ? pt.tagName : 'nothing') : 'missing'), ctl: ''});
        document.body.click();
      }
    }
    close();
  }
  // states the sweep's house cannot reach: the builder's back button, a screen's MAC to confirm, a finished or discovered ESP screen, a satellite with a microphone (Settings -> Voice)
  const UNREACHABLE = new Set(['panels.back', 'panels.mac', 'esp.download', 'esp.usb', 'esp.code', 'esp.name', 'esp.bind', 'voiceset.needwake',
    'device.roommap',     // a map-set room: the house has no map (test_room_map_ui covers it)
    'device.ok', 'device.renchan', 'tabs.roommenu',                    // OK after a change; a channel's name; a room tab
    'climate.fan', 'climate.mode', 'climate.more',                     // one or the other by the width: fan and mode behind More on a phone
    'ir.fwpress', 'ir.fwslider', 'ir.fwselect', 'panels.qr']);           // a blaster with its own firmware keys; a launcher address set
  // the core's entries and the plug-ins whose state the sweep's house has (ui_sweep_house/); the others join when it has theirs
  const plugHelp = new Set(HomePlugins.help.map(e => e.id)), checked = id => !plugHelp.has(id) || /^(panels|tablets|esp|ir|irbutton|irtile|climate|acwin|voiceset)\./.test(id);
  for(const e of Home.help.entries()) if(e.id !== 'header.help' && checked(e.id) && !UNREACHABLE.has(e.id) && !hqSeen.has(e.id)) hits.push({where: 'help', kind: 'hq-never', text: e.id, ctl: e.sel});
  return JSON.stringify(hits);
})()
"""


def _free() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait(url: str, timeout: float = 60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            return urllib.request.urlopen(url, timeout=2).read()
        except urllib.error.HTTPError as e:                     # it answers (401 without the token): it is up
            return e.read()
        except Exception:                                       # noqa: BLE001 - not up yet
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not answer")


async def _cdp(ws_url: str, url: str, safe: bool = False) -> list:
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
        hits = []
        await call("Page.addScriptToEvaluateOnNewDocument", source=("window.__sweepSafe = true;" if safe else "") + "window.__errs = []; addEventListener('error', e => __errs.push(String(e.message) + ' @' + e.lineno)); addEventListener('unhandledrejection', e => __errs.push('rejected: ' + (e.reason && e.reason.message || e.reason)));")
        for w in WIDTHS:
            await call("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=w < 700)
            await call("Page.navigate", url=url)
            await asyncio.sleep(6)
            if os.environ.get("SWEEP_INJECT"):                     # a measurement aid: a style laid over the page (the CSS of before)
                await call("Runtime.evaluate", expression=os.environ["SWEEP_INJECT"])
            r = await call("Runtime.evaluate", expression=CHECK_JS, awaitPromise=True, returnByValue=True, timeout=240000)
            val = (r.get("result") or {}).get("value")
            if not isinstance(val, str):
                p = await call("Runtime.evaluate", expression="location.origin + location.pathname + ' | ' + (window.__errs || []).join(' ; ').slice(0, 400)", returnByValue=True)
                hits.append({"width": w, "where": "page", "kind": "error", "text": _redact(json.dumps(r)[:160] + " PAGE: " + str((p.get("result") or {}).get("value"))), "ctl": ""})
                continue
            hits += [{"width": w, **{k: _redact(v) for k, v in h.items()}} for h in json.loads(val)]
        return hits


def sweep(house: "pathlib.Path | None" = None) -> list:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="yashome-sweep-"))
    app = chrome = None
    try:
        data = tmp / "house"
        shutil.copytree(house or ROOT / "tests" / "fixtures", data, ignore=shutil.ignore_patterns(".git", "esphome", "voice", "*.db"))
        for f in (ROOT / "tests" / "ui_sweep_house").rglob("*") if house is None else ():   # what the fixtures lack (devices, tablets, panels, an IR remote,
            to = data / f.relative_to(ROOT / "tests" / "ui_sweep_house")   # its blasters and tile): added, never over a house's own file
            if f.is_file() and not to.exists():
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, to)
        plugins = sorted(p.name for p in (ROOT / "plugins").glob("*/*") if p.is_dir() and (p / "__init__.py").exists() and p.name not in SKIP_PLUGINS)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp), "HOME_STACK_ROOT": str(ROOT), "HOUSE_ROOT": str(data), "PLUGINS": ",".join(plugins),
               "MQTT_HOST": "192.0.2.1", "DASHBOARD_TOKEN": TOKEN}
        for line in (ROOT / "tests" / "plugins" / "test.env").read_text().splitlines():
            if line.strip() and not line.startswith("#") and not line.startswith("DASHBOARD_TOKEN"):
                k, v = line.split("=", 1)
                env.setdefault(k, v)
        env.update(HOST_SOCKETS_DIR=str(tmp / "no-sockets"))   # a copy of a live house has real tablets: its watchdog must reach none
        port, dbg = _free(), _free()
        app = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env,
                               stdout=open(tmp / "app.log", "w"), stderr=subprocess.STDOUT)
        _wait(f"http://127.0.0.1:{port}/")
        chrome = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={dbg}",
                                   f"--user-data-dir={tmp / 'chrome'}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _wait(f"http://127.0.0.1:{dbg}/json/version")
        page = next(t for t in json.loads(_wait(f"http://127.0.0.1:{dbg}/json/list")) if t.get("type") == "page")
        return asyncio.run(_cdp(page["webSocketDebuggerUrl"], f"http://127.0.0.1:{port}/?token={TOKEN}"))
    finally:
        for p in (chrome, app):
            if p is not None:
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def sweep_live(url: str) -> list:
    """The same sweep on a running house (SWEEP_URL with ?token=), read-only: windows, settings, tabs and map sheets; no Add window."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="yashome-sweep-"))
    chrome = None
    try:
        dbg = _free()
        chrome = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={dbg}",
                                   f"--user-data-dir={tmp / 'chrome'}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _wait(f"http://127.0.0.1:{dbg}/json/version")
        page = next(t for t in json.loads(_wait(f"http://127.0.0.1:{dbg}/json/list")) if t.get("type") == "page")
        return asyncio.run(_cdp(page["webSocketDebuggerUrl"], url, safe=True))
    finally:
        if chrome is not None:
            chrome.terminate()
            chrome.wait(10)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    found = sweep_live(os.environ["SWEEP_URL"]) if os.environ.get("SWEEP_URL") else sweep(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    for h in found:
        print(f"{h['width']:>5} {h['kind']:<8} {h['where'][:40]:<40} {h['text']!r} <- {h['ctl']!r}")
    found = [h for h in found if h["kind"] != "stat"]                 # numbers to read, not faults
    print(f"hits: {len(found)}")
    sys.exit(1 if found else 0)
