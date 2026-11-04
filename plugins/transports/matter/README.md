# matter

Matter devices in the house. The controller is [python-matter-server](https://github.com/home-assistant-libs/python-matter-server)
(the open-source one Home Assistant uses), run by the role **Matter controller** of the plug-in `computers`; this plug-in talks
to its WebSocket API, makes every commissioned node a house device and sends the cards' commands to it. Requires `computers`.

## The controller

- One per house. Settings -> Devices -> Computers and servers -> a computer's card -> Matter controller -> Install
  (Linux, not a WSL; the house's own computer too). Install refuses a second computer; there is no Use: the house uses the
  computer it is installed on.
- `helper/roles/matter-install.sh` (run there as root by the helper): docker when missing, the image
  `ghcr.io/home-assistant-libs/python-matter-server` 8.1.0 pinned by digest, in `/opt/yashome-matter` (`compose.yml`, the fabric
  in `data/`, root's only). Host network: Matter is IPv6 and mDNS; Thread nodes are reached through the house's Thread border
  routers, whose routes that computer has. Bluetooth: `hci0` through BlueZ on the host's D-Bus (`/run/dbus` read-only,
  `--bluetooth-adapter 0`); without `hci0` it runs without Bluetooth (only codes shared from a phone app).
- Its WebSocket listens on `127.0.0.1:5580` only. The house reaches it through the helper's ssh tunnel `matter`
  (`helper:5580`, `ws://helper:5580/ws`); the plug-in points the tunnel at the computer with the role every minute
  (`MATTER_URL` in `.env` overrides the address, for a test).
- Remove (the card) stops it and keeps its fabric in `/var/backups/yashome-matter-<time>`: put back as `data/`, the devices
  answer again; a new fabric does not reach them until each is reset and paired again.

## Pairing

Add -> Matter (or Settings -> Devices -> Matter -> Pairing) has one field, the code:

- **A new device**: the 11 digits under its QR code (spaces and dashes are fine) or the QR code's text (`MT:...`). Put it near
  the controller's computer and switch it on: the controller finds it over Bluetooth, gives it the house's Thread network (a
  Thread device) and lets it in.
- **A device in a phone app** (Google Home, Apple Home: "Share with another app", multi-admin): its code. It is on the
  network already; the controller finds it there, no Bluetooth.

Both go to `commission_with_code(code, network_only)`: `network_only` is false when the controller has Bluetooth (it then
looks over Bluetooth and the network), true without it. It runs in the background, up to 5 minutes; the window follows it
and opens the new card. The code is never logged, kept or answered.

**The Thread network.** On every connection, and before each pairing, the controller gets the house's Thread dataset from
the plug-in thread (`thread.dataset()`) with `set_thread_dataset`; it is never logged, kept here or answered (the controller
logs it only at its verbose level, off by default). No Thread network kept: Wi-Fi and Ethernet devices only.

## Devices

Each node is a house device `matter-<node id>` (transport `matter`), named by its NodeLabel, else its product name (the
card's pencil renames it in the house). Its channels, per endpoint (a kind on several endpoints: `_<endpoint>` on each code):

| cluster | channel | kind |
|---|---|---|
| OnOff | `state` | switch: `On` / `Off` |
| LevelControl | `brightness` 0-254 | bright: `MoveToLevelWithOnOff` |
| TemperatureMeasurement | `temperature` | sensor, degrees C |
| RelativeHumidityMeasurement | `humidity` | sensor, % |
| OccupancySensing | `occupancy` | sensor (bit 0) |
| BooleanState | `contact` | sensor (true: closed) |
| PowerSource | `battery` | sensor, % (when the node reports BatPercentRemaining) |

The rest of a node's clusters (ColorControl, WindowCovering, Thermostat, DoorLock, ...) are listed as "not shown yet" in its
window and in Settings -> Devices -> Matter. A node with none of the above is an "unsupported" device with a Reachable channel.
Values come from the controller's subscriptions (`attribute_updated` events); a node it marks unavailable is offline at once.
The last list of devices is kept in `plugin-state/matter/nodes.json`: the cards (offline) stand before the controller answers.

## Routes (house token)

| route | what |
|---|---|
| `GET /api/matter` | the controller (computer, answers, Bluetooth, Thread network given) and the nodes: name, vendor, product, reachable, network (thread / wifi / ethernet), channels, not shown yet |
| `POST /api/matter/commission {code}` | pairing starts (409 while one runs, 503 without the controller) |
| `GET /api/matter/commission` | its state: running / done / failed, the new device, the error |
| `DELETE /api/matter/nodes/{id}` | Unpair: `remove_node` (the node leaves the fabric), its device leaves the house |

## The API calls used

`start_listening` (the node list, then events: `node_added`, `node_updated`, `attribute_updated`, `node_removed`,
`server_info_updated`), `set_thread_dataset`, `commission_with_code`, `device_command(node_id, endpoint_id, cluster_id,
command_name, payload)`, `remove_node`. The server sends its info first on every connection.

## Health

Settings -> Overview: no controller (install the role); the controller does not answer; installed on more than one computer;
no Bluetooth; it has no Thread network yet; each node it cannot reach.

## Limits

- Color, covers, locks, thermostats, fans and buttons are not shown yet. A bridge's endpoints all land on one device.
- Bluetooth pairing needs the device near the controller's computer.
- `open_commissioning_window` (sharing a house device to a phone app) is not offered yet.
