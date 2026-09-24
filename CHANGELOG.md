# Changelog -- Yashome

## 1.0.0 - 2026-09-24

First public cut: the core. MQTT broker + zigbee2mqtt + backend + dashboard.

- devices from zigbee2mqtt (auto) and `mqtt_devices.json` (generic MQTT)
- universal bus `home/cmd|state/<device>/<code>`
- rules: bindings, schedules, auto-off; rooms, favourites, PWA
- token-gated API, English/Russian UI
- `make check / install / test / update`
