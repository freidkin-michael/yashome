# Plug-ins that ship with Yashome

Each plug-in is a directory in a group folder by its role: `transports/` (how devices are
reached), `devices/` (a kind of device on the dashboard, over any transport), `sources/` (data and
events), `tools/`. A plug-in that cannot work without another lists it in its `requires` file;
switching it on brings that one too (docs/plugins.md has the contract). Nothing here is one house's
data: keys, addresses and device lists go into `.env` (each plug-in's keys are in its
`setup/env.example`) and `plugin-data/<name>/`. A plug-in loads only when `PLUGINS` in `.env`
names it; `make install` switches on the ones whose `setup/detect.sh` finds their hardware. Switch
one on or off, and set its keys, in Settings -> Maintenance -> Plug-ins and update (or `yashctl plugin`).

**Where they are in Settings.** The groups are Overview, House, Devices, Automation, Voice, This screen
and Maintenance. A plug-in adds a page (or a tab of a page) to one of them: map -> House -> Home map,
family -> House -> Family, weather -> House -> Weather; zigbee -> Devices -> Zigbee, ir -> Devices -> IR,
tv, webos, tizen, androidtv and player -> Devices -> TVs, panels -> Devices -> Panels, nfc -> Devices -> NFC tags,
computers -> Devices -> Computers and servers; history and watchdog -> Automation; voice -> Voice;
fwgen -> Maintenance -> Firmware builder. Health rows of every plug-in are on Overview. The rest have no
page: they work through the Add window, a tab or a device's window (see each README).

**Examples of a house's configuration sit in each plug-in's `setup/`:** `env.example` (its keys,
merged into `.env` by `make install` / `make update`), `settings.example.json` (its settings
sections, kept in `plugin-data/<name>/settings.json`) and `<file>.example.json` for each file it
reads from `plugin-data/<name>/` that you may write or seed, e.g. webos `devices.example.json` ->
`plugin-data/webos/devices.json` (ir remotes are normally learned in the dashboard; keys and counters
the plug-ins write in `plugin-state/` have none).
Every shipped plug-in has a README.md (what it does, its files, routes and keys). The examples are documentation
(nothing copies them), and a test loads every `*.example.json` through its plug-in
(the other examples, a YAML for instance, are not loaded). `tv` and `stats` need no configuration.

| group | plug-in | what | `.env` / needs |
|---|---|---|---|
| transports | `zigbee` | Zigbee through zigbee2mqtt: devices, pairing, key rename, remove; brings the zigbee2mqtt container; Settings -> Devices -> Zigbee (see its README) | `Z2M_SERIAL`, a coordinator stick |
| transports | `webos` | LG webOS TVs as devices: SSAP + Wake-on-LAN, live polling, the LG cards on Settings -> Devices -> TVs; `media` takes play, pause, stop, volume_up, volume_down (relative steps reach a sound bar over HDMI ARC) | the WoL relay on the host (`host/wol-relay.py`, `sudo sh tools/host-sockets.sh`); TVs in `plugin-data/webos/devices.json`; see its README |
| transports | `tizen` | Samsung Tizen TVs (2016 and later) as devices: power, volume, inputs and apps, Wake-on-LAN, an IR power-on fallback; Settings -> Devices -> TVs (see its README) | TVs in `plugin-data/tizen/settings.json` |
| transports | `androidtv` | Android TV / Google TV (Sony, Philips, TCL, Xiaomi, Chromecast with Google TV) as devices: the Google TV remote protocol, pairing by a code on the TV, power, volume, inputs, apps, the remote's keys; Settings -> Devices -> TVs (see its README) | `EXTRA_PIP` with its packages (`setup/env.example`); TVs in `plugin-data/androidtv/settings.json` |
| transports | `ir` | learned IR/RF remotes and appliances through ESPHome blasters; Settings -> Devices -> IR | blasters publishing on MQTT |
| devices | `climate` | air conditioners: the room reading of a climate card from another device's sensor | - |
| devices | `switchbot` | SwitchBot locks (Bluetooth LE only): a bridge on a host near the lock (host piece, one systemd instance per lock) on `switchbot/<id>/`; Add -> SwitchBot lock makes it a device the Yash! lock tile finds; battery and link on the health page (see its README) | the bridge host (Bluetooth) + `MQTT_SWITCHBOT_PASSWORD`; the lock's key fetched once |
| devices | `panels` | wall panels: an Android tablet (the Home Panel launcher: pairing, a key of its own, camera, touchpad) or an ESP screen (firmware built and flashed over the air, a broker account per screen); the builder, the kiosk page, the Panels tab and Settings -> Devices -> Panels (see its README) | the build plug-in for ESP screens and the app; `PANELS_*` keys |
| devices | `tv` | a TV card face (power, volume, inputs), the "Appliances" tab and the page Settings -> Devices -> TVs | - |
| devices | `cameras` | IP cameras proxied same-origin: snapshot and MJPEG stream, a camera tab | `plugin-data/cameras/settings.json` (see its example); passwords as `${VAR}` from `.env` |
| sources | `weather` | Open-Meteo forecast every 15 min, the weather now from a near station, `GET /api/weather`, a retained copy on `home/weather` | the place in Settings -> House -> Weather (or located by IP) |
| sources | `nfc` | NTAG 424 DNA stickers (SDM, one-time links): a verified tap is the event "card = <name>" of the device `nfc`; your rules decide (unlock a door, a scene); Settings -> Devices -> NFC tags | `NFC_*` keys and cards, `EXTRA_PIP=cryptography==50.0.1` |
| sources | `presence` | is anyone home: the phones on the Wi-Fi (a host piece on any LAN host, arping by MAC), the header badge, arrival / departure events for rules | the host piece + `MQTT_PRESENCE_PASSWORD` (see its README) |
| sources | `fwdesc` | what a firmware built by fwgen says of itself: its retained `<node>/fwgen/description` and hash read into the device (docs/device-description.md) | - |
| tools | `provision` | gives an ESPHome node that lost its Wi-Fi (its open `<name>-setup` AP) the house Wi-Fi again: through a neighbour node with the plug-in's firmware package, or a host with a spare Wi-Fi card (host piece); firmware packages for your own nodes (see its README) | `HOUSE_WIFI_*`; the host piece: `sudo sh tools/host-sockets.sh` |
| tools | `history` | one timeline of everything that happened (states with who caused them, commands, rules, offline/online, watchdog, errors), SQLite, filters, erase; the History button and Settings -> Automation -> History | - |
| tools | `watchdog` | the house keeps itself consistent: a rule's state put right when its source says otherwise, retained ghosts cleared, dangling data references listed and forgotten after a while, the Health button, `GET /api/watchdog`; Settings -> Automation -> Watchdog | - |
| tools | `connect` | one Add / bind window: Zigbee pairing, ESPHome on MQTT, setup networks (yashGen, nodes that lost their Wi-Fi), tablets and ESP screens all at once in one live list, a code typed once finds its device; replaces the Add menu (see its README) | the plug-ins of the sources it opens |
| tools | `stats` | `GET /api/stats`: counts for a status page widget | - |
| tools | `voice` | the house answers by voice (docs/voice.md): the TTS service (Silero, container `tts`, or on a computer of the house), the satellites and their cards, say / play / stop / volume, the reply to the satellite that heard, typed and spoken questions from the app; Settings -> Voice (see its README) | `COMPOSE_PROFILES` with `voice`, `BIND_ADDRESS`, `VOICE_TTS_KEY` |
| tools | `map` | the floor plan of the house (owner only): devices and loads put on it by a tap, what controls what, the links the house knows, facts for the voice; the Home map tab and Settings -> House -> Home map (see its README) | optional: `plugin-data/map/scheme.svg` |
| tools | `player` | a film or a stream on a TV, played by the computer at it (role Player of plug-in computers: mpv driven over ssh, no port); in the TV's window, the Films tab and Settings -> Devices -> TVs -> Players. Requires `computers` | the role, installed from the computer's card |
| tools | `touchmouse` | a phone or a wall panel as the touchpad of a TV box or a computer: the list of receivers (`GET /api/touchmouse`, Add -> Touchmouse) and the receiver of a Linux host (see its README) | the receiver on the target host |
| tools | `computers` | the house's computers on its network and the servers on them (build server, app builder, yashGen, TTS, LLM, player, touchmouse): roles installed from a computer's card over ssh by the helper, the house reaches them through ssh tunnels; Settings -> Devices -> Computers and servers (see its README) | the house's ssh key on each computer |
| tools | `build` | the build server for the house's own firmware (ESPHome) and the apps: on a computer of the house (Settings -> Devices -> Computers and servers, plug-in computers); other plug-ins send it jobs (see its README) | `computers` |
| tools | `family` | the Yash! app for the people of the house: pairing by QR, a token and rights per phone, ring (a button or a rule), delete, presence of each phone (home / away, arrival and departure for rules), the app updating itself on the house's command; with Firebase it brings the family-bridge container (on this host or another), without it works through the dashboard alone; the app source; Settings -> House -> Family (see its README) | the address for the phones (Settings -> House -> Family -> App); optional: your Firebase project (`FAMILY_FIREBASE_DB`, `FAMILY_FIREBASE_KEY`) |
| tools | `pair` | pairing a fwgen device: its own MQTT account, the house Wi-Fi and its OTA password given at pairing; Add -> Pair a device (docs/pairing.md) | the `provision` host relay, `HOUSE_WIFI_*` |
| tools | `fwgen` | Settings -> Maintenance -> Firmware builder: a link to the yashGen firmware builder, on a computer of the house (role yashGen of plug-in computers) | `computers`, a `yashgen.<domain>` host of the front door |

`_example/` is a complete small plug-in (it is not loaded: names starting with `_` are skipped).
To try it, copy it as `hello` into `PLUGINS_EXTRA_DIR` (default `./plugins-extra`), add `hello` to
`PLUGINS` and `docker compose restart home`. Your own plug-ins live in that directory too; the
tests of the shipped ones are in `tests/plugins/` (`make dev-test`).

## Known limits

- webOS: SSAP runs over plain `ws://` on port 3000 with no TV identity; the client key goes to
  whatever answers at the TV's address. The TV's IP comes from `webos_hosts.json` ({device id: ip}, optional: written by a helper of your own that resolves the TV's MAC; without it the configured host is used).

- nfc: a link is a one-time credential (a replayed or forged one is refused before any rule runs); the counter makes a used one worthless, including the
  copies in the access log and the phone's history.
