# Pairing a fwgen device - the house side

Status: design, no code yet. The device side (the setup AP `<node>-setup`, `POST /yash/pair`, the 8-character code, `<prefix>/yash/unpair`) is
fwgen's `docs/pairing.md`; this page says what the house does. It is for NEW nodes only: the shared MQTT user `esp` of the nodes that run
today is not touched.

## What a firmware carries and what the house gives

The firmware carries no house secret. At pairing the house hands over, in one request: its id (`house_id`), the Wi-Fi (ssid, password), the
broker (host, port), the device's OWN MQTT user and password, and the device's OTA password. A device can be paired again into any house.

## Where things live

- `plugin-state/fwgen/pairing/<node>.json` (0600, outside git and backups of the house files): `{node, user, ota_password, house_id, paired}`.
  `plugin-state/pair/mqtt_accounts.json` (0600) keeps the account's password too (the broker's file for its next start is written from it,
  the way the panels' accounts are); a lost device is re-paired with a new account.
- The broker account: `dev-<node>`, made at runtime through mosquitto's dynamic-security (the way the panels plug-in makes `panel-<name>`;
  `$CONTROL/dynamic-security/v1`, no broker restart) and written for the next start by `tools/mkpasswd.py --accounts-only`.
- ACL of that account: read and write `<node>/#`; read only what the device's description needs (`home/clock/#` for a clock, `home/weather`
  for a weather screen) and nothing of `home/state/#`, `home/cmd/#` or another node's topics.

## The flow ("Pair a device" in Settings -> Add device; with the `connect` plug-in a yashGen row of the Add / bind window)

1. The person types the code from the device's label or scans its QR, or picks a device built in this house (the code is computed from the build
   record's secret and the MAC). The dashboard shows the device's name and board from the build record when it has one.
2. The house makes the account and the OTA password, then sends the pair record to the device's setup AP through a relay: the same two relays as
   the Wi-Fi recovery (docs of the provision plug-in) - a neighbour ESP32 node, or the host relay on the Linux host with a spare Wi-Fi card.
   The relay protocol gets a second portal kind next to `/wifisave`: `POST http://192.168.4.1/yash/pair`, form-encoded with a Content-Length,
   the fields of fwgen's doc; the answer `{"ok", "node", "hash"}` goes back. The host relay is the first to support it; a neighbour node
   needs its firmware rebuilt with the new kind (open: whether to do that before the first test).
3. The device reboots into the house Wi-Fi with its own account and publishes its description (docs/device-description.md). The house marks it
   paired when `<node>/status` turns online with that account, and shows the build line of the device window.
4. A failed request changes nothing on either side: the house deletes the account it made and keeps no record.

## Removing, moving

- Removing a paired device in the house: publish `<node>/yash/unpair` (the device wipes its record and returns to its setup AP), wait for its
  status to go offline (at most 10 s), then delete the broker account, clear the retained `<node>/fwgen/*` topics (already done on removal) and
  drop `plugin-state/fwgen/pairing/<node>.json`. The account is revoked even when the device never answered.
- "Moved to another house": the same, with the card kept until the person confirms; the window of such a device says "device moved to another
  house" and its channels show as offline instead of vanishing (rules and panels point at them).
- A device whose pair record the house has no more (its card was removed while it was away) simply cannot connect: its account is gone.

## Order of work

1. This page, agreed with the device side (the payload is fwgen's).
2. Accounts: create and revoke through dynamic-security, the ACL above, with tests against a fake broker answer.
3. The pair record store and the relay request (host relay first); the "Pair a device" dialog; removal and "moved".
4. First test on a spare board chosen with Michael; the live nodes are not touched.


## Pairing a panel (kind "panel")

A screen of the panels plug-in that already has a layout in the house pairs with `kind: "panel"`. Its account is `dev-<name>`:
it writes only `panel/<name>/#` and reads the house state, weather and clock the layout needs. The panel keeps its topic
prefix `panel/<name>`, so the pairing is done when `panel/<name>/status` is online; it gets no card (the panels plug-in shows it).

A house restart between the pair request and the device's first connection does not strand the record: the wait resumes at startup.

When a device is unpaired, removed or marked moved, the house empties every retained topic under its own prefix (`<node>/#`, for a panel `panel/<node>/#` except the layout the house publishes) after the account is gone; other nodes keep theirs.

A paired node, like any node with a description, is skipped by the Add ESPHome discovery (`core.DISCOVERY_IGNORE`), so a build that also publishes Home Assistant discovery configs never gets a second card; a card made through discovery before the description came is kept (the window offers the channels it lacks). Unpair, removal and a move empty the node's `homeassistant/+/<node>/+/config` too.
