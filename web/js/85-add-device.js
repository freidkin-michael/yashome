// ---------- Add device flow ----------


function openAddDevice(){
  if(HomePlugins.addFlow.length) return HomePlugins.safe(HomePlugins.addFlow[0]);   // a plug-in's one window for every way (connect)
  releaseModal();
  const md = document.getElementById('md');
  md.innerHTML = `
    <div class="head">
      <button class="closebtn" onclick="closeModal()">\u00d7</button>
      <h2>${i18n('Add device')}</h2>
      <div class="sub">${i18n('Choose how the device joins the home.')}</div>
    </div>
    <div class="body">
      <section>
        <div class="row" id="ad-esp" style="cursor:pointer">
          <span class="rlabel">${icon('wifi', 16)} ${i18n('Wi-Fi device')}<br><span style="color:var(--dim);font-weight:400;font-size:12px">${i18n('Finds the house\'s own Wi-Fi devices that are switched on')}</span></span>
          <button class="dlg-btn primary">${i18n('Find')}</button>
        </div>
        ${HomePlugins.addMenu.map((e, i) => `<div class="row" id="ad-p${i}" style="cursor:pointer">
          <span class="rlabel">${escapeHtml(i18n(e.label))}<br><span style="color:var(--dim);font-weight:400;font-size:12px">${escapeHtml(i18n(e.hint || ''))}</span></span>
          <button class="dlg-btn primary">${escapeHtml(i18n(e.button || 'Open'))}</button>
        </div>`).join('')}
      </section>
    </div>`;
  document.getElementById('bd').classList.add('show');
  document.body.style.overflow = 'hidden';
  openModalId = '__add_device__';
  md.querySelector('#ad-esp').onclick = openEsphomeAdd;
  HomePlugins.addMenu.forEach((e, i) => { md.querySelector('#ad-p' + i).onclick = () => HomePlugins.safe(e.onClick); });
}


// The tab of a freshly added node, from what it has: anything to switch -> Switches, readings only -> Sensors
function espCategory(n){
  const c = (n.controls || []).filter(x => !x.aux);       // a node's own setting (status LED, ...) is no light or switch
  return c.some(x => !x.ro && x.kind !== 'sensor') ? 'switch' : c.length ? 'sensor' : 'other';
}

async function openEsphomeAdd(){
  releaseModal();
  const md = document.getElementById('md');
  md.innerHTML = `
    <div class="head">
      <button class="closebtn" onclick="closeModal()">\u00d7</button>
      <h2>${i18n('Add a Wi-Fi device')}</h2>
      <div class="sub">${i18n('The house\'s Wi-Fi devices that are switched on are listed here: press Add to put one on the dashboard.')}</div>
    </div>
    <div class="body"><section><h3>${i18n('Found on the network')}</h3>
      <div id="esp-list"><div class="bindcaption">${i18n('Looking on the network\u2026')}</div></div>
    </section>
    <section><details><summary style="cursor:pointer;font-weight:600">${i18n('Advanced')}</summary>
      <div class="bindcaption" style="margin-top:8px">${i18n('For your own firmware: what to put into an ESPHome device so the house finds it.')}</div>
      <div id="esp-mqtt" style="margin-top:8px"></div></details></section></div>`;
  document.getElementById('bd').classList.add('show');
  document.body.style.overflow = 'hidden';
  openModalId = '__esphome_add__';
  renderMqttBlock(document.getElementById('esp-mqtt'), '');
  let r;
  try{ r = await api('/api/esphome/discover'); }
  catch(e){
    const l = document.getElementById('md').querySelector('#esp-list');
    if(l) l.innerHTML = `<div class="bindcaption" style="color:var(--bad)">${i18n('Could not look on the network: close this window and try again in a minute.')}</div>`;
    console.warn('esphome discover', e);
    return;
  }
  if(openModalId !== '__esphome_add__') return;   // closed while scanning
  renderEsphomeList(r.nodes || []);
}

// The mqtt: block for a node's yaml, with a device account (MQTT_DEVICE_USERS); the password goes to secrets.yaml
async function renderMqttBlock(box, account){
  if(!box) return;
  let b;
  try { b = await api('/api/esphome/mqtt_block?account=' + encodeURIComponent(account || '')); }
  catch(e){ box.innerHTML = `<div class="bindcaption">${escapeHtml(e.message || String(e))}</div>`; return; }
  if(!b.accounts.length){
    box.innerHTML = `<div class="bindcaption">${escapeHtml(i18n('The house has no password for its devices yet. Whoever looks after the house adds it: MQTT_DEVICE_USERS=esp and MQTT_ESP_PASSWORD in the .env file, then make update.'))}</div>`;
    return;
  }
  const pre = t => `<pre style="white-space:pre-wrap;user-select:all;background:var(--card2);padding:8px;border-radius:8px;margin:6px 0">${escapeHtml(t)}</pre>`;
  box.innerHTML = `<div class="row"><span class="rlabel">${escapeHtml(i18n('Device account'))}</span>
      <select id="esp-acc">${b.accounts.map(a => `<option${a === b.account ? ' selected' : ''}>${escapeHtml(a)}</option>`).join('')}</select></div>
    ${b.warning ? `<div class="bindcaption" style="color:var(--bad)">${escapeHtml(i18n(b.warning))}</div>` : ''}
    <div class="bindcaption">${escapeHtml(i18n('Into the device\'s yaml (api: may stay):'))}</div>${pre(b.yaml)}
    <div class="bindcaption">${escapeHtml(i18n('Into its secrets.yaml (the password is not shown here):'))}</div>
    <pre id="esp-secret" style="white-space:pre-wrap;background:var(--card2);padding:8px;border-radius:8px;margin:6px 0">${escapeHtml(maskSecrets(b.secret))}</pre>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="dlg-btn" id="esp-copy">${escapeHtml(i18n('Copy secrets.yaml'))}</button>
      <button class="dlg-btn" id="esp-show">${escapeHtml(i18n('Show for 15 s'))}</button><span class="bindcaption" id="esp-copied"></span></div>
    <div class="bindcaption">${escapeHtml(i18n('Flash the device; once it is online it shows up above (open this window again).'))}</div>`;
  box.querySelector('#esp-acc').onchange = ev => renderMqttBlock(box, ev.target.value);
  const say = t => { const o = box.querySelector('#esp-copied'); if(o){ o.textContent = t; setTimeout(() => { if(o.textContent === t) o.textContent = ''; }, 4000); } };
  box.querySelector('#esp-copy').onclick = async () => {
    try { await navigator.clipboard.writeText(b.secret); say(i18n('Copied')); }
    catch(e){ say(i18n('The browser did not allow copying: use Show')); }
  };
  let shownUntil = null;
  box.querySelector('#esp-show').onclick = () => {
    const pre = box.querySelector('#esp-secret'); if(!pre) return;
    pre.textContent = b.secret;
    clearTimeout(shownUntil);
    shownUntil = setTimeout(() => { if(pre.isConnected) pre.textContent = maskSecrets(b.secret); }, 15000);
  };
}
// a secrets.yaml text with every value replaced by dots: the keys stay readable, the passwords do not
function maskSecrets(text){
  return String(text || '').replace(/^(\s*[\w-]+:\s*).+$/gm, (m, k) => k + '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022');
}

// Rendered once per discovery -- no polling here, so nothing can steal focus
// from the name field the way a polled list would.
function renderEsphomeList(nodes){
  const list = document.getElementById('md').querySelector('#esp-list');
  if(!list) return;
  if(!nodes.length){
    list.innerHTML = '<div class="bindcaption">' + i18n('Nothing found. Switch the device on and connect it to the house Wi-Fi, then open this window again.') + '</div>';
    return;
  }
  const dim = t => `<br><span style="color:var(--dim);font-weight:400;font-size:12px">${escapeHtml(t)}</span>`;
  list.innerHTML = nodes.map(n=>{
    // alive: 'online' | 'offline' | null. null = the node never published its LWT: it is gone, only its leftovers are on the
    // broker; they need cleaning up, not "adding"
    if(n.alive == null && !n.registered) return `<div class="row">
      <span class="rlabel">${icon('warn', 14)} ${escapeHtml(n.title)}${dim(i18n('Not answering: it was taken away. Forget it so it is not listed again.'))}</span>
      <button class="dlg-btn esp-purge" data-node="${escapeHtml(n.node)}">${i18n('Forget')}</button></div>`;
    if(n.registered) return `<div class="row">
      <span class="rlabel">${icon('check', 14)} ${escapeHtml((BY_ID[n.device_id] || {}).name || n.title)}${dim(i18n('already on the dashboard'))}</span>
      <button class="dlg-btn esp-del" data-node="${escapeHtml(n.node)}" data-id="${escapeHtml(n.device_id||'')}">${i18n('Remove')}</button></div>`;
    if(!n.n_controls) return `<div class="row">
      <span class="rlabel">${escapeHtml(n.title)}${dim(i18n('nothing on it the house can show or switch'))}</span></div>`;
    const off = n.alive !== 'online' ? dim(i18n('switched off now')) : '';
    return `<div class="row" style="flex-wrap:wrap;gap:8px">
      <span class="rlabel">${escapeHtml(n.title)}${off}</span>
      <span style="display:flex;gap:6px;flex-wrap:wrap">
        <input class="esp-name" data-node="${escapeHtml(n.node)}" placeholder="${i18n('name')}" aria-label="${i18n('name')}"
          value="${escapeHtml(n.title)}"
          style="background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 10px;font:inherit;width:140px">
        <button class="dlg-btn primary esp-add" data-node="${escapeHtml(n.node)}">${i18n('Add')}</button>
      </span></div>`;
  }).join('');
  list.querySelectorAll('.esp-purge').forEach(btn=>{
    btn.onclick = async ()=>{
      const node = btn.dataset.node, hit = nodes.find(x => x.node === node);
      if(!confirm(i18n('Forget "{0}"? If it is ever switched on again, it shows up here again.', hit ? hit.title : node))) return;
      btn.disabled = true; btn.textContent = '\u2026';
      try{
        await api('/api/esphome/purge_discovery', {method:'POST',
          headers:{'Content-Type':'application/json'}, body: JSON.stringify({node})});
        openEsphomeAdd();        // redrawing the list is the confirmation
      }catch(e){
        btn.disabled = false; btn.textContent = i18n('Forget');
        alert(i18n('Failed: ') + (e.message||e));
      }
    };
  });
  list.querySelectorAll('.esp-del').forEach(btn=>{
    btn.onclick = async ()=>{
      const id = btn.dataset.id, node = btn.dataset.node, hit0 = nodes.find(x => x.node === node);
      if(!id){ alert(i18n('This device was set up by hand in the house files: whoever looks after the house removes it there.')); return; }
      if(!confirm(i18n('Remove "{0}" from the dashboard? The device itself is not touched.', hit0 ? hit0.title : id))) return;
      btn.disabled = true; btn.textContent = '\u2026';
      try{
        await api('/api/esphome/remove', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id}),
        });
        DEVICES = localizeDevices(await api('/api/devices'));
        BY_ID = Object.fromEntries(DEVICES.map(d => [d.id, d]));
        renderTabs(); render();
        const hit = nodes.find(x => x.node === node);
        if(hit){ hit.registered = false; hit.device_id = null; }
        renderEsphomeList(nodes);
      }catch(e){
        btn.disabled = false; btn.textContent = i18n('Remove');
        alert(i18n('Could not remove: ') + (e.message || e));
      }
    };
  });
  list.querySelectorAll('.esp-add').forEach(btn=>{
    btn.onclick = async ()=>{
      const node = btn.dataset.node;
      const nm = list.querySelector(`.esp-name[data-node="${CSS.escape(node)}"]`);
      // a node that is not answering gets a card that stays offline: ask first
      const hitNow = nodes.find(x => x.node === node);
      let force = false;
      if(hitNow && hitNow.alive !== 'online'){
        if(!confirm(i18n('"{0}" is switched off now: its card does nothing until it is back on. Add it anyway?', hitNow.title))) return;
        force = true;
      }
      btn.disabled = true; btn.textContent = '\u2026';
      try{
        const res = await api('/api/esphome/add', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({node, name:(nm && nm.value || '').trim(),
                                category: espCategory(hitNow || {}), force}),
        });
        DEVICES = localizeDevices(await api('/api/devices'));
        BY_ID = Object.fromEntries(DEVICES.map(d => [d.id, d]));
        renderTabs(); render();
        // mark it locally instead of re-scanning MQTT (a rescan costs ~6s)
        const hit = nodes.find(x => x.node === node);
        if(hit){ hit.registered = true; hit.device_id = res.id; }
        renderEsphomeList(nodes);
      }catch(e){
        btn.disabled = false; btn.textContent = i18n('Add');
        alert(i18n('Could not add: ') + (e.message || e));
      }
    };
  });
}

function tabForCategory(cat){
  const t = allTabs().find(t => (t.cats||[]).includes(cat));
  return t ? t.label : i18n('Other');
}

