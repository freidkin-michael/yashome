let gridEditMode = false;   // "Order" on device grids (tabs and rooms)
let resizing = false;       // a gesture is in progress (resize/drag) -- do not repack

// Masonry for .grid.packed (every device grid): 8px tracks (see CSS), the span is computed from the real
// content height of the card. Recomputed after render, on resize and on the WS tick
// (card content changes height, e.g. a brightness slider that appeared).
// Tile width in columns: explicit (resized), otherwise 1
function tileW(cd, scope, cols){
  const s = (TILE_SIZES[scope] || {})[cd.dataset.dev] || {};
  if(s.w) return Math.min(s.w, cols);
  return 1;
}

function packGrid(g){
  const rowH = 8, gap = GRID_GAP;
  const kids = Array.from(g.children);
  const cols = gridColCount(g);
  const scope = g.dataset.scope || activeTab;
  // two passes (otherwise every card = a relayout of the whole grid): first let every
  // card release its height -- align-self:start gives the box its natural content height,
  // a stretched box cannot be measured; then place them all in one go
  for(const cd of kids){
    // do NOT reset gridRowStart/End: the grid would then reflow automatically, its
    // height would drop and the browser would clip scrollTop -- the page jumps up on
    // every refresh. align-self:start already gives the natural height.
    cd.style.alignSelf = 'start';
  }
  const hs = kids.map(cd => cd.getBoundingClientRect().height);
  // we lay out ourselves: pinned ones (col) go to their column, the rest to the
  // shortest one; free[c] = the first free 8px track of column c
  const free = new Array(cols).fill(0);
  kids.forEach((cd, i) => {
    cd.style.alignSelf = '';
    const s = (TILE_SIZES[scope] || {})[cd.dataset.dev] || {};
    const w = tileW(cd, scope, cols);
    const spanRows = Math.max(1, Math.ceil((hs[i] + gap) / (rowH + gap)));
    let col;
    if(Number.isInteger(s.col) && s.col >= 0){
      col = Math.min(s.col, cols - w);
    }else{
      col = 0; let best = Infinity;
      for(let c = 0; c + w <= cols; c++){
        const m = Math.max(...free.slice(c, c + w));
        if(m < best){ best = m; col = c; }
      }
    }
    const row = Math.max(...free.slice(col, col + w));
    cd.style.gridColumnStart = String(col + 1);
    cd.style.gridColumnEnd = 'span ' + w;
    cd.style.gridRowStart = String(row + 1);
    cd.style.gridRowEnd = 'span ' + spanRows;
    for(let c = col; c < col + w; c++) free[c] = row + spanRows;
    cd.dataset.col = col;
    cd.dataset.colspan = w;
  });
}

function packGrids(){
  const sc = document.scrollingElement || document.documentElement;
  const y = sc.scrollTop;
  document.querySelectorAll('.grid.packed').forEach(packGrid);
  if(sc.scrollTop !== y) sc.scrollTop = y;
}

window.addEventListener('resize', ()=>{
  clearTimeout(window._packT);
  window._packT = setTimeout(packGrids, 120);
});

// Tile reorder mode: a shield over the card swallows clicks on its controls
// (otherwise dragging would flip switches), drag is on the card, the corner
// handle resizes.
function armGridDrag(cd, g, scope, persistOrder){
  cd.classList.add('editing');
  const shield = el('div');
  shield.style.cssText = 'position:absolute;inset:0;z-index:4';
  shield.onclick = ev => ev.stopPropagation();
  cd.append(shield);
  attachDragReorder(cd, '.card', (parent, info) => {
    if(g.classList.contains('packed') && info && info.x != null){
      dropIntoColumn(g, scope, cd, info.x, info.y, () => persistOrder(parent));
    }else{
      persistOrder(parent);
    }
  });
  attachResizer(cd, g, scope);
}

// Manual placement: a dropped tile is PINNED to the column under the pointer
// and placed vertically where it was dropped; unpinned ones keep finding their own place.
async function dropIntoColumn(g, scope, cd, x, y, persistOrder){
  const cols = gridColCount(g);
  const gr = g.getBoundingClientRect();
  const colW = (gr.width - (cols - 1) * GRID_GAP) / cols;
  const w = tileW(cd, scope, cols);
  const col = Math.max(0, Math.min(
    Math.floor((x - gr.left) / (colW + GRID_GAP)), cols - w));
  // position within the column: before its first tile whose centre is below the drop point
  const inCol = Array.from(g.children).filter(k => {
    if(k === cd) return false;
    const c0 = +k.dataset.col, cw = +k.dataset.colspan || 1;
    return c0 <= col && col < c0 + cw;
  }).sort((a, b) => (+a.style.gridRowStart || 0) - (+b.style.gridRowStart || 0));
  const before = inCol.find(k => {
    const r = k.getBoundingClientRect();
    return y < r.top + r.height / 2;
  });
  if(before) g.insertBefore(cd, before);
  else if(inCol.length) g.insertBefore(cd, inCol[inCol.length - 1].nextSibling);
  const s = (TILE_SIZES[scope] = TILE_SIZES[scope] || {})[cd.dataset.dev] || {};
  TILE_SIZES[scope][cd.dataset.dev] = {...s, col};
  packGrid(g);
  persistOrder();
  await saveTileSize(scope, cd.dataset.dev, {col});
}

const GRID_GAP = 10;

function gridColCount(g){
  return getComputedStyle(g).gridTemplateColumns.split(' ').length;
}

// Saved tile size: w in columns (clamped to what fits), min-height h, on a narrow screen hn
function applyTileSize(cd, scope, dev, g){
  const s = (TILE_SIZES[scope] || {})[dev];
  if(!s) return;
  if(s.w) cd.style.gridColumn = 'span ' + Math.min(s.w, gridColCount(g));
  const h = NARROW.matches ? s.hn : s.h;
  cd.style.minHeight = h ? h + 'px' : '';
}
if(NARROW.addEventListener) NARROW.addEventListener('change', () => {      // a turn or a resize across 640 px: the other height
  document.querySelectorAll('.grid.packed').forEach(g => {
    for(const cd of g.children) if(cd.dataset.dev) applyTileSize(cd, g.dataset.scope || activeTab, cd.dataset.dev, g);
  });
  packGrids();
});

// change = only the fields this act sets, over the entry as the server has it now: another screen's h or hn is never
// overwritten from this one's memory; hn is sent only when it is changed (the server keeps it otherwise)
async function saveTileSize(scope, dev, change){
  try{
    const cur = ((await api('/api/tile_sizes'))[scope] || {})[dev] || {};
    const s = {...cur, ...change};
    if(!s.w && !s.h && !s.hn && !(s.col >= 0)){ if(TILE_SIZES[scope]) delete TILE_SIZES[scope][dev]; }
    else (TILE_SIZES[scope] = TILE_SIZES[scope] || {})[dev] = s;
    await api('/api/tile_sizes/'+encodeURIComponent(scope), {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({device: dev, w: s.w || 0, h: s.h || 0, col: Number.isInteger(s.col) ? s.col : -1,
                            ...('hn' in change ? {hn: change.hn || 0} : {})})
    });
  }catch(e){ notifyFail(i18n('Not saved: ') + e.message); }
}

function attachResizer(cd, g, scope){
  const rz = el('div','resizer', icon('resize', 16));
  rz.title = i18n('Resize the tile; double-click for auto size');
  cd.append(rz);
  rz.ondblclick = async ev=>{                     // auto size for this screen only: a narrow one forgets hn, a wide one w, h, col
    ev.stopPropagation();
    cd.style.minHeight = '';
    if(!NARROW.matches) cd.style.gridColumn = '';
    if(g.classList.contains('packed')) packGrid(g);
    await saveTileSize(scope, cd.dataset.dev, NARROW.matches ? {hn: 0} : {w: 0, h: 0, col: -1});
  };
  rz.addEventListener('pointerdown', e=>{
    if(e.pointerType === 'mouse' && e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    const pid = e.pointerId;
    const cols = gridColCount(g);
    const colW = (g.getBoundingClientRect().width - (cols-1)*GRID_GAP) / cols;
    const start = cd.getBoundingClientRect();
    let w = Math.max(1, Math.round((start.width + GRID_GAP) / (colW + GRID_GAP)));
    let h = Math.round(start.height);
    // leave the grid alone during the gesture (otherwise the tile jumps under the hand):
    // draw a ghost of the future size, apply once on release
    resizing = true;
    const ghost = el('div','rzghost');
    ghost.style.left = start.left + 'px';
    ghost.style.top = start.top + 'px';
    document.body.append(ghost);
    const draw = ()=>{
      ghost.style.width = (w*colW + (w-1)*GRID_GAP) + 'px';
      ghost.style.height = h + 'px';
      ghost.textContent = w + i18n(' qty \u00d7 ') + h + 'px';
    };
    draw();
    const move = ev=>{
      if(ev.pointerId !== pid) return;
      ev.preventDefault();
      w = Math.min(cols, Math.max(1, Math.round(
            (start.width + ev.clientX - e.clientX + GRID_GAP) / (colW + GRID_GAP))));
      h = Math.max(40, Math.round(start.height + ev.clientY - e.clientY));
      draw();
    };
    const up = async ev=>{
      if(ev.pointerId !== pid) return;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      resizing = false;
      ghost.remove();
      if(ev.type === 'pointercancel') return;
      cd.style.gridColumn = 'span ' + w;
      // height not above the natural one -- do not fix it (let the content decide)
      cd.style.minHeight = ''; cd.style.alignSelf = 'start';
      const nat = cd.getBoundingClientRect().height;
      cd.style.alignSelf = '';
      const hKeep = h > nat + 8 ? h : 0;
      if(hKeep) cd.style.minHeight = hKeep + 'px';
      if(g.classList.contains('packed')) packGrid(g);
      await saveTileSize(scope, cd.dataset.dev, {w, [NARROW.matches ? 'hn' : 'h']: hKeep});
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  });
}

async function saveTabOrder(tabId, parent){
  const order = Array.from(parent.children).map(x => x.dataset.dev).filter(Boolean);
  TAB_ORDER[tabId] = order;
  try{
    await api('/api/tab_order/'+encodeURIComponent(tabId), {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({order})
    });
  }catch(e){ notifyFail(i18n('Not saved: ') + e.message); }
}

