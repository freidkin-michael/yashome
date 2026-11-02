// Repack at most once per frame: every WS tick used to force a synchronous
// layout of the whole grid.
let _packRaf = 0;
function schedulePack(){
  if(_packRaf) return;
  _packRaf = requestAnimationFrame(()=>{ _packRaf = 0; if(!resizing) packGrids(); });
}

// Reconnect with capped exponential backoff, and treat a socket that has gone
// quiet for 60 s as dead: a phone hopping WiFi->LTE leaves it half-open and the
// UI frozen on "online" otherwise.
let _wsDelay = 1000, _wsLast = 0, _wsWatch = 0, _wsRefused = 0;   // _wsRefused: closes before open in a row
function connectWS(){
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(withToken(`${proto}://${location.host}/ws`));
  let opened = false;
  const retry = ()=>{
    clearInterval(_wsWatch);
    $('#conn').textContent=i18n('No connection, reconnecting'); $('#conn').title = i18n('What you see may be old until the link comes back.'); $('#conn').classList.remove('live');
    // Refused before it ever opened: ask the API why. A 401 brings up the token prompt. If the
    // API answers, a restart would explain one or two misses; three in a row with a healthy API
    // mean the socket itself is refused -- an Origin the backend does not accept.
    if(!opened){
      _wsRefused++;
      api('/api/health').then(()=>{
        if(_wsRefused < 3) return;
        $('#conn').textContent = i18n('No connection: the house does not accept this address'); $('#conn').title = i18n('Open the dashboard by the house\'s usual address. A new address is allowed where the house is installed.');
        console.warn('live link refused: add this origin to DASHBOARD_ORIGINS in .env');
      }).catch(()=>{});
    }
    const wait = _wsDelay + Math.random() * 500;
    _wsDelay = Math.min(_wsDelay * 2, 30000);
    setTimeout(connectWS, wait);
  };
  ws.onopen  = ()=>{
    opened = true; _wsRefused = 0;
    $('#conn').textContent = ''; $('#conn').title = ''; $('#conn').classList.add('live');   // a live link is the normal state: nothing shown
    _wsDelay = 1000; _wsLast = Date.now();
    clearInterval(_wsWatch);
    _wsWatch = setInterval(()=>{
      if(Date.now() - _wsLast > 60000){
        // close() waits for a handshake a half-open socket never answers: detach and reconnect now
        ws.onclose = ws.onmessage = ws.onerror = null;
        try{ ws.close(); }catch(_){}
        retry();
      }
    }, 10000);
  };
  ws.onclose = retry;
  ws.onerror = ()=>{ try{ ws.close(); }catch(_){} };
  ws.onmessage = e =>{
    _wsLast = Date.now();
    const incoming = JSON.parse(e.data);
    const now = Date.now();
    for(const [k, info] of Object.entries(optimistic)){
      if(info.expires < now){ delete optimistic[k]; continue; }
      const slash = k.indexOf('/');
      const id = k.slice(0, slash), code = k.slice(slash+1);
      if(incoming[id]?.values) incoming[id].values[code] = info.value;
    }
    delete incoming._meta;
    STATE = incoming;
    updateCards();
    // card content changes height (sliders, statuses) -- repack,
    // but not in the middle of a gesture: repacking under the finger makes the tile jump
    schedulePack();
    if(openModalId) updateModal();
  };
}

