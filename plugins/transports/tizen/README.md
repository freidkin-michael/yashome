# tizen - Samsung Tizen TVs (2016 and later)

The TVs are the settings section `tizen_tvs` (`plugin-data/tizen/settings.json`), edited in Settings -> Devices -> TVs (the Samsung cards and **Add a Samsung TV**):
`{id, name, host, mac, inputs {name: KEY_...}, apps {name: app id}, ir_on {device, code}}` (`setup/settings.example.json`).
Each one becomes a device of category `tv` (the TV plug-in's card): power, volume, mute, media, and input / app when set.

| What | How |
|---|---|
| Pairing | `POST /api/tizen/tvs/{id}/pair`: the TV asks its owner (Allow, 30 s), the token goes to `plugin-state/tizen/tokens.json` (600). It is never logged: the channel's logger stays at WARNING (websockets logs the URL with the token at DEBUG). A pairing kept under another key is taken over by `POST .../adopt {key}` |
| State | Every 10 s (60 s once off for 10 min): UPnP `GetVolume` on :9197 answers only while the screen is on (it stops ~5 s after standby; the REST API on :8001 lags ~20 s). One answer = on, two silent looks = off. A TV is never offline, it is off |
| Power on | With an IR on key (`ir_on`: a key that only switches on, learned apart from "off"): that key and one Wake-on-LAN at once, every time; the key lights the screen in any state, a lit TV ignores it. Without one: Wake-on-LAN (`core.wake_on_lan`; "Power On with Mobile" must be on in the TV's Expert Settings), repeated at +20, +45 and +90 s (a WoL soon after standby may wake the network and not the screen), the first one not before 60 s after our own off; the repeats stop at a power-off, or when the TV, seen on, goes off again (the remote). `KEY_POWER` never powers on: it is a toggle, and in the dark phase after an off it lights the screen only sometimes |
| Power off | `KEY_POWER` over the remote channel (wss :8002), only while it is on (the key toggles). After it the state stays off for `hold_off_s` (settings, default 600 s): this model brings its network back about 35-50 s after an off with the screen dark, and UPnP may answer then too; the UPnP phase with a dark screen lasts about 80 s, so only UPnP answering without a gap for `hold_confirm_s` (default 150 s), a power-on command or the end of the hold count as on. With "Power On with Mobile" on, the TV may also come back on by itself about a minute after an off; whether that happens without other connections to it (the plug-in asks only :9197) is not verified. The house then shows it on: `power` is in the device's `no_enforce`, so the watchdog does not switch it off again (it cannot tell that from the remote) |
| Volume, mute | UPnP `SetVolume` / `SetMute` (absolute) |
| Media, input | keys (`KEY_PLAY` ...; an input is the key in its settings, e.g. `KEY_HDMI1`) |
| App | `POST :8001/api/v2/applications/<id>` |
| Discovery | `GET /api/tizen/discover`: SSDP for `urn:samsung.com:device:RemoteControlReceiver:1` (a bridged container sees no multicast: enter the address by hand) |

`GET /api/tizen` lists the TVs (with `paired`), the unassigned pairings and whether `tokens.json` is readable;
`PUT` / `DELETE /api/tizen/tvs/{id}` change them. Health warns about a TV that is not paired.

| file | what |
|---|---|
| `__init__.py` | the devices, the poller, the routes above, the health rows |
| `_tv.py` | the TV's channels: remote (wss :8002), REST (:8001), UPnP (:9197), SSDP discovery |
| `ui.js`, `i18n.json` | the Samsung cards on Settings -> Devices -> TVs: add, edit, pair, remove, the TVs found on the LAN |
| `setup/settings.example.json` | the shape of `tizen_tvs` |

Setup: turn `tizen` on, give the TV a fixed address (reserve it in the router), turn on "Power On with
Mobile", then Settings -> Devices -> TVs -> **Add a Samsung TV** (or **Find on the network**) and **Pairing**
(press Allow on the TV within 30 s).
