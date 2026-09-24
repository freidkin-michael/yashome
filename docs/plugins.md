# Plug-ins

The core does MQTT, Zigbee, rules and the dashboard. Everything else - another transport, a
vendor integration, a page of its own - is a plug-in: a Python package the backend imports at
start. The core never names a plug-in; it offers hooks, and a plug-in registers itself on them.
Plug-ins are trusted code: they run inside the backend with its rights.

## Where they live

`PLUGINS_DIR` in `.env` is a directory on the host, outside the repository (default: `./plugins`,
git-ignored). Compose mounts it read-only as `/app/plugins`; the backend always reads that path
(a bare `uvicorn app:app` reads `plugins/` next to the data). After changing `PLUGINS_DIR` run
`make update` (a new mount needs the container recreated); after adding, changing or removing a
plug-in a restart is enough: `docker compose restart home`.

Every sub-directory with an `__init__.py` is a plug-in. Names: lowercase letters, digits and `_`,
starting with a letter; names starting with `_` are skipped (helpers). They load in alphabetical
order after the core. A plug-in that fails to import - or registers something it may not (see
"Reserved") - is taken back completely (its hooks, devices and routes), logged, and listed under
Settings -> Plug-ins with the error; the rest of the house keeps running.

```
plugins/
  hello/
    __init__.py     # the Python side: registers hooks when imported
    ui.js           # optional, public: the dashboard side
    i18n.json       # optional, public: {"ru": {"English text": "translation"}}
    static/         # optional, public: icons, css ... (.js .css .json .png .svg)
    anything_else   # private: source, config, data - never served
```

Only `ui.js`, `i18n.json` and files in `static/` are served, without the token, at
`/plugins/<name>/...`. Keep nothing secret there. Everything else a plug-in adds under `/plugins/`
or elsewhere is a route and needs the token.

`examples/plugins/hello/` is a complete one. Copy it into your plug-in directory and restart: a
"Hello lamp" card, a "Hello" tab, an "Add" row, a toolbar button and a "Say hello" rule target
appear. It does not use `ASYNC_CONTROL`, `CONTROL_KINDS`, `STARTUP_TASKS`, `DEVICE_FIELDS` and
`OPEN_PATHS`; the table below says what they do.

## Python side

```python
import app as core

PLUGIN = {"name": "Hello", "version": "1.0.0"}      # a dict; shown in Settings
```

| hook | what it does |
|---|---|
| `core.app` | the FastAPI app: add routes with `@core.app.get(...)`; they need the token like every route |
| `core.register_device(dev)` | add a device the plug-in owns (`id`, `name`, `transport`, `category`, `controls`); refuses an id that already exists; once, at import (there is no update or removal) |
| `core.TRANSPORTS[t] = fn(cfg, code, val, kind)` | switch a device whose `transport` is `t` (worker threads: bindings, schedules, timers; also `/control` when there is no async variant) |
| `core.ASYNC_CONTROL[t] = async fn(cfg, code, val)` | the same for `POST /api/devices/{id}/control`, on the event loop |
| `core.CONTROL_KINDS[k] = fn(cfg, ctl, val) -> dict` | a stateless channel kind (an IR button): no value stored; rules use the action `press` |
| `core.MQTT_SUBSCRIPTIONS.append(filter)` | a topic filter under a topic of your own, subscribed after the core's on every (re)connect |
| `core.MQTT_HANDLERS.append((prefix, fn(parts, payload, retain)))` | messages whose topic starts with the `prefix` tuple; `retain` is True for a replayed message - an event handler should ignore those |
| `core.STARTUP_TASKS.append(fn)` | started once the broker is up, before the timers are replayed (nothing waits for it); a coroutine function runs as a task, a plain callable in a thread of its own - either may loop forever; errors, `sys.exit` included, are logged. Timers of your devices may fire before your task has run: a transport that is not ready yet should raise (the core logs it and retries) |
| `core.SETTINGS_SECTIONS[key] = fn()` | a section of `settings.json` the plug-in owns (must be JSON: dict, list, str, number; not a core key); read it back with `core._cfg_section(key, default)`. A route of yours that writes it should call `core._save_settings()` like the core does |
| `core.BINDING_ACTIONS[a] = {validate, run, describe}` | a rule action other than switching a device - see below |
| `core.DEVICE_FIELDS.append(key)` | an extra device key `GET /api/devices` passes to the dashboard |
| `core.OPEN_PATHS.add(path)` | a page of the plug-in served without the token (its API calls still carry it); never `/api...`, `/ws` or `/plugins/...` |
| `core._store_status(id, st)`, `core._status_copy(id, default)`, `core.log` | read and publish device state, log |

A section of `settings.json` whose plug-in is missing or failing is kept as it is on every save,
so removing a plug-in for a while loses nothing. A section whose value cannot be stored keeps its
last stored value (and the error is logged); every other setting still saves.

**Rule actions.** `validate(body) -> entry` gets the `PUT /api/bindings/...` body (all its fields,
your own included) and returns what is stored; it must contain `"action"` (your action name) and
a string `"target"` (any id of yours - it is not a device). `run(entry) -> dict` executes it (a
worker thread). `describe(entry) -> dict` is what the rule list shows: `{"type": <your ui type>,
"title": <short text>, ...}`. A rule whose plug-in is not loaded stays stored; the dashboard lets
you enable, disable or delete it, not edit it, and `POST /api/rules/run` answers 409.

**Reserved.** A plug-in may not register the core's transports (`z2m`, `mqtt`), control kinds
(`switch`, `bright`, `sensor`, ...), actions (`on`, `off`, `toggle`, `bright_up`, `bright_down`,
`press`), device keys (`id`, `name`, `controls`, ...), MQTT handlers or subscriptions under
`home/`, `zigbee2mqtt/`, `homeassistant/`, `$SYS`, or open paths under `/api`, `/ws`, `/plugins/`.
Nor may it replace or remove what the core or an earlier plug-in registered (a hook entry, a device,
a settings section); changing such an object in place is not detected - do not. A route under
`/plugins/<name>/` is yours, except one whose path looks like a public file (`ui.js`, `i18n.json`,
`static/...`): that is refused, it would be served without the token.

## Dashboard side

`ui.js` is a plain script, loaded before the first render. It registers what it adds:

```js
HomePlugins.register({
  tabs:        [{id, label, render(box), badge?(), visible?(), after?: 'fav'}],
  addMenu:     [{label, hint, button, onClick()}],
  cards:       [(cardElement, device) => { /* decorate a device card */ }],
  ruleActions: [{type, label, visible?(), html(then|null), read() -> body, chip?(then), title?(then)}],
  toolbar:     [{tab: 'auto' | 'other', label, visible?(), onClick()}],
  onReload:    [async () => { /* refresh your data; at start and on every resume */ }],
});
```

- `onReload`: after it the dashboard redraws the tab bar (badges), not an open plug-in tab: call
  `Home.refresh()` when your tab shows data that changed. It waits at most 5 s for them, then renders anyway; a slower callback
  still finishes later, so do not let an old reply overwrite a newer one.
- `render(box)` gets an element of its own inside the page; after a tab switch it is detached, so
  a late write after an `await` does no harm.
- `ruleActions`: `type` is the `describe().type` of the Python side; `html(then)` draws the form
  (`then` is the stored rule when editing, else `null`); `read()` returns the body for
  `PUT /api/bindings/...` - at least `{action, target}` - or throws an `Error` with the message
  to show; `chip` / `title` are optional (the server's `title` is used otherwise). A rule whose
  `type` has no registered action, or whose `visible()` is false, opens read-only.
- `badge()` returns a number or a short text (shown as text); `ui.js` that fails to load or throws
  is listed as failed in Settings.
- Labels are English source text; `i18n.json` adds translations. It never replaces a core word:
  a key the core already translates keeps the core's translation.

Helpers: `Home.api(path, opts)`, `Home.openModal(html, id)`, `Home.closeModal()`, `Home.modalId()`,
`Home.escapeHtml(s)`, `Home.el(tag, cls, html)`, `Home.devices()`, `Home.state(id)`,
`Home.activeTab()`, `Home.sendControl(id, code, value)`, `Home.refresh()`, and `i18n(text)` /
`i18nPlural(key, n)`. Every call into a plug-in is guarded (a thrown error or a rejected promise
is logged in the console); the dashboard keeps working.
