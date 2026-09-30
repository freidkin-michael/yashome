# Plug-ins that ship with Yashome

Each directory is one plug-in (docs/plugins.md has the contract). Nothing here is one house's
data: keys, addresses and device lists go into `.env` (each plug-in's keys are in its
`setup/env.example`) and `plugin-data/<name>/`. A plug-in loads only when `PLUGINS` in `.env`
names it; `make install` switches on the ones whose `setup/detect.sh` finds their hardware.

| plug-in | what | `.env` / needs |
|---|---|---|
| `zigbee` | Zigbee through zigbee2mqtt: devices, pairing, key rename, remove; brings the zigbee2mqtt container (see its README) | `Z2M_SERIAL`, a coordinator stick |
| `weather` | Open-Meteo forecast every 15 min, `GET /api/weather`, a retained copy on `home/weather` | `HOME_LAT`, `HOME_LON` (or located by IP) |
| `climate` | air conditioners: the room reading of a climate card from another device's sensor | - |
| `tv` | a TV card face (power, volume, inputs) and the "Appliances" tab | - |
| `webos` | LG webOS TVs as devices: SSAP + Wake-on-LAN, live polling | `WOL_RELAY`; TVs in `plugin-data/webos/devices.json` |
| `ir` | learned IR/RF remotes and appliances through ESPHome blasters | blasters publishing on MQTT |
| `cameras` | IP cameras proxied same-origin: snapshot and MJPEG stream, a camera tab | cameras in Settings |
| `nfc` | NTAG 424 DNA stickers (SDM, one-time links): a verified tap is the event "card = <name>" of the device `nfc`; your rules decide (unlock a door, a scene) | `NFC_*` keys and cards, `EXTRA_PIP=cryptography==50.0.1` |
| `stats` | `GET /api/stats`: counts for a status page widget | - |

`_example/` is a complete small plug-in (it is not loaded: names starting with `_` are skipped).
To try it, copy it as `hello` into `PLUGINS_EXTRA_DIR` (default `./plugins-extra`), add `hello` to
`PLUGINS` and `docker compose restart home`. Your own plug-ins live in that directory too; the
tests of the shipped ones are in `tests/plugins/` (`make dev-test`).

## Known limits

- webOS: SSAP runs over plain `ws://` on port 3000 with no TV identity; the client key goes to
  whatever answers at the TV's address. The TV's IP comes from `webos_hosts.json` ({device id: ip}, optional: written by a helper of your own that resolves the TV's MAC; without it the configured host is used).

- nfc: a link is a one-time credential (a replayed or forged one is refused before any rule runs); the counter makes a used one worthless, including the
  copies in the access log and the phone's history.
