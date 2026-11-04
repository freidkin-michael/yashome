# Pairing a fwgen device - the house side

Status: implemented in the plug-in `pair` (`plugins/tools/pair/`), with the host relay of the plug-in `provision`. A few
parts are still planned (end of this page). The device side (the setup AP `<node>-setup`, `POST /yash/pair`, the
8-character code, `<prefix>/yash/unpair`) is fwgen's `docs/pairing.md`; this page says what the house does. It is for
NEW nodes only: the shared device accounts of `MQTT_DEVICE_USERS` are not touched.

## What a firmware carries and what the house gives

The firmware carries no house secret. At pairing the house hands over, in one request: its id (`house_id`, default
`yashome`, at most 23 characters), the Wi-Fi (`HOUSE_WIFI_SSID`, `HOUSE_WIFI_PASSWORD` in `.env`), the broker (host and
port as the nodes reach it, from `BIND_ADDRESS`), the device's OWN MQTT user and password, and the device's OTA
password. A device can be paired again into any house.

## Where things live

All files are 0600, under `plugin-state/pair/` of the house folder (outside git; the nightly backup has them):

- `mqtt_accounts.json`: the accounts `{user, password, kind, topics, read}`. `tools/mkpasswd.py --accounts-only` writes
  the broker's file for its next start from it (the plug-in's `setup/broker.json` names this file), the way the panels'
  accounts are kept.
- `pairings.json`: node -> `{user, ota_password, house_id, state, made, sent, paired, via}` (`via`: wifi | thread). `state` is `new`, `sent` or
  `paired`.
- `moved.json`: node -> when the person unpaired it (Unpair keeps the card), and its card.

A lost device is paired again with a new account.

The broker account is `dev-<node>`. It is made at run time through mosquitto's dynamic security
(`$CONTROL/dynamic-security/v1`, no broker restart); the house waits for the broker's answer, and a refused account
leaves no record (503). Its ACL: read and write `<node>/#`; read only `home/clock/#` and `home/weather` when the request
asks for them; nothing of `home/state/#`, `home/cmd/#` or another node's topics.

A node name is a small Latin letter, then letters, digits, `-` or `_`, at most 28 characters (`dev-` + the name fits the
broker's 32). It may not be a namespace of the house or of a plug-in (`home`, `homeassistant`, `zigbee2mqtt`, `panel`,
`provision` and the plug-ins' own), nor the first topic level or the id of a device the house already has (409). The
same node paired again gets its own account back.

## The flow (All devices -> + Add device -> Pair a device)

With the plug-in `connect` the Add button opens its "Add / bind" window; "Pair a device" is then under "Other ways".


1. The person types the setup network (`<node>-setup`) and the code from the device's label, or scans its QR
   (`YASHPAIR:1:<node>-setup:<code>`, where the browser can read QR codes), or picks the network from "Look for setup
   networks" (`POST /api/esphome/ap/scan`). The code is 8 characters, letters A-Z and digits 2-7.
2. `POST /api/pair/start {ap, code, house_id?, bssid?, reads?, kind?}` (house token): the house makes the account and the
   OTA password, then asks the host relay (`/run/yashome/provision.sock`, installed by `tools/host-sockets.sh`) to join
   the device's setup AP (its key is the code) and `POST http://192.168.4.1/yash/pair`, form-encoded with a
   Content-Length. The device's answer `{ok, node, hash}` and the relay's status lines (no secrets) come back.
3. A refused or unreachable request deletes the account it made (unless the node was paired before) and keeps no
   record.
4. The device reboots into the house Wi-Fi with its own account. The house waits up to 150 s for `<node>/status`
   `online` and marks it `paired`. When its description has arrived (docs/device-description.md, up to about 60 s more),
   the house makes its card from it: `sensor`, `binary_sensor`, `text_sensor` and `switch` entities become channels,
   their topics expanded with the node's prefix; `rssi`, `uptime` and the like go under advanced. A card that already
   exists (made through discovery) is kept. `POST /api/pair/{node}/card` makes the card by hand.
5. A house restart between the request and the device's first connection does not strand the record: the wait resumes
   at startup for every record in state `sent`.

## Over Thread (plug-in thread)

A Yash Thread node (yashGen) has no setup AP: it joins the Thread network with the public joiner code while the person
has a pairing open (Add -> Thread), and the thread plug-in then sends the same pair request to it over Thread: `ctl.sh pair
<IPv6>` on the main border router posts the form to `http://[<address>]/yash/pair`. The form has no `code` and no Wi-Fi
(`thread_form`: `house_id`, `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_password`, `ota_password`): the request comes from
inside the Thread network, which only the house lets a device into, and the node takes it only while it is not paired
(after its button was held 5 s). `pair_with` is the same path as the AP's: the account, the wait, the card.

## Removing, moving

- `DELETE /api/pair/{node}` (house token), or removing a paired device in the house (`core.DEVICE_REMOVED`): publish
  `<node>/yash/unpair` (the device wipes its record and returns to its setup AP), wait 3 s, then delete the broker
  account and the file entries. The account is revoked even when the device never answered. Then every retained topic
  under the node's prefix (`<node>/#`) and its Home Assistant configs (`homeassistant/+/<node>/+/config`) are emptied;
  other nodes keep theirs.
- "Unpair" (a button in the window of a paired device, `POST /api/pair/{node}/moved`): the same, and the card stays.
  The window says what the device does then by the record's `via`: a Wi-Fi device opens its setup network, a Thread one
  stays on Thread and waits for a new pairing (blue blink); then "This device is unpaired; its card stays offline until
  it is paired here again". Pairing it again clears the mark.
- A device whose pair record the house has no more simply cannot connect: its account is gone.
- `GET /api/pair` (house token) lists the paired nodes and the moved ones, without secrets.

A paired node, like any node with a stored description, is skipped by the Add ESPHome discovery
(`core.DISCOVERY_IGNORE`). So a build that also publishes Home Assistant discovery configs never gets a second card.

## Pairing a panel (kind "panel")

A screen of the panels plug-in that already has a layout in the house pairs with `kind: "panel"` (through the API; the
dialog pairs devices only). Its account is `dev-<name>`: it writes only `panel/<name>/#` and reads `home/state/#`,
`home/weather` and `home/clock/#`. The panel keeps its topic prefix `panel/<name>`, so the pairing is done when
`panel/<name>/status` is online. It gets no card (the panels plug-in shows it). On unpair the house empties
`panel/<name>/#` except the layout it publishes (`panel/<name>/layout`).

## Not implemented yet

- The neighbour ESP32 relay: only the host relay (a Linux host with a Wi-Fi card) sends the pair request.
- Picking a device built in this house (the code computed from the build record) and showing its name and board before
  pairing.
- Card entities other than sensor, binary_sensor, text_sensor and switch (they are skipped and logged).
- The house keeps the OTA password, but no update path uses it yet.
