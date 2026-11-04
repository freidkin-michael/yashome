// Settings -> Devices -> Thread (owner only): the network (name, channel, the main border router), each border router and its
// state, the nodes (role, parent, best link, a router's links; Add to the house or its card) and pairing (PSKd and an optional EUI-64). Add ->
// Thread opens the same pairing. A node that is a Thread router gets a mark on the home map (window.MapMarks).
(() => {
  const esc = Home.escapeHtml;
  const api = (path, opts) => Home.api(path, opts);
  const send = (method, path, body) => api(path, {method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  const CARD = 'border:1px solid var(--line, rgba(148,163,184,.25));border-radius:12px;padding:10px 12px;margin:8px 0';
  const ROLE = {leader: 'leader', router: 'router', child: 'child', detached: 'detached', disabled: 'off'};
  let marks = {}, byDev = {};        // byDev: house device id -> its Thread node (the owner's view)

  const routerLine = (r, v) => {
    const s = r.status || {}, n = r.node || {};
    const bad = !r.rest ? i18n('its border router does not answer') : !r.rcp ? i18n('no radio (RCP)') : r.net === 'other' ? i18n('on another Thread network') : r.why ? i18n(r.why) : '';
    const bits = [n.state ? i18n(ROLE[n.state] || n.state) : '', s.trel_peers !== undefined && v.routers.length > 1 ? i18n('TREL peers: {0}', s.trel_peers) : '',
                  s.leaderweight ? i18n('leader weight {0}', s.leaderweight) : '',
                  r.restarted ? i18n('restarted by the house at {0}', new Date(r.restarted * 1000).toTimeString().slice(0, 5)) : ''].filter(Boolean);
    return `<div style="${CARD}"><div><span style="color:${bad ? 'var(--bad)' : 'var(--ok)'}">\u25cf</span> <b>${esc(r.name)}</b>${r.main ? ` <span class="bindcaption">(${esc(i18n('main'))})</span>` : ''}</div>
      ${bad ? `<div class="bindcaption" style="color:var(--bad)">${esc(bad)}</div>` : ''}<div class="bindcaption">${esc(bits.join(' \u00b7 '))}</div></div>`;
  };

  const network = box => {
    const draw = async () => {
      if(!box.isConnected) return;
      let v;
      try { v = await api('/api/thread'); } catch(e){ box.innerHTML = `<div class="bindcaption">${esc(e.message || e)}</div>`; return; }
      const net = v.network;
      box.innerHTML = `
        <div class="row" style="flex-wrap:wrap;gap:8px"><span class="rlabel">${esc(i18n('Network'))}<br><span class="bindcaption">${esc(net.kept
            ? [net.name, i18n('channel {0}', net.channel), net.extpanid].filter(Boolean).join(' \u00b7 ') : i18n('not formed yet: the main border router forms it'))}</span></span></div>
        ${net.kept ? '' : `<div class="row" style="flex-wrap:wrap;gap:8px"><span class="rlabel">${esc(i18n('A new network'))}</span><span class="rval" style="display:flex;gap:6px">
            <input data-name value="${esc(net.want.name)}" maxlength="16" style="width:120px"><select data-ch>${Array.from({length: 16}, (_, i) => i + 11).map(c =>
              `<option${c === net.want.channel ? ' selected' : ''}>${c}</option>`).join('')}</select><button class="dlg-btn" data-save>${esc(i18n('Save'))}</button></span></div>`}
        <div class="row" style="flex-wrap:wrap;gap:8px"><span class="rlabel">${esc(i18n('Main border router'))}<br><span class="bindcaption">${
            esc(i18n('it leads the network and lets new devices in'))}</span></span><span class="rval">${v.routers.length ? `<select data-main>${v.routers.map(r =>
              `<option value="${esc(r.id)}"${r.main ? ' selected' : ''}>${esc(r.name)}</option>`).join('')}</select>` : ''}</span></div>
        ${v.routers.map(r => routerLine(r, v)).join('') || `<div class="bindcaption">${esc(i18n('No border router yet: install the role Thread border router on a computer with a Thread radio (Computers and servers).'))}</div>`}`;
      const m = box.querySelector('[data-main]');
      if(m) m.onchange = async () => { try { await send('PATCH', '/api/thread', {main: m.value}); } catch(e){ Home.toast(e.message || String(e)); } draw(); };
      const sv = box.querySelector('[data-save]');
      if(sv) sv.onclick = async () => {
        try { await send('PATCH', '/api/thread', {name: box.querySelector('[data-name]').value, channel: +box.querySelector('[data-ch]').value}); Home.toast(i18n('Saved')); }
        catch(e){ Home.toast(e.message || String(e)); }
        draw();
      };
    };
    draw();
  };

  // a node's Thread line: role, parent, its link and a router's links (the Nodes tab and the device window)
  const nodeInfo = n => [n.border_router ? i18n('border router') : i18n(ROLE[n.role] || n.role || ''), n.parent ? i18n('via {0}', n.parent) : '',
    n.lq !== null && n.lq !== undefined ? i18n('link {0}/3', n.lq) : '', typeof n.rssi === 'number' ? n.rssi + ' dBm' : ''].filter(Boolean).join(' \u00b7 ');
  const nodeLinks = n => (n.links || []).length
    ? i18n('links: {0}', n.links.map(k => `${k.node} ${k.rssi} dBm${k.lq !== null && k.lq !== undefined ? ` ${k.lq}/3` : ''}`).join(', ')) : '';
  const keep = v => { byDev = Object.fromEntries((v.nodes || []).filter(n => n.device && !n.border_router).map(n => [n.device, n])); };

  // "Add to the house": the node's ESPHome device by its host name, under the name asked; then the node finds it
  const addToHouse = async n => {
    const nm = await Home.askText(i18n('Name in the house'), n.name);
    if(nm === null) return;
    let found;
    try { found = ((await api('/api/esphome/discover')).nodes || []).find(x => x.node === n.name); } catch(e){ Home.toast(e.message || String(e)); return; }
    if(!found){ Home.toast(i18n('"{0}" does not announce itself over MQTT: is it on?', n.name)); return; }
    try {
      await send('POST', '/api/esphome/add', {node: n.name, name: nm.trim(), category: Home.esphomeCategory(found), force: found.alive !== 'online'});
      await Home.reloadDevices();
      await send('POST', '/api/thread/node', {node: n.eui || n.ext, device: ''});     // matched by its host name again: the new device
      Home.toast(i18n('Added'));
    } catch(e){ Home.toast(e.message || String(e)); }
  };

  const nodes = box => {
    const draw = async () => {
      if(!box.isConnected) return;
      let v;
      try { v = await api('/api/thread'); } catch(e){ box.innerHTML = `<div class="bindcaption">${esc(e.message || e)}</div>`; return; }
      keep(v);
      const all = Home.devices() || [];
      const dev = id => all.find(d => d.id === id);
      const row = (n, i) => {
        const d = n.device && dev(n.device);
        const host = n.name || n.ext, nm = d ? d.name || d.id : host;    // the house name; the host name too when it differs
        const title = `<b${d ? ` data-open="${esc(d.id)}" style="cursor:pointer"` : ''}>${esc(nm)}</b>${nm !== host ? ` <span class="bindcaption">${esc(host)}</span>` : ''}`;
        const links = nodeLinks(n);
        const act = n.border_router ? '' : d ? `<button class="dlg-btn" data-open="${esc(d.id)}">${esc(i18n('Open its card'))}</button>`
          : n.name ? `<button class="dlg-btn primary" data-add="${i}">${esc(i18n('Add to the house'))}</button>` : '';
        // the text on the left, its one button on the right: a node is two short lines, not a tall box
        return `<div style="${CARD};display:flex;align-items:center;gap:10px"><div style="flex:1;min-width:0">
          <div>${title} <span class="bindcaption">${esc(nodeInfo(n))}</span></div>${links ? `<div class="bindcaption">${esc(links)}</div>` : ''}</div>
          ${act ? `<div style="flex:none">${act}</div>` : ''}</div>`;
      };
      box.innerHTML = v.nodes.length ? v.nodes.map(row).join('')
        : `<div class="bindcaption">${esc(i18n('No Thread nodes seen yet (the list is read every 5 minutes).'))}</div>`;
      box.querySelectorAll('[data-open]').forEach(b => b.onclick = () => Home.openDevice(b.dataset.open));
      box.querySelectorAll('[data-add]').forEach(b => b.onclick = async () => { b.disabled = true; await addToHouse(v.nodes[+b.dataset.add]); draw(); });
    };
    draw();
  };

  // pairing: the PSKd from the device's label (and its EUI-64 when known), 3 minutes on the main border router
  const pairForm = box => {
    let timer = null;
    const follow = async () => {
      if(!box.isConnected){ clearInterval(timer); return; }
      let p;
      try { p = await api('/api/thread/pair'); } catch(e){ return; }
      const out = box.querySelector('[data-out]');
      if(!out) return;
      out.style.color = p.state === 'joined' ? 'var(--ok)' : p.state === 'open' ? 'inherit' : 'var(--bad)';
      out.textContent = p.state === 'open' ? i18n('Waiting for the device: {0} s left. Switch it on (or restart it) now.', p.left)
        : p.state === 'joined' ? i18n('Joined') + (p.node ? ': ' + p.node : '') : p.state === 'timeout' ? i18n('No device joined in 3 minutes')
        : p.state === 'failed' ? i18n('The border router could not let it in') : '';
      const done = (p.paired || []).map(x => x.ok ? i18n('Paired: {0}', x.node) : i18n('{0} did not pair: {1}', x.node, x.error || '')).join('; ');
      if(done) out.textContent += (out.textContent ? ' \u00b7 ' : '') + done;
      if(p.state !== 'open' && Date.now() - since > 330000) clearInterval(timer);     // the house pairs up to 2 min after the window
    };
    let since = 0;
    const go = async body => {
      try { await send('POST', '/api/thread/pair', body); since = Date.now(); clearInterval(timer); timer = setInterval(follow, 3000); follow(); }
      catch(e){ Home.toast(e.message || String(e)); }
    };
    box.innerHTML = `<div class="bindcaption">${esc(i18n('Hold the button on the device for 5 seconds until it blinks blue, then press Let it in. The house lets it into the network for 3 minutes and pairs it itself.'))}</div>
      <div class="row" style="margin-top:8px"><button class="dlg-btn primary" data-go>${esc(i18n('Let it in'))}</button></div>
      <div data-out class="bindcaption" style="margin-top:8px"></div>`;
    box.querySelector('[data-go]').onclick = () => go({});
    follow();
  };
  const openPair = () => {
    Home.openModal(`<div class="head"><button class="closebtn" onclick="closeModal()">\u00d7</button><h2>${esc(i18n('Add a Thread device'))}</h2></div>
      <div class="body"><section data-pairbox></section></div>`, 'thread-pair');
    const b = document.querySelector('[data-pairbox]');
    if(b) pairForm(b);
  };

  // the home map asks every function in window.MapMarks for a device's mark
  (window.MapMarks = window.MapMarks || []).push(id => marks[id] === 'router' ? {glyph: 'T', title: i18n('Thread router')} : null);

  HomePlugins.register({
    settings: [{subject: 'thread', title: 'Network', needs: ['GET /api/thread'], render: network},
               {subject: 'thread', title: 'Nodes', needs: ['GET /api/thread'], render: nodes},
               {subject: 'thread', title: 'Pairing', needs: ['POST /api/thread/pair'], render: pairForm}],
    addMenu: [{label: 'Thread', hint: 'Lets a new Thread device in by its PSKd', button: 'Pairing', onClick: openPair}],
    onReload: [async () => { try { marks = await api('/api/thread/marks'); } catch(e){ marks = {}; } },
               async () => { if(Home.can('GET /api/thread')) try { keep(await api('/api/thread')); } catch(e){ /* the Nodes tab says why */ } }],
    // a house device that is a Thread node: its role, parent and links in its window
    modalSections: [{match: d => !!byDev[d.id], order: 5,
      html: d => { const n = byDev[d.id], l = nodeLinks(n);
        return `<h3>Thread</h3><div class="bindcaption">${esc([n.name, nodeInfo(n)].filter(Boolean).join(' \u00b7 '))}</div>${l ? `<div class="bindcaption">${esc(l)}</div>` : ''}`; }}],
  });
})();
