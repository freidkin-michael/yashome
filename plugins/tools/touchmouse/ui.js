// Touchmouse: the receivers a phone or a panel may use as a touchpad (Add -> Touchmouse).
(function () {
const MID = '__touchmouse__';
const esc = Home.escapeHtml;

async function open(){
  Home.openModal(`
    <div class="head">
      <button class="closebtn" onclick="Home.closeModal()">\u00d7</button>
      <h2>${i18n('Touchmouse')}</h2>
      <div class="sub">${i18n('A phone or a wall panel moves the pointer of a TV box or a computer. Each one needs the receiver running (see the plug-in README).')}</div>
    </div>
    <div class="body"><section>
      <div id="tm-list"><div class="bindcaption">${i18n('Loading\u2026')}</div></div>
      <h3>${i18n('Add a receiver')}</h3>
      <div class="row"><input id="tm-id" placeholder="${i18n('id, latin letters: tv-box')}" style="flex:1" autocapitalize="off">
        <input id="tm-name" placeholder="${i18n('name')}" style="flex:1"></div>
      <div class="row"><input id="tm-host" placeholder="${i18n('address: 192.0.2.30')}" style="flex:2" autocapitalize="off">
        <input id="tm-port" type="number" value="7709" style="width:90px">
        <select id="tm-os"><option value="linux">Linux</option><option value="android">Android</option></select></div>
      <button class="primary" id="tm-add" style="margin-top:8px">${i18n('Add')}</button>
      <div id="tm-out" class="bindcaption"></div>
    </section></div>`, MID);
  document.getElementById('tm-add').onclick = add;
  await load();
}

async function load(){
  const box = document.getElementById('tm-list');
  let list;
  try { list = await Home.api('/api/touchmouse'); }
  catch(e){ if(box) box.innerHTML = `<div class="bindcaption" style="color:var(--bad)">${esc(e.message||e)}</div>`; return; }
  if(Home.modalId() !== MID || !box) return;
  box.innerHTML = list.length ? list.map(r => `<div class="row"><span class="rlabel"><b>${esc(r.name)}</b>
      <span style="color:var(--dim);font-size:12px"> \u00b7 ${esc(r.host)}:${esc(r.port)} \u00b7 ${esc(r.os)}${r.device ? ' \u00b7 ' + i18n('a device') : r.found ? ' \u00b7 ' + i18n('found in the network') : ''}</span></span>
      ${r.device || r.found ? '' : `<button data-del="${esc(r.id)}" style="color:var(--bad)">${i18n('Delete')}</button>`}</div>`).join('')
    : `<div class="bindcaption">${i18n('No receivers yet.')}</div>`;
  box.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    try { await Home.api('/api/touchmouse/receivers/' + encodeURIComponent(b.dataset.del), {method: 'DELETE'}); }
    catch(e){ alert(e.message||e); }
    load();
  });
}

async function add(){
  const v = id => document.getElementById(id).value.trim();
  const body = {id: v('tm-id').toLowerCase(), name: v('tm-name'), host: v('tm-host'), port: Number(v('tm-port')) || 7709, os: v('tm-os')};
  const out = document.getElementById('tm-out');
  try {
    await Home.api('/api/touchmouse/receivers/' + encodeURIComponent(body.id), {method: 'PUT',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    out.textContent = '';
    ['tm-id', 'tm-name', 'tm-host'].forEach(id => { document.getElementById(id).value = ''; });
  } catch(e){ out.textContent = e.message || e; }
  load();
}

HomePlugins.register({
  help: [
    {id: 'touchmouse.id', screen: 'touchmouse', screenName: 'Touchmouse', sel: '#tm-id', title: 'Id', text: 'Latin letters: the receiver\'s id in the house.'},
    {id: 'touchmouse.name', screen: 'touchmouse', screenName: 'Touchmouse', sel: '#tm-name', title: 'Name', text: 'What a phone or a panel shows for this receiver.'},
    {id: 'touchmouse.host', screen: 'touchmouse', screenName: 'Touchmouse', sel: '#tm-host', title: 'Address', text: 'The address of the computer or TV box the receiver runs on.'},
    {id: 'touchmouse.port', screen: 'touchmouse', screenName: 'Touchmouse', sel: '#tm-port', title: 'Port', text: 'The receiver\'s port: 7709 unless it was started with another.'},
    {id: 'touchmouse.os', screen: 'touchmouse', screenName: 'Touchmouse', sel: '#tm-os', title: 'System', text: 'The system of that computer or box: Linux or Android.'},
    {id: 'touchmouse.add', screen: 'touchmouse', screenName: 'Touchmouse', sel: '#tm-add', title: 'Add', text: 'Adds the receiver; a phone or a panel can then move its pointer.'},
    {id: 'touchmouse.del', screen: 'touchmouse', screenName: 'Touchmouse', sel: '#tm-list [data-del]', title: 'Delete', text: 'Takes the receiver out; one that comes with a device has no Delete: it goes with its device.'},
  ],
  addMenu: [{id: 'touchmouse', label: i18n('Touchmouse'), hint: i18n('A phone as the touchpad of a TV box or a computer'),
             button: i18n('Open'), onClick: open}],
});
})();
