// Panels tab: the wall panels by room (open, edit, room), and pairing of an Android tablet.
(() => {
  // window.PW (the panels' widgets) also draws the IR remote layout and the AC icons on dashboard cards
  if(!window.PW){
    window.T = window.T || i18n;
    const pw = Object.assign(document.createElement('script'), {src: '/plugins/panels/static/widgets.js'});
    pw.onload = () => Home.refresh();
    document.head.appendChild(pw);
  }
  const esc = Home.escapeHtml;
  const J = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  let panels = [], rooms = [], hwLabels = {}, hwFirmware = new Set(), hwChip = {}, tabBox = null, published = null;
  const load = async () => {
    try { panels = await Home.api('/api/panels') || []; } catch(e){ panels = []; }
    try { published = await Home.api('/plugins/panels/app'); } catch(e){ published = null; }   // the launcher the tablets update to
    try { rooms = await Home.api('/api/rooms') || []; } catch(e){ rooms = []; }
    try {
      const hw = await Home.api('/api/panels/hw') || [];
      hwLabels = Object.fromEntries(hw.map(h => [h.hw, h.label]));
      hwFirmware = new Set(hw.filter(h => h.firmware).map(h => h.hw));
      hwChip = Object.fromEntries(hw.map(h => [h.hw, h.chip]));
    } catch(e){ hwLabels = {}; hwFirmware = new Set(); }
  };
  const ago = t => !t ? '' : (Date.now() / 1000 - t < 120 ? i18n('online') : i18n('seen {0}', Home.ago(Date.now() / 1000 - t)));

  // the kind of a panel's screen, always said: an Android tablet (the launcher) or an ESP screen (its own firmware)
  const TABLET_SVG = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="5" y="2.5" width="14" height="19" rx="2.5"/><path d="M11 18.5h2" stroke-linecap="round"/></svg>';
  const CHIP_SVG = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3"/></svg>';
  const isAndroid = p => p.hw === 'android';
  const kindBadge = p => `<span class="pnl-kind ${isAndroid(p) ? 'k-android' : 'k-esp'}">${isAndroid(p) ? TABLET_SVG : CHIP_SVG}${esc(i18n(isAndroid(p) ? 'Android tablet' : 'ESP screen'))}</span>`;
  function card(p){
    const hw = isAndroid(p) ? '' : (hwLabels[p.hw] || p.hw);
    const opts = [`<option value="">${esc(i18n('no room'))}</option>`].concat(rooms.map(r =>
      `<option value="${esc(r.id)}"${r.id === p.room ? ' selected' : ''}>${esc(r.name || r.id)}</option>`)).join('');
    return `<div class="row" style="flex-direction:column;align-items:stretch;gap:6px">
      <div>${kindBadge(p)} <b>${esc(p.title)}</b> <span class="bindcaption">${esc(p.name)}${hw ? ' \u00b7 ' + esc(hw) : ''} \u00b7 ${p.w}\u00d7${p.h}
        \u00b7 ${esc(i18n('{0} widgets', p.widgets))}${p.hw === 'android' ? ' \u00b7 ' + esc(ago(p.seen) || i18n('never seen')) : ''}</span></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <button class="dlg-btn pn-edit" data-name="${esc(p.name)}">${esc(i18n('Edit'))}</button>
        <button class="dlg-btn pn-float" data-name="${esc(p.name)}">${esc(i18n('Open beside'))}</button>
        ${hwFirmware.has(p.hw) ? `<button class="dlg-btn pn-flash" data-name="${esc(p.name)}">${esc(i18n('Flash'))}</button>` : ''}
        <select class="pn-room" data-name="${esc(p.name)}" title="${esc(i18n('The room the panel is in.'))}">${opts}</select>
        ${p.hw === 'android' ? `<button class="dlg-btn pn-repair">${esc(i18n('Pair again'))}</button>` : ''}
        <button class="dlg-btn pn-del" data-name="${esc(p.name)}" style="color:var(--bad)">${esc(i18n('Delete'))}</button></div>
      ${hwFirmware.has(p.hw) ? screenLine(p) : ''}
      <div class="bindcaption pn-build" data-name="${esc(p.name)}"></div></div>`;
  }

  // A panel is flashed only at its MAC; one without a stored MAC is confirmed here by a human.
  const mac = m => String(m || '').replace(/(..)(?!$)/g, '$1:');
  function screenLine(p){
    if(p.mac) return `<div class="bindcaption">${esc(i18n('Screen {0}', mac(p.mac)))}</div>`;
    if(!p.candidate) return `<div class="bindcaption">${esc(i18n('Screen not confirmed and not on the network'))}</div>`;
    return `<div class="bindcaption">${esc(i18n('A screen at {1} says it is this panel; its MAC is {0}. Check the MAC (router or the board label) before you confirm.', mac(p.candidate.mac), p.candidate.ip))}
      <button class="dlg-btn pn-mac" data-name="${esc(p.name)}" data-mac="${esc(p.candidate.mac)}">${esc(i18n('Yes, this is it'))}</button></div>`;
  }

  // The panels as they look (each drawn live by /panel-view); a click opens the builder in this tab
  const STYLE = `<style>
    .pnl-grid{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start;margin:6px 0 14px}
    .pnl-card{display:flex;flex-direction:column;align-items:center;justify-content:space-between;gap:8px;
      min-width:200px;min-height:232px;padding:14px;border-radius:16px;background:var(--card);
      border:1px solid var(--line);cursor:pointer;transition:border-color .15s,transform .15s}
    .pnl-card:hover{border-color:var(--accent,var(--on));transform:translateY(-2px)}
    .pnl-screen{flex:none;margin:auto 0;border:5px solid black;border-radius:10px;overflow:hidden;background:black;
      box-shadow:0 0 0 1px var(--line),0 6px 16px rgba(0,0,0,.45)}
    .pnl-screen iframe{width:100%;height:100%;border:0;pointer-events:none;display:block}
    .pnl-foot{font-size:14px;font-weight:600}
    .pnl-where{color:var(--dim);font-weight:400}
    .pnl-kind{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;padding:2px 8px;border-radius:10px;border:1px solid var(--line);vertical-align:middle}
    .pnl-kind.k-android{color:#7cc4ff;border-color:#2a4a66} .pnl-kind.k-esp{color:#ffb86b;border-color:#5c4128}
  </style>`;
  let editing = null;          // the panel open in the builder ('' = the builder itself, to pick or create one); null = the cards
  function tile(p){
    const sc = Math.min(300 / p.w, 170 / p.h);
    const room = (rooms.find(r => r.id === p.room) || {}).name;
    return `<div class="pnl-card" data-name="${esc(p.name)}" title="${esc(i18n('Open in the builder'))}">
      <div class="pnl-screen" style="width:${Math.round(p.w * sc)}px;height:${Math.round(p.h * sc)}px">
        <iframe src="/panel-view?panel=${encodeURIComponent(p.name)}&poll=10000&camslow=1" loading="lazy" tabindex="-1"></iframe></div>
      ${kindBadge(p)}<div class="pnl-foot">${esc(p.title)}${room ? ` <span class="pnl-where">\u00b7 ${esc(room)}</span>` : ''}</div></div>`;
  }

  // the builder takes the whole window over the settings (its palette, canvas and properties need the width); "All panels"
  // closes it and shows the list again, with what it saved
  function openBuilder(box){
    document.getElementById('pnl-builder')?.remove();
    const ov = document.createElement('iframe');                   // its own header has "All panels" (?embed=1)
    ov.id = 'pnl-builder';
    ov.src = '/panel-builder?embed=1' + (editing ? '&panel=' + encodeURIComponent(editing) : '');
    ov.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;border:0;z-index:calc(var(--z-section) + 10);background:#0a0c10';   // over the settings, under a dialog
    document.body.appendChild(ov);
    const close = () => { removeEventListener('hashchange', close); removeEventListener('message', msg); ov.remove(); editing = null; };
    const msg = e => { if(e.origin === location.origin && e.source === ov.contentWindow && e.data && e.data.type === 'panel-builder-close'){ close(); render(box); } };
    addEventListener('message', msg);
    addEventListener('hashchange', close);                         // the settings left (another page, Back): the builder goes too
  }

  async function render(box){
    tabBox = box;
    if(editing !== null){ openBuilder(box); return; }
    await load();
    const known = new Set(rooms.map(r => r.id));
    const byRoom = list => rooms.map(r => [r.name || r.id, list.filter(p => p.room === r.id)])
      .concat([[i18n('No room'), list.filter(p => !p.room || !known.has(p.room))]]).filter(g => g[1].length);
    const kinds = [[i18n('Tablets (Android)'), panels.filter(isAndroid)], [i18n('ESP screens'), panels.filter(p => !isAndroid(p))]].filter(k => k[1].length);
    box.innerHTML = `${STYLE}<div style="padding:0 12px"><div style="display:flex;flex-wrap:wrap;gap:8px;margin:8px 0">
        <button class="dlg-btn primary" id="pn-builder">${esc(i18n('Panel builder'))}</button>
        <button class="dlg-btn" id="pn-pair">${esc(i18n('Connect an Android tablet'))}</button>
        ${HomePlugins.addFlow.length ? `<button class="dlg-btn" id="pn-tabs">${esc(i18n('Android tablets'))}</button>` : ''}
        <button class="dlg-btn" id="pn-esp">${esc(i18n('Connect an ESP screen'))}</button></div>
      ${panels.length ? `<div class="pnl-grid">${panels.map(tile).join('')}</div>
        <details><summary style="cursor:pointer;margin:6px 0">${esc(i18n('Manage panels'))}</summary>
      <div class="row" style="gap:8px;margin:0 0 8px"><span class="rlabel">${esc(i18n('Panel launcher'))} (${esc(i18n('Android tablets'))}):
        <span id="pn-app">\u2014</span></span><button class="dlg-btn" id="pn-app-qr" hidden>QR</button>
        <button class="dlg-btn" id="pn-app-build">${esc(i18n('Build the app'))}</button></div>
      <div id="pn-app-qrbox" hidden style="margin:0 0 8px"><img id="pn-app-qrimg" alt="" style="width:200px;height:200px;image-rendering:pixelated">
        <div class="bindcaption" id="pn-app-qrurl"></div></div>
      <div class="bindcaption" id="pn-app-log"></div>
        ${kinds.map(([kind, list]) => `<h3>${esc(kind)}</h3>${byRoom(list).map(([room, l]) => `<h4 style="margin:10px 0 4px;color:var(--dim)">${esc(room)}</h4>${l.map(card).join('')}`).join('')}`).join('')}</details>`
        : `<div class="skeleton">${esc(i18n('No panels yet. A panel comes with its screen: connect a tablet running the panel launcher, or an ESP screen (see the panels plug-in README).'))}</div>`}</div>`;
    box.querySelectorAll('.pnl-card').forEach(c => c.onclick = () => { editing = c.dataset.name; render(box); });
    box.querySelector('#pn-builder').onclick = () => { editing = ''; render(box); };
    box.querySelectorAll('.pn-edit').forEach(b => b.onclick = () => { editing = b.dataset.name; render(box); });
    box.querySelectorAll('.pn-float').forEach(b => b.onclick = () => floatOpen(b.dataset.name));
    box.querySelector('#pn-pair').onclick = () => HomePlugins.addFlow.length ? Home.openAdd() : pair();   // the one Add / bind window when connect is on
    const tabs = box.querySelector('#pn-tabs'); if(tabs) tabs.onclick = pair;      // the tablets of the house: Unbind
    box.querySelector('#pn-app-build').onclick = buildApp;
    box.querySelector('#pn-app-qr').onclick = appQr;
    appState();
    box.querySelector('#pn-esp').onclick = espPair;
    box.querySelectorAll('.pn-flash').forEach(b => b.onclick = () => flash(box, b.dataset.name));
    box.querySelectorAll('.pn-repair').forEach(b => b.onclick = pair);     // the tablet asks again with its own id
    box.querySelectorAll('.pn-del').forEach(b => b.onclick = async () => {
      if(!confirm(i18n('Delete panel {0}? Its layout, its tablets and their keys, and its messages on the broker go.', b.dataset.name))) return;
      try { await Home.api('/api/panels/' + encodeURIComponent(b.dataset.name), {method: 'DELETE'}); render(box); }
      catch(e){ alert(e.message || e); }
    });
    box.querySelectorAll('.pn-build').forEach(o => buildState(o, o.dataset.name));
    box.querySelectorAll('.pn-mac').forEach(b => b.onclick = async () => {
      try { await Home.api(`/api/panels/${encodeURIComponent(b.dataset.name)}/mac`, {...J({mac: b.dataset.mac}), method: 'PUT'}); render(box); }
      catch(e){ b.title = e.message || e; }
    });
    box.querySelectorAll('.pn-room').forEach(s => s.onchange = async () => {
      try {
        await Home.api(`/api/panels/${encodeURIComponent(s.dataset.name)}/room`,
          {...J({room: s.value || null}), method: 'PUT'});
        render(box);
      } catch(e){ s.title = e.message || e; }
    });
  }

  const PHASE = {compiling: 'compiling', ready: 'compiled', safe_mode: 'restarting the panel in safe mode',
                 uploading: 'flashing', done: 'done', error: 'failed'};
  async function buildState(out, name){
    let st;
    try { st = await Home.api(`/api/panels/${encodeURIComponent(name)}/build`); } catch(e){ return; }
    if(!st || !st.job){ out.textContent = ''; return; }
    const phase = st.phase === 'error' || st.state === 'error' ? 'error' : st.state === 'done' ? 'done'
      : (st.phase === 'safe_mode' ? 'safe_mode' : st.state);
    out.textContent = i18n('Firmware: {0}', i18n(PHASE[phase] || phase || '')) + (st.progress != null && phase !== 'done' ? ` ${st.progress}%` : '')
      + (st.error ? ' \u00b7 ' + st.error : '');
    if(!['done', 'error'].includes(phase) && out.isConnected) setTimeout(() => buildState(out, name), 3000);
  }
  async function flash(box, name){
    const out = box.querySelector(`.pn-build[data-name="${CSS.escape(name)}"]`);
    try { await Home.api(`/api/panels/${encodeURIComponent(name)}/build`, J({ota: true})); buildState(out, name); }
    catch(e){ out.textContent = e.message || e; }
  }

  // A new ESP screen: its first firmware over USB, then it shows a code here to bind it.
  async function espPair(){
    let hws = [];
    try { hws = (await Home.api('/api/panels/hw') || []).filter(h => h.hw !== 'android' && h.firmware); } catch(e){ /* none */ }
    Home.openModal(`<div class="head"><button class="closebtn" onclick="Home.closeModal()">\u00d7</button>
        <h2>${esc(i18n('Connect an ESP screen'))}</h2>
        <div class="sub">${esc(i18n('1. Build the first firmware for its board, download it and flash it over USB with ESPHome Web (web.esphome.io) or esptool. 2. The screen joins the Wi-Fi and shows a code: type it below.'))}</div></div>
      <div class="body"><section>
        <div class="row" style="gap:8px"><select id="pe-hw">${hws.map(h => `<option value="${esc(h.hw)}">${esc(h.label)}</option>`).join('')}</select>
          <button class="dlg-btn" id="pe-build">${esc(i18n('Build the first firmware'))}</button></div>
        <div id="pe-out" style="font-size:13px;color:var(--dim);margin:6px 0"></div><div id="pe-list"></div></section></div>`, '__panels_esp__');
    const out = document.getElementById('pe-out'), list = document.getElementById('pe-list');
    document.getElementById('pe-build').onclick = async () => {
      try {
        const hw = document.getElementById('pe-hw').value;
        const r = await Home.api('/api/panels/starter/' + encodeURIComponent(hw), J({}));
        const poll = async () => {
          let st; try { st = await Home.api('/api/build/jobs/' + r.job); } catch(e){ out.textContent = e.message || e; return; }
          if(st.state === 'done'){
            out.innerHTML = `<div class="row" style="gap:8px;flex-wrap:wrap">
                <button class="dlg-btn primary pe-dl">${esc(i18n('Download the firmware'))}</button>
                <button class="dlg-btn pe-usb">${esc(i18n('Flash over USB (beta)'))}</button></div>
              <div class="pe-usb-out" style="font-size:13px;color:var(--dim)">${navigator.serial ? esc(i18n('Plug the screen in by USB; the browser asks which port: pick the screen, never a Zigbee stick.'))
                : esc(i18n('USB from the browser needs Chrome or Edge and the dashboard over HTTPS (README); else download it and use ESPHome Web.'))}</div>`;
            const image = async () => {                       // a link carries no token: fetch it with the token
              const f = await fetch(r.firmware, {headers: Home.authHeaders({})});
              if(!f.ok) throw new Error(String(f.status));
              return f.blob();
            };
            out.querySelector('.pe-dl').onclick = async () => {
              try {
                const a = document.createElement('a');
                a.href = URL.createObjectURL(await image()); a.download = `panel-${hw}.factory.bin`;
                a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
              } catch(e){ out.append(' ' + (e.message || e)); }
            };
            const usb = out.querySelector('.pe-usb'), say = t => { out.querySelector('.pe-usb-out').textContent = t; };
            usb.disabled = !navigator.serial;
            usb.onclick = async () => {
              if(usb.disabled) return;
              usb.disabled = true;
              let transport = null;
              try {
                const port = await navigator.serial.requestPort();      // first: the click's gesture lasts seconds only
                say(i18n('Connecting\u2026'));
                const data = new Uint8Array(await (await image()).arrayBuffer());
                const {ESPLoader, Transport} = await import('/plugins/panels/static/esptool.mjs');
                transport = new Transport(port, false);
                const quiet = {clean(){}, writeLine(){}, write(){}};
                const loader = new ESPLoader({transport, baudrate: 460800, terminal: quiet});
                const chip = await loader.main();
                const want = hwChip[hw];
                if(!want) throw new Error(i18n('the chip of this board is not known here: nothing was written'));
                if((loader.chip.CHIP_NAME || '').toUpperCase() !== want.toUpperCase())
                  throw new Error(i18n('this port is a {0}, the firmware is for a {1}: nothing was written', loader.chip.CHIP_NAME, want));
                say(i18n('Erasing and writing\u2026'));
                await loader.writeFlash({fileArray: [{data, address: 0}], flashMode: 'keep', flashFreq: 'keep',
                  flashSize: 'keep', eraseAll: true, compress: true,
                  reportProgress: (i, done, total) => say(i18n('{0}: flashing {1}%', chip, Math.round(done * 100 / total)))});
                await loader.after('hard_reset');
                say(i18n('Done: the screen restarts, joins the Wi-Fi and shows its code below. If it stays dark, unplug it and plug it in again.'));
              } catch(e){
                say(e && e.name === 'NotFoundError' ? i18n('No port chosen.')
                  : i18n('USB flash failed: {0}. Unplug the screen and plug it in again (it may wait in its loader); use the download with ESPHome Web, or hold BOOT while plugging in and try again.', e.message || e));
              }
              finally {
                usb.disabled = false;
                if(transport) try {
                  await transport.device.setSignals({dataTerminalReady: false, requestToSend: false});   // idle lines before the close
                  await transport.disconnect();
                } catch(e){ /* already closed */ }
              }
            };
          }
          else if(st.state === 'error') out.textContent = i18n('Build failed: {0}', st.error || '');
          else { out.textContent = i18n('Building\u2026 {0}%', st.progress || 0); if(Home.modalId() === '__panels_esp__') setTimeout(poll, 3000); }
        };
        poll();
      } catch(e){ out.textContent = e.message || e; }
    };
    const tick = async () => {
      if(Home.modalId() !== '__panels_esp__') return;
      let s; try { s = await Home.api('/api/panels/discovered'); } catch(e){ s = {}; }
      const esp = s.esp || [];
      const sig = JSON.stringify(esp.map(e => e.mac));
      if(list.dataset.sig !== sig){
        list.dataset.sig = sig;
        list.innerHTML = esp.length ? esp.map(e => `<div class="row" style="gap:8px;flex-wrap:wrap">
            <b>${esc(hwLabels[e.hw] || e.hw)}</b> <span>${esc(mac(e.mac))}</span>
            <span class="bindcaption">${esc([e.node, e.ip].filter(Boolean).join(' \u00b7 '))}</span>
            <input class="pe-code" data-mac="${esc(e.mac)}" placeholder="${esc(i18n('code from the screen'))}" inputmode="numeric" maxlength="8" style="width:170px">
            <input class="pe-name" data-mac="${esc(e.mac)}" value="panel-${esc(e.mac.slice(-4).toLowerCase())}" style="width:150px">
            <button class="dlg-btn primary pe-bind" data-mac="${esc(e.mac)}">${esc(i18n('Bind'))}</button>
            <span class="bindcaption pe-res" data-mac="${esc(e.mac)}"></span></div>`).join('')
          : `<div class="bindcaption">${esc(i18n('No new screen yet\u2026'))}</div>`;
        const q = (cls, mac) => list.querySelector(`.${cls}[data-mac="${CSS.escape(mac)}"]`);
        list.querySelectorAll('.pe-bind').forEach(b => b.onclick = async () => {
          const mac = b.dataset.mac;
          try {
            const r = await Home.api('/api/panels/bind_esp', J({mac, code: q('pe-code', mac).value.trim(), panel: q('pe-name', mac).value.trim()}));
            q('pe-res', mac).textContent = i18n('Panel {0}: its firmware is on the way', r.panel);
            load().then(() => Home.refresh());
          } catch(e){ q('pe-res', mac).textContent = e.message || e; }
        });
      }
      setTimeout(tick, 3000);
    };
    tick();
  }

  // as the server's _needs_update: by build number when the tablet sends one, else by a differing name
  const older = c => !!published && (Number.isInteger(c.app_code) ? c.app_code < published.code : !!c.app && c.app !== published.name);
  const capsLine = d => {
    const c = d.caps || {}, sc = c.screen || {};
    return [c.app ? `${i18n('launcher')} ${c.app}${older(c) ? ` \u2192 ${published.name}` : ''}` : '', c.update_error ? `${i18n('update failed')}: ${c.update_error}` : '', sc.w && sc.h ? `${sc.w}\u00d7${sc.h}` : '', (c.cameras || []).length ? i18n('camera') : '',
            c.mic ? i18n('microphone') : '', c.battery != null ? `${c.battery} %` : '',
            c.adb ? (c.adb.enabled && c.adb.port ? `adb :${c.adb.port}` : c.adb.enabled ? 'adb' : 'adb off') : '',
            c.front === false ? i18n('not in front') : '',
            c.dnd ? (c.dnd === 'alarms' ? i18n('do not disturb') : c.dnd === 'no-access' ? i18n('do not disturb: no access') : c.dnd) : ''].filter(Boolean).join(' \u00b7 ');
  };
  // the tablet's own pin (from its heartbeat) is what the toggle shows; the house's wish waits for it; no device owner: nothing to pin
  const pinRow = (d, id) => {
    const c = d.caps || {}, actual = typeof c.home_pinned === 'boolean' ? c.home_pinned : d.home_pin !== false, wish = d.home_pin !== false;
    if(c.owner === false) return `<span class="bindcaption">${esc(i18n('Home screen pin: needs Device Owner mode'))}</span>`;
    return `<span style="display:inline-flex;align-items:center;gap:6px" title="${esc(i18n('Off: another home screen may be chosen on the tablet'))}">${esc(i18n('Home screen pinned'))}
      <div class="toggle setting pn-pin${actual ? ' on' : ''}" data-id="${id}" data-wish="${wish ? 1 : 0}"></div>${actual !== wish ? `<span class="bindcaption">${esc(i18n('waits for the tablet'))}</span>` : ''}</span>`;
  };
  // the watchdog of a bound tablet: on, after how many silent minutes, at most how often; its last try or "adb unavailable"
  const watchRow = (d, id) => {
    const w = d.watch || {}, l = d.watch_last || {}, at = l.at ? new Date(l.at * 1000).toTimeString().slice(0, 5) : '';
    const num = (cls, v, max) => `<input type="number" class="${cls}" data-id="${id}" min="1" max="${max}" value="${esc(v)}" style="width:60px">`;
    return `<span style="display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap">${esc(i18n('Raise the launcher if it fell'))}
      <div class="toggle setting pn-won${w.on ? ' on' : ''}" data-id="${id}" role="switch" tabindex="0" aria-checked="${!!w.on}"></div>
      <label>${esc(i18n('after'))} ${num('pn-wafter', w.after_min, 60)} ${esc(i18n('min'))}</label>
      <label>${esc(i18n('at most once in'))} ${num('pn-wevery', w.every_min, 240)} ${esc(i18n('min'))}</label>
      ${l.noadb ? `<span class="bindcaption">${esc(i18n('adb unavailable'))}</span>` : ''}
      ${at ? `<span class="bindcaption">${esc(i18n('last raise: {0}', at))}${l.ok ? '' : ' \u00b7 ' + esc(i18n('failed: {0}', l.why || ''))}</span>` : ''}</span>`;
  };
  const suggest = d => String(d.name || d.model || 'panel_1').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32) || 'panel_1';

  // The launcher APK: built by the build server's app builder with the house's key; polled while busy
  async function appState(){
    const out = document.getElementById('pn-app');
    if(!out) return;
    let s;
    if(Home.can && !Home.can('GET /api/panels/launcher')){ out.textContent = i18n('Not available here'); return; }
    try { s = await Home.api('/api/panels/launcher'); } catch(e){ out.textContent = e.message || e; return; }
    out.innerHTML = s.apk ? [`<a href="/plugins/panels/panel.apk">panel.apk</a>`, s.apk_name ? esc(`${s.apk_name} (${s.apk_code})`) : '',
      esc(Home.when ? Home.when(s.apk_time * 1000) : new Date(s.apk_time * 1000).toLocaleString())].filter(Boolean).join(' \u00b7 ')
      : esc(i18n('not built yet'));
    document.getElementById('pn-app-qr').hidden = !s.apk;
    if(!s.busy) published = s.apk_name != null ? {code: s.apk_code, name: s.apk_name} : null;   // a new build: the tablet list compares with it
    if(s.busy) out.innerHTML += ` \u00b7 ${esc(i18n('building'))}${s.progress != null ? ' ' + s.progress + '%' : ''}`;
    if(s.state === 'error') out.innerHTML += ` \u00b7 <span style="color:var(--bad)">${esc(s.error || i18n('failed'))}</span>`;
    const btn = document.getElementById('pn-app-build');
    if(btn) btn.disabled = !!s.busy;
    const log = document.getElementById('pn-app-log');
    if(log) log.textContent = (s.busy || s.state === 'error') ? (s.log || []).slice(-3).join('\n') : '';
    if(s.busy) setTimeout(appState, 3000);
  }
  // A QR of the APK's address on this dashboard: a tablet's camera opens the download (qrcode-generator, MIT)
  let qrLib = null;
  async function appQr(){
    const qrBox = document.getElementById('pn-app-qrbox'), img = document.getElementById('pn-app-qrimg');
    if(!qrBox.hidden){ qrBox.hidden = true; return; }
    qrLib = qrLib || (window.qrcode ? Promise.resolve(window.qrcode) : null) || new Promise((ok, fail) => document.head.appendChild(Object.assign(document.createElement('script'),
      {src: '/plugins/panels/static/qrcode.js', onload: () => ok(window.qrcode), onerror: () => { qrLib = null; fail(new Error('qrcode.js')); }})));
    try {
      const q = (await qrLib)(0, 'L');
      const url = location.origin + '/plugins/panels/panel.apk';   // shown too: a tunnel's address is no use to a tablet
      q.addData(url); q.make();
      img.src = q.createDataURL(5, 8); img.alt = url; document.getElementById('pn-app-qrurl').textContent = url; qrBox.hidden = false;
    } catch(e){ document.getElementById('pn-app-log').textContent = e.message || e; }
  }
  async function buildApp(){
    document.getElementById('pn-app-build').disabled = true;
    try { await Home.api('/api/panels/launcher', {method: 'POST'}); } catch(e){ alert(e.message || e); }
    appState();
  }

  // The server invites tablets while this window is open; each one shows a code on its screen.
  async function pair(){
    Home.openModal(`<div class="head"><button class="closebtn" onclick="Home.closeModal()">\u00d7</button>
        <h2>${esc(i18n('Connect an Android tablet'))}</h2>
        <div class="sub">${esc(i18n('Start the panel launcher on the tablet (same network). It shows a code: type it here.'))}
          ${esc(i18n('Pairing stays open while this window is open.'))}</div></div>
      <div class="body"><section><div class="bindcaption" id="pn-banner"></div><div id="pn-list"></div></section></div>`,
      '__panels_pair__', {onClose: stop});
    let tick = null;
    function stop(){
      clearInterval(tick);
      Home.api('/api/panels/pairing', J({off: true})).catch(() => {});
      load().then(() => Home.refresh());                // the tab and its badge
    }
    try { await Home.api('/api/panels/pairing', J({minutes: 5})); }
    catch(e){ document.getElementById('pn-banner').textContent = i18n('Could not start pairing: {0}', e.message || e); return; }
    const list = document.getElementById('pn-list');
    async function refresh(){
      if(Home.modalId() !== '__panels_pair__'){ clearInterval(tick); return; }
      let s;
      try { s = await Home.api('/api/panels/discovered'); } catch(e){ return; }
      document.getElementById('pn-banner').textContent = s.pairing > 0
        ? i18n('Inviting tablets for {0} s more', s.pairing) : i18n('Pairing is closed: close this and open it again.');
      const byId = {};
      for(const d of s.devices || []) byId[d.id] = {...d};
      for(const d of s.registered || []) byId[d.id] = {...(byId[d.id] || {}), ...d};
      const devs = Object.values(byId);
      const sig = JSON.stringify(devs.map(d => [d.id, d.name, d.status, d.panel, d.ip, d.home_pin, (d.caps || {}).home_pinned, (d.caps || {}).owner,
        (d.caps || {}).app, (d.caps || {}).update_error, published && published.code, d.watch, d.watch_last]));   // the pin row and the version follow the tablet
      if(list.dataset.sig === sig) return;                // a redraw would lose the typed code
      list.dataset.sig = sig;
      list.innerHTML = devs.length ? devs.map(d => {
        const bound = d.status === 'bound', id = esc(d.id);
        const ctl = bound
          ? `<span>${esc(i18n('bound to panel {0}', d.panel || ''))}</span>
             <button class="dlg-btn pn-unbind" data-id="${id}">${esc(i18n('Unbind'))}</button>
             ${pinRow(d, id)}
             ${watchRow(d, id)}`
          : d.status === 'pending'
            ? `<input class="pn-code" data-id="${id}" placeholder="${esc(i18n('code from the screen'))}" inputmode="numeric" maxlength="16" style="width:170px">
               <input class="pn-name" data-id="${id}" value="${esc(d.repairs || suggest(d))}" style="width:150px">
               ${d.repairs ? `<span class="bindcaption">${esc(i18n("pairing again: check the new code"))}</span>` : ""}
               <button class="dlg-btn primary pn-bind" data-id="${id}">${esc(i18n('Bind'))}</button>`
            : `<span class="bindcaption">${esc(i18n('heard, waiting for it to introduce itself'))}</span>`;
        return `<div class="row" style="flex-direction:column;align-items:stretch;gap:6px">
          <div><b>${esc(d.name || d.model || d.id)}</b>${d.status === 'bound' ? ` <button class="renbtn pn-ren" data-id="${id}" title="${esc(i18n('Rename the tablet'))}">${Home.icon('pen', 16)}</button>` : ''} <span class="bindcaption">${esc([d.model, d.ip].filter(Boolean).join(' \u00b7 '))}</span></div>
          <div class="bindcaption">${esc(capsLine(d) || '\u2014')}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">${ctl}</div>
          <div class="bindcaption pn-out" data-id="${id}"></div></div>`;
      }).join('') : `<div class="bindcaption">${esc(i18n('Nobody has answered yet\u2026'))}</div>`;
      const q = (cls, id) => list.querySelector(`.${cls}[data-id="${CSS.escape(id)}"]`);
      list.querySelectorAll('.pn-bind').forEach(b => b.onclick = async () => {
        const id = b.dataset.id;
        try {
          const r = await Home.api('/api/panels/bind', J({id, code: q('pn-code', id).value.trim(), panel: q('pn-name', id).value.trim()}));
          list.dataset.sig = '';
          await refresh();
          q('pn-out', id).textContent = i18n('Panel {0}: canvas {1}\u00d7{2} (landscape; turn it in the builder)', r.panel, r.w, r.h);
        } catch(e){ q('pn-out', id).textContent = e.message || e; }
      });
      list.querySelectorAll('.pn-ren').forEach(b => b.onclick = async () => {
        const d = devs.find(x => x.id === b.dataset.id) || {};
        const name = ((await Home.askText(i18n('New tablet name:'), d.name || '')) || '').trim();
        if(!name || name === d.name) return;
        try { await Home.api('/api/panels/bind/' + encodeURIComponent(b.dataset.id), {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name})});
              list.dataset.sig = ''; await refresh(); }
        catch(e){ q('pn-out', b.dataset.id).textContent = e.message || e; }
      });
      list.querySelectorAll('.pn-pin').forEach(t => t.onclick = async () => {
        const want = !t.classList.contains('on');
        try { await Home.api('/api/panels/bind/' + encodeURIComponent(t.dataset.id), {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({home_pin: want})});
              t.classList.toggle('on', want); q('pn-out', t.dataset.id).textContent = i18n('The tablet takes it at its next heartbeat (within a minute).'); }
        catch(e){ q('pn-out', t.dataset.id).textContent = e.message || e; }
      });
      const watch = async (id, body) => {
        try { await Home.api('/api/panels/bind/' + encodeURIComponent(id) + '/watch', {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
              list.dataset.sig = ''; refresh(); }
        catch(e){ q('pn-out', id).textContent = e.message || e; }
      };
      list.querySelectorAll('.pn-won').forEach(t => {
        t.onclick = () => watch(t.dataset.id, {on: !t.classList.contains('on')});
        t.onkeydown = ev => { if(ev.key === 'Enter' || ev.key === ' '){ ev.preventDefault(); t.onclick(); } };
      });
      list.querySelectorAll('.pn-wafter').forEach(n => n.onchange = () => watch(n.dataset.id, {after_min: +n.value}));
      list.querySelectorAll('.pn-wevery').forEach(n => n.onchange = () => watch(n.dataset.id, {every_min: +n.value}));
      list.querySelectorAll('.pn-unbind').forEach(b => b.onclick = async () => {
        if(!confirm(i18n('Unbind this tablet? Its key stops working; the panel layout stays.'))) return;
        try { await Home.api('/api/panels/bind/' + encodeURIComponent(b.dataset.id), {method: 'DELETE'}); list.dataset.sig = ''; refresh(); }
        catch(e){ q('pn-out', b.dataset.id).textContent = e.message || e; }
      });
    }
    tick = setInterval(refresh, 2000);
    refresh();
  }

  // ---- a panel in a floating window over the dashboard: its live view (presses act for real), dragged by its head,
  // resized by its corner, minimized to a chip, closed; a phone gets a bottom sheet. Place, size and panel: this browser.
  const PF_KEY = 'panels.float', PF_MIN_W = 220;
  const pfRead = () => { try { return JSON.parse(localStorage.getItem(PF_KEY) || '{}') || {}; } catch(e){ return {}; } };
  const pfWrite = st => localStorage.setItem(PF_KEY, JSON.stringify(st));
  const pfPhone = () => innerWidth < 640;
  let pfEl = null, pfChip = null;
  function pfStyle(){
    if(document.getElementById('pf-style')) return;
    const s = document.createElement('style'); s.id = 'pf-style';
    s.textContent = `.pf{position:fixed;z-index:var(--z-float,7500);display:flex;flex-direction:column;background:#000;border:1px solid var(--line);border-radius:12px;
        box-shadow:0 10px 30px rgba(0,0,0,.6);overflow:hidden;min-width:${PF_MIN_W}px;min-height:160px}
      .pf.sheet{left:0!important;right:0;bottom:0;top:auto!important;width:auto!important;height:50vh!important;border-radius:14px 14px 0 0}
      .pf-head{display:flex;align-items:center;gap:6px;padding:6px 8px;background:var(--card);cursor:move;touch-action:none;user-select:none;flex:none}
      .pf.sheet .pf-head{cursor:default}
      .pf-head:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
      .pf-head b{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px}
      .pf-head select{font:inherit;font-size:13px;max-width:40%;background:var(--card2);color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:2px 4px}
      .pf-head button{flex:none;width:30px;height:30px;border-radius:8px;border:1px solid var(--line);background:transparent;color:var(--txt);cursor:pointer;padding:0;
        display:flex;align-items:center;justify-content:center}
      .pf iframe{flex:1;width:100%;border:0;background:#000;display:block}
      .pf.moving iframe{pointer-events:none}
      .pf-grip{position:absolute;right:0;bottom:0;width:22px;height:22px;cursor:nwse-resize;touch-action:none;
        background:linear-gradient(135deg,transparent 50%,var(--dim) 50%,var(--dim) 58%,transparent 58%,transparent 70%,var(--dim) 70%,var(--dim) 78%,transparent 78%)}
      .pf.sheet .pf-grip{display:none}
      .pf-chip{position:fixed;z-index:var(--z-float,7500);right:12px;bottom:84px;display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:999px;
        border:1px solid var(--accent);background:var(--card);color:var(--txt);font:inherit;font-size:13px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.5)}`;
    document.head.append(s);
  }
  function pfFit(st){                              // inside the screen, never smaller than the least size
    const w = Math.max(PF_MIN_W, Math.min(st.w || 420, innerWidth - 16)), h = Math.max(160, Math.min(st.h || 300, innerHeight - 16));
    return {...st, w, h, x: Math.max(8, Math.min(st.x ?? innerWidth - w - 16, innerWidth - w - 8)), y: Math.max(8, Math.min(st.y ?? 80, innerHeight - h - 8))};
  }
  function pfPlace(st){
    pfEl.classList.toggle('sheet', pfPhone());
    if(pfPhone()) return;
    Object.assign(pfEl.style, {left: st.x + 'px', top: st.y + 'px', width: st.w + 'px', height: st.h + 'px'});
  }
  async function floatOpen(name){
    if(!panels.length) await load();
    const p = panels.find(x => x.name === name) || panels.find(x => x.name === pfRead().name) || panels[0];
    if(!p) return false;
    pfStyle();
    const old = pfRead(), first = !old.w || old.name !== p.name, base = {...old, name: p.name, open: true, min: false};
    let st = pfPhone() ? base : pfFit({...base,      // a phone's sheet keeps the desktop's place and size as they were
      ...(first ? {w: Math.min(480, innerWidth - 24), h: Math.round(Math.min(480, innerWidth - 24) * p.h / p.w) + 44} : {})});
    pfWrite(st);
    if(pfChip){ pfChip.remove(); pfChip = null; }
    if(!pfEl){
      pfEl = document.createElement('div');
      pfEl.className = 'pf'; pfEl.setAttribute('role', 'dialog');
      pfEl.innerHTML = `<div class="pf-head" tabindex="0"><b></b><select aria-label="${esc(i18n('Panel'))}"></select>
          <button type="button" data-pf="min" aria-label="${esc(i18n('Minimize'))}"><svg viewBox="0 0 24 24" width="16" height="16"><path d="M5 18h14" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg></button>
          <button type="button" data-pf="close" aria-label="${esc(i18n('Close'))}"><svg viewBox="0 0 24 24" width="16" height="16"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg></button></div>
        <iframe></iframe><div class="pf-grip" aria-hidden="true"></div>`;
      document.body.append(pfEl);
      pfBind();
    }
    const head = pfEl.querySelector('.pf-head'), sel = pfEl.querySelector('select'), fr = pfEl.querySelector('iframe');
    pfEl.setAttribute('aria-label', i18n('Panel {0}', p.title));
    head.setAttribute('aria-label', i18n('{0}: move it with the arrows, Esc minimizes', p.title));
    pfEl.querySelector('.pf-head b').textContent = p.title;
    sel.innerHTML = panels.map(x => `<option value="${esc(x.name)}"${x.name === p.name ? ' selected' : ''}>${esc(x.title)}</option>`).join('');
    sel.hidden = panels.length < 2;
    fr.title = p.title;
    const src = '/panel-view?panel=' + encodeURIComponent(p.name);   // the view signs in as the dashboard does (this browser's token)
    if(fr.getAttribute('src') !== src) fr.setAttribute('src', src);
    pfEl.hidden = false;
    pfPlace(st);
    return true;
  }
  function pfMin(){
    if(!pfEl) return;
    const st = {...pfRead(), min: true}; pfWrite(st);
    pfEl.hidden = true;
    pfChip = pfChip || document.createElement('button');
    pfChip.type = 'button'; pfChip.className = 'pf-chip';
    pfChip.textContent = pfEl.querySelector('.pf-head b').textContent;
    pfChip.setAttribute('aria-label', i18n('Show the panel {0}', pfChip.textContent));
    pfChip.onclick = () => floatOpen(st.name);
    document.body.append(pfChip);
    pfChip.focus();
  }
  function pfClose(){
    pfWrite({...pfRead(), open: false, min: false});
    if(pfEl){ pfEl.remove(); pfEl = null; }
    if(pfChip){ pfChip.remove(); pfChip = null; }
  }
  function pfBind(){
    const head = pfEl.querySelector('.pf-head'), grip = pfEl.querySelector('.pf-grip');
    pfEl.querySelector('[data-pf="min"]').onclick = pfMin;
    pfEl.querySelector('[data-pf="close"]').onclick = pfClose;
    pfEl.querySelector('select').onchange = ev => floatOpen(ev.target.value);
    const drag = (el, apply) => el.addEventListener('pointerdown', ev => {
      if(pfPhone() || ev.button || ev.target.closest('button,select')) return;
      const st0 = pfRead(), x0 = ev.clientX, y0 = ev.clientY;
      el.setPointerCapture(ev.pointerId); pfEl.classList.add('moving');
      const move = e => { const st = pfFit(apply(st0, e.clientX - x0, e.clientY - y0)); pfPlace(st); pfWrite(st); };
      const up = () => { el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up); el.removeEventListener('pointercancel', up); pfEl.classList.remove('moving'); };
      el.addEventListener('pointermove', move); el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up);
    });
    drag(head, (s, dx, dy) => ({...s, x: s.x + dx, y: s.y + dy}));
    drag(grip, (s, dx, dy) => ({...s, w: s.w + dx, h: s.h + dy}));
    head.addEventListener('keydown', ev => {
      if(ev.key === 'Escape'){ ev.preventDefault(); ev.stopPropagation(); pfMin(); return; }
      const d = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[ev.key];
      if(!d || pfPhone()) return;
      ev.preventDefault();
      const k = ev.shiftKey ? 60 : 16, st = pfFit({...pfRead(), x: pfRead().x + d[0] * k, y: pfRead().y + d[1] * k});
      pfPlace(st); pfWrite(st);
    });
  }
  addEventListener('resize', () => { if(pfEl && !pfEl.hidden){ if(pfPhone()) return pfPlace(pfRead()); const st = pfFit(pfRead()); pfPlace(st); pfWrite(st); } });
  window.PanelFloat = {open: floatOpen, minimize: pfMin, close: pfClose};
  setTimeout(() => { const st = pfRead(); if(st.open) floatOpen(st.name).then(ok => { if(ok && st.min) pfMin(); }); }, 1500);   // as it was left

  HomePlugins.register({
    addMenu: [{label: 'ESP screen: its first firmware over USB', hint: 'Build it, flash it from the browser, then bind the screen by its code', button: 'Open', onClick: espPair}],
    onReload: [load],
    settings: [{subject: 'panels', title: 'Panels', render}],
    tabs: [{id: 'panels', label: 'Panels', after: 'tech', render, visible: () => true, badge: () => panels.length}],
    // the help sheet and the header's dragged "?" (README "Help")
    help: [
      {id: 'panels.builder', screen: 'tab:p:panels', sel: '#pn-builder', title: 'Panel builder', text: 'Opens the editor of the panels\' layouts: pick a panel, place its widgets.'},
      {id: 'panels.back', screen: 'tab:p:panels', sel: '#pn-back', title: '\u2190 All panels', text: 'Back from the builder to the list of panels.'},
      {id: 'panels.pair', screen: 'tab:p:panels', sel: '#pn-pair', title: 'Connect an Android tablet', text: 'Opens the window where a new Android tablet running the panel launcher is bound by the code on its screen.'},
      {id: 'panels.tablets', screen: 'tab:p:panels', sel: '#pn-tabs', title: 'Android tablets', text: 'The Android tablets of the house: the home-screen pin, the launcher restart, unbinding.'},
      {id: 'panels.esp', screen: 'tab:p:panels', sel: '#pn-esp', title: 'Connect an ESP screen', text: 'A new ESP32 screen: build its first firmware, flash it over USB, bind it by the code it shows.'},
      {id: 'panels.app', screen: 'tab:p:panels', sel: '#pn-app', at: 'label', title: 'Panel launcher', text: 'The launcher the Android tablets install: its version and build date. A tablet behind it updates itself; ESP screens have their own firmware.'},
      {id: 'panels.qr', screen: 'tab:p:panels', sel: '#pn-app-qr', title: 'QR', text: 'A QR code of the launcher\'s address on this dashboard, for a tablet\'s camera; the address is under it.'},
      {id: 'panels.build', screen: 'tab:p:panels', sel: '#pn-app-build', title: 'Build the app', text: 'Builds the launcher on the build server, signed with the house\'s key; the tablets update to it.'},
      {id: 'panels.edit', screen: 'tab:p:panels', sel: '.pn-edit', title: 'Edit', text: 'Opens this panel in the builder.'},
      {id: 'panels.float', screen: 'tab:p:panels', sel: '.pn-float', title: 'Open beside',
       text: 'The panel in a window over the dashboard: press on it and watch the house. Drag it by its top, resize it by its corner; it stays over every tab.'},
      {id: 'panels.flash', screen: 'tab:p:panels', sel: '.pn-flash', title: 'Flash', text: 'ESP screens only: builds this screen\'s firmware and sends it over Wi-Fi.'},
      {id: 'panels.room', screen: 'tab:p:panels', sel: '.pn-room', title: 'Room', text: 'The room the panel is in.'},
      {id: 'panels.repair', screen: 'tab:p:panels', sel: '.pn-repair', title: 'Pair again', text: 'Android tablets only: opens the Android tablets window; the tablet is bound again by its new code.'},
      {id: 'panels.delete', screen: 'tab:p:panels', sel: '.pn-del', title: 'Delete', text: 'Deletes the panel: its layout, its tablets with their keys, its retained messages on the broker.'},
      {id: 'panels.mac', screen: 'tab:p:panels', sel: '.pn-mac', title: 'Yes, this is it', text: 'Confirms the screen\'s MAC; only a confirmed screen is flashed over Wi-Fi.'},
      {id: 'tablets.code', screen: 'panels-tablets', screenName: 'Android tablets', sel: '.pn-code', title: 'Code', text: 'The code the tablet shows on its screen.'},
      {id: 'tablets.name', screen: 'panels-tablets', screenName: 'Android tablets', sel: '.pn-name', title: 'Panel', text: 'The panel this tablet shows: Latin letters, digits, - or _.'},
      {id: 'tablets.bind', screen: 'panels-tablets', screenName: 'Android tablets', sel: '.pn-bind', title: 'Bind', text: 'Binds the tablet to the panel; it gets a key of its own.'},
      {id: 'tablets.unbind', screen: 'panels-tablets', screenName: 'Android tablets', sel: '.pn-unbind', title: 'Unbind', text: 'The tablet\'s key stops working; the panel\'s layout stays.'},
      {id: 'tablets.rename', screen: 'panels-tablets', screenName: 'Android tablets', sel: '.pn-ren', title: 'Rename the tablet',
       text: 'Its name in the house: the list, the panels and the cameras show it. It stays when the tablet is paired again.'},
      {id: 'tablets.pin', screen: 'panels-tablets', screenName: 'Android tablets', sel: '.pn-pin', title: 'Home screen pinned', text: 'On: the launcher stays the home screen (needs Device Owner mode). Off: another home screen may be chosen.', more: 'Device Owner is set once over adb on a tablet with no accounts: dpm set-device-owner dev.freidkin.panel/.AdminReceiver (the launcher\'s README).'},
      {id: 'tablets.restart', screen: 'panels-tablets', screenName: 'Android tablets', sel: '.pn-won', title: 'Raise the launcher if it fell', text: 'When the tablet is silent longer than set, the house restarts its launcher by itself.'},
      {id: 'tablets.after', screen: 'panels-tablets', screenName: 'Android tablets', sel: '.pn-wafter', title: 'After', text: 'Minutes of silence that count as fallen (1-60).'},
      {id: 'tablets.every', screen: 'panels-tablets', screenName: 'Android tablets', sel: '.pn-wevery', title: 'At most once in', text: 'The least time between two tries, in minutes (1-240).'},
      {id: 'esp.board', screen: 'panels-esp', screenName: 'Connect an ESP screen', sel: '#pe-hw', title: 'Board', text: 'The screen\'s board; the firmware is built for it.'},
      {id: 'esp.build', screen: 'panels-esp', screenName: 'Connect an ESP screen', sel: '#pe-build', title: 'Build the first firmware', text: 'A firmware that joins the house\'s Wi-Fi and shows a code to bind the screen.'},
      {id: 'esp.download', screen: 'panels-esp', screenName: 'Connect an ESP screen', sel: '.pe-dl', title: 'Download the firmware', text: 'The built file, to flash with ESPHome Web or esptool.'},
      {id: 'esp.usb', screen: 'panels-esp', screenName: 'Connect an ESP screen', sel: '.pe-usb', title: 'Flash over USB (beta)', text: 'Flashes from this browser (Chrome or Edge over HTTPS); pick the screen\'s port, never a Zigbee stick. If it fails, download the firmware and flash it with ESPHome Web.'},
      {id: 'esp.code', screen: 'panels-esp', screenName: 'Connect an ESP screen', sel: '.pe-code', title: 'Code', text: 'The code the new screen shows.'},
      {id: 'esp.name', screen: 'panels-esp', screenName: 'Connect an ESP screen', sel: '.pe-name', title: 'Panel', text: 'The name of the new panel.'},
      {id: 'esp.bind', screen: 'panels-esp', screenName: 'Connect an ESP screen', sel: '.pe-bind', title: 'Bind', text: 'Binds the screen; its full firmware is then built and sent.'},
    ],
  });
})();
