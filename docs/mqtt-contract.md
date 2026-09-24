# MQTT contract

Everything the backend publishes or listens to. `<device>` is the device id (`id` in
`mqtt_devices.json`, or the zigbee2mqtt friendly name -- which may contain spaces, so scripts
should read topics with `mosquitto_sub -F '%t'`). `<code>` is the channel code.

## Universal bus

```
home/state/<device>/<code>        retained   channel state (true/false, number, text)
home/state/<device>/<code>_pct    retained   brightness in percent; inside the channel's
                                             [floor, ceil] window when one is set in the dashboard
home/state/<device>/online        retained   true/false
home/cmd/<device>/<code>                     true | false | on | off | toggle | <number> | up | down
                                             up/down on a brightness = one tenth of the window;
                                             down at the floor switches the channel off
```

Commands arriving on `home/cmd/...` are re-posted to the backend's own `/api/devices/{id}/control`,
so validation, the optimistic cache and the logs apply to MQTT and REST alike.

The `home/state` mirror is pruned at start-up: retained branches of devices that no longer exist
are removed (skipped if the zigbee2mqtt device list could not be loaded).

## zigbee2mqtt

```
zigbee2mqtt/<name>                           state frames (ingested, retained by z2m)
zigbee2mqtt/<name>/set | /get                commands / refresh requests
zigbee2mqtt/bridge/devices                   live device list -> (re)registration
zigbee2mqtt/bridge/event                     device_joined / interview -> pairing discovery
zigbee2mqtt/bridge/request/*                 rename, remove, permit_join
```

## ESPHome discovery

`GET /api/esphome/discover` reads the retained `homeassistant/+/+/+/config` messages ESPHome nodes
publish (MQTT discovery) and proposes them as devices; nothing is subscribed permanently.

## Generic MQTT devices (`mqtt_devices.json`)

Topics under `home/state/` and `home/cmd/` belong to the backend's own bus: a device or channel
that declares one there is skipped with a warning (it would read its own mirror).

A device declares its topics; two payload shapes may be mixed on one device:

- device-level `state_topic` carrying a JSON object `{code: value}` (z2m-like);
- per-control `state_topic` / `command_topic` carrying a bare scalar (ESPHome/Tasmota style).

Optional `availability_topic` with `payload_online` / `payload_offline` (an LWT) drives the
online flag; without it a device counts as online while its state keeps arriving.

Device fields: `id`, `name`, `category` (dashboard tab: `switch`, `sensor`, `button`,
`indicator`; anything else lands under "Other"),
`state_topic`, `command_topic`, `availability_topic`, `payload_online`, `payload_offline`,
`controls`.

Control fields: `kind` (`switch`, `bright`, `sensor`, `sensor_text`, `setting_enum`), `code`,
`label`, `state_topic`, `command_topic`, `payload_on`, `payload_off`, `unit`, `ro`,
`vtype` (`bool` | `num` | `text`), `min` / `max` for `bright`, `text_on` / `text_off` for a
switch whose state is shown as words, `labels` for `setting_enum`; `on_card: true` puts a
`setting_enum` on the card as a toggle + picker, where "off" sends `off_option` (default `Static`).

Rooms are not a device field: assign devices to rooms in the dashboard (stored in `settings.json`).
