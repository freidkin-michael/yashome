// IR remotes: the "IR remotes" tab, the remote and appliance windows, the appliance tile face,
// learned buttons on the card an appliance extends, and "+ IR appliance" on the Appliances tab.
(() => {
let IR = {remotes: [], appliances: [], blasters: []}, IR_TS = 0;
const escapeHtml = s => Home.escapeHtml(s), el = (t, c, h) => Home.el(t, c, h);
const sendControl = (id, code, value) => Home.sendControl(id, code, value);
const byId = id => Home.devices().find(d => d.id === id);
const ctl = d => ({presses: (d.controls || []).filter(c => c.kind === 'ir_press')});
// i18n: plural forms per language
const IR_PLURALS = {"en": {"button": ["button", "buttons"]}, "ru": {"button": ["\u043a\u043d\u043e\u043f\u043a\u0430", "\u043a\u043d\u043e\u043f\u043a\u0438", "\u043a\u043d\u043e\u043f\u043e\u043a"]}};
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
  .rc .rrow .dotwrap.inrow{flex-direction:row;gap:8px;min-width:0;margin-right:8px} .rc .rrow .dotwrap.inrow .dotlabel{display:none}
  .rc.folded > :not(:first-child){display:none} .irmore{align-self:stretch;margin-top:6px}
  .acbtn.withstate{display:flex;align-items:center;justify-content:center;gap:8px;width:100%}
  .acbtn.withstate .dot{width:12px;height:12px;min-width:0;box-shadow:none;border-width:1px;font-size:0}
  .acbtn.withstate .dotlabel{font-size:13px;max-width:none;color:var(--txt);display:block}
  .irzone{border:1px solid var(--line);border-radius:12px;padding:8px 10px 10px;display:flex;flex-direction:column;gap:6px}
  .irzone > .aclbl{margin:0;font-weight:600;color:var(--txt)}`;
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

window.irHostedAppliances = id => hostedAppliances(id);   // the TV's remote (plug-in tv) takes their keys
const FACED = ['tv', 'climate'];   // hosts whose card has its own face: the IR part is in their window
// a device an IR tile may extend: its own face, no IR tile itself, no blaster card
function isHostCard(d){
  return FACED.includes(d.category) && !d.ir_appliance && !isBlasterCard(d);
}
// IR appliances living on this card (usually one)
function hostedAppliances(hostId){                 // its appliances, and its own IR part (a merged remote) as one
  const out = Home.devices().filter(d => d.ir_appliance && d.ir_card === hostId);
  const host = byId(hostId), part = host && host.parts && host.parts.ir;
  if(part) out.push({id: hostId, name: host.name, ir_appliance: true, ir_card: hostId, ir_remote: part.remote, ir_via: part.blaster,
                     ir_hide: part.hide || [], controls: (host.controls || []).filter(c => c.part === 'ir')});
  return out;
}

// Block of extra learned IR buttons for a host card: the appliance's buttons in groups plus
// a line with the remote, the blaster and a cog into its settings.
// a phone: a remote shows its first row, the rest opens on "Show all"
function foldRemote(wrap, always){
  if(!always && !(window.matchMedia && window.matchMedia('(max-width: 640px)').matches)) return;
  const rc = wrap.querySelector('.rc:last-of-type');
  if(!rc || rc.children.length < 2 || rc.classList.contains('folded')) return;
  rc.classList.add('folded');
  const more = el('button', 'dlg-btn irmore', i18n('Show all'));
  more.onclick = ev => { ev.stopPropagation(); const f = rc.classList.toggle('folded'); more.textContent = i18n(f ? 'Show all' : 'Fewer'); };
  rc.after(more);
}
// opt.buttons: the remote's buttons; opt.foot: the line with the remote, the blaster and Set up
function appendHostedIr(c, hostId, opt = {buttons: true, foot: true}){
  for(const ap of hostedAppliances(hostId)){
    const C = ctl(ap);
    const wrap = el('div','acpanel');
    const order = [];
    const byGroup = new Map();
    for(const ctl of shownKeys(C.presses, ap.ir_hide)){                    // a key hidden in the IR tab stays hidden
      const g = i18n(ctl.group || '');
      if(!byGroup.has(g)){ byGroup.set(g, []); order.push(g); }
      byGroup.get(g).push(ctl);
    }
    const lay1 = (window.PW && PW.remoteHtml && !multiGroup(C.presses)) ? PW.remoteHtml(C.presses.map(x=>({code:x.code, icon:x.icon, label:(x.group && x.label && x.label.startsWith(x.group+' \u00b7 ')) ? x.label.slice(x.group.length+3) : (x.label||x.code)})), {all: true, hide: ap.ir_hide || []}) : '';
    if(lay1){
      wrap.insertAdjacentHTML('beforeend', lay1);
      foldRemote(wrap, opt.fold);
      for(const b of wrap.querySelectorAll('button[data-code]')) b.onclick = ev=>{ ev.stopPropagation(); const was=b.innerHTML; b.style.opacity='.45';
        sendControl(ap.id, b.dataset.code, true).finally(()=>{ b.innerHTML=was; b.style.opacity=''; }); };
      order.length = 0;                       // the layout is drawn -- flat rows are not needed
      // the card's single power switch (a media box: power by probe) -- goes into the remote's power row
      const dws = c.querySelectorAll('.dotwrap'), prow = wrap.querySelector('.rc > .rrow');
      const pbtn = prow && prow.querySelector('button');
      if(dws.length === 1 && pbtn){   // the state is part of the power key: its dot and word sit inside it, one control
        pbtn.classList.add('withstate');
        pbtn.append(dws[0].querySelector('.dot'), dws[0].querySelector('.dotlabel'));
        pbtn.title = i18n('Power: on when lit');
        dws[0].remove();
      }
    }
    for(const g of order){
      wrap.append(el('div','aclbl', g ? escapeHtml(g) : i18n('IR buttons')));
      const row = el('div','acrow irbtns');
      for(const ctl of byGroup.get(g)){
        const face = g && ctl.label && ctl.label.startsWith(g + ' \u00b7 ')
          ? ctl.label.slice(g.length + 3) : (ctl.label || ctl.code);
        const ico = (window.PW && PW.irIcon) ? PW.irIcon(ctl.code, face, ctl.icon) : '';
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
        escapeHtml(via ? via.name : ap.ir_via || i18n('no blaster set'))}`));
    const gear = el('button','dlg-btn', i18n('Set up'));
    gear.style.cssText = 'min-height:44px;padding:8px 14px;flex:none';
    gear.title = i18n('Set up the IR appliance: remote, blaster, binding');
    gear.onclick = ev=>{ ev.stopPropagation(); openIrApplianceModal(ap.id); };
    foot.append(gear);
    wrap.append(foot);
    if(!opt.buttons) for(const ch of [...wrap.children]) if(ch !== foot) ch.remove();
    if(!opt.foot) foot.remove();
    if(opt.buttons) greyOff(wrap, ap.id);
    c.append(wrap);
  }
}

// the keys a remote shows: the groups it hides (power, nav, volume...) left out, as on the remote's own layout
const keyFace = x => (x.group && x.label && x.label.startsWith(x.group + ' \u00b7 ')) ? x.label.slice(x.group.length + 3) : (x.label || x.code);
function shownKeys(list, hide){
  if(!(hide || []).length || !(window.PW && PW.irVisible)) return list;
  const keep = new Set(PW.irVisible(list.map(x => ({code: x.code, label: keyFace(x)})), hide).map(b => b.code));
  return list.filter(x => keep.has(x.code));
}
// "Power from": the feed's name while it is off (the keys are refused then), else ''
function noPower(devId){
  const pw = (byId(devId) || {}).ir_power;
  return pw && (((Home.state(pw.device) || {}).values) || {})[pw.code] === false ? ((byId(pw.device) || {}).name || pw.device) : '';
}
// every key of an appliance drawn in wrap is greyed while its feed is off, with the line that says which switch to turn on; called
// again on every state change (onState below), so the keys come back the moment the feed is on
function greyOff(wrap, devId){
  wrap.dataset.irgrey = devId;
  const feed = noPower(devId), cap = wrap.querySelector(':scope > .irnopower');
  if(feed && !cap) wrap.prepend(el('div', 'bindcaption irnopower', escapeHtml(i18n('No power: switch on "{0}"', feed))));
  if(!feed && cap) cap.remove();
  for(const l of wrap.querySelectorAll('.aclbl[data-irzone]')) zoneLabel(l, devId, !!feed);
  for(const b of wrap.querySelectorAll('button[data-code], .irbtns button')){
    if(feed){ b.disabled = true; b.style.opacity = '.4'; b.title = i18n('No power'); b.dataset.greyed = '1'; }
    else if(b.dataset.greyed){ b.disabled = false; b.style.opacity = ''; b.title = ''; delete b.dataset.greyed; }
  }
}
// a zone's title with the key last sent in its group; none while the feed is off (that send did nothing)
function zoneLabel(l, devId, off){
  const g = l.dataset.irzone, last = (((Home.state(devId) || {}).values) || {})['sent' + (g ? '_' + g : '')];
  l.textContent = (g ? i18n(g) : i18n('IR buttons')) + (last && !off ? ' \u00b7 ' + i18n('sent: {0}', i18n(last)) : '');
}
// a group's "part of this remote" switch: on when all its buttons say so, mixed (old data) when some do; a click sets all
function partBtn(r, g){
  const bs = (r.buttons || []).filter(x => i18n(x.group || '') === g), n = bs.filter(x => x.part).length;
  const st = n && n === bs.length ? 'true' : n ? 'mixed' : 'false';
  return ` <button class="dlg-btn irpart${st === 'true' ? ' primary' : ''}" data-act="part-group" data-r="${escapeHtml(r.id)}" data-g="${escapeHtml(g)}" aria-pressed="${st}"
    title="${escapeHtml(i18n('Part of this remote: a keypad or the inputs, not another device'))}" style="min-height:26px;padding:1px 9px;font-size:12px">${
    st === 'mixed' ? '\u2013 ' : st === 'true' ? (Home.icon ? Home.icon('check', 12) + ' ' : '') : ''}${escapeHtml(i18n('part of the remote'))}</button>`;
}
// A remote with two or more groups drives two things (a ceiling light with a fan): each group is a
// titled zone of its own, keys by name in their stored order; the layout of one remote would mix them.
function irZones(wrap, devId, list){
  const d = byId(devId) || {}, hide = d.ir_hide || [];
  const order = [], byGroup = new Map();
  for(const p of shownKeys(list, hide)){                                  // a key hidden in the IR tab stays hidden here too
    const g = p.group || '';
    if(!byGroup.has(g)){ byGroup.set(g, []); order.push(g); }
    byGroup.get(g).push(p);
  }
  for(const g of order){
    const zone = el('div', 'irzone');
    const lbl = el('div', 'aclbl');
    lbl.dataset.irzone = g;                                               // filled by greyOff below, and again on every state change
    zone.append(lbl);
    const row = el('div', 'acrow irbtns');
    const keys = byGroup.get(g), faceOf = p => g && p.label && p.label.startsWith(g + ' \u00b7 ') ? p.label.slice(g.length + 3) : (p.label || p.code);
    const icons = window.PW && PW.irIcon && keys.every(p => PW.irIcon(p.code, faceOf(p), p.icon));    // a zone is all icons or all words, never mixed
    for(const p of keys){
      const face = faceOf(p), ico = icons ? PW.irIcon(p.code, face, p.icon) : '';
      const b = el('button', 'acbtn' + (ico ? ' ico' : ''), ico || escapeHtml(i18n(face)));
      if(ico){ b.title = i18n(face); b.setAttribute('aria-label', i18n(face)); }
      b.onclick = ev => { ev.stopPropagation(); const was = b.innerHTML; b.style.opacity = '.45';
        sendControl(devId, p.code, true).finally(() => { b.innerHTML = was; b.style.opacity = ''; }); };
      row.append(b);
    }
    zone.append(row);
    wrap.append(zone);
  }
  greyOff(wrap, devId);
}
// two or more groups of DEVICES (a light and its fan); a group that is part of this remote (a keypad, the inputs) does not count
const multiGroup = list => new Set(list.filter(p => !p.part).map(p => p.group || '')).size > 1;

function irFace(c, d){
    const C2 = ctl(d);
    const wrap = el('div','acpanel');
    if(multiGroup(C2.presses)){
      irZones(wrap, d.id, C2.presses);
      c.append(wrap);
      c.onclick = ()=> openIrApplianceModal(d.id);
      return;
    }
    // Buttons come in groups by function ("Fan", "Light") \u2014 in the same formation as FAN
    // and MODE on the air conditioner. Without a group \u2014 one row on top.
    const groups = [];
    const byGroup = new Map();
    for(const c of shownKeys(C2.presses, d.ir_hide)){
      const g = i18n(c.group || '');
      if(!byGroup.has(g)){ byGroup.set(g, []); groups.push(g); }
      byGroup.get(g).push(c);
    }
    const lay2 = (window.PW && PW.remoteHtml) ? PW.remoteHtml(C2.presses.map(x=>({code:x.code, icon:x.icon, label:(x.group && x.label && x.label.startsWith(x.group+' \u00b7 ')) ? x.label.slice(x.group.length+3) : (x.label||x.code)})), {all: true, hide: d.ir_hide || []}) : '';
    if(lay2){
      wrap.insertAdjacentHTML('beforeend', lay2);
      foldRemote(wrap, true);
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
        const ico = (window.PW && PW.irIcon) ? PW.irIcon(c.code, face, c.icon) : '';
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
    if(!C2.presses.length) wrap.append(el('div','bindcaption',   // with buttons the tile is clean: its remote and blaster are in its window
      `${i18n('Remote "')}${escapeHtml((rem && rem.name) || d.ir_remote || '?')}\u00bb \u00b7 ${
        via ? escapeHtml(via.name) : escapeHtml(d.ir_via || i18n('no blaster set'))}${tail}`));
    greyOff(wrap, d.id);
    c.append(wrap);
    c.onclick = ()=> openIrApplianceModal(d.id);
}

// One window per blaster, a section per remote inside: a flat list of buttons with
// the action's name. Learn = the server arms the node and waits for a frame (the request
// hangs exactly as long as the node listens), press = the server publishes the stored code
// to <node>/tx/{ir,rf}_raw. The node holds no codes, so reflashing loses nothing.
// the ESPHome nodes of the network that may be a blaster: online, not one already; IR-looking names first
function blasterCandidates(nodes){
  const have = new Set((IR.blasters || []).map(b => b.node)), ir = n => /(^|[-_ ])(ir|blaster)/i.test(n.node + ' ' + n.title) ? 0 : 1;
  return nodes.filter(n => n.alive === 'online' && !have.has(n.node)).sort((a, b) => ir(a) - ir(b) || String(a.title).localeCompare(String(b.title)));
}
// a new blaster: picked from the found nodes, its house name from the node's own; the name of its firmware is typed only
// when nothing was found. Resolves {node, name} or null (cancelled)
function askBlasterForm(nodes){
  return new Promise(resolve => {
    const ov = el('div', 'askbd'); ov.style.zIndex = 60;
    const card = el('div', 'askcard'); card.style.maxWidth = '440px';
    card.innerHTML = nodes.length ? `<div class="asklbl">${escapeHtml(i18n('Which device is the blaster?'))}</div>
        <select class="askin" data-f="node">${nodes.map(n => `<option value="${escapeHtml(n.node)}">${escapeHtml(n.title || n.node)}</option>`).join('')}</select>`
      : `<div class="asklbl">${escapeHtml(i18n('No new blaster found on the network. Switch it on and connect it to the house Wi-Fi, then try again; or type the name from its firmware:'))}</div>
        <input class="askin" data-f="node" autocomplete="off" placeholder="ir-blaster">`;
    card.innerHTML += `<div class="asklbl" style="margin-top:12px">${escapeHtml(i18n('Its name in the house'))}</div>
      <input class="askin" data-f="name" autocomplete="off">
      <div class="askbtns"><button class="dlg-btn" data-f="cancel">${escapeHtml(i18n('Cancel'))}</button>
        <button class="dlg-btn primary" data-f="ok">${escapeHtml(i18n('Add'))}</button></div>`;
    const q = f => card.querySelector(`[data-f="${f}"]`);
    const titleOf = v => (nodes.find(n => n.node === v) || {}).title || '';
    let auto = true;                                  // the name follows the pick until it is typed
    q('name').value = titleOf(q('node').value);
    q('name').oninput = () => { auto = false; };
    q('node').onchange = () => { if(auto) q('name').value = titleOf(q('node').value); };
    const done = v => { ov.remove(); resolve(v); };
    q('cancel').onclick = () => done(null);
    q('ok').onclick = () => {
      const node = q('node').value.trim();
      if(!node){ q('node').focus(); return; }
      done({node, name: q('name').value.trim()});
    };
    card.onkeydown = e => { if(e.key === 'Enter' && e.target.tagName === 'INPUT') q('ok').click(); else if(e.key === 'Escape') done(null); };
    ov.append(card); ov.onclick = e => { if(e.target === ov) done(null); };
    document.body.append(ov);
    q('node').focus();
  });
}
// one window for a button: its name, its group (one of the remote's, or a new one) and its icon from the layout's set
// resolves {name, group, icon} or null (cancelled)
// the heading of each icon set of widgets.js (IR_ICON_SETS) in the button form
const ICON_SET_NAMES = {power: 'Power', sound: 'Sound', navigation: 'Navigation', playback: 'Playback', inputs: 'Inputs', colors: 'Colours',
                        light: 'Light', climate: 'Climate', numbers: 'Numbers', misc: 'Other'};
function askButtonForm(cur, groups){
  return new Promise(resolve => {
    const sets = (window.PW && PW.irIconSets) ? PW.irIconSets() : [];
    const ov = el('div', 'askbd'); ov.style.zIndex = 60; ov.style.background = 'rgba(8,10,14,.97)';   // opaque: the window below must not show through
    const card = el('div', 'askcard'); card.style.maxWidth = '440px';
    card.innerHTML = `<div class="asklbl">${escapeHtml(i18n('What does this button do?'))}</div>
      <input class="askin" data-f="name" autocomplete="off">
      <div class="asklbl" style="margin-top:12px">${escapeHtml(i18n('Group \u2014 appliance function (empty = no group)'))}</div>
      <input class="askin" data-f="group" list="ir-groups" autocomplete="off">
      <datalist id="ir-groups">${groups.map(g => `<option value="${escapeHtml(g)}">`).join('')}</datalist>
      <div class="asklbl" style="margin-top:12px">${escapeHtml(i18n('Icon for the button (or text only)'))}</div>
      <div data-f="icons" style="max-height:46vh;overflow:auto;margin:8px 0;scrollbar-width:thin;scrollbar-color:#3a4458 transparent"></div>
      <div class="askbtns"><button class="dlg-btn" data-f="cancel">${escapeHtml(i18n('Cancel'))}</button>
        <button class="dlg-btn primary" data-f="ok">OK</button></div>`;
    const q = f => card.querySelector(`[data-f="${f}"]`);
    // the words as the dashboard shows them; what the user did not touch stays as stored
    const shownName = i18n(cur.name || ''), shownGroup = i18n(cur.group || '');
    q('name').value = shownName; q('group').value = shownGroup;
    const chips = el('div'); chips.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;margin-top:6px';
    for(const g of [...new Set(groups.map(x => i18n(x)))]){
      const ch = el('button', 'dlg-btn', escapeHtml(g)); ch.type = 'button'; ch.style.cssText = 'min-height:36px;padding:4px 12px';
      ch.onclick = () => { q('group').value = g; }; chips.append(ch);
    }
    q('group').after(chips);
    let icon = cur.icon || '';
    const grid = q('icons');
    const paint = () => grid.querySelectorAll('button').forEach(x => x.classList.toggle('sel', x.dataset.k === icon));
    let row = null;
    const section = title => { grid.append(el('div', 'aclbl', escapeHtml(title))); row = el('div'); row.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px'; grid.append(row); };
    const add = (key, html, title) => {
      const bt = el('button', 'acbtn', html); bt.type = 'button'; bt.dataset.k = key; bt.title = title; bt.setAttribute('aria-label', title);
      bt.style.cssText = 'min-width:46px;min-height:46px;flex:0 0 auto'; bt.onclick = () => { icon = key; paint(); };
      row.append(bt);
    };
    sets.forEach(([g, ks], i) => { section(i18n(ICON_SET_NAMES[g] || g)); if(!i) add('', escapeHtml(i18n('Text')), i18n('Text only')); for(const n of ks) add(n, PW.irIcon('', '', n), i18n('icon:' + n)); });
    paint();
    const done = v => { ov.remove(); resolve(v); };
    q('cancel').onclick = () => done(null);
    q('ok').onclick = () => {
      const typed = q('name').value.trim(), g = q('group').value.trim();
      if(!typed){ q('name').focus(); return; }
      done({name: typed === shownName && cur.name ? cur.name : typed, group: g === shownGroup && cur.group ? cur.group : g, icon});
    };
    card.onkeydown = e => { if(e.key === 'Enter' && e.target.tagName === 'INPUT') q('ok').click(); else if(e.key === 'Escape') done(null); };
    ov.append(card); ov.onclick = e => { if(e.target === ov) done(null); };
    document.body.append(ov);
    q('name').focus(); if(window.matchMedia('(pointer:fine)').matches) q('name').select();   // a phone: no selection handles
  });
}

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
      <span class="rlabel">${escapeHtml(i18n(b.name))}
        ${b.proto === 'rf' ? `<span class="rval">${i18n('433 MHz')}</span>` : ''}</span>
      <span style="display:flex;gap:6px;align-items:center">
        <button class="dlg-btn primary" data-act="send" data-r="${r.id}" data-b="${b.id}"
                title="${i18n('Transmit this button')}">${Home.icon('play', 16)}</button>
        <button class="renbtn" data-act="ren-btn" data-r="${r.id}" data-b="${b.id}"
                title="${i18n('Rename')}">${Home.icon('pen', 16)}</button>
        <button class="renbtn" data-act="del-btn" data-r="${r.id}" data-b="${b.id}"
                title="${i18n('Delete button')}">${Home.icon('trash', 16)}</button>
      </span>
    </div>`;
  // Inside a remote the buttons are grouped by function too \u2014 in the same order as on the appliance
  const order = [];
  const byGroup = new Map();
  for(const b of btns){
    const g = i18n(b.group || '');
    if(!byGroup.has(g)){ byGroup.set(g, []); order.push(g); }
    byGroup.get(g).push(b);
  }
  const hostNames = (r.used_by || [])
    .map(u => (IR.appliances || []).find(a => a.id === u.id))
    .filter(a => a && a.device).map(a => a.device_name || a.device);
  const rows = btns.length
    ? order.map(g => (g ? `<div class="aclbl">${escapeHtml(g)} <button class="renbtn" data-act="ren-group" data-r="${escapeHtml(r.id)}" data-g="${escapeHtml(g)}" title="${i18n('Rename the group')}">${Home.icon('pen', 14)}</button>${partBtn(r, g)}</div>` : '')
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
        <span class="rval">${i18n('only for the test send of a key; the permanent blaster is set on the appliance')}</span></span>
      <select data-ir-test="${escapeHtml(r.id)}">${
        (IR.blasters||[]).map(b=>`<option value="${escapeHtml(b.node)}"${
          b.node === preset ? ' selected' : ''}>${escapeHtml(b.name)}${
          b.online ? '' : i18n(' (offline)')}</option>`).join('')
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
      } else if(c.kind === 'switch'){                 // on/off: the one power button of the dashboard; no report yet = unknown
        const known = cur !== undefined && cur !== null && cur !== '', on = known && !!cur;      // !!value: as isOn() of the cards
        body = `<div class="toggle${Home.toggleGlyph ? Home.toggleGlyph(c.code) : ''}${on ? ' on' : ''}${known ? '' : ' unknown'}" data-act="fw-press" data-dev="${escapeHtml(dev.id)}" data-code="${escapeHtml(c.code)}" data-val="${on ? 'false' : 'true'}"></div>`;
      } else {
        // by its value (cool, low, play...): a row is all icons, the word their title, when each key has one, else all words
        const ico = v => window.PW ? (PW.acIcon(String(v.value)) || PW.irIcon(String(v.value), '', '')).replace('<svg ', '<svg width="20" height="20" ') : '';
        const icons = vals.every(v => ico(v));
        body = vals.map(v => `<button class="dlg-btn${
             String(v.value) === String(cur) ? ' primary' : ''}" data-act="fw-press"
             data-dev="${escapeHtml(dev.id)}" data-code="${escapeHtml(c.code)}"
             data-val="${escapeHtml(String(v.value))}"${icons ? ` aria-label="${escapeHtml(v.label)}"` : ''}
             title="${icons ? escapeHtml(v.label) + ' \u00b7 ' : ''}${i18n('Send to "')}${escapeHtml(dev.name)}\u00bb">${icons ? ico(v) : escapeHtml(v.label)}</button>`).join(' ');
      }
      return `<div class="row"><span class="rlabel">${escapeHtml(c.label || c.code)}</span>
        <span style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;align-items:center">${body}</span></div>`;
    }).join('');
    return rows ? `<div class="aclbl">${i18n('From firmware \u00b7')} ${escapeHtml(dev.name)}</div>${rows}` : '';
  }).join('');
  const fwNote = fwSections
    ? `${fwSections}<div class="bindcaption">${i18n('These buttons are built into the blaster: they work even when the house server is down. Below are the ones learned here.')}</div>` : '';
  return `
    <section>
      <h3>${escapeHtml(titleOverride || r.name)}
        <button class="renbtn" data-act="ren-remote" data-r="${r.id}"
                title="${i18n('Rename remote')}">${Home.icon('pen', 16)}</button>
        <button class="renbtn" data-act="del-remote" data-r="${r.id}"
                title="${i18n('Delete the remote with all its buttons')}">${Home.icon('trash', 16)}</button></h3>
      ${used}${fwNote}${btns.length ? testSel : ''}${rows}
      ${hideRow(r)}
      <div class="row" style="justify-content:flex-end;gap:6px">
        <button class="dlg-btn primary" data-act="learn" data-r="${r.id}" data-proto="ir">${i18n('+ Learn a button')}</button>
        <button class="dlg-btn" data-act="learn" data-r="${r.id}" data-proto="rf"
                title="${i18n('Learn a 433 MHz remote button (not IR)')}">${i18n('+ 433 MHz')}</button>
      </div>
      ${titleOverride ? ''      /* in a single remote's window this is already said in the header */
        : `<div class="bindcaption">${i18n('Always learned through')}
        ${escapeHtml((IR.blasters||[]).find(b=>b.rx)?.name || i18n('the receiving node'))} \u2014
        ${i18n('has the only receiver.')}</div>`}
      <div class="bindcaption ir-st" data-r="${escapeHtml(r.id)}"></div>
    </section>`;
}

// which groups of keys the remote's face shows: a lit chip is shown, a press hides or shows it (the keys stay learned)
const IR_GROUPS = ['power', 'nav', 'volume', 'channels', 'media', 'other'];
function hideRow(r){
  const hid = r.hide || [];
  return `<div class="aclbl">${i18n('Shown on the remote')}</div><div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:8px">`
    + IR_GROUPS.map(g => `<button class="dlg-btn${hid.includes(g) ? '' : ' primary'}" data-act="hide-group" data-r="${escapeHtml(r.id)}" data-g="${g}"
        title="${i18n('Show or hide this group of keys on the remote')}">${i18n('irgroup:' + g)}</button>`).join('') + '</div>';
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
  // a long remote name broke onto two lines. The footer is the card grid's third row.
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
              irPlural('button', fw.buttons)} ${i18n('built into the blaster')}`
          : i18n('No buttons')}</div>
      <div>${used.length
        ? i18n('Appliances:') + ' <b style="color:var(--txt)">' +
          used.map(u => escapeHtml(u.name)).join('</b>, <b style="color:var(--txt)">') + '</b>'
        : i18n('No appliances')}</div>
      ${r.learned_on && r.learned_on !== hubNode
        ? `<div style="color:var(--bad)">${escapeHtml(i18n('learned through another blaster: {0}', (irBlaster(r.learned_on) || {}).name || r.learned_on))}</div>` : ''}
    </div>
    <div style="display:flex;align-items:center;justify-content:flex-end;gap:8px">
      <span style="display:flex;gap:2px;flex:none">
        <button class="renbtn" data-act="ren-remote" data-r="${escapeHtml(r.id)}"
                title="${i18n('Rename remote')}">${Home.icon('pen', 16)}</button>
        <button class="renbtn" data-act="del-remote" data-r="${escapeHtml(r.id)}"
                title="${i18n('Delete the remote with all its buttons')}">${Home.icon('trash', 16)}</button>
      </span>
    </div>
  </div>`;
}

// the blasters of the house: one card each, its name (renamed here), state, who sends through it; delete is refused while anything does
function blasterTile(b){
  const users = b.users || [];
  return `<div class="card" data-blaster="${escapeHtml(b.node)}">
    <div class="name">${escapeHtml(b.name)}</div>
    <div class="info" style="font-size:15px;gap:6px">
      <div>${b.online ? i18n('online') : `<span style="color:var(--bad)">${i18n('offline: check that it has power and Wi-Fi')}</span>`}${b.rx ? ' \u00b7 ' + i18n('learns') : ''}</div>
      <div>${users.length ? i18n('Sends for:') + ' <b style="color:var(--txt)">' + users.map(escapeHtml).join('</b>, <b style="color:var(--txt)">') + '</b>'
        : i18n('Nothing sends through it')}</div>
    </div>
    <div style="display:flex;align-items:center;justify-content:flex-end;gap:6px;flex-wrap:wrap">
      ${b.device ? `<button class="dlg-btn" data-act="card-blaster" data-d="${escapeHtml(b.device)}" data-st="_" style="min-height:32px;padding:2px 10px">${i18n('Open its card')}</button>` : ''}
      ${b.rx ? '' : `<button class="dlg-btn" data-act="rx-blaster" data-n="${escapeHtml(b.node)}" data-st="_" style="min-height:32px;padding:2px 10px" title="${i18n('Learn new buttons through this blaster')}">${i18n('Learn through it')}</button>`}
      <button class="renbtn" data-act="ren-blaster" data-n="${escapeHtml(b.node)}" data-st="_" title="${i18n('Rename blaster')}">${Home.icon('pen', 16)}</button>
      <button class="renbtn" data-act="del-blaster" data-n="${escapeHtml(b.node)}" data-st="_" title="${i18n('Delete blaster')}">${Home.icon('trash', 16)}</button>
    </div>
  </div>`;
}

function renderIrTab(app, opts = {}){
  const page = !!opts.page;                // Settings -> IR remotes: its add buttons are the page's own row; no padding of the main tab
  const rs = IR.remotes || [];
  const hub = (IR.blasters || []).find(b => b.rx);
  const wrap = document.createElement('div');
  // A remote = codes only. Where to shine is decided by the tile under "Appliances",
  // otherwise the same remote would have to be learned twice \u2014 once per room.
  wrap.innerHTML = `<div style="padding:${page ? '0' : '12px 16px 0'};font-size:14px;
        color:var(--dim);line-height:1.5;max-width:900px">
      ${i18n('A remote is a set of codes; the tile decides where to shine.')}</div>`
    + (page ? `<h3 style="margin:16px 0 0;font-size:16px">${escapeHtml(i18n('Remotes'))}</h3>` : '');
  // Creation \u2014 with a button in the bar above the grid, not a dashed tile: a tile in the
  // grid reads as one more remote that does not exist.
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;gap:8px;padding:8px 16px 0;flex-wrap:wrap';
  const addBtn = document.createElement('button');
  addBtn.className = 'dlg-btn primary';
  addBtn.textContent = i18n('+ New remote');
  addBtn.title = i18n('Create a remote: a set of codes learned through ')
               + (hub ? hub.name : i18n('the receiving node'));
  addBtn.dataset.act = 'add-remote';
  addBtn.dataset.st = '_';
  addBtn.onclick = ev => irClick(ev);
  bar.append(addBtn);
  if(!page) wrap.append(bar);
  const g = document.createElement('div');
  g.className = 'grid';
  g.innerHTML = rs.length ? rs.map(r => irTile(r)).join('')
    : '<div class="skeleton">' + i18n('\u2014 no remotes yet \u2014') + '</div>';
  wrap.append(g);
  wrap.insertAdjacentHTML('beforeend', `<div style="display:flex;gap:8px;align-items:center;padding:${page ? '16px 0 0' : '12px 16px 0'}"><h3 style="margin:0;font-size:16px">${escapeHtml(i18n('Blasters'))}</h3>
    ${page ? '' : `<button class="dlg-btn" data-act="add-blaster" data-st="_" style="min-height:32px;padding:2px 10px">${escapeHtml(i18n('+ Blaster'))}</button>`}</div>`);
  if(!page) wrap.lastElementChild.querySelector('[data-act]').onclick = ev => irClick(ev);
  const bg = document.createElement('div');
  bg.className = 'grid';
  bg.innerHTML = (IR.blasters || []).map(blasterTile).join('') || '<div class="skeleton">' + i18n('\u2014 no blasters \u2014') + '</div>';
  bg.onclick = ev => { if(ev.target.closest('[data-act]')) irClick(ev); };
  wrap.append(bg);
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
      <div class="sub">${i18n('The usual buttons are there; learn the missing ones here.')}
        ${hub ? escapeHtml(i18n('Point the remote at {0} to learn.', hub.name)) : ''}</div>
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
  // Cards an IR tile may extend: a device with a face of its own (a network TV, an AC), never a blaster;
  // the tile's room first, then TVs. A host set before this rule stays listed so it keeps working.
  const room = Home.roomOf ? Home.roomOf(a.id) : '';
  const cards = Home.devices().filter(d => isHostCard(d) || d.id === a.device)
    .sort((x, y) => (Home.roomOf(y.id) === room) - (Home.roomOf(x.id) === room) || (y.category === 'tv') - (x.category === 'tv')
          || String(x.name).localeCompare(String(y.name)));
  // channels that can feed it: a switch of another device ("Switch 1 \u00b7 Left")
  const feeds = Home.devices().filter(d => !d.ir_appliance).flatMap(d => (d.controls || [])
    .filter(c => c.kind === 'switch' && !c.ro).map(c => ({dev: d.id, code: c.code, label: d.name + ((d.controls || []).filter(x => x.kind === 'switch').length > 1 ? ' \u00b7 ' + (c.label || c.code) : '')})));
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
          <button class="renbtn" data-ira="ren" title="${i18n('Rename tile')}">${Home.icon('pen', 16)}</button>
          <button class="renbtn" data-ira="del" title="${i18n('Delete tile')}">${Home.icon('trash', 16)}</button></h3>
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
            b.online?'':i18n(' (offline)')}</option>`).join('')}</select>
        </div>
        <div class="row">
          <span class="rlabel">${i18n('Extends the card')}
            <span class="rval">${i18n('Show this appliance\'s IR keys in another device\'s window (a network TV, say) and hide its own card')}</span></span>
          <select data-ira-sel="device">
            <option value=""${a.device?'':' selected'}>${i18n('\u2014 not bound \u2014')}</option>
            ${cards.map(d=>`<option value="${escapeHtml(d.id)}"${
              d.id===a.device?' selected':''}>${escapeHtml(d.name)}</option>`).join('')}
          </select>
        </div>
        <div class="row">
          <span class="rlabel">${i18n('Power from')} <span class="rval">${i18n('the switch channel that feeds it; IR keys wait for it')}</span></span>
          <select data-ira-power>
            <option value=""${a.power ? '' : ' selected'}>${i18n('\u2014 none \u2014')}</option>
            ${feeds.map(f => `<option value="${escapeHtml(f.dev + '|' + f.code)}"${a.power && a.power.device === f.dev && a.power.code === f.code ? ' selected' : ''}>${escapeHtml(f.label)}</option>`).join('')}
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
  const pwSel = body.querySelector('[data-ira-power]');
  pwSel.onchange = () => { const [device, code] = pwSel.value.split('|'); irAppliancePatch(a.id, {power: pwSel.value ? {device, code} : {}}); };
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
      const name = (await Home.askText(i18n('New tile name:'), a.name) || '').trim();
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
  const name = (await Home.askText(i18n('Tile name:')) || '').trim();
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
      const name = (await Home.askText(i18n('Remote name:') + '\n' + i18n('Then press the buttons of the real remote at the receiving node: each press is learned as a button.'), '', i18n('TV in the living room')) || '').trim();
      if(!name) return;
      // No blaster given: the remote is learned on the receiving node and at first transmits
      // through it too; the emitter can be changed in the remote itself ("Transmit through").
      const made = await Home.api('/api/ir/remotes', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name})});
      await irReload();
      if(made && made.id && (IR.blasters || []).some(x => x.rx)){ openIrRemoteModal(made.id); irLearn(made.id, 'ir'); }   // straight on to the first button
    } else if(act === 'hide-group'){
      const g = b.dataset.g, hid = new Set(r ? r.hide || [] : []);
      if(hid.has(g)) hid.delete(g); else hid.add(g);
      await Home.api(`/api/ir/remotes/${encodeURIComponent(rid)}`, {method:'PATCH',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({hide: [...hid]})});
      await irReload();
      if(Home.modalId() === '__ir_remote__') openIrRemoteModal(rid);
    } else if(act === 'ren-remote'){
      const name = (await Home.askText(i18n('New remote name:'), r ? r.name : '') || '').trim();
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
      if(b.classList.contains('toggle')){
        const was = [b.classList.contains('on'), b.classList.contains('unknown'), b.dataset.val];
        b.classList.toggle('on', val === true); b.classList.remove('unknown'); b.dataset.val = String(val !== true);
        if(await sendControl(b.dataset.dev, b.dataset.code, val)){            // false = not sent: it said why already
          irStatus(stKey, i18n('Sent: ') + i18n(val === true ? 'On' : 'Off'), 'ok');
        } else {
          b.classList.toggle('on', was[0]); b.classList.toggle('unknown', was[1]); b.dataset.val = was[2];
          irStatus(stKey, i18n('Not sent'), 'bad');
        }
      } else {
        const was = b.textContent;
        b.textContent = '\u2026';
        try{ const ok = await sendControl(b.dataset.dev, b.dataset.code, val);
             irStatus(stKey, ok ? i18n('Sent: ') + was : i18n('Not sent'), ok ? 'ok' : 'bad'); }
        finally{ b.textContent = was; }
      }
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
           irStatus(rid, i18n('Sent via ') + ((irBlaster(res.via || tx) || {}).name || i18n('the receiving node')), 'ok'); }
      finally{ b.textContent = was; }
    } else if(act === 'ren-btn'){
      const cur = r ? (r.buttons||[]).find(x => x.id === bid) : null;
      const groups = [...new Set((r ? r.buttons || [] : []).map(x => x.group).filter(Boolean))];
      const f = await askButtonForm({name: cur ? cur.name : '', group: cur && cur.group, icon: cur && cur.icon}, groups);
      if(!f) return;
      await Home.api(`/api/ir/remotes/${encodeURIComponent(rid)}/buttons/${encodeURIComponent(bid)}`,
        {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify(f)});
      await irReload();
    } else if(act === 'ren-group'){
      const old = b.dataset.g;
      const name = await Home.askText(i18n('New name of the group'), old);
      if(name === null || !name.trim() || name.trim() === old) return;
      for(const x of (r ? r.buttons || [] : []).filter(x => i18n(x.group || '') === old))
        await Home.api(`/api/ir/remotes/${encodeURIComponent(rid)}/buttons/${encodeURIComponent(x.id)}`,
          {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({group: name.trim(), part: !!x.part})});    // the group keeps its kind
      await irReload();
    } else if(act === 'part-group'){
      const g = b.dataset.g, on = b.getAttribute('aria-pressed') !== 'true';                // off or mixed: all on; on: all off
      for(const x of (r ? r.buttons || [] : []).filter(x => i18n(x.group || '') === g))
        await Home.api(`/api/ir/remotes/${encodeURIComponent(rid)}/buttons/${encodeURIComponent(x.id)}`,
          {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({part: on})});
      await irReload();
    } else if(act === 'add-blaster'){
      irStatus('_', i18n('Looking for blasters on the network\u2026'));
      let found = [];
      try { found = (await Home.api('/api/esphome/discover')).nodes || []; } catch(e){ /* nothing found: the form asks for the name */ }
      irStatus('_', '');
      const f = await askBlasterForm(blasterCandidates(found));
      if(!f) return;
      await Home.api('/api/ir/blasters', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(f)});
      await irReload();
    } else if(act === 'rx-blaster'){
      await Home.api(`/api/ir/blasters/${encodeURIComponent(b.dataset.n)}`, {method:'PATCH',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({rx: true})});
      await irReload();
    } else if(act === 'card-blaster'){
      Home.openDevice(b.dataset.d);                    // the card that shows it: its node's, or its own device
    } else if(act === 'ren-blaster'){
      const bl = irBlaster(b.dataset.n);
      const name = (await Home.askText(i18n('New blaster name:'), bl ? bl.name : '') || '').trim();
      if(!name || (bl && name === bl.name)) return;
      await Home.api(`/api/ir/blasters/${encodeURIComponent(b.dataset.n)}`, {method:'PATCH',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
      await irReload();
    } else if(act === 'del-blaster'){
      const bl = irBlaster(b.dataset.n);
      if((bl && bl.users || []).length){ irStatus(stKey, i18n('Cannot delete: it sends for ') + bl.users.join(', '), 'bad'); return; }
      if(!confirm(`${i18n('Delete blaster "')}${bl ? bl.name : b.dataset.n}\u00bb?`)) return;
      await Home.api(`/api/ir/blasters/${encodeURIComponent(b.dataset.n)}`, {method:'DELETE'});
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
  const r0 = (IR.remotes || []).find(x => x.id === rid);
  const known = [...new Set((r0 ? r0.buttons || [] : []).map(b => b.group).filter(Boolean))];
  const f = await askButtonForm({name: '', group: known[0] || '', icon: ''}, known);
  if(!f) return;
  const {name, group, icon} = f;
  IR_BUSY = true;
  irStatus(rid, proto === 'rf'
    ? i18n('Press a button on the 433 MHz remote next to the blaster\u2026')
    : i18n('Press a button on the remote, pointing it at the blaster from 10\u201320 cm\u2026'));
  try{
    const res = await Home.api(`/api/ir/remotes/${encodeURIComponent(rid)}/learn`, {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, group, proto, timeout: 30, icon: icon || undefined})});
    await irReload();
    irStatus(rid, `${i18n('Learned:')} ${res.button.name}`, 'ok');
  }catch(e){
    irStatus(rid, irErrText(e).slice(0, 200), 'bad');
  }finally{
    IR_BUSY = false;
  }
}


function irTileFace(c, d){
  c.append(el('div', 'name', (Home.devIcon ? Home.devIcon(d) : '') + escapeHtml(d.name)));
  c.append(el('div', 'info', `<div class="meta">${escapeHtml((Home.roomOf && Home.roomOf(d.id)) || i18n('IR tile'))}</div>`));
  irFace(c, d);
}

HomePlugins.register({
  onReload: [loadIrRemotes],
  settings: [{subject: 'ir', title: 'IR remotes', render: box => {          // Settings -> IR remotes: one row of adds, then the remotes and the blasters
    const row = Home.el('div', 'stx-actions');
    for(const [label, act] of [['+ IR appliance', ''], ['+ Remote', 'add-remote'], ['+ Blaster', 'add-blaster']]){
      const b = Home.el('button', 'dlg-btn', Home.escapeHtml(i18n(label)));
      if(act){ b.dataset.act = act; b.dataset.st = '_'; b.onclick = ev => irClick(ev); } else b.onclick = () => irApplianceCreate();
      row.append(b);
    }
    box.append(row);
    const in2 = Home.el('div'); box.append(in2); renderIrTab(in2, {page: true}); }}],
  tabs: [{id: 'tech', label: 'Appliances', cats: ['ir', 'media'], after: 'button'},
         {id: 'ir', label: 'IR remotes', after: 'tech', render: box => renderIrTab(box), badge: () => (IR.remotes || []).length,
          visible: () => (IR.remotes || []).length > 0 || (IR.blasters || []).length > 0}],
  cardFaces: [{match: d => !!d.ir_appliance, render: irTileFace,
               update: (c, d) => { if(d.ir_power){ c.querySelectorAll('.acpanel').forEach(x => x.remove()); irFace(c, d); } }}],   // its feed or what was sent changed
  windows: [{id: 'ir.remote', open: async id => { await loadIrRemotes(); openIrRemoteModal(id); }},           // opened from other plug-ins:
             {id: 'ir.appliance', open: async id => { await loadIrRemotes(); openIrApplianceModal(id); }}],   // the list fresh first
  // a TV or an AC keeps only its daily controls on the card; its IR remote and binding are in its window
  cards: [(card, d) => { if(!d.ir_appliance && hostedAppliances(d.id).length && !FACED.includes(d.category)) appendHostedIr(card, d.id, {buttons: true, foot: false, fold: true}); }],
  modalSections: [{                    // a lamp with a fan opened from elsewhere (the map): its zones, the power hint, what was sent
    match: d => !!d.ir_appliance && multiGroup(ctl(d).presses),
    html: () => '<div data-ir="zones"></div>',
    wire(box, d){ const w = el('div', 'acpanel'); irZones(w, d.id, ctl(d).presses); box.querySelector('[data-ir="zones"]').append(w); },
    update(box, d){ const z = box.querySelector('[data-ir="zones"]'); if(!z) return; z.innerHTML = ''; const w = el('div', 'acpanel'); irZones(w, d.id, ctl(d).presses); z.append(w); },
  }, {
    match: d => !d.ir_appliance && hostedAppliances(d.id).length > 0,
    html: () => `<h3>${escapeHtml(i18n('IR control'))}</h3><div class="bindcaption">${escapeHtml(i18n('The IR remote that switches this device on when it is off, and the blaster that sends it.'))}</div><div data-ir="host"></div>`,
    // a TV: its keys are in the TV's one remote (plug-in tv), here only which IR remote and blaster
    wire(box, d){ appendHostedIr(box.querySelector('[data-ir="host"]'), d.id, {buttons: FACED.includes(d.category) && d.category !== 'tv', foot: true}); },
  }],
  hides: [d => isHostedAppliance(d) || isBlasterCard(d)],
  // the help sheet and the header's dragged "?" (README "Help"); icon buttons with a title are in the sheet only
  help: [
    {id: 'ir.addremote', screen: 'tab:p:ir', sel: '[data-act="add-remote"]', title: '+ New remote', text: 'A new remote: then press the real remote\'s keys at the receiving blaster, each press is learned as a button.'},
    {id: 'ir.learn', screen: 'tab:p:ir', sel: '[data-act="learn"]', title: '+ Learn a button', text: 'Point the remote at the receiving blaster and press the key: its code is learned. + 433 MHz does the same for a radio remote.'},
    {id: 'ir.test', screen: 'tab:p:ir', sel: '[data-ir-test]', title: 'Check after', text: 'The blaster the test button sends through; nothing is saved.'},
    {id: 'ir.send', screen: 'tab:p:ir', sel: '[data-act="send"]', title: 'Transmit this button', text: 'Sends the learned code now, through the blaster chosen above it.'},
    {id: 'ir.renbtn', screen: 'tab:p:ir', sel: '[data-act="ren-btn"]', title: 'Edit the button', text: 'Its name, its group and its icon.'},
    {id: 'ir.delbtn', screen: 'tab:p:ir', sel: '[data-act="del-btn"]', title: 'Delete button', text: 'Forgets this learned code.'},
    {id: 'ir.rengroup', screen: 'tab:p:ir', sel: '[data-act="ren-group"]', title: 'Rename the group', text: 'Renames the group in every button of it.'},
    {id: 'ir.partgroup', screen: 'tab:p:ir', sel: '[data-act="part-group"]', title: 'part of the remote',
     text: 'On: the group is a keypad or the inputs of this remote and stays on its face. Off: the group is another device (a lamp\'s fan) and gets a zone of its own on the card. A dash: its buttons disagree; a tap sets all.'},
    {id: 'ir.hidegroup', screen: 'tab:p:ir', sel: '[data-act="hide-group"]', title: 'Shown on the remote', text: 'A lit group is shown on the remote\'s face; a press hides or shows it. Its keys stay learned.'},
    {id: 'ir.renremote', screen: 'tab:p:ir', sel: '[data-act="ren-remote"]', title: 'Rename remote', text: 'A new name for this remote.'},
    {id: 'ir.delremote', screen: 'tab:p:ir', sel: '[data-act="del-remote"]', title: 'Delete the remote with all its buttons', text: 'The tiles that use it go with it; you are asked first.'},
    {id: 'ir.fwpress', screen: 'tab:p:ir', sel: '[data-act="fw-press"]', title: 'Firmware button', text: 'A key the blaster\'s firmware builds itself (an AC\'s): a press sends it now.'},
    {id: 'ir.fwslider', screen: 'tab:p:ir', sel: '[data-fw-slider]', title: 'Value', text: 'Sets this value now, for example the temperature.'},
    {id: 'ir.fwselect', screen: 'tab:p:ir', sel: '[data-fw-select]', title: 'Setting', text: 'Picks this setting now, for example the mode.'},
    {id: 'ir.rx', screen: 'tab:p:ir', sel: '[data-act="rx-blaster"]', title: 'Learn through it', text: 'New buttons are learned through this blaster\'s receiver.'},
    {id: 'ir.renblaster', screen: 'tab:p:ir', sel: '[data-act="ren-blaster"]', title: 'Rename blaster', text: 'A new name for this blaster.'},
    {id: 'ir.blastercard', screen: 'tab:p:ir', sel: '[data-act="card-blaster"]', title: 'Open its card', text: 'The device window of this blaster: its room, its name, where it is used.'},
    {id: 'ir.delblaster', screen: 'tab:p:ir', sel: '[data-act="del-blaster"]', title: 'Delete blaster', text: 'Forgets the blaster; its icon leaves the map.'},
    {id: 'ir.addblaster', screen: 'tab:p:ir', sel: '[data-act="add-blaster"]', title: '+ Blaster', text: 'Adds a blaster found on the network, under a name in the house.'},
    {id: 'irblaster.node', screen: 'ir-blaster-form', screenName: 'IR blaster', sel: '[data-f="node"]', title: 'Blaster', text: 'The device found on the network that is the blaster; when none is found, the name from its firmware.'},
    {id: 'irbutton.name', screen: 'ir-button-form', screenName: 'IR button', sel: '[data-f="name"]', at: 'label', title: 'Name', text: 'The button\'s name on the remote and on the tile.'},
    {id: 'irbutton.group', screen: 'ir-button-form', screenName: 'IR button', sel: '[data-f="group"]', at: 'label', title: 'Group', text: 'One of the remote\'s groups or a new one: a group\'s keys sit together.'},
    {id: 'irbutton.cancel', screen: 'ir-button-form', screenName: 'IR button', sel: '[data-f="cancel"]', title: 'Cancel', text: 'Nothing changes.'},
    {id: 'irbutton.ok', screen: 'ir-button-form', screenName: 'IR button', sel: '[data-f="ok"]', title: 'OK', text: 'Saves the button.'},
    {id: 'irtile.ren', screen: 'ir-tile', screenName: 'IR tile', sel: '[data-ira="ren"]', title: 'Rename tile', text: 'A new name for this tile.'},
    {id: 'irtile.del', screen: 'ir-tile', screenName: 'IR tile', sel: '[data-ira="del"]', title: 'Delete tile', text: 'The tile goes, with its Power from wire; the remote stays.'},
    {id: 'irtile.remote', screen: 'ir-tile', screenName: 'IR tile', sel: '[data-ira-sel="remote"]', title: 'Remote', text: 'Whose learned keys the tile has.'},
    {id: 'irtile.blaster', screen: 'ir-tile', screenName: 'IR tile', sel: '[data-ira-sel="blaster"]', title: 'Blaster', text: 'The blaster that sends the tile\'s keys.'},
    {id: 'irtile.device', screen: 'ir-tile', screenName: 'IR tile', sel: '[data-ira-sel="device"]', title: 'Extends the card', text: 'For example: the bedroom TV is a network TV and also has a learned IR remote. Choose the network TV here: its window gets the IR keys (an input, a power-on), and this IR tile no longer shows as a card of its own. Only devices with a face of their own are offered (a TV, an air conditioner).'},
    {id: 'irtile.power', screen: 'ir-tile', screenName: 'IR tile', sel: '[data-ira-power]', title: 'Power from', text: 'The switch channel that feeds it: while it is off, the IR keys wait. A wire on the map.'},
  ],
  toolbar: [{tab: 'p:tech', label: '+ IR appliance', onClick: () => irApplianceCreate(),
    help: {text: 'A tile for a device you control by IR: a name, then its remote and blaster. Needs an IR remote first.'}}],
  onState: [() => document.querySelectorAll('[data-irgrey]').forEach(w => greyOff(w, w.dataset.irgrey)),   // one onState key: a second replaces it
    () => {                               // the window closed in the middle of learning: disarm the node
    if(IR_BUSY && !['__ir_remote__', '__ir_appliance__'].includes(Home.modalId())){
      IR_BUSY = false;
      fetch('/api/ir/learn/cancel', {method: 'POST', headers: Home.authHeaders()}).catch(() => {});
    }
  }],
});
})();
