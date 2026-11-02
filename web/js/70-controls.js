// SVG icons (a font without the glyph would show a box): tools and device kinds
const ICON = {
  pen: '<path d="M3 17.2V21h3.8L17.8 10 14 6.2 3 17.2zM20.7 7.1a1 1 0 0 0 0-1.4l-2.4-2.4a1 1 0 0 0-1.4 0L15 5.2 18.8 9l1.9-1.9z" fill="currentColor"/>',
  trash: '<path d="M6 7h12l-1 14H7L6 7zm3-4h6l1 2h4v2H4V5h4l1-2z" fill="currentColor"/>',
  play: '<path d="M8 5v14l11-7z" fill="currentColor"/>',
  unlock: '<path d="M7 11V8a5 5 0 0 1 9.6-2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><rect x="5" y="11" width="14" height="10" rx="2" fill="currentColor"/>',
  switch: '<path d="M13 3h-2v10h2V3zm4.83 2.17l-1.42 1.42A6.92 6.92 0 0 1 19 12a7 7 0 0 1-14 0c0-2.05.88-3.9 2.58-5.41L6.17 5.17A8.93 8.93 0 0 0 3 12a9 9 0 0 0 18 0c0-2.74-1.23-5.18-3.17-6.83z" fill="currentColor"/>',
  sensor: '<path d="M14 14.8V5a2 2 0 1 0-4 0v9.8a4 4 0 1 0 4 0zM12 4a1 1 0 0 1 1 1v3h-2V5a1 1 0 0 1 1-1z" fill="currentColor"/>',
  button: '<path d="M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm1 4v3h6V6H9zm0 6v2h2v-2H9zm4 0v2h2v-2h-2zm-4 4v2h2v-2H9zm4 0v2h2v-2h-2z" fill="currentColor"/>',
  climate: '<path d="M11 2h2v4.6l2.5-1.5 1 1.7L13 9.1v1.7l1.5-.8 3.1-1.8-.1-2.9h2l.1 1.8 4-2.3 1 1.7-4 2.3 1.5 1-1 1.7-2.5-1.5-3.1 1.8L13 13.2v1.7l3.5 2.3-1 1.7L13 17.4V22h-2v-4.6l-2.5 1.5-1-1.7 3.5-2.3v-1.7L7.4 15 4.9 16.5l-1-1.7 1.5-1-4-2.3 1-1.7 4 2.3.1-1.8h2l-.1 2.9 3.1 1.8 1.5.8V9.1L7.5 6.8l1-1.7L11 6.6V2z" fill="currentColor" transform="scale(.9) translate(1.3 1.3)"/>',
  tv: '<path d="M3 4h18a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-7v2h3v2H7v-2h3v-2H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm1 2v9h16V6H4z" fill="currentColor"/>',
  lock: '<path d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm-3 5a3 3 0 0 1 6 0v3H9V7zm3 7a1.5 1.5 0 0 1 1 2.6V18h-2v-1.4A1.5 1.5 0 0 1 12 14z" fill="currentColor"/>',
  person: '<path d="M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zm0 2c-4 0-8 2-8 5v2h16v-2c0-3-4-5-8-5z" fill="currentColor"/>',
  camera: '<path d="M9 3 7.2 5H4a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3.2L15 3H9zm3 5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zm0 2a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z" fill="currentColor"/>',
  ir: '<path d="M4 6h16v12H4V6zm2 2v8h12V8H6zm1 1h3v2H7V9zm4 0h3v2h-3V9zm4 0h2v2h-2V9zM7 13h3v2H7v-2zm4 0h3v2h-3v-2zm4 0h2v2h-2v-2z" fill="currentColor"/>',
  other: '<path d="M4 4h16v16H4V4zm2 2v12h12V6H6zm2 2h8v8H8V8z" fill="currentColor"/>',
  // small signs drawn, not typed: a character the device's font lacks shows as an empty box
  check: '<path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
  warn: '<path d="M12 3 2 20h20L12 3zm-1 6h2v6h-2V9zm0 8h2v2h-2v-2z" fill="currentColor"/>',
  info: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M11 10.5h2V17h-2zM11 7h2v2h-2z" fill="currentColor"/>',
  clock: '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M12 7.5V12l3 2" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
  resize: '<path d="M20 8 8 20M20 14l-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
  more: '<circle cx="5" cy="12" r="2" fill="currentColor"/><circle cx="12" cy="12" r="2" fill="currentColor"/><circle cx="19" cy="12" r="2" fill="currentColor"/>',
  network: '<circle cx="6" cy="12" r="2.6" fill="currentColor"/><circle cx="18" cy="6" r="2.6" fill="currentColor"/><circle cx="18" cy="18" r="2.6" fill="currentColor"/><path d="M8.4 10.8l7.2-3.6M8.4 13.2l7.2 3.6" fill="none" stroke="currentColor" stroke-width="2"/>',
  chat: '<path d="M4 4h16v12H9l-5 4V4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M8 9h8M8 12h5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  home: '<path d="M12 3 2 11.5h3V21h5.5v-6h3v6H19v-9.5h3L12 3z" fill="currentColor"/>',
  screen: '<path d="M3 4h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1h-7v2h3v2H7v-2h3v-2H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm1 2v10h16V6H4z" fill="currentColor"/>',
  globe: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18" fill="none" stroke="currentColor" stroke-width="2"/>',
  key: '<path d="M8 10a5 5 0 1 1 3.9 4.9L10 17H8v2H6v2H3v-3l5.1-5.1A5 5 0 0 1 8 10zm5-2a2 2 0 1 0 0 .1z" fill="currentColor"/>',
  archive: '<path d="M3 4h18v4H3V4zm1 5h16v11H4V9zm5 3v2h6v-2H9z" fill="currentColor"/>',
  puzzle: '<path d="M10 3a2 2 0 0 1 4 0v2h4a1 1 0 0 1 1 1v4h-2a2 2 0 1 0 0 4h2v4a1 1 0 0 1-1 1h-4v-2a2 2 0 1 0-4 0v2H6a1 1 0 0 1-1-1v-4h2a2 2 0 1 0 0-4H5V6a1 1 0 0 1 1-1h4V3z" fill="currentColor"/>',
  heart: '<path d="M12 21s-8-5.2-8-11a4.5 4.5 0 0 1 8-2.8A4.5 4.5 0 0 1 20 10c0 5.8-8 11-8 11z" fill="currentColor"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M15.5 15.5 21 21" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>',
  back: '<path d="M15 5 8 12l7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
  cloud: '<path d="M7 19a4.5 4.5 0 0 1-.7-8.9A6 6 0 0 1 17.8 9 5 5 0 0 1 17.5 19H7z" fill="currentColor"/>',
  mic: '<path d="M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3zM5 11h2a5 5 0 0 0 10 0h2a7 7 0 0 1-6 6.9V20h3v2H8v-2h3v-2.1A7 7 0 0 1 5 11z" fill="currentColor"/>',
  shower: '<path d="M3 3h6a8 8 0 0 1 8 8v1H5v-1a6 6 0 0 0-2-4.5V3zm3 11h2v2H6v-2zm5 0h2v2h-2v-2zm5 0h2v2h-2v-2zM8 18h2v2H8v-2zm5 0h2v2h-2v-2zm-8 0h2v2H5v-2z" fill="currentColor"/>',
  chip: '<path d="M8 2h2v3h4V2h2v3h1a2 2 0 0 1 2 2v1h3v2h-3v4h3v2h-3v1a2 2 0 0 1-2 2h-1v3h-2v-3h-4v3H8v-3H7a2 2 0 0 1-2-2v-1H2v-2h3v-4H2V8h3V7a2 2 0 0 1 2-2h1V2zm1 7v6h6V9H9z" fill="currentColor"/>',
  hammer: '<path d="M13 2l8 8-2.5 2.5-2-2-1.6 1.6 2 2-2.4 2.4-2-2L5 22l-3-3 7.5-7.5-2-2L10 7l2 2 1.6-1.6-2-2L13 2z" fill="currentColor"/>',
  nfc: '<path d="M6 3h12a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3zm2 5v8h2v-4.6l4 4.6h2V8h-2v4.6L10 8H8z" fill="currentColor"/>',
  people: '<path d="M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm7.5 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM9 13c-3.3 0-7 1.7-7 4.5V20h14v-2.5C16 14.7 12.3 13 9 13zm7.5 0c-.6 0-1.2.1-1.8.2 1.4 1 2.3 2.4 2.3 4.3V20h5v-2.5c0-2.5-2.9-4.5-5.5-4.5z" fill="currentColor"/>',
  map: '<path d="M9 3 3 5v16l6-2 6 2 6-2V3l-6 2-6-2zm0 2.2 6 2v11.6l-6-2V5.2z" fill="currentColor"/>',
  link: '<path d="M10.6 13.4a4 4 0 0 1 0-5.7l3-3a4 4 0 0 1 5.7 5.7l-1.6 1.6-1.4-1.4 1.6-1.6a2 2 0 0 0-2.9-2.9l-3 3a2 2 0 0 0 0 2.9l-1.4 1.4zm2.8-2.8a4 4 0 0 1 0 5.7l-3 3a4 4 0 0 1-5.7-5.7l1.6-1.6 1.4 1.4-1.6 1.6a2 2 0 0 0 2.9 2.9l3-3a2 2 0 0 0 0-2.9l1.4-1.4z" fill="currentColor"/>',
  chart: '<path d="M3 20V4h2v14h16v2H3zm4-4v-6h2v6H7zm4 0V7h2v9h-2zm4 0v-4h2v4h-2z" fill="currentColor"/>',
  shield: '<path d="M12 2 4 5v6c0 5 3.4 9.5 8 11 4.6-1.5 8-6 8-11V5l-8-3zm-1 13.5-3.5-3.5 1.4-1.4 2.1 2.1 4.6-4.6 1.4 1.4-6 6z" fill="currentColor"/>',
  pointer: '<path d="M5 3l14 7-6.3 1.8L10.8 18 5 3z" fill="currentColor"/>',
  wifi: '<path d="M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><circle cx="12" cy="19.5" r="1.4" fill="currentColor"/>',
};
function icon(name, size){
  const s = size || 18;
  return `<svg class="svgi" viewBox="0 0 24 24" width="${s}" height="${s}" aria-hidden="true">${ICON[name] || ICON.other}</svg>`;
}
// the kind of a device as an icon: by what it does, else by its category
function devIconName(d){
  const cs = d.controls || [];
  if(d.switchbot || cs.some(c => c.kind === 'switch' && /lock/i.test((c.text_on || '') + (c.code || '')))) return 'lock';
  if(d.ir_appliance) return 'ir';
  const cat = d.category || '';
  if(cat === 'climate') return 'climate';
  if(cat === 'tv' || cat === 'media') return 'tv';
  if(cat === 'button') return 'button';
  if(cat === 'indicator' || d.transport === 'presence' || d.transport === 'family') return 'person';
  if(cat === 'camera') return 'camera';
  if(cat === 'switch' || cs.some(c => c.kind === 'switch' || c.kind === 'bright')) return 'switch';
  if(cat === 'sensor' || cs.some(c => c.kind === 'sensor')) return 'sensor';
  return 'other';
}
function devIcon(d){ return `<span class="devicon">${icon(devIconName(d), 18)}</span>`; }

// A text question as a small window of the page (prompt() is invisible to kiosks and automation,
// and ugly). Resolves with the text, or null when cancelled (Escape, Cancel, the backdrop).
function askText(message, initial, placeholder){
  return new Promise(resolve => {
    let ov = document.getElementById('ask');
    if(ov) ov.remove();
    ov = document.createElement('div'); ov.id = 'ask'; ov.className = 'askbd';
    if(openModalId) ov.style.background = 'rgba(8,10,14,.97)';   // over a window: opaque, nothing shows through
    ov.innerHTML = `<div class="askcard" role="dialog" aria-modal="true">
        <div class="asklbl"></div>
        <input class="askin" type="text" autocomplete="off" autocapitalize="off" spellcheck="false">
        <div class="askbtns"><button class="dlg-btn" data-ask="cancel">${i18n('Cancel')}</button>
          <button class="dlg-btn primary" data-ask="ok">OK</button></div></div>`;
    ov.querySelector('.asklbl').textContent = message || '';
    const inp = ov.querySelector('.askin'); inp.value = initial || ''; inp.placeholder = placeholder || '';
    const done = v => { ov.remove(); resolve(v); };
    ov.querySelector('[data-ask="ok"]').onclick = () => done(inp.value);
    ov.querySelector('[data-ask="cancel"]').onclick = () => done(null);
    ov.onclick = e => { if(e.target === ov) done(null); };
    inp.onkeydown = e => { if(e.key === 'Enter') done(inp.value); else if(e.key === 'Escape') done(null); };
    document.body.appendChild(ov);
    inp.focus(); if(window.matchMedia('(pointer:fine)').matches) inp.select();   // a phone: no selection handles
  });
}

// a yes/no window in the page; resolves true on the main button
function askConfirm(message, okLabel){
  return new Promise(resolve => {
    const ov = document.createElement('div'); ov.className = 'askbd';
    ov.innerHTML = `<div class="askcard" role="dialog" aria-modal="true"><div class="asklbl"></div>
        <div class="askbtns"><button class="dlg-btn" data-ask="cancel">${i18n('Cancel')}</button>
          <button class="dlg-btn primary" data-ask="ok"></button></div></div>`;
    ov.querySelector('.asklbl').textContent = message;
    ov.querySelector('[data-ask="ok"]').textContent = okLabel || 'OK';
    const done = v => { ov.remove(); resolve(v); };
    ov.querySelector('[data-ask="ok"]').onclick = () => done(true);
    ov.querySelector('[data-ask="cancel"]').onclick = () => done(false);
    ov.onclick = e => { if(e.target === ov) done(false); };
    document.body.appendChild(ov);
  });
}

const PEN_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M3 17.2V21h3.8L17.8 10 14 6.2 3 17.2zM20.7 7.1a1 1 0 0 0 0-1.4l-2.4-2.4a1 1 0 0 0-1.4 0L15 5.2 18.8 9l1.9-1.9z" fill="currentColor"/></svg>';
const STAR_SVG = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M12 2.8l2.8 6 6.5.8-4.8 4.5 1.3 6.5L12 17.4l-5.8 3.2 1.3-6.5L2.7 9.6l6.5-.8z" fill="currentColor" fill-opacity="var(--star-fill,0)" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>';
// how the house reaches a device: through an IR blaster (a tile, a card a tile extends, an AC the blaster's firmware
// sends to) or over the network (a TV's own API); the blaster's own card shows none
function transportsOf(d){
  if(d.ir_blaster) return [];
  const out = [], blasters = new Set(DEVICES.filter(x => x.ir_blaster).map(x => x.ir_blaster));
  if(d.transport === 'tizen' || d.transport === 'webos')
    out.push({t: 'net', icon: 'network', title: i18n('Over the network ({0})', d.transport === 'tizen' ? 'Samsung Tizen' : 'LG webOS')});
  const via = d.ir_appliance ? d.ir_via
    : (d.controls || []).map(c => String(c.command_topic || '').split('/')[0]).find(n => blasters.has(n))
      || (DEVICES.find(a => a.ir_appliance && a.ir_card === d.id) || {}).ir_via;
  if(via){
    const b = DEVICES.find(x => x.ir_blaster === via);
    out.push({t: 'ir', icon: 'ir', title: i18n('By IR through {0}', b ? b.name : via)});
  }
  return out;
}
function transportBadges(d){
  return transportsOf(d).map(x => { const s = document.createElement('span'); s.className = 'tbadge'; s.dataset.t = x.t;
    s.title = x.title; s.setAttribute('aria-label', x.title); s.innerHTML = icon(x.icon, 14); return s; });
}
function favStar(d){
  const b = document.createElement('button');
  b.className = 'favstar'; b.type = 'button';
  const paint = () => { const on = FAVORITES.some(f => f.device === d.id); b.classList.toggle('on', on);
    b.innerHTML = STAR_SVG; b.title = i18n(on ? 'Remove from favourites' : 'Add to favourites'); };
  paint();
  b.onclick = async ev => {
    ev.stopPropagation();
    const mine = FAVORITES.filter(f => f.device === d.id);
    if(mine.length){ for(const f of mine) await removeFav(f); return; }
    const C = controlsOf(d);
    const main = C.switches[0] || C.sensors[0] || (d.controls || [])[0];
    if(!main){ notifyFail(i18n('This card has no channel to keep as a favourite')); return; }
    if(await addFavorite(d.id, main.code, d.name)){ renderTabs(); render(); }
  };
  return b;
}

async function addFavorite(device, code, alias){
  try{
    const r = await api(`/api/favorites/${encodeURIComponent(device)}/${encodeURIComponent(code)}`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({alias})
    });
    const existing = FAVORITES.findIndex(f => f.device===device && f.code===code);
    if(existing >= 0) FAVORITES[existing] = r.favorite;
    else FAVORITES.push(r.favorite);
  }catch(e){ alert(i18n('Error: ')+e.message); return false; }
  return true;
}

function openFavEditor(f){
  releaseModal();
  const md = document.getElementById('md');
  const have = new Set(FAVORITES.map(x => `${x.device}/${x.code}`));
  const switchDevs = DEVICES.filter(dev => (dev.controls||[]).some(c => c.kind==='switch'));
  md.innerHTML = `
    <div class="head">
      <button class="closebtn" onclick="closeModal()">\u00d7</button>
      <h2>${i18n('Edit favourites')}</h2>
      <div class="sub">${i18n('Name + source (switch / channel)')}</div>
    </div>
    <div class="body">
      <section style="display:flex;gap:10px;justify-content:space-between;align-items:center;
                      padding-top:0;border-top:0">
        <button id="fav-edit-delete" class="dlg-btn danger">${i18n('Delete')}</button>
        <button id="fav-edit-cancel" class="dlg-btn">${i18n('Cancel')}</button>
        <button id="fav-edit-save"   class="dlg-btn primary">${i18n('Save')}</button>
      </section>
      <section>
        <h3>${i18n('Name')}</h3>
        <input id="fav-edit-alias" type="text" value="${escapeHtml(f.alias)}"
               style="width:100%;background:var(--card2);border:1px solid var(--line);
                      color:var(--txt);border-radius:10px;padding:10px 12px;
                      font:inherit;font-size:16px;box-sizing:border-box">
      </section>
      <section>
        <h3>${i18n('Source')}</h3>
        <div class="picker-list">
          ${switchDevs.map(dev=>{
            const switches = (dev.controls||[]).filter(c => c.kind==='switch');
            const isMulti = switches.length > 1;
            return `<div class="picker-dev">
                      <div class="picker-name">${escapeHtml(dev.name)}<span class="picker-meta">${escapeHtml(dev.category||'')}${isMulti?' \u00b7 '+switches.length+i18n(' channels'):''}</span></div>
                      <div class="picker-channels">${
                        switches.map((c, i)=>{
                          const key = `${dev.id}/${c.code}`;
                          const isCurrent = (dev.id === f.device && c.code === f.code);
                          const taken = have.has(key) && !isCurrent;
                          const label = isMulti ? (c.label || String(i+1)) : (c.label || i18n('Power'));
                          const cls = isCurrent ? 'picker-chan active' : (taken ? 'picker-chan exists' : 'picker-chan');
                          return `<button class="${cls}" data-key="${escapeHtml(key)}" ${taken?'disabled':''}>
                                    ${isCurrent?'\u25cf ':(taken?icon('check', 13) + ' ':'')}${escapeHtml(label)}
                                  </button>`;
                        }).join('')
                      }</div>
                    </div>`;
          }).join('')}
        </div>
      </section>
    </div>`;
  document.getElementById('bd').classList.add('show');
  document.body.style.overflow = 'hidden';
  openModalId = '__fav_editor__';

  let selected = {device: f.device, code: f.code};
  md.querySelectorAll('.picker-chan').forEach(btn=>{
    if(btn.classList.contains('exists')) return;
    btn.onclick = ()=>{
      md.querySelectorAll('.picker-chan').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const cut = btn.dataset.key.lastIndexOf('/');   // a code never has a /
      const dev = btn.dataset.key.slice(0, cut), code = btn.dataset.key.slice(cut + 1);
      selected = {device: dev, code};
    };
  });
  md.querySelector('#fav-edit-cancel').onclick = ()=> closeModal();
  md.querySelector('#fav-edit-delete').onclick = async ()=>{
    if(!confirm(i18n('Remove from favourites?'))) return;
    await removeFav(f);
    closeModal();
  };
  md.querySelector('#fav-edit-save').onclick = async ()=>{
    const newAlias = md.querySelector('#fav-edit-alias').value.trim() || f.alias;
    const sourceChanged = (selected.device !== f.device || selected.code !== f.code);
    try{
      if(sourceChanged){
        // add new with new key, then remove old (a failed add loses nothing; preserve position)
        const idx = FAVORITES.findIndex(x => x.device===f.device && x.code===f.code);
        const r = await api(`/api/favorites/${encodeURIComponent(selected.device)}/${encodeURIComponent(selected.code)}`, {
          method:'PUT', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({alias: newAlias})
        });
        await api(`/api/favorites/${encodeURIComponent(f.device)}/${encodeURIComponent(f.code)}`, {method:'DELETE'});
        // splice into local list to preserve position, then reorder on server
        FAVORITES = FAVORITES.filter(x => !(x.device===f.device && x.code===f.code));
        if(idx >= 0){
          FAVORITES.splice(idx, 0, r.favorite);
          await api('/api/favorites/reorder', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({order: FAVORITES.map(x=>`${x.device}/${x.code}`)})
          });
        } else {
          FAVORITES.push(r.favorite);
        }
      } else if(newAlias !== f.alias){
        await renameFav(f, newAlias);
      }
      closeModal();
      renderTabs(); render();
    }catch(e){ alert(i18n('Error: ')+e.message); }
  };
}

function openPicker(){
  releaseModal();
  const md = document.getElementById('md');
  const have = new Set(FAVORITES.map(f => `${f.device}/${f.code}`));
  // Filter devices that have at least one switch-kind control
  const switchDevs = DEVICES.filter(d => (d.controls||[]).some(c => c.kind==='switch'));
  md.innerHTML = `
    <div class="head">
      <button class="closebtn" onclick="closeModal()">\u00d7</button>
      <h2>${i18n('Add to favourites')}</h2>
      <div class="sub">${i18n('Click the channel you need. On multi-gang switches pick the specific channel.')}</div>
    </div>
    <div class="body">
      <div class="picker-list">
        ${switchDevs.map(d=>{
          const switches = (d.controls||[]).filter(c => c.kind === 'switch');
          const isMulti = switches.length > 1;
          return `<div class="picker-dev">
                    <div class="picker-name">${escapeHtml(d.name)}<span class="picker-meta">${escapeHtml(catLabel(d.category))}${isMulti?' \u00b7 '+switches.length+i18n(' channels'):''}</span></div>
                    <div class="picker-channels">${
                      switches.map((c, i)=>{
                        const key = `${d.id}/${c.code}`;
                        const exists = have.has(key);
                        const label = isMulti ? (c.label || String(i+1)) : (c.label || i18n('Power'));
                        const dname = `${d.name} \u00b7 ${label}`;
                        return `<button class="picker-chan ${exists?'exists':''}"
                                        data-key="${escapeHtml(key)}"
                                        data-name="${escapeHtml(dname)}"
                                        ${exists?'disabled':''}>
                                  ${exists?icon('check', 13) + ' ':''}${escapeHtml(label)}
                                </button>`;
                      }).join('')
                    }</div>
                  </div>`;
        }).join('')}
      </div>
    </div>`;
  document.getElementById('bd').classList.add('show');
  document.body.style.overflow = 'hidden';
  md.querySelectorAll('.picker-chan').forEach(btn=>{
    if(btn.classList.contains('exists')) return;
    btn.onclick = async ()=>{
      const cut = btn.dataset.key.lastIndexOf('/');   // a code never has a /
      const device = btn.dataset.key.slice(0, cut), code = btn.dataset.key.slice(cut + 1);
      const ok = await addFavorite(device, code, btn.dataset.name);
      if(ok){ closeModal(); renderTabs(); render(); }
    };
  });
  openModalId = '__picker__';
}

function escapeHtml(s){
  return String(s ?? '').replace(/[&<>"']/g, m=>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}
function el(tag, cls, html){
  const e=document.createElement(tag); if(cls)e.className=cls;
  if(html!=null) e.innerHTML=html; return e;
}

// Inline brightness slider shared by device cards and favorite tiles.
// Card and fav tabs never render at the same time, so reusing the same
// element id (br_<id>_<code>) is collision-free; syncBright() patches it.
function brightRow(d, b){
  const brow=el('div','brightrow');
  brow.innerHTML = `<span class="brlbl" style="font-size:12px;opacity:.75">${escapeHtml(b.label||b.code)}</span><input type="range" min="0" max="100" value="0"
      id="br_${escapeHtml(d.id)}_${escapeHtml(b.code)}"><span class="brpc" id="brpc_${escapeHtml(d.id)}_${escapeHtml(b.code)}">\u2014</span>`;
  brow.onclick = ev=>ev.stopPropagation();
  const sl = brow.querySelector('input');
  // keep slider drags from triggering the card's long-press / fav toggle
  ['pointerdown','pointerup','pointermove'].forEach(ev =>
    sl.addEventListener(ev, e => e.stopPropagation()));
  sl.oninput = ()=>{
    const pc=document.getElementById(`brpc_${d.id}_${b.code}`);
    if(pc) pc.textContent = sl.value+'%';
    pending[d.id+'/'+b.code] = {t:Date.now(), v:+sl.value};
  };
  sl.onchange = ()=>{ sendControl(d.id, b.code, +sl.value); };
  return brow;
}

// One gang of a multi-channel dimmer as a single group: button, bar, percent.
// Anonymous dots in one row plus a detached list of sliders below made it
// guesswork which slider drove which channel.
function dimGroupRow(d, s, b){
  const box = el('div','dimgroup');
  box.onclick = ev=>ev.stopPropagation();
  const row = el('div','brightrow');
  // Channel label sits outside the pill so a 2-gang card stays identifiable
  // ("1"/"Room 1") without stealing room from the three in-pill zones.
  const lbl = el('span','brlbl');
  lbl.style.cssText = 'font-size:12px;opacity:.75';
  lbl.textContent = s.label || '';
  // The pill is a 3-zone rocker: tap left = dimmer, centre = on/off, right =
  // brighter; a drag anywhere sets the level by finger position. "-", the live
  // %, and "+" label the three zones. The yellow fill shows the real level and
  // its visibility is gated by .on (driven by the switch state), so an off
  // channel reads empty. The pill travel spans the channel's usable window
  // [floor, ceil]; the gear opens that window's editor.
  const dot = el('div','dot dimdot dimpill');
  dot.id = `sw_${d.id}_${s.code}`;
  dot.style.cssText = 'width:auto;min-width:104px;max-width:52%;height:34px;'
                    + 'padding:0 10px;border-radius:17px;font-size:13px;'
                    + 'justify-content:space-between;gap:6px;white-space:nowrap;'
                    + 'touch-action:none;cursor:pointer';
  const fill = el('div','dotfill');
  fill.id = `dotfill_${d.id}_${b.code}`;
  const minus = el('span','', '\u2212');
  minus.style.cssText = 'font-weight:700;opacity:.7';
  const pct = el('span','', '');
  pct.id = `dimpc_${d.id}_${b.code}`;               // setDimFill writes % here
  pct.style.cssText = 'font-weight:700;min-width:34px;text-align:center';
  const plus = el('span','', '+');
  plus.style.cssText = 'font-weight:700;opacity:.7';
  dot.append(fill, minus, pct, plus);
  if(isOn(d.id, s.code)) dot.classList.add('on');
  attachDimZones(dot, d, s, b);
  dotKeys(dot, d, s, b);
  setDimFill(d, b, favLevel(d, b));                 // seed % + fill before first state push
  row.append(lbl, dot);
  box.append(row);
  return box;
}

// The group pill's gesture: a plain tap picks a zone (left third dims a step,
// centre toggles on/off, right third brightens a step), while any drag past a
// few px sets the level continuously by finger position along the width. Pointer
// capture follows the finger past the edge; only the centre tap can turn off.
//
// Stepping works on the tenth INDEX of the [floor, ceil] window and rounds to the
// nearest tenth, deliberately not floor/ceil: the lamp echoes back a level a
// point or two off what we sent (30 comes back as 29, 20 as 21), and flooring
// that echo recomputes the very same step, so +/- would sit there refusing to
// move. Rounding absorbs the drift, so every tap advances exactly one tenth.
// A tenth of the channel's window [floor, ceil] up (dir 1) or down (-1) from `base`, snapped to
// the nearest tenth first; the pill's side zones and the arrow keys both step with it.
function dimStep(b, base, dir){
  const f = dimFloor(b), c = dimCeil(b), span = (c - f) || 1;
  if(base < f) return dir > 0 ? Math.max(1, f) : base;   // outside the window: into it, never the other way
  if(base > c) return dir < 0 ? c : base;
  let k = Math.round((base - f) / span * 10);   // nearest tenth, not floor
  k = Math.max(0, Math.min(10, Math.max(0, Math.min(10, k)) + dir));
  let v = Math.round(f + k * span / 10);
  if(dir > 0 ? v <= base : v >= base) v = base + dir;   // a window under 10 points: one point at least
  if(v < f || v > c) return base;               // at the window's end already
  return Math.max(1, v);
}
function attachDimZones(dot, d, s, b){
  let pid = -1, startX = 0, moved = false;
  const posAt = x=>{
    const r = dot.getBoundingClientRect();
    return Math.max(0, Math.min(100, Math.round((x - r.left) / (r.width || 1) * 100)));
  };
  const previewUi = ui=>{
    pending[d.id+'/'+b.code] = {t:Date.now(), v:ui};
    setDimFill(d, b, ui);
  };
  dot.addEventListener('pointerdown', e=>{
    e.stopPropagation();
    pid = e.pointerId; startX = e.clientX; moved = false;
    try{ dot.setPointerCapture(pid); }catch(_){}
  });
  dot.addEventListener('pointermove', e=>{
    if(e.pointerId !== pid) return;
    if(!moved && Math.abs(e.clientX - startX) > 6) moved = true;
    if(moved) previewUi(uiFromPos(b, posAt(e.clientX)));
  });
  dot.addEventListener('pointerup', e=>{
    if(e.pointerId !== pid) return;
    e.stopPropagation();
    try{ dot.releasePointerCapture(pid); }catch(_){}
    pid = -1;
    flushKeySteps(d.id);
    if(moved){                                   // a drag: send the positioned level
      sendControl(d.id, b.code, pending[d.id+'/'+b.code].v);
      if(!switchOn(d.id, s.code)) sendControl(d.id, s.code, true);
      return;
    }
    const r = dot.getBoundingClientRect();
    const rel = (e.clientX - r.left) / (r.width || 1);
    if(rel >= 0.34 && rel <= 0.66){              // centre zone: on/off
      sendControl(d.id, s.code, !switchOn(d.id, s.code));
      return;
    }
    const base = favLevel(d, b), ui = dimStep(b, base, rel < 0.34 ? -1 : 1);
    if(ui === base) return;                      // at the window's end: nothing to send
    sendControl(d.id, b.code, ui);
    if(!switchOn(d.id, s.code)) sendControl(d.id, s.code, true);
    previewUi(ui);
  });
  dot.addEventListener('pointercancel', ()=>{
    try{ dot.releasePointerCapture(pid); }catch(_){}
    pid = -1;
    delete pending[d.id+'/'+b.code];               // never sent: show the real level again
    setDimFill(d, b, favLevel(d, b));
  });
  dot.addEventListener('click', e=>e.stopPropagation());
}

function colorRow(d, c){
  const row = el('div','brightrow');
  const cur = (STATE[d.id] && STATE[d.id].values && STATE[d.id].values[c.code]) || c.default || '#00b0ff';
  row.innerHTML = `<span class="brlbl" style="font-size:12px;opacity:.75">${escapeHtml(c.label||i18n('Colour'))}</span>`;
  const inp = document.createElement('input');
  inp.type = 'color'; inp.id = `col_${d.id}_${c.code}`; inp.value = cur;
  inp.style.cssText = 'flex:1;min-width:0;height:28px;padding:0;border:0;background:none;cursor:pointer';
  ['pointerdown','pointerup','pointermove','click'].forEach(ev => inp.addEventListener(ev, e => e.stopPropagation()));
  inp.onchange = () => { sendControl(d.id, c.code, inp.value); };
  row.append(inp);
  row.onclick = ev => ev.stopPropagation();
  return row;
}

function syncBright(d, b, val){
  if(val==null) return;
  const p=pending[d.id+'/'+b.code];
  if(p && Date.now()-p.t < 4000) return;   // honour just-dragged value
  const span=(b.max-b.min)||1;
  const ui=Math.max(0,Math.min(100,Math.round((val-b.min)/span*100)));
  const sl=document.getElementById(`br_${d.id}_${b.code}`);   // extra card slider, if any
  if(sl){ sl.value=ui; const pc=document.getElementById(`brpc_${d.id}_${b.code}`); if(pc) pc.textContent=ui+'%'; }
  setDimFill(d, b, ui);                                       // circle fill (fav + card)
}

// Per-channel usable brightness window. The pill's 0..100 travel maps into
// [floor, ceil] (raw UI percent), because the LED wired to a gang may only light
// from ~15% and reach full by ~60%. Unset => full 0..100, i.e. identity.
function dimFloor(b){ return Math.max(0, Math.min(99, +b.floor||0)); }
function dimCeil(b){ const c=(b.ceil==null?100:+b.ceil); return Math.max(dimFloor(b)+1, Math.min(100, c)); }
function hasDimWindow(b){ return dimFloor(b)>0 || dimCeil(b)<100; }
function posFromUi(b, ui){ const f=dimFloor(b), c=dimCeil(b); return Math.max(0, Math.min(100, Math.round((ui-f)/(c-f)*100))); }
function uiFromPos(b, pos){ const f=dimFloor(b), c=dimCeil(b); return Math.max(1, Math.min(100, Math.round(f + pos/100*(c-f)))); }

// brightness level 0-100, honouring an in-flight drag
function favLevel(d, b){
  // a level the user just set wins for a moment (the device has not echoed it yet);
  // after that the reported level counts, or +/- would keep stepping from an old value
  const key=d.id+'/'+b.code, p=pending[key];
  if(p && Date.now()-p.t < 3000) return p.v;
  if(p) delete pending[key];
  const val=STATE[d.id]?.values?.[b.code];
  if(val==null) return 0;
  const span=(b.max-b.min)||1;
  return Math.max(0,Math.min(100,Math.round((val-b.min)/span*100)));
}

// fill the dimmer CIRCLE to the level; floor visual at 12% so 1% is clearly on.
// % shows as a small label below the circle. Fill opacity is gated by .on in CSS.
function setDimFill(d, b, lvl){
  // an ON light parked at the device floor brightness computes 0% -- show it
  // as 1% so the circle fills and the % label reads "on" instead of blank.
  if(lvl===0){
    const vals=STATE[d.id]?.values;
    // the switch on THIS gang, not simply the first one
    const sw=ctlForCode(d, b.code, 'switch') || controlsOf(d).switches[0];
    if(vals && sw && vals[sw.code]) lvl=1;
  }
  const fill=document.getElementById(`dotfill_${d.id}_${b.code}`);
  let disp = lvl;                    // what the % readout and pill width show
  if(fill){
    if(fill.parentElement && fill.parentElement.classList.contains('dimpill')){
      // horizontal pill: fills across its usable window, so a level at floor reads
      // 0% and at ceil reads 100% -- the whole travel is meaningful
      disp = hasDimWindow(b) ? posFromUi(b, lvl) : lvl;
      fill.style.width = (disp>0 ? disp : 0) + '%';
    } else {
      // round dot: floor the visual at 12% so 1% still reads as "on"
      fill.style.height = (lvl>0 ? Math.max(lvl,12) : 0) + '%';
    }
  }
  const pc=document.getElementById(`dimpc_${d.id}_${b.code}`);
  if(pc) pc.textContent = lvl>0 ? disp+'%' : '';
}

// Dimmer gesture on the round control: vertical swipe = brightness (min 1%),
// tap = toggle, long-press = optional (editor on fav tiles). Fixed sensitivity
// so the small circle is still easy to sweep; pointer capture follows the finger
// beyond the circle. stopPropagation keeps the card's modal-open quiet.
// The keyboard (docs/device-state.md): Tab reaches every card and each switch dot on it. Enter / Space
// on a card opens its window, on a dot switches it; on a dimmer's dot the arrows step a tenth. A dot of
// an offline device is out of the tab order and does nothing.
// What a card says of its device's life to the keyboard and a screen reader: an offline card's
// controls leave the tab order and say they are disabled; a focused one hands the focus to the card.
function markLife(card, d, life){
  const off = life === 'offline';
  if(off && card !== document.activeElement && card.contains(document.activeElement)) card.focus();
  if(!card._face){                               // a plug-in face manages its own controls
    for(const w of card.querySelectorAll('.dotwrap, .dimpill, .toggle')){
      w.tabIndex = off ? -1 : 0;
      w.setAttribute('aria-disabled', String(off));
    }
    for(const w of card.querySelectorAll('input, select')) w.disabled = off;
  }
  card.setAttribute('aria-label', d.name + (life === 'live' ? '' : ', ' + i18n(off ? 'offline' : 'waiting for report')));
}
function cardKeys(card, d){
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  card.setAttribute('aria-label', d.name);
}
const _keyStep = new Map();  // dev -> Map(brightness code -> {timer, go}): an arrow's level about to go out
const _swIntent = {};        // dev/switch code -> {on, t}: what sendControl last asked of a switch
// The switch as last asked while that command is in flight (its first 1.5 s, or while the core lists
// it in cmd_wait); after the device's report, the report. Every path asks through sendControl.
function switchOn(id, code){
  const i = _swIntent[id + '/' + code];
  if(!i) return isOn(id, code);
  const age = Date.now() - i.t, w = STATE[id] && STATE[id].cmd_wait;
  return age < 1500 || (age < 5000 && w && code in w) ? i.on : isOn(id, code);
}
function flushKeySteps(devId){                   // another action on the device: the waiting step goes now
  const m = _keyStep.get(devId);
  if(!m) return;
  _keyStep.delete(devId);
  for(const p of m.values()){ clearTimeout(p.timer); p.go(); }
}
function dotKeys(wrap, d, s, dimB){
  if(!wrap.id) wrap.id = `swk_${d.id}_${s.code}`;
  wrap.tabIndex = 0;
  wrap.setAttribute('role', 'switch');
  wrap.setAttribute('aria-label', d.name + (s.label ? ' ' + s.label : ''));
  if(dimB) wrap.setAttribute('aria-describedby', `dimpc_${d.id}_${dimB.code}`);   // the level is read out
  wrap.addEventListener('keydown', ev=>{
    const card = document.getElementById('c_' + d.id);
    const dir = {ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1}[ev.key];
    if(!(ev.key === 'Enter' || ev.key === ' ' || (dimB && dir))) return;
    ev.preventDefault(); ev.stopPropagation();
    if(card && card.classList.contains('offline')) return;
    if(!dir){
      if(ev.repeat) return;                     // a held key switches once
      flushKeySteps(d.id);
      sendControl(d.id, s.code, !switchOn(d.id, s.code));
      return;
    }
    const base = favLevel(d, dimB) || 0, lvl = dimStep(dimB, base, dir);
    if(lvl === base) return;                    // at the window's end: nothing to send
    pending[d.id + '/' + dimB.code] = {t: Date.now(), v: lvl};
    setDimFill(d, dimB, lvl);
    const key = d.id + '/' + dimB.code;
    let m = _keyStep.get(d.id);
    if(!m) _keyStep.set(d.id, m = new Map());
    const prev = m.get(dimB.code);
    if(prev) clearTimeout(prev.timer);          // a held or repeated arrow sends its last level once
    const go = ()=>{
      const c = document.getElementById('c_' + d.id);
      if(c && c.classList.contains('offline')){  // gone meanwhile: nothing goes out, the real level shows
        delete pending[key]; setDimFill(d, dimB, favLevel(d, dimB)); return;
      }
      sendControl(d.id, dimB.code, lvl);
      if(!switchOn(d.id, s.code)) sendControl(d.id, s.code, true);    // as the pill: a step switches on
    };
    m.set(dimB.code, {go, timer: setTimeout(()=>{ m.delete(dimB.code); go(); }, 250)});
  });
}
function attachDimmerDot(dot, d, b, toggleFn, longpressFn){
  let pid=-1, startY=0, startLvl=0, moved=false, lp=false, lpTimer=null;
  const SENS=160;   // px of vertical drag for a full 0->100 sweep
  dot.style.touchAction='none';
  const calc=y=>Math.max(1,Math.min(100,Math.round(startLvl + (startY-y)/SENS*100)));
  dot.addEventListener('pointerdown', e=>{
    e.stopPropagation();
    pid=e.pointerId; startY=e.clientY; moved=false; lp=false;
    startLvl=favLevel(d,b)||1;
    try{ dot.setPointerCapture(pid); }catch(_){}
    if(longpressFn) lpTimer=setTimeout(()=>{ lp=true; longpressFn(); }, 600);
  });
  dot.addEventListener('pointermove', e=>{
    if(e.pointerId!==pid) return;
    if(!moved && Math.abs(startY-e.clientY)>6){ moved=true; clearTimeout(lpTimer); }
    if(moved){ const lvl=calc(e.clientY); pending[d.id+'/'+b.code]={t:Date.now(),v:lvl}; setDimFill(d,b,lvl); }
  });
  dot.addEventListener('pointerup', e=>{
    if(e.pointerId!==pid) return;
    e.stopPropagation(); clearTimeout(lpTimer);
    try{ dot.releasePointerCapture(pid); }catch(_){}
    pid=-1;
    if(lp) return;
    if(!moved && Math.abs(startY-e.clientY)>6){ const lvl=calc(e.clientY); pending[d.id+'/'+b.code]={t:Date.now(),v:lvl}; setDimFill(d,b,lvl); moved=true; }
    const lvl = moved ? pending[d.id+'/'+b.code].v : null;
    flushKeySteps(d.id);                          // a waiting arrow step goes first: this touch is the last word
    if(lvl !== null) sendControl(d.id, b.code, lvl);
    else toggleFn();
  });
  dot.addEventListener('pointercancel', ()=>{ clearTimeout(lpTimer); pid=-1; });
  dot.addEventListener('click', e=>e.stopPropagation());
}

// Gang suffix of a control code: 'state_l2' -> 'l2', 'brightness' -> ''.
function chanOf(code){
  for(const base of ['state','brightness'])
    if(String(code||'').startsWith(base))
      return String(code).slice(base.length).replace(/^_/,'');
  return '';
}

// The control of `kind` on the SAME gang as `code`. A multi-gang dimmer has one
// brightness per channel, so a favourite tile on gang 2 must not fill from gang
// 1's level. Returns null rather than a wrong channel.
function ctlForCode(d, code, kind){
  const list = (d.controls||[]).filter(c=>c.kind===kind);
  if(!list.length) return null;
  const want = chanOf(code);
  if(want) return list.find(c => chanOf(c.code) === want) || null;
  // Fall back to "the one obvious control" ONLY when there is nothing to confuse it
  // with. A card with two switches (power + backlight) used to pair BOTH with the
  // first slider, and rendered as a two-gang dimmer that drove one channel twice.
  const sw = (d.controls||[]).filter(c => c.kind === 'switch' && !c.card_toggle);
  return (list.length === 1 && sw.length === 1) ? list[0] : null;
}
function brightForCode(d, code){ return ctlForCode(d, code, 'bright'); }

function controlsOf(d){
  const k = type => d.controls.filter(c=>c.kind===type);
  return {
    switches: k('switch'),
    brights:  k('bright'),
    colors:   k('color'),
    sensors:  k('sensor'),
    sens_txt: k('sensor_text'),
    set_int:  k('setting_int'),
    set_enum: k('setting_enum'),
    set_bool: k('setting_bool'),
    trig_txt: k('trigger_text'),
  };
}

// "last card: open, 12:04" - the newest event of a button or card device, in words
function lastPressText(d, st){
  const ev = st && st.last_event;
  if(!ev || !st.last_event_ts) return i18n('nothing pressed yet');
  const v = String(ev.value == null ? '' : ev.value), word = whenGesture(v, d.id);
  const when = new Date(st.last_event_ts * 1000), today = new Date().toDateString() === when.toDateString(),
    yest = new Date(Date.now() - 86400000).toDateString() === when.toDateString();
  const day = today ? '' : yest ? i18n('yesterday') + ' ' : when.toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit'}) + ' ';
  return i18n(ev.code === 'card' ? 'last card: {0}, {1}' : 'last press: {0}, {1}', word, day + hhmm(st.last_event_ts));
}

function autoVis(d){
  const auto = isOn(d.id,'auto');
  document.querySelectorAll(`.brightrow[data-dev="${CSS.escape(d.id)}"][data-when]`).forEach(r=>{
    r.style.display = ((r.dataset.when==='auto') === !!auto) ? '' : 'none';
  });
}

