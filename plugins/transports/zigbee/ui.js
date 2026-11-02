// Zigbee on the dashboard: "Add" -> pairing, and in a z2m device's modal the rename of its key.
(function(){
  const H = Home, esc = s => Home.escapeHtml(s), T = s => i18n(s);
  const MODAL = '__zigbee_pair__';
  let poll = null;
  const post = (path, body) => H.api(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                            body: body === undefined ? undefined : JSON.stringify(body)});

  async function stopPair(){
    if(poll){ clearInterval(poll); poll = null; }
    try{ await post('/api/zigbee/pair/stop'); }catch(e){}
  }

  async function openPair(){
    H.openModal(`
      <div class="head">
        <button class="closebtn" onclick="closeModal()">\u00d7</button>
        <h2>${T('Add Zigbee')}</h2>
        <div class="sub">${T('Pairing mode is on. Put the device into pairing \u2014 usually')}
          ${T('hold the button 5\u201310 s until it blinks fast. Whatever is found appears below.')}</div>
      </div>
      <div class="body">
        <div id="zb-banner" style="font-size:13px;color:var(--ok);text-align:center;margin-bottom:10px">${T('Enabling pairing\u2026')}</div>
        <section>
          <h3>${T('Discovered')}</h3>
          <div id="zb-list"><div class="bindcaption">${T('Nothing yet. Waiting for devices\u2026')}</div></div>
        </section>
      </div>`, MODAL, {onClose: stopPair});
    try{ await post('/api/zigbee/pair/start'); }
    catch(e){ const b = document.querySelector('#zb-banner'); if(b) b.innerHTML =
      `<span style="color:var(--bad)">${T('Could not enable pairing:')} ${esc(String(e.message || e))}</span>`; }
    // closed while start was on its way: its stop may have run first, so stop once more
    if(H.modalId() !== MODAL){ post('/api/zigbee/pair/stop').catch(()=>{}); return; }
    if(poll) clearInterval(poll);
    pairPoll();
    poll = setInterval(pairPoll, 2000);
  }

  const nameInput = (f, value) => `<input class="zb-name" data-ieee="${esc(f.ieee)}" data-fn="${esc(f.friendly_name)}"
      placeholder="${T('device name')}" value="${esc(value)}"
      style="background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 10px;font:inherit;width:150px">`;

  function rowFound(f, typed){
    const title = esc(f.description || f.model || f.friendly_name);
    const sub = esc([f.vendor, f.model].filter(Boolean).join(' \u00b7 ') || f.ieee);
    if(f.registered){
      const raw = String(f.name || '').startsWith('0x');
      const cur = typed[f.ieee] !== undefined ? typed[f.ieee] : (raw ? '' : f.name);
      const shown = raw ? (f.description || f.model || f.name) : f.name;
      return `<div class="row" style="flex-wrap:wrap;gap:8px">
        <span class="rlabel">${H.icon ? H.icon('check', 14) : ''} ${esc(shown)}
          <span style="color:var(--dim);font-weight:400">\u2192 ${esc(H.tabForCategory(f.category))} ${T('\u00b7 added')}</span>
          ${f.unsupported ? `<br><span style="color:var(--warn,#e0a030);font-weight:400;font-size:12px">${T('z2m did not recognise the device \u2014 nothing to control. Remove it and pair again closer to the coordinator.')}</span>` : ''}</span>
        <span style="display:flex;gap:6px">${nameInput(f, cur)}
          <button class="dlg-btn zb-add" data-ieee="${esc(f.ieee)}">${T('Rename')}</button></span></div>`;
    }
    if(f.interview !== 'done'){
      const txt = f.interview === 'failed' ? T('interview failed') : T('identifying\u2026');
      return `<div class="row"><span class="rlabel">${title}<br><span style="color:var(--dim);font-weight:400;font-size:12px">${sub}</span></span>
        <span style="color:var(--dim)">${txt}</span></div>`;
    }
    const dflt = typed[f.ieee] !== undefined ? typed[f.ieee] : (f.model || '');
    return `<div class="row" style="flex-wrap:wrap;gap:8px">
      <span class="rlabel">${title}<br><span style="color:var(--dim);font-weight:400;font-size:12px">${sub}</span></span>
      <span style="display:flex;gap:6px">${nameInput(f, dflt)}
        <button class="dlg-btn primary zb-add" data-ieee="${esc(f.ieee)}">OK</button></span></div>`;
  }

  function rowOrphan(o){
    return `<div class="row">
      <span class="rlabel">${esc(o.name)}<br><span style="color:var(--dim);font-weight:400;font-size:12px">${esc(o.ieee || '')} ${T('\u00b7 z2m could not identify the model')}</span></span>
      <button class="dlg-btn zb-del" data-id="${esc(o.id)}">${T('Delete')}</button></div>`;
  }

  async function pairPoll(){
    if(H.modalId() !== MODAL) return;
    let s;
    try{ s = await H.api('/api/zigbee/pair/status'); }catch(e){ return; }
    const banner = document.querySelector('#zb-banner');
    if(banner) banner.textContent = s.active
      ? `${T('Pairing stays open for')} ${s.remaining} ${T('s \u2014 put the device into pairing')}`
      : T('The pairing window is closed. Close and reopen to extend it.');
    const list = document.querySelector('#zb-list');
    if(!list) return;
    const orphans = s.orphans || [];
    // redraw only when the data changed, and give focus and caret back: typing a name survives the 2 s poll
    const sig = JSON.stringify([s.found.map(f => [f.ieee, f.interview, f.registered, f.name, f.category, f.unsupported]),
                                orphans.map(o => [o.id, o.ieee])]);
    if(list.dataset.sig === sig) return;
    const ae = document.activeElement;
    const focusIeee = ae && ae.classList && ae.classList.contains('zb-name') ? ae.dataset.ieee : null;
    const sel = focusIeee ? [ae.selectionStart, ae.selectionEnd] : null;
    if(!s.found.length && !orphans.length){
      list.innerHTML = '<div class="bindcaption">' + T('Nothing yet. Waiting for devices\u2026') + '</div>';
      list.dataset.sig = sig;
      return;
    }
    const typed = {};
    list.querySelectorAll('.zb-name').forEach(i => typed[i.dataset.ieee] = i.value);
    list.innerHTML = s.found.map(f => rowFound(f, typed)).join('') + (orphans.length ? `
      <div class="bindcaption" style="margin-top:12px">${T('Unidentified on the network (from earlier attempts)')}</div>
      ${orphans.map(rowOrphan).join('')}` : '');
    list.dataset.sig = sig;
    if(focusIeee){
      const back = list.querySelector(`.zb-name[data-ieee="${CSS.escape(focusIeee)}"]`);
      if(back){ back.focus(); try{ back.setSelectionRange(sel[0], sel[1]); }catch(e){} }
    }
    list.querySelectorAll('.zb-del').forEach(btn => { btn.onclick = () => removeOrphan(btn, list); });
    list.querySelectorAll('.zb-add').forEach(btn => { btn.onclick = () => addFound(btn, list); });
  }

  async function removeOrphan(btn, list){
    const id = btn.dataset.id;
    if(!confirm(`${T('Delete "')}${id}${T('" from the Zigbee network?')}`)) return;
    btn.disabled = true; btn.textContent = '\u2026';
    try{
      let r = await post('/api/zigbee/device/remove', {id});
      if(!r.removed) r = await post('/api/zigbee/device/remove', {id, force: true});   // it never answered
      if(!r.removed) throw new Error(T('z2m did not release the device'));
      await H.reloadDevices();
      list.dataset.sig = '';
      pairPoll();
    }catch(e){
      btn.disabled = false; btn.textContent = T('Delete');
      alert(T('Could not delete: ') + (e.message || e));
    }
  }

  async function addFound(btn, list){
    const ieee = btn.dataset.ieee;
    const inp = list.querySelector(`.zb-name[data-ieee="${CSS.escape(ieee)}"]`);
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '\u2026';
    try{
      await post('/api/zigbee/pair/add', {ieee, friendly_name: inp ? inp.dataset.fn : '', name: (inp && inp.value || '').trim()});
      await H.reloadDevices();
      list.dataset.sig = '';
      pairPoll();
    }catch(e){
      btn.disabled = false; btn.textContent = orig;
      alert(T('Failed: ') + (e.message || e));
    }
  }

  // Settings -> Zigbee -> Gateway: the coordinator and its network as they are; the connection and adapter are edited
  // (through the helper, then Apply restarts zigbee2mqtt); channel, PAN and key are the network's own
  const ADAPTERS = ['ember', 'zstack', 'deconz', 'zigate', 'ezsp'];
  async function gatewayPage(box){
    const E = Home.escapeHtml, hex = n => n == null ? '\u2014' : '0x' + Number(n).toString(16).toUpperCase().padStart(4, '0');
    box.innerHTML = `<div class="bindcaption">${E(i18n('Loading\u2026'))}</div>`;
    let g, keys = {};
    try { g = await Home.api('/api/zigbee/gateway'); } catch(e){ box.innerHTML = `<div class="bindcaption">${E(e.message || String(e))}</div>`; return; }
    try { for(const k of ((await Home.api('/api/helper/state')).keys || {}).zigbee || []) keys[k.key] = k.value || ''; } catch(e){}
    const hm = t => { const d = new Date(t * 1000); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };
    const status = g.online === true ? `<span style="color:var(--ok)">\u25cf</span> ${E(i18n('in the network'))}`
      : g.online === false ? `<span style="color:var(--bad)">\u25cf</span> ${E(g.since ? i18n('offline since {0}', hm(g.since)) : i18n('offline'))}`
      : `<span style="color:var(--dim)">\u25cf</span> ${E(i18n('not heard from yet'))}`;
    const row = (k, v, extra) => `<div class="row"><span class="rlabel">${E(i18n(k))}</span><span class="rval">${v}</span>${extra || ''}</div>`;
    const serial = keys.Z2M_SERIAL || g.port, adapter = keys.Z2M_ADAPTER || '';
    box.innerHTML = `<section>
      ${row('Status', status)}
      ${row('Connection', E(g.port || '\u2014'))}
      ${row('Adapter', E(g.adapter || '\u2014'))}
      ${row('Coordinator', E([g.coordinator, g.firmware].filter(Boolean).join(' ') || '\u2014'))}
      ${row('Channel', E(g.channel == null ? '\u2014' : String(g.channel)))}
      ${row('PAN ID', E(hex(g.pan_id)))}
      ${row('Extended PAN ID', E(g.ext_pan_id || '\u2014'))}
      ${row('Network key', `<span data-zg="key">${E(g.key_set ? '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' : '\u2014')}</span>`,
        g.key_set ? ` <button class="dlg-btn" data-zg="show" style="padding:2px 10px;font-size:12px">${E(i18n('Show'))}</button>` : '')}
      ${row('zigbee2mqtt', E(g.version || '\u2014'), ` <button class="dlg-btn" data-zg="z2m" style="padding:2px 10px;font-size:12px">${E(i18n('Open zigbee2mqtt'))}</button>`)}
      <div class="bindcaption">${E(i18n('Channel, PAN ID and the network key belong to the Zigbee network itself: changing them means pairing every device again, so they are only shown here.'))}</div>
    </section>
    <section><h3>${E(i18n('Change the connection'))}</h3>
      <div class="row" style="flex-wrap:wrap;gap:8px"><span class="rlabel">${E(i18n('Connection'))}<br><span class="bindcaption">${E(i18n('A network coordinator as tcp://address:port (SLZB, ZigStar); a USB stick is set on the computer itself.'))}</span></span>
        <input class="askin" data-zg="port" data-autosave value="${E(serial)}" placeholder="tcp://slzb-06.local:6638" style="flex:1;min-width:220px"></div>
      <div class="row"><span class="rlabel">${E(i18n('Adapter'))}<br><span class="bindcaption">${E(i18n('ember: Silicon Labs (SLZB-06M, SkyConnect); zstack: TI CC2652. Empty keeps the one it runs on.'))}</span></span>
        <select data-zg="adapter" data-autosave>${['', ...ADAPTERS].map(a => `<option value="${a}"${a === adapter ? ' selected' : ''}>${E(a || i18n('as it is'))}</option>`).join('')}</select></div>
      <div class="stx-actions"><button class="dlg-btn" data-zg="check">${E(i18n('Check the connection'))}</button>
        <button class="dlg-btn primary" data-zg="save">${E(i18n('Save and restart Zigbee'))}</button></div>
      <div class="bindcaption" data-zg="out"></div>
    </section>`;
    const q = k => box.querySelector(`[data-zg="${k}"]`), out = q('out');
    if(q('show')) q('show').onclick = async () => {
      try { q('key').textContent = (await Home.api('/api/zigbee/gateway/key')).key; q('show').remove(); } catch(e){ out.textContent = e.message || String(e); }
    };
    q('z2m').onclick = async () => {              // the window first (a click opens it), then the pass and its address
      const w = window.open('', '_blank');
      try { const r = await Home.api('/api/zigbee/ui', {method: 'POST'}); if(w) w.location = r.url; else location.assign(r.url); }
      catch(e){ if(w) w.close(); out.textContent = e.message || String(e); }
    };
    q('check').onclick = async () => {
      const port = q('port').value.trim();
      out.textContent = i18n('Checking\u2026');
      try {
        const r = await Home.api('/api/zigbee/gateway/check', {method: 'POST', body: JSON.stringify({port: port === g.port ? '' : port})});
        out.innerHTML = `<span style="color:${r.ok === true ? 'var(--ok)' : r.ok === false ? 'var(--bad)' : 'var(--dim)'}">${E(i18n(r.how, ...(r.args || [])))}</span>`;
      } catch(e){ out.textContent = e.message || String(e); }
    };
    q('save').onclick = async () => {
      const port = q('port').value.trim(), ad = q('adapter').value;
      if(port === serial && ad === adapter){ out.textContent = i18n('Nothing changed.'); return; }
      if(!/^tcp:\/\/[A-Za-z0-9.\-]+:\d{1,5}$/.test(port) && port !== 'none'){ out.textContent = i18n('Write it as tcp://address:port.'); return; }
      if(!confirm(i18n('Zigbee restarts with the new connection; the house applies it for about a minute. Go on?'))) return;
      try {
        if(port !== serial) await Home.api('/api/helper/env', {method: 'POST', body: JSON.stringify({key: 'Z2M_SERIAL', value: port})});
        if(ad !== adapter) await Home.api('/api/helper/env', {method: 'POST', body: JSON.stringify({key: 'Z2M_ADAPTER', value: ad})});
        await Home.api('/api/helper/jobs', {method: 'POST', body: JSON.stringify({kind: 'apply'})});
        out.textContent = i18n('Saved; the house is applying it (about a minute).');
      } catch(e){ out.textContent = e.message || String(e); }
    };
  }

  HomePlugins.register({
    help: [
      {id: 'zigbee.name', screen: 'zigbee', screenName: 'Add Zigbee', sel: '.zb-name', title: 'Device name', text: 'The name the device gets here and in zigbee2mqtt; its model is the default.'},
      {id: 'zigbee.add', screen: 'zigbee', screenName: 'Add Zigbee', sel: '.zb-add', title: 'Add', text: 'OK adds a found device under that name, its card in its tab; for one added already it renames it.'},
      {id: 'zigbee.del', screen: 'zigbee', screenName: 'Add Zigbee', sel: '.zb-del', title: 'Delete', text: 'After a question takes a device z2m could not identify off the Zigbee network; pair it again closer to the coordinator.'},
      {id: 'zigbee.gwport', screen: 'settings:zigbee/gateway', sel: '[data-zg="port"]', title: 'Connection', text: 'Where the coordinator is: tcp://address:port for a network one. Save restarts Zigbee with it.'},
      {id: 'zigbee.gwadapter', screen: 'settings:zigbee/gateway', sel: '[data-zg="adapter"]', title: 'Adapter', text: 'The coordinator\'s chip family; empty keeps the one it runs on.'},
      {id: 'zigbee.gwcheck', screen: 'settings:zigbee/gateway', sel: '[data-zg="check"]', title: 'Check the connection', text: 'Tries the address written above (3 s); the one in use is fine while Zigbee is in the network.'},
      {id: 'zigbee.gwsave', screen: 'settings:zigbee/gateway', sel: '[data-zg="save"]', title: 'Save and restart Zigbee', text: 'Writes the connection and adapter and applies them: Zigbee restarts, the devices come back by themselves.'},
      {id: 'zigbee.gwopen', screen: 'settings:zigbee/gateway', sel: '[data-zg="z2m"]', title: 'Open zigbee2mqtt', text: 'zigbee2mqtt\'s own page in a new tab, through the dashboard: no address and no login to remember.'},
      {id: 'zigbee.gwshow', screen: 'settings:zigbee/gateway', sel: '[data-zg="show"]', title: 'Show', text: 'Shows the network key, kept in zigbee2mqtt\'s coordinator backup.'},
    ],
    settings: [{subject: 'zigbee', title: 'Zigbee', tabs: [                 // Settings -> Zigbee: its devices, pairing, the gateway
      {id: 'devices', title: 'Devices', render: box => {
        const list = Home.devices().filter(d => d.transport === 'z2m').sort((a, b) => String(a.name).localeCompare(String(b.name)));
        box.innerHTML = list.map(d => `<div class="row" data-z="${Home.escapeHtml(d.id)}" style="cursor:pointer"><span class="rlabel">${Home.escapeHtml(d.name)}</span><span class="rval">${Home.escapeHtml([d.vendor, d.model].filter(Boolean).join(' '))}</span></div>`).join('') || `<div class="bindcaption">${Home.escapeHtml(i18n('No Zigbee device yet'))}</div>`;
        box.querySelectorAll('[data-z]').forEach(r => r.onclick = () => Home.openDevice(r.dataset.z)); }},
      {id: 'pair', title: 'Pairing', render: box => {
        box.innerHTML = `<div class="bindcaption">${Home.escapeHtml(i18n('Puts the coordinator into pairing mode for 254 s: a device that joins is listed with its interview.'))}</div>`;
        const b = Home.el('button', 'dlg-btn primary', Home.escapeHtml(i18n('Pairing'))); b.onclick = openPair; box.append(b); }},
      {id: 'gateway', title: 'Gateway', needs: ['GET /api/zigbee/gateway'], render: gatewayPage},
    ]}],
    addMenu: [{label: 'Zigbee', hint: 'Puts the coordinator into pairing mode', button: 'Pairing', onClick: openPair, scan: true}],
  });
})();
