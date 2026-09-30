# REST API

Everything under `/api` needs `Authorization: Bearer <DASHBOARD_TOKEN>` (or the header
`X-Token`); `/ws` takes it as `?token=` because a browser cannot set headers on a WebSocket.
The page itself accepts `/?token=<token>&tab=<tab>` once to sign a device in (see security.md).
POST/PUT/DELETE with an `Origin` other than the dashboard's host get 403. The page, its script
and the PWA files are open.

## Devices and control

```
GET    /api/devices                             devices + controls
GET    /api/devices/{id}/status                 cached state {online, values}
POST   /api/devices/{id}/control                {code, value} - the only actuation path
POST   /api/devices/{id}/rename                 display name only
POST   /api/devices/{id}/channel/{code}/rename  channel label
POST   /api/devices/{id}/channel/{code}/dimrange {floor, ceil} window of a brightness channel
GET    /api/devices/{id}/events                 recent events of the device
GET    /api/devices/{id}/rules                  rules it takes part in
POST   /api/devices/relink                      {stale, into} - move every reference of an id that is gone onto a live one
POST   /api/resync                              plug-in transports ask their devices to report now,
                                                the home/state mirror is republished: {ok, asked, devices}
GET    /api/health                              {ok, mqtt, ts} - liveness
WS     /ws                                      full state snapshot every 2 s
```

## Rules

```
GET    /api/rules                               one view over bindings and automations
POST   /api/rules/run | enable | delete         {id, ...}
PUT    /api/bindings/{src}/{code}/{gesture}     create or change a binding: {target, code, action,
                                                for_sec?, retrigger?, conditions?, time_window?,
                                                threshold?, hyst?} (docs/rules.md)
GET    /api/automation/{dev}/{code}             auto-off + schedule of a channel
POST   /api/automation/{dev}/{code}             {inching_sec?, schedule?: [{time, days, action}]};
                                                a field left out keeps its current value
```

## Favourites, rooms, layout

```
GET    /api/favorites ; PUT|DELETE /api/favorites/{device}/{code} ; POST /api/favorites/reorder
GET    /api/rooms ; POST /api/rooms ; PUT|DELETE /api/rooms/{id} ; POST /api/rooms/reorder
GET    /api/tab_order ; POST /api/tab_order/{tab}
GET    /api/tile_sizes ; PUT /api/tile_sizes/{scope}
```

`/api/devices/relink` is the repair after an id changed behind the dashboard's back (a device
renamed in its own system, a node re-flashed under another name): bindings (source, target,
condition), favourites, rooms, automations, disabled-rule ids, name and channel overrides, state,
events and timers move onto the live id, and the old retained mirror is cleared. The reference
audit at start-up logs every stored id that resolves to nothing. `/api/devices/{id}/rename`
changes only what you see.

## Generic MQTT / ESPHome discovery

```
GET    /api/esphome/discover                    nodes publishing homeassistant/*/config
POST   /api/esphome/add                         {node, id?, name?, category?, codes?} -> mqtt_devices.json
POST   /api/esphome/remove
POST   /api/esphome/purge_discovery             drop the retained configs of a dead node
```

## Plug-ins

```
GET    /api/plugins                             {loaded: [{id, name, version, ui, i18n}], failed: [{id, error}], off: [id], dirs}
GET    /plugins/{name}/ui.js                    the public files of a loaded plug-in, no token (GET and HEAD)
GET    /plugins/{name}/i18n.json
GET    /plugins/{name}/static/{file}            .js .css .json .png .svg
```

Plug-ins add their own routes (they need the token); see docs/plugins.md. The Zigbee plug-in:

```
POST   /api/zigbee/pair/start | stop            permit_join
GET    /api/zigbee/pair/status                  {active, remaining, found, orphans}
POST   /api/zigbee/pair/add                     {ieee, friendly_name, name?} - name a newly joined device
POST   /api/zigbee/device/rename                {id, to} - renames in zigbee2mqtt AND moves every reference
POST   /api/zigbee/device/remove                {id, force?}
```
