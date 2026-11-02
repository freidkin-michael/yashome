# REST API

Everything under `/api` needs `Authorization: Bearer <DASHBOARD_TOKEN>` (or the header
`X-Token`); `/ws` takes it as `?token=` because a browser cannot set headers on a WebSocket.
The page itself accepts `/?token=<token>&tab=<tab>` once to sign a device in (see security.md).
Any method but GET, HEAD and OPTIONS with an `Origin` other than the dashboard's host gets 403 (the
host is `Host` or `X-Forwarded-Host`; `DASHBOARD_ORIGINS` adds names a proxy uses). The page, its script,
`/i18n.js`, `/display.js`, the PWA files, `/guest`, `POST /api/signin` and the two guest request routes are open. In restricted mode
(`YASHOME_RESTRICTED=1`, security.md) only the routes a plug-in lists answer; every other one is 403.

## Devices and control

```
GET    /api/devices                             devices + controls
GET    /api/devices/{id}/status                 cached state {online, values, raw, last_seen?, waiting?
                                                ("start" | "return"), waiting_since?, offline_since?, cmd_wait?
                                                {code: value sent, in the device's own units: a raw brightness,
                                                true/false}, ...}: online = live (docs/device-state.md)
POST   /api/devices/{id}/control                {code, value} -> {ok, code, value, waiting?, via?} - the only actuation
                                                path; waiting: a confirm channel shows the device's report, not
                                                the value sent; 409 when the device is offline (never for a device
                                                marked wakes, a TV; via: a part of it, an IR remote, sent it instead);
                                                a brightness value is 0-100 % of the full range (home/cmd on the
                                                bus takes percent of the dim window)
POST   /api/devices/{id}/rename                 {name}: the display name only
POST   /api/devices/{id}/channel/{code}/rename  {name}: the channel label
POST   /api/devices/{id}/channel/{code}/dimrange {floor, ceil} -> {ok, code, floor, ceil}: the window of a
                                                brightness channel
GET    /api/devices/{id}/events                 {items}: its last events (at most 20)
GET    /api/devices/{id}/rules                  rules it takes part in
POST   /api/devices/relink                      {stale, into} -> {ok, id, from, migrated}: move every reference of an
                                                id that is gone onto a live one
GET    /api/devices/merge-plan?old=&new=        {old, new, moves: {what: count}, codes_only_old}: the dry run of a
                                                merge (house token; docs/devices.md)
POST   /api/devices/merge                       {old, new} -> {ok, backup, plan, migrated}: a light backup first
                                                (503 and no merge when it fails), then old becomes part of new
                                                (its alias) (house token)
GET    /api/devices/forgotten                   {items: [{id, name, data, kept}]}: gone devices still stored (house token);
                                                data = "kind:where" leftovers, kept = rules and panels naming it
DELETE /api/devices/{id}/leftovers              {ok, removed, kept}: forget a gone device's data in one step (house
                                                token); 400 when it is here, 404 when nothing is stored
POST   /api/resync                              plug-in transports ask their devices to report now,
                                                the home/state mirror is republished: {ok, asked, devices}
GET    /api/wiring                              {entries: [{id, switch: {dev, code}, target: {dev} | {load, seg?},
                                                note}], state: ok | warn | bad} (house token; README "Wiring")
PUT    /api/wiring                              {entries}: the whole list; a new or changed entry must name a
                                                switch channel and an existing dev target (400), 409 while
                                                wiring.json is lost or broken
GET    /api/health                              {ok, mqtt, ts} - liveness
GET    /api/health/report                       {rows: [{id, state, title, detail, fix, args}], worst, ts}:
                                                Settings -> Overview (house token)
WS     /ws                                      full state snapshot: when a status changed, at most one
                                                every 0.25 s (the latest), an unchanged one only every 2 s
```

## Rules

```
GET    /api/rules                               one view over bindings and automations: [{id, kind, enabled,
                                                when, conditions, then, title, ...}]; id b|<src>|<code>|<gesture>
                                                (a binding) or a|<device>/<code>|sched|<time>|<action>, a|...|inch
POST   /api/rules/run                           {id} -> {ok, ...}: run a rule now (its window and conditions
                                                ignored); a binding adds what it did, a schedule {results};
                                                409 when its target is offline or gone (nothing sent), 502 when the
                                                command did not go out (e.g. a zigbee publish that failed)
POST   /api/rules/enable                        {id, enabled?} -> {ok, enabled}: false (or 0) disables, absent or null enables
POST   /api/rules/delete                        {id}
PUT    /api/bindings/{src}/{code}/{gesture}     create or change a binding: {target, code, action,
                                                for_sec?, retrigger?, conditions?, time_window?,
                                                threshold?, hyst?} (docs/rules.md); a plug-in action
                                                marked house_token_only: 403 for any other token, also
                                                on rules/run, enable and delete
GET    /api/automation/{dev}/{code}             auto-off + schedule of a channel
POST   /api/automation/{dev}/{code}             {inching_sec?, schedule?: [{time, days, action}]};
                                                a field left out keeps its current value
```

## Favourites, rooms, layout

```
GET    /api/favorites
PUT    /api/favorites/{device}/{code}           {alias} -> {ok, favorite}: switch channels only (else 400)
DELETE /api/favorites/{device}/{code}           -> {ok, removed}
POST   /api/favorites/reorder                   {order: ["device/code", ...]}
GET    /api/rooms                               -> [{id, name, devices, sensors: {temperature|humidity: {pick, order}}}]: order = the sources the room's reading is tried in
GET    /api/rooms/locked                        -> {devices, rooms, hidden}: what a plug-in owns (the map) and the rooms with no tab; a PUT that would move such a device answers 409
POST   /api/rooms                               {name, devices?} -> {ok, room: {id, name, devices}}: made by the map (a room with no outline yet)
PUT    /api/rooms/{id}                          {name?, devices?} -> {ok, room}: what is left out stays; a new name renames its outline too
GET    /api/rooms/{id}/sensors                  -> {temperature|humidity: {pick, order, options: [{dev, code, name, value, advanced, preferred}], reading, silent}}: what may read the room, what reads it now; silent: a hand choice that gives nothing (its last report)
PUT    /api/rooms/{id}/sensors                  {kind: temperature|humidity, source: auto|"dev/code"} -> {ok, pick, order}: one of the room's options; auto never takes a module's own board
GET    /api/rooms/{id}/refs                     -> {named_by: [{kind: rule|panel|satellite, name}]}: what still names it, asked before a delete; changes nothing
DELETE /api/rooms/{id}                          -> {ok, removed, named_by}: the room and its outline on the map; its tile sizes and tab order go with it
GET    /api/tab_order
POST   /api/tab_order/{tab}                     {order: [device ids]} -> {ok, tab, count}
GET    /api/tile_sizes
PUT    /api/tile_sizes/{scope}                  {device, w?, h?, col?, hn?} -> {ok, scope, device, w, h, col, hn}: w columns
                                                (0-8), h min height px (0-2000), col pinned column (-1-32), hn min height
                                                px on a narrow screen (<= 640 px; absent = kept as stored), clamped;
                                                all 0 and col -1 = auto, the entry is dropped
GET    /api/house                               -> {lang, langs}: the house language (Settings -> House language) and the ones there are
                                                (English + every dictionary of i18n.json)
PUT    /api/house                               {lang} -> {lang, langs}: the house token only; /i18n.js leads with it
                                                (window.HOUSE_LANG), the default of a screen where nobody picked a language
```

`/api/devices/relink` is the repair after an id changed behind the dashboard's back (a device
renamed in its own system, a node re-flashed under another name): bindings (source, target,
condition), favourites, rooms, automations, disabled-rule ids, name and channel overrides, wiring
entries, state, events and timers move onto the live id, and the old retained mirror is cleared. The reference
audit at start-up logs every stored id that resolves to nothing. `/api/devices/{id}/rename`
changes only what you see.

## Generic MQTT / ESPHome discovery

```
GET    /api/esphome/discover                    {nodes: [{node, title, model, availability_topic, alive, controls,
                                                n_controls, skipped, registered, device_id}]}: nodes publishing
                                                homeassistant/*/config
POST   /api/esphome/add                         {node, id?, name?, category?, codes?, force?} -> mqtt_devices.json,
                                                answer {id, name, category, controls, subscribed} (force: add a
                                                node that is not answering now)
POST   /api/esphome/remove                      {id} -> {ok, id}: a generic MQTT device off the dashboard
POST   /api/esphome/purge_discovery             {node, force?}: drop the retained configs of a dead node
GET    /api/esphome/mqtt_block?account=         {accounts, account, yaml, warning, secret}: the mqtt: block for a
                                                node's yaml with a device account (house token: it shows a password);
                                                an unknown account: the first by name; secret may carry ap_password;
                                                no device account (MQTT_DEVICE_USERS, each with its password):
                                                only {accounts: [], yaml: "", secret: ""}
```

discover and add answer 503 "broker not reachable" while the backend has no broker connection.

## Signing in, backups, update

```
POST   /api/signin/start                        -> {code, valid_s}: a 6-digit code for a new screen (house token)
POST   /api/signin                              {code} -> {token}: no token needed; once, within 5 min, 5 tries
POST   /api/guest/request                       -> {id, secret, code}: a browser asks to come in (/guest); no token, 5 per IP in 10 min
GET    /api/guest/request/{id}?secret=          {state: pending|allowed|denied|expired}; allowed: {token, hours} once
GET    /api/guest                               {pending: [{id, code, ip, ua, age_s}], guests: [{gid, label, scope, expires, ip, ua}]} (an administrator)
POST   /api/guest/{id}/allow                    {hours: 1|2|8|24, scope: no_locks|view} -> a yg_ key for that browser (an administrator)
POST   /api/guest/{id}/deny                     the request is refused (an administrator)
DELETE /api/guest/keys/{gid}                    the guest key stops working (an administrator)
GET    /api/backups                             {items, age_s, error, warnings, at, nightly, keep} (house token)
POST   /api/backups                             a backup now: {ok, name, warnings} (house token)
GET    /api/backups/{name}                      the archive (house token)
POST   /api/backups/upload                      the raw body of a backup file, kept as a new archive: {ok, name} (house token)
GET    /api/helper/state                        the update helper's view: plug-ins, keys, versions (house token)
POST   /api/helper/plugins                      {on: [names]}: the plug-ins to run (house token)
POST   /api/helper/env                          {key, value}: a key of .env (house token)
POST   /api/helper/jobs                         {kind: apply | update | rollback | restore | restart, file: a backups/
                                                archive for restore} (house token); 404 no such backup
GET    /api/helper/jobs/current                 the running or last job (house token)
```

"House token" here: the dashboard's own token; a phone's token or a panel's key get 403. The
helper routes answer 503 when the host has no helper (run `make update` once there) or it does not answer.

A merge makes a light backup (`yashome-backup-<date>-<time>-light.tar.gz`): the house's settings and
data without the ESPHome build cache and the voice recordings. It is listed and restorable like the
others; the newest 5 stay, and it never counts as the nightly backup. Settings -> Sign in from
another screen asks for the code; the new screen types it on its sign-in card.

## Plug-ins

```
GET    /api/plugins                             {loaded: [{id, name, version, ui, ui_v, i18n, group, requires, icon, menu_group, menu, required_by}], failed: [{id, error}], off: [id], dirs}
GET    /plugins/{name}/ui.js                    the public files of a loaded plug-in, no token (GET and HEAD)
GET    /plugins/{name}/i18n.json
GET    /plugins/{name}/static/{file}            .js .mjs .css .json .png .svg
```

Plug-ins add their own routes. They need the token, except the few a plug-in opens itself: pairing
(`POST /api/family/claim` -> {tok, uid, name}, `POST /api/panels/register`, `GET /api/panels/register/{id}`), the files a
computer of the house installs (`/plugins/build/server.tar.gz`, `/plugins/player/host.tar.gz`,
`/plugins/touchmouse/host.tar.gz`, `/plugins/voice/tts.tar.gz`), the satellites' spoken files
(`/plugins/voice/tts/{kind}/{name}.wav`), the apps (`/plugins/family/yash.apk`, `/plugins/panels/panel.apk`,
`/plugins/panels/app`: the launcher's version), `/nfc` (GET and POST), the panel pages (`/panel-view`, `/panel-view.html`,
`/panel-builder`) and zigbee2mqtt's page (`/plugins/zigbee/ui`, with the pass below). A plug-in's README describes its
routes; the Zigbee plug-in:

```
POST   /api/zigbee/pair/start                   permit_join on
POST   /api/zigbee/pair/stop                    permit_join off
GET    /api/zigbee/pair/status                  {active, remaining, found, orphans}
POST   /api/zigbee/pair/add                     {ieee, friendly_name?, name?} - name a newly joined device;
                                                409 a name taken, 504 (renamed) / 500 the device did not register
POST   /api/zigbee/device/rename                {id, to} - renames in zigbee2mqtt AND moves every reference
POST   /api/zigbee/device/remove                {id, force?}
GET    /api/zigbee/gateway                      {port, adapter, coordinator, firmware, ieee, channel, pan_id, ext_pan_id,
                                                key_set, version, online, since, info_at} (house token)
GET    /api/zigbee/gateway/key                  {key}: the network key (house token)
POST   /api/zigbee/gateway/check                {port?} -> {ok, how, args}: a TCP connect to a network coordinator (house token)
POST   /api/zigbee/ui                           {url}: a pass (a cookie for /plugins/zigbee/ui, 12 h) to zigbee2mqtt's page (house token)
```
