// -- plug-ins (docs/plugins.md) ---------------------------------------------
// A plug-in ships /plugins/<id>/ui.js (+ i18n.json). The dashboard loads every script before the
// first render; a script registers what it adds. Helpers a plug-in may use are on `Home`.
const HomePlugins = {
  list: [], failed: [], off: [], tabs: [], addMenu: [], addFlow: [], cards: [], ruleActions: [], toolbar: [], onReload: [],
  header: [], onState: [], cardFaces: [], modalSections: [], holds: [], hides: [], settings: [], help: [], ask: [], deviceSettings: [],
  register(m){
    for(const k of ['tabs', 'addMenu', 'addFlow', 'cards', 'ruleActions', 'toolbar', 'onReload',
                    'header', 'onState', 'cardFaces', 'modalSections', 'holds', 'hides', 'settings', 'help', 'ask', 'deviceSettings'])
      if(Array.isArray(m && m[k])) this[k].push(...(k === 'settings' ? m[k].map(x => Object.assign(x, {_plugin: this._cur})) : m[k]));
  },
  // one plug-in's bug must not blank the dashboard: every call into a plug-in goes through this
  // the header's Ask the house: the first plug-in with ask: [{open(text)}] answers; Enter in the field opens it with the text
  renderAsk(){
    const host = document.getElementById('askslot'), a = this.ask[0];
    if(!host) return;
    host.innerHTML = '';
    if(!a || typeof a.open !== 'function') return;
    // one entry: the field (Enter asks); a phone has no room for it, so there its button opens the field
    host.innerHTML = `<form class="askbar" role="search"><button type="submit" class="dlg-btn askbtn" title="${escapeHtml(i18n('Ask the house'))}" aria-label="${escapeHtml(i18n('Ask the house'))}">${icon('chat', 18)}</button>
      <input class="pillfield" type="search" enterkeyhint="send" autocomplete="off" placeholder="${escapeHtml(i18n('Ask the house\u2026'))}" aria-label="${escapeHtml(i18n('Ask the house'))}"></form>`;
    const f = host.querySelector('form'), inp = host.querySelector('input');
    const phone = () => matchMedia('(max-width:640px)').matches;
    const drop = show => {                           // a provider's examples under the empty field; a click sends one
      f.querySelector('.askdrop')?.remove();
      const ex = show && !inp.value.trim() && typeof a.examples === 'function' ? this.safe(a.examples) || [] : [];
      if(!ex.length) return;
      const d = document.createElement('div'); d.className = 'askdrop'; d.setAttribute('role', 'listbox');
      for(const q of ex){ const b = document.createElement('button'); b.type = 'button'; b.setAttribute('role', 'option'); b.textContent = q;
        b.onmousedown = ev => ev.preventDefault(); b.onclick = () => go(q); d.append(b); }
      f.append(d);
    };
    const go = t => { inp.value = ''; drop(false); f.classList.remove('open'); inp.blur(); this.safe(a.open, t); };
    f.onsubmit = ev => {
      ev.preventDefault();
      const t = inp.value.trim();
      if(!t && phone() && !f.classList.contains('open')){                    // a phone: the button first opens the field under the header
        const h = document.querySelector('header'); document.documentElement.style.setProperty('--askt', Math.round(h.getBoundingClientRect().bottom + 4) + 'px');
        f.classList.add('open'); inp.focus(); drop(true); return;
      }
      go(t);
    };
    inp.onfocus = () => drop(true);
    inp.oninput = () => drop(true);
    inp.onblur = () => setTimeout(() => { drop(false); if(!inp.value.trim()) f.classList.remove('open'); }, 150);
    inp.onkeydown = ev => { if(ev.key === 'Escape'){ inp.value = ''; inp.blur(); } };
  },
  safe(fn, ...args){
    try{
      const r = fn(...args);
      if(r && typeof r.catch === 'function') r.catch(e => console.warn('plug-in', e));   // async callbacks too
      return r;
    }catch(e){ console.warn('plug-in', e); return undefined; }
  },
  // every onReload gets 5 s; a plug-in that never answers must not hold the dashboard
  reload(){
    const limit = f => Promise.race([Promise.resolve().then(f), new Promise(res => setTimeout(res, 5000))])
      .catch(e => console.warn('plug-in', e));
    return Promise.all(this.onReload.map(limit));
  },
  ruleAction(type){ return this.ruleActions.find(a => a.type === type); },
  toolbarButtons(tab){ return this.toolbar.filter(b => b.tab === tab && (!b.visible || this.safe(b.visible))); },
  // the first face whose match(d) is true draws the whole card of d instead of the core body
  // a device a plug-in shows elsewhere (buttons drawn on another card): not in the tab grids
  hidden(d){ return d.transport === 'presence' || this.hides.some(f => this.safe(f, d) === true); },   // presence lives on the home strip
  cardFace(d){ return this.cardFaces.find(f => typeof f.render === 'function' && this.safe(f.match, d)); },
  sectionsFor(d){ return this.modalSections.filter(s => typeof s.html === 'function' && this.safe(s.match, d)); },
  // how long an optimistic value is shown before the device must confirm it: the longest asked for
  holdMs(d, code, value){
    const asked = this.holds.map(h => Number(this.safe(h, d, code, value))).filter(n => Number.isFinite(n) && n > 0);
    return Math.min(60000, Math.max(OPT_HOLD_MS, ...asked));
  },
  // each header entry gets a box of its own in its slot ('brand' next to the name, 'conn' before Log out); then Ask
  renderHeader(){
    this.renderAsk();
    for(const slot of ['brand', 'conn']){
      const host = document.getElementById('plug-' + slot); if(!host) continue;
      host.innerHTML = '';
      for(const h of this.header.filter(x => (x.slot || 'conn') === slot && typeof x.render === 'function')){
        const box = document.createElement('span'); host.append(box);
        this.safe(h.render, box);
      }
    }
  },
};
// dates and times as the UI language writes them, not the browser's locale
const LOCALE = I18N_LANG === 'ru' ? 'ru-RU' : undefined;
// how long ago, in the UI language: minutes below an hour, hours below two days, then days
function agoText(sec){
  const day = 86400;
  return sec < 3600 ? i18n('{0} min ago', Math.max(1, Math.round(sec / 60))) : sec < 2 * day ? i18n('{0} h ago', Math.round(sec / 3600))
    : i18n('{0} days ago', Math.round(sec / day));
}
function fmtWhen(ms){                                   // today "at 15:55", yesterday "yesterday at 15:55", else the date (the year only when not this one)
  const d = new Date(ms), now = new Date(), t = d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit', hour12: false});
  const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime(), ago = Math.round((day(now) - day(d)) / 86400000);
  if(ago === 0) return i18n('at {0}', t);
  if(ago === 1) return i18n('yesterday at {0}', t);
  return i18n('{0} at {1}', d.toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit', ...(d.getFullYear() === now.getFullYear() ? {} : {year: 'numeric'})}), t);
}
// a rare setting a family seldom needs: folded under "Advanced", closed until opened; nothing to fold -> nothing
function advancedHTML(html){
  return html ? `<details class="adv"><summary>${escapeHtml(i18n('Advanced'))}</summary><div class="advbody">${html}</div></details>` : '';
}
const Home = {
  api: (path, opts) => api(path, opts),
  advanced: html => advancedHTML(html),    // HTML of rare settings folded under "Advanced" (closed), as the core's own pages do
  escapeHtml: s => escapeHtml(s), el: (tag, cls, html) => el(tag, cls, html),
  i18n, i18nPlural,
  // opts.onClose runs once when the modal closes (Escape, backdrop, x, Home.closeModal) or another modal replaces it
  openModal(html, id, opts){
    releaseModal();
    _modalOnClose = opts && typeof opts.onClose === 'function' ? opts.onClose : null;
    document.getElementById('md').classList.remove('devoff');
    document.getElementById('md').innerHTML = html;
    document.getElementById('bd').classList.add('show');
    document.body.style.overflow = 'hidden';
    openModalId = id || '__plugin__';
  },
  closeModal: () => closeModal(), modalId: () => openModalId,
  devices: () => DEVICES, state: id => STATE[id], activeTab: () => activeTab,
  sendControl: (id, code, value) => sendControl(id, code, value),
  authHeaders: h => authHeaders(h),   // for a plug-in's own fetch (binary bodies, SDP)
  refresh: () => { renderTabs(); render(); },
  toggleGlyph: code => toggleGlyph(code),  // the class of a switch's sign: power unless its code says mute, lock or microphone
  refreshTabs: () => refreshTabs(),        // the room tabs from the server's rooms again (a plug-in that owns the rooms, after a change)
  can: route => stOk([route]),                              // "GET /api/x": allowed here (restricted mode names what is)
  confirmRoomDelete: room => confirmRoomDelete(room),       // the question before a room goes (the map asks it too)
  // after a plug-in added, renamed or removed devices: the list, favourites and tabs again
  async reloadDevices(){
    DEVICES = localizeDevices(await api('/api/devices'));
    BY_ID = Object.fromEntries(DEVICES.map(x => [x.id, x]));
    await loadFavorites();
    renderTabs(); render();
  },
  tabForCategory: cat => tabForCategory(cat),
  roomOf: id => roomNameOf(id),            // the room a device is in, '' when none
  when: ms => fmtWhen(ms),                 // a date and time in the UI language
  ago: sec => agoText(sec),                // how long ago, from an age in seconds: "5 min ago", "3 h ago", "4 days ago"
  toast: text => stToast(text),            // a short note at the bottom ("Saved")
  askText: (msg, initial, ph) => askText(msg, initial, ph),   // a text question as a small window; null when cancelled
  icon: (name, size) => icon(name, size),  // an SVG icon by name: pen, trash, play, switch, sensor, button, climate, tv, lock, person, camera, ir
  devIcon: d => devIcon(d),                // the kind icon of a device
  kindOf: cat => catLabel(cat),            // the user's word for a category
  // a plug-in opens a tab by its id ("p:<id>"), e.g. from a header button
  openTab(id){ if(!allTabs().some(t => t.id === id) && !(id.startsWith('room:') && roomById(id.slice(5)))) return; activeTab = id; gridEditMode = false;
    rememberTab(activeTab); renderTabs(); render(); },
  openEsphomeAdd: () => openEsphomeAdd(),   // the core's "Add ESPHome" dialog
  openAdd: () => openAddDevice(),           // the header's Add button: a plug-in's addFlow window, else the Add menu
  roomClimate: id => { const r = roomById(id), c = r ? roomClimate(r) : null; return c ? {value: c.value, humidity: c.humidity, note: c.note} : null; },     // the temperature the room's tab shows (same rule), or null
  openDevice: id => openModal(id),         // the window of another device (it replaces the open one)
  rules: async () => (RULES = await api('/api/rules')),     // every rule, as Automations lists them
  ruleCard: r => ruleCard(r),              // one rule's card: tap to edit, run, switch, delete
  newRule: preset => openRuleBuilder(null, preset),   // the rule window, {nfc: '<tag name>'} for a tap of that tag
  watchRules: f => { RULE_WATCH.add(f); return () => RULE_WATCH.delete(f); },   // f() after a rule changed; returns the unwatch
  help: {entries: () => helpEntries(), marks: () => helpMarks().map(m => m.e.id),         // the list; what the screen has hints for (ui_sweep)
    pop: id => { const m = helpMarks().find(k => k.e.id === id); if(m) helpPop(m.ctl, m.e); return !!m; }},
};
// every text the core translates somewhere: a plug-in may add words, never re-word these
const CORE_WORDS = new Set(Object.values(I18N).flatMap(t => Object.keys(t)));
// the server's title of a plug-in's rule action; an absent plug-in's one in the UI language
function pluginThenTitle(t){
  if(t.type === 'plugin-absent') return `${t.action} (${i18n('plug-in not loaded')})`;
  return t.title || t.action || t.type;
}
async function loadPlugins(){
  let r = {loaded: [], failed: []};
  try{ r = await api('/api/plugins'); }catch(e){ /* a core without plug-ins answers too */ }
  HomePlugins.list = r.loaded || []; HomePlugins.failed = r.failed || []; HomePlugins.off = r.off || [];
  for(const p of HomePlugins.list){
    for(const [lang, tbl] of Object.entries(p.i18n || {})){
      if(!/^[a-z]{2}$/.test(lang) || !tbl || typeof tbl !== 'object') continue;
      const dst = I18N[lang] = I18N[lang] || {};
      for(const [k, v] of Object.entries(tbl))
        if(typeof v === 'string' && !(k in dst) && !CORE_WORDS.has(k)
           && !['__proto__', 'constructor', 'prototype'].includes(k)) dst[k] = v;
    }
    HomePlugins._cur = p;                                  // the plug-in whose script registers now
    if(p.ui) await new Promise(res => {
      const sc = document.createElement('script'); sc.src = p.ui + (p.ui_v ? '?v=' + p.ui_v : '');          // the build is in the address: a changed script is never taken from a cache
      const onErr = e => {
        if(document.currentScript === sc || (e.filename && e.filename.split('?')[0].endsWith(p.ui))) p.uiFailed = true;
      };
      window.addEventListener('error', onErr);
      sc.onload = () => { window.removeEventListener('error', onErr); res(); };
      sc.onerror = () => { window.removeEventListener('error', onErr); console.warn('plug-in ui failed', p.id); p.uiFailed = true; res(); };
      document.head.appendChild(sc);
    });
  }
  stRoute();                                            // a page opened at #settings/... (a reload, a link)
}
// core tabs + the plug-ins' (ids "p:<id>"; `after` places one behind a core tab)
function allTabs(){
  const out = TABS.slice();
  // one tab id from several plug-ins is one tab: their categories joined, the first one's label and place
  const byId = new Map();
  for(const t of HomePlugins.tabs){
    const cats = Array.isArray(t.cats) ? t.cats.filter(c => typeof c === 'string') : [];
    const seen = byId.get(t.id);
    if(seen){ for(const c of cats) if(!seen.cats.includes(c)) seen.cats.push(c); continue; }
    byId.set(t.id, {id: 'p:' + t.id, label: i18n(t.label), compact: !!t.compact, plugin: t, cats});
  }
  let todo = [...byId.values()];
  // `after` may name a tab of a plug-in loaded later: place what can be placed, repeat; the rest goes last
  for(let moved = true; todo.length && moved;){
    moved = false;
    todo = todo.filter(tab => {
      const a = tab.plugin.after;
      const at = a ? out.findIndex(x => x.id === a || x.id === 'p:' + a) : out.length - 1;
      if(at < 0) return true;
      out.splice(at + 1, 0, tab); moved = true; return false;
    });
  }
  out.push(...todo);
  return out.filter(t => !t.plugin || !t.plugin.visible || HomePlugins.safe(t.plugin.visible));
}

