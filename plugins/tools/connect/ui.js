// Add / bind: the header's Add button opens ONE window that starts every way a device joins at once (Zigbee pairing, ESPHome on
// MQTT, setup networks, tablets, ESP screens) and lists everything new in one live list; each row binds in place. A code typed in
// the top box finds the device that shows it. They stay open while the window is (renewed every minute); closing it closes what it
// opened, not a way someone opened elsewhere. Texts: English source, i18n.json.
(() => {
const MID = '__connect__';
const esc = Home.escapeHtml;
const J = b => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
const IN = 'background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 10px;font:inherit';
const KIND = {zigbee: 'Zigbee', esphome: 'ESPHome', yashgen: 'yashGen', lostwifi: 'Lost its Wi-Fi', tablet: 'Android tablet', screen: 'ESP screen'};
const CATS = ['switch', 'sensor', 'indicator', 'other'];
const SCAN_EVERY = 40000, FAST = 2000, ESP_EVERY = 6000, KEEP_OPEN = 60000;       // KEEP_OPEN: Zigbee closes after 254 s, the invite after 5 min
let S = null;                                    // the open window's state; null when closed

const box = sel => document.getElementById('md').querySelector(sel);
const live = () => S && Home.modalId() === MID;

async function start(){                          // also every minute while open: the ways stay open as long as the window
  if(!S) return;
  const me = S, window_ = S.id;
  try {
    const r = await Home.api('/api/connect/start', J({window: window_}));
    if(S !== me){ Home.api('/api/connect/stop', J({window: window_})).catch(() => {}); return; }      // closed meanwhile: let go of what this call kept open
    S.errors = r.errors || {};
  } catch(e){ if(S === me) S.errors = {all: e.message || String(e)}; }
  if(S === me) paint();
}

function stop(){
  if(!S) return;
  for(const t of S.timers) clearInterval(t);
  const id = S.id;
  S = null;
  Home.api('/api/connect/stop', J({window: id})).catch(() => {});   // the house closes what no other window keeps open
  Home.reloadDevices().catch(() => {});
}

async function poll(){
  if(!live()) return;
  const [zb, pn] = await Promise.all([
    Home.api('/api/zigbee/pair/status').catch(() => null),
    Home.api('/api/panels/discovered').catch(() => null)]);
  if(!live()) return;
  S.zb = zb; S.pn = pn;
  paint();
}

async function pollEsp(){
  if(!live()) return;
  const r = await Home.api('/api/esphome/discover').catch(() => null);
  if(!live()) return;
  S.esp = r ? r.nodes || [] : null;
  paint();
}

async function scan(){
  if(!live() || S.scanning) return;
  S.scanning = true; paint();
  let r = null;
  try { r = await Home.api('/api/esphome/ap/scan', J({wait: 8})); } catch(e){ if(S) S.errors.scan = e.message || String(e); }
  if(!live()) return;
  S.scanning = false;
  if(r){ S.aps = r.aps || []; delete S.errors.scan; }
  paint();
}

// every new thing as a row {key, kind, title, sub, ctl (html), act (fn on its button)}
function rows(){
  const out = [];
  for(const f of (S.zb && S.zb.found) || []){
    const key = 'zb:' + f.ieee, sub = [f.vendor, f.model, f.interview].filter(Boolean).join(' \u00b7 ');
    if(f.registered) out.push({key, kind: 'zigbee', title: f.name, sub, done: i18n('added')});
    else out.push({key, kind: 'zigbee', title: f.friendly_name, sub, fields: [['name', f.friendly_name]], act: v =>
      Home.api('/api/zigbee/pair/add', J({ieee: f.ieee, friendly_name: f.friendly_name, name: v.name}))});
  }
  for(const n of S.esp || []){
    if(n.registered || n.alive !== 'online' || !n.n_controls) continue;
    out.push({key: 'esp:' + n.node, kind: 'esphome', title: n.title, sub: [n.model, i18n('{0} channels', n.n_controls)].filter(Boolean).join(' \u00b7 '),
      fields: [['name', n.title]], cat: true, act: v => Home.api('/api/esphome/add', J({node: n.node, name: v.name, category: v.cat}))});
  }
  for(const a of S.aps || []){
    if(!a.setup) continue;
    const refuse = a.conflict || a.house_open || (a.bssids || []).length > 1;
    const key = 'ap:' + a.ssid, node = a.ssid.replace(/-setup$/, '');
    if(refuse) out.push({key, kind: a.secured ? 'yashgen' : 'lostwifi', title: node, sub: i18n('two networks with this name, or not one of the house: refused')});
    else if(a.secured) out.push({key, kind: 'yashgen', title: node, sub: i18n('the code is on its label'), code: true, act: v =>
      Home.api('/api/pair/start', J({ap: a.ssid, code: v.code.toUpperCase()}))});
    else out.push({key, kind: 'lostwifi', title: node, sub: i18n('it opened its setup network: the house hands it the Wi-Fi'), button: 'Send Wi-Fi', act: () => provision(a)});
  }
  const pn = S.pn || {}, byId = {};
  for(const d of pn.devices || []) byId[d.id] = {...d};
  for(const d of pn.registered || []) byId[d.id] = {...(byId[d.id] || {}), ...d};
  for(const d of Object.values(byId)){
    if(d.status === 'bound') continue;
    const key = 'tab:' + d.id, title = d.name || d.model || d.id, sub = [d.model, d.ip].filter(Boolean).join(' \u00b7 ');
    if(d.status !== 'pending') out.push({key, kind: 'tablet', title, sub: i18n('heard, waiting for it to introduce itself')});
    else out.push({key, kind: 'tablet', title, sub: sub + (d.repairs ? ' \u00b7 ' + i18n('pairing again') : ''), code: true,
      fields: [['panel', d.repairs || slug(title)]], act: v => Home.api('/api/panels/bind', J({id: d.id, code: v.code, panel: v.panel}))});
  }
  for(const e of pn.esp || [])
    out.push({key: 'scr:' + e.mac, kind: 'screen', title: e.hw, sub: [e.mac, e.ip].filter(Boolean).join(' \u00b7 '), code: true,
      fields: [['panel', 'panel-' + String(e.mac).replace(/:/g, '').slice(-4).toLowerCase()]],
      act: v => Home.api('/api/panels/bind_esp', J({mac: e.mac, code: v.code, panel: v.panel}))});
  return out;
}
const slug = s => String(s || 'panel').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32) || 'panel';

async function provision(a){
  const body = {ap: a.ssid, via: 'auto', portal: 'esphome'};
  for(;;){
    let r;
    try { r = await Home.api('/api/esphome/ap/provision', J(body)); } catch(e){ r = {ok: false, detail: e.message || String(e)}; }
    if(r.ok) return r;
    // a broker that went down with the node connected keeps its "online": the owner may override
    if(body.force || !/is online/.test(r.detail || '') || !confirm(i18n('The house still sees {0} online. If you are sure it is not (it shows its setup network), try anyway?', a.ssid)))
      throw new Error(r.detail || i18n('failed'));
    body.force = true;
  }
}

function rowHtml(r){
  const f = (name, val, ph) => `<input data-f="${name}" value="${esc(val || '')}" placeholder="${esc(ph)}" autocomplete="off" style="${IN};width:150px">`;
  const ctl = r.done ? `<span class="bindcaption">${Home.icon ? Home.icon('check', 14) : ''} ${esc(r.done)}</span>` : !r.act ? '' : `
    ${r.code ? `<input data-f="code" placeholder="${esc(i18n('code from the device'))}" autocomplete="off" maxlength="16" style="${IN};width:150px">` : ''}
    ${(r.fields || []).map(([n, v]) => f(n, v, i18n(n === 'panel' ? 'panel name' : 'name'))).join('')}
    ${r.cat ? `<select data-f="cat" style="${IN}">${CATS.map(c => `<option value="${c}">${esc(i18n('cat:' + c))}</option>`).join('')}</select>` : ''}
    <button class="dlg-btn primary" data-go>${esc(i18n(r.button || 'Bind'))}</button>`;
  return `<div class="row" data-k="${esc(r.key)}" style="flex-wrap:wrap;gap:8px">
    <span class="rlabel"><span class="cn-tag">${esc(i18n(KIND[r.kind]))}</span> ${esc(r.title)}<br>
      <span style="color:var(--dim);font-weight:400;font-size:12px">${esc(r.sub || '')}</span></span>
    <span style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">${ctl}</span>
    <div class="bindcaption" data-out style="flex-basis:100%">${esc((S.msgs[r.key] || [])[0] || '')}</div></div>`;
}

function banner(){
  const z = S.zb, p = S.pn, bits = [];
  if(z) bits.push(z.active ? i18n('Zigbee: pairing open {0} s', z.remaining) : i18n('Zigbee: pairing closed'));
  if(p) bits.push(p.pairing > 0 ? i18n('tablets: invited {0} s', p.pairing) : i18n('tablets: invitation closed'));
  bits.push(S.scanning ? i18n('looking for setup networks...') : S.aps ? i18n('setup networks: {0}', S.aps.filter(a => a.setup).length) : '');
  const errs = Object.entries(S.errors || {}).map(([k, v]) => `${k}: ${v}`);
  const closed = (z && !z.active) || (p && !(p.pairing > 0));
  return `${esc(bits.filter(Boolean).join(' \u00b7 '))}${closed ? ` <button class="dlg-btn" data-again style="min-height:30px;padding:2px 10px">${esc(i18n('Open again'))}</button>` : ''}${
    errs.length ? `<div style="color:var(--bad)">${esc(errs.join('; '))}</div>` : ''}`;
}

// redraw only what changed, and keep what was typed and where the caret was: the list is polled every 2 s
function paint(){
  if(!live()) return;
  box('#cn-state').innerHTML = banner();
  const again = box('[data-again]'); if(again) again.onclick = start;
  const list = box('#cn-list'), rs = rows();
  const sig = JSON.stringify([rs.map(r => [r.key, r.title, r.sub, r.done, !!r.act]), S.msgs]);
  if(list.dataset.sig === sig) return;
  const typed = {}, ae = document.activeElement;
  list.querySelectorAll('[data-k] [data-f]').forEach(i => { typed[i.closest('[data-k]').dataset.k + '|' + i.dataset.f] = i.value; });
  const focus = ae && ae.dataset && ae.dataset.f && ae.closest('[data-k]') ? [ae.closest('[data-k]').dataset.k, ae.dataset.f, ae.selectionStart, ae.selectionEnd] : null;
  list.dataset.sig = sig;
  list.innerHTML = rs.length ? rs.map(rowHtml).join('') : `<div class="bindcaption">${esc(i18n('Nothing new yet. Put the device into pairing, power it on or start its app: it shows up here.'))}</div>`;
  list.querySelectorAll('[data-k] [data-f]').forEach(i => { const v = typed[i.closest('[data-k]').dataset.k + '|' + i.dataset.f]; if(v != null) i.value = v; });
  if(focus){
    const el = list.querySelector(`[data-k="${CSS.escape(focus[0])}"] [data-f="${focus[1]}"]`);
    if(el){ el.focus(); try { el.setSelectionRange(focus[2], focus[3]); } catch(e){ /* a select */ } }
  }
  for(const r of rs){
    const row = list.querySelector(`[data-k="${CSS.escape(r.key)}"]`), go = row && row.querySelector('[data-go]');
    if(go) go.onclick = () => bind(r, row, go);
  }
}

async function bind(r, row, go){
  const v = {};
  row.querySelectorAll('[data-f]').forEach(i => { v[i.dataset.f] = i.value.trim(); });
  if(r.code && !v.code){ row.querySelector('[data-f="code"]').focus(); return; }
  go.disabled = true;
  S.msgs[r.key] = [i18n('Binding...')]; paint();
  try {
    await r.act(v);
    if(S) S.msgs[r.key] = [i18n('Done: its card appears once it reports.')];
    Home.reloadDevices().catch(() => {});
  } catch(e){ if(S) S.msgs[r.key] = [e.message || String(e)]; }
  if(S){ paint(); poll(); }
}

// the top code box: which waiting device shows this code (tablets and ESP screens); a yashGen code is its network's key
async function findCode(){
  const code = box('#cn-code').value.trim(), out = box('#cn-code-out');
  if(!code) return;
  let m = [];
  try { m = (await Home.api('/api/connect/code', J({code}))).matches || []; } catch(e){ out.textContent = e.message || String(e); return; }
  if(m.length === 1 && m[0].bound) return rebindAsk(m[0], code, out);
  const key = m.length === 1 ? (m[0].kind === 'tablet' ? 'tab:' : 'scr:') + m[0].id : null;
  const yg = rows().filter(r => r.kind === 'yashgen' && r.act);
  const target = key || (!m.length && yg.length === 1 ? yg[0].key : null);
  if(!target){
    out.textContent = m.length ? i18n('Two devices show this code: power one off') : i18n('No device shows this code: type it in its row');
    return;
  }
  const row = box(`#cn-list [data-k="${CSS.escape(target)}"]`);
  if(!row){ out.textContent = i18n('Found, but it is not in the list yet: wait a moment'); return; }
  row.querySelector('[data-f="code"]').value = code;
  row.scrollIntoView({block: 'center'});
  row.style.outline = '2px solid var(--on)';
  out.textContent = m.length ? i18n('Found: {0} {1}. Check it and press Bind in its row.', i18n(KIND[m[0].kind === 'tablet' ? 'tablet' : 'screen']), m[0].model)
    : i18n('Put into the only yashGen device in sight: press Bind in its row.');
}

// a tablet that is bound already shows its code: it is known, so it is bound again by the house alone (its key stays,
// its next heartbeat tells it the panel); no invitation over the network is needed
function rebindAsk(t, code, out){
  out.innerHTML = `${esc(i18n('This is "{0}" ({1}), bound already to panel {2}. Bind it again?', t.name || t.id, t.model, t.panel))}
    <span style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px"><input data-rb="panel" value="${esc(t.panel)}" autocomplete="off" style="${IN};width:150px">
    <button class="dlg-btn primary" data-rb="go">${esc(i18n('Bind again'))}</button></span><div data-rb="res"></div>`;
  out.querySelector('[data-rb="go"]').onclick = async ev => {
    const panel = out.querySelector('[data-rb="panel"]').value.trim(), res = out.querySelector('[data-rb="res"]');
    ev.target.disabled = true;
    try {
      const r = await Home.api('/api/panels/bind', J({id: t.id, code, panel, keep_key: true}));
      res.textContent = i18n('Bound to panel {0}: the tablet switches at its next heartbeat (within a minute).', r.panel);
    } catch(e){ res.textContent = e.message || String(e); ev.target.disabled = false; }
  };
}

function open(){
  if(S) stop();
  const other = HomePlugins.addMenu.filter(e => !e.scan);
  Home.openModal(`<div class="head"><button class="closebtn" onclick="Home.closeModal()">\u00d7</button>
      <h2>${esc(i18n('Add / bind'))}</h2>
      <div class="sub">${esc(i18n('Every way a device joins is open while this window is: Zigbee pairing, ESPHome on the network, setup networks, tablets and screens. Put the device into pairing; it shows up below.'))}</div></div>
    <div class="body"><section>
      <div class="bindcaption" id="cn-state"></div>
      <div class="row" style="gap:8px;flex-wrap:wrap"><input id="cn-code" placeholder="${esc(i18n('code from the device screen or label'))}" autocomplete="off" maxlength="16" style="${IN};flex:1 1 180px">
        <button class="dlg-btn" id="cn-find">${esc(i18n('Find by code'))}</button><button class="dlg-btn" id="cn-scan">${esc(i18n('Look for networks again'))}</button></div>
      <div class="bindcaption" id="cn-code-out"></div>
      <div id="cn-list" style="margin-top:8px"></div></section>
      ${other.length ? `<section><details><summary style="cursor:pointer;font-weight:600">${esc(i18n('Other ways'))}</summary>
        ${other.map((e, i) => `<div class="row"><span class="rlabel">${esc(i18n(e.label))}<br><span style="color:var(--dim);font-weight:400;font-size:12px">${esc(i18n(e.hint || ''))}</span></span>
          <button class="dlg-btn" data-other="${i}">${esc(i18n(e.button || 'Open'))}</button></div>`).join('')}</details></section>` : ''}</div>
    <style>.cn-tag{display:inline-block;font-size:11px;font-weight:600;padding:1px 7px;border-radius:9px;border:1px solid var(--line);color:var(--dim);margin-right:4px}</style>`,
    MID, {onClose: stop});
  S = {zb: null, pn: null, esp: null, aps: null, scanning: false, errors: {}, msgs: {}, timers: [], id: Math.random().toString(36).slice(2, 12)};
  box('#cn-find').onclick = findCode;
  box('#cn-code').onkeydown = ev => { if(ev.key === 'Enter') findCode(); };
  box('#cn-scan').onclick = scan;
  document.getElementById('md').querySelectorAll('[data-other]').forEach(b => { b.onclick = () => HomePlugins.safe(other[+b.dataset.other].onClick); });
  S.timers.push(setInterval(poll, FAST), setInterval(pollEsp, ESP_EVERY), setInterval(scan, SCAN_EVERY), setInterval(start, KEEP_OPEN));
  start().then(poll);
  pollEsp();
  scan();
}

HomePlugins.register({addFlow: [open], help: [
  {id: 'connect.code', screen: 'connect', screenName: 'Add / bind', sel: '#cn-code', title: 'Code', text: 'The code a tablet or a screen shows (or its label has): it finds that device and fills its row.'},
  {id: 'connect.find', screen: 'connect', screenName: 'Add / bind', sel: '#cn-find', title: 'Find by code', text: 'Looks for the device that shows the code typed beside it; Enter does the same.'},
  {id: 'connect.scan', screen: 'connect', screenName: 'Add / bind', sel: '#cn-scan', title: 'Look for networks again', text: 'Scans for setup networks now; it does so every 40 s by itself.'},
  {id: 'connect.again', screen: 'connect', screenName: 'Add / bind', sel: '[data-again]', title: 'Open again', text: 'Opens again the ways that closed: Zigbee pairing after 254 s, the tablet invitation after 5 min.'},
  {id: 'connect.name', screen: 'connect', screenName: 'Add / bind', sel: '#cn-list [data-f="name"]', title: 'Name', text: 'The name the new device gets.'},
  {id: 'connect.panel', screen: 'connect', screenName: 'Add / bind', sel: '#cn-list [data-f="panel"]', title: 'Panel', text: 'The panel an Android tablet or an ESP screen shows.'},
  {id: 'connect.devcode', screen: 'connect', screenName: 'Add / bind', sel: '#cn-list [data-f="code"]', title: 'Code', text: 'The code the device shows on its screen or label.'},
  {id: 'connect.cat', screen: 'connect', screenName: 'Add / bind', sel: '#cn-list [data-f="cat"]', title: 'Kind', text: 'Where it goes on the dashboard: switch, sensor, indicator or other.'},
  {id: 'connect.go', screen: 'connect', screenName: 'Add / bind', sel: '[data-go]', title: 'Bind', text: 'Binds the device of this row with what is filled in.'},
  {id: 'connect.rbpanel', screen: 'connect', screenName: 'Add / bind', sel: '[data-rb="panel"]', title: 'Panel', text: 'The panel this tablet, bound already, shows from now on.'},
  {id: 'connect.rbgo', screen: 'connect', screenName: 'Add / bind', sel: '[data-rb="go"]', title: 'Bind again', text: 'Binds the tablet to that panel; its key stays and it switches within a minute.'},
  {id: 'connect.other', screen: 'connect', screenName: 'Add / bind', sel: '[data-other]', title: 'Other ways', text: 'Ways with a window of their own (an ESP screen\'s first firmware, a yashGen QR, a phone, a SwitchBot lock, Touchmouse).'},
]});
})();
