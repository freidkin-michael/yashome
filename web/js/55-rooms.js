// -- Rooms (living zones) -----------------------------------------------------
// Named device groups; one device may live in several rooms at once. CRUD --
// /api/rooms, the room's membership is replaced as a whole (PUT).
async function loadRoomLocks(){ const r = await api('/api/rooms/locked').catch(() => ({})); ROOM_LOCKED = r.devices || []; ROOM_ZONED = r.rooms || []; ROOM_HIDDEN = r.hidden || []; }
async function refreshTabs(){ try{ ROOMS = await api('/api/rooms'); await loadRoomLocks(); }catch(e){ return; } renderTabs(); }       // the room tabs only, never the open tab
// a switch's sign by its code: the power sign unless it is mute, a lock or a microphone (an on/off that is not power)
const SWITCH_GLYPH = {mute: 'mute', muted: 'mute', locked: 'lock', lock: 'lock', listen: 'mic', mic: 'mic'};
const toggleGlyph = code => SWITCH_GLYPH[String(code || '')] ? ' g-' + SWITCH_GLYPH[String(code || '')] : '';
function roomById(rid){ return ROOMS.find(r => r.id === rid); }

async function reloadRooms(){
  try{ ROOMS = await api('/api/rooms'); await loadRoomLocks(); }catch(e){}
  renderTabs();
  render();
}

// a device lives in one room: it leaves the others when it joins the chosen one
async function moveDeviceToRoom(devId, roomId){
  for(const r of ROOMS){
    const has = (r.devices || []).includes(devId);
    if(r.id === roomId && !has)
      await api('/api/rooms/' + encodeURIComponent(r.id), {method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({devices: (r.devices || []).concat([devId])})});
    else if(r.id !== roomId && has)
      await api('/api/rooms/' + encodeURIComponent(r.id), {method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({devices: (r.devices || []).filter(x => x !== devId)})});
  }
  await reloadRooms();
}

// a new room by name: its id, or null (cancelled, refused: said)
async function newRoom(){
  const name = (await askText(i18n('Room name:'), '', i18n('Living room')) || '').trim();
  if(!name) return null;
  try{
    const r = await api('/api/rooms', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
    return r.room.id;
  }catch(e){ alert(i18n('Error: ')+e.message); return null; }
}

async function addRoomPrompt(){
  const created = await newRoom();
  if(created){ activeTab = 'room:' + created; rememberTab(activeTab); }
  await reloadRooms();
  if(created) openRoomPicker(created);   // the next step of a new room: which devices live in it
}

// asked before a room goes: it takes its outline on the map with it, and what still names it is listed
async function confirmRoomDelete(room){
  let refs = [];
  try{ refs = (await api('/api/rooms/' + encodeURIComponent(room.id) + '/refs')).named_by || []; }catch(e){}
  const what = r => r.kind === 'panel' ? i18n('panel {0}', r.name) : r.kind === 'satellite' ? i18n('satellite {0}', r.name)
    : r.kind === 'rule' ? i18n(r.name === 'automations' ? 'a schedule' : 'a rule') : r.name;
  return confirm(i18n('Delete "{0}"? This deletes the room and its outline on the map. It cannot be undone.', room.name)
    + (refs.length ? '\n' + i18n('It is still named by: {0}.', refs.map(what).join(', ')) : ''));
}

// a "more" button with its menu: [{t, f, red}] and '-' for a line; Esc, a tap outside or a choice closes it, the arrows move
function menuButton(items, tag, label){
  const b = el('button', 'dlg-btn', icon('more', 18));
  b.dataset.tb = 'more-' + tag;
  b.title = i18n(label); b.setAttribute('aria-label', i18n(label));
  b.setAttribute('aria-haspopup', 'menu'); b.setAttribute('aria-expanded', 'false');
  let m = null;
  const close = (focus) => { if(!m) return; m.remove(); m = null; b.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', keys, true); if(focus) b.focus(); };
  const outside = e => { if(m && !m.contains(e.target) && !b.contains(e.target)) close(false); };
  const keys = e => {
    if(!m) return;
    const its = [...m.querySelectorAll('[role="menuitem"]')], i = its.indexOf(document.activeElement);
    if(e.key === 'Escape'){ e.preventDefault(); e.stopPropagation(); close(true); }
    else if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){ e.preventDefault(); its[(i + (e.key === 'ArrowDown' ? 1 : its.length - 1)) % its.length].focus(); }
  };
  b.onclick = () => {
    if(m){ close(true); return; }
    m = el('div', 'roommenu'); m.setAttribute('role', 'menu');
    for(const it of items){
      if(it === '-'){ m.append(el('div', 'roommenu-sep')); continue; }
      const r = el('button', 'roommenu-item' + (it.red ? ' danger' : ''), escapeHtml(i18n(it.t)));
      r.setAttribute('role', 'menuitem'); r.dataset.mi = it.t;
      r.onclick = () => { close(false); it.f(); };
      m.append(r);
    }
    b.after(m); b.setAttribute('aria-expanded', 'true');
    document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', keys, true);
    m.querySelector('[role="menuitem"]').focus();
  };
  const wrap = el('span', 'roommenu-wrap'); wrap.append(b);
  return wrap;
}

function renderRoom(app, rid){
  const room = roomById(rid);
  if(!room){   // the room was deleted / failed to load -- do not leave an empty tab
    activeTab = 'fav'; rememberTab('fav');
    renderTabs(); render(); return;
  }
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;gap:8px;padding:8px 16px 0;flex-wrap:wrap';
  const rename = async ()=>{
    const name = (await askText(i18n('New room name:'), room.name) || '').trim();
    if(!name || name === room.name) return;
    try{
      await api('/api/rooms/'+encodeURIComponent(room.id), {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name})
      });
    }catch(e){ alert(i18n('Error: ')+e.message); }
    await reloadRooms();
  };
  const remove = async ()=>{
    if(!await confirmRoomDelete(room)) return;
    try{
      await api('/api/rooms/'+encodeURIComponent(room.id), {method:'DELETE'});
    }catch(e){ alert(i18n('Error: ')+e.message); }
    activeTab = 'fav'; rememberTab('fav');
    await reloadRooms();
  };
  const devs = (room.devices||[]).map(id => BY_ID[id]).filter(Boolean)
  ;
  if(devs.length > 1){
    const ord = document.createElement('button');
    ord.className = 'dlg-btn' + (gridEditMode ? ' primary' : '');
    ord.dataset.tb = 'order';
    ord.textContent = gridEditMode ? i18n('Done') : i18n('Order');
    ord.onclick = ()=>{ gridEditMode = !gridEditMode; render(); };
    bar.append(ord);
  }
  const items = [{t: 'Devices of the room', f: ()=> openRoomPicker(room.id)}, {t: 'Thermometer\u2026', f: ()=> openRoomSensors(room.id)},
                 {t: 'Rename room', f: rename}];   // the core hands rename and delete to the map
  if(allTabs().some(t => t.id === 'p:map')) items.push({t: 'Edit on the map', f: ()=>{ activeTab = 'p:map'; rememberTab(activeTab); renderTabs(); render(); }});
  items.push('-', {t: 'Delete room', red: true, f: remove});
  bar.append(menuButton(items, 'room', 'Room actions'));
  app.append(bar);
  if(!devs.length){
    app.append(el('div','skeleton',i18n('\u2014 no devices in this room yet: "Devices of the room" in the menu \u2014')));
    return;
  }
  const g = document.createElement('div'); g.className='grid packed compact';
  g.dataset.scope = activeTab;
  for(const d of devs){
    const cd = card(d);
    for(const f of HomePlugins.cards) HomePlugins.safe(f, cd, d);   // plug-in decorations
    cd.dataset.dev = d.id;
    if(gridEditMode) armGridDrag(cd, g, activeTab, async parent => {
      // visible order + the room's invisible (missing) members at the tail
      const visible = Array.from(parent.children).map(x => x.dataset.dev).filter(Boolean);
      const rest = (room.devices||[]).filter(id => !visible.includes(id));
      room.devices = visible.concat(rest);
      try{
        await api('/api/rooms/'+encodeURIComponent(room.id), {
          method:'PUT', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({devices: room.devices})
        });
      }catch(e){ notifyFail(i18n('Not saved: ') + e.message); }
    });
    g.appendChild(cd);
  }
  app.append(g);
  for(const cd of g.children) applyTileSize(cd, activeTab, cd.dataset.dev, g);
  packGrid(g);
}

function openRoomPicker(rid){
  const room = roomById(rid); if(!room) return;
  releaseModal();
  const md = document.getElementById('md');
  const selected = new Set(room.devices || []);
  const pickable = DEVICES.slice().sort((a,b)=> (a.name||a.id).localeCompare(b.name||b.id, LANG));
  md.innerHTML = `
    <div class="head">
      <button class="closebtn" onclick="closeModal()">\u00d7</button>
      <h2>${i18n('Devices:')} ${escapeHtml(room.name)}</h2>
      <div class="sub">${i18n('Click devices to put them into the room. One device may be in several rooms.')}</div>
    </div>
    <div class="body">
      <section style="padding-top:0;border-top:0">
        <div class="picker-list">
          ${pickable.map(d=>{
            const others = ROOMS.filter(r => r.id !== rid && (r.devices||[]).includes(d.id)).map(r=>r.name);
            const on = selected.has(d.id);
            return `<div class="picker-dev">
                      <button class="picker-chan ${on?'active':''}" data-dev="${escapeHtml(d.id)}"
                              style="width:100%;text-align:left">
                        <span class="pick-mark">${on?icon('check', 14):'+'}</span> ${escapeHtml(d.name)}<span class="picker-meta"> \u00b7 ${escapeHtml(d.category||d.transport||'')}${others.length?i18n(' \u00b7 also in: ')+escapeHtml(others.join(', ')):''}</span>
                      </button>
                    </div>`;
          }).join('')}
        </div>
      </section>
      <section style="display:flex;gap:10px;justify-content:flex-end">
        <button id="room-pick-cancel" class="dlg-btn">${i18n('Cancel')}</button>
        <button id="room-pick-save" class="dlg-btn primary">${i18n('Save')}</button>
      </section>
    </div>`;
  document.getElementById('bd').classList.add('show');
  document.body.style.overflow = 'hidden';
  openModalId = '__room_picker__';
  md.querySelectorAll('[data-dev]').forEach(btn=>{
    btn.onclick = ()=>{
      const id = btn.dataset.dev;
      if(selected.has(id)) selected.delete(id); else selected.add(id);
      const on = selected.has(id);
      btn.classList.toggle('active', on);
      const mark = btn.querySelector('.pick-mark');
      if(mark) mark.innerHTML = on ? icon('check', 14) : '+';
    };
  });
  md.querySelector('#room-pick-cancel').onclick = ()=> closeModal();
  md.querySelector('#room-pick-save').onclick = async ()=>{
    // order: the previous membership (minus removed), then the newly added alphabetically
    const prev = (room.devices||[]).filter(id => selected.has(id));
    const added = pickable.map(d=>d.id).filter(id => selected.has(id) && !prev.includes(id));
    try{
      await api('/api/rooms/'+encodeURIComponent(room.id), {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({devices: prev.concat(added)})
      });
    }catch(e){ alert(i18n('Error: ')+e.message); return; }
    closeModal();
    await reloadRooms();
  };
}


