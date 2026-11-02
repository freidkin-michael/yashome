// Settings -> Devices -> Computers and servers (owner only): the house's computers, each a card: whether the house reaches it
// (and what to do when not), its name (renamed in place), the TV and input its screen is (offered from the
// screen's EDID), its servers (install, remove, use: one computer per server), Remove; "Search the network" finds the others,
// "Add by address" takes one by hand.
(() => {
  const esc = Home.escapeHtml;
  const api = (path, opts) => Home.api(path, opts);
  const send = (method, path, body) => api(path, {method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  const CARD = 'border:1px solid var(--line, rgba(148,163,184,.25));border-radius:12px;padding:10px 12px;margin:10px 0';
  // the helper's words -> what a person does about it; the words themselves stay in the log and under Advanced
  const WHY = {'the key is refused': 'no connection: the computer does not let the house in; add it again',
               'no ssh there': 'no connection: turn on remote login on the computer',
               'not reachable': 'no connection: is the computer switched on?', 'no answer': 'no connection: is the computer switched on?',
               'sudo asks for a password': 'the house cannot install there: the computer asks for a password',
               'its host key changed': 'Reinstalled? Press Trust again'};
  const why = e => i18n(WHY[e] || 'no connection: is the computer switched on?');
  const SERVERS = ['build', 'tts', 'llm', 'yashgen', 'player', 'touchmouse'];   // the app builder ('apk') is an option of the build server
  const MANY = ['player', 'touchmouse'];                       // on every computer that wants one: Servers lists where they are

  const page = box => {
    let alive = true, timer = null, renaming = null, adding = false, busy = '', open = null;
    const gone = () => !box.isConnected;
    const stop = () => { alive = false; clearInterval(timer); };
    const draw = async v => {
      if(gone()){ stop(); return; }
      try { v = v || await api('/api/computers'); }
      catch(e){ box.innerHTML = `<div class="bindcaption">${esc(e.message || e)}</div>`; return; }
      if(gone()){ stop(); return; }
      const tvs = Object.fromEntries(v.tvs.map(t => [t.id, t]));
      const tvName = id => (tvs[id] || {}).name || id;
      // the open card's servers: each one Install or Remove here (the one in use stays: the Servers tab moves it); the app
      // builder is an option of the build server. The house's own computer: a build server like any other; its TTS, Ollama
      // and yashGen are its own services, shown only.
      const roles = c => {
        const run = c.run, going = run && run.state === 'running', r = c.roles || {};
        const btn = (k, act, label, color) => `<button class="dlg-btn" data-role="${esc(k)}" data-act="${act}" data-cid="${esc(c.id)}"${
          color ? ` style="color:${color}"` : ''}>${esc(i18n(label))}</button>`;
        const can = !going && c.status && c.status.ok;
        const row = (k, title, sub) => {
          const x = r[k] || {};
          const state = c.self && k === 'llm' ? i18n('the small model') : x.active && x.installed ? i18n('runs here') : x.installed ? i18n('installed') : i18n('not installed');
          const act = !can || c.self && k !== 'build' && k !== 'apk' && !MANY.includes(k) ? '' : !x.installed ? ((k === 'yashgen' || MANY.includes(k)) && c.wsl ? '' : btn(k, 'install', 'Install')) : x.active && k !== 'apk' ? '' : btn(k, 'remove', 'Remove', 'var(--bad)');
          return `<div class="row" style="gap:8px;margin-top:4px${sub ? ';padding-left:18px' : ''}"><span class="rlabel">${esc(i18n(title))}<br><span class="bindcaption" style="color:${
            x.active && x.installed && !(c.self && k === 'llm') ? 'var(--ok)' : 'inherit'}">${esc(state)}${x.active && x.installed && !c.self && k !== 'apk'
            ? ' \u00b7 ' + esc(i18n('another one: the Servers tab')) : ''}</span></span>
            <span class="rval">${act}</span></div>`;
        };
        const rows = SERVERS.filter(k => (v.roles[k] || {}).on).map(k => row(k, v.roles[k].title)
          + (k === 'build' && (v.roles.apk || {}).on && (r.build || {}).installed ? row('apk', v.roles.apk.title, true) : '')).join('');
        const state = going ? `<div class="bindcaption" style="margin-top:6px">${esc(i18n(run.action === 'remove' ? 'removing...' : 'installing...'))} ${
            esc(i18n((v.roles[run.role] || {}).title || run.role))}</div>`
          : run && run.state === 'failed' ? `<div class="bindcaption" style="margin-top:6px;color:var(--bad)">${esc(i18n('did not work'))}</div>` : '';
        const log = run && (going || run.state === 'failed') ? `<pre style="white-space:pre-wrap;max-height:140px;overflow:auto;font-size:12px;margin:4px 0 0">${
          esc((run.log || []).slice(-12).join('\n') || '...')}</pre>` : '';
        return `<div class="bindcaption" style="margin-top:10px"><b>${esc(i18n('Servers'))}</b></div>${rows}${state}${log}`;
      };
      // a short card: name, address, its TV and the servers on it (green: the house uses it there); a click opens it
      const short = c => {
        const st = c.status || {}, ok = st.ok === true, r = c.roles || {};
        const dot = `<span style="color:${ok ? 'var(--ok)' : st.ts ? 'var(--bad)' : 'var(--muted, #94a3b8)'}">\u25cf</span>`;
        const srv = SERVERS.filter(k => (v.roles[k] || {}).on && (r[k] || {}).installed && !(c.self && k === 'llm' && !r[k].active))
          .map(k => r[k].active ? `<span style="color:var(--ok)">${esc(i18n(v.roles[k].title))}</span>` : esc(i18n(v.roles[k].title))).join(', ');
        const going = c.run && c.run.state === 'running';
        return `<section class="pc-card" data-open="${esc(c.id)}" role="button" tabindex="0" style="${CARD};margin:0;cursor:pointer"${going ? ' data-running' : ''}>
          <div>${dot} <b>${esc(c.name)}</b>${c.self ? ` <span class="bindcaption">(${esc(i18n('the house runs here'))})</span>` : ''}</div>
          ${ok || !st.ts ? '' : `<div class="bindcaption" style="color:var(--bad)">${esc(why(st.error))}</div>`}
          ${c.tv ? `<div class="bindcaption">${esc(tvName(c.tv) + (c.input ? ' \u00b7 ' + c.input : ''))}</div>` : ''}
          <div class="bindcaption" style="margin-top:4px">${going ? esc(i18n(c.run.action === 'remove' ? 'removing...' : 'installing...')) : srv || esc(i18n('no servers'))}</div></section>`;
      };
      const card = c => {
        if(open !== c.id) return short(c);
        const st = c.status || {}, ok = st.ok === true, g = c.guess || {};
        const dot = `<span style="color:${ok ? 'var(--ok)' : st.ts ? 'var(--bad)' : 'var(--muted, #94a3b8)'}">\u25cf</span>`;
        const line = [c.hostname, c.host, c.os, c.wsl ? 'Windows, WSL ' + c.wsl : ''].filter(Boolean).join(' \u00b7 ');
        const state = ok ? i18n('reachable')
          : st.ts ? why(st.error) + (st.seen ? ' \u00b7 ' + i18n('last seen {0}', Home.ago(Date.now() / 1000 - st.seen)) : '') : i18n('not checked yet');
        const head = renaming === c.id
          ? `<span class="rlabel" style="display:flex;gap:6px;flex-wrap:wrap;flex:1"><input class="pc-name" value="${esc(c.name)}" maxlength="40" style="flex:1;min-width:140px" data-autosave>
               <button class="dlg-btn primary" data-rename-ok="${esc(c.id)}">${esc(i18n('Save'))}</button><button class="dlg-btn" data-rename-off>${esc(i18n('Cancel'))}</button></span>`
          : `<span class="rlabel">${dot} <b>${esc(c.name)}</b>${c.self ? ` <span class="bindcaption">(${esc(i18n('the house runs here'))})</span>` : ''}
               <button class="dlg-btn" data-rename="${esc(c.id)}" title="${esc(i18n('Rename'))}" aria-label="${esc(i18n('Rename'))}" style="padding:2px 8px">${Home.icon('pen', 14)}</button><br>
               <span class="bindcaption" style="color:${ok || !st.ts ? 'inherit' : 'var(--bad)'}">${esc(state)}</span></span>
             <span class="rval" style="display:flex;gap:6px;flex-wrap:wrap"><button class="dlg-btn" data-close>${esc(i18n('Close'))}</button>${st.error === 'its host key changed'
               ? `<button class="dlg-btn" data-forget="${esc(c.id)}" title="${esc(i18n('After a reinstall: the house trusts the computer again'))}">${esc(i18n('Trust again'))}</button>` : ''}${
               c.self ? '' : `<button class="dlg-btn" data-del="${esc(c.id)}" style="color:var(--bad)">${esc(i18n('Remove'))}</button>`}</span>`;
        const tv = tvs[c.tv], inputs = tv ? tv.inputs : [];
        const seen = (st.screens || []).filter(s => s.port).map(s => i18n('its screen is HDMI {0} of a {1} TV', s.port, s.name || s.vendor));
        const offer = g.tv && (g.tv !== c.tv || (g.input && g.input !== c.input))
          ? `<button class="dlg-btn" data-take="${esc(c.id)}">${esc(i18n('Set {0}', tvName(g.tv) + (g.input ? ', ' + g.input : '')))}</button>` : '';
        return `<section class="pc-card" style="${CARD};margin:0;grid-column:1/-1"${c.run && c.run.state === 'running' ? ' data-running' : ''}><div class="row">${head}</div>
          <div class="row" style="flex-wrap:wrap;gap:8px;margin-top:6px"><span class="rlabel">${esc(i18n('Its screen'))}${seen.length ? `<br><span class="bindcaption">${esc(seen.join('; '))}</span>` : ''}</span>
            <span class="rval" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">${offer}
              <select data-tv="${esc(c.id)}"><option value="">${esc(i18n('no TV'))}</option>${c.tv && !tv ? `<option value="${esc(c.tv)}" selected>${esc(c.tv)}</option>` : ''}${v.tvs.map(t => `<option value="${esc(t.id)}"${t.id === c.tv ? ' selected' : ''}>${esc(t.name)}</option>`).join('')}</select>
              ${tv ? `<select data-input="${esc(c.id)}"><option value="">${esc(i18n('input?'))}</option>${inputs.map(i => `<option${i === c.input ? ' selected' : ''}>${esc(i)}</option>`).join('')}</select>` : ''}</span></div>
          ${roles(c)}
          ${line || st.error ? `<details style="margin-top:8px"><summary class="bindcaption">${esc(i18n('Advanced'))}</summary><div class="bindcaption">${
            esc([line, st.ts ? i18n('checked {0}', Home.ago(Date.now() / 1000 - st.ts)) : '', st.error || ''].filter(Boolean).join(' \u00b7 '))}</div></details>` : ''}</section>`;
      };
      const found = v.found.map(f => `<div class="row" style="${CARD};flex-wrap:wrap;gap:8px;${f.ok ? 'border-color:var(--on)' : 'opacity:.7'}">
          <span class="rlabel"><b>${esc(f.hostname || f.host)}</b>${f.ok ? '' : `<br><span class="bindcaption">${esc(why(f.error))}</span>`}</span>
          ${f.ok ? `<button class="dlg-btn primary" data-addfound="${esc(f.host)}" data-name="${esc(f.hostname || '')}">${esc(i18n('Add'))}</button>` : ''}</div>`).join('');
      box.innerHTML = `
        ${v.user ? '' : `<div class="bindcaption" style="color:var(--bad);margin-bottom:8px">${esc(i18n('The house does not know which user to log in to its computers as: whoever set up the house sets it (see Advanced).'))}</div>`}
        <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:4px 0 8px">
          <button class="dlg-btn primary" data-scan ${busy ? 'disabled' : ''}>${esc(busy === 'scan' ? i18n('Searching...') : i18n('Search the network'))}</button>
          <button class="dlg-btn" data-check ${busy ? 'disabled' : ''}>${esc(busy === 'check' ? i18n('Checking...') : i18n('Check again'))}</button>
          <button class="dlg-btn" data-adding>${esc(i18n('+ Add by address'))}</button>
          <span class="bindcaption">${esc(v.scan.ts ? i18n('searched {0}', Home.ago(Date.now() / 1000 - v.scan.ts)) : '')}</span></div>
        ${adding ? `<div class="row" style="${CARD};flex-wrap:wrap;gap:8px"><input class="pc-host" placeholder="${esc(i18n('address or host name'))}" style="width:180px" data-autosave>
            <input class="pc-newname" placeholder="${esc(i18n('name (optional)'))}" maxlength="40" style="flex:1;min-width:120px" data-autosave>
            <button class="dlg-btn primary" data-add>${esc(i18n('Add'))}</button><button class="dlg-btn" data-adding-off>${esc(i18n('Cancel'))}</button>
            <details style="flex-basis:100%"><summary class="bindcaption">${esc(i18n('Advanced'))}</summary>
              <input class="pc-wsl" placeholder="${esc(i18n('Windows: WSL distribution/user'))}" maxlength="80" style="width:220px" data-autosave></details></div>` : ''}
        ${v.computers.length ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-top:8px">${v.computers.map(card).join('')}</div>`
          : `<div class="bindcaption">${esc(i18n('No computers yet: press "Search the network".'))}</div>`}
        ${found ? `<div class="bindcaption" style="margin-top:14px">${esc(i18n('Found on the network, not on the list:'))}</div>${found}` : ''}
        <details style="margin-top:14px"><summary class="bindcaption">${esc(i18n('Advanced'))}</summary><div class="bindcaption">${
          esc(i18n('A computer of the house: the house logs in to it with its key as {0} and sudo asks no password.', v.user || '?'))}${
          v.user ? '' : ' ' + esc(i18n('COMPUTERS_SSH_USER is not set: the user the house logs in to its computers as.'))}${
          v.scan.net || v.net ? ' \u00b7 ' + esc(i18n('network {0}', v.scan.net || v.net)) : ''}</div></details>`;
      const act = (sel, fn) => box.querySelectorAll(sel).forEach(b => b.onclick = async () => {
        b.disabled = true;
        let nv;
        try { nv = await fn(b); } catch(e){ Home.toast(e.message || String(e)); }
        draw(nv);
      });
      act('[data-scan]', async () => { busy = 'scan'; draw(v); try { return await send('POST', '/api/computers/scan'); } finally { busy = ''; } });
      act('[data-check]', async () => { busy = 'check'; draw(v); try { return await send('POST', '/api/computers/check'); } finally { busy = ''; } });
      act('[data-adding]', async () => { adding = true; return v; });
      act('[data-adding-off]', async () => { adding = false; return v; });
      act('[data-add]', async () => {
        const host = box.querySelector('.pc-host').value.trim();
        if(!host) throw new Error(i18n('Give its address'));
        const r = await send('POST', '/api/computers', {host, name: box.querySelector('.pc-newname').value, wsl: box.querySelector('.pc-wsl').value.trim()});
        adding = false; Home.toast(i18n('Added')); return r;
      });
      act('[data-addfound]', async b => { const r = await send('POST', '/api/computers', {host: b.dataset.addfound, name: b.dataset.name}); Home.toast(i18n('Added')); return r; });
      act('[data-rename]', async b => { renaming = b.dataset.rename; return v; });
      act('[data-rename-off]', async () => { renaming = null; return v; });
      act('[data-rename-ok]', async b => {
        const r = await send('PATCH', '/api/computers/' + encodeURIComponent(b.dataset.renameOk), {name: box.querySelector('.pc-name').value});
        renaming = null; return r;
      });
      act('[data-forget]', async b => send('POST', '/api/computers/' + encodeURIComponent(b.dataset.forget) + '/forget-key'));
      act('[data-take]', async b => {
        const c = v.computers.find(x => x.id === b.dataset.take);
        return send('PATCH', '/api/computers/' + encodeURIComponent(c.id), {tv: c.guess.tv, input: c.guess.input || ''});
      });
      act('[data-del]', async b => {
        const c = v.computers.find(x => x.id === b.dataset.del);
        if(!confirm(i18n('Remove {0} from the list? Nothing changes on the computer itself.', c.name))) return v;
        const r = await send('DELETE', '/api/computers/' + encodeURIComponent(c.id)); Home.toast(i18n('Removed')); return r;
      });
      act('[data-role]', async b => {
        const c = v.computers.find(x => x.id === b.dataset.cid), role = (v.roles[b.dataset.role] || {}).title || b.dataset.role, a = b.dataset.act;
        const ask = a === 'install' && b.dataset.role === 'touchmouse' ? i18n('The house logs in to {0} and installs the touchmouse receiver there as root: a phone or a panel moves its pointer. Go on?', c.name)
          : a === 'install' && b.dataset.role === 'player' ? i18n('The house logs in to {0} and installs its player there as root (mpv, when missing): it plays on the screen of the user the house logs in as. Go on?', c.name)
          : a === 'install' ? i18n('The house logs in to {0} and installs the {1} there as root (docker too, when missing). The Servers tab picks where it runs. Go on?', c.name, i18n(role))
          : a === 'remove' ? i18n('Remove the {1} from {0}, with its data (caches, models)?', c.name, i18n(role)) : '';
        if(ask && !confirm(ask)) return v;
        return send('POST', `/api/computers/${encodeURIComponent(c.id)}/roles/${encodeURIComponent(b.dataset.role)}`, {action: a});
      });
      box.querySelectorAll('[data-open]').forEach(el => {
        const go = () => { open = el.dataset.open; renaming = null; draw(v); };
        el.onclick = go;
        el.onkeydown = e => { if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); go(); } };
      });
      act('[data-close]', async () => { open = null; renaming = null; return v; });
      box.querySelectorAll('[data-tv]').forEach(s => s.onchange = async () => {
        try { draw(await send('PATCH', '/api/computers/' + encodeURIComponent(s.dataset.tv), {tv: s.value})); } catch(e){ Home.toast(e.message || String(e)); draw(); }
      });
      box.querySelectorAll('[data-input]').forEach(s => s.onchange = async () => {
        try { draw(await send('PATCH', '/api/computers/' + encodeURIComponent(s.dataset.input), {input: s.value})); } catch(e){ Home.toast(e.message || String(e)); draw(); }
      });
      box.querySelectorAll('.pc-name,.pc-host,.pc-newname,.pc-wsl').forEach(f => f.onkeydown = e => {
        if(e.key === 'Enter'){ const b = f.parentElement.querySelector('.primary'); if(b) b.click(); }
        if(e.key === 'Escape'){ const b = f.parentElement.querySelector('[data-rename-off],[data-adding-off]'); if(b) b.click(); }
      });
      const f = box.querySelector('.pc-name') || box.querySelector('.pc-host');
      if(f && document.activeElement === document.body){ f.focus(); if(f.select) f.select(); }
    };
    let tick = 0;
    timer = setInterval(() => {                                       // the house checks them every 5 minutes: the page follows; a run: every 3 s
      if(gone()){ stop(); return; }
      const running = !!box.querySelector('[data-running]');
      if(!running && ++tick % 10) return;
      if(!alive || busy || renaming || adding || box.contains(document.activeElement) && /INPUT|SELECT/.test(document.activeElement.tagName)) return;
      draw();
    }, 3000);
    draw();
  };

  // Servers tab: each server and the computer it runs on, picked among those it is installed on (Computers tab: a card)
  const where = (c, k) => c.self && k === 'llm' ? i18n('{0}: only the small model', c.name) : c.name;
  const servers = box => {
    let timer = null;
    const draw = async () => {
      if(!box.isConnected){ clearInterval(timer); return; }
      let v;
      try { v = await api('/api/computers'); }
      catch(e){ box.innerHTML = `<div class="bindcaption">${esc(e.message || e)}</div>`; return; }
      const many = MANY.filter(k => (v.roles[k] || {}).on).map(k => {        // on every computer that wants one: where they are
        const have = v.computers.filter(c => ((c.roles || {})[k] || {}).installed);
        return `<div class="row" style="flex-wrap:wrap;gap:8px"><span class="rlabel">${esc(i18n(v.roles[k].title))}<br><span class="bindcaption">${
            esc(i18n('on every computer that has it; install it from the computer\'s card'))}</span></span>
          <span class="rval bindcaption" style="color:${have.length ? 'var(--ok)' : 'inherit'}">${esc(have.length ? have.map(c => c.name).join(', ') : i18n('not installed anywhere'))}</span></div>`;
      }).join('');
      box.innerHTML = SERVERS.filter(k => (v.roles[k] || {}).on && !MANY.includes(k)).map(k => {
        const have = v.computers.filter(c => ((c.roles || {})[k] || {}).installed), cur = have.find(c => c.roles[k].active);
        const t = (v.tunnels || {})[k], down = cur && !cur.self && t && t.host === cur.host && t.state !== 'up';
        const pick = have.length ? `<select data-server="${esc(k)}" style="max-width:260px">${cur ? '' : `<option value="">${esc(i18n('not chosen'))}</option>`}${
            have.map(c => `<option value="${esc(c.id)}"${c === cur ? ' selected' : ''}>${esc(where(c, k))}</option>`).join('')}</select>`
          : `<span class="bindcaption">${esc(i18n('not installed anywhere'))}</span>`;
        return `<div class="row" style="flex-wrap:wrap;gap:8px"><span class="rlabel">${esc(i18n(v.roles[k].title))}${down
            ? `<br><span class="bindcaption" style="color:var(--bad)" title="${esc(t.error || '')}">${esc(i18n('no connection: is the computer switched on?'))}</span>` : ''}</span>
          <span class="rval">${pick}</span></div>`;
      }).join('') + many;
      box.querySelectorAll('[data-server]').forEach(sel => sel.onchange = async () => {
        if(!sel.value) return;
        sel.disabled = true;
        try { await send('POST', `/api/computers/${encodeURIComponent(sel.value)}/roles/${encodeURIComponent(sel.dataset.server)}`, {action: 'use'}); }
        catch(e){ Home.toast(e.message || String(e)); }
        draw();
      });
      clearInterval(timer);
      if(Object.values(v.tunnels || {}).some(t => t.state !== 'up')) timer = setInterval(draw, 5000);     // a tunnel coming up: follow it
    };
    draw();
  };

  HomePlugins.register({
    settings: [{subject: 'computers', title: 'Computers', needs: ['GET /api/computers'], render: page},
               {subject: 'computers', title: 'Servers', needs: ['GET /api/computers'], render: servers}],
  });
})();
