# androidtv - Android TV / Google TV

An Android TV or Google TV (Sony, Philips, TCL, Xiaomi, a Chromecast with Google TV) as a device of the
house, over the protocol of the Google TV phone remote (the `androidtvremote2` package): power, volume,
mute, inputs (HDMI 1-4), apps, the media keys and the remote's keys (the tv plug-in's remote).

**Not tried on a live TV yet**: the house has none; the tests use a fake client.

## Setup

1. Add the packages to `EXTRA_PIP` in `.env` and run `make update` (they are baked into the backend image):
   `androidtvremote2==0.3.2 aiofiles==25.1.0 protobuf==7.36.2 cryptography==50.0.1`
   (`setup/env.example`). Without them the plug-in loads, and Health and the card say what is missing.
2. Turn `androidtv` on (`PLUGINS`), give the TV a fixed address (reserve it in the router).
3. Settings -> Devices -> TVs -> **Add a TV**: the tv plug-in finds it (port 6466 or 6467) and asks its name;
   its MAC comes from the certificate the TV shows before any pairing.
4. **Pairing** on its card: the TV shows a code, type it and **Confirm**. The house's one client certificate
   (`plugin-state/androidtv/client.pem`, `client.key`, 0600) is trusted by that TV from then on;
   `paired.json` lists the TVs that trust it.
5. **Edit**: the inputs to show and the apps (`name = package`, e.g. `com.netflix.ninja`, or a link such as
   `https://www.youtube.com`); the address and MAC under Advanced.

## How it works

| what | how |
|---|---|
| Connection | one per paired TV (`_keeper`, every 30 s for one that is not connected); the package reconnects a dropped one; a TV that no longer trusts the house leaves `paired.json` (Health: not paired) |
| State | pushed by the TV: power (`is_on`), volume (level of its max, shown 0-100) and mute. No connection = off: a TV is never offline, it sleeps |
| Power | `WAKEUP` / `SLEEP` (discrete keys, not the `POWER` toggle); with no connection: Wake-on-LAN to its MAC |
| Volume | the TV takes steps only: as many `VOLUME_UP` / `VOLUME_DOWN` as the change needs, at most 60 |
| Mute | `VOLUME_MUTE` when the TV's mute differs |
| Input, app, media, keys | `TV_INPUT_HDMI_n`; launch of a package or link; `MEDIA_PLAY` ...; `DPAD_*`, `BACK`, `HOME` ... |

`GET /api/androidtv`, `POST /api/androidtv/tvs`, `PUT` / `DELETE /api/androidtv/tvs/{id}`,
`POST /api/androidtv/tvs/{id}/pair` (the TV shows a code), `POST /api/androidtv/tvs/{id}/pair/code`
(the house token).

| file | what |
|---|---|
| `__init__.py` | the devices, the connections, the routes above, the health rows |
| `_atv.py` | the package (imported here only), the key names |
| `ui.js`, `i18n.json` | the card on Settings -> Devices -> TVs (pairing, edit, remove) and the form of a new one for Add a TV |
| `setup/env.example` | the packages for `EXTRA_PIP` |
| `setup/settings.example.json` | the shape of `androidtv_tvs` |
