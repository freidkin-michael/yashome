// LG TV (webOS): its card on Settings -> TVs - address, MAC for power-on, pairing, its inputs and apps; Edit writes
// them back (the card's input and app lists follow), Pair asks the TV once (Allow on its screen).
(() => {
  const esc = Home.escapeHtml;
  const lines = m => Object.entries(m || {}).map(([k, v]) => `${k} = ${v}`).join('\n');
  const parse = t => Object.fromEntries(t.split('\n').map(l => l.split('=')).filter(p => p.length === 2 && p[0].trim() && p[1].trim())
    .map(p => [p[0].trim(), p[1].trim()]));

  const list = async () => (await Home.api('/api/webos/tvs')).tvs;

  function card(t, box, reload){                                     // one LG TV on the TVs page
    box.innerHTML = `<div class="row" data-lg-tv="${esc(t.id)}" style="flex-wrap:wrap"><span class="rlabel bindcaption">${
        esc([t.live_host || t.host, t.mac, i18n(t.paired ? 'paired' : 'not paired')].filter(Boolean).join(' \u00b7 '))}<br>${
        esc(i18n('Inputs'))}: ${esc(Object.keys(t.inputs).join(', ') || '\u2014')}<br>${esc(i18n('Apps'))}: ${esc(Object.keys(t.apps).join(', ') || '\u2014')}</span>
      <span style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
        <button class="dlg-btn" data-lg="pair">${esc(i18n('Pairing'))}</button>
        <button class="dlg-btn" data-lg="edit">${esc(i18n('Edit'))}</button>
        <button class="dlg-btn danger" data-lg="del">${esc(i18n('Remove'))}</button></span>
      <div class="bindcaption" data-lg="msg" style="flex-basis:100%"></div><div data-lg="form" style="flex-basis:100%"></div></div>`;
    const b = k => box.querySelector(`[data-lg="${k}"]`), say = m => { b('msg').textContent = m; };
    b('pair').onclick = async () => {
      say(i18n('Press Allow on the TV now (30 s)'));
      try { await Home.api(`/api/webos/${encodeURIComponent(t.id)}/pair`, {method: 'POST'}); reload(); } catch(e){ say(e.message || String(e)); }
    };
    b('del').onclick = async () => {
      if(!confirm(i18n('Remove {0} from the house? Its card, rules and pairing go; the TV itself is not touched.', t.name))) return;
      try { await Home.api('/api/webos/tvs/' + encodeURIComponent(t.id), {method: 'DELETE'}); await Home.reloadDevices(); reload(); } catch(e){ say(e.message || String(e)); }
    };
    b('edit').onclick = () => {
      b('form').innerHTML = `<div class="row"><span class="rlabel">${esc(i18n('Name'))}</span><input class="askin" data-lf="name" value="${esc(t.name)}"></div>
        <div class="row"><span class="rlabel">${esc(i18n('Address'))}<br><span class="bindcaption">${esc(i18n('A fixed one: reserve it in the router'))}</span></span><input class="askin" data-lf="host" value="${esc(t.host)}"></div>
        <div class="row"><span class="rlabel">MAC<br><span class="bindcaption">${esc(i18n('For power-on (Wake-on-LAN)'))}</span></span><input class="askin" data-lf="mac" value="${esc(t.mac)}"></div>
        <div class="row"><span class="rlabel">${esc(i18n('Inputs'))}<br><span class="bindcaption">${esc(i18n('name = input id (HDMI_1), one a line'))}</span></span><textarea class="askin" rows="4" data-lf="inputs">${esc(lines(t.inputs))}</textarea></div>
        <div class="row"><span class="rlabel">${esc(i18n('Apps'))}<br><span class="bindcaption">${esc(i18n('name = app id, one a line'))}</span></span><textarea class="askin" rows="4" data-lf="apps">${esc(lines(t.apps))}</textarea></div>
        <div class="row" style="justify-content:flex-end"><button class="dlg-btn primary" data-lf="save">${esc(i18n('Save'))}</button></div>`;
      const f = k => b('form').querySelector(`[data-lf="${k}"]`);
      f('save').onclick = async () => {
        const body = {name: f('name').value.trim(), host: f('host').value.trim(), mac: f('mac').value.trim(), inputs: parse(f('inputs').value), apps: parse(f('apps').value)};
        try { await Home.api('/api/webos/tvs/' + encodeURIComponent(t.id), {method: 'PUT', body: JSON.stringify(body)}); await Home.reloadDevices(); reload(); }
        catch(e){ say(e.message || String(e)); }
      };
    };
  }

  HomePlugins.register({
    deviceSettings: [{category: 'tv', transport: 'webos', label: 'LG', list, card}],
    help: [
      {id: 'webos.pair', screen: 'settings:tv', sel: '[data-lg="pair"]', title: 'Pair',
       text: 'The TV shows a question: press Allow on it within 30 s. Once is enough; the key is kept in the house.'},
      {id: 'webos.del', screen: 'settings:tv', sel: '[data-lg="del"]', title: 'Remove',
       text: 'Takes the TV out of the house: its card and its pairing go; the TV itself is not touched.'},
      {id: 'webos.edit', screen: 'settings:tv', sel: '[data-lg="edit"]', title: 'Edit',
       text: 'Its name, address, MAC for power-on, and the inputs and apps its card offers.'},
    ],
  });
})();
