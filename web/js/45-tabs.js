// Tab layout
const TABS = [
  {id:'fav',     label:i18n('Favourites'),   cats:[]},                // user-curated channels
  {id:'switch',  label:i18n('Switches'), cats:['switch'], compact:true},
  {id:'sensor',  label:i18n('Sensors'),     cats:['sensor']},
  {id:'button',  label:i18n('Buttons'),      cats:['button'], compact:true},
  {id:'auto',    label:i18n('Automations'), cats:[]},             // bindings view over buttons+sensors
  {id:'indicator', label:i18n('Indicators'), cats:['indicator'], compact:true},
  {id:'other',   label:i18n('Other'),   cats:[]},
];
let activeTab = sessionStorage.getItem('home.tab') || 'fav';   // this session's last tab, else favourites
function rememberTab(id){ localStorage.setItem('home.tab', id); sessionStorage.setItem('home.tab', id); }
let FAVORITES = [];   // [{device, code, alias}]
let ROOM_HIDDEN = [];   // rooms kept only because something names them: no tab
let ROOM_ZONED = [];   // rooms that are zones of the map: hidden from the tabs while empty
let ROOM_LOCKED = [];  // devices whose room comes from the map (read-only in the device window)
let ROOMS = [];       // [{id, name, devices:[ids]}] -- living zones, tabs after TABS
let TAB_ORDER = {};   // {tabId:[device ids]} -- manual tile order on a tab
let TILE_SIZES = {};  // {scope:{dev:{w,h,col,hn}}} -- resized tiles; scope = tab/room:<id>; hn = the height on a narrow screen
const NARROW = window.matchMedia ? matchMedia('(max-width:640px)') : {matches: false};


function devsForTab(tabId){
  const tabs = allTabs();
  const tab = tabs.find(t => t.id === tabId) || TABS[0];
  // "Other" -- catch-all for anything not in other tabs (plus its own cats)
  let devs;
  if(tab.id === 'other'){
    const knownCats = new Set(tabs.filter(t=>t.id!=='other').flatMap(t=>t.cats));
    devs = DEVICES.filter(d => !knownCats.has(effCat(d)) && !HomePlugins.hidden(d));
  }else{
    devs = DEVICES.filter(d => tab.cats.includes(effCat(d)) && !HomePlugins.hidden(d));
  }
  return applyTabOrder(tab.id, devs);
}

// Manual tab order: the listed ones first, the rest after them as they were
function applyTabOrder(tabId, devs){
  const ord = TAB_ORDER[tabId];
  if(!Array.isArray(ord) || !ord.length) return devs;
  const pos = new Map(ord.map((id, i) => [id, i]));
  return devs.slice().sort((a, b) =>
    (pos.has(a.id) ? pos.get(a.id) : 1e9) - (pos.has(b.id) ? pos.get(b.id) : 1e9));
}


// a room's temperature: the first temperature reading of a device in it
function roomTemp(r){ const t = roomClimate(r); return t ? t.value : null; }
function roomClimateTip(c){ return c ? [c.humidity != null ? i18n('Humidity') + ' ' + c.humidity + '%' : '', c.note].filter(Boolean).join(' \u00b7 ') : ''; }
// a room's reading: the first live source of the order the house sends (a hand choice alone, or auto: no module board)
function roomSource(r, kind){
  for(const src of ((r.sensors || {})[kind] || {}).order || []){
    const k = src.lastIndexOf('/'), id = src.slice(0, k), code = src.slice(k + 1), st = STATE[id], v = st && st.values;
    if(st && !st.stale && st.online !== false && v && typeof v[code] === 'number') return {id, code, v: v[code]};
  }
  return null;
}
function roomClimate(r){
  const t = roomSource(r, 'temperature'); if(!t) return null;
  const h = roomSource(r, 'humidity'), c = ((BY_ID[t.id] || {}).controls || []).find(x => x.code === t.code) || {};
  return {value: YDisp.temp(t.v, t.id + '/' + t.code), humidity: h ? YDisp.hum(h.v, h.id + '/' + h.code) : null, note: c.note || ''};
}
// the room's thermometer and hygrometer: Auto (what it reads now) or one of the room's channels, a module's board marked
async function openRoomSensors(rid){
  const room = roomById(rid); if(!room) return;
  let S;
  try { S = await api('/api/rooms/' + encodeURIComponent(rid) + '/sensors'); } catch(e){ alert(i18n('Not loaded: ') + (e.message || e)); return; }
  const hhmm = t => { const d = new Date(t * 1000); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };
  const fmt = (k, v) => typeof v !== 'number' ? '\u2014' : k === 'temperature' ? YDisp.temp(v) + '\u00b0' : YDisp.hum(v) + '%';
  const sec = k => { const x = S[k], now = x.reading ? x.options.find(o => o.dev === x.reading.dev && o.code === x.reading.code) : null;
    const row = (src, label, meta, on) => `<div class="picker-dev"><button class="picker-chan ${on ? 'active' : ''}" data-k="${k}" data-src="${escapeHtml(src)}" style="width:100%;text-align:left">
      <span class="pick-mark">${on ? icon('check', 14) : ''}</span> ${escapeHtml(label)}<span class="picker-meta">${meta ? ' \u00b7 ' + escapeHtml(meta) : ''}</span></button></div>`;
    const auto = x.pick === 'auto' ? (now ? now.name + ' ' + fmt(k, x.reading.value) : i18n('nothing reads it now')) : '';
    return `<section style="padding-top:0;border-top:0"><h3 style="margin:6px 0">${i18n(k === 'temperature' ? 'Thermometer' : 'Hygrometer')}</h3>
      ${x.moved ? `<div class="bindcaption" style="color:#ffb020">${escapeHtml(i18n(x.moved.room ? '{0} is in the room {1} now: this room reads Auto' : '{0} is in no room now: this room reads Auto',
          (DEVICES.find(d => d.id === x.moved.dev) || {}).name || x.moved.dev, x.moved.room))}</div>` : ''}
      ${x.silent ? `<div class="bindcaption" style="color:#ffb020">${escapeHtml(i18n('{0} has not answered since {1}', x.silent.name, x.silent.since ? hhmm(x.silent.since) : '?'))}</div>` : ''}
      <div class="picker-list">${row('auto', i18n('Auto'), auto, x.pick === 'auto')}
      ${x.options.map(o => row(o.dev + '/' + o.code, o.name, [fmt(k, o.value), o.advanced ? i18n('module board') : '', o.outdoor ? i18n('outdoors') : ''].filter(Boolean).join(' \u00b7 '), x.pick === o.dev + '/' + o.code)).join('')}</div></section>`; };
  Home.openModal(`<div class="head"><button class="closebtn" onclick="closeModal()">\u00d7</button><h2>${escapeHtml(i18n('Thermometer: {0}', room.name))}</h2>
      <div class="sub">${escapeHtml(i18n('What the room shows, Ask answers and an AC set to the room reads. Auto never takes a module\'s own board or a sensor outdoors.'))}</div></div>
    <div class="body">${sec('temperature')}${sec('humidity')}</div>`, '__room_sensors__');
  document.getElementById('md').querySelectorAll('[data-src]').forEach(b => b.onclick = async () => {
    try { await api('/api/rooms/' + encodeURIComponent(rid) + '/sensors', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind: b.dataset.k, source: b.dataset.src})}); }
    catch(e){ alert(i18n('Not saved: ') + (e.message || e)); return; }
    await refreshTabs(); openRoomSensors(rid);
  });
}
// the places row: Favourites, the rooms (each with its temperature); "+ Room" only while the house has no room at all
function placesHtml(){
  const fav = `<button class="tab ${activeTab==='fav'?'active':''}" data-tab="fav">${escapeHtml(i18n('Favourites'))}<span class="badge">${FAVORITES.length}</span></button>`;
  const top = allTabs().filter(t => t.plugin && t.plugin.row === 'places' && !t.plugin.hidden).map(t =>      // a plug-in tab of the top row: after Favourites, before the rooms
    { const dot = t.plugin.dot ? HomePlugins.safe(t.plugin.dot) : '', bd = t.plugin.badge ? HomePlugins.safe(t.plugin.badge) : '';       // a colour dot and a count the plug-in keeps current
      return `<button class="tab ${t.id===activeTab?'active':''}" data-tab="${escapeHtml(t.id)}">${dot ? `<i style="display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;background:${escapeHtml(dot)}"></i>` : ''}${escapeHtml(t.label)}${bd !== '' && bd != null ? `<span class="badge">${escapeHtml(String(bd))}</span>` : ''}</button>`; }).join('');
  return fav + top + ROOMS.filter(r => !ROOM_HIDDEN.includes(r.id) && ((r.devices || []).length || !ROOM_ZONED.includes(r.id) || 'room:' + r.id === activeTab)).map(r => {      // map mode: a room without a device lives on the map only
    const tid = 'room:' + r.id, c = roomClimate(r), t = c ? c.value : null;
    return `<button class="tab ${tid===activeTab?'active':''}" data-tab="${escapeHtml(tid)}">${escapeHtml(r.name)}<b class="rtemp" id="rtemp_${escapeHtml(r.id)}" title="${escapeHtml(roomClimateTip(c))}">${t != null ? t + '°' : ''}</b></button>`;
  }).join('') + (ROOMS.length ? '' :       // the first room; later ones on the map (its bar: New room)
    `<button class="tab room-add" id="addroom-chip" title="${i18n('New room (living zone)')}">+ ${i18n('Room')}</button>`);
}
function renderTabs(){
  const bar = document.getElementById('tabs'), kinds = document.getElementById('kinds');
  const pbadge = t => { const v = t.plugin.badge ? HomePlugins.safe(t.plugin.badge) : '';
    return (typeof v === 'number' || typeof v === 'string') ? escapeHtml(String(v)) : ''; };
  const empty = t => !t.plugin && t.cats.length && !devsForTab(t.id).length;   // a core section with no device
  bar.innerHTML = placesHtml();
  kinds.innerHTML = allTabs().filter(t => t.id !== 'fav' && !(t.plugin && (t.plugin.hidden || t.plugin.row === 'places')) && !empty(t) && !(t.id === 'other' && !devsForTab('other').length)).map(t => {
    const n = t.plugin ? (t.plugin.badge ? pbadge(t) : t.cats && t.cats.length ? devsForTab(t.id).length : '') : (t.id === 'auto') ? RULES.length : devsForTab(t.id).length;
    return `<button class="tab ${t.id===activeTab?'active':''}" data-tab="${t.id}">${escapeHtml(t.label)}${
      n === '' || n == null ? '' : `<span class="badge">${n}</span>`}</button>`;
  }).join('');
  const ar = document.getElementById('addroom-chip'); if(ar) ar.onclick = ()=> addRoomPrompt();
  for(const b of [bar, kinds]){
    const cur = b.querySelector('.tab.active');
    if(cur && b.scrollWidth > b.clientWidth) cur.scrollIntoView({inline: 'center', block: 'nearest'});
    b.querySelectorAll('.tab[data-tab]').forEach(btn => {
      btn.onclick = ev =>{
        if(btn.dataset.tab === activeTab && ev && ev.target.closest && ev.target.closest('.rtemp') && activeTab.startsWith('room:')){ openRoomSensors(activeTab.slice(5)); return; }   // the room's own temperature: which one
        const ns = document.getElementById('navsearch'); if(ns && ns.value){ ns.value = ''; SEARCH_Q = ''; ns.classList.remove('open'); }
        activeTab = btn.dataset.tab;
        gridEditMode = false;
        rememberTab(activeTab);
        renderTabs();
        render();
      };
    });
  }
}

// the rooms are drawn by renderTabs (after Favourites); kept for the callers
function renderRooms(){ renderTabs(); }

let favEditMode = false;
let favDeleteMode = false;
let favSelected = new Set();   // keys "device/code" picked for deletion

function render(){
  const a = document.activeElement, fid = a && a !== document.body && a.id;
  renderPage();
  updateCards();                                  // a card drawn for an offline device is off at once, not at the next frame
  if(fid && !openModalId){                        // the keyboard's place survives the redraw
    const n = document.getElementById(fid);
    if(n && n !== document.activeElement && n.tabIndex >= 0) n.focus({preventScroll: true});
  }
}
let SEARCH_Q = '', _tabBeforeSearch = null;
function renderSearch(app){
  const q = SEARCH_Q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hay = d => [d.name, roomNameOf(d.id), catLabel(d.category), d.id].join(' ').toLowerCase();
  const devs = DEVICES.filter(d => !HomePlugins.hidden(d) && q.every(w => hay(d).includes(w)));
  const bar = el('div'); bar.style.cssText = 'padding:10px 16px 0;font-size:13px;color:var(--dim)';
  bar.textContent = devs.length ? i18n('Found:') + ' ' + devs.length + ' ' + i18nPlural('device', devs.length) : i18n('Nothing found');
  app.append(bar);
  if(!devs.length) return;
  const g = document.createElement('div'); g.className = 'grid packed';
  g.dataset.scope = 'search';
  for(const d of devs){
    const cd = card(d);
    for(const f of HomePlugins.cards) HomePlugins.safe(f, cd, d);
    cd.dataset.dev = d.id;
    g.appendChild(cd);
  }
  app.append(g);
  for(const cd of g.children) applyTileSize(cd, 'search', cd.dataset.dev, g);
  packGrid(g);
}
function renderPage(){
  const app=$('#app'); app.innerHTML='';
  if(activeTab === 'search'){ renderSearch(app); return; }
  if(activeTab.startsWith('room:')){ renderRoom(app, activeTab.slice(5)); return; }
  const ptab = activeTab.startsWith('p:') ? allTabs().find(x => x.id === activeTab) : null;
  if(activeTab.startsWith('p:') && !(ptab && ptab.cats.length && !ptab.plugin.render)){
    const t = ptab;
    const box = el('div'); app.append(box);   // detached by the next render(): stale async writes are harmless
    if(t) HomePlugins.safe(() => t.plugin.render(box));
    return;
  }
  if(activeTab === 'fav'){ renderFavorites(app); return; }
  if(activeTab === 'auto'){ renderAutomations(app); return; }
  const devs = devsForTab(activeTab);
  // plug-in buttons above a device grid ("+ IR appliance"), independent of the devices on it
  const otherBtns = HomePlugins.toolbarButtons(activeTab);
  let toolbarRow = null;
  if(otherBtns.length){
    const bar = el('div'); bar.style.cssText = 'margin:8px 16px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:center'; toolbarRow = bar;   // Order joins this row
    for(const tb of otherBtns){
      const b = el('button', 'addbtn', escapeHtml(i18n(tb.label)));
      b.dataset.tb = tb.label;
      b.style.cssText = 'width:auto;margin:0;padding:10px 16px;background:var(--card)';
      b.onclick = () => HomePlugins.safe(tb.onClick);
      bar.append(b);
    }
    app.append(bar);
  }
  if(!devs.length){
    if(!otherBtns.length) app.innerHTML = '<div class="skeleton">' + i18n('\u2014 no devices on this tab \u2014') + '</div>';
    return;
  }
  const bar = toolbarRow || document.createElement('div');
  if(!toolbarRow) bar.style.cssText = 'display:flex;gap:8px;padding:8px 16px 0;flex-wrap:wrap;align-items:center';
  if(devs.length > 1){
    const ord = document.createElement('button');
    ord.className = 'dlg-btn' + (gridEditMode ? ' primary' : '');
    ord.dataset.tb = 'order';
    ord.textContent = gridEditMode ? i18n('Done') : i18n('Order');
    ord.onclick = ()=>{ gridEditMode = !gridEditMode; render(); };
    bar.append(ord);
    if(gridEditMode){
      const hint = document.createElement('span');
      hint.style.cssText = 'font-size:13px;color:var(--dim)';
      hint.textContent = i18n('drag the tiles into the order you want');
      bar.append(hint);
    }
  }
  if(bar.children.length && !toolbarRow) app.append(bar);
  const tabDef = allTabs().find(t => t.id === activeTab);
  const g=document.createElement('div');
  g.className = 'grid packed' + (tabDef && tabDef.compact ? ' compact' : '');
  g.dataset.scope = activeTab;
  for(const d of devs){
    const cd = card(d);
    for(const f of HomePlugins.cards) HomePlugins.safe(f, cd, d);   // plug-in decorations
    cd.dataset.dev = d.id;
    if(gridEditMode) armGridDrag(cd, g, activeTab, parent => saveTabOrder(activeTab, parent));
    g.appendChild(cd);
  }
  app.append(g);
  for(const cd of g.children) applyTileSize(cd, activeTab, cd.dataset.dev, g);
  if(g.classList.contains('packed')) packGrid(g);
}

