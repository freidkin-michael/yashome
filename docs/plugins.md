# Plug-ins

The core does generic MQTT devices, rules and the dashboard. Everything else - another transport
(Zigbee through zigbee2mqtt), a vendor integration, a page of its own - is a plug-in: a Python
package the backend imports at start, optionally with a `setup/` folder that brings its own
containers, broker account, secrets and install steps. The core never names a plug-in; it offers
hooks, and a plug-in registers itself on them. Plug-ins are trusted code: they run inside the
backend with its rights.

## Where they live

`plugins/` of the checkout holds the plug-ins that ship with the core (listed in
`plugins/README.md`); `PLUGINS_EXTRA_DIR` in `.env` is a directory of your own beside it (default
`./plugins-extra`, git-ignored), mounted read-only as `/app/plugins-extra`. `PLUGINS` in `.env`
names the ones that load, from either (`zigbee,weather`; `*` = every one present; empty = none);
a name in `PLUGINS` that exists nowhere is an error in Settings, a shipped name reused in the
extra directory is skipped. After changing `PLUGINS` or adding a plug-in with a `setup/` folder
run `make update` (it lists the plug-ins' containers in `COMPOSE_FILE`, derives their secrets
and recreates what changed); for one without it a restart is enough: `docker compose restart home`.

Every sub-directory with an `__init__.py` is a plug-in. Names: lowercase letters, digits and `_`,
starting with a letter; names starting with `_` are skipped (helpers and `_example`). They load
in alphabetical order after the core. A plug-in that fails to import - or registers something it
may not (see "Reserved") - is taken back completely (its hooks, devices, routes, namespace),
logged, and listed under Settings -> Plug-ins with the error; the rest of the house keeps running.

```
plugins/
  hello/
    __init__.py     # the Python side: registers hooks when imported
    ui.js           # optional, public: the dashboard side
    i18n.json       # optional, public: {"ru": {"English text": "translation"}}
    static/         # optional, public: icons, css ... (.js .css .json .png .svg)
    setup/          # optional, never served: containers, broker account, secrets, install steps
    anything_else   # private: source, templates - never served
```

Only `ui.js`, `i18n.json` and files in `static/` are served, without the token, at
`/plugins/<name>/...`. Keep nothing secret there. Everything else a plug-in adds under `/plugins/`
or elsewhere is a route and needs the token.

**Code and templates in the package, your house in `plugin-data/`.** The package is the same in
every house: code, defaults, empty templates (`setup/configuration.example.yaml`,
`setup/env.example`). What belongs to one house - keys, device lists, a coordinator's network
database, generated secrets - lives in `core.plugin_data(name)` (`plugin-data/<name>/` of the
checkout, git-ignored), in `.env`, or in a data directory `.env` points at. So a plug-in folder can
be copied, updated or shipped with the core without carrying anyone's data; `make lint` refuses a
LAN address, a MAC address or a home directory in `plugins/` (`tools/check_plugins.py`; test values
come from the documentation ranges: `192.0.2.x`, `00:11:22:33:44:55`, `/home/someone`).

`plugins/_example/` is a complete one. Copy it as `hello` into `PLUGINS_EXTRA_DIR`, add `hello` to
`PLUGINS` and restart: a "Hello lamp" card, a "Hello" tab, an "Add" row, a toolbar button and a
"Say hello" rule target appear. It does not use `ASYNC_CONTROL`, `CONTROL_KINDS`, `STARTUP_TASKS`, `DEVICE_FIELDS`,
`OPEN_PATHS` or the transport hooks; the tables below say what they do.

## Python side

```python
import app as core

PLUGIN = {"name": "Hello", "version": "1.0.0"}      # a dict; shown in Settings
```

| hook | what it does |
|---|---|
| `core.app` | the FastAPI app: add routes with `@core.app.get(...)`; they need the token like every route |
| `core.register_device(dev)` | add a device the plug-in owns (`id`, `name`, `transport`, `category`, `controls`, plus keys of its own starting with `_`); refuses an id that already exists and a transport another plug-in registered (`TRANSPORTS`). The id is one MQTT topic level and one URL path segment: 1-256 printable characters without `/ + # \|` (spaces and any script are fine: a zigbee2mqtt friendly_name) |
| `core.update_device(dev)`, `core.remove_device(id, transport)` | change or drop a device the plug-in registered itself (same id and `transport`; never a core device or another plug-in's: the core remembers which plug-in registered it); its state is kept on update, dropped on removal (`keep_state=True`: kept until the cache is pruned - a device that only left its system's list for a moment) |
| `core.TRANSPORTS[t] = fn(cfg, code, val, kind)` | switch a device whose `transport` is `t` (worker threads: bindings, schedules, timers; also `/control` when there is no async variant). Raise `HTTPException(503)` when the device cannot be reached |
| `core.ASYNC_CONTROL[t] = async fn(cfg, code, val)` | the same for `POST /api/devices/{id}/control`, on the event loop |
| `core.CONTROL_KINDS[k] = fn(cfg, ctl, val) -> dict` | a stateless channel kind (an IR button): no value stored; rules use the action `press` |
| `core.MQTT_SUBSCRIPTIONS.append(filter)` | a topic filter under a topic of your own (see "Reserved"), subscribed after the core's on every (re)connect, in list order (the broker replays retained messages in that order: a device registry before its state frames) |
| `core.MQTT_HANDLERS.append((prefix, fn(parts, payload, retain)))` | messages whose topic starts with the `prefix` tuple; `retain` is True for a replayed message - an event handler should ignore those |
| `core.MQTT_NAMESPACES.add(top)` | claim a whole top-level topic (`zigbee2mqtt`): only you may subscribe or handle under it, a discovered ESPHome node may not point into it (a hand-written `mqtt_devices.json` entry is yours: do not point one there, it would take those frames first). A plug-in that loaded earlier and already subscribes there makes the claim fail. Not the core's own (`home`, `homeassistant`, `$...`), one plain level |
| `core.TOKEN_CHECKS.append(fn(token, method, path) -> bool)` | accept a token your plug-in issued (a key per kiosk panel, revocable one by one) for THIS request: `method` is the HTTP method (`WS` for the WebSocket), `path` the URL path - admit only what that client needs; only an exact `True` admits, an exception refuses. The master token never reaches your check |
| `core.ON_MQTT_CONNECT.append(fn(cli))` | called after every (re)connect to the broker, once the subscriptions are in: republish retained topics of your own (a layout a device reads); an exception is logged |
| `core.STARTUP_TASKS.append(fn)` | started once the broker is up, before the timers are replayed (nothing waits for it); a coroutine function runs as a task, a plain callable in a thread of its own - either may loop forever; errors, `sys.exit` included, are logged. Timers of your devices may fire before your task has run: a transport that is not ready yet should raise (the core logs it and retries) |
| `core.SETTINGS_SECTIONS[key] = fn()` | a section of `settings.json` the plug-in owns (must be JSON: dict, list, str, number; not a core key); read it back with `core._cfg_section(key, default)`. A route of yours that writes it should call `core._save_settings()` like the core does |
| `core.BINDING_ACTIONS[a] = {validate, run, describe}` | a rule action other than switching a device - see below |
| `core.DEVICE_FIELDS.append(key)` | an extra device key `GET /api/devices` passes to the dashboard |
| `core.plugin_data(name)` | the directory for your house's files (keys, counters, builds): `ROOT/plugin-data/<name>/`, created on first use and ignored by git - keep nothing of yours next to the core's files |
| `@core.open_route` | above `@core.app.post(...)`: that one route of yours (not other paths of the same function) is served without the token (a device pairing before it has one); only a route the plug-in adds itself, decided after routing. Guard it yourself (a pairing window) |
| `core.OPEN_PATHS.add(path)` | a page of the plug-in served without the token (its API calls still carry it); never `/api...`, `/ws` or `/plugins/...` |
| `core._store_status(id, st)`, `core._store_status_exact(id, st)`, `core._status_copy(id, default)`, `core.log` | read and publish device state (`_exact`: no grace period - the device's own system said it is offline), log |

A section of `settings.json` whose plug-in is missing or failing is kept as it is on every save,
so removing a plug-in for a while loses nothing. A section whose value cannot be stored keeps its
last stored value (and the error is logged); every other setting still saves.

**Rule actions.** `validate(body) -> entry` gets the `PUT /api/bindings/...` body (all its fields,
your own included) and returns what is stored; it must contain `"action"` (your action name) and
a string `"target"` (any id of yours - it is not a device). `run(entry) -> dict` executes it (a
worker thread). `describe(entry) -> dict` is what the rule list shows: `{"type": <your ui type>,
"title": <short text>, ...}`. A rule whose plug-in is not loaded stays stored; the dashboard lets
you enable, disable or delete it, not edit it, and `POST /api/rules/run` answers 409.

## A transport plug-in

A plug-in that brings a whole family of devices from another system (the Zigbee plug-in: the
devices of zigbee2mqtt's retained `bridge/devices`) uses these on top:

| hook | what it does |
|---|---|
| `core.registry_pending(name)` at import, `core.registry_ready(name)` once the list is in | until then the core treats no unknown id as gone: no pruning of the state cache or the retained `home/state` mirror, no reference audit (they wait up to 60 s, then skip with a log line) |
| `core.RESYNC_HOOKS.append(fn(cli, force) -> int)` | every 30 s (`force=False`) and on `POST /api/resync` (`force=True`): ask your devices to report now; return how many you asked (the answer's `asked`); it runs in a worker thread, keep it short. Skip what just reported by itself |
| `core.TRANSPORT_BINDINGS[t] = fn(src, code, gesture, rule) -> dict \| None` | a rule targeting a device of transport `t` goes here first: do it your own way (a relative brightness step the device does itself, `TOGGLE`) and return `{"target", "code", "value"}` - `code` is the channel whose auto-off and `for` timers the core then arms; return `None` for the core's generic path (absolute values from the cached state through `TRANSPORTS`) |
| `core.migrate_device_key(old, new) -> dict` | the id of a device changed in its own system (a rename in zigbee2mqtt): moves every binding (source, target, condition), favourite, room, tab order, tile size, automation, disabled-rule id, name and channel-name override, state, event log and timer; returns how many of each. The same as `POST /api/devices/relink` |
| `core.ON_RENAME.append(fn(old, new))` | after `migrate_device_key`: re-key caches of your own |

Device ids come from the other system, so register them as they are (see `register_device`) and
never re-key them silently: a rename there is a rename here, through `migrate_device_key`.

## Setup: containers, broker account, secrets, install steps

A plug-in that needs more than code puts it into `setup/`. The make targets pick it up for every
enabled plug-in (helper functions for the shell steps: `tools/envlib.sh`, `get KEY`,
`put KEY VALUE [what]` sets an empty key only, `env_set KEY VALUE` replaces one; the steps run
from the checkout root with `PLUGIN_DIR` set):

| file | when | what |
|---|---|---|
| `setup/detect.sh` | first `make install` (while `PLUGINS` is empty) | exit 0 = its hardware is here: the plug-in is put into `PLUGINS` |
| `setup/env.example` | `make install` | the plug-in's `.env` keys with their comments; the ones `.env` lacks are appended |
| `setup/install.sh` | `make install`, after the core's keys | fill the keys: generate passwords, find hardware, ask when it must (stdin may be a terminal) |
| `setup/compose.yml` | `make install`, `make update` | the plug-in's containers; `make secrets` writes `COMPOSE_FILE=docker-compose.yml:<each compose.yml>` into `.env`. Paths are relative to the checkout; gate a service with a profile (`COMPOSE_PROFILES`) when it only runs with hardware |
| `setup/secrets.sh` | `make install`, `make update` (`make secrets`), before the broker accounts | derive what the shell can: data directories (made here, so they belong to you, not to root), a profile in `COMPOSE_PROFILES` |
| `setup/broker.json` | the same, in the home container | broker accounts: `{"accounts": [{"user_key", "user_default", "password_key", "role", "topics": [...]}], "device_readonly": [...]}` - each account gets its own role with full rights on `topics`; `device_readonly` filters device accounts may read but never publish to |
| `setup/secrets.py` | the same, in the home container, with `env` (the parsed `.env`) and `ROOT` | files derived from secrets (a `secret.yaml`), and a first config copied from a template in `setup/` |
| `setup/update.sh` | `make update`, after `docker compose up` | e.g. remove a container whose profile went off (`up` never stops one) |
| `setup/preflight.sh` | `make check` | checks of its own (a free port, the hardware); exit 1 = a hard failure |
| `setup/smoke.sh` | `make test`, after the core's smoke test | is its part of the running stack healthy; exit 1 = failure |
| `setup/summary.sh` | end of `make install`, with `HOST` | where its frontend is |

Keep passwords off command lines: the shell steps read `.env` with `get`, and `secrets.py` gets
them as data.

## Reserved

A plug-in may not register the core's transport (`mqtt`), control kinds (`switch`, `bright`,
`sensor`, ...), actions (`on`, `off`, `toggle`, `bright_up`, `bright_down`, `press`), device keys
(`id`, `name`, `controls`, ...), MQTT handlers or subscriptions under `home/` other than
`home/<its own name>/` (the core's `home/cmd`, `home/state` and the `home/<device>` topics of
generic MQTT devices), `homeassistant/`, a namespace another plug-in claimed, anything starting
with `$` (`$SYS`, `$share`, `$queue`), or open paths under `/api`, `/ws`, `/plugins/`. The plug-in
`weather` may use `home/weather/...`; the plug-in that claimed `zigbee2mqtt` may use all of it.
Nor may it replace or remove what the core or an earlier plug-in registered (a hook entry, a device,
a settings section, a namespace); changing such an object in place is not detected - do not. A
route under `/plugins/<name>/` is yours (never under another plug-in's name), except one whose
path looks like a public file (`ui.js`, `i18n.json`, `static/...`): that is refused. A pattern of
yours that also covers a public file (`static/{f}`, `{p:path}`) wins over it and needs the token.

**Commands only the backend may send.** A topic your plug-in publishes and devices obey (a relay
command) should not be publishable by a device account: list it in `setup/broker.json` as
`device_readonly`, or say so in your docs and the owner adds the filter to `MQTT_BACKEND_ONLY` in
`.env` (device accounts: `MQTT_DEVICE_USERS`).

**Python packages.** A plug-in that needs a package the core does not ship lists it in its own
docs; the owner puts it into `EXTRA_PIP` in `.env` as `name==version` (space separated, no pip
options) and runs `make update`, which bakes it into the backend image. The core's own pinned
packages stay as they are (they are constraints of that install).

## Dashboard side

`ui.js` is a plain script, loaded before the first render. It registers what it adds:

```js
HomePlugins.register({
  tabs:        [{id, label, render(box), badge?(), visible?(), after?: 'fav'}],
  addMenu:     [{label, hint, button, onClick()}],
  cards:       [(cardElement, device) => { /* decorate a device card */ }],
  ruleActions: [{type, label, visible?(), html(then|null), read() -> body, chip?(then), title?(then)}],
  toolbar:     [{tab: 'auto' | <a grid tab id: 'other', 'switch', 'p:tech'...>, label, visible?(), onClick()}],
  onReload:    [async () => { /* refresh your data; at start and on every resume */ }],
  header:      [{slot: 'brand' | 'conn', render(box)}],
  onState:     [(STATE) => { /* after every state frame and every sendControl */ }],
  cardFaces:   [{match(device), render(cardElement, device), update?(cardElement, device, state)}],
  modalSections: [{match(device), html(device) -> string, wire?(box, device, later), update?(box, device, state), hideCodes?: [code]}],
  holds:       [(device, code, value) => ms],
  hides:       [(device) => true],                 /* shown elsewhere: not in the tab grids */
});
```

- `addMenu`: a row of the "Add" dialog after the core's ESPHome row (the Zigbee plug-in puts its
  pairing there); `label`, `hint` and `button` are translated.
- `tabs` with `cats: [...]` and no `render`: a device grid of those categories, like a core tab
  (the "Other" tab no longer shows them). `compact: true` packs small tiles. With `cats` AND
  `render` the categories still leave "Other": your `render` has to show those devices itself.
- `header`: each entry gets a `<span>` of its own, drawn once after the plug-ins loaded; `brand`
  sits next to the name, `conn` before "Log out". Keep it live from `onState`.
- `cardFaces`: the first face whose `match` is true draws the whole card (a click still opens
  the device modal unless `render` sets its own `onclick`); `update` is called on every state
  frame instead of the core's patching. A `render` that throws falls back to the core card.
- `modalSections`: a `<section>` of the device modal after the controls; `wire(box, device, later)`
  once when the modal opens - `later(async fn)` queues a save that the modal's OK button runs
  (in order; a throw keeps the modal open and shows the error; closing with x drops it), `update` on every frame; `hideCodes` removes those channels from the core's
  Readings and Settings lists (the section shows them its own way).
- `holds`: how long (ms) a value sent from the dashboard is shown before the device must
  confirm it; the longest answer wins, at least the core's 4 s, at most 60 s (a TV waking up).

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

Helpers: `Home.api(path, opts)`, `Home.openModal(html, id, {onClose})` (onClose runs once when it closes or another modal replaces it),
`Home.closeModal()`, `Home.modalId()`,
`Home.escapeHtml(s)`, `Home.el(tag, cls, html)`, `Home.devices()`, `Home.state(id)`,
`Home.activeTab()`, `Home.sendControl(id, code, value)`, `Home.refresh()`,
`Home.reloadDevices()` (after your plug-in added, renamed or removed devices: the device list,
favourites and tabs again), `Home.tabForCategory(cat)` (the label of the tab a category shows in),
`Home.authHeaders(h)` (the token
header for a `fetch` of your own - an image as a blob, SDP; the core never takes the token from a URL, and
`img-src` allows `blob:`), and `i18n(text)` /
`i18nPlural(key, n)`. Every call into a plug-in is guarded (a thrown error or a rejected promise
is logged in the console); the dashboard keeps working.
