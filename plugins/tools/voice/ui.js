// Voice / Sound: the tab of the satellites' cards, and a section in the device window with the satellite's settings.
(() => {
  const esc = Home.escapeHtml;

  function form(s, voices){
    const opt = v => `<option value="${esc(v)}"${v === (s.voice || '') ? ' selected' : ''}>${esc(v || i18n('automatic'))}</option>`;
    const st = s.stats || {};
    const peer = `<div class="row"><span class="rlabel">${esc(i18n('Audio comes from (address)'))}</span><input class="askin" data-f="peer" value="${esc(s.peer)}"></div>`;
    const where = s.room_name ? `${esc(i18n('Room'))}: ${esc(s.room_name)}` : esc(i18n('Not in a room: put it in a room on the map'));
    return `<div class="bindcaption">${where}</div>
      ${s.peer || s.phone ? '' : `<div class="bindcaption">${esc(i18n('The house does not know where the sound of this satellite comes from: type its address in the network'))}</div>${peer}`}
      ${s.player ? `<div class="row"><span class="rlabel">${esc(i18n('Answers in voice'))}</span><select class="askin" data-f="voice">${['', ...voices].map(opt).join('')}</select></div>
        <div class="row"><span class="rlabel">${esc(i18n('Silent when not understood'))}<br><span class="bindcaption">${esc(i18n('No reply and no sound when a command was not understood: for a satellite that wakes on the TV'))}</span></span><input type="checkbox" data-f="quiet_fail" ${s.quiet_fail ? 'checked' : ''}></div>` : ''}
      <details><summary>${esc(i18n('Advanced'))}</summary>
        ${s.peer && !s.phone ? peer : ''}
        <div class="bindcaption">${esc(i18n('Wakes'))}: ${st.wakes || 0} &middot; ${esc(i18n('Commands'))}: ${st.commands || 0} &middot; ${esc(i18n('Not understood'))}: ${st.errors || 0}${st.last_wake ? ' &middot; ' + esc(i18n('Last wake')) + ': ' + esc(st.last_wake) : ''}</div>
      </details>
      <div class="row" style="justify-content:flex-end;gap:6px"><span data-f="msg" class="bindcaption"></span><button class="dlg-btn primary" data-f="save">${esc(i18n('Save'))}</button></div>`;
  }

  async function fill(box, d){
    const out = box.querySelector('[data-voice="box"]');
    let s, voices = [];
    try {
      s = (await Home.api('/api/voice/satellites')).find(x => x.id === d.voice_sat);
      voices = ((await Home.api('/api/voice/status')).tts || {}).voices || [];
    } catch(e){ out.textContent = e.message || String(e); return; }
    if(!s){ out.textContent = i18n('No such satellite'); return; }
    out.innerHTML = form(s, voices);
    const val = f => out.querySelector(`[data-f="${f}"]`);
    val('save').onclick = async () => {
      const keep = Object.fromEntries(['wake_chime', 'chime', 'lead_in', 'listen_once', 'phone', 'need_wake'].filter(k => k in s).map(k => [k, s[k]]));   // what this form does not show stays as it is
      const body = {...keep, node: s.node, panel: s.panel || '', mic: !!s.mic, player: !!s.player, volume_state: s.volume_state || '',
                    name: s.name || '', room: s.room || '', peer: val('peer') ? val('peer').value.trim() : (s.peer || ''), voice: val('voice') ? val('voice').value : (s.voice || ''),
                    quiet_fail: val('quiet_fail') ? val('quiet_fail').checked : !!s.quiet_fail};
      try { await Home.api('/api/voice/satellites/' + encodeURIComponent(s.id), {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); val('msg').textContent = i18n('Saved'); }
      catch(e){ val('msg').textContent = e.message || String(e); }
    };
  }


  // "Ask the house": a typed phrase takes the road of a spoken one after recognition; a guess or a risky device asks Yes / No first
  async function askHouse(text){
    let sats = [];
    let rooms = {};
    try { sats = (await Home.api('/api/voice/satellites')).filter(x => x.player); } catch(e){ /* the box works without a speaker */ }
    try { (await Home.api('/api/rooms')).forEach(r => { rooms[r.id] = r.name; }); } catch(e){ /* no room names: the speaker is listed by its own name */ }
    const place = x => {                                                 // the speaker's card as renamed, and the map's room
      const n = (x.cards || {}).spk || x.name || x.id, r = x.room_name || rooms[x.room] || x.room;
      return n + (r && !n.includes(r) ? ` (${r})` : '');
    };
    const bub = 'padding:8px 12px;border-radius:12px;max-width:85%;font-size:15px;line-height:1.35;white-space:pre-wrap;word-break:break-word;';
    Home.openModal(`<div style="padding:16px 18px;display:flex;flex-direction:column;gap:12px;box-sizing:border-box;max-width:100%">
      <div style="display:flex;align-items:center;justify-content:space-between"><h2 style="margin:0">${esc(i18n('Ask the house'))}</h2>
        <button class="dlg-btn" data-a="close" aria-label="${esc(i18n('Close'))}" style="font-size:20px;line-height:1;padding:4px 12px">&times;</button></div>
      <div data-a="log" class="asklog" style="display:flex;flex-direction:column;gap:8px;max-height:50vh;overflow-y:auto;padding:2px"></div>
      <div data-a="yn" style="display:none;gap:8px"><button class="dlg-btn primary" data-a="yes">${esc(i18n('Yes'))}</button><button class="dlg-btn" data-a="no">${esc(i18n('No'))}</button></div>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><label style="display:flex;align-items:center;gap:6px;font-size:14px"><input type="checkbox" data-a="speak"> ${esc(i18n('Say the answer aloud'))}</label>
        <span data-a="where" style="display:none;align-items:center;gap:6px;font-size:14px">${esc(i18n('Where to answer'))}
          <select class="askin" data-a="target" style="min-width:0">${sats.map(x => `<option value="${esc(x.id)}">${esc(place(x))}</option>`).join('')}</select></span></div>
      <div style="display:flex;gap:8px"><input class="askin" data-a="text" placeholder="${esc(i18n('What should the house do?'))}" style="flex:1;min-width:0;box-sizing:border-box;font-size:15px"><button class="dlg-btn primary" data-a="send" style="flex:none">${esc(i18n('Ask'))}</button></div>
    </div>`, '__voice_ask__');
    const md = document.getElementById('md'), q = a => md.querySelector(`[data-a="${a}"]`);
    let token = '';
    const history = [];
    q('speak').onchange = () => { q('where').style.display = q('speak').checked && sats.length ? 'inline-flex' : 'none'; };
    const speak = () => (q('speak').checked && q('target').value) ? q('target').value : '';
    // a chat bubble: yours on the right, the house's on the left; `badge` is a small mark over the text ("Error")
    const bubble = (mine, text, badge, tip) => {
      const d = document.createElement('div');
      d.style.cssText = bub + (mine ? 'align-self:flex-end;background:var(--accent-fill);color:#fff' : 'align-self:flex-start;background:var(--card2);border:1px solid var(--line)');
      if(badge){ const s = document.createElement('div'); s.style.cssText = 'font-size:11px;opacity:.75;margin-bottom:2px'; s.textContent = badge; d.append(s); }
      if(tip) d.title = tip;
      const t = document.createElement('span'); t.textContent = text; d.append(t);
      q('log').append(d); q('log').scrollTop = q('log').scrollHeight;
      return t;
    };
    // the model said it is no command: a chat answer, streamed; it cannot act, and a small "i" by the bubble says so
    const chat = async text => {
      const tip = i18n('An answer of the model: nothing in the house was done');
      const out = bubble(false, i18n('Thinking\u2026'), '', tip);          // the big model reasons first: seconds
      const mark = document.createElement('span');
      mark.innerHTML = Home.icon('info', 12); mark.title = tip; mark.setAttribute('aria-label', tip); mark.style.cssText = 'opacity:.6;margin-left:6px';
      out.after(mark);
      let first = true;
      try {
        const r = await fetch('/api/voice/chat', {method: 'POST', headers: Home.authHeaders({'Content-Type': 'application/json'}), body: JSON.stringify({text, history})});
        if(!r.ok) throw new Error(i18n('The model is not available now'));
        const rd = r.body.getReader(), dec = new TextDecoder();
        for(;;){ const {done, value} = await rd.read(); if(done) break; if(first){ out.textContent = ''; first = false; }
          out.textContent += dec.decode(value, {stream: true}); q('log').scrollTop = q('log').scrollHeight; }
        if(first) out.textContent = i18n('The model did not answer.');
        history.push({role: 'user', content: text}, {role: 'assistant', content: out.textContent});
        history.splice(0, Math.max(0, history.length - 6));
      } catch(e){ out.textContent = e.message || String(e); }
    };
    const show = (r, text) => {
      if(r.chat){ chat(text); token = ''; q('yn').style.display = 'none'; return; }
      bubble(false, r.said);                                                                               // the engine and the model stay in the log
      token = r.ask && r.token ? r.token : '';
      q('yn').style.display = token ? 'flex' : 'none';
    };
    const post = async (path, body) => {
      try { show(await Home.api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}), body.text); }
      catch(e){ bubble(false, e.message || String(e), i18n('Error')); }
    };
    const send = () => {
      const t = q('text').value.trim();
      if(!t) return;
      bubble(true, t); q('text').value = ''; q('yn').style.display = 'none'; token = '';
      post('/api/voice/ask', {text: t, speak: speak()});
    };
    q('close').onclick = () => Home.closeModal();
    q('send').onclick = send;
    q('text').onkeydown = e => { if(e.key === 'Enter') send(); };
    const answer = yes => { const tk = token; token = ''; q('yn').style.display = 'none'; bubble(true, i18n(yes ? 'Yes' : 'No')); post('/api/voice/ask/confirm', {token: tk, yes, speak: speak()}); };
    q('yes').onclick = () => answer(true);
    q('no').onclick = () => answer(false);
    q('text').focus();
    if(text){ q('text').value = text; send(); }   // from the header's field: the phrase goes at once
  }

  // "Voice recording": samples of the wake phrase (positive) and of everything else (negative) for training; raw audio, no recognition, no action
  async function recordVoice(){
    let st;
    try { st = await Home.api('/api/voice/record/state'); } catch(e){ Home.openModal(`<div style="padding:16px">${esc(e.message || String(e))}</div>`, '__voice_rec__'); return; }
    const bub = 'font-size:15px;line-height:1.4';
    Home.openModal(`<div style="padding:16px 18px;display:flex;flex-direction:column;gap:12px;box-sizing:border-box;max-width:100%">
      <div style="display:flex;align-items:center;justify-content:space-between"><h2 style="margin:0">${esc(i18n('Voice recording'))}</h2>
        <button class="dlg-btn" data-a="close" aria-label="${esc(i18n('Close'))}" style="font-size:20px;line-height:1;padding:4px 12px">&times;</button></div>
      ${st.background ? `<div class="bindcaption">${esc(i18n('A background recording is running: stop it on the card before recording samples'))}</div>` : ''}
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><span>${esc(i18n('Speaker'))}</span>
        <select class="askin" data-a="spk" style="min-width:0"></select>
        <input class="askin" data-a="newspk" placeholder="${esc(i18n('New speaker'))}" style="min-width:0;width:9em"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><span>${esc(i18n('Device'))}</span>
        <select class="askin" data-a="dev" style="min-width:0"><option value="browser">${esc(i18n('This browser'))}</option>${st.devices.map(d => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('')}</select></div>
      <div style="display:flex;gap:16px;flex-wrap:wrap"><label><input type="radio" name="lbl" value="positive" checked> ${esc(i18n('The wake phrase'))}</label>
        <label><input type="radio" name="lbl" value="negative"> ${esc(i18n('Something else'))}</label></div>
      <div data-a="hint" style="${bub};min-height:3.2em"></div>
      <div style="display:flex;align-items:center;gap:12px"><button class="dlg-btn primary" data-a="rec" style="font-size:20px;padding:14px 28px">${esc(i18n('Record'))}</button>
        <span data-a="count" style="font-size:22px"></span></div>
      <div data-a="msg" class="bindcaption" style="min-height:1.4em"></div>
      <div style="display:flex;gap:8px"><button class="dlg-btn" data-a="play">${esc(i18n('Play back'))}</button><button class="dlg-btn" data-a="del">${esc(i18n('Delete the last'))}</button></div>
    </div>`, '__voice_rec__');
    const md = document.getElementById('md'), q = a => md.querySelector(`[data-a="${a}"]`);
    let last = null, phrases = [], busy = false;
    const label = () => md.querySelector('input[name="lbl"]:checked').value;
    const speaker = () => q('newspk').value.trim() || q('spk').value;
    const fillSpeakers = () => {
      const sp = Object.entries(st.speakers);
      q('spk').innerHTML = sp.map(([k, v]) => `<option value="${esc(k)}">${esc(v.name)}</option>`).join('') || `<option value="">-</option>`;
    };
    const count = () => {
      const typed = q('newspk').value.trim();
      const e = typed ? null : st.speakers[q('spk').value];
      q('count').textContent = ((e ? e[label()] : 0) || 0) + ' / ' + st.target;
    };
    const hint = async () => {
      if(label() === 'positive'){ q('hint').textContent = i18n('Say the wake phrase'); return; }
      try { phrases = (await Home.api('/api/voice/record/phrases?n=3')).phrases || []; } catch(e){ phrases = []; }
      q('hint').innerHTML = esc(i18n('Read aloud')) + ':<br>' + phrases.map(p => `<b>${esc(p)}</b>`).join('<br>');
    };
    const refresh = async () => { try { st = await Home.api('/api/voice/record/state'); } catch(e){ /* keep the old counters */ } const keep = q('spk').value; fillSpeakers(); if(keep) q('spk').value = keep; count(); };
    const post = (path, body) => Home.api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const beep = (ctx, ms) => new Promise(res => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 880; g.gain.value = 0.2; o.connect(g); g.connect(ctx.destination); o.start();
      setTimeout(() => { o.stop(); res(); }, ms);
    });
    const fromBrowser = async () => {
      const stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false}});
      const ctx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 16000});
      try {
        await beep(ctx, 500);
        const src = ctx.createMediaStreamSource(stream), proc = ctx.createScriptProcessor(4096, 1, 1), mute = ctx.createGain(), chunks = [];
        mute.gain.value = 0; proc.onaudioprocess = e => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
        src.connect(proc); proc.connect(mute); mute.connect(ctx.destination);
        await new Promise(r => setTimeout(r, 3000));
        src.disconnect(); proc.disconnect();
        const n = chunks.reduce((a, c) => a + c.length, 0), all = new Float32Array(n);
        let o = 0; chunks.forEach(c => { all.set(c, o); o += c.length; });
        const rate = ctx.sampleRate, outN = Math.floor(all.length * 16000 / rate), pcm = new Int16Array(outN);
        for(let i = 0; i < outN; i++){ const x = all[Math.min(all.length - 1, Math.floor(i * rate / 16000))]; pcm[i] = Math.max(-32768, Math.min(32767, Math.round(x * 32767))); }
        if(pcm.reduce((m, x) => Math.max(m, Math.abs(x)), 0) < 800) return {state: 'quiet', quiet: true};      // nobody spoke: not uploaded
        const typed = q('newspk').value.trim();
        const url = '/api/voice/record/sample?speaker=' + encodeURIComponent(speaker()) + '&label=' + label() + '&sat=browser' + (typed ? '&name=' + encodeURIComponent(typed) : '');
        const r = await fetch(url, {method: 'POST', headers: Home.authHeaders({'Content-Type': 'application/octet-stream'}), body: pcm.buffer});
        if(!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
      } finally { stream.getTracks().forEach(t => t.stop()); ctx.close(); }
    };
    const record = async () => {
      if(busy || !speaker()){ if(!speaker()) q('msg').textContent = i18n('Pick or type a speaker'); return; }
      busy = true; q('rec').disabled = true; q('msg').textContent = i18n('Recording...');
      try {
        const typed = q('newspk').value.trim();
        const r = q('dev').value === 'browser' ? await fromBrowser()
                : await post('/api/voice/record', {speaker: speaker(), label: label(), device: q('dev').value, name: typed});
        last = r.file || null;
        q('msg').textContent = r.state === 'quiet' ? i18n('Did not hear you, again') : r.state !== 'saved' ? r.state : i18n('Saved') + ' (' + r.secs + ' s)';
        await refresh();
        if(label() === 'negative') hint();
      } catch(e){ q('msg').textContent = e.message || String(e); }
      busy = false; q('rec').disabled = false;
    };
    q('close').onclick = () => Home.closeModal();
    q('rec').onclick = record;
    q('del').onclick = async () => {
      if(!speaker()) return;
      try { const r = await post('/api/voice/record/delete', {speaker: speaker(), label: label()}); q('msg').textContent = r.deleted ? i18n('Deleted') : ''; last = null; await refresh(); }
      catch(e){ q('msg').textContent = e.message || String(e); }
    };
    q('play').onclick = async () => {
      if(!last){ q('msg').textContent = i18n('Record first'); return; }
      try {
        const r = await fetch('/api/voice/record/file?f=' + encodeURIComponent(last), {headers: Home.authHeaders()});
        if(!r.ok) throw new Error('HTTP ' + r.status);
        new Audio(URL.createObjectURL(await r.blob())).play();
      } catch(e){ q('msg').textContent = e.message || String(e); }
    };
    md.querySelectorAll('input[name="lbl"]').forEach(r => { r.onchange = () => { count(); hint(); }; });
    q('spk').onchange = count;
    q('newspk').oninput = count;
    fillSpeakers(); count(); hint();
  }

  // Settings -> Voice: what the voice service (voice-vad) runs with; it reads these every 10 s, nothing restarts
  async function settingsPage(box){
    let c, sats = [], house = {};
    try { [c, sats, house] = await Promise.all([Home.api('/api/voice/config'), Home.api('/api/voice/satellites'), Home.api('/api/house')]); }
    catch(e){ box.textContent = e.message || String(e); return; }
    const row = (label, why, field) => `<div class="row"><span class="rlabel">${esc(i18n(label))}${why ? `<br><span class="bindcaption">${esc(i18n(why))}</span>` : ''}</span>${field}</div>`;
    const tog = (f, on) => `<div class="toggle setting${on ? ' on' : ''}" role="switch" data-c="${f}"></div>`;
    const num = (f, min, max, step) => `<input class="askin" type="number" data-c="${f}" min="${min}" max="${max}" step="${step}" value="${esc(c[f] == null ? '' : c[f])}" style="max-width:7em">`;
    const mics = sats.filter(s => s.mic && !s.phone && s.peer);
    const langName = (window.I18N_NAMES || {})[c.lang] || c.lang;
    const maxDist = w => Math.max(0, Math.min(3, Math.floor((w.length - 1) / 2)));      // as the server: more on a short word wakes on anything
    const shortPrefix = c.wake_prefix && c.wake_prefix.length < 3;
    box.innerHTML = `${c.seeded ? '' : `<div class="bindcaption" data-c="wait">${esc(i18n('Waiting for the voice service: it reports its settings within 10 s of its start. Saving is possible after that.'))}</div>`}
      ${row('Wake word', 'What a phrase starts with on a satellite that needs it', `<input class="askin" data-c="wake" value="${esc(c.wake || '')}">`)}
      ${row('Smart answers', 'A phrase no rule understood goes to the language model; what it suggests is checked before anything acts', tog('llm', c.llm !== false))}
      ${row('Ask before "turn everything off"', 'When the model understood "turn everything off", the house asks first', tog('confirm_all_off', c.confirm_all_off !== false))}
      <div class="bindcaption">${esc(i18n('Answers are in the house language'))}: ${esc(langName)} (${esc(i18n('Settings - General'))})</div>
      <details${shortPrefix ? ' open' : ''}><summary>${esc(i18n('Advanced'))}</summary>
      ${row('How exact', 'How many letters a heard word may differ by and still wake (0 = exactly); a short word allows fewer', `<input type="range" data-c="wake_maxdist" min="0" max="${maxDist(c.wake || '')}" step="1" value="${esc(c.wake_maxdist == null ? 0 : c.wake_maxdist)}"><span data-c="maxdist_out" style="min-width:1.5em;text-align:right"></span>`)}
      ${row('Start of the word', 'A heard word that starts like this also wakes: at least 3 letters; empty = off', `<input class="askin" data-c="wake_prefix" value="${esc(c.wake_prefix || '')}">`)}
      ${shortPrefix ? `<div class="bindcaption" style="color:var(--bad)">${esc(i18n('This start of the word is too short and does not count: make it 3 letters or more, or empty.'))}</div>` : ''}
      ${row('Also heard as', 'How the recogniser also writes the wake word, comma separated; a word ending in * takes any ending', `<textarea class="askin" data-c="wake_variants" rows="2">${esc((c.wake_variants || []).join(', '))}</textarea>`)}
      ${mics.length ? `<div class="bindcaption">${esc(i18n('Needs the wake word in the phrase: tick a satellite that wakes by mistake; the others listen for it themselves.'))}</div>
        ${mics.map(s => row((s.cards || {}).mic || s.name || s.id, '', `<input type="checkbox" data-nw="${esc(s.id)}" ${s.need_wake ? 'checked' : ''}>`)).join('')}` : ''}
      ${row('Model', 'On this computer', `<select class="askin" data-c="llm_model"><option>${esc(c.llm_model || '')}</option></select>`)}
      ${row('Big model', 'On another computer, used while it is on', `<select class="askin" data-c="big_model"><option>${esc(c.big_model || '')}</option></select>`)}
      ${row('Wait for the model (s)', '', num('llm_timeout', 1, 60, 0.5))}
      ${row('A question waits (s)', 'How long the house waits for yes or no', num('ask_ttl', 3, 120, 1))}
      </details>
      <div class="row" style="justify-content:flex-end;gap:6px"><span data-c="msg" class="bindcaption"></span><button class="dlg-btn primary" data-c="save">${esc(i18n('Save'))}</button></div>`;
    const q = f => box.querySelector(`[data-c="${f}"]`);
    const out = () => { q('maxdist_out').textContent = q('wake_maxdist').value; };
    q('wake_maxdist').oninput = out; out();
    q('wake').oninput = () => { q('wake_maxdist').max = maxDist(q('wake').value.trim()); out(); };   // the range clamps its value itself
    q('save').disabled = !c.seeded;
    // before the seed: ask every 3 s while the page is shown; when it comes, the fields nobody touched take its values, Save comes on
    const touched = new Set();
    const touch = e => { const f = e.target.closest('[data-c]'); if(f) touched.add(f.dataset.c); };
    box.addEventListener('input', touch);                                  // typed, slid or picked: an edit
    box.addEventListener('click', e => { if(e.target.closest('.toggle[data-c]')) touch(e); });      // a click is an edit only on a toggle
    const setF = (f, v) => {
      const el = q(f);
      if(!el || touched.has(f) || v == null) return;
      if(el.classList.contains('toggle')) el.classList.toggle('on', v !== false);
      else if(el.tagName === 'SELECT'){ if(![...el.options].some(o => o.value === v)) el.add(new Option(v, v)); el.value = v; }
      else el.value = Array.isArray(v) ? v.join(', ') : v;
    };
    const poll = async () => {
      if(!box.isConnected || box.offsetParent === null) return;           // the page was closed or left: no more asking
      let n = null;
      try { n = await Home.api('/api/voice/config'); } catch(e){ /* asked again below */ }
      if(!n || !n.seeded){ setTimeout(poll, 3000); return; }
      Object.assign(c, n);
      if(!touched.has('wake')) q('wake_maxdist').max = maxDist(n.wake || '');
      for(const f of ['wake', 'wake_maxdist', 'wake_prefix', 'wake_variants', 'llm', 'llm_model', 'big_model', 'llm_timeout', 'confirm_all_off', 'ask_ttl']) setF(f, n[f]);
      out();
      if(q('wait')) q('wait').remove();
      q('save').disabled = false;
    };
    if(!c.seeded) setTimeout(poll, 3000);
    box.querySelectorAll('.toggle[data-c]').forEach(t => t.onclick = () => t.classList.toggle('on'));
    Home.api('/api/voice/models', {method: 'POST'}).then(m => {           // the installed models; the current one stays listed if it is missing
      for(const [f, list] of [['llm_model', m.small], ['big_model', m.big]]){
        const cur = c[f] || '';
        q(f).innerHTML = [...new Set([cur, ...(list || [])])].map(v => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(v || i18n('none'))}</option>`).join('');
      }
    }).catch(() => { /* the service is down: the current names stay */ });
    q('save').onclick = async () => {
      const n = f => q(f).value === '' ? null : Number(q(f).value);
      const body = {wake: q('wake').value.trim().toLowerCase() || null, wake_maxdist: Number(q('wake_maxdist').value), wake_prefix: q('wake_prefix').value.trim().toLowerCase(),
                    wake_variants: q('wake_variants').value.split(',').map(x => x.trim().toLowerCase()).filter(Boolean),
                    llm: q('llm').classList.contains('on'), llm_model: q('llm_model').value || null, big_model: q('big_model').value || null,
                    llm_timeout: n('llm_timeout'), confirm_all_off: q('confirm_all_off').classList.contains('on'), ask_ttl: n('ask_ttl')};
      try {
        await Home.api('/api/voice/config', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        for(const s of mics){
          const want = box.querySelector(`[data-nw="${s.id}"]`).checked;
          if(want === !!s.need_wake) continue;
          const {id, stats, room_name, cards, ...rec} = s;
          await Home.api('/api/voice/satellites/' + encodeURIComponent(id), {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...rec, need_wake: want})});
          s.need_wake = want;
        }
        q('msg').textContent = i18n('Saved: the voice service takes it within 10 s');
      } catch(e){ q('msg').textContent = e.message || String(e); }
    };
  }

  HomePlugins.register({
    settings: [{subject: 'voice', title: 'Wake word and answers', expl: 'Wake word, smart answers, questions before acting', render: settingsPage}],
    ask: [{open: text => askHouse(text)}],          // the header's Ask the house
    toolbar: [{tab: 'p:voice', label: 'Voice recording', onClick: () => recordVoice(),
      help: {text: 'Records samples of the wake phrase and other words on a speaker or in this browser, to tune the listening.'}}],
    tabs: [{id: 'voice', label: 'Voice / Sound', cats: ['voice'], after: 'button'}],
    modalSections: [{
      match: d => !!d.voice_sat,
      html: () => `<h3>${esc(i18n('Voice settings'))}</h3><div data-voice="box" class="bindcaption">&hellip;</div>`,
      wire(box, d){ fill(box, d); },
    }],
    help: [
      {id: 'voicesat.peer', screen: 'voice-sat', screenName: 'Satellite settings', sel: '[data-voice="box"] [data-f="peer"]', at: 'label', title: 'Audio comes from (address)',
       text: 'The address the satellite\'s audio comes from; the answer goes back to the satellite of that address.'},
      {id: 'voicesat.voice', screen: 'voice-sat', screenName: 'Satellite settings', sel: '[data-voice="box"] [data-f="voice"]', at: 'label', title: 'Answers in voice',
       text: 'The voice its answers are spoken in; automatic: the house\'s voice.'},
      {id: 'voicesat.quiet', screen: 'voice-sat', screenName: 'Satellite settings', sel: '[data-voice="box"] [data-f="quiet_fail"]', at: 'label', title: 'Silent when not understood',
       text: 'A question the house asks is still said.'},
      {id: 'voicesat.save', screen: 'voice-sat', screenName: 'Satellite settings', sel: '[data-voice="box"] [data-f="save"]', title: 'Save',
       text: 'Saves this satellite; what the form does not show stays as it was.'},
      {id: 'voiceask.text', screen: 'voice-ask', screenName: 'Ask the house', sel: '[data-a="text"]', title: 'What should the house do?',
       text: 'A command or a question, typed as you would say it; Enter sends it. A guess, or a device that needs care, is asked about first.'},
      {id: 'voiceask.send', screen: 'voice-ask', screenName: 'Ask the house', sel: '[data-a="send"]', title: 'Ask', text: 'Sends the phrase to the house.'},
      {id: 'voiceask.speak', screen: 'voice-ask', screenName: 'Ask the house', sel: '[data-a="speak"]', title: 'Say the answer aloud', text: 'The answer is also said on a speaker of the house.'},
      {id: 'voiceask.target', screen: 'voice-ask', screenName: 'Ask the house', sel: '[data-a="target"]', title: 'Where to answer', text: 'The speaker that says the answer.'},
      {id: 'voiceask.yes', screen: 'voice-ask', screenName: 'Ask the house', sel: '[data-a="yes"]', title: 'Yes', text: 'Yes to the house\'s question: then it acts.'},
      {id: 'voiceask.no', screen: 'voice-ask', screenName: 'Ask the house', sel: '[data-a="no"]', title: 'No', text: 'No to the house\'s question: nothing is done.'},
      {id: 'voiceask.close', screen: 'voice-ask', screenName: 'Ask the house', sel: '[data-a="close"]', title: 'Close', text: 'Closes the window; the conversation is not kept.'},
      {id: 'voicerec.spk', screen: 'voice-rec', screenName: 'Voice recording', sel: '[data-a="spk"]', title: 'Speaker', text: 'Whose voice the samples are: they are counted per speaker.'},
      {id: 'voicerec.newspk', screen: 'voice-rec', screenName: 'Voice recording', sel: '[data-a="newspk"]', title: 'New speaker', text: 'A name for a new speaker: while it is typed, it is used instead of the list.'},
      {id: 'voicerec.dev', screen: 'voice-rec', screenName: 'Voice recording', sel: '[data-a="dev"]', title: 'Device', text: 'What records: the microphone of this browser or a satellite.'},
      {id: 'voicerec.label', screen: 'voice-rec', screenName: 'Voice recording', sel: 'input[name="lbl"]', title: 'The wake phrase',
       text: 'Record the wake phrase, or something else (phrases to read aloud are shown): samples of both tune the listening.'},
      {id: 'voicerec.rec', screen: 'voice-rec', screenName: 'Voice recording', sel: '[data-a="rec"]', title: 'Record',
       text: 'A beep, then a short recording; a silent one is not kept. The counter shows the samples of this kind and how many are wanted.'},
      {id: 'voicerec.play', screen: 'voice-rec', screenName: 'Voice recording', sel: '[data-a="play"]', title: 'Play back', text: 'Plays the last sample.'},
      {id: 'voicerec.del', screen: 'voice-rec', screenName: 'Voice recording', sel: '[data-a="del"]', title: 'Delete the last', text: 'Deletes the last sample of this speaker and kind.'},
      {id: 'voicerec.close', screen: 'voice-rec', screenName: 'Voice recording', sel: '[data-a="close"]', title: 'Close', text: 'Closes the window; the samples are kept.'},
      {id: 'voiceset.wake', screen: 'settings:voice', sel: '[data-c="wake"]', at: 'label', title: 'Wake word', text: 'Saved in lower case. A satellite that wakes on the device by its own word does not use it.'},
      {id: 'voiceset.maxdist', screen: 'settings:voice', sel: '[data-c="wake_maxdist"]', at: 'label', title: 'How exact',
       text: 'The number beside it is the value; a wake word of 7 letters or more allows up to 3.'},
      {id: 'voiceset.prefix', screen: 'settings:voice', sel: '[data-c="wake_prefix"]', at: 'label', title: 'Start of the word', text: 'For example the first 4 letters of the wake word: a word cut off by the recogniser still wakes.'},
      {id: 'voiceset.variants', screen: 'settings:voice', sel: '[data-c="wake_variants"]', at: 'label', title: 'Also heard as',
       text: 'Other spellings the recogniser gives the wake word, comma separated; a word ending in * takes any ending.'},
      {id: 'voiceset.needwake', screen: 'settings:voice', sel: '[data-nw]', at: 'label', title: 'Needs the wake word',
       text: 'A ticked satellite acts only on a phrase that holds the wake word: for one that wakes by mistake.'},
      {id: 'voiceset.llm', screen: 'settings:voice', sel: '[data-c="llm"]', at: 'label', title: 'Smart answers',
       text: 'A phrase no rule understood goes to the language model; what it suggests is checked before anything acts.'},
      {id: 'voiceset.model', screen: 'settings:voice', sel: '[data-c="llm_model"]', at: 'label', title: 'Model', text: 'The list is what the voice service has installed; the current model stays listed even when it is missing.'},
      {id: 'voiceset.bigmodel', screen: 'settings:voice', sel: '[data-c="big_model"]', at: 'label', title: 'Big model', text: 'While that computer is off, the model on this computer answers instead.'},
      {id: 'voiceset.timeout', screen: 'settings:voice', sel: '[data-c="llm_timeout"]', at: 'label', title: 'Wait for the model (s)', text: 'How long the house waits for the model\'s answer.'},
      {id: 'voiceset.alloff', screen: 'settings:voice', sel: '[data-c="confirm_all_off"]', at: 'label', title: 'Ask before "turn everything off"',
       text: 'When the model understood "turn everything off", the house asks first.'},
      {id: 'voiceset.askttl', screen: 'settings:voice', sel: '[data-c="ask_ttl"]', at: 'label', title: 'A question waits (s)', text: 'After it the question lapses and nothing is done.'},
      {id: 'voiceset.save', screen: 'settings:voice', sel: '[data-c="save"]', title: 'Save', text: 'The voice service takes the settings within 10 s; nothing restarts.'},
    ],
  });
})();
