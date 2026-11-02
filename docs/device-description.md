# Device description - the house side

Status: design, no code yet. The device side (the topics, the JSON schema v1, the hash, what is never
inside) is fwgen's `docs/device-description.md`; this page says what the house does with it: where it
keeps things, when it compares, what the device window shows, how a build is registered.

A device built by fwgen publishes, on every MQTT connect and retained (QoS 1), first its description,
then its hash:

```
<node>/fwgen/description    JSON, schema v1 (a {"enc": "gzip+base64", "data": ...} wrapper when over 2 KB, 16 KB at most)
<node>/fwgen/hash           12 hex characters: sha256 of the canonical JSON without build.hash and build.date
```

`<node>` is the node's `topic_prefix` (in the house the node's name). A hash topic means "a Yash build".

## What the house keeps

All under `plugin-data/fwgen/` of the house folder, so a backup has it:

```
builds/<hash>.json     a BUILD RECORD, written when fwgen builds: {hash, date, fwgen: {version, commit}, description, svg, registered}
devices/<node>.json    what the device said last: {node, state, hash, description, received, history: [{hash, received, known}]}
```

- `state` is `yash` (a hash topic was seen) or `foreign` (none was, case 3).
- `description` is the DEVICE'S copy, decoded and checked; the build record never overrides it. The device is
  always right, the house copy is a mirror.
- `history` keeps the last five replaced hashes with the time each came and whether a build record knew it.
- Removing the device in the house clears both retained topics, `<node>/fwgen/description` and `/hash` (an empty retained
  publish), and drops `devices/<node>.json`; otherwise the old description would come back at the next start. Build
  records stay (a build is not the device).
- A cleared retained topic does not erase the stored copy (a broker that lost its data is not a changed device).
- Size: a description is decoded to at most 64 KB; a longer one is refused (a compressed bomb is no description).

## Reading (one extra subscription, `+/fwgen/#`)

House nodes' descriptions carry the fwgen VERSION only (no commit): the hash changes when the node or the description's content
changes, and the builder's version is bumped on purpose when its output changes.

A message counts only when the topic is exactly `<prefix>/fwgen/hash` or `<prefix>/fwgen/description` with a
prefix of `[a-z0-9_-]+`, the payload is within the limits, the description is a JSON object with an integer
`schema` and its `device.topic_prefix` equals that prefix (the device is keyed by `topic_prefix`, never derived from
`device.name` or from the entities' topics; in house builds it equals the name). A description with `device.mac_suffix: true` (one
image for many boards, ESPHome `name_add_mac_suffix`) has no `topic_prefix`: the prefix of the topic is taken as the device's when it
equals `device.name` or matches `<name>-<6 lowercase hex>`. Anything else is dropped
with one warning per node. A message is handled at most once per 10 s per node. Unknown description fields are
skipped; a newer `schema` is stored as it is and shown as "newer format, update the house".

The description arrives before the hash, so the house remembers the last description seen per node and
decides when the HASH arrives:

| case | sign | the house does | the device window says |
|---|---|---|---|
| 1. a Yash build, known hash | hash topic equals the stored hash of the node | nothing | "Yash build <hash>, <date>" (the dashboard shows these lines in its language) |
| 2. a Yash build, new hash | hash topic differs from the stored one (or none stored) | takes the description that came with it, checks that its own sha256 matches the hash (a mismatch is stored and flagged "description does not match its hash"), replaces the copy (the old one into `history`), looks for `builds/<hash>.json` | "description updated from the device, build <hash>, <date>"; with no build record also "build not from this house" |
| 3. foreign or self-made firmware | no hash topic (30 s after the node is first seen online, and every later start) | expects no description; knows the device only by the topics it already knows (ESPHome MQTT discovery and the card), as today | "firmware not from Yash: no description" |

A device that later starts publishing the hash moves from case 3 to case 2. (A retained hash outlives the firmware that
published it, so the house cannot see a flash with a foreign build by itself: the window says so only once the node's
topics stop matching its description, or the person marks it.) One that stops (a node built by fwgen and
flashed with a foreign build afterwards) keeps its last copy and the window says which build it last had: "last Yash build
<hash> of <date>, now foreign firmware" (the hash and time come from `history`); its description is shown as of that build.

## Registering a build (new, proposed)

The builder must tell the house about every build when it makes it, before the device exists:

```
POST /api/fwgen/builds      {hash, date, fwgen: {version, commit}, description, svg}  -> {ok, known}
GET  /api/fwgen/builds/{hash}                                                        -> the record, 404 when none
```

The house recomputes the hash from `description` (canonical JSON: `json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
in UTF-8, with the keys `hash` and `date` removed from `build`, `build` itself kept even when empty; sha256, the first 12 hex characters) and answers
400 when it differs, so a record cannot lie about itself; it runs the same secret guard and size limit as the
topic reader; the same hash twice is a 200 with `known: true`. Who may call: a token of its own role
"builder" (only these two routes). fwgen's server runs on the same computer and holds that token itself,
server-side, and calls the house on its local address, not through nginx; the house token is never given to it.

## What the device window shows (section "Firmware and hardware", under Details on a phone)

- the line of case 1, 2 or 3 above, with the build time, and for case 2 a "what changed" line (new/removed
  entities);
- the wiring picture: the build record carries the SVG that fwgen already drew for this build (`svg`, at most 200 KB,
  checked to hold no script or external reference) and the window shows it as an image; only when there is no build
  record (case 2 with an unknown build) the page draws a plain fallback from `modules[].pins`, `modules[].layout`/`variant`,
  `buses` and `power_from_gpio` (the board's pins, each module with its wires, the pins that power a module marked,
  "GPIO5 = power of the sht4x VIN"). The dashboard does not grow a second drawing of its own;
- a table of modules (title, variant, parameters) and of buses;
- the entities with their roles (temperature, presence ...), the house's channels beside them;
- for the house's own boards (`house_base`) its profile and the snapshot it came from.
No section when there is no description (case 3: only the one line).

## Pairing and changes

- Settings -> Add device -> ESPHome lists a node that has a description by what it is ("3 sensors, 2 relays"),
  proposes the card's kind and channels from the entity `role`s (a presence/temperature/humidity role is a
  reading; `rssi`, `uptime` and the like go under Details as diagnostics) and the room from the description;
  the person confirms the name and the room, nothing is created before that. A node without a description (case
  3) keeps today's flow.
- A new hash whose entities differ from the card does NOT change the card silently (rules and panels point at
  its channels): a banner on the window and in Settings -> Home status says "firmware changed: +2 channels,
  -1 channel - update the card?". Confirming adds the new channels; a removed channel that a rule, a schedule, a
  favourite or a panel still uses is listed first and removed only when the person says so.
- Rebuild and OTA from a description is a later step with its own page.

## MQTT access (the ACL)

No new rule. The device accounts (`MQTT_DEVICE_USERS`) already may publish everything except `home/state/#`,
`device_readonly` topics and `MQTT_BACKEND_ONLY`, so `<node>/fwgen/description` and `/hash` are allowed; the
backend account `home` reads them. Panels' `panel-<name>` accounts do not use them. A plug-in's namespace
(`zigbee2mqtt/`) is not a place for them.

Limit to know: the device accounts are shared, so one node could publish under another's prefix. The checks above (a
description names its own prefix, a hash must match its description, a change never edits the card by itself)
keep a typo out, not a hostile node on the Wi-Fi; per-node accounts are the next step if that ever matters.
The house rejects a description that holds a key named like `password`, `token`, `secret`, `psk` or a value
equal to one in the house's secrets (`.env`, the nodes' `secrets.yaml`); the device side must never send one, this is the guard.

## REST for the window (to add with the code)

`GET /api/devices/{id}/description` - the stored description with `state`, `received`, the history and the
case (1-3); 404 when there is none. The same sign-in as the device's status. Nothing writes it over REST: the
device is the only author of a description, the builder of a build record.
