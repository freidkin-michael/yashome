// How every screen of the house shows a temperature and a humidity (steady, not jumping): whole degrees and whole
// percent; a reading keeps what it shows until the value is 0.6 or more away, so 22.5 does not flip between 22 and 23 with
// every report. Only the display: stored values, rules, the history and the API keep every digit. Read by the dashboard,
// the tablets and the panel builder.
(() => {
  const shown = new Map();
  const num = v => typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' && isFinite(+v) ? +v : null;
  const held = (key, v) => {                       // the whole number shown for this reading, moved only past the hysteresis
    const n = key ? shown.get(key) : null;
    if (n != null && Math.abs(v - n) < 0.6) return n;
    const r = Math.round(v);
    if (key) shown.set(key, r);
    return r;
  };
  const temp = (v, key) => {
    const n = num(v);
    if (n == null) return '';
    return String(held(key ? 't:' + key : '', n));
  };
  const hum = (v, key) => { const n = num(v); return n == null ? '' : String(held(key ? 'h:' + key : '', n)); };
  // a channel's code -> 'temp' (a measured temperature, not an AC's set point), 'hum' or ''
  const kind = code => /humid/i.test(code || '') ? 'hum' : /temperature|room_temp|^temp_?(in|out|room)/i.test(code || '') ? 'temp' : '';
  const reading = (code, v, key) => { const k = kind(code); return k === 'temp' ? temp(v, key) : k === 'hum' ? hum(v, key) : null; };
  window.YDisp = {temp, hum, kind, reading};
})();
