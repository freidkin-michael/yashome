// Device window: what a firmware built by fwgen says of itself (the three cases of docs/device-description.md).
(() => {
  const esc = Home.escapeHtml;
  const when = t => t ? new Date(t * 1000).toLocaleString(undefined, {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}) : '';
  const dateOf = r => (r.build && r.build.date ? String(r.build.date).replace('T', ' ').slice(0, 16) : '') || when(r.received);

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

  function html(r){
    if(r.case === 3) return `<div class="bindcaption">${esc(i18n('The firmware was not built in the Yash builder, so its wiring is unknown. Rebuild it in the builder to see the pin map.'))}</div>`;
    const line = r.case === 2
      ? i18n('Description updated from the device, build {0}, {1}', r.hash, dateOf(r))
      : i18n('Yash build {0}, {1}', r.hash, dateOf(r));
    const flags = [];
    if(!r.build || !r.build.known) flags.push(i18n('Build not from this house'));
    if(r.mismatch) flags.push(i18n('The description does not match its hash'));
    const ch = r.changes || {};
    const diff = (ch.added && ch.added.length) || (ch.removed && ch.removed.length)
      ? `<div class="bindcaption" style="color:var(--on)">${esc(i18n('Firmware changed: +{0} channels, -{1} channels compared with the previous build', (ch.added || []).length, (ch.removed || []).length))}</div>` : '';
    const gap = (r.card_gap && r.card_gap.missing) || [];
    const more = gap.length
      ? `<details><summary class="bindcaption">${esc(i18n('The device has {0} more channels than its card shows', gap.length))}</summary><div class="bindcaption">${gap.map(esc).join('<br>')}</div></details>` : '';
    const dev = r.description.device || {};
    const ents = (r.description.entities || []).map(e => `<div class="row"><span class="rlabel">${esc(e.name)}</span><span class="rval">${esc(e.role || e.domain)}${e.unit ? ' (' + esc(e.unit) + ')' : ''}</span></div>`).join('');
    const hist = (r.history || []).map(h => `${esc(h.hash)} ${esc(when(h.received))}`).join(', ');
    return `<div class="bindcaption">${esc(line)}</div>${flags.map(f => `<div class="bindcaption" style="color:var(--on)">${esc(f)}</div>`).join('')}${diff}${more}
      <div class="row"><span class="rlabel">${esc(i18n('Board'))}</span><span class="rval">${esc((dev.board && dev.board.id) || dev.chip || '')}</span></div>
      ${wiring(r)}
      <div class="bindcaption" style="margin-top:8px">${esc(i18n('Entities'))}</div>${ents}
      ${hist ? `<div class="bindcaption">${esc(i18n('Earlier builds'))}: ${hist}</div>` : ''}`;
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
        try { out.className = ''; out.innerHTML = html(await Home.api('/api/devices/' + encodeURIComponent(d.id) + '/description')); }
        catch(e){ out.className = 'bindcaption'; out.textContent = e.message || String(e); }
      });
    },
  }]});
})();
