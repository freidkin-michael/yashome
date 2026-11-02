// History (owner only): one timeline of everything that happened in the house. A button in the header opens it. Texts are English source; i18n.json translates them.
(() => {
  const esc = Home.escapeHtml;
  const CAT = {states: '#5b8cff', commands: '#ffd24d', rules: '#3ddc84', errors: '#ff5c5c'};
  let owner = false, btn = null, rooms = [];
  const dev = id => Home.devices().find(d => d.id === id);
  const name = e => e.name || (dev(e.device) || {}).name || e.device || '';
  // a channel by its label; without one, its code as words ("color_temp" -> "color temp"), translated when the house knows the word
  const ctl = (id, code) => { const c = ((dev(id) || {}).controls || []).find(x => x.code === code); return (c && c.label && c.label !== '-' ? i18n(c.label) : '') || i18n(String(code || '').replace(/_/g, ' ')); };
  const fmt = (v, id) => v === true || v === 'ON' ? i18n('on') : v === false || v === 'OFF' ? i18n('off') : v == null ? '' : typeof v === 'number' ? String(Math.round(v * 100) / 100)
    : typeof v === 'object' ? Object.entries(v).map(([k, x]) => (k === 'state' ? '' : ctl(id, k) + ' ') + fmt(x, id)).join(', ') : i18n(String(v));   // a transport answers an object ({state: OFF}): in words
  const gword = (g, id) => typeof whenGesture === 'function' ? whenGesture(g, dev(id)) : String(g || '');      // the core's gesture words ("double press")
  const words = e => (e.parts || []).map(([t, a]) => i18n(t, ...a.map(x => i18n(x)))).join('');
  const span = s => s < 90 ? i18n('{0} s', s) : s < 5400 ? i18n('{0} min', Math.round(s / 60)) : i18n('{0} h', Math.round(s / 360) / 10);
  const source = s => !s ? '' : s === 'device' ? i18n('the device itself (a hand, a sensor)') : s === 'app' ? i18n('the app') : s === 'voice' ? i18n('voice') : s === 'watchdog' ? i18n('the watchdog')
    : s === 'timer' ? i18n('a timer') : s === 'log' ? i18n('the log') : s.startsWith('rule:') ? ruleSource(s.slice(5)) : s;
  // "rule:<device>/<code>=<gesture>": the rule's trigger in words, never its ids
  const ruleSource = t => {
    const m = t.match(/^(.*)\/([^/=]+)=(.*?)(?: \(test\))?$/);
    return m ? i18n('a rule: {0}', [name({device: m[1]}), gword(m[3], m[1])].filter(Boolean).join(' ')) : i18n('a rule');
  };
  const line = e => {
    const n = name(e);
    switch(e.kind){
      case 'state': return i18n('{0}: {1}: {2} \u2192 {3}', n, ctl(e.device, e.code), fmt(e.old, e.device), fmt(e.new, e.device));
      case 'command': return i18n('{0}: {1} set to {2}', n, ctl(e.device, e.code), fmt(e.new, e.device));
      case 'rule': return e.error ? i18n('Rule: when {0} {1} = {2}, {3} failed: {4}', n, ctl(e.device, e.code), gword(e.gesture, e.device), name({device: e.target}), e.error)
        : i18n('Rule: when {0} {1} = {2}, {3} is set to {4}', n, ctl(e.device, e.code), gword(e.gesture, e.device), name({device: e.target}), fmt(e.value, e.target));
      case 'offline': return i18n('{0} went offline', n);
      case 'online': return i18n('{0} is back online', n);
      case 'watchdog': return i18n('Watchdog: {0}', words(e));
      case 'issue': return i18n('Problem: {0}', words(e)) + (e.opened > 1 ? ' (' + i18n('came back {0} times', e.opened) + ')' : '');
      case 'solved': return i18n('Solved by itself after {0}: {1}', span(e.seconds || 0), words(e));
      default: return e.text || e.kind;
    }
  };
  const p2 = n => String(n).padStart(2, '0');
  const when = ts => { const d = new Date(ts * 1000), t = p2(d.getHours()) + ':' + p2(d.getMinutes()); return (new Date().toDateString() === d.toDateString() ? '' : p2(d.getDate()) + '.' + p2(d.getMonth() + 1) + ' ') + t; };       // 24 hours, whatever the browser's locale
  const probe = async () => { try { await Home.api('/api/history/info'); owner = true; } catch(e){ owner = false; } if(btn) btn.style.display = owner ? 'inline-flex' : 'none'; };

  const openIt = async (into) => {                     // into: a box of a page (Settings -> History); none: the dialog
    try { rooms = (await Home.api('/api/rooms')).map(r => r.name); } catch(e){ rooms = []; }
    const devs = Home.devices().slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
    const html = `<div style="padding:${into ? '0' : '14px 16px'};min-width:min(720px,92vw)" data-h="box">
      ${into ? '' : `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><h2 style="margin:0;font-size:19px">${esc(i18n('History'))}</h2><button class="dlg-btn" data-h="close">&times;</button></div>`}
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin:10px 0">
        <select class="askin" data-h="kind" style="width:auto;flex:1 1 130px"><option value="">${esc(i18n('Everything'))}</option><option value="states">${esc(i18n('States'))}</option><option value="commands">${esc(i18n('Commands'))}</option><option value="rules">${esc(i18n('Rules'))}</option><option value="errors">${esc(i18n('Errors'))}</option></select>
        <select class="askin" data-h="room" style="width:auto;flex:1 1 130px"><option value="">${esc(i18n('All rooms'))}</option>${rooms.map(r => `<option>${esc(r)}</option>`).join('')}</select>
        <select class="askin" data-h="device" style="width:auto;flex:1 1 160px"><option value="">${esc(i18n('All devices'))}</option>${devs.map(d => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('')}</select>
        <input class="askin" data-h="q" placeholder="${esc(i18n('Search'))}" style="width:auto;flex:2 1 160px">
        <label class="bindcaption">${esc(i18n('From'))} <input class="askin" type="datetime-local" data-h="since" style="width:auto"></label>
        <label class="bindcaption">${esc(i18n('To'))} <input class="askin" type="datetime-local" data-h="until" style="width:auto"></label>
      </div>
      <div data-h="list" style="max-height:56vh;overflow:auto;border-top:1px solid var(--line)"></div>
      <div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap"><button class="dlg-btn" data-h="more" style="display:none">${esc(i18n('Show more'))}</button>
        <span style="flex:1"></span><button class="dlg-btn" data-h="erase" style="color:#e5484d;border-color:#e5484d">${esc(i18n('Erase...'))}</button></div>
      <div data-h="sure" class="bindcaption" style="display:none;margin-top:8px"></div></div>`;
    if(into) into.innerHTML = html; else Home.openModal(html, '__history__');
    const md = into || document.getElementById('md'), q = k => md.querySelector(`[data-h="${k}"]`);
    let last = 0, more = false, timer = null;
    const params = () => {
      const p = new URLSearchParams();
      for(const k of ['kind', 'room', 'device', 'q']) if(q(k).value.trim()) p.set(k, q(k).value.trim());
      for(const k of ['since', 'until']) if(q(k).value) p.set(k, String(new Date(q(k).value).getTime() / 1000));
      return p;
    };
    const rowHtml = e => `<div data-dev="${esc(e.device)}" style="display:flex;gap:10px;padding:8px 2px;border-bottom:1px solid var(--line);${dev(e.device) ? 'cursor:pointer' : ''}">
      <span style="flex:none;width:9px;height:9px;border-radius:50%;margin-top:6px;background:${e.level === 'alert' ? '#ff5c5c' : e.level === 'warn' ? '#ffd24d' : CAT[e.cat] || '#999'}"></span>
      <div style="flex:1"><div>${esc(line(e))}</div><div class="bindcaption">${esc([when(e.ts), e.room, source(e.source)].filter(Boolean).join(' \u00b7 '))}</div></div></div>`;
    const load = async (append) => {
      const p = params(); p.set('limit', '50');
      if(append && last) p.set('before', String(last));
      let r; try { r = await Home.api('/api/history?' + p.toString()); } catch(e){ q('list').innerHTML = `<div class="bindcaption" style="padding:12px">${esc(e.message || e)}</div>`; return; }
      if(!append) q('list').innerHTML = '';
      q('list').insertAdjacentHTML('beforeend', r.events.map(rowHtml).join('') || (append ? '' : `<div class="bindcaption" style="padding:12px">${esc(i18n('Nothing here yet'))}</div>`));
      last = r.events.length ? r.events[r.events.length - 1].id : last; more = r.more; q('more').style.display = more ? 'inline-block' : 'none';
    };
    const reload = () => { clearTimeout(timer); timer = setTimeout(() => { last = 0; load(false); }, 250); };
    for(const k of ['kind', 'room', 'device', 'q', 'since', 'until']) q(k).addEventListener(k === 'q' ? 'input' : 'change', reload);
    q('more').onclick = () => load(true);
    if(q('close')) q('close').onclick = () => Home.closeModal();
    q('list').onclick = ev => { const row = ev.target.closest('[data-dev]'); if(row && dev(row.dataset.dev)) Home.openDevice(row.dataset.dev); };
    q('erase').onclick = () => {
      const p = params(), filtered = [...p.keys()].length > 0, s = q('sure');
      s.style.display = 'block';
      s.innerHTML = `${esc(i18n('Erase from the history:'))} ${filtered ? `<button class="dlg-btn" data-h="e1" style="color:#e5484d">${esc(i18n('what the filters show'))}</button> ` : ''}<button class="dlg-btn" data-h="e2" style="color:#e5484d">${esc(i18n('everything'))}</button> <button class="dlg-btn" data-h="e0">${esc(i18n('Cancel'))}</button>`;
      const done = async url => { try { const r = await Home.api(url, {method: 'DELETE'}); s.textContent = i18n('Erased: {0}', r.deleted); } catch(e){ s.textContent = e.message || String(e); } last = 0; load(false); };
      if(filtered) s.querySelector('[data-h="e1"]').onclick = () => done('/api/history?' + p.toString());
      s.querySelector('[data-h="e2"]').onclick = () => done('/api/history?all=1');
      s.querySelector('[data-h="e0"]').onclick = () => { s.style.display = 'none'; };
    };
    load(false);
  };

  HomePlugins.register({
    onReload: [probe],
    settings: [{subject: 'history', title: 'History', render: box => openIt(box)}],
    header: [{slot: 'conn', render: box => {
      btn = document.createElement('button');
      btn.className = 'dlg-btn'; btn.dataset.h = 'open'; btn.style.cssText = 'display:none'; btn.title = i18n('History'); btn.setAttribute('aria-label', i18n('History'));
      btn.innerHTML = '<span class="ico"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg></span><span class="btn-text">' + esc(i18n('History')) + '</span>';      // a phone: the clock alone
      btn.onclick = openIt;
      box.appendChild(btn);
    }}],
    help: [
    {id: 'history.open', screen: 'header', sel: '[data-h="open"]', title: 'History', text: 'One timeline of everything in the house: states, commands, rules and errors. Only the owner sees it.'},
    {id: 'history.kind', screen: 'history', screenName: 'History', sel: '[data-h="kind"]', title: 'What', text: 'Everything, or only states, commands, rules or errors.'},
    {id: 'history.room', screen: 'history', screenName: 'History', sel: '[data-h="room"]', title: 'Room', text: 'Only what happened in one room, by the room its device was in then.'},
    {id: 'history.device', screen: 'history', screenName: 'History', sel: '[data-h="device"]', title: 'Device', text: 'Only one device; a tap on a line opens its device.'},
    {id: 'history.search', screen: 'history', screenName: 'History', sel: '[data-h="q"]', title: 'Search', text: 'Words of the line: a name, a value, a rule.'},
    {id: 'history.since', screen: 'history', screenName: 'History', sel: '[data-h="since"]', title: 'From', text: 'Only what happened after this moment.'},
    {id: 'history.until', screen: 'history', screenName: 'History', sel: '[data-h="until"]', title: 'To', text: 'Only what happened before this moment.'},
    {id: 'history.more', screen: 'history', screenName: 'History', sel: '[data-h="more"]', title: 'Show more', text: 'The next 50 older lines.'},
    {id: 'history.erase', screen: 'history', screenName: 'History', sel: '[data-h="erase"]', title: 'Erase...', text: 'Asks first what to erase: what the filters show, or everything. Nothing goes without that second tap.'},
    {id: 'history.eraseshown', screen: 'history', screenName: 'History', sel: '[data-h="e1"]', title: 'what the filters show', text: 'Erases only the lines the filters show now.'},
    {id: 'history.eraseall', screen: 'history', screenName: 'History', sel: '[data-h="e2"]', title: 'everything', text: 'Erases the whole history of the house.'},
    {id: 'history.erasecancel', screen: 'history', screenName: 'History', sel: '[data-h="e0"]', title: 'Cancel', text: 'Nothing is erased.'},
    {id: 'history.close', screen: 'history', screenName: 'History', sel: '[data-h="close"]', title: 'Close', text: 'Closes the history; it goes on being written.'},
    ],
  });
})();
