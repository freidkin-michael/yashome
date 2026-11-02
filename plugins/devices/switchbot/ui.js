// SwitchBot: the locks behind a BLE bridge (Add -> SwitchBot lock).
(function () {
const MID = '__switchbot__';
const esc = Home.escapeHtml;
const MODELS = {lock_ultra: 'Lock Ultra', lock_pro: 'Lock Pro', lock: 'Lock'};

async function open(){
  Home.openModal(`
    <div class="head">
      <button class="closebtn" onclick="Home.closeModal()">\u00d7</button>
      <h2>${i18n('SwitchBot lock')}</h2>
      <div class="sub">${i18n('The lock talks Bluetooth only: a bridge on a host near it (see the plug-in README) links it to the house. Add the lock here with the same id as the bridge.')}</div>
    </div>
    <div class="body"><section>
      <div id="sb-list"><div class="bindcaption">${i18n('Loading\u2026')}</div></div>
      <h3>${i18n('Add a lock')}</h3>
      <div class="row"><input id="sb-id" placeholder="${i18n('id, latin letters: front_lock')}" style="flex:1" autocapitalize="off">
        <input id="sb-name" placeholder="${i18n('name')}" style="flex:1"></div>
      <div class="row"><select id="sb-model">${Object.entries(MODELS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select></div>
      <button class="primary" id="sb-add" style="margin-top:8px">${i18n('Add')}</button>
      <div id="sb-out" class="bindcaption"></div>
    </section></div>`, MID);
  document.getElementById('sb-add').onclick = add;
  await load();
}

function line(k){
  const st = k.online ? [k.state, k.battery != null ? k.battery + '%' : '', k.door].filter(Boolean).join(' \u00b7 ')
                      : i18n('silent: the bridge or the lock');
  return `<div class="row"><span class="rlabel"><b>${esc(k.name)}</b>
      <span style="color:${k.online ? 'var(--dim)' : 'var(--bad)'};font-size:12px"> \u00b7 ${esc(k.id)} \u00b7 ${esc(st)}</span></span>
      <button data-del="${esc(k.id)}" style="color:var(--bad)">${i18n('Delete')}</button></div>`;
}

async function load(){
  const box = document.getElementById('sb-list');
  let list;
  try { list = await Home.api('/api/switchbot'); }
  catch(e){ if(box) box.innerHTML = `<div class="bindcaption" style="color:var(--bad)">${esc(e.message||e)}</div>`; return; }
  if(Home.modalId() !== MID || !box) return;
  box.innerHTML = list.length ? list.map(line).join('') : `<div class="bindcaption">${i18n('No locks yet.')}</div>`;
  box.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if(!confirm(i18n('Delete the lock {0} from the house? The bridge keeps running until you stop it.', b.dataset.del))) return;
    try { await Home.api('/api/esphome/remove', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                 body: JSON.stringify({id: b.dataset.del})}); }
    catch(e){ alert(e.message||e); }
    load();
  });
}

async function add(){
  const v = id => document.getElementById(id).value.trim();
  const id = v('sb-id').toLowerCase();
  const out = document.getElementById('sb-out');
  try {
    await Home.api('/api/switchbot/locks/' + encodeURIComponent(id), {method: 'PUT',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: v('sb-name'), model: v('sb-model')})});
    out.textContent = '';
    ['sb-id', 'sb-name'].forEach(i => { document.getElementById(i).value = ''; });
  } catch(e){ out.textContent = e.message || e; }
  load();
}

HomePlugins.register({
  help: [
    {id: 'switchbot.id', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '#sb-id', title: 'Id', text: 'Latin letters, the same id the bridge was started with: that is how the house finds the lock.'},
    {id: 'switchbot.name', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '#sb-name', title: 'Name', text: 'What the dashboard calls the lock.'},
    {id: 'switchbot.model', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '#sb-model', title: 'Model', text: 'As written on the lock: Lock Ultra, Lock Pro or Lock.'},
    {id: 'switchbot.add', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '#sb-add', title: 'Add', text: 'Adds the lock; it shows as silent until its bridge reports.'},
    {id: 'switchbot.del', screen: 'switchbot', screenName: 'SwitchBot lock', sel: '#sb-list [data-del]', title: 'Delete', text: 'Takes the lock out of the house after a question; the bridge keeps running until you stop it.'},
  ],
  addMenu: [{id: 'switchbot', label: i18n('SwitchBot lock'), hint: i18n('A Bluetooth lock through a bridge on a host near it'),
             button: i18n('Open'), onClick: open}],
});
})();
