# Device description - the house side

Status: implemented in the plug-in `fwdesc` (`plugins/sources/fwdesc/`), with a few parts still planned (end of this page).
The device side (the topics, the JSON schema v1, the hash, what is never inside) is fwgen's `docs/device-description.md`.
This page says what the house does with it: where it keeps things, when it compares, what the device window shows, how a
build is registered.

A device built by fwgen publishes, on every MQTT connect and retained (QoS 1), its description and its hash:

```
<node>/fwgen/description    JSON, schema v1 (a {"enc": "gzip+base64", "data": ...} wrapper when it is large)
<node>/fwgen/hash           12 hex characters: sha256 of the canonical JSON without build.hash and build.date
```

`<node>` is the node's `topic_prefix` (in the house the node's name). A hash topic means "a Yash build".

## What the house keeps

All under `plugin-data/fwgen/` of the house folder, so a backup has it:

```
builds/<hash>.json     a BUILD RECORD, written when fwgen registers a build: {hash, date, fwgen, description, svg, registered}
devices/<node>.json    what the device said last: {node, state, hash, description, received, seen, mismatch, build_known,
                       history: [{hash, received, known}], changes, card_gap}
```

- `description` is the DEVICE'S copy, decoded and checked. The build record never overrides it. The device is always
  right; the house copy is a mirror.
- `history` keeps the last five replaced hashes, with the time each came and whether a build record knew it.
- `changes` lists the channels (state topics) the new description has and the previous one did not, and the reverse.
- `card_gap` lists the channels of the description that the device's card does not show.
- Removing the device in the house (`core.DEVICE_REMOVED`) clears both retained topics, `<node>/fwgen/description` and
  `/hash` (an empty retained publish), clears its Home Assistant discovery configs (`homeassistant/+/<node>/+/config`)
  and drops `devices/<node>.json`. Otherwise the old description would come back at the next start. Build records stay
  (a build is not the device).
- A cleared retained topic does not erase the stored copy (a broker that lost its data is not a changed device).
- Size: a message is at most 24 KB on the wire; a description is decoded to at most 64 KB. A longer one is refused (a
  compressed bomb is no description).

A device this house paired itself (the plug-in pair has its record) needs no registration by the builder: when its
description comes with an unknown hash that matches it, that description becomes the build record (`from: paired device`,
no wiring SVG). So a builder never needs the house's address or a token of it.

## Reading (`core.NODE_SUBTOPICS["fwgen"]`)

The core hands every `<node>/fwgen/...` message to the plug-in. A message counts only when:

- the topic is exactly `<prefix>/fwgen/hash` or `<prefix>/fwgen/description`, with a prefix of `[a-z0-9_-]` (1-64
  characters);
- the hash is 12 lowercase hex characters;
- the description is a JSON object (or the gzip+base64 wrapper) with an integer `schema`;
- its `device.topic_prefix` equals the topic's prefix. The device is keyed by `topic_prefix`, never derived from
  `device.name` or from the entities' topics. A description with `device.mac_suffix: true` (one image for many boards,
  ESPHome `name_add_mac_suffix`) has no fixed prefix: the topic's prefix counts when it equals `device.name` or matches
  `<name>-<6 lowercase hex>`, and every entity `state_topic` must start with `~/`;
- it holds nothing like a secret (see "MQTT access" below).

Anything else is dropped with one warning per node and reason. An unchanged hash is handled at most once per 10 s per
node.

The description and the hash arrive close together, in either order. The house keeps the last valid description per
node and decides when the HASH arrives. A description from the same burst (5 s), or one whose own hash matches, goes
with that hash; else the house waits for the next description.

| case | sign | the house does | the device window says |
|---|---|---|---|
| 1. a Yash build, known hash | the stored copy has this hash, received more than 24 h ago | notes the time it was seen | "Yash build <hash>, <date>" |
| 2. a Yash build, new hash | the hash differs from the stored one (or none is stored); the window shows case 2 for 24 h | checks that the description's own sha256 matches the hash (a mismatch is stored and flagged), replaces the copy (the old hash goes into `history`), looks for `builds/<hash>.json` | "Description updated from the device, build <hash>, <date>"; with no build record also "Build not from this house" |
| 3. foreign or self-made firmware | no stored copy for the node | nothing | "The firmware was not built in the Yash builder, so its wiring is unknown." |

A device that starts publishing the hash moves from case 3 to case 2. The dashboard shows these lines in its language.

A node that has a stored description is skipped by the Add ESPHome discovery (`core.DISCOVERY_IGNORE`). So a build that
also publishes Home Assistant discovery configs never gets a second card.

## Registering a build

The builder tells the house about every build when it makes it, before the device exists:

```
POST /api/fwgen/builds      {hash, date, fwgen: {version, commit}, description, svg}  -> {ok, known}
GET  /api/fwgen/builds/{hash}                                                        -> the record, 404 when none
```

The house refuses (400):

- a hash that is not 12 hex characters;
- a description that fails the checks above (no integer schema, a secret inside) or is over 64 KB;
- a hash that is not the description's own. The house recomputes it: canonical JSON
  (`json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` in UTF-8, with the keys `hash` and `date`
  removed from `build`, `build` itself kept even when empty), sha256, the first 12 hex characters. So a record cannot lie
  about itself;
- an `svg` over 200 KB, one that does not start with `<svg` or `<?xml`, or one with a script, an event handler, an
  `<image>`, an `<iframe>` or an external reference.

The same hash twice is a 200 with `known: true`; the first record stays.

Who may call: the house token (the dashboard), or the builder with its own token `FWGEN_BUILDER_TOKEN` (`.env`; it
opens only these two routes). Empty: only the dashboard may register. The builder's host piece gets this token from the
plug-in `computers` (see its README).

## What the device window shows

A device of the generic MQTT transport has a section "Firmware and hardware" (folded; it loads when opened) with
`GET /api/devices/{id}/description`:

- the line of case 1, 2 or 3 above, with the build time;
- "Build not from this house" when there is no build record, and "The description does not match its hash" on a
  mismatch;
- "Firmware changed: +N channels, -M channels compared with the previous build" when `changes` is not empty;
- "The device has N more channels than its card shows", with the list folded, when `card_gap` is not empty. This is an
  offer, not an alarm: the card may be hand-written;
- the board (`device.board.id`, else `device.chip`);
- the wiring: the SVG of the build record as an image; without a build record a plain list from `modules[].pins`,
  `buses` and `power_from_gpio` ("Powered from pins");
- the entities with their roles (or domain) and units;
- the earlier builds from `history`.

Case 3 shows only its one line.

## MQTT access (the ACL)

No new rule. The device accounts (`MQTT_DEVICE_USERS`) may publish everything except `home/state/#`, `home/clock/#`, a
plug-in's `device_readonly` topics and `MQTT_BACKEND_ONLY` (`tools/mkpasswd.py`). So `<node>/fwgen/description` and
`/hash` are allowed; the backend account `home` reads them.

Limit to know: the device accounts are shared, so one node could publish under another's prefix. The checks above (a
description names its own prefix, a hash must match its description, a change never edits the card by itself) keep a
typo out, not a hostile node on the Wi-Fi.

The secret guard: the house refuses a description with a key named like `password`, `passwd`, `token`, `secret`, `psk`,
`key` or `ssid`, or with a value equal to a secret of the house. The secrets are the values of the house environment
whose names look like a secret, and the secret-named values of the nodes' `secrets.yaml`. That file is mounted
read-only from the host path in `ESPHOME_SECRETS` (`.env`). When it cannot be read, Settings -> Overview shows a warning
and the check runs on the house environment only. The device side must never send a secret; this is the guard.

## REST

- `GET /api/devices/{id}/description` - for a generic MQTT device: `{case, state, node, hash, description, received,
  history, mismatch, changes, card_gap, build: {known, date, fwgen, svg}}`; case 3 gives `{case: 3, state: "foreign",
  node}`. 404 for another transport or an unknown id. The same sign-in as the device's status.
- `POST /api/fwgen/builds`, `GET /api/fwgen/builds/{hash}` - above.

Nothing writes a description over REST: the device is the only author of a description, the builder of a build record.

## Not implemented yet

- Add device -> ESPHome does not use a description: it does not list a node by what it is, nor propose the card's
  kind, channels and room from the entity roles.
- A firmware change does not offer "update the card?": the window shows the difference only. Nothing adds or removes
  channels from it.
- No "newer format" message for a `schema` above 1: such a description is stored as it is.
- No "last Yash build, now foreign firmware" state for a node that stops publishing its hash: it keeps its last copy
  and shows it.
- The window does not show the house base profile, a module table with parameters, or the house's channels beside the
  entities.
- Per-node broker accounts (see docs/pairing.md for the pairing accounts).
- Rebuild and OTA from a description.
