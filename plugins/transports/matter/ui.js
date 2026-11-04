// Settings -> Devices -> Matter (owner only): the controller (where it runs, Bluetooth, the Thread network), the nodes (name,
// vendor and product, reachable, Thread or Wi-Fi, what is not shown yet, Unpair) and pairing by one code. Add -> Matter opens
// the same pairing. A Matter device's window shows its node.
(() => {
  const esc = Home.escapeHtml;
  const api = (path, opts) => Home.api(path, opts);
  const send = (method, path, body) => api(path, {method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  const CARD = 'border:1px solid var(--line, rgba(148,163,184,.25));border-radius:12px;padding:10px 12px;margin:8px 0';
  const NET = {thread: 'Thread', wifi: 'Wi-Fi', ethernet: 'Ethernet'};
  let byDev = {};                    // house device id -> its Matter node (the owner's view)

  const keep = v => { byDev = Object.fromEntries((v.nodes || []).filter(n => n.device).map(n => [n.device, n])); };
  const nodeInfo = n => [[n.vendor, n.product].filter(Boolean).join(' '), NET[n.network] || '', n.bridge ? i18n('bridge') : '',
    n.available ? i18n('reachable') : i18n('not reachable')].filter(Boolean).join(' \u00b7 ');
  const notShown = n => (n.not_shown || []).length ? i18n('not shown yet: {0}', n.not_shown.join(', ')) : '';

  const serverLine = s => {
    const bad = !s.computer ? i18n('No controller yet: install the role Matter controller on a computer with Bluetooth (Computers and servers).')
      : !s.up ? i18n('The controller on {0} does not answer', s.computer) : '';
    const bits = s.up ? [i18n('on {0}', s.computer), s.info.bluetooth_enabled ? i18n('Bluetooth: new devices') : i18n('no Bluetooth: only codes shared from a phone app'),
      s.thread ? i18n('has the Thread network') : s.thread_kept ? i18n('no Thread network yet') : ''].filter(Boolean) : [];
    return `<div style="${CARD}"><div><span style="color:${bad ? 'var(--bad)' : 'var(--ok)'}">\u25cf</span> <b>${esc(i18n('Matter controller'))}</b></div>
      ${bad ? `<div class="bindcaption" style="color:var(--bad)">${esc(bad)}</div>` : `<div class="bindcaption">${esc(bits.join(' \u00b7 '))}</div>`}</div>`;
  };

  const nodes = box => {
    const draw = async () => {
      if(!box.isConnected) return;
      let v;
      try { v = await api('/api/matter'); } catch(e){ box.innerHTML = `<div class="bindcaption">${esc(e.message || e)}</div>`; return; }
      keep(v);
      const row = n => {
        const ns = notShown(n);
        return `<div style="${CARD}"><div><span style="color:${n.available ? 'var(--ok)' : 'var(--bad)'}">\u25cf</span> <b${n.device
            ? ` data-open="${esc(n.device)}" style="cursor:pointer"` : ''}>${esc(n.name)}</b> <span class="bindcaption">${esc(nodeInfo(n))}</span></div>
          ${ns ? `<div class="bindcaption">${esc(ns)}</div>` : ''}
          <div class="row" style="gap:8px;margin-top:6px;flex-wrap:wrap">${n.device ? `<button class="dlg-btn" data-open="${esc(n.device)}">${esc(i18n('Open its card'))}</button>` : ''}
            <button class="dlg-btn" data-unpair="${n.node_id}" style="color:var(--bad)">${esc(i18n('Unpair'))}</button></div></div>`;
      };
      box.innerHTML = serverLine(v.server) + (v.nodes.length ? v.nodes.map(row).join('')
        : `<div class="bindcaption">${esc(i18n('No Matter devices yet: Add -> Matter.'))}</div>`);
      box.querySelectorAll('[data-open]').forEach(b => b.onclick = () => Home.openDevice(b.dataset.open));
      box.querySelectorAll('[data-unpair]').forEach(b => b.onclick = async () => {
        const n = v.nodes.find(x => String(x.node_id) === b.dataset.unpair);
        if(!confirm(i18n('Unpair {0}? It leaves the house\'s Matter network; to bring it back, reset it and pair it again.', n.name))) return;
        b.disabled = true;
        try { await send('DELETE', '/api/matter/nodes/' + n.node_id); await Home.reloadDevices(); Home.toast(i18n('Unpaired')); }
        catch(e){ Home.toast(e.message || String(e)); }
        draw();
      });
    };
    draw();
  };

  // pairing: one field - the code of a new device (Bluetooth, near the controller) or a code shared from a phone app
  const pairForm = box => {
    let timer = null;
    const follow = async () => {
      if(!box.isConnected){ clearInterval(timer); return; }
      let p;
      try { p = await api('/api/matter/commission'); } catch(e){ return; }
      const out = box.querySelector('[data-out]');
      if(!out) return;
      out.style.color = p.state === 'done' ? 'var(--ok)' : p.state === 'failed' ? 'var(--bad)' : 'inherit';
      out.textContent = p.state === 'running' ? i18n('Pairing: {0} s. A new device: keep it near the controller\'s computer; this takes up to 5 minutes.', p.seconds)
        : p.state === 'done' ? i18n('Paired') : p.state === 'failed' ? i18n('Not paired: {0}', p.error || '') : '';
      const go = box.querySelector('[data-go]');
      if(go) go.disabled = p.state === 'running';
      if(p.state !== 'running'){
        clearInterval(timer);
        if(p.state === 'done' && p.device && !box.querySelector('[data-card]')){
          await Home.reloadDevices();
          out.insertAdjacentHTML('afterend', `<div class="row" style="margin-top:6px"><button class="dlg-btn" data-card>${esc(i18n('Open its card'))}</button></div>`);
          box.querySelector('[data-card]').onclick = () => Home.openDevice(p.device);
        }
      }
    };
    box.innerHTML = `<div class="bindcaption">${esc(i18n('A new device: the code under its QR code (11 digits) or the QR code\'s text (MT:...); put it near the controller\'s computer and switch it on. A device in a phone app: there "Share with another app" (Google Home, Apple Home), then its code here.'))}</div>
      <div class="row" style="margin-top:8px;gap:8px"><input data-code autocomplete="off" spellcheck="false" placeholder="${esc(i18n('Pairing code'))}" style="flex:1;min-width:0">
        <button class="dlg-btn primary" data-go>${esc(i18n('Pair'))}</button></div>
      <div data-out class="bindcaption" style="margin-top:8px"></div>`;
    const input = box.querySelector('[data-code]');
    box.querySelector('[data-go]').onclick = async () => {
      const c = input.value.trim();
      if(!c) return;
      const old = box.querySelector('[data-card]');
      if(old) old.parentElement.remove();
      try { await send('POST', '/api/matter/commission', {code: c}); input.value = ''; clearInterval(timer); timer = setInterval(follow, 2000); follow(); }
      catch(e){ Home.toast(e.message || String(e)); }
    };
    input.onkeydown = e => { if(e.key === 'Enter') box.querySelector('[data-go]').click(); };
    follow().then(() => { const p = box.querySelector('[data-go]'); if(p && p.disabled){ clearInterval(timer); timer = setInterval(follow, 2000); } });
  };
  const openPair = () => {
    Home.openModal(`<div class="head"><button class="closebtn" onclick="closeModal()">\u00d7</button><h2>${esc(i18n('Add a Matter device'))}</h2></div>
      <div class="body"><section data-matterpair></section></div>`, 'matter-pair');
    const b = document.querySelector('[data-matterpair]');
    if(b) pairForm(b);
  };

  HomePlugins.register({
    settings: [{subject: 'matter', title: 'Nodes', needs: ['GET /api/matter'], render: nodes},
               {subject: 'matter', title: 'Pairing', needs: ['POST /api/matter/commission'], render: pairForm}],
    addMenu: [{label: 'Matter', hint: 'A new device by its code, or one shared from a phone app', button: 'Pair', onClick: openPair}],
    onReload: [async () => { if(Home.can('GET /api/matter')) try { keep(await api('/api/matter')); } catch(e){ /* the Nodes tab says why */ } }],
    // a house device that is a Matter node: vendor, product, network, what is not shown yet
    modalSections: [{match: d => !!byDev[d.id], order: 5,
      html: d => { const n = byDev[d.id], ns = notShown(n);
        return `<h3>Matter</h3><div class="bindcaption">${esc(nodeInfo(n))}</div>${ns ? `<div class="bindcaption">${esc(ns)}</div>` : ''}`; }}],
  });
})();
