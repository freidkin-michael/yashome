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
        <span class="rlabel">\u2713 ${esc(shown)}
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

  async function renameKey(id){
    const to = (prompt(T('New device name in zigbee2mqtt (this changes the MQTT topics too):'), id) || '').trim();
    if(!to || to === id) return;
    try{
      const r = await post('/api/zigbee/device/rename', {id, to});
      const parts = Object.entries(r.migrated || {}).filter(([, n]) => n).map(([k, n]) => `${T(k.replace(/_/g, ' ') + ':')} ${n}`);
      alert(`${T('Done:')} ${id} -> ${to}` + (parts.length ? `${T('\nMoved --')} ${parts.join(', ')}` : ''));
      H.closeModal();
      await H.reloadDevices();
    }catch(e){ alert(T('Could not rename: ') + e.message); }
  }

  HomePlugins.register({
    addMenu: [{label: '\u{1f4e1} Zigbee', hint: 'Puts the coordinator into pairing mode', button: 'Pairing', onClick: openPair}],
    modalSections: [{
      match: d => d.transport === 'z2m',
      html: () => `<section><h3>Zigbee</h3><div class="row"><span class="rlabel">${T('Rename in zigbee2mqtt: changes the device name itself (and the MQTT topics), moves bindings, favourites and automations')}</span>
        <button class="dlg-btn zb-renkey">\u{1f3f7} ${T('Rename')}</button></div></section>`,
      wire: (box, d) => { const b = box.querySelector('.zb-renkey'); if(b) b.onclick = () => renameKey(d.id); },
    }],
  });
})();
