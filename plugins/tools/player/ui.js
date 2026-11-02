// Player: the Films tab (search the sources or paste a film's page from a site, Continue, what each player plays), a film's
// window (translation, season, episode, quality, the player to watch it on), each player in the window of its TV and a line
// on the TV's card while it plays, Settings -> TVs -> Players (every player, the sources and the folders of Files).
(function () {
const esc = Home.escapeHtml;
const send = (path, body, method) => Home.api(path, {method: method || 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
const q = o => Object.entries(o).filter(([, v]) => v != null && v !== '').map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&');
const CARD = 'border:1px solid var(--line, rgba(148,163,184,.25));border-radius:12px;padding:10px 12px;margin:10px 0';
const MID = '__player_film__';
let PLAYERS = [], RECENT = [];

const load = async () => {
  try { PLAYERS = (await Home.api('/api/player')).players || []; } catch(e){ PLAYERS = []; }
  return PLAYERS;
};
const loadRecent = async () => { try { RECENT = (await Home.api('/api/player/recent')).recent || []; } catch(e){ RECENT = []; } return RECENT; };
const time = s => {
  if(s == null || !isFinite(s)) return '--:--';
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${x}` : `${m}:${x}`;
};
const playing = p => p.state && !p.state.idle;
const trackName = t => [t.lang, t.title].filter(Boolean).join(' \u00b7 ') || '#' + t.id;
const isUrl = s => /^https?:\/\//i.test(s.trim());
const epLabel = w => w && w.season != null && w.episode != null ? i18n('season {0}, episode {1}', w.season, w.episode) : '';
const LAST = 'player.last';                     // the player picked last on this screen
const lastPlayer = () => { try { return localStorage.getItem(LAST) || ''; } catch(e){ return ''; } };
const keepPlayer = id => { try { localStorage.setItem(LAST, id); } catch(e){} };

// a poster: the house fetches it (the page shows no image from elsewhere), as a blob
const posterUrls = new Map();
async function poster(img, u){
  if(!u) return;
  try {
    if(!posterUrls.has(u)){
      const r = await fetch('/api/player/poster?' + q({u}), {headers: Home.authHeaders({})});
      if(!r.ok) return;
      posterUrls.set(u, URL.createObjectURL(await r.blob()));
    }
    img.src = posterUrls.get(u); img.style.visibility = 'visible';
  } catch(e){}
}
const posters = el => el.querySelectorAll('img[data-poster]').forEach(i => poster(i, i.dataset.poster));

// one player: what it plays and its buttons, and a field for a film's address or page
function html(p, withName){
  const st = p.state || {}, on = playing(p), w = p.watching;
  const head = `<div class="row"><span class="rlabel"><b>${esc(withName ? p.name : i18n('Player'))}</b>${withName && p.tv_name
      ? `<br><span class="bindcaption">${esc(p.tv_name + (p.input ? ' \u00b7 ' + p.input : ''))}</span>` : ''}</span>
    <span class="rval bindcaption">${p.error ? `<span style="color:var(--bad)">${esc(i18n(p.error))}</span>` : on ? '' : esc(i18n('waiting'))}</span></div>`;
  const pick = type => {
    const ts = (st.tracks || []).filter(t => t.type === type);
    if(!ts.length) return '';
    return `<label class="bindcaption" style="display:inline-flex;gap:4px;align-items:center">${esc(i18n(type === 'audio' ? 'Sound' : 'Subtitles'))}
      <select data-track="${type}">${type === 'sub' ? `<option value="0">${esc(i18n('none'))}</option>` : ''}${
        ts.map(t => `<option value="${t.id}"${t.selected ? ' selected' : ''}>${esc(trackName(t))}</option>`).join('')}</select></label>`;
  };
  const now = on ? `
    <div class="bindcaption" style="margin-top:6px"><b>${esc(st.title || st.url)}</b>${st.buffering ? ' \u00b7 ' + esc(i18n('loading\u2026')) : ''}</div>
    <input type="range" data-seek min="0" max="${Math.max(1, Math.floor(st.duration || 0))}" value="${Math.floor(st.pos || 0)}" style="width:100%"${st.duration ? '' : ' disabled'}>
    <div class="bindcaption" style="display:flex;justify-content:space-between"><span>${time(st.pos)}</span><span>${time(st.duration)}</span></div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px">
      <button class="dlg-btn" data-cmd="seek" data-by="-30" title="${esc(i18n('30 s back'))}">${esc(i18n('\u221230 s'))}</button>
      <button class="dlg-btn primary" data-cmd="pause">${esc(i18n(st.pause ? 'Go on' : 'Pause'))}</button>
      <button class="dlg-btn" data-cmd="seek" data-by="30" title="${esc(i18n('30 s on'))}">${esc(i18n('+30 s'))}</button>
      ${w && w.episode != null ? `<button class="dlg-btn" data-next>${esc(i18n('Next episode'))}</button>` : ''}
      <button class="dlg-btn" data-cmd="stop" style="color:var(--bad)">${esc(i18n('Stop'))}</button></div>` : '';
  const vol = p.error && !on ? '' : `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;align-items:center">
      <button class="dlg-btn" data-cmd="volume" data-by="-5">\u2212</button>
      <span class="bindcaption">${esc(i18n('Volume'))} ${st.volume == null ? '?' : Math.round(st.volume)}%</span>
      <button class="dlg-btn" data-cmd="volume" data-by="5">+</button>
      <button class="dlg-btn" data-cmd="mute">${esc(i18n(st.mute ? 'Sound on' : 'Mute'))}</button>
      ${on ? pick('audio') + pick('sub') : ''}</div>`;
  const url = `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">
      <input data-url type="url" placeholder="${esc(i18n('a film or its page on a site: https://\u2026'))}" style="flex:1;min-width:180px" autocapitalize="off" data-autosave>
      <button class="dlg-btn${on ? '' : ' primary'}" data-play>${esc(i18n('Open'))}</button></div>${p.tv_name && !withName
      ? `<div class="bindcaption">${esc(i18n('The TV comes on and switches to {0} by itself.', p.input || i18n('its input')))}</div>` : ''}`;
  return `<div class="pl-one" data-pid="${esc(p.id)}">${head}${now}${vol}${url}</div>`;
}

// the buttons of the players drawn in el; redraw() after each answer
function wire(el, redraw){
  el.querySelectorAll('.pl-one').forEach(one => {
    const pid = one.dataset.pid, path = '/api/player/' + encodeURIComponent(pid);
    const go = async (fn, b) => {
      if(b) b.disabled = true;
      try { const p = await fn(); PLAYERS = PLAYERS.map(x => x.id === p.id ? p : x); redraw(); if(p.tv_note) Home.toast(i18n(p.tv_note)); }
      catch(e){ Home.toast(i18n(e.message || String(e))); await load(); redraw(); }
    };
    one.querySelectorAll('[data-cmd]').forEach(b => b.onclick = () => go(() => send(path + '/cmd', {cmd: b.dataset.cmd,
      ...(b.dataset.by ? {by: Number(b.dataset.by)} : {})}), b));
    const nx = one.querySelector('[data-next]');
    if(nx) nx.onclick = () => go(() => send(path + '/next', {}), nx);
    const seek = one.querySelector('[data-seek]');
    if(seek){
      seek.oninput = () => { seek.dataset.moving = '1'; };
      seek.onchange = () => go(() => send(path + '/cmd', {cmd: 'seek', to: Number(seek.value)}));
    }
    one.querySelectorAll('[data-track]').forEach(s => s.onchange = () => go(() => send(path + '/cmd', {cmd: s.dataset.track, id: Number(s.value)})));
    const url = one.querySelector('[data-url]'), open = one.querySelector('[data-play]');
    const start = async () => {
      const u = url.value.trim();
      if(!isUrl(u)){ Home.toast(i18n('Give an address that starts with http:// or https://')); return; }
      open.disabled = true;
      try { const r = await send('/api/player/resolve', {url: u}); url.value = ''; film(r, {player: pid}); }
      catch(e){ Home.toast(i18n(e.message || String(e))); }
      open.disabled = false;
    };
    open.onclick = start;
    url.onkeydown = e => { if(e.key === 'Enter') start(); };
  });
}

// keeps el current while it is on screen: the players' state every 2 s (not while a field there is in use)
function follow(el, draw, every){
  const t = setInterval(async () => {
    if(!el.isConnected){ clearInterval(t); return; }
    const a = document.activeElement;
    if(el.contains(a) && (a.matches('input[type=url],input[type=search],select,textarea') || a.dataset.moving)) return;
    await load();
    if(el.isConnected) draw();
  }, every || 2000);
}

// ---- a film's window: what it has, the choice, the player, Watch / Continue ----
async function film(r, opts){
  opts = opts || {};
  let info = r.info || {}, tr = opts.translation || '', season = opts.season, episode = opts.episode, quality = opts.quality || '';
  const src = r.source, id = r.id;
  if(!PLAYERS.length) await load();
  const draw = () => {
    const trs = info.translations || [], seasons = info.seasons || [], qs = info.qualities || [];
    if(trs.length && !trs.some(t => String(t.id) === String(tr))) tr = String(trs[0].id);
    if(seasons.length){
      if(!seasons.some(s => s.n == season)) season = seasons[0].n;
      const eps = (seasons.find(s => s.n == season) || {}).episodes || [];
      if(!eps.some(e => e.n == episode)) episode = eps.length ? eps[0].n : null;
    } else { season = null; episode = null; }
    if(qs.length && !qs.includes(quality)) quality = qs[0];
    const rec = RECENT.find(x => x.source === src && String(x.id) === String(id) && (season == null || (x.season == season && x.episode == episode)));
    const cur = opts.player || lastPlayer();
    const sel = (name, label, items, val) => items.length ? `<label class="bindcaption" style="display:flex;flex-direction:column;gap:2px;min-width:120px">${esc(i18n(label))}
        <select data-pick="${name}">${items.map(([v, t]) => `<option value="${esc(v)}"${String(v) === String(val) ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>` : '';
    const eps = (seasons.find(s => s.n == season) || {}).episodes || [];
    Home.openModal(`
      <div class="head"><button class="closebtn" onclick="Home.closeModal()">\u00d7</button>
        <h2>${esc(info.title || id)}${info.year ? ` <span class="bindcaption">(${esc(info.year)})</span>` : ''}</h2>
        <div class="sub">${esc(i18n(r.source_name || src))}</div></div>
      <div class="body"><section>
        <div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap">
          ${info.poster ? `<img data-poster="${esc(info.poster)}" alt="" style="width:120px;border-radius:8px;visibility:hidden">` : ''}
          <div class="bindcaption" style="flex:1;min-width:180px;white-space:pre-line">${esc(info.description || '')}</div></div>
        <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:10px">
          ${sel('tr', 'Translation', trs.map(t => [t.id, t.name || t.id]), tr)}
          ${sel('season', 'Season', seasons.map(s => [s.n, i18n('Season {0}', s.n)]), season)}
          ${sel('episode', 'Episode', eps.map(e => [e.n, e.title ? `${e.n}. ${e.title}` : i18n('Episode {0}', e.n)]), episode)}
          ${sel('quality', 'Quality', qs.map(x => [x, x]), quality)}
          ${PLAYERS.length ? sel('player', 'Watch on', PLAYERS.map(p => [p.id, p.tv_name ? `${p.tv_name} (${p.name})` : p.name]), cur || PLAYERS[0].id)
            : `<div class="bindcaption" style="color:var(--bad)">${esc(i18n('No players yet: Settings -> Computers and servers, a computer at a TV -> Player (mpv) -> Install.'))}</div>`}
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px">
          ${rec && rec.pos > 60 ? `<button class="dlg-btn primary" data-watch="${Math.floor(rec.pos)}">${esc(i18n('Continue from {0}', time(rec.pos)))}</button>` : ''}
          <button class="dlg-btn${rec && rec.pos > 60 ? '' : ' primary'}" data-watch="0"${PLAYERS.length ? '' : ' disabled'}>${esc(i18n(rec && rec.pos > 60 ? 'From the start' : 'Watch'))}</button>
        </div><div class="bindcaption" data-out></div>
      </section></div>`, MID);
    const box = document.getElementById('md');
    posters(box);
    box.querySelectorAll('[data-pick]').forEach(s => s.onchange = async () => {
      const k = s.dataset.pick;
      if(k === 'tr'){
        tr = s.value;
        try { info = (await Home.api('/api/player/info?' + q({source: src, id, translation: tr}))).info || info; } catch(e){ Home.toast(i18n(e.message || String(e))); }
      } else if(k === 'season'){ season = Number(s.value); episode = null; }
      else if(k === 'episode') episode = Number(s.value);
      else if(k === 'quality') quality = s.value;
      else if(k === 'player'){ opts.player = s.value; keepPlayer(s.value); }
      draw();
    });
    box.querySelectorAll('[data-watch]').forEach(b => b.onclick = async () => {
      const pid = (box.querySelector('[data-pick=player]') || {}).value;
      if(!pid) return;
      keepPlayer(pid);
      const out = box.querySelector('[data-out]');
      box.querySelectorAll('[data-watch]').forEach(x => { x.disabled = true; });
      out.textContent = i18n('Turning on\u2026');
      try {
        const p = await send(`/api/player/${encodeURIComponent(pid)}/watch`, {source: src, id: String(id), translation: tr || null, season, episode,
          quality: quality || null, start: Number(b.dataset.watch) || null, title: info.title || '', poster: info.poster || ''});
        PLAYERS = PLAYERS.map(x => x.id === p.id ? p : x);
        if(p.tv_note) Home.toast(i18n(p.tv_note));
        Home.closeModal();
        Home.toast(i18n('Playing on {0}', p.tv_name || p.name));
        loadRecent();
      } catch(e){ out.textContent = i18n(e.message || String(e)); box.querySelectorAll('[data-watch]').forEach(x => { x.disabled = false; }); }
    });
  };
  if(!RECENT.length) await loadRecent();
  draw();
}

const openRecent = async x => {
  try {
    const r = await Home.api('/api/player/info?' + q({source: x.source, id: x.id, translation: x.translation || ''}));
    film(r, {translation: x.translation, season: x.season, episode: x.episode, quality: x.quality});
  } catch(e){ Home.toast(i18n(e.message || String(e))); }
};

// ---- the Films tab ----
let lastQuery = '', lastResults = null;
function tab(box){
  box.innerHTML = `<div style="max-width:1100px;margin:0 auto;padding:8px">
    <form data-find style="display:flex;gap:8px;flex-wrap:wrap">
      <input type="search" data-q value="${esc(lastQuery)}" placeholder="${esc(i18n('A film\'s name, or its page pasted from a site'))}" style="flex:1;min-width:200px" autocapitalize="off" enterkeyhint="search">
      <button class="dlg-btn primary" type="submit">${esc(i18n('Find'))}</button></form>
    <div data-out class="bindcaption" style="margin-top:6px"></div>
    <div data-res></div><div data-recent></div><div data-now></div></div>`;
  const out = box.querySelector('[data-out]'), res = box.querySelector('[data-res]');
  const drawRes = r => {
    lastResults = r;
    const errs = Object.entries(r.errors || {}).map(([s, e]) => `${s}: ${i18n(e)}`);
    out.innerHTML = errs.length ? `<span style="color:var(--bad)">${esc(errs.join('; '))}</span>` : '';
    res.innerHTML = r.searched && !r.searched.length ? `<div class="bindcaption" style="margin-top:10px;color:var(--bad)">${esc(i18n('Nowhere to search: no film site is connected. Give Files the folders of a NAS in Settings -> TVs -> Players, or ask whoever set up the house to add a site.'))}</div>`
      : r.results.length ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin-top:10px">${
      r.results.map((x, i) => `<button class="dlg-btn" data-item="${i}" style="display:flex;flex-direction:column;align-items:stretch;text-align:left;padding:6px;height:auto">
        ${x.poster ? `<img data-poster="${esc(x.poster)}" alt="" style="width:100%;aspect-ratio:2/3;object-fit:cover;border-radius:6px;visibility:hidden">` : ''}
        <b style="margin-top:4px">${esc(x.title)}</b><span class="bindcaption">${esc([x.year, x.note, i18n(x.source_name || x.source)].filter(Boolean).join(' \u00b7 '))}</span></button>`).join('')}</div>`
      : `<div class="bindcaption" style="margin-top:10px">${esc(i18n('Nothing found.'))}</div>`;
    posters(res);
    res.querySelectorAll('[data-item]').forEach(b => b.onclick = async () => {
      const x = r.results[Number(b.dataset.item)];
      b.disabled = true;
      try { film(await Home.api('/api/player/info?' + q({source: x.source, id: x.id}))); } catch(e){ Home.toast(i18n(e.message || String(e))); }
      b.disabled = false;
    });
  };
  box.querySelector('[data-find]').onsubmit = async ev => {
    ev.preventDefault();
    const v = box.querySelector('[data-q]').value.trim();
    if(!v) return;
    lastQuery = v;
    out.textContent = i18n('Looking\u2026');
    try {
      if(isUrl(v)){ out.textContent = ''; film(await send('/api/player/resolve', {url: v})); }
      else drawRes(await Home.api('/api/player/search?' + q({q: v})));
    } catch(e){ out.innerHTML = `<span style="color:var(--bad)">${esc(i18n(e.message || String(e)))}</span>`; }
  };
  if(lastResults) drawRes(lastResults);
  const drawRecent = async () => {
    await loadRecent();
    const el = box.querySelector('[data-recent]');
    if(!el) return;
    el.innerHTML = RECENT.length ? `<h3 style="margin:16px 0 6px">${esc(i18n('Continue'))}</h3><div style="display:flex;gap:8px;overflow-x:auto;padding-bottom:4px">${
      RECENT.slice(0, 12).map((x, i) => `<button class="dlg-btn" data-rec="${i}" style="flex:0 0 auto;max-width:220px;text-align:left;height:auto;padding:6px 10px">
        <b>${esc(x.title || x.id)}</b><br><span class="bindcaption">${esc([epLabel(x), x.pos ? time(x.pos) + (x.duration ? ' / ' + time(x.duration) : '') : ''].filter(Boolean).join(' \u00b7 '))}</span>
        ${x.duration ? `<div style="height:3px;background:var(--line, rgba(148,163,184,.25));border-radius:2px;margin-top:4px"><div style="height:3px;width:${Math.min(100, Math.round(100 * (x.pos || 0) / x.duration))}%;background:var(--accent, #f5b942);border-radius:2px"></div></div>` : ''}
      </button>`).join('')}</div>` : '';
    el.querySelectorAll('[data-rec]').forEach(b => b.onclick = () => openRecent(RECENT[Number(b.dataset.rec)]));
  };
  const now = box.querySelector('[data-now]');
  const drawNow = () => {
    now.innerHTML = PLAYERS.length ? `<h3 style="margin:16px 0 6px">${esc(i18n('Players'))}</h3>` + PLAYERS.map(p => `<section style="${CARD}">${html(p, true)}</section>`).join('') : '';
    wire(now, drawNow);
  };
  drawRecent();
  load().then(drawNow);
  follow(now, drawNow, 3000);
}

// ---- Settings -> TVs -> Players: every player, the sources, the folders of Files ----
async function settingsPage(box){
  box.innerHTML = `<div class="bindcaption">${esc(i18n('Loading\u2026'))}</div>`;
  await load();
  let srcs = {sources: [], folders: []};
  try { srcs = await Home.api('/api/player/sources'); } catch(e){}
  const draw = () => {
    const players = PLAYERS.length ? PLAYERS.map(p => `<section style="${CARD}">${html(p, true)}</section>`).join('')
      : `<div class="bindcaption">${esc(i18n('No players yet: Settings -> Computers and servers, a computer at a TV -> Player (mpv) -> Install.'))}</div>`;
    box.innerHTML = `<div data-players>${players}</div>
      <h3 style="margin:18px 0 6px">${esc(i18n('Sources'))}</h3>
      ${srcs.sources.map(s => `<div class="row"><span class="rlabel"><b>${esc(i18n(s.name))}</b>${s.own ? ` <span class="bindcaption">(${esc(i18n('the house\'s own'))})</span>` : ''}</span>
        <span class="rval bindcaption">${s.error ? `<span style="color:var(--bad)">${esc(s.error)}</span>` : esc([s.search ? i18n('search') : '', s.match ? i18n('pasted pages') : ''].filter(Boolean).join(', '))}</span></div>`).join('')}
      <details style="margin-top:6px"><summary class="bindcaption">${esc(i18n('Advanced'))}</summary>
        <div class="bindcaption">${esc(i18n('The house\'s own sources are plugin-data/player/sources/<name>.py (see the plug-in README); then Files and Link.'))}</div></details>
      <h3 style="margin:18px 0 6px">${esc(i18n('Folders of Files'))}</h3>
      <div class="bindcaption">${esc(i18n('Web listings of a NAS, one per line (their sub-folders too): http://192.0.2.5/films/'))}</div>
      <textarea data-folders rows="3" style="width:100%;margin-top:4px" autocapitalize="off">${esc((srcs.folders || []).join('\n'))}</textarea>
      <button class="dlg-btn" data-save-folders style="margin-top:6px">${esc(i18n('Save'))}</button>`;
    wire(box.querySelector('[data-players]'), draw);
    box.querySelector('[data-save-folders]').onclick = async () => {
      try {
        const r = await send('/api/player/folders', {folders: box.querySelector('[data-folders]').value.split('\n').map(s => s.trim()).filter(Boolean)}, 'PUT');
        srcs.folders = r.folders; Home.toast(i18n('Saved'));
      } catch(e){ Home.toast(i18n(e.message || String(e))); }
    };
  };
  draw();
  const pl = box.querySelector('[data-players]');
  follow(pl, () => { pl.innerHTML = PLAYERS.length ? PLAYERS.map(p => `<section style="${CARD}">${html(p, true)}</section>`).join('') : pl.innerHTML; wire(pl, () => {}); });
}

const ofTv = id => PLAYERS.filter(p => p.tv === id);

HomePlugins.register({
  onReload: [load],
  tabs: [{id: 'films', label: i18n('Films'), render: tab, hidden: true}],      // opened by the header's Films button
  header: [{slot: 'brand', render: box => {                                    // right of the logo: the Films tab
    const b = document.createElement('button');
    b.className = 'dlg-btn'; b.dataset.films = '1'; b.title = i18n('Films'); b.setAttribute('aria-label', i18n('Films'));
    b.innerHTML = `<span class="ico">${Home.icon('play', 18)}</span><span class="btn-text">${esc(i18n('Films'))}</span>`;
    b.onclick = () => Home.openTab('p:films');
    box.append(b);
  }}],
  cards: [(card, d) => {                       // a line on the TV's card while its player plays
    const p = ofTv(d.id).find(playing);
    if(!p || card.querySelector('.pl-card')) return;
    const line = Home.el('div', 'bindcaption pl-card', `${Home.icon('play', 14)} ${esc(p.state.title || i18n('a film'))}`);
    line.style.cssText = 'margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
    card.append(line);
  }],
  modalSections: [{
    match: d => ofTv(d.id).length > 0,
    html: d => `<div class="pl-box">${ofTv(d.id).map(p => html(p, ofTv(d.id).length > 1)).join('')}</div>`,
    wire(box, d){
      const el = box.querySelector('.pl-box') || box;
      const draw = () => { el.innerHTML = ofTv(d.id).map(p => html(p, ofTv(d.id).length > 1)).join(''); wire(el, draw); };
      wire(el, draw);
      follow(el, draw);
    },
  }],
  settings: [{subject: 'tv', title: 'Players', needs: ['GET /api/player'], render: settingsPage}],
  help: [
    {id: 'player.open', screen: 'tabs', sel: '[data-films]', title: 'Films', text: 'Opens Films: search, a pasted page of a site, Continue and the players.'},
    {id: 'player.find', screen: 'tab:p:films', sel: '[data-find] [data-q]', title: 'Find a film', text: 'A name searches every source; a page address pasted from a site opens that film (the source of that site takes it apart).'},
    {id: 'player.url', screen: 'device', sel: '.pl-one [data-url]', title: 'Film address', text: 'A film\'s own address or its page on a site: the house finds the stream and opens the film\'s window.'},
    {id: 'player.next', screen: 'device', sel: '.pl-one [data-next]', title: 'Next episode', text: 'The next episode of the series this player shows (it also starts by itself when one ends).'},
  ],
});
})();
