// Device window: what a firmware built by fwgen says of itself (the three cases of docs/device-description.md).
(() => {
  const esc = Home.escapeHtml;
  const when = t => t ? new Date(t * 1000).toLocaleString(undefined, {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}) : '';
  // the build's own date is UTC (ISO, maybe without the zone): shown in local time, like every time of the house
  const dateOf = r => { const b = r.build && r.build.date ? Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(r.build.date) ? r.build.date : r.build.date + 'Z') : NaN;
    return Number.isFinite(b) ? when(b / 1000) : when(r.received); };

  function pins(m){
    return Object.entries(m.pins || {}).map(([role, pin]) => `<span class="fwpin">${esc(role)} &rarr; ${esc(pin)}</span>`).join(' ');
  }

  // the picture comes with the build record; without one, a plain list of the same facts
  function wiring(r){
    const d = r.description || {};
    if(r.build && r.build.svg)
      return `<img alt="" style="max-width:100%;background:#fff;border-radius:8px" src="data:image/svg+xml;charset=utf-8,${encodeURIComponent(r.build.svg)}">`;
    const mods = (d.modules || []).map(m => `<div class="row"><span class="rlabel">${esc(m.title || m.type)}</span><span class="rval fwpins">${pins(m)}</span></div>`).join('');
    const buses = Object.entries(d.buses || {}).map(([k, list]) => (list || []).map(b => `${k}: ${Object.entries(b).map(([a, v]) => a + ' ' + v).join(', ')}`).join('; ')).filter(Boolean).join('<br>');
    const pw = (d.power_from_gpio || []).map(p => `${esc(p.pin)} ${esc(p.level)} &rarr; ${esc(p.for)}`).join('<br>');
    return mods + (buses ? `<div class="bindcaption">${buses}</div>` : '') + (pw ? `<div class="bindcaption">${i18n('Powered from pins')}: ${pw}</div>` : '');
  }

  function html(r, d){
    if(r.case === 3) return `<div class="bindcaption">${esc(i18n('The firmware was not built in the Yash builder, so its wiring is unknown. Rebuild it in the builder to see the pin map.'))}</div>`;
    const line = i18n('Built in the Yash builder, {0}', dateOf(r));
    const flags = [];
    if(!r.build || !r.build.known) flags.push(i18n('Build not from this house'));
    if(r.mismatch) flags.push(i18n('The description does not match its hash'));
    const dev = r.description.device || {};
    const board = (dev.board && dev.board.id) || dev.chip || '', model = (d && d.model) || '';   // the card's Model already says it
    return `<div class="bindcaption">${esc(line)}</div>${flags.map(f => `<div class="bindcaption" style="color:var(--on)">${esc(f)}</div>`).join('')}
      ${board && board !== model ? `<div class="row"><span class="rlabel">${esc(i18n('Board'))}</span><span class="rval">${esc(board)}</span></div>` : ''}
      ${wiring(r)}`;
  }

  HomePlugins.register({modalSections: [{
    match: d => d.transport === 'mqtt',
    html: () => `<details><summary>${esc(i18n('Firmware and hardware'))}</summary><div data-fw="box" class="bindcaption">\u2026</div></details>`,
    wire(box, d){
      const out = box.querySelector('[data-fw="box"]');
      const sec = box.closest('section') || box;    // after the daily things, next to Details
      if(sec.parentNode) sec.parentNode.append(sec);
      let done = false;
      box.querySelector('details').addEventListener('toggle', async () => {
        if(done || !box.querySelector('details').open) return;
        done = true;
        try { out.className = ''; out.innerHTML = html(await Home.api('/api/devices/' + encodeURIComponent(d.id) + '/description'), d); }
        catch(e){ out.className = 'bindcaption'; out.textContent = e.message || String(e); }
      });
    },
  }]});
})();
