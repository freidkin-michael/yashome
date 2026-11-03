# zigbee - Zigbee through zigbee2mqtt

Zigbee for Yashome through [zigbee2mqtt](https://www.zigbee2mqtt.io/), with its container.

## What it does

- **Devices.** The coordinator's retained `zigbee2mqtt/bridge/devices` becomes devices of
  transport `z2m` through the core's `register_device` / `update_device` / `remove_device`. The
  zigbee2mqtt friendly name is the device id (spaces and any script are fine); channels come from
  the device's `exposes`: switches and dimmers per gang (`state_l2`, `brightness_l2`), read-only
  sensors, battery, the `action` of a remote. A device z2m could not identify is registered with a
  signal readout and `unsupported`, so it can be removed and paired again. EF00-only (Tuya
  datapoint) devices are never asked with `/get`.
- **State.** `zigbee2mqtt/<name>` frames into the core's cache; `.../availability` and
  `bridge/state` mark devices offline at once (zigbee2mqtt down = all of them); a button's
  `action` goes into the rule engine as gesture `<action>` of code `action`, once per press.
- **Actuation.** `zigbee2mqtt/<id>/set`; a rule targeting a Zigbee device sends what the device
  does itself (`TOGGLE`, a relative brightness step on the right gang); an EF00-only one takes the
  core's generic path.
- **Keeping in sync.** Every 30 s the controllable devices that did not report by themselves are
  asked (`/get`), with a 10 min backoff for one that does not answer; `POST /api/resync` asks all.
  A device back after an outage (or waiting since the start) that has not reported in 5 s is asked
  once (`core.STATE_ASK`, docs/device-state.md).
- **Leaving.** A device out of the list keeps its state for 10 min (a rejoin or a rename brings it back); then the
  plug-ins hear `DEVICE_REMOVED` once (an unpair from the dashboard: at once). A device back in a list drops its record.
  While the last list is empty or lacks more than half of the known devices (waiting records count), nobody is told.
  The waiting records are kept in `plugin-data/zigbee/gone.json`, so a restart does not lose them.
- **Dashboard.** "Add" -> Zigbee (with the `connect` plug-in: the Add / bind window opens it with everything else): permit_join for 254 s, the devices that join with their interview
  state, a room and a name for each, leftovers of failed pairings to remove. A room picked with no name typed names the
  device "<Room> <kind>" ("Hallway motion sensor", the kind in the house language); moved to another room later (the
  device window, the map, a renamed room), such an automatic name follows the room, a typed one never changes. A Zigbee device's modal has
  "Rename in zigbee2mqtt": the key itself changes, and the core moves every binding, favourite,
  room, automation and timer with it.
- **Settings -> Devices -> Zigbee.** Three tabs: Devices (every Zigbee device; a tap opens its
  window), Pairing (the same pairing window) and Gateway (the coordinator, its network, the
  connection; zigbee2mqtt's own page).

Routes (token needed): `POST /api/zigbee/pair/start|stop`, `GET /api/zigbee/pair/status`,
`POST /api/zigbee/pair/add`, `POST /api/zigbee/device/rename`, `POST /api/zigbee/device/remove`,
`GET /api/zigbee/gateway`, `GET /api/zigbee/gateway/key`, `POST /api/zigbee/gateway/check`, `POST /api/zigbee/ui`
(Settings -> Devices -> Zigbee -> Gateway: the
coordinator and its network from bridge/info, else configuration.yaml and the coordinator backup; the connection and adapter
are saved through the helper as Z2M_SERIAL / Z2M_ADAPTER and applied).

## Setup

`make install` / `make update` of the core run `setup/`:

| key in `.env` | what |
|---|---|
| `Z2M_SERIAL` | the coordinator: a stick by its `/dev/serial/by-id/` path, or a network one as `tcp://host:port` (SMLIGHT SLZB, ZigStar); found or asked at install (USB, then mDNS); Settings may set a `tcp://` one or `none` (`[network-ok]`), a stick only on the host; empty or `none` = no zigbee2mqtt; `external` = it runs elsewhere on this broker |
| `Z2M_DEVICE` | set by `make update` from `Z2M_SERIAL`: the device passed into the container, `/dev/null` for a network coordinator |
| `Z2M_ADAPTER` | `ember` (Silicon Labs EFR32) or `zstack` (TI CC2652); at install from the stick's name or the radio type a network coordinator announces; empty keeps the one in `configuration.yaml` |
| `Z2M_AUTH_TOKEN` | the frontend login (generated); the dashboard fills it in, nobody types it |
| `MQTT_Z2M_USER`, `MQTT_Z2M_PASSWORD` | zigbee2mqtt's broker account (generated); role `zigbee2mqtt`, rights on `zigbee2mqtt/#` only |
| `Z2M_DATA_DIR` | zigbee2mqtt's data: `configuration.yaml`, `database.db`, the network. Set by `make update`: `./config/zigbee2mqtt` when a network is already there (an existing zigbee2mqtt network), else `./plugin-data/zigbee/zigbee2mqtt` |

- `setup/compose.yml`: the `zigbee2mqtt` service, profile `zigbee`; `setup/secrets.sh` switches
  that profile on exactly when `Z2M_SERIAL` names a coordinator and sets `Z2M_DEVICE`; `setup/update.sh` removes the
  container when it went off and restarts it when its secrets or its coordinator changed.
- `setup/secrets.py` writes `secret.yaml` into `Z2M_DATA_DIR`, on the first run
  `configuration.yaml` from `setup/configuration.example.yaml`, and on every run the `serial:` block in step with
  `Z2M_SERIAL`: `port: /dev/ttyUSB0` for a stick, `port: tcp://...` with `baudrate: 115200` and `rtscts: false` for a
  network coordinator; the rest of the file stays. Values are compared, not text, so zigbee2mqtt's own rewrite of
  the file is no change.
- `setup/discover.sh`: network coordinators announced over mDNS (`_slzb-06._tcp`, `_zigstar_gw._tcp`,
  `_tubeszb._tcp`, needs `avahi-browse`), as `tcp://IP:PORT ADAPTER`; `setup/install.sh` and `setup/detect.sh` use it.
- `setup/broker.json`: device accounts may read `zigbee2mqtt/#` but never publish into it, so a
  node cannot pair, remove or switch a Zigbee device; only the backend and zigbee2mqtt can.
- `setup/preflight.sh` (the stick, or that a network coordinator answers on its port), `setup/smoke.sh` (zigbee2mqtt reports
  online), `setup/summary.sh` (where the frontend is).
- zigbee2mqtt's own page publishes no port: Settings -> Devices -> Zigbee -> Gateway -> Open zigbee2mqtt. The dashboard hands it on at
  `/plugins/zigbee/ui/` (its `frontend.base_url`, set in `setup/compose.yml`) to whoever got a pass there (`POST /api/zigbee/ui`,
  the house token: a cookie for that path only, 12 hours) and fills in zigbee2mqtt's login itself.

### Moving the network to another coordinator

Same chip family only (EFR32 to EFR32, CC2652 to CC2652), the old one unplugged before the new one
starts: two coordinators with one network fight. Set `Z2M_SERIAL` (and `Z2M_ADAPTER` if needed),
`make update`: zigbee2mqtt finds a radio whose network differs from `configuration.yaml` and forms it
again from `coordinator_backup.json`. The `ember` adapter does not carry over the coordinator's
address (IEEE), and devices bound to it stop reporting: stop zigbee2mqtt, write the old address
(`ieeeAddr` of the Coordinator in `database.db`) with `universal-silabs-flasher --device
socket://<host>:<port> write-ieee --ieee <it>`, make the radio leave its network (bellows `leave`),
start zigbee2mqtt so it forms from the backup once more. A network coordinator needs a steady 5 V
supply: a weak one resets it the moment the radio starts sending.

Nothing of one house lives in this folder: the network, the device names and the secrets are in
`Z2M_DATA_DIR` and `.env`.

## Tests

`tests/plugins/test_zigbee.py`: exposes, sync, state and availability, bridge state, actuation and
rule shortcuts, the periodic /get, pairing, the key rename, the setup files (`mkpasswd.py` with
`broker.json`, `secret.yaml`), and a frozen check - `tests/plugins/zigbee_parity/`: a made-up
device list (`make_fixture.py`) fed with the same frames through the plug-in must give exactly the
devices, channels, states, fired rules and published topics recorded in `expected.json`.
