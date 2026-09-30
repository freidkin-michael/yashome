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
- a bearer token on `/api` and `/ws` (fail closed), same-origin checks, a strict CSP; broker
  accounts through mosquitto's dynamic-security plug-in: the backend, one account per plug-in
  service, optional device accounts that cannot write the mirror, a plug-in's read-only bus or
  `MQTT_BACKEND_ONLY`
- English UI with a Russian dictionary, the browser's language by default
- `make check`, `make install`, `make test`, `make update`; `make lint` and `make dev-test` in CI

**Plug-ins** (docs/plugins.md; which ones load: `PLUGINS` in `.env`)
- the contract: devices of their own, transports and their rule shortcuts, MQTT topics and a
  namespace of their own, rule actions, settings sections, routes, token checks, start tasks, a
  device registry the core waits for, key migration; on the dashboard tabs, the "Add" menu, card
  decorations and faces, header boxes, sections of the device modal
- `setup/` of a plug-in: `detect.sh`, `env.example`, `compose.yml` (in `COMPOSE_FILE`),
  `broker.json`, `secrets.py` and the install / secrets / update / preflight / smoke / summary steps
- shipped: zigbee (zigbee2mqtt with its container, pairing, key rename, remove), weather, climate,
  tv, webos, ir, cameras, nfc, stats, and
  `_example`; their tests in `tests/plugins/`
- `tools/check_plugins.py` keeps any one house's data (LAN addresses, MACs, Zigbee addresses,
  home directories) out of the repository
