// PWA: register service worker + custom "Install" button (Chrome auto-prompt
// isn't guaranteed; the button fires the captured beforeinstallprompt).
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
(function(){
  let prompt = null;
  const btn = document.getElementById('install-btn');
  addEventListener('beforeinstallprompt', e => { e.preventDefault(); prompt = e; if(btn) btn.dataset.ready = '1'; });   // Settings -> This device shows it
  addEventListener('appinstalled', () => { if(btn) delete btn.dataset.ready; });
  if (btn) btn.onclick = async () => {
    if (!prompt) return;
    prompt.prompt(); await prompt.userChoice; prompt = null; delete btn.dataset.ready;
  };
})();

Object.assign(window, {Home, HomePlugins, closeModal, logout, modalOk, openHelp, openRoomSensors, openSettings, setLang});
let _initDone = false;
init().catch(e=>{
  if(e.message === '401') return;                    // the sign-in card is on screen
  $('#app').innerHTML = `<div class="skeleton">${i18n('Error:')} ${escapeHtml(e.message)}</div>`;
});

// --- auto-refresh structural data on resume -------------------------------
// Devices / favorites load once in init(); live values come
// over WebSocket. When the PWA is backgrounded and config changes server-side
// (new favorite, freshly paired device), the running tab never re-fetches the
// list. Re-pull it whenever the app regains visibility or focus, throttled so a
// visibility+focus burst doesn't double-fetch, and skip re-render mid-modal.
let _lastSoftReload = 0;
async function softReload(){
  if (!_initDone) return;   // init still retrying: its own load shows the stored tab
  const now = Date.now();
  if (now - _lastSoftReload < 3000) return;
  _lastSoftReload = now;
  try {
    DEVICES = localizeDevices(await api('/api/devices'));
    BY_ID = Object.fromEntries(DEVICES.map(d => [d.id, d]));
    await loadFavorites();
    try { RULES = await api('/api/rules'); } catch(e){ }
    await HomePlugins.reload();
    // a plug-in tab that is no longer offered (visible() false now) would render empty
    if (!activeTab.startsWith('room:') && !allTabs().some(t => t.id === activeTab)) activeTab = 'fav';
    if (!openModalId && (ST.page === 'rules' || RULE_WATCH.size)) rulesRedraw();     // Settings -> Automations (or NFC tags) after a rule's window closed
    if (!openModalId) {
      const pt = allTabs().find(t => t.id === activeTab);
      // a plug-in's own page is not redrawn (a form being typed): its onReload calls Home.refresh()
      if (pt && pt.plugin && !(pt.cats.length && !pt.plugin.render)) renderTabs();
      else if (activeTab === 'auto') renderAutomations($('#app'), true);   // in place, no jump to the top
      else { renderTabs(); render(); }
    }
  } catch(e){ /* offline / transient -- WS reconnect recovers live state */ }
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') softReload();
});
window.addEventListener('focus', softReload);
