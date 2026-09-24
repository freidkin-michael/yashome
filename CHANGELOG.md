# Changelog -- Yashome

## 1.1.0 - unreleased

Plug-ins: everything beyond the core arrives as a package in PLUGINS_DIR (docs/plugins.md).

- backend hooks: transports, stateless control kinds (`press`), MQTT subscriptions and handlers,
  startup tasks, settings sections (kept when the plug-in is absent), rule actions, extra device
  fields, open paths, `register_device`; a failing plug-in is logged, the core keeps running
- dashboard hooks: tabs, "Add" rows, card decorators, rule targets, toolbar buttons, reload
  callbacks, i18n tables; every call into a plug-in is guarded
- `GET /api/plugins`, `/plugins/<name>/<asset>`, Settings -> Plug-ins, compose mounts PLUGINS_DIR
- `examples/plugins/hello`: a complete plug-in; contract tests in tests/test_plugins.py

## 1.0.0 - 2026-09-24

First public cut: the core. MQTT broker + zigbee2mqtt + backend + dashboard.

- devices from zigbee2mqtt (auto) and `mqtt_devices.json` (generic MQTT)
- universal bus `home/cmd|state/<device>/<code>`
- rules: bindings, schedules, auto-off; rooms, favourites, PWA
- token-gated API, English/Russian UI
- `make check / install / test / update`
