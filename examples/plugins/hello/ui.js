// hello: every dashboard hook once. Labels are English source text; i18n.json translates them.
(() => {
  let greetings = 0;
  const load = async () => { try { greetings = (await Home.api('/api/hello')).greetings; } catch(e){ /* offline */ } };
  HomePlugins.register({
    onReload: [load],                                   // at start and on every resume, 5 s at most
    tabs: [{
      id: 'hello', label: 'Hello', after: 'fav', badge: () => greetings,
      // `box` is this tab's own element: after a tab switch it is detached, late writes are harmless
      async render(box){
        await load();
        box.innerHTML = `<div class="skeleton">${Home.escapeHtml(i18n('Greetings so far:'))} ${greetings}</div>`;
      },
    }],
    addMenu: [{label: 'Hello lamp', hint: 'Already here: the plug-in registers it itself', button: 'Show',
               onClick(){ Home.closeModal(); }}],
    cards: [(card, dev) => { if(dev.id === 'hello_lamp') card.title = i18n('Made by the hello plug-in'); }],
    ruleActions: [{
      type: 'hello',                                    // = describe().type on the Python side
      label: 'Say hello',
      html: t => `<div class="rb-row"><label>${i18n('Text')}</label>
        <input id="rb-hello" value="${Home.escapeHtml(t && t.text ? t.text : 'hello')}"></div>`,
      read(){                                           // the PUT /api/bindings body: action + target at least
        const text = document.getElementById('rb-hello').value.trim();
        if(!text) throw new Error(i18n('Text'));
        return {action: 'greet', target: 'hello', text};
      },
      chip: t => `\u{1f44b} ${Home.escapeHtml(t.text || '')}`,
      title: t => `${i18n('say')} ${t.text || ''}`,
    }],
    toolbar: [{tab: 'auto', label: 'Greetings', onClick(){ alert(`${i18n('Greetings so far:')} ${greetings}`); }}],
  });
})();
