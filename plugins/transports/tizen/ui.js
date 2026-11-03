// Samsung TV (Tizen): Settings -> Samsung TV - the TVs (address, MAC for Wake-on-LAN, inputs, apps, the IR power-on fallback),
// the pairing (one Allow on the TV) and the form of a new one (Add a TV). The card is the TV plug-in's (category tv).
(() => {
  const esc = Home.escapeHtml;
  const J = {'Content-Type': 'application/json'};
  const irChoices = () => (Home.devices() || []).flatMap(d => (d.controls || []).filter(c => c.kind === 'ir_press')
    .map(c => ({v: `${d.id}|${c.code}`, t: `${d.name || d.id} \u00b7 ${c.label || c.code}`})));

  // the edit form: what a person decides - the inputs to show (and their names), the apps; a new TV also its name.
  // The address, the timings and the IR fallback sit under Advanced; the id and the MAC the house finds itself.
  const INPUTS = [['KEY_HDMI1', 'HDMI 1'], ['KEY_HDMI2', 'HDMI 2'], ['KEY_HDMI3', 'HDMI 3'], ['KEY_HDMI4', 'HDMI 4'], ['KEY_TV', 'TV']];
  const slug = v => String(v || '').toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40);
  function form(into, t, host, reload, say, seed){
    const isNew = !t;
    t = t || {id: '', name: (seed && seed.name) || '', host: host || '', mac: (seed && seed.mac) || '', inputs: {'HDMI 1': 'KEY_HDMI1', 'HDMI 2': 'KEY_HDMI2'}, apps: {}};
    const cur = t.ir_on ? `${t.ir_on.device}|${t.ir_on.code}` : '';
    const byKey = Object.fromEntries(Object.entries(t.inputs || {}).map(([n, k]) => [k, n]));
    const inputs = INPUTS.map(([k, def]) => `<div class="row"><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" data-in="${k}"${byKey[k] ? ' checked' : ''}> ${esc(def)}</label>
        <input class="askin" data-in-name="${k}" placeholder="${esc(def)}" value="${esc(byKey[k] && byKey[k] !== def ? byKey[k] : '')}"></div>`).join('');
    into.innerHTML = `${isNew ? `<div class="row"><span class="rlabel">${esc(i18n('Name'))}</span><input class="askin" data-tf="name" placeholder="${esc(i18n('Bedroom TV'))}" value="${esc(t.name)}"></div>` : ''}
      <h3 style="margin:10px 0 4px;font-size:15px">${esc(i18n('Inputs'))}</h3>
      <div class="bindcaption">${esc(i18n('Ticked ones are buttons on the card; a name of your own, e.g. PlayStation, is optional'))}</div>${inputs}
      <h3 style="margin:14px 0 4px;font-size:15px">${esc(i18n('Apps'))}</h3>
      <div data-tf="apps"><div class="bindcaption">${esc(i18n('Loading...'))}</div></div>
      <details style="margin-top:10px"><summary>${esc(i18n('Advanced'))}</summary>
        <div class="row"><span class="rlabel">${esc(i18n('Address'))}<br><span class="bindcaption">${esc(i18n('Its IP on the home network: keep it fixed in the router'))}</span></span><input class="askin" data-tf="host" value="${esc(t.host)}"></div>
        <div class="row"><span class="rlabel">MAC<br><span class="bindcaption">${esc(i18n('For power-on (Wake-on-LAN); the house reads it from the TV when it pairs'))}</span></span><input class="askin" data-tf="mac" value="${esc(t.mac)}"></div>
        <div class="row"><span class="rlabel">${esc(i18n('IR on key'))}<br><span class="bindcaption">${esc(i18n('A separate "on" key of its IR remote: pressed with every power-on'))}</span></span>
          <select class="askin" data-tf="ir"><option value="">${esc(i18n('none'))}</option>${irChoices().map(o => `<option value="${esc(o.v)}"${o.v === cur ? ' selected' : ''}>${esc(o.t)}</option>`).join('')}</select></div>
        <div class="row"><span class="rlabel">${esc(i18n('After a switch-off, s'))}<br><span class="bindcaption">${esc(i18n('The TV counts as off this long after the house switched it off'))}</span></span>
          <input class="askin" type="number" min="0" max="3600" data-tf="hold" value="${esc(t.hold_off_s == null ? 600 : t.hold_off_s)}"></div>
        <div class="row"><span class="rlabel">${esc(i18n('On again after, s'))}<br><span class="bindcaption">${esc(i18n('In that time it counts as on only after answering this long without a gap'))}</span></span>
          <input class="askin" type="number" min="0" max="3600" data-tf="confirm" value="${esc(t.hold_confirm_s == null ? 150 : t.hold_confirm_s)}"></div>
      </details>
      <div class="row" style="justify-content:flex-end"><button class="dlg-btn primary" data-tf="save">${esc(i18n('Save'))}</button></div>`;
    const f = k => into.querySelector(`[data-tf="${k}"]`);
    const chosen = new Set(Object.values(t.apps || {}));
    const drawApps = (list, note) => {
      const names = Object.fromEntries(Object.entries(t.apps || {}).map(([n, id]) => [id, n]));
      const all = [...list, ...Object.entries(names).filter(([id]) => !list.some(a => a.id === id)).map(([id, name]) => ({id, name}))];
      f('apps').innerHTML = (note ? `<div class="bindcaption">${esc(i18n(note))}</div>` : '') + all.map(a =>
        `<label class="row" style="justify-content:flex-start;gap:8px"><input type="checkbox" data-app="${esc(a.id)}" data-app-name="${esc(names[a.id] || a.name)}"${chosen.has(a.id) ? ' checked' : ''}> ${esc(names[a.id] || a.name)}</label>`).join('');
    };
    if(isNew) drawApps([['YouTube', '111299001912'], ['Netflix', '3201907018807']].map(([name, id]) => ({id, name})), 'More apps after the pairing');
    else Home.api(`/api/tizen/tvs/${encodeURIComponent(t.id)}/apps`).then(r => drawApps(r.apps, r.from_tv ? '' : 'The TV is off: the usual apps only; switch it on and open Edit again for its own list'))
      .catch(e => drawApps([], e.message || String(e)));
    f('save').onclick = async () => {
      const [dev, code] = f('ir').value.split('|');
      const name = isNew ? f('name').value.trim() : t.name;
      if(isNew && !name){ say(i18n('Give the TV a name')); return; }
      const inputs = {};
      INPUTS.forEach(([k, def]) => { if(into.querySelector(`[data-in="${k}"]`).checked) inputs[into.querySelector(`[data-in-name="${k}"]`).value.trim() || def] = k; });
      const apps = {};
      into.querySelectorAll('[data-app]').forEach(c => { if(c.checked) apps[c.dataset.appName] = c.dataset.app; });
      const body = {id: t.id || slug('samsung_' + name + '_' + (f('host').value.trim().split('.').pop() || '')), name, host: f('host').value.trim(), mac: f('mac').value.trim(),
                    inputs, apps, ir_on: dev ? {device: dev, code} : null, hold_off_s: +f('hold').value || 0, hold_confirm_s: +f('confirm').value || 0};
      if(!body.host){ say(i18n('The address is under Advanced')); return; }
      try { await Home.api('/api/tizen/tvs/' + encodeURIComponent(body.id), {method: 'PUT', headers: J, body: JSON.stringify(body)}); reload(); }
      catch(e){ say(e.message || String(e)); }
    };
  }

  let last = {tvs: [], unassigned: []};
  async function list(){ last = await Home.api('/api/tizen'); return last.tvs; }

  function card(t, box, reload){                                     // one Samsung TV on the TVs page
    box.innerHTML = `<div class="row" data-tz-tv="${esc(t.id)}" style="flex-wrap:wrap"><span class="rlabel bindcaption">${
        esc(i18n(t.paired ? 'paired' : 'not paired: press Pairing with the TV on'))}</span>
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

  HomePlugins.register({
    deviceSettings: [{category: 'tv', transport: 'tizen', label: 'Samsung', list, card,
      add: (found, into, reload, say) => form(into, null, found.host, reload, say, found)}],   // the tv plug-in's Add a TV
    help: [
      {id: 'tizen.pair', screen: 'settings:tv', sel: '[data-tz="pair"]', title: 'Pair',
       text: 'The TV shows a question: press Allow on it within 30 s. Once is enough; the key is kept in the house.'},
      {id: 'tizen.ir', screen: 'settings:tv', sel: '[data-tf="ir"]', at: 'label', title: 'IR on key',
       text: 'Only a key that switches on and never off (learned apart from "off"). It lights the screen in any state, so a power-on is this key plus one Wake-on-LAN; without it, Wake-on-LAN is repeated for 90 s.'},
    ],
  });
})();
