// Family phones (Yash! app): pair by one QR, see who is connected, ring or revoke a phone.
(function () {
const MID = '__family__';
const esc = Home.escapeHtml;

function when(ms){
  if(!ms) return '\u2014';
  const d = new Date(ms);
  if(Home.when) return Home.when(ms);
  return d.toLocaleDateString(undefined,{day:'2-digit',month:'2-digit'}) + ' ' +
         d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'});
}


function open(){ Home.openTab('p:family'); }

// The Family page, opened by the header button: a card per phone, then the add card and the app card
let pageBox = null;
async function renderPage(box){
  pageBox = box;
  await load();
}

let phonesNow = [];

function dotOf(p){
  return `<i style="display:inline-block;width:12px;height:12px;border-radius:50%;background:${
    p.home === true ? 'var(--ok,#2e9e5b)' : p.home === false ? 'var(--dim)' : 'transparent'};border:1px solid var(--dim)"
    title="${esc(i18n(p.home === true ? 'at home' : p.home === false ? 'away' : 'not known yet'))}"></i>`;
}

// a small square per phone; a click opens the window with everything about it
function phoneTile(p){
  // green tint: at home; plain: away; dashed: not known yet
  const tint = p.home === true ? 'background:linear-gradient(160deg,#17361f,var(--card));border-color:#2e6b44;'
    : p.home === false ? 'opacity:.75;' : 'border-style:dashed;';
  return `<div class="card pface" data-phone="${esc(p.uid)}" style="${tint}min-height:110px;align-items:center;text-align:center;justify-items:center">
    <div style="font-size:15px;display:flex;align-items:center;gap:8px">${dotOf(p)}<b>${esc(p.name||p.uid)}</b></div>
    <div class="bindcaption">${esc(i18n(p.home === true ? 'at home' : p.home === false ? 'away' : 'not known yet'))}${
      p.update ? ' \u00b7 ' + i18n('update available') : ''}${p.state !== 'active' ? ' \u00b7 ' + esc(i18n(p.state)) : ''}</div>
  </div>`;
}

function openPhone(uid){
  const p = phonesNow.find(x => x.uid === uid);
  if(!p) return;
  const info = `${i18n('last ring ack')} ${esc(when(p.ack))}${
    p.link ? ' \u00b7 ' + esc(i18n(p.link)) : ''}${
    p.live ? ' \u00b7 ' + i18n('reachable now') : p.cloud ? (p.token ? '' : ' \u00b7 ' + i18n('no push token')) : ' \u00b7 ' + i18n('not reachable now')}${
    (p.app ? ' \u00b7 ' + i18n('app') + ' ' + esc(p.app) : '') + (p.update ? ' \u00b7 ' + i18n('update available') : '') + (p.server ? '' : ' \u00b7 ' + i18n('only in Firebase: a leftover'))}${
    p.old_app ? ' \u00b7 ' + i18n('paired without its id: New QR (with an updated app) stops duplicates') : ''}`;
  Home.openModal(`
    <div class="head"><button class="closebtn" onclick="Home.closeModal()">\u00d7</button>
      <h2>${dotOf(p)} ${esc(p.name||p.uid)}</h2><div class="sub">${esc(i18n(p.state))}</div></div>
    <div class="body"><section>
      <div class="bindcaption">${info}</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px">
      ${p.state==='active' ? `<button data-rights>${i18n('Rights')}</button>
      ${p.server ? `<button data-repair>${i18n('New QR')}</button>` : ''}
      ${p.update ? `<button data-update>${i18n('Update app')}</button>` : ''}
      <button data-ring>${i18n('Ring')}</button>
      <button data-stop>${i18n('Stop')}</button>` : ''}
      <button data-revoke style="color:var(--bad)">${i18n('Delete')}</button></div>
      ${p.server ? `<div class="row" style="margin-top:12px"><span class="rlabel">${esc(i18n('Also at home when'))}<br><span class="bindcaption">${
        esc(i18n('its Wi-Fi presence device says so (a phone of the cloud has no live link)'))}</span></span>
        <select data-presence><option value="">${esc(i18n('no device'))}</option></select></div>` : ''}
    </section></div>`, MID + 'phone');
  const md = document.getElementById('md');
  const on = (sel, fn) => { const b = md.querySelector(sel); if(b) b.onclick = () => fn(b); };
  on('[data-rights]', () => openRights(uid));
  const sel = md.querySelector('[data-presence]');
  if(sel){
    Home.api('/api/family/presence/devices').then(list => {
      sel.innerHTML += list.map(d => `<option value="${esc(d.id)}"${d.id === p.presence ? ' selected' : ''}>${esc(d.name)}</option>`).join('');
      if(p.presence && !list.some(d => d.id === p.presence)) sel.innerHTML += `<option value="${esc(p.presence)}" selected>${esc(p.presence)}</option>`;
    }).catch(() => {});
    sel.onchange = async () => {
      try { await Home.api(`/api/family/${encodeURIComponent(uid)}/presence`, {method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({device: sel.value})}); p.presence = sel.value; Home.toast(i18n('Saved')); }
      catch(e){ Home.toast(e.message || String(e)); }
    };
  }
  on('[data-repair]', () => {
    if(confirm(i18n('A new QR for this phone? Its current link stops working at once; its rights stay.') + '\n' + uid)) pair(uid);
  });
  on('[data-update]', b => act('/api/family/update', uid, b));
  on('[data-ring]', b => act('/api/family/ring', uid, b));
  on('[data-stop]', b => act('/api/family/stop', uid, b));
  on('[data-revoke]', b => {
    if(confirm(i18n('Delete this phone? Its token, its widgets and the rules that ring it go; it can be paired again.') + '\n' + uid))
      act('/api/family/revoke', uid, b).then(() => Home.closeModal());
  });
}

async function load(){
  refreshPhones();
  const box = pageBox;
  if(!box || !box.isConnected) return;
  let phones;
  try { phones = await Home.api('/api/family'); }
  catch(e){ box.innerHTML = `<div class="bindcaption" style="color:var(--bad)">${esc(e.message||e)}</div>`; return; }
  if(!box.isConnected) return;
  phonesNow = phones;
  const names = a => a.map(p => p.name || p.uid).join(', ') || i18n('nobody');
  const status = phones.length ? i18n('At home now: {0}. Away: {1}.', names(phones.filter(p => p.home === true)), names(phones.filter(p => p.home === false)))
    : i18n('No one in the family yet.');
  box.innerHTML = `<section><div class="row"><span class="rlabel"><b>${esc(status)}</b></span></div>${phones.map(memberRow).join('')}</section>
    <section><h3>+ ${esc(i18n('Add a family member'))}</h3>
      <div class="row" style="flex-wrap:wrap;gap:8px"><input class="askin" id="fam-name" data-autosave placeholder="${esc(i18n('Name, in any language'))}" style="flex:1;min-width:200px">
        <label style="display:flex;align-items:center;gap:6px" title="${esc(i18n('Off: the phone sees nothing until you give it devices in Rights.'))}"><input type="checkbox" id="fam-full"> ${esc(i18n('Full access'))}</label>
        <button class="dlg-btn primary" id="fam-go">${esc(i18n('Create QR'))}</button></div>
      <div class="bindcaption">${esc(i18n('The QR is scanned with the new phone\'s camera: it opens the Yash! app and connects it.'))}</div>
      <div class="bindcaption" id="fam-add-out"></div></section>`;
  document.getElementById('fam-go').onclick = () => pair();
  box.querySelectorAll('[data-member]').forEach(r => {
    const uid = r.dataset.member, p = phonesNow.find(x => x.uid === uid), b = k => r.querySelector(`[data-fm="${k}"]`);
    b('rename').onclick = async () => {
      const name = ((await Home.askText(i18n('Name:'), p.name || uid)) || '').trim();
      if(!name || name === p.name) return;
      try { await Home.api('/api/family/' + encodeURIComponent(uid) + '/name', {method: 'PUT', body: JSON.stringify({name})}); load(); }
      catch(e){ alert(e.message || e); }
    };
    b('rights').onclick = () => openRights(uid);
    if(b('qr')) b('qr').onclick = () => { if(confirm(i18n('A new QR for this phone? Its current link stops working at once; its rights stay.') + '\n' + (p.name || uid))) pair(uid); };
    b('more').onclick = () => openPhone(uid);
    b('revoke').onclick = () => {
      if(confirm(i18n('Remove {0} from the family? The phone\'s token, its widgets and the rules that ring it go; it can be added again.', p.name || uid)))
        act('/api/family/revoke', uid, b('revoke'));
    };
  });
}

// a member: its name, at home or away (and when it was seen), its phone's link and app; what can be done with it
function memberRow(p){
  const st = [i18n(p.home === true ? 'at home' : p.home === false ? 'away' : 'not known yet'), p.home_ts ? i18n('seen {0}', when(p.home_ts)) : '',
              p.link ? i18n(p.link) : '', p.app ? i18n('app') + ' ' + p.app : '', p.update ? i18n('update available') : '',
              p.state !== 'active' ? i18n(p.state) : ''].filter(Boolean).join(' \u00b7 ');
  return `<div class="row" data-member="${esc(p.uid)}" style="flex-wrap:wrap"><span class="rlabel" style="display:flex;gap:10px;align-items:center">${dotOf(p)}<span><b>${esc(p.name || p.uid)}</b><br><span class="bindcaption">${esc(st)}</span></span></span>
    <span style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
      <button class="dlg-btn" data-fm="rename">${esc(i18n('Rename'))}</button>
      <button class="dlg-btn" data-fm="rights">${esc(i18n('Rights'))}</button>
      ${p.server ? `<button class="dlg-btn" data-fm="qr">${esc(i18n('New QR'))}</button>` : ''}
      <button class="dlg-btn" data-fm="more">${esc(i18n('Ring\u2026'))}</button>
      <button class="dlg-btn danger" data-fm="revoke">${esc(i18n('Remove'))}</button></span></div>`;
}

// Settings -> Family -> App: the Yash! app, built on the build server and signed with the house's key
function renderApp(box){
  box.innerHTML = `<section><div class="row"><span class="rlabel">${esc(i18n('The app'))}<br><span class="bindcaption">${esc(i18n('The Yash! app for the family phones; a phone told of a newer one updates itself.'))}</span></span>
      <span class="rval" id="fam-app">\u2014</span></div>
    <div class="stx-actions"><button class="dlg-btn primary" id="fam-app-build">${esc(i18n('Build the app'))}</button></div>
    <pre class="bindcaption" id="fam-app-log" style="white-space:pre-wrap"></pre>
    <div class="row" style="flex-wrap:wrap;gap:8px"><span class="rlabel">${esc(i18n('Address for the phones'))}<br><span class="bindcaption" id="fam-srv-note"></span></span>
      <span class="rval" style="display:flex;gap:6px;flex-wrap:wrap"><input id="fam-srv" style="min-width:240px">
        <button class="dlg-btn" id="fam-srv-save">${esc(i18n('Save'))}</button></span></div></section>`;
  document.getElementById('fam-app-build').onclick = buildApp;
  appState();
  const inp = document.getElementById('fam-srv'), note = document.getElementById('fam-srv-note');
  const show = st => {
    inp.value = st.server_url || ''; inp.placeholder = st.server_url_default || '';
    note.textContent = i18n('It goes into every pairing QR. Empty: this house on its network ({0}); a name over https reaches the phones away from home too.', st.server_url_default || '?')
      + (st.server_url_warning ? ' \u00b7 ' + i18n('Away from home the phones cannot reach this address.') : '');
  };
  Home.api('/api/family/settings').then(show).catch(() => {});
  document.getElementById('fam-srv-save').onclick = async () => {
    try { show(await Home.api('/api/family/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({server_url: inp.value})}));
      Home.toast(i18n('Saved')); }
    catch(e){ Home.toast(e.message || String(e)); }
  };
}

// Build the app: the build server's app builder, signed with the house's key; the state is polled here
async function appState(){
  const box = document.getElementById('fam-app');
  if(!box || !box.isConnected) return;
  let s;
  try { s = await Home.api('/api/family/app/build'); } catch(e){ box.textContent = e.message || e; return; }
  const running = !!s.busy;
  box.innerHTML = s.apk ? [`<a href="/plugins/family/yash.apk">yash.apk</a>`, s.apk_name ? esc(`${s.apk_name} (${s.apk_code})`) : '',
    esc(when(s.apk_time * 1000))].filter(Boolean).join(' \u00b7 ') : esc(i18n('not built yet'));
  if(running) box.innerHTML += ` \u00b7 ${esc(i18n('building'))}${s.progress != null ? ' ' + s.progress + '%' : ''}`;
  if(s.state === 'error') box.innerHTML += ` \u00b7 <span style="color:var(--bad)">${esc(s.error || i18n('failed'))}</span>`;
  const bb = document.getElementById('fam-app-build');
  if(bb) bb.disabled = running;
  const lg = document.getElementById('fam-app-log');
  if(lg) lg.textContent = (running || s.state === 'error') ? (s.log || []).slice(-3).join('\n') : '';
  if(running) setTimeout(appState, 3000);
}

async function buildApp(){
  const btn = document.getElementById('fam-app-build');
  btn.disabled = true;
  try { await Home.api('/api/family/app/build', {method: 'POST'}); }
  catch(e){ alert(e.message || e); }
  appState();
}

async function act(path, name, btn){
  btn.disabled = true;
  try { await Home.api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})}); }
  catch(e){ alert(e.message||e); }
  btn.disabled = false;
  load();
}

// The pairing QR: a window of its own (a card is too narrow for it); the cards refresh when it closes
async function pair(again){
  const nameIn = document.getElementById('fam-name');
  const name = again ? ((phonesNow.find(x => x.uid === again) || {}).name || again) : (nameIn ? nameIn.value.trim() : '');
  const out = document.getElementById('fam-add-out');
  if(out) out.textContent = i18n('Creating\u2026');
  try{
    const full = document.getElementById('fam-full');
    const r = await Home.api('/api/family/pair', {method:'POST', headers:{'Content-Type':'application/json'},
                                                  body: JSON.stringify({name, uid: again || '', full: !again && !!(full && full.checked)})});
    const src = r.png ? 'data:image/png;base64,' + r.png : await qrImage(r.text);
    if(out) out.textContent = '';
    Home.openModal(`
      <div class="head"><button class="closebtn" onclick="Home.closeModal()">\u00d7</button><h2>${esc(name)}</h2></div>
      <div class="body"><section>
        <div style="background:#fff;padding:12px;border-radius:12px;display:inline-block">
          <img src="${src}" style="width:320px;max-width:80vw;image-rendering:pixelated"></div>
        <div class="bindcaption">${i18n('Scan with the phone camera: the page opens the app and connects it. Valid for one hour; close this window after the scan.')}</div>
      </section></div>`, MID + 'qr', {onClose: load});
  }catch(e){
    if(out) out.innerHTML = `<span style="color:var(--bad)">${esc(e.message||e)}</span>`;
    else alert(e.message || e);
  }
}

// Rights per name: full access, or per device hidden / view / control (checked on the server)
async function openRights(uid){
  let r;
  try { r = await Home.api('/api/family/' + encodeURIComponent(uid) + '/rights'); }
  catch(e){ alert(e.message||e); return; }
  const devs = (Home.devices()||[]).filter(d => !d.unsupported).slice().sort((a,b) => String(a.name).localeCompare(String(b.name)));
  const opt = (v, cur, label) => `<option value="${v}"${cur===v?' selected':''}>${label}</option>`;
  Home.openModal(`
    <div class="head">
      <button class="closebtn" onclick="Home.closeModal()">\u00d7</button>
      <h2>${i18n('Rights')}: ${esc(r.name||uid)}</h2>
      <div class="sub">${i18n('What this phone sees and may switch. Hidden devices do not reach the phone at all.')}</div>
    </div>
    <div class="body"><section>
      <label class="row"><input type="checkbox" id="fr-full" ${r.full?'checked':''}> <span class="rlabel">${i18n('Full access')}</span></label>
      <div id="fr-list" style="${r.full?'opacity:.4;pointer-events:none':''}">
        <div class="row" style="gap:6px">
          <button data-all="">${i18n('all hidden')}</button><button data-all="view">${i18n('all view')}</button><button data-all="control">${i18n('all control')}</button>
        </div>
        ${devs.map(d => `<div class="row"><span class="rlabel">${esc(d.name)}</span>
          <select data-dev="${esc(d.id)}">${opt('', r.devices[d.id]||'', i18n('hidden'))}${opt('view', r.devices[d.id]||'', i18n('view'))}${opt('control', r.devices[d.id]||'', i18n('control'))}</select></div>`).join('')}
      </div>
      <button class="primary" id="fr-save" style="margin-top:10px">${i18n('Save')}</button>
      <div id="fr-out" class="bindcaption"></div>
    </section></div>`, MID + 'rights');
  const md = document.getElementById('md');
  md.querySelector('#fr-full').onchange = e => {
    const l = md.querySelector('#fr-list'); l.style.opacity = e.target.checked ? .4 : 1; l.style.pointerEvents = e.target.checked ? 'none' : '';
  };
  md.querySelectorAll('[data-all]').forEach(b => b.onclick = () => md.querySelectorAll('[data-dev]').forEach(s => s.value = b.dataset.all));
  md.querySelector('#fr-save').onclick = async () => {
    const devices = {};
    md.querySelectorAll('[data-dev]').forEach(s => { if (s.value) devices[s.dataset.dev] = s.value; });
    try {
      await Home.api('/api/family/' + encodeURIComponent(uid) + '/rights', {method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({full: md.querySelector('#fr-full').checked, devices})});
      Home.closeModal(); load();
    } catch(e){ md.querySelector('#fr-out').textContent = e.message||e; }
  };
}

// Without Firebase the server sends the QR's text and the browser draws it (qrcode-generator, MIT)
let qrLib = null;
function qrImage(text){
  qrLib = qrLib || new Promise((ok, fail) => {
    const s = document.createElement('script');
    s.src = '/plugins/family/static/qrcode.js'; s.onload = () => ok(window.qrcode); s.onerror = () => fail(new Error('qrcode.js'));
    document.head.appendChild(s);
  });
  return qrLib.then(qrcode => { const q = qrcode(0, 'L'); q.addData(text); q.make(); return q.createDataURL(5, 8); });
}

let phonesForRules = [];
function refreshPhones(){ Home.api('/api/family/names').then(p => { phonesForRules = p; }).catch(() => {}); }

// A button in the header, before Log out: the Family window with a card per phone
function headerButton(box){
  const btn = document.createElement('button');
  btn.className = 'dlg-btn'; btn.dataset.fam = 'open';
  btn.innerHTML = (Home.icon ? Home.icon('person', 18) : '');
  btn.title = i18n('Family');
  btn.onclick = open;
  box.append(btn);
  Home.api('/api/family').then(phones => {
    const n = phones.filter(p => p.home === true).length;
    if(phones.length){
      btn.innerHTML = (Home.icon ? Home.icon('person', 18) : '') + `<span> ${n}/${phones.length}</span>`;
      btn.title = i18n('Family') + ': ' + i18n('{0} of {1} at home', n, phones.length);
    }
  }).catch(() => {});
}

HomePlugins.register({
  settings: [{subject: 'family', title: 'Family', tabs: [{id: 'members', title: 'Family', render: renderPage}, {id: 'app', title: 'App', render: renderApp}]}],
  tabs: [{id: 'family', label: 'Family phones', hidden: true, render: renderPage}],
  help: [
    {id: 'family.open', screen: 'header', sel: '[data-fam="open"]', title: 'Family', text: 'The family phones: who is at home, their rights, a new phone by QR. Its count is who is home of all.'},
    {id: 'family.name', screen: 'settings:family', sel: '#fam-name', title: 'Name', text: 'The member\'s name in any language: the header, voice answers and rules show it.'},
    {id: 'family.rename', screen: 'settings:family', sel: '[data-fm="rename"]', title: 'Rename', text: 'Changes the name people see; the phone stays connected.'},
    {id: 'family.mrights', screen: 'settings:family', sel: '[data-fm="rights"]', title: 'Rights', text: 'What this member\'s phone sees and may switch.'},
    {id: 'family.mqr', screen: 'settings:family', sel: '[data-fm="qr"]', title: 'New QR', text: 'Pairs the phone again: its current link stops at once; its rights stay.'},
    {id: 'family.more', screen: 'settings:family', sel: '[data-fm="more"]', title: 'Ring', text: 'Ring the phone to find it, stop it, or tell it to update the app.'},
    {id: 'family.remove', screen: 'settings:family', sel: '[data-fm="revoke"]', title: 'Remove', text: 'After a question the phone\'s token, its widgets and the rules that ring it go; it can be added again.'},
    {id: 'family.full', screen: 'settings:family', sel: '#fam-full', title: 'Full access', text: 'On: the new phone sees and switches everything. Off: it sees nothing until it gets devices in Rights.'},
    {id: 'family.go', screen: 'settings:family', sel: '#fam-go', title: 'Create QR', text: 'A QR for the phone\'s camera: it opens the app and connects it; valid for one hour.'},
    {id: 'family.appbuild', screen: 'settings:family/app', sel: '#fam-app-build', title: 'Build the app', text: 'Builds the Yash! app on the build server, signed with the house\'s key; a phone told of the update installs it.'},
    {id: 'family.srv', screen: 'settings:family/app', sel: '#fam-srv', at: 'label', title: 'Address for the phones', text: 'Empty: this house on its own network. A name over https (behind a proxy) lets the phones reach it away from home too.'},
    {id: 'family.srvsave', screen: 'settings:family/app', sel: '#fam-srv-save', title: 'Save', text: 'The next pairing QR holds the new address; phones paired before keep theirs until a New QR.'},
    {id: 'family.rights', screen: 'family-phone', screenName: 'Family phone', sel: '[data-rights]', title: 'Rights', text: 'What this phone sees and may switch.'},
    {id: 'family.repair', screen: 'family-phone', screenName: 'Family phone', sel: '[data-repair]', title: 'New QR', text: 'Pairs this phone again: its current link stops at once; its rights stay.'},
    {id: 'family.update', screen: 'family-phone', screenName: 'Family phone', sel: '[data-update]', title: 'Update app', text: 'Tells the phone to install the newer app.'},
    {id: 'family.ring', screen: 'family-phone', screenName: 'Family phone', sel: '[data-ring]', title: 'Ring', text: 'Rings the phone now, to find it.'},
    {id: 'family.stop', screen: 'family-phone', screenName: 'Family phone', sel: '[data-stop]', title: 'Stop', text: 'Stops its ringing.'},
    {id: 'family.revoke', screen: 'family-phone', screenName: 'Family phone', sel: '[data-revoke]', title: 'Delete', text: 'After a question its token, its widgets and the rules that ring it go; it can be paired again.'},
    {id: 'family.rfull', screen: 'family-rights', screenName: 'Rights', sel: '#fr-full', at: 'label', title: 'Full access', text: 'Every device, seen and switched; the list below does not count then.'},
    {id: 'family.rall', screen: 'family-rights', screenName: 'Rights', sel: '[data-all]', title: 'All at once', text: 'Sets every device below to hidden, view or control; Save keeps it.'},
    {id: 'family.rdev', screen: 'family-rights', screenName: 'Rights', sel: '#fr-list [data-dev]', title: 'Per device', text: 'Hidden: it never reaches the phone. View: seen, not switched. Control: seen and switched.'},
    {id: 'family.rsave', screen: 'family-rights', screenName: 'Rights', sel: '#fr-save', title: 'Save', text: 'Saves the rights; the server checks them on every command of the phone.'},
    {id: 'family.rbphone', screen: 'rule-builder', screenName: 'Rule builder', sel: '#rb-fam-phone', title: 'Phone', text: 'The family phone the rule rings.'},
    {id: 'family.rbcmd', screen: 'rule-builder', screenName: 'Rule builder', sel: '#rb-fam-cmd', title: 'Do', text: 'Ring, or stop its ringing.'},
    {id: 'family.rbsecs', screen: 'rule-builder', screenName: 'Rule builder', sel: '#rb-fam-secs', title: 'Seconds', text: 'How long it rings: 5 to 300 seconds.'},
  ],
  header: [{slot: 'conn', render: headerButton}],
  hides: [d => d.transport === 'family' && String(d.id).indexOf('person_') === 0],   // shown on the Family card only
  addMenu: [{id:'family', label: i18n('Family phone'), hint: i18n('Pair the Yash! app by QR'), button: i18n('Open'), onClick: open}],
  ruleActions: [{
    type: 'family-ring',                                // = describe().type in __init__.py
    label: i18n('Ring a phone'),
    html(t){
      refreshPhones();
      const names = phonesForRules.map(p => p.uid), label = n => (phonesForRules.find(p => p.uid === n) || {}).name || n;
      if(t && t.target && !names.includes(t.target)) names.unshift(t.target);
      const stop = t && t.cmd === 'stop';
      return `<div class="rb-row"><label>${i18n('Phone')}</label><select id="rb-fam-phone">${names.map(n =>
          `<option value="${esc(n)}"${t && t.target === n ? ' selected' : ''}>${esc(label(n))}</option>`).join('')}</select></div>
        <div class="rb-row"><label>${i18n('Do')}</label><select id="rb-fam-cmd">
          <option value="ring"${stop ? '' : ' selected'}>${i18n('Ring')}</option>
          <option value="stop"${stop ? ' selected' : ''}>${i18n('Stop ringing')}</option></select></div>
        <div class="rb-row"><label>${i18n('Seconds')}</label>
          <input id="rb-fam-secs" type="number" min="5" max="300" value="${t && t.secs ? t.secs : 60}"></div>`;
    },
    read(){
      const target = document.getElementById('rb-fam-phone').value;
      if(!target) throw new Error(i18n('Pair a phone first'));
      return {action: 'ring', target, cmd: document.getElementById('rb-fam-cmd').value,
              secs: Number(document.getElementById('rb-fam-secs').value) || 60};
    },
    chip: t => `${esc(i18n(t.cmd === 'stop' ? 'stop ringing' : 'ring'))} ${esc(t.name || t.target || '')}`,
    title: t => `${i18n(t.cmd === 'stop' ? 'stop ringing' : 'ring')} ${t.name || t.target || ''}`,
  }],
});
refreshPhones();
})();
