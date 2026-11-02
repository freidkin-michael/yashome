# fwdesc - what a firmware built by fwgen says of itself

The house side of docs/device-description.md. A device built by fwgen publishes, retained, `<node>/fwgen/description` (its
hardware description, JSON schema v1) and `<node>/fwgen/hash` on every connect. This plug-in:

- reads both (`core.NODE_SUBTOPICS["fwgen"]`): checks size, the `schema`, that `device.topic_prefix` is the topic's prefix and that
  no secret is inside (keys like password/token/key/ssid, values equal to the house's secrets), then keeps the device's copy in
  `plugin-data/fwgen/devices/<node>.json` with the last five replaced hashes;
- tells the device window which of three cases it is (`GET /api/devices/{id}/description`): a known hash, a new hash read from the
  device, or no hash topic at all (firmware not from Yash: no description);
- clears both retained topics and drops the copy when the device is removed (`core.DEVICE_REMOVED`);
- keeps BUILD RECORDS that the builder registers when it builds (`POST /api/fwgen/builds`, `GET /api/fwgen/builds/{hash}`): the house
  recomputes the hash (canonical JSON without `build.hash`/`build.date`, sha256, 12 hex) and refuses a record that does not match,
  holds secrets, or carries an SVG with a script or a reference. Who may call: the dashboard, or the builder with
  `FWGEN_BUILDER_TOKEN` (these two routes only).

A new hash never changes the device's card by itself: `changes` lists the channels the card lacks and the ones the description lost.

- a node with a description is skipped by the Add ESPHome discovery (`core.DISCOVERY_IGNORE`): its Home Assistant configs make no second card.
- removing the device also empties its `homeassistant/+/<node>/+/config`.
