// Climate: the AC remote on its card face and in the modal; "in the room" from another device's temperature.
(() => {
  const DEG = '\u00b0';
  const css = document.createElement('style');
  css.textContent = `.acpanel{display:flex;flex-direction:column;gap:8px;margin-top:10px}
    .acrow{display:flex;flex-wrap:wrap;gap:6px}
    .aclbl{font-size:12px;color:var(--dim);text-transform:uppercase;margin-top:4px}
    .acbtn{background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:10px;padding:8px 10px;
      cursor:pointer;font:inherit;font-size:14px;font-weight:500;min-height:38px;flex:1 0 auto}
    .acbtn.sel{background:var(--accent-fill);border-color:var(--accent-fill)}
    .acstate{display:flex;align-items:center;gap:8px;flex:1;min-height:38px;font-size:14px;font-weight:600;color:var(--dim)}
    .acstate .acdot{width:12px;height:12px;border-radius:50%;background:#b04040} .acstate.on{color:var(--txt)} .acstate.on .acdot{background:var(--on)}
    .acact{flex:0 0 auto}
    .acbtn.ico{display:flex;align-items:center;justify-content:center} .acbtn.ico svg{width:22px;height:22px;display:block}
    .acbtn.sel svg [stroke]:not([stroke=none]){stroke:#1a1500} .acbtn.sel svg [fill]:not([fill=none]){fill:#1a1500}
    .acslide{display:flex;align-items:center;gap:12px;padding:2px 2px 0} .acslide input{flex:1}
    .acval{min-width:72px;text-align:right;font-size:34px;font-weight:700}
    .acroom{display:flex;align-items:baseline;gap:8px;line-height:1.1}
    .acroom b{font-size:26px;font-weight:700} .acroom span{font-size:14px;color:var(--dim)}`;
  document.head.append(css);
  const E = s => Home.escapeHtml(s);
  const stop = ev => ev.stopPropagation();
  const ctl = (d, code) => (d.controls || []).find(x => x.code === code && Array.isArray(x.range) && x.range.length);
  const label = (c, opt) => (c.labels || {})[opt] || opt;
  const icon = v => (window.PW && PW.acIcon) ? PW.acIcon(v) : '';   // panel-widgets.js, when the panels plug-in is there
  const recent = el => el.dataset.drag && Date.now() - el.dataset.drag < 4000;   // not under the finger
  const tempWords = () => ['temp', i18n('Temperature').toLowerCase()];
  function tempSources(selfId){
    return Home.devices().filter(x => x.id !== selfId && (x.controls || []).some(c => c.kind === 'sensor' &&
      (c.code === 'temperature' || tempWords().some(w => (c.label || '').toLowerCase().includes(w)) ||
       /(^|[^A-Za-z])C$/.test((c.unit || '').trim()))));
  }
  function slider(d, c, send){
    const lo = parseInt(c.range[0], 10), hi = parseInt(c.range[c.range.length - 1], 10);
    const row = Home.el('div', 'acslide'); row.onclick = stop;
    const sl = document.createElement('input');
    sl.type = 'range'; sl.min = lo; sl.max = hi; sl.step = 1; sl.dataset.ac = 'temp';
    const txt = Home.el('div', 'acval', '\u2014'); txt.dataset.ac = 'tempval';
    sl.oninput = () => { txt.textContent = sl.value + DEG + 'C'; sl.dataset.drag = Date.now(); };
    sl.onchange = () => { sl.dataset.drag = Date.now(); send(parseInt(sl.value, 10)); };
    row.append(sl, txt);
    return row;
  }
  // a phone: the rows below the essentials open on request
  function foldable(wrap, more){
    if(!(window.matchMedia && window.matchMedia('(max-width: 640px)').matches) || !more.children.length){ wrap.append(more); return; }
    more.classList.add('acmore', 'folded');
    const b = Home.el('button', 'dlg-btn acmorebtn', i18n('More'));
    b.dataset.ac = 'more';
    b.onclick = ev => { ev.stopPropagation(); const f = more.classList.toggle('folded'); b.textContent = i18n(f ? 'More' : 'Less'); };
    wrap.append(b, more);
  }
  function optionRows(d, box){
    for(const code of ['fan', 'mode']){
      const c = ctl(d, code); if(!c) continue;
      box.append(Home.el('div', 'aclbl', E(i18n(code === 'fan' ? 'Fan' : code === 'mode' ? 'Mode' : (c.label || code)))));
      const row = Home.el('div', 'acrow');
      const icons = c.range.every(o => icon(o));                // one group: all icons (the word their title) or all words, never mixed
      for(const opt of c.range){
        const ico = icons ? icon(opt) : '', b = Home.el('button', 'acbtn' + (ico ? ' ico' : ''), ico || E(label(c, opt)));
        if(ico){ b.title = label(c, opt); b.setAttribute('aria-label', label(c, opt)); }
        b.dataset.code = code; b.dataset.value = opt;
        b.onclick = ev => { stop(ev); Home.sendControl(d.id, code, opt); };
        row.append(b);
      }
      box.append(row);
    }
  }
  function patch(box, v){
    const sl = box.querySelector('[data-ac="temp"]'), tx = box.querySelector('[data-ac="tempval"]');
    if(sl && v.temp != null && !recent(sl)){ sl.value = v.temp; if(tx) tx.textContent = v.temp + DEG + 'C'; }
    box.querySelectorAll('.acbtn[data-code]').forEach(b => b.classList.toggle('sel', b.dataset.value === String(v[b.dataset.code])));
  }

  function render(c, d){
    c.append(Home.el('div', 'name', (Home.devIcon ? Home.devIcon(d) : '') + E(d.name)));
    c.append(Home.el('div', 'info', `<div class="meta">${E((Home.roomOf && Home.roomOf(d.id)) || i18n('Climate'))}</div>`));
    if((d.controls || []).some(x => x.code === 'room_temp')){
      c.append(Home.el('div', 'acroom', `<b data-ac="room">\u2014</b><span>${E(i18n('in the room'))}</span>`));
      c.classList.add('has-acroom');
    }
    const wrap = Home.el('div', 'acpanel');
    const pw = Home.el('div', 'acrow');
    // power: its name and the one power button of the dashboard (on = filled); the word is its tooltip
    pw.style.cssText = 'align-items:center;justify-content:space-between';
    pw.append(Home.el('div', 'acstate', `<span class="acstatetxt">${E(i18n('Power'))}</span>`));
    const pb = Home.el('div', 'toggle acact');
    pb.dataset.power = 'true'; pb.dataset.ac = 'power';
    pb.onclick = ev => { stop(ev); pb.classList.toggle('on', pb.dataset.power === 'true'); Home.sendControl(d.id, 'power', pb.dataset.power === 'true'); };
    pw.append(pb);
    wrap.append(pw);
    const t = ctl(d, 'temp');
    if(t){ wrap.append(Home.el('div', 'aclbl', E(i18n('Temperature')))); wrap.append(slider(d, t, x => Home.sendControl(d.id, 'temp', x))); }
    const more = Home.el('div');
    optionRows(d, more);
    foldable(wrap, more);
    c.append(wrap);
  }
  function update(c, d, st){
    const v = (st && st.values) || {};
    const on = !!(st && st.online && v.power);
    c.classList.toggle('any-on', on);
    const stt = c.querySelector('.acstate'), pb = c.querySelector('.acact');
    if(stt) stt.classList.toggle('on', on);
    if(pb){ pb.dataset.power = String(!on); pb.classList.toggle('on', on); pb.title = i18n(on ? 'Switched on' : 'Switched off'); }
    const rb = c.querySelector('[data-ac="room"]');
    if(rb) rb.textContent = v.room_temp != null ? YDisp.temp(v.room_temp, d.id + '/room_temp') + DEG : '\u2014';
    patch(c, v);
  }

  const modal = {
    match: d => d.category === 'climate',
    hideCodes: ['temp', 'fan', 'mode'],
    html(d){
      const opts = [`<option value="">${E(i18n('\u2014 none \u2014'))}</option>`,
        `<option value="room"${d.room_temp_from === 'room' ? ' selected' : ''}>${E(i18n("The room's thermometer"))}</option>`].concat(tempSources(d.id).map(s =>
        `<option value="${E(s.id)}"${s.id === d.room_temp_from ? ' selected' : ''}>${E(s.name)}</option>`)).join('');
      return `<h3>${E(i18n('Climate'))}</h3><div class="acpanel" data-ac="panel"></div>
        <div class="aclbl" style="margin-top:14px">${E(i18n('Room temperature from'))}</div>
        <div class="row"><select data-ac="src" style="flex:1;min-width:0;background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 8px;font:inherit">${opts}</select>
          <span class="rval" style="margin-left:8px"><b data-ac="room">\u2014</b>${DEG}</span></div>
        <div class="bindcaption" data-ac="note" style="min-height:0"></div>`;
    },
    wire(box, d, later){
      const panel = box.querySelector('[data-ac="panel"]');
      const t = ctl(d, 'temp');
      if(t){ panel.append(Home.el('div', 'aclbl hqlbl', E(t.label || i18n('Temperature')))); panel.append(slider(d, t, x => Home.sendControl(d.id, 'temp', x))); }      // its help "?" on this line
      optionRows(d, panel);
      const src = box.querySelector('[data-ac="src"]'), note = box.querySelector('[data-ac="note"]');
      src.onchange = () => { note.textContent = i18n('there are unsaved changes \u2014 press OK'); note.style.color = 'var(--dim)'; };
      later(async () => {
        const want = src.value || null;
        if((d.room_temp_from || null) === want) return;
        await Home.api(`/api/devices/${encodeURIComponent(d.id)}/room-temp-from`,
          {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({source: src.value})});
        d.room_temp_from = want || undefined;
      });
    },
    update(box, d, st){
      const v = (st && st.values) || {};
      patch(box, v);
      const rm = box.querySelector('[data-ac="room"]');
      if(rm) rm.textContent = v.room_temp != null ? YDisp.temp(v.room_temp, d.id + '/room_temp') : '\u2014';
    },
  };

  HomePlugins.register({
    tabs: [{id: 'tech', label: 'Appliances', cats: ['climate'], after: 'button'}],
    cardFaces: [{match: d => d.category === 'climate', render, update}],
    modalSections: [modal],
    help: [
      {id: 'climate.power', screen: 'tab:p:tech', sel: '.card [data-ac="power"]', title: 'Power', text: 'Switches the AC on or off; filled means it is on.'},
      {id: 'climate.temp', screen: 'tab:p:tech', sel: '.card [data-ac="temp"]', title: 'Temperature', text: 'The temperature the AC keeps: it is sent when the slider is let go.'},
      {id: 'climate.fan', screen: 'tab:p:tech', sel: '.card .acbtn[data-code="fan"]', title: 'Fan', text: 'How hard the AC blows; the lit button is its speed now.'},
      {id: 'climate.mode', screen: 'tab:p:tech', sel: '.card .acbtn[data-code="mode"]', title: 'Mode', text: 'Cool, heat, dry, fan only and what else the AC has; the lit button is its mode now.'},
      {id: 'climate.more', screen: 'tab:p:tech', sel: '.card [data-ac="more"]', title: 'More', text: 'On a phone the fan and mode rows are folded: this opens them.'},
      {id: 'acwin.temp', screen: 'climate', screenName: 'AC window', sel: '#md [data-ac="temp"]', at: 'label', title: 'Temperature', text: 'The temperature the AC keeps: it is sent when the slider is let go.'},
      {id: 'acwin.fan', screen: 'climate', screenName: 'AC window', sel: '#md .acbtn[data-code="fan"]', title: 'Fan', text: 'How hard the AC blows; the lit button is its speed now.'},
      {id: 'acwin.mode', screen: 'climate', screenName: 'AC window', sel: '#md .acbtn[data-code="mode"]', title: 'Mode', text: 'Cool, heat, dry, fan only and what else the AC has; the lit button is its mode now.'},
      {id: 'acwin.src', screen: 'climate', screenName: 'AC window', sel: '#md [data-ac="src"]', title: 'Room temperature from',
       text: 'A thermometer of the house: its reading is shown as "in the room" on the card. Saved with OK; none: no reading.'},
    ],
  });
})();
