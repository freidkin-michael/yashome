// IR remotes: the "IR remotes" tab, the remote and appliance windows, the appliance tile face,
// learned buttons on the card an appliance extends, and "+ IR appliance" on the Appliances tab.
(() => {
let IR = {remotes: [], appliances: [], blasters: []}, IR_TS = 0;
const escapeHtml = s => Home.escapeHtml(s), el = (t, c, h) => Home.el(t, c, h);
const sendControl = (id, code, value) => Home.sendControl(id, code, value);
const byId = id => Home.devices().find(d => d.id === id);
const ctl = d => ({presses: (d.controls || []).filter(c => c.kind === 'ir_press')});
const IR_PLURALS = {"en": {"interval": ["interval", "intervals"], "button": ["button", "buttons"], "function": ["function", "functions"]}, "ru": {"interval": ["\u0438\u043d\u0442\u0435\u0440\u0432\u0430\u043b", "\u0438\u043d\u0442\u0435\u0440\u0432\u0430\u043b\u0430", "\u0438\u043d\u0442\u0435\u0440\u0432\u0430\u043b\u043e\u0432"], "button": ["\u043a\u043d\u043e\u043f\u043a\u0430", "\u043a\u043d\u043e\u043f\u043a\u0438", "\u043a\u043d\u043e\u043f\u043e\u043a"], "function": ["\u0444\u0443\u043d\u043a\u0446\u0438\u0438", "\u0444\u0443\u043d\u043a\u0446\u0438\u044f\u0445", "\u0444\u0443\u043d\u043a\u0446\u0438\u044f\u0445"]}};
function irPlural(key, n){             // the core has no plural forms for these words
  const ru = typeof LANG === 'string' && LANG === 'ru', f = (IR_PLURALS[ru ? 'ru' : 'en'] || {})[key] || [key, key];
  n = Math.abs(Math.trunc(n || 0));
  if(ru){ const a = n % 100, b = a % 10; return f[(a > 10 && a < 20) ? 2 : (b > 1 && b < 5) ? 1 : (b === 1) ? 0 : 2]; }
  return f[n === 1 ? 0 : 1];
}
async function loadIrRemotes(){ try { IR = await Home.api('/api/ir/remotes'); } catch(e){ /* keep the last */ } }
const css = document.createElement('style');
css.textContent = `.acpanel{display:flex;flex-direction:column;gap:8px;margin-top:10px}
  .acrow{display:flex;flex-wrap:wrap;gap:6px}
  .aclbl{font-size:12px;color:var(--dim);text-transform:uppercase;margin-top:4px}
  .acbtn{background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:10px;padding:8px 10px;
    cursor:pointer;font:inherit;font-size:14px;font-weight:500;min-height:38px;flex:1 0 auto}
  .acrow.irbtns .acbtn{font-size:13px;padding:8px 10px;flex:1 1 44%}
  .acrow.irbtns .acbtn.ico{flex:1 1 18%;min-width:44px} .acrow.irbtns .acbtn.ico svg{width:20px;height:20px}
  .rc .rrow .dotwrap.inrow{flex-direction:row;gap:8px;min-width:0;margin-right:8px} .rc .rrow .dotwrap.inrow .dotlabel{display:none}`;
document.head.append(css);

// An IR appliance bound to an existing card gets NO tile of its own: its buttons are
// drawn on the host card (air conditioner + extra learned buttons in one place), otherwise
// the tab would show two tiles for the same AC.
function isHostedAppliance(d){
  return !!(d.ir_appliance && d.ir_card && byId(d.ir_card));
}

// The IR/RF blaster itself needs no card on the dashboard: it stores nothing and does
// nothing by itself, the blaster is chosen on the appliance and buttons are learned in the
// remote's window. It stays in DEVICES \u2014 the online state of the appliances behind it comes from it.
function isBlasterCard(d){
  return !!d.ir_blaster;
}

// IR appliances living on this card (usually one)
function hostedAppliances(hostId){
  return Home.devices().filter(d => d.ir_appliance && d.ir_card === hostId);
}

// Block of extra learned IR buttons for a host card: the appliance's buttons in groups plus
// a line with the remote, the blaster and a cog into its settings.
function appendHostedIr(c, hostId){
  for(const ap of hostedAppliances(hostId)){
    const C = ctl(ap);
    const wrap = el('div','acpanel');
    const order = [];
    const byGroup = new Map();
    for(const ctl of C.presses){
      const g = ctl.group || '';
      if(!byGroup.has(g)){ byGroup.set(g, []); order.push(g); }
      byGroup.get(g).push(ctl);
    }
    const lay1 = (window.PW && PW.remoteHtml) ? PW.remoteHtml(C.presses.map(x=>({code:x.code, label:(x.group && x.label && x.label.startsWith(x.group+' \u00b7 ')) ? x.label.slice(x.group.length+3) : (x.label||x.code)}))) : '';
    if(lay1){
      wrap.insertAdjacentHTML('beforeend', lay1);
      for(const b of wrap.querySelectorAll('button[data-code]')) b.onclick = ev=>{ ev.stopPropagation(); const was=b.innerHTML; b.style.opacity='.45';
        sendControl(ap.id, b.dataset.code, true).finally(()=>{ b.innerHTML=was; b.style.opacity=''; }); };
      order.length = 0;                       // the layout is drawn -- flat rows are not needed
      // the card's single power switch (a media box: power by probe) -- goes into the remote's power row
      const dws = c.querySelectorAll('.dotwrap'), prow = wrap.querySelector('.rc > .rrow');
      if(dws.length === 1 && prow){ dws[0].classList.add('inrow'); prow.insertBefore(dws[0], prow.firstChild); }
    }
    for(const g of order){
      wrap.append(el('div','aclbl', g ? escapeHtml(g) : i18n('IR buttons')));
      const row = el('div','acrow irbtns');
      for(const ctl of byGroup.get(g)){
        const face = g && ctl.label && ctl.label.startsWith(g + ' \u00b7 ')
          ? ctl.label.slice(g.length + 3) : (ctl.label || ctl.code);
        const ico = (window.PW && PW.irIcon) ? PW.irIcon(ctl.code, face) : '';
        const b = el('button','acbtn'+(ico?' ico':''), ico ? '' : escapeHtml(face));
        if(ico){ b.innerHTML = ico; b.title = face; }
        b.onclick = ev=>{
          ev.stopPropagation();
          const was = b.innerHTML;                 // icon or label -- put back as it was
          b.style.opacity = '.45';
          sendControl(ap.id, ctl.code, true).finally(()=>{ b.innerHTML = was; b.style.opacity = ''; });
        };
        row.append(b);
      }
      wrap.append(row);
    }
    const via = irBlaster(ap.ir_via);
    const rem = (IR.remotes || []).find(x => x.id === ap.ir_remote);
    const foot = el('div','bindcaption');
    foot.style.cssText = 'display:flex;align-items:center;gap:6px;justify-content:space-between';
    foot.append(el('span', null,
      `${i18n('IR: remote "')}${escapeHtml((rem && rem.name) || ap.ir_remote || '?')}\u00bb \u00b7 ${
        escapeHtml(via ? via.name : ap.ir_via || i18n('no blaster set'))}${
        C.presses.length ? '' : i18n(' \u00b7 no buttons yet')}`));
    const gear = el('button','renbtn','\u2699');
    gear.title = i18n('Set up the IR appliance: remote, blaster, binding');
    gear.onclick = ev=>{ ev.stopPropagation(); openIrApplianceModal(ap.id); };
    foot.append(gear);
    wrap.append(foot);
    c.append(wrap);
  }
}

function irFace(c, d){
    const C2 = ctl(d);
    const wrap = el('div','acpanel');
    // Buttons come in groups by function ("Fan", "Light") \u2014 in the same formation as FAN
    // and MODE on the air conditioner. Without a group \u2014 one row on top.
    const groups = [];
    const byGroup = new Map();
    for(const c of C2.presses){
      const g = c.group || '';
      if(!byGroup.has(g)){ byGroup.set(g, []); groups.push(g); }
      byGroup.get(g).push(c);
    }
    const lay2 = (window.PW && PW.remoteHtml) ? PW.remoteHtml(C2.presses.map(x=>({code:x.code, label:(x.group && x.label && x.label.startsWith(x.group+' \u00b7 ')) ? x.label.slice(x.group.length+3) : (x.label||x.code)}))) : '';
    if(lay2){
      wrap.insertAdjacentHTML('beforeend', lay2);
      for(const b of wrap.querySelectorAll('button[data-code]')) b.onclick = ev=>{ ev.stopPropagation(); const was=b.innerHTML; b.style.opacity='.45';
        sendControl(d.id, b.dataset.code, true).finally(()=>{ b.innerHTML=was; b.style.opacity=''; }); };
      groups.length = 0;
    }
    for(const g of groups){
      if(g) wrap.append(el('div','aclbl', escapeHtml(g)));   // like FAN on the AC
      const row = el('div','acrow irbtns');
      for(const c of byGroup.get(g)){
        // inside a group the button name has lost its prefix \u2014 only the action is on the button
        const face = g && c.label && c.label.startsWith(g + ' \u00b7 ')
          ? c.label.slice(g.length + 3) : (c.label || c.code);
        const ico = (window.PW && PW.irIcon) ? PW.irIcon(c.code, face) : '';
        const b = el('button','acbtn'+(ico?' ico':''), ico ? '' : escapeHtml(face));
        if(ico){ b.innerHTML = ico; b.title = face; }
        b.onclick = ev=>{
          ev.stopPropagation();
          const was = b.innerHTML;                 // icon or label -- put back as it was
          b.style.opacity = '.45';
          // sendControl already knows rollback and a loud error; IR has no state,
          // so value is only a reason to send the frame
          sendControl(d.id, c.code, true).finally(()=>{ b.innerHTML = was; b.style.opacity = ''; });
        };
        row.append(b);
      }
      wrap.append(row);
    }
    const via = irBlaster(d.ir_via);
    const rem = (IR.remotes || []).find(x => x.id === d.ir_remote);
    // A bound card's buttons are built by the firmware (mqtt_devices.json), so
    // "no buttons" would be a lie here: none among the EXTRA learned ones, not none at all.
    const card = d.ir_card ? (byId(d.ir_card) || {}).name || d.ir_card : null;
    const tail = C2.presses.length ? ''
      : (card ? ` ${i18n('\u00b7 basic buttons on the card "')}${escapeHtml(card)}${i18n('" (firmware), no extra ones learned yet')}`
              : i18n(' \u00b7 no buttons, learn them under "IR remotes"'));
    wrap.append(el('div','bindcaption',
      `${i18n('Remote "')}${escapeHtml((rem && rem.name) || d.ir_remote || '?')}\u00bb \u00b7 ${
        via ? escapeHtml(via.name) : escapeHtml(d.ir_via || i18n('no blaster set'))}${tail}`));
    c.append(wrap);
    c.onclick = ()=> openIrApplianceModal(d.id);
}

// One window per blaster, a section per remote inside: a flat list of buttons with
// the action's name. Learn = the server arms the node and waits for a frame (the request
// hangs exactly as long as the node listens), press = the server publishes the stored code
// to <node>/tx/{ir,rf}_raw. The node holds no codes, so reflashing loses nothing.
let IR_BUSY = false;      // learning in progress \u2014 the server would refuse a second request anyway

function irErrText(e){                 // FastAPI answers {"detail":"\u2026"} \u2014 show the detail
  const s = String((e && e.message) || e || '');
  try{ const j = JSON.parse(s); if(j && j.detail) return String(j.detail); }catch(_){}
  return s;
}

function irStatus(key, text, cls){
  // The same remote may be drawn twice (the tab + a modal on top of it), so the
  // status line is addressed by class and data-r, not by id: write to every match.
  // Panel-level operations ("new remote") have no key \u2014 they get the "_" line.
  const sel = '.ir-st[data-r="' + CSS.escape(String(key || '_')) + '"]';
  document.querySelectorAll(sel).forEach(e => {
    e.textContent = text || '';
    e.style.color = cls === 'bad' ? 'var(--bad)' : (cls === 'ok' ? 'var(--ok)' : '');
  });
}

// Channels of a card that count as "buttons from the firmware": pressable, with a
// clear set of values. Sensors and text do not qualify.
function irFwKinds(c){
  return ['switch','setting_enum'].includes(c.kind) && !c.ro;
}

// A channel's values as a list {value, label}: on/off for a switch, for an enum its
// range with human labels when given.
function irFwValues(c){
  if(c.kind === 'switch') return [{value: true, label: i18n('On')}, {value: false, label: i18n('Off')}];
  const labels = c.labels || {};
  return (c.range || []).map(v => ({value: v, label: labels[v] || v}));
}

// How many buttons the firmware gives this remote (over all its appliances, without
// repeating the same channel in two rooms).
function irFwCount(r){
  const seen = new Set();
  let n = 0;
  for(const u of (r.used_by || [])){
    const ap = (IR.appliances || []).find(x => x.id === u.id);
    const dev = ap && ap.device ? byId(ap.device) : null;
    if(!dev) continue;
    for(const c of (dev.controls || [])){
      if(!irFwKinds(c) || seen.has(c.code)) continue;
      seen.add(c.code);
      n += irFwValues(c).length;
    }
  }
  return {codes: seen.size, buttons: n};
}

// titleOverride: in a single remote's modal its name already stands in the window title,
// and repeating it in h3 is pointless \u2014 but the \u270e/\u{1f5d1} buttons live exactly there.
function irRemoteSection(r, titleOverride){
  const btns = r.buttons || [];
  const btnRow = b => `
    <div class="row">
      <span class="rlabel">${escapeHtml(b.name)}
        <span class="rval">${b.n} ${irPlural('interval', b.n)}${
          b.proto === 'rf' ? i18n(' \u00b7 433 MHz') : ''}</span></span>
      <span style="display:flex;gap:6px;align-items:center">
        <button class="dlg-btn primary" data-act="send" data-r="${r.id}" data-b="${b.id}"
                title="${i18n('Transmit this button')}">\u25b6</button>
        <button class="renbtn" data-act="ren-btn" data-r="${r.id}" data-b="${b.id}"
                title="${i18n('Rename')}">\u270e</button>
        <button class="renbtn" data-act="del-btn" data-r="${r.id}" data-b="${b.id}"
                title="${i18n('Delete button')}">\u{1f5d1}</button>
      </span>
    </div>`;
  // Inside a remote the buttons are grouped by function too \u2014 in the same order as on the appliance
  const order = [];
  const byGroup = new Map();
  for(const b of btns){
    const g = b.group || '';
    if(!byGroup.has(g)){ byGroup.set(g, []); order.push(g); }
    byGroup.get(g).push(b);
  }
  const hostNames = (r.used_by || [])
    .map(u => (IR.appliances || []).find(a => a.id === u.id))
    .filter(a => a && a.device).map(a => a.device_name || a.device);
  const rows = btns.length
    ? order.map(g => (g ? `<div class="aclbl">${escapeHtml(g)}</div>` : '')
                     + byGroup.get(g).map(btnRow).join('')).join('')
    : `<div class="bindcaption">${hostNames.length
        ? i18n('No extra buttons learned. The basic ones are already on the card') +
          (hostNames.length > 1 ? i18n('s ') : i18n(' ')) +
          hostNames.map(n => '\u00ab' + escapeHtml(n) + '\u00bb').join(', ') +
          i18n(' \u2014 the firmware builds those. Only what the firmware lacks is learned here.')
        : i18n('No buttons yet \u2014 learn the first one')}</div>`;
  // A remote is codes only. Where they fly is the tile's decision, so this is not an
  // emitter picker but the list of tiles that use this remote.
  const used = (r.used_by || []).length
    ? `<div class="bindcaption">${i18n('Used by tiles:')} ${r.used_by.map(u =>
        `\u00ab${escapeHtml(u.name)}\u00bb \u2192 ${escapeHtml(u.blaster_name || u.blaster)}`).join(', ')}.
        ${i18n('The blaster is set there.')}</div>`
    : `<div class="bindcaption">${i18n('No tile uses this remote, so its')}
        ${i18n('has no buttons in the home yet: the tile is created under "Appliances", where')}
        ${i18n('you pick a remote and a blaster.')}</div>`;
  // The permanent emitter belongs to the appliance, but a code has to be tested through
  // a specific blaster \u2014 otherwise it is unclear where the frame went. So the choice IS
  // here, and it affects only \u25b6: nothing is saved.
  const testVia = (r.used_by || [])[0];
  const preset = testVia ? testVia.blaster
                         : ((IR.blasters||[]).find(b=>b.rx)||{}).node;
  const testSel = `<div class="row">
      <span class="rlabel">${i18n('Check after')}
        <span class="rval">${i18n('only for \u25b6, the permanent blaster is set on the appliance')}</span></span>
      <select data-ir-test="${escapeHtml(r.id)}">${
        (IR.blasters||[]).map(b=>`<option value="${escapeHtml(b.node)}"${
          b.node === preset ? ' selected' : ''}>${escapeHtml(b.name)}${
          b.online ? '' : ' (offline)'}</option>`).join('')
      }</select></div>`;
  // This remote's appliances may extend existing cards whose frame is built by the
  // firmware (air conditioners). Their buttons are not, and cannot be, in the remote
  // library \u2014 show where they live, so "no buttons" does not look like a loss.
  // Firmware buttons are just as real, only the blaster builds their frame instead of
  // the server. Show them as a list and make them pressable: hiding them behind the
  // words "none learned" would be a lie, the appliance has them.
  const fwSections = (r.used_by || []).map(u => {
    const ap = (IR.appliances || []).find(x => x.id === u.id);
    const dev = ap && ap.device ? byId(ap.device) : null;
    if(!dev) return '';
    const rows = (dev.controls || []).filter(c => irFwKinds(c)).map(c => {
      const vals = irFwValues(c);
      const cur = ((Home.state(dev.id) || {}).values || {})[c.code];
      // A long NUMERIC range (temperature 16..30) \u2014 a slider, as on the card:
      // fifteen buttons do not fit in a row, and showing one range as text would
      // mean walking to another card for a single degree.
      const numeric = vals.length > 8 && vals.every(v => /^-?\d+$/.test(String(v.value)));
      const unit = c.unit || (c.code === 'temp' ? '\u00b0C' : '');
      let body;
      if(numeric){
        const lo = parseInt(vals[0].value, 10), hi = parseInt(vals[vals.length-1].value, 10);
        const now = (cur === undefined || cur === null || cur === '') ? lo : parseInt(cur, 10);
        body = `<span class="acslide" style="min-width:190px">
            <input type="range" min="${lo}" max="${hi}" step="1" value="${now}"
                   data-fw-slider data-dev="${escapeHtml(dev.id)}"
                   data-code="${escapeHtml(c.code)}" data-unit="${escapeHtml(unit)}">
            <span class="acval" data-fw-out
                  style="font-size:15px;min-width:52px">${now}${escapeHtml(unit)}</span>
          </span>`;
      } else if(vals.length > 8){
        body = `<select data-fw-select data-dev="${escapeHtml(dev.id)}"
                        data-code="${escapeHtml(c.code)}">${
          vals.map(v => `<option value="${escapeHtml(String(v.value))}"${
            String(v.value) === String(cur) ? ' selected' : ''}>${escapeHtml(v.label)}</option>`).join('')
        }</select>`;
      } else {
        body = vals.map(v => `<button class="dlg-btn${
             String(v.value) === String(cur) ? ' primary' : ''}" data-act="fw-press"
             data-dev="${escapeHtml(dev.id)}" data-code="${escapeHtml(c.code)}"
             data-val="${escapeHtml(String(v.value))}"
             title="${i18n('Send to "')}${escapeHtml(dev.name)}\u00bb">${escapeHtml(v.label)}</button>`).join(' ');
      }
      return `<div class="row"><span class="rlabel">${escapeHtml(c.label || c.code)}</span>
        <span style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;align-items:center">${body}</span></div>`;
    }).join('');
    return rows ? `<div class="aclbl">${i18n('From firmware \u00b7')} ${escapeHtml(dev.name)}</div>${rows}` : '';
  }).join('');
  const fwNote = fwSections
    ? `${fwSections}<div class="bindcaption">${i18n('These buttons are built by the blaster firmware')}
        ${i18n('(mqtt_devices.json) \u2014 they work even with the server down. Below is what was')}
        ${i18n('learned here.')}</div>` : '';
  return `
    <section>
      <h3>${escapeHtml(titleOverride || r.name)}
        <button class="renbtn" data-act="ren-remote" data-r="${r.id}"
                title="${i18n('Rename remote')}">\u270e</button>
        <button class="renbtn" data-act="del-remote" data-r="${r.id}"
                title="${i18n('Delete the remote with all its buttons')}">\u{1f5d1}</button></h3>
      ${used}${fwNote}${btns.length ? testSel : ''}${rows}
      <div class="row" style="justify-content:flex-end;gap:6px">
        <button class="dlg-btn primary" data-act="learn" data-r="${r.id}" data-proto="ir">${i18n('\uff0b Learn a button')}</button>
        <button class="dlg-btn" data-act="learn" data-r="${r.id}" data-proto="rf"
                title="${i18n('Learn a 433 MHz remote button (not IR)')}">${i18n('\uff0b 433 MHz')}</button>
      </div>
      ${titleOverride ? ''      /* in a single remote's window this is already said in the header */
        : `<div class="bindcaption">${i18n('Always learned through')}
        ${escapeHtml((IR.blasters||[]).find(b=>b.rx)?.name || i18n('the receiving node'))} \u2014
        ${i18n('has the only receiver.')}</div>`}
      <div class="bindcaption ir-st" data-r="${escapeHtml(r.id)}"></div>
    </section>`;
}

// Brings the remotes' markup to life: the same buttons and selectors work both in the
// blaster's modal and on the "IR remotes" tab \u2014 one handler, no difference in behaviour.
function wireIrPane(root){
  root.onclick = irClick;      // the test-emitter selector is read at the moment \u25b6 is pressed
  // Firmware channels: the slider sends its value on release (sending a frame for every
  // pixel of movement is a queue of IR bursts into nowhere), a list sends at once.
  const note = (node, text, cls)=>{
    const sec = node.closest('section');
    const e = sec && sec.querySelector('.ir-st');
    if(!e) return;
    e.textContent = text || '';
    e.style.color = cls === 'bad' ? 'var(--bad)' : (cls === 'ok' ? 'var(--ok)' : '');
  };
  root.querySelectorAll('[data-fw-slider]').forEach(sl=>{
    const out = sl.parentElement.querySelector('[data-fw-out]');
    sl.oninput = ()=>{ if(out) out.textContent = sl.value + (sl.dataset.unit || ''); };
    sl.onchange = async ()=>{
      try{
        const ok = await sendControl(sl.dataset.dev, sl.dataset.code, parseInt(sl.value, 10));
        note(sl, ok ? i18n('Sent: ') + sl.value + (sl.dataset.unit || '') : i18n('not sent'), ok ? 'ok' : 'bad');
      }catch(e){ note(sl, irErrText(e).slice(0,200), 'bad'); }
    };
  });
  root.querySelectorAll('[data-fw-select]').forEach(sel=>{
    sel.onchange = async ()=>{
      try{
        const ok = await sendControl(sel.dataset.dev, sel.dataset.code, sel.value);
        note(sel, ok ? i18n('Sent: ') + sel.selectedOptions[0].textContent.trim() : i18n('not sent'), ok ? 'ok' : 'bad');
      }catch(e){ note(sel, irErrText(e).slice(0,200), 'bad'); }
    };
  });
}

// "IR remotes" tab: one tile per remote of the home, on the tile the blaster it transmits
// through. All management (buttons, learning, renaming, deleting) lives in the remote's
// modal, so the tab stays an overview and not a wall of text.
// The blaster's modal shows only the remotes learned on it, so a remote moved to another
// emitter is not visible there \u2014 here the whole home is.
function irBlaster(node){
  return (IR.blasters || []).find(b => b.node === node);
}

function irTile(r){
  const btns = r.buttons || [];
  const rf = btns.filter(x => x.proto === 'rf').length;
  const used = r.used_by || [];
  const hubNode = ((IR.blasters || []).find(x => x.rx) || {}).node;
  // cards the appliances of this remote are bound to: they already have buttons
  // (firmware), so an empty list of EXTRA learned buttons is not "no buttons"
  const fw = irFwCount(r);          // buttons the appliances' firmware provides
  // \u270e and \u{1f5d1} in the footer, not in the corner: on top they squeezed the title and
  // "Samsung air conditioner" broke onto two lines. The footer is the card grid's third row.
  return `<div class="card" data-ir="${escapeHtml(r.id)}"
               title="${i18n('Open the remote: buttons, learning, renaming')}">
    <div class="name">${escapeHtml(r.name)}</div>
    <div class="info" style="font-size:15px;gap:6px">
      <div>${btns.length
        ? `<b style="color:var(--txt)">${btns.length}</b> ${
            irPlural('button', btns.length)}${
            rf ? ` \u00b7 ${rf} ${i18n('at 433 MHz')}` : ''}${
            fw.buttons ? ` ${i18n('\u00b7 more')} <b style="color:var(--txt)">${fw.buttons}</b> ${i18n('from firmware')}` : ''}`
        : fw.buttons
          // "no buttons" would be untrue: the appliance has them, only their frame is
          // built by the firmware. Count and show them \u2014 they open in the remote's window.
          ? `<b style="color:var(--txt)">${fw.buttons}</b> ${
              irPlural('button', fw.buttons)} ${i18n('from the firmware in')} ${
              fw.codes} ${irPlural('function', fw.codes)}`
          : i18n('No buttons')}</div>
      <div>${used.length
        ? i18n('Appliances:') + ' <b style="color:var(--txt)">' +
          used.map(u => escapeHtml(u.name)).join('</b>, <b style="color:var(--txt)">') + '</b>'
        : i18n('No appliances')}</div>
      ${r.learned_on && r.learned_on !== hubNode
        ? `<div style="color:var(--bad)">${i18n('learned on')} ${escapeHtml(r.learned_on)}</div>` : ''}
    </div>
    <div style="display:flex;align-items:center;justify-content:flex-end;gap:8px">
      <span style="display:flex;gap:2px;flex:none">
        <button class="renbtn" data-act="ren-remote" data-r="${escapeHtml(r.id)}"
                title="${i18n('Rename remote')}">\u270e</button>
        <button class="renbtn" data-act="del-remote" data-r="${escapeHtml(r.id)}"
                title="${i18n('Delete the remote with all its buttons')}">\u{1f5d1}</button>
      </span>
    </div>
  </div>`;
}

function renderIrTab(app){
  const rs = IR.remotes || [];
  const hub = (IR.blasters || []).find(b => b.rx);
  const wrap = document.createElement('div');
  // A remote = codes only. Where to shine is decided by the tile under "Appliances",
  // otherwise the same Samsung would have to be learned twice \u2014 once per room.
  wrap.innerHTML = `<div style="padding:12px 16px 0;font-size:14px;
        color:var(--dim);line-height:1.5;max-width:900px">
      ${i18n('A remote is a set of codes. Learned through')}
      <b style="color:var(--txt)">${escapeHtml(hub ? hub.name : i18n('the receiving node'))}</b>:
      ${i18n('has the only receiver. For the buttons to work in the home, under the')}
      ${i18n('"Appliances" tab you create')} <b style="color:var(--txt)">${i18n('appliance')}</b> ${i18n('\u2014 there')}
      ${i18n('you pick a remote and a blaster shining into the right room. One remote may serve')}
      ${i18n('in several appliances.')}</div>`;
  // Creation \u2014 with a button in the bar above the grid, not a dashed tile: a tile in the
  // grid reads as one more remote that does not exist.
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;gap:8px;padding:8px 16px 0;flex-wrap:wrap';
  const addBtn = document.createElement('button');
  addBtn.className = 'dlg-btn primary';
  addBtn.textContent = i18n('\uff0b New remote');
  addBtn.title = i18n('Create a remote: a set of codes learned through ')
               + (hub ? hub.name : i18n('the receiving node'));
  addBtn.dataset.act = 'add-remote';
  addBtn.dataset.st = '_';
  addBtn.onclick = ev => irClick(ev);
  bar.append(addBtn);
  wrap.append(bar);
  const g = document.createElement('div');
  g.className = 'grid';
  g.innerHTML = rs.length ? rs.map(r => irTile(r)).join('')
    : '<div class="skeleton">' + i18n('\u2014 no remotes yet \u2014') + '</div>';
  wrap.append(g);
  wrap.insertAdjacentHTML('beforeend',
    '<div class="bindcaption ir-st" data-r="_" style="padding:0 16px"></div>');
  app.append(wrap);

  g.onclick = ev => {
    if(ev.target.closest('[data-act]')){ irClick(ev); return; }
    const tile = ev.target.closest('[data-ir]');
    if(tile) openIrRemoteModal(tile.dataset.ir);
  };

  // the blaster's online state comes with this same answer, not over WS, so entering the
  // tab re-reads a fresh one \u2014 but at most once per 5 s, otherwise render() would loop
  // (loadIrRemotes bumps IR_TS, and the second pass is cut off by this same condition).
  if(Date.now() - IR_TS > 5000){
    IR_TS = Date.now();
    loadIrRemotes().then(()=>{ if(Home.activeTab() === 'p:ir') Home.refresh(); });
  }
}

// The modal of ONE remote \u2014 the same contents as in the blaster's window: change the
// emitter, learn/rename/delete a button, rename/delete the remote.
let IR_RID = null;

function openIrRemoteModal(rid){
  IR_RID = rid;
  Home.openModal('', '__ir_remote__');
  buildIrRemoteModal();
}

function buildIrRemoteModal(){
  const r = (IR.remotes || []).find(x => x.id === IR_RID);
  if(!r){ closeModal(); return; }        // the remote was deleted from this very window
  const hub = (IR.blasters || []).find(x => x.rx);
  const md = document.getElementById('md');
  md.innerHTML = `
    <div class="head">
      <button class="closebtn" onclick="closeModal()">\u00d7</button>
      <h2>${escapeHtml(r.name)}</h2>
      <div class="sub">${i18n('A remote is a set of codes; the tile decides where to shine.')}
        <br>${i18n('Learning \u2014 through')} ${escapeHtml(hub ? hub.name : i18n('the receiving node'))}${i18n(': receiver')}
        ${i18n('has the only receiver. The codes live on the server, the blaster keeps none and only')}
        ${i18n('transmits.')}</div>
    </div>
    <div class="body" id="ir-body">${irRemoteSection(r, i18n('Remote'))}</div>`;
  wireIrPane(md.querySelector('#ir-body'));
}

// IR tile window: what we control (remote), where we shine (blaster), which card we
// extend. Buttons are pressed on the tile's face, so they are not here \u2014 only the links.
let IR_AID = null;

function openIrApplianceModal(aid){
  IR_AID = aid;
  Home.openModal('', '__ir_appliance__');
  buildIrApplianceModal();
}

function buildIrApplianceModal(){
  const a = (IR.appliances || []).find(x => x.id === IR_AID);
  if(!a){ closeModal(); return; }          // the tile was deleted from this very window
  const rem = (IR.remotes || []);
  const md = document.getElementById('md');
  // Cards an IR tile may extend: climate and TV build their frame in firmware,
  // and the tile adds the buttons the firmware lacks.
  const cards = Home.devices().filter(d => !d.ir_appliance &&
                                    ['climate','tv','ir','media'].includes(d.category));
  md.innerHTML = `
    <div class="head">
      <button class="closebtn" onclick="closeModal()">\u00d7</button>
      <h2>${escapeHtml(a.name)}</h2>
      <div class="sub">${i18n('IR tile: remote + blaster. Buttons are pressed on the tile face,')}
        ${i18n('here \u2014 what it controls and where it shines.')}</div>
    </div>
    <div class="body" id="ira-body">
      <section>
        <h3>${i18n('Tile')}
          <button class="renbtn" data-ira="ren" title="${i18n('Rename tile')}">\u270e</button>
          <button class="renbtn" data-ira="del" title="${i18n('Delete tile')}">\u{1f5d1}</button></h3>
        <div class="row">
          <span class="rlabel">${i18n('Remote')} <span class="rval">${i18n('where the buttons come from')}</span></span>
          <select data-ira-sel="remote">${rem.map(r=>`<option value="${escapeHtml(r.id)}"${
            r.id===a.remote?' selected':''}>${escapeHtml(r.name)} (${
            (r.buttons||[]).length})</option>`).join('')}</select>
        </div>
        <div class="row">
          <span class="rlabel">${i18n('Blaster')} <span class="rval">${i18n('which room it shines into')}</span></span>
          <select data-ira-sel="blaster">${(IR.blasters||[]).map(b=>`<option value="${
            escapeHtml(b.node)}"${b.node===a.blaster?' selected':''}>${escapeHtml(b.name)}${
            b.online?'':' (offline)'}</option>`).join('')}</select>
        </div>
        <div class="row">
          <span class="rlabel">${i18n('Extends the card')}
            <span class="rval">${i18n('optional: the AC frame is built by the firmware')}</span></span>
          <select data-ira-sel="device">
            <option value=""${a.device?'':' selected'}>${i18n('\u2014 not bound \u2014')}</option>
            ${cards.map(d=>`<option value="${escapeHtml(d.id)}"${
              d.id===a.device?' selected':''}>${escapeHtml(d.name)}</option>`).join('')}
          </select>
        </div>
        <div class="bindcaption">${(a.buttons||[]).length
          ? `${i18n('Buttons on the remote:')} ${a.buttons.length} ${i18n('\u2014 all of them on the tile face.')}`
          : i18n('The remote has no buttons: learn them under "IR remotes".')}</div>
        <div class="bindcaption ir-st" data-r="ira_${escapeHtml(a.id)}"></div>
      </section>
    </div>`;
  const body = md.querySelector('#ira-body');
  body.onclick = irApplianceClick;
  body.querySelectorAll('[data-ira-sel]').forEach(sel=>{
    sel.onchange = ()=> irAppliancePatch(a.id, {[sel.dataset.iraSel]: sel.value});
  });
}

async function irAppliancePatch(aid, patch){
  try{
    await Home.api(`/api/ir/appliances/${encodeURIComponent(aid)}`, {method:'PATCH',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(patch)});
    await irReload();
    irStatus('ira_' + aid, i18n('Saved'), 'ok');
  }catch(e){ irStatus('ira_' + aid, irErrText(e).slice(0,200), 'bad'); }
}

async function irApplianceClick(ev){
  const b = ev.target.closest('[data-ira]');
  if(!b) return;
  ev.stopPropagation();
  const a = (IR.appliances || []).find(x => x.id === IR_AID);
  if(!a) return;
  try{
    if(b.dataset.ira === 'ren'){
      const name = (prompt(i18n('New tile name:'), a.name) || '').trim();
      if(!name || name === a.name) return;
      await irAppliancePatch(a.id, {name});
    } else if(b.dataset.ira === 'del'){
      if(!confirm(`${i18n('Delete tile "')}${a.name}${i18n('"? The remote and its buttons stay.')}`)) return;
      const res = await Home.api(`/api/ir/appliances/${encodeURIComponent(a.id)}`,
                            {method:'DELETE'});
      if((res.broke || []).length)
        alert(i18n('Tile deleted. It was referenced by:\n') + res.broke.join('\n'));
      closeModal();
      await irReload();
    }
  }catch(e){ irStatus('ira_' + a.id, irErrText(e).slice(0,200), 'bad'); }
}

// Creating a tile: ask for the name, the remote and blaster are chosen right after \u2014 in
// its window, which already has both selectors.
async function irApplianceCreate(){
  const rem = IR.remotes || [];
  if(!rem.length){
    alert(i18n('First create a remote under "IR remotes" \u2014 a tile needs a set of codes.'));
    return;
  }
  const name = (prompt(i18n('Tile name (e.g. "Living room fan"):')) || '').trim();
  if(!name) return;
  const hub = (IR.blasters || []).find(b => b.rx) || (IR.blasters || [])[0];
  try{
    const a = await Home.api('/api/ir/appliances', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, remote: rem[0].id, blaster: hub ? hub.node : ''})});
    await irReload();
    openIrApplianceModal(a.id);      // choose the remote and blaster right away
  }catch(e){ alert(irErrText(e).slice(0,300)); }
}

// Re-read remotes/appliances and redraw everything that shows them. No more scrolling to
// a specific remote: the window is now open on that one alone anyway.
async function irReload(){
  await loadIrRemotes();
  if(Home.modalId() === '__ir_remote__') buildIrRemoteModal();
  else if(Home.modalId() === '__ir_appliance__') buildIrApplianceModal();
  Home.refresh();
}

async function irClick(ev){
  const b = ev.target.closest('[data-act]');
  if(!b) return;
  ev.stopPropagation();
  const act = b.dataset.act, rid = b.dataset.r, bid = b.dataset.b;
  // Where to write the status: for operations on a remote it is its id, for window/tab-level
  // operations an explicit data-st (otherwise the answer would land in someone else's line).
  const stKey = b.dataset.st || rid;
  const r = (IR.remotes || []).find(x => x.id === rid);
  try{
    if(act === 'add-remote'){
      const name = (prompt(i18n('Remote name (e.g. "Bedroom TV"):')) || '').trim();
      if(!name) return;
      // No blaster given: the remote is learned on the receiving node and at first transmits
      // through it too; the emitter can be changed in the remote itself ("Transmit through").
      await Home.api('/api/ir/remotes', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name})});
      await irReload();
    } else if(act === 'ren-remote'){
      const name = (prompt(i18n('New remote name:'), r ? r.name : '') || '').trim();
      if(!name || (r && name === r.name)) return;
      await Home.api(`/api/ir/remotes/${encodeURIComponent(rid)}`, {method:'PATCH',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
      await irReload();
    } else if(act === 'del-remote'){
      const n = r ? (r.buttons||[]).length : 0;
      const apps = r ? (r.used_by || []).map(u => u.name) : [];
      // appliances without a remote have nothing to press, the server removes them with it \u2014
      // ask about this directly, otherwise the tile under "Appliances" would vanish silently
      if(!confirm(`${i18n('Delete remote "')}${r ? r.name : rid}\u00bb${
            n ? ` ${i18n('and all its buttons (')}${n})` : ''}?${
            apps.length ? `${i18n('\n\nThese appliances go with it:')} ${apps.join(', ')}.` : ''}`)) return;
      const res = await Home.api(`/api/ir/remotes/${encodeURIComponent(rid)}`, {method:'DELETE'});
      if((res.broke || []).length)
        alert(i18n('Remote deleted. Its buttons were referenced by:\n') + res.broke.join('\n'));
      if(Home.modalId() === '__ir_remote__') closeModal();
      await irReload();
    } else if(act === 'learn'){
      await irLearn(rid, b.dataset.proto || 'ir');
    } else if(act === 'fw-press'){
      // a host card's button: the switch value arrives as a string
      const raw = b.dataset.val;
      const val = raw === 'true' ? true : raw === 'false' ? false : raw;
      const was = b.textContent;
      b.textContent = '\u2026';
      try{ await sendControl(b.dataset.dev, b.dataset.code, val);
           irStatus(stKey, i18n('Sent: ') + was, 'ok'); }
      finally{ b.textContent = was; }
    } else if(act === 'send'){
      // \u25b6 \u2014 test the code through the node chosen in "Check through" of this same section
      const sel = b.closest('section') &&
                  b.closest('section').querySelector('[data-ir-test]');
      const tx = sel ? sel.value : null;
      const was = b.textContent;
      b.textContent = '\u2026';
      try{ const res = await Home.api(
             `/api/ir/remotes/${encodeURIComponent(rid)}/buttons/${encodeURIComponent(bid)}/send`,
             {method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify(tx ? {tx} : {})});
           irStatus(rid, i18n('Sent via ') + (res.via || tx || i18n('the receiving node')), 'ok'); }
      finally{ b.textContent = was; }
    } else if(act === 'ren-btn'){
      const cur = r ? (r.buttons||[]).find(x => x.id === bid) : null;
      const name = (prompt(i18n('What does this button do?'), cur ? cur.name : '') || '').trim();
      if(!name) return;
      const groups = [...new Set((r ? r.buttons || [] : [])
                       .map(x => x.group).filter(Boolean))];
      const g = prompt(i18n('Group \u2014 appliance function (empty = no group)')
                       + (groups.length ? i18n('\nalready there: ') + groups.join(', ') : ''),
                       (cur && cur.group) || '');
      if(g === null && name === (cur && cur.name)) return;   // cancelled both
      await Home.api(`/api/ir/remotes/${encodeURIComponent(rid)}/buttons/${encodeURIComponent(bid)}`,
        {method:'PATCH', headers:{'Content-Type':'application/json'},
         body: JSON.stringify(g === null ? {name} : {name, group: g.trim()})});
      await irReload();
    } else if(act === 'del-btn'){
      const cur = r ? (r.buttons||[]).find(x => x.id === bid) : null;
      if(!confirm(`${i18n('Delete button "')}${cur ? cur.name : bid}\u00bb?`)) return;
      await Home.api(`/api/ir/remotes/${encodeURIComponent(rid)}/buttons/${encodeURIComponent(bid)}`,
        {method:'DELETE'});
      await irReload();
    }
  }catch(e){
    irStatus(stKey, irErrText(e).slice(0, 200), 'bad');
  }
}

async function irLearn(rid, proto){
  if(IR_BUSY){ irStatus(rid, i18n('Already learning another button'), 'bad'); return; }
  const name = (prompt(i18n('What does the button do? (e.g. "High", "Power")')) || '').trim();
  if(!name) return;
  // Group = appliance function: buttons of one function form their own block on the tile,
  // like FAN on the air conditioner. Suggest the groups this remote already has.
  const r0 = (IR.remotes || []).find(x => x.id === rid);
  const known = [...new Set((r0 ? r0.buttons || [] : [])
                   .map(b => b.group).filter(Boolean))];
  const group = (prompt(i18n('Button group \u2014 appliance function (empty = no group)')
                        + (known.length ? i18n('\nalready there: ') + known.join(', ') : ''),
                        known[0] || '') || '').trim();
  IR_BUSY = true;
  irStatus(rid, proto === 'rf'
    ? i18n('Press a button on the 433 MHz remote next to the blaster\u2026')
    : i18n('Press a button on the remote, pointing it at the blaster from 10\u201320 cm\u2026'));
  try{
    const res = await Home.api(`/api/ir/remotes/${encodeURIComponent(rid)}/learn`, {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, group, proto, timeout: 30})});
    await irReload();
    irStatus(rid, `${i18n('Learned:')} ${res.button.name} (${res.button.n} ${i18n('intervals)')}`, 'ok');
  }catch(e){
    irStatus(rid, irErrText(e).slice(0, 200), 'bad');
  }finally{
    IR_BUSY = false;
  }
}


function irTileFace(c, d){
  c.append(el('div', 'name', escapeHtml(d.name)));
  c.append(el('div', 'info', `<div class="meta">${escapeHtml(i18n('IR tile'))}</div>`));
  irFace(c, d);
}

HomePlugins.register({
  onReload: [loadIrRemotes],
  tabs: [{id: 'ir', label: 'IR remotes', after: 'tech', render: box => renderIrTab(box),
          visible: () => (IR.remotes || []).length > 0 || (IR.blasters || []).length > 0}],
  cardFaces: [{match: d => !!d.ir_appliance, render: irTileFace}],
  cards: [(card, d) => { if(!d.ir_appliance && hostedAppliances(d.id).length) appendHostedIr(card, d.id); }],
  hides: [d => isHostedAppliance(d) || isBlasterCard(d)],
  toolbar: [{tab: 'p:tech', label: '\uff0b IR appliance', onClick: () => irApplianceCreate()}],
  onState: [() => {                       // the window closed in the middle of learning: disarm the node
    if(IR_BUSY && !['__ir_remote__', '__ir_appliance__'].includes(Home.modalId())){
      IR_BUSY = false;
      fetch('/api/ir/learn/cancel', {method: 'POST', headers: Home.authHeaders()}).catch(() => {});
    }
  }],
});
})();
