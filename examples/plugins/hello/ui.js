// hello: a tab of its own, filled from the plug-in's route.
HomePlugins.register({
  tabs: [{
    id: 'hello', label: 'Hello',
    async render(app){
      const r = await Home.api('/api/hello').catch(() => ({greetings: '?'}));
      app.innerHTML = `<div class="skeleton">${Home.escapeHtml(i18n('Greetings so far:'))} ${r.greetings}</div>`;
    },
  }],
});
