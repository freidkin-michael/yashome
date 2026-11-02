# provision - ESP recovery via the setup AP

An ESPHome node that lost its Wi-Fi (a factory reset, a new router, a new password) opens its own access
point `<name>-setup` with a captive portal at `http://192.168.4.1`. This plug-in hands it the house Wi-Fi
through a relay, so nobody has to walk there with a phone: Add -> **ESP via its setup AP** (with the `connect` plug-in: such a node is a row of the Add / bind window, Send Wi-Fi), pick the AP,
**Send Wi-Fi**. The node restarts into the house network; the plug-in reports success only once the node
is back: its `<name>/status` or `panel/<name>/status` turns online after the attempt started, or it sends
a scan report. A node whose status says online already is not in its setup AP: the backend refuses
(409, "the node <name> is online: nothing to recover; a twin?") before any relay leaves. A broker that
went down while the node was connected may keep a stale retained "online": the dialog then asks and
resends with `force: true`, which first publishes a retained "offline" there (by hand:
`mosquitto_pub -r -t <name>/status -m offline`), so the node's own "online" once back counts again.

A brand-new blank board still gets its first firmware over USB; this plug-in only recovers nodes that
already run firmware with a fallback AP (`firmware/base.yaml` has one).

## The two relays

| relay | what it hands over | needs |
|---|---|---|
| a neighbour node | the Wi-Fi it runs on itself (no password travels anywhere) | `firmware/provision.yaml` in its firmware |
| the host relay | `HOUSE_WIFI_SSID` / `HOUSE_WIFI_PASSWORD` from the core's `.env` | `host/esp-provision.py` on a Linux host with a spare Wi-Fi card |

Neighbours are discovered: any node with the package announces itself on `provision/seen/<node>`
(retained, at every MQTT connect and after every scan; a retained one only says the node exists, never
what it hears now). A node that missed 3 scans in a row is shown as gone and not waited for. "auto"
tries the two loudest nodes that hear the AP, then the host; the dialog can also pick one. A relay node
leaves the network for about 80-90 s at most (it does not answer meanwhile) and reboots itself if it does
not get back. A node already relaying answers a second command "busy" at once, and the next relay is tried.
The longest a Send Wi-Fi can take (every relay timing out) is shown in the dialog, about 8 minutes.

Portals: ESPHome (`POST /wifisave`, `ssid`/`psk`) is the tested one; OpenBeken and Tasmota presets are
there but untested. The portal must be an IPv4 address inside 192.168.4.0/24; the backend, the host
helper and the relay node check the path (`/` letters, digits, `/ _ . - ? = &`) and field names (letters,
digits, `_`) alike.

## The setup-AP key

Every house-built node secures its setup AP with one shared key, `PANELS_AP_PASSWORD` (make install /
make update generate it; the panels use it, and `firmware/base.yaml` takes it as `ap_password`). For a
secured `*-setup` AP the dialog's password field may stay empty: the host relay gets `PANELS_AP_PASSWORD`
from the backend, a relay node uses its own compiled `ap_password` (the same key; the `do` carries it
empty). So an OPEN AP named `*-setup` is not a house node: it is refused (409, "an open -setup AP: not
one of the house's nodes") when `PANELS_AP_PASSWORD` is set, and so is an open AP named after a node or
device the house knows; the host helper and the relay node refuse to join an open `*-setup` AP too. Open
APs of other names stay possible, behind the dialog's confirmation. A typed password is never dropped: when one name is heard both open and secured, the backend refuses (409, "two
APs with this name"), and so it does for one name from two MACs, or a typed password for an AP heard
open. The dialog shows each AP's MAC; it is red when it is not an Espressif MAC (it may be a fake AP).
Both relays join only that MAC: the host pins it in nmcli, a node checks it before it posts anything.
The house does not know a node's MAC beforehand, so "the node's MAC + 1" is not checked.

## The host relay

`host/esp-provision.py` is a small HTTP service (python3 only) that drives NetworkManager: it joins the
AP on the spare card, posts the house Wi-Fi to the portal and always puts the card back on the
connection it had before, also when the relay fails. It listens on `/run/yashome/provision.sock`, a socket file systemd
makes, writable by the house's group only (the home container has `/run/yashome` mounted): no port, no token, no address.

1. Set the core keys `HOUSE_WIFI_SSID` / `HOUSE_WIFI_PASSWORD`.
2. On the house's host with the Wi-Fi card (NetworkManager managing it), with the plug-in on:
   `sudo sh tools/host-sockets.sh` (once, and again after an update that changes the host pieces) installs the house's helpers
on its host - this one too: the user `yashome-provision`, the root-owned nmcli gate with its sudoers line,
   `yashome-provision.socket` and its service. `/etc/yashome/provision.env` may name the card (`PROVISION_WIFI_IF`).
3. Settings -> Health shows "ESP recovery: host relay" as ok, or the helper's own error (e.g. "no Wi-Fi
   interface" when it finds no Wi-Fi card; then /scan and /provision answer 503 too).

`PROVISION_WIFI_IF` empty = the first Wi-Fi interface NetworkManager knows (`wlan0`, ...). Use a card
that is not the host's own uplink: during a relay it is off the house network. The helper reads with plain
nmcli and changes only through `esp-provision-nmcli`, a root-owned gate that runs a fixed set of nmcli
commands (rescan, list, connect to one AP as the profile `yashome-provision` made with autoconnect no,
up/down/delete of that profile, up of a saved profile to restore the card) and refuses the rest. The
helper deletes a leftover `yashome-provision` at start. When putting the card back fails it tries once
more, then reports `restore_failed` (the dialog shows it): check the card by hand.

## Recovery is not a firmware change

What the lost node's portal saves goes to its flash (NVS), keyed by the hash of its config: it survives
reboots and identical rebuilds, and is dropped only by a build with a changed config or another ESPHome
version; the node then runs on its compiled Wi-Fi again, and with an outdated one it shows `<name>-setup`
once more. The relay node joins the setup AP with `save: false` and goes home to the network it ran on
before without saving anything (its flash stays as it was). The durable fix is an OTA build with the current `HOUSE_WIFI_*`.

## Upgrading

OTA the relay nodes first, then update the core: a relay with the old firmware takes the new `do` (an
empty `ap_password` for a `*-setup` AP) as an open AP and fails slowly (about 80-90 s per attempt).

## When the house Wi-Fi changes

1. Set the new `HOUSE_WIFI_SSID` / `HOUSE_WIFI_PASSWORD` and OTA every node (and the panels) while the
   old network still runs. Each one drops off after its OTA and keeps looking for the new network (its
   setup AP opens meanwhile), so do this right before step 2.
2. Then switch the router: the nodes join the new network by themselves.
3. A node missed in step 1 opens its setup AP; recover it here. After a full network change only the host
   relay can start the chain: a relay node hands over the Wi-Fi it runs on itself, which is now the dead
   one. Once one node runs on the new network, it can relay too.

## Firmware packages

`firmware/` holds ESPHome packages for your own node configs: `base.yaml` (Wi-Fi with the fallback AP
`${name}-setup`, captive portal, MQTT), `provision.yaml` (makes the node a relay) and
`provision_scan.h` (its C++ helpers). See `firmware/README.md`.

## Bus

The plug-in owns the namespace `provision/`:

| topic | from | payload |
|---|---|---|
| `provision/scan` | backend | `1`: every relay node scans |
| `provision/seen/<node>` | node, retained | `{"node", "aps": [{"ssid", "rssi", "ch", "open", "setup"}]}` |
| `provision/<node>/do` | backend | `ap\|host\|METHOD\|path\|ssid_field\|psk_field\|bssid\|id\|ap_password` |
| `provision/<node>/result` | node | `{"node", "ap", "id", "ok", "busy", "detail"}` |

`do` always has 9 fields; `bssid` (`AA:BB:CC:DD:EE:FF`) and `ap_password` may be empty; `id` is the
attempt, echoed in `result`: the backend takes only the result of its own attempt and AP, so a late one
of an older attempt is ignored. `ap_password` is last and runs to the end of the line; it is empty for
the house key (the node uses its own) and holds only a key typed in the dialog.

`setup/broker.json` makes `provision/scan` and `provision/+/do` read-only for device accounts: only the
backend can send a node away from the network. `provision/<node>/result` stays writable (a relay node is a
device account), so a result alone proves nothing: the backend reports success only when the recovered
node itself shows up within 60 s (its status turning online after the start, a retained "online" from
before does not count), else "the relay says done; waiting for the node to show up". A node removed for
good leaves its retained announcement; clear it with `mosquitto_pub -r -n -t provision/seen/<node>`.

## Security

- The house Wi-Fi password never goes on MQTT. A neighbour relay hands over its own credentials from
  its flash; only the host relay gets `HOUSE_WIFI_PASSWORD`, in the HTTP body to the helper over its socket file: it never
  crosses a network.
- The socket file's mode is the only guard of the host helper: whoever may write to it (root and the house's group, the
  home container) can make the host join any AP and post the house Wi-Fi to a portal in 192.168.4.0/24. The sudoers line
  gives that user only the nmcli gate.
- The shared setup-AP key (`PANELS_AP_PASSWORD`) does not go on MQTT either: a relay node has it compiled
  in. Only a key typed in the dialog (an AP that is not a house node) travels in the `do` command, which
  device accounts can read. The host helper hands the key to its gate on stdin, and the gate to nmcli in
  a 0600 passwd-file: never in a process's arguments or a log.
- There are no per-node MQTT accounts: any house device account can read the bus and could publish a
  false `provision/<node>/result`, `provision/seen/<node>` or `<node>/status`, i.e. lie about a recovery
  (or hide one). It cannot learn the house Wi-Fi password this way, which never goes on MQTT.
- `esp-provision-nmcli` brings up only its own profile or a saved Wi-Fi profile bound to the pinned card
  (or to no interface), to restore the card.
- Both routes (`POST /api/esphome/ap/scan`, `POST /api/esphome/ap/provision`) take only the dashboard's own
  token, not a phone's or a panel's; at most two run at once. The dialog asks before it sends the house
  Wi-Fi to an AP not named `*-setup` (a neighbour's unprovisioned device is also an open AP).

If no relay manages it: join `<name>-setup` from a phone and open `http://192.168.4.1`.
