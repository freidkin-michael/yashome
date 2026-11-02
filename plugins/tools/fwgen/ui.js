// Settings -> yashGen: the link to the firmware builder (the computer it runs on: Computers and servers).
(() => {
  // the builder has its own host next to the dashboard's (yashgen.<domain>): another origin, so a link opens in a browser tab
  // and not in the installed app, whose scope is the dashboard's origin
  function href(){
    const m = location.hostname.match(/^home\.(.+)$/);
    return m ? location.protocol + '//yashgen.' + m[1] + '/' : '/fwgen/';
  }
  function render(box){
    const esc = Home.escapeHtml;
    box.innerHTML = `<div class="bindcaption">${esc(i18n('Describe a device, get its ESPHome firmware, flash it over USB.'))}</div>
      <div class="row"><a class="dlg-btn primary" target="_blank" rel="noopener" href="${href()}" style="text-decoration:none;display:inline-flex;align-items:center">${esc(i18n('Open the builder'))}</a></div>
      <div class="bindcaption" style="margin-top:10px">${esc(i18n('The computer it runs on: Settings -> Devices -> Computers and servers.'))}</div>`;
  }
  HomePlugins.register({settings: [{subject: 'fwgen', title: 'Firmware builder', expl: 'Open the yashGen firmware builder in a new tab', render}]});
})();
