// Home map (owner only): the floor plan, the devices and loads on it, what controls what. Labels are English source text; i18n.json translates them.
(() => {
  const LOADED = (document.currentScript && new URL(document.currentScript.src).searchParams.get('v')) || '';       // the build this page loaded
  const esc = Home.escapeHtml;
  let redraw = null;                                   // the open map's own redraw (on a state frame)
  let owner = false;                                   // set by the first call: a non-owner gets 403 and never sees the tab
  let parts = {}, partOf = new Set();                  // primary -> its parts (a remote that is part of a TV or an AC), and every part
  // one inline icon set (24x24, stroke): the same on every device, no emoji font; a switch must not look like the lamp it drives
  const SVG = {
    lamp: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1v.5h5v-.5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
    switch: '<rect x="5" y="3" width="14" height="18" rx="2"/><rect x="9" y="7" width="6" height="10" rx="1"/><path d="M9 12h6"/>',
    dimmer: '<rect x="5" y="3" width="14" height="18" rx="2"/><circle cx="12" cy="12" r="3.5"/><path d="M12 8.5V10.5"/>',
    socket: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9.5 10v2M14.5 10v2M12 15v1.5"/>',
    led: '<path d="M3 15c3-8 6 8 9 0s6 8 9 0"/><path d="M5 5v2M12 4v2M19 5v2"/>',
    motion: '<circle cx="13" cy="4.5" r="1.8"/><path d="M12 8l-2.5 4 3 2v5M9.5 12l-3 1M12 8l3.5 2 2.5-.5M12.5 14l2.5 5"/>',
    climate: '<path d="M10 14V5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0z"/><path d="M12 9v8"/>',
    door: '<rect x="6" y="3" width="12" height="18" rx="1"/><circle cx="15" cy="12" r=".9"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    unlock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-1.8"/>',
    ac: '<rect x="3" y="5" width="18" height="7" rx="2"/><path d="M6 16c0 2 2 2 2 4M12 16c0 2 2 2 2 4M18 16c0 2 2 2 2 4"/>',
    tv: '<rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8M12 17v4"/>',
    blaster: '<rect x="3" y="13" width="11" height="6" rx="1.5"/><path d="M15 12a4 4 0 0 1 5 0M16.5 9a8 8 0 0 1 7 0"/>',
    speaker: '<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
    panel: '<rect x="4" y="3" width="16" height="18" rx="2"/><circle cx="12" cy="18" r="1"/>',
    phone: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
    button: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
    fan: '<circle cx="12" cy="12" r="1.5"/><path d="M12 10.5C12 6 14 4 16 4s2 3-1 5M13.5 12c4.5 0 6.5 2 6.5 4s-3 2-5-1M12 13.5c0 4.5-2 6.5-4 6.5s-2-3 1-5M10.5 12C6 12 4 10 4 8s3-2 5 1"/>',
    boiler: '<rect x="6" y="3" width="12" height="18" rx="4"/><path d="M12 8v3M10 15h4"/>',
    other: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/>',
    fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    stack: '<rect x="8" y="3" width="12" height="12" rx="2"/><path d="M5 8v10a2 2 0 0 0 2 2h10"/>',
    menu: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
  };
  const ico = (k, px = 20) => `<svg viewBox="0 0 24 24" width="${px}" height="${px}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" style="display:block">${SVG[k] || SVG.other}</svg>`;
  const WALL_KINDS = ['switch', 'dimmer', 'socket', 'blaster', 'button', 'lock'];            // things that hang on a wall: a drop snaps them to its line
  const KIND_EN = {lamp: 'Lamps', switch: 'Switches', dimmer: 'Dimmers', socket: 'Sockets', lock: 'Locks', ac: 'Air conditioners', tv: 'TVs', blaster: 'IR blasters', motion: 'Motion sensors', climate: 'Thermometers', door: 'Door sensors', button: 'Buttons', speaker: 'Speakers', panel: 'Panels and tablets', other: 'Other'};
  const KIND1_EN = {lamp: 'Lamp', switch: 'Switch', dimmer: 'Dimmer', socket: 'Socket', lock: 'Lock', ac: 'Air conditioner', tv: 'TV', blaster: 'IR blaster', motion: 'Motion sensor', climate: 'Thermometer', door: 'Door sensor', button: 'Button', speaker: 'Speaker', panel: 'Panel', other: 'Device'};
  const LOAD_EN = {lamp: 'Lamp', socket: 'Socket', ac: 'Air conditioner', tv: 'TV', boiler: 'Boiler', led: 'LED strip', fan: 'Fan', other: 'Other'};
  const kindName = k => i18n(KIND_EN[k] || 'Other'), kind1 = k => i18n(KIND1_EN[k] || 'Device'), loadName = t => i18n(LOAD_EN[t] || 'Other');
  const KIND_NAMES = {lamp: 1, switch: 1, dimmer: 1, socket: 1, lock: 1, ac: 1, tv: 1, blaster: 1, motion: 1, climate: 1, door: 1, button: 1, speaker: 1, panel: 1};
  const uid = () => 'i' + Math.random().toString(36).slice(2, 9);
  // a path through the points a..b of `pts` (fractions of the plan) in plan pixels: rounded (Catmull-Rom as Beziers, the neighbours of the ends are the points beside them) or straight
  const pathD = (pts, a, b, smooth, w, h) => {
    const P = i => { const p = pts[Math.max(0, Math.min(pts.length - 1, i))]; return [p.x * w, p.y * h]; };
    let d = `M${P(a)[0]},${P(a)[1]}`;
    for(let i = a; i < b; i++){
      const p0 = P(i - 1), p1 = P(i), p2 = P(i + 1), p3 = P(i + 2);
      d += smooth ? ` C${p1[0] + (p2[0] - p0[0]) / 6},${p1[1] + (p2[1] - p0[1]) / 6} ${p2[0] - (p3[0] - p1[0]) / 6},${p2[1] - (p3[1] - p1[1]) / 6} ${p2[0]},${p2[1]}` : ` L${p2[0]},${p2[1]}`;
    }
    return d;
  };
  // the colour of a strip (or a part of it) from the live state of the channel that controls it: off dark, on warm, dimmed by the brightness of that channel
  const ledColour = (dev, code) => {
    const st = Home.state(dev);
    if(!st || st.online === false) return '#555b66';
    const v = st.values || {};
    const on = code ? !!v[code] : Object.values(v).some(x => x === true);
    const sfx = (code || '').replace(/^state_?/, '');
    const b = v[sfx ? 'brightness_' + sfx : 'brightness'];
    const lvl = typeof b === 'number' ? Math.max(0.35, Math.min(1, (b > 100 ? b / 255 : b / 100))) : 1;
    return on ? `rgba(255,210,77,${lvl})` : '#2f3540';
  };
  const probe = async () => { try { await Home.api('/api/map'); owner = true; } catch(e){ owner = false; } };
  const loadParts = async () => { try { parts = await Home.api('/api/map/parts'); } catch(e){ parts = {}; } partOf = new Set(Object.values(parts).flat()); };

  // opts (MapScene): {data, cands, rooms, auto, wiring} a copy to show instead of the house's map; readonly: no taps, nothing stored
  async function render(box, opts = {}){
    const SHOW_ONLY = !!opts.readonly;
    box.innerHTML = '<div class="bindcaption">&hellip;</div>';
    let data, cands = [], rooms = [];
    try {
      data = opts.data ? JSON.parse(JSON.stringify(opts.data)) : await Home.api('/api/map');
      cands = opts.cands || await Home.api('/api/map/candidates');
      rooms = opts.rooms || await Home.api('/api/rooms').catch(() => []);
    } catch(e){ box.textContent = e.message || String(e); return; }
    if(SHOW_ONLY) box.style.pointerEvents = 'none';                  // a scene: no sheet, no tool, nothing that could write
    const stale = LOADED && data.ui_v && String(data.ui_v) !== LOADED;      // the map screen changed on the house since this page loaded it
    const noEmptyPaths = () => { for(const it of data.items) if(it.path && !it.path.length) delete it.path; };      // only a strip has a path: [] from the house is no path
    noEmptyPaths();
    data.walls = data.walls || []; data.openings = data.openings || [];      // walls inside a zone, doors and windows
    const imgs = {};                                   // plan key -> blob URL
    const S = {live: localStorage.getItem('map.live') !== '0', sig: {}, mode: '', pending: null, poly: [], pathItem: null, seg: null, scale: 1, ox: 0, oy: 0, dirty: false, saving: false, auto: [], wiring: [],
      wires: localStorage.getItem('map.wires') !== '0', radio: localStorage.getItem('map.radio') !== '0'};      // the two layers: kept per browser
    const plan = () => data.scheme || null;                 // {w, h} of the house's scheme: the frame of every coordinate
    const placedDev = () => new Set(data.items.filter(i => i.kind === 'device').map(i => i.dev));
    const unplaced = () => cands.filter(c => !placedDev().has(c.id));
    const dispName = c => (((Home.devices && Home.devices()) || []).find(d => d.id === c.id) || {}).name || c.name;      // the name the dashboard shows
    const roomOfC = c => (Home.roomOf ? Home.roomOf(c.id) : '') || c.room || '';
    const mapLabel = it => {                                         // the name on the plan: the house's short label, else the name without a "(room)" that the zone already says
      const c = cands.find(x => x.id === it.dev) || {};
      if(it.kind !== 'load' && c.label) return c.label;
      const n = nameOf(it), m = /^(.*\S)\s*\(([^()]*)\)\s*$/.exec(n), z = zoneAt(it);
      return m && z && m[2].trim().toLowerCase() === String(z.name).trim().toLowerCase() ? m[1] : n;
    };
    const nameOf = it => it.kind === 'load' ? it.name : (dispName(cands.find(c => c.id === it.dev) || {id: it.dev, name: it.dev}));
    const iconKey = it => it.kind === 'load' ? (SVG[it.type] ? it.type : 'other') : ((cands.find(c => c.id === it.dev) || {}).category in SVG ? (cands.find(c => c.id === it.dev) || {}).category : 'other');
    const iconOf = it => ico(iconKey(it), 20);

    box.innerHTML = `${stale ? `<div class="bindcaption" style="padding:8px 0;color:#ffb020">${esc(i18n('The page runs an older map screen: reload it (Ctrl+F5)'))} <button class="dlg-btn" onclick="location.reload()">${esc(i18n('Reload'))}</button></div>` : ''}<style>.mapbar,[data-m=view],[data-m=view] *{-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}.mapbar .sec{display:contents}.mapbar .moreb{display:none}.mapbar.narrow .moreb{display:inline-block}.mapbar.narrow .sec{display:none}.mapbar.narrow.open .sec{display:flex;gap:8px;flex-wrap:wrap;align-items:center;flex-basis:100%}.mb,.mn{display:none}[data-m=stage].big .mb,[data-m=stage].big .mn,.mn.always,[data-id]:hover .mn{display:block}.mn.always{font-size:max(11px,calc(11px * var(--one,1) / var(--inv,1)))!important}[data-id]:hover .mn{visibility:visible!important;z-index:9}@keyframes mpulse{0%{box-shadow:0 0 0 0 rgba(61,220,132,.8)}100%{box-shadow:0 0 0 16px rgba(61,220,132,0)}}@keyframes mwave{0%{box-shadow:0 0 0 0 rgba(74,163,255,.8)}100%{box-shadow:0 0 0 14px rgba(74,163,255,0)}}</style><div class="mapbar" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
        <button class="dlg-btn" data-m="fit" title="${esc(i18n('Fit the plan'))}" style="padding:4px 8px">${ico('fit', 18)}</button>
        <button class="dlg-btn" data-m="left" title="${esc(i18n('The things not on the plan yet: pick one, then tap its spot.'))}" style="padding:4px 12px;font-size:15px"></button><button class="dlg-btn moreb" data-m="more" style="padding:4px 8px" title="${esc(i18n('More'))}">${ico('menu', 18)}</button>
        <span class="sec"><label style="font-size:14px"><input type="checkbox" data-m="live" ${S.live ? 'checked' : ''}> ${esc(i18n('State'))}</label><button class="dlg-btn" data-m="legend" title="${esc(i18n('What each icon means.'))}" style="padding:4px 10px">${esc(i18n('Legend'))}</button>
        <label style="font-size:14px"><input type="checkbox" data-m="wires" ${S.wires ? 'checked' : ''}> ${esc(i18n('Wires'))}</label>
        <label style="font-size:14px"><input type="checkbox" data-m="radio" ${S.radio ? 'checked' : ''}> ${esc(i18n('IR'))}</label>
        <button class="dlg-btn" data-m="underlay" title="${esc(i18n('A photo or a drawing of the flat under the rooms, to draw them along it.'))}" style="padding:4px 10px">${esc(i18n('Plan of the flat'))}</button>
        <button class="dlg-btn" data-m="walltool" style="padding:4px 10px" title="${esc(i18n('Walls inside rooms'))}">${esc(i18n('Walls'))}</button>
        <button class="dlg-btn" data-m="optool" style="padding:4px 10px" title="${esc(i18n('Doors and windows: draw one along a wall'))}">${esc(i18n('Openings'))}</button>
        <button class="dlg-btn" data-m="newroom" style="padding:4px 10px" title="${esc(i18n('A new room: a small outline in the middle of the plan, to drag to its place'))}">${esc(i18n('New room'))}</button>
        </span><span data-m="state" class="bindcaption"></span></div>
      <div data-m="view" style="position:relative;overflow:hidden;border:1px solid var(--line);border-radius:10px;height:70vh;background:#0b0e13;touch-action:none">
        <div data-m="stage" style="position:absolute;left:0;top:0;transform-origin:0 0"><img data-m="img" draggable="false" style="display:block;width:100%;user-select:none"><img data-m="ul" draggable="false" alt="" style="display:none">
          <svg data-m="svg" style="position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none" preserveAspectRatio="none"></svg><div data-m="icons"></div></div>
        <div data-m="legend" style="display:none;position:absolute;right:8px;bottom:8px;background:var(--card);padding:8px 10px;border-radius:8px;font-size:13px;line-height:1.6;max-width:260px"></div>
        <div data-m="hint" class="bindcaption" style="position:absolute;left:8px;top:8px;background:var(--card);padding:6px 10px;border-radius:8px;display:none;font-size:14px;font-style:normal;color:#e6edf5"></div></div>
      <div data-m="sheet" style="display:none"></div>`;
    const q = a => box.querySelector(`[data-m="${a}"]`);
    const view = q('view'), stage = q('stage'), sheet = q('sheet');
    box.style.position = 'relative';
    view.addEventListener('selectstart', e => e.preventDefault());                                           // a long press selects no label
    const wide = () => box.clientWidth >= 900;
    const setRot = () => { const r = box.clientWidth < 700 && window.innerHeight > window.innerWidth ? 1 : 0; const was = S.rot; S.rot = r; return r !== was; };       // a portrait phone: the scheme is turned
    setRot();
    const layoutBar = () => box.querySelector('.mapbar').classList.toggle('narrow', box.clientWidth < 600);
    layoutBar();
    q('more').onclick = () => box.querySelector('.mapbar').classList.toggle('open');
    if(window.RESTRICTED) q('underlay').style.display = 'none';                                              // restricted: no picture from outside
    const ownerTools = () => { if(window.RESTRICTED) ['left', 'walltool', 'optool'].forEach(k => { q(k).style.display = EDIT() ? '' : 'none'; }); };   // while it may edit
    const sizeView = () => {                                       // a phone: the pane is as tall as the plan is at the width of the screen; else all that is left
      const free = window.innerHeight - view.getBoundingClientRect().top - 14, p = plan();
      view.style.height = Math.max(200, p && view.clientWidth < 700 && !S.rot ? Math.min(free, view.clientWidth * p.h / p.w * 1.04) : Math.max(S.rot ? 300 : 320, free)) + 'px';
    };

    // ---- saving: every change is sent whole after a short pause, with the revision it started from ----
    let timer = null;
    const KEY = {rooms: r => r.id, items: i => i.id, links: l => [l.load, l.dev, l.code, (l.seg || []).join('-')].join('|'), walls: w => w.id, openings: o => o.id};
    const snap = () => JSON.parse(JSON.stringify({rooms: data.rooms, items: data.items, links: data.links, walls: data.walls, openings: data.openings}));
    let base = snap();                                  // what the house had when this tab last loaded or saved
    // the house's map changed under us (another tab): what this tab changed since `base` (added, changed, removed) is laid over the new one
    function rebase(srv){
      for(const k of Object.keys(KEY)){
        const f = KEY[k], was = new Map((base[k] || []).map(x => [f(x), JSON.stringify(x)])), mine = new Map((data[k] || []).map(x => [f(x), x]));
        const out = new Map((srv[k] || []).map(x => [f(x), x]));
        for(const [id, x] of mine) if(was.get(id) !== JSON.stringify(x)) out.set(id, x);
        for(const id of was.keys()) if(!mine.has(id)) out.delete(id);
        data[k] = [...out.values()];
      }
      data.rev = srv.rev; noEmptyPaths();
    }
    const changed = () => {
      if(SHOW_ONLY){ draw(); return; }
      const n0 = data.openings.length; data.openings = data.openings.filter(onAnyWall); S.opGone = (S.opGone || 0) + n0 - data.openings.length;   // a room deleted, merged or redrawn: its doors go with its walls
      S.dirty = true; S.gen = (S.gen || 0) + 1; q('state').textContent = '\u2026'; q('state').title = ''; clearTimeout(timer); timer = setTimeout(save, 700); draw(); };
    const put = async () => {
      const sent = snap(), gen = S.gen || 0, sentOps = new Set(sent.openings.map(o => o.id));
      const r = await Home.api('/api/map', {method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({rev: data.rev, rooms: data.rooms, items: data.items, links: data.links, walls: data.walls, openings: data.openings})});
      data.rev = r.rev; base = sent; S.dirty = (S.gen || 0) !== gen;
      if(Home.refreshTabs) Home.refreshTabs();                         // the tab bar follows the zones at once
      for(const z of r.zones || []){ const mine = data.rooms.find(x => x.id === z.id); if(mine) mine.house_room = z.house_room; }       // the room each zone became
      q('state').onclick = null; q('state').title = '';
      q('state').textContent = (r.kept || []).length ? i18n('Kept, a rule names it') + ': ' + r.kept.map(k => k.room).join(', ') : i18n('Saved');
      if(r.openings_dropped){                                          // the house dropped some of what was sent: those go here too, a newer edit stays
        const kept = new Set(((await Home.api('/api/map')).openings || []).map(o => o.id));
        data.openings = data.openings.filter(o => kept.has(o.id) || !sentOps.has(o.id));
      }
      const gone = (S.opGone || 0) + (r.openings_dropped || 0); S.opGone = 0;
      if(gone){ q('state').textContent = i18n('{0} openings removed: no wall under them', gone); draw(); }
    };
    async function save(){
      if(SHOW_ONLY || !EDIT()) return;                                // restricted: only the session a plug-in lets edit saves
      if(S.saving){ timer = setTimeout(save, 400); return; }
      S.saving = true;
      try {
        try { await put(); }
        catch(e){
          if(e.status !== 409 && !/changed meanwhile/.test(e.detail || e.message || '')) throw e;
          rebase(await Home.api('/api/map')); await put(); draw();           // once more on top of the new revision
        }
      } catch(e){
        const msg = e.message || String(e), st = q('state');
        if(msg.length > 40) st.innerHTML = esc(i18n('Not saved')) + ' ' + (Home.icon ? Home.icon('info', 14) : ''); else st.textContent = msg; st.title = msg;          // a long answer: the tooltip, and a tap shows it (a tablet cannot hover)
        st.onclick = msg.length > 40 ? () => { st.textContent = msg; st.onclick = null; } : null;
        if(/a room named/.test(e.detail || e.message || '')){ data.rooms = JSON.parse(JSON.stringify(base.rooms)); S.dirty = false; draw(); }       // the name stays what it was
      }
      S.saving = false;
    }
    const flush = async () => {                                   // before anything that takes the house's copy: send what is waiting
      clearTimeout(timer);
      while(S.saving) await new Promise(r => setTimeout(r, 50));
      if(S.dirty) await save();
    };

    // ---- the plan ----
    async function loadImage(){                           // the frame on a grid; the plan of the flat (the underlay) under the zones while it is shown
      const p = plan();
      if(!p){ box.innerHTML = `<div class="bindcaption" style="padding:16px">${esc(i18n('The house has no scheme'))}</div>`; return false; }
      q('img').style.display = 'none';
      stage.style.width = p.w + 'px'; stage.style.height = p.h + 'px';
      stage.style.backgroundImage = 'linear-gradient(#1c222b calc(var(--gridw, 1) * 1px), transparent calc(var(--gridw, 1) * 1px)), '
        + 'linear-gradient(90deg, #1c222b calc(var(--gridw, 1) * 1px), transparent calc(var(--gridw, 1) * 1px))';      // --gridw: one device pixel at any zoom
      stage.style.backgroundSize = '50px 50px';
      await paintUnderlay();
      fit();
      draw();
      const want = sessionStorage.getItem('map.focus');                  // "Show on the map" in a device window
      if(want){ sessionStorage.removeItem('map.focus'); focusDevice(want); }
      return true;
    }
    // the underlay: its own picture, placed (x, y, w, h in frame units), turned around its middle, see-through; a house of before has its scheme
    async function paintUnderlay(){
      const u = data.underlay, el = q('ul'), p = plan();
      const legacy = !u && data.scheme_img !== false, src = legacy ? '/api/map/scheme' : '/api/map/underlay';
      if(!p || !(legacy || (u && u.show))){ el.style.display = 'none'; return; }
      const k = legacy ? 'scheme' : u.file + ':' + (u.v || '');
      if(!imgs[k]){
        const r = await fetch(src, {headers: Home.authHeaders()});
        if(!r.ok){ el.style.display = 'none'; return; }
        imgs[k] = URL.createObjectURL(await r.blob());
      }
      const g = legacy ? {x: 0, y: 0, w: p.w, h: p.h, rot: 0, opacity: 1} : u;
      el.src = imgs[k];
      el.style.cssText = `display:block;position:absolute;left:${g.x / p.w * 100}%;top:${g.y / p.h * 100}%;width:${g.w / p.w * 100}%;height:${g.h / p.h * 100}%;transform:rotate(${g.rot || 0}deg);opacity:${g.opacity == null ? 0.6 : g.opacity};pointer-events:none;user-select:none`;
    }
    async function underlayPatch(ch){
      const r = await Home.api('/api/map/underlay', {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(ch)});
      data.underlay = r.underlay; data.rev = r.rev; data.scheme_img = !!r.underlay.show;
      await paintUnderlay(); await loadWalls(); draw();
    }
    // the sheet "Plan of the flat": upload, shown or not, opacity, size, turn, a drag to move, walls from an SVG plan, remove
    function underlaySheet(msg){
      const u = data.underlay, p = plan();
      const range = (k, lo, hi, step, v, label) => `<div class="row" style="gap:8px"><span class="rlabel">${esc(i18n(label))}</span><input type="range" data-u="${k}" min="${lo}" max="${hi}" step="${step}" value="${v}" style="flex:1"><span data-uv="${k}" style="min-width:44px;text-align:right">${v}</span></div>`;
      openSheet(`<div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">${esc(i18n('Plan of the flat'))}</h3><button class="dlg-btn" data-s="close">&times;</button></div>
        <div class="bindcaption" style="margin:6px 0">${esc(i18n('A photo or a drawing of the flat under the rooms, to draw them along it. PNG, JPG or SVG up to 8 MB.'))}</div>
        <div class="row"><input type="file" data-u="file" accept="image/png,image/jpeg,image/svg+xml"></div>
        ${u ? `<div class="row"><label><input type="checkbox" data-u="show" ${u.show ? 'checked' : ''}> ${esc(i18n('Show the plan'))}</label></div>
          ${range('opacity', 10, 100, 5, Math.round((u.opacity == null ? .6 : u.opacity) * 100), 'Opacity, %')}
          ${range('size', 10, 300, 1, Math.round(u.w / p.w * 100), 'Size, % of the map')}
          ${range('rot', -180, 180, 0.5, u.rot || 0, 'Turn, degrees')}
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px"><button class="dlg-btn" data-u="move">${esc(i18n('Move it'))}</button>
            <button class="dlg-btn" data-u="del" style="color:#e5484d;border-color:#e5484d">${esc(i18n('Remove the plan'))}</button></div>` : ''}
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--line)"><button class="dlg-btn" data-u="tidy">${esc(i18n('Join neighbour walls'))}</button>
          <div class="bindcaption">${esc(i18n('Rooms drawn each on its side of a wall get one shared edge: one wall line instead of two.'))}</div></div>
        <div class="bindcaption" data-u="msg" style="margin-top:6px">${esc(msg || '')}</div>`);
      const sq = k => sheet.querySelector(`[data-u="${k}"]`), say = t => { sq('msg').textContent = t; };
      sheet.querySelector('[data-s="close"]').onclick = closeSheet;
      sq('tidy').onclick = async () => {
        try { const r = await Home.api('/api/map/zones/tidy', {method: 'POST'}); data = {...data, ...(await Home.api('/api/map'))}; noEmptyPaths(); draw(); underlaySheet(i18n('Edges moved onto a shared line: {0}', r.moved)); }
        catch(e){ say(e.message || String(e)); }
      };
      sq('file').onchange = async () => {
        const f = sq('file').files[0]; if(!f) return;
        say(i18n('Uploading...'));
        let w = 0, h = 0;
        if(f.type !== 'image/svg+xml'){ const im = new Image(); im.src = URL.createObjectURL(f); try { await im.decode(); w = im.naturalWidth; h = im.naturalHeight; } catch(e){ /* the house refuses it */ } }
        try {
          const r = await fetch(`/api/map/underlay?w=${w}&h=${h}`, {method: 'PUT', headers: Home.authHeaders({'Content-Type': f.type || 'application/octet-stream'}), body: f});
          const j = await r.json(); if(!r.ok) throw new Error(i18n(j.detail || String(r.status)));
          data.underlay = j.underlay; data.rev = j.rev; data.scheme_img = true;
          await paintUnderlay(); await loadWalls(); draw(); underlaySheet(i18n('The plan is in: size, turn and move it to fit the rooms'));
        } catch(e){ say(e.message || String(e)); }
      };
      if(!u) return;
      sq('show').onchange = () => underlayPatch({show: sq('show').checked}).catch(e => say(e.message));
      for(const k of ['opacity', 'size', 'rot']){
        const el = sq(k);
        el.oninput = () => { sheet.querySelector(`[data-uv="${k}"]`).textContent = el.value; };
        el.onchange = () => {
          const v = +el.value, ch = {};
          if(k === 'opacity') ch.opacity = v / 100;
          if(k === 'rot') ch.rot = v;
          if(k === 'size'){ const cx = u.x + u.w / 2, cy = u.y + u.h / 2, nw = p.w * v / 100, nh = nw * (u.h / u.w); Object.assign(ch, {w: nw, h: nh, x: cx - nw / 2, y: cy - nh / 2}); }      // about its middle, its shape kept
          underlayPatch(ch).catch(e => say(e.message));
        };
      }
      sq('move').onclick = () => { closeSheet(); moveUnderlay(); };
      sq('del').onclick = async () => {
        if(!confirm(i18n('Remove the plan? Rooms, walls and things stay.'))) return;
        try { const r = await Home.api('/api/map/underlay', {method: 'DELETE'}); data.underlay = null; data.rev = r.rev; data.scheme_img = false; await paintUnderlay(); await loadWalls(); draw(); underlaySheet(); }
        catch(e){ say(e.message || String(e)); }
      };
    }
    // a drag anywhere on the map moves the plan; "Done" saves its place, Esc puts it back. The cover keeps its pointers to itself: the map's own
    // handlers (pan, tap, pointer capture) never see them, so the button gets its press; Done acts on its pointerup (a click as well)
    function moveUnderlay(){
      const u = data.underlay, p = plan(); if(!u || !p) return;
      const was = {x: u.x, y: u.y}, cover = document.createElement('div');
      cover.dataset.m = 'ulmove';
      cover.style.cssText = 'position:absolute;inset:0;z-index:20;cursor:move;touch-action:none';
      cover.innerHTML = `<button class="dlg-btn primary" data-b="uldone" style="position:absolute;left:8px;top:8px">${esc(i18n('Done'))}</button><span class="bindcaption" style="position:absolute;left:90px;top:14px;background:var(--card);padding:2px 8px;border-radius:6px">${esc(i18n('Drag the plan into place'))}</span>`;
      view.appendChild(cover);
      const btn = cover.querySelector('button');
      let from = null, over = false;
      const say = cover.querySelector('span');
      const end = async keep => {
        if(over) return; over = true;
        if(keep){
          try { const r = await Home.api('/api/map/underlay', {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({x: u.x, y: u.y})}); data.underlay = r.underlay; data.rev = r.rev; }
          catch(e){ over = false; say.textContent = i18n('Not saved') + ': ' + (e.message || String(e)); say.style.color = '#ff9a9a'; return; }   // the cover stays: try again or Esc
          paintUnderlay().catch(() => {}); loadWalls().then(draw).catch(() => {});
        } else { u.x = was.x; u.y = was.y; paintUnderlay(); }
        cover.remove(); document.removeEventListener('keydown', key, true);
      };
      const key = e => { if(e.key === 'Escape'){ e.stopPropagation(); end(false); } };
      document.addEventListener('keydown', key, true);
      for(const t of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'click', 'wheel', 'contextmenu']) cover.addEventListener(t, e => e.stopPropagation());
      btn.addEventListener('pointerup', e => { e.preventDefault(); end(true); });
      btn.addEventListener('click', () => end(true));
      cover.addEventListener('pointerdown', e => { if(e.target.closest('button')) return; if(cover.setPointerCapture) cover.setPointerCapture(e.pointerId); from = {id: e.pointerId, at: planXY(e), x: u.x, y: u.y}; });
      cover.addEventListener('pointermove', e => { if(!from || from.id !== e.pointerId) return; const at = planXY(e); u.x = from.x + (at.x - from.at.x) * p.w; u.y = from.y + (at.y - from.at.y) * p.h; paintUnderlay(); });
      for(const t of ['pointerup', 'pointercancel']) cover.addEventListener(t, e => { if(from && from.id === e.pointerId) from = null; });
    }
    function fit(){
      const p = plan(); if(!p) return;
      sizeView(); S.userZoomed = false;
      if(!view.clientWidth || !view.clientHeight){ requestAnimationFrame(fit); return; }         // not laid out yet: once more next frame
      const lh = S.rot ? p.w : p.h;                                         // the plan as it lies on the screen
      S.scale = fitScale();
      S.ox = S.rot ? (view.clientWidth + p.h * S.scale) / 2 : (view.clientWidth - p.w * S.scale) / 2;
      S.oy = (view.clientHeight - lh * S.scale) / 2;
      place();
    }
    const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
    const COARSE = !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);                       // a touch screen
    const RO = !!window.RESTRICTED;                                    // restricted (the public demo): the plan is shown, its things open, phone-sized labels
    const EDIT = () => !RO || !!(window.RESTRICTED_EDIT && window.RESTRICTED_EDIT());      // a plug-in lets one session edit (the demo's guest in control)
    const iconPx = () => { const p = plan(); return p ? clamp(0.024 * p.w * S.scale, COARSE || RO ? 28 : 14, 44) : 34; };           // an icon grows and shrinks with the plan, within what a finger can hit
    let placeAt = 0, placeTimer = 0;
    const placeSoon = () => {                                    // a pan or a pinch lays out at most once per 16 ms (a frame), not per pointer event
      const now = performance.now();
      if(now - placeAt >= 16){ clearTimeout(placeTimer); placeTimer = 0; placeAt = now; place(); }
      else if(!placeTimer) placeTimer = setTimeout(() => { placeTimer = 0; placeAt = performance.now(); place(); }, 16 - (now - placeAt));
    };
    function place(){
      stage.style.transform = `translate(${S.ox}px,${S.oy}px) ${S.rot ? 'rotate(90deg) ' : ''}scale(${S.scale})`;
      stage.style.setProperty('--rot', S.rot ? 1 : 0);
      const px = iconPx();
      stage.style.setProperty('--inv', (px / 34) / S.scale);                  // an icon is drawn 34 px wide: this brings it to px on the screen
      stage.style.setProperty('--one', 1 / S.scale);
      stage.style.setProperty('--gridw', 1 / (S.scale * (window.devicePixelRatio || 1)));      // the grid: one device pixel, never thinner
      stage.style.setProperty('--hs', 1 / S.scale);                           // a handle of the outline keeps its screen size                          // a cluster badge keeps its screen size
      stage.classList.toggle('big', px >= 28);                                // values and names only when the icon is big enough
      q('svg').style.setProperty('--sw', clamp(3.3 * S.scale, 3, 10) / S.scale);        // the width of a strip: in plan units, between 3 and 10 screen pixels
      q('svg').querySelectorAll('text[data-fs]').forEach(t => { t.style.display = +t.dataset.fs * S.scale < 8 || t.dataset.nameless ? 'none' : ''; });        // a label too small to read is not drawn
      layoutClusters(px);
    }
    // icons of one zone that overlap at this zoom become one badge "Room 1 . 7": a tap zooms into them
    // each name goes to the first free side of its icon (below, right, left, above), then one step smaller; none free: hidden, shown on hover. TVs and ACs choose first.
    function placeLabels(icons, items){
      const box = e => { const r = e.getBoundingClientRect(); return {l: r.left - 1, t: r.top - 1, r: r.right + 1, b: r.bottom + 1}; };
      const hit = (a, b) => a.l < b.r && b.l < a.r && a.t < b.b && b.t < a.b;
      const obstacles = [...icons.querySelectorAll('[data-id],[data-cluster]')].filter(e => e.style.display !== 'none').map(e => ({e, r: box(e)})), taken = [];
      for(const b of icons.querySelectorAll('.mb')) if(b.offsetParent) obstacles.push({e: null, r: box(b)});           // a value under an icon is in the way too
      for(const t of q('svg').querySelectorAll('text[data-fs]')) if(t.style.display !== 'none') obstacles.push({e: null, r: box(t)});       // and the name of a room
      const vr = view.getBoundingClientRect(), off = (x, y) => x < vr.left - 60 || x > vr.right + 60 || y < vr.top - 60 || y > vr.bottom + 60;   // a name is only placed on the screen
      const seg = (ax, ay, bx, by, m) => { for(let k = 0, n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / 8)); k <= n; k++){ const x = ax + (bx - ax) * k / n, y = ay + (by - ay) * k / n; if(!off(x, y)) obstacles.push({e: null, r: {l: x - m, t: y - m, r: x + m, b: y + m}}); } };
      for(const wl of data.walls){ const [ax, ay] = planToScreen(wl.a[0], wl.a[1]), [bx, by] = planToScreen(wl.b[0], wl.b[1]); seg(ax, ay, bx, by, 9); }      // the free walls
      for(const o of data.openings){ const [ax, ay] = planToScreen(o.a[0], o.a[1]), [bx, by] = planToScreen(o.b[0], o.b[1]); seg(ax, ay, bx, by, 9); }   // the doors and windows
      const pp = plan();
      for(const l of q('svg').querySelectorAll('line[data-a]')) if(l.style.display !== 'none' && pp){                                 // and the link lines
        const [ax, ay] = planToScreen(+l.getAttribute('x1') / pp.w, +l.getAttribute('y1') / pp.h), [bx, by] = planToScreen(+l.getAttribute('x2') / pp.w, +l.getAttribute('y2') / pp.h); seg(ax, ay, bx, by, 6); }
      for(const z of data.rooms) (z.walls || []).forEach((w, i) => {                                   // and the walls: a name never lies on a wall line
        if(!w || z.walls.length !== z.poly.length) return;
        const [ax, ay] = planToScreen(z.poly[i].x, z.poly[i].y), [bx, by] = planToScreen(z.poly[(i + 1) % z.poly.length].x, z.poly[(i + 1) % z.poly.length].y);
        const m = 9;                                                                                       // the line is 5 px wide, a name may sit 4 px off its box
        if(Math.abs(ax - bx) < 2 || Math.abs(ay - by) < 2) obstacles.push({e: null, r: {l: Math.min(ax, bx) - m, t: Math.min(ay, by) - m, r: Math.max(ax, bx) + m, b: Math.max(ay, by) + m}});
        else seg(ax, ay, bx, by, m);
      });
      const sides = [['below', (r, w, h, g) => ({l: (r.l + r.r - w) / 2, t: r.b + g, r: (r.l + r.r + w) / 2, b: r.b + g + h}), ''],
                     ['right', (r, w, h, g) => ({l: r.r + g, t: (r.t + r.b - h) / 2, r: r.r + g + w, b: (r.t + r.b + h) / 2}), 'left:calc(100% + 3px);right:auto;top:50%;transform:translateY(-50%)'],
                     ['left', (r, w, h, g) => ({l: r.l - g - w, t: (r.t + r.b - h) / 2, r: r.l - g, b: (r.t + r.b + h) / 2}), 'left:auto;right:calc(100% + 3px);top:50%;transform:translateY(-50%)'],
                     ['above', (r, w, h, g) => ({l: (r.l + r.r - w) / 2, t: r.t - g - h, r: (r.l + r.r + w) / 2, b: r.t - g}), 'left:50%;right:auto;top:auto;bottom:calc(100% + 2px);transform:translateX(-50%)'],
                     // the corners: a name moved off to a side before it is hidden
                     ['below-right', (r, w, h, g) => ({l: r.r - 4, t: r.b + g, r: r.r - 4 + w, b: r.b + g + h}), 'left:calc(100% - 4px);right:auto;transform:none'],
                     ['below-left', (r, w, h, g) => ({l: r.l + 4 - w, t: r.b + g, r: r.l + 4, b: r.b + g + h}), 'left:auto;right:calc(100% - 4px);transform:none'],
                     ['above-right', (r, w, h, g) => ({l: r.r - 4, t: r.t - g - h, r: r.r - 4 + w, b: r.t - g}), 'left:calc(100% - 4px);right:auto;top:auto;bottom:calc(100% + 2px);transform:none'],
                     ['above-left', (r, w, h, g) => ({l: r.l + 4 - w, t: r.t - g - h, r: r.l + 4, b: r.t - g}), 'left:auto;right:calc(100% - 4px);top:auto;bottom:calc(100% + 2px);transform:none']];
      const order = items.slice().sort((a, b) => (standalone(b) ? 1 : 0) - (standalone(a) ? 1 : 0) || a.y - b.y);
      for(const it of order){
        const el = icons.querySelector(`[data-id="${CSS.escape(it.id)}"]`), mn = el && el.querySelector('.mn');
        if(!mn) continue;
        if(!mn.dataset.base) mn.dataset.base = mn.style.cssText;                       // the template's own position (below the icon)
        mn.style.cssText = mn.dataset.base;
        if(mn.dataset.full) mn.textContent = mn.dataset.full;                         // a name shortened by the last layout
        const size = () => { const was = mn.style.display; mn.style.display = 'block'; mn.style.visibility = 'hidden';
          const b = mn.getBoundingClientRect(); mn.style.display = was; mn.style.visibility = ''; return b; };
        const big = stage.classList.contains('big') || mn.classList.contains('always');
        let m = size();
        if(!big || !m.width) continue;
        const r = box(el), u = (r.r - r.l - 2) / 34, hasBadge = !!el.querySelector('.mb'), me = obstacles.find(o => o.e === el);
        const place = () => {
          for(const k of [1, 0.85, 0.7]) for(const [name, rect, css] of sides){       // smaller before hidden
            const g = (name.startsWith('below') && hasBadge ? 16 : 2) * u, c = rect(r, m.width * k, m.height * k, g);
            const busy = c => c.l < vr.left || c.r > vr.right || c.t < vr.top || c.b > vr.bottom || obstacles.some(o => o !== me && hit(c, o.r)) || taken.some(t => hit(c, t));
            if(busy(c)) continue;                                                   // inside the plan, on free ground
            mn.style.cssText = mn.dataset.base + (css ? ';' + css : '');
            if(k < 1) mn.style.setProperty('font-size', (parseFloat(getComputedStyle(mn).fontSize) * k) + 'px', 'important');
            const a = mn.getBoundingClientRect(), d = {l: a.left, t: a.top, r: a.right, b: a.bottom};
            if(busy(d)) continue;                                                   // the name as drawn, not the estimate
            taken.push({l: d.l - 2, t: d.t - 2, r: d.r + 2, b: d.b + 2}); return true;                     // a gap between two names
          }
          mn.style.cssText = mn.dataset.base;
          return false;
        };
        let done = place();
        const full = mn.textContent, zn = ((data.rooms.find(z => z.id === it.room) || {}).name || '').trim();
        if(!done && zn && full.toLowerCase().startsWith(zn.toLowerCase() + ' ')){   // shorter before hidden: the room's name is on the plan
          mn.dataset.full = full; mn.textContent = full.slice(zn.length + 1).replace(/^./, ch => ch.toUpperCase()); m = size();
          done = place();
          if(!done) mn.textContent = full;
        }
        if(!done) mn.style.visibility = 'hidden';                                       // no free side: the name shows on hover
      }
    }
    const standalone = it => ['tv', 'ac'].includes((cands.find(c => c.id === it.dev) || {}).category);       // a big appliance is never put into a badge: it keeps its icon and name
    function layoutClusters(px){
      const icons = q('icons'), p = plan();
      if(!icons || !p) return;
      icons.querySelectorAll('[data-cluster]').forEach(e => e.remove());
      const els = new Map();
      icons.querySelectorAll('[data-id]').forEach(e => { e.style.display = ''; els.set(e.dataset.id, e); });
      if(S.spider && (S.spider.scale !== S.scale || S.mode)) S.spider = null;   // a fan closes when the zoom changes
      icons0(els);
      const fan = S.spider ? fanOut(els, p, px) : new Set();
      if(S.mode || (lift && lift.active)) return;                              // while something is placed, moved or drawn every icon is there
      const its = data.items.filter(i => !i.path && els.has(i.id) && !standalone(i) && !fan.has(i.id)), key = it => (zoneAt(it) || {name: ''}).name;
      const parent = its.map((_, i) => i), find = i => parent[i] === i ? i : (parent[i] = find(parent[i]));
      for(let i = 0; i < its.length; i++) for(let j = i + 1; j < its.length; j++)
        if(key(its[i]) === key(its[j]) && Math.hypot((its[i].x - its[j].x) * p.w * S.scale, (its[i].y - its[j].y) * p.h * S.scale) < px * 1.15) parent[find(i)] = find(j);
      const groups = new Map();
      its.forEach((it, i) => { const r = find(i); if(!groups.has(r)) groups.set(r, []); groups.get(r).push(it); });
      for(const g of groups.values()){
        if(g.length < 2) continue;
        g.forEach(it => { els.get(it.id).style.display = 'none'; });
        const cx = g.reduce((a, it) => a + it.x, 0) / g.length, cy = g.reduce((a, it) => a + it.y, 0) / g.length, name = key(g[0]);
        const el = document.createElement('div');
        el.title = (name ? name + ': ' : '') + g.map(it => nameOf(it)).join(', ');
        el.dataset.cluster = g.map(it => it.id).join(',');
        el.style.cssText = `position:absolute;left:${cx * 100}%;top:${cy * 100}%;transform:translate(-50%,-50%) rotate(calc(var(--rot,0) * -90deg)) scale(var(--inv,1));width:34px;height:34px;border-radius:50%;background:var(--card);border:3px solid #5b8cff;color:#e6edf5;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:12px;line-height:12px;font-weight:700;cursor:pointer;z-index:2`;
        el.innerHTML = ico('stack', 14) + g.length;
        icons.appendChild(el);
      }
      const gone = new Set(its.filter(i => els.get(i.id).style.display === 'none').map(i => i.id));
      q('svg').querySelectorAll('[data-i]').forEach(c => { c.style.display = gone.has(c.dataset.i) ? 'none' : ''; });
      q('svg').querySelectorAll('[data-a]').forEach(l => { l.style.display = gone.has(l.dataset.a) || gone.has(l.dataset.b) ? 'none' : ''; });        // no line to an icon that is inside a badge
      relabel();
      placeLabels(icons, data.items.filter(i => !i.path && els.has(i.id) && els.get(i.id).style.display !== 'none'));
    }
    // a room's name and temperature move together to a spot no icon, badge or fanned icon covers, at this zoom
    function relabel(){
      const p = plan(), svg = q('svg');
      if(!p || !svg) return;
      for(const z of data.rooms){
        const t = svg.querySelector(`text[data-z="${CSS.escape(z.id)}"]`);
        if(!t) continue;
        const l = labelOf(z), tt = svg.querySelector(`text[data-zt="${CSS.escape(z.id)}"]`);
        t.setAttribute('x', l.x * p.w); t.setAttribute('y', l.y * p.h); t.setAttribute('font-size', l.size); t.dataset.fs = l.size; t.dataset.nameless = l.nameless ? '1' : '';
        const dn = l.nameless ? 0 : l.size * 1.15;                       // under the name on the screen: +x of the plan when it is turned
        if(tt){ tt.setAttribute('x', l.x * p.w + (S.rot ? dn : 0)); tt.setAttribute('y', l.y * p.h + (S.rot ? 0 : dn)); tt.setAttribute('font-size', l.size * .7); tt.dataset.fs = l.size * .7; }
        if(tt) tt.dataset.nameless = l.notemp ? '1' : '';
        for(const e of [t, tt]) if(e) e.style.display = +e.dataset.fs * S.scale < 8 || (e === t && l.nameless) || (e === tt && l.notemp) ? 'none' : '';
      }
    }
    // icons that no zoom separates (one spot, or the zoom is at its limit) fan out around their spot, each one tappable;
    // the fan closes on a tap elsewhere or a zoom change. -> the ids fanned out
    function fanOut(els, p, px){
      const its = data.items.filter(i => S.spider.ids.includes(i.id) && els.has(i.id));
      if(its.length < 2) { S.spider = null; return new Set(); }
      const cx = its.reduce((a, it) => a + it.x, 0) / its.length, cy = its.reduce((a, it) => a + it.y, 0) / its.length;
      const r = px * (its.length > 6 ? 1.6 : 1.15) / S.scale;                // in plan pixels: about one icon from the spot on the screen
      its.forEach((it, k) => {
        const a = -Math.PI / 2 + 2 * Math.PI * k / its.length, e = els.get(it.id);
        e.style.left = (cx + r * Math.cos(a) / p.w) * 100 + '%';
        e.style.top = (cy + r * Math.sin(a) / p.h) * 100 + '%';
        e.dataset.fan = '1';
      });
      return new Set(its.map(i => i.id));
    }
    function icons0(els){                                                      // icons of an earlier fan back on their own spot
      els.forEach((e, id) => {
        if(!e.dataset.fan) return;
        const it = data.items.find(i => i.id === id);
        if(it){ e.style.left = it.x * 100 + '%'; e.style.top = it.y * 100 + '%'; }
        delete e.dataset.fan;
      });
    }
    function focusDevice(id){                                                  // zoom to a device's icon(s) and make them pulse a while
      const ids = data.items.filter(i => i.kind === 'device' && i.dev === id).map(i => i.id);
      if(!ids.length) return;
      zoomTo(ids, 3);                                                          // close enough to find it, the room still around it
      setTimeout(() => ids.forEach(x => {
        const el = q('icons').querySelector(`[data-id="${CSS.escape(x)}"]`);
        if(el){ el.dataset.focus = '1'; el.style.animation = 'mpulse 1.4s 4'; setTimeout(() => { el.style.animation = ''; }, 6000); }
      }), 60);
    }
    function zoomTo(ids, most = 8){                                            // fit the plan to these icons, at most `most` times in
      const p = plan(), its = data.items.filter(i => ids.includes(i.id));
      if(!p || !its.length) return;
      const x0 = Math.min(...its.map(i => i.x)) * p.w, x1 = Math.max(...its.map(i => i.x)) * p.w, y0 = Math.min(...its.map(i => i.y)) * p.h, y1 = Math.max(...its.map(i => i.y)) * p.h;
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, bw = S.rot ? y1 - y0 : x1 - x0, bh = S.rot ? x1 - x0 : y1 - y0;
      S.userZoomed = true;
      S.scale = clamp(Math.min(view.clientWidth / Math.max(bw, 1), view.clientHeight / Math.max(bh, 1)) * 0.55, S.scale, Math.max(most, S.scale));
      S.ox = S.rot ? view.clientWidth / 2 + cy * S.scale : view.clientWidth / 2 - cx * S.scale;
      S.oy = view.clientHeight / 2 - (S.rot ? cx : cy) * S.scale;
      place();
    }

    // ---- live state: what every thing is doing now. thingView() is pure; paintState() repaints only the things whose view changed ----
    const kindOf = it => it.kind === 'load' ? it.type : ((cands.find(c => c.id === it.dev) || {}).category || '');
    const clock = ts => { const d = new Date(ts * 1000); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };
    const num = (x, f = 0) => typeof x === 'number' ? x.toFixed(f) : '';
    const pct = (b) => typeof b === 'number' ? Math.round(b > 100 ? b / 2.55 : b) : null;
    // a plug-in's mark on a device's icon (thread: a Thread router): window.MapMarks = [fn(device id) -> {glyph, title} | null]
    const markOf = it => { if(it.kind !== 'device') return null; for(const f of (window.MapMarks || [])){ try { const m = f(it.dev); if(m) return m; } catch(e){ /* a plug-in's */ } } return null; };
    function thingView(it){
      const V = {tone: '#c9d1dc', glow: 0, badge: '', tip: nameOf(it), bad: false, ring: '', dim: false, glyph: '', mark: markOf(it)};
      if(it.kind === 'device' && !cands.some(c => c.id === it.dev || (c.members || []).includes(it.dev))){   // its device is gone (a removed blaster): a marker
        V.tone = '#555b66'; V.bad = true; V.dim = true; V.tip += ' - ' + i18n('device removed');
        return V;
      }
      if(!S.live) return V;
      const kind = kindOf(it), now = Date.now() / 1000;
      const chans = it.kind === 'load' ? data.links.filter(l => l.load === it.id).map(l => ({dev: l.dev, code: l.code})) : (it.dev.startsWith('panel:') ? [] : [{dev: it.dev, code: ''}]);
      const sts = chans.map(c => ({...c, st: Home.state(c.dev)})).filter(c => c.st);
      if(sts.length && sts.every(c => c.st.online === false)){          // offline: grey, a "!" and when it was last heard
        const seen = Math.max(0, ...sts.map(c => c.st.last_seen || 0));
        V.tone = '#555b66'; V.bad = true; V.tip += ' - ' + i18n('no contact') + (seen ? ' ' + i18n('since') + ' ' + clock(seen) : '');
        return V;
      }
      const seen = Math.max(0, ...sts.map(c => c.st.last_seen || 0));
      if(seen && now - seen > 12 * 3600 && kind !== 'button') V.dim = true;     // heard of long ago: dimmed
      const v = (sts[0] && sts[0].st.values) || {};
      const dv = it.kind === 'device' ? ((Home.devices && Home.devices()) || []).find(x => x.id === it.dev) : null;
      if(dv && dv.ir_power){                     // an IR fixture on a wall switch (a lamp with a fan): the light is the switch's word, the rest what was last SENT
        const fed = ((Home.state(dv.ir_power.device) || {}).values || {})[dv.ir_power.code];
        const sent = Object.keys(v).filter(k => /^sent/.test(k)).sort().map(k => i18n(v[k]));
        V.tone = fed ? '#ffd24d' : '#c9d1dc'; if(fed) V.glow = 0.6;
        V.badge = fed === false ? i18n('Off') : sent.join(' ');
        V.tip += ' - ' + (fed === false ? i18n('no power') : fed ? i18n('on') : '') + (sent.length && fed !== false ? ' \u00b7 ' + i18n('sent: {0}', sent.join(', ')) : '');
        return V;
      }
      if(it.kind === 'load'){
        const on = sts.some(c => c.code ? !!(c.st.values || {})[c.code] : Object.values(c.st.values || {}).some(x => x === true));
        let lvl = 1;
        for(const c of sts){ const sfx = (c.code || '').replace(/^state_?/, ''), b = pct((c.st.values || {})[sfx ? 'brightness_' + sfx : 'brightness']); if(b !== null) lvl = Math.max(0.35, b / 100); }
        V.tone = on ? '#ffd24d' : '#c9d1dc';
        if(on && ['lamp', 'led', 'other'].includes(kind)) V.glow = Math.round((0.25 + 0.6 * lvl) * 100) / 100;
        return V;
      }
      switch(kind){
        case 'switch': case 'dimmer': {
          const keys = Object.keys(v).filter(k => /^state/.test(k)).sort();
          V.badge = keys.map(k => { const sfx = k.replace(/^state_?/, ''), b = pct(v[sfx ? 'brightness_' + sfx : 'brightness']); return v[k] ? (kind === 'dimmer' && b !== null ? b + '%' : '\u25cf') : '\u25cb'; }).join(kind === 'dimmer' ? ' ' : '');
          V.tone = keys.some(k => v[k]) ? '#3ddc84' : '#c9d1dc'; break;
        }
        case 'socket': V.tone = v.state === true || v.state === 'ON' ? '#ffd24d' : '#c9d1dc'; if(typeof v.power === 'number' && v.power >= 1) V.badge = Math.round(v.power) + ' W'; break;
        case 'lock':
          V.glyph = ico(v.locked === false ? 'unlock' : 'lock', 20); V.tone = v.locked === false ? '#ff8a3d' : '#3ddc84';
          if(v.door === 'open'){ V.badge = i18n('open'); V.tone = '#e5484d'; }
          break;
        case 'door': if(v.contact === false || v.door === 'open'){ V.badge = i18n('open'); V.tone = '#e5484d'; } else V.tone = '#3ddc84'; break;
        case 'motion': if(v.occupancy || v.presence){ V.ring = 'pulse'; V.tone = '#3ddc84'; } break;
        case 'climate': V.badge = [typeof v.temperature === 'number' ? YDisp.temp(v.temperature, it.dev + '/temperature') + '\u00b0' : '', typeof v.humidity === 'number' ? YDisp.hum(v.humidity, it.dev + '/humidity') + '%' : ''].filter(Boolean).join(' '); break;
        case 'ac': if(v.power === true || v.power === 'ON'){ V.tone = v.mode === 'heat' ? '#ff8a3d' : v.mode === 'cool' ? '#4aa3ff' : '#3ddc84'; if(typeof v.temp === 'number') V.badge = num(v.temp) + '\u00b0'; } break;
        case 'tv': if(v.power === true){ V.tone = '#ffd24d'; V.glow = 0.3; } break;
        case 'speaker': {
          const cand = cands.find(c => c.id === it.dev) || {}, spk = Home.state((cand.members || [])[0]) || {};
          if(spk.values && spk.values.now && spk.values.now !== 'idle') V.ring = 'wave'; else if(v.listen) V.ring = 'listen';
          break;
        }
        default: if(sts.length) V.tone = '#c9d1dc';
      }
      return V;
    }
    function iconHTML(it){
      const V = thingView(it), lifted = lift && lift.active && lift.it === it && lift.i === undefined;
      const cue = V.ring === 'pulse' ? 'animation:mpulse 1.4s infinite;' : V.ring === 'wave' ? 'animation:mwave 1s infinite;' : V.ring === 'listen' ? 'box-shadow:0 0 0 4px rgba(61,220,132,.35);' : '';
      return `<div data-id="${esc(it.id)}" title="${esc(V.tip)}" style="position:absolute;left:${it.x * 100}%;top:${it.y * 100}%;opacity:${V.dim ? .55 : 1};
        transform:translate(-50%,-50%) rotate(calc(var(--rot,0) * -90deg)) scale(var(--inv,1));width:34px;height:34px;border-radius:50%;background:#0f141b;border:3px solid ${V.tone};filter:drop-shadow(0 0 2px rgba(0,0,0,.95));${cue}
        display:flex;align-items:center;justify-content:center;color:#fff;cursor:pointer;touch-action:none;z-index:${V.badge || V.bad ? 3 : 1};${S.pending && S.pending.id === it.id ? 'outline:3px solid #ffb020;' : ''}${lifted ? 'outline:4px solid #ffb020;box-shadow:0 8px 18px rgba(0,0,0,.6);' : ''}">${V.glyph || iconOf(it)}<span style="position:absolute;left:-9px;top:-9px;right:-9px;bottom:-9px;border-radius:50%"></span>
        ${V.badge ? `<span class="mb" style="position:absolute;left:50%;top:100%;transform:translateX(-50%);margin-top:2px;font-size:11px;line-height:14px;white-space:nowrap;background:rgba(0,0,0,.7);color:${V.tone === '#e5484d' ? '#ff9a9a' : '#fff'};padding:0 4px;border-radius:6px">${esc(V.badge)}</span>` : ''}
        <span class="mn${standalone(it) ? ' always' : ''}" style="position:absolute;left:50%;top:calc(100% + ${V.badge ? 16 : 2}px);transform:translateX(-50%);font-size:11px;line-height:14px;white-space:nowrap;color:#cfd8e3;text-shadow:0 0 3px #000,0 0 3px #000">${esc(mapLabel(it))}</span>
        ${V.mark ? `<span title="${esc(V.mark.title || '')}" style="position:absolute;left:-6px;top:-6px;background:#4aa3ff;color:#fff;border-radius:50%;width:16px;height:16px;font-size:10px;line-height:16px;text-align:center;font-weight:700">${esc(V.mark.glyph || '')}</span>` : ''}
        ${V.bad ? '<span style="position:absolute;right:-6px;top:-6px;background:#e5484d;color:#fff;border-radius:50%;width:16px;height:16px;font-size:11px;line-height:16px;text-align:center;font-weight:700">!</span>' : ''}</div>`;
    }
    function glowSVG(){                                                  // a soft warm light around each lamp that is on
      const p = plan(), w = p ? p.w : 800, h = p ? p.h : 400;
      return data.items.map(it => { const g = thingView(it).glow; return g ? `<circle data-i="${esc(it.id)}" cx="${it.x * w}" cy="${it.y * h}" r="${w * 0.045}" fill="url(#mglow)" opacity="${g}"/>` : ''; }).join('');
    }
    const tempOf = rid => { const c = Home.roomClimate ? Home.roomClimate(rid) : null; return c ? c.value + '\u00b0' : ''; };
    const roomTemp = z => tempOf(z.house_room);
    function paintState(){                                               // only the things whose view changed are repainted
      let strip = false, glow = false;
      for(const it of data.items){
        const sig = JSON.stringify(thingView(it));
        if(S.sig[it.id] === sig) continue;
        S.sig[it.id] = sig;
        if(it.path) { strip = true; continue; }
        glow = true;
        const el = q('icons').querySelector(`[data-id="${CSS.escape(it.id)}"]`);
        if(el) el.outerHTML = iconHTML(it);
      }
      q('svg').querySelectorAll('text[data-rt]').forEach(t => { t.textContent = tempOf(t.dataset.rt); });
      if(glow) layoutClusters(iconPx());
      if(strip){ draw(); return; }
      const gg = q('svg').querySelector('[data-glow]');
      if(glow && gg) gg.innerHTML = glowSVG();
    }

    const nearOnPath = (path, qx, qy, w, h) => {                                     // the point of the strip's polyline nearest to (qx, qy)
      let best = [path[0].x * w, path[0].y * h], bd = 1e18;
      for(let i = 0; i + 1 < path.length; i++){
        const ax = path[i].x * w, ay = path[i].y * h, vx = path[i + 1].x * w - ax, vy = path[i + 1].y * h - ay, t = Math.max(0, Math.min(1, ((qx - ax) * vx + (qy - ay) * vy) / (vx * vx + vy * vy || 1)));
        const fx = ax + t * vx, fy = ay + t * vy, d = (qx - fx) ** 2 + (qy - fy) ** 2; if(d < bd){ bd = d; best = [fx, fy]; }
      }
      return best;
    };
    // ---- drawing: lines, outlines, icons ----
    // the wires: one source, the wiring's entries (a switch, socket or controller channel -> a device or a load)
    function wireList(){
      return (S.wiring || []).map(e => ({dev: e.switch && e.switch.dev, code: e.switch && e.switch.code, load: e.target && e.target.load, target: e.target && e.target.dev}));
    }
    function twinOf(it){ return it.kind === 'load' && it.dev ? data.items.find(d => d.kind === 'device' && d.dev === it.dev) || null : null; }
    function draw(){
      ownerTools();
      const p = plan(); const w = p ? p.w : 800, h = p ? p.h : 400;
      const pt = (x, y) => `${x * w},${y * h}`;
      const svg = q('svg'); svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
      let g = `<defs><radialGradient id="mglow"><stop offset="0" stop-color="#ffd24d" stop-opacity=".95"/><stop offset="1" stop-color="#ffb020" stop-opacity="0"/></radialGradient></defs><g data-glow="1">${glowSVG()}</g>`;
      const shown = new Set(), drop = S.drop ? S.drop.z : null;      // where a dragged icon would land (liftTo)
      for(const r of data.rooms){
        if(S.edit && S.edit.z === r) continue;                           // the zone being edited is drawn by the editor below
        g += r === drop ? `<polygon data-drop="${esc(r.id)}" points="${r.poly.map(a => pt(a.x, a.y)).join(' ')}" fill="#ffb020" fill-opacity=".12" stroke="#ffb020" stroke-width="2.5" vector-effect="non-scaling-stroke"/>`
          : `<polygon points="${r.poly.map(a => pt(a.x, a.y)).join(' ')}" fill="none" stroke="#5b8cff" stroke-opacity="${S.edit ? .3 : (S.hoverZone === r ? .55 : 0)}" stroke-width="1.5" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>`;
        g += edgeLines(r.poly, r.walls, w, h, '#cfd8e3', '#6b7686');
        if(shown.has(r.name.toLowerCase())) continue;                // two outlines with one name are one room: one label
        shown.add(r.name.toLowerCase());
        const l = labelOf(r); g += `<text data-z="${esc(r.id)}" data-fs="${l.size}" style="transform-box:fill-box;transform-origin:center;transform:rotate(calc(var(--rot,0) * -90deg + ${l.vert ? -90 : 0}deg));letter-spacing:.5px" x="${l.x * w}" y="${l.y * h}" font-size="${l.size}" text-anchor="middle" dominant-baseline="middle" fill="#9fb0c4" fill-opacity=".85" font-weight="600">${esc(r.name)}</text>`;
        if(r.house_room && !l.vert) g += `<text data-zt="${esc(r.id)}" data-rt="${esc(r.house_room)}" data-fs="${l.size * .7}" style="transform-box:fill-box;transform-origin:center;transform:rotate(calc(var(--rot,0) * -90deg))" x="${l.x * w + (S.rot ? l.size * 1.15 : 0)}" y="${l.y * h + (S.rot ? 0 : l.size * 1.15)}" font-size="${l.size * .7}" text-anchor="middle" dominant-baseline="middle" fill="#9fb0c4" fill-opacity=".75">${esc(roomTemp(r))}</text>`;       // the room's temperature, small under its name
      }
      if(drop) g += `<text data-dropname="1" x="${S.drop.x * w}" y="${S.drop.y * h - 26}" text-anchor="middle" fill="#ffb020" font-size="14" font-weight="600" style="paint-order:stroke;stroke:#0d1117;stroke-width:4px">${esc(drop.name)}</text>`;
      for(const wl of data.walls) g += `<line data-w="${esc(wl.id)}" x1="${wl.a[0] * w}" y1="${wl.a[1] * h}" x2="${wl.b[0] * w}" y2="${wl.b[1] * h}" stroke="${S.mode === 'wall' ? '#ffb020' : '#cfd8e3'}" stroke-width="5" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
      for(const o of data.openings) g += openingSVG(o);
      if(S.mode === 'opening' && S.opDraw){                                // the stroke being drawn, and where it lands on the wall
        const D = S.opDraw, on = snapOpening(D.a, D.b);
        g += `<line x1="${D.a.x * w}" y1="${D.a.y * h}" x2="${D.b.x * w}" y2="${D.b.y * h}" stroke="#ffb020" stroke-width="2" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"/>`;
        if(on) g += `<line x1="${on.a[0] * w}" y1="${on.a[1] * h}" x2="${on.b[0] * w}" y2="${on.b[1] * h}" stroke="#ffb020" stroke-width="9" stroke-opacity=".6" vector-effect="non-scaling-stroke"/>`;
      }
      if(S.mode === 'wall' && S.wallStart) g += `<circle cx="${S.wallStart.x * w}" cy="${S.wallStart.y * h}" r="6" fill="#ffb020" vector-effect="non-scaling-stroke"/>`;
      for(const it of data.items){                                           // an LED strip: a grey line, then each linked part in the colour of its channel
        if(!(it.path && it.path.length > 1)) continue;
        const sm = it.smooth !== false, st = 'fill="none" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"';
        g += `<path d="${pathD(it.path, 0, it.path.length - 1, sm, w, h)}" stroke="#2f3540" style="stroke-width:var(--sw,8)" ${st.replace('vector-effect="non-scaling-stroke"', '')}/>`;
        for(const ln of data.links.filter(l => l.load === it.id)){
          const sg = ln.seg && ln.seg.length === 2 ? ln.seg : [0, it.path.length - 1];
          g += `<path d="${pathD(it.path, sg[0], sg[1], sm, w, h)}" stroke="${ledColour(ln.dev, ln.code)}" style="stroke-width:calc(var(--sw,8) * .62)" ${st.replace('vector-effect="non-scaling-stroke"', '')}/>`;
        }
        g += it.path.map((a, i) => lift && lift.active && lift.it === it && lift.i === i ? `<circle cx="${a.x * w}" cy="${a.y * h}" r="9" fill="#ffb020" stroke="#fff" vector-effect="non-scaling-stroke"/>` : `<circle cx="${a.x * w}" cy="${a.y * h}" r="2.5" fill="#fff" fill-opacity=".6"/>`).join('');
        if(S.mode === 'seg' && S.seg && S.seg.load === it.id) g += it.path.map((a, i) => `<circle cx="${a.x * w}" cy="${a.y * h}" r="6" fill="${S.seg.pts.includes(i) ? '#ffb020' : '#fff'}" stroke="#222" vector-effect="non-scaling-stroke"/>`).join('');
      }
      if(S.mode === 'path' && S.pathItem && S.pathItem.points.length){
        const P = S.pathItem;
        if(P.points.length > 1) g += `<path d="${pathD(P.points, 0, P.points.length - 1, P.smooth, w, h)}" fill="none" stroke="#ffb020" stroke-width="5" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
        g += P.points.map(a => `<circle cx="${a.x * w}" cy="${a.y * h}" r="5" fill="#ffb020" vector-effect="non-scaling-stroke"/>`).join('');
        if(S.hover){ const l = P.points[P.points.length - 1]; g += `<line x1="${l.x * w}" y1="${l.y * h}" x2="${S.hover.x * w}" y2="${S.hover.y * h}" stroke="#ffb020" stroke-width="3" stroke-dasharray="6 5" vector-effect="non-scaling-stroke"/><circle cx="${S.hover.x * w}" cy="${S.hover.y * h}" r="6" fill="none" stroke="#ffb020" vector-effect="non-scaling-stroke"/>`; }
      }
      if(S.edit){                                                        // the outline: edges, "+" at the middle of each, a handle at each corner
        const E = S.edit, n = E.poly.length;
        g += `<polygon points="${E.poly.map(a => pt(a.x, a.y)).join(' ')}" fill="rgba(255,176,32,.12)" stroke="none"/>`;
        g += edgeLines(E.poly, E.walls || E.poly.map(() => true), w, h, '#ffb020', '#ffb020');
        for(let i = 0; i < n; i++){
          const a = E.poly[i], b = E.poly[(i + 1) % n], mx = (a.x + b.x) / 2 * w, my = (a.y + b.y) / 2 * h;
          g += `<g style="transform:translate(${mx}px,${my}px) scale(var(--hs))"><circle r="6" fill="#0f141b" stroke="#ffb020" stroke-width="1.5"/><path d="M-3 0H3M0 -3V3" stroke="#ffb020" stroke-width="1.5"/></g>`;
        }
        E.poly.forEach((a, i) => { g += `<circle style="transform:translate(${a.x * w}px,${a.y * h}px) scale(var(--hs))" r="7" fill="${i === E.sel ? '#ffb020' : '#fff'}" stroke="#222" stroke-width="1.5"/>`; });
      }
      if(S.snapHi && S.snapHi.length && ((S.mode === 'path' && S.hover) || (lift && lift.active) || S.drag))       // the edge the point would stick to
        g += S.snapHi.map(e => `<line x1="${e.ax}" y1="${e.ay}" x2="${e.bx}" y2="${e.by}" stroke="#ffb020" stroke-width="3" stroke-opacity=".85" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`).join('');
      const byDev = new Map([...data.items.filter(i => i.kind === 'load' && i.dev && !twinOf(i)), ...data.items.filter(i => i.kind === 'device')].map(i => [i.dev, i]));   // a load bound to a device with no card of its own stands for it
      const byLoad = new Map(data.items.map(i => [i.id, twinOf(i) || i]));       // a load bound to its device is drawn as that device
      // a solid line is a physical wire: from a switch, socket or controller to what it feeds (one list: the wiring, a load or a device at its end)
      const wires = wireList().map(e => ({a: byDev.get(e.dev), b: e.load ? byLoad.get(e.load) : byDev.get(e.target)})).filter(x => x.a && x.b && x.a !== x.b);
      if(S.wires) for(const {a, b} of wires){
        const strip = b.path && b.path.length > 1, e = strip ? nearOnPath(b.path, a.x * w, a.y * h, w, h) : [b.x * w, b.y * h];       // a strip: from its nearest point, not along it
        g += `<line data-a="${esc(b.id)}" data-b="${esc(a.id)}" x1="${e[0]}" y1="${e[1]}" x2="${a.x * w}" y2="${a.y * h}" stroke="#5b8cff" stroke-width="${strip ? 1.2 : 2}" vector-effect="non-scaling-stroke"/>`;
      }
      // a dashed line is IR from a blaster to what it drives; a rule or a panel gets no line (the device window lists them)
      if(S.radio) for(const l of S.auto.filter(x => x.via === 'ir')){
        const a = byDev.get(l.from), b = byDev.get(l.to);
        if(a && b) g += `<line data-a="${esc(a.id)}" data-b="${esc(b.id)}" x1="${a.x * w}" y1="${a.y * h}" x2="${b.x * w}" y2="${b.y * h}" stroke="#8a93a3" stroke-width="1.5" stroke-dasharray="6 5" vector-effect="non-scaling-stroke"><title>${esc(l.via + ' ' + (l.note || ''))}</title></line>`;
      }
      svg.innerHTML = g;
      q('icons').innerHTML = data.items.filter(it => !twinOf(it)).map(iconHTML).join('');       // one physical thing, one icon
      for(const it of data.items) S.sig[it.id] = JSON.stringify(thingView(it));
      place();
      q('left').textContent = i18n('Left to place') + ': ' + unplaced().length;
      const hint = q('hint');
      hint.style.display = S.mode ? 'block' : 'none';
      hint.style.pointerEvents = S.mode === 'path' || S.mode === 'edit' ? 'auto' : 'none';
      hint.textContent = S.mode === 'move' ? i18n('Tap the new spot') : S.mode === 'place' ? i18n('Tap the spot for') + ' ' + ((cands.find(c => c.id === (S.pending || {}).id) || {}).name || '') :
        S.mode === 'seg' ? i18n('Tap the first and the last point of the part') : S.mode === 'linkpick' ? i18n('Tap the lamp this switch turns on') : '';
      if(S.mode === 'wall'){
        hint.style.pointerEvents = 'auto';
        hint.innerHTML = `${esc(i18n('Tap, tap: a new wall. A tap on a wall: remove it.'))}
          <button class="dlg-btn" data-b="wundo" style="padding:2px 10px" ${(S.wallHist || []).length ? '' : 'disabled'}>${esc(i18n('Undo'))}</button><button class="dlg-btn" data-b="wredo" style="padding:2px 10px" ${(S.wallRedo || []).length ? '' : 'disabled'}>${esc(i18n('Redo'))}</button>
          <button class="dlg-btn primary" data-b="wdone" style="padding:2px 10px">${esc(i18n('Done'))}</button>`;
        hint.querySelector('[data-b="wundo"]').onclick = () => wallUndo(S.wallHist, S.wallRedo);
        hint.querySelector('[data-b="wredo"]').onclick = () => wallUndo(S.wallRedo, S.wallHist);
        hint.querySelector('[data-b="wdone"]').onclick = () => { S.mode = ''; S.wallStart = null; closeSheet(); draw(); };
      }
      if(S.mode === 'opening'){
        hint.style.pointerEvents = 'auto';
        hint.innerHTML = `${esc(i18n('Draw a door or a window along a wall; a tap on one changes it.'))} ${S.opMsg ? `<b style="color:#ff9a9a">${esc(S.opMsg)}</b>` : ''}
          <button class="dlg-btn" data-b="wundo" style="padding:2px 10px" ${(S.wallHist || []).length ? '' : 'disabled'}>${esc(i18n('Undo'))}</button><button class="dlg-btn" data-b="wredo" style="padding:2px 10px" ${(S.wallRedo || []).length ? '' : 'disabled'}>${esc(i18n('Redo'))}</button>
          <button class="dlg-btn primary" data-b="wdone" style="padding:2px 10px">${esc(i18n('Done'))}</button>`;
        hint.querySelector('[data-b="wundo"]').onclick = () => wallUndo(S.wallHist, S.wallRedo);
        hint.querySelector('[data-b="wredo"]').onclick = () => wallUndo(S.wallRedo, S.wallHist);
        hint.querySelector('[data-b="wdone"]').onclick = () => { S.mode = ''; S.opDraw = null; closeSheet(); draw(); };
      }
      if(S.mode === 'edit' && S.edit){
        const E = S.edit;
        hint.innerHTML = `${esc(i18n('Drag the points; a + adds one; a double click deletes one'))} ${esc(i18n('A tap on an edge: wall / no wall (a door: two points, then its middle part without a wall)'))} ${E.msg ? `<b style="color:#ff9a9a">${esc(E.msg)}</b>` : ''}
          <button class="dlg-btn" data-b="eundo" style="padding:2px 10px" ${E.hist.length ? '' : 'disabled'}>${esc(i18n('Undo'))}</button><button class="dlg-btn" data-b="eredo" style="padding:2px 10px" ${E.redo.length ? '' : 'disabled'}>${esc(i18n('Redo'))}</button>
          <button class="dlg-btn primary" data-b="edone" style="padding:2px 10px">${esc(i18n('Done'))}</button><button class="dlg-btn" data-b="ecancel" style="padding:2px 10px">${esc(i18n('Cancel'))}</button>`;
        hint.querySelector('[data-b="eundo"]').onclick = editUndo; hint.querySelector('[data-b="eredo"]').onclick = editRedo;
        hint.querySelector('[data-b="edone"]').onclick = editDone; hint.querySelector('[data-b="ecancel"]').onclick = editCancel;
      }
      if(S.mode === 'path' && S.pathItem){                                    // the bar of a strip being drawn: its points so far and what to do
        const P = S.pathItem;
        hint.innerHTML = `${esc(i18n('Tap the points along the strip'))} (${P.points.length}) <button class="dlg-btn primary" data-b="done" style="padding:2px 10px">${esc(i18n('Done'))}</button>
          <button class="dlg-btn" data-b="undo" style="padding:2px 10px">${esc(i18n('Undo the last point'))}</button><span style="display:inline-flex"><button class="dlg-btn${P.smooth ? '' : ' primary'}" data-b="straight" style="padding:2px 10px;border-radius:8px 0 0 8px">${esc(i18n('Straight'))}</button><button class="dlg-btn${P.smooth ? ' primary' : ''}" data-b="smooth" style="padding:2px 10px;border-radius:0 8px 8px 0">${esc(i18n('Smooth'))}</button></span>
          <button class="dlg-btn" data-b="cancel" style="padding:2px 10px">${esc(i18n('Cancel'))}</button>`;
        hint.querySelector('[data-b="done"]').onclick = finishPath;
        hint.querySelector('[data-b="undo"]').onclick = () => { P.points.pop(); draw(); };
        hint.querySelector('[data-b="straight"]').onclick = () => { P.smooth = false; draw(); };
        hint.querySelector('[data-b="smooth"]').onclick = () => { P.smooth = true; draw(); };
        hint.querySelector('[data-b="cancel"]').onclick = () => { S.mode = ''; S.pathItem = null; draw(); };
      }
    }

    // ---- pan and pinch, tap = a click ----
    const ptrs = new Map(); let moved = false, start = null, lift = null;
    view.addEventListener('pointerdown', e => { if(e.pointerType === 'mouse' && e.button !== 0) return;                // the right and middle buttons are no taps (the menu is theirs)
      if(e.target.closest && e.target.closest('[data-m="hint"]')) return;       // a press on the bar is the bar's, not a tap on the plan
      ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY}); moved = false; start = {x: e.clientX, y: e.clientY, ox: S.ox, oy: S.oy}; view.setPointerCapture(e.pointerId);
      if(S.mode === 'edit'){                                                     // the editor: a corner or a "+" is grabbed, anything else pans
        const h = editHit(e); S.noPan = !!h;
        if(h && e.button !== 2) editGrab(h);
        return;
      }
      const oh = S.mode !== 'edit' && opHandle(e);
      if(oh){                                                                    // a handle of the selected door or window: it is dragged
        const p = plan(), q = planXY(e); wallPush();
        S.opDrag = {...oh, id: e.pointerId, a: oh.o.a.slice(), b: oh.o.b.slice(), start: {x: q.x * p.w, y: q.y * p.h}}; S.noPan = true; return;
      }
      if(S.mode === 'opening'){                                                  // one pointer draws a stroke; a second one makes it a pinch
        S.noPan = true;
        S.opDraw = ptrs.size === 1 ? {id: e.pointerId, a: planXY(e), b: planXY(e), x0: e.clientX, y0: e.clientY} : null;
        return;
      }
      armLift(e);
      S.noPan = !S.mode && !!(lift || (e.target.closest && e.target.closest('[data-id],[data-cluster]')));       // a press on a thing never pans the map: only a press on empty space does
    });
    // an icon or a point of an LED strip: a mouse drags it after 4 px, a finger after a 350 ms press (it may wander 10 px meanwhile; more and nothing happens,
    // the map does not pan); a release drops it; Esc or a release at the start cancels; a short tap opens the sheet; a drag on empty space pans
    function armLift(e){
      if(S.mode || ptrs.size !== 1) return;
      const ic = e.target.closest && e.target.closest('[data-id]');
      let t = null;
      const hit = ic && data.items.find(i => i.id === ic.dataset.id);
      if(hit && !hit.path) t = {it: hit};
      if(!t){                                                  // a point of a strip (its icon sits on the middle point)
        let best = 16;
        for(const it of data.items) (it.path || []).forEach((pt, i) => { const [sx, sy] = planToScreen(pt.x, pt.y), d = Math.hypot(sx - e.clientX, sy - e.clientY); if(d < best){ best = d; t = {it, i}; } });
      }
      if(!t || !EDIT()) return;
      lift = {...t, id: e.pointerId, mouse: e.pointerType === 'mouse', x0: e.clientX, y0: e.clientY, active: false, orig: t.i === undefined ? {x: t.it.x, y: t.it.y} : {...t.it.path[t.i]}};
      lift.timer = setTimeout(() => { if(lift && !moved) startLift(); }, lift.mouse ? 400 : 350);
    }
    const hiSnap = (l, at) => { if(l.i !== undefined) snapPoint(at, l.it.path[l.i - 1] || l.it.path[l.i + 1]); else if(WALL_KINDS.includes((cands.find(c => c.id === l.it.dev) || {}).category)) snapPoint(at, null, 16); };
    function startLift(){ lift.active = true; if(navigator.vibrate) navigator.vibrate(25); draw(); }
    function liftTo(l, at){
      S.drop = l.i === undefined && lift ? {z: zoneAt(at), x: at.x, y: at.y} : null;
      if(l.i === undefined){ l.it.x = at.x; l.it.y = at.y; }
      else { l.it.path[l.i] = at; const m = l.it.path[Math.floor(l.it.path.length / 2)]; l.it.x = m.x; l.it.y = m.y; }
    }
    function dropLift(keep){
      const l = lift; lift = null; S.drop = null; clearTimeout(l.timer);
      if(l.active){
        if(!keep) liftTo(l, l.orig);
        else if(l.i !== undefined) { const pts = l.it.path; liftTo(l, snapPoint(pts[l.i], pts[l.i - 1] || pts[l.i + 1])); }                          // a strip point: the walls and the axes
        else if(WALL_KINDS.includes((cands.find(c => c.id === l.it.dev) || {}).category)) liftTo(l, snapPoint({x: l.it.x, y: l.it.y}, null, 16));   // a wall thing: onto the wall
        draw(); if(keep) changed();
      }
    }
    document.addEventListener('keydown', e => { if(e.key === 'Escape' && lift && box.isConnected) dropLift(false); });
    view.addEventListener('pointermove', e => {
      const prev = ptrs.get(e.pointerId); if(!prev) return;
      if(S.drag){ editDrag(e); return; }
      if(S.opDrag && S.opDrag.id === e.pointerId){ opDragTo(S.opDrag, e); moved = true; draw(); return; }
      if(S.opDraw && S.opDraw.id === e.pointerId && ptrs.size === 1){ S.opDraw.b = planXY(e); ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY}); moved = true; draw(); return; }
      if(lift && lift.id === e.pointerId){
        if(lift.active){ liftTo(lift, planXY(e)); hiSnap(lift, planXY(e)); draw(); return; }
        const d = Math.hypot(e.clientX - lift.x0, e.clientY - lift.y0);
        if(lift.mouse && d > 4){ clearTimeout(lift.timer); startLift(); liftTo(lift, planXY(e)); hiSnap(lift, planXY(e)); draw(); return; }          // a mouse drags at once
        if(!lift.mouse && d > 10){ clearTimeout(lift.timer); lift = null; moved = true; }                                  // a finger moved before the press: nothing, and no pan
      }
      if(ptrs.size === 2){                              // pinch: zoom about the middle of the two fingers
        const other = [...ptrs.entries()].find(([id]) => id !== e.pointerId)[1];
        const d0 = Math.hypot(prev.x - other.x, prev.y - other.y), d1 = Math.hypot(e.clientX - other.x, e.clientY - other.y);
        if(d0 > 0) zoomAt((e.clientX + other.x) / 2, (e.clientY + other.y) / 2, d1 / d0);
        moved = true;
      } else if(S.noPan){
        if(Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) moved = true;          // it began on a thing: not a tap any more, but never a pan
      } else if(start && (moved || Math.hypot(e.clientX - start.x, e.clientY - start.y) > 7)){
        moved = true; S.userZoomed = true; S.ox = start.ox + e.clientX - start.x; S.oy = start.oy + e.clientY - start.y; keepInView();
        start.ox = S.ox - (e.clientX - start.x); start.oy = S.oy - (e.clientY - start.y); placeSoon();     // held at the edge: a way back moves it at once
      }
      ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
    });
    let lastCorner = {i: -1, at: 0};
    const panFromRest = () => {                     // a finger of a pinch lifted: the other pans on from here, not from where it was pressed
      const [o] = ptrs.values(); if(ptrs.size === 1) start = {x: o.x, y: o.y, ox: S.ox, oy: S.oy};
    };
    const up = e => {
      const was = ptrs.has(e.pointerId); ptrs.delete(e.pointerId); panFromRest();
      if(S.opDrag && S.opDrag.id === e.pointerId){ const d = S.opDrag; S.opDrag = null; if(moved) changed(); else draw(); if(!moved) openingSheet(d.o); return; }
      if(S.opDraw && S.opDraw.id === e.pointerId){                               // a stroke: an opening on the nearest wall; a tap: the one under it
        const D = S.opDraw; S.opDraw = null;
        if(Math.hypot(e.clientX - D.x0, e.clientY - D.y0) >= 8) newOpening(D.a, D.b);
        else { const h = hitWall(e); if(h && h.op) openingSheet(h.op); }
        draw(); return;
      }
      if(S.drag){                                                                // a corner let go; two quick presses on one corner delete it
        const d = S.drag; S.drag = null;
        if(!d.moved && !d.fresh){ if(lastCorner.i === d.i && Date.now() - lastCorner.at < 400){ editDelete(d.i); lastCorner = {i: -1, at: 0}; } else lastCorner = {i: d.i, at: Date.now()}; }
        draw(); return;
      }
      if(lift && lift.id === e.pointerId){
        const active = lift.active, back = Math.hypot(e.clientX - lift.x0, e.clientY - lift.y0) < 8;
        dropLift(!back);                                     // a release back at the start cancels
        if(active) return;                                   // it was a lift, not a tap
      } if(was && !moved && ptrs.size === 0 && e.type === 'pointerup') tap(e); };
    view.addEventListener('pointerup', up); view.addEventListener('pointercancel', e => { ptrs.delete(e.pointerId); panFromRest(); S.opDraw = null; if(lift) dropLift(false); });
    view.addEventListener('wheel', e => { e.preventDefault(); zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.15 : 1 / 1.15); }, {passive: false});
    function zoomAt(cx, cy, k){                  // from half the fitted plan to 8 times it (at most 6): a phone at 6 showed one wall
      const fs = fitScale(); if(!(k > 0) || !isFinite(k) || !(fs > 0)) return;
      const r = view.getBoundingClientRect(), nx = clamp(S.scale * k, fs / 2, Math.max(fs, Math.min(6, fs * 8))), f = nx / S.scale;
      S.userZoomed = true; S.ox = (cx - r.left) - ((cx - r.left) - S.ox) * f; S.oy = (cy - r.top) - ((cy - r.top) - S.oy) * f; S.scale = nx; keepInView(); placeSoon();
    }
    function fitScale(){ const p = plan(), w = view.clientWidth, h = view.clientHeight; return p && w && h ? Math.min(w / (S.rot ? p.h : p.w), h / (S.rot ? p.w : p.h)) * 0.98 : 0; }
    function keepInView(){                       // a pan or a zoom never takes the plan off the pane: half the pane (or half the plan) stays covered
      const p = plan(), vw = view.clientWidth, vh = view.clientHeight; if(!p || !vw || !vh) return;
      const W = (S.rot ? p.h : p.w) * S.scale, H = (S.rot ? p.w : p.h) * S.scale, dx = S.rot ? W : 0;      // turned, the plan lies left of ox
      const mx = Math.min(W, vw) / 2, my = Math.min(H, vh) / 2;
      S.ox = clamp(S.ox - dx, mx - W, vw - mx) + dx; S.oy = clamp(S.oy, my - H, vh - my);
    }
    const planToScreen = (x, y) => {                                           // a point of the plan (0..1) -> client coordinates
      const p = plan(), vr = view.getBoundingClientRect(), X = x * p.w * S.scale, Y = y * p.h * S.scale;
      const l = vr.left + view.clientLeft, t = vr.top + view.clientTop;                           // the pane's border is outside the plan's origin
      return S.rot ? [l + S.ox - Y, t + S.oy + X] : [l + S.ox + X, t + S.oy + Y];
    };
    function planXY(e){
      const p = plan(), vr = view.getBoundingClientRect(), a = e.clientX - vr.left - view.clientLeft - S.ox, b = e.clientY - vr.top - view.clientTop - S.oy;
      const X = (S.rot ? b : a) / (p.w * S.scale), Y = (S.rot ? -a : b) / (p.h * S.scale);
      return {x: Math.min(1, Math.max(0, X)), y: Math.min(1, Math.max(0, Y))};
    }

    // ---- zones: outlines the owner names; the name is the label and the dashboard room ----
    const inPoly = (pts, x, y) => {
      let inside = false;
      for(let i = 0, j = pts.length - 1; i < pts.length; j = i++)
        if((pts[i].y > y) !== (pts[j].y > y) && x < (pts[j].x - pts[i].x) * (y - pts[i].y) / ((pts[j].y - pts[i].y) || 1e-12) + pts[i].x) inside = !inside;
      return inside;
    };
    const polyArea = pts => Math.abs(pts.reduce((a, p, i) => a + p.x * pts[(i + 1) % pts.length].y - pts[(i + 1) % pts.length].x * p.y, 0)) / 2;
    const zoneAt = at => data.rooms.filter(z => inPoly(z.poly, at.x, at.y)).sort((a, b) => polyArea(a.poly) - polyArea(b.poly))[0] || null;
    const labelCache = new Map();
    function labelOf(z){                                               // {x, y, w, h, size, vert} in fractions; the visual centre of the free floor (outline minus walls), upright or turned 90 degrees
      const p = plan(), W = p ? p.w : 800, H = p ? p.h : 400;
      const shownIcons = [...((q('icons') || {querySelectorAll: () => []}).querySelectorAll('[data-id],[data-cluster]'))].filter(e => e.style.display !== 'none' && e.style.left);
      const icons = shownIcons.length ? shownIcons.map(e => ({x: parseFloat(e.style.left) / 100 * W, y: parseFloat(e.style.top) / 100 * H}))   // what is on the screen: icons, badges, a fan
        : data.items.filter(i => !i.path).map(i => ({x: i.x * W, y: i.y * H}));
      for(const wl of data.walls){ const n = Math.max(1, Math.ceil(Math.hypot((wl.b[0] - wl.a[0]) * W, (wl.b[1] - wl.a[1]) * H) / 6));     // free walls and link lines are in the way too
        for(let k = 0; k <= n; k++) icons.push({x: (wl.a[0] + (wl.b[0] - wl.a[0]) * k / n) * W, y: (wl.a[1] + (wl.b[1] - wl.a[1]) * k / n) * H}); }
      for(const l of ((q('svg') || {querySelectorAll: () => []}).querySelectorAll('line[data-a]'))){
        if(l.style.display === 'none') continue;
        const x1 = +l.getAttribute('x1'), y1 = +l.getAttribute('y1'), x2 = +l.getAttribute('x2'), y2 = +l.getAttribute('y2'), n = Math.max(1, Math.ceil(Math.hypot(x2 - x1, y2 - y1) / 6));
        for(let k = 0; k <= n; k++) icons.push({x: x1 + (x2 - x1) * k / n, y: y1 + (y2 - y1) * k / n});
      }
      if(RO){                                                          // the demo: doors with their swing, windows and LED strips are in the way too
        const lr = 6 / (S.scale || 1);                                 // a line keeps a name 6 px off, not an icon's berth
        const along = (a, b) => { const n = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / 6)); for(let k = 0; k <= n; k++) icons.push({x: a.x + (b.x - a.x) * k / n, y: a.y + (b.y - a.y) * k / n, r: lr}); };
        for(const o of data.openings){
          const L = opAB(o); if(!L) continue;
          const {A, B} = L, dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1, sg = o.side ? -1 : 1, nx = -dy / len * sg, ny = dx / len * sg;
          const deep = o.kind === 'door' && o.open !== 'slide' ? len : 12;
          for(let k = 0; k <= 4; k++) along({x: A.x + nx * deep * k / 4, y: A.y + ny * deep * k / 4}, {x: B.x + nx * deep * k / 4, y: B.y + ny * deep * k / 4});
        }
        for(const it of data.items) (it.path || []).forEach((pt, i, a) => { if(i) along({x: a[i - 1].x * W, y: a[i - 1].y * H}, {x: pt.x * W, y: pt.y * H}); });
      }
      const ir = Math.max(0.012 * W, iconPx() / (S.scale || 1) * 0.6), temp = !!z.house_room;
      let tempOn = temp;
      const minFs = RO ? 8.5 / (S.scale || 1) : 7;                     // the demo: never smaller than the screen shows (8 px)
      const pl = z.poly.map(a => ({x: a.x * W, y: a.y * H}));
      const xs = pl.map(a => a.x), ys = pl.map(a => a.y);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      const mine = icons.filter(i => i.x > x0 - ir && i.x < x1 + ir && i.y > y0 - ir && i.y < y1 + ir);      // only what lies over the room can be in its name's way
      icons.length = 0; icons.push(...mine);
      const key = JSON.stringify([z.poly, z.name, W, H, S.rot, walls.length, icons.map(i => [Math.round(i.x), Math.round(i.y)]), Math.round(ir), temp, RO ? Math.round(S.scale * 50) : 0]);
      const hit = labelCache.get(z); if(hit && hit.key === key) return hit.l;
      const near = walls.filter(r => r.x1 > x0 - 60 && r.x0 < x1 + 60 && r.y1 > y0 - 60 && r.y0 < y1 + 60);
      const inWall = (x, y) => near.some(r => x > r.x0 && x < r.x1 && y > r.y0 && y < r.y1);
      const free = (x, y) => inPoly(pl, x, y) && !inWall(x, y);
      const segD = (px, py, ax, ay, bx, by) => { const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy || 1, t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l2)); return Math.hypot(px - ax - t * dx, py - ay - t * dy); };
      const clear = (x, y) => { let d = 1e9;                           // the distance to the nearest outline edge or wall
        for(let i = 0, j = pl.length - 1; i < pl.length; j = i++) d = Math.min(d, segD(x, y, pl[j].x, pl[j].y, pl[i].x, pl[i].y));
        for(const r of near) d = Math.min(d, Math.hypot(Math.max(r.x0 - x, 0, x - r.x1), Math.max(r.y0 - y, 0, y - r.y1)));
        return d; };
      const cells = [], step = Math.max(1, Math.max(x1 - x0, y1 - y0) / 64);
      let best = {x: (x0 + x1) / 2, y: (y0 + y1) / 2, d: -1};
      for(let x = x0; x <= x1; x += step) for(let y = y0; y <= y1; y += step){
        if(!free(x, y)) continue;
        const d = clear(x, y); cells.push({x, y, d}); if(d > best.d) best = {x, y, d};
      }
      const mx = cells.reduce((a, c) => a + c.x, 0) / (cells.length || 1), my = cells.reduce((a, c) => a + c.y, 0) / (cells.length || 1);
      const core = cells.filter(c => c.d >= best.d * 0.9);             // the plateau of the pole: the one nearest the middle of the floor
      if(core.length) best = core.reduce((u, v) => Math.hypot(u.x - mx, u.y - my) <= Math.hypot(v.x - mx, v.y - my) ? u : v);
      const reach = (c, dx, dy) => { let n = 0; while(n < 600 && free(c.x + dx * n, c.y + dy * n)) n++; return n; };      // the free run from a point, in plan units
      const span = (c, dx, dy) => Math.min(reach(c, dx, dy), reach(c, -dx, -dy)) * 2;                                   // centred on the point
      const len = z.name.length * (RO ? 0.7 : 0.62);                   // the width of the text per unit of font size (the demo: with a margin)
      const sizeFor = c => { const sx = span(c, 1, 0), sy = span(c, 0, 1), sh = S.rot ? sy : sx, sv = S.rot ? sx : sy;      // the spans as they lie on the screen
        const ok = sh * 0.9 >= len * 9 || sv <= sh;                    // a narrow room: the long side carries the text
        return {vert: !ok, size: Math.max(7, Math.min(RO ? 34 : 17, (ok ? sh : sv) * 0.9 / Math.max(3, len)))}; };      // the demo: names a phone can read
      const below = f => tempOn && !f.vert ? f.size * 1.5 : 0;           // the temperature line under the name
      const boxOf = (c, f) => { const tw = len * f.size, th = f.size * 1.1 + below(f), tall = f.vert !== !!S.rot; return tall ? {hw: th / 2, hh: tw / 2} : {hw: tw / 2, hh: th / 2}; };      // the box in plan units
      const gap = RO ? ir + 4 / (S.scale || 1) : ir;                   // the demo keeps 4 px more around icons and badges
      const mid = (c, f) => S.rot ? [c.x + below(f) / 2, c.y] : [c.x, c.y + below(f) / 2];      // the middle of name and temperature
      const clashes = (c, f) => { const bx = boxOf(c, f), [cx, cy] = mid(c, f); return icons.some(i => Math.abs(i.x - cx) < bx.hw + (i.r || gap) && Math.abs(i.y - cy) < bx.hh + (i.r || gap)); };
      const fits = (c, g) => { const bx = boxOf(c, g), [cx, cy] = mid(c, g);          // the demo: the whole box stays in its room
        return !RO || [[-1, -1], [1, -1], [-1, 1], [1, 1], [0, -1], [0, 1], [-1, 0], [1, 0]].every(([a, b]) => {
          const x = cx + a * bx.hw, y = cy + b * bx.hh; return free(x, y) && clear(x, y) >= 9 / (S.scale || 1); }); };      // 9 px off the walls
      const bad = (c, g) => clashes(c, g) || !fits(c, g);
      let f = sizeFor(best), pick = best, nameless = false;
      if(bad(best, f)){                                                // an icon on the spot: the nearest free spot of the floor, else one font step less
        const steps = [[1, 0.25], [0.85, 0.25], [0.7, 0.5], [0.55, 0.5], ...(RO ? [[0.5, 1], [0.4, 1]] : [])];      // the demo: down to the house's size anywhere
        let found = false;
        for(const [k, far] of steps){      // near the centre first, then smaller, then anywhere in the room
          const g = {vert: f.vert, size: Math.max(minFs, f.size * k)};
          const cand = cells.filter(c => c.d >= Math.min(best.d, g.size * 0.7) && !bad(c, g) && Math.hypot(c.x - best.x, c.y - best.y) <= far * Math.max(x1 - x0, y1 - y0)).sort((u, v) => Math.hypot(u.x - best.x, u.y - best.y) - Math.hypot(v.x - best.x, v.y - best.y))[0];
          if(cand){ pick = cand; f = g; found = true; break; }
        }
        if(RO && !found && temp){                                      // the name alone at the house's size, anywhere in the room
          tempOn = false;
          const g = {vert: f.vert, size: Math.max(minFs, f.size * 0.5)};
          const cand = cells.filter(c => !bad(c, g)).sort((u, v) => Math.hypot(u.x - best.x, u.y - best.y) - Math.hypot(v.x - best.x, v.y - best.y))[0];
          if(cand){ pick = cand; f = g; found = true; } else tempOn = temp;
        }
        if(RO && !found){ nameless = true; f = {vert: false, size: Math.max(7, f.size * 0.5)}; }       // no free spot: the temperature alone
      }
      const l = {x: pick.x / W, y: pick.y / H, w: len * f.size / W, size: f.size, vert: f.vert, nameless, notemp: temp && !tempOn};
      labelCache.set(z, {key, l}); return l;
    }
    const labelHit = e => { const p = plan(); if(!p) return null;                 // the label is upright on the screen, so it is tested there
      for(const z of data.rooms){
        const l = labelOf(z), [sx, sy] = planToScreen(l.x, l.y);
        const hw = l.w * p.w * S.scale / 2 + 6, hh = l.size * S.scale / 2 + 6, rot = l.vert;                                             // on the screen the text is vertical exactly when vert
        if(Math.abs(e.clientX - sx) < (rot ? hh : hw) && Math.abs(e.clientY - sy) < (rot ? hw : hh)) return z;
      }
      return null; };
    function mergeSheet(z){
      const others = data.rooms.filter(o => o !== z && o.name !== z.name);
      openSheet(`<h3 style="margin:0 0 8px">${esc(i18n('Merge with...'))}</h3>${others.map((o, i) => row(String(i), o.name)).join('') || `<div class="bindcaption">${esc(i18n('Nothing left'))}</div>`}`);
      sheet.onclick = ev => { const r = ev.target.closest('[data-pick]'); if(!r) return; const o = others[+r.dataset.pick]; z.name = o.name; z.house_room = o.house_room || ''; closeSheet(); changed(); };   // one name = one room, two outlines
    }
    // the room goes with its outline (every outline of it), after the same question as on its tab; one not saved yet just goes.
    // Not undoable, so never mixed with edits not saved yet: those are saved or cancelled first, never saved here.
    async function delRoom(z){
      if(!z.house_room){ data.rooms = data.rooms.filter(o => o !== z); changed(); return; }
      if(S.dirty || S.saving || S.edit){ q('state').textContent = i18n('Save or cancel the edits first'); return; }
      const room = rooms.find(r => r.id === z.house_room) || {id: z.house_room, name: z.name};
      if(!Home.confirmRoomDelete || !await Home.confirmRoomDelete(room)) return;
      try {
        await Home.api('/api/rooms/' + encodeURIComponent(z.house_room), {method: 'DELETE'});
        const srv = await Home.api('/api/map');
        Object.assign(data, {rev: srv.rev, rooms: srv.rooms, openings: srv.openings}); base = snap();
        rooms = await Home.api('/api/rooms').catch(() => rooms);
        if(Home.refreshTabs) Home.refreshTabs();
        draw();
      } catch(e){ q('state').textContent = e.message || String(e); }
    }

    function zoneSheet(z){
      setTimeout(() => { const l = labelOf(z); reveal(l.x, l.y); }, 0);
      const room = rooms.find(r => r.id === z.house_room), devs = ((room && room.devices) || []).map(d => dispName({id: d, name: d}));
      openSheet(`<div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">${esc(i18n('Zone'))}</h3><button class="dlg-btn" data-s="close">&times;</button></div>
        <div class="row"><input class="askin" data-s="zname" value="${esc(z.name)}" style="width:100%;box-sizing:border-box"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px"><button class="dlg-btn primary" data-s="rename">${esc(i18n('Rename'))}</button>
          <button class="dlg-btn" data-s="merge">${esc(i18n('Merge with...'))}</button>
          <button class="dlg-btn" data-s="shape">${esc(i18n('Change the outline'))}</button>${z.house_room && window.openRoomSensors ? `<button class="dlg-btn" data-s="sensors">${esc(i18n('Thermometer\u2026'))}</button>` : ''}<button class="dlg-btn" data-s="del" style="color:#e5484d;border-color:#e5484d">${esc(i18n('Delete the room'))}</button></div><div data-s="msg" class="bindcaption" style="margin-top:6px"></div>
        ${devs.length ? `<div class="bindcaption" style="margin-top:10px">${esc(i18n('In this room'))}: ${esc(devs.join(', '))}</div>` : ''}`);
      const msg = t => { sheet.querySelector('[data-s="msg"]').textContent = t; };
      sheet.querySelector('[data-s="close"]').onclick = closeSheet;
      sheet.querySelector('[data-s="rename"]').onclick = () => {
        const n = sheet.querySelector('[data-s="zname"]').value.trim();
        if(!n || n === z.name) return;
        if(data.rooms.some(o => o !== z && o.name.toLowerCase() === n.toLowerCase())) return msg(i18n('A zone with this name exists: use Merge'));
        if(rooms.some(r => r.id !== z.house_room && String(r.name).toLowerCase() === n.toLowerCase())) return msg(i18n('A room with this name exists already'));
        z.name = n; closeSheet(); changed();
      };
      sheet.querySelector('[data-s="merge"]').onclick = () => mergeSheet(z);
      sheet.querySelector('[data-s="shape"]').onclick = () => startEdit(z);
      sheet.querySelector('[data-s="del"]').onclick = () => { closeSheet(); delRoom(z); };
      const sn = sheet.querySelector('[data-s="sensors"]'); if(sn) sn.onclick = () => { closeSheet(); window.openRoomSensors(z.house_room); };     // the room's thermometer
    }

    // ---- the outline of a zone: drag a corner, add one at the "+" of an edge, delete one, undo and redo; Done saves once, Cancel puts everything back ----
    const simplePoly = pts => {                                          // the same rule as the house: no edge crosses another, and the outline has an area
      const n = pts.length, cr = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
      const on = (a, b, c) => Math.abs(cr(a, b, c)) < 1e-12 && Math.min(a.x, b.x) - 1e-12 <= c.x && c.x <= Math.max(a.x, b.x) + 1e-12 && Math.min(a.y, b.y) - 1e-12 <= c.y && c.y <= Math.max(a.y, b.y) + 1e-12;
      const hit = (p, q, r, s) => { const d1 = cr(p, q, r), d2 = cr(p, q, s), d3 = cr(r, s, p), d4 = cr(r, s, q);
        return ((d1 > 0) !== (d2 > 0) && (d3 > 0) !== (d4 > 0) && d1 && d2 && d3 && d4) || on(p, q, r) || on(p, q, s) || on(r, s, p) || on(r, s, q); };
      for(let i = 0; i < n; i++) for(let j = i + 1; j < n; j++){
        if(j === i + 1 || (i === 0 && j === n - 1)) continue;
        if(hit(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n])) return false;
      }
      return polyArea(pts) > 1e-6;
    };
    // a zone edge is a wall (a thick line) or open (a thin dashed border: an open-plan kitchen); walls not marked yet: nothing is drawn
    function edgeLines(poly, walls, w, h, wallColour, openColour){
      if(!walls || walls.length !== poly.length) return '';
      return poly.map((a, i) => { const b = poly[(i + 1) % poly.length];
        return walls[i] ? `<line x1="${a.x * w}" y1="${a.y * h}" x2="${b.x * w}" y2="${b.y * h}" stroke="${wallColour}" stroke-width="5" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`
          : `<line x1="${a.x * w}" y1="${a.y * h}" x2="${b.x * w}" y2="${b.y * h}" stroke="${openColour}" stroke-width="1.5" stroke-dasharray="6 5" vector-effect="non-scaling-stroke"/>`; }).join('');
    }
    // ---- free walls (inside a zone), doors and windows (on any wall); the "Walls" tool ----
    const opAB = o => { const p = plan(); return p ? {A: {x: o.a[0] * p.w, y: o.a[1] * p.h}, B: {x: o.b[0] * p.w, y: o.b[1] * p.h}} : null; };     // plan units
    // a window: a gap with a thin double line; a swing door: its leaf and a quarter circle; a sliding door: a leaf beside the gap and an arrow
    function openingSVG(o){
      const L = opAB(o); if(!L) return '';
      const {A, B} = L, dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1, sgn = o.side ? -1 : 1, nx = -dy / len * sgn, ny = dx / len * sgn;
      const hi = S.mode === 'opening' || S.mode === 'wall' ? '#ffb020' : '#cfd8e3';
      let g = `<line x1="${A.x}" y1="${A.y}" x2="${B.x}" y2="${B.y}" stroke="#0b0e13" stroke-width="9" vector-effect="non-scaling-stroke"/>`;
      if(o.kind === 'window'){
        for(const k of [-1.6, 1.6]) g += `<line x1="${A.x + nx * k}" y1="${A.y + ny * k}" x2="${B.x + nx * k}" y2="${B.y + ny * k}" stroke="${hi}" stroke-width="1.3" vector-effect="non-scaling-stroke"/>`;
      } else if(o.open === 'slide'){
        const off = Math.min(6, len * 0.15), ux = dx / len, uy = dy / len, E = {x: A.x + dx * 0.7 + nx * off, y: A.y + dy * 0.7 + ny * off}, aw = Math.min(6, len * 0.2);
        g += `<line x1="${A.x + nx * off}" y1="${A.y + ny * off}" x2="${E.x}" y2="${E.y}" stroke="${hi}" stroke-width="2.2" vector-effect="non-scaling-stroke"/>`;
        g += `<path d="M${E.x + ux * aw * 1.4},${E.y + uy * aw * 1.4} m${-ux * aw - nx * aw * .5},${-uy * aw - ny * aw * .5} l${ux * aw + nx * aw * .5},${uy * aw + ny * aw * .5} l${-ux * aw + nx * aw * .5},${-uy * aw + ny * aw * .5}" fill="none" stroke="${hi}" stroke-width="1.4" vector-effect="non-scaling-stroke"/>`;
      } else {
        const H = o.open === 'right' ? B : A, E = o.open === 'right' ? A : B, T = {x: H.x + nx * len, y: H.y + ny * len};
        const sweep = (T.x - H.x) * (E.y - H.y) - (T.y - H.y) * (E.x - H.x) > 0 ? 1 : 0;
        g += `<path d="M${H.x},${H.y} L${T.x},${T.y} A${len},${len} 0 0 ${sweep} ${E.x},${E.y}" fill="none" stroke="${hi}" stroke-width="1.2" vector-effect="non-scaling-stroke"/>`;
      }
      if(S.opSel === o.id){                                             // the one being changed: its ends and middle are handles
        const r = 7 / S.scale, M = {x: (A.x + B.x) / 2, y: (A.y + B.y) / 2};
        for(const P of [A, B]) g += `<circle cx="${P.x}" cy="${P.y}" r="${r}" fill="#ffb020" stroke="#0b0e13" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
        g += `<rect x="${M.x - r}" y="${M.y - r}" width="${2 * r}" height="${2 * r}" rx="${r * 0.3}" fill="#ffb020" stroke="#0b0e13" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
      }
      return `<g data-op="${esc(o.id)}">${g}</g>`;
    }
    // a handle of the selected opening under the pointer: 'a', 'b' (its ends: the width) or 'm' (its middle: along the wall)
    function opHandle(e){
      const o = S.opSel && data.openings.find(x => x.id === S.opSel); if(!o) return null;
      const near = (x, y) => { const [sx, sy] = planToScreen(x, y); return Math.hypot(sx - e.clientX, sy - e.clientY) < 16; };
      if(near(o.a[0], o.a[1])) return {o, which: 'a'};
      if(near(o.b[0], o.b[1])) return {o, which: 'b'};
      if(near((o.a[0] + o.b[0]) / 2, (o.a[1] + o.b[1]) / 2)) return {o, which: 'm'};
      return null;
    }
    function opDragTo(D, e){                                           // the pointer projected onto the opening's own line (the wall)
      const p = plan(), q = planXY(e), P = {x: q.x * p.w, y: q.y * p.h}, A0 = {x: D.a[0] * p.w, y: D.a[1] * p.h}, B0 = {x: D.b[0] * p.w, y: D.b[1] * p.h};
      const len = Math.hypot(B0.x - A0.x, B0.y - A0.y) || 1, ux = (B0.x - A0.x) / len, uy = (B0.y - A0.y) / len, min = Math.max(len * 0.15, p.w * 0.01);
      const along = (Q, O) => (Q.x - O.x) * ux + (Q.y - O.y) * uy, at = (O, t) => [+((O.x + ux * t) / p.w).toFixed(6), +((O.y + uy * t) / p.h).toFixed(6)];
      if(D.which === 'm'){ const t = along(P, D.start); D.o.a = at(A0, t); D.o.b = at(B0, t); }
      else if(D.which === 'a'){ const t = Math.min(along(P, A0), len - min); D.o.a = at(A0, t); }
      else { const t = Math.max(along(P, A0), min); D.o.b = at(A0, t); }
    }
    const wallPush = () => { (S.wallHist = S.wallHist || []).push(JSON.stringify({walls: data.walls, openings: data.openings})); S.wallRedo = []; };
    function wallUndo(from, to){
      if(!from || !from.length) return;
      to.push(JSON.stringify({walls: data.walls, openings: data.openings}));
      const o = JSON.parse(from.pop()); data.walls = o.walls; data.openings = o.openings; closeSheet(); changed();
    }
    // every wall line of the map in plan fractions: the zone edges marked as walls and the free walls
    const wallLines = () => [...data.rooms.flatMap(z => z.poly.map((a, i) => (z.walls || [])[i] && z.walls.length === z.poly.length ? {A: a, B: z.poly[(i + 1) % z.poly.length]} : null).filter(Boolean)),
                             ...data.walls.map(wl => ({A: {x: wl.a[0], y: wl.a[1]}, B: {x: wl.b[0], y: wl.b[1]}, wall: wl.id}))];
    // an opening still lies on a wall: both ends within a unit of one wall line, inside it
    function onAnyWall(o){
      const p = plan(); if(!p) return true;
      const P = q => ({x: q.x * p.w, y: q.y * p.h}), a = P({x: o.a[0], y: o.a[1]}), b = P({x: o.b[0], y: o.b[1]});
      return wallLines().some(wl => { const A = P(wl.A), B = P(wl.B), dx = B.x - A.x, dy = B.y - A.y, l2 = dx * dx + dy * dy; if(l2 < 1) return false;
        const on = q => { const t = ((q.x - A.x) * dx + (q.y - A.y) * dy) / l2; return t >= -0.01 && t <= 1.01 && Math.abs((q.x - A.x) * dy - (q.y - A.y) * dx) / Math.sqrt(l2) <= 1; };
        return on(a) && on(b); });
    }
    // a stroke drawn near a wall lands on it: both ends within 30 screen px of the wall's line, projected onto it; null: no wall there
    function snapOpening(a, b){
      const p = plan(); if(!p) return null;
      const sc = S.scale || 1, P = q => ({x: q.x * p.w, y: q.y * p.h});
      let best = null;
      for(const wl of wallLines()){
        const A = P(wl.A), B = P(wl.B), dx = B.x - A.x, dy = B.y - A.y, l2 = dx * dx + dy * dy; if(l2 < 1) continue;
        const proj = q => { const Q = P(q), t = ((Q.x - A.x) * dx + (Q.y - A.y) * dy) / l2, tc = Math.max(0, Math.min(1, t)); return {t: tc, d: Math.hypot(Q.x - A.x - tc * dx, Q.y - A.y - tc * dy) * sc}; };
        const pa = proj(a), pb = proj(b), d = Math.max(pa.d, pb.d);
        if(d > 30 || (best && d >= best.d)) continue;
        const t0 = Math.min(pa.t, pb.t), t1 = Math.max(pa.t, pb.t);
        if((t1 - t0) * Math.sqrt(l2) < 4) continue;                            // a few units at least: else it was no stroke along the wall
        best = {d, A, B, t0, t1};
      }
      if(!best) return null;
      const at = t => [+((best.A.x + (best.B.x - best.A.x) * t) / p.w).toFixed(6), +((best.A.y + (best.B.y - best.A.y) * t) / p.h).toFixed(6)];
      let ea = at(best.t0), eb = at(best.t1);
      if(eb[0] < ea[0] - 1e-6 || (Math.abs(eb[0] - ea[0]) <= 1e-6 && eb[1] < ea[1])) [ea, eb] = [eb, ea];      // a runs left (or up) of b: "left" and "right" mean the same on every wall
      return {a: ea, b: eb};
    }
    function newOpening(a, b){
      const on = snapOpening(a, b);
      if(!on){ S.opMsg = i18n('Draw it along a wall: a room edge marked as a wall, or a wall inside a room'); draw(); return; }
      S.opMsg = '';
      openingSheet({id: uid(), a: on.a, b: on.b, kind: '', open: 'left', side: false}, true);
    }
    function inSwing(o, e){                                            // a press inside a swing door's quarter circle is on that door
      const L = opAB(o); if(!L || o.kind !== 'door' || o.open === 'slide') return false;
      const p = plan(), q = planXY(e), P = {x: q.x * p.w, y: q.y * p.h}, {A, B} = L, dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1;
      const sgn = o.side ? -1 : 1, nx = -dy / len * sgn, ny = dx / len * sgn, H = o.open === 'right' ? B : A, E = o.open === 'right' ? A : B;
      const vx = P.x - H.x, vy = P.y - H.y, ux = (E.x - H.x) / len, uy = (E.y - H.y) / len;
      return Math.hypot(vx, vy) <= len * 1.05 && vx * nx + vy * ny >= -len * 0.05 && vx * ux + vy * uy >= -len * 0.05;
    }
    function hitWall(e){                                                // what wall or opening is under the pointer (screen distance)
      const d2 = (A, B) => { const [ax, ay] = planToScreen(A.x, A.y), [bx, by] = planToScreen(B.x, B.y), dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy || 1;
        const t = Math.max(0, Math.min(1, ((e.clientX - ax) * dx + (e.clientY - ay) * dy) / l2)); return [Math.hypot(e.clientX - ax - t * dx, e.clientY - ay - t * dy), t]; };
      for(const o of data.openings) if(d2({x: o.a[0], y: o.a[1]}, {x: o.b[0], y: o.b[1]})[0] < 10 || inSwing(o, e)) return {op: o};
      for(const wl of data.walls){ const [d, t] = d2({x: wl.a[0], y: wl.a[1]}, {x: wl.b[0], y: wl.b[1]}); if(d < 9) return {wall: wl.id, t}; }
      return null;
    }
    function wallTap(e, at){
      const h = hitWall(e), p = plan();
      if(h && h.op){ openingSheet(h.op); return; }
      if(h && h.wall && !S.wallStart){ wallSheet(h); return; }
      if(!S.wallStart){ S.wallStart = snapPoint(at, null); draw(); return; }
      const b = snapPoint(at, S.wallStart);
      if(Math.hypot((b.x - S.wallStart.x) * p.w, (b.y - S.wallStart.y) * p.h) >= 3){ wallPush(); data.walls.push({id: uid(), a: [S.wallStart.x, S.wallStart.y], b: [b.x, b.y]}); }
      S.wallStart = null; changed();
    }
    function wallSheet(h){                                              // a tap on a free wall: it goes, with the doors and windows on it
      openSheet(`<div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">${esc(i18n('Wall'))}</h3><button class="dlg-btn" data-s="close">&times;</button></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px"><button class="dlg-btn" data-s="delwall" style="color:#e5484d;border-color:#e5484d">${esc(i18n('Remove the wall'))}</button></div>`);
      sheet.querySelector('[data-s="close"]').onclick = closeSheet;
      sheet.querySelector('[data-s="delwall"]').onclick = () => { const wl = data.walls.find(x => x.id === h.wall); wallPush(); data.walls = data.walls.filter(x => x !== wl); closeSheet(); changed(); };      // changed() drops (and counts) the doors on it
    }
    // an opening's sheet: Window / Door; a door opens sliding, to the left or to the right, and its leaf may lie on the other side; remove.
    // A new one (just drawn) is kept only once Window or Door is chosen.
    function openingSheet(o, isNew){
      S.opSel = isNew ? null : o.id; draw();
      const seg = (k, v, label) => `<button class="dlg-btn${o[k] === v ? ' primary' : ''}" data-s="${k}-${v}">${esc(i18n(label))}</button>`;
      openSheet(`<div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">${esc(i18n(o.kind === 'door' ? 'Door' : o.kind === 'window' ? 'Window' : 'Window or door?'))}</h3><button class="dlg-btn" data-s="close">&times;</button></div>
        <div style="display:flex;gap:8px;margin:10px 0">${seg('kind', 'window', 'Window')}${seg('kind', 'door', 'Door')}</div>
        ${o.kind === 'door' ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin:6px 0">${seg('open', 'slide', 'Sliding')}${seg('open', 'left', 'To the left')}${seg('open', 'right', 'To the right')}</div>
          <div style="margin:6px 0"><button class="dlg-btn" data-s="side">${esc(i18n('The other way'))}</button></div>` : ''}
        ${isNew ? '' : `<div class="bindcaption" style="margin-top:8px">${esc(i18n('On the map: drag an end to change its width, the middle to move it along the wall'))}</div>
          <div style="margin-top:10px"><button class="dlg-btn" data-s="del" style="color:#e5484d;border-color:#e5484d">${esc(i18n('Remove'))}</button></div>`}`);
      const sq = k => sheet.querySelector(`[data-s="${k}"]`);
      sq('close').onclick = closeSheet;
      const set = (k, v) => { wallPush(); o[k] = v; if(isNew){ data.openings.push(o); isNew = false; } changed(); openingSheet(o, false); };
      for(const v of ['window', 'door']) sq('kind-' + v).onclick = () => set('kind', v);
      for(const v of ['slide', 'left', 'right']) if(sq('open-' + v)) sq('open-' + v).onclick = () => set('open', v);
      if(sq('side')) sq('side').onclick = () => set('side', !o.side);
      if(sq('del')) sq('del').onclick = () => { wallPush(); data.openings = data.openings.filter(x => x !== o); closeSheet(); changed(); };
    }
    function startEdit(z, isNew){
      S.edit = {z, poly: z.poly.map(p => ({x: p.x, y: p.y})), walls: z.walls && z.walls.length === z.poly.length ? z.walls.slice() : null, hist: [], redo: [], sel: -1, msg: '', isNew: !!isNew};
      S.mode = 'edit'; closeSheet(); draw();
    }
    const editSnap = E => JSON.stringify({poly: E.poly, walls: E.walls});
    const editLoad = (E, t) => { const o = JSON.parse(t); E.poly = o.poly; E.walls = o.walls; };
    const edgeLen = (E, i) => { const p = plan(), a = E.poly[i], b = E.poly[(i + 1) % E.poly.length]; return Math.hypot((b.x - a.x) * p.w, (b.y - a.y) * p.h); };
    const editPush = () => { S.edit.hist.push(editSnap(S.edit)); S.edit.redo = []; };
    function editUndo(){ const E = S.edit; if(!E || !E.hist.length) return; E.redo.push(editSnap(E)); editLoad(E, E.hist.pop()); E.sel = -1; E.msg = ''; draw(); }
    function editRedo(){ const E = S.edit; if(!E || !E.redo.length) return; E.hist.push(editSnap(E)); editLoad(E, E.redo.pop()); E.sel = -1; E.msg = ''; draw(); }
    function editDelete(i){
      const E = S.edit; if(!E || E.poly.length <= 3 || i < 0) return; editPush();
      if(E.walls){ const n = E.poly.length, pre = (i + n - 1) % n; E.walls[pre] = E.walls[pre] || E.walls[i]; E.walls.splice(i, 1); }    // two edges become one: a wall if either was
      E.poly.splice(i, 1); E.sel = -1; E.msg = ''; draw();
    }
    function editWall(i){                                                // a tap on an edge: wall <-> open
      const E = S.edit; editPush();
      E.walls = E.walls || E.poly.map(() => true);
      E.walls[i] = !E.walls[i]; E.sel = -1; draw();
    }
    const sameEdge = (a, b, c, d) => { const eq = (p, q) => Math.abs(p.x - q.x) < 1e-4 && Math.abs(p.y - q.y) < 1e-4; return (eq(a, c) && eq(b, d)) || (eq(a, d) && eq(b, c)); };
    function editDone(){
      const E = S.edit;
      if(!simplePoly(E.poly)){ E.msg = i18n('The outline crosses itself'); draw(); return; }
      E.z.poly = E.poly.map(p => ({x: p.x, y: p.y}));
      if(E.walls){
        E.z.walls = E.walls.slice();
        for(const o of data.rooms){                                      // the neighbour's side of a shared edge says the same
          if(o === E.z) continue;
          o.poly.forEach((a, j) => { const b = o.poly[(j + 1) % o.poly.length];
            E.poly.forEach((c, i) => { if(sameEdge(a, b, c, E.poly[(i + 1) % E.poly.length])){ o.walls = o.walls && o.walls.length === o.poly.length ? o.walls : o.poly.map(() => true); o.walls[j] = E.walls[i]; } }); });
        }
      }
      if(E.isNew) data.rooms.push(E.z);
      S.edit = null; S.mode = ''; changed();
    }
    function editCancel(){ S.edit = null; S.mode = ''; S.drag = null; draw(); }
    function editHit(e){                                                 // a corner within 14 px, else the middle of an edge within 12 px
      const E = S.edit; if(!E) return null;
      let best = null, bd = 14;
      E.poly.forEach((a, i) => { const [x, y] = planToScreen(a.x, a.y), d = Math.hypot(x - e.clientX, y - e.clientY); if(d < bd){ bd = d; best = {kind: 'v', i}; } });
      if(best) return best;
      bd = 12;
      E.poly.forEach((a, i) => { const b = E.poly[(i + 1) % E.poly.length], [x, y] = planToScreen((a.x + b.x) / 2, (a.y + b.y) / 2), d = Math.hypot(x - e.clientX, y - e.clientY); if(d < bd){ bd = d; best = {kind: 'm', i}; } });
      if(best) return best;
      bd = 9;                                                            // else the edge itself: wall or open
      E.poly.forEach((a, i) => { const b = E.poly[(i + 1) % E.poly.length], [ax, ay] = planToScreen(a.x, a.y), [bx, by] = planToScreen(b.x, b.y);
        const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy || 1, t = Math.max(0, Math.min(1, ((e.clientX - ax) * dx + (e.clientY - ay) * dy) / l2));
        const d = Math.hypot(e.clientX - ax - t * dx, e.clientY - ay - t * dy); if(d < bd){ bd = d; best = {kind: 'e', i}; } });
      return best;
    }
    function editGrab(h){                                                // a press on a corner drags it; a press on a "+" makes a corner there and drags that
      const E = S.edit;
      if(h.kind === 'e'){ editWall(h.i); return; }
      if(h.kind === 'm'){ editPush(); const a = E.poly[h.i], b = E.poly[(h.i + 1) % E.poly.length]; E.poly.splice(h.i + 1, 0, {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}); if(E.walls) E.walls.splice(h.i + 1, 0, E.walls[h.i]); h = {kind: 'v', i: h.i + 1, fresh: true}; }   // both halves keep the edge's mark
      E.sel = h.i; S.drag = {i: h.i, pushed: !!h.fresh, moved: false, fresh: !!h.fresh}; draw();
    }
    function editDrag(e){
      const E = S.edit, d = S.drag;
      if(!d.pushed){ editPush(); d.pushed = true; }
      d.moved = true;
      const n = E.poly.length, prev = E.poly[(d.i + n - 1) % n];
      E.poly[d.i] = snapPoint(planXY(e), prev);                          // onto the walls and to 0 / 90 degrees from the neighbour
      draw();
    }
    document.addEventListener('keydown', e => {
      if(S.mode !== 'edit' || !box.isConnected || /^(INPUT|SELECT|TEXTAREA)$/.test((e.target || {}).tagName || '')) return;
      const k = e.key.toLowerCase();
      if((e.ctrlKey || e.metaKey) && k === 'z' && !e.shiftKey){ e.preventDefault(); editUndo(); }
      else if((e.ctrlKey || e.metaKey) && (k === 'y' || (k === 'z' && e.shiftKey))){ e.preventDefault(); editRedo(); }
      else if(k === 'delete' || k === 'backspace'){ e.preventDefault(); editDelete(S.edit.sel); }
      else if(k === 'escape'){ editCancel(); }
    });

    // ---- the menu of the right button (a desktop; a touch screen has the sheets) ----
    function closeCtx(){ const m = box.querySelector('[data-m="ctx"]'); if(m) m.remove(); }
    function openCtx(e, entries){
      closeCtx();
      if(!entries.length) return;
      const m = document.createElement('div');
      m.dataset.m = 'ctx';
      m.style.cssText = `position:fixed;left:${Math.min(e.clientX, window.innerWidth - 230)}px;top:${Math.min(e.clientY, window.innerHeight - 40 - entries.length * 34)}px;min-width:210px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px;box-shadow:0 6px 20px rgba(0,0,0,.45);z-index:60;font-size:15px`;
      entries.forEach((en, i) => {
        if(en === '-'){ m.insertAdjacentHTML('beforeend', '<div style="border-top:1px solid var(--line);margin:4px 0"></div>'); return; }
        const r = document.createElement('div');
        r.dataset.i = i; r.textContent = en.t; r.style.cssText = `padding:7px 12px;border-radius:6px;cursor:pointer;${en.red ? 'color:#ff6b6b' : ''}`;
        r.onpointerdown = ev => ev.stopPropagation();
        r.onclick = () => { closeCtx(); en.f(); };
        m.appendChild(r);
      });
      box.appendChild(m);
    }
    document.addEventListener('pointerdown', e => { if(box.isConnected && !(e.target.closest && e.target.closest('[data-m="ctx"]'))) closeCtx(); }, true);
    document.addEventListener('keydown', e => { if(e.key === 'Escape') closeCtx(); });
    const removeItem = it => { data.items = data.items.filter(i => i.id !== it.id); data.links = data.links.filter(l => l.load !== it.id); changed(); };
    function blink(it){                                                  // show which one it is: switch its first channel and back
      const st = Home.state(it.dev) || {}, k = Object.keys(st.values || {}).find(c => /^state/.test(c) && typeof st.values[c] === 'boolean');
      if(!k) return;
      const was = st.values[k];
      Home.sendControl(it.dev, k, !was); setTimeout(() => Home.sendControl(it.dev, k, was), 900);
    }
    async function renameLoad(it){                                          // a lamp's name: the server checks it on save too
      const name = ((await Home.askText(i18n('New name:'), it.name || '')) || '').trim();
      if(!name || name === it.name) return;
      if(data.items.some(i => i !== it && i.kind === 'load' && String(i.name || '').trim().toLowerCase() === name.toLowerCase())){
        q('state').textContent = i18n("'{0}' is already taken by another lamp", name); return;
      }
      it.name = name; changed();
    }
    function ctxEntries(e){
      const at = planXY(e), hit = document.elementFromPoint(e.clientX, e.clientY), out = [];
      if(S.mode === 'edit'){ const h = editHit(e); if(h && h.kind === 'v' && S.edit.poly.length > 3) out.push({t: i18n('Delete the point'), f: () => editDelete(h.i)}); return out; }
      if(S.mode || !EDIT()) return out;
      const ic = hit && hit.closest ? hit.closest('[data-id]') : null, it = ic && data.items.find(i => i.id === ic.dataset.id);
      if(it){
        const cand = cands.find(c => c.id === it.dev) || {};
        if(it.kind === 'load') out.push({t: i18n('Link'), f: () => linkSheet(it)}, {t: i18n('Rename'), f: () => renameLoad(it)});
        else if(['switch', 'dimmer', 'socket'].includes(cand.category)) out.push({t: i18n('What it turns on'), f: () => drivesSheet(it)});
        if(!it.path) out.push({t: i18n('Move'), f: () => { S.mode = 'move'; S.pending = it; draw(); }});
        if(it.kind === 'device') out.push({t: i18n('Blink'), f: () => blink(it)});
        out.push('-', {t: i18n('Remove'), red: true, f: () => removeItem(it)});
        return out;
      }
      const z = zoneAt(at);
      if(z) out.push({t: i18n('Rename'), f: () => zoneSheet(z)}, {t: i18n('Change the outline'), f: () => startEdit(z)}, {t: i18n('Merge with...'), f: () => mergeSheet(z)}, {t: i18n('Delete the room'), red: true, f: () => delRoom(z)}, '-');
      out.push({t: i18n('Place a device here'), f: () => spotSheet(at)}, {t: i18n('New lamp here'), f: () => newLoadSheet(at, 'lamp')}, {t: i18n('New room here'), f: () => newRoomSheet(at)});
      return out;
    }
    view.addEventListener('contextmenu', e => { e.preventDefault(); openCtx(e, ctxEntries(e)); });
    // a new room starts as a small rectangle at the spot and is edited like any other
    function newRoomSheet(at){
      const names = rooms.map(r => r.name || r.id).filter(n => !data.rooms.some(z => z.name === n));
      openSheet(`<h3 style="margin:0 0 8px">${esc(i18n('New room'))}</h3>${names.map(n => row(n, n)).join('')}<div class="row"><input class="askin" data-s="rname" placeholder="${esc(i18n('Name'))}">
        <button class="dlg-btn primary" data-s="rok">${esc(i18n('Create'))}</button></div>`);
      const make = n => {
        const p = plan(), hw = 0.05, hh = hw * p.w / p.h, x0 = clamp(at.x, hw, 1 - hw), y0 = clamp(at.y, hh, 1 - hh);
        startEdit({id: uid(), name: n, house_room: (rooms.find(r => (r.name || r.id) === n) || {}).id || '', poly: [{x: x0 - hw, y: y0 - hh}, {x: x0 + hw, y: y0 - hh}, {x: x0 + hw, y: y0 + hh}, {x: x0 - hw, y: y0 + hh}]}, true);
      };
      sheet.onclick = ev => { const r = ev.target.closest('[data-pick]'); if(r) make(r.dataset.pick); };
      sheet.querySelector('[data-s="rok"]').onclick = () => { const n = sheet.querySelector('[data-s="rname"]').value.trim(); if(n) make(n); };
    }

    // ---- snapping of a strip's points: onto the walls of the vector plan, and to 0 / 90 degrees from the previous point ----
    let walls = [], snapBase = [];                                     // the wall rectangles of the vector plan (its own units); the edges of the walls and of the shapes marked data-snap
    const edgesOf = (pts, closed) => pts.slice(0, closed ? pts.length : -1).map((a, i) => { const b = pts[(i + 1) % pts.length]; return {ax: a[0], ay: a[1], bx: b[0], by: b[1]}; });
    async function loadWalls(){
      const u = data.underlay, legacy = !u && data.scheme_img !== false;
      if(!legacy && !(u && u.show && u.kind === 'svg' && !((u.rot || 0) % 360))){ walls = []; snapBase = []; return; }      // only a shown, unturned SVG plan: else the zone edges
      try {
        const r = await fetch(legacy ? '/api/map/scheme' : '/api/map/underlay', {headers: Home.authHeaders()});
        const doc = new DOMParser().parseFromString(await r.text(), 'image/svg+xml'), g = doc.querySelector('g[data-walls], g[shape-rendering="crispEdges"]');
        const vb = (doc.documentElement.getAttribute('viewBox') || '0 0 1 1').trim().split(/[\s,]+/).map(Number);
        const k = legacy ? 1 : u.w / (vb[2] || 1), ox0 = legacy ? 0 : u.x, oy0 = legacy ? 0 : u.y;
        const tx = v => ox0 + (v - vb[0]) * k, ty = v => oy0 + (v - vb[1]) * k;      // the drawing's units -> the frame
        const box4 = e => { const x = tx(+e.getAttribute('x')), y = ty(+e.getAttribute('y')); return {x0: x, y0: y, x1: x + +e.getAttribute('width') * k, y1: y + +e.getAttribute('height') * k}; };
        walls = g ? [...g.querySelectorAll('rect')].map(box4) : [];
        snapBase = [];
        for(const r4 of walls) snapBase.push(...edgesOf([[r4.x0, r4.y0], [r4.x1, r4.y0], [r4.x1, r4.y1], [r4.x0, r4.y1]], true));
        for(const e of doc.querySelectorAll('[data-snap]')){                                   // the garden and fence outlines: not walls, but things stick to them
          const t = e.localName;
          if(t === 'rect'){ const r4 = box4(e); snapBase.push(...edgesOf([[r4.x0, r4.y0], [r4.x1, r4.y0], [r4.x1, r4.y1], [r4.x0, r4.y1]], true)); }
          else if(t === 'polygon' || t === 'polyline'){ const pts = (e.getAttribute('points') || '').trim().split(/[\s,]+/).map(Number); const q2 = []; for(let i = 0; i + 1 < pts.length; i += 2) q2.push([tx(pts[i]), ty(pts[i + 1])]); snapBase.push(...edgesOf(q2, t === 'polygon')); }
          else if(t === 'line') snapBase.push({ax: tx(+e.getAttribute('x1')), ay: ty(+e.getAttribute('y1')), bx: tx(+e.getAttribute('x2')), by: ty(+e.getAttribute('y2'))});
        }
      } catch(e){ walls = []; snapBase = []; }
    }
    function snapSegs(){                                               // the scheme's edges, every zone edge (not the outline being edited), the free walls
      const p = plan(), out = snapBase.slice();
      for(const wl of data.walls) out.push({ax: wl.a[0] * p.w, ay: wl.a[1] * p.h, bx: wl.b[0] * p.w, by: wl.b[1] * p.h});
      for(const z of data.rooms){ if(S.edit && S.edit.z === z) continue; z.poly.forEach((a, i) => { const b = z.poly[(i + 1) % z.poly.length]; out.push({ax: a.x * p.w, ay: a.y * p.h, bx: b.x * p.w, by: b.y * p.h}); }); }
      return out;
    }
    function snapPoint(at, prev, px = 10){                             // the nearest edge within px screen pixels; its highlight is S.snapHi
      const p = plan(); if(!p) return at;
      let x = at.x * p.w, y = at.y * p.h, bx = null, by = null, sx = null, sy = null, sl = null;
      const tol = px / S.scale;                                        // a few screen pixels, in plan units
      let dx = tol, dy = tol, ds = tol;
      for(const e of snapSegs()){
        const lox = Math.min(e.ax, e.bx), hix = Math.max(e.ax, e.bx), loy = Math.min(e.ay, e.by), hiy = Math.max(e.ay, e.by);
        if(Math.abs(e.ax - e.bx) < 1e-6){ if(y > loy - tol && y < hiy + tol && Math.abs(x - e.ax) < dx){ dx = Math.abs(x - e.ax); bx = e.ax; sx = e; } }
        else if(Math.abs(e.ay - e.by) < 1e-6){ if(x > lox - tol && x < hix + tol && Math.abs(y - e.ay) < dy){ dy = Math.abs(y - e.ay); by = e.ay; sy = e; } }
        else {                                                         // a slanted edge: the foot of the perpendicular
          const vx = e.bx - e.ax, vy = e.by - e.ay, t = Math.max(0, Math.min(1, ((x - e.ax) * vx + (y - e.ay) * vy) / (vx * vx + vy * vy))), fx = e.ax + t * vx, fy = e.ay + t * vy, d = Math.hypot(x - fx, y - fy);
          if(d < ds){ ds = d; sl = {e, fx, fy}; }
        }
      }
      if(bx !== null) x = bx;
      if(by !== null) y = by;
      if(bx === null && by === null && sl){ x = sl.fx; y = sl.fy; }
      S.snapHi = [sx, sy, sl && bx === null && by === null ? sl.e : null].filter(Boolean);
      if(prev){
        const px = prev.x * p.w, py = prev.y * p.h, ang = Math.abs(Math.atan2(y - py, x - px)) * 180 / Math.PI;
        if((ang < 12 || ang > 168) && by === null && !sl) y = py;      // along the previous point horizontally ...
        else if(Math.abs(ang - 90) < 12 && bx === null && !sl) x = px; // ... or vertically
      }
      return {x: Math.min(1, Math.max(0, x / p.w)), y: Math.min(1, Math.max(0, y / p.h))};
    }
    view.addEventListener('pointermove', e => {                        // the zone under a mouse pointer shows its outline
      if(S.mode || ptrs.size || !data.rooms.length) return;
      const z = zoneAt(planXY(e));
      if(z !== S.hoverZone){ S.hoverZone = z; cancelAnimationFrame(S.zraf); S.zraf = requestAnimationFrame(draw); }
    });
    view.addEventListener('pointermove', e => {                        // the live segment from the last point to the pointer (not while the bar is under it)
      if(S.mode !== 'path' || !S.pathItem || ptrs.has(e.pointerId) || (e.target.closest && e.target.closest('[data-m="hint"]'))) return;
      const pts = S.pathItem.points;
      S.hover = snapPoint(planXY(e), pts[pts.length - 1]);
      cancelAnimationFrame(S.hraf); S.hraf = requestAnimationFrame(draw);
    });

    // ---- taps ----
    function tap(e){
      if(S.mode === 'edit') return;
      const hit = document.elementFromPoint(e.clientX, e.clientY);                 // the pointer is captured by the view: ask what is under it
      const cl = hit && hit.closest ? hit.closest('[data-cluster]') : null;
      if(cl && !S.mode){
        const ids = cl.dataset.cluster.split(','), was = S.scale;
        zoomTo(ids);
        const still = [...q('icons').querySelectorAll('[data-cluster]')].find(c => ids.every(id => c.dataset.cluster.split(',').includes(id)));
        if(still || S.scale === was){ S.spider = {ids, scale: S.scale}; place(); }      // zooming did not part them: fan them out
        return;
      }
      const icon = hit && hit.closest ? hit.closest('[data-id]') : null;
      if(S.spider && !(icon && icon.dataset.fan)){ S.spider = null; place(); if(!icon) return; }     // a tap beside the fan closes it
      const fx = !S.mode && icon && data.items.find(i => i.id === icon.dataset.id && i.kind === 'device');
      const fdev = fx && ((Home.devices && Home.devices()) || []).find(d => d.id === fx.dev && d.ir_power);
      if(fdev && Home.openDevice){ Home.openDevice(fdev.id); return; }          // an IR fixture opens its own window: its zones and what was sent
      const at = planXY(e);
      if(S.mode === 'wall'){ wallTap(e, at); return; }
      if(S.mode === 'linkpick'){                                                  // the lamp a switch turns on
        const tgt = icon && data.items.find(i => i.id === icon.dataset.id);
        if(tgt && tgt.kind === 'load' && !data.links.some(l => l.load === tgt.id && l.dev === S.linkFrom.dev && l.code === S.linkFrom.code)) data.links.push({load: tgt.id, dev: S.linkFrom.dev, code: S.linkFrom.code, note: '', seg: []});
        S.mode = ''; S.linkFrom = null; changed(); return;
      }
      if(S.mode === 'path'){ const pts = S.pathItem.points; pts.push(snapPoint(at, pts[pts.length - 1])); S.hover = null; draw(); return; }
      if(S.mode === 'seg'){                                                         // the nearest point of the strip's path
        const it = data.items.find(i => i.id === S.seg.load), p = plan();
        if(it && it.path && p){
          let best = 0, bd = 1e9;
          it.path.forEach((a, i) => { const d = Math.hypot((a.x - at.x) * p.w, (a.y - at.y) * p.h); if(d < bd){ bd = d; best = i; } });
          if(!S.seg.pts.includes(best)) S.seg.pts.push(best);
          if(S.seg.pts.length === 2){
            const [a, b] = S.seg.pts.sort((x, y) => x - y);
            data.links.push({load: S.seg.load, dev: S.seg.dev, code: S.seg.code, note: '', seg: [a, b]}); S.mode = ''; S.seg = null; changed(); return;
          }
        }
        draw(); return;
      }
      if(S.mode === 'place'){ data.items.push({id: uid(), kind: 'device', dev: S.pending.id, name: '', type: '', x: at.x, y: at.y, room: ''}); S.mode = ''; S.pending = null; changed(); return; }
      if(S.mode === 'move'){ const it = data.items.find(i => i.id === S.pending.id); if(it){ it.x = at.x; it.y = at.y; } S.mode = ''; S.pending = null; changed(); return; }
      if(icon && !EDIT()){                                                  // a watcher: a thing opens its device (a load: the one that switches it)
        const it = data.items.find(i => i.id === icon.dataset.id), ln = it && data.links.find(l => l.load === it.id);
        const dev = it && (it.kind === 'device' ? it.dev : ln && ln.dev);
        if(dev && !dev.startsWith('blaster:') && Home.openDevice) Home.openDevice(dev);
        return;
      }
      if(icon){ itemSheet(data.items.find(i => i.id === icon.dataset.id)); return; }
      if(!plan() || !EDIT()) return;
      const lz = labelHit(e); if(lz){ zoneSheet(lz); return; }
      const wh = hitWall(e); if(wh && wh.op){ openingSheet(wh.op); return; }      // a door or a window: its own card, in any mode
      spotSheet(at);
    }
    const tapMark = Object.assign(document.createElement('div'), {className: 'map-tapmark'});      // the point a popover belongs to
    tapMark.style.cssText = 'display:none;position:absolute;width:18px;height:18px;margin:-9px 0 0 -9px;border:2px solid #ffb020;border-radius:50%;pointer-events:none;z-index:49';
    box.append(tapMark);
    const lastTap = {x: 0, y: 0, at: 0};
    view.addEventListener('pointerdown', e => { lastTap.x = e.clientX; lastTap.y = e.clientY; lastTap.at = Date.now(); }, true);
    const closeSheet = () => { sheet.style.display = 'none'; sheet.innerHTML = ''; tapMark.style.display = 'none'; if(S.opSel){ S.opSel = null; draw(); } };
    document.addEventListener('keydown', e => {                    // Esc closes the open card (a field in it first loses its focus)
      if(e.key !== 'Escape' || sheet.style.display === 'none' || !sheet.isConnected) return;
      if(sheet.contains(document.activeElement) && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)){ document.activeElement.blur(); return; }
      closeSheet();
    });
    const finishPath = () => {                                                   // a strip drawn (or re-drawn) with its points: saved, its handle is the middle
      const P = S.pathItem;
      if(!P || P.points.length < 2) return;
      const mid = P.points[Math.floor(P.points.length / 2)], old = data.items.find(i => i.id === P.id);
      if(old){
        Object.assign(old, {path: P.points, smooth: P.smooth, x: mid.x, y: mid.y});
        data.links = data.links.filter(l => !(l.load === old.id && l.seg && l.seg.length && l.seg[1] >= P.points.length));
      } else data.items.push({id: P.id, kind: 'load', dev: '', name: P.name, type: 'led', x: mid.x, y: mid.y, room: '', path: P.points, smooth: P.smooth});
      S.mode = ''; S.pathItem = null; changed();
    };
    const phone = () => !matchMedia('(min-width: 600px)').matches;
    const openSheet = html => {                                     // beside the last tap (a toolbar button: the map's top right corner)
      sheet.onclick = null; sheet.innerHTML = html;
      const base = 'display:block;overflow:auto;background:var(--card);border:1px solid var(--line);z-index:50;box-shadow:0 4px 16px rgba(0,0,0,.35);';
      const br = box.getBoundingClientRect(), vr = view.getBoundingClientRect(), near = Date.now() - lastTap.at < 3000;
      if(near){ tapMark.style.left = (lastTap.x - br.left) + 'px'; tapMark.style.top = (lastTap.y - br.top) + 'px'; tapMark.style.display = 'block'; }
      else tapMark.style.display = 'none';
      if(phone()){ sheet.style.cssText = base + 'position:fixed;left:0;right:0;bottom:0;max-height:45vh;border-radius:12px 12px 0 0;padding:12px 16px'; return sheet; }
      const W = Math.min(360, vr.width - 16), H = Math.min(480, vr.height - 16), tx = near ? lastTap.x : vr.right - 8, ty = near ? lastTap.y : vr.top + 8;
      let left = tx + 16;
      if(left + W > vr.right - 8) left = tx - 16 - W;
      left = Math.max(vr.left + 8, Math.min(left, vr.right - 8 - W));
      const top = Math.max(vr.top + 8, Math.min(ty - 24, vr.bottom - 8 - H));
      sheet.style.cssText = base + `position:absolute;left:${left - br.left}px;top:${top - br.top}px;width:${W}px;max-height:${H}px;border-radius:10px;padding:12px 14px`;
      return sheet;
    };
    function reveal(x, y){                                          // pan so that the point is not under the sheet
      const p = plan(); if(!p || sheet.style.display === 'none') return;
      const vr = view.getBoundingClientRect(), sr = sheet.getBoundingClientRect(), m = 48;
      const [sx, sy] = planToScreen(x, y);
      if(!phone()) return;                                          // a popover sits beside the point: nothing to move
      if(sy > sr.top - m) S.oy -= sy - (sr.top - m);
      S.userZoomed = true; place();
    }
    const chanName = (dev, code) => {                              // a channel as people name it, never its code
    const c = (((cands.find(x => x.id === dev) || {}).controls) || []).find(x => x.code === code) || {};
    return i18n(c.label && !/^(state|switch)(_\w+)?$/i.test(c.label) ? c.label : (/(_|l)(\d+)$/.exec(code) || [])[2] ? i18n('Channel') + ' ' + /(\d+)$/.exec(code)[1] : 'Power');
  };
  const row = (id, text, sub) => `<div data-pick="${esc(id)}" style="padding:10px 4px;border-bottom:1px solid var(--line);cursor:pointer;font-size:16px">${esc(text)}${sub ? ` <span class="bindcaption">${esc(sub)}</span>` : ''}</div>`;

    // the list of what is not on the plan yet: by room, inside a room by kind; a pick goes to onPick(id)
    function thingList(head, foot, onPick, top = ''){                   // top: buttons right under the search, above the list (always in sight)
      const list = () => {
        const f = (sheet.querySelector('[data-s="find"]') || {value: ''}).value.trim().toLowerCase();
        return unplaced().filter(c => !f || [dispName(c), roomOfC(c), c.detail, kindName(c.category)].join(' ').toLowerCase().includes(f));
      };
      const thingRow = c => `<div data-pick="${esc(c.id)}" style="padding:8px 4px;border-bottom:1px solid var(--line);cursor:pointer;font-size:16px;display:flex;gap:10px;align-items:center">
        <span style="width:24px;display:inline-block;color:#cfd8e3">${ico(c.category, 22)}</span><span>${esc(dispName(c))}<br><span class="bindcaption">${esc([kind1(c.category), c.online === false ? i18n('offline') : ''].filter(Boolean).join(' \u00b7 '))}</span></span></div>`;
      const paint = () => {
        const byRoom = new Map();
        for(const c of list()){ const r = roomOfC(c); if(!byRoom.has(r)) byRoom.set(r, []); byRoom.get(r).push(c); }
        const order = [...new Set(data.rooms.map(z => z.name))];
        const keys = [...byRoom.keys()].sort((a, b) => (order.indexOf(a) < 0 ? 99 : order.indexOf(a)) - (order.indexOf(b) < 0 ? 99 : order.indexOf(b)) || (a === '') - (b === '') || a.localeCompare(b));
        let html = '';
        for(const r of keys){
          html += `<div style="margin-top:10px;font-weight:700;font-size:15px">${esc(r || i18n('No room'))}</div>`;
          let last = '';
          for(const c of byRoom.get(r)){
            if(c.category !== last){ last = c.category; html += `<div class="bindcaption" style="margin-top:4px">${esc(kindName(c.category))}</div>`; }
            html += thingRow(c);
          }
        }
        sheet.querySelector('[data-s="list"]').innerHTML = html || `<div class="bindcaption">${esc(i18n('Nothing left'))}</div>`;
      };
      openSheet(`<div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">${head}</h3><button class="dlg-btn" data-s="close">&times;</button></div>
        <div style="position:sticky;top:-12px;z-index:1;background:var(--card);padding:8px 0 4px;margin-top:0"><input class="askin" data-s="find" placeholder="${esc(i18n('Search by name or room'))}" style="width:100%;box-sizing:border-box">
          ${top ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">${top}</div>` : ''}</div><div data-s="list"></div>${foot}`);      // the search and the top buttons stay in sight while the list scrolls
      paint();
      sheet.querySelector('[data-s="close"]').onclick = closeSheet;
      sheet.querySelector('[data-s="find"]').oninput = paint;
      sheet.querySelector('[data-s="list"]').onclick = ev => { const r = ev.target.closest('[data-pick]'); if(r) onPick(r.dataset.pick); };
    }
    function spotSheet(at){
      setTimeout(() => reveal(at.x, at.y), 0);
      thingList(esc(i18n('What is here?')), '',
        id => { data.items.push({id: uid(), kind: 'device', dev: id, name: '', type: '', x: at.x, y: at.y, room: ''}); closeSheet(); changed(); },
        `<button class="dlg-btn primary" data-s="new">${esc(i18n('New load'))}</button>${zoneAt(at) ? `<button class="dlg-btn" data-s="zone">${esc(i18n('Zone'))}</button>` : ''}`);
      const zb = sheet.querySelector('[data-s="zone"]'); if(zb) zb.onclick = () => zoneSheet(zoneAt(at));
      sheet.querySelector('[data-s="new"]').onclick = () => newLoadSheet(at);
    }

    function newLoadSheet(at, pre){
      openSheet(`<h3 style="margin:0 0 8px">${esc(i18n('New load'))}</h3>
        <div class="row"><span class="rlabel">${esc(i18n('Type'))}</span><select class="askin" data-s="type">${data.load_types.map(t => `<option value="${t}">${esc(loadName(t))}</option>`).join('')}</select></div>
        <div class="row"><span class="rlabel">${esc(i18n('Name'))}</span><input class="askin" data-s="name" placeholder="${esc(i18n('lamp 1'))}"></div>
        <div style="display:flex;gap:8px;margin-top:10px"><button class="dlg-btn primary" data-s="ok">${esc(i18n('Add'))}</button><button class="dlg-btn" data-s="close">${esc(i18n('Cancel'))}</button></div>`);
      if(pre) sheet.querySelector('[data-s="type"]').value = pre;
      sheet.querySelector('[data-s="close"]').onclick = closeSheet;
      sheet.querySelector('[data-s="ok"]').onclick = () => {
        const name = sheet.querySelector('[data-s="name"]').value.trim(); if(!name) return;
        const type = sheet.querySelector('[data-s="type"]').value;
        if(type === 'led'){ closeSheet(); S.mode = 'path'; S.pathItem = {id: uid(), name, points: [], smooth: false, isNew: true}; draw(); return; }
        data.items.push({id: uid(), kind: 'load', dev: '', name, type, x: at.x, y: at.y, room: ''}); closeSheet(); changed();
      };
    }

    // what a switch turns on: pick a lamp on the plan, or make a new lamp here; a channel first when the device has several
    function drivesSheet(it){
      const st = Home.state(it.dev) || {}, codes = Object.keys(st.values || {}).filter(k => /^state/.test(k)).sort();
      const label = k => { const m = /^state_(left|center|right|l(\d+))$/.exec(k); return !m ? k : m[2] ? i18n('Channel') + ' ' + m[2] : i18n({left: 'Left key', center: 'Middle key', right: 'Right key'}[m[1]]); };
      openSheet(`<div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">${esc(i18n('What it turns on'))}</h3><button class="dlg-btn" data-s="close">&times;</button></div>
        ${codes.length > 1 ? `<div class="row"><span class="rlabel">${esc(i18n('Which key'))}</span><select class="askin" data-s="chan">${codes.map(k => `<option value="${esc(k)}">${esc(label(k))}</option>`).join('')}</select></div>` : ''}
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0"><button class="dlg-btn primary" data-s="pick">${esc(i18n('Pick a lamp on the plan'))}</button></div>
        <div class="row"><span class="rlabel">${esc(i18n('Name'))}</span><input class="askin" data-s="lname" placeholder="${esc(i18n('lamp 1'))}"></div>
        <div style="margin-top:8px"><button class="dlg-btn" data-s="newlamp">${esc(i18n('New lamp here'))}</button></div>`);
      const code = () => (sheet.querySelector('[data-s="chan"]') || {value: codes[0] || ''}).value;
      sheet.querySelector('[data-s="close"]').onclick = closeSheet;
      sheet.querySelector('[data-s="pick"]').onclick = () => { S.mode = 'linkpick'; S.linkFrom = {dev: it.dev, code: code()}; closeSheet(); draw(); };
      sheet.querySelector('[data-s="newlamp"]').onclick = () => {
        const name = sheet.querySelector('[data-s="lname"]').value.trim(); if(!name) return;
        const id = uid();
        data.items.push({id, kind: 'load', dev: '', name, type: 'lamp', x: Math.min(1, it.x + 0.025), y: Math.min(1, it.y + 0.025), room: ''});
        data.links.push({load: id, dev: it.dev, code: code(), note: '', seg: []});
        closeSheet(); changed();
      };
    }

    function itemSheet(it){
      if(!it) return;
      const cand = cands.find(c => c.id === it.dev) || {}, V = thingView(it), links = data.links.filter(l => l.load === it.id);
      const room = (zoneAt(it) || {}).name || (it.kind === 'device' ? roomOfC(cand) : ''), kind = it.kind === 'load' ? loadName(it.type) : kind1(cand.category || 'other');
      const chans = it.kind === 'load' ? links.map(l => ({dev: l.dev, code: l.code})) : [{dev: it.dev, code: ''}];             // what can be switched: the channels that say on/off
      const switches = [];
      for(const c of chans){
        const d = ((Home.devices && Home.devices()) || []).find(x => x.id === c.dev), st = Home.state(c.dev) || {};
        for(const k of ((d && d.controls) || [])) if((c.code ? k.code === c.code : /^state/.test(k.code)) && (k.kind === 'switch' || /^state/.test(k.code)) && typeof (st.values || {})[k.code] === 'boolean')
          switches.push({dev: c.dev, code: k.code, on: st.values[k.code], label: k.label || k.code});
      }
      const chanLabel = w => { const m = /^state_(left|center|right|l(\d+))$/.exec(w.code); return !m ? w.label : m[2] ? i18n('Channel') + ' ' + m[2] : i18n({left: 'Left key', center: 'Middle key', right: 'Right key'}[m[1]]); };
      const stateTxt = V.bad ? V.tip.split(' - ')[1] || i18n('no contact') : switches.length ? '' : [V.badge, V.glow ? i18n('on') : ''].filter(Boolean).join(' \u00b7 ');
      openSheet(`<div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><div style="display:flex;gap:10px;align-items:center"><span style="color:#cfd8e3">${iconOf(it)}</span>
          <div><div style="font-size:17px;font-weight:700">${esc(nameOf(it))}</div><div class="bindcaption">${esc([kind, room, V.bad ? i18n('offline') : ''].filter(Boolean).join(' \u00b7 '))}</div></div></div><button class="dlg-btn" data-s="close">&times;</button></div>
        ${stateTxt ? `<div style="margin:10px 0;font-size:15px">${esc(stateTxt)}</div>` : ''}
        ${switches.length ? `<div style="margin:10px 0">${switches.map((w, i) => `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:4px 0"><span>${esc(switches.length > 1 ? chanLabel(w) : nameOf(it))}</span>
          <div class="toggle${w.on ? ' on' : ''}" data-ctl="${i}"></div></div>`).join('')}</div>` : ''}
        ${it.kind === 'device' && data.links.some(l => l.dev === it.dev) ? `<div class="bindcaption" style="margin:6px 0">${esc(i18n('Turns on'))}: ${data.links.filter(l => l.dev === it.dev).map(l => esc((data.items.find(i => i.id === l.load) || {}).name || '')).filter(Boolean).join(', ')}</div>` : ''}
        ${it.kind === 'device' ? '<div data-s="does"></div>' : ''}
        ${links.map((l, i) => `<div class="bindcaption" style="margin:6px 0">${esc(i18n('Controlled by'))}: ${esc(dispName(cands.find(c => c.id === l.dev) || {id: l.dev, name: l.dev}))}${l.code ? ' / ' + esc(chanName(l.dev, l.code)) : ''}${l.seg && l.seg.length ? ' (' + (l.seg[0] + 1) + '-' + (l.seg[1] + 1) + ')' : ''}
          <button class="dlg-btn" data-unlink="${i}" style="padding:2px 8px">&times;</button></div>`).join('')}
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">${it.kind === 'load' ? `<button class="dlg-btn primary" data-s="link">${esc(i18n('Link'))}</button>` : (['switch', 'dimmer', 'socket'].includes(cand.category) ? `<button class="dlg-btn primary" data-s="drives">${esc(i18n('What it turns on'))}</button>` : '')}
          ${it.path ? `<button class="dlg-btn" data-s="edit">${esc(i18n('Edit the path'))}</button><button class="dlg-btn" data-s="drop" ${it.path.length > 2 ? '' : 'disabled'}>${esc(i18n('Remove the last point'))}</button>
            <span style="display:inline-flex"><button class="dlg-btn${it.smooth === true ? '' : ' primary'}" data-s="straight" style="border-radius:8px 0 0 8px">${esc(i18n('Straight'))}</button><button class="dlg-btn${it.smooth === true ? ' primary' : ''}" data-s="smooth" style="border-radius:0 8px 8px 0">${esc(i18n('Smooth'))}</button></span>` : `<button class="dlg-btn" data-s="move">${esc(i18n('Move'))}</button>`}</div>
        <div style="margin-top:14px;padding-top:10px;border-top:1px solid var(--line)"><button class="dlg-btn" data-s="remove" style="color:#e5484d;border-color:#e5484d">${esc(i18n('Remove'))}</button></div>`);
      reveal(it.x, it.y);
      if(it.kind === 'device' && Home.triggersOf) Home.triggersOf(it.dev).then(list => {    // a remote or a switch: what each press does, runnable
        const box = sheet.querySelector('[data-s="does"]');
        if(!box || !list.length) return;
        box.innerHTML = `<div class="bindcaption" style="margin:8px 0 2px">${esc(i18n('What it does'))}</div>` + list.map((r, i) =>
          `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:3px 0${r.enabled ? '' : ';opacity:.5'}"><span>${esc(r.text)}</span>
            <button class="dlg-btn" data-run="${i}" title="${esc(i18n('Do it now'))}" style="padding:2px 10px">${Home.icon('play', 14)}</button></div>`).join('');
        box.querySelectorAll('[data-run]').forEach(b => { b.onclick = async () => {
          b.disabled = true;
          try { await Home.runRule(list[+b.dataset.run].id); b.innerHTML = Home.icon('check', 14); }
          catch(e){ b.textContent = '!'; b.title = e.message || String(e); }
          setTimeout(() => { b.innerHTML = Home.icon('play', 14); b.disabled = false; }, 1200);
        }; });
      }).catch(() => {});
      sheet.querySelectorAll('[data-ctl]').forEach(b => { b.onclick = () => { const w = switches[+b.dataset.ctl]; b.classList.toggle('on', !w.on); Home.sendControl(w.dev, w.code, !w.on); setTimeout(() => { if(sheet.style.display !== 'none' && sheet.querySelector('[data-ctl]')) itemSheet(it); }, 900); }; });
      sheet.querySelector('[data-s="close"]').onclick = closeSheet;
      sheet.querySelectorAll('[data-unlink]').forEach(b => { b.onclick = () => { data.links.splice(+b.dataset.unlink, 1); closeSheet(); changed(); }; });
      const mv = sheet.querySelector('[data-s="move"]'); if(mv) mv.onclick = () => { S.mode = 'move'; S.pending = it; closeSheet(); draw(); };
      const ed = sheet.querySelector('[data-s="edit"]'); if(ed) ed.onclick = () => { closeSheet(); S.mode = 'path'; S.pathItem = {id: it.id, name: it.name, points: [], smooth: it.smooth !== false, isNew: false}; draw(); };
      const dr = sheet.querySelector('[data-s="drop"]');
      if(dr) dr.onclick = () => { it.path.pop(); data.links = data.links.filter(l => !(l.load === it.id && l.seg && l.seg[1] >= it.path.length)); const m = it.path[Math.floor(it.path.length / 2)]; it.x = m.x; it.y = m.y; closeSheet(); changed(); };
      for(const [k, v] of [['straight', false], ['smooth', true]]){ const b = sheet.querySelector(`[data-s="${k}"]`); if(b) b.onclick = () => { it.smooth = v; closeSheet(); changed(); }; }
      sheet.querySelector('[data-s="remove"]').onclick = () => {
        data.items = data.items.filter(i => i.id !== it.id); data.links = data.links.filter(l => l.load !== it.id); closeSheet(); changed();
      };
      const lk = sheet.querySelector('[data-s="link"]'); if(lk) lk.onclick = () => linkSheet(it);
      const dr2 = sheet.querySelector('[data-s="drives"]'); if(dr2) dr2.onclick = () => drivesSheet(it);
    }

    async function linkSheet(it){
      let devs = [];
      try { devs = await Home.api('/api/devices'); } catch(e){ /* the list stays empty */ }
      devs = devs.filter(d => d.category !== 'voice' && (d.controls || []).some(c => ['switch', 'bright', 'press', 'ir_button'].includes(c.kind) || c.kind));
      const paint = () => {
        const f = sheet.querySelector('[data-s="find"]').value.trim().toLowerCase();
        sheet.querySelector('[data-s="list"]').innerHTML = devs.filter(d => !f || ((d.name || '') + ' ' + d.id).toLowerCase().includes(f)).slice(0, 60)
          .map(d => row(d.id, d.name || d.id, d.category)).join('');
      };
      openSheet(`<div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">${esc(i18n('Controlled by'))}: ${esc(nameOf(it))}</h3><button class="dlg-btn" data-s="close">&times;</button></div>
        <div style="margin:8px 0"><input class="askin" data-s="find" placeholder="${esc(i18n('Search'))}" style="width:100%;box-sizing:border-box"></div><div data-s="list"></div>`);
      paint();
      sheet.querySelector('[data-s="close"]').onclick = closeSheet;
      sheet.querySelector('[data-s="find"]').oninput = paint;
      sheet.querySelector('[data-s="list"]').onclick = ev => {
        const r = ev.target.closest('[data-pick]'); if(!r) return;
        const d = devs.find(x => x.id === r.dataset.pick); if(!d) return;
        const ch = (d.controls || []).filter(c => c.kind !== 'sensor' && !c.ro);
        sheet.querySelector('[data-s="list"]').innerHTML = `<div class="bindcaption">${esc(d.name || d.id)}</div>` + ch.map(c => row(c.code, c.label || c.code, c.code)).join('') +
          `<div data-pick="" style="padding:10px 4px;cursor:pointer" class="bindcaption">${esc(i18n('The whole device'))}</div>`;
        sheet.querySelector('[data-s="list"]').onclick = e2 => {
          const c = e2.target.closest('[data-pick]'); if(!c) return;
          const code = c.dataset.pick;
          if(it.path && it.path.length > 2){                                         // a strip: all of it, or a part between two of its points
            openSheet(`<h3 style="margin:0 0 8px">${esc(nameOf(it))}</h3><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="dlg-btn primary" data-s="all">${esc(i18n('Whole strip'))}</button>
              <button class="dlg-btn" data-s="part">${esc(i18n('Part of the strip'))}</button></div>`);
            sheet.querySelector('[data-s="all"]').onclick = () => { data.links.push({load: it.id, dev: d.id, code, note: '', seg: []}); closeSheet(); changed(); };
            sheet.querySelector('[data-s="part"]').onclick = () => { S.mode = 'seg'; S.seg = {load: it.id, dev: d.id, code, pts: []}; closeSheet(); draw(); };
            return;
          }
          data.links.push({load: it.id, dev: d.id, code, note: '', seg: []}); closeSheet(); changed();
        };
      };
    }

    // ---- toolbar ----
    q('fit').onclick = fit;
    q('left').onclick = () => thingList(esc(i18n('Left to place')) + ': ' + unplaced().length, '', id => { S.mode = 'place'; S.pending = {id}; closeSheet(); draw(); });       // a pick, then a tap on the plan
    q('live').onchange = () => { S.live = q('live').checked; localStorage.setItem('map.live', S.live ? '1' : '0'); draw(); };
    q('legend').onclick = () => {
      const l = box.querySelector('div[data-m="legend"]');
      l.innerHTML = [['\u25cf \u25cb', 'a key on / off'], ['', 'a lamp that is on glows, brighter when brighter'], ['27\u00b0 45%', 'temperature and humidity'], ['', 'motion: pulses while active'],
                     ['open', 'a door or window open: red'], ['!', 'no contact (grey): since when is in the tip'], ['', 'AC: blue cool, orange heat, set temperature'], ['', 'dimmed: not heard for long'],
                     ['\u2014', 'Wires: from a switch, socket or controller to what it feeds'], ['- -', 'Dashed: IR from a blaster']]
        .map(r => `<div><b>${esc(r[0])}</b> ${esc(i18n(r[1]))}</div>`).join('') + '<hr style="border:0;border-top:1px solid var(--line)">' +
        Object.keys(KIND_NAMES).map(k => `<div style="display:flex;gap:8px;align-items:center">${ico(k, 18)} ${esc(kindName(k))}</div>`).join('');
      l.style.display = l.style.display === 'none' ? 'block' : 'none';
    };
    for(const k of ['wires', 'radio']) q(k).onchange = () => { S[k] = q(k).checked; localStorage.setItem('map.' + k, S[k] ? '1' : '0'); draw(); };
    q('underlay').onclick = () => underlaySheet();
    q('walltool').onclick = () => { S.mode = S.mode === 'wall' ? '' : 'wall'; S.wallStart = null; S.wallHist = []; S.wallRedo = []; closeSheet(); draw(); };
    q('newroom').onclick = () => newRoomSheet({x: 0.5, y: 0.5});
    q('optool').onclick = () => { S.mode = S.mode === 'opening' ? '' : 'opening'; S.opDraw = null; S.opMsg = ''; S.wallHist = []; S.wallRedo = []; closeSheet(); draw(); };
    window.addEventListener('restricted-role', () => { if(box.isConnected) draw(); });          // the session may edit now, or no more
    window.addEventListener('resize', () => { if(box.isConnected){ layoutBar(); const turned = setRot(); fit(); if(turned) draw(); } });
    new ResizeObserver(() => requestAnimationFrame(() => { if(box.isConnected && !S.userZoomed) fit(); })).observe(box);   // next frame: a fit inside the callback loops

    if(opts.data){ S.auto = opts.auto || []; S.wiring = opts.wiring || []; }
    else {
      try { S.auto = await Home.api('/api/map/auto'); } catch(e){ S.auto = []; }
      try { const w = await Home.api('/api/wiring'); S.wiring = Array.isArray(w && w.entries) ? w.entries : []; } catch(e){ S.wiring = []; }
    }
    if(!await loadImage()) return;
    await loadWalls();
    draw();
    if(!SHOW_ONLY) redraw = () => { if(box.isConnected) paintState(); };    // a state frame: only what changed (a scene paints its own copy)
    if(!SHOW_ONLY) window.MapView = {zoomTo: (ids, most) => { if(box.isConnected) zoomTo(ids, most); }, fit: () => { if(box.isConnected) fit(); }};   // the live plan, for a guide that points at it
    return {                                                         // what a scene drives
      data: () => data,
      set(part){ Object.assign(data, JSON.parse(JSON.stringify(part))); noEmptyPaths(); data.walls = data.walls || []; data.openings = data.openings || []; place(); draw(); },
      draw: () => draw(), fit: () => fit(), zoomTo: (ids, most) => zoomTo(ids, most), focusDevice: id => focusDevice(id),
      layers(wires, radio){ S.wires = !!wires; S.radio = !!radio; draw(); },
      lines(auto, wiring){ S.auto = auto || []; S.wiring = wiring || []; draw(); },     // the radio lines and the wiring's IR feeds
      underlay(show){ if(data.underlay){ data.underlay = {...data.underlay, show: !!show}; return paintUnderlay(); } },
      el: id => q('icons').querySelector(`[data-id="${CSS.escape(id)}"]`), bar: name => box.querySelector(`[data-m="${name}"]`),
    };
  }
  window.MapScene = {render: (box, opts) => render(box, {...opts, readonly: true})};

  HomePlugins.register({
    onReload: [probe, loadParts],
    hides: [d => partOf.has(d.id)],                    // a part is shown inside its primary
    modalSections: [{match: d => (parts[d.id] || []).length > 0, html: d => `<section style="margin:12px 0"><div class="bindcaption">${esc(i18n('Remote control (IR)'))}</div><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
        ${parts[d.id].map(id => `<button class="dlg-btn" data-part="${esc(id)}">${esc(((Home.devices().find(x => x.id === id)) || {name: id}).name)}</button>`).join('')}</div></section>`,
      wire: box => box.querySelectorAll('[data-part]').forEach(b => { b.onclick = () => Home.openDevice(b.dataset.part); })}],
    onState: [() => { if(redraw) redraw(); }],
    settings: [{subject: 'rooms', title: 'Home map', render}],    // Settings -> Home map: the editor, the page itself
    tabs: [{id: 'map', label: 'Home map', after: 'fav', row: 'places', render, visible: () => owner}],
    // the help sheet and the header's dragged "?" (README "Help")
    help: [
      {id: 'map.fit', screen: 'tab:p:map', sel: '[data-m="fit"]', title: 'Fit the plan', text: 'Fits the whole plan into the window.'},
      {id: 'map.left', screen: 'tab:p:map', sel: '[data-m="left"]', title: 'Left to place', text: 'The things not on the plan yet: pick one, then tap its spot.'},
      {id: 'map.more', screen: 'tab:p:map', sel: '[data-m="more"]', title: 'More', text: 'On a narrow screen: the rest of the bar\'s buttons.'},
      {id: 'map.newroom', screen: 'tab:p:map', sel: '[data-m="newroom"]', title: 'New room',
       text: 'Makes a room with a small outline in the middle of the plan; drag its corners to the walls. A long press on the plan does the same at that spot.'},
      {id: 'map.live', screen: 'tab:p:map', sel: '[data-m="live"]', title: 'State', text: 'Shows on the icons what is on, the readings and what is offline; kept in this browser.'},
      {id: 'map.legend', screen: 'tab:p:map', sel: '[data-m="legend"]', title: 'Legend', text: 'What each icon means.'},
      {id: 'map.wires', screen: 'tab:p:map', sel: '[data-m="wires"]', title: 'Wires', text: 'Wires: from a switch, socket or controller to what it feeds. Kept in this browser.'},
      {id: 'map.radio', screen: 'tab:p:map', sel: '[data-m="radio"]', title: 'IR', text: 'Dashed: IR from a blaster to what it drives. Kept in this browser.'},
      {id: 'map.underlay', screen: 'tab:p:map', sel: '[data-m="underlay"]', title: 'Plan of the flat', text: 'A photo or a drawing of the flat under the rooms, to draw them along it.'},
      {id: 'map.walltool', screen: 'tab:p:map', sel: '[data-m="walltool"]', title: 'Walls', text: 'Walls inside rooms: tap, tap - a new wall; a tap on a wall removes it.'},
      {id: 'map.optool', screen: 'tab:p:map', sel: '[data-m="optool"]', title: 'Openings', text: 'Doors and windows: draw one along a wall; a tap on one changes it.'},
      {id: 'plan.file', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-u="file"]', title: 'Plan file', text: 'PNG, JPG or SVG up to 8 MB.'},
      {id: 'plan.show', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-u="show"]', title: 'Show the plan', text: 'The plan under the rooms on or off.'},
      {id: 'plan.opacity', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-u="opacity"]', title: 'Opacity, %', text: 'How much of the plan shows through.'},
      {id: 'plan.size', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-u="size"]', title: 'Size, % of the map', text: 'The plan\'s width against the map\'s.'},
      {id: 'plan.rot', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-u="rot"]', title: 'Turn, degrees', text: 'Turns the plan to lie along the rooms.'},
      {id: 'plan.move', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-u="move"]', title: 'Move it', text: 'Drag the plan to its place, then Done.'},
      {id: 'plan.del', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-u="del"]', title: 'Remove the plan', text: 'Takes the plan away; the rooms stay.'},
      {id: 'plan.tidy', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-u="tidy"]', title: 'Join neighbour walls', text: 'Rooms drawn each on its side of a wall get one shared edge: one wall line instead of two.'},
      {id: 'plan.done', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="uldone"]', title: 'Done', text: 'The plan stays where you put it.'},
      {id: 'walls.undo', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="wundo"]', title: 'Undo', text: 'Takes back the last wall or opening change.'},
      {id: 'walls.redo', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="wredo"]', title: 'Redo', text: 'Puts back what Undo took.'},
      {id: 'walls.done', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="wdone"]', title: 'Done', text: 'Ends the Walls or Openings tool.'},
      {id: 'outline.undo', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="eundo"]', title: 'Undo', text: 'Takes back the last change of the outline.'},
      {id: 'outline.redo', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="eredo"]', title: 'Redo', text: 'Puts back what Undo took.'},
      {id: 'outline.done', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="edone"]', title: 'Done', text: 'Keeps the new outline of the room.'},
      {id: 'outline.cancel', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="ecancel"]', title: 'Cancel', text: 'The room keeps its old outline.'},
      {id: 'path.done', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="done"]', title: 'Done', text: 'Ends the strip\'s path.'},
      {id: 'path.undo', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="undo"]', title: 'Undo the last point', text: 'Takes back the last point of the path.'},
      {id: 'path.straight', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="straight"]', title: 'Straight', text: 'The strip runs in straight lines between the points.'},
      {id: 'path.smooth', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="smooth"]', title: 'Smooth', text: 'The strip runs in a smooth curve through the points.'},
      {id: 'path.cancel', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-b="cancel"]', title: 'Cancel', text: 'Drops the path being drawn.'},
      {id: 'sheet.close', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="close"]', title: 'Close', text: 'Closes this window.'},
      {id: 'room.name', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="zname"]', title: 'Room name', text: 'The room\'s name on the map and on the dashboard.'},
      {id: 'room.rename', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="rename"]', title: 'Rename', text: 'Saves the new name.'},
      {id: 'room.merge', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="merge"]', title: 'Merge with...', text: 'Gives this outline the name of another room: one name = one room.'},
      {id: 'room.shape', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="shape"]', title: 'Change the outline', text: 'Drag the points; a + adds one; a double click deletes one.'},
      {id: 'room.sensors', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="sensors"]', title: 'Thermometer...', text: 'Which thermometer and hygrometer the room shows, Ask answers with and an AC set to the room reads: Auto, or one by hand.'},
      {id: 'sheet.del', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="del"]', title: 'Delete', text: 'Deletes this room, or this door or window.'},
      {id: 'wall.del', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="delwall"]', title: 'Remove the wall', text: 'Takes this wall away.'},
      {id: 'opening.window', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="kind-window"]', title: 'Window', text: 'This opening is a window.'},
      {id: 'opening.door', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="kind-door"]', title: 'Door', text: 'This opening is a door.'},
      {id: 'opening.slide', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="open-slide"]', title: 'Sliding', text: 'It slides along the wall.'},
      {id: 'opening.left', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="open-left"]', title: 'To the left', text: 'It opens to the left.'},
      {id: 'opening.right', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="open-right"]', title: 'To the right', text: 'It opens to the right.'},
      {id: 'opening.side', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="side"]', title: 'The other way', text: 'It opens to the other side of the wall.'},
      {id: 'newroom.name', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="rname"]', title: 'Name', text: 'The new room\'s name.'},
      {id: 'newroom.ok', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="rok"]', title: 'Create', text: 'Makes the new room here.'},
      {id: 'list.find', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="find"]', title: 'Search by name or room', text: 'Narrows the list as you type.'},
      {id: 'spot.new', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="new"]', title: 'New load', text: 'A lamp, a socket or another load here: something a switch feeds.'},
      {id: 'spot.zone', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="zone"]', title: 'Zone', text: 'The room this spot is in: its name, outline, merge.'},
      {id: 'load.type', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="type"]', title: 'Type', text: 'What kind of load it is: its icon.'},
      {id: 'load.name', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="name"]', title: 'Name', text: 'The load\'s name on the map.'},
      {id: 'load.ok', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="ok"]', title: 'Add', text: 'Puts the new load on the map.'},
      {id: 'drives.chan', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="chan"]', title: 'Which key', text: 'The key of the switch that feeds it.'},
      {id: 'drives.pick', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="pick"]', title: 'Pick a lamp on the plan', text: 'Then tap the lamp this key turns on: a wire from the key to it.'},
      {id: 'drives.name', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="lname"]', title: 'Name', text: 'The name of a new lamp.'},
      {id: 'drives.newlamp', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="newlamp"]', title: 'New lamp here', text: 'A new lamp beside the switch, fed by this key.'},
      {id: 'item.switch', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-ctl]', title: 'On / off', text: 'Switches this channel now.'},
      {id: 'item.unlink', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-unlink]', title: 'Unlink', text: 'Removes this wire: the switch no longer feeds this load.'},
      {id: 'item.link', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="link"]', title: 'Link', text: 'Pick the switch that feeds this load: a wire to it.'},
      {id: 'item.drives', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="drives"]', title: 'What it turns on', text: 'Picks or adds the lamps this switch feeds.'},
      {id: 'strip.edit', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="edit"]', title: 'Edit the path', text: 'Tap the strip\'s points again.'},
      {id: 'strip.drop', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="drop"]', title: 'Remove the last point', text: 'The strip ends one point earlier.'},
      {id: 'strip.straight', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="straight"]', title: 'Straight', text: 'The strip runs in straight lines.'},
      {id: 'strip.smooth', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="smooth"]', title: 'Smooth', text: 'The strip runs in a smooth curve.'},
      {id: 'item.move', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="move"]', title: 'Move', text: 'Then tap its new spot.'},
      {id: 'item.remove', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="remove"]', title: 'Remove', text: 'Takes it off the map, with its wires.'},
      {id: 'link.all', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="all"]', title: 'Whole strip', text: 'The key feeds the whole strip.'},
      {id: 'link.part', screen: 'map-sheet', screenName: 'Home map: windows and tools', sel: '[data-s="part"]', title: 'Part of the strip', text: 'Then tap the first and the last point of the part it feeds.'},
      {id: 'map.part', screen: 'device', sel: '#md [data-part]', title: 'Remote control (IR)', text: 'The devices joined to this one on the map (its IR remote): a tap opens one.'},
    ],
  });
})();
