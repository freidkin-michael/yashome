async function init(){
  translateDom(document);   // static shell; dynamic markup goes through i18n()
  // CSS cannot call i18n(): the "offline" caption of a card comes in as a variable
  document.documentElement.style.setProperty('--offline-txt', JSON.stringify(i18n('offline')));
  document.documentElement.style.setProperty('--waiting-txt', JSON.stringify(i18n('waiting for report')));
  document.addEventListener('keydown', e => {
    if(e.key === 'Escape' && openModalId) closeModal();
    // Tab stays inside an open modal: focus wraps from the last control to the first and back
    if(e.key === 'Tab' && openModalId){
      const md = document.getElementById('md');
      const f = [...md.querySelectorAll('button, [href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])')]
        .filter(x => !x.disabled && x.offsetParent !== null);
      if(f.length){
        const first = f[0], last = f[f.length - 1];
        if(e.shiftKey && (document.activeElement === first || document.activeElement === md)){ e.preventDefault(); last.focus(); }
        else if(!e.shiftKey && document.activeElement === last){ e.preventDefault(); first.focus(); }
      }
    }
    // on/off toggles are divs: Enter / Space act like a click once they have focus
    if((e.key === 'Enter' || e.key === ' ') && e.target.classList
        && (e.target.classList.contains('toggle') || e.target.classList.contains('card'))){
      e.preventDefault();
      if(e.repeat) return;                       // a held key acts once
      // a switch on the card of an offline device does nothing (its card itself still opens)
      if(!(e.target.classList.contains('toggle') && e.target.closest('.card.offline'))) e.target.click();
    }
  });
  // every toggle, whenever it is rendered, is a focusable switch; a modal takes the focus
  new MutationObserver(muts => {
    for(const t of document.querySelectorAll('.toggle:not([tabindex])')){
      t.tabIndex = 0; t.setAttribute('role', 'switch');
    }
    // aria-checked follows the "on" class whenever a toggle is drawn or flips
    for(const m of muts){
      const els = m.type === 'attributes' ? [m.target] : [...m.addedNodes].filter(n => n.querySelectorAll)
        .flatMap(n => [n, ...n.querySelectorAll('.toggle')]);
      for(const t of els) if(t.classList && t.classList.contains('toggle')){
        const v = t.classList.contains('unknown') ? 'mixed' : t.classList.contains('on') ? 'true' : 'false';
        if(t.getAttribute('aria-checked') !== v) t.setAttribute('aria-checked', v);
        if(!t.dataset.ownTitle){                                  // the word is for the pointer's tooltip and the screen reader, not on the button
          if(t.title && t.dataset.autoTitle !== t.title) t.dataset.ownTitle = '1';
          else { const w = i18n(v === 'mixed' ? 'Unknown' : v === 'true' ? 'On' : 'Off'); if(t.title !== w){ t.title = w; t.dataset.autoTitle = w; } }
        }
      }
      const dots = m.type === 'attributes' ? [m.target] : [...m.addedNodes].filter(n => n.querySelectorAll)
        .flatMap(n => [...n.querySelectorAll('.dot')]);
      for(const t of dots) if(t.classList && t.classList.contains('dot') && t.parentNode){
        const v = t.classList.contains('on') ? 'true' : 'false';
        const sw = t.getAttribute('role') === 'switch' ? t : t.parentNode;
        if(sw.getAttribute('role') === 'switch' && sw.getAttribute('aria-checked') !== v) sw.setAttribute('aria-checked', v);
      }
    }
    const md = document.getElementById('md');
    if(openModalId && md && !md.contains(document.activeElement) && !document.querySelector('.askbd')){   // a question window over the modal keeps its focus
      if(!_focusBeforeModal) _focusBeforeModal = document.activeElement;   // given back on close
      md.focus();
    }
  }).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['class']});
  // a tablet may boot before the backend: keep asking (the WebSocket starts only after this)
  for(let wait = 2000;; wait = Math.min(wait * 2, 30000)){
    try{ DEVICES = localizeDevices(await api('/api/devices')); break; }
    catch(e){
      if(e.message === '401') throw e;   // the sign-in card is on screen
      $('#app').innerHTML = `<div class="skeleton">${i18n('Error:')} ${escapeHtml(e.message)} \u2014 ${i18n('reconnecting\u2026')}</div>`;
      await new Promise(res => setTimeout(res, wait));
    }
  }
  BY_ID = Object.fromEntries(DEVICES.map(d => [d.id, d]));
  await loadFavorites();
  try { RULES = await api('/api/rules'); } catch(e){ RULES = []; }
  await loadPlugins();
  translateDom(document);   // again: plug-in dictionaries are in now
  HomePlugins.renderHeader();
  helpDrag();                                      // the header "?": a tap opens the sheet, a drag points at one control
  DEVICES = localizeDevices(DEVICES);   // and device labels, now with the plug-ins' words
  BY_ID = Object.fromEntries(DEVICES.map(d => [d.id, d]));
  let wanted = activeTab;                          // a plug-in tab may only become visible after its reload
  if(!allTabs().some(t => t.id === activeTab) && !activeTab.startsWith('room:')) activeTab = 'fav';
  HomePlugins.reload().then(() => {
    const shown = id => id.startsWith('room:') || allTabs().some(t => t.id === id);
    if(!shown(wanted) && shown('p:' + wanted) && localStorage.getItem('home.tab') === wanted){   // ?tab=<plug-in tab id>
      wanted = 'p:' + wanted; rememberTab(wanted);
    }
    // the stored tab, unless the user has picked one meanwhile (a pick is stored)
    if(activeTab !== wanted && shown(wanted) && localStorage.getItem('home.tab') === wanted) activeTab = wanted;
    else if(!shown(activeTab)) activeTab = 'fav';  // the plug-in hid it
    else { renderTabs(); return; }                 // badges only: no scroll/focus reset
    renderTabs(); if(!openModalId) render();
  });
  renderTabs();
  render();
  connectWS();
  _initDone = true;
  document.getElementById('bd').onclick = e => {
    if(e.target.id === 'bd') closeModal();
  };
  document.getElementById('add-device-btn').onclick = openAddDevice;
  if(TOKEN.startsWith('yg_')){                               // a guest browser: no setting-up controls, its time in the header
    document.body.classList.add('guest');
    const until = +localStorage.getItem('hs_guest_until') || 0, conn = document.getElementById('conn');
    if(conn && until){ conn.dataset.guest = i18n('Guest until {0}', new Date(until).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})); conn.textContent = conn.dataset.guest; conn.classList.add('guestmark'); }
  }
  const ns = document.getElementById('navsearch');
  ns.oninput = () => {
    SEARCH_Q = ns.value;
    if(SEARCH_Q.trim()){ if(activeTab !== 'search'){ _tabBeforeSearch = activeTab; activeTab = 'search'; } }
    else if(activeTab === 'search'){ activeTab = _tabBeforeSearch || 'fav'; }
    renderTabs(); render();
  };
  ns.onkeydown = e => { if(e.key === 'Escape'){ ns.value = ''; ns.oninput(); ns.blur(); } };
  ns.onfocus = () => ns.classList.add('open');
  ns.onblur = () => { if(!ns.value) ns.classList.remove('open'); };
}

async function loadFavorites(){
  try{ FAVORITES = await api('/api/favorites'); }
  catch(e){ FAVORITES = []; }
  try{ ROOMS = await api('/api/rooms'); await loadRoomLocks(); }
  catch(e){ ROOMS = []; }
  try{ TAB_ORDER = await api('/api/tab_order'); }
  catch(e){ TAB_ORDER = {}; }
  try{ TILE_SIZES = await api('/api/tile_sizes'); }
  catch(e){ TILE_SIZES = {}; }
}


