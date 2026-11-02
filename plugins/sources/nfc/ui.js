// Settings -> Devices -> NFC tags (owner only): the list of the tags, each with its name (renamed in place), its last tap
// and the rules its tap starts; "Add a tag" waits for the tap of a new tag on a phone and puts it on the list with the name
// given; a sticker of ours tapped while nobody waited is offered for a few minutes; a tag removed with its rules.
// "Write a card" (an administrator): the house writes a blank or its own NTAG 424 DNA through the Yash! app, keys stay home.
(() => {
  const esc = Home.escapeHtml;
  const api = (path, opts) => Home.api(path, opts);
  const send = (method, path, body) => api(path, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const post = (path, body) => send('POST', path, body);
  const CARD = 'border:1px solid var(--line, rgba(148,163,184,.25));border-radius:12px;padding:10px 12px;margin:10px 0';

  const page = box => {
    let alive = true, unwatch = null, timer = null, adding = false, writing = false, advanced = false, seenWrite = null, renaming = null, phones = null, seen = null;
    const gone = () => !box.isConnected;                         // the page was left: stop polling and watching
    const stop = () => { alive = false; clearInterval(timer); if(unwatch) unwatch(); };
    const draw = async () => {
      if(gone()){ stop(); return; }
      let v, rules;
      try { [v, rules] = await Promise.all([api('/api/nfc'), Home.rules().catch(() => [])]); }
      catch(e){ box.innerHTML = `<div class="bindcaption">${esc(e.message || e)}</div>`; return; }
      if(phones === null) phones = await api('/api/family/names').then(l => l.map(p => p.name)).catch(() => []);
      if(gone()){ stop(); return; }
      if(v.added && seen && v.added.ts > seen){ Home.toast(i18n('The tag {0} is on the list', v.added.name)); adding = false; Home.reloadDevices(); }
      seen = v.added ? v.added.ts : (seen || 1);
      const mine = name => rules.filter(r => r.kind === 'binding' && r.when && r.when[0] && r.when[0].device === v.device
        && r.when[0].code === 'card' && r.when[0].gesture === name);
      const nameField = (k, ph, val) => `<input class="nfc-name" data-k="${esc(k)}" placeholder="${esc(i18n(ph))}" value="${esc(val || '')}" maxlength="40" style="flex:1;min-width:140px" data-autosave>`;
      const where = phones.length ? i18n('to a phone with the Yash! app ({0})', phones.join(', ')) : i18n('to a phone with the Yash! app');
      const enrol = v.enroll
        ? `<div class="row" style="${CARD};border-color:var(--on);flex-wrap:wrap;gap:8px">
             <span class="rlabel"><b>${esc(i18n('Hold the new tag {0}', where))}</b><br><span class="bindcaption">${
               esc(i18n('it is added as "{0}", {1} s left', v.enroll.name, v.enroll.left))}</span></span>
             <button class="dlg-btn" data-enroll-cancel>${esc(i18n('Cancel'))}</button></div>`
        : adding
        ? `<div class="row" style="${CARD};flex-wrap:wrap;gap:8px">${nameField('enroll', 'Name of the new tag')}
             <button class="dlg-btn primary" data-enroll>${esc(i18n('Wait for its tap'))}</button>
             <button class="dlg-btn" data-adding-off>${esc(i18n('Cancel'))}</button></div>`
        : `<button class="dlg-btn primary" data-adding style="margin:4px 0 6px">${esc(i18n('+ Add a tag'))}</button>`;
      const w = v.write;
      const wres = w && w.done ? w.result || {} : null;
      const wbox = w && !w.done
        ? `<div class="row" style="${CARD};border-color:var(--on);flex-wrap:wrap;gap:8px">
             <span class="rlabel"><b>${esc(i18n('Open the Yash! app on a phone with full access and hold the card to it'))}</b><br><span class="bindcaption">${
               esc(i18n('card "{0}": {1}', w.name, i18n(w.step)))}</span></span>
             <button class="dlg-btn" data-write-cancel="${esc(w.sid)}">${esc(i18n('Cancel'))}</button></div>`
        : writing
        ? `<div class="row" style="${CARD};flex-wrap:wrap;gap:8px">${nameField('write', 'Name of the new card')}
             <button class="dlg-btn primary" data-write>${esc(i18n('Write'))}</button>
             <button class="dlg-btn" data-writing-off>${esc(i18n('Cancel'))}</button></div>`
        : `<button class="dlg-btn" data-writing style="margin:4px 0 6px" ${v.write_ready ? '' : 'disabled'} title="${
             esc(v.write_ready ? '' : i18n('The house has no card master key (NFC_MASTER_KEY in the .env file).'))}">${esc(i18n('Write a card'))}</button>`;
      const wdone = wres ? `<div class="bindcaption" style="margin:4px 0 8px;color:${wres.ok ? 'var(--on)' : 'var(--bad)'}">${esc(wres.ok
          ? i18n('The card {0} is written', w.name) + (wres.note ? ' - ' + wres.note : '')
          : i18n('The card was not written: {0}', wres.error || ''))}</div>` : '';
      const adv = `<details style="margin-top:14px" ${advanced ? 'open' : ''} data-adv><summary class="bindcaption">${esc(i18n('Advanced'))}</summary>
          <div class="row" style="flex-wrap:wrap;gap:8px;margin-top:6px"><span class="rlabel">${esc(i18n('Card address'))}</span>
          <input class="nfc-name" data-k="url" style="flex:1;min-width:240px" value="${esc(v.card_url)}" placeholder="${esc(v.card_url_default || 'https://.../nfc?picc={PICC}&cmac={MAC}')}">
          <button class="dlg-btn" data-url>${esc(i18n('Save'))}</button></div>
          <div class="bindcaption">${esc(i18n('The link written into new cards, with {PICC} and {MAC}; empty = the house\'s public address.'))}</div></details>`;
      box.innerHTML = `
        ${v.ready ? '' : `<div class="bindcaption" style="color:var(--bad);margin-bottom:8px">${esc(i18n('Tags do not work yet: the house has no tag keys. Whoever looks after the house sets them (NFC_SDM_META_KEY and NFC_SDM_FILE_KEY in the .env file).'))}</div>`}
        <div class="bindcaption" style="margin-bottom:8px">${esc(i18n('A tap of a tag on the list is an event for the rules: each tag starts what its rules say.'))}</div>
        ${enrol} ${wbox} ${wdone}
        ${v.new.map(n => `<div class="row" style="${CARD};flex-wrap:wrap;gap:8px;border-color:var(--on)">
            <span class="rlabel">${esc(i18n('A new tag was tapped'))}<br><span class="bindcaption">${esc(Home.ago(Date.now() / 1000 - n.ts))}</span></span>
            ${nameField(n.uid, 'Its name')}<button class="dlg-btn primary" data-add="${esc(n.uid)}">${esc(i18n('Add'))}</button></div>`).join('')}
        <div class="nfc-list">${v.cards.map(c => {
          const rs = mine(c.name);
          const head = renaming === c.uid
            ? `<span class="rlabel" style="display:flex;gap:6px;flex-wrap:wrap;flex:1">${nameField('rename', 'Its name', c.name)}
                 <button class="dlg-btn primary" data-rename-ok="${esc(c.uid)}">${esc(i18n('Save'))}</button>
                 <button class="dlg-btn" data-rename-off>${esc(i18n('Cancel'))}</button></span>`
            : `<span class="rlabel">${Home.icon('nfc', 16)} <b>${esc(c.name)}</b>
                 <button class="dlg-btn" data-rename="${esc(c.uid)}" title="${esc(i18n('Rename'))}" aria-label="${esc(i18n('Rename'))}" style="padding:2px 8px">${Home.icon('pen', 14)}</button><br>
                 <span class="bindcaption">${esc(c.last ? i18n('last tap {0}', Home.ago(Date.now() / 1000 - c.last)) : i18n('no tap since the start'))}</span></span>
               <span class="rval" style="display:flex;gap:6px"><button class="dlg-btn" data-rule="${esc(c.name)}">${esc(i18n('+ Rule'))}</button>
                 <button class="dlg-btn" data-del="${esc(c.uid)}" style="color:var(--bad)">${esc(i18n('Remove'))}</button></span>`;
          return `<section class="nfc-card" data-uid="${esc(c.uid)}" style="${CARD}">
            <div class="row">${head}</div>
            <div class="bindcaption" style="margin:6px 0 4px">${esc(rs.length ? i18n('A tap starts:') : i18n('no rule: a tap does nothing'))}</div>
            <div class="nfc-rules"></div></section>`; }).join('')
          || `<div class="bindcaption">${esc(i18n('No tags yet: press "+ Add a tag" and hold the tag to a phone.'))}</div>`}</div>${adv}`;
      box.querySelectorAll('.nfc-card').forEach(sc => {
        const c = v.cards.find(x => x.uid === sc.dataset.uid), into = sc.querySelector('.nfc-rules');
        mine(c.name).forEach(r => into.append(Home.ruleCard(r)));
      });
      const field = k => (box.querySelector(`.nfc-name[data-k="${CSS.escape(k)}"]`) || {}).value || '';
      const act = (sel, fn) => box.querySelectorAll(sel).forEach(b => b.onclick = async () => {
        b.disabled = true;
        try { await fn(b); } catch(e){ Home.toast(e.message || String(e)); }
        draw();
      });
      const focus = k => { const f = box.querySelector(`.nfc-name[data-k="${k}"]`); if(f){ f.focus(); f.select(); } };
      box.querySelectorAll('[data-rule]').forEach(b => b.onclick = () => Home.newRule({nfc: b.dataset.rule}));
      act('[data-adding]', async () => { adding = true; });
      act('[data-adding-off]', async () => { adding = false; });
      act('[data-enroll]', async () => {
        if(!field('enroll').trim()) throw new Error(i18n('Give the tag a name'));
        await post('/api/nfc/enroll', {name: field('enroll')});
      });
      act('[data-enroll-cancel]', async () => { adding = false; await api('/api/nfc/enroll', {method: 'DELETE'}); });
      act('[data-writing]', async () => { writing = true; });
      act('[data-writing-off]', async () => { writing = false; });
      act('[data-write]', async () => {
        if(!field('write').trim()) throw new Error(i18n('Give the card a name'));
        await post('/api/nfc/write/start', {name: field('write')});
        writing = false;
      });
      act('[data-write-cancel]', async b => { await api('/api/nfc/write/' + encodeURIComponent(b.dataset.writeCancel), {method: 'DELETE'}); });
      act('[data-url]', async () => { advanced = true; await send('PUT', '/api/nfc/settings', {card_url: field('url')}); Home.toast(i18n('Saved')); });
      const det = box.querySelector('[data-adv]'); if(det) det.ontoggle = () => { advanced = det.open; };
      act('[data-rename]', async b => { renaming = b.dataset.rename; });
      act('[data-rename-off]', async () => { renaming = null; });
      act('[data-rename-ok]', async b => {
        if(!field('rename').trim()) throw new Error(i18n('Give the tag a name'));
        await send('PATCH', '/api/nfc/cards/' + encodeURIComponent(b.dataset.renameOk), {name: field('rename')});
        renaming = null; Home.toast(i18n('Renamed')); await Home.reloadDevices();
      });
      act('[data-add]', async b => {
        if(!field(b.dataset.add).trim()) throw new Error(i18n('Give the tag a name'));
        await post('/api/nfc/cards', {uid: b.dataset.add, name: field(b.dataset.add)});
        Home.toast(i18n('Added')); await Home.reloadDevices();
      });
      act('[data-del]', async b => {
        const c = v.cards.find(x => x.uid === b.dataset.del), rs = mine(c.name);
        if(!confirm(rs.length ? i18n('Remove the tag {0} and its {1} rules? Its taps are refused from now on.', c.name, rs.length)
                              : i18n('Remove the tag {0}? Its taps are refused from now on.', c.name))) return;
        for(const r of rs) await post('/api/rules/delete', {id: r.id});
        await api('/api/nfc/cards/' + encodeURIComponent(c.uid), {method: 'DELETE'});
        Home.toast(i18n('Removed')); await Home.reloadDevices();
      });
      box.querySelectorAll('.nfc-name').forEach(f => f.onkeydown = e => {
        if(e.key === 'Enter'){ const b = f.parentElement.querySelector('.primary'); if(b) b.click(); }
        if(e.key === 'Escape'){ const b = f.parentElement.querySelector('[data-rename-off],[data-adding-off]'); if(b) b.click(); }
      });
      if(wres && wres.ok && wres.listed && seenWrite !== w.sid){ seenWrite = w.sid; Home.reloadDevices(); }
      if(renaming) focus('rename'); else if(adding && !v.enroll) focus('enroll'); else if(writing) focus('write');
    };
    unwatch = Home.watchRules(() => { if(alive) draw(); });
    timer = setInterval(async () => {                             // a tag tapped while the page is open shows up by itself
      if(gone()){ stop(); return; }
      if(box.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;   // a name being typed
      try {
        const v = await api('/api/nfc');
        const waiting = !!box.querySelector('[data-enroll-cancel]');
        if(waiting || v.enroll || (v.write && !v.write.done) || box.querySelector('[data-write-cancel]') || v.new.length !== box.querySelectorAll('[data-add]').length) draw();   // the wait counts down
      } catch(e){}
    }, 2000);
    draw();
  };

  HomePlugins.register({
    settings: [{subject: 'nfc', title: 'NFC tags', needs: ['GET /api/nfc'], render: page}],
  });
})();
