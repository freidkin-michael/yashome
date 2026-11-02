// ESP recovery via the setup AP: the Add menu row and its dialog. Relay nodes and the host scan,
// the user picks an AP (and a relay, or auto), the backend relays the house Wi-Fi to its portal.
(() => {
const MID = '__provision__';
const esc = Home.escapeHtml;
const box = id => document.getElementById('md').querySelector(id);
const SEL = 'background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 8px;font:inherit';

function manualHint(ap){
  return `<div class="bindcaption" style="margin-top:8px">${esc(i18n(
    'By hand: join the network {0} from a phone or a laptop and open http://192.168.4.1 - it has the form for the house Wi-Fi.',
    ap || 'name-setup'))}</div>`;
}

async function open(){
  Home.openModal(`
    <div class="head">
      <button class="closebtn" onclick="Home.closeModal()">x</button>
      <h2>${i18n('ESP via its setup AP')}</h2>
      <div class="sub">${esc(i18n('A node that lost its Wi-Fi opens its own network name-setup. A neighbour node with the provision package, or the host relay, joins it and hands over the house Wi-Fi.'))}</div>
    </div>
    <div class="body"><section>
      <h3>${i18n('Relays')}</h3><div id="pv-relays"><div class="bindcaption">${i18n('Asking the nodes and the host...')}</div></div>
      <h3>${i18n('Setup APs found')}</h3><div id="pv-list"><div class="bindcaption">${i18n('Scanning (about 8 s)...')}</div></div>
      <div id="pv-out"></div>
    </section></div>`, MID);
  let r;
  try{
    r = await Home.api('/api/esphome/ap/scan', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({wait: 8})});
  }catch(e){
    if(Home.modalId() === MID) box('#pv-list').innerHTML =
      `<div class="bindcaption" style="color:var(--bad)">${esc(i18n('Scan failed: {0}', e.message || e))}</div>`;
    return;
  }
  if(Home.modalId() === MID) render(r);
}

let worstMin = 8;

// "MAC xx:..:xx" per BSSID heard; red when it is not an Espressif one (it may be a fake AP)
function macs(a){
  return (a.bssids || []).map(b => {
    const fake = (a.espressif || {})[b] === false;
    return `<span style="color:${fake ? 'var(--bad)' : 'var(--dim)'}" title="${fake ? esc(i18n('Not an Espressif MAC: this may be a fake AP')) : ''}">${
      esc(i18n('MAC {0}', b))}${fake ? ' - ' + esc(i18n('not Espressif, may be a fake')) : ''}</span>`;
  }).join('<br>');
}

function render(r){
  if(r.worst_s) worstMin = Math.ceil(r.worst_s / 60);
  const rows = (r.relays || []).map(x => `<div class="row"><span class="rlabel">${esc(x.node)}</span>
    <span style="font-size:12px;color:${x.answered || x.gone ? 'var(--dim)' : 'var(--bad)'}">${esc(
      x.answered ? i18n('hears {0}', x.seen) : x.gone ? i18n('missed {0} scans - gone?', x.missed) : i18n('did not answer'))}</span></div>`);
  const s = r.server || {};
  rows.push(`<div class="row"><span class="rlabel">${i18n('Host relay')}</span><span style="font-size:12px;color:${
    s.ok || !s.configured ? 'var(--dim)' : 'var(--bad)'}">${esc(!s.configured ? i18n('not configured')
    : s.ok ? i18n('scanned') : i18n('unavailable: {0}', s.error || '?'))}</span></div>`);
  if(r.mqtt_error) rows.push(`<div class="bindcaption" style="color:var(--bad)">${esc(i18n('MQTT: {0}', r.mqtt_error))}</div>`);
  box('#pv-relays').innerHTML = rows.join('');
  const list = box('#pv-list'), aps = r.aps || [];
  if(!aps.length){
    list.innerHTML = `<div class="bindcaption">${i18n('No setup AP in sight: the node is not in its setup AP, is already back, or nobody hears it.')}</div>` + manualHint('');
    return;
  }
  const presets = Object.entries(r.presets || {}).map(([k, p]) => `<option value="${esc(k)}">${esc(i18n(p.label))}</option>`).join('');
  list.innerHTML = aps.map((a, i) => {
    const relays = a.candidates.map(c => `<option value="${esc(c.via)}">${esc(i18n(c.label))}${c.rssi != null ? ' ' + c.rssi + ' dBm' : ''}</option>`).join('');
    const tag = a.conflict ? i18n('two APs with this name: one open, one secured - refusing')
      : a.house_open ? i18n("an open -setup AP: not one of the house's nodes")
      : (a.bssids || []).length > 1 ? i18n('two APs with this name (different MACs) - refusing')
      : a.secured ? i18n('secured') : a.setup ? i18n('a node in its setup AP') : i18n('open, not named name-setup');
    const refuse = a.conflict || a.house_open || (a.bssids || []).length > 1;
    return `<div class="row" style="flex-wrap:wrap;gap:8px" data-i="${i}">
      <span class="rlabel">${esc(a.setup ? i18n('portal {0}', a.ssid) : a.ssid)}<br><span style="color:${refuse ? 'var(--bad)' : 'var(--dim)'};font-weight:400;font-size:12px">${esc(tag)}${
        (a.bssids || []).length ? '<br>' + macs(a) : ''}</span></span>
      ${refuse ? '' : `<span style="display:flex;gap:6px;flex-wrap:wrap">
        ${a.setup ? '' : `<select class="pv-portal" style="${SEL}" title="${i18n('Which portal the device has')}">${presets}</select>`}
        ${a.secured ? `<input class="pv-pw" type="password" autocomplete="off" placeholder="${i18n('AP password (empty = the house key)')}" style="${SEL};width:12em">` : ''}
        <select class="pv-via" style="${SEL}"><option value="auto">${i18n('auto (the closest first)')}</option>${relays}</select>
        <button class="dlg-btn primary pv-go">${i18n('Send Wi-Fi')}</button>
      </span>`}</div>`;
  }).join('') + `<div class="bindcaption" style="margin-top:6px">${i18n('A relay node leaves the network for about 80-90 s and does not answer meanwhile.')}</div>`;
  list.querySelectorAll('.pv-go').forEach(b => { b.onclick = () => go(b, aps[+b.closest('[data-i]').dataset.i]); });
}

async function go(btn, a){
  const row = btn.closest('[data-i]'), out = box('#pv-out');
  const pick = c => row.querySelector(c);
  const body = {ap: a.ssid, via: pick('.pv-via').value, portal: pick('.pv-portal') ? pick('.pv-portal').value : 'esphome'};
  if(a.secured && pick('.pv-pw').value) body.ap_password = pick('.pv-pw').value;   // empty: PANELS_AP_PASSWORD
  if(!a.ssid.endsWith('-setup') && !confirm(i18n(
      '{0} is not named name-setup. It will get the house Wi-Fi password. Do this only for your own device that you have just reset. Continue?', a.ssid))) return;
  document.querySelectorAll('.pv-go').forEach(b => { b.disabled = true; });
  btn.textContent = i18n('Sending...');
  out.innerHTML = `<div class="bindcaption">${esc(i18n('Handing the Wi-Fi to {0}; usually about a minute, at worst about {1} min.', a.ssid, worstMin))}</div>`;
  let r;
  for(;;){
    try{
      r = await Home.api('/api/esphome/ap/provision', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)});
    }catch(e){
      r = {ok: false, detail: String(e.message || e)};
    }
    // a broker that went down with the node still connected keeps its "online": the owner may override
    if(r.ok || body.force || !/is online/.test(r.detail || '') || !confirm(i18n(
        'The house still sees {0} online. If you are sure it is not (it shows its setup AP), try anyway?', a.ssid))) break;
    body.force = true;
  }
  if(Home.modalId() !== MID) return;
  document.querySelectorAll('.pv-go').forEach(b => { b.disabled = false; });
  btn.textContent = i18n(r.ok ? 'Done' : 'Again');
  const tried = (r.attempts || []).map(x => `<div class="row"><span class="rlabel">${esc(i18n(x.label || x.via))}</span>
    <span style="font-size:12px;color:${x.ok ? 'var(--ok,#4ade80)' : x.busy ? 'var(--dim)' : 'var(--bad)'}">${esc(i18n(x.detail || ''))}</span></div>`).join('');
  const color = r.ok ? 'var(--ok,#4ade80)' : r.pending ? 'var(--dim)' : 'var(--bad)';
  const head = `<div class="bindcaption" style="color:${color}">${esc(i18n(r.detail || (r.ok ? 'Done' : 'Failed')))}${
    r.pending && r.node ? ' (' + esc(r.node) + ')' : ''}</div>` + (r.restore_failed
    ? `<div class="bindcaption" style="color:var(--bad)">${esc(i18n('The host relay could not put its Wi-Fi card back on its network: check that host.'))}</div>` : '');
  if(!r.ok){ out.innerHTML = head + tried + (r.pending ? '' : manualHint(a.ssid)); return; }
  const next = typeof Home.openEsphomeAdd === 'function'
    ? `<div class="row" style="margin-top:8px"><span class="rlabel">${i18n('Next: its card on the dashboard')}</span>
       <button class="dlg-btn primary" id="pv-next">${i18n('Add ESPHome')}</button></div>`
    : `<div class="bindcaption">${i18n('It should appear in Add -> ESPHome in about 30 s.')}</div>`;
  out.innerHTML = head + tried + next;
  const nx = box('#pv-next');
  if(nx) nx.onclick = () => Home.openEsphomeAdd();
}

HomePlugins.register({help: [
  {id: 'provision.portal', screen: 'provision', screenName: 'ESP via its setup AP', sel: '.pv-portal', title: 'Portal', text: 'Only for an open network not named name-setup: which setup page the device shows, so the Wi-Fi goes into the right form.'},
  {id: 'provision.pw', screen: 'provision', screenName: 'ESP via its setup AP', sel: '.pv-pw', title: 'AP password', text: 'Only for a secured setup network: its password; empty: the house key.'},
  {id: 'provision.via', screen: 'provision', screenName: 'ESP via its setup AP', sel: '.pv-via', title: 'Through', text: 'Which relay joins the device\'s network: auto tries the closest first. A relay node is off the network for about 80-90 s meanwhile.'},
  {id: 'provision.go', screen: 'provision', screenName: 'ESP via its setup AP', sel: '.pv-go', title: 'Send Wi-Fi', text: 'Hands the house Wi-Fi to the device\'s setup page; about a minute. A network not named name-setup asks first.'},
  {id: 'provision.next', screen: 'provision', screenName: 'ESP via its setup AP', sel: '#pv-next', title: 'Add ESPHome', text: 'Opens the ESPHome window, where the device shows up in about 30 s.'},
], addMenu: [{
  label: 'ESP via its setup AP',
  hint: 'A node that lost its Wi-Fi opened name-setup: a neighbour node or the host relay hands it the house Wi-Fi',
  button: 'Search', onClick: open, scan: true,      // the Add / bind window (connect) finds these by itself
}]});
})();
