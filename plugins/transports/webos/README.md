# webos - LG webOS TVs

An LG webOS TV as a device of the house, spoken to over SSAP (a websocket to the TV, port 3000):
power (on by Wake-on-LAN, off by SSAP), volume (0-100), mute (on / off), input and app (a label of
the lists below or an id: up to 64 letters, digits and `_.:-`, starting with a letter or digit),
media keys (`play`, `pause`, `stop`, `volume_up`, `volume_down`). A TV is never "offline" for the
house, it is off: power-on always goes out; any other command to a TV that is off (its last poll) or
does not answer gets 409 "the TV is off or unreachable (power it on first)" - at once when the poll
already saw it off. A value a command does not take is refused with 400 before anything goes to the
TV (and before the 409 of a TV that is off).

| file | what |
|---|---|
| `__init__.py` | the transport, the poller, `POST /api/webos/<id>/pair`, `GET` / `POST /api/webos/tvs`, `PUT` / `DELETE /api/webos/tvs/<id>` (the house token) |
| `ui.js`, `i18n.json` | the LG card on Settings -> Devices -> TVs: address, MAC, pairing, inputs and apps; Pairing, Edit, Remove; the form of a new one for Add a TV |
| `_ssap.py` | the SSAP client and Wake-on-LAN; also a command line (see its docstring: pair, status, cmd, lists of inputs and apps, wol) |
| `host/wol-relay.py` | the Wake-on-LAN relay on the host (`tools/host-sockets.sh` installs it) |
| `setup/devices.example.json` | a TV: `id`, `name`, `host`, `mac`, `_webos_inputs` (label -> input id), `_webos_apps` (label -> app id), its controls |
| `setup/webos_hosts.example.json` | `{device id: ip}`, when the TV's address changes |

## Files

- `plugin-data/webos/devices.json`: the TVs (entries with `"transport": "webos"`); a bad entry is skipped
  with a log line, the others stay.
- `plugin-data/webos/webos_hosts.json` (optional): `{device id: ip}`. Where it exists it wins over the
  entry's `host`; a helper of yours writes it when the TV's address changes (the container cannot see
  the LAN's ARP table).
- `plugin-state/webos/webos_keys.json` (0600): the client key of each paired TV. A file that does not
  parse turns pairing off (503) until it is fixed or removed.

## Setting up a TV

1. Give the TV a fixed address (reserve it in the router) and allow its wake over the network in its settings.
2. Settings -> Devices -> TVs -> **Add a TV**: the house finds it on the LAN (or by its address), asks its
   name and adds it (`POST /api/webos/tvs`: an entry in `devices.json` with HDMI 1-3, YouTube and Netflix).
   Then **Pairing** (a TV without a MAC tells its own there, for power-on), and **Edit** for its own inputs and apps.
3. Settings -> Devices -> TVs: its LG card. **Edit** changes its name, address, MAC, inputs and apps
   (up to 40 lines each, `name = id`); it rewrites the entry in `devices.json`. **Remove** drops the
   entry and its pairing key; the TV itself is not touched.
4. Install the Wake-on-LAN relay on the host (see "Wake-on-LAN" below).

## Pairing

Settings -> Devices -> TVs -> the LG card -> **Pairing**, or by hand:

    curl -X POST -H "Authorization: Bearer <DASHBOARD_TOKEN>" http://<house>:8765/api/webos/<id>/pair

Then accept the prompt on the TV within 40 s. The answer is `{ok, paired, persisted}` (and a `warning`
when the key could not be saved; the TV gives a new key only after another prompt). 404: no such TV;
502 `pair failed: ...` (the message may be empty on a timeout). A command to a TV that is not paired
answers 400 "TV not paired"; a failed one 502 `webos: ...`.

## State

Every TV is polled every 20 s (plus the time of the poll, at most 30 s). Not paired: offline, power off.
Paired but SSAP does not answer: online, power off (the TV is in standby and can be woken). After a
command the state is checked again at 2, 4, 7, 12, 20 and 30 s, until it matches. SSAP: 8 s to connect,
8 s per answer, 20 s for one whole command.

## Wake-on-LAN

Power on needs a valid `mac` of the TV in its entry (400 when it is missing or malformed; any of
`aa:bb:..`, `aa-bb-..`, `aabb..` is sent on as `aa:bb:cc:dd:ee:ff`). It sends one datagram `WOL <mac>` to
the relay on this host (`/run/yashome/wol.sock`; not installed: skipped), then the magic packet from the container itself
(ports 9 and 7, broadcast) - a container on Docker's bridge network cannot reach the LAN that way, so the relay is what wakes
the TV. `sudo sh tools/host-sockets.sh` (once, and again after an update that changes the host pieces) installs the house's helpers
on its host: the relay among them (`yashome-wol.socket`, a service with no rights of its own; at most 10 packets in
10 s). No port, no setting.

Only the house's group may write to the socket; the relay sends to 255.255.255.255 (the host's own LAN),
ports 9 and 7. A send that fails is logged, the other targets are still tried, and the relay keeps running.
