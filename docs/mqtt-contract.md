# MQTT contract

Everything the backend publishes or listens to. `<device>` is the device id (`id` in
`mqtt_devices.json`, or the id a plug-in gives it - the Zigbee plug-in uses the zigbee2mqtt
friendly name, which may contain spaces, so scripts should read topics with
`mosquitto_sub -F '%t'`). `<code>` is the channel code.

## Universal bus

```
home/state/<device>/<code>        retained   channel state (true/false, number, text)
home/state/<device>/<code>_pct    retained   brightness in percent; inside the channel's
                                             [floor, ceil] window when one is set in the dashboard
home/state/<device>/online        retained   true while the device is live (docs/device-state.md)
home/cmd/<device>/<code>                     true | false | on | off | toggle | <number> | up | down
                                             a <number> on a brightness is percent of its dim window
                                             (as the _pct mirror; REST /control takes 0-100 % of the range)
                                             up/down on a brightness = one tenth of the window;
                                             down at the floor switches the channel off
home/clock/time                   retained   "HH:MM", each minute, in TZ (devices may not write it)
home/clock/date                   retained   "Mon 27 July" in the house language (en, ru) - a clock prints it as is
home/clock/words                  retained   {key: text, "wx": {code: text}}: the words of the ESP screens and knobs
                                             in the house language (plug-in panels); their firmware has none of its own
```

Commands arriving on `home/cmd/...` are re-posted to the backend's own `/api/devices/{id}/control`,
so validation, the confirm rule and the logs apply to MQTT and REST alike. Who may publish them: by
default the backend account `home` only (`MQTT_BACKEND_ONLY=home/cmd/#`); a device account
(`MQTT_DEVICE_USERS`) is refused. An empty `MQTT_BACKEND_ONLY=` lets devices publish there too.

The `home/state` mirror is pruned at start-up: retained branches of devices that no longer exist
are removed - once every plug-in that loads its devices from outside said its list is in
(`registry_ready`); after 60 s without it the prune is skipped.

## Node self-description (the fwdesc plug-in)

```
<node>/fwgen/description   retained   what a firmware built by fwgen says of itself (JSON, schema v1; gzip+base64 wrapper over 2 KB)
<node>/fwgen/hash          retained   12 hex characters, the hash of that description
```

A node publishes both on every connect; the house reads them (`+/fwgen/#`) and, when a device is removed, clears both. See
docs/device-description.md.

## Plug-in namespaces

A plug-in may claim a whole top-level topic (`MQTT_NAMESPACES`, docs/plugins.md). Nobody else
subscribes or handles there, and a discovered ESPHome node may not point a topic into it. The
Zigbee plug-in owns `zigbee2mqtt/`:

```
zigbee2mqtt/<name>                           state frames (ingested, retained by z2m)
zigbee2mqtt/<name>/availability              online / offline of the device
zigbee2mqtt/<name>/set | /get                commands / refresh requests (the backend only)
zigbee2mqtt/bridge/state                     zigbee2mqtt itself online / offline
zigbee2mqtt/bridge/devices                   live device list -> (re)registration
zigbee2mqtt/bridge/event                     device_joined / interview -> pairing discovery
zigbee2mqtt/bridge/request/*                 rename, remove, permit_join (the backend only)
```

Device accounts (`MQTT_DEVICE_USERS`) may read it but not publish into it (`device_readonly` in the
plug-in's `setup/broker.json`); zigbee2mqtt has an account of its own with rights on it alone.
Each ESP wall panel has an account of its own on `panel/<name>/#` (made by the panels plug-in when
the panel is built); a screen with no account of its own uses the shared `panels` account on `panel/#`.
The panels plug-in keeps two retained topics there for each ESP panel: `panel/<name>/layout` and
`panel/<name>/away` (the panel's devices that are not live: `/<device>#o/<device>#w/`, offline and
waiting for their report; `/` when all are live; docs/device-state.md).

## ESPHome discovery

`GET /api/esphome/discover` reads the retained `homeassistant/+/+/+/config` messages ESPHome nodes
publish (MQTT discovery) and proposes them as devices; nothing is subscribed permanently.

## Generic MQTT devices (`mqtt_devices.json`)

Topics under `home/state/` and `home/cmd/` belong to the backend's own bus: a device or channel
that declares one there is skipped with a warning (it would read its own mirror). Ids are Latin
letters, digits and `_ . -`.

A device declares its topics; two payload shapes may be mixed on one device:

- device-level `state_topic` carrying a JSON object `{code: value}` (the zigbee2mqtt shape);
- per-control `state_topic` / `command_topic` carrying a bare scalar (ESPHome/Tasmota style).

Optional `availability_topic` with `payload_online` / `payload_offline` (an LWT) says when the device
is gone and back; how it waits after an outage or a start (a report, a 35 s grace, a lock or dimmer
that keeps waiting) is in docs/device-state.md. A live report brings it back by itself.

Device fields: `id`, `name`, `category` (dashboard tab: `switch`, `sensor`, `button`,
`indicator`; anything else lands under "Other"),
`state_topic`, `command_topic`, `availability_topic`, `payload_online`, `payload_offline`,
`offline_after_s` (seconds of silence after which the device counts as offline, whether its
heartbeat was learned or not; docs/device-state.md), `state_request` (`{"topic": "...", "payload":
"..."}`: published once when the device is back, or waiting since the start, and has not reported in
5 s; the topic is held to the rules of a discovered one - plain, at most 256 printable characters, no
wildcard, not `$...`, not a plug-in's namespace, not a backend-only filter - and is never under
`home/`; the payload is text, an object or a list goes as JSON; one that breaks a rule is refused with a
warning in the log), `controls`.

Control fields: `kind` (`switch`; `bright`; `color`: `#rrggbb`, sent as 0-255 to its `channels`
{r, g, b} topics, never reported back (generic MQTT devices; a plug-in's colour channel is its own); `sensor`, `sensor_text`: read only; `trigger`, `trigger_text`:
an event (a press, a text), read only; `setting_bool`, `setting_int`, `setting_enum`: a device
setting shown in its window), `code`,
`label`, `state_topic`, `command_topic`, `payload_on`, `payload_off`, `unit`, `ro`,
`vtype` (`bool` | `num` | `text`), `min` / `max` for `bright`, `text_on` / `text_off` for a
switch whose state is shown as words, `labels` for `setting_enum`; `on_card: true` puts a
`setting_enum` on the card as a toggle + picker, where "off" sends `off_option` (default `Static`).
`confirm: true` (a lock): after a command the channel keeps its reported value until the device
reports the new one; like any device it is refused (409) while offline, never while it only waits.
A brightness channel and the switch it dims are confirm channels by themselves (`<x>_bright` dims
`<x>`, `brightness[_g]` dims `state[_g]`, and a device with one switch and one brightness pairs
them) when the device reports them: a state topic on the channel or on the device, or a plug-in's
transport; `confirm: false` in the entry keeps one showing the value sent (a channel never
reported). A toggle or a step (`up` / `down`, a rule's brightness step) right after a command builds
on the value sent while it is unanswered (docs/device-state.md: an echo answers it, a value no command
asked for settles after 1 s, 5 s at most).
A command never changes whether a device counts as live: only the device does.

Rooms are not a device field: assign devices to rooms in the dashboard (stored in `settings.json`).
