// The first screen's strip: who is home (the family and presence devices) and each room with its temperature
function homeStripHtml(){
  const people = DEVICES.filter(d => d.transport === 'family' && String(d.id).startsWith('person_'))
    .filter(d => (STATE[d.id] && STATE[d.id].values || {}).home === true).map(d => d.name);
  const presence = DEVICES.find(d => d.transport === 'presence');
  const someone = people.length || (presence && (STATE[presence.id] && STATE[presence.id].values || {}).home === true);
  const who = people.length ? `${i18n('At home:')} <b>${people.map(escapeHtml).join(', ')}</b>`
            : someone ? `<b>${i18n('Someone is home')}</b>` : i18n('Nobody is home');
  return `<span class="hs-who">${icon('person', 16)} ${who}</span>`;
}
function homeStrip(){
  const box = el('div', 'homestrip'); box.id = 'homestrip';
  box.innerHTML = homeStripHtml();
  return box;
}
function renderFavorites(app){
  const tb = document.createElement('div');
  tb.className='fav-toolbar';
  if(favDeleteMode){
    const n = favSelected.size;
    tb.innerHTML = `
      <div style="font-size:13px;color:var(--dim)">${i18n('pick the cards to delete')}</div>
      <div style="display:flex;gap:8px">
        <button id="fav-del-go" class="danger" ${n?'':'disabled'}>${i18n('Delete')}${n?` (${n})`:''}</button>
        <button id="fav-del-cancel">${i18n('Cancel')}</button>
      </div>`;
  }else{
    const btns = favEditMode
      ? `<button id="fav-add" class="primary">${i18n('Pick favourites')}</button>
         <button id="fav-edit" class="active">${i18n('Done')}</button>`
      : `<button id="fav-add" class="primary">${i18n('Pick favourites')}</button>
         <button id="fav-edit">${i18n('Order')}</button>
         <button id="fav-del">${i18n('Remove cards')}</button>`;
    tb.innerHTML = `
      <div style="font-size:13px;color:var(--dim)">${
        favEditMode ? i18n('drag the cards into the order you want') : ''
      }</div>
      <div style="display:flex;gap:8px">${btns}</div>`;
  }
  app.append(tb);
  if(!favDeleteMode && !favEditMode) tb.firstElementChild.replaceWith(homeStrip());   // the strip takes the hint's place
  if(!FAVORITES.length){
    app.append(el('div','fav-empty',i18n('Empty so far: press the star on a card, or "Pick favourites" here.')));
  }else{
    const g = el('div','fav-grid');
    FAVORITES.forEach(f => g.appendChild(favCard(f)));
    app.append(g);
  }
  if(favDeleteMode){
    document.getElementById('fav-del-cancel').onclick = ()=>{
      favDeleteMode = false; favSelected.clear(); render();
    };
    const go = document.getElementById('fav-del-go');
    if(go) go.onclick = async ()=>{
      const targets = FAVORITES.filter(f => favSelected.has(`${f.device}/${f.code}`));
      const gone = new Set();
      for(const f of targets){
        try{
          await api(`/api/favorites/${encodeURIComponent(f.device)}/${encodeURIComponent(f.code)}`, {method:'DELETE'});
          gone.add(`${f.device}/${f.code}`);
        }catch(e){ notifyFail(i18n('Could not remove from favourites: ') + e.message); }
      }
      FAVORITES = FAVORITES.filter(f => !gone.has(`${f.device}/${f.code}`));
      favDeleteMode = false; favSelected.clear();
      renderTabs(); render();
    };
    return;
  }
  document.getElementById('fav-add').onclick = ()=> openPicker();
  document.getElementById('fav-edit').onclick = ()=>{
    favEditMode = !favEditMode; render();
  };
  const delBtn = document.getElementById('fav-del');
  if(delBtn) delBtn.onclick = ()=>{
    favDeleteMode = true; favEditMode = false; favSelected.clear(); render();
  };
}

function favCard(f){
  const d = BY_ID[f.device];
  const c = el('div','fav-card');
  c.id = `fav_${f.device}_${f.code}`;
  c.dataset.device = f.device;
  c.dataset.code = f.code;

  if(favDeleteMode){
    const key = `${f.device}/${f.code}`;
    c.classList.add('selectable');
    if(favSelected.has(key)) c.classList.add('selected');
    c.append(el('div','selmark', favSelected.has(key) ? icon('check', 14) : ''));
    c.append(el('div','alias', escapeHtml(f.alias || f.code)));
    if(!d) c.append(el('div','orphan',i18n('\u2014 device is gone \u2014')));
    c.onclick = ()=>{
      if(favSelected.has(key)) favSelected.delete(key); else favSelected.add(key);
      render();
    };
    return c;
  }

  if(!d){
    c.innerHTML = `<div class="alias">${i18n('\u2014 device is gone \u2014')}</div>`;
    return c;
  }
  if(favEditMode) c.classList.add('editing');

  const alias = el('div','alias', devIcon(d) + escapeHtml(f.alias));
  c.append(alias);

  const dot = el('div','bigdot');
  dot.id = `favdot_${f.device}_${f.code}`;
  c.append(dot);
  if(d.category === 'climate'){ const tt = el('div','dottemp'); tt.id = `favtemp_${f.device}_${f.code}`; dot.append(tt); dot.classList.add('climdot'); }
  const sw = el('div','favstate'); sw.id = `favstate_${f.device}_${f.code}`; sw.textContent = '\u2014'; c.append(sw);

  if(favEditMode){
    attachDragReorder(c);
  } else {
    const bright = brightForCode(d, f.code);
    if(bright){
      // dimmable: the CIRCLE fills yellow = brightness; tap=on/off,
      // swipe on the circle=brightness, long-press=editor.
      const fill = el('div','dotfill'); fill.id = `dotfill_${d.id}_${bright.code}`;
      dot.classList.add('dimdot'); dot.prepend(fill);
      const pc = el('div','dimpc'); pc.id = `dimpc_${d.id}_${bright.code}`;
      c.append(pc);
      attachDimmerDot(dot, d, bright,
        ()=> sendControl(f.device, f.code, !switchOn(f.device, f.code)),
        ()=> openFavEditor(f));
    } else {
      attachLongPress(c, ()=> sendControl(f.device, f.code, !switchOn(f.device, f.code)),
                         ()=> openFavEditor(f));
    }
  }
  return c;
}

// Drag a card to reorder. Strategy: lift the card with a transform that
// follows the pointer, highlight the drop-target under the pointer, swap DOM
// only on release. Avoids visual snapping during drag.
// selector = siblings to swap with; onDrop(parent) = persist the new order
// (default persists FAVORITES -- the original consumer of this drag).
function attachDragReorder(card, selector = '.fav-card', onDrop = null){
  let dragging = false;
  let pid = -1;
  let grabX = 0, grabY = 0;
  let dropTarget = null;
  let dropAfter = false;   // pointer right of the centre / below the target -- insert AFTER it

  const setDropTarget = t=>{
    if(dropTarget === t) return;
    dropTarget?.classList.remove('drop-target');
    dropTarget = t;
    dropTarget?.classList.add('drop-target');
  };

  // Missed the cards (grid holes, gaps, margins) -- snap to the geometrically
  // nearest one, otherwise a drop into the void is silently rolled back.
  const nearestCard = (x, y)=>{
    let best = null, bd = 150*150;   // no snapping beyond 150px
    for(const k of card.parentElement.children){
      if(k === card || !k.matches(selector)) continue;
      const r = k.getBoundingClientRect();
      const dx = Math.max(r.left - x, 0, x - r.right);
      const dy = Math.max(r.top - y, 0, y - r.bottom);
      const d = dx*dx + dy*dy;
      if(d < bd){ bd = d; best = k; }
    }
    return best;
  };

  const onMove = (e)=>{
    if(!dragging || e.pointerId !== pid) return;
    e.preventDefault();
    card.style.transform = `translate(${e.clientX - grabX}px, ${e.clientY - grabY}px)`;
    card.style.visibility = 'hidden';
    const under = document.elementFromPoint(e.clientX, e.clientY);
    card.style.visibility = '';
    let t = under?.closest(selector);
    if(!(t && t !== card && t.parentElement === card.parentElement)) t = null;
    if(!t) t = nearestCard(e.clientX, e.clientY);
    if(t){
      const r = t.getBoundingClientRect();
      dropAfter = e.clientY > r.bottom
               || (e.clientY >= r.top && e.clientX > r.left + r.width/2);
    }
    setDropTarget(t);
  };

  const finish = async (e)=>{
    if(!dragging || (e && e.pointerId !== pid)) return;
    dragging = false;
    resizing = false;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', finish);
    window.removeEventListener('pointercancel', finish);
    card.classList.remove('dragging');
    card.style.transform = '';
    card.style.visibility = '';
    const t = dropTarget;
    setDropTarget(null);
    if(t){
      const parent = card.parentElement;
      if(dropAfter) parent.insertBefore(card, t.nextSibling);
      else          parent.insertBefore(card, t);
      if(onDrop){ onDrop(parent, {x: e && e.clientX, y: e && e.clientY}); return; }
      const newOrder = Array.from(parent.children)
        .map(c => ({device: c.dataset.device, code: c.dataset.code}))
        .filter(x => x.device);
      FAVORITES = newOrder
        .map(o => FAVORITES.find(f => f.device===o.device && f.code===o.code))
        .filter(Boolean);
      try{
        await api('/api/favorites/reorder', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({order: FAVORITES.map(f=>`${f.device}/${f.code}`)})
        });
      }catch(e){ notifyFail(i18n('Not saved: ') + e.message); }
    }
  };

  card.addEventListener('pointerdown', e=>{
    if(e.pointerType === 'mouse' && e.button !== 0) return;
    dragging = true;
    resizing = true;   // freeze WS-driven repacking for the duration of the gesture
    pid = e.pointerId;
    grabX = e.clientX; grabY = e.clientY;
    card.classList.add('dragging');
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', finish);
    window.addEventListener('pointercancel', finish);
    e.preventDefault();
  });
}

// Long-press handler: short tap fires `onShort`; press held >550ms fires `onLong`
// (vibrates briefly on phones if supported)
function attachLongPress(elx, onShort, onLong){
  let timer = null;
  let fired = false;
  let startX = 0, startY = 0;
  const LONG_MS = 550;
  const start = (x, y) => {
    fired = false; startX = x; startY = y;
    timer = setTimeout(()=>{
      fired = true;
      try{ navigator.vibrate?.(30); }catch(e){}
      onLong();
    }, LONG_MS);
  };
  const cancel = () => { if(timer){ clearTimeout(timer); timer = null; } };
  const end = () => {
    if(timer){ clearTimeout(timer); timer = null; if(!fired) onShort(); }
  };
  elx.addEventListener('pointerdown', e => { start(e.clientX, e.clientY); });
  elx.addEventListener('pointerup',   e => { end(); });
  elx.addEventListener('pointercancel', cancel);
  elx.addEventListener('pointerleave',  cancel);
  elx.addEventListener('pointermove',   e => {
    if(Math.abs(e.clientX - startX) > 8 || Math.abs(e.clientY - startY) > 8) cancel();
  });
  elx.addEventListener('contextmenu', e => { e.preventDefault(); if(fired) return; cancel(); fired = true; onLong(); });
}

async function removeFav(f){
  try{
    await api(`/api/favorites/${encodeURIComponent(f.device)}/${encodeURIComponent(f.code)}`, {method:'DELETE'});
  }catch(e){ notifyFail(i18n('Could not remove from favourites: ') + e.message); return; }
  FAVORITES = FAVORITES.filter(x => !(x.device===f.device && x.code===f.code));
  renderTabs(); render();
}

async function renameFav(f, newAlias){
  if(!newAlias || newAlias === f.alias) return;
  try{
    await api(`/api/favorites/${encodeURIComponent(f.device)}/${encodeURIComponent(f.code)}`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({alias: newAlias})
    });
    f.alias = newAlias;
  }catch(e){ alert(i18n('Error: ')+e.message); render(); }
}

// The card's star: on = some channel of the device is a favourite. A tap adds the main channel
// (the first switch, else the first reading) under the device's name, or removes all of them.

