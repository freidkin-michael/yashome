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
          <span class="rlabel">${icon('wifi', 16)} ESPHome / WiFi<br><span style="color:var(--dim);font-weight:400;font-size:12px">${i18n('Finds ESP nodes already publishing to MQTT')}</span></span>
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


// Tab buckets offered for a freshly added ESPHome node (see TABS above).
const ESP_CATS = [['switch',i18n('Switches')],['sensor',i18n('Sensors')],
                  ['indicator',i18n('Indicators')],['other',i18n('Other')]];

async function openEsphomeAdd(){
  releaseModal();
  const md = document.getElementById('md');
  md.innerHTML = `
    <div class="head">
      <button class="closebtn" onclick="closeModal()">\u00d7</button>
      <h2>${i18n('Add ESPHome')}</h2>
      <div class="sub">${i18n('Devices already on the house network are listed below: OK gives one a card. A new device must join the Wi-Fi first (see the folded help).')}</div>
    </div>
    <div class="body"><section><h3>${i18n('Found')}</h3>
      <div id="esp-list"><div class="bindcaption">${i18n('Scanning MQTT\u2026')}</div></div>
    </section>
    <section><details><summary style="cursor:pointer;font-weight:600">${i18n('How to set up a node that is not here yet')}</summary>
      <div id="esp-mqtt" style="margin-top:8px"></div></details></section></div>`;
  document.getElementById('bd').classList.add('show');
  document.body.style.overflow = 'hidden';
  openModalId = '__esphome_add__';
  renderMqttBlock(document.getElementById('esp-mqtt'), '');
  let r;
  try{ r = await api('/api/esphome/discover'); }
  catch(e){
    const l = document.getElementById('md').querySelector('#esp-list');
    if(l) l.innerHTML = `<div class="bindcaption" style="color:var(--bad)">${i18n('Scan failed:')} ${escapeHtml(String(e.message||e))}</div>`;
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
    box.innerHTML = `<div class="bindcaption">${escapeHtml(i18n('No device account yet: set MQTT_DEVICE_USERS=esp and MQTT_ESP_PASSWORD in .env, then make update.'))}</div>`;
    return;
  }
  const pre = t => `<pre style="white-space:pre-wrap;user-select:all;background:var(--card2);padding:8px;border-radius:8px;margin:6px 0">${escapeHtml(t)}</pre>`;
  box.innerHTML = `<div class="row"><span class="rlabel">${escapeHtml(i18n('Broker account'))}</span>
      <select id="esp-acc">${b.accounts.map(a => `<option${a === b.account ? ' selected' : ''}>${escapeHtml(a)}</option>`).join('')}</select></div>
    ${b.warning ? `<div class="bindcaption" style="color:var(--bad)">${escapeHtml(i18n(b.warning))}</div>` : ''}
    <div class="bindcaption">${escapeHtml(i18n('Into the node\'s yaml (api: may stay):'))}</div>${pre(b.yaml)}
    <div class="bindcaption">${escapeHtml(i18n('Into its secrets.yaml (the password is not shown here):'))}</div>
    <pre id="esp-secret" style="white-space:pre-wrap;background:var(--card2);padding:8px;border-radius:8px;margin:6px 0">${escapeHtml(maskSecrets(b.secret))}</pre>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="dlg-btn" id="esp-copy">${escapeHtml(i18n('Copy secrets.yaml'))}</button>
      <button class="dlg-btn" id="esp-show">${escapeHtml(i18n('Show for 15 s'))}</button><span class="bindcaption" id="esp-copied"></span></div>
    <div class="bindcaption">${escapeHtml(i18n('Flash the node; once it is online it shows up above (open this window again).'))}</div>`;
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
    list.innerHTML = '<div class="bindcaption">' + i18n('Nothing found. The node needs an mqtt: block in its yaml.') + '</div>';
    return;
  }
  list.innerHTML = nodes.map(n=>{
    // alive: 'online' | 'offline' | null. null = the node never published its LWT, so it is
    // already gone and only its retained configs are left on the broker. Those hang around
    // forever and confuse this list -- they need cleaning up, not "adding".
    const live = n.alive === 'online' ? i18n('online')
               : n.alive === 'offline' ? i18n('off') : i18n('leftover on the broker, no node');
    const sub = [n.model, `${n.n_controls} ${i18n('channels')}`, live].filter(Boolean).join(' \u00b7 ');
    if(n.alive == null && !n.registered) return `<div class="row">
      <span class="rlabel">${icon('warn', 14)} ${escapeHtml(n.title)}<br><span style="color:var(--dim);font-weight:400;font-size:12px">${escapeHtml(sub)} ${i18n('\u2014 nothing to add, the node does not answer')}</span></span>
      <button class="dlg-btn esp-purge" data-node="${escapeHtml(n.node)}">${i18n('Clean up')}</button></div>`;
    if(n.registered) return `<div class="row">
      <span class="rlabel">${icon('check', 14)} ${escapeHtml(n.title)}<br><span style="color:var(--dim);font-weight:400;font-size:12px">${escapeHtml(sub)} ${i18n('\u00b7 already added as "')}${escapeHtml(n.device_id||'')}"</span></span>
      <button class="dlg-btn esp-del" data-node="${escapeHtml(n.node)}" data-id="${escapeHtml(n.device_id||'')}">${i18n('Remove')}</button></div>`;
    if(!n.n_controls) return `<div class="row">
      <span class="rlabel">${escapeHtml(n.title)}<br><span style="color:var(--dim);font-weight:400;font-size:12px">${i18n('no supported channels')}${(n.skipped||[]).length ? ` ${i18n('\u00b7 skipped:')} ${escapeHtml(n.skipped.slice(0,4).join(', '))}` : ''}</span></span></div>`;
    return `<div class="row" style="flex-wrap:wrap;gap:8px">
      <span class="rlabel">${escapeHtml(n.title)}<br><span style="color:var(--dim);font-weight:400;font-size:12px">${escapeHtml(sub)}</span></span>
      <span style="display:flex;gap:6px;flex-wrap:wrap">
        <input class="esp-name" data-node="${escapeHtml(n.node)}" placeholder="${i18n('name')}"
          value="${escapeHtml(n.title)}"
          style="background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 10px;font:inherit;width:140px">
        <select class="esp-cat" data-node="${escapeHtml(n.node)}"
          style="background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 8px;font:inherit">
          ${ESP_CATS.map(([v,l])=>`<option value="${v}">${escapeHtml(l)}</option>`).join('')}
        </select>
        <button class="dlg-btn primary esp-add" data-node="${escapeHtml(n.node)}">OK</button>
      </span></div>`;
  }).join('');
  list.querySelectorAll('.esp-purge').forEach(btn=>{
    btn.onclick = async ()=>{
      const node = btn.dataset.node;
      if(!confirm(`${i18n('Remove the retained configs of "')}${node}${i18n('"?\n\nThe node itself is not touched \u2014 if it ever comes back online, its configs are published again.')}`)) return;
      btn.disabled = true; btn.textContent = '\u2026';
      try{
        await api('/api/esphome/purge_discovery', {method:'POST',
          headers:{'Content-Type':'application/json'}, body: JSON.stringify({node})});
        openEsphomeAdd();        // redrawing the list is the confirmation
      }catch(e){
        btn.disabled = false; btn.textContent = i18n('Clean up');
        alert(i18n('Failed: ') + (e.message||e));
      }
    };
  });
  list.querySelectorAll('.esp-del').forEach(btn=>{
    btn.onclick = async ()=>{
      const id = btn.dataset.id, node = btn.dataset.node;
      if(!id){ alert(i18n('This device was added by hand \u2014 remove it from mqtt_devices.json')); return; }
      if(!confirm(`${i18n('Remove "')}${id}${i18n('" from the dashboard? The device itself is not touched.')}`)) return;
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
      const ct = list.querySelector(`.esp-cat[data-node="${CSS.escape(node)}"]`);
      // Onboarding a node that is not answering produces a card that is offline
      // forever -- almost always a retired node still advertising itself through
      // retained configs. Say so, and only proceed if that is really the intent.
      const hitNow = nodes.find(x => x.node === node);
      let force = false;
      if(hitNow && hitNow.alive !== 'online'){
        const why = hitNow.alive === 'offline' ? i18n('node is off')
                                               : i18n('node is gone, only its leftovers on the broker');
        if(!confirm(`"${hitNow.title}": ${why}${i18n('.\n\nThe card will be offline and its controls')} `
                    + `${i18n('will do nothing. Add it anyway?')}`)) return;
        force = true;
      }
      btn.disabled = true; btn.textContent = '\u2026';
      try{
        const res = await api('/api/esphome/add', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({node, name:(nm && nm.value || '').trim(),
                                category:(ct && ct.value) || 'switch', force}),
        });
        DEVICES = localizeDevices(await api('/api/devices'));
        BY_ID = Object.fromEntries(DEVICES.map(d => [d.id, d]));
        renderTabs(); render();
        // mark it locally instead of re-scanning MQTT (a rescan costs ~6s)
        const hit = nodes.find(x => x.node === node);
        if(hit){ hit.registered = true; hit.device_id = res.id; }
        renderEsphomeList(nodes);
      }catch(e){
        btn.disabled = false; btn.textContent = 'OK';
        alert(i18n('Could not add: ') + (e.message || e));
      }
    };
  });
}

function tabForCategory(cat){
  const t = allTabs().find(t => (t.cats||[]).includes(cat));
  return t ? t.label : i18n('Other');
}

