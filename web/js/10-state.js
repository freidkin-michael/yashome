const $ = s => document.querySelector(s);
let DEVICES = [];
let BY_ID   = {};
let STATE   = {};
let pending = {};   // local-applied values vs WS
let optimistic = {}; // id+'/'+code -> {value, expires} -- preserved against WS revert
let openModalId = null;
const OPT_HOLD_MS = 4000;
// docs/device-state.md: live, waiting (after a start or an outage), offline. What a card shows changes
// only once the new state held 3 s: a flapping device does not flicker.
const _life = {};
function lifeOf(id){
  const st = STATE[id];
  const noData = st && st.online && !st.last_seen && !Object.keys(st.values || {}).length;   // online by contract, nothing heard yet
  const real = !st ? 'waiting' : st.offline_since ? 'offline' : (st.waiting || noData) ? 'waiting' : st.online ? 'live' : 'offline';
  const l = _life[id] || (_life[id] = {now: real, since: 0, shown: real});
  if(l.now !== real){
    l.now = real; l.since = Date.now();
    clearTimeout(l.timer);                      // no frame may come at 3 s: look again then
    if(real !== 'live') l.timer = setTimeout(() => { updateCards(); if(openModalId === id) updateModal(); }, 3050);
  }
  if(real === 'live') l.shown = real;           // fresh data shows at once; only going away waits 3 s
  else if(l.shown !== real && Date.now() - l.since >= 3000) l.shown = real;
  return l.shown;
}
const DEVOFF_ACTS = '[data-act="toggle"], [data-act="set_bool"], [data-act="bright"], [data-act="set_enum"], [data-act="set_int"]';
function hhmm(sec){ return sec ? new Date(sec * 1000).toTimeString().slice(0, 5) : ''; }
// Commands to a confirm channel (a dimmer, its switch, a lock): nothing is shown as done until the
// device says so. The core lists them in the status (cmd_wait) until an echo, a settled other value
// or 5 s; here: what this screen sent, to say "No answer" when the wait ends without it.
const _sentWait = {};      // id/code -> {v: raw value sent, v0: the value then, t: ms, hold: ms the wait may take}
function waitsNow(d, code){
  const k = d.id + '/' + code, w = STATE[d.id]?.cmd_wait, s = _sentWait[k];
  if(s && Date.now() - s.t >= s.hold) return false;     // a stale frame's cmd_wait does not outlive it
  if(w && code in w) return true;
  if(!s) return false;
  const got = STATE[d.id]?.values?.[code];
  // the first frame may come late; past the core's 5 s a plug-in's holds (a TV: up to 60 s) wait on,
  // but only while the channel reported nothing since the send
  return Date.now() - s.t < 1500 || (got === s.v0 && got !== s.v);
}
// without frames (the WebSocket down) the wait still ends: the pulse stops and the toast comes
function expireWaitLater(id, code, hold){
  setTimeout(() => {
    const d = BY_ID[id], w = STATE[id]?.cmd_wait;
    if(w && code in w) delete w[code];          // the frame that listed it is old now
    if(d){ settleWaits(d); updateCards(); }
  }, hold + 250);
}
function settleWaits(d){
  for(const k of Object.keys(_sentWait)){
    if(!k.startsWith(d.id + '/')) continue;
    const code = k.slice(d.id.length + 1);
    if(waitsNow(d, code)) continue;
    const sent = _sentWait[k], got = STATE[d.id]?.values?.[code];
    delete _sentWait[k];
    delete pending[k];
    const ctl = (d.controls || []).find(c => c.code === code) || {};
    const tol = ctl.kind === 'bright' ? Math.max(1, ((ctl.max ?? 100) - (ctl.min ?? 0)) * 0.02) : 0;
    const same = typeof sent.v === 'number' && typeof got === 'number' ? Math.abs(sent.v - got) <= tol : sent.v === got;
    // a device that reported another value answered: the control shows it, no toast
    if(!same && got === sent.v0 && Date.now() - sent.t >= Math.min(4500, sent.hold - 500))
      notifyFail(`${i18n('No answer from')} ${d.name}`);
  }
}

