function card(d){
  const face = HomePlugins.cardFace(d);
  if(face){
    const f = el('div','card pface'); f.id = 'c_'+d.id; f._face = face;   // updateCards asks this face, not match()
    f.onclick = ()=> openModal(d.id);            // the face may replace it
    cardKeys(f, d);
    try{ face.render(f, d);
         const bar = f.querySelector('.topbar'), nm = f.querySelector('.name');   // a face gets the same badges
         if(bar) for(const b of transportBadges(d)) bar.insertBefore(b, bar.querySelector('.favstar'));
         else if(nm) nm.append(...transportBadges(d));
         return f; }
    catch(e){ console.warn('plug-in', e); }      // a fresh core card below: nothing of the face stays
  }
  const c=el('div','card'); c.id='c_'+d.id;
  const C = controlsOf(d);

  // top: name + the favourite star
  const top = el('div','topbar'); top.append(el('div','name', devIcon(d) + escapeHtml(d.name)), ...transportBadges(d), favStar(d)); c.append(top);

  // middle: info + sensors
  const info=el('div','info');
  const sens=el('div','sens');
  const readsOnCard = !C.switches.length && !C.brights.length;   // a switch's card is state + switch; its power figures are in its window
  for(const s of readsOnCard ? keyReadings(d, C.sensors) : []){
    sens.innerHTML += `<span id="sens_${escapeHtml(d.id)}_${escapeHtml(s.code)}">${escapeHtml(s.label||s.code)}: <b>\u2014</b>${escapeHtml(unitOf(s))}</span>`;
  }
  if(sens.childElementCount) info.append(sens);
  if(C.trig_txt.length) info.append(el('div', 'lastpress', ''));   // filled by updateCards: what was pressed last, and when
  // an unsupported device has no real controls -- say why instead of showing a
  // bare card the user cannot act on
  const where = roomNameOf(d.id);
  info.append(el('div','meta', d.unsupported
    ? i18n('the model was not recognised \u2014 nothing to control')
    : escapeHtml(where || catLabel(effCat(d)))));
  c.append(info);



  // bottom: switch dots with labels
  const row=el('div','switches'+(C.switches.length<=1?' center':'')+(C.switches.length > 2 && C.switches.length <= 4 ? ' many' : ''));
  // Fallback for a lone dimmable whose brightness can't be paired to its switch
  // by gang suffix -- it still gets the round swipe dot.
  const dimB = (C.switches.length===1 && C.brights.length===1) ? C.brights[0] : null;
  // Every switch paired with the brightness on its own gang renders as one group
  // row (oval 3-zone rocker + %). One channel or many -- same widget, so a single
  // dimmer looks and behaves exactly like a gang of a multi-channel one.
  const dimPairs = C.switches
    .filter(s => !s.card_toggle)
    .map(s => ({s, b: brightForCode(d, s.code)}))
    .filter(p => p.b);
  const grouped = dimPairs.length >= 1;
  const pairedSw = new Set(grouped ? dimPairs.map(p=>p.s.code) : []);
  const pairedBr = new Set(grouped ? dimPairs.map(p=>p.b.code) : []);
  if(C.switches.length){
    for(let i=0;i<C.switches.length;i++){
      const s=C.switches[i];
      if(s && s.card_toggle) continue;
      if(pairedSw.has(s.code)) continue;
      const wrap=el('div','dotwrap');
      const dot=el('div','dot');
      dot.id=`sw_${d.id}_${s.code}`;
      if(C.switches.length>1) dot.textContent = i+1;
      if(/lock/i.test(s.code) && (s.text_on || s.text_off)){ dot.dataset.lock = '1'; dot.innerHTML = icon('lock', 22); }   // a padlock, not a light's circle
      const lbl=el('div','dotlabel', escapeHtml(s.label && !NO_NAME.test(s.label) ? s.label : ''));   // an unnamed gang: the number on its button is enough
      lbl.id='swlbl_'+d.id+'_'+s.code;
      // caption = the state word: for the single unnamed toggle (On/Off)
      // and for any with text_on/text_off (a lock: Locked/Unlocked instead of a "Locked" caption under a dark dot)
      if((!chanLabel(s, i, C.switches.length) && C.switches.length===1) || s.text_on || s.text_off){
        lbl.dataset.state='1'; lbl.dataset.on = s.text_on && s.text_on !== 'On' ? i18n(s.text_on) : i18n('Switched on'); lbl.dataset.off = s.text_off && s.text_off !== 'Off' ? i18n(s.text_off) : i18n('Switched off');
      }
      wrap.append(dot, lbl);
      dotKeys(wrap, d, s, i===0 ? dimB : null);
      if(i===0 && dimB){
        const fill=el('div','dotfill'); fill.id=`dotfill_${d.id}_${dimB.code}`;
        dot.classList.add('dimdot'); dot.prepend(fill);
        const pc=el('div','dimpc'); pc.id=`dimpc_${d.id}_${dimB.code}`;
        wrap.append(pc);
        attachDimmerDot(dot, d, dimB, ()=> sendControl(d.id, s.code, !switchOn(d.id, s.code)));
      } else {
        wrap.onclick = ev=>{
          ev.stopPropagation();
          sendControl(d.id, s.code, !switchOn(d.id, s.code));
        };
      }
      row.append(wrap);
    }
  }
  if(row.childElementCount) c.append(row);   // a reading-only card has no dot row

  // sliders only for brightness controls not bound to a dimmer dot
  for(const cc of C.colors){ c.append(colorRow(d, cc)); }
  for(const s of C.switches.filter(x=>x.card_toggle)){
    const trow=el('div','brightrow'); trow.onclick=ev=>ev.stopPropagation();
    const lbl=el('span','brlbl'); lbl.style.cssText='font-size:12px;opacity:.75;flex:1'; lbl.textContent=s.label||s.code;
    const tg=el('div','toggle' + toggleGlyph(s.code)); tg.id=`sw_${d.id}_${s.code}`;
    if(isOn(d.id,s.code)) tg.classList.add('on');
    tg.onclick=ev=>{ ev.stopPropagation(); const nv=!switchOn(d.id,s.code); sendControl(d.id,s.code,nv); tg.classList.toggle('on',nv); autoVis(d); };
    trow.append(lbl,tg); c.append(trow);
  }
  for(const p of dimPairs){                  // one row per gang: dot, bar, %
    if(grouped) c.append(dimGroupRow(d, p.s, p.b));
  }
  for(const b of C.brights){
    if(b===dimB || pairedBr.has(b.code)) continue;
    const r=brightRow(d,b);
    if(b.when){ r.dataset.when=b.when; r.dataset.dev=d.id;
      r.style.display = ((b.when==="auto")===!!isOn(d.id,"auto")) ? "" : "none"; }
    c.append(r);
  }
  autoVis(d);

  // on-card enum: toggle "effects/static" + a dropdown to choose the effect
  for(const s of C.set_enum.filter(x=>x.on_card)){
    const OFFOPT = (s.off_option || 'Static');   // a device value, never translated
    const erow = el('div','enumrow');
    erow.style.cssText = 'display:flex;align-items:center;gap:10px;margin-top:8px';
    erow.onclick = ev=>ev.stopPropagation();
    const lbl = el('span','', escapeHtml(s.label||s.code));
    lbl.style.cssText = 'font-size:12px;opacity:.75;min-width:52px';
    const tgl = el('div','toggle');
    tgl.id = `cet_${d.id}_${s.code}`;
    const sel = document.createElement('select');
    sel.id = `ces_${d.id}_${s.code}`;
    sel.style.cssText = 'flex:1;background:#1b2330;color:#dfe7f3;border:1px solid #2a3547;border-radius:8px;padding:6px 8px;font-size:13px';
    for(const opt of (s.range||[]).filter(o=>o!==OFFOPT)){
      const o = document.createElement('option');
      o.value = opt; o.textContent = (s.labels||{})[opt] || opt;
      sel.append(o);
    }
    tgl.onclick = ev=>{
      ev.stopPropagation();
      const on = tgl.classList.contains('on');
      sendControl(d.id, s.code, on ? OFFOPT : sel.value);
    };
    sel.onchange = ev=>{ ev.stopPropagation(); sendControl(d.id, s.code, sel.value); };
    ['pointerdown','pointerup','click'].forEach(e=>sel.addEventListener(e, ev=>ev.stopPropagation()));
    erow.append(lbl, tgl, sel);
    c.append(erow);
  }

  // event flash dot (buttons and remotes)
  if(d.category === 'button' || d.controls.some(x=>x.kind==='trigger_text')){
    c.append(el('div','eventdot'));
  }

  c.onclick = ()=> openModal(d.id);
  cardKeys(c, d);
  return c;
}

function isOn(id, code){
  const st = STATE[id]; if(!st) return false;   // waiting or offline: its last value still says it
  return !!st.values?.[code];
}

function updateCards(){
  const hs = document.getElementById('homestrip'); if(hs) hs.innerHTML = homeStripHtml();
  for(const r of ROOMS){ const t = document.getElementById('rtemp_' + r.id); if(t){ const c = roomClimate(r); t.textContent = c ? c.value + '\u00b0' : ''; t.title = roomClimateTip(c); } }
  // fav-dots
  for(const f of FAVORITES){
    const dot = document.getElementById(`favdot_${f.device}_${f.code}`);
    if(!dot) continue;
    const st = STATE[f.device];
    const fc = document.getElementById(`fav_${f.device}_${f.code}`);
    const sw = document.getElementById(`favstate_${f.device}_${f.code}`);
    if(!st || !st.online){
      if(fc) fc.classList.add('offline');
      dot.classList.remove('on');
      if(sw) sw.textContent = i18n('offline');
      continue;
    }
    if(fc) fc.classList.remove('offline');
    const on = !!st.values?.[f.code];
    dot.classList.toggle('on', on);
    const tt = document.getElementById(`favtemp_${f.device}_${f.code}`);
    if(tt) tt.textContent = st.values && st.values.temp != null ? st.values.temp + '\u00b0' : '';
    if(sw){ const ctl = (BY_ID[f.device] && BY_ID[f.device].controls || []).find(x => x.code === f.code) || {};
      sw.textContent = on ? (ctl.text_on ? i18n(ctl.text_on) : i18n('Switched on')) : (ctl.text_off ? i18n(ctl.text_off) : i18n('Switched off')); }
    if(fc) fc.classList.toggle('on', on);
    const dd = BY_ID[f.device];
    if(dd) for(const b of controlsOf(dd).brights)
      syncBright(dd, b, st.values?.[b.code]);
  }
  for(const d of DEVICES){
    const card=document.getElementById('c_'+d.id);
    if(!card) continue;
    const st=STATE[d.id];
    const life = lifeOf(d.id);
    card.classList.toggle('offline', life === 'offline');
    card.classList.toggle('waiting', life === 'waiting');
    card.classList.toggle('stale', life === 'live' && !!(st && st.stale));                                   // online, but its values are old (the watchdog says so)
    if(st && st.stale && st.last_seen) card.style.setProperty('--stale-txt', JSON.stringify(`${i18n('stale')} \u00b7 ${hhmm(st.last_seen)}`));
    markLife(card, d, life);
    if(!card.title || card.dataset.lastReport){        // a plug-in's own title stays
      card.title = st && st.last_seen ? `${i18n('last report')} ${hhmm(st.last_seen)}` : '';
      card.dataset.lastReport = '1';
    }
    if(life === 'offline' && st && st.offline_since)
      card.style.setProperty('--offline-txt', JSON.stringify(`${i18n('offline since')} ${hhmm(st.offline_since)}`));
    if(life === 'waiting') card.style.setProperty('--waiting-txt', JSON.stringify(st && st.last_seen ? i18n('no data since {0}', hhmm(st.last_seen)) : i18n('waiting for report')));
    if(st) settleWaits(d);
    if(card._face){
      if(card._face.update) HomePlugins.safe(card._face.update, card, d, st || {online: false, values: {}});
      for(const e of card.querySelectorAll('.cmdwait')) e.classList.remove('cmdwait');
      if(d.controls.some(c => waitsNow(d, c.code))) card.classList.add('cmdwait-face');
      else card.classList.remove('cmdwait-face');
      continue;
    }
    if(!st) continue;
    const waits = new Set(d.controls.filter(c => waitsNow(d, c.code)).map(c => c.code));
    for(const e of card.querySelectorAll('.cmdwait')) e.classList.remove('cmdwait');
    for(const code of waits){                     // a command that waits for the device pulses
      for(const e of card.querySelectorAll(`[id="sw_${CSS.escape(d.id)}_${CSS.escape(code)}"], `
                                           + `[id="br_${CSS.escape(d.id)}_${CSS.escape(code)}"]`)) e.classList.add('cmdwait');
      const fill = card.querySelector(`[id="dotfill_${CSS.escape(d.id)}_${CSS.escape(code)}"]`);
      if(fill && fill.parentElement) fill.parentElement.classList.add('cmdwait');
      if(pending[d.id+'/'+code]) pending[d.id+'/'+code].t = Date.now();   // stays where released
    }

    const v=st.values||{};
    let anyOn=false;
    for(const ctl of d.controls){
      const val=v[ctl.code];
      if(ctl.kind==='switch'){
        const dot=document.getElementById(`sw_${d.id}_${ctl.code}`);
        if(dot) dot.classList.toggle('on', !!val);
        const swl=document.getElementById(`swlbl_${d.id}_${ctl.code}`);
        if(swl && swl.dataset.state) swl.textContent = val ? swl.dataset.on : swl.dataset.off;
        const ld = document.getElementById(`sw_${d.id}_${ctl.code}`);
        if(ld && ld.dataset.lock) ld.innerHTML = icon(val ? 'lock' : 'unlock', 22);
        if(val) anyOn=true;
      }
      if(ctl.kind==='sensor'){
        const cell=document.getElementById(`sens_${d.id}_${ctl.code}`);
        if(cell) cell.querySelector('b').textContent = ctl.code === 'uptime' && val != null ? fmtUptime(val) : fmtReading(val, d.id, ctl.code);
      }
      if(ctl.kind==='bright') syncBright(d, ctl, val);
      if(ctl.kind==='setting_enum' && ctl.on_card){
        const OFFOPT = (ctl.off_option || 'Static');
        const tgl=document.getElementById(`cet_${d.id}_${ctl.code}`);
        const sel=document.getElementById(`ces_${d.id}_${ctl.code}`);
        const active = val!=null && val!==OFFOPT;
        if(tgl) tgl.classList.toggle('on', active);
        if(sel && active && document.activeElement!==sel) sel.value = val;
      }
    }
    card.classList.toggle('any-on', anyOn);

    const lp = card.querySelector('.lastpress');
    if(lp) lp.textContent = lastPressText(d, st);
    // event flash for button devices
    if(st.last_event_ts){
      const ageMs = Date.now() - st.last_event_ts*1000;
      card.classList.toggle('eventflash', ageMs < 30000);
    } else {
      card.classList.remove('eventflash');
    }
  }
  for(const f of HomePlugins.onState) HomePlugins.safe(f, STATE);   // plug-in chips, faces, header
}

