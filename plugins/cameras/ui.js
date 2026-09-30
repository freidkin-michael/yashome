// Cameras tab: one camera at a time, snapshot polling fetched with the token and shown as blob: images.
(() => {
  let cams = [], sel = null;
  const E = s => Home.escapeHtml(s);
  const MEDIA_CSS = 'max-width:100%;max-height:78vh;width:auto;height:auto;object-fit:contain;border-radius:14px;'
    + 'background:#000;border:1px solid var(--line)';
  const load = async () => { try { cams = await Home.api('/api/cameras') || []; } catch(e){ cams = []; } };
  const url = (cid, what) => `/api/camera/${encodeURIComponent(cid)}/${what}`;

  async function render(box){
    box.innerHTML = `<div class="skeleton">${E(i18n('Loading cameras\u2026'))}</div>`;
    await load();
    if(!cams.length){ box.innerHTML = `<div class="skeleton">${E(i18n('\u2014 no cameras \u2014'))}</div>`; return; }
    box.innerHTML = '';
    if(!cams.some(c => c.id === sel)) sel = cams[0].id;
    const tabs = Home.el('div'); tabs.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin:12px 14px 6px';
    const stage = Home.el('div'); stage.style.cssText = 'margin:0 14px';
    box.append(tabs, stage);

    function drawTabs(){
      tabs.innerHTML = '';
      for(const c of cams){
        const b = Home.el('button', null, E(c.name));
        b.style.cssText = 'padding:7px 14px;border-radius:10px;cursor:pointer;font-weight:600;border:1px solid var(--line);'
          + (c.id === sel ? 'background:var(--accent-fill);color:#fff' : 'background:var(--card);color:var(--txt)');
        b.onclick = () => { if(sel !== c.id){ sel = c.id; drawTabs(); show(); } };
        tabs.append(b);
      }
    }

    function show(){
      stage.innerHTML = '';
      const c = cams.find(x => x.id === sel); if(!c) return;
      const cid = c.id;
      const row = Home.el('div'); row.style.cssText = 'display:flex;gap:14px;align-items:center;justify-content:center;flex-wrap:wrap';
      const wrap = Home.el('div'); wrap.style.cssText = 'flex:1 1 480px;min-width:0;max-width:1100px;display:flex;justify-content:center';
      row.append(wrap); stage.append(row);
      const img = document.createElement('img'); img.alt = c.name; img.style.cssText = MEDIA_CSS;
      const FPS_MS = 400, HANG_MS = 8000, RETRY_MS = 2000;
      let shown = null;
      const alive = () => Home.activeTab() === 'p:cameras' && sel === cid && document.body.contains(img);
      async function poll(){
        if(!alive()) return;
        const ctl = new AbortController(), wd = setTimeout(() => ctl.abort(), HANG_MS);
        try{
          const r = await fetch(url(cid, 'snapshot') + '?t=' + Date.now(), {headers: Home.authHeaders(), signal: ctl.signal});
          if(!r.ok) throw new Error(r.status);
          const next = URL.createObjectURL(await r.blob());
          img.src = next;
          if(shown) URL.revokeObjectURL(shown);
          shown = next;
          if(alive()) setTimeout(poll, FPS_MS); else URL.revokeObjectURL(shown);
        }catch(e){ if(alive()) setTimeout(poll, RETRY_MS); }
        finally{ clearTimeout(wd); }
      }
      wrap.append(img);
      poll();
    }
    drawTabs();
    show();
  }

  HomePlugins.register({
    onReload: [load],
    tabs: [{id: 'cameras', label: 'Cameras', after: 'tech', render, visible: () => cams.length > 0, badge: () => cams.length}],
  });
})();
