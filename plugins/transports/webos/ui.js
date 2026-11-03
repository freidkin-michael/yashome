// LG TV (webOS): its card on Settings -> TVs - address, MAC for power-on, pairing, its inputs and apps; Edit writes
// them back (the card's input and app lists follow), Pair asks the TV once (Allow on its screen); add() is a new one's name.
(() => {
  const esc = Home.escapeHtml;

  const list = async () => (await Home.api('/api/webos/tvs')).tvs;

  function card(t, box, reload){                                     // one LG TV on the TVs page
    box.innerHTML = `<div class="row" data-lg-tv="${esc(t.id)}" style="flex-wrap:wrap"><span class="rlabel bindcaption">${
        esc(i18n(t.paired ? 'paired' : 'not paired: press Pairing with the TV on'))}<br>${
        esc(i18n('Inputs'))}: ${esc(Object.keys(t.inputs).join(', ') || '\u2014')}<br>${esc(i18n('Apps'))}: ${esc(Object.keys(t.apps).join(', ') || '\u2014')}</span>
      <span style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
        <button class="dlg-btn" data-lg="pair">${esc(i18n('Pairing'))}</button>
        <button class="dlg-btn" data-lg="edit">${esc(i18n('Edit'))}</button>
        <button class="dlg-btn danger" data-lg="del">${esc(i18n('Remove'))}</button></span>
      <div class="bindcaption" data-lg="msg" style="flex-basis:100%"></div><div data-lg="form" style="flex-basis:100%"></div></div>`;
    const b = k => box.querySelector(`[data-lg="${k}"]`), say = m => { b('msg').textContent = m; };
    b('pair').onclick = async () => {
      say(i18n('Accept the request on the TV now (40 s)'));
      try { await Home.api(`/api/webos/${encodeURIComponent(t.id)}/pair`, {method: 'POST'}); reload(); } catch(e){ say(e.message || String(e)); }
    };
    b('del').onclick = async () => {
      if(!confirm(i18n('Remove {0} from the house? Its card, rules and pairing go; the TV itself is not touched.', t.name))) return;
      try { await Home.api('/api/webos/tvs/' + encodeURIComponent(t.id), {method: 'DELETE'}); await Home.reloadDevices(); reload(); } catch(e){ say(e.message || String(e)); }
    };
    b('edit').onclick = async () => {
      // what a person decides: the inputs and apps to show (names optional); the address and MAC under Advanced
      const fm = b('form');
      fm.innerHTML = `<div class="bindcaption">${esc(i18n('Loading...'))}</div>`;
      let got = {inputs: {}, apps: {}, from_tv: false};
      try { got = await Home.api(`/api/webos/tvs/${encodeURIComponent(t.id)}/lists`); } catch(e){ say(e.message || String(e)); }
      const pick = (what, fromTv) => {
        const mine = Object.fromEntries(Object.entries(t[what] || {}).map(([n, id]) => [id, n]));
        const all = {...fromTv, ...Object.fromEntries(Object.entries(mine).filter(([id]) => !(id in fromTv)).map(([id, n]) => [id, n]))};
        return Object.entries(all).map(([id, label]) => `<div class="row"><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" data-pk="${what}" data-id="${esc(id)}"${id in mine ? ' checked' : ''}> ${esc(label)}</label>
          ${what === 'inputs' ? `<input class="askin" data-nm="${esc(id)}" placeholder="${esc(label)}" value="${esc(mine[id] && mine[id] !== label ? mine[id] : '')}">` : ''}</div>`).join('')
          || `<div class="bindcaption">${esc(i18n('Switch the TV on and open Edit again: the list comes from the TV'))}</div>`;
      };
      fm.innerHTML = `${got.from_tv ? '' : `<div class="bindcaption">${esc(i18n('The TV is off or not paired: only what is set now is shown'))}</div>`}
        <h3 style="margin:10px 0 4px;font-size:15px">${esc(i18n('Inputs'))}</h3>
        <div class="bindcaption">${esc(i18n('Ticked ones are buttons on the card; a name of your own, e.g. PlayStation, is optional'))}</div>${pick('inputs', got.inputs)}
        <h3 style="margin:14px 0 4px;font-size:15px">${esc(i18n('Apps'))}</h3>${pick('apps', got.apps)}
        <details style="margin-top:10px"><summary>${esc(i18n('Advanced'))}</summary>
          <div class="row"><span class="rlabel">${esc(i18n('Address'))}<br><span class="bindcaption">${esc(i18n('A fixed one: reserve it in the router'))}</span></span><input class="askin" data-lf="host" value="${esc(t.host)}"></div>
          <div class="row"><span class="rlabel">MAC<br><span class="bindcaption">${esc(i18n('For power-on (Wake-on-LAN)'))}</span></span><input class="askin" data-lf="mac" value="${esc(t.mac)}"></div>
        </details>
        <div class="row" style="justify-content:flex-end"><button class="dlg-btn primary" data-lf="save">${esc(i18n('Save'))}</button></div>`;
      const f = k => fm.querySelector(`[data-lf="${k}"]`);
      f('save').onclick = async () => {
        const out = {inputs: {}, apps: {}};
        fm.querySelectorAll('[data-pk]').forEach(c => {
          if(!c.checked) return;
          const id = c.dataset.id, nm = fm.querySelector(`[data-nm="${CSS.escape(id)}"]`);
          out[c.dataset.pk][(nm && nm.value.trim()) || c.parentElement.textContent.trim()] = id;
        });
        const body = {name: t.name, host: f('host').value.trim(), mac: f('mac').value.trim(), inputs: out.inputs, apps: out.apps};
        try { await Home.api('/api/webos/tvs/' + encodeURIComponent(t.id), {method: 'PUT', body: JSON.stringify(body)}); await Home.reloadDevices(); reload(); }
        catch(e){ say(e.message || String(e)); }
      };
    };
  }

  // the tv plug-in's Add a TV found an LG TV: its name, then the house adds it; Pairing and Edit follow on its card
  function add(found, into, reload, say){
    into.innerHTML = `<div class="row"><span class="rlabel">${esc(i18n('Name'))}</span><input class="askin" data-lgn="name" placeholder="${esc(i18n('Living room TV'))}" value="${esc(found.name || '')}"></div>
      <div class="row" style="justify-content:flex-end"><button class="dlg-btn primary" data-lgn="save">${esc(i18n('Save'))}</button></div>`;
    into.querySelector('[data-lgn="save"]').onclick = async () => {
      const name = into.querySelector('[data-lgn="name"]').value.trim();
      if(!name){ say(i18n('Give the TV a name')); return; }
      try {
        await Home.api('/api/webos/tvs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, host: found.host, mac: found.mac || ''})});
        await Home.reloadDevices(); reload();
      } catch(e){ say(e.message || String(e)); }
    };
  }

  HomePlugins.register({
    deviceSettings: [{category: 'tv', transport: 'webos', label: 'LG', list, card, add}],
    help: [
      {id: 'webos.pair', screen: 'settings:tv', sel: '[data-lg="pair"]', title: 'Pair',
       text: 'The TV shows a question: accept it within 40 s. Once is enough; the key is kept in the house.'},
      {id: 'webos.del', screen: 'settings:tv', sel: '[data-lg="del"]', title: 'Remove',
       text: 'Takes the TV out of the house: its card and its pairing go; the TV itself is not touched.'},
      {id: 'webos.edit', screen: 'settings:tv', sel: '[data-lg="edit"]', title: 'Edit',
       text: 'Its name, address, MAC for power-on, and the inputs and apps its card offers.'},
    ],
  });
})();
