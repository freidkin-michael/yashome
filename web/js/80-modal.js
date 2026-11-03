// ---------- Modal ----------
function openModal(id){
  if(!BY_ID[id]) return;   // a device that is gone (a stale favourite): no modal state without a modal
  flushKeySteps(id);
  releaseModal();
  openModalId = id;
  _modalLastEventTs = 0;
  buildModal();
  document.getElementById('bd').classList.add('show');
  document.body.style.overflow = 'hidden';
}
let _modalOnClose = null;
// the open modal is done: its onClose runs whether it is closed or replaced by another one
function releaseModal(){
  const oc = _modalOnClose; _modalOnClose = null;
  if(oc) HomePlugins.safe(oc);
}
// every dialog ends in a footer: its own (.mfoot), or one whose primary button is the dialog's header OK / Save moved
// down; else "Close" (secondary) when the dialog has actions of its own, "OK" (primary) when it only tells something.
// Checked again as the dialog fills in (once a frame), so a window that draws its controls later gets "Close" too.
function frameDialog(){
  const md = document.getElementById('md');
  if(!md || !md.firstElementChild) return;
  let f = md.querySelector(':scope > .mfoot');
  if(f ? !f.dataset.auto : md.querySelector('.mfoot')) return;                // the dialog's own footer
  const own = md.querySelector('.okbtn:not([hidden])');       // a header OK shown only while there is something to save
  const acts = [...md.querySelectorAll('button, input, select, textarea, [role=switch]')].some(e => !e.closest('.closebtn, .okbtn, .mfoot') && !/close/.test(e.dataset.x || e.dataset.w || e.dataset.a || ''));
  if(!f){
    f = document.createElement('div'); f.className = 'mfoot'; f.dataset.auto = '1';
    const b = document.createElement('button'); b.className = 'dlg-btn'; b.dataset.mf = 'ok';
    b.onclick = () => { const o = md.querySelector('.okbtn:not([hidden])'); if(o) o.click(); else closeModal(); };
    f.append(b); md.append(f);
    if(!md.contains(document.activeElement) || document.activeElement === md) b.focus();
  }
  const b = f.firstElementChild, text = own ? own.textContent.trim() || i18n('OK') : acts ? i18n('Close') : i18n('OK');
  if(b.textContent !== text) b.textContent = text;
  b.classList.toggle('primary', !!own || !acts);
}
let _frameAsk = 0;
new MutationObserver(() => { if(!_frameAsk) _frameAsk = requestAnimationFrame(() => { _frameAsk = 0; frameDialog(); }); })
  .observe(document.getElementById('md'), {childList: true, subtree: true, attributes: true, attributeFilter: ['hidden']});
function closeModal(){
  releaseModal();
  openModalId = null;
  document.getElementById('md').classList.remove('devoff');
  const back = _focusBeforeModal && (document.body.contains(_focusBeforeModal) ? _focusBeforeModal
    : _focusBeforeModal.id && document.getElementById(_focusBeforeModal.id));   // redrawn meanwhile
  if(back) back.focus();
  _focusBeforeModal = null;
  document.getElementById('bd').classList.remove('show');
  document.body.style.overflow = '';
}


/* Build modal HTML ONCE on open. updateModal() only patches values
   so that <details> stays expanded and scroll position stays put. */
function buildModal(){
  const id = openModalId; const d = BY_ID[id];
  _modalPending = [];   // deferred edits of plug-in sections: run by OK, dropped by closing
  const C  = controlsOf(d);
  const md = document.getElementById('md');

  // Per-channel groups: pair every switch with the brightness on its own gang so
  // name, toggle, slider and threshold window live together. Brights that can't
  // be paired (rare) trail as slider-only groups; pure switches stay toggle-only.
  const chanPairs = C.switches.map(s => ({s, b: brightForCode(d, s.code)}));
  const pairedBr = new Set(chanPairs.filter(p=>p.b).map(p=>p.b.code));
  const loneBrights = C.brights.filter(b => !pairedBr.has(b.code));
  const rename = s => C.switches.length>1
    ? ` <button class="renbtn" data-ren-chan="${escapeHtml(s.code)}" title="${i18n('Rename channel')}">${PEN_SVG}</button>` : '';
  const chanGroup = (s, b, i) => {
    const name = (s && chanLabel(s, i, chanPairs.length) && (i18n('Channel') + ' ' + chanLabel(s, i, chanPairs.length) === s.label ? s.label : chanLabel(s, i, chanPairs.length))) || (b && b.label) || i18n(chanPairs.length > 1 ? 'Channel' : 'Power');
    const head = s ? `
        <div class="row">
          <span class="rlabel">${escapeHtml(name)}${rename(s)}</span>
          <div class="toggle${toggleGlyph(s.code)}" id="m_sw_${escapeHtml(s.code)}" data-code="${escapeHtml(s.code)}" data-act="toggle"></div>
        </div>` : `
        <div class="row"><span class="rlabel">${escapeHtml(name)}</span></div>`;
    const isLight = !['tv', 'media', 'climate'].includes(d.category || '') && !/volume/i.test(b ? b.code : '');
    const bright = b ? `
        <div class="row">
          <span class="rlabel" style="opacity:.7">${escapeHtml(b.label && b.label !== name ? b.label : (isLight ? i18n('Brightness') : i18n('Level')))}</span>
          <span class="rval"><b id="m_pct_${escapeHtml(b.code)}">0</b>%</span>
        </div>
        <input type="range" id="m_br_${escapeHtml(b.code)}" min="0" max="100" value="0"
               data-code="${escapeHtml(b.code)}" data-min="${escapeHtml(String(b.min))}" data-max="${escapeHtml(String(b.max))}" data-act="bright">
        ${isLight ? advancedHTML(`<div class="dimwin">
          <span>${i18n('Limits: min')}</span>
          <input type="number" min="0" max="100" id="m_dmin_${escapeHtml(b.code)}" data-code="${escapeHtml(b.code)}" data-act="dimwin" value="${dimFloor(b)}">
          <span>${i18n('max')}</span>
          <input type="number" min="0" max="100" id="m_dmax_${escapeHtml(b.code)}" data-code="${escapeHtml(b.code)}" data-act="dimwin" value="${dimCeil(b)}">
          <span style="opacity:.6">${i18n('% (0/100 = no limits)')}</span>
        </div>`) : ''}` : '';
    return `<div class="chgroup">${head}${bright}</div>`;
  };
  const sectSwitches = (chanPairs.length || loneBrights.length) ? `
    <section><h3>${i18n('Controls')}</h3>${
      chanPairs.map((p, i) => chanGroup(p.s, p.b, i)).join('') +
      loneBrights.map(b => chanGroup(null, b)).join('')
    }</section>` : '';
  const sectBright = '';   // brightness now lives inside each channel group above

  const psects = HomePlugins.sectionsFor(d);
  const hidden = new Set(psects.flatMap(s => Array.isArray(s.hideCodes) ? s.hideCodes : []));
  const sectPlugins = psects.map((s, i) => {
    const h = HomePlugins.safe(s.html, d);
    return typeof h === 'string' && h ? `<section data-psect="${i}">${h}</section>` : '';
  }).join('');
  const allSens = [...C.sensors, ...C.sens_txt].filter(s => !hidden.has(s.code));
  const sensList = allSens.filter(s => !isDiag(d, s));
  const diagList = allSens.filter(s => isDiag(d, s));   // triggers (action) live in bindings + the press log, not here
  const sectSensors = sensList.length ? `
    <section><h3 data-h="readings">${i18n('Readings')}</h3>${
      sensList.map(s=>`
        <div class="row${s.advanced?' adv':''}">
          <span class="rlabel">${escapeHtml(s.label||s.code)}</span>
          <span class="rval"><b id="m_sn_${escapeHtml(s.code)}">\u2014</b> ${escapeHtml(unitOf(s))}</span>
        </div>`).join('')
    }</section>` : '';

  const settings = [...C.set_bool, ...C.set_enum, ...C.set_int].filter(s => !hidden.has(s.code));
  // a Zigbee device's own settings (power-on state, switch type...) and those marked advanced are technical: folded
  const rare = s => !!s.advanced || d.transport === 'z2m';
  const setMain = settings.filter(s => !rare(s)), setRare = settings.filter(rare);
  const sectSet = settings.length ? `<section><h3 data-h="settings">${i18n('Settings')}</h3>${settings_html(d, setMain)}${advancedHTML(settings_html(d, setRare))}</section>` : '';

  // Schedule/inching and binding editors moved out of the modal -- all rule
  // creation/editing now lives in the unified "Automations" tab (the modal
  // shows participation read-only below). Kept the editors out to avoid a second
  // path that could clobber rules.
  // Events log (for any device with trigger_text or for debugging)
  const sectEvents = C.trig_txt.length ? `
    <section${d.virtual === 'person' || d.virtual === 'presence' ? ' hidden' : ''}>
      <h3 data-h="presslog">${i18n(d.virtual === 'tag' ? 'Tap log' : 'Press log')}</h3>
      <div class="evlog" id="m_evlog">${i18n('\u2014 no events \u2014')}</div>
    </section>` : '';

  const model = [d.vendor, d.model].filter(Boolean).join(' ');
  md.innerHTML = `
    <div class="head">
      <button class="closebtn" onclick="closeModal()">\u00d7</button>
      <button class="okbtn" id="m_modal_ok" onclick="modalOk()" title="${i18n('Save changes and close')}" hidden>OK</button>
      <h2 id="m_name"><span id="m_name_txt">${escapeHtml(d.name)}</span> <button class="renbtn" id="m_ren_dev"
          title="${i18n('Rename')}">${PEN_SVG}</button></h2>
      <div class="sub">${d.virtual ? '' : escapeHtml(deviceKind(d))}<span id="m_online"></span></div>
      ${whatIsHTML(d)}
    </div>
    <div class="body">
      <section${d.virtual ? ' hidden' : ''}>
        <div class="row"><span class="rlabel">${i18n('Room')}</span>
          <span class="rval" id="m_room_box"><select id="m_room" ${ROOM_LOCKED.includes(d.id) ? `disabled title="${i18n('The map sets the room')}"` : ''}><option value="">${i18n('No room')}</option>${ROOMS.map(r =>
            `<option value="${escapeHtml(r.id)}"${(r.devices||[]).includes(d.id) ? ' selected' : ''}>${escapeHtml(r.name)}</option>`).join('')}</select>
          ${ROOM_LOCKED.includes(d.id) ? `<button class="dlg-btn" id="m_room_map">${i18n('Show on the map')}</button>` : ''}</span></div>
        <div class="row"><span class="rlabel">${i18n('Serves')}<br><span class="bindcaption">${i18n('The room its voice commands, room tab and family rights go by')}</span></span>
          <span class="rval"><select id="m_serves"><option value="">${i18n('Same as where it stands')}</option>${ROOMS.map(r =>
            `<option value="${escapeHtml(r.id)}"${d.serves === r.id ? ' selected' : ''}>${escapeHtml(r.name)}</option>`).join('')}</select></span></div>
        <div class="row"><span class="rlabel">${i18n('Favourite')}</span><button class="dlg-btn" id="m_fav"></button></div>
      </section>
      ${sectSwitches}${sectBright}${sectPlugins}${sectSensors}${sectSet}${sectEvents}
      <section><h3 data-h="rules">${i18n('Used in automations')}</h3>
        <div id="m_rules" style="font-size:13px;color:var(--dim)">\u2026</div></section>
      ${model || diagList.length ? `<section><details data-h="details"><summary>${i18n('Details')}</summary>
        ${model ? `<div class="row"><span class="rlabel">${i18n('Model')}</span><span class="rval">${escapeHtml(model)}</span></div>` : ''}
        ${diagList.map(s => `<div class="row"><span class="rlabel">${escapeHtml(s.label || s.code)}${s.note ? `<br><span class="bindcaption">${escapeHtml(i18n(s.note))}</span>` : ''}</span>
          <span class="rval"><b id="m_sn_${escapeHtml(s.code)}">\u2014</b> ${escapeHtml(unitOf(s))}</span></div>`).join('')}
        </details></section>` : ''}
    </div>`;

  const vp = md.querySelector('[data-vpage]');
  if(vp) vp.onclick = () => { closeModal(); openSettings(vp.dataset.vpage); };
  // wire handlers ONCE. Rename buttons are bound here, not via inline
  // onclick="fn('${id}')": a device id with a quote in it was an XSS.
  const rd = document.getElementById('m_ren_dev'); if(rd) rd.onclick = ()=> renameDevice(d.id);
  const roomSel = document.getElementById('m_room');
  const roomWas = roomSel ? roomSel.value : '';
  if(roomSel) roomSel.onchange = async () => {
    const to = roomSel.value || null;                  // rooms are made on the map (its bar: New room)
    try { await moveDeviceToRoom(d.id, to); }
    catch(e){ alert(i18n('Error: ') + (e.message || e)); }
  };
  const sv = document.getElementById('m_serves');
  if(sv) sv.onchange = async () => {
    try { await api('/api/devices/' + encodeURIComponent(d.id) + '/serves', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({room: sv.value})}); }
    catch(e){ alert(i18n('Error: ') + (e.message || e)); return; }
    d.serves = sv.value; await reloadRooms();
  };
  const rm = document.getElementById('m_room_map');
  if(rm) rm.onclick = () => { sessionStorage.setItem('map.focus', d.id); closeModal(); Home.openTab('p:map'); };   // the map zooms to it
  const fb = document.getElementById('m_fav');
  const paintFav = () => { if(fb) fb.textContent = i18n(FAVORITES.some(f => f.device === d.id) ? 'Remove from favourites' : 'Add to favourites'); };
  paintFav();
  if(fb) fb.onclick = async () => {
    const mine = FAVORITES.filter(f => f.device === d.id);
    if(mine.length){ for(const f of mine) await removeFav(f); }
    else {
      const main = C.switches[0] || C.sensors[0] || (d.controls || [])[0];
      if(!main){ notifyFail(i18n('This card has no channel to keep as a favourite')); return; }
      if(await addFavorite(d.id, main.code, d.name)){ renderTabs(); render(); }
    }
    paintFav();
  };
  md.querySelectorAll('[data-ren-chan]').forEach(b=>{ b.onclick = ()=> renameChannel(d.id, b.dataset.renChan); });
  md.querySelectorAll('[data-act="toggle"],[data-act="set_bool"]').forEach(elx=>{
    elx.onclick = ()=>{
      if(md.classList.contains('devoff')) return;   // offline: by pointer or by keyboard (Enter / Space)
      const code = elx.dataset.code;
      const on = switchOn(d.id, code);
      elx.classList.toggle('on', !on);
      sendControl(d.id, code, !on);
    };
  });
  md.querySelectorAll('[data-act="bright"]').forEach(elx=>{
    elx.oninput = ()=>{
      const pct = document.getElementById('m_pct_'+elx.dataset.code);
      if(pct) pct.textContent = elx.value;
      pending[d.id+'/'+elx.dataset.code] = {t:Date.now(), v:+elx.value};
    };
    elx.onchange = ()=>{ sendControl(d.id, elx.dataset.code, +elx.value); };
  });
  md.querySelectorAll('[data-act="dimwin"]').forEach(inp=>{
    inp.onchange = async ()=>{
      const code = inp.dataset.code;
      const lo = document.getElementById('m_dmin_'+code);
      const hi = document.getElementById('m_dmax_'+code);
      try{
        const r = await api(`/api/devices/${encodeURIComponent(d.id)}/channel/${encodeURIComponent(code)}/dimrange`,
                            {method:'POST', headers:{'Content-Type':'application/json'},
                             body:JSON.stringify({floor:+lo.value||0, ceil:+hi.value||100})});
        const bb = controlsOf(d).brights.find(x=>x.code===code);
        if(bb){ bb.floor = r.floor; bb.ceil = r.ceil; }   // shared with the card widget
        if(lo) lo.value = r.floor;                        // reflect clamp/clear (0/100)
        if(hi) hi.value = r.ceil;
      }catch(err){ inp.style.borderColor = 'var(--bad)'; }
    };
  });
  md.querySelectorAll('[data-act="set_enum"]').forEach(btn=>{
    btn.onclick = ()=>{
      btn.parentElement.querySelectorAll('button').forEach(b=>b.classList.remove('sel'));
      btn.classList.add('sel');
      sendControl(d.id, btn.dataset.code, btn.dataset.value);
    };
  });
  md.querySelectorAll('[data-act="set_int"]').forEach(inp=>{
    inp.onchange = ()=>{
      const val = parseInt(inp.value,10);
      if(!isNaN(val)) sendControl(d.id, inp.dataset.code, val);
    };
  });

  // schedule/inching + binding editors removed from the modal -- managed in the
  // "Automations" tab. Keep the press journal + participation view.
  if(C.trig_txt.length) loadEventsLog(d.id);
  loadDeviceRules(d.id);
  _modalSects = psects.map((s, i) => ({s, box: md.querySelector(`[data-psect="${i}"]`)})).filter(x => x.box);
  const later = fn => { if(typeof fn === 'function') _modalPending.push(fn); };
  for(const x of _modalSects) if(x.s.wire) HomePlugins.safe(x.s.wire, x.box, d, later);
  // OK shows once the user changed a field of a deferred section: until then x is the one way out
  const showOk = () => { const ob = document.getElementById('m_modal_ok'); if(ob && _modalPending.length) ob.hidden = false; };
  md.oninput = showOk; md.onchange = showOk;

  updateModal();   // fill initial values
}
let _modalSects = [];   // plug-in sections of the open device modal: [{s, box}]
let _modalPending = [];
async function modalOk(){
  const btn = document.getElementById('m_modal_ok');
  if(btn) btn.disabled = true;
  try{
    while(_modalPending.length){ await _modalPending[0](); _modalPending.shift(); }   // a failed save stays queued
    closeModal();
  }catch(e){
    if(btn) btn.disabled = false;
    alert(i18n('Not saved: ') + ((e && e.message) || e));   // one press, one answer: not throttled
  }
}

// "What it is": the kind of a device in plain words, how it talks, its model, and what it does in this house
const GUESS_KINDS = ['switch', 'light', 'sensor', 'button', 'lock', 'socket', 'cover', 'other'];   // kinds read from their channels
const MODEL_KINDS = [[/router/i, 'Zigbee repeater'], [/switch module/i, 'Wall switch module'], [/\bplug\b/i, 'plug'], [/dimmer/i, 'Dimmable light'],
  [/motion/i, 'Motion sensor'], [/wireless switch|remote/i, 'Wireless button']];      // a model description (z2m) -> its kind
function deviceKind(d){
  const C = d.controls || [], codes = new Set(C.map(c => c.code)), kinds = new Set(C.map(c => c.kind));
  const has = (...x) => x.some(k => codes.has(k)), sw = C.filter(c => c.kind === 'switch').length;
  if(d.category === 'tv') return i18n('Television');
  if(d.category === 'climate') return i18n('Air conditioner');
  if(d.ir_appliance) return i18n('An appliance with an infrared remote');
  if(!GUESS_KINDS.includes(d.category || 'other')) return catLabel(d.category);     // a plug-in's own kind: its word, never a guess
  const byModel = MODEL_KINDS.find(([re]) => re.test(d.description || ''));     // the model's own words before a guess
  if(byModel) return i18n(byModel[1] === 'plug' ? (has('power', 'energy') ? 'Smart plug with power metering' : 'Smart plug') : byModel[1]);
  if(has('locked')) return i18n('Smart lock');
  if(kinds.has('bright')) return i18n('Dimmable light');
  if(sw > 1) return i18n('Wall switch with {0} channels', sw);
  if(sw && has('power', 'watts')) return i18n('Smart plug with power metering');
  if(sw) return i18n('Switch or relay');
  if(has('presence') && has('distance')) return i18n('mmWave presence radar');
  if(has('occupancy')) return i18n('Motion sensor');
  if(has('contact')) return i18n('Door or window contact');
  if(has('water_leak')) return i18n('Water leak sensor');
  if(has('smoke')) return i18n('Smoke alarm');
  if(has('co2')) return i18n('Air quality sensor (CO2)');
  if(has('wind', 'rain')) return i18n('Weather station');
  if(has('temperature') && has('humidity')) return i18n('Temperature and humidity sensor');
  if(kinds.has('trigger_text')) return i18n('Wireless button');
  if(has('power') && has('energy')) return i18n('Energy meter');
  return catLabel(d.category);
}
function deviceVia(d){                                               // how it is connected, in the words a family knows
  if(d.transport === 'tizen') return 'Samsung';
  if(d.transport === 'webos') return 'LG';
  if(d.transport === 'z2m') return 'Zigbee';
  if(d.ir_appliance || d.transport === 'ir') return i18n('IR');
  if(d.transport === 'mqtt') return 'Wi-Fi';
  return '';                                                         // a voice satellite, a plug-in's own way: its kind says it
}
const VIRTUAL_PAGE = {person: 'family', presence: 'family', tag: 'nfc'};
function virtualWhat(d){                                            // what a virtual device is, in words, with its state now
  const v = (STATE[d.id] || {}).values || {};
  if(d.virtual === 'person') return i18n('A person of the family: {0}', i18n(v.home === true ? 'at home' : v.home === false ? 'away' : 'not known yet'));
  if(d.virtual === 'presence'){                                  // who, by the family's people, as the Family page says it
    const home = DEVICES.filter(x => x.virtual === 'person' && ((STATE[x.id] || {}).values || {}).home === true).map(x => x.name);
    return home.length ? i18n('At home: {0}', home.join(', ')) : i18n(v.home === true ? 'someone is at home' : v.home === false ? 'Nobody is at home' : 'not known yet');
  }
  return i18n('NFC tags and cards: a tap is a trigger of a rule');
}
function whatIsHTML(d){
  if(d.virtual) return `<div class="whatis" id="m_what"><b>${escapeHtml(virtualWhat(d))}</b>
    <button class="dlg-btn" data-vpage="${escapeHtml(VIRTUAL_PAGE[d.virtual] || 'devices')}" style="margin-left:8px;padding:2px 10px;font-size:12px">${escapeHtml(i18n('Open'))}</button><div class="wrule" id="m_what_rule"></div></div>`;
  return `<div class="whatis" id="m_what"><div class="wrule" id="m_what_rule"></div></div>`;     // filled when a rule uses it
}
const ROLE_LABELS = {trigger:i18n('trigger'), condition:i18n('condition'), action:i18n('action')};
async function loadDeviceRules(devId){
  const box = document.getElementById('m_rules');
  if(!box) return;
  let rules = [];
  // the answer may come after the modal was closed or shows another device: drop it then
  const gone = () => !box.isConnected || openModalId !== devId;
  try { rules = await api(`/api/devices/${encodeURIComponent(devId)}/rules`); }
  catch(e){ if(!gone()) box.textContent = i18n('\u2014 failed to load \u2014'); return; }
  if(gone()) return;
  const wr = document.getElementById('m_what_rule');
  if(wr && rules.length) wr.textContent = i18n('In this house: {0}', ruleTitle(rules[0])) + (rules.length > 1 ? ' ' + i18n('(and {0} more)', rules.length - 1) : '');
  if(!rules.length){ box.textContent = i18n('\u2014 in none \u2014'); return; }
  box.innerHTML = '';
  for(const r of rules){
    const row = el('div');
    row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:7px 0;'+
      'border-top:1px dashed var(--line)';
    const badges = r.roles.map(role=>
      `<span class="role-badge ${escapeHtml(role)}">${escapeHtml(ROLE_LABELS[role]||role)}</span>`).join('');
    row.innerHTML = `
      <div style="flex:1;min-width:0;cursor:pointer${r.enabled?'':';opacity:.5'}">
        <span style="color:var(--txt)">${escapeHtml(ruleTitle(r))}</span>${badges}</div>
      <div class="toggle setting ${r.enabled?'on':''}" style="flex:none"></div>`;
    row.querySelector('div').onclick = ()=>{
      closeModal(); activeTab='auto'; rememberTab('auto');
      renderTabs(); render();
    };
    row.querySelector('.toggle').onclick = async (e)=>{
      e.stopPropagation();
      const on = !row.querySelector('.toggle').classList.contains('on');
      try{
        await api('/api/rules/enable', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({id:r.id, enabled:on})});
        loadDeviceRules(devId);
      }catch(err){ alert(i18n('Error: ')+err.message); }
    };
    box.append(row);
  }
}

// ---------- Bindings UI ----------
function switchTargetsList(){
  // Targets a rule may control: a switch or a dimmer
  return DEVICES.filter(d => (d.controls||[]).some(c =>
    c.kind==='switch' || c.kind==='bright'));
}

const GESTURE_LABELS = {
  'click':        i18n('1\u00d7 click'),
  'double_click': i18n('2\u00d7 double'),
  'press':        i18n('long press'),
  'single':       i18n('Pressed'),
  'true':         i18n('became "yes"'),
  'false':        i18n('became "no"'),
  'below':        i18n('below'),
  'above':        i18n('above'),
  'double':       i18n('double press'),
  'triple':       i18n('triple press'),
  'hold':         i18n('hold'),
  'long':         i18n('long press'),
  'release':      i18n('release'),
};
function gestureLabel(g){
  if(GESTURE_LABELS[g]) return GESTURE_LABELS[g];
  // multi-button remotes: "2_single" is button 2, "2_double" its double press
  const m = String(g).match(/^(\d+)_(.+)$/);
  if(!m){ const t = i18n('gesture:' + g); return t === 'gesture:' + g ? g : t; }   // a plug-in may name its values
  return i18n('Button ') + m[1] + (m[2] === 'single' ? '' : ' ' + (GESTURE_LABELS[m[2]] || m[2]));
}

// a press in the log in words: a known gesture, a numbered button's, a plug-in's own word; anything else is a "press"
function pressWord(v){
  const g = String(v == null ? '' : v);
  if(GESTURE_LABELS[g]) return GESTURE_LABELS[g];
  if(/^\d+_./.test(g)) return gestureLabel(g);
  const t = i18n('gesture:' + g);
  return t === 'gesture:' + g ? i18n('press') : t;
}
function sensorBindControls(d, C){
  return C.sensors.filter(s => s.code!=='battery' && s.code!=='linkquality');
}

async function loadEventsLog(id){
  const box = document.getElementById('m_evlog');
  if(!box) return;
  const gone = () => !box.isConnected || openModalId !== id;   // closed or another device meanwhile
  try{
    const r = await api(`/api/devices/${encodeURIComponent(id)}/events`);
    if(gone()) return;
    const items = r.items || [];
    if(!items.length){ box.innerHTML = i18n('\u2014 no events \u2014'); return; }
    box.innerHTML = items.slice().reverse().map(ev => {
      const dt = new Date(ev.ts*1000);
      const time = dt.toTimeString().slice(0,8);
      const fired = ev.fired ? ` <span class="vchip" style="background:#1d4d3a">${icon('play', 12)} ${escapeHtml(ev.fired)}</span>` : '';
      return `<div class="ev">${time} \u2014 <span class="vchip">${escapeHtml(pressWord(ev.value))}</span>${fired}</div>`;
    }).join('');
  }catch(e){
    if(!gone()) box.innerHTML = `<span style="color:var(--bad)">${i18n('Error:')} ${escapeHtml(e.message)}</span>`;
  }
}


/* Patch only dynamic values; never touches structure */
let _modalLastEventTs = 0;

function updateModal(){
  const id = openModalId;
  if(!id || id === '__picker__' || id === '__fav_editor__' || id === '__add_device__'
        || id === '__room_picker__') return;
  const d = BY_ID[id]; if(!d) return;
  const st = STATE[id] || {online:false};
  const v = st.values || {};
  const md = document.getElementById('md');

  // Re-fetch events log if a new button event came in
  if(st.last_event_ts && st.last_event_ts !== _modalLastEventTs){
    _modalLastEventTs = st.last_event_ts;
    if(document.getElementById('m_evlog')) loadEventsLog(d.id);
  }

  const onEl = document.getElementById('m_online');
  const life = lifeOf(d.id);
  md.classList.toggle('devoff', life === 'offline');
  for(const e of md.querySelectorAll(DEVOFF_ACTS)){
    e.disabled = life === 'offline';
    if(e.classList.contains('toggle')){           // a div: say it and take it out of the tab order
      e.setAttribute('aria-disabled', String(life === 'offline'));
      e.tabIndex = life === 'offline' ? -1 : 0;
    }
  }
  if(onEl && !d.virtual) onEl.innerHTML = '\u00b7 ' + (life === 'live'
    ? `<span style="color:var(--ok)">${i18n('online')}</span>`
    : life === 'waiting'
    ? `<span style="color:var(--dim)">${i18n('waiting for report')}</span>`
    : `<span style="color:var(--bad)">${st.offline_since ? i18n('offline since') + ' ' + hhmm(st.offline_since) : i18n('offline')}</span>`);

  for(const ctl of d.controls){
    const val = v[ctl.code];
    if(ctl.kind==='switch' || ctl.kind==='setting_bool'){
      const t = document.getElementById('m_sw_'+ctl.code) ||
                md.querySelector(`[data-code="${CSS.escape(ctl.code)}"][data-act="set_bool"]`);
      if(t) t.classList.toggle('on', !!val);
    }
    if(ctl.kind==='bright'){
      const sl = document.getElementById('m_br_'+ctl.code);
      const pc = document.getElementById('m_pct_'+ctl.code);
      const pkey = d.id+'/'+ctl.code;
      const p = pending[pkey];
      const fresh = p && Date.now()-p.t < 4000;
      if(sl && !fresh && val!=null){
        const span = ctl.max - ctl.min;
        const ui = Math.round((val-ctl.min)/span*100);
        sl.value = Math.max(0, ui); if(pc) pc.textContent = val < 0 ? '\u2014' : ui;   // a TV that is off reports -1
      }
    }
    if(ctl.kind==='sensor' || ctl.kind==='sensor_text' || ctl.kind==='trigger_text'){
      const cell = document.getElementById('m_sn_'+ctl.code);
      if(cell){
        cell.textContent = ctl.kind === 'trigger_text' ? (val==null ? '\u2014' : String(val)) : ctl.code === 'uptime' && val != null ? fmtUptime(val) : fmtReading(val, d.id, ctl.code);
        cell.classList.toggle('blob', typeof val==='string' && val.length > 24);
      }
    }
    if(ctl.kind==='setting_enum'){
      const buttons = md.querySelectorAll(`[data-code="${CSS.escape(ctl.code)}"][data-act="set_enum"]`);
      buttons.forEach(b => b.classList.toggle('sel', b.dataset.value === String(val)));
    }
    if(ctl.kind==='setting_int'){
      const inp = md.querySelector(`[data-code="${CSS.escape(ctl.code)}"][data-act="set_int"]`);
      if(inp && document.activeElement !== inp && val!=null) inp.value = val;
    }
  }
  for(const x of _modalSects) if(x.s.update && document.body.contains(x.box)) HomePlugins.safe(x.s.update, x.box, d, st);
}

// an option of a device setting in words: "enum:<value>" in the dictionary, else the value with spaces
const ENUM_EN = {previous: 'as before', toggle: 'switch over'};
function enumWord(opt){
  const t = i18n('enum:' + opt);
  return t !== 'enum:' + opt ? t : ENUM_EN[opt] || String(opt).replace(/_/g, ' ');
}
function settings_html(d, list){
  let h='';
  for(const s of list){
    const adv = s.advanced ? ' adv' : '';
    if(s.kind==='setting_bool'){
      h += `<div class="row${adv}">
              <span class="rlabel">${escapeHtml(s.label)}</span>
              <div class="toggle setting" data-code="${escapeHtml(s.code)}" data-act="set_bool"></div>
            </div>`;
    } else if(s.kind==='setting_enum'){
      h += `<div class="row${adv}"><span class="rlabel">${escapeHtml(s.label)}</span></div>
            <div class="enum${adv?' adv':''}">${
              s.range.map(opt=>`<button data-code="${escapeHtml(s.code)}" data-value="${escapeHtml(opt)}" data-act="set_enum">${
                escapeHtml((s.labels||{})[opt] || enumWord(opt))
              }</button>`).join('')
            }</div>`;
    } else if(s.kind==='setting_int'){
      h += `<div class="row${adv}">
              <span class="rlabel">${escapeHtml(s.label)}</span>
              <div class="num">
                <input type="number" min="${escapeHtml(String(s.min))}" max="${escapeHtml(String(s.max))}"
                       data-code="${escapeHtml(s.code)}" data-act="set_int">
                <span class="unit">${escapeHtml(i18n(s.unit||''))}</span>
              </div>
            </div>`;
    }
  }
  return h;
}

async function renameChannel(id, code){
  const d = BY_ID[id]; if(!d) return;
  const c = (d.controls||[]).find(x=>x.code===code); if(!c) return;
  const cur = c.label || code;
  const raw = await askText(i18n('Channel name (empty = default):'), cur);
  if(raw === null) return;
  const name = raw.trim();
  if(name === cur) return;
  try{
    const r = await api(`/api/devices/${encodeURIComponent(id)}/channel/${encodeURIComponent(code)}/rename`,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name}),
    });
    c.label = r.label;
    // only the name in the open modal: a rebuild would drop unsaved edits of plug-in sections
    const btn = openModalId === id && document.querySelector(`#md [data-ren-chan="${CSS.escape(code)}"]`);
    const txt = btn && btn.parentElement.firstChild;
    if(txt && txt.nodeType === Node.TEXT_NODE) txt.textContent = c.label || i18n('Channel');
    render();
  }catch(e){ alert(i18n('Could not rename the channel: ')+e.message); }
}

async function renameDevice(id){
  const d = BY_ID[id]; if(!d) return;
  const name = (await askText(i18n('New device name:'), d.name) || '').trim();
  if(!name || name === d.name) return;
  try{
    const r = await api(`/api/devices/${encodeURIComponent(id)}/rename`,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name}),
    });
    d.name = r.name;
    const nt = document.getElementById('m_name_txt');
    if(nt) nt.textContent = r.name;          // only the text: both rename buttons stay
    const cn = document.querySelector('#c_'+CSS.escape(id)+' .name');
    if(cn) cn.innerHTML = devIcon(d) + escapeHtml(r.name);
  }catch(e){ alert(i18n('Could not rename: ')+e.message); }
}

// One dialog per burst: with the broker down every tap fails, and a per-tap
// alert() made the app unusable exactly when you needed to read the reason.
const _toastSeen = {};     // text -> ms: the same text again within 5 s is not repeated
// made at load: a screen reader may miss a live region inserted together with its first text
function toastBox(){
  let box = document.getElementById('toasts');
  if(!box){
    box = el('div', 'toasts'); box.id = 'toasts';
    box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite');
    document.body.append(box);
  }
  return box;
}
if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', toastBox); else toastBox();
function notifyFail(msg){
  // a toast, never a blocking dialog: a wall panel must not stop on "No answer"
  const now = Date.now();
  if(now - (_toastSeen[msg] || 0) < 5000) { console.warn(msg); return; }
  _toastSeen[msg] = now;
  const box = toastBox();
  while(box.children.length >= 3){ delete _toastSeen[box.firstChild.textContent]; box.firstChild.remove(); }
  const t = el('div', 'toast');
  t.textContent = msg;
  box.append(t);
  setTimeout(() => t.remove(), 4000);
}


async function sendControl(id, code, value){
  const prev = STATE[id]?.values ? STATE[id].values[code] : undefined;
  const intent = typeof value === 'boolean' ? {on: value, t: Date.now()} : null;
  const before = intent ? _swIntent[id + '/' + code] : undefined;
  if(intent) _swIntent[id + '/' + code] = intent;   // a next toggle builds on this, not on a stale report
  // a bright control is sent in percent, STATE holds its raw units (as the backend maps them)
  const ctl = ((BY_ID[id] || {}).controls || []).find(c => c.code === code);
  const raw = ctl && ctl.kind === 'bright' && typeof value === 'number' && Number.isFinite(value)
    ? Math.trunc(ctl.min + (ctl.max - ctl.min) * Math.max(0, Math.min(100, Math.trunc(value))) / 100) : value;
  if(ctl && ctl.confirm){                       // the device's own report, nothing earlier
    const hold = Math.max(5000, HomePlugins.holdMs(BY_ID[id], code, value));
    _sentWait[id+'/'+code] = {v: raw, v0: prev, t: Date.now(), hold};
    expireWaitLater(id, code, hold);
    setTimeout(updateCards, 1550);            // the first frame's grace ends: an early echo stops the pulse
  } else {
    if(STATE[id]?.values) STATE[id].values[code] = raw;
    optimistic[id+'/'+code] = {value: raw, expires: Date.now() + HomePlugins.holdMs(BY_ID[id], code, value)};
  }
  updateCards();
  try{
    await api(`/api/devices/${encodeURIComponent(id)}/control`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code, value}),
    });
    return true;
  }catch(e){
    // The command did NOT happen. Holding the optimistic value for the full
    // OPT_HOLD_MS showed a lie (lamp "on" while the room stays dark) and hid the
    // error, so drop the hold, put the old value back and say it out loud.
    delete optimistic[id+'/'+code];
    delete _sentWait[id+'/'+code];
    delete pending[id+'/'+code];
    if(intent && _swIntent[id+'/'+code] === intent){   // not asked after all: the one before it counts again
      if(before) _swIntent[id+'/'+code] = before; else delete _swIntent[id+'/'+code];
    }
    if(STATE[id]?.values && prev !== undefined && !(ctl && ctl.confirm)) STATE[id].values[code] = prev;
    updateCards();
    notifyFail(`${i18n('Could not run:')} ${e.message}`);
    console.warn('control fail', id, code, e.message);
    return false;
  }
}

