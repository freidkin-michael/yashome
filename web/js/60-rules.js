// Central "Automations" tab -- one unified list over /api/rules (bindings +
// schedule + inching), with a guided WHEN -> IF -> THEN builder. Creation writes
// through the proven /api/bindings + /api/automation endpoints.
const ACT_LABELS = {on:i18n('On'), off:i18n('Off'), toggle:i18n('Toggle'),
                    bright_up:i18n('Brighter'), bright_down:i18n('Darker'), press:i18n('Press')};
const ACTIONS = [['on',i18n('On')],['off',i18n('Off')],['toggle',i18n('Toggle')],
                 ['bright_up',i18n('Brighter')],['bright_down',i18n('Darker')]];
const COND_OPS = [['<','<'],['>','>'],['<=','\u2264'],['>=','\u2265'],
                  ['==','='],['!=','\u2260'],['on',i18n('on')],['off',i18n('off')]];
const DOW = [i18n('Mon'),i18n('Tue'),i18n('Wed'),i18n('Thu'),i18n('Fri'),i18n('Sat'),i18n('Sun')];
const GESTURE_WORDS = {single: 'press', true: 'triggered', false: 'cleared', below: 'below threshold', above: 'above threshold'};
// human title of a rule, composed here so it follows the UI language (the server sends structure)
function ruleTitle(r){
  const w = (r.when && r.when[0]) || {}, t = (r.then && r.then[0]) || {};
  if(r.kind === 'binding'){
    const g = whenGesture(w.gesture, w.device, w.type === 'sensor' ? w.code : '');
    const wc = w.type === 'sensor' ? ((BY_ID[w.device] || {}).controls || []).find(x => x.code === w.code) : null;
    const who = w.device_name + (wc && wc.label ? ' \u00b7 ' + wc.label : '');
    if(t.type && t.type !== 'device'){
      const pa = HomePlugins.ruleAction(t.type);
      return `${who}${g.startsWith('=') ? ' ' : ': '}${g} \u2192 ${(pa && pa.title && HomePlugins.safe(pa.title, t)) || pluginThenTitle(t)}`;
    }
    return `${who}${g.startsWith('=') ? ' ' : ': '}${g} \u2192 ${t.device_name} ${ACT_LABELS[t.action] || t.action}` + (t.for ? ` ${i18n('for')} ${durWords(t.for)}` : '');
  }
  if(r.kind === 'schedule'){
    const tm = (w.times && w.times[0]) || {}, days = tm.days || [];
    const dl = days.length === 7 ? '' : ' (' + days.map(d => DOW[d - 1]).join(',') + ')';
    return `${t.device_name}: ${i18n(t.action === 'on' ? 'on' : 'off')} ${i18n('at')} ${tm.time}${dl}`;
  }
  if(r.kind === 'inching') return `${t.device_name}: ${i18n('auto-off')} ${t.after}${i18n('s')} ${i18n('after turning on')}`;
  return r.title || r.id;
}
let RULES = [];

function buttonSources(){ return DEVICES.filter(d => controlsOf(d).trig_txt.length); }
function sensorSources(){ return DEVICES.filter(d => sensorBindControls(d, controlsOf(d)).length); }
function condDevices(){ return DEVICES.filter(d => { const C=controlsOf(d);
  return C.sensors.length || C.switches.length; }); }

// Chips are built from the rule's structure in the UI language; the server's English
// `label` is only the last resort for a shape this page does not know.
// a device category as a word of the UI language (the id itself is data: switch, sensor, ...)
// readings: a unit by channel code when the device names none, and no more than one decimal on a card
const UNIT_BY_CODE = {temperature:'\u00b0C', humidity:'%', battery:'%', power:'W', current:'A', voltage:'V', energy:'kWh',
                      distance:'cm', illuminance:'lx', linkquality:'lqi', pressure:'hPa', co2:'ppm', voc:'ppb', pm25:'\u00b5g/m\u00b3'};
// an ESP node's own health channels: under Details, never on the card or among the readings
const DIAG_CODE = /^(wifi_signal|rssi|uptime|ip_address|ssid|bssid|mac_address|free_heap|reset_reason|esphome_version|connected_ssid|link_quality|linkquality)$/;
// the readings a card keeps: the purpose of the device - one, or temperature with humidity; the rest is in its window
const KEY_READ = ['presence', 'occupancy', 'water_leak', 'contact', 'smoke', 'temperature', 'humidity', 'co2', 'illuminance', 'pressure'];
function keyReadings(d, sensors){
  const list = sensors.filter(x => !isDiag(d, x) && x.code !== 'battery');
  const rank = x => { const i = KEY_READ.indexOf(x.code); return i < 0 ? 99 : i; };
  list.sort((a, b) => rank(a) - rank(b));
  const pair = list.length > 1 && list[0].code === 'temperature' && list[1].code === 'humidity';
  return list.slice(0, pair ? 2 : 1);
}
function isDiag(d, s){ return !!(s.advanced || DIAG_CODE.test(s.code || '') || (s.code === 'button' && d && (d.controls || []).some(c => c.code === 'wifi_signal' || c.code === 'uptime'))); }   // an ESP node's boot button
// a rule's time in words: whole minutes when it is minutes, else seconds
function durWords(sec){
  const n = Number(sec) || 0;
  return n >= 60 && n % 60 === 0 ? i18n('{0} min', n / 60) : n >= 60 ? i18n('{0} min', Math.round(n / 6) / 10) : i18n('{0} s', n);
}
function fmtUptime(sec){
  const m = Math.floor(Number(sec) / 60), h = Math.floor(m / 60), dd = Math.floor(h / 24);
  return dd ? i18n('{0} d {1} h', dd, h % 24) : h ? i18n('{0} h {1} min', h, m % 60) : i18n('{0} min', m);
}
function unitOf(s){ if(s.code === 'uptime') return ''; const u = i18n(s.unit === 'C' ? '\u00b0C' : s.unit || UNIT_BY_CODE[String(s.code || '').toLowerCase()] || ''); return u && !/^[%\u00b0]/.test(u) ? '\u202f' + u : u; }
// a yes/no reading in the words of its sensor: [when true, when false] (a contact is true when the door is closed)
const BOOL_WORDS = {contact: ['closed', 'open'], occupancy: ['someone', 'nobody'], presence: ['someone', 'nobody'],
  water_leak: ['leak', 'dry'], smoke: ['smoke', 'clear'], gas: ['gas', 'clear'], carbon_monoxide: ['gas', 'clear'],
  vibration: ['shaking', 'still'], tamper: ['opened', 'whole'], battery_low: ['low', 'fine']};
function fmtReading(val, dev, code){
  if(val == null) return '\u2014';
  const climate = YDisp.reading(code, val, dev + '/' + code);       // a temperature or a humidity: as every screen shows it
  if(climate) return climate;
  if(typeof val === 'boolean'){ const w = BOOL_WORDS[code]; return i18n(w ? w[val ? 0 : 1] : val ? 'yes' : 'no'); }
  const n = typeof val === 'number' ? val : (typeof val === 'string' && val.trim() !== '' && isFinite(+val) ? +val : null);
  if(n === null) return typeof val === 'string' ? i18n(val) : String(val);
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
}
function roomNameOf(id){ const r = ROOMS.find(x => (x.devices || []).includes(id)); return r ? r.name : ''; }
// the devices a room serves (its tab, family rights): the server's `serves`, else those standing in it
const roomServes = r => r.serves || r.devices || [];
function servedRoomOf(id){ const r = ROOMS.find(x => roomServes(x).includes(id)); return r ? r.name : ''; }
// a channel label a user left as "-" or a "nothing" word is no name: the channel is numbered instead
const NO_NAME = /^[\s\-\u2013\u2014_.\u00b7]*$|^(\u043d\u0438\u0447\u0435\u0433\u043e|\u043d\u0435\u0442|none|no|empty|\u043f\u0443\u0441\u0442\u043e)$/i;   // i18n: the words users type for "no name"
function chanLabel(s, i, n){ return (s.label && !NO_NAME.test(s.label)) ? s.label : (n > 1 ? String(i + 1) : ''); }
// a device whose category is not one of the dashboard's: its kind by its channels
const KNOWN_CATS = new Set(['switch', 'sensor', 'button', 'indicator', 'other', 'climate', 'tv', 'media', 'ir', 'camera']);
function effCat(d){
  const cat = d.category || '';
  if(KNOWN_CATS.has(cat) || TABS.concat(HomePlugins.tabs).some(t => (t.cats || []).includes(cat))) return cat;
  const cs = d.controls || [];
  if(cs.some(c => c.kind === 'switch' || c.kind === 'bright')) return 'switch';
  if(cs.some(c => c.kind === 'trigger_text')) return 'button';
  if(cs.some(c => c.kind === 'sensor' || c.kind === 'sensor_text')) return 'sensor';
  return cat;
}
const CAT_EN = {switch: 'switch', sensor: 'sensor', button: 'button', indicator: 'indicator light', other: 'device', climate: 'air conditioner', tv: 'TV',
                media: 'media player', ir: 'IR remote', camera: 'camera'};      // English has no table of its own (and restricted mode drops the tables)
function catLabel(c){ if(!c) return ''; const t = i18n('category:' + c); return t.startsWith('category:') ? CAT_EN[c] || i18n('device') : t; }
// a yes/no trigger of a channel in its words: the channel's own labels, else BOOL_WORDS ("became open"), else on/off
function boolGesture(g, dev, code){
  if(g !== 'true' && g !== 'false') return '';
  const ctl = ((BY_ID[dev] || {}).controls || []).find(x => x.code === code) || {}, w = BOOL_WORDS[code];
  const word = (ctl.labels || {})[g] || i18n(w ? w[g === 'true' ? 0 : 1] : g === 'true' ? 'on' : 'off');
  return i18n('became {0}', word);
}
function whenGesture(g, dev, code){
  if(dev && isNfc(dev)) return g;                 // a tag's name is its own word, never a gesture's ("lock" stays "lock")
  if(code && boolGesture(g, dev, code)) return boolGesture(g, dev, code);
  if(GESTURE_LABELS[g]) return GESTURE_LABELS[g];
  if(GESTURE_WORDS[g]) return i18n(GESTURE_WORDS[g]);
  return gestureLabel(g);
}
function whenChip(w){
  if(w.type === 'button') return `${escapeHtml(w.device_name)}: ${escapeHtml(whenGesture(w.gesture, w.device))}`;
  if(w.type === 'sensor'){
    const g = whenGesture(w.gesture, w.device, w.code);
    const ctl = ((BY_ID[w.device] || {}).controls || []).find(x => x.code === w.code);
    const chan = ctl && ctl.label ? ` \u00b7 ${ctl.label}` : '';
    const t = (w.threshold!=null) ? ` ${g} ${w.threshold}` : ` ${g}`;
    return `${escapeHtml(w.device_name + chan)}${escapeHtml(t)}`;
  }
  if(w.type === 'time'){
    const tm = (w.times && w.times[0]) || {}, days = tm.days || [];
    const dl = days.length === 7 ? '' : ' (' + days.map(d => DOW[d - 1]).join(',') + ')';
    return `${icon('clock', 13)} ${escapeHtml((tm.time || w.label || '') + dl)}`;
  }
  if(w.type === 'state') return `${escapeHtml(w.device_name)} ${escapeHtml(i18n(w.label || ''))}`;
  return escapeHtml(i18n(w.label || ''));
}
function condChip(c){
  const op = (COND_OPS.find(o=>o[0]===c.op)||[])[1] || c.op;
  const dn = (BY_ID[c.device]||{}).name || c.device;
  return (c.op==='on'||c.op==='off') ? `${escapeHtml(dn)} ${escapeHtml(op)}`
                                     : `${escapeHtml(dn)} ${escapeHtml(op)} ${escapeHtml(String(c.value))}`;
}
function thenChip(t){
  if(t.type && t.type !== 'device'){          // a plug-in's action (its plug-in may be absent)
    const pa = HomePlugins.ruleAction(t.type);
    const c = pa && pa.chip ? HomePlugins.safe(pa.chip, t) : null;
    return typeof c === 'string' ? c : escapeHtml(pluginThenTitle(t));
  }
  let act = t.after!=null ? `${i18n('off after')} ${t.after}${i18n('s')}`
          : (ACT_LABELS[t.action]||t.action);
  if(t.for) act += ` ${i18n('for')} ${durWords(t.for)}`;
  return `${escapeHtml(t.device_name)} ${escapeHtml(act)}`;
}
function ruleFlowHtml(r){
  let h = r.when.map(w=>`<span class="rule-chip when">${whenChip(w)}</span>`).join('');
  for(const c of (r.conditions||[]))
    h += `<span class="rule-arrow">\u00b7</span><span class="rule-chip cond">${condChip(c)}</span>`;
  if(r.time_window){
    const tw = r.time_window;
    h += `<span class="rule-arrow">\u00b7</span><span class="rule-chip cond">${icon('clock', 13)} ${
      escapeHtml(tw.from)}\u2013${escapeHtml(tw.to)}</span>`;
  }
  h += `<span class="rule-arrow">\u2192</span>` +
       r.then.map(t=>`<span class="rule-chip then">${thenChip(t)}</span>`).join('');
  return h;
}

// the rules where they show: the main tab, Settings -> Automations, or both
const RULE_WATCH = new Set();                              // a page that shows rules of its own (NFC tags): redrawn with them
function rulesRedraw(){
  const sc = ST.page === 'rules' && document.querySelector('#stx:not([hidden]) .stx-body > section');
  if(sc) renderAutomations(sc, true);
  if(activeTab === 'auto') renderAutomations($('#app'), true);
  RULE_WATCH.forEach(f => { try { f(); } catch(e){ console.warn(e); } });
}
async function renderAutomations(app, keepScroll){
  const y = window.scrollY;
  if(!keepScroll) app.innerHTML = '<div class="skeleton">' + i18n('Loading\u2026') + '</div>';
  try { RULES = await api('/api/rules'); } catch(e){ RULES = []; }
  if(app.id === 'app' && activeTab !== 'auto') return;   // switched away while loading: do not draw into another tab (Settings draws its own)
  if(app.id === 'app') renderTabs();                      // the tab badge counts rules: show the fresh number now
  const wrap = el('div','auto-wrap');
  const tb = el('div','auto-toolbar');
  tb.innerHTML = `<div style="color:var(--dim);font-size:13px">${RULES.length} ${
    i18nPlural('rule', RULES.length)}</div>
    <div style="display:flex;gap:8px">
    ${HomePlugins.toolbarButtons('auto').map((b, i) => `<button class="addbtn" style="width:auto;margin:0;padding:10px 16px;background:var(--card)" id="rb-x${i}" data-tb="${escapeHtml(b.label)}">${escapeHtml(i18n(b.label))}</button>`).join('')}
    <button class="addbtn" style="width:auto;margin:0;padding:10px 16px" id="rb-new">${i18n('+ New rule')}</button></div>`;
  wrap.append(tb);
  if(!RULES.length){
    wrap.append(el('div','auto-empty',
      i18n('No automations yet.') + '<br>' + i18n('Press "+ New rule" to create the first one:') + '<br>'+
      '<i>' + i18n('when something happened \u2192 (if a condition holds) \u2192 do an action.') + '</i>'));
  } else {
    for(const r of RULES) wrap.append(ruleCard(r));
  }
  app.innerHTML = ''; app.append(wrap);
  // inside this copy: the main view may show the same page with the same ids
  wrap.querySelector('#rb-new').onclick = ()=> openRuleBuilder(null);
  HomePlugins.toolbarButtons('auto').forEach((b, i) => { wrap.querySelector('#rb-x' + i).onclick = () => HomePlugins.safe(b.onClick); });
  if(keepScroll) window.scrollTo(0, y);   // enable/delete redraws in place, no jump to the top
}


function ruleCard(r){
  const c = el('div','rule-card'+(r.enabled?'':' off'));
  c.innerHTML = `
    <div class="rule-main">
      <div class="rule-title"><span class="rule-kind">${
        {binding:i18n('trigger'),schedule:i18n('schedule'),inching:i18n('auto-off')}[r.kind]||''}</span> ${escapeHtml(ruleTitle(r))}</div>
      <div class="rule-flow">${ruleFlowHtml(r)}</div>
    </div>
    <div class="rule-actions">
      <div class="rule-actions-top">
        <button class="rule-run" title="${i18n('Run the action now')}" aria-label="${i18n('Run the action now')}">${icon('play', 18)}</button>
        <div class="toggle setting ${r.enabled?'on':''}" title="${i18n('enable/disable the rule')}"></div>
        <button class="rule-del" title="${i18n('Delete the rule')}" aria-label="${i18n('Delete the rule')}">${icon('trash', 18)}</button>
      </div>
    </div>`;
  c.querySelector('.rule-main').onclick = ()=> openRuleBuilder(r);
  c.querySelector('.rule-run').onclick = async (e)=>{
    e.stopPropagation();
    const btn = e.currentTarget;
    btn.disabled = true; const prev = btn.innerHTML; btn.textContent = '\u2026';
    try{
      await api('/api/rules/run', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:r.id})});
      btn.innerHTML = icon('check', 18);
      setTimeout(()=>{ btn.innerHTML = prev; btn.disabled = false; }, 900);
    }catch(err){
      btn.innerHTML = prev; btn.disabled = false;
      alert(i18n('Error: ')+err.message);
    }
  };
  c.querySelector('.toggle').onclick = async (e)=>{
    e.stopPropagation();
    const on = !c.querySelector('.toggle').classList.contains('on');
    try{
      await api('/api/rules/enable', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id:r.id, enabled:on})});
      rulesRedraw();
    }catch(err){ alert(i18n('Error: ')+err.message); }
  };
  c.querySelector('.rule-del').onclick = async (e)=>{
    e.stopPropagation();
    if(!confirm(i18n('Delete the rule?\n')+ruleTitle(r))) return;
    try{
      await api('/api/rules/delete', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:r.id})});
      rulesRedraw();
    }catch(err){ alert(i18n('Error: ')+err.message); }
  };
  return c;
}

// ---------- Rule builder (WHEN -> IF -> THEN) ----------
function optsHtml(pairs, sel){
  return pairs.map(([v,l])=>`<option value="${escapeHtml(String(v))}"${
    String(v)===String(sel)?' selected':''}>${escapeHtml(l)}</option>`).join('');
}
function devOpts(list, sel){
  return (sel ? '' : `<option value="">${i18n('\u2014 choose \u2014')}</option>`) + optsHtml(list.map(d=>[d.id, d.name]), sel);
}
function switchTargetOpts(sel){ return devOpts(switchTargetsList(), sel); }
function codeOptsFor(devId, kinds, sel){
  const d = BY_ID[devId]; if(!d) return '';
  const cs = (d.controls||[]).filter(c=>kinds.includes(c.kind));
  const name = (c, i) => c.label || (cs.length === 1 ? i18n('Power') : (c.kind === 'switch' ? String(i + 1) : c.code));
  return optsHtml(cs.map((c, i)=>[c.code, name(c, i)]), sel);
}
function daysPickerHtml(sel){
  const s = sel || [1,2,3,4,5,6,7];
  return `<div class="rb-days">`+DOW.map((n,i)=>
    `<button type="button" class="rb-day${s.includes(i+1)?' on':''}" data-d="${i+1}">${n}</button>`
  ).join('')+`</div>`;
}

const nfcDevice = () => DEVICES.find(d => d.transport === 'nfc');
const isNfc = id => ((BY_ID[id] || {}).transport) === 'nfc';
function openRuleBuilder(rule, preset){
  const t0 = rule && rule.then && rule.then[0];
  const pa0 = t0 && HomePlugins.ruleAction(t0.type);
  if(t0 && t0.type && t0.type !== 'device' && (!pa0 || (pa0.visible && !HomePlugins.safe(pa0.visible)))){
    // no editor of its plug-in here: editing would turn it into a device rule
    alert(i18n(pa0 ? 'The plug-in of this rule does not offer editing it now; it can be enabled, disabled or deleted.'
                   : 'This rule belongs to a plug-in that is not loaded here; it can be enabled, disabled or deleted, not edited.'));
    return;
  }
  releaseModal();
  openModalId = '__rule_builder__';
  const edit = !!rule;
  // derive initial trigger type from an existing rule
  let ttype = 'button';
  if(edit){
    const w = rule.when[0];
    // a tag's tap first: its code is "card", not "action", so the server calls it a sensor rule
    ttype = isNfc(w.device) ? 'nfc' : w.type === 'sensor' ? 'sensor' : w.type === 'time' ? 'time'
          : w.type === 'state' ? 'deviceon' : 'button';
  } else if(preset && preset.nfc && nfcDevice()) ttype = 'nfc';
  const md = document.getElementById('md');
  md.innerHTML = `
    <div class="head">
      <button class="closebtn" onclick="closeModal()">\u00d7</button>
      <h2>${i18n(edit ? 'Edit the automation' : 'New automation')}</h2>
    </div>
    <div class="body">
      <div class="rb-step"><h4>${i18n('When')}</h4>
        <div class="rb-row"><label>${i18n('Event')}</label>
          <select id="rb-ttype">
            <option value="button">${i18n('Button pressed')}</option>
            ${nfcDevice() ? `<option value="nfc">${i18n('NFC tag tapped')}</option>` : ''}
            <option value="sensor">${i18n('Sensor triggered')}</option>
            <option value="time">${i18n('Time reached')}</option>
            <option value="deviceon">${i18n('Device turned on')}</option>
          </select></div>
        <div id="rb-tfields"></div>
      </div>
      <div class="rb-step" id="rb-condstep"><h4>${i18n('If (optional)')}</h4>
        <div id="rb-conds"></div>
        <button type="button" id="rb-addcond" class="rb-day" style="width:auto;padding:6px 12px">${i18n('+ condition')}</button>
        <div id="rb-twin" style="margin-top:12px"></div>
      </div>
      <div class="rb-step"><h4>${i18n('Then')}</h4><div id="rb-afields"></div></div>
      <div id="rb-msg" style="color:var(--bad);min-height:18px;margin:6px 2px"></div>
      <button class="addbtn" id="rb-save">${edit?i18n('Save'):i18n('Create')}</button>
    </div>`;
  document.getElementById('bd').classList.add('show');
  document.body.style.overflow = 'hidden';

  const tsel = document.getElementById('rb-ttype');
  tsel.value = ttype;
  tsel.onchange = ()=>{ rbTriggerFields(tsel.value, null); rbActionFields(tsel.value, null);
                        rbToggleCond(tsel.value); };
  rbTriggerFields(ttype, edit ? rule : (preset && preset.nfc && nfcDevice() ? {when: [{device: nfcDevice().id, code: 'card', gesture: preset.nfc}]} : null));
  rbActionFields(ttype, edit?rule:null);
  rbToggleCond(ttype);
  rbRenderConds(edit ? (rule.conditions||[]) : []);
  rbRenderTimeWindow(edit ? rule.time_window : null);
  document.getElementById('rb-addcond').onclick = ()=> rbAddCondRow({});
  document.getElementById('rb-save').onclick = ()=> rbSave(edit ? rule : null);
  if(edit && rbUnshowable(rule)){
    document.getElementById('rb-msg').textContent = i18n('The editor cannot show all of this rule (a gesture, action or device it does not offer), so saving it would change it; it can be enabled, disabled or deleted.');
    document.getElementById('rb-save').disabled = true;
  }
}

// a stored value the lists do not offer would be saved as their first option (and the old
// rule deleted): compare every select of the freshly built editor with the rule
function rbUnshowable(rule){
  const w = rule.when[0] || {}, t = rule.then[0] || {};
  const want = [];   // [select id, stored value]
  if(w.type === 'button') want.push(['rb-bdev', w.device], ['rb-bgest', `${w.code}|${w.gesture}`]);
  else if(w.type === 'sensor') want.push(['rb-sdev', w.device], ['rb-scode', w.code], ['rb-sedge', w.gesture]);
  else if(w.type === 'state') want.push(['rb-odev', t.device], ['rb-ocode', t.code]);
  const kind = document.getElementById('rb-akind');
  if(w.type !== 'state' && (!kind || kind.value === 'device'))
    want.push(['rb-atgt', t.device], ['rb-acode', t.code], ['rb-aact', t.action]);
  const val = id => { const e = document.getElementById(id); return e ? e.value : undefined; };
  if(want.some(([id, v]) => val(id) !== String(v))) return true;
  const rows = [...document.querySelectorAll('#rb-conds .rb-cond-row')];
  return (rule.conditions || []).some((c, i) => !rows[i]
    || rows[i].querySelector('.rbc-dev').value !== String(c.device)
    || rows[i].querySelector('.rbc-code').value !== String(c.code)
    || rows[i].querySelector('.rbc-op').value !== String(c.op || 'on'));
}

function rbToggleCond(type){
  document.getElementById('rb-condstep').style.display =
    (type==='button'||type==='nfc'||type==='sensor') ? '' : 'none';
}

function rbTriggerFields(type, rule){
  const box = document.getElementById('rb-tfields');
  const w = rule ? rule.when[0] : null;
  if(type==='button' || type==='nfc'){                       // a tag's tap is the nfc device's "card" gesture: the same rule
    const nfc = type === 'nfc', srcs = nfc ? [nfcDevice()].filter(Boolean) : buttonSources().filter(d => !isNfc(d.id));
    box.innerHTML = `<div class="rb-row"><label>${i18n(nfc ? 'Tag' : 'Button')}</label>
        <select id="rb-bdev"${nfc ? ' hidden' : ''}>${devOpts(srcs, w ? w.device : (nfc && srcs[0] ? srcs[0].id : ''))}</select>
        <select id="rb-bgest"></select></div>`;
    const dsel = document.getElementById('rb-bdev');
    const rebuild = ()=>{
      const d = BY_ID[dsel.value]; const C = d?controlsOf(d):{trig_txt:[]};
      const opts = [];
      for(const t of C.trig_txt){
        const vals = t.values||[], single = vals.filter(v=>v==='single'||/_single$/.test(v));
        const gs = single.length ? single : vals;      // a button: its single presses; else every value (events, cards)
        for(const g of gs) opts.push([`${t.code}|${g}`, nfc ? g : gestureLabel(g)]);
      }
      document.getElementById('rb-bgest').innerHTML = optsHtml(opts,
        w ? `${w.code}|${w.gesture}` : '');
    };
    dsel.onchange = rebuild; rebuild();
  } else if(type==='sensor'){
    box.innerHTML = `<div class="rb-row"><label>${i18n('Sensor')}</label>
        <select id="rb-sdev">${devOpts(sensorSources(), w?w.device:'')}</select>
        <select id="rb-scode"></select></div>
      <div class="rb-row"><label>${i18n('When')}</label>
        <select id="rb-sedge"></select>
        <input id="rb-sthr" type="number" step="any" placeholder="${i18n('limit')}" style="width:90px;display:none"></div>
      <div id="rb-shyst-box" style="display:none">${advancedHTML(`<div class="rb-row"><label>${i18n('Margin')}</label>
        <input id="rb-shyst" type="number" step="any" min="0" style="width:80px"></div>
        <div class="bindcaption">${i18n('How far back past the limit the reading must go before the rule can fire again; empty: the house picks it.')}</div>`)}</div>`;
    const dsel = document.getElementById('rb-sdev');
    const rebuildCode = ()=>{
      const d = BY_ID[dsel.value]; const C = d?controlsOf(d):{};
      const ss = d ? sensorBindControls(d, C) : [];
      document.getElementById('rb-scode').innerHTML =
        optsHtml(ss.map(s=>[s.code, s.label||s.code]), w?w.code:'');
      rebuildEdge();
    };
    const rebuildEdge = ()=>{
      const d = BY_ID[dsel.value]; const C = d?controlsOf(d):{};
      const code = document.getElementById('rb-scode').value;
      const s = (d?sensorBindControls(d,C):[]).find(x=>x.code===code) || {};
      const numeric = s.vtype === 'num';
      const edges = numeric ? [['below',i18n('went below')],['above',i18n('went above')]]
                            : [['true',i18n('triggered')],['false',i18n('cleared')]];
      document.getElementById('rb-sedge').innerHTML = optsHtml(edges, w?w.gesture:'');
      const showThr = numeric;
      document.getElementById('rb-sthr').style.display = showThr?'':'none';
      document.getElementById('rb-shyst-box').style.display = showThr?'':'none';
      if(w && w.threshold!=null) document.getElementById('rb-sthr').value = w.threshold;
      if(w && w.hyst!=null) document.getElementById('rb-shyst').value = w.hyst;
    };
    dsel.onchange = rebuildCode;
    document.getElementById('rb-scode').onchange = rebuildEdge;
    rebuildCode();
  } else if(type==='time'){
    const times = (w&&w.times&&w.times[0]) ? w.times[0] : {time:'08:00', days:[1,2,3,4,5,6,7]};
    box.innerHTML = `<div class="rb-row"><label>${i18n('Time')}</label>
        <input id="rb-time" type="text" inputmode="numeric" pattern="^([01][0-9]|2[0-3]):[0-5][0-9]$" placeholder="HH:MM" maxlength="5" style="width:84px" value="${times.time||'08:00'}"></div>
      <div class="rb-row"><label>${i18n('Days')}</label>${daysPickerHtml(times.days)}</div>`;
    box.querySelectorAll('.rb-day').forEach(b=> b.onclick=()=>b.classList.toggle('on'));
  } else if(type==='deviceon'){
    const dev = rule ? rule.then[0].device : '';
    const code = rule ? rule.then[0].code : '';
    box.innerHTML = `<div class="rb-row"><label>${i18n('Device')}</label>
        <select id="rb-odev">${switchTargetOpts(dev)}</select>
        <select id="rb-ocode">${codeOptsFor(dev||switchTargetsList()[0]?.id, ['switch'], code)}</select></div>
      <div class="bindcaption">${i18n('fires whenever this channel turns on')}</div>`;
    const dsel = document.getElementById('rb-odev');
    dsel.onchange = ()=>{ document.getElementById('rb-ocode').innerHTML =
        codeOptsFor(dsel.value, ['switch'], ''); };
  }
}

function rbActionFields(type, rule){
  const box = document.getElementById('rb-afields');
  if(type==='deviceon'){
    const after = rule ? (rule.then[0].after||5) : 5;
    box.innerHTML = `<div class="rb-row"><label>${i18n('Auto-off after')}</label>
        <input id="rb-after" type="number" min="1" value="${after}" style="width:90px"> ${i18n('sec')}</div>`;
    return;
  }
  const t = rule ? rule.then[0] : null;
  const acts = (type==='time') ? [['on',i18n('On')],['off',i18n('Off')]] : ACTIONS;
  const forHtml = (type==='time') ? '' : `
    <div class="rb-row"><label>${i18n('Duration')}</label>
      <input id="rb-for" type="number" min="0" step="any" placeholder="${i18n('empty: stays so')}"
             style="width:130px" value="${t&&t.for?+(t.for / 60).toFixed(3):''}"> ${i18n('min')}
      <select id="rb-retrig" style="display:none" title="${i18n('When it is triggered again meanwhile')}">
        <option value="restart">${i18n('start again')}</option>
        <option value="extend">${i18n('extend')}</option>
        <option value="ignore">${i18n('leave as is')}</option>
      </select></div>
    <div class="bindcaption" id="rb-forcap" style="display:none">
      ${i18n('It goes back after this time. Triggered again meanwhile: the time starts again, gets longer, or is left as is.')}</div>`;
  // plug-in targets (a phone to ring, a greeting ...) instead of a device, for button/sensor rules
  const pacts = (type==='button'||type==='nfc'||type==='sensor')
    ? HomePlugins.ruleActions.filter(a => !a.visible || HomePlugins.safe(a.visible)) : [];
  const cur = t && pacts.find(a => a.type === t.type);
  const kindHtml = pacts.length ? `<div class="rb-row"><label>${i18n('Target')}</label>
      <select id="rb-akind"><option value="device">${i18n('Device')}</option>
        ${pacts.map(a => `<option value="${escapeHtml(a.type)}">${escapeHtml(i18n(a.label))}</option>`).join('')}</select></div>` : '';
  const plugHtml = pacts.map(a => `<div class="rb-apl" data-type="${escapeHtml(a.type)}" style="display:none">${
      HomePlugins.safe(a.html, cur === a ? t : null) || ''}</div>`).join('');
  box.innerHTML = kindHtml + `<div id="rb-adev">
      <div class="rb-row"><label>${i18n('Device')}</label>
        <select id="rb-atgt">${switchTargetOpts(t?t.device:'')}</select>
        <select id="rb-acode"></select></div>
      <div class="rb-row"><label>${i18n('Action')}</label>
        <select id="rb-aact">${optsHtml(acts, t?t.action:'')}</select></div>${forHtml}
    </div>` + plugHtml;
  if(pacts.length){
    const kind = document.getElementById('rb-akind');
    kind.value = cur ? cur.type : 'device';
    const toggle = ()=>{
      box.querySelectorAll('.rb-apl').forEach(d => { d.style.display = d.dataset.type === kind.value ? '' : 'none'; });
      document.getElementById('rb-adev').style.display = kind.value === 'device' ? '' : 'none'; };
    kind.onchange = toggle; toggle();
  }
  const tsel = document.getElementById('rb-atgt');
  const csel = ()=> document.getElementById('rb-acode');
  const syncActs = ()=>{
    const aact = document.getElementById('rb-aact');
    const wanted = (type==='time') ? [['on',i18n('On')],['off',i18n('Off')]] : ACTIONS;
    const cur = aact.value;
    aact.innerHTML = optsHtml(wanted, wanted.some(w=>w[0]===cur) ? cur
                                    : (t ? t.action : ''));
    const forRow = document.getElementById('rb-for');
    if(forRow) forRow.parentElement.style.display =
      (aact.value==='on'||aact.value==='off') ? '' : 'none';
  };
  const rebuild = ()=>{ csel().innerHTML =
      codeOptsFor(tsel.value, type==='time' ? ['switch'] : ['switch','bright'], t?t.code:'');
    syncActs(); };
  tsel.onchange = rebuild; rebuild();
  csel().addEventListener('change', syncActs);
  if(type!=='time'){
    const fin = document.getElementById('rb-for');
    const rtg = document.getElementById('rb-retrig');
    const cap = document.getElementById('rb-forcap');
    const aact = document.getElementById('rb-aact');
    if(rule && rule.retrigger) rtg.value = rule.retrigger;
    const sync = ()=>{ const on = +fin.value > 0;
      rtg.style.display = on?'':'none'; cap.style.display = on?'':'none';
      const okAct = aact.value==='on'||aact.value==='off';
      fin.parentElement.style.display = okAct?'':'none'; };
    fin.oninput = sync; aact.onchange = sync; sync();
  }

}

function rbRenderConds(conds){
  document.getElementById('rb-conds').innerHTML = '';
  (conds||[]).forEach(c=> rbAddCondRow(c));
}
function rbAddCondRow(c){
  const box = document.getElementById('rb-conds');
  const row = el('div','rb-cond-row');
  row.innerHTML = `
    <select class="rbc-dev">${devOpts(condDevices(), c.device||'')}</select>
    <select class="rbc-code"></select>
    <select class="rbc-op">${optsHtml(COND_OPS, c.op||'on')}</select>
    <input class="rbc-val" type="number" step="any" placeholder="${i18n('value')}" style="width:80px" value="${c.value!=null?escapeHtml(String(c.value)):''}">
    <button type="button" class="rb-del">\u00d7</button>`;
  box.append(row);
  const dsel = row.querySelector('.rbc-dev');
  const rebuild = ()=>{ row.querySelector('.rbc-code').innerHTML =
      codeOptsFor(dsel.value, ['sensor','switch'], c.code||''); };
  dsel.onchange = rebuild; rebuild();
  const opsel = row.querySelector('.rbc-op');
  const valToggle = ()=>{ row.querySelector('.rbc-val').style.display =
      (opsel.value==='on'||opsel.value==='off')?'none':''; };
  opsel.onchange = valToggle; valToggle();
  row.querySelector('.rb-del').onclick = ()=> row.remove();
}
function rbReadConds(){
  const out = [];
  document.querySelectorAll('#rb-conds .rb-cond-row').forEach(row=>{
    const device = row.querySelector('.rbc-dev').value;
    const code = row.querySelector('.rbc-code').value;
    const op = row.querySelector('.rbc-op').value;
    if(!device||!code) return;
    const cond = {device, code, op};
    if(op!=='on'&&op!=='off'){ const v = row.querySelector('.rbc-val').value;
      cond.value = v===''?null:(isNaN(+v)?v:+v); }
    out.push(cond);
  });
  return out;
}
function rbRenderTimeWindow(tw){
  const box = document.getElementById('rb-twin');
  if(!box) return;
  const on = !!tw;
  box.innerHTML = `
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
      <input type="checkbox" id="rb-tw-on" ${on?'checked':''}> ${i18n('only within the time window')}</label>
    <div id="rb-tw-fields" style="display:${on?'':'none'};margin-top:8px">
      <div class="rb-row"><label>${i18n('From')}</label>
        <input id="rb-tw-from" type="text" inputmode="numeric" pattern="^([01][0-9]|2[0-3]):[0-5][0-9]$" placeholder="HH:MM" maxlength="5" style="width:84px" value="${tw&&tw.from?tw.from:'22:00'}">
        <label style="margin:0 8px">${i18n('to')}</label>
        <input id="rb-tw-to" type="text" inputmode="numeric" pattern="^([01][0-9]|2[0-3]):[0-5][0-9]$" placeholder="HH:MM" maxlength="5" style="width:84px" value="${tw&&tw.to?tw.to:'07:00'}"></div>
      <div class="rb-row"><label>${i18n('Days')}</label>${daysPickerHtml(tw?tw.days:null)}</div>
    </div>`;
  const chk = document.getElementById('rb-tw-on');
  const f = document.getElementById('rb-tw-fields');
  chk.onchange = ()=>{ f.style.display = chk.checked?'':'none'; };
  f.querySelectorAll('.rb-day').forEach(b=> b.onclick=()=>b.classList.toggle('on'));
}
function rbReadTimeWindow(){
  const chk = document.getElementById('rb-tw-on');
  if(!chk || !chk.checked) return null;
  const from = document.getElementById('rb-tw-from').value.trim();
  const to = document.getElementById('rb-tw-to').value.trim();
  if(!from || !to) return null;
  const hhmm = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
  if(!hhmm.test(from) || !hhmm.test(to)) throw new Error(i18n('the time as HH:MM, 24-hour'));
  const days = [...document.querySelectorAll('#rb-tw-fields .rb-day.on')].map(b=>+b.dataset.d);
  return {from, to, days: days.length?days:[1,2,3,4,5,6,7]};
}

// one rule per trigger: a write under the id of another rule would replace that rule
function rbCheckFree(newId, editRule){
  if((!editRule || editRule.id !== newId) && RULES.some(r => r.id === newId))
    throw new Error(i18n('a rule for this trigger already exists; edit that one'));
}

async function rbSave(editRule){
  const msg = document.getElementById('rb-msg'); msg.textContent='';
  const type = document.getElementById('rb-ttype').value;
  // An edit writes the new rule FIRST and removes the old one only after that
  // succeeded (and only if it is a different rule): a rejected form loses nothing.
  let newId = null;
  try{
    if(type==='button' || type==='nfc' || type==='sensor'){
      let body;
      const kind = document.getElementById('rb-akind');
      const pa = kind && kind.value !== 'device' ? HomePlugins.ruleAction(kind.value) : null;
      if(pa){
        body = Object.assign(pa.read(), {conditions: rbReadConds()});   // pa.read() throws on a bad form
      } else {
        const tgt = document.getElementById('rb-atgt').value;
        const tcode = document.getElementById('rb-acode').value;
        const action = document.getElementById('rb-aact').value;
        if(!tgt||!tcode) throw new Error(i18n('pick the target device'));
        body = {target:tgt, code:tcode, action, conditions: rbReadConds()};
        const forEl = document.getElementById('rb-for');
        const forv = forEl ? Math.round(+(forEl.value||0) * 60) : 0;     // minutes on the screen, seconds in the rule
        if(forv>0 && (action==='on'||action==='off')){
          body.for_sec = forv;
          body.retrigger = document.getElementById('rb-retrig').value;
        }
      }
      const tw = rbReadTimeWindow();
      if(tw) body.time_window = tw;
      let src, scode, gest;
      if(type==='button' || type==='nfc'){
        src = document.getElementById('rb-bdev').value;
        [scode, gest] = document.getElementById('rb-bgest').value.split('|');
      } else {
        src = document.getElementById('rb-sdev').value;
        scode = document.getElementById('rb-scode').value;
        gest = document.getElementById('rb-sedge').value;
        if(gest==='below'||gest==='above'){
          const thr = document.getElementById('rb-sthr').value;
          if(thr==='') throw new Error(i18n('enter a limit'));
          body.threshold = +thr;
          const h = document.getElementById('rb-shyst').value;
          if(h!=='') body.hyst = +h;
        }
      }
      if(!src||!gest) throw new Error(i18n('pick a trigger'));
      newId = `b|${src}|${scode}|${gest}`;
      rbCheckFree(newId, editRule);
      await api(`/api/bindings/${encodeURIComponent(src)}/${encodeURIComponent(scode)}/${encodeURIComponent(gest)}`,
        {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    } else if(type==='time'){
      const dev = document.getElementById('rb-atgt').value;
      const code = document.getElementById('rb-acode').value;
      const action = document.getElementById('rb-aact').value;
      const time = document.getElementById('rb-time').value.trim();
      if(!dev||!code||!time) throw new Error(i18n('fill in the device and the time'));
      if(!/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(time)) throw new Error(i18n('the time as HH:MM, 24-hour'));
      const days = [...document.querySelectorAll('#rb-tfields .rb-day.on')].map(b=>+b.dataset.d);
      if(!days.length) throw new Error(i18n('pick at least one day'));
      const cur = await api(`/api/automation/${encodeURIComponent(dev)}/${encodeURIComponent(code)}`); // no fallback: posting an empty list would wipe the channel's schedule
      // editing a row of this same channel: replace it in one write instead of delete + add
      const old = editRule && editRule.id.startsWith(`a|${dev}/${code}|sched|`) ? editRule.id.split('|') : null;
      const rest = (cur.schedule||[]).filter(r => !(old && r.time === old[3] && (r.action||'on') === old[4]));
      const sched = rest.concat([{time, days, action}]);
      newId = `a|${dev}/${code}|sched|${time}|${action}`;   // the row's own id: a clash is refused
      rbCheckFree(newId, editRule);
      await api(`/api/automation/${encodeURIComponent(dev)}/${encodeURIComponent(code)}`,
        {method:'POST', headers:{'Content-Type':'application/json'},
         body: JSON.stringify({inching_sec:cur.inching_sec||0, schedule:sched})});
    } else if(type==='deviceon'){
      const dev = document.getElementById('rb-odev').value;
      const code = document.getElementById('rb-ocode').value;
      const after = +document.getElementById('rb-after').value;
      if(!dev||!code||!(after>0)) throw new Error(i18n('fill in the device and the time'));
      newId = `a|${dev}/${code}|inch`;
      rbCheckFree(newId, editRule);
      const cur = await api(`/api/automation/${encodeURIComponent(dev)}/${encodeURIComponent(code)}`); // no fallback: posting an empty list would wipe the channel's schedule
      await api(`/api/automation/${encodeURIComponent(dev)}/${encodeURIComponent(code)}`,
        {method:'POST', headers:{'Content-Type':'application/json'},
         body: JSON.stringify({inching_sec:after, schedule:cur.schedule||[]})});
    }
    if(editRule && editRule.id !== newId){
      // a new id starts enabled: a rule that was off stays off
      if(editRule.enabled === false) await api('/api/rules/enable', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:newId, enabled:false})});
      await api('/api/rules/delete', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:editRule.id})});
    }
    closeModal();
    activeTab = 'auto'; rememberTab('auto');
    renderTabs(); render();
  }catch(e){ msg.textContent = i18n('Error: ')+e.message; }
}


