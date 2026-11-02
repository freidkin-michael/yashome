// One bearer token gates the API. Entered once per device, kept in
// localStorage, sent on every request; a 401 clears it and asks again.
const TOKEN_KEY = 'hs_token';
// ?token=... once (kiosks, screenshots, first visit from a link) and ?tab=... as a deep link;
// both leave the address bar so they are not bookmarked or shared by accident
const _qs = new URLSearchParams(location.search);
if(_qs.get('token')){ localStorage.setItem(TOKEN_KEY, _qs.get('token').trim()); }
if(_qs.get('tab')){ rememberTab(_qs.get('tab')); }
if(_qs.has('token') || _qs.has('tab') || _qs.has('lang')){ _qs.delete('token'); _qs.delete('tab'); _qs.delete('lang'); history.replaceState(null, '', location.pathname + (_qs.toString() ? '?' + _qs : '') + location.hash); }   // the hash stays: #settings/...
let TOKEN = localStorage.getItem(TOKEN_KEY) || '';
// a one-time sign-in code from a signed-in screen (Settings -> Sign in from another screen), typed at the prompt
async function signinWithCode(code){
  try {
    const r = await fetch('/api/signin', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                          body: JSON.stringify({code: code})});
    if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
    TOKEN = (await r.json()).token; localStorage.setItem(TOKEN_KEY, TOKEN);
  } catch(e){
    const out = $('#si-err');
    if(out){ out.textContent = i18n('Sign-in code: ') + (e.message || e); return; }
    alert(i18n('Sign-in code: ') + (e.message || e));
  }
  location.reload();
}
function authHeaders(h){ return Object.assign({}, h || {}, TOKEN ? {'Authorization': 'Bearer ' + TOKEN} : {}); }
function withToken(url){ return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN); }
// The front door: a card with one field for the 6-digit code (or the token) instead of a bare 401
function askToken(msg){
  if(window.RESTRICTED){ location.replace('/try'); return; }   // the public demo signs in on its own page (the demo plug-in)
  document.body.classList.add('signedout');
  const conn = $('#conn'); if(conn){ conn.textContent = i18n('not signed in'); conn.classList.remove('live'); }
  $('#app').innerHTML = `<div class="signin"><div class="signin-card">
      <h2>${i18n('Sign in to the house')}</h2>
      <p>${i18n('On a screen that is already signed in, open Settings -> Sign in from another screen: it shows a 6-digit code. Type it here (or the house access token).')}</p>
      <input id="si-code" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${i18n('6-digit code or token')}">
      <button class="dlg-btn primary" id="si-go">${i18n('Sign in')}</button>
      <div class="signin-err" id="si-err">${msg ? escapeHtml(msg) : ''}</div>
      <p>${i18n('No code? Ask whoever runs this house to show it from a signed-in screen.')}</p>
    </div></div>`;
  const inp = $('#si-code'), go = () => {
    const t = inp.value.trim();
    if(!t){ inp.focus(); return; }
    if(/^\d{6}$/.test(t)) return signinWithCode(t);
    TOKEN = t; localStorage.setItem(TOKEN_KEY, TOKEN); location.reload();
  };
  $('#si-go').onclick = go;
  inp.onkeydown = e => { if(e.key === 'Enter') go(); };
  inp.focus();
}
function logout(){ localStorage.removeItem(TOKEN_KEY); TOKEN = ''; location.reload(); }
// no eager prompt: behind a proxy that injects the header nobody is asked,
// so we only ask when the API actually answers 401 (see api() below).
let _focusBeforeModal = null;   // where the focus was before a modal took it
let _tokenAsked = false;   // one prompt per page: parallel 401s must not ask (or wipe) again
async function api(path, opts){
  opts = Object.assign({}, opts || {}); opts.headers = authHeaders(opts.headers);
  const sent = TOKEN;
  const r = await fetch(path, opts);
  if(r.status === 401){
    // only forget the token this request carried, never one typed in meanwhile
    if(localStorage.getItem(TOKEN_KEY) === sent) localStorage.removeItem(TOKEN_KEY);
    if(!_tokenAsked){ _tokenAsked = true; askToken(sent ? i18n('Token rejected \u2014 enter it again') : ''); }
    throw new Error('401');
  }
  if(!r.ok){
    const body = await r.text(), err = new Error(apiErrorText(body, r.status));
    err.status = r.status;
    try { const d = JSON.parse(body).detail; if(typeof d === 'string') err.detail = d; } catch(e){ /* not JSON */ }       // the server's own (English) text, for code that tells errors apart
    throw err;
  }
  return r.json();
}
// {"detail": "..."} or pydantic's {"detail": [{msg, loc}, ...]} -> one readable line
// a server message with values in it: match it against the dictionary's {0}/{1} keys
function i18nTemplate(msg){
  const keys = Object.keys(I18N[LANG] || {}).filter(k => /\{\d+\}/.test(k)).sort((a, b) => b.length - a.length);
  for(const k of keys){
    const order = [...k.matchAll(/\{(\d+)\}/g)].map(m => +m[1]);
    const re = new RegExp('^' + k.split(/\{\d+\}/).map(p => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('(.+?)') + '$');
    const m = msg.match(re);
    if(!m) continue;
    const args = [];
    order.forEach((n, i) => { const a = m[i + 1];               // a reason inside is translated too (always a shorter text)
      args[n] = i18n(a) !== a ? i18n(a) : a.length < msg.length ? i18nTemplate(a) : a; });
    return i18n(k, ...args);
  }
  return msg;
}

function apiErrorText(body, status){
  let d;
  // a page instead of JSON (a proxy's own error page, e.g. Cloudflare's for a 502): never shown as text
  const short = () => (body && body.length < 200 && !/<[a-z!]/i.test(body)) ? body : i18n('No answer (HTTP {0})', status);
  try { d = JSON.parse(body).detail; } catch(e) { return short(); }
  if(typeof d === 'string') return i18n(d) !== d ? i18n(d) : i18nTemplate(d);
  if(Array.isArray(d)) return d.map(x => `${(x.loc || []).slice(1).join('.')}: ${i18n(x.msg || '')}`).join('; ');
  return short();
}

