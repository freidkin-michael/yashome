// Presence: marks the brand badge from the `presence` device on every state frame: green when someone is home; nobody home
// is neutral (an outline, the logo dimmed: red is for faults only), no phone seen for days a dashed amber outline.
(() => {
  const DEVICE = 'presence';
  const css = document.createElement('style');
  css.textContent = `#brandtitle.home{background:#1f9d63;color:#fff}
    #brandtitle.away{background:transparent;box-shadow:inset 0 0 0 1.5px var(--line);opacity:.75}
    #brandtitle.stale{background:transparent;outline:1.5px dashed #c98a00;outline-offset:-1.5px}`;
  document.head.append(css);
  HomePlugins.register({
    onState: [STATE => {
      const el = document.getElementById('brandtitle'); if(!el) return;
      const st = STATE[DEVICE];
      el.classList.remove('home', 'away', 'stale');
      const say = t => { if(t){ el.title = t; el.setAttribute('aria-label', t); } else { el.removeAttribute('title'); el.removeAttribute('aria-label'); } };
      if(!st){ say(''); return; }
      if(!st.online){ say(i18n('presence: no link')); return; }
      const v = st.values || {};
      const stale = !v.home && !!v.stale;
      el.classList.add(v.home ? 'home' : stale ? 'stale' : 'away');
      say(v.home ? i18n('At home: someone is in') : stale ? i18n('No phone seen for a while') + ' (' + i18n('presence: no phone seen for {0} days', v.unseen_days) + ')'
        : i18n('At home: nobody'));
    }],
  });
})();
