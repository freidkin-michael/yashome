// TV: its own card face (power, volume + mute, inputs), the "Appliances" tab, a 12 s hold while a TV wakes.
(() => {
  const POWER_SVG = '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M13 3h-2v10h2V3zm4.83 ' +
    '2.17l-1.42 1.42A6.92 6.92 0 0 1 19 12a7 7 0 0 1-14 0c0-2.05.88-3.9 2.58-5.41L6.17 5.17A8.93 8.93 0 0 0 3 12a9 9 ' +
    '0 0 0 18 0c0-2.74-1.23-5.18-3.17-6.83z"/></svg>';
  const SPK = '\u{1f50a}', MUTED = '\u{1f507}';
  const css = document.createElement('style');
  css.textContent = `.acrow{display:flex;flex-wrap:wrap;gap:6px}
    .aclbl{font-size:12px;color:var(--dim);text-transform:uppercase;margin-top:4px}
    .acbtn{background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:10px;padding:8px 10px;
      cursor:pointer;font:inherit;font-size:14px;font-weight:500;min-height:38px;flex:1 0 auto}
    .acbtn.sel{background:var(--accent-fill);border-color:var(--accent-fill)}
    .acslide{display:flex;align-items:center;gap:12px;padding:2px 2px 0} .acslide input{flex:1}
    .acval{min-width:72px;text-align:right;font-size:34px;font-weight:700}
    .tvpower{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;background:var(--card2);
      border:1px solid var(--line);color:var(--txt);border-radius:12px;padding:12px;margin-top:10px;cursor:pointer;
      font:inherit;font-size:15px;font-weight:600;min-height:46px}
    .tvpower.on{background:var(--accent-fill);border-color:var(--accent-fill)}
    .tvmute{flex:0 0 auto;font-size:18px;min-width:46px}`;
  document.head.append(css);
  const on = (id, code) => { const st = Home.state(id); return !!(st && st.online && st.values && st.values[code]); };
  const stop = ev => ev.stopPropagation();

  function render(c, d){
    c.append(Home.el('div', 'name', Home.escapeHtml(d.name)));
    c.append(Home.el('div', 'info', `<div class="meta">${Home.escapeHtml(i18n('TV'))} \u00b7 ${Home.escapeHtml(d.transport || '')}</div>`));
    const pw = Home.el('button', 'tvpower', POWER_SVG + '<span>' + Home.escapeHtml(i18n('Power')) + '</span>');
    pw.dataset.tv = 'power';
    pw.onclick = ev => { stop(ev); Home.sendControl(d.id, 'power', !on(d.id, 'power')); };
    c.append(pw);
    if((d.controls || []).some(x => x.code === 'volume')){
      c.append(Home.el('div', 'aclbl', Home.escapeHtml(i18n('Volume'))));
      const row = Home.el('div', 'acslide'); row.onclick = stop;
      const sl = document.createElement('input');
      sl.type = 'range'; sl.min = 0; sl.max = 100; sl.step = 1; sl.dataset.tv = 'vol';
      const vt = Home.el('div', 'acval', '\u2014'); vt.dataset.tv = 'volval';
      sl.oninput = () => { vt.textContent = sl.value; sl.dataset.drag = Date.now(); };
      sl.onchange = () => { sl.dataset.drag = Date.now(); Home.sendControl(d.id, 'volume', parseInt(sl.value, 10)); };
      const mb = Home.el('button', 'acbtn tvmute', SPK); mb.dataset.tv = 'mute';
      mb.onclick = ev => { stop(ev); Home.sendControl(d.id, 'mute', !on(d.id, 'mute')); };
      row.append(sl, vt, mb);
      c.append(row);
    }
    const ictl = (d.controls || []).find(x => x.code === 'input' && Array.isArray(x.range));
    if(ictl){
      c.append(Home.el('div', 'aclbl', Home.escapeHtml(ictl.label || i18n('Input'))));
      const row = Home.el('div', 'acrow'); row.onclick = stop;
      for(const opt of ictl.range){
        const b = Home.el('button', 'acbtn', Home.escapeHtml((ictl.labels || {})[opt] || opt));
        b.dataset.code = 'input'; b.dataset.value = opt;
        b.onclick = ev => { stop(ev); Home.sendControl(d.id, 'input', opt); };
        row.append(b);
      }
      c.append(row);
    }
  }

  function update(c, d, st){
    const v = (st && st.online && st.values) || {};
    c.classList.toggle('any-on', !!v.power);
    const pw = c.querySelector('[data-tv="power"]');
    if(pw){
      pw.classList.toggle('on', !!v.power);
      pw.querySelector('span').textContent = v.power ? i18n('Switched on') : i18n('Switched off');
    }
    const sl = c.querySelector('[data-tv="vol"]'), vt = c.querySelector('[data-tv="volval"]');
    if(sl && v.volume != null && !(sl.dataset.drag && Date.now() - sl.dataset.drag < 4000)){   // not under the finger
      sl.value = v.volume;
      if(vt) vt.textContent = v.volume;
    }
    const mb = c.querySelector('[data-tv="mute"]');
    if(mb){ mb.classList.toggle('sel', !!v.mute); mb.textContent = v.mute ? MUTED : SPK; }
    c.querySelectorAll('.acbtn[data-code]').forEach(b => b.classList.toggle('sel', b.dataset.value === String(v[b.dataset.code])));
  }

  HomePlugins.register({
    tabs: [{id: 'tech', label: 'Appliances', cats: ['tv', 'climate', 'ir', 'media'], after: 'button'}],
    cardFaces: [{match: d => d.category === 'tv', render, update}],
    holds: [(d, code, value) => d && d.transport === 'webos' && code === 'power' && value === true ? 12000 : 0],
  });
})();
