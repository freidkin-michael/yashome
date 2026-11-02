// SwitchBot: the locks behind a BLE bridge (Add -> SwitchBot lock). A bridge already running is found on the broker, so its id
// is never typed; the hand form (Advanced) is for a bridge not heard yet.
(function () {
const MID = '__switchbot__';
const esc = Home.escapeHtml;
const MODELS = {lock_ultra: 'Lock Ultra', lock_pro: 'Lock Pro', lock: 'Lock'};
const STATES = {LOCKED: 'Locked', UNLOCKED: 'Unlocked', NOT_FULLY_LOCKED: 'Not fully locked', LOCKING: 'Locking', UNLOCKING: 'Unlocking', JAMMED: 'Jammed'};
const IN = 'background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 10px;font:inherit';
const pretty = id => { const t = String(id).replace(/[-_]+/g, ' ').trim(); return t.charAt(0).toUpperCase() + t.slice(1); };   // front_lock -> Front lock
const slug = name => String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 32);
const modelSel = cls => `<select class="${cls}" style="${IN}" aria-label="${esc(i18n('Model'))}">${Object.entries(MODELS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select>`;

async function open(){
  Home.openModal(`
    <div class="head">
      <button class="closebtn" onclick="Home.closeModal()">\u00d7</button>
      <h2>${i18n('SwitchBot lock')}</h2>
      <div class="sub">${i18n('A lock joins through its bridge, a small program on a computer near the lock. A running bridge shows up here: give the lock a name and press Add.')}</div>
    </div>
    <div class="body"><section>
      <div id="sb-list"><div class="bindcaption">${i18n('Loading\u2026')}</div></div>
      <h3>${i18n('Found')}</h3>
      <div id="sb-found"><div class="bindcaption">${i18n('Looking for bridges\u2026')}</div></div>
      <div id="sb-out" class="bindcaption"></div>
    </section>
    <section><details><summary style="cursor:pointer;font-weight:600">${i18n('Advanced')}</summary>
      <div class="bindcaption" style="margin-top:8px">${i18n('A bridge that is not running yet: add the lock under the id the bridge will be started with.')}</div>
      <div class="row" style="gap:6px;flex-wrap:wrap"><input id="sb-name" placeholder="${i18n('name')}" style="flex:1;${IN}">
        <input id="sb-id" placeholder="${i18n('id, latin letters: front_lock')}" style="flex:1;${IN}" autocapitalize="off"></div>
      <div class="row">${modelSel('sb-model-hand')}<button class="dlg-btn" id="sb-add">${i18n('Add')}</button></div>
    </details></section></div>`, MID);
  const nm = document.getElementById('sb-name'), id = document.getElementById('sb-id');
  nm.oninput = () => { if(!id.dataset.typed) id.value = slug(nm.value); };      // the id follows the name until it is typed
  id.oninput = () => { id.dataset.typed = '1'; };
  document.getElementById('sb-add').onclick = () => add(id.value.trim().toLowerCase(), nm.value.trim(), document.querySelector('.sb-model-hand').value);
  await load();
}

function line(k){
  const st = k.online ? [i18n(STATES[k.state] || ''), k.battery != null ? i18n('battery {0}%', k.battery) : '',
                         k.door ? i18n(k.door === 'open' ? 'door open' : 'door closed') : ''].filter(Boolean).join(' \u00b7 ')
                      : i18n('Not answering: check that its bridge is running on the computer near the lock.');
  return `<div class="row"><span class="rlabel"><b>${esc(k.name)}</b>
      <span style="color:${k.online ? 'var(--dim)' : 'var(--bad)'};font-size:12px"> \u00b7 ${esc(st)}</span></span>
      <button class="dlg-btn" data-del="${esc(k.id)}" data-name="${esc(k.name)}" style="color:var(--bad)">${i18n('Delete')}</button></div>`;
}

async function load(){
  const box = document.getElementById('sb-list'), fb = document.getElementById('sb-found');
  let list, found = [];
  try { list = await Home.api('/api/switchbot'); }
  catch(e){ if(box) box.innerHTML = `<div class="bindcaption" style="color:var(--bad)">${esc(e.message||e)}</div>`; return; }
  try { found = (await Home.api('/api/switchbot/found')).found || []; } catch(e){ /* none found: the text says what to do */ }
  if(Home.modalId() !== MID || !box) return;
  box.innerHTML = list.length ? list.map(line).join('') : `<div class="bindcaption">${i18n('No locks yet.')}</div>`;
  box.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if(!confirm(i18n('Delete the lock {0} from the house? The bridge keeps running until you stop it.', b.dataset.name))) return;
    try { await Home.api('/api/esphome/remove', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                 body: JSON.stringify({id: b.dataset.del})}); }
    catch(e){ alert(e.message||e); }
    load();
  });
  fb.innerHTML = found.length ? found.map(f => `<div class="row" data-f="${esc(f)}" style="flex-wrap:wrap;gap:6px">
      <span class="rlabel">${esc(i18n('A lock bridge'))}</span>
      <input class="sb-fname" value="${esc(pretty(f))}" aria-label="${esc(i18n('name'))}" style="${IN};width:150px">${modelSel('sb-fmodel')}
      <button class="dlg-btn primary sb-fadd">${i18n('Add')}</button></div>`).join('')
    : `<div class="bindcaption">${i18n('No bridge found. Start the bridge on the computer near the lock (the plug-in guide says how), then open this window again.')}</div>`;
  fb.querySelectorAll('[data-f]').forEach(r => { r.querySelector('.sb-fadd').onclick = () =>
    add(r.dataset.f, r.querySelector('.sb-fname').value.trim(), r.querySelector('.sb-fmodel').value); });
}

async function add(id, name, model){
  const out = document.getElementById('sb-out');
  if(!id){ out.textContent = i18n('Give the lock a name in latin letters, or type its id.'); return; }
  try {
    await Home.api('/api/switchbot/locks/' + encodeURIComponent(id), {method: 'PUT',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, model})});
    out.textContent = '';
    ['sb-id', 'sb-name'].forEach(i => { const e = document.getElementById(i); if(e){ e.value = ''; delete e.dataset.typed; } });
  } catch(e){ out.textContent = e.message || e; }
  load();
}

HomePlugins.register({
  help: [
    {id: 'switchbot.fname', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '.sb-fname', title: 'Name', text: 'What the dashboard calls the lock found through its bridge.'},
    {id: 'switchbot.fadd', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '.sb-fadd', title: 'Add', text: 'Adds the lock of this bridge.'},
    {id: 'switchbot.id', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '#sb-id', title: 'Id', text: 'Latin letters, the same id the bridge was started with: that is how the house finds the lock.'},
    {id: 'switchbot.name', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '#sb-name', title: 'Name', text: 'What the dashboard calls the lock.'},
    {id: 'switchbot.model', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '.sb-fmodel', title: 'Model', text: 'As written on the lock: Lock Ultra, Lock Pro or Lock.'},
    {id: 'switchbot.add', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '#sb-add', title: 'Add', text: 'Adds the lock; it shows as silent until its bridge reports.'},
    {id: 'switchbot.del', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '#sb-list [data-del]', title: 'Delete', text: 'Takes the lock out of the house after a question; the bridge keeps running until you stop it.'},
  ],
  addMenu: [{id: 'switchbot', label: i18n('SwitchBot lock'), hint: i18n('A Bluetooth lock, through a bridge on a computer near it'),
             button: i18n('Open'), onClick: open}],
});
})();
