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

Every sub-directory with an `__init__.py` is a plug-in, directly in `plugins/` or in a group
folder (a directory without `__init__.py`). The shipped ones are grouped by role:
`transports/` (how devices are reached: zigbee, thread, matter, webos, tizen, ir), `devices/` (a kind of device on the
dashboard over any transport: climate, tv, cameras, panels, switchbot), `sources/` (data and events: weather,
nfc, presence, fwdesc), `tools/` (the rest: map, voice, family, history, watchdog, player, stats ...;
`plugins/README.md` lists them all). The group is only a folder: the name is unique across all of them, and
`PLUGINS`, `plugin-data/<name>/` and `/plugins/<name>/` use the name alone. Names: lowercase letters, digits and `_`,
starting with a letter, at most 32 characters; names starting with `_` are skipped (helpers and `_example`).

**A plug-in brings what it needs.** A file `requires` in the plug-in (one plug-in name per line,
`#` comments) lists the plug-ins it cannot work without - typically its transport. Naming the
plug-in in `PLUGINS` switches those on too (and theirs): their keys are merged into `.env`, their
containers and install steps run with `make update`, and they load before it. A requirement that
is missing, fails to load or is part of a circle keeps the plug-in off with the reason in Settings;
so does a `requires` file that cannot be read or holds anything but one name per line, and a
plug-in whose requirement is off. One resolver, `tools/pluginlib.py`, decides for the backend,
`make install` / `make update` (python3 on the host, which `make check` asks for) and the broker
accounts; install and update print why a plug-in stays off. In `/api/plugins` a
plug-in switched on this way has `required_by`. Of the shipped plug-ins only `player`, `thread` and `matter` require another
(`computers`). A shared tab is declared by each plug-in that fills it (`tabs` with the same `id` become one tab).

**Always on: map.** The rooms of the house are the map's (`pluginlib.REQUIRED`): it loads whether
`PLUGINS` names it or not (`required_by` "core"), Settings shows it ticked and greyed, "always on",
and the helper's state says `required`. Missing or failing, it is a red row in Settings -> Overview.

They load in alphabetical order after the core, a requirement before what requires it. A plug-in
that fails to import - or registers something it may not (see "Reserved") - is taken back completely (its hooks, devices, routes, namespace),
logged, and listed under Settings -> Plug-ins and update with the error; the rest of the house keeps running.

```
plugins/
  devices/          # a group folder (or put the plug-in straight into plugins/)
    hello/
      __init__.py     # the Python side: registers hooks when imported
      requires        # optional: plug-ins switched on and loaded before it, one name per line
      ui.js           # optional, public: the dashboard side
      i18n.json       # optional, public: {"ru": {"English text": "translation"}}
      static/         # optional, public: icons, css, pages ... (.js .mjs .css .json .png .svg .html)
      discover.py     # optional, never served: a discovery module for the core's discover container
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
house: `HOUSE_DIR`, by default the checkout, git-ignored there) for what you write, in
`core.plugin_state(name)` (`plugin-state/<name>/`, never in a house's git) for what the plug-in writes
itself, in `.env`, or in a data directory `.env` points at. So a plug-in folder can
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

PLUGIN = {"name": "Hello", "version": "1.0.0", "icon": "puzzle"}   # shown in Settings; icon: a name of the dashboard's set;
                                                                   # "menu": False keeps its pages out of the Settings menu;
                                                                   # "menu_group" is passed on in GET /api/plugins
```

| hook | what it does |
|---|---|
| `core.app` | the FastAPI app: add routes with `@core.app.get(...)`; they need the token like every route |
| `core.register_device(dev)` | add a device the plug-in owns (`id`, `name`, `transport`, `category`, `controls`, plus keys of its own starting with `_`); refuses an id that already exists and a transport another plug-in registered (`TRANSPORTS`). A brightness channel and the switch it dims become confirm channels (docs/mqtt-contract.md): set `confirm: false` on one your system never reports. The id is one MQTT topic level and one URL path segment: 1-256 printable characters without `/ + # \|` (spaces and any script are fine: a zigbee2mqtt friendly_name) |
| `core.update_device(dev)`, `core.remove_device(id, transport)` | change or drop a device the plug-in registered itself (same id and `transport`; never a core device or another plug-in's: the core remembers which plug-in registered it); its state is kept on update, dropped on removal (`keep_state=True`: kept until the cache is pruned - a device that only left its system's list for a moment) |
| `core.TRANSPORTS[t] = fn(cfg, code, val, kind)` | switch a device whose `transport` is `t` (worker threads: bindings, schedules, timers; also `/control` when there is no async variant). Raise `HTTPException(503)` when the device cannot be reached |
| `core.ASYNC_CONTROL[t] = async fn(cfg, code, val)` | the same for `POST /api/devices/{id}/control`, on the event loop |
| `core.CONTROL_KINDS[k] = fn(cfg, ctl, val) -> dict` | a stateless channel kind (an IR button): no value stored; rules use the action `press` |
| `core.MQTT_SUBSCRIPTIONS.append(filter)` | a topic filter under a topic of your own (see "Reserved"), subscribed after the core's on every (re)connect, in list order (the broker replays retained messages in that order: a device registry before its state frames) |
| `core.MQTT_HANDLERS.append((prefix, fn(parts, payload, retain)))` | messages whose topic starts with the `prefix` tuple; `retain` is True for a replayed message - an event handler should ignore those |
| `core.NODE_SUBTOPICS[name] = fn(parts, payload, retain)` | what a node says of itself under `<node>/<name>/...`, for ANY node (`parts[0]` is its topic prefix, never `home` or a claimed namespace): the core subscribes `+/<name>/#` with the plug-in topics; the retained replay arrives on every connect |
| `core.DEVICE_REMOVED.append(fn(device))` | a device left the house: a generic-MQTT one removed (Add ESPHome -> Remove) any plug-in's `remove_device` without `keep_state`, or a Zigbee device unpaired or out of the z2m list for 10 min (a rejoin or a rename brings it back sooner); a relink keeps state and fires nothing; the TV plug-in has no removal: forget what you kept for it, clear its retained topics |
| `core.WIRING_CHANGED.append(fn(entries))` | the wiring list (README "Wiring") was stored, or read at start; called after it, outside the wiring's lock: re-read what it says |
| `core.WIRING_MIGRATORS.append({name, files, entries})` | what a plug-in kept before the wiring list, taken over at the first start the plug-in is on (each plug-in by itself, recorded in `.wiring-migrated`): `files` are backed up (with their sha256) before anything is written, `entries()` are merged by key, kept even when a device is not known (yet). Once taken over (`core.wiring_owns(name)`), the plug-in reads `core.wiring()` only and never writes its old copy again: the old fields stay as they were |
| `core.DISCOVERY_IGNORE.append(fn(names))` | `names` = a discovered node's name and the first topic levels of its channels; return True when the node is yours (it has a description or a pairing): the add dialog does not offer it and its retained discovery configs are not read |
| `core.MQTT_NAMESPACES.add(top)` | claim a whole top-level topic (`zigbee2mqtt`): only you may subscribe or handle under it, a discovered ESPHome node may not point into it (a hand-written `mqtt_devices.json` entry is yours: do not point one there, it would take those frames first). A plug-in that loaded earlier and already subscribes there makes the claim fail. Not the core's own (`home`, `homeassistant`, `$...`), one plain level |
| `core.MQTT_SHARED_READ.append(filter)` | inside your namespace, topics a generic `mqtt_devices.json` device may read as its state (a screen's own sensors): the device gets the frame and your handler still does; nothing may publish there |
| `core.TOKEN_CHECKS.append(fn(token, method, path) -> bool)` | accept a token your plug-in issued (a key per kiosk panel, revocable one by one) for THIS request: `method` is the HTTP method (`WS` for the WebSocket), `path` the URL path - admit only what that client needs; only an exact `True` admits, an exception refuses. The master token never reaches your check |
| `core.KEY_REFUSALS.append(fn(token, method, path) -> str \| None)` | a key your plug-in issued, known but not for this route: return a reason and the request gets 403 with it (not 401, which tells a client its key is wrong and it is signed out); `None` = not yours, the plain 401 |
| `core.WS_FILTERS.append(fn(websocket, payload) -> payload)` | trim the /ws snapshot ({device id: status}) for the caller behind `websocket` (its headers and query, where a browser puts the token) - return the dict to send; runs for every frame built (at most every 0.25 s, shared by the connections whose headers and query are equal), keep it cheap. It must depend on the headers and the query only (not on `websocket.client` or other state): the shared frame is keyed on them |
| `core.is_house_token(headers) -> bool` | the request carries the house token itself, not a token a plug-in issued (a phone, a panel): guard what only the dashboard may do |
| `core.is_admin_request(request) -> bool`, `core.ADMIN_CHECKS.append(fn(request) -> bool)` | an administrator of the house calls: the house token, or a key an `ADMIN_CHECKS` fn returns True for (the family plug-in: a phone marked Administrator); never a guest `yg_` key, a failing fn counts as no. Guards what lets people in (the guest answers) |
| `core.RESTRICTED`, `core.RESTRICTED_ALLOW[(method, path)] = "view" \| "control"`, `core.RESTRICTED_ROLE.append(fn(token, method, path) -> "view" \| "control" \| None)`, `core.restricted_role(headers, query, method, path)`, `core.RESTRICTED_CHECKS.append(fn(method, path, params, body) -> why \| None)`, `core.RESTRICTED_REFUSED`, `core.RESTRICTED_BODY_MAX`, `core.RESTRICTED_WS_MAX` | restricted mode (`YASHOME_RESTRICTED=1`, `YASHOME_DEMO=1` is read too; the public demo plug-in turns it on and fills the table): a route runs only when `RESTRICTED_ALLOW` names its method and route path (`WS` for a WebSocket); "control" ones only for the guest in control; every other route answers 403 `RESTRICTED_REFUSED`, so a new route is refused until it is listed. A body over `RESTRICTED_BODY_MAX` is 413 before it is parsed; a `RESTRICTED_CHECKS` answer is 400 (an exception refuses too); `GET /api/plugins` names no paths and no errors. `RESTRICTED_ROLE` tells whose demo session a token is; in demo mode `is_house_token` is true for the guest in control, and for a watcher only on a route listed "view"; a session holds at most `RESTRICTED_WS_MAX` WebSockets; the house has English only. In the page `window.RESTRICTED` is true and the map is read-only; a plug-in lets one session edit it with `window.RESTRICTED_EDIT = () => true` while it may, firing `restricted-role` on `window` when that changes |
| `core.ON_EVENT.append(fn(event))` | called inline with every event of the house: `{kind: state | command | rule | offline | online | ..., device, code, old, new, source, ts}`; keep it fast, it must not raise (the history plug-in uses it) |
| `core.GUEST_NOTIFY.append(fn(req))` | called inline when a browser asks to come in (`/guest`): `{id, code, ua}`, no secret; keep it fast (hand sending to a thread), it must not raise (the family plug-in tells the administrators' phones) |
| `core.ON_REPORT.append(fn(dev_id, values, ts))` | called inline with every report a device makes itself (not a command's echo): the channels it carries and their values now, and when; keep it fast, it must not raise (the watchdog learns from it how fast each channel answers its commands) |
| `core.room_reading(room id, "temperature" \| "humidity") -> {dev, code, value} \| None`, `core.room_sensor_order(room id, kind) -> {pick, order}` | a room's temperature or humidity as the dashboard shows it: the owner's hand choice alone (silent: None, Settings -> Overview says why), else auto - the room's channels of that kind but never a module's own board (`advanced`, an AC's built-in one), a sensor outdoors (a device marked `outdoor`, or one with a wind or rain channel) nor a copy of a sensor the room has; an AC's room channel with no value is no option; marked `preferred` first, then by id. `order` is what is tried, the first live one reads |
| ui.js `deviceSettings: [{category, transport, label, list(), card(item, box, reload), actions?(box, reload)}]` | a kind's own settings of its devices on one page per category: Settings -> TVs draws a card per TV with the kind in its title (the TV plug-in) |
| ui.js `window.MapMarks.push(fn(device id) -> {glyph, title} \| null)` | a mark on a device's icon of the home map (the map plug-in's README); the thread plug-in marks Thread routers |
| ui.js `windows: [{id, open(arg)}]` | a window of this plug-in that another one opens by name: `HomePlugins.openWindow(id, arg)` (false when none has it); the IR plug-in has `ir.remote` (a remote's keys) and `ir.appliance` (which remote and blaster) |
| a device's `virtual: "person" \| "presence" \| "tag"` | no thing in the house: All devices does not list it, its window says what it is and opens its page (Family, NFC), with no room, favourite, press log or answer time |
| `core.extend_device(dev_id, part, channels, transport, info=None, cfg=None, map=None)`, `core.drop_part(dev_id, part)`, `core.PARTS` | a part of a device another plug-in owns (an IR remote of a TV, a tablet's microphone and speaker): docs/devices.md |
| `core.MERGE_PLAN.append(fn(old, new) -> {what: count})`, `core.DEVICE_MERGED.append(fn(old, new))`, `core.merge_plan(old, new)`, `core.aliases(dev_id)`, `core.merged(old)` | merging two devices of one thing: a plug-in's own references in the dry run, its move after the merge (the owner of old stops registering it), the former ids (docs/devices.md) |
| `core.HEALTH_CHECKS.append(fn() -> [row])` | rows for Settings -> Overview: `core.health_row(id, state, title, detail="", fix="", args=(), link="", cmd="")`, state `ok` / `warn` / `bad`, link a Settings place (`devices/forgotten`) the row opens, cmd host commands shown under More (never translated); title, detail and fix are English words the dashboard translates (`{0}`... take `args`, numbered across the three). Keep it quick: all checks share a 5 s budget, a slow one becomes a "did not answer" row |
| `core.mqtt_command(dev_id, code, payload, topic="")` | run one command in the `home/cmd` payload language (`true`/`false`/`on`/`off`/`toggle`/`up`/`down`/number, bytes) through the device control like the core bus does - for a plug-in that relays commands from its own topics after checking who may send them |
| `core.ON_MQTT_CONNECT.append(fn(cli))` | called after every (re)connect to the broker, once the subscriptions are in: republish retained topics of your own (a layout a device reads); an exception is logged |
| `core.STARTUP_TASKS.append(fn)` | started once the broker is up, before the timers are replayed (nothing waits for it); a coroutine function runs as a task, a plain callable in a thread of its own - either may loop forever; errors, `sys.exit` included, are logged and shown in Settings -> Overview, and the task starts again (5 s, doubling up to 5 min; one that returns stays ended). Iterate `core.DEVICES` over a snapshot (`list(core.DEVICES.values())`): the MQTT thread adds and drops devices meanwhile. Timers of your devices may fire before your task has run: a transport that is not ready yet should raise (the core logs it and retries) |
| `core.SETTINGS_SECTIONS[key] = fn()` | a settings section the plug-in owns (must be JSON: dict, list, str, number; not a core key), kept in its own `plugin-data/<name>/settings.json` - a file name the core reserves - (a section of a loaded plug-in left in `settings.json` moves there at the next start; if both copies exist and differ, the plug-in's file wins and both are kept as `*.pre-split-<time>`; a section found in two plug-ins' files stops all saves until one is removed; a section another plug-in takes over leaves the old owner's file); read it back with `core.cfg_section(key, default)`. `setup/settings.example.json` shows its shape. A route of yours that writes it should call `core.save_settings()` like the core does |
| `core.BINDING_ACTIONS[a] = {validate, run, describe}` | a rule action other than switching a device - see below |
| `core.DEVICE_FIELDS.append(key)` | an extra device key `GET /api/devices` passes to the dashboard |
| a device's `wakes: true` (with `DEVICE_FIELDS.append("wakes")`) | a device that sleeps and is woken over the network (a TV: Wake-on-LAN): a command to it is never refused as offline, and the TV card holds an "on" 12 s while it wakes |
| a device's `no_enforce: [code, ...]` | channels whose state may change by itself (a TV that wakes on its own): the watchdog neither re-sends a command to them nor reports one as not taken |
| `core.plugin_data(name)` | the directory for your house's configuration files (device lists, builds): `HOUSE/plugin-data/<name>/`, created on first use - keep nothing of yours next to the core's files |
| `core.plugin_state(name)`, `core.plugin_state_file(name, file, merge=None)` | the directory for what the plug-in writes itself (paired keys, anti-replay counters): `HOUSE/plugin-state/<name>/`, outside a house's git; `plugin_state_file` also moves a copy found in `plugin-data/<name>/` there once |
| `@core.open_route` | above `@core.app.post(...)`: that one route of yours (not other paths of the same function) is served without the token (a device pairing before it has one); only a route the plug-in adds itself, decided after routing. Guard it yourself (a pairing window) |
| `@core.use_route` | above `@core.app.post(...)`: that one route of yours is using the house (a device, a rule, the player, a question), not setting it up: a key that is not the house token (a full-access phone) may call it. Every other route that changes something stays with the house token. `core.is_use_route(method, path)` answers for a request |
| `core.request_token(headers, query=None)` | the key a request carries: `Authorization: Bearer`, else `X-Token`, else `?token=` when `query` is given (a WebSocket). A plug-in that issues keys of its own (panels, family) reads them with it |
| `core.Body` | the base of a request body model (`class MyBody(core.Body)`): no string in it is longer than 4096 characters unless its field says more (`Field(max_length=...)`); a longer one is refused with 422 before your route runs |
| `core.OPEN_PATHS.add(path)` | a page of the plug-in served without the token (its API calls still carry it); never `/api...`, `/ws` or `/plugins/...` |
| `core.store_status(id, st)`, `core.store_status_exact(id, st)`, `core.status_copy(id, default)`, `core.log` | read and publish device state, log. `store_status`: the device said something (online: it is live); `codes=` the channels this report answers (a command in flight on another one is not): pass every channel of a per-channel message, only the changed ones of a frame that carries the whole device (zigbee2mqtt); left out, the changed values count. `store_status_exact`: a word about it as given, no grace period - offline at once; an online word after an outage makes it wait for its report (docs/device-state.md). A device that never reports (an IR appliance: its blaster's word is all there is) stores its online with `store_status` |
| `core.get_status(id) -> dict` | a device's state as stored (the live dict: read it, never change it; `status_copy` for a copy); `{"online": False, "error": "no data yet"}` before the first |
| `core.get_rooms() -> list` | the dashboard's rooms `[{id, name, devices: [ids]}]` as stored (a copy: read it, never change it) |
| `core.served_room(dev) -> str` | the id of the room a device serves: its Serves pick, else the first room it stands in (`devices`); '' for none. Voice, room tabs and family rights go by it; the map by where it stands |
| `core.served_devices(rid) -> list` | the devices that serve a room: those standing in it that serve no other, then those picked to serve it (`GET /api/rooms` gives it as `serves`) |
| `core.rooms_edit(fn) -> list` | change the rooms in one step: `fn(rooms)` edits a deep copy in place (an exception changes nothing), the result is cleaned and saved |
| `core.set_room_locked(devices, rooms=(), hidden=())` | the devices, the rooms a plug-in owns and the rooms the tabs hide (kept only for what names them), replaced in one step: the room API answers 409 when such a device would move or such a room (or one holding them) would be deleted; the dashboard shows the devices' room read-only and hides an empty room of the set from its tabs (`core.ROOM_LOCKED`, `core.ROOM_ZONED`, `core.ROOM_HIDDEN` hold them: read only) |
| `await core.control(id, core.ControlBody(code=..., value=...))` | switch a device like `POST /api/devices/{id}/control` does; from a thread: `asyncio.run_coroutine_threadsafe(..., core.main_loop)`. It raises what the route answers (`HTTPException` 400, 404, 409, 500, 502, 503): catch it |
| `core.MQTT_HOST`, `core.MQTT_PORT` | the broker, for a connection of the plug-in's own: `core.mqtt_auth(cli).connect(core.MQTT_HOST, core.MQTT_PORT)` (the backend's account) |
| `core.node_broker_host(request) -> (host, warning)`, `core.node_broker_port()` | the broker address a device on the LAN is given (`BIND_ADDRESS`, else the request's host; the warning says when that may not reach) |
| `core.CHECKOUT` | the core's checkout directory (its `tools/`) |
| `core.hostsock` | the house's helpers on its host, on socket files in `/run/yashome` (`tools/hostsock.py`): `there(name)`, `datagram(name, data)`, `line(name, text, timeout)`, `http(name, method, path, body, timeout) -> (status, json)` |
| `core.wake_on_lan(mac)` | a Wake-on-LAN magic packet to that MAC (`aa:bb:cc:dd:ee:ff`): to the host relay (`/run/yashome/wol.sock`, `tools/host-sockets.sh`) first (a container cannot broadcast to the LAN; the relay is `plugins/transports/webos/host/wol-relay.py`), then the broadcast; blocking, a few ms |

The wiring list: `core.wiring()` (a copy), `core.wiring_put(entries, check=True)` (the whole list; check: a new or changed entry
must name a switch channel of the house, and a dev target that exists), `core.wiring_edit(change, check=True)` (change(list) -> list
under one lock), `core.WiringBroken` (raised while wiring.json is lost or broken after the migration, or while a migration waits) and
`core.wiring_postponed()` (True while a plug-in's migration waits for a backup: the plug-in keeps writing its own old copy) and
`core.wiring_owns(name)` (True once the list speaks for that plug-in and is readable; False before the start read it, while its
migration waits, or while wiring.json is lost or broken: the plug-in reads its old copy then). A device key change
(`migrate_device_key`, a relink) moves the list's entries of that key; a removed device keeps its entries.

`core.__all__` lists every public name above and below; a test keeps it equal to this page.

**The rest of the plug-in API** is the list `core.PLUGIN_API`: names a plug-in may use besides the hooks
and helpers above - device state (`status_copy`, `store_status`, `store_status_exact`, `status_cache`,
`cache_lock`, `events`, `events_lock`), the device lists (`wait_registry`: every plug-in's list is in; `registry_known`: whether it is, now; `plugins`: the manifests of the loaded plug-ins), the broker (`mqtt_client`, `mqtt_pub`, `mqtt_auth`, `paho`,
`retained_values`, `retained_topics`, `register_mqtt_device`, `mqtt_devices_upsert`, `mqtt_resubscribe_generic`,
`mqtt_publish_state`), settings and rules (`cfg_section`, `save_settings`, `settings_lock`, `settings_bad`,
`load_failed`, `bindings`, `bindings_lock`, `favorites`, `autos`, `find_room`, `safe_execute_binding`,
`fire_sensor_triggers`, `inching_hook`, `deferred_active`, `cancel_deferred`, `chan_of`, `safe_controls`, `sensor_last` / `sensor_since` (the last broker value of a binary
sensor, and since when), `bind_origin` ((target, channel) -> the rule that last switched it), `is_offline`, `rule_enabled`, `rules_disabled` (the ids of switched-off rules: a plug-in that renames a rule moves its id), `orphan_refs` (the stored references to
devices that do not exist; `core.forgotten()` the same per gone device, `core.VIRTUAL_PREFIXES` the map's ids that are no device), `data_ref_kinds` (which of their kinds are data only), `sent_commands` (the commands the channels were sent lately: device, code, value, the value reported before, time, cause), `emit_event` (tell the `core.ON_EVENT` listeners something happened: a dict with `kind` and `device`), `quiet_device` (the true edges of a device's binary sensors are not trusted for N seconds: a radar sees a phantom target after a settings write; the edge is judged again when the time ends), `prune_data_refs` (called with device keys: removes the data-only ones: names, favourites, rooms, tab order, tile sizes), `prune_state_mirror` (clears the retained
`home/state/<id>/*` of devices that are gone),
`check_topic_name`, `json_state`, `device_owner`), the request (`house_token_only`, `same_origin`), the
page policy (`csp`: the Content-Security-Policy every page gets; a plug-in page that needs one more source sets a changed copy on its own response) and the
rest (`atomic_json_dump`, `bg`, `main_loop`, `helper` (method, path, body, timeout): one call to the helper, as Settings makes it, e.g. `/v1/hosts/check`). Each resolves when it is read, so a value the core replaces
later (the broker client after a reconnect, the rules after a reload) is the current one. A name with a
leading underscore (`core._x`) is the core's own and may change in any version: a test fails when a
shipped plug-in uses one (also through `getattr`, `vars()` or `from app import _x`).

The core's own sections (names, rooms, bindings, automations...) are registered with `_store()` in
`core/hooks.py`: how one is cleaned on load, saved, how a renamed device's key moves inside it and which device
keys the startup audit checks; its key is also listed in `_CORE_SECTIONS`. `rules_disabled` registers
after `automations`: a rename drops the disabled ids of the rules the merge lost, which those find.

A section of `settings.json` whose plug-in is missing or failing is kept as it is on every save,
so removing a plug-in for a while loses nothing. A section whose value cannot be stored keeps its
last stored value (and the error is logged); every other setting still saves.

**Rule actions.** `validate(body) -> entry` gets the `PUT /api/bindings/...` body (all its fields,
your own included) and returns what is stored; it must contain `"action"` (your action name) and
a string `"target"` (any id of yours - it is not a device). `run(entry) -> dict` executes it (a
worker thread). `describe(entry) -> dict` is what the rule list shows: `{"type": <your ui type>,
"title": <short text>, ...}`. `"house_token_only": True` in the `BINDING_ACTIONS[a]` dict, beside
`validate` and `run`: only the dashboard's own token stores, runs, enables or deletes such a rule (not
a phone with full access). `run` gets `"_forced": True` in the entry when the rule list's Run fired it
(no time window, no conditions). A rule whose plug-in is not loaded stays stored; the dashboard lets
you enable, disable or delete it, not edit it, and `POST /api/rules/run` answers 409.

## A transport plug-in

A plug-in that brings a whole family of devices from another system (the Zigbee plug-in: the
devices of zigbee2mqtt's retained `bridge/devices`) uses these on top:

| hook | what it does |
|---|---|
| `core.registry_pending(name)` at import, `core.registry_ready(name)` once the list is in | until then the core treats no unknown id as gone: no pruning of the state cache or the retained `home/state` mirror, no reference audit (they wait up to 60 s, then skip with a log line) |
| `core.STATE_ASK[t] = fn(id) -> bool` | a device of transport `t` that came back (or waits since the start) and did not report in 5 s: ask it once to report now; return whether it was asked (docs/device-state.md). It runs on the core's liveness thread, one device after another, only while the broker is connected, at most 10 a tick; an exception is logged. Keep it short: a slow one holds up the offline rule of every other device |
| `core.RESYNC_HOOKS.append(fn(cli, force) -> int)` | every 30 s (`force=False`) and on `POST /api/resync` (`force=True`): ask your devices to report now; return how many you asked (the answer's `asked`); it runs in a worker thread, keep it short. Skip what just reported by itself |
| `core.TRANSPORT_BINDINGS[t] = fn(src, code, gesture, rule) -> dict \| None` | a rule targeting a device of transport `t` goes here first: do it your own way (a relative brightness step the device does itself, `TOGGLE`) and return `{"target", "code", "value"}` - `code` is the channel whose auto-off and `for` timers the core then arms; return `None` for the core's generic path (absolute values from the cached state through `TRANSPORTS`) |
| `core.migrate_device_key(old, new) -> dict` | the id of a device changed in its own system (a rename in zigbee2mqtt): moves every binding (source, target, condition), favourite, room, tab order, tile size, automation, disabled-rule id, name and channel-name override, state, event log and timer; returns how many of each. The same as `POST /api/devices/relink` |
| `core.ON_RENAME.append(fn(old, new))` | after `migrate_device_key`: re-key caches of your own |
| `core.check_name(name, kind, taken=(), most=60, id_like=False)` | every name typed in the dashboard goes through it before it is stored: trimmed, not empty, at most `most` characters, no control character, none of `taken` in any case (409), `id_like` (an id or a topic) only `[A-Za-z0-9_-]`; returns the trimmed name, else 400/409 with an English text that `i18n.js` translates |
| `core.set_device_name(id, name)` | the one way a display name changes (the card's pencil, `POST /api/devices/<id>/rename`): a plug-in that keeps the name stores it, else the override in `settings.json` "names"; never both, so the tile, the card, voice and the map show one name |
| `core.ROOMS_OWNER.update(create=fn(name) -> room, rename=fn(rid, name) -> room, delete=fn(rid))` | the rooms are yours (the map): `POST`, `PUT` (a new name) and `DELETE /api/rooms` call these instead of changing the rooms themselves; raise HTTPException for a refusal. Without an owner the core keeps the rooms itself and logs a warning |
| `core.ON_ROOM_MOVE.append(fn(dev_id, old_room_names, new_room_name))` | after a device came into a room (moved, placed on the map, its room renamed): the zigbee plug-in renames a device whose name is still the automatic one |
| `core.ROOM_REFS.append(fn(rid) -> [{kind, name}])` | what of yours names a room (a panel hangs in it, a satellite stands in it): `GET /api/rooms/{id}/refs` lists it before a delete |
| `core.own_names(load, find, save, lock=None) -> owner` | your devices' names live in your own records (a TV's entry, an IR tile): one call makes them the one stored name. `load()` -> your data, `find(data, id)` -> the record (a dict with "name") or None, `save(data)` stores it; a rename (the card's pencil) lands in the record, and at the call a rename already in the overrides moves into it (`owner.adopt()` again). Call it once your settings section is registered |
| `core.NAME_OWNERS.append(fn(id, name) -> bool)` | your plug-in keeps the display names of its devices (an IR tile in its record): store the new one and return True; the core then keeps no override for it |
| `core.take_name_override(id) -> str or None` | at start, take over a rename saved in the overrides before you kept names yourself: returned and dropped there |
| `core.house_lang() -> str` | the house language (Settings -> House language, a code like `"ru"`, see `GET /api/house` "langs"): what a plug-in speaks or prints for the whole house |
| `core.wait_registry(timeout) -> bool` | blocks (a thread, not the event loop) until every plug-in's device list is in (`registry_ready`); False on timeout |

Device ids come from the other system, so register them as they are (see `register_device`) and
never re-key them silently: a rename there is a rename here, through `migrate_device_key`.

## Discovery module

What must run in the host's network - a UDP broadcast, the host's neighbour table - is a discovery
module, not a container of its own: a broadcast from the backend's bridge network reaches nobody. A
plug-in ships `discover.py` next to its `__init__.py`; the core's `discover` container
(`docker-compose.yml`, `discover/runner.py`, always there) runs the module of every enabled plug-in
(`PLUGINS` and `requires`, resolved by `tools/pluginlib.py` as for the backend). Turning the plug-in
on or off and `make update` (Settings: Apply) recreates the container with the new set; none on: it idles.

| part | what |
|---|---|
| `run(ctx)` | the module's only entry point; it loops until `ctx.stop` is set, then returns |
| `ctx.name` | the plug-in's name |
| `ctx.state` | `Path` of `plugin-state/<name>/` (made before the start): the files it writes for the backend, which reads them with `core.plugin_state(name)` |
| `ctx.stop` | a `threading.Event`, set when the container stops |
| `ctx.wait(s)` | sleeps up to `s` seconds; True = stopping, return now |
| `ctx.log(msg)` | one line in `docker compose logs discover`, prefixed `discover[<name>]` |

Each module runs in a thread of its own and is read again at every start. One that raises or returns is
logged and started again after 5 s, the pause doubling up to 5 minutes (back to 5 s after a run of a
minute or more); the other modules go on. The module is standard library only (`python:3.12-alpine`), never
imports the backend (`app`, `core`) and reads its settings from the environment the container has
(`PLUGINS`, `DASHBOARD_PORT`, `TZ`); the container runs as the house's user with no capabilities, a
read-only root, the code read-only and `plugin-state/` as the only writable folder. Ports and wire formats
are the module's own; keep them fixed, the devices on the network expect them. Examples:
`plugins/tools/computers/discover.py`, `plugins/devices/panels/discover.py`.

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
| `setup/broker.json` | the same, in the home container | broker accounts: `{"accounts": [{"user_key", "user_default", "password_key", "role", "topics": [...], "read": [...], "hidden": [...]}], "device_readonly": [...]}` - each account gets its own role with full rights on `topics` (its own bus: never under `home/`, `homeassistant/` or a wildcard at the top), read-only rights on `read` (for example `home/state/#`) and, inside its own bus, publish-only rights on `hidden` (it never receives what others publish there: a code a device shows); `device_readonly` filters device accounts may read but never publish to; `"accounts_file": "<file>"` names accounts the plug-in makes itself at runtime (one per ESP screen): a JSON list of `{user, password, topics, read, hidden}` in `<HOUSE_ROOT>/plugin-state/<plugin>/<file>`, each with a role named after its user and the same topic rules; the backend may add them to the running broker over `$CONTROL/dynamic-security/#` (its role has that right) |
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

`ui.js` is a plain script, loaded before the first render. The dashboard's own script runs in a scope of its
own: a `ui.js` reaches it through `Home` and `HomePlugins` (`closeModal` stays global for `onclick=` markup;
`i18n` and `LANG` come from `i18n.js`, `PW` from the panels plug-in's `static/widgets.js`). It registers what it adds:

```js
HomePlugins.register({
  tabs:        [{id, label, render(box) | cats: [category], badge?(), visible?(), after?: 'fav', hidden?: true, row?: 'places'}],  /* row: 'places' = the top row (Favourites, this tab, the rooms) instead of the row of types; hidden: not in the tab bar; Home.openTab('p:<id>') opens it */  /* same id = one tab: cats joined, the rest from the first; share a tab with cats, label and after only */
  addMenu:     [{label, hint, button, onClick(), scan?: true}],
  addFlow:     [() => { /* the Add button opens this window instead of the Add menu (the first one wins) */ }],
  cards:       [(cardElement, device) => { /* decorate a device card */ }],
  ruleActions: [{type, label, visible?(), html(then|null), read() -> body, chip?(then), title?(then)}],
  toolbar:     [{tab: 'auto' | <a grid tab id: 'other', 'switch', 'p:tech'...>, label, visible?(), onClick(), help?: {text, more?}}],  /* help: its entry in the help sheet (the dragged "?" finds it too) */
  onReload:    [async () => { /* refresh your data; at start and on every resume */ }],
  header:      [{slot: 'brand' | 'conn', render(box)}],
  ask:         [{open(text), examples?()}],   /* the header's Ask the house (the first one answers): Ask and a field right of the logo; text '' = just open;
                                               examples: strings dropped under the empty field, a click sends one */
  onState:     [(STATE) => { /* after every state frame and every sendControl */ }],
  cardFaces:   [{match(device), render(cardElement, device), update?(cardElement, device, state)}],
  modalSections: [{match(device), html(device) -> string, wire?(box, device, later), update?(box, device, state), hideCodes?: [code], order?: number}],
  holds:       [(device, code, value) => ms],
  hides:       [(device) => true],                 /* shown elsewhere: not in the tab grids */
  settings:    [{subject?, title, expl?, icon?, tab?, tabs?, needs?, render(box)}],   /* a tab of a Settings page, drawn each time it opens;
                                               subject: the id of a core page (rooms, family, weather, zigbee, ir, tv, panels, nfc,
                                               computers, history, watchdog, voice, fwgen ...: ST_SUBJECTS in web/js/35-settings.js)
                                               it joins as a tab; without one it gets a page of its own in the group Other;
                                               expl = its one line in the menu; needs: ["METHOD /path"] it greys in restricted mode */
  help:        [{id, screen, sel, title, text, more?, at?: 'label', screenName?}],   /* README "Help"; screen: 'tab:p:<id>', 'settings:<English title of your page>', 'device', or a name of your own window with screenName (its title in the sheet) */
});
```

- `addMenu`: a row of the "Add" dialog after the core's ESPHome row (the Zigbee plug-in puts its
  pairing there); `label`, `hint` and `button` are translated. `scan: true`: what the row adds is found by the
  `connect` plug-in's Add / bind window by itself, so that window does not list the row under "Other ways".
- `addFlow`: one window that replaces the Add menu (the `connect` plug-in: every way at once); `Home.openAdd()` opens
  whichever the Add button opens.
- `tabs` with `cats: [...]` and no `render`: a device grid of those categories, like a core tab
  (they are not on the "Other" tab). `compact: true` packs small tiles. With `cats` AND
  `render` the categories still leave "Other": your `render` has to show those devices itself.
- `header`: each entry gets a `<span>` of its own, drawn once after the plug-ins loaded; `brand`
  sits next to the name, `conn` at the right end of the header, before "Add device". Keep it live from `onState`.
- `cardFaces`: the first face whose `match` is true draws the whole card (a click still opens
  the device modal unless `render` sets its own `onclick`); `update` is called on every state
  frame instead of the core's patching. A `render` that throws falls back to the core card.
- `modalSections`: a `<section>` of the device modal after the controls; `wire(box, device, later)`
  once when the modal opens - `later(async fn)` queues a save that the modal's OK button runs
  (in order; a throw keeps the modal open and shows the error; closing with x drops it), `update` on every frame; `hideCodes` removes those channels from the core's
  Readings and Settings lists (the section shows them its own way); `order` (default 0, lower first) places it among the sections.
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
- Labels are English source text; `i18n.json` adds translations (read again when the file changes, like `ui.js`:
  an edited dictionary shows after a reload of the page, no restart). It never replaces a core word:
  a key the core already translates keeps the core's translation.
- The values of a `trigger_text` control (events such as `arrival`) show as they are in the rule
  editor; `"gesture:<value>"` keys in `i18n.json` translate them (house data such as card names stays as is).

Helpers: `Home.api(path, opts)`, `Home.openModal(html, id, {onClose})` (onClose runs once when it closes or another modal replaces it),
`Home.closeModal()`, `Home.modalId()`,
`Home.escapeHtml(s)`, `Home.el(tag, cls, html)`, `Home.devices()`, `Home.rooms()` (`[{id, name}]`), `Home.state(id)`,
`Home.activeTab()`, `Home.sendControl(id, code, value)`, `Home.refresh()`,
`Home.reloadDevices()` (after your plug-in added, renamed or removed devices: the device list,
favourites and tabs again), `Home.tabForCategory(cat)` (the label of the tab a category shows in),
`Home.openEsphomeAdd()` (the core's "Add ESPHome" dialog), `Home.openDevice(id)` (the window of that device),
`Home.toast(text)` (a short note at the bottom), `Home.rules()` (every rule, as Automations lists them),
`Home.ruleCard(rule)` (its card: edit, run, switch, delete), `Home.newRule(preset)` (the rule window; `{nfc: '<tag>'}`
starts it on a tap of that tag), `Home.watchRules(fn)` (fn() after a rule changed; returns the unwatch),
`Home.authHeaders(h)` (the token
header for a `fetch` of your own - an image as a blob, SDP; the core never takes the token from a URL, and
`img-src` allows `blob:`), and `i18n(text)` /
`i18nPlural(key, n)`. Every call into a plug-in is guarded (a thrown error or a rejected promise
is logged in the console); the dashboard keeps working.

`core.retained_topics(filters, listen=2.5)` returns the topics under the filters (wildcards allowed) that hold a retained value; to clear leftovers publish an empty retained message to each.
