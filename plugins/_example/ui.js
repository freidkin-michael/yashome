// hello: every dashboard hook once. Labels are English source text; i18n.json translates them.
(() => {
  let greetings = 0;
  const load = async () => { try { greetings = (await Home.api('/api/hello')).greetings; } catch(e){ /* offline */ } };
  const lampOn = st => !!(st && st.online && st.values && st.values.power);
  HomePlugins.register({
    onReload: [load],                                   // at start and on every resume
    tabs: [{
      id: 'hello', label: 'Hello', after: 'fav', badge: () => greetings,
      // `box` is this tab's own element: after a tab switch it is detached, late writes are harmless
      async render(box){
        await load();
        box.innerHTML = `<div class="skeleton">${Home.escapeHtml(i18n('Greetings so far:'))} ${greetings}</div>`;
      },
    }, {
      id: 'hellogrid', label: 'Hello devices', cats: ['hello'],   // no render(): a grid of these categories
    }],
    addMenu: [{label: 'Hello lamp', hint: 'Already here: the plug-in registers it itself', button: 'Show',
               onClick(){ Home.closeModal(); }}],
    cards: [(card, dev) => { if(dev.id === 'hello_lamp') card.title = i18n('Made by the hello plug-in'); }],
    cardFaces: [{                                       // the whole card; update() on every state frame
      match: d => d.id === 'hello_lamp',
      render(card, d){
        card.innerHTML = `<div class="name">${Home.escapeHtml(d.name)}</div>
          <button class="dlg-btn primary hello-face">\u{1f4a1}</button>`;
        card.querySelector('.hello-face').onclick = ev => {
          ev.stopPropagation(); Home.sendControl(d.id, 'power', !lampOn(Home.state(d.id)));
        };
      },
      update(card, d, st){ card.querySelector('.hello-face').textContent = lampOn(st) ? i18n('on') : i18n('off'); },
    }],
    modalSections: [{                                   // hideCodes drops those rows from the core's lists
      match: d => d.id === 'hello_lamp', hideCodes: [],
      html: () => `<h3>${i18n('Hello')}</h3><div class="row"><span class="rlabel">${i18n('Lamp')}</span>
        <span class="rval" data-hello="st">\u2014</span></div>`,
      update(box, d, st){ box.querySelector('[data-hello="st"]').textContent = lampOn(st) ? i18n('on') : i18n('off'); },
    }],
    header: [{slot: 'conn', render(box){ box.innerHTML = '<span class="hello-chip" title="hello">\u{1f44b}</span>'; }}],
    onState: [st => { const c = document.querySelector('.hello-chip'); if(c) c.style.opacity = lampOn(st.hello_lamp) ? 1 : .4; }],
    holds: [(d, code, value) => d && d.id === 'hello_lamp' && code === 'power' && value === true ? 8000 : 0],
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
