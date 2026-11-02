// Settings: a full-screen section: the menu of groups on the left (search on top) beside the open page; a group's subjects
// are the tabs on top of its page, a subject's own tabs a row below. Every subject has its URL #settings/<subject>/<tab>
// (browser Back works). A plug-in's page: settings: [{subject?, title, expl, icon?, tabs?, render, status?}]
const ST_GROUPS = [['overview', '', 'heart'], ['house', 'House', 'home'], ['devices', 'Devices', 'other'], ['auto', 'Automation', 'switch'],
                   ['voice', '', 'mic'], ['screen', 'This screen', 'screen'], ['service', 'Maintenance', 'archive'], ['other', 'Other', 'puzzle']];
const stSlug = t => String(t || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'page';
// the subjects of the menu; core: the core draws a tab of it (tabs: its own several); a plug-in's settings entry names a subject
const ST_SUBJECTS = [
  {id: 'overview', group: 'overview', icon: 'heart', title: 'Overview', expl: 'The house checks itself: what is fine and what needs you', core: true, needs: ['GET /api/health/report']},
  {id: 'rooms', group: 'house', icon: 'map', title: 'Home map', expl: 'The plan of the house; tap a room to rename it, pick its thermometer or delete it'},
  {id: 'family', group: 'house', icon: 'people', title: 'Family', expl: 'The people of the house: phones, rights, the app'},
  {id: 'houselang', group: 'house', icon: 'globe', title: 'House language', expl: 'The language the voice, the clocks and the panels speak', core: true, needs: ['PUT /api/house']},
  {id: 'weather', group: 'house', icon: 'cloud', title: 'Weather', expl: 'The forecast and the weather now: sources and station'},
  {id: 'devices', group: 'devices', icon: 'other', title: 'All devices', expl: 'Every device: where it is, whether it answers; add one', core: true,
   tabs: [{id: 'main', title: 'List'}, {id: 'forgotten', title: 'Forgotten'}]},
  {id: 'zigbee', group: 'devices', icon: 'network', title: 'Zigbee', expl: 'The gateway, its devices, pairing'},
  {id: 'ir', group: 'devices', icon: 'ir', title: 'IR', expl: 'Blasters, remotes, learning, IR appliances'},
  {id: 'tv', group: 'devices', icon: 'tv', title: 'TVs', expl: 'Every TV: address, pairing, inputs, apps'},
  {id: 'panels', group: 'devices', icon: 'screen', title: 'Panels', expl: 'Wall panels: layouts and the builder, binding, firmware'},
  {id: 'nfc', group: 'devices', icon: 'nfc', title: 'NFC tags', expl: 'Tags and cards: what each one starts; add a new one, remove one'},
  {id: 'computers', group: 'devices', icon: 'screen', title: 'Computers and servers', expl: 'The computers of the house and its servers on them: build, speech, big model, yashGen; which TV each one is'},
  {id: 'rules', group: 'auto', icon: 'switch', title: 'Rules', expl: 'What happens when: buttons, sensors, schedules', core: true},
  {id: 'history', group: 'auto', icon: 'clock', title: 'History', expl: 'Everything that happened in the house, and who did it'},
  {id: 'watchdog', group: 'auto', icon: 'shield', title: 'Watchdog', expl: 'The house checks itself: what it puts right, when it forgets a gone device'},
  {id: 'voice', group: 'voice', icon: 'mic', title: 'Voice', expl: 'Wake word, answers, satellites'},
  {id: 'lang',    group: 'screen', icon: 'globe', title: 'Language', expl: 'The language of this screen', core: true},
  {id: 'device',  group: 'screen', icon: 'key', title: 'Sign-in', expl: 'Sign-in of this screen and the app shortcut', core: true},
  {id: 'signin', group: 'screen', icon: 'key', title: 'Sign in from another screen', expl: 'Let another phone, tablet or computer of this network in', core: true, needs: ['POST /api/signin/start']},
  {id: 'backups', group: 'service', icon: 'archive', title: 'Backups', expl: 'Nightly archives of the house\'s settings and data; one now, downloads', core: true, needs: ['GET /api/backups']},
  {id: 'plugins', group: 'service', icon: 'puzzle', title: 'Plug-ins and update', expl: 'What is switched on and what each is; new versions', core: true, needs: ['GET /api/helper/state'],
   tabs: [{id: 'list', title: 'Plug-ins'}, {id: 'update', title: 'Update'}]},
  {id: 'restart', group: 'service', icon: 'switch', title: 'Restart', expl: 'When something stays stuck: every service of the house started anew', core: true, needs: ['POST /api/helper/jobs']},
  {id: 'fwgen', group: 'service', icon: 'chip', title: 'Firmware builder', expl: 'Open the yashGen firmware builder'},
];
const ST_ALIAS = {general: 'houselang', health: 'overview', build: 'computers'};    // pages that were merged: an old link opens where it went
// a page: its subject, the core's tab(s), then each plug-in entry naming it as a tab; one with no tab is not listed.
// An entry naming no subject (a plug-in of someone else) gets a page of its own under "Other".
function ST_PAGES(){
  const pages = ST_SUBJECTS.map(x => ({...x, tabs: x.core ? (x.tabs || [{id: 'main', title: x.coreTitle || x.title}]).map(t => ({...t, core: true})) : [], titles: []}));
  const by = Object.fromEntries(pages.map(x => [x.id, x]));
  for(const e of HomePlugins.settings){
    const m = e._plugin || {};
    if(m.menu === false) continue;
    let pg = by[e.subject];
    if(!pg){
      const id = 'p-' + (m.id || stSlug(e.title));
      pg = by[id] || (by[id] = {id, group: 'other', icon: ICON[m.icon] ? m.icon : 'puzzle', title: e.title || m.name || id, expl: e.expl || '', tabs: [], titles: []});
      if(!pages.includes(pg)) pages.push(pg);
    }
    for(const t of Array.isArray(e.tabs) ? e.tabs : [{id: stSlug(e.tab || e.title), title: e.tab || e.title, render: e.render}]) pg.tabs.push({...t, needs: t.needs || e.needs});
    pg.titles.push(e.title);
  }
  return pages.filter(x => x.tabs.length);
}
// All devices: every device with its kind, how it is connected, its room, whether it answers; a tap opens its window;
// the house's own helpers (who is home, NFC, family, the shower) are folded apart at the end
const stKind = d => (d.ir_appliance ? (/tv/i.test(d.ir_card || '') ? i18n('TV') : /^ac/i.test(d.ir_card || '') ? i18n('Air conditioner') : i18n('IR appliance'))
  : d.category === 'ir' ? i18n('IR blaster') : catLabel(d.category)).replace(/^(.)(?=[a-zа-яё])/u, c => c.toLowerCase());   // i18n: Latin or Russian; a word that opens with an acronym (IR...) keeps its capitals
const stVia = d => ({z2m: 'Zigbee', ir: i18n('IR'), tizen: 'Samsung', webos: 'LG', voice: i18n('voice satellite')})[d.transport]
  || (d.transport === 'mqtt' ? (d.esphome ? 'Wi-Fi (ESPHome)' : 'MQTT') : d.transport || '');
function stDevices(box, quiet){
  const sc = el('section'); box.append(sc);
  if(quiet) sc.dataset.stab = 'main';
  sc.innerHTML = `<div class="stx-actions"><input class="askin stx-dq" type="search" placeholder="${escapeHtml(i18n('Search'))}" style="flex:1;min-width:140px">
    <button class="dlg-btn stx-add">+ ${escapeHtml(i18n('Add device'))}</button></div><div class="stx-dl"></div>`;
  const room = id => (ROOMS.find(r => (r.devices || []).includes(id)) || {}).name || '';
  const row = d => { const st = STATE[d.id], on = !(st && st.online === false);
    return `<div class="row stx-dev" data-id="${escapeHtml(d.id)}" style="cursor:pointer"><span class="rlabel"><span style="color:${on ? 'var(--ok)' : 'var(--bad)'}">\u25cf</span> ${escapeHtml(d.name)}<br>
      <span class="bindcaption">${escapeHtml([stKind(d), stVia(d), room(d.id) || i18n('no room')].filter(Boolean).join(' \u00b7 '))}</span></span><span class="rval">${escapeHtml(on ? '' : i18n('offline'))}</span></div>`; };
  const draw = () => {
    const q = sc.querySelector('.stx-dq').value.trim().toLowerCase();
    const list = DEVICES.filter(d => !q || [d.name, d.id, room(d.id), stKind(d), stVia(d)].join(' ').toLowerCase().includes(q))
      .sort((a, b) => String(a.name).localeCompare(String(b.name), LANG));
    const real = list.filter(d => !d.virtual);                      // a person, Who is home, the tags: on Family and their own page
    sc.querySelector('.stx-dl').innerHTML = real.map(row).join('') || `<div class="bindcaption">${escapeHtml(i18n('Nothing found'))}</div>`;
    sc.querySelectorAll('.stx-dev').forEach(r => r.onclick = () => openModal(r.dataset.id));
  };
  sc.querySelector('.stx-dq').oninput = draw;
  sc.querySelector('.stx-add').onclick = () => openAddDevice();
  draw();
}
// devices that are gone but still stored (a name, a room, a favourite): Forget removes them; a rule naming one stays
function stForgotten(box, quiet){
  const sc = el('section'); box.append(sc);
  if(quiet) sc.dataset.stab = 'forgotten';
  if(!stHere({needs: ['GET /api/devices/forgotten']})){ sc.innerHTML = `<div class="bindcaption">${escapeHtml(i18n('Not available here'))}</div>`; return; }
  const draw = async () => {
    let items;
    try { items = (await api('/api/devices/forgotten')).items; } catch(e){ sc.innerHTML = `<div class="bindcaption">${escapeHtml(e.message || String(e))}</div>`; return; }
    const place = w => w.startsWith('room:') ? (ROOMS.find(r => r.id === w.slice(5)) || {}).name || i18n('a room') : catLabel(w);
    const what = p => { const [k, w = ''] = p.split(/:(.*)/);       // in words: no ids, the room's name, the tab's kind
      if(k === 'binding-source' || k === 'binding-target') return i18n(k === 'binding-source' ? 'a rule\'s trigger' : 'a rule\'s action');
      if(k === 'tab-order' || k === 'tile-size') return i18n(ST_LEFTOVER[k]) + ' ' + place(w);
      if(k === 'room-sensor') return i18n(ST_LEFTOVER[k]) + ' ' + place('room:' + w);
      if(k === 'room' || k === 'panel' || k === 'automation') return i18n(ST_LEFTOVER[k] || k) + (w ? ' ' + w : '');
      return i18n(ST_LEFTOVER[k] || k);
    };
    const words = ps => [...new Set(ps.map(what))].join(' \u00b7 ');
    sc.innerHTML = `<div class="bindcaption" style="margin-bottom:8px">${escapeHtml(i18n('Devices that are gone but still have a name, a room or a place stored. Forget removes all of it; a rule that names one stays until you change it. The watchdog forgets them by itself after the days set on its page.'))}</div>`
      + (items.length ? items.map(f => `<div class="row" style="flex-wrap:wrap"><span class="rlabel">${escapeHtml(f.name)}${f.name !== f.id ? ` <span class="bindcaption">${escapeHtml(f.id)}</span>` : ''}<br>
        <span class="bindcaption">${escapeHtml([words(f.data), f.kept.length ? i18n('Still named by: {0}', [...new Set(f.kept.map(what))].join(', ')) : '', f.data.length ? '' : i18n('Nothing to forget: only a rule or a panel names it. Change that and it is gone.')].filter(Boolean).join(' \u00b7 '))}</span></span>
        <span class="rval">${f.data.length ? `<button class="dlg-btn" data-fg="${escapeHtml(f.id)}">${escapeHtml(i18n('Forget'))}</button>`
          : `<button class="dlg-btn" data-fo="${f.kept.some(p => p.startsWith('panel')) && !f.kept.some(p => !p.startsWith('panel')) ? 'panels' : 'rules'}">${escapeHtml(i18n(f.kept.some(p => !p.startsWith('panel')) ? 'Open the rules' : 'Open the panels'))}</button>`}</span></div>`).join('')
      : `<div class="bindcaption">${escapeHtml(i18n('Nothing is left of gone devices.'))}</div>`);
    sc.querySelectorAll('[data-fo]').forEach(b => b.onclick = () => openSettings(b.dataset.fo));
    sc.querySelectorAll('[data-fg]').forEach(b => b.onclick = async () => {
      b.disabled = true;
      try { await api('/api/devices/' + encodeURIComponent(b.dataset.fg) + '/leftovers', {method: 'DELETE'}); stToast(i18n('Forgotten')); }
      catch(e){ alert(i18n('Error: ') + e.message); }
      await refreshTabs(); draw();
    });
  };
  draw();
}
const ST_LEFTOVER = {'name-override': 'its name', 'channel-names': 'channel names', 'channel-dimrange': 'dim windows', favorite: 'a favourite', room: 'room',
  'tab-order': 'place in a tab', 'tile-size': 'tile size in', 'room-sensor': 'thermometer of a room', binding: 'a rule', automation: 'automation', panel: 'panel'};
// Maintenance -> Plug-ins: one row per plug-in (its switch when the helper runs, its name, version and state, what it is);
// "More" opens what the old About page showed: folder, what it needs, its health rows
function stPluginMeta(id){
  const m = HomePlugins.list.find(x => x.id === id) || (HomePlugins.failed.find(f => f.id === id) ? {id, name: id, failed: HomePlugins.failed.find(f => f.id === id).error || true} : null)
    || {id, name: id, off: true};
  m.label = (PLUGIN_INFO[m.id] || [])[0] || m.name;
  return m;
}
function stPluginRow(id, sw){
  const m = stPluginMeta(id), info = PLUGIN_INFO[id];
  const state = m.off ? i18n('off') : m.failed ? i18n('did not load') : m.uiFailed ? i18n('ui.js failed to load') : (m.version ? m.version + ' \u00b7 ' : '') + i18n('loaded');
  const d = el('div');
  d.innerHTML = `<div class="row"><span class="rlabel">${sw || ''}${escapeHtml(i18n(m.label))}${info ? `<br><span class="bindcaption">${escapeHtml(i18n(info[1]))}</span>` : ''}</span>
      <span class="rval" style="${m.failed || m.uiFailed ? 'color:var(--bad)' : m.off ? 'color:var(--dim)' : ''}">${escapeHtml(state)}</span></div>
    <details class="stx-about" style="margin:-14px 0 2px;padding:0"><summary class="bindcaption" style="padding:2px 0">${escapeHtml(i18n('More'))}</summary><div></div></details>`;
  const det = d.querySelector('details');
  det.addEventListener('toggle', () => { if(det.open && !det.dataset.done){ det.dataset.done = '1'; stAbout(m, det.querySelector('div')); } });
  return d;
}
async function stAbout(m, box){
  const st = m.off ? i18n('switched off') : m.failed ? i18n('did not load') + (typeof m.failed === 'string' ? ': ' + m.failed : '') : m.uiFailed ? i18n('ui.js failed to load') : i18n('loaded');
  const row = (k, v) => `<div class="row"><span class="rlabel">${escapeHtml(i18n(k))}</span><span class="rval">${escapeHtml(v)}</span></div>`;
  box.innerHTML = `<section>${row('Name', i18n(m.label || m.name))}${row('Version', m.version || '\u2014')}${row('Status', st)}${row('Folder', (m.group ? m.group + '/' : '') + m.id)}
    ${(m.requires || []).length ? row('Needs', m.requires.join(', ')) : ''}<h3>${escapeHtml(i18n('Health'))}</h3><div class="stx-health"></div></section>`;
  const hb = box.querySelector('.stx-health');
  if(!stHere({needs: ['GET /api/health/report']})){ hb.previousElementSibling.remove(); hb.remove(); return; }     // restricted: no health page here
  const r = await fetch('/api/health/report', {headers: authHeaders()}).catch(() => null);
  const h = r && r.ok ? await r.json().catch(() => null) : null;
  const mine = h ? h.rows.filter(x => { const id = String(x.id || ''); return id === m.id || id.startsWith(m.id + ':') || id.startsWith(m.id + '-'); }) : [];
  const t = (s, args) => s ? i18n(s, ...(args || [])) : '';
  hb.innerHTML = mine.length ? mine.map(x => `<div class="row"><span class="rlabel"><span style="color:${HEALTH_COLOR[x.state]}">\u25cf</span> ${escapeHtml(t(x.title, x.args))}</span>
      <span class="rval">${escapeHtml(x.state === 'ok' && !x.detail ? i18n('fine') : t(x.detail, x.args))}</span></div>`).join('')
    : `<div class="bindcaption">${escapeHtml(i18n(h ? 'It reports nothing to the health page.' : 'No answer from the house.'))}</div>`;
}
// restricted mode: a page whose routes the mode refuses is shown greyed, «Not available here», and draws nothing
const stOk = needs => !window.RESTRICTED || !Array.isArray(window.RESTRICTED_ROUTES) || (needs || []).every(n => window.RESTRICTED_ROUTES.includes(n));
const stHere = p => stOk(p.needs) && (!(p.tabs || []).length || p.tabs.some(t => t.core || stOk(t.needs)));   // a page: one of its tabs at least
const ST = {page: '', tab: '', dirty: false, index: null, back: ''};
const stPhone = () => matchMedia('(max-width:700px)').matches;
function openSettings(page, tab){                         // the gear, a help line and every "Settings -> X" go here
  const h = '#settings' + (page ? '/' + page + (tab ? '/' + tab : '') : '');
  if(location.hash === h) stRoute(); else location.hash = h;
}
function stLeave(){                                       // unsaved text asks first
  if(ST.dirty && !confirm(i18n('There are unsaved changes on this page. Leave without saving?'))) return false;
  ST.dirty = false; return true;
}
function stRoute(){
  const m = location.hash.match(/^#settings(?:\/([^/]+))?(?:\/([^/]+))?$/);
  if(!m){                                                 // Back out of Settings asks too; a No puts the page back
    if(ST.page && ST.dirty && !stLeave()){ history.pushState(null, '', ST.back); return; }
    stHide(); return;
  }
  const page = m[1] ? decodeURIComponent(m[1]) : '', tab = m[2] ? decodeURIComponent(m[2]) : '';
  const redraws = page !== ST.page || ((tab || ST.tab) !== ST.tab && ST.page !== 'plugins');   // a tab of a page redraws it
  if(ST.page && redraws && ST.dirty && !stLeave()){ history.replaceState(null, '', ST.back); return; }
  stShow(page, tab);
}
window.addEventListener('hashchange', stRoute);
function stHide(){
  const el = document.getElementById('stx');
  if(!el || el.hidden) return;
  el.hidden = true; document.body.classList.remove('stx-open'); ST.page = ''; ST.index = null; ST.dirty = false;
}
function stClose(){ if(!stLeave()) return; history.pushState(null, '', location.pathname + location.search); stHide(); }
function stToast(text){
  let t = document.getElementById('stx-toast');
  if(!t){ t = el('div', 'stx-toast'); t.id = 'stx-toast'; t.setAttribute('role', 'status'); document.body.append(t); }
  t.textContent = text; t.classList.add('on'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), 1800);
}
function stMenu(q){
  const pages = ST_PAGES(), ql = (q || '').trim().toLowerCase(), hit = p => !ql || (i18n(p.title) + ' ' + i18n(p.expl)).toLowerCase().includes(ql);
  const curG = (pages.find(p => p.id === ST.page) || {}).group;
  let out = '';
  for(const [g, gt, gi] of ST_GROUPS){
    const ps = pages.filter(p => p.group === g);
    const hits = ps.filter(hit);
    if(!ps.length || (ql && !hits.length && !i18n(gt).toLowerCase().includes(ql))) continue;
    const first = ps.find(p => stHere(p)) || ps[0];
    out += `<button class="stx-item${g === curG ? ' on' : ''}${ps.every(p => p.off || !stHere(p)) ? ' off' : ''}" data-pg="${escapeHtml(first.id)}"
      title="${escapeHtml(ps.map(p => i18n(p.title)).join(', '))}">${icon(ICON[gi] ? gi : first.icon, 18)}<span>${escapeHtml(i18n(gt || first.title))}</span></button>`;
    if(ql && ps.length > 1) out += hits.map(p => `<button class="stx-item stx-subitem" data-pg="${escapeHtml(p.id)}"><span>${escapeHtml(i18n(p.title))}</span></button>`).join('');
  }
  const rows = ql.length >= 2 && ST.index ? ST.index.filter(r => r.text.toLowerCase().includes(ql)).slice(0, 12) : [];
  if(rows.length) out += `<div class="stx-g">${escapeHtml(i18n('Found on the pages'))}</div>` + rows.map(r => `<button class="stx-item stx-row" data-pg="${escapeHtml(r.page)}"
      data-tb="${escapeHtml(r.tab)}" data-row="${escapeHtml(r.text)}">${icon('search', 16)}<span>${escapeHtml(r.text)}<small>${escapeHtml(i18n(r.title))}</small></span></button>`).join('');
  return out || `<div class="bindcaption" style="padding:10px">${escapeHtml(i18n('Nothing found'))}</div>`;
}
function stWireMenu(){
  const nav = document.querySelector('#stx .stx-nav');
  nav.querySelectorAll('[data-pg]').forEach(b => b.onclick = () => {
    if(b.dataset.row){ ST.find = b.dataset.row; }
    openSettings(b.dataset.pg, b.dataset.tb || '');
  });
  nav.querySelectorAll('[data-sg]').forEach(b => b.onclick = () => {
    const k = 'stx.shut.' + b.dataset.sg; localStorage.setItem(k, localStorage.getItem(k) === '1' ? '0' : '1');
    nav.innerHTML = stMenu(document.querySelector('#stx .stx-q').value); stWireMenu();
  });
}
// every page's rows (a label's first line) for the search: each page drawn once, off the screen
async function stIndex(){
  if(ST.index) return;
  const index = ST.index = [], pages = ST_PAGES();
  const off = el('div', 'stx-off'); document.body.append(off);
  try {
    for(const pg of pages){
      const box = el('div'); off.append(box);
      try { if(stHere(pg)) stRenderBody(pg, box, '', 'index'); } catch(e){}   // drawn whole (the rows a page fetches too), all its tabs
    }
    await new Promise(r => setTimeout(r, 1500));
    [...off.children].forEach((box, i) => {
      const pg = pages[i];
      box.querySelectorAll('.rlabel, h3').forEach(e => {
        const text = (e.childNodes[0] && e.childNodes[0].textContent || e.textContent || '').trim();
        const tab = (e.closest('[data-stab]') || {dataset: {}}).dataset.stab || '';
        if(text && text.length < 80) index.push({page: pg.id, tab, text, title: pg.title});
      });
    });
  } finally {
    off.remove();                                         // always: its pages' pollers stop once it is gone
  }
  if(ST.index !== index) return;                          // Settings closed meanwhile: a later search draws again
  const q = document.querySelector('#stx .stx-q');
  if(q && q.value.trim().length >= 2){ document.querySelector('#stx .stx-nav').innerHTML = stMenu(q.value); stWireMenu(); }
}
function stRenderBody(cur, box, tab, quiet){
  const plug = (t, into) => { if(!stHere({needs: t.needs})) into.innerHTML = `<div class="bindcaption">${escapeHtml(i18n('Not available here'))}</div>`;
    else if(typeof t.render === 'function') HomePlugins.safe(t.render, into); };
  if(quiet){                                              // the search: every tab, each in its section
    if(cur.tabs.some(t => t.core)) stRenderCore(cur, box, '', quiet);
    cur.tabs.filter(t => !t.core).forEach(t => { const sc = el('section'); sc.dataset.stab = t.id; box.append(sc); plug(t, sc); });
    return;
  }
  const t = cur.tabs.find(x => x.id === tab) || cur.tabs[0];
  if(t.core) stRenderCore(cur, box, t.id, quiet);
  else { const sec = el('section'); box.append(sec); plug(t, sec); }
}
function stRenderCore(cur, box, tab, quiet){
  const own = localStorage.getItem('lang');
  const langs = `<button class="dlg-btn ${own ? '' : 'primary'}" onclick="setLang('')">${i18n('As the house')}</button> ` + Object.entries(I18N_NAMES || {en: 'English'}).filter(([k]) => k === 'en' || (I18N[k] && !window.RESTRICTED)).map(([k, v]) =>
    `<button class="dlg-btn ${own === k ? 'primary' : ''}" onclick="setLang('${k}')">${escapeHtml(v)}</button>`).join(' ');
  const bodies = {
    device: `<section>
        <div class="row"><span class="rlabel">${i18n('Signed in to the house')}<br><span class="bindcaption">${i18n('Log out: this browser forgets the sign-in; other screens are not touched.')}</span></span>
          <button class="dlg-btn" id="st-logout" onclick="logout()">${i18n('Log out')}</button></div>
        <div class="row" id="st-install-row" hidden><span class="rlabel">${i18n('Install as an app')}<br><span class="bindcaption">${i18n('A shortcut on the home screen that opens full-screen, without the browser bar.')}</span></span>
          <button class="dlg-btn primary" id="st-install">${i18n('Install')}</button></div>
        <div class="row" id="st-install-no"><span class="rlabel bindcaption">${i18n('To put the dashboard on the home screen use the browser\'s menu: Install app / Add to Home screen.')}</span></div>
      </section>`,
    houselang: `<section>
        <div class="row" id="st-house-row"><span class="rlabel">${i18n('House language')}<br><span class="bindcaption">${i18n('Voice answers, clocks and panels speak it; a screen with no language of its own shows it too (This screen -> Language).')}</span></span><span class="enum" id="st-house-lang"></span></div>
        <div class="bindcaption" id="st-house-out"></div>
      </section>`,
    lang: `<section>
        <div class="row"><span class="rlabel">${i18n('Dashboard language')}<br><span class="bindcaption">${i18n('This screen only. Only the dashboard\'s own words change; the names you gave devices and rooms stay.')}</span></span><span class="enum" id="st-lang">${langs}</span></div>
      </section>`,
    restart: `<section>
        <div class="row"><span class="rlabel">${i18n('Full restart')}<br><span class="bindcaption">${i18n('Every service of the house is made anew, about a minute without the house. For when something stays stuck after a network drop: Zigbee does not come back, devices do not answer. The page reloads by itself.')}</span></span>
          <button class="dlg-btn" id="st-restart">${i18n('Restart the house')}</button></div><div id="st-restart-out"></div>
      </section>`,
    signin: `<section>
        <div class="row"><span class="rlabel">${i18n('Show code')}<br><span class="bindcaption">${i18n('A 6-digit code, good once for 5 minutes: type it on the sign-in card of the other screen (same network). Only for your own devices.')}</span></span>
          <button class="dlg-btn primary" id="st-signin">${i18n('Show code')}</button></div><div id="st-signin-out"></div>
      </section>`,
    backups: `<section><div class="bindcaption" style="margin-bottom:8px">${i18n('Back up now makes an archive of the house\'s settings and data; the nightly ones are kept on this computer in backups/. Download keeps a copy elsewhere.')}</div><div id="st-backups"></div></section>`,
    plugins: `<section><h3 id="st-h-plugins">${i18n('Plug-ins')}</h3><div class="bindcaption" style="margin-bottom:8px">${i18n('Tick what should run, then Apply (the dashboard restarts). Each plug-in\'s keys are on its own page.')}</div>
        <div id="st-helper"></div>
      </section>
      <section><h3 id="st-h-update">${i18n('Update')}</h3><div class="bindcaption" style="margin-bottom:8px">${i18n('A new version of Yashome: what it brings and the button to apply it (a backup is made first).')}</div><div id="st-update"></div></section>`,
    health: `<section><div class="bindcaption" style="margin-bottom:8px">${i18n('A green dot means fine. Every row says what it checks; a yellow or red one says what to do.')}</div><div id="st-health"></div></section>`,
  };
  if(cur.id === 'devices'){
    if(quiet || tab !== 'forgotten') stDevices(box, quiet);
    if(quiet || tab === 'forgotten') stForgotten(box, quiet);
    return;
  }
  if(cur.id === 'rules'){ const sc = el('section'); if(quiet) sc.dataset.stab = 'main'; box.append(sc); renderAutomations(sc); return; }
  box.innerHTML = bodies[cur.id === 'overview' ? 'health' : cur.id] || '';
  if(cur.id === 'plugins'){                                 // its sections are its tabs: the list, what each is, the update
    const [a, b] = box.querySelectorAll(':scope > section');
    if(a) a.dataset.stab = 'list';
    if(b) b.dataset.stab = 'update';
    if(!quiet) box.querySelectorAll(':scope > section').forEach(sc => { sc.hidden = sc.dataset.stab !== (tab || 'list'); });
  }
  if(cur.id === 'backups') renderBackups(box.querySelector('#st-backups'));
  if(cur.id === 'houselang') renderHouse(box.querySelector('#st-house-lang'), box.querySelector('#st-house-out'));
  if(cur.id === 'restart') box.querySelector('#st-restart').onclick = () => {
    if(confirm(i18n('Restart every service of the house now? About a minute without the house.'))) helperJob('restart', box.querySelector('#st-restart-out'));
  };
  if(cur.id === 'health' || cur.id === 'overview') renderHealth(box.querySelector('#st-health'));
  if(cur.id === 'device'){
    const ib = document.getElementById('install-btn'), ir = box.querySelector('#st-install-row'), no = box.querySelector('#st-install-no');
    if(window.matchMedia && matchMedia('(display-mode: standalone)').matches){   // already the installed app: nothing to install
      if(ir) ir.hidden = true;
      if(no){ no.hidden = false; no.querySelector('.rlabel').textContent = i18n('This is the installed app.'); }
    } else
    if(ib && ib.dataset.ready && ir){ ir.hidden = false; if(no) no.hidden = true; box.querySelector('#st-install').onclick = () => ib.click(); }
  }
  if(cur.id === 'plugins') renderHelper(box.querySelector('#st-helper'), box.querySelector('#st-update'));
  if(cur.id === 'signin') box.querySelector('#st-signin').onclick = async () => {
    const out = box.querySelector('#st-signin-out');
    try {
      const r = await api('/api/signin/start', {method: 'POST'});
      out.innerHTML = `<div style="font-size:32px;letter-spacing:8px;font-weight:700;margin:6px 0">${escapeHtml(r.code)}</div>
        <div class="bindcaption">${escapeHtml(i18n('Works once, for 5 minutes. It gives the device the house access: only for your own devices.'))}</div>`;
    } catch(e){ out.textContent = e.message || e; }
  };
}
function stShow(page, tab){
  let root = document.getElementById('stx');
  if(!root){
    root = el('div'); root.id = 'stx'; root.hidden = true;
    root.innerHTML = `<aside class="stx-menu"><div class="stx-mhead"><h2>${escapeHtml(i18n('Settings'))}</h2><button class="dlg-btn stx-x">${escapeHtml(i18n('Close'))}</button></div><div class="stx-sbox">${icon('search', 16)}<input class="stx-q pillfield" type="search" autocomplete="off"
        placeholder="${escapeHtml(i18n('Search settings'))}" aria-label="${escapeHtml(i18n('Search settings'))}"></div><nav class="stx-nav"></nav></aside>
      <main class="stx-page"><div class="stx-top"><button class="dlg-btn stx-back" aria-label="${escapeHtml(i18n('Settings'))}">${icon('back', 18)}</button>
        <div class="stx-tt"><h2></h2><div class="sub"></div></div><button class="dlg-btn stx-close">${escapeHtml(i18n('Close'))}</button></div>
        <div class="stx-tabs stx-sects" role="tablist"></div><div class="stx-tabs stx-own" role="tablist"></div><div class="stx-body"></div></main>`;
    document.body.append(root);
    root.querySelector('.stx-x').onclick = stClose;
    root.querySelector('.stx-close').onclick = stClose;
    root.querySelector('.stx-back').onclick = () => { if(stLeave()) openSettings(); };
    const q = root.querySelector('.stx-q');
    q.oninput = () => { root.querySelector('.stx-nav').innerHTML = stMenu(q.value); stWireMenu(); if(q.value.trim().length >= 2) stIndex(); };
    q.onkeydown = e => { if(e.key === 'Enter'){ const f = root.querySelector('.stx-nav [data-pg]'); if(f) f.click(); } };
    root.addEventListener('keydown', e => { if(e.key === 'Escape' && !document.getElementById('bd').classList.contains('show')) stClose(); });
    const body = root.querySelector('.stx-body');
    body.addEventListener('input', e => { if(e.target.matches('input[type=text], input[type=number], input[type=password], input:not([type]), textarea') && !e.target.closest('[data-autosave]')) ST.dirty = true; });     // data-autosave: it saves on change itself
    body.addEventListener('click', e => { const b = e.target.closest('button'); if(b && /save|apply|ok|\u0441\u043e\u0445\u0440\u0430\u043d/i.test(b.textContent)) ST.dirty = false; });   // i18n: Save in Russian too
  }
  if(openModalId) closeModal();
  helpPopClose();
  const pages = ST_PAGES();
  page = ST_ALIAS[page] || page;
  if(!page && !stPhone()) page = (pages.find(p => p.id === 'overview' && stHere(p)) || pages.find(p => stHere(p)) || {id: 'lang'}).id;
  const cur = pages.find(p => p.id === page) || (page ? pages.find(p => p.id === 'lang') : null);
  root.hidden = false; document.body.classList.add('stx-open');
  root.classList.toggle('stx-pageview', !!cur && !!page);
  ST.back = location.hash;
  const sameP = ST.page === (cur && cur.id);
  ST.page = cur ? cur.id : ''; ST.tab = cur ? (cur.tabs.find(t => t.id === tab) || cur.tabs[0]).id : '';
  root.querySelector('.stx-nav').innerHTML = stMenu(root.querySelector('.stx-q').value); stWireMenu();
  if(!cur) return;
  const grp = ST_GROUPS.find(g => g[0] === cur.group) || [], sibs = pages.filter(p => p.group === cur.group);
  root.querySelector('.stx-top h2').textContent = i18n(grp[1] || cur.title);
  root.querySelector('.stx-top .sub').textContent = i18n(stHere(cur) ? cur.expl : 'Not available here');
  const sects = root.querySelector('.stx-sects');           // the group's subjects, as the tabs of the page
  sects.innerHTML = sibs.length > 1 ? sibs.map(p => `<button class="stx-tab stx-stab${p.id === cur.id ? ' on' : ''}${stHere(p) ? '' : ' off'}" role="tab" aria-selected="${p.id === cur.id}" data-pg="${escapeHtml(p.id)}">${escapeHtml(i18n(p.title))}</button>`).join('') : '';
  sects.hidden = sibs.length < 2;
  sects.querySelectorAll('[data-pg]').forEach(b => b.onclick = () => openSettings(b.dataset.pg));
  const on = sects.querySelector('.on'); if(on) on.scrollIntoView({block: 'nearest', inline: 'nearest'});
  const tabs = root.querySelector('.stx-own');
  tabs.innerHTML = cur.tabs.length > 1 ? cur.tabs.map(t => `<button class="stx-tab${t.id === ST.tab ? ' on' : ''}${t.core || stOk(t.needs) ? '' : ' off'}" role="tab" aria-selected="${t.id === ST.tab}" data-t="${escapeHtml(t.id)}">${escapeHtml(i18n(t.title))}</button>`).join('') : '';
  tabs.hidden = cur.tabs.length < 2;
  tabs.querySelectorAll('[data-t]').forEach(b => b.onclick = () => openSettings(cur.id, b.dataset.t));
  tabs.classList.toggle('stx-under', sibs.length > 1);       // below the subjects: smaller, as chips
  const body = root.querySelector('.stx-body');
  if(sameP && cur.id === 'plugins'){                        // a tab of a page drawn already: its section only
    body.querySelectorAll(':scope > section').forEach(sc => { sc.hidden = sc.dataset.stab !== ST.tab; });
  } else {
    body.innerHTML = ''; body.scrollTop = 0; ST.dirty = false;
    if(stHere(cur)) stRenderBody(cur, body, ST.tab);
  }
  if(ST.find){                                             // a search hit: its row, lit
    const want = ST.find; ST.find = '';
    setTimeout(() => {
      const r = [...body.querySelectorAll('.rlabel, h3')].find(e => (e.textContent || '').trim().startsWith(want));
      const row = r && (r.closest('.row') || r);
      if(row){ row.scrollIntoView({block: 'center'}); row.classList.add('stx-hit'); setTimeout(() => row.classList.remove('stx-hit'), 2400); }
    }, 400);
  }
}

// Settings -> General: the house language (core /api/house); a screen with no language of its own follows it at once;
async function renderHouse(box, out){
  let h;
  try { h = await api('/api/house'); } catch(e){ out.textContent = e.message || e; return; }
  box.innerHTML = h.langs.map(k => `<button class="dlg-btn ${h.lang === k ? 'primary' : ''}" data-l="${escapeHtml(k)}">${escapeHtml((I18N_NAMES || {})[k] || k)}</button>`).join(' ');
  box.querySelectorAll('[data-l]').forEach(b => b.onclick = async () => {
    try { await api('/api/house', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({lang: b.dataset.l})}); }
    catch(e){ out.textContent = e.message || e; return; }
    if(!localStorage.getItem('lang') && b.dataset.l !== I18N_LANG){ location.reload(); return; }
    renderHouse(box, out);
    stToast(i18n('Saved'));
  });
}

// Settings -> Plug-ins and Update through the helper (helper/helper.py): switch plug-ins, their keys,
// apply (make update), update from git with a backup first, roll back. Without it: how to do it by hand.
// what each plug-in is and what each of its .env keys means, in the dashboard's words (translated in i18n.js)
const PLUGIN_INFO = {
  "zigbee": ["Zigbee", "The Zigbee network through zigbee2mqtt: the gateway, pairing and renaming sensors, switches and remotes."],
  "ir": ["IR remotes", "Learned infrared and radio remotes sent through ESP blasters: TVs, air conditioners, anything with a remote."],
  "tizen": ["Samsung TV", "Samsung TVs (2016 and later) over the network: power by Wake-on-LAN, volume, inputs, apps."],
  "webos": ["LG TV", "LG webOS TVs over the network: power, volume, inputs, apps."],
  "tv": ["TV cards", "The face of every TV on the dashboard (power, volume, inputs) and the Appliances tab. The TV itself is driven by Samsung, LG or IR."],
  "climate": ["Air conditioners", "The card and the remote of an air conditioner: power, temperature, mode, fan; it can show the room's thermometer."],
  "switchbot": ["SwitchBot lock", "A SwitchBot lock through a Bluetooth bridge on a computer near the door: lock, unlock, battery, door."],
  "cameras": ["Cameras", "IP cameras on the dashboard and panels, shown through the house so a camera's password never reaches a browser."],
  "panels": ["Wall panels", "Tablets and ESP screens on the walls: the layout builder, pairing a tablet, its launcher app and its updates."],
  "nfc": ["NFC tags", "Tags and cards that start rules when a phone taps them (open the door, a scene); forged and used links are refused."],
  "computers": ["Computers and servers", "The house's computers on its network (it logs in to them with its key, sudo works) and the servers on them - build, speech, big model, yashGen: install, remove, use; which TV and HDMI input each one is."],
  "presence": ["Who is home", "Sees which family phones are on the home Wi-Fi: at home or away, arrival and departure for rules. Needs its piece on the host."],
  "weather": ["Weather", "The weather now and the forecast for the house's place, from several sources: panels show it, rules and the voice use it."],
  "family": ["Family phones", "The Yash! app on the family's phones: their devices, who is home, ringing a phone, the door while away."],
  "voice": ["Voice", "The house answers by voice: the wake word on the satellites (tablets, ReSpeaker), speech to text, the answer spoken back."],
  "map": ["Home map", "The floor plan with the rooms and every device in its place, live; the rooms of the dashboard come from it."],
  "history": ["History", "One timeline of everything that happened and who did it, kept on this computer. Answers why something switched."],
  "watchdog": ["Watchdog", "Checks the house every 45 s: puts right what is safe to (a ghost, a lost state), reports the rest, forgets gone devices after some days."],
  "connect": ["Add a device", "The one Add window: Zigbee, ESP, tablets and codes found together in one live list, each bound in place."],
  "provision": ["ESP rescue", "Hands the house Wi-Fi to an ESP device that lost it (it opens its own setup Wi-Fi), so nobody walks there with a phone."],
  "build": ["Build server", "Compiles the firmware of the house's ESP devices (panels, sensors, blasters) and the Android apps. Needed to make or update firmware."],
  "pair": ["Device pairing", "Gives a yashGen device its own broker account, the Wi-Fi and its update password when it is paired."],
  "fwdesc": ["Device descriptions", "Reads what a yashGen device says of itself (its channels, its hardware) and checks that no secret is inside."],
  "fwgen": ["yashGen", "The yashGen firmware builder: a device described in its editor becomes ESPHome firmware. Settings -> yashGen opens it and picks the computer it runs on."],
  "touchmouse": ["Touch mouse", "A phone or a wall panel as a touchpad and keyboard for a TV box or a computer of the house."],
  "stats": ["Counts", "Numbers for an outside status page (Homepage): devices online and offline, rules, events a minute. The house does not need it."]
};
async function renderHelper(box, upd){
  if(!box) return;
  const all = [...new Set(HomePlugins.list.map(p => p.id).concat(HomePlugins.failed.map(f => f.id), HomePlugins.off))];
  const r = await fetch('/api/helper/state', {headers: authHeaders()}).catch(() => null);
  if(!r || !r.ok){
    const why = r ? ((await r.json().catch(() => ({}))).detail || r.status) : '';
    const more = t => `<details class="stx-more"><summary>${escapeHtml(i18n('More'))}</summary><div class="bindcaption">${escapeHtml(i18n(t))}</div></details>`;
    box.innerHTML = `<div class="bindcaption">${escapeHtml(i18n('The house helper switches plug-ins on and off; it does not answer now, so the list is shown only.'))}</div>
      ${why ? `<div class="bindcaption">${escapeHtml(String(why))}</div>` : ''}${more('Which plug-ins run: PLUGINS in .env (comma separated names), then make update (docs/plugins.md).')}`;
    all.forEach(id => box.append(stPluginRow(id)));
    if(upd) upd.innerHTML = `<div class="bindcaption">${escapeHtml(i18n('The house helper installs updates; it does not answer now, so this house is not updated from here.'))}</div>${more('On the host: git pull, then make update.')}`;
    return;
  }
  const st = await r.json();
  const post = (u, b) => api(u, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
  box.innerHTML = st.plugins_all ? `<div class="bindcaption">${escapeHtml(i18n('Every plug-in runs (set so on the host): they are not picked here.'))}</div>` : '';
  for(const p of st.plugins){
    const note = p.required || p.requires_on ? `<span style="color:var(--dim)"> (${escapeHtml(i18n(p.required ? 'always on' : 'needed by another'))})</span>` : '';
    const row = stPluginRow(p.name, `<input type="checkbox" class="hl-on" value="${escapeHtml(p.name)}" ${p.on ? 'checked' : ''}
      ${p.required || p.requires_on || st.plugins_all ? 'disabled' : ''} style="margin-right:6px">`);
    if(note) row.querySelector('.rlabel').insertAdjacentHTML('beforeend', note);
    box.append(row);
  }
  all.filter(id => !st.plugins.some(p => p.name === id)).forEach(id => box.append(stPluginRow(id)));    // another folder's, no switch here
  box.insertAdjacentHTML('beforeend', `<div class="row"><button class="dlg-btn primary" id="hl-apply">${escapeHtml(i18n('Apply'))}</button></div>
    <div class="bindcaption" id="hl-out"></div>`);
  const out = box.querySelector('#hl-out');
  box.querySelectorAll('.hl-on').forEach(c => c.onchange = async () => {
    const names = [...box.querySelectorAll('.hl-on:checked:not(:disabled)')].map(x => x.value);
    try { await post('/api/helper/plugins', {on: names}); out.textContent = i18n('Saved; Apply to start it.'); }
    catch(e){ out.textContent = e.message || e; c.checked = !c.checked; }
  });
  box.querySelector('#hl-apply').onclick = () => helperJob('apply', out);
  if(!upd) return;
  const g = st.git || {};
  upd.innerHTML = `<div class="row"><span class="rlabel">${escapeHtml(i18n('This version'))}</span>
      <span class="rval">${escapeHtml([g.tag, g.branch || '?', String(g.commit || '').slice(0, 7)].filter(Boolean).join(' \u00b7 '))}</span></div>
    ${g.fetch_error ? `<div class="bindcaption" data-up="failed" style="color:var(--bad)">${escapeHtml(i18n('Could not check for updates: {0}', i18n(g.fetch_reason || 'git could not reach the repository')))}</div>
      <details><summary class="bindcaption" style="cursor:pointer">${escapeHtml(i18n('More'))}</summary><pre style="white-space:pre-wrap;font-size:12px">${escapeHtml(g.fetch_error)}</pre></details>`
      : `<div class="bindcaption" data-up="checked">${escapeHtml(g.behind ? i18n('{0} new commits upstream.', g.behind) : i18n('Up to date.'))}</div>`}
    ${(g.changelog || []).length ? `<pre style="white-space:pre-wrap;max-height:240px;overflow:auto">${escapeHtml(g.changelog.join('\n'))}</pre>` : ''}
    <div class="row" style="gap:8px">
      <button class="dlg-btn primary" id="up-go" ${g.behind && !g.dirty && !g.worktree ? '' : 'disabled'}>${escapeHtml(i18n('Update (a backup first)'))}</button>
      <button class="dlg-btn" id="up-back" ${st.previous && !g.worktree ? '' : 'disabled'}>${escapeHtml(i18n('Roll back'))}</button></div>
    ${g.dirty ? `<div class="bindcaption">${escapeHtml(i18n('The checkout has local changes: update it by hand.'))}</div>` : ''}
    <div class="bindcaption">${escapeHtml(i18n('Roll back returns the code to the version before the last update; the house files stay - restore a backup for those.'))}</div>
    <div class="bindcaption" id="up-out"></div>`;
  const uo = upd.querySelector('#up-out');
  upd.querySelector('#up-go').onclick = () => helperJob('update', uo);
  upd.querySelector('#up-back').onclick = () => helperJob('rollback', uo);
  if(st.job && st.job.state === 'running') helperWatch(uo);
}
async function helperJob(kind, out){
  try {
    await api('/api/helper/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind})});
    helperWatch(out);
  } catch(e){ out.textContent = e.message || e; }
}
// the job restarts the dashboard itself: polls ride over that gap, then the page reloads
async function helperWatch(out){
  let fails = 0;
  for(;;){
    await new Promise(res => setTimeout(res, 3000));
    let j;
    try { j = (await api('/api/helper/jobs/current')).job; fails = 0; }
    catch(e){ if(++fails > 60){ out.textContent = i18n('The dashboard did not come back: check on the host.'); return; } continue; }
    if(!j) return;
    out.innerHTML = `<div>${escapeHtml(i18n('{0}: {1}', j.kind, i18n(j.state)))}</div>
      <pre style="white-space:pre-wrap;max-height:200px;overflow:auto">${escapeHtml((j.log || []).slice(-20).join('\n'))}</pre>`;
    if(j.state !== 'running'){ if(j.state === 'done') setTimeout(() => location.reload(), 3000); return; }
  }
}

// Settings -> Health: every check of the core and the plug-ins in plain words, worst first, a fix per row
const HEALTH_COLOR = {ok: 'var(--ok)', warn: 'var(--on)', bad: 'var(--bad)'};
// what a health row checks and what to do when it is red, by the row's id (a prefix before ":" or "-")
const HEALTH_HELP = {
  broker: ['the MQTT broker every device talks through', 'start the stack: make update, or look at docker compose ps'],
  clock: ['the server\'s time and time zone; schedules depend on them', 'fix the computer\'s clock or its time zone'],
  disk: ['free space on the computer\'s disk', 'free some space: old backups, unused docker images'],
  backup: ['the age of the last nightly backup', 'Settings -> Backups -> Back up now; days in red mean the nightly job fails'],
  build: ['the server that compiles panel firmware and the apps', 'Settings -> Computers and servers -> Servers'],
  family: ['the bridge to the Yash! phones: pushes and who is home', 'check the family-bridge container and Firebase'],
  'panels-discover': ['the helper that finds new panels on the network', 'make update restarts it'],
  panel: ['this panel reports every few minutes while it is on', 'is the tablet on, with the panel app running?'],
  presence: ['the host piece that watches the phones on the Wi-Fi', 'systemctl status phone-presence on the host'],
  provision: ['rescue of ESP devices that lost the Wi-Fi', 'optional: a relay node or the host helper (the plug-in\'s README)'],
  switchbot: ['the Bluetooth bridge to the lock', 'systemctl status switchbot-lock@<id> on the bridge\'s host'],
  weather: ['the weather feed for panels and rules', 'the place in Settings -> Weather and internet access'],
  zigbee: ['zigbee2mqtt and the coordinator stick', 'docker compose logs zigbee2mqtt; the stick must be plugged in'],
  schedules: ['timed rules that fired while their device was away', 'check the device and the rule\'s time'],
  plugin: ['a plug-in that failed to load', 'its error is in the server log'],
  settings: ['the house\'s settings file', 'settings.json is damaged: restore it from a backup'],
};
function healthHelp(rid){
  const key = Object.keys(HEALTH_HELP).find(k => rid === k || rid.startsWith(k + ':') || rid.startsWith(k + '-'));
  return key ? HEALTH_HELP[key] : null;
}
async function renderHealth(box){
  if(!box) return;
  box.innerHTML = `<div class="bindcaption">${escapeHtml(i18n('Checking\u2026'))}</div>`;
  const r = await fetch('/api/health/report', {headers: authHeaders()}).catch(() => null);
  if(r && r.status === 403){ box.closest('section').remove(); return; }    // a phone's or a panel's token
  if(!r || !r.ok){ healthDot(''); box.innerHTML = `<div class="bindcaption">${escapeHtml(i18n(r && r.status === 401 ? 'Signed out.' : 'No answer from the house.'))}</div>`; return; }
  const h = await r.json();
  healthDot(h.worst);
  const t = (s, args) => s ? i18n(s, ...(args || [])) : '';
  box.innerHTML = h.rows.map(r => `<div class="row" style="flex-wrap:wrap">
      <span class="rlabel"><span style="color:${HEALTH_COLOR[r.state]}">\u25cf</span> ${escapeHtml(t(r.title, r.args))}</span>
      <span class="rval">${escapeHtml(r.state === 'ok' && !r.detail ? i18n('fine') : t(r.detail, r.args))}</span>
      ${(() => { const hh = healthHelp(r.id || ''); const parts = [];
          if(hh) parts.push(i18n('checks') + ': ' + i18n(hh[0]));
          if(r.fix) parts.push(t(r.fix, r.args)); else if(hh && r.state !== 'ok') parts.push(i18n('what to do') + ': ' + i18n(hh[1]));
          const go = r.link ? ` <button class="dlg-btn" data-hlink="${escapeHtml(r.link)}" style="padding:2px 10px;font-size:12px;margin-left:6px">${escapeHtml(i18n('Open'))}</button>` : '';
          return parts.length || go ? `<div class="bindcaption" style="flex-basis:100%">${escapeHtml(parts.join(' \u00b7 '))}${go}</div>` : ''; })()}
      ${r.cmd ? `<details style="flex-basis:100%"><summary class="bindcaption" style="cursor:pointer">${escapeHtml(i18n('More'))}</summary><code style="white-space:pre-wrap;font-size:12px">${escapeHtml(r.cmd.replace(/\{(\d+)\}/g, (m, i) => (r.args || [])[i] ?? m))}</code></details>` : ''}</div>`).join('')
    + `<div class="row"><button class="dlg-btn" id="hl-again">${escapeHtml(i18n('Check again'))}</button></div>`;
  box.querySelector('#hl-again').onclick = () => renderHealth(box);
  box.querySelectorAll('[data-hlink]').forEach(b => b.onclick = () => openSettings(...b.dataset.hlink.split('/')));
}
// a dot on the gear when something needs a look; checked at start and every 10 minutes
function healthDot(worst){
  const g = document.getElementById('gearbtn');
  if(g) g.style.boxShadow = worst === 'bad' ? 'inset 0 0 0 2px var(--bad)' : worst === 'warn' ? 'inset 0 0 0 2px var(--on)' : '';
}
// no token prompt from here; a 401/403 stops the poll (signed out, or not the house token)
let _healthPolls = true, _healthAt = 0;
async function healthPoll(){
  if(!_healthPolls || !TOKEN || document.hidden || Date.now() - _healthAt < 30000) return;   // one at a time, 30 s apart; none before sign-in
  _healthAt = Date.now();
  const r = await fetch('/api/health/report', {headers: authHeaders()}).catch(() => null);
  if(r && (r.status === 401 || r.status === 403)){ _healthPolls = false; healthDot(''); return; }
  healthDot(r && r.ok ? (await r.json()).worst : '');
}
setTimeout(healthPoll, 5000);
setInterval(healthPoll, 600000);
document.addEventListener('visibilitychange', () => { if(!document.hidden) healthPoll(); });

// Settings -> Backups: one every night (the backend), here its age, "back up now" and the downloads
async function renderBackups(box){
  if(!box) return;
  let b;
  try { b = await api('/api/backups'); }
  catch(e){ box.innerHTML = `<div class="bindcaption">${escapeHtml(e.message || String(e))}</div>`; return; }
  const day = 86400, age = b.age_s;
  const when = age == null ? i18n('never') : agoText(age);
  const bad = age == null || age > 2 * day || b.error;
  box.innerHTML = `
    <div class="row"><span class="rlabel">${i18n('Last backup')}</span>
      <span class="rval" style="${bad ? 'color:var(--bad)' : ''}">${escapeHtml(when)}</span>
      <button class="dlg-btn" id="bk-now">${i18n('Back up now')}</button></div>
    ${b.error ? `<div class="bindcaption" style="color:var(--bad)">${escapeHtml(b.error)}</div>` : ''}
    ${(b.warnings || []).map(w => `<div class="bindcaption" style="color:var(--bad)">${escapeHtml(w)}</div>`).join('')}
    ${b.items.slice(0, 5).map(x => `<div class="row" style="flex-wrap:wrap;gap:6px"><span class="rlabel">${escapeHtml(fmtWhen(x.ts * 1000))}</span>
      <span class="rval">${Math.max(1, Math.round(x.size / 1024))} KB</span>
      <button class="dlg-btn" data-bk="${escapeHtml(x.name)}">${i18n('Download')}</button>
      <button class="dlg-btn" data-restore="${escapeHtml(x.name)}">${i18n('Restore')}</button></div>`).join('')}
    <div class="row" style="gap:6px"><button class="dlg-btn" id="bk-file">${i18n('Restore from a file')}</button>
      <input type="file" id="bk-pick" accept=".gz,.tgz,application/gzip" hidden></div>
    <div class="bindcaption" id="bk-out"></div>
    <div class="bindcaption">${escapeHtml(b.nightly ? i18n('Every night at {0}; the last {1} stay in backups/ of this computer. Keep a copy on another disk too.', b.at, b.keep)
      : i18n('No nightly backup (BACKUP_AT={0}).', b.at))}</div>
    <div class="bindcaption">${escapeHtml(i18n('A backup holds the house settings, the .env secrets, the Zigbee network and your own plug-ins; not the logs.'))}</div>`;
  box.querySelector('#bk-now').onclick = async ev => {
    ev.target.disabled = true;
    try { await api('/api/backups', {method: 'POST'}); } catch(e){ alert(e.message || e); }
    renderBackups(box);
  };
  const out = box.querySelector('#bk-out');
  const restore = async name => {
    const ok = await askConfirm(i18n('Restore the house from {0}? The house stops for a minute; what is there now is backed up first and kept aside, nothing is deleted.', name), i18n('Restore'));
    if(!ok) return;
    out.textContent = i18n('Restoring\u2026');
    try {
      await api('/api/helper/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind: 'restore', file: name})});
      helperWatch(out);
    } catch(e){ out.textContent = i18n('Could not restore: {0}', e.message || e); }
  };
  box.querySelectorAll('[data-restore]').forEach(btn => btn.onclick = () => restore(btn.dataset.restore));
  const pick = box.querySelector('#bk-pick');
  box.querySelector('#bk-file').onclick = () => pick.click();
  pick.onchange = async () => {
    const f = pick.files[0]; if(!f) return;
    out.textContent = i18n('Uploading\u2026');
    try {
      const r = await api('/api/backups/upload', {method: 'POST', headers: {'Content-Type': 'application/gzip'}, body: f});
      out.textContent = '';
      await restore(r.name);
      if(!out.textContent) renderBackups(box);
    } catch(e){ out.textContent = i18n('Could not read the file: {0}', e.message || e); }
  };
  box.querySelectorAll('[data-bk]').forEach(btn => btn.onclick = async () => {
    const r = await fetch('/api/backups/' + encodeURIComponent(btn.dataset.bk), {headers: authHeaders()});
    if(!r.ok){ alert(r.status); return; }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = btn.dataset.bk;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  });
}

