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
      <h2>${i18n('A device lost its Wi-Fi')}</h2>
      <div class="sub">${esc(i18n('A Wi-Fi device that lost the house network opens a network of its own. The house finds it and hands it the Wi-Fi again: press Send Wi-Fi by its name.'))}</div>
    </div>
    <div class="body"><section>
      <h3>${i18n('Found')}</h3><div id="pv-list"><div class="bindcaption">${i18n('Looking (about 8 s)...')}</div></div>
      <div id="pv-out"></div>
    </section>
    <section><details><summary style="cursor:pointer;font-weight:600">${i18n('Advanced')}</summary>
      <h3>${i18n('Who listens')}</h3><div id="pv-relays"><div class="bindcaption">${i18n('Asking the devices and the house computer...')}</div></div>
    </details></section></div>`, MID);
  let r;
  try{
    r = await Home.api('/api/esphome/ap/scan', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({wait: 8})});
  }catch(e){
    if(Home.modalId() === MID) box('#pv-list').innerHTML =
      `<div class="bindcaption" style="color:var(--bad)">${esc(i18n('Could not look for it: close this window and try again in a minute.'))}</div>`;
    return;
  }
  if(Home.modalId() === MID) render(r);
}

let worstMin = 8;

// a warning when a network heard is not one of an Espressif chip (it may be a fake one); the addresses stay in the API
function fakeWarn(a){
  return (a.bssids || []).some(b => (a.espressif || {})[b] === false)
    ? `<br><span style="color:var(--bad)">${esc(i18n('This may be a fake network: send the Wi-Fi only if it is your own device.'))}</span>` : '';
}
// how close a relay hears the device, in words
const near = rssi => rssi == null ? '' : ' (' + i18n(rssi >= -60 ? 'close' : rssi >= -75 ? 'not far' : 'far') + ')';

function render(r){
  if(r.worst_s) worstMin = Math.ceil(r.worst_s / 60);
  const rows = (r.relays || []).map(x => `<div class="row"><span class="rlabel">${esc(x.node)}</span>
    <span style="font-size:12px;color:${x.answered || x.gone ? 'var(--dim)' : 'var(--bad)'}">${esc(
      x.answered ? i18n('hears {0}', x.seen) : x.gone ? i18n('not heard lately: is it switched on?') : i18n('did not answer'))}</span></div>`);
  const s = r.server || {};
  rows.push(`<div class="row"><span class="rlabel">${i18n('The house computer')}</span><span style="font-size:12px;color:${
    s.ok || !s.configured ? 'var(--dim)' : 'var(--bad)'}">${esc(!s.configured ? i18n('not set up') : s.ok ? i18n('looked') : i18n('could not look now'))}</span></div>`);
  if(r.mqtt_error) rows.push(`<div class="bindcaption" style="color:var(--bad)">${esc(i18n('The house cannot ask its devices now: only the house computer looks.'))}</div>`);
  if(s.error || r.mqtt_error) console.warn('provision scan', s.error, r.mqtt_error);
  box('#pv-relays').innerHTML = rows.join('');
  const list = box('#pv-list'), aps = r.aps || [];
  if(!aps.length){
    list.innerHTML = `<div class="bindcaption">${i18n('Nothing found: the device is back on the Wi-Fi already, or too far. Bring it closer, switch it off and on, then open this window again.')}</div>` + manualHint('');
    return;
  }
  const presets = Object.entries(r.presets || {}).map(([k, p]) => `<option value="${esc(k)}">${esc(i18n(p.label))}</option>`).join('');
  list.innerHTML = aps.map((a, i) => {
    const relays = a.candidates.map(c => `<option value="${esc(c.via)}">${esc(i18n(c.label))}${esc(near(c.rssi))}</option>`).join('');
    const tag = a.conflict || (a.bssids || []).length > 1 ? i18n('two networks with this name: not sent, to be safe. Switch one of them off.')
      : a.house_open ? i18n('not a device of the house: not sent')
      : a.setup ? i18n('a device waiting for the Wi-Fi') : i18n('an open network with another name: send only to your own device');
    const refuse = a.conflict || a.house_open || (a.bssids || []).length > 1;
    return `<div class="row" style="flex-wrap:wrap;gap:8px" data-i="${i}">
      <span class="rlabel">${esc(a.setup ? a.ssid.replace(/-setup$/, '') : a.ssid)}<br><span style="color:${refuse ? 'var(--bad)' : 'var(--dim)'};font-weight:400;font-size:12px">${esc(tag)}${fakeWarn(a)}</span></span>
      ${refuse ? '' : `<span style="display:flex;gap:6px;flex-wrap:wrap">
        ${a.setup ? '' : `<select class="pv-portal" style="${SEL}" title="${i18n('Which portal the device has')}">${presets}</select>`}
        ${a.secured ? `<input class="pv-pw" type="password" autocomplete="off" placeholder="${i18n('its password (empty = the house key)')}" style="${SEL};width:12em">` : ''}
        <select class="pv-via" style="${SEL}"><option value="auto">${i18n('auto (the closest first)')}</option>${relays}</select>
        <button class="dlg-btn primary pv-go">${i18n('Send Wi-Fi')}</button>
      </span>`}</div>`;
  }).join('') + `<div class="bindcaption" style="margin-top:6px">${i18n('The device that passes the Wi-Fi on is away for about a minute and a half meanwhile.')}</div>`;
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
  const head = `<div class="bindcaption" style="color:${color}">${esc(i18n(r.detail || (r.ok ? 'Done' : 'Failed')))}</div>` + (r.restore_failed
    ? `<div class="bindcaption" style="color:var(--bad)">${esc(i18n('The host relay could not put its Wi-Fi card back on its network: check that host.'))}</div>` : '');
  if(!r.ok){ out.innerHTML = head + tried + (r.pending ? '' : manualHint(a.ssid)); return; }
  const next = typeof Home.openEsphomeAdd === 'function'
    ? `<div class="row" style="margin-top:8px"><span class="rlabel">${i18n('Next: its card on the dashboard')}</span>
       <button class="dlg-btn primary" id="pv-next">${i18n('Add a Wi-Fi device')}</button></div>`
    : `<div class="bindcaption">${i18n('It shows up in Add -> Wi-Fi device in about 30 s.')}</div>`;
  out.innerHTML = head + tried + next;
  const nx = box('#pv-next');
  if(nx) nx.onclick = () => Home.openEsphomeAdd();
}

HomePlugins.register({help: [
  {id: 'provision.portal', screen: 'provision', screenName: 'A device lost its Wi-Fi', sel: '.pv-portal', title: 'Portal', text: 'Only for an open network not named name-setup: which setup page the device shows, so the Wi-Fi goes into the right form.'},
  {id: 'provision.pw', screen: 'provision', screenName: 'A device lost its Wi-Fi', sel: '.pv-pw', title: 'AP password', text: 'Only for a secured setup network: its password; empty: the house key.'},
  {id: 'provision.via', screen: 'provision', screenName: 'A device lost its Wi-Fi', sel: '.pv-via', title: 'Through', text: 'Which relay joins the device\'s network: auto tries the closest first. A relay node is off the network for about 80-90 s meanwhile.'},
  {id: 'provision.go', screen: 'provision', screenName: 'A device lost its Wi-Fi', sel: '.pv-go', title: 'Send Wi-Fi', text: 'Hands the house Wi-Fi to the device\'s setup page; about a minute. A network not named name-setup asks first.'},
  {id: 'provision.next', screen: 'provision', screenName: 'A device lost its Wi-Fi', sel: '#pv-next', title: 'Add a Wi-Fi device', text: 'Opens the Wi-Fi device window, where the device shows up in about 30 s.'},
], addMenu: [{
  label: 'A device lost its Wi-Fi',
  hint: 'It opened a network of its own: the house hands it the Wi-Fi again',
  button: 'Search', onClick: open, scan: true,      // the Add / bind window (connect) finds these by itself
}]});
})();
