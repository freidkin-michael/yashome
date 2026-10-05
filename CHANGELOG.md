# Changelog -- Yashome

## 2.0.0 - first public release

The core and the plug-ins in one repository.

**Core**
- one FastAPI backend (`app.py`) and one dashboard page (`index.html`, a PWA), mosquitto in the middle
- generic MQTT devices from `mqtt_devices.json` or ESPHome's MQTT discovery ("Add" -> ESPHome)
- the universal bus: `home/state/<device>/<code>` (retained mirror of every device, `_pct` for
  brightness in the channel's window) and `home/cmd/<device>/<code>`
- rules: triggers (buttons, gestures, sensor thresholds with hysteresis, times), conditions,
  actions, `for` windows; per channel a schedule and an auto-off after any turn-on; timers survive
  a restart with the right remaining time
- rooms, favourites, tab order, tile sizes, channel renames and brightness windows, all saved in
  `settings.json`; an id that changed in its own system moves every reference
  (`POST /api/devices/relink`, audited at start)
- `HOUSE_DIR` in `.env`: the house's files (devices, settings, `plugin-data/`, `plugin-state/`,
  runtime state) in a directory of their own, e.g. a private repository (`examples/house.gitignore`
  keeps state out of git); empty = beside the code. `make move-house` moves an existing house there;
  `make install` / `make update` refuse an empty `HOUSE_DIR` while the checkout still holds a house.
  zigbee2mqtt's network stays under `Z2M_DATA_DIR` in the checkout
- a bearer token on `/api` and `/ws` (fail closed), same-origin checks, a strict CSP; broker
  accounts through mosquitto's dynamic-security plug-in: the backend, one account per plug-in
  service, optional device accounts that cannot write the mirror, a plug-in's read-only bus or
  `MQTT_BACKEND_ONLY`
- English UI with a Russian dictionary, the browser's language by default
- `make check`, `make install`, `make test`, `make update`; `make lint` and `make dev-test` in CI
- `tools/yashctl`: the house from a shell (devices, channels, watch, rules, rooms, plug-ins), `--json` for scripts

**Plug-ins** (docs/plugins.md; which ones load: `PLUGINS` in `.env`)
- the contract: devices of their own, transports and their rule shortcuts, MQTT topics and a
  namespace of their own, rule actions, settings sections, routes, token checks, start tasks, a
  device registry the core waits for, key migration; on the dashboard tabs, the "Add" menu, card
  decorations and faces, header boxes, sections of the device modal
- grouped by role: `plugins/transports/` (how devices are reached), `devices/` (a kind of device
  on the dashboard, over any transport), `sources/` (data and events), `tools/`; the group is only
  a folder, the plug-in's name is its id everywhere
- `requires` in a plug-in (one name per line): what it cannot work without is switched on, set up
  and loaded with it, the same way in the backend, `make install` / `make update` and the broker
  accounts; a missing, failing or circular requirement, or a malformed file, keeps it off with the
  reason in Settings; tabs with the same id from several plug-ins are one tab (the Appliances tab of
  tv, ir and climate)
- a plug-in's settings sections in its own `plugin-data/<name>/settings.json` (one of a loaded
  plug-in left in `settings.json` moves there at the start; if both copies differ, the plug-in's
  file wins and both are kept as `*.pre-split-<time>`; a section in two plug-ins' files stops saves;
  the core reserves that file name in every `plugin-data/<name>/`);
  what a plug-in writes itself (paired keys, counters) in `plugin-state/<name>/` (`core.plugin_state`),
  moved there from `plugin-data/` once
- `setup/` of a plug-in: `detect.sh`, `env.example`, `compose.yml` (in `COMPOSE_FILE`),
  `broker.json`, `secrets.py` and the install / secrets / update / preflight / smoke / summary steps;
  `settings.example.json` and `<file>.example.json` show its configuration, and a test loads them
- shipped: transports zigbee (zigbee2mqtt with its container, pairing, key rename, remove), webos,
  ir; devices climate, tv, cameras; sources weather, nfc; tools stats; and `_example`; their tests
  in `tests/plugins/`. Connectors only: no devices, names or secrets of any house
- `tools/check_plugins.py` keeps any one house's data (LAN addresses, MACs, Zigbee addresses,
  home directories, your house's words) out of the repository, and Russian out of everything but
  the translations
