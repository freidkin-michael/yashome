// Samsung TV (Tizen): Settings -> Samsung TV - the TVs (address, MAC for Wake-on-LAN, inputs, apps, the IR power-on fallback),
// the pairing (one Allow on the TV) and the TVs found on the LAN. The card is the TV plug-in's (category tv).
(() => {
  const esc = Home.escapeHtml;
  const J = {'Content-Type': 'application/json'};
  const lines = m => Object.entries(m || {}).map(([k, v]) => `${k} = ${v}`).join('\n');
  const parse = t => Object.fromEntries(t.split('\n').map(l => l.split('=')).filter(p => p.length === 2 && p[0].trim() && p[1].trim())
    .map(p => [p[0].trim(), p[1].trim()]));
  const irChoices = () => (Home.devices() || []).flatMap(d => (d.controls || []).filter(c => c.kind === 'ir_press')
    .map(c => ({v: `${d.id}|${c.code}`, t: `${d.name || d.id} \u00b7 ${c.label || c.code}`})));

  // the edit form: a TV (Edit) or a new one (Add, Find); saved, the page is drawn again
  function form(into, t, host, reload, say){
    t = t || {id: '', name: '', host: host || '', mac: '', inputs: {'HDMI 1': 'KEY_HDMI1', 'HDMI 2': 'KEY_HDMI2'}, apps: {}};
    const cur = t.ir_on ? `${t.ir_on.device}|${t.ir_on.code}` : '';
    into.innerHTML = `<div class="row"><span class="rlabel">Id</span><input class="askin" data-tf="id" value="${esc(t.id)}"${t.id ? ' disabled' : ''}></div>
      <div class="row"><span class="rlabel">${esc(i18n('Name'))}</span><input class="askin" data-tf="name" value="${esc(t.name)}"></div>
      <div class="row"><span class="rlabel">${esc(i18n('Address'))}</span><input class="askin" data-tf="host" value="${esc(t.host)}"></div>
      <div class="row"><span class="rlabel">MAC<br><span class="bindcaption">${esc(i18n('For power-on (Wake-on-LAN)'))}</span></span><input class="askin" data-tf="mac" value="${esc(t.mac)}"></div>
      <div class="row"><span class="rlabel">${esc(i18n('Inputs'))}<br><span class="bindcaption">${esc(i18n('name = key, one a line'))}</span></span><textarea class="askin" rows="3" data-tf="inputs">${esc(lines(t.inputs))}</textarea></div>
      <div class="row"><span class="rlabel">${esc(i18n('Apps'))}<br><span class="bindcaption">${esc(i18n('name = app id, one a line'))}</span></span><textarea class="askin" rows="3" data-tf="apps">${esc(lines(t.apps))}</textarea></div>
      <div class="row"><span class="rlabel">${esc(i18n('IR on key'))}<br><span class="bindcaption">${esc(i18n('A separate "on" key of its IR remote: pressed with every power-on'))}</span></span>
        <select class="askin" data-tf="ir"><option value="">${esc(i18n('none'))}</option>${irChoices().map(o => `<option value="${esc(o.v)}"${o.v === cur ? ' selected' : ''}>${esc(o.t)}</option>`).join('')}</select></div>
      <div class="row"><span class="rlabel">${esc(i18n('After a switch-off, s'))}<br><span class="bindcaption">${esc(i18n('The TV counts as off this long after the house switched it off'))}</span></span>
        <input class="askin" type="number" min="0" max="3600" data-tf="hold" value="${esc(t.hold_off_s == null ? 600 : t.hold_off_s)}"></div>
      <div class="row"><span class="rlabel">${esc(i18n('On again after, s'))}<br><span class="bindcaption">${esc(i18n('In that time it counts as on only after answering this long without a gap'))}</span></span>
        <input class="askin" type="number" min="0" max="3600" data-tf="confirm" value="${esc(t.hold_confirm_s == null ? 150 : t.hold_confirm_s)}"></div>
      <div class="row" style="justify-content:flex-end"><button class="dlg-btn primary" data-tf="save">${esc(i18n('Save'))}</button></div>`;
    const f = k => into.querySelector(`[data-tf="${k}"]`);
    f('save').onclick = async () => {
      const [dev, code] = f('ir').value.split('|');
      const body = {id: f('id').value.trim(), name: f('name').value.trim(), host: f('host').value.trim(), mac: f('mac').value.trim(),
                    inputs: parse(f('inputs').value), apps: parse(f('apps').value), ir_on: dev ? {device: dev, code} : null, hold_off_s: +f('hold').value || 0, hold_confirm_s: +f('confirm').value || 0};
      try { await Home.api('/api/tizen/tvs/' + encodeURIComponent(body.id), {method: 'PUT', headers: J, body: JSON.stringify(body)}); reload(); }
      catch(e){ say(e.message || String(e)); }
    };
  }

  let last = {tvs: [], unassigned: []};
  async function list(){ last = await Home.api('/api/tizen'); return last.tvs; }

  function card(t, box, reload){                                     // one Samsung TV on the TVs page
    box.innerHTML = `<div class="row" data-tz-tv="${esc(t.id)}" style="flex-wrap:wrap"><span class="rlabel bindcaption">${
        esc([t.host, t.mac, i18n(t.paired ? 'paired' : 'not paired')].filter(Boolean).join(' \u00b7 '))}<br>${
        esc(i18n('Inputs'))}: ${esc(Object.keys(t.inputs || {}).join(', ') || '\u2014')}<br>${esc(i18n('Apps'))}: ${esc(Object.keys(t.apps || {}).join(', ') || '\u2014')}</span>
      <span style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
        <button class="dlg-btn" data-tz="pair">${esc(i18n('Pairing'))}</button>
        ${!t.paired && last.unassigned.length ? `<select class="askin" data-tz="adopt-key">${last.unassigned.map(k => `<option>${esc(k)}</option>`).join('')}</select>
          <button class="dlg-btn" data-tz="adopt">${esc(i18n('Use this pairing'))}</button>` : ''}
        <button class="dlg-btn" data-tz="edit">${esc(i18n('Edit'))}</button>
        <button class="dlg-btn danger" data-tz="del">${esc(i18n('Remove'))}</button></span>
      <div class="bindcaption" data-tz="msg" style="flex-basis:100%"></div><div data-tz="form" style="flex-basis:100%"></div></div>`;
    const b = k => box.querySelector(`[data-tz="${k}"]`), say = m => { b('msg').textContent = m; };
    box.querySelectorAll('button[data-tz]').forEach(x => x.addEventListener('click', () => say(''), true));   // a new press clears the old word
    b('edit').onclick = () => form(b('form'), t, '', reload, say);
    b('del').onclick = async () => {
      try { await Home.api('/api/tizen/tvs/' + encodeURIComponent(t.id), {method: 'DELETE'}); reload(); } catch(e){ say(e.message || String(e)); }
    };
    b('pair').onclick = async () => {
      say(i18n('Press Allow on the TV now (30 s)'));
      try { await Home.api(`/api/tizen/tvs/${encodeURIComponent(t.id)}/pair`, {method: 'POST'}); reload(); } catch(e){ say(e.message || String(e)); }
    };
    if(b('adopt')) b('adopt').onclick = async () => {
      try { await Home.api(`/api/tizen/tvs/${encodeURIComponent(t.id)}/adopt`, {method: 'POST', headers: J, body: JSON.stringify({key: b('adopt-key').value})}); reload(); }
      catch(e){ say(e.message || String(e)); }
    };
  }

  function actions(box, reload){                                     // under the cards: find a Samsung TV on the LAN, add one
    const w = Home.el('div', '');
    w.innerHTML = `<div class="row" style="justify-content:flex-end;gap:6px"><span data-tz="msg" class="bindcaption"></span>
        <button class="dlg-btn" data-tz="find">${esc(i18n('Find on the network'))}</button>
        <button class="dlg-btn primary" data-tz="add">${esc(i18n('Add a Samsung TV'))}</button></div>
      <div data-tz="found"></div><div data-tz="form"></div>`;
    box.append(w);
    const q = k => w.querySelector(`[data-tz="${k}"]`), say = t => { q('msg').textContent = t; };
    q('add').onclick = () => form(q('form'), null, '', reload, say);
    q('find').onclick = async () => {
      say(i18n('Searching...'));
      try {
        const r = await Home.api('/api/tizen/discover');
        say(r.found.length ? '' : i18n('None answered (the TV may be off, or the container has no LAN multicast).'));
        q('found').innerHTML = r.found.filter(f => !f.known).map(f => `<div class="row"><span class="rlabel">${esc(f.name)}<br><span class="bindcaption">${esc(f.host)}</span></span>
          <button class="dlg-btn" data-tz-host="${esc(f.host)}">${esc(i18n('Add'))}</button></div>`).join('');
        q('found').querySelectorAll('[data-tz-host]').forEach(b => { b.onclick = () => form(q('form'), null, b.dataset.tzHost, reload, say); });
      } catch(e){ say(e.message || String(e)); }
    };
  }

  HomePlugins.register({
    deviceSettings: [{category: 'tv', transport: 'tizen', label: 'Samsung', list, card, actions}],
    help: [
      {id: 'tizen.add', screen: 'settings:tv', sel: '[data-tz="add"]', title: 'Add a TV',
       text: 'The address (a fixed one: reserve it in the router), its MAC for power-on, the inputs and apps it should offer.'},
      {id: 'tizen.find', screen: 'settings:tv', sel: '[data-tz="find"]', title: 'Find on the network',
       text: 'Asks the LAN which Samsung TVs are there; a TV that is off does not answer.'},
      {id: 'tizen.pair', screen: 'settings:tv', sel: '[data-tz="pair"]', title: 'Pair',
       text: 'The TV shows a question: press Allow on it within 30 s. Once is enough; the key is kept in the house.'},
      {id: 'tizen.ir', screen: 'settings:tv', sel: '[data-tf="ir"]', at: 'label', title: 'IR on key',
       text: 'Only a key that switches on and never off (learned apart from "off"). It lights the screen in any state, so a power-on is this key plus one Wake-on-LAN; without it, Wake-on-LAN is repeated for 90 s.'},
    ],
  });
})();
