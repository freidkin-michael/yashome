// Home health (owner only): a "Health" button in the header beside History, its dot and border coloured by the worst open issue; it opens the report. Texts are English source; i18n.json translates them.
(() => {
  const esc = Home.escapeHtml;
  const COLOUR = {green: '#3ddc84', yellow: '#ffd24d', orange: '#ff9a3d', red: '#ff5c5c'};
  const WORDS = {green: 'Everything is fine', yellow: 'Needs a look', orange: 'The house is trying again', red: 'Needs attention'};
  let H = {status: 'green', count: 0, issues: [], fixes: [], resolved: [], slow: [], digest: {}}, owner = false, badge = null, last = 0, busy = false, more = {}, advOpen = false;
  const hist = (sec, title, list, at, mk, k) => {             // the last 24 h; older rows behind "More N"
    const now = Date.now() / 1000, day = list.filter(x => now - at(x) < 86400), older = list.length - day.length;
    const rows = (more[k] ? list : day).map(mk);
    if(!more[k] && older) rows.push(`<button class="dlg-btn" data-more="${k}" style="margin-top:6px">${esc(i18n('More {0}', older))}</button>`);
    return sec(title, rows);
  };
  const text = p => (p.parts || []).map(([t, a]) => i18n(t, ...a.map(x => i18n(x)))).join('') || p.text || '';      // an argument that is a known word (home, on) is translated too
  const p2 = n => String(n).padStart(2, '0');
  const when = ts => { const d = new Date(ts * 1000), t = p2(d.getHours()) + ':' + p2(d.getMinutes()); return (new Date().toDateString() === d.toDateString() ? '' : p2(d.getDate()) + '.' + p2(d.getMonth() + 1) + ' ') + t; };       // 24 hours, whatever the browser's locale
  const span = s => s < 90 ? i18n('{0} s', s) : s < 5400 ? i18n('{0} min', Math.round(s / 60)) : i18n('{0} h', Math.round(s / 360) / 10);
  const paint = () => {
    if(!badge) return;
    const c = COLOUR[H.status] || COLOUR.green;
    badge.innerHTML = `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${c}"></span><span class="btn-text">${esc(i18n('Health'))}</span>${H.status === 'green' ? '' : ' ' + esc(String(H.count || '!'))}`;      // a phone: the dot and the count
    badge.style.borderColor = H.status === 'green' ? '' : c;
    badge.style.display = owner ? 'inline-flex' : 'none';
    badge.title = i18n('Home health') + ': ' + i18n(WORDS[H.status] || WORDS.green);
    badge.setAttribute('aria-label', badge.title);
  };
  const load = async () => {
    if(busy) return;
    busy = true;
    try { H = await Home.api('/api/watchdog'); owner = true; } catch(e){ owner = false; } finally { busy = false; }
    last = Date.now(); paint();
    if(Home.modalId() === MODAL && open) open();
  };
  const MODAL = '__watchdog__';
  let open = null;
  const show = () => {
    more = {}; advOpen = false;
    Home.openModal('<div data-w="box"></div>', MODAL, {onClose: () => { open = null; }});
    const box = document.getElementById('md').querySelector('[data-w="box"]');
    open = () => {
      const w = H.status === 'green' ? i18n(WORDS.green) : i18n('Open issues') + ': ' + H.count;
      const sec = (title, rows) => rows.length ? `<h3 style="margin:18px 0 6px;font-size:15px">${esc(i18n(title))}</h3>${rows.join('')}` : '';
      const row = (dot, t, sub, after = '') => `<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid var(--line)"><span style="flex:none;width:10px;height:10px;border-radius:50%;margin-top:5px;background:${dot}"></span><div style="flex:1">${t}<div class="bindcaption">${esc(sub)}</div>${after}</div></div>`;
      const d = H.digest || {};
      box.innerHTML = `<div style="padding:12px 16px;max-width:760px">
        <div style="display:flex;align-items:center;gap:12px"><span style="flex:none;width:18px;height:18px;border-radius:50%;background:${COLOUR[H.status]}"></span><h2 style="margin:0;font-size:20px;flex:1">${esc(i18n('Home health'))}: ${esc(w)}</h2><button class="dlg-btn" data-w="close">&times;</button></div>
        ${sec('Open issues', H.issues.map(i => row(i.level === 'alert' ? COLOUR.red : i.level === 'retry' ? COLOUR.orange : i.level === 'info' ? 'var(--dim)' : COLOUR.yellow, esc(text(i)) + (i.link ? ` <button class="dlg-btn" data-link="${esc(i.link)}">${esc(i18n('Open'))}</button>` : ''),
          `${i18n('since {0}', when(i.since))}${i.opened > 1 ? ' \u00b7 ' + i18n('came back {0} times', i.opened) : ''}${i.hint ? ' \u00b7 ' + i18n(i.hint[0]) : ''}`,
          i.resend ? `<div><button class="dlg-btn" data-resend="${esc(i.key)}" style="margin-top:6px">${esc(i18n('Send again'))}</button></div>` : '')))}
        ${hist(sec, 'Fixed by the house', H.fixes, f => f.ts, f => row(COLOUR.green, esc(text(f)), when(f.ts)), 'fixes')}
        <details data-w="adv" style="margin-top:18px"${advOpen ? ' open' : ''}><summary>${esc(i18n('Advanced'))}</summary>
        ${hist(sec, 'Answered slowly', H.slow || [], x => x.ts, x => row(COLOUR.yellow, esc(text(x)), when(x.ts)), 'slow')}
        ${hist(sec, 'Solved by themselves', H.resolved, r => r.until, r => row(COLOUR.green, esc(text(r)), `${when(r.until)} \u00b7 ${i18n('lasted {0}', span(r.seconds))}${r.opened > 1 ? ' \u00b7 ' + i18n('came back {0} times', r.opened) : ''}`), 'resolved')}
        ${sec('Observations', (H.notes || []).map(n => row(COLOUR.green, `<b>${esc(n.title)}</b><pre style="white-space:pre-wrap;margin:4px 0 0;font:12px/1.4 monospace">${esc(n.text)}</pre>`, when(n.ts))))}
        ${d.ts ? `<h3 style="margin:18px 0 6px;font-size:15px">${esc(i18n('Daily digest'))}</h3><div class="bindcaption">${esc(i18n('{0} open issues, {1} fixes, {2} solved by themselves in the last 24 hours', d.open, d.fixes, d.solved))}</div>` : ''}
        <div class="bindcaption" style="margin-top:14px">${esc(i18n('Checked every 45 seconds'))}</div></details></div>`;
      box.querySelector('[data-w="close"]').onclick = () => Home.closeModal();
      box.querySelector('[data-w="adv"]').ontoggle = e => { advOpen = e.target.open; };      // a redraw every 30 s keeps it as it was
      box.querySelectorAll('[data-more]').forEach(b => { b.onclick = () => { more[b.dataset.more] = true; open(); }; });
      box.querySelectorAll('[data-link]').forEach(b => { b.onclick = () => { Home.closeModal(); openSettings(...b.dataset.link.split('/')); }; });
      box.querySelectorAll('[data-resend]').forEach(b => { b.onclick = async () => {
        b.disabled = true;
        try { await Home.api('/api/watchdog/resend', {method: 'POST', body: JSON.stringify({key: b.dataset.resend})}); Home.toast(i18n('Sent again')); }
        catch(e){ Home.toast(e.message || String(e)); }
        load();
      }; });
    };
    open();
    load();
  };
  setInterval(() => { if(document.visibilityState !== 'hidden') load(); }, 30000);
  const forgetPage = async box => {                 // Settings -> Watchdog: after how many days a gone device is forgotten by itself
    box.innerHTML = `<div class="row"><span class="rlabel">${esc(i18n('Forget a gone device after, days'))}<br><span class="bindcaption">${esc(i18n('Its name, room and places go by themselves after this many days away, only while its system runs; 0: never, only by hand (All devices, Forgotten).'))}</span></span>
      <input type="number" min="0" max="3650" step="1" data-w="days" data-autosave style="width:90px"></div>`;
    const inp = box.querySelector('[data-w="days"]');
    try { inp.value = (await Home.api('/api/watchdog/settings')).forget_days; } catch(e){ inp.disabled = true; }
    inp.onchange = async () => {
      try { inp.value = (await Home.api('/api/watchdog/settings', {method: 'PUT', body: JSON.stringify({days: Math.max(0, +inp.value || 0)})})).forget_days; Home.toast(i18n('Saved')); }
      catch(e){ Home.toast(e.message || String(e)); }
    };
  };
  HomePlugins.register({
    settings: [{subject: 'watchdog', title: 'Forgetting', needs: ['PUT /api/watchdog/settings'], render: forgetPage}],
    onReload: [load],
    onState: [() => { if(Date.now() - last > 20000) load(); }],
    header: [{slot: 'conn', render: box => {
      badge = document.createElement('button');
      badge.className = 'dlg-btn'; badge.dataset.w = 'badge'; badge.style.cssText = 'display:none;align-items:center';
      badge.onclick = show;
      box.appendChild(badge); paint();
    }}],
    modalSections: [{                               // the device window: how fast it answers, how long the house waits
      match: d => owner && !d.virtual && (d.controls || []).some(c => c.kind !== 'sensor' && c.kind !== 'trigger_text'),
      html: () => `<details><summary>${esc(i18n('Answer time'))}</summary><div data-w="ans" class="bindcaption">\u2026</div></details>`,
      wire(box, d){
        const out = box.querySelector('[data-w="ans"]'), url = '/api/watchdog/answers/' + encodeURIComponent(d.id);
        const draw = r => {
          out.className = '';
          out.innerHTML = r.channels.map(c => `<div class="row" style="flex-wrap:wrap;gap:6px"><span class="rlabel">${esc(i18n(c.label))}</span>
            <span class="rval">${esc(c.typical == null ? i18n('not measured yet, waits {0} s', c.wait) : i18n('answers in about {0} s, waits {1} s', c.typical, c.wait))}</span>
            <input type="number" min="1" max="3600" step="1" data-code="${esc(c.code)}" value="${c.set == null ? '' : c.set}" placeholder="${esc(i18n('auto'))}"
              aria-label="${esc(i18n('Wait, s'))}" title="${esc(i18n('Wait, s'))}" style="width:80px"></div>`).join('');
          out.querySelectorAll('input[data-code]').forEach(inp => { inp.onchange = async () => {
            const v = inp.value.trim();
            try { draw(await Home.api(url, {method: 'PUT', body: JSON.stringify({code: inp.dataset.code, seconds: v ? +v : null})})); }
            catch(e){ Home.toast(e.message || String(e)); }
          }; });
        };
        let done = false;
        box.querySelector('details').addEventListener('toggle', async () => {
          if(done || !box.querySelector('details').open) return;
          done = true;
          try { draw(await Home.api(url)); } catch(e){ out.textContent = e.message || String(e); }
        });
      },
    }],
    help: [
      {id: 'watchdog.days', screen: 'settings:watchdog', sel: '[data-w="days"]', title: 'Forget a gone device after, days',
       text: 'A device that is gone loses its name, room and places by itself after this many days, only while its system runs. 0: never; Settings, All devices, Forgotten forgets by hand.'},
      {id: 'watchdog.badge', screen: 'header', sel: '[data-w="badge"]', title: 'Health',
       text: 'The house checks itself every 45 seconds: the dot is green when all is well, else yellow, orange or red by the worst open issue, and the number counts them. It opens the report; only the owner sees it.'},
      {id: 'watchdog.close', screen: 'watchdog', screenName: 'Home health', sel: '[data-w="close"]', title: 'Close', text: 'Closes the report; it is checked on in the background.'},
    ],
  });
})();
