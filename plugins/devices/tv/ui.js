// TV: its own card face (power, volume + mute, inputs), the "Appliances" tab, a 12 s hold while a TV wakes.
(() => {
  const SPK = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 7.97v8.05A4.5 4.5 0 0 0 16.5 12zM14 3.23v2.06a7 7 0 0 1 0 13.42v2.06A9 9 0 0 0 14 3.23z"/></svg>';
  const MUTED = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M16.5 12A4.5 4.5 0 0 0 14 7.97v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51A8.8 8.8 0 0 0 21 12a9 9 0 0 0-7-8.77v2.06A7 7 0 0 1 19 12zM4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06a8.99 8.99 0 0 0 3.69-1.81L19.73 21 21 19.73l-8.5-8.5L4.27 3zM12 4 9.91 6.09 12 8.18V4z"/></svg>';
  const css = document.createElement('style');
  css.textContent = `.acrow{display:flex;flex-wrap:wrap;gap:6px}
    .aclbl{font-size:12px;color:var(--dim);text-transform:uppercase;margin-top:4px}
    .acbtn{background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:10px;padding:8px 10px;
      cursor:pointer;font:inherit;font-size:14px;font-weight:500;min-height:44px;flex:1 0 auto}
    .acbtn.sel{background:var(--accent-fill);border-color:var(--accent-fill)}
    .acbtn{min-width:44px} .acbtn.ico{display:inline-flex;align-items:center;justify-content:center;gap:6px} .acbtn .acword{font-size:13px}
    .acmore.folded{display:none} .acmorebtn{align-self:stretch;margin-top:6px;width:100%}
    .acslide{display:flex;align-items:center;gap:12px;padding:2px 2px 0} .acslide input{flex:1}
    .acval{min-width:72px;text-align:right;font-size:34px;font-weight:700}
    .tvpowrow{display:flex;align-items:center;justify-content:space-between;margin-top:10px}
    .tvmute{flex:0 0 auto;font-size:18px;min-width:46px}`;
  document.head.append(css);
  const on = (id, code) => { const st = Home.state(id); return !!(st && st.online && st.values && st.values[code]); };
  const stop = ev => ev.stopPropagation();

  function render(c, d){
    c.append(Home.el('div', 'name', (Home.devIcon ? Home.devIcon(d) : '') + Home.escapeHtml(d.name)));
    c.append(Home.el('div', 'info', `<div class="meta">${Home.escapeHtml((Home.roomOf && Home.roomOf(d.id)) || i18n('TV'))}</div>`));
    // power: its name and the one power button of the dashboard (on = filled); the word is its tooltip
    const row = Home.el('div', 'tvpowrow', `<span>${Home.escapeHtml(i18n('Power'))}</span>`);
    const pw = Home.el('div', 'toggle');
    pw.dataset.tv = 'power';
    row.append(pw);
    // a TV that is off cannot hear the network: its IR remote (set up in its window) wakes it
    const irPower = () => {
      const ap = Home.devices().find(x => x.ir_appliance && x.ir_card === d.id);
      return ap && (ap.controls || []).find(x => x.kind === 'ir_press' && (x.button === 'power' || x.code === 'ir_power')) ? [ap, 'ir_power'] : null;
    };
    pw.onclick = ev => {
      stop(ev);
      const ir = !on(d.id, 'power') && irPower();
      if(ir){ const c2 = (ir[0].controls || []).find(x => x.kind === 'ir_press' && (x.button === 'power' || x.code === 'ir_power')); Home.sendControl(ir[0].id, c2.code, true); }
      Home.sendControl(d.id, 'power', !on(d.id, 'power'));
    };
    c.append(row);
    if((d.controls || []).some(x => x.code === 'volume')){
      c.append(Home.el('div', 'aclbl', Home.escapeHtml(i18n('Volume'))));
      const row = Home.el('div', 'acslide'); row.onclick = stop;
      const sl = document.createElement('input');
      sl.type = 'range'; sl.min = 0; sl.max = 100; sl.step = 1; sl.dataset.tv = 'vol';
      const vt = Home.el('div', 'acval', '\u2014'); vt.dataset.tv = 'volval';
      sl.oninput = () => { vt.textContent = sl.value; sl.dataset.drag = Date.now(); };
      sl.onchange = () => { sl.dataset.drag = Date.now(); Home.sendControl(d.id, 'volume', parseInt(sl.value, 10)); };
      const mb = Home.el('button', 'acbtn tvmute', SPK); mb.dataset.tv = 'mute'; mb.title = i18n('Mute');
      mb.onclick = ev => { stop(ev); Home.sendControl(d.id, 'mute', !on(d.id, 'mute')); };
      row.append(sl, vt, mb);
      c.append(row);
    }
    const ictl = (d.controls || []).find(x => x.code === 'input' && Array.isArray(x.range));
    if(ictl){
      const more = Home.el('div');
      more.append(Home.el('div', 'aclbl', Home.escapeHtml(ictl.label || i18n('Input'))));
      const row = Home.el('div', 'acrow'); row.onclick = stop;
      for(const opt of ictl.range){
        const b = Home.el('button', 'acbtn', Home.escapeHtml((ictl.labels || {})[opt] || opt));
        b.dataset.code = 'input'; b.dataset.value = opt;
        b.onclick = ev => { stop(ev); Home.sendControl(d.id, 'input', opt); };
        row.append(b);
      }
      more.append(row);
      // a phone: the inputs open on request
      if(window.matchMedia && window.matchMedia('(max-width: 640px)').matches){
        more.classList.add('acmore', 'folded');
        const mb = Home.el('button', 'dlg-btn acmorebtn', i18n('More'));
        mb.onclick = ev => { stop(ev); const f = more.classList.toggle('folded'); mb.textContent = i18n(f ? 'More' : 'Less'); };
        c.append(mb);
      }
      c.append(more);
    }
  }

  function update(c, d, st){
    const v = (st && st.online && st.values) || {};
    c.classList.toggle('any-on', !!v.power);
    const pw = c.querySelector('[data-tv="power"]');
    if(pw){
      pw.classList.toggle('on', !!v.power);
    }
    const sl = c.querySelector('[data-tv="vol"]'), vt = c.querySelector('[data-tv="volval"]');
    if(sl && v.volume != null && !(sl.dataset.drag && Date.now() - sl.dataset.drag < 4000)){   // not under the finger
      sl.value = v.volume;
      if(vt) vt.textContent = v.volume < 0 ? '\u2014' : v.volume;
    }
    const mb = c.querySelector('[data-tv="mute"]');
    if(mb){ mb.classList.toggle('sel', !!v.mute); mb.innerHTML = v.mute ? MUTED : SPK; mb.title = i18n(v.mute ? 'Sound on' : 'Mute'); }
    c.querySelectorAll('.acbtn[data-code]').forEach(b => b.classList.toggle('sel', b.dataset.value === String(v[b.dataset.code])));
  }

  // Settings -> TVs: one card per TV, its kind in the title, that kind's settings inside (the transports' deviceSettings)
  async function tvPage(box){
    const E = Home.escapeHtml, provs = HomePlugins.deviceSettings.filter(p => p.category === 'tv');
    const reload = () => tvPage(box);
    box.innerHTML = `<div class="bindcaption">${E(i18n('Loading\u2026'))}</div>`;
    const lists = await Promise.all(provs.map(p => Promise.resolve().then(() => p.list()).catch(e => ({error: e.message || String(e)}))));
    box.innerHTML = '';
    const seen = new Set();
    if(!document.getElementById('tv-compact')) document.head.insertAdjacentHTML('beforeend', `<style id="tv-compact">
      .tvset{border-top:1px solid var(--line);padding:4px 0}.tvset:first-child{border-top:0}
      .tvset .row{padding:4px 0;min-height:0;border-top:0}.tvset .rlabel .bindcaption{font-style:normal;margin:0;font-size:12px}</style>`);
    const irButtons = (box, remote, appliance) => {             // Keys: that remote's own window; Change remote: which remote and blaster
      const open = (win, id) => HomePlugins.openWindow(win, id) || openSettings('ir');
      box.querySelectorAll('[data-tvir]').forEach(b => { b.onclick = () => b.dataset.tvir === 'keys' ? open('ir.remote', remote) : open('ir.appliance', appliance); });
    };
    const card = () => { const sc = Home.el('section', 'tvset'); sc.innerHTML = '<div></div>'; box.append(sc); return sc.lastElementChild; };
    const head = (into, title, id, plug, ip) => {                // the first row's label: the name over where, plug-in, address, state
      const lb = into.querySelector('.row .rlabel'), was = lb ? lb.innerHTML.trim() : '';
      const meta = E([Home.roomOf(id), plug, ip].filter(Boolean).join(' \u00b7 ')) + (was ? ' \u00b7 ' + was : '');
      const html = `<b style="font-size:16px">${E(title)}</b><br><span class="bindcaption">${meta}</span>`;
      if(lb){ lb.classList.remove('bindcaption'); lb.innerHTML = html; }
      else into.insertAdjacentHTML('afterbegin', `<div class="row"><span class="rlabel">${html}</span></div>`);
    };
    provs.forEach((p, i) => {
      const l = lists[i];
      if(l && l.error){
        const e = card(); e.innerHTML = `<div class="row"><span class="rlabel bindcaption">${E(l.error)}</span></div>`;
        head(e, i18n(p.label), '', p.transport, ''); return;
      }
      for(const t of l || []){
        seen.add(t.id);
        const dev = Home.devices().find(d => d.id === t.id) || {};                       // the house's name is the one shown
        const into = card();
        HomePlugins.safe(p.card, t, into, reload);
        head(into, dev.name || t.name || t.id, t.id, p.transport, t.live_host || t.host);
        const ir = (dev.parts || {}).ir;                                                  // its IR remote, a part of the same TV
        if(ir) into.insertAdjacentHTML('beforeend', `<div class="row"><span class="rlabel bindcaption">${E(i18n('IR remote: {0}, through {1}', ir.remote_name || ir.remote || '\u2014', (Home.devices().find(x => x.id === ir.blaster || x.ir_blaster === ir.blaster) || {}).name || ir.blaster || '\u2014'))}</span>
          <span style="display:flex;gap:6px"><button class="dlg-btn" data-tvir="keys">${E(i18n('Keys'))}</button><button class="dlg-btn" data-tvir="bind">${E(i18n('Change remote'))}</button></span></div>`);
        irButtons(into, ir.remote, ir.appliance);
      }
    });
    for(const d of Home.devices().filter(d => d.category === 'tv' && !seen.has(d.id))){       // a TV no transport sets up here: IR
      const b = card();
      b.innerHTML = `<div class="row"><span class="rlabel bindcaption">${E(i18n(d.transport === 'ir' ? 'Its remote and keys are on the IR page.' : 'Nothing to set up here.'))}</span>
        ${d.transport === 'ir' ? `<button class="dlg-btn" data-tvir="1">${E(i18n('Open'))}</button>` : ''}</div>`;
      head(b, d.name, d.id, d.transport || 'ir', d.host);
      irButtons(b, null, d.transport === 'ir' ? d.id : null);
    }
    if(!box.querySelector('.tvset')) box.insertAdjacentHTML('afterbegin', `<div class="bindcaption">${E(i18n('No TV yet.'))}</div>`);
    const foot = Home.el('section', '');
    box.append(foot);
    addWizard(foot, provs, reload);
    for(const p of provs) if(typeof p.actions === 'function') HomePlugins.safe(p.actions, foot, reload);
  }

  // Add a TV: the TVs a sweep of the LAN finds (on each press only), or one address; the house names the platform itself. A platform with a plug-in
  // opens that plug-in's form (deviceSettings add); one without is named and sent to the IR page; no answer = off.
  const PLATFORM = {tizen: 'Samsung (Tizen)', webos: 'LG (webOS)', androidtv: 'Android TV / Google TV', roku: 'Roku TV',
                    vidaa: 'Hisense (VIDAA)', philips: 'Philips'};
  function addWizard(foot, provs, reload){
    const E = Home.escapeHtml, J = {'Content-Type': 'application/json'};
    const adders = Object.fromEntries(provs.filter(p => typeof p.add === 'function').map(p => [p.transport, p]));
    const w = Home.el('div', '');
    w.innerHTML = `<div class="row" style="justify-content:flex-end"><button class="dlg-btn primary" data-tvw="open">${E(i18n('Add a TV'))}</button></div>
      <div data-tvw="body" hidden>
        <div class="row"><span class="rlabel bindcaption" data-tvw="msg"></span><button class="dlg-btn" data-tvw="again">${E(i18n('Search again'))}</button></div>
        <div data-tvw="list"></div>
        <div class="row"><span class="rlabel">${E(i18n('Not in the list? Its address'))}</span>
          <span style="display:flex;gap:6px"><input class="askin" data-tvw="host" placeholder="${E(i18n('IP address'))}" style="width:150px">
          <button class="dlg-btn" data-tvw="check">${E(i18n('Check'))}</button></span></div>
        <div data-tvw="one"></div><div class="bindcaption" data-tvw="say"></div><div data-tvw="form"></div></div>`;
    foot.append(w);
    const q = k => w.querySelector(`[data-tvw="${k}"]`), say = m => { q('say').textContent = m; };
    const row = r => {
      const what = r.platform === 'androidtv' && r.maker && r.maker !== 'Android TV' ? `${r.maker} \u00b7 ${PLATFORM.androidtv}` : (PLATFORM[r.platform] || r.maker);
      let act;
      if(r.known) act = `<span class="bindcaption">${E(i18n('Already added'))}</span>`;
      else if(adders[r.platform] && r.supported) act = `<button class="dlg-btn primary" data-tvw-add="${E(r.host)}">${E(i18n('Add'))}</button>`;
      else if(r.platform) act = `<span class="bindcaption">${E(i18n('Not over the network yet: add it by IR'))}</span><button class="dlg-btn" data-tvw-ir="1">${E(i18n('IR'))}</button>`;
      else act = `<span class="bindcaption">${E(i18n('No answer: switch the TV on and try again'))}</span>`;
      return `<div class="row"><span class="rlabel">${E(r.house_name || r.name || r.model || r.host)}<br><span class="bindcaption">${E([what, r.host].filter(Boolean).join(' \u00b7 '))}</span></span>
        <span style="display:flex;gap:6px;align-items:center">${act}</span></div>`;
    };
    const wire = (box, rows) => {
      box.querySelectorAll('[data-tvw-add]').forEach(b => { b.onclick = () => {
        const r = rows.find(x => x.host === b.dataset.tvwAdd); say('');
        HomePlugins.safe(adders[r.platform].add, r, q('form'), reload, say);
      }; });
      box.querySelectorAll('[data-tvw-ir]').forEach(b => { b.onclick = () => openSettings('ir'); });
    };
    const search = async () => {
      q('msg').textContent = i18n('Searching the network (a few seconds)...'); q('list').innerHTML = ''; q('again').disabled = true;
      try {
        const r = await Home.api('/api/tv/search', {method: 'POST'});
        q('msg').textContent = r.tvs.length ? '' : i18n('No TV answered on the network: a TV that is off does not answer');
        q('list').innerHTML = r.tvs.map(row).join(''); wire(q('list'), r.tvs);
      } catch(e){ q('msg').textContent = e.message || String(e); }
      q('again').disabled = false;
    };
    q('open').onclick = () => { const b = q('body'); b.hidden = !b.hidden; if(!b.hidden) search(); };
    q('again').onclick = search;
    q('check').onclick = async () => {
      const host = q('host').value.trim(); say(''); q('one').innerHTML = '';
      if(!host){ say(i18n('Type the TV\'s address')); return; }
      say(i18n('Checking...'));
      try {
        const r = await Home.api('/api/tv/probe', {method: 'POST', headers: J, body: JSON.stringify({host})});
        say(r.platform || r.known ? '' : i18n('{0} did not answer: switch the TV on and press Check again', host));
        if(r.platform || r.known){ q('one').innerHTML = row(r); wire(q('one'), [r]); }       // a TV of the house: "Already added", even when off
      } catch(e){ say(e.message || String(e)); }
    };
  }

  // The TV's one remote, the same for every TV: each key over the network when the TV has it (its tv_key controls, the
  // media keys), else through its IR remote (a part of the TV or an IR appliance on its card); a key neither has is not drawn.
  // Power and the inputs stay in the core's rows above (power: the network, or the IR on key the plug-in knows).
  const MEDIA_KEY = {play: 'play', pause: 'pause', stop: 'stop', rewind: 'rew', fast_forward: 'ff'};
  function remoteKeys(d){
    const out = [], have = new Set(), PW = window.PW || {};
    for(const c of d.controls || []) if(c.kind === 'tv_key' && c.key && !have.has(c.key)){
      have.add(c.key); out.push({code: 'n|' + c.code, icon: c.key, label: i18n(c.label || c.key)});
    }
    const media = (d.controls || []).find(c => c.code === 'media' && Array.isArray(c.range));
    for(const v of (media && media.range) || []) if(MEDIA_KEY[v] && !have.has(MEDIA_KEY[v])){
      have.add(MEDIA_KEY[v]); out.push({code: 'm|' + v, icon: MEDIA_KEY[v], label: i18n(v)});
    }
    const net = code => (d.controls || []).some(c => c.code === code), netPower = net('power'), netInput = net('input');
    if(net('mute')) have.add('mute');                                               // its switch in the rows above
    const aps = typeof window.irHostedAppliances === 'function' ? window.irHostedAppliances(d.id) : [];
    for(const ap of aps){
      const hide = ap.ir_hide || [];
      for(const c of (ap.controls || []).filter(x => x.kind === 'ir_press')){
        const label = c.group && c.label && c.label.startsWith(c.group + ' \u00b7 ') ? c.label.slice(c.group.length + 3) : (c.label || c.code);
        const k = PW.irKey ? PW.irKey(c.code, label, c.icon) : '';
        if(k && have.has(k)) continue;                                              // the network has it
        if(netPower && ['power', 'on', 'off'].includes(k)) continue;                // the power row above
        if(netInput && /hdmi|source|input/i.test(c.code + ' ' + (c.button || ''))) continue;   // the inputs above
        if(PW.irGroup && hide.includes(PW.irGroup(k, false))) continue;            // hidden in the IR tab
        if(k) have.add(k);
        out.push({code: 'i|' + ap.id + '|' + c.code, icon: c.icon, label});
      }
    }
    return out;
  }
  function drawRemote(box, d){
    box.innerHTML = '';
    const keys = remoteKeys(d), PW = window.PW || {};
    if(!keys.length){ box.closest('section') && (box.closest('section').hidden = true); return; }
    const html = PW.remoteHtml ? PW.remoteHtml(keys, {all: true}) : '';
    const wrap = Home.el('div', 'acpanel', html || '');
    if(!html){                                          // too few known keys for the layout: a row of buttons
      const row = Home.el('div', 'acrow');
      for(const k of keys){ const b = Home.el('button', 'acbtn', Home.escapeHtml(k.label)); b.dataset.code = k.code; row.append(b); }
      wrap.append(row);
    }
    for(const b of wrap.querySelectorAll('button[data-code]')) b.onclick = ev => {
      stop(ev);
      const [how, a, b2] = b.dataset.code.split('|');
      b.style.opacity = '.45';
      const sent = how === 'n' ? Home.sendControl(d.id, a, true) : how === 'm' ? Home.sendControl(d.id, 'media', a) : Home.sendControl(a, b2, true);
      Promise.resolve(sent).finally(() => { b.style.opacity = ''; });
    };
    box.append(wrap);
  }

  HomePlugins.register({
    modalSections: [{
      match: d => d.category === 'tv', order: -1,      // above the IR remote's line
      hideCodes: ['media'],                              // its keys are on the remote
      html: () => `<h3>${Home.escapeHtml(i18n('Remote'))}</h3><div data-tvrc></div>`,
      wire(box, d){ const b = box.querySelector('[data-tvrc]'); if(b) drawRemote(b, d); },
    }],
    settings: [{subject: 'tv', title: 'TVs', render: tvPage}],
    tabs: [{id: 'tech', label: 'Appliances', cats: ['tv', 'media'], after: 'button'}],
    cardFaces: [{match: d => d.category === 'tv', render, update}],
    help: [{id: 'tv.add', screen: 'settings:tv', sel: '[data-tvw="open"]', title: 'Add a TV',
            text: 'Finds the TVs on the network and tells their make itself; one that is not listed: type its address. A TV that is off does not answer: switch it on first.'}],
    holds: [(d, code, value) => d && (d.transport === 'webos' || d.wakes) && code === 'power' && value === true ? 12000 : 0],
  });
})();
