// Cameras tab: one camera at a time, snapshot polling fetched with the token and shown as blob: images.
(() => {
  let cams = [], sel = null;
  const E = s => Home.escapeHtml(s);
  const MEDIA_CSS = 'max-width:100%;max-height:78vh;width:auto;height:auto;object-fit:contain;border-radius:14px;'
    + 'background:#000;border:1px solid var(--line)';
  const load = async () => { try { cams = await Home.api('/api/cameras') || []; } catch(e){ cams = []; } };
  const url = (cid, what) => `/api/camera/${encodeURIComponent(cid)}/${what}`;
  // "SCL23 - rear" (a phone's camera as the panels plug-in names it) -> "SCL23 - rear camera" in the UI language
  const pretty = n => String(n).replace(/\s*-\s*(rear|back)$/i, ' \u00b7 ' + i18n('rear camera')).replace(/\s*-\s*front$/i, ' \u00b7 ' + i18n('front camera'));

  async function render(box){
    box.innerHTML = `<div class="skeleton">${E(i18n('Loading cameras\u2026'))}</div>`;
    await load();
    if(!cams.length){ box.innerHTML = `<div class="skeleton">${E(i18n('\u2014 no cameras \u2014'))}</div>`; return; }
    box.innerHTML = '';
    // the first visit: the camera that last gave a real picture, else the first
    if(!cams.some(c => c.id === sel)) sel = (cams.find(c => c.id === localStorage.getItem('cam_good')) || cams[0]).id;
    const tabs = Home.el('div'); tabs.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin:12px 14px 6px';
    const stage = Home.el('div'); stage.style.cssText = 'margin:0 14px';
    const allDark = Home.el('div'); allDark.style.cssText = 'display:none;margin:12px 14px 0;padding:10px 12px;border-radius:10px;background:var(--card);color:var(--dim);font-size:14px';
    box.append(allDark, tabs, stage);
    let picked = false;

    function drawTabs(){
      tabs.innerHTML = '';
      for(const c of cams){
        const b = Home.el('button', null, E(pretty(c.name)));
        b.style.cssText = 'padding:7px 14px;border-radius:10px;cursor:pointer;font-weight:600;border:1px solid var(--line);'
          + (c.id === sel ? 'background:var(--accent-fill);color:#fff' : 'background:var(--card);color:var(--txt)');
        b.onclick = () => { picked = true; if(sel !== c.id){ sel = c.id; drawTabs(); show(); } };
        tabs.append(b);
      }
    }

    // one look at every camera: open on one that shows a picture; say so once when none does
    async function probeAll(){
      const dark = await Promise.all(cams.map(async c => {
        try {
          const r = await fetch(url(c.id, 'snapshot') + '?t=' + Date.now(), {headers: Home.authHeaders()});
          if(!r.ok) return null;
          const bm = await createImageBitmap(await r.blob()), cv = document.createElement('canvas'); cv.width = 16; cv.height = 9;
          const g = cv.getContext('2d'); g.drawImage(bm, 0, 0, 16, 9);
          const px = g.getImageData(0, 0, 16, 9).data; let sum = 0;
          for(let i = 0; i < px.length; i += 4) sum += px[i] + px[i + 1] + px[i + 2];
          return sum / (px.length / 4 * 3) < 10;
        } catch(e){ return null; }
      }));
      const good = cams.find((c, i) => dark[i] === false);
      if(good){ localStorage.setItem('cam_good', good.id); if(!picked && sel !== good.id){ sel = good.id; drawTabs(); show(); } }
      if(dark.length && dark.every(x => x === true)){ allDark.textContent = i18n('Every camera gives a black frame now: the phones\' screens are off.'); allDark.style.display = 'block'; }
    }

    function show(){
      stage.innerHTML = '';
      const c = cams.find(x => x.id === sel); if(!c) return;
      const cid = c.id;
      const row = Home.el('div'); row.style.cssText = 'display:flex;gap:14px;align-items:center;justify-content:center;flex-wrap:wrap';
      const wrap = Home.el('div'); wrap.style.cssText = 'flex:1 1 480px;min-width:0;max-width:1100px;display:flex;justify-content:center';
      row.append(wrap); stage.append(row);
      const img = document.createElement('img'); img.alt = pretty(c.name); img.style.cssText = MEDIA_CSS; img.style.display = 'none';   // never a broken image: shown with its first frame
      const cap = Home.el('div'); cap.style.cssText = 'color:var(--dim);font-size:14px;margin:0 0 8px;text-align:center';
      cap.textContent = pretty(c.name);
      const note = Home.el('div'); note.style.cssText = 'position:absolute;inset:0;display:none;align-items:center;justify-content:center;padding:24px;text-align:center;color:var(--dim);font-size:15px';
      const box = Home.el('div'); box.style.cssText = 'position:relative;display:flex;justify-content:center;min-width:0;max-width:100%;min-height:240px;width:100%';
      let fails = 0;
      // a frame that is all but black (a covered lens, a phone with its screen off): say so inside the box
      const probe = document.createElement('canvas'); probe.width = 16; probe.height = 9;
      const dark = () => { try { const g = probe.getContext('2d'); g.drawImage(img, 0, 0, 16, 9); const px = g.getImageData(0, 0, 16, 9).data;
        let sum = 0; for(let i = 0; i < px.length; i += 4) sum += px[i] + px[i + 1] + px[i + 2]; return sum / (px.length / 4 * 3) < 10; } catch(e){ return false; } };
      const say = t => { note.textContent = t || ''; note.style.display = t ? 'flex' : 'none'; };
      const FPS_MS = 400, HANG_MS = 8000, RETRY_MS = 2000;
      let shown = null, probed = false, keptAt = 0;
      // the last good frame, small, kept on this device: the next visit paints it at once
      const keepFrame = () => {
        if(Date.now() - keptAt < 20000) return;
        keptAt = Date.now();
        try { const cv = document.createElement('canvas'); cv.width = 480; cv.height = Math.round(480 * img.naturalHeight / img.naturalWidth);
          cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height); localStorage.setItem('cam_last_' + cid, cv.toDataURL('image/jpeg', 0.6)); } catch(e){ /* storage full: no cache */ }
      };
      const alive = () => Home.activeTab() === 'p:cameras' && sel === cid && document.body.contains(img);
      async function poll(){
        if(!alive()) return;
        const ctl = new AbortController(), wd = setTimeout(() => ctl.abort(), HANG_MS);
        try{
          const r = await fetch(url(cid, 'snapshot') + '?t=' + Date.now(), {headers: Home.authHeaders(), signal: ctl.signal});
          if(!r.ok) throw new Error(r.status);
          const next = URL.createObjectURL(await r.blob());
          img.src = next; fails = 0;
          img.onload = () => { img.style.display = ''; const black = dark(); if(!black) localStorage.setItem('cam_good', cid);
            if(!black) keepFrame(); else if(!probed){ probed = true; probeAll(); }
            say(black ? i18n('The camera gives a black frame: the phone\'s screen is off or the lens is covered.') : '');
            cap.textContent = pretty(c.name) + ' \u00b7 ' + i18n('frame at') + ' ' + new Date().toLocaleTimeString(Home.when ? undefined : undefined, {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false}); };
          if(shown) URL.revokeObjectURL(shown);
          shown = next;
          if(alive()) setTimeout(poll, FPS_MS); else URL.revokeObjectURL(shown);
        }catch(e){
          if(++fails >= 3) say(i18n('No picture: the phone is off, its screen is locked, or the camera is in use.'));
          if(alive()) setTimeout(poll, RETRY_MS);
        }
        finally{ clearTimeout(wd); }
      }
      box.append(img, note);
      const old = localStorage.getItem('cam_last_' + cid);
      if(old){ img.src = old; img.style.display = ''; cap.textContent = pretty(c.name) + ' \u00b7 ' + i18n('last frame'); }
      else say(i18n('Checking the camera\u2026'));
      wrap.append(box);
      stage.prepend(cap);
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
