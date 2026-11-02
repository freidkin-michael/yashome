// Shared renderer of the complex tiles: weather, air conditioner, remote. Loaded by the
// panel view (live data) and by the builder (sample data), so the preview matches the
// wall. Styles inside, so it is one file.
(function () {
// the page's T() (set as window.T after i18n.json loads); called at render time, not at load
const i18n = (s, ...a) => (window.T ? window.T(s, ...a) : s);
// user text (labels, titles, button names) goes into innerHTML only through esc()
const esc = t => String(t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const CSS = `  /* Weather: one tile, icons are SVG with CSS animation (rays, clouds, drops, snow, lightning). */
  .wx { display:flex; flex-direction:column; justify-content:space-evenly; overflow:hidden; border-radius:12px; padding:3% 4%; box-sizing:border-box; color:var(--fg); align-items:stretch; }
  .wx .now { display:flex; align-items:center; gap:.5em; }
  .wx .ico { flex:none; } .wx .ico svg { width:100%; height:100%; display:block; overflow:visible; }
  .wx .big { font-weight:700; line-height:1; letter-spacing:-.03em; flex:none; }
  .wx .desc { display:flex; flex-direction:column; gap:.2em; min-width:0; flex:1; }
  .wx .cond { font-weight:500; font-size:.8em; line-height:1.1; overflow:hidden; }
  .wx .descrow .desc { flex-direction:row; align-items:baseline; gap:.6em; flex-wrap:wrap; }
  .wx .descrow .cond { font-size:.9em; }
  .wx .sub { color:#8A93A3; font-size:.72em; line-height:1.15; overflow:hidden; }
  .wx .strip { display:flex; justify-content:space-between; border-top:1px solid rgba(255,255,255,.09); padding-top:.45em; margin-top:.2em; }
  .wx .cell { display:flex; flex-direction:column; align-items:center; flex:1; min-width:0; line-height:1.15; }
  .wx .cell > * + * { margin-top:.12em; }
  .wx .cell .k { color:#8A93A3; } .wx .cell .p { color:#6FB5FF; } .wx .cell .lo { color:#8A93A3; font-weight:400; }
  .wx .cell .ico { width:var(--cico, 2em); height:var(--cico, 2em); }
  .wx .none { color:#8A93A3; font-size:.8em; text-align:center; width:100%; }
  .wx svg * { transform-box:fill-box; transform-origin:center; }
  /* Endless icon animations load weak phones heavily; the page turns them off
     with the "lite" class. */
  @keyframes wxspin { to { transform:rotate(360deg); } }
  @keyframes wxsway { from { transform:translateX(-3px); } to { transform:translateX(3px); } }
  @keyframes wxfall { 0% { transform:translateY(0); opacity:0; } 20% { opacity:1; } 100% { transform:translateY(18px); opacity:0; } }
  @keyframes wxflake { 0% { transform:translate(0,0); opacity:0; } 20% { opacity:1; } 100% { transform:translate(5px,18px); opacity:0; } }
  @keyframes wxflash { 0%,86%,100% { opacity:0; } 88%,93% { opacity:1; } 90% { opacity:.3; } }
  @keyframes wxtwinkle { 0%,100% { opacity:.25; } 50% { opacity:1; } }
  @keyframes wxfog { from { transform:translateX(-5px); } to { transform:translateX(5px); } }
  @keyframes wxbreathe { from { transform:scale(.96); } to { transform:scale(1.04); } }
  .wx .rays { animation:wxspin 40s linear infinite; }
  .wx .disc { animation:wxbreathe 4s ease-in-out infinite alternate; }
  .wx .cl { animation:wxsway 6s ease-in-out infinite alternate; }
  .wx .cl2 { animation:wxsway 8s ease-in-out infinite alternate-reverse; }
  .wx .drop { animation:wxfall 1.4s linear infinite; }
  .wx .flake { animation:wxflake 2.8s linear infinite; }
  .wx .bolt { animation:wxflash 5s linear infinite; }
  .wx .star { animation:wxtwinkle 2.6s ease-in-out infinite; }
  .wx .fogl { animation:wxfog 7s ease-in-out infinite alternate; }
  /* Air conditioner: header, power/slider/degrees row, mode and fan rows. */
  .ac { display:flex; flex-direction:column; justify-content:space-evenly; gap:.35em; padding:3% 4%; box-sizing:border-box; border-radius:12px; color:var(--fg); overflow:hidden; align-items:stretch; }
  .ac .hd { display:flex; justify-content:space-between; font-size:.9em; }
  .ac .hd .nm { font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .ac .hd .rm { color:#8A93A3; white-space:nowrap; margin-left:.5em; }
  .ac .tr { display:flex; align-items:center; gap:.2em; }
  .ac .tv { font-weight:700; font-size:2em; line-height:1; flex:none; text-align:right; }
  .ac button { font:inherit; font-size:.62em; color:var(--fg); background:#2E3746; border:0; border-radius:10px; padding:0 .6em; height:1.9em; }
  .ac button.on { background:#177A75; color:#FFFFFF; box-shadow:inset 0 0 0 .07em #3CC2B8; }
  .ac .pwrow { display:flex; gap:.4em; }
  .ac .pwrow { flex:0 1 auto; }
  .ac .pwrow button + button, .ac .row .chip + .chip { margin-left:.35em; }   /* gap: old WebViews do not know it */
  .ac .pwrow button { flex:1; height:var(--btnh, 2.1em); font-size:.75em; font-weight:500; display:flex; align-items:center; justify-content:center; }
  .ac .pwrow button svg { height:1.7em; width:1.7em; display:block; }
  .ac .pwrow .pw { flex:0 0 auto; width:var(--btnh, 2.1em); background:transparent; color:#9AA3B2; box-shadow:inset 0 0 0 .08em #5A6578; }
  .ac .pwrow .pw.on { background:#177A75; color:#FFFFFF; box-shadow:inset 0 0 0 .07em #3CC2B8; }
  .ac .pwrow .pw.wait { opacity:.5; animation:acwait 1s ease-in-out infinite; }     /* sent, the device has not said yet */
  @keyframes acwait { 50% { opacity:.25; } }
  @media (prefers-reduced-motion: reduce) { .ac .pwrow .pw.wait { animation:none; } }
  /* the slider is drawn by hand: track, fill, knob; the real <input type=range> lies on top, transparent,
     and catches the finger -- range pseudo-element styles were not applied on the kiosk WebViews */
  .ac .slw { position:relative; flex:1; min-width:0; height:2em; }
  .ac .trk { position:absolute; left:.3em; right:.6em; top:50%; height:.22em; margin-top:-.11em; border-radius:.11em; background:#3A4556; }
  .ac .trk .fill { position:absolute; left:0; top:0; bottom:0; width:0; border-radius:.225em; background:#3CC2B8; }
  .ac .trk .thumb { position:absolute; top:50%; left:0; width:.9em; height:.9em; margin:-.45em 0 0 -.45em; border-radius:50%; background:#3CC2B8; }
  .ac .sl { position:absolute; left:0; top:0; width:100%; height:100%; margin:0; opacity:0; }
  .ac .row { display:flex; gap:.3em; }
  .ac .row .chip { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; padding:0 .15em; height:var(--btnh, 1.9em); display:flex; align-items:center; justify-content:center; }
  .ac .row .chip svg { height:1.5em; width:1.5em; display:block; }
  .ac .row .chip.on svg [stroke]:not([stroke=none]) { stroke:currentColor; } .ac .row .chip.on svg [fill]:not([fill=none]) { fill:currentColor; }
  .ac.off .trk, .ac.off .tv { opacity:.55; }`;
const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
const DOW_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
// ---- weather (Open-Meteo via /api/weather) -------------------------------------------
// The tile survives re-renders (class keep): render() runs every second for the clock,
// and a re-created SVG would restart the animation -- the drops would twitch.
// English keys, translated with i18n() where they are shown
const WX_TXT = {0:'Clear',1:'Mainly clear',2:'Partly cloudy',3:'Overcast',45:'Fog',48:'Rime fog',
  51:'Light drizzle',53:'Drizzle',55:'Dense drizzle',56:'Freezing drizzle',57:'Freezing drizzle',
  61:'Light rain',63:'Rain',65:'Heavy rain',66:'Freezing rain',67:'Freezing rain',
  71:'Light snow',73:'Snow',75:'Heavy snow',77:'Snow grains',80:'Rain showers',81:'Rain showers',82:'Violent rain showers',
  85:'Snow showers',86:'Heavy snow showers',95:'Thunderstorm',96:'Thunderstorm with hail',99:'Thunderstorm with hail'};
function wxKind(code, day) {
  if (code === 0 || code === 1) return day ? 'sun' : 'moon';
  if (code === 2) return day ? 'psun' : 'pmoon';
  if (code === 3) return 'cloud';
  if (code === 45 || code === 48) return 'fog';
  if (code >= 51 && code <= 57) return 'drizzle';
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return 'rain';
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return 'snow';
  if (code >= 95) return 'storm';
  return 'cloud';
}
const WX_CLOUD = 'M30 72a14 14 0 0 1-2-27.8A20 20 0 0 1 66 40a12 12 0 0 1 8 32z';
function wxSun(cx, cy, r) {
  let rays = '';
  for (let i = 0; i < 8; i++) {
    const a = i * Math.PI / 4, x1 = cx + Math.cos(a) * (r + 5), y1 = cy + Math.sin(a) * (r + 5);
    const x2 = cx + Math.cos(a) * (r + 12), y2 = cy + Math.sin(a) * (r + 12);
    rays += '<line x1="' + x1.toFixed(1) + '" y1="' + y1.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + y2.toFixed(1)
          + '" stroke="#FFD35C" stroke-width="4" stroke-linecap="round"/>';
  }
  return '<g class="rays">' + rays + '</g><circle class="disc" cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="#FFD35C"/>';
}
function wxMoon(cx, cy, r) {
  return '<circle class="star" cx="' + (cx - r - 6) + '" cy="' + (cy - r) + '" r="2" fill="#F2E9C8"/>'
       + '<circle class="star" style="animation-delay:.9s" cx="' + (cx + r + 4) + '" cy="' + (cy - r - 6) + '" r="1.6" fill="#F2E9C8"/>'
       + '<circle class="star" style="animation-delay:1.7s" cx="' + (cx + r + 9) + '" cy="' + (cy + 4) + '" r="1.3" fill="#F2E9C8"/>'
       + '<path class="disc" fill="#F2E9C8" d="M' + cx + ' ' + (cy - r) + 'A' + r + ' ' + r + ' 0 1 0 ' + cx + ' ' + (cy + r)
       + 'A' + (r * 1.25) + ' ' + (r * 1.25) + ' 0 0 1 ' + cx + ' ' + (cy - r) + 'z"/>';
}
function wxDrops(n, cls, col, w) {
  let s = '';
  for (let i = 0; i < n; i++) {
    const x = 32 + i * (40 / Math.max(1, n - 1));
    s += '<line class="' + cls + '" style="animation-delay:' + (i * .35).toFixed(2) + 's" x1="' + x + '" y1="74" x2="' + (x - 3)
       + '" y2="' + (74 + w) + '" stroke="' + col + '" stroke-width="' + (w > 6 ? 4 : 3) + '" stroke-linecap="round"/>';
  }
  return s;
}
function wxFlakes(n) {
  let s = '';
  for (let i = 0; i < n; i++) {
    const x = 33 + i * (36 / Math.max(1, n - 1));
    s += '<circle class="flake" style="animation-delay:' + (i * .6).toFixed(2) + 's" cx="' + x + '" cy="76" r="3" fill="#E8F1FF"/>';
  }
  return s;
}
function wxSvg(kind) {
  let body = '';
  const cloud = (col, cls) => '<path class="' + (cls || 'cl') + '" d="' + WX_CLOUD + '" fill="' + col + '"/>';
  switch (kind) {
    case 'sun':   body = wxSun(50, 50, 20); break;
    case 'moon':  body = wxMoon(52, 50, 20); break;
    case 'psun':  body = wxSun(34, 34, 14) + cloud('#DCE3EC'); break;
    case 'pmoon': body = wxMoon(36, 34, 13) + cloud('#C9D2DE'); break;
    case 'cloud': body = '<path class="cl2" d="' + WX_CLOUD + '" fill="#9AA5B5" transform="translate(12,-14) scale(.8)"/>' + cloud('#DCE3EC'); break;
    case 'fog':   body = cloud('#B9C3D0') + [0, 1, 2].map(i => '<line class="fogl" style="animation-delay:' + i * .8 + 's" x1="24" y1="' + (80 + i * 7)
                       + '" x2="76" y2="' + (80 + i * 7) + '" stroke="#DCE3EC" stroke-width="3.5" stroke-linecap="round" opacity=".8"/>').join(''); break;
    case 'drizzle': body = cloud('#B9C3D0') + wxDrops(4, 'drop', '#6FB5FF', 5); break;
    case 'rain':  body = cloud('#9AA5B5') + wxDrops(4, 'drop', '#6FB5FF', 9); break;
    case 'snow':  body = cloud('#C9D2DE') + wxFlakes(4); break;
    case 'storm': body = cloud('#6F7A8A') + wxDrops(3, 'drop', '#6FB5FF', 7)
                       + '<path class="bolt" d="M52 58 40 80h10l-3 16 14-22h-9l6-16z" fill="#FFE066"/>'; break;
    default:      body = cloud('#DCE3EC');
  }
  return '<svg viewBox="0 0 100 100">' + body + '</svg>';
}
function wxMode(w) {
  const m = w.mode || 'auto';
  if (m !== 'auto') return m;
  if (w.h >= 260 && w.w >= 340) return 'both';
  if (w.h >= 170) return 'hours';
  return 'now';
}
// font size from the tile height per mode (more strips, smaller), but no wider than
// the width allows, so a narrow tall tile does not clip the labels
const wxFs = w => Math.min(w.h / ({now: 7, hours: 11, days: 11, both: 15.5}[wxMode(w)] || 11), w.w / 16);
function wxHtml(w, WX, WX_ERR) {
  const mode = wxMode(w);
  const fs = wxFs(w);
  if (!WX || !WX.current) return '<div class="none">' + (WX_ERR ? esc(i18n('weather: {0}', WX_ERR)) : i18n('weather: waiting for data') + '\u2026') + '</div>';
  const c = WX.current, day = c.is_day !== 0;
  const t = YDisp.temp(c.temperature_2m, 'wx:now'), feels = YDisp.temp(c.apparent_temperature, 'wx:feels');
  const sub = [i18n('feels like {0}', feels + '\u00B0'), c.relative_humidity_2m != null ? c.relative_humidity_2m + '%' : '',
               c.wind_speed_10m != null ? Math.round(c.wind_speed_10m) + ' ' + i18n('m/s') : ''].filter(Boolean).join(' \u00B7 ');
  // spare height: icons grow (up to 2x), the rest is handed out by justify-content
  const need = fs * (3.3 + 0.7 + (mode === 'hours' || mode === 'both' ? 4.6 : 0) + (mode === 'days' || mode === 'both' ? 4.6 : 0)) + w.h * 0.06;
  const grow = Math.max(1, Math.min(2, 1 + (w.h - need) / (fs * 9)));
  const bigIco = (3.3 * Math.min(grow, 1.5)).toFixed(2), cIco = (2 * grow).toFixed(2);
  // text to the right of the degrees only if ~8 font sizes of width remain, else a line below
  const descW = w.w * 0.92 - (parseFloat(bigIco) + 2.6 * 0.56 * 3.4 + 1) * fs;
  const desc = '<div class="desc"><div class="cond">' + (WX_TXT[c.weather_code] ? i18n(WX_TXT[c.weather_code]) : '') + '</div><div class="sub">' + sub + '</div></div>';
  let h = '<div class="now" style="--cico:' + cIco + 'em"><div class="ico" style="width:' + bigIco + 'em;height:' + bigIco + 'em">' + wxSvg(wxKind(c.weather_code, day)) + '</div>'
        + '<div class="big" style="font-size:2.6em">' + t + '&deg;</div>'
        + (descW >= 8 * fs ? desc : '') + '</div>'
        + (descW >= 8 * fs ? '' : '<div class="descrow">' + desc + '</div>');
  const H = WX.hourly || {}, D = WX.daily || {};
  if ((mode === 'hours' || mode === 'both') && H.time) {
    const n = Math.max(3, Math.min(8, Math.floor(w.w / (fs * 3.6))));
    const step = Math.max(1, Math.floor(H.time.length / n));
    let cells = '';
    for (let i = 0, k = 0; k < n && i < H.time.length; i += step, k++) {
      const p = H.precipitation_probability ? H.precipitation_probability[i] : null;
      cells += '<div class="cell" style="font-size:.7em"><div class="k">' + String(H.time[i]).slice(11, 16) + '</div>'
             + '<div class="ico">' + wxSvg(wxKind(H.weather_code[i], H.is_day ? H.is_day[i] !== 0 : true)) + '</div>'
             + '<div>' + YDisp.temp(H.temperature_2m[i]) + '&deg;</div>'
             + '<div class="p">' + (p != null && p >= 20 ? p + '%' : '&nbsp;') + '</div></div>';
    }
    h += '<div class="strip" style="--cico:' + cIco + 'em">' + cells + '</div>';
  }
  if ((mode === 'days' || mode === 'both') && D.time) {
    const n = Math.max(3, Math.min(7, Math.floor(w.w / (fs * 4))));
    let cells = '';
    for (let i = 0; i < n && i < D.time.length; i++) {
      const dt = new Date(D.time[i] + 'T12:00:00'), p = D.precipitation_probability_max ? D.precipitation_probability_max[i] : null;
      cells += '<div class="cell" style="font-size:.7em"><div class="k">' + (i === 0 ? i18n('today') : i18n(DOW_SHORT[dt.getDay()])) + '</div>'
             + '<div class="ico">' + wxSvg(wxKind(D.weather_code[i], true)) + '</div>'
             + '<div>' + YDisp.temp(D.temperature_2m_max[i]) + '&deg; <span class="lo">' + YDisp.temp(D.temperature_2m_min[i]) + '&deg;</span></div>'
             + '<div class="p">' + (p != null && p >= 20 ? p + '%' : '&nbsp;') + '</div></div>';
    }
    h += '<div class="strip" style="--cico:' + cIco + 'em">' + cells + '</div>';
  }
  return h;
}
// ---- AC mode/fan icons (channel value -> SVG); no icon: the label stays
const S_ = (body, stroke) => '<svg viewBox="0 0 24 24"' + (stroke ? ' fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"' : ' fill="currentColor"') + '>' + body + '</svg>';
const bars = xs => S_(xs.map(x => '<rect x="' + x + '" y="5" width="3.6" height="14" rx="1.4"/>').join(''));
const AC_ICON = {
  cool: S_('<path stroke="#7CC4FF" d="M12 2v20M2 12h20M4.9 4.9l14.2 14.2M19.1 4.9L4.9 19.1M12 5.5l-2.2-2.2M12 5.5l2.2-2.2M12 18.5l-2.2 2.2M12 18.5l2.2 2.2M5.5 12l-2.2-2.2M5.5 12l-2.2 2.2M18.5 12l2.2-2.2M18.5 12l2.2 2.2"/>', true),
  heat: S_('<path fill="#FF9F43" d="M12 2c.6 3.8 5.5 6.2 5.5 11.2A5.5 5.5 0 0 1 6.5 13.2c0-2.2 1.1-3.8 2.3-5 .1 2 1 3.2 2.2 3.5.6-2.4-.9-5.3 1-9.7z"/>'),
  dry: S_('<path fill="#6FB5FF" d="M12 2.5c3.2 4.6 6.2 8 6.2 11.6a6.2 6.2 0 0 1-12.4 0C5.8 10.5 8.8 7.1 12 2.5z"/>'),
  fan: S_('<ellipse cx="12" cy="6.2" rx="2.5" ry="4.6"/><ellipse cx="12" cy="6.2" rx="2.5" ry="4.6" transform="rotate(120 12 12)"/><ellipse cx="12" cy="6.2" rx="2.5" ry="4.6" transform="rotate(240 12 12)"/><circle cx="12" cy="12" r="2.2"/>'),
  auto: S_('<circle cx="12" cy="12" r="9.3"/><path d="M8.6 16.2 12 7.8l3.4 8.4M9.7 13.5h4.6"/>', true),
  low: bars([10.2]), med: bars([7.2, 13.2]), max: bars([4.2, 10.2, 16.2]),
  on: S_('<path d="M12 3v9M7.2 6.6a7.5 7.5 0 1 0 9.6 0"/>', true),      // IEC "power"
  off: S_('<circle cx="12" cy="12" r="8"/>', true),                        // IEC "off"
};
const AC_ALIAS = {medium: 'med', mid: 'med', middle: 'med', high: 'max', min: 'low'};      // other names a firmware gives the fan speeds
const acIcon = v => AC_ICON[String(v)] || AC_ICON[AC_ALIAS[String(v)]] || '';
// ---- IR remote button icons: by button id (ir_<id>) or by its label ------
const dot = c => S_('<circle cx="12" cy="12" r="7" fill="' + c + '" stroke="none"/>');
const IR_ICON = {
  power: AC_ICON.on, on: S_('<path d="M12 4v16"/>', true), off: AC_ICON.off,      // IEC 5009 power, 5007 on (a bar), 5008 off
  vol_up: S_('<path d="M4 10v4h3l4 3V7L7 10z"/><path stroke="#6FB5FF" d="M15 9.5a3.5 3.5 0 0 1 0 5M17.5 7a7 7 0 0 1 0 10"/>', true),
  vol_down: S_('<path d="M4 10v4h3l4 3V7L7 10z"/><path stroke="#6FB5FF" d="M15 9.5a3.5 3.5 0 0 1 0 5"/>', true),
  mute: S_('<path d="M4 10v4h3l4 3V7L7 10z"/><path stroke="#FF6B6B" d="M15 9l6 6M21 9l-6 6"/>', true),
  ch_up: S_('<path d="M6 15l6-6 6 6"/>', true), ch_down: S_('<path d="M6 9l6 6 6-6"/>', true),
  up: S_('<path d="M12 19V5M5 12l7-7 7 7"/>', true), down: S_('<path d="M12 5v14M5 12l7 7 7-7"/>', true),
  left: S_('<path d="M19 12H5M12 5l-7 7 7 7"/>', true), right: S_('<path d="M5 12h14M12 5l7 7-7 7"/>', true),
  ok: S_('<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3" fill="currentColor"/>', true),
  back: S_('<path d="M9 14 4 9l5-5M4 9h9a6 6 0 0 1 0 12H8"/>', true),
  home: S_('<path d="M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10"/>', true),
  menu: S_('<path d="M4 7h16M4 12h16M4 17h16"/>', true),
  info: S_('<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>', true),
  exit: S_('<path d="M14 4h6v16h-6M10 8l-4 4 4 4M6 12h10"/>', true),
  source: S_('<path d="M8 3v5M16 3v5M5 8h14v3a7 7 0 0 1-14 0zM12 18v3"/>', true), hdmi: S_('<path d="M2 9h20v5l-2 2H4l-2-2zM6 12h12"/>', true),
  playpause: S_('<path fill="#3CC2B8" d="M2 5l10 7-10 7z"/><path d="M14 5h3v14h-3zM19.5 5h3v14h-3z"/>'),
  play: S_('<path fill="#3CC2B8" d="M7 4l13 8-13 8z"/>'), pause: S_('<path d="M6 4h4v16H6zM14 4h4v16h-4z"/>'), stop: S_('<path fill="#FF6B6B" d="M5 5h14v14H5z"/>'),
  prev: S_('<path d="M6 5v14M20 5l-12 7 12 7z"/>'), next: S_('<path d="M18 5v14M4 5l12 7-12 7z"/>'),
  rew: S_('<path d="M12 5l-10 7 10 7zM22 5l-10 7 10 7z"/>'), ff: S_('<path d="M2 5l10 7-10 7zM12 5l10 7-10 7z"/>'),
  del: S_('<path d="M8 5h13v14H8l-5-7zM11 9l6 6M17 9l-6 6"/>', true),
  tools: S_('<path d="M14.5 6.5a4 4 0 0 0 5 5L9 22l-3-3zM14.5 6.5L18 3l3 3-3.5 3.5"/>', true),
  guide: S_('<path d="M4 5h16v14H4zM4 10h16M9 10v9"/>', true),
  red: dot('#E5484D'), green: dot('#46A758'), yellow: dot('#FFC53D'), blue: dot('#3E8DE3'),
  x_bulb: S_("<path d=\"M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z\"/>", true),
  x_sun: S_("<circle cx=\"12\" cy=\"12\" r=\"4\"/><path d=\"M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1\"/>", true),
  x_moon: S_("<path d=\"M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z\"/>", true),
  x_thermo: S_("<path d=\"M10 14V5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0z\"/>", true),
  x_swing: S_("<path d=\"M4 8c3-3 5 3 8 0s5 3 8 0M4 15c3-3 5 3 8 0s5 3 8 0\"/>", true),
  x_eco: S_("<path d=\"M5 19C5 10 10 5 20 4c0 10-5 15-13 15zM5 19l8-8\"/>", true),
  x_turbo: S_("<path d=\"M13 2L4 14h7l-1 8 9-12h-7z\"/>", true),
  x_timer: S_("<circle cx=\"12\" cy=\"13\" r=\"8\"/><path d=\"M12 9v4l3 2M9 2h6\"/>", true),
  x_star: S_("<path d=\"M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z\"/>", true),
  x_bell: S_("<path d=\"M6 17V11a6 6 0 0 1 12 0v6l2 2H4zM10 21h4\"/>", true),
  x_lock: S_("<rect x=\"5\" y=\"11\" width=\"14\" height=\"10\" rx=\"2\"/><path d=\"M8 11V8a4 4 0 0 1 8 0v3\"/>", true),
  x_wifi: S_("<path d=\"M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5v.1\"/>", true),
  x_gear: S_("<circle cx=\"12\" cy=\"12\" r=\"3\"/><path d=\"M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2\"/>", true),
  x_camera: S_("<path d=\"M4 8h3l2-3h6l2 3h3v11H4z\"/><circle cx=\"12\" cy=\"13\" r=\"3.5\"/>", true),
  x_plus: S_("<path d=\"M12 5v14M5 12h14\"/>", true),
  x_minus: S_("<path d=\"M5 12h14\"/>", true),
  x_refresh: S_("<path d=\"M20 12a8 8 0 1 1-2.5-5.8M20 4v5h-5\"/>", true),
  x_aspect: S_("<path d=\"M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5\"/>", true),
  x_tv: S_("<rect x=\"3\" y=\"5\" width=\"18\" height=\"12\" rx=\"2\"/><path d=\"M8 21h8M12 17v4\"/>", true),
  x_speaker: S_("<rect x=\"6\" y=\"2\" width=\"12\" height=\"20\" rx=\"2\"/><circle cx=\"12\" cy=\"15\" r=\"3\"/><circle cx=\"12\" cy=\"7\" r=\"1\"/>", true),
  x_music: S_("<path d=\"M9 18V5l11-2v13\"/><circle cx=\"6.5\" cy=\"18\" r=\"2.5\"/><circle cx=\"17.5\" cy=\"16\" r=\"2.5\"/>", true),
  x_mic: S_("<rect x=\"9\" y=\"3\" width=\"6\" height=\"11\" rx=\"3\"/><path d=\"M5 11a7 7 0 0 0 14 0M12 18v3\"/>", true),
  x_subs: S_("<rect x=\"3\" y=\"5\" width=\"18\" height=\"14\" rx=\"2\"/><path d=\"M7 12h4M13 12h4M7 15h2M11 15h6\"/>", true),
  x_cool: AC_ICON.cool, x_heat: AC_ICON.heat, x_dry: AC_ICON.dry, x_fan: AC_ICON.fan, x_auto: AC_ICON.auto, x_low: AC_ICON.low, x_med: AC_ICON.med, x_max: AC_ICON.max,
};
// digits and the Roman I, II, III as strokes (no <text>: the app's vector icons keep only shapes), in a 6 x 10 box drawn with M, L and C only
const DIGIT_D = {
  0: 'M3 0C1 0 0 2 0 5C0 8 1 10 3 10C5 10 6 8 6 5C6 2 5 0 3 0Z', 1: 'M1.2 2L3.6 0L3.6 10',
  2: 'M0.3 2.2C0.8 0.6 2 0 3.1 0C4.8 0 5.8 1.2 5.8 2.7C5.8 5.4 0.2 6.8 0.2 10L6 10',
  3: 'M0.4 1.3C1 0.4 2 0 3 0C4.6 0 5.6 1 5.6 2.4C5.6 3.9 4.4 4.8 2.6 4.8C4.6 4.8 5.9 5.8 5.9 7.4C5.9 9 4.6 10 3 10C1.8 10 0.8 9.5 0.2 8.6',
  4: 'M4.6 10L4.6 0L0 7L6 7', 5: 'M5.6 0L1 0L0.6 4.4C1.3 3.9 2.1 3.7 2.9 3.7C4.7 3.7 5.9 4.9 5.9 6.8C5.9 8.8 4.6 10 2.8 10C1.7 10 0.8 9.5 0.2 8.7',
  6: 'M5.3 0.4C4.7 0.1 4.1 0 3.4 0C1.3 0 0 2.4 0 5.9C0 8.6 1.2 10 3 10C4.8 10 6 8.7 6 6.8C6 5 4.8 3.8 3.1 3.8C1.8 3.8 0.6 4.6 0.1 5.8',
  7: 'M0 0L6 0L2.2 10',
  8: 'M3 4.6C1.6 4.6 0.6 3.7 0.6 2.3C0.6 0.9 1.6 0 3 0C4.4 0 5.4 0.9 5.4 2.3C5.4 3.7 4.4 4.6 3 4.6C1.3 4.6 0.2 5.6 0.2 7.3C0.2 9 1.4 10 3 10C4.6 10 5.8 9 5.8 7.3C5.8 5.6 4.7 4.6 3 4.6Z',
  9: 'M0.7 9.6C1.3 9.9 1.9 10 2.6 10C4.7 10 6 7.6 6 4.1C6 1.4 4.8 0 3 0C1.2 0 0 1.3 0 3.2C0 5 1.2 6.2 2.9 6.2C4.2 6.2 5.4 5.4 5.9 4.2',
  // I, II, III as the typeface draws them: the strokes and one bar on top and one at the bottom across them all
  i: 'M3 0L3 10M1.4 0L4.6 0M1.4 10L4.6 10', ii: 'M1.2 0L1.2 10M4.8 0L4.8 10M-0.2 0L6.2 0M-0.2 10L6.2 10',
  iii: 'M-0.6 0L-0.6 10M3 0L3 10M6.6 0L6.6 10M-2 0L8 0M-2 10L8 10',
};
const fitD = (d, x0, y0, k) => { let i = 0; return d.replace(/-?\d*\.?\d+/g, n => +((i++ % 2 ? y0 : x0) + n * k).toFixed(2)); };   // numbers come in x, y pairs
const inBox = (d, k = 1, w = 1.9) => S_('<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path stroke-width="' + w + '" d="' + fitD(d, 12 - 3 * k, 12 - 5 * k, k) + '"/>', true);
const hdmiN = n => S_('<path d="M3 3.5h18V7l-1.5 1.5h-15L3 7zM6.5 5.6h11"/><path stroke-width="1.9" d="' + fitD(DIGIT_D[n], 9.45, 11.6, 0.85) + '"/>', true);
for (const k of Object.keys(DIGIT_D)) IR_ICON[/^\d$/.test(k) ? 'x_d' + k : 'x_' + k] = /^\d$/.test(k) ? inBox(DIGIT_D[k]) : inBox(DIGIT_D[k], 1, 1.6);
for (const n of [1, 2, 3, 4]) IR_ICON['hdmi' + n] = hdmiN(n);
// the icon picker's sets: [set, [icon keys]]
const IR_ICON_SETS = [["power", ["power", "on", "off"]], ["sound", ["vol_up", "vol_down", "mute", "x_music", "x_speaker", "x_mic", "x_subs"]], ["navigation", ["up", "down", "left", "right", "ok", "back", "home", "menu", "info", "exit", "guide", "tools", "ch_up", "ch_down", "x_plus", "x_minus", "x_refresh", "x_aspect"]], ["playback", ["play", "pause", "playpause", "stop", "prev", "next", "rew", "ff"]], ["inputs", ["source", "hdmi", "hdmi1", "hdmi2", "hdmi3", "hdmi4", "x_tv"]], ["colors", ["red", "green", "yellow", "blue"]], ["light", ["x_bulb", "x_sun", "x_moon"]], ["climate", ["x_cool", "x_heat", "x_dry", "x_fan", "x_auto", "x_low", "x_med", "x_max", "x_thermo", "x_swing", "x_eco", "x_turbo", "x_timer"]], ["numbers", ["x_d1", "x_d2", "x_d3", "x_d4", "x_d5", "x_d6", "x_d7", "x_d8", "x_d9", "x_d0", "x_i", "x_ii", "x_iii"]], ["misc", ["x_star", "x_bell", "x_lock", "x_wifi", "x_gear", "x_camera", "del"]]];
// optional label -> slot id tables per language (window.I18N_IR_NAMES), when a page provides them
const IR_BY_NAME = Object.assign({}, ...Object.values(window.I18N_IR_NAMES || {}));
// ids that mean a discrete power key (a remote learned with separate On and Off buttons)
const IR_ID_ALIAS = {power_on: 'on', poweron: 'on', power_off: 'off', poweroff: 'off'};
function irIcon(code, label, icon) {
  if (icon && IR_ICON[icon]) return IR_ICON[icon];   // the one picked when the button was named
  const id = IR_ID_ALIAS[String(code || '').replace(/^ir_/, '')] || String(code || '').replace(/^ir_/, '');
  if (IR_ICON[id]) return IR_ICON[id];
  const k = IR_BY_NAME[String(label || '').trim().toLowerCase()];
  return k ? IR_ICON[k] : '';
}
// ---- air conditioner: markup and applying the state ----------------------------------
function acMarkup(w) {
  const chips = (key, items) => items.map(([v, l]) => '<button class="chip" data-k="' + key + '" data-v="' + esc(v) + '" title="' + esc(l) + '">' + (acIcon(v) || esc(l)) + '</button>').join('');
  return '<div class="hd"><span class="nm">' + esc(w.text || i18n('Air conditioner')) + '</span><span class="rm"></span></div>'
    + '<div class="pwrow"><button class="pw" aria-pressed="false" title="' + esc(i18n('Turn on')) + '">' + acIcon('on') + '</button></div>'
    + '<div class="tr"><div class="slw"><div class="trk"><div class="fill"></div><div class="thumb"></div></div>'
    + '<input class="sl" type="range" min="' + (w.temp_min || 16) + '" max="' + (w.temp_max || 30)
    + '" step="' + (w.temp_step || 1) + '"></div><span class="tv">--&deg;</span></div>'
    + (w.modes && w.modes.length ? '<div class="row">' + chips('mode', w.modes) + '</div>' : '')
    + (w.fans && w.fans.length ? '<div class="row">' + chips('fan', w.fans) + '</div>' : '');
}
const acFs = w => Math.min(w.h / 10.5, w.w / 14);
// knob and fill position from the slider value (called while dragging too)
function acSlide(box) {
  const sl = box.querySelector('.sl'), f = box.querySelector('.trk .fill'), th = box.querySelector('.trk .thumb');
  if (!sl || !f) return;
  const lo = +sl.min, hi = +sl.max, p = hi > lo ? (Math.max(lo, Math.min(hi, +sl.value)) - lo) / (hi - lo) * 100 : 0;
  f.style.width = p + '%'; th.style.left = p + '%';
}
// v = device values {power, temp, mode, fan, room_temp}; dragging: leave the slider alone
function acApply(box, w, v, dragging) {
  // spare height -> taller buttons (up to 1.6x), the rest is handed out by justify-content
  const fs = acFs(w), rows = 3 + (w.modes && w.modes.length ? 1 : 0) + (w.fans && w.fans.length ? 1 : 0);
  const need = fs * (1.1 + 2.1 + 2.2 + (rows - 3) * 1.9 + rows * 0.35) + w.h * 0.06;
  const grow = Math.max(1, Math.min(1.6, 1 + (w.h - need) / (fs * 6)));
  box.style.setProperty('--btnh', (2.1 * grow).toFixed(2) + 'em');
  box.querySelector('.rm').textContent = v.room_temp != null ? YDisp.temp(v.room_temp, 'ac:' + w.id) + '\u00B0 ' + i18n('in the room') : '';
  const pw = box.querySelector('.pw'), want = pw.dataset.want;      // one button: its state, a command on its way
  if (want && (String(v.power === true) === want || Date.now() > +pw.dataset.until)) { delete pw.dataset.want; pw.classList.remove('wait'); }
  pw.classList.toggle('on', v.power === true);
  pw.setAttribute('aria-pressed', String(v.power === true));
  pw.title = i18n(v.power === true ? 'Turn off' : 'Turn on');
  const sl = box.querySelector('.sl'), tv = box.querySelector('.tv');
  if (v.temp != null && !dragging) { sl.value = v.temp; tv.textContent = Math.round(v.temp) + '\u00B0'; }
  acSlide(box);
  for (const b of box.querySelectorAll('.chip')) b.classList.toggle('on', String(v[b.dataset.k]) === b.dataset.v);
  box.classList.toggle('off', !v.power);
}
// Sample data for the preview in the builder
function sampleWeather() {
  const hr = [], dy = [], now = new Date(); now.setMinutes(0, 0, 0);
  const codes = [1, 2, 3, 61, 2, 0, 0, 2];
  for (let i = 0; i < 24; i++) { const t = new Date(now.getTime() + i * 3600000); const h = t.getHours();
    hr.push({time: t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0') + 'T' + String(h).padStart(2, '0') + ':00',
             c: codes[(i / 3 | 0) % 8], d: h >= 6 && h < 19 ? 1 : 0, t: 22 + Math.round(6 * Math.sin((h - 9) / 24 * 2 * Math.PI)), p: i >= 9 && i < 12 ? 40 : 0}); }
  for (let i = 0; i < 7; i++) { const t = new Date(now.getTime() + i * 86400000);
    dy.push({time: t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0'), c: codes[i], hi: 27 + (i % 3), lo: 19 + (i % 2), p: i === 3 ? 60 : 0}); }
  return {fetched_at: 1, current: {temperature_2m: 24, apparent_temperature: 25, relative_humidity_2m: 48, wind_speed_10m: 3, weather_code: 2, is_day: 1},
          hourly: {time: hr.map(x => x.time), weather_code: hr.map(x => x.c), is_day: hr.map(x => x.d), temperature_2m: hr.map(x => x.t), precipitation_probability: hr.map(x => x.p)},
          daily: {time: dy.map(x => x.time), weather_code: dy.map(x => x.c), temperature_2m_max: dy.map(x => x.hi), temperature_2m_min: dy.map(x => x.lo), precipitation_probability_max: dy.map(x => x.p)}};
}
const SAMPLE_AC = {power: true, temp: 22, mode: 'cool', fan: 'auto', room_temp: 24.6};
// ---- IR remote layout "like a real one": slots by id/label, the rest as chips ---------
function irKey(code, label, icon) {
  if (icon && IR_ICON[icon]) return icon;
  const id = IR_ID_ALIAS[String(code || '').replace(/^ir_/, '')] || String(code || '').replace(/^ir_/, '');
  if (IR_ICON[id] || /^d\d$/.test(id) || id === 'power') return id;
  return IR_BY_NAME[String(label || '').trim().toLowerCase()] || (/^\d$/.test(String(label || '').trim()) ? 'd' + String(label).trim() : '');
}
const RC_CSS = `
  /* sizes in em of .rc: 20.8px on the dashboard, in a panel tile from its size */
  /* --u = layout unit in px (= font-size of .rc): em inside var() would be taken from the BUTTON font
     (.72em) and keys would come out lower than rmRowsEm assumes; --kx stretches the keys horizontally */
  .rc { font-size:20.8px; --u:20.8px; --kx:1; --k:calc(var(--u) * 2.6 * var(--kx)); --kh:calc(var(--u) * 2.1); --ko:calc(var(--u) * 2.6); --g:calc(var(--u) * .4);
        display:flex; flex-direction:column; align-items:center; padding:calc(var(--u) * .05) 0 0; }
  /* gaps as margins, not gap: old Android WebViews do not know flex gap */
  .rc > * { margin:calc(var(--u) * .25) 0; }
  .rc .rrow { display:flex; justify-content:center; flex-wrap:wrap; }
  .rc > .rrow { margin:calc(var(--u) * .05) 0; }
  .rc .rrow > * { margin:calc(var(--g) / 2); }
  .rc .grid3 { display:grid; grid-template-columns:repeat(3, var(--k)); gap:var(--g); }
  .rc .rock { align-items:center; }
  .rc .rrow.nw { flex-wrap:nowrap; align-self:stretch; }
  .rc .rrow.nw > .acbtn.k { flex:1 1 0; min-width:0; width:auto; }
  .rc .rock .cap { width:calc(var(--u) * 1.6); text-align:right; font-size:.55em; letter-spacing:.06em; color:#8A93A3; }
  .rc .acbtn.k { width:var(--k); height:var(--kh); padding:0; display:flex; align-items:center; justify-content:center; font-size:.72em; font-weight:500; border-radius:calc(var(--u) * .5); }
  .rc .acbtn.k svg { width:calc(var(--u) * 1.05); height:calc(var(--u) * 1.05); display:block; }
  .rc .acbtn.k.ok { border-radius:calc(var(--u) * 1.3); height:var(--ko); }
  .rc .acbtn.k.pon { color:#3CC2B8; } .rc .acbtn.k.poff { color:#FF6B6B; }
  /* learned On and Off keys: one segmented pair, not two look-alike toggles */
  .rc .rrow.nw > .pair { flex:2 1 0; min-width:0; display:flex; }
  .rc .pair > .acbtn.k { flex:1 1 0; min-width:0; width:auto; margin:0; border-radius:0; }
  .rc .pair > .acbtn.k:first-child { border-radius:calc(var(--u) * .5) 0 0 calc(var(--u) * .5); }
  .rc .pair > .acbtn.k:last-child { border-radius:0 calc(var(--u) * .5) calc(var(--u) * .5) 0; box-shadow:inset .06em 0 0 #0E1116; }
  .rc .acbtn.k.wide { width:calc(var(--k) * 2 + var(--g)); }
  /* media: prev | play-pause (two rows high) | next, rew and ff under prev and next */
  .rc .media2 { display:grid; grid-template-columns:repeat(3, var(--k)); grid-template-rows:repeat(2, var(--kh)); gap:var(--g); }
  .rc .media2 .acbtn.k.tall { height:auto; }
  .rc .media2 .acbtn.k.tall svg { width:calc(var(--u) * 1.5); height:calc(var(--u) * 1.5); }
  .rc .acbtn.chip { height:calc(var(--u) * 1.75); padding:0 calc(var(--u) * .6); display:flex; align-items:center; font-size:.62em; border-radius:calc(var(--u) * .5); }
  .rc .acbtn.chip svg { width:calc(var(--u) * .87); height:calc(var(--u) * .87); margin-right:calc(var(--u) * .3); }
  .rc .sep { width:60%; height:1px; background:rgba(255,255,255,.08); }
  /* the "remote" tile on a panel */
  /* side room as child margins: % padding on an absolute tile counts from the canvas width, not the tile;
     flex:none so a tight fit can never squeeze the title to zero height */
  .rm { display:flex; flex-direction:column; align-items:stretch; justify-content:space-evenly; padding:0; box-sizing:border-box; border-radius:12px; color:var(--fg); overflow:hidden; }
  .rm > * { flex:none; margin-left:3%; margin-right:3%; }
  .rm .ttl { font-weight:500; text-align:center; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-bottom:.2em; }
  .rm .acbtn { background:#2E3746; border:0; color:inherit; }
  .rm .acbtn:active { filter:brightness(1.35); }`;
document.head.appendChild(Object.assign(document.createElement('style'), {textContent: RC_CSS}));
// layout height in em at width Wem (see remoteHtml): sections + .5em gaps between them
function rmRowsEm(buttons, Wem, kx) {
  const keys = {}, rest = [];
  for (const b of buttons) { const k = irKey(b.code, b.label); if (irDropped(k)) continue; if (k && !keys[k]) keys[k] = b; else rest.push(b); }
  const has = (...ks) => ks.some(k => keys[k]);
  const K = 2.6 * (kx || 1), W = Wem * 0.97;
  const perLine = Math.max(1, Math.floor(W / (K + 0.4)));
  const lines = n => Math.ceil(n / perLine);
  // a wrapping row: item widths in em (with their .4 margins) -> number of lines
  const pack = ws => ws.reduce(([n, cur], x) => cur + x > W ? [n + 1, x] : [n, cur + x], [0, Infinity])[0];
  let em = 0, sections = 0;
  const add = h => { em += h; sections++; };
  if (has('power', 'on', 'off', 'source', 'hdmi')) {
    const pw = keys.on || keys.off ? ['on', 'off'].filter(k => keys[k]) : ['power'].filter(k => keys[k]);
    const ex = ['source', 'hdmi'].filter(k => keys[k]), wide = pw.length + ex.length <= 2;
    const n = pack(pw.map(() => wide ? 2 * K + 0.8 : K + 0.4).concat(ex.map(() => K + 0.4)));
    add(n * 2.1 + (n - 1) * 0.4);
  }
  if (has('up', 'down', 'left', 'right', 'ok')) add(2.1 + 2.6 + 2.1 + 0.8);
  if (has('vol_up', 'vol_down', 'mute')) add(2.1);
  if (has('ch_up', 'ch_down')) add(2.1);
  const tallKey = mediaTall(keys), block = tallKey && has('prev', 'next', 'rew', 'ff');
  const media = MEDIA.filter(k => keys[k] && !(block && ['prev', 'next', 'rew', 'ff', tallKey].includes(k))).length;
  if (block) add(2.1 * 2 + 0.4);
  if (media) add(lines(media) * 2.5);
  // chips: whatever has no slot (info and home go to a chip when exit and menu took theirs)
  const slotted = ['power', 'on', 'off', 'source', 'hdmi', 'up', 'down', 'left', 'right', 'ok', 'menu', 'home', 'back', keys.exit ? 'exit' : 'info',
    'vol_up', 'vol_down', 'mute', 'ch_up', 'ch_down', ...MEDIA];
  const chips = rest.concat(Object.keys(keys).filter(k => !slotted.includes(k)).map(k => keys[k]));
  // chip = padding 1.2 + icon 1.17 + label at .62em (~.6em per glyph) + .4 margins
  if (chips.length) add(pack(chips.map(b => IR_ICON[irKey(b.code, b.label)] ? 1.6 + 1.17 : 1.6 + String(b.label).length * 0.62 * 0.6)) * 2.15 + 0.6);
  return em + Math.max(0, sections - 1) * 0.5 + 0.6;
}
// tile font size: at most 9.5 em by width (keypad 3x2.6 + margins); by height so the whole layout fits
function rmFs(w) {
  const bs = rmButtons(w);
  let fs = w.w / 9.5;
  for (let i = 0; i < 3; i++) {
    const em = rmRowsEm(bs, w.w * 0.94 / fs, rmKx(w, fs)) + (w.text ? 1.1 : 0);
    fs = Math.min(w.w / 9.5, w.h * 0.96 / em);
  }
  return Math.max(8, fs);
}
// tall tile: the font size hit the height while width remains, so keys stretch (up to 1.8x),
// but so that the longest row (power, volume, media, colours) stays on one line
function rmKx(w, fs) {
  const Wem = w.w * 0.94 / fs;
  const keys = {};
  for (const b of w.buttons || []) { const k = irKey('ir_' + b.id, b.label); if (k) keys[k] = 1; }
  const rows = [[3, 0]];                                   // [keys per row, fixed width in em]
  const np = (keys.on || keys.off) ? (keys.on ? 1 : 0) + (keys.off ? 1 : 0) : (keys.power ? 1 : 0);
  const nr = np + (keys.source ? 1 : 0) + (keys.hdmi ? 1 : 0);
  if (nr) rows.push([nr <= 2 ? nr + np : nr, 0]);   // power keys are double width in a row of <= 2
  if (keys.vol_up || keys.vol_down || keys.mute) rows.push([2 + (keys.mute ? 1 : 0), 0]);
  if (keys.ch_up || keys.ch_down) rows.push([2, 2.0]);
  const tallKey = mediaTall(keys), block = tallKey && (keys.prev || keys.next || keys.rew || keys.ff);
  const media = MEDIA.filter(k => keys[k] && !(block && ['prev', 'next', 'rew', 'ff', tallKey].includes(k))).length;
  if (block) rows.push([3, 0]);
  if (media) rows.push([media, 0]);
  let kx = 1.8;
  for (const [n, fixed] of rows) kx = Math.min(kx, (Wem * 0.97 - fixed - n * 0.4) / (n * 2.6));   // .2 margins on both sides of a key
  return Math.min(1.8, Math.max(1, kx));
}
// panel tile: title + layout; font size from the tile size (both sides)
function rmHtml(w) {
  const bs = rmButtons(w);
  let body = remoteHtml(bs);
  if (!body) body = '<div class="rc"><div class="rrow">' + bs.filter(b => !irDropped(irKey(b.code, b.label))).map(b => '<button class="acbtn chip" data-code="' + esc(b.code) + '">' + (irIcon(b.code, b.label) || '') + '<span>' + esc(b.label) + '</span></button>').join('') + '</div></div>';
  const fs = rmFs(w), kx = rmKx(w, fs);
  return (w.text ? '<div class="ttl" style="font-size:' + (fs * 0.8).toFixed(1) + 'px">' + esc(w.text) + '</div>' : '')
    + body.replace('<div class="rc">', '<div class="rc" style="font-size:' + fs.toFixed(1) + 'px;--u:' + fs.toFixed(1) + 'px;--kx:' + kx.toFixed(2) + '">');
}
// the group a key belongs to (a remote can hide whole groups): power, nav, volume, channels, media, other (info, hdmi 1-4, digits, colours...)
const IR_GROUP_OF = {power: 'power', on: 'power', off: 'power', source: 'power', hdmi: 'power',
  menu: 'nav', up: 'nav', down: 'nav', left: 'nav', right: 'nav', ok: 'nav', back: 'nav', home: 'nav', exit: 'nav', info: 'nav',
  vol_up: 'volume', vol_down: 'volume', mute: 'volume', ch_up: 'channels', ch_down: 'channels',
  prev: 'media', rew: 'media', playpause: 'media', play: 'media', pause: 'media', stop: 'media', ff: 'media', next: 'media'};
const irGroup = (k, hasExit) => (k === 'info' && hasExit) ? 'other' : (IR_GROUP_OF[k] || 'other');
// the buttons of a panel tile without the groups the remote hides
function rmButtons(w) {
  const all = (w.buttons || []).map(b => ({code: 'ir_' + b.id, label: b.label || b.id}));
  const hasExit = all.some(b => irKey(b.code, b.label) === 'exit');
  return all.filter(b => !(w.hide || []).includes(irGroup(irKey(b.code, b.label), hasExit)));
}
// [{code, label}] without the keys of the groups a remote hides (the dashboard's flat rows and zones use it too)
function irVisible(list, hide) {
  const hasExit = list.some(b => irKey(b.code, b.label) === 'exit');
  return list.filter(b => !(hide || []).includes(irGroup(irKey(b.code, b.label), hasExit)));
}
// digits, "delete" and the colour keys never make it onto the remote
const irDropped = k => /^d\d$/.test(k) || k === 'del' || ['red', 'green', 'yellow', 'blue', 'ch_up', 'ch_down'].includes(k);
const MEDIA = ['prev', 'rew', 'playpause', 'play', 'pause', 'stop', 'ff', 'next'];
// the key that gets the two-row play button: play/pause if learned, else a lone play or pause
const mediaTall = keys => keys.playpause ? 'playpause' : (keys.play && !keys.pause ? 'play' : (keys.pause && !keys.play ? 'pause' : null));
// buttons: [{code, label}] -> html, or '' when too few buttons are recognised (keep the list);
// opts.all (the dashboard): nothing is dropped, digits and colour keys become chips below the layout
function remoteHtml(buttons, opts) {
  const by = {}, rest = [], hide = (opts && opts.hide) || [];
  const hasExit = buttons.some(b => irKey(b.code, b.label, b.icon) === 'exit');
  for (const b of buttons) {
    const k = irKey(b.code, b.label, b.icon);
    if (hide.includes(irGroup(k, hasExit))) continue;
    if (irDropped(k) && !(opts && opts.all)) continue;
    if (k && !by[k]) by[k] = b; else rest.push(b);
  }
  if (Object.keys(by).filter(k => !/^d\d$/.test(k)).length < 4) return '';
  const btn = (k, cls, face) => {
    const b = by[k]; if (!b) return '<span class="acbtn k" style="visibility:hidden"></span>';
    delete by[k];
    const ico = IR_ICON[k] || '';
    return '<button class="acbtn k ' + (cls || '') + '" data-code="' + esc(b.code) + '" title="' + esc(b.label) + '">' + (ico || esc(face || b.label)) + '</button>';
  };
  const has = (...ks) => ks.some(k => by[k]);
  let h = '<div class="rc">';
  if (has('power', 'on', 'off', 'source', 'hdmi')) {
    // power keys double width only when the row is short, else on/off/input/HDMI share one row of plain keys
    const w2 = ['on', 'off', 'source', 'hdmi'].filter(k => by[k]).length + (by.on || by.off ? 0 : by.power ? 1 : 0) <= 2 ? ' wide' : '';
    const pair = by.on && by.off ? '<span class="pair">' + btn('on', 'pon') + btn('off', 'poff') + '</span>' : '';
    h += '<div class="rrow nw">' + (pair || (by.on || by.off ? (by.on ? btn('on', 'pon' + w2) : '') + (by.off ? btn('off', 'poff' + w2) : '') : (by.power ? btn('power', 'pon' + w2) : '')))
       + (by.source ? btn('source') : '') + (by.hdmi ? btn('hdmi') : '') + '</div>';
  }
  if (has('up', 'down', 'left', 'right', 'ok')) {
    h += '<div class="grid3">' + btn('menu') + btn('up') + btn('home') + btn('left') + btn('ok', 'ok') + btn('right') + btn('back') + btn('down') + btn(by.exit ? 'exit' : 'info') + '</div>';
  }
  // volume and channels: horizontal rows with the caption on the left (one row)
  if (has('vol_up', 'vol_down', 'mute')) h += '<div class="rrow rock nw">' + btn('vol_down') + btn('vol_up') + (by.mute ? btn('mute') : '') + '</div>';
  if (has('ch_up', 'ch_down')) h += '<div class="rrow rock nw"><span class="cap">' + i18n('CH') + '</span>' + btn('ch_down') + btn('ch_up') + '</div>';
  const tall = mediaTall(by);
  if (tall && has('prev', 'next', 'rew', 'ff')) {
    const at = (k, area, cls) => btn(k, cls).replace('<button ', '<button style="grid-area:' + area + '" ').replace('style="visibility:hidden"', 'style="visibility:hidden;grid-area:' + area + '"');
    h += '<div class="media2">' + at('prev', '1/1') + at(tall, '1/2/3/3', 'tall') + at('next', '1/3') + at('rew', '2/1') + at('ff', '2/3') + '</div>';
  }
  if (has('prev', 'rew', 'play', 'pause', 'stop', 'ff', 'next', 'playpause')) {
    h += '<div class="rrow">' + MEDIA.filter(k => by[k]).map(k => btn(k)).join('') + '</div>';
  }
  const chips = Object.keys(by).map(k => by[k]).concat(rest);
  if (chips.length) {
    h += '<div class="sep"></div><div class="rrow">' + chips.map(b => {
      const k = irKey(b.code, b.label, b.icon), ico = k ? IR_ICON[k] : '';
      return '<button class="acbtn chip" data-code="' + esc(b.code) + '" title="' + esc(b.label) + '">' + (ico || esc(b.label)) + '</button>';        // an icon alone: the name is its tooltip
    }).join('') + '</div>';
  }
  return h + '</div>';
}
// a dimmer's level in percent from the device's raw value: the widget's own scale (state_max), else the house's
// "<code>_pct" mirror scaled as the server scales it (the bright control's min..max), else the value as it is
function dimPercent(w, raw, devices) {
  const n = parseFloat(raw);
  if (!isFinite(n)) return null;
  if (w.state_max) return Math.round(n * 100 / w.state_max);
  const m = String(w.state_topic || '').match(/^home\/state\/([^/]+)\/(.+)_pct$/);
  const d = m && (devices || []).find(x => x.id === m[1]), c = d && (d.controls || []).find(x => x.code === m[2]);
  if (c && typeof c.max === 'number') { const lo = c.min || 0; return Math.max(0, Math.min(100, Math.round((n - lo) / ((c.max - lo) || 1) * 100))); }
  return Math.round(n);
}
const irIconSets = () => IR_ICON_SETS.map(([g, ks]) => [g, ks.filter(k => IR_ICON[k])]);
window.PW = {dimPercent, irKey, irGroup, irVisible, irIconSets, wxHtml, wxMode, wxFs, wxSvg, wxKind, acMarkup, acApply, acSlide, acFs, acIcon, irIcon, remoteHtml, rmHtml, rmFs, sampleWeather, SAMPLE_AC};
})();
