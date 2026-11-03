// -- help: one list of entries {id, screen, sel, title, text, more?, at?} (README "Help") ---------------------------
// The header "?" opens all of them as a sheet; dragged onto a control that sel matches (its first visible match, with
// at: 'label' its row's label too) it shows that one entry.
const HELP = [
  {id: 'header.help', screen: 'header', sel: '#helpbtn', title: 'Help',
   text: 'A tap: all the hints in one list, with search; "Show" leads to the control itself. Dragged onto anything: its hint.'},
  {id: 'header.ask', screen: 'header', sel: '#askslot .askbar', title: 'Ask the house',
   text: 'Type a question or what the house should do, then Enter: the answer comes in a window. On a phone the button opens the field.'},
  {id: 'header.settings', screen: 'header', sel: '#gearbtn', title: 'Settings',
   text: 'Everything about the house: language, screens\' sign-in, backups, plug-ins, the house\'s health.',
   more: 'Groups on the left: Overview (the house checks itself), House, Devices, Automation, Voice, This screen, Maintenance (backups, plug-ins, update). The pages of a group are tabs on top.'},
  {id: 'header.add', screen: 'header', sel: '#add-device-btn', title: 'Add device',
   text: 'Connect a new device. The window shows the ways this house has: Zigbee, devices on the home network, tablets and screens, and what the plug-ins can do.'},
  {id: 'nav.search', screen: 'nav', sel: '#navsearch', title: 'Search',
   text: 'Finds a device by its name, room or kind. Several words narrow it. Esc or an empty field brings everything back.'},
  {id: 'nav.places', screen: 'nav', sel: '#tabs', title: 'Places',
   text: 'Favourites, the rooms and the plug-ins\' places. A tap opens one.'},
  {id: 'nav.kinds', screen: 'nav', sel: '#kinds', title: 'By kind',
   text: 'The same devices by kind: switches, sensors, buttons, automations and the rest.'},
  {id: 'nav.addroom', screen: 'nav', sel: '#addroom-chip', title: '+ Room',
   text: 'Only while the house has no room: makes the first one. Later rooms are made on the home map (its bar: New room).'},
  {id: 'tabs.roommenu', screen: 'tabs', sel: '[data-tb="more-room"]', title: 'Room actions',
   text: 'The room\'s own actions: which devices live in it, its name, its outline on the map, and deleting it (asked first).'},
  {id: 'tabs.order', screen: 'tabs', sel: '[data-tb="order"]', title: 'Order',
   text: 'Lets you drag the tiles into the order you want; each move is kept at once. "Done" ends it.'},
  {id: 'auto.new', screen: 'tab:auto', sel: '#rb-new', title: '+ New rule',
   text: 'A rule: when something happens (and a condition holds), the house does an action.'},
  {id: 'settings.restart.full', screen: 'settings:restart', sel: '#st-restart', at: 'label', title: 'Full restart',
   text: 'Makes every service of the house anew. For a stuck Zigbee or devices that stay silent after a network drop.'},
  {id: 'settings.device.logout', screen: 'settings:device', sel: '#st-logout', at: 'label', title: 'Log out',
   text: 'This browser forgets the sign-in; other screens stay signed in.'},
  {id: 'settings.device.install', screen: 'settings:device', sel: '#st-install', at: 'label', title: 'Install as an app',
   text: 'Puts the dashboard on this device as an app: full screen, without the browser bar.'},
  {id: 'settings.lang.houselang', screen: 'settings:houselang', sel: '#st-house-lang', at: 'label', title: 'House language',
   text: 'Voice, clocks and panels speak it. A screen shows it until someone picks another language there.'},
  {id: 'settings.lang.lang', screen: 'settings:lang', sel: '#st-lang', at: 'label', title: 'Dashboard language',
   text: 'Only the dashboard\'s own words change; the names of devices and rooms stay.'},
  {id: 'settings.guest.accept', screen: 'settings:device', sel: '#st-guest-acc', at: 'label', title: 'Take entry requests',
   text: 'Off: nobody can even ask to come in from /guest, and no notice reaches your phone. More than 10 requests in an hour pause them by themselves for an hour.'},
  {id: 'settings.connect.code', screen: 'settings:device', sel: '#st-signin', at: 'label', title: 'Show code',
   text: 'A one-time code for the dashboard in another browser: type it on its sign-in card within 5 minutes.'},
  {id: 'settings.backups.now', screen: 'settings:backups', sel: '#bk-now', title: 'Back up now',
   text: 'An archive of the house\'s settings and data, now. The nightly ones are made by themselves.'},
  {id: 'settings.backups.file', screen: 'settings:backups', sel: '#bk-file', title: 'Restore from a file',
   text: 'Brings the house back from an archive kept elsewhere. What is there now is backed up first.'},
  {id: 'settings.backups.at', screen: 'settings:backups', sel: '#bk-at', title: 'Backup time',
   text: 'When the nightly backup runs; the switch beside it turns it off. Taken at once, no restart.'},
  {id: 'settings.plugins.list', screen: 'settings:plugins', sel: '#st-h-plugins', title: 'Plug-ins',
   text: 'What is loaded and its version; below, switching plug-ins on and off and their keys.'},
  {id: 'settings.plugins.update', screen: 'settings:plugins', sel: '#st-h-update', title: 'Update',
   text: 'A new version of Yashome: what it brings and the button to apply it. A backup is made first.'},
  {id: 'settings.health', screen: 'settings:overview', sel: '#st-health', title: 'Health of the house',
   text: 'The house checks itself: broker, clock, disk, backups, helpers. A yellow or red row says what to do.'},
  {id: 'settings.demo', screen: 'settings:overview', sel: '#st-demo', title: 'Demo host',
   text: 'A computer the house watches too (name or address, :port if not 443): yellow on this page when it does not answer.'},
  {id: 'settings.demo.save', screen: 'settings:overview', sel: '#st-demo-save', title: 'Save',
   text: 'Keeps the demo host; empty: none is watched.'},
  {id: 'device.rename', screen: 'device', sel: '#m_ren_dev', title: 'Rename',
   text: 'The device\'s name on the cards and in the rules.'},
  {id: 'device.room', screen: 'device', sel: '#m_room', at: 'label', title: 'Room',
   text: 'The room the device stands in; its tab shows it unless Serves names another. A grey field: the map sets the room - the line under it names the zone.'},
  {id: 'device.serves', screen: 'device', sel: '#m_serves', at: 'label', title: 'Serves',
   text: 'The room the device works for: its voice commands, its room tab and family rights go by it. The map keeps where it stands.'},
  {id: 'device.roommap', screen: 'device', sel: '#m_room_map', title: 'Show on the map',
   text: 'Opens the map at the device\'s icon. Move the icon into another zone to change its room.'},
  {id: 'device.fav', screen: 'device', sel: '#m_fav', at: 'label', title: 'Favourite',
   text: 'Puts the device\'s main channel on the Favourites tab, or takes it off.'},
  {id: 'device.toggle', screen: 'device', sel: '#md .chgroup [data-act="toggle"]', at: 'label', title: 'On / off',
   text: 'Switches this channel. Does nothing while the device is offline.'},
  {id: 'device.renchan', screen: 'device', sel: '#md [data-ren-chan]', title: 'Rename channel',
   text: 'A name for this channel, on the card and in the rules.'},
  {id: 'device.bright', screen: 'device', sel: '#md [data-act="bright"]', at: 'label', title: 'Brightness',
   text: 'The level of this channel; it is sent when you let go.'},
  {id: 'device.dimwin', screen: 'device', sel: '#md .dimwin:not([hidden])', title: 'Limits',
   text: 'The lowest and highest level the slider and the card use. 0 and 100: no limits.'},
  {id: 'device.readings', screen: 'device', sel: '#md h3[data-h="readings"]', title: 'Readings',
   text: 'What the device measures, as it last reported.'},
  {id: 'device.settings', screen: 'device', sel: '#md h3[data-h="settings"]', title: 'Device settings',
   text: 'The device\'s own settings; a change goes to the device at once.'},
  {id: 'device.presslog', screen: 'device', sel: '#md h3[data-h="presslog"]', title: 'Press log',
   text: 'The last presses of its buttons, with the time.'},
  {id: 'device.rules', screen: 'device', sel: '#md h3[data-h="rules"]', title: 'Used in automations',
   text: 'The rules this device takes part in. They are edited on the Automations tab.'},
  {id: 'device.details', screen: 'device', sel: '#md details[data-h="details"] > summary', title: 'Details',
   text: 'Connection, model, address, id, service readings and the raw state.'},
  {id: 'device.ok', screen: 'device', sel: '#m_modal_ok', title: 'OK',
   text: 'Appears after a change in a section that is saved on OK; closing the window without it drops the change.'},
];
// every entry: the core's, the plug-ins' (register({help})) and one per plug-in toolbar button that has help
function helpEntries(){
  const tb = HomePlugins.toolbar.filter(b => b.help).map(b => ({id: 'toolbar.' + b.label, screen: b.tab === 'auto' ? 'tab:auto' : 'tabs',
    sel: `[data-tb="${CSS.escape(b.label)}"]`, title: b.label, ...b.help}));
  return [...HELP, ...tb, ...HomePlugins.help];
}
// a control's entry: its first visible match; at: 'label' lets its row's label (or the label line over it) stand for it too
function helpTarget(e){
  const all = [...document.querySelectorAll(e.sel)];
  const ctl = all.find(x => x.getClientRects().length) || all[0];
  if(!ctl) return null;
  if(e.at === 'label'){                              // the row's label, or the label line just above (a slider)
    const row = ctl.closest('.row'), prev = ctl.previousElementSibling;   // a field under its own label line (.asklbl) too
    const up = ctl.parentNode && ctl.parentNode.previousElementSibling;      // a plug-in's label line over the control's own row (.hqlbl)
    const lbl = (row && row.querySelector('.rlabel')) || (prev && (prev.matches('.asklbl, .hqlbl') ? prev : prev.querySelector('.rlabel')))
      || (up && up.matches('.hqlbl') ? up : null);
    if(lbl) return {ctl, label: lbl};
  }
  return {ctl, label: null};
}
// what the screen has hints for now: each entry whose control shows, with the elements that stand for it
function helpMarks(){
  return helpEntries().map(e => { const t = e.id === 'header.help' ? null : helpTarget(e);
    return t && t.ctl.getClientRects().length ? {e, ctl: t.ctl, els: [t.ctl, t.label].filter(Boolean)} : null; }).filter(Boolean);
}
function helpUnder(x, y, marks){                     // the innermost marked element under a point
  for(let n = document.elementFromPoint(x, y); n && n !== document.documentElement; n = n.parentElement){
    const m = marks.find(k => k.els.includes(n));
    if(m) return m;
  }
  return null;
}
// the header's "?": a tap (or Enter) opens the sheet; dragged, it turns green over a control that has an entry and,
// let go there, shows that entry. A finger would hide the control: on touch the "?" rides 56 px above it.
function helpDrag(){
  const btn = document.getElementById('helpbtn');
  btn.setAttribute('aria-haspopup', 'dialog');
  btn.addEventListener('pointerdown', ev => {
    if(ev.button) return;
    const x0 = ev.clientX, y0 = ev.clientY, lift = ev.pointerType === 'touch' ? 56 : 0;
    let ghost = null, nm = null, on = null, marks = [];
    const mark = m => { if(on) on.ctl.classList.remove('hq-target'); on = m; if(m) m.ctl.classList.add('hq-target'); };
    const move = e2 => {
      if(!ghost){
        if(Math.hypot(e2.clientX - x0, e2.clientY - y0) < 8) return;
        helpPopClose(); marks = helpMarks();
        ghost = Object.assign(document.createElement('div'), {className: 'hq-drag', textContent: '?'});
        nm = Object.assign(document.createElement('div'), {className: 'hq-name', hidden: true});
        document.body.append(ghost, nm);
      }
      const x = e2.clientX, y = e2.clientY - lift, m = helpUnder(x, y, marks);
      ghost.style.left = x + 'px'; ghost.style.top = y + 'px';
      if(m !== on) mark(m);
      ghost.classList.toggle('on', !!m); nm.hidden = !m;
      if(!m) return;
      nm.textContent = i18n(m.e.title);                // the title over the "?", under it at the top edge, never past a side
      const w = nm.offsetWidth, h = nm.offsetHeight;
      nm.style.left = Math.max(8, Math.min(innerWidth - w - 8, x - w / 2)) + 'px';
      nm.style.top = (y - 30 - h > 8 ? y - 30 - h : y + 30) + 'px';
    };
    const up = () => {
      removeEventListener('pointermove', move); removeEventListener('pointerup', up); removeEventListener('pointercancel', up);
      if(!ghost) return;                             // a tap: its click opens the sheet
      ghost.remove(); nm.remove(); btn.dataset.dragged = '1';
      const m = on; mark(null);
      setTimeout(() => { delete btn.dataset.dragged; if(m) helpPop(m.ctl, m.e); }, 0);   // after the click this release makes
    };
    addEventListener('pointermove', move); addEventListener('pointerup', up); addEventListener('pointercancel', up);
  });
  btn.addEventListener('click', ev => { if(btn.dataset.dragged){ ev.stopImmediatePropagation(); ev.preventDefault(); } }, true);
  if(!localStorage.getItem('help.tip')){             // once per browser: how this "?" is used
    localStorage.setItem('help.tip', '1');
    setTimeout(() => { if(document.body.classList.contains('stx-open')) return; btn.classList.add('hq-wiggle'); helpPop(btn, {id: 'help.tip', title: 'Help', text: 'Tap it for all the hints. Drag it onto anything to see what that is.'}); }, 1200);
  }
}
function helpPopClose(){ document.querySelectorAll('.hq-pop').forEach(p => p.remove()); }
function helpPop(btn, e){
  const was = document.querySelector('.hq-pop');
  helpPopClose();
  if(was && was.dataset.hq === e.id) return;
  const p = document.createElement('div');
  p.className = 'hq-pop'; p.dataset.hq = e.id; p.setAttribute('role', 'dialog');
  p.innerHTML = `<b>${escapeHtml(i18n(e.title))}</b><div>${escapeHtml(i18n(e.text))}</div>
    <button type="button" class="dlg-btn hq-more">${escapeHtml(i18n('More'))}</button>`;
  p.querySelector('.hq-more').onclick = () => { helpPopClose(); openHelp(e.id); };
  document.body.append(p);
  const r = btn.getBoundingClientRect(), w = p.offsetWidth, h = p.offsetHeight;
  p.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + 'px';
  p.style.top = (r.bottom + 6 + h > innerHeight - 8 ? Math.max(8, r.top - h - 6) : r.bottom + 6) + 'px';
  p.querySelector('.hq-more').focus();
}
document.addEventListener('click', ev => { if(!ev.target.closest('.hq-pop,#helpbtn')) helpPopClose(); });
document.addEventListener('keydown', ev => {
  if(ev.key !== 'Escape') return;
  const p = document.querySelector('.hq-pop'), s = document.getElementById('hqsheet');
  if(p){ helpPopClose(); document.getElementById('helpbtn').focus(); ev.stopPropagation(); }
  else if(s){ s.remove(); document.getElementById('helpbtn').focus(); ev.stopPropagation(); }
}, true);
document.addEventListener('scroll', helpPopClose, true);
// the sheet: every entry by screen, a search over title and text, "Show" leads to the control and pulses it
// a settings page by its id, or (a plug-in's page, whose id follows the load order) by its English title
const helpPage = s => ST_PAGES().find(x => x.id === (ST_ALIAS[s.slice(9).split('/')[0]] || s.slice(9).split('/')[0])) || ST_PAGES().find(x => (x.titles || []).includes(s.slice(9)));   // settings:<page>[/<tab>]
function helpScreenName(s, named){
  if(s === 'header') return i18n('Header');
  if(s === 'nav') return i18n('Places and kinds');
  if(s === 'tabs') return i18n('Device tabs');
  if(s === 'device') return i18n('Device window');
  if(s.startsWith('settings:')){ const p = helpPage(s); return i18n('Settings') + (p ? ' \u2192 ' + i18n(p.title) : ''); }
  if(s.startsWith('tab:')){ const t = allTabs().find(x => x.id === s.slice(4)); return t ? t.label : s.slice(4); }
  return named ? i18n(named) : s;                     // a plug-in's window: the screenName of its entries
}
function openHelp(focusId){
  helpPopClose();
  let sh = document.getElementById('hqsheet');
  if(sh) sh.remove();
  sh = document.createElement('div');
  sh.id = 'hqsheet'; sh.setAttribute('role', 'dialog'); sh.setAttribute('aria-label', i18n('Help'));
  sh.innerHTML = `<div class="hq-head"><h2>${escapeHtml(i18n('Help'))}</h2><button type="button" class="dlg-btn hq-x">${escapeHtml(i18n('Close'))}</button></div>
    <input type="search" class="hq-q" placeholder="${escapeHtml(i18n('Search the help'))}" aria-label="${escapeHtml(i18n('Search the help'))}">
    <div class="hq-list"></div>`;
  document.body.append(sh);
  sh.querySelector('.hq-x').onclick = () => sh.remove();
  const list = sh.querySelector('.hq-list'), q = sh.querySelector('.hq-q');
  const draw = () => {
    const words = q.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const found = helpEntries().filter(e => { const hay = [e.title, e.text, e.more].filter(Boolean).map(x => i18n(x)).join(' ').toLowerCase(); return words.every(w => hay.includes(w)); });
    const groups = [...new Set(found.map(e => e.screen))];
    list.innerHTML = groups.map(g => `<h3>${escapeHtml(helpScreenName(g, (found.find(e => e.screen === g && e.screenName) || {}).screenName))}</h3>` + found.filter(e => e.screen === g).map(e =>
      `<div class="hq-item" data-hq="${escapeHtml(e.id)}"><b>${escapeHtml(i18n(e.title))}</b><div>${escapeHtml(i18n(e.text))}</div>
        ${e.more ? `<div class="bindcaption">${escapeHtml(i18n(e.more))}</div>` : ''}
        <button type="button" class="dlg-btn hq-show" data-hq="${escapeHtml(e.id)}"${helpCanShow(e) ? '' : ` disabled title="${escapeHtml(i18n(e.screen === 'device' ? 'Open a device window first' : 'Open that window first'))}"`}>${escapeHtml(i18n('Show'))}</button></div>`).join('')).join('')
      || `<div class="bindcaption">${escapeHtml(i18n('Nothing found'))}</div>`;
    list.querySelectorAll('.hq-show').forEach(b => b.onclick = () => helpShow(helpEntries().find(e => e.id === b.dataset.hq)));
  };
  q.oninput = draw;
  draw();
  const it = focusId && [...list.querySelectorAll('.hq-item')].find(x => x.dataset.hq === focusId);
  if(it){ it.scrollIntoView({block: 'center'}); it.classList.add('hq-pulse'); it.querySelector('.hq-show').focus(); }
  else q.focus();
}
const helpDeviceOpen = () => !!(openModalId && BY_ID[openModalId]);
// Show opens a tab or a settings page; a device window or a plug-in's own window (opening it may start something,
// such as pairing) only when it is on the screen already
const helpOpens = e => ['header', 'nav', 'tabs'].includes(e.screen) || e.screen.startsWith('tab:') || (e.screen.startsWith('settings:') && !!helpPage(e.screen));
function helpCanShow(e){ return helpOpens(e) || (e.screen === 'device' && helpDeviceOpen()) || !!document.querySelector(e.sel); }
async function helpShow(e){
  if(!e) return;
  const sh = document.getElementById('hqsheet'); if(sh) sh.remove();
  if(!helpOpens(e)){ if(!helpCanShow(e)) return; }
  else if(e.screen.startsWith('settings:')) openSettings(helpPage(e.screen).id, e.screen.slice(9).split('/')[1] || '');
  else {
    if(document.getElementById('bd').classList.contains('show')) closeModal();
    if(e.screen.startsWith('tab:')) Home.openTab(e.screen.slice(4));
  }
  for(let i = 0; i < 40; i++){
    const t = helpTarget(e);
    if(t && t.ctl.getClientRects().length){
      t.ctl.scrollIntoView({block: 'center'});
      t.ctl.classList.remove('hq-pulse'); void t.ctl.offsetWidth; t.ctl.classList.add('hq-pulse');
      setTimeout(() => t.ctl.classList.remove('hq-pulse'), 2000);
      return;
    }
    await new Promise(r => setTimeout(r, 50));
  }
}

