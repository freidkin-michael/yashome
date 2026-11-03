// Android TV / Google TV: its card on Settings -> TVs (pairing by the code on its screen, inputs, apps; address and MAC
// under Advanced) and the form of a new one for the tv plug-in's Add a TV. The card itself is the tv plug-in's.
(() => {
  const esc = Home.escapeHtml;
  const J = {'Content-Type': 'application/json'};
  const INPUTS = [['TV_INPUT_HDMI_1', 'HDMI 1'], ['TV_INPUT_HDMI_2', 'HDMI 2'], ['TV_INPUT_HDMI_3', 'HDMI 3'], ['TV_INPUT_HDMI_4', 'HDMI 4']];
  let missing = '';
  async function list(){ const r = await Home.api('/api/androidtv'); missing = r.missing || ''; return r.tvs; }

  function card(t, box, reload){                                     // one Android TV on the TVs page
    const state = !t.paired ? 'not paired: press Pairing with the TV on' : t.connected ? 'paired' : 'paired, not connected (off?)';
    box.innerHTML = `<div class="row" style="flex-wrap:wrap"><span class="rlabel bindcaption">${esc(i18n(state))}${
        missing ? `<br>${esc(i18n('Needs in EXTRA_PIP: {0}', missing))}` : ''}</span>
      <span style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
        <button class="dlg-btn" data-at="pair">${esc(i18n('Pairing'))}</button>
        <button class="dlg-btn" data-at="edit">${esc(i18n('Edit'))}</button>
        <button class="dlg-btn danger" data-at="del">${esc(i18n('Remove'))}</button></span>
      <div class="bindcaption" data-at="msg" style="flex-basis:100%"></div><div data-at="form" style="flex-basis:100%"></div></div>`;
    const b = k => box.querySelector(`[data-at="${k}"]`), say = m => { b('msg').textContent = m; };
    const id = encodeURIComponent(t.id);
    b('pair').onclick = async () => {
      say(i18n('Asking the TV...'));
      try { await Home.api(`/api/androidtv/tvs/${id}/pair`, {method: 'POST'}); }
      catch(e){ say(e.message || String(e)); return; }
      say('');
      b('form').innerHTML = `<div class="row"><span class="rlabel">${esc(i18n('The code on the TV'))}</span>
        <span style="display:flex;gap:6px"><input class="askin" data-at="code" maxlength="12" style="width:110px;text-transform:uppercase">
        <button class="dlg-btn primary" data-at="send">${esc(i18n('Confirm'))}</button></span></div>`;
      b('code').focus();
      b('send').onclick = async () => {
        try { await Home.api(`/api/androidtv/tvs/${id}/pair/code`, {method: 'POST', headers: J, body: JSON.stringify({code: b('code').value.trim()})}); reload(); }
        catch(e){ say(e.message || String(e)); }
      };
    };
    b('del').onclick = async () => {
      try { await Home.api(`/api/androidtv/tvs/${id}`, {method: 'DELETE'}); await Home.reloadDevices(); reload(); } catch(e){ say(e.message || String(e)); }
    };
    b('edit').onclick = () => {
      const byKey = Object.fromEntries(Object.entries(t.inputs || {}).map(([n, k]) => [k, n]));
      const apps = Object.entries(t.apps || {}).map(([n, l]) => `${n} = ${l}`).join('\n');
      b('form').innerHTML = `<h3 style="margin:10px 0 4px;font-size:15px">${esc(i18n('Inputs'))}</h3>
        <div class="bindcaption">${esc(i18n('Ticked ones are buttons on the card; a name of your own, e.g. PlayStation, is optional'))}</div>
        ${INPUTS.map(([k, def]) => `<div class="row"><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" data-in="${k}"${byKey[k] ? ' checked' : ''}> ${esc(def)}</label>
          <input class="askin" data-in-name="${k}" placeholder="${esc(def)}" value="${esc(byKey[k] && byKey[k] !== def ? byKey[k] : '')}"></div>`).join('')}
        <h3 style="margin:14px 0 4px;font-size:15px">${esc(i18n('Apps'))}</h3>
        <div class="bindcaption">${esc(i18n('One a line: name = package (com.netflix.ninja) or link (https://www.youtube.com)'))}</div>
        <textarea class="askin" data-at="apps" rows="4" style="width:100%">${esc(apps)}</textarea>
        <details style="margin-top:10px"><summary>${esc(i18n('Advanced'))}</summary>
          <div class="row"><span class="rlabel">${esc(i18n('Address'))}<br><span class="bindcaption">${esc(i18n('A fixed one: reserve it in the router'))}</span></span><input class="askin" data-at="host" value="${esc(t.host)}"></div>
          <div class="row"><span class="rlabel">MAC<br><span class="bindcaption">${esc(i18n('For power-on (Wake-on-LAN)'))}</span></span><input class="askin" data-at="mac" value="${esc(t.mac || '')}"></div>
        </details>
        <div class="row" style="justify-content:flex-end"><button class="dlg-btn primary" data-at="save">${esc(i18n('Save'))}</button></div>`;
      b('save').onclick = async () => {
        const inputs = {}, apps = {};
        INPUTS.forEach(([k, def]) => { if(box.querySelector(`[data-in="${k}"]`).checked) inputs[box.querySelector(`[data-in-name="${k}"]`).value.trim() || def] = k; });
        for(const line of b('apps').value.split('\n')){
          const i = line.indexOf('=');
          if(i > 0 && line.slice(i + 1).trim()) apps[line.slice(0, i).trim()] = line.slice(i + 1).trim();
        }
        const body = {name: t.name, host: b('host').value.trim(), mac: b('mac').value.trim(), inputs, apps};
        try { await Home.api(`/api/androidtv/tvs/${id}`, {method: 'PUT', headers: J, body: JSON.stringify(body)}); await Home.reloadDevices(); reload(); }
        catch(e){ say(e.message || String(e)); }
      };
    };
  }

  // the tv plug-in's Add a TV found an Android TV: its name, then the house adds it; Pairing follows on its card
  function add(found, into, reload, say){
    into.innerHTML = `<div class="row"><span class="rlabel">${esc(i18n('Name'))}</span><input class="askin" data-atn="name" placeholder="${esc(i18n('Living room TV'))}" value="${esc(found.name || '')}"></div>
      <div class="row" style="justify-content:flex-end"><button class="dlg-btn primary" data-atn="save">${esc(i18n('Save'))}</button></div>`;
    into.querySelector('[data-atn="save"]').onclick = async () => {
      const name = into.querySelector('[data-atn="name"]').value.trim();
      if(!name){ say(i18n('Give the TV a name')); return; }
      try {
        await Home.api('/api/androidtv/tvs', {method: 'POST', headers: J, body: JSON.stringify({name, host: found.host, mac: found.mac || ''})});
        await Home.reloadDevices(); reload();
      } catch(e){ say(e.message || String(e)); }
    };
  }

  HomePlugins.register({
    deviceSettings: [{category: 'tv', transport: 'androidtv', label: 'Android TV', list, card, add}],
    help: [
      {id: 'androidtv.pair', screen: 'settings:tv', sel: '[data-at="pair"]', title: 'Pair',
       text: 'The TV shows a code: type it here and confirm. Once is enough; the TV trusts the house from then on.'},
    ],
  });
})();
