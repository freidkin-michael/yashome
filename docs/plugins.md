# Plug-ins

The core does MQTT, Zigbee, rules and the dashboard. Everything else - another transport, a
vendor integration, a page of its own - is a plug-in: a Python package the backend imports at
start. The core never names a plug-in; it offers hooks, and a plug-in registers itself on them.

## Where they live

`PLUGINS_DIR` in `.env` points at a directory outside the repository (default: `./plugins`,
git-ignored); compose mounts it read-only as `/app/plugins`. Every sub-directory with an
`__init__.py` is a plug-in; names are lowercase letters, digits and `_`, starting with a letter;
names starting with `_` are skipped (helpers). They load in alphabetical order after the core.

A plug-in that fails to import is logged and listed under Settings -> Plug-ins with the error;
the rest of the house keeps running. Adding, changing or removing one takes a restart of the
backend: `docker compose restart home`.

```
plugins/
  hello/
    __init__.py     # the Python side: registers hooks when imported
    ui.js           # optional: the dashboard side
    i18n.json       # optional: {"ru": {"English text": "translation"}}
    icon.svg        # optional static files: .js .css .json .png .svg, served as /plugins/hello/<file>
```

`examples/plugins/hello/` is a complete one that touches every hook once. Copy it into
`plugins/`, restart, and a "Hello lamp" card, a "Hello" tab and a "greet" rule action appear.

## Python side

```python
import app as core

PLUGIN = {"name": "Hello", "version": "1.0.0"}      # shown in Settings
```

| hook | what it does |
|---|---|
| `core.app` | the FastAPI app: add routes with `@core.app.get(...)`; they need the token like every `/api` route |
| `core.register_device(dev)` | add a device the plug-in owns (`id`, `name`, `transport`, `category`, `controls`) |
| `core.TRANSPORTS[t] = fn(cfg, code, val, kind)` | switch a device whose `transport` is `t` (worker threads: bindings, schedules, timers) |
| `core.ASYNC_CONTROL[t] = async fn(cfg, code, val)` | the same for `POST /api/devices/{id}/control`, on the event loop (else `TRANSPORTS` runs in a thread) |
| `core.CONTROL_KINDS[k] = fn(cfg, ctl, val) -> dict` | a stateless channel kind (an IR button): no value stored, rules use the action `press` |
| `core.MQTT_SUBSCRIPTIONS.append(filter)` | topics subscribed after the core's own on every (re)connect |
| `core.MQTT_HANDLERS.append((prefix, fn(parts, payload, retain)))` | messages whose topic starts with the `prefix` tuple, tried before `home/cmd` |
| `core.STARTUP_TASKS.append(fn)` | run once the broker is up; a coroutine function runs as a task (a poller) |
| `core.SETTINGS_SECTIONS[key] = fn()` | a section of `settings.json` the plug-in owns; read it back with `core._cfg_section(key, default)` |
| `core.BINDING_ACTIONS[a] = {validate, run, describe}` | a rule action other than switching a device (ring a phone) |
| `core.RULE_THEN[type] = fn(then)` | the manual "run" of such a rule (`describe` returns `then` with that `type`) |
| `core.DEVICE_FIELDS.append(key)` | an extra device key `GET /api/devices` passes to the dashboard |
| `core.OPEN_PATHS.add(path)` | a page served without the token (its API calls still carry it) |
| `core._store_status(id, st)`, `core._status_copy(id, default)`, `core.log` | read and publish device state, log |

A section of `settings.json` whose plug-in is missing or failing is kept as it is on every save,
so removing a plug-in for a while loses nothing.

## Dashboard side

`ui.js` is a plain script, loaded before the first render. It registers what it adds:

```js
HomePlugins.register({
  tabs:        [{id, label, render(container), badge?(), visible?(), after?: 'fav'}],
  addMenu:     [{label, hint, button, onClick()}],
  cards:       [(cardElement, device) => { /* decorate a device card */ }],
  ruleActions: [{type, label, visible?(), html(then|null), read() -> body, chip(then), title(then)}],
  toolbar:     [{tab: 'auto' | 'other', label, visible?(), onClick()}],
  onReload:    [async () => { /* refresh your data; runs at start and on every resume */ }],
});
```

Helpers: `Home.api(path, opts)`, `Home.openModal(html, id)`, `Home.closeModal()`, `Home.modalId()`,
`Home.escapeHtml(s)`, `Home.el(tag, cls, html)`, `Home.devices()`, `Home.state(id)`,
`Home.activeTab()`, `Home.sendControl(id, code, value)`, `Home.refresh()`, and `i18n(text)` /
`i18nPlural(key, n)`. Labels are English source text; `i18n.json` translates them. Every call into
a plug-in is guarded: an exception is logged in the console, the dashboard keeps working.
