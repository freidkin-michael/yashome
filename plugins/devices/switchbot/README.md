# switchbot - SwitchBot locks over Bluetooth

A SwitchBot Lock, Lock Pro or Lock Ultra talks Bluetooth LE only (no Wi-Fi, no Zigbee). A bridge on a
Linux host within Bluetooth range of the lock links it to the house over MQTT. The lock then becomes a
normal device: a Lock switch (Locked / Unlocked), State, Battery and Door. The Yash! lock tile and
lock widgets find it by themselves. After the key is fetched once, the SwitchBot cloud is not needed.

| file | what |
|---|---|
| `__init__.py` | `GET /api/switchbot` (the locks and what the bridge last reported), `PUT /api/switchbot/locks/<id>` (add or rename, house token), the health page rows |
| `ui.js`, `i18n.json` | Add -> SwitchBot lock (delete uses the core's `/api/esphome/remove`) |
| `host/switchbot-lock.py`, `host/switchbot-lock@.service` | the bridge, one systemd instance per lock |
| `host/switchbot-get-key.py` | fetches a lock's encryption key once from the SwitchBot account |
| `host/install.sh`, `host/requirements.txt`, `host/switchbot.env.example` | the bridge's venv, unit and env file |
| `setup/broker.json`, `setup/install.sh`, `setup/env.example` | the bridge's broker account `switchbot` (rights on `switchbot/#` only) |

## Topics (`switchbot/<id>/...`)

    switchbot/<id>                retained  JSON {state, battery, door, calibrated, ts}
    switchbot/<id>/state          retained  LOCKED | UNLOCKED | NOT_FULLY_LOCKED | LOCKING | UNLOCKING ...
    switchbot/<id>/battery        retained  percent
    switchbot/<id>/door           retained  open | closed (only with a door sensor)
    switchbot/<id>/availability   retained  online | offline (LWT; offline also after 3 min with no advert)
    switchbot/<id>/set                      lock | unlock (also LOCKED | UNLOCKED, on | off); a retained one is ignored
    switchbot/<id>/result                   JSON {cmd, ok, err?, ts}: the answer to a command

The bridge reads the lock passively from its BLE adverts. A command opens an encrypted BLE session
with the lock's key. The lock takes one session at a time: while the phone app is connected to it,
a command fails (see `result`). Unlock only retracts the bolt; the latch is never driven.

The house never assumes a command worked: the Lock channel is a "confirm" channel, so the dashboard,
panels and Yash! show Locked only once the lock's advert says so, and a command to a lock that is
offline is refused (409). If the scan dies (bluetoothd restarted, the adapter gone), the bridge sees
no advert for 6 min, exits with code 1, and systemd starts it again with a fresh scan.

Check by hand, from the core host (the broker's admin account or the bridge's):

    mosquitto_sub -v -t 'switchbot/<id>/#'           # state, battery, door, availability, result
    mosquitto_pub -t 'switchbot/<id>/set' -m lock    # then watch result and state

Security: like every device of the house, the lock's `set` topic is open to the house's device
accounts (ESP nodes, panels). A node that can be reached from outside the door should not share the
broker with the lock.

## Setting up a lock

1. Add the lock in the SwitchBot app once (it calibrates there too: the motor's end points are set
   only in the app, on the mounted lock).
2. On the core host: `make update` generates `MQTT_SWITCHBOT_PASSWORD` (account `switchbot`).
3. On the bridge host (Bluetooth, Python 3 with venv), from a checkout of this repository:

       sudo sh plugins/devices/switchbot/host/install.sh front_lock

   The id is lowercase latin letters, digits, `-` and `_` (the bridge and the dashboard check the same
   rule). It writes `/etc/yashome/switchbot-front_lock.env` (chmod 600). Fill in `SWITCHBOT_LOCK_MAC`, the model,
   `MQTT_HOST` and `MQTT_SWITCHBOT_PASSWORD`. Then fetch the key:

       sudo /usr/local/lib/yashome/switchbot-venv/bin/python /usr/local/lib/yashome/switchbot-get-key.py <MAC> <e-mail>

   This needs the SwitchBot account's own password. If you sign in with Google, set a password in the
   app first (Profile -> Account Security). Turn 2FA off while fetching the key. Copy the printed
   `SWITCHBOT_LOCK_KEY_ID` / `SWITCHBOT_LOCK_ENC_KEY` lines into the env file. A factory reset of the
   lock changes the key.
4. `sudo systemctl enable --now switchbot-lock@front_lock`.
5. Dashboard -> Add -> SwitchBot lock, with the same id (`front_lock`) and the model. The lock is then
   listed in Settings -> Devices -> All devices.

A wrong env file stops the unit with exit code 2, and the message is in its journal. The unit runs as
a dynamic user with no files or home of its own (BlueZ accepts D-Bus calls from any user).

## A device of the same id

<!-- cutover-compat -->

Add -> SwitchBot lock replaces an existing device of the same id only when it is a SwitchBot lock:
one made by this plug-in, or a hand-made `mqtt_devices.json` entry whose `model` starts with
"SwitchBot". Its topics become `switchbot/<id>/`. Keep the id: Yash! finds its lock by the id.

- Only one bridge can hold the lock's BLE session. Disable any other bridge of the same lock for
  good (`sudo systemctl disable --now <its unit>`), or it comes back after a reboot.
- A panel lock widget keeps the topics it was built with: pick the lock again in the panel builder
  and save (an ESP screen: rebuild and flash it).
