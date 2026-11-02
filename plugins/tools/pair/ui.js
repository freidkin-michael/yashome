// Pair a device: the Add menu row and its dialog. The device shows its setup network "<name>-setup" and a code
// (also the QR of its label: YASHPAIR:1:<name>-setup:<code>); the house hands it the Wi-Fi, an account and an OTA password.
(() => {
const MID = '__pair__';
const esc = Home.escapeHtml;
const box = sel => document.getElementById('md').querySelector(sel);
const IN = 'background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:8px 10px;font:inherit;width:100%;box-sizing:border-box';
const QR = /^YASHPAIR:1:([a-z][a-z0-9_-]*-setup):([A-Z2-7]{8})$/;

// a label's QR text (or the code typed alone) into the two fields
function fill(text){
  const m = QR.exec(String(text || '').trim());
  if(m){ box('#pr-ap').value = m[1]; box('#pr-code').value = m[2]; return true; }
  return false;
}

async function scanQr(btn){
  const out = box('#pr-out');
  try {
    const det = new BarcodeDetector({formats: ['qr_code']});
    const stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: 'environment'}});
    const v = Object.assign(document.createElement('video'), {srcObject: stream, playsInline: true, muted: true});
    v.style.cssText = 'width:100%;border-radius:8px;margin-top:8px';
    box('#pr-cam').append(v);
    await v.play();
    for(let i = 0; i < 150 && Home.modalId() === MID; i++){        // about 30 s
      const hit = (await det.detect(v))[0];
      if(hit && fill(hit.rawValue)) break;
      await new Promise(r => setTimeout(r, 200));
    }
    stream.getTracks().forEach(t => t.stop());
    v.remove();
  } catch(e){
    out.innerHTML = `<div class="bindcaption" style="color:var(--bad)">${esc(i18n('The camera could not read a code: {0}', e.message || e))}</div>`;
  }
}

async function scan(){
  const list = box('#pr-list');
  list.innerHTML = `<div class="bindcaption">${esc(i18n('Scanning (about 8 s)...'))}</div>`;
  try {
    const r = await Home.api('/api/esphome/ap/scan', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({wait: 8})});
    const aps = (r.aps || []).filter(a => a.setup && a.secured);
    list.innerHTML = aps.length ? aps.map(a => `<button class="dlg-btn pr-ap" data-ap="${esc(a.ssid)}">${esc(a.ssid)}</button>`).join(' ')
      : `<div class="bindcaption">${esc(i18n('No device in its setup network in sight.'))}</div>`;
    list.querySelectorAll('.pr-ap').forEach(b => { b.onclick = () => { box('#pr-ap').value = b.dataset.ap; box('#pr-code').focus(); }; });
  } catch(e){
    list.innerHTML = `<div class="bindcaption" style="color:var(--bad)">${esc(i18n('Scan failed: {0}', e.message || e))}</div>`;
  }
}

async function pair(btn){
  const out = box('#pr-out'), ap = box('#pr-ap').value.trim(), code = box('#pr-code').value.trim().toUpperCase();
  if(fill(ap)) return pair(btn);
  if(!/-setup$/.test(ap) || !/^[A-Z2-7]{8}$/.test(code)){
    out.innerHTML = `<div class="bindcaption" style="color:var(--bad)">${esc(i18n('The setup network is <name>-setup and the code has 8 characters (letters and the digits 2-7).'))}</div>`;
    return;
  }
  btn.disabled = true;
  out.innerHTML = `<div class="bindcaption">${esc(i18n('Pairing {0}: the house joins its network and hands over the Wi-Fi; about a minute.', ap))}</div>`;
  try {
    const r = await Home.api('/api/pair/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ap, code})});
    out.innerHTML = `<div class="bindcaption" style="color:var(--ok,#4ade80)">${esc(i18n('Paired: {0}. It joins the house network now; its card appears when it has described itself.', r.node))}</div>`;
  } catch(e){
    out.innerHTML = `<div class="bindcaption" style="color:var(--bad)">${esc(i18n('Not paired: {0}', e.message || e))}</div>`;
  }
  btn.disabled = false;
}

function open(){
  Home.openModal(`
    <div class="head">
      <button class="closebtn" onclick="Home.closeModal()">x</button>
      <h2>${esc(i18n('Pair a device'))}</h2>
      <div class="sub">${esc(i18n('A new device built in yashGen shows its setup network name-setup and a code (on its label, also as a QR). Type them or scan the QR.'))}</div>
    </div>
    <div class="body"><section>
      <div class="row"><button class="dlg-btn" id="pr-scan">${esc(i18n('Look for setup networks'))}</button>
        ${'BarcodeDetector' in window ? `<button class="dlg-btn" id="pr-qr">${esc(i18n('Scan the QR'))}</button>` : ''}</div>
      <div id="pr-list" style="margin:8px 0"></div><div id="pr-cam"></div>
      <div class="bindcaption hqlbl">${esc(i18n('Setup network'))}</div><input id="pr-ap" style="${IN}" autocomplete="off" autocapitalize="off" placeholder="air-setup">
      <div class="bindcaption hqlbl" style="margin-top:8px">${esc(i18n('Code'))}</div><input id="pr-code" style="${IN};text-transform:uppercase" autocomplete="off" maxlength="40" placeholder="ABCD2345">
      <div class="row" style="margin-top:10px"><button class="dlg-btn primary" id="pr-go">${esc(i18n('Pair'))}</button></div>
      <div id="pr-out"></div>
    </section></div>`, MID);
  box('#pr-scan').onclick = scan;
  const q = box('#pr-qr'); if(q) q.onclick = () => scanQr(q);
  box('#pr-go').onclick = ev => pair(ev.target);
  box('#pr-code').onpaste = () => setTimeout(() => fill(box('#pr-code').value), 0);
}

// the device window: "paired with this house" and its way out, or "moved to another house"
let info = {paired: [], moved: []};
async function load(){ try { info = await Home.api('/api/pair'); } catch(e){ /* the plug-in is off */ } }
const mine = d => info.paired.find(p => p.device === d.id), gone = d => info.moved.find(m => m.device === d.id);

HomePlugins.register({
  onReload: [load],
  modalSections: [{
    match: d => !!(mine(d) || gone(d)),
    html: d => mine(d)
      ? `<div class="row"><span class="rlabel">${esc(i18n('Paired with this house'))}</span><button class="dlg-btn" id="pr-moved">${esc(i18n('Moved to another house'))}</button></div>
         <div class="bindcaption">${esc(i18n('The device forgets this house and returns to its setup network; its card stays here, offline.'))}</div><div id="pr-mv-out"></div>`
      : `<div class="bindcaption" style="color:var(--on)">${esc(i18n('This device moved to another house; its card stays offline until it is paired here again.'))}</div>`,
    wire(box, d){
      const b = box.querySelector('#pr-moved'); if(!b) return;
      b.onclick = async () => {
        if(!confirm(i18n('Unpair this device from the house? It forgets this house and opens its setup network.'))) return;
        const p = mine(d);
        try { await Home.api('/api/pair/' + encodeURIComponent(p.node) + '/moved', {method: 'POST'}); await load(); box.querySelector('#pr-mv-out').textContent = i18n('Done: the device moved.'); b.disabled = true; }
        catch(e){ box.querySelector('#pr-mv-out').textContent = e.message || String(e); }
      };
    },
  }],
  help: [
    {id: 'pair.scan', screen: 'pair', screenName: 'Pair a device', sel: '#pr-scan', title: 'Look for setup networks', text: 'Lists the devices in sight that show their setup network (about 8 s); a tap on one fills Setup network.'},
    {id: 'pair.qr', screen: 'pair', screenName: 'Pair a device', sel: '#pr-qr', title: 'Scan the QR', text: 'Reads the QR of the label with the camera (about 30 s) and fills both fields; only where the browser reads QR codes.'},
    {id: 'pair.found', screen: 'pair', screenName: 'Pair a device', sel: '.pr-ap', title: 'A setup network', text: 'One found nearby: a tap puts its name into Setup network.'},
    {id: 'pair.ap', screen: 'pair', screenName: 'Pair a device', sel: '#pr-ap', at: 'label', title: 'Setup network', text: 'As the device shows it: <name>-setup. Pasting the text of its QR here fills both fields.'},
    {id: 'pair.code', screen: 'pair', screenName: 'Pair a device', sel: '#pr-code', at: 'label', title: 'Code', text: 'Eight characters from the label, letters and the digits 2-7; small or capital letters alike.'},
    {id: 'pair.go', screen: 'pair', screenName: 'Pair a device', sel: '#pr-go', title: 'Pair', text: 'The house joins the device\'s setup network and hands it the Wi-Fi, its own account and an update password; about a minute.'},
    {id: 'pair.moved', screen: 'device', sel: '#pr-moved', title: 'Moved to another house', text: 'After a question the device forgets this house and opens its setup network again; its card stays here, offline.'},
  ],
  addMenu: [{
  label: 'Pair a device',
  hint: 'A new device from yashGen: its setup network and its code; the house hands it the Wi-Fi and its own account',
  button: 'Pair', onClick: open,
  }],
});
})();
