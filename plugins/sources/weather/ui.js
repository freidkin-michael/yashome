// Weather: Settings -> Weather sets the house's place (a city search, or the latitude and longitude), the IMS station whose last
// observation is the weather now (the nearest with a thermometer, or one by name), and the forecast model (several that must agree on
// rain, or one); it says where "now" comes from.
(() => {
  const E = s => Home.escapeHtml(String(s == null ? '' : s));
  const MODEL = {consensus: 'Several models (rain when most agree)', best_match: 'Open-Meteo best match', ecmwf_ifs025: 'ECMWF IFS',
                 icon_seamless: 'DWD ICON', ukmo_seamless: 'UK Met Office', gfs_seamless: 'NOAA GFS', meteofrance_seamless: 'Meteo-France'};
  const WHY = {far: 'no weather station near the house', away: 'the weather stations do not answer now', stale: 'the station has been silent for an hour'};
  async function settingsPage(box){
    let s;
    try { s = await Home.api('/api/weather/settings'); } catch(e){ box.textContent = e.message || String(e); return; }
    const near = (s.stations || [])[0];
    const src = s.now_source || {};
    const nowLine = src.kind === 'station' ? i18n('Now: from the station {0}, {1} km away, {2} min ago', src.name, src.km, src.age_min)
      : src.kind === 'model' ? i18n('Now: from the forecast ({0})', i18n(WHY[src.reason] || 'no weather station near the house')) : '';
    const map = (s.lat != null) ? `https://www.openstreetmap.org/?mlat=${s.lat}&mlon=${s.lon}#map=13/${s.lat}/${s.lon}` : '';
    box.innerHTML = `
      <div class="row"><span class="rlabel">${E(i18n('Place of the house'))}<br><span class="bindcaption">${E(s.guessed ? i18n('Guessed from the internet address: find your city') : i18n('The forecast is for this place'))}</span></span>
        <span style="display:flex;gap:6px;flex-wrap:wrap;align-items:center"><input class="askin" data-w="q" placeholder="${E(i18n('City'))}" style="max-width:220px">
        <button class="dlg-btn" data-w="find">${E(i18n('Find'))}</button></span></div>
      <div data-w="found" class="bindcaption"></div>
      <div class="bindcaption" data-w="placename">${E(s.place || '')}</div>
      <div class="bindcaption" data-w="now">${E(nowLine)}</div>
      <details${s.guessed && !s.place ? ' open' : ''}><summary>${E(i18n('Advanced'))}</summary>
      <div class="row"><span class="rlabel">${E(i18n('Latitude, longitude'))}</span>
        <span style="display:flex;gap:6px;align-items:center"><input class="askin" data-w="lat" inputmode="decimal" style="width:110px" value="${E(s.lat ?? '')}">
        <input class="askin" data-w="lon" inputmode="decimal" style="width:110px" value="${E(s.lon ?? '')}">
        ${map ? `<a class="dlg-btn" data-w="map" href="${E(map)}" target="_blank" rel="noopener">${E(i18n('On the map'))}</a>` : ''}</span></div>
      <div class="row"><span class="rlabel">${E(i18n('Station of the weather now'))}<br><span class="bindcaption">${E(i18n('The Israel Meteorological Service: public, no key. Farther than {0} km or silent for an hour: the models say the weather now', s.near_km))}</span></span>
        <select class="askin" data-w="station"><option value="auto">${E(near ? i18n('The nearest: {0}, {1} km', near.name, near.km) : i18n('The nearest'))}</option>
        ${(s.stations || []).slice(0, 40).map(x => `<option value="${x.num}"${String(x.num) === String(s.station) ? ' selected' : ''}>${E(x.name)} \u00b7 ${E(x.km)} km</option>`).join('')}</select></div>
      <div class="row"><span class="rlabel">${E(i18n('Forecast'))}<br><span class="bindcaption">${E(i18n('Several models: rain only when most of them agree'))}</span></span>
        <select class="askin" data-w="model">${(s.models || []).map(m => `<option value="${E(m)}"${m === s.model ? ' selected' : ''}>${E(i18n(MODEL[m] || m))}</option>`).join('')}</select></div>
      ${src.why ? `<div class="bindcaption">${E(src.why)}</div>` : ''}
      </details>
      <div class="row" style="justify-content:flex-end;gap:6px"><span data-w="msg" class="bindcaption"></span><button class="dlg-btn primary" data-w="save">${E(i18n('Save'))}</button></div>`;
    const q = k => box.querySelector(`[data-w="${k}"]`);
    let place = s.place || '';
    q('find').onclick = async () => {
      const name = q('q').value.trim();
      if(name.length < 2) return;
      try {
        const r = await Home.api('/api/weather/geocode?q=' + encodeURIComponent(name));
        q('found').innerHTML = r.length ? r.map((p, i) => `<button class="dlg-btn" data-p="${i}" style="margin:2px">${E([p.name, p.admin, p.country].filter(Boolean).join(', '))}</button>`).join('')
          : E(i18n('Nothing found'));
        q('found').querySelectorAll('[data-p]').forEach(b => { b.onclick = () => {
          const p = r[+b.dataset.p];
          q('lat').value = (+p.lat).toFixed(4); q('lon').value = (+p.lon).toFixed(4); place = p.name;
          q('placename').textContent = [p.name, p.admin].filter(Boolean).join(', '); q('found').innerHTML = '';
        }; });
      } catch(e){ q('found').textContent = e.message || String(e); }
    };
    q('q').onkeydown = ev => { if(ev.key === 'Enter') q('find').click(); };
    q('save').onclick = async () => {
      try {
        await Home.api('/api/weather/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({lat: +q('lat').value, lon: +q('lon').value, place, station: q('station').value, model: q('model').value})});
        q('msg').textContent = i18n('Saved: the weather is fetched again');
        setTimeout(() => settingsPage(box), 4000);
      } catch(e){ q('msg').textContent = e.message || String(e); }
    };
  }
  HomePlugins.register({
    settings: [{subject: 'weather', title: 'Weather', expl: 'The place, the station of the weather now, the forecast', render: settingsPage}],
    help: [
      {id: 'weather.find', screen: 'settings:weather', sel: '[data-w="find"]', title: 'Find', text: 'Finds the city; a tap on one sets its latitude and longitude.'},
      {id: 'weather.point', screen: 'settings:weather', sel: '[data-w="lat"]', at: 'label', title: 'Latitude, longitude',
       text: 'The exact point of the house in degrees; the forecast and the nearest station are chosen by it.'},
      {id: 'weather.station', screen: 'settings:weather', sel: '[data-w="station"]', at: 'label', title: 'Station of the weather now',
       text: 'Its last observation (every 10 minutes) is the weather now: the temperature, rain, and the sun from its radiation.'},
      {id: 'weather.model', screen: 'settings:weather', sel: '[data-w="model"]', at: 'label', title: 'Forecast',
       text: 'Several models: rain only when most of them agree. One model: its own forecast as it is.'},
      {id: 'weather.save', screen: 'settings:weather', sel: '[data-w="save"]', title: 'Save', text: 'Kept for the house; the weather is fetched again at once.'},
    ],
  });
})();
