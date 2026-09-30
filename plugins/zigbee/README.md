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
- **Dashboard.** "Add" -> Zigbee: permit_join for 254 s, the devices that join with their interview
  state, a name for each, leftovers of failed pairings to remove. A Zigbee device's modal has
  "Rename in zigbee2mqtt": the key itself changes, and the core moves every binding, favourite,
  room, automation and timer with it.

Routes (token needed): `POST /api/zigbee/pair/start|stop`, `GET /api/zigbee/pair/status`,
`POST /api/zigbee/pair/add`, `POST /api/zigbee/device/rename`, `POST /api/zigbee/device/remove`.

## Setup

`make install` / `make update` of the core run `setup/`:

| key in `.env` | what |
|---|---|
| `Z2M_SERIAL` | the coordinator stick by its `/dev/serial/by-id/` path (found or asked at install); empty or `none` = no zigbee2mqtt; `external` = it runs elsewhere on this broker |
| `Z2M_PORT` | host port of the zigbee2mqtt frontend (8080) |
| `Z2M_AUTH_TOKEN` | the frontend login (generated) |
| `MQTT_Z2M_USER`, `MQTT_Z2M_PASSWORD` | zigbee2mqtt's broker account (generated); role `zigbee2mqtt`, rights on `zigbee2mqtt/#` only |
| `Z2M_DATA_DIR` | zigbee2mqtt's data: `configuration.yaml`, `database.db`, the network. Set by `make update`: `./config/zigbee2mqtt` when a network is already there (an existing zigbee2mqtt network), else `./plugin-data/zigbee/zigbee2mqtt` |

- `setup/compose.yml`: the `zigbee2mqtt` service, profile `zigbee`; `setup/secrets.sh` switches
  that profile on exactly when `Z2M_SERIAL` names a stick, `setup/update.sh` removes the
  container when it went off.
- `setup/secrets.py` writes `secret.yaml` into `Z2M_DATA_DIR` and, on the first run,
  `configuration.yaml` from `setup/configuration.example.yaml` (adapter `ember` for Silicon Labs
  sticks; `zstack` for TI ones such as the ZBDongle-P).
- `setup/broker.json`: device accounts may read `zigbee2mqtt/#` but never publish into it, so a
  node cannot pair, remove or switch a Zigbee device; only the backend and zigbee2mqtt can.
- `setup/preflight.sh` (the stick, the frontend port), `setup/smoke.sh` (zigbee2mqtt reports
  online), `setup/summary.sh` (where the frontend is).

Nothing of one house lives in this folder: the network, the device names and the secrets are in
`Z2M_DATA_DIR` and `.env`.

## Tests

`tests/plugins/test_zigbee.py`: exposes, sync, state and availability, bridge state, actuation and
rule shortcuts, the periodic /get, pairing, the key rename, the setup files (`mkpasswd.py` with
`broker.json`, `secret.yaml`), and a frozen check - `tests/plugins/zigbee_parity/`: a made-up
device list (`make_fixture.py`) fed with the same frames through the plug-in must give exactly the
devices, channels, states, fired rules and published topics recorded in `expected.json`.
