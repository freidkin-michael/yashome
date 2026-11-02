# pair - pairing a fwgen device (the house side)

The house side of docs/pairing.md. A fwgen firmware carries no house secret; at pairing the house gives it its own MQTT account,
its Wi-Fi and its OTA password. This plug-in (first step) holds the accounts and the passwords:

- `make_account(node, reads)`: a broker account `dev-<node>` made at run time through the broker's dynamic security (no broker
  restart) that reads and writes `<node>/#` and reads only `home/clock/#` and `home/weather` when asked; nothing of `home/state/`
  or `home/cmd/`. The broker's file for its next start is written by `tools/mkpasswd.py --accounts-only` from
  `plugin-state/pair/mqtt_accounts.json` (0600).
- `prepare(node, house_id)`: that account and the device's own OTA password, kept in `plugin-state/pair/pairings.json` (0600): what
  the pair request carries.
- `revoke(node)`: tells the device to wipe its record (`<node>/yash/unpair`), then deletes its account, its file entries and the OTA
  password; removing the device in the house does it too (`core.DEVICE_REMOVED`). The shared MQTT user `esp` of the nodes that run
  today is never touched.
- `GET /api/pair` lists the paired nodes (no secrets), `DELETE /api/pair/{node}` revokes one (house token).
- `POST /api/pair/start {ap, code}` (house token): pairs the device behind its setup AP `<node>-setup` (the AP key is the pairing code):
  makes its account and OTA password, asks the host relay of the provision plug-in to join the AP and POST the form to `/yash/pair`
  (the house Wi-Fi, the broker as nodes reach it, the account, the OTA password); a refused or unreachable request leaves no account.
  After the device is online and has described itself (fwdesc) its card is made from the description (`POST /api/pair/{node}/card` does
  it by hand): sensor, binary_sensor, text_sensor and switch entities, topics expanded with the node's prefix.
- The house's name a paired device stores: `yashome` (or the one a request names).

A panel of the house pairs with `kind: "panel"`: account `dev-<name>` on `panel/<name>/#`, done when `panel/<name>/status` is online, no card (see `docs/pairing.md`).

A restart before the device first connects does not strand the record: the wait resumes at startup.

Unpair, removal and a move also empty the node's retained topics (`<node>/#`; a panel's `panel/<node>/#` without its layout).

A paired node is skipped by the Add ESPHome discovery; unpair also empties its `homeassistant/+/<node>/+/config`.
