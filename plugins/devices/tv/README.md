# tv - TV cards, the Appliances tab and Settings -> TVs

The dashboard side of every TV, and **Add a TV**. The TV itself is reached by another plug-in: `webos`
for LG webOS TVs, `tizen` for Samsung Tizen TVs, `ir` for an IR one.

| file | what |
|---|---|
| `__init__.py` | the remote's keys (`tv_key`), `POST /api/tv/search`, `POST /api/tv/probe` (the house token) |
| `_probe.py` | what TV sits at an address (the port or URL each platform answers on), and the sweep of a /24 |
| `ui.js`, `i18n.json` | the card face of category `tv` (power, volume + mute, inputs), the Appliances tab (`tv` and `media` devices), a 12 s hold of "on" while a TV wakes over Wake-on-LAN (webos, or a device with `wakes`, as tizen), the page Settings -> Devices -> TVs |

## Settings -> Devices -> TVs

One card per TV. A transport plug-in (webos, tizen) fills in its own card: address, pairing,
inputs, apps. A TV with an IR remote shows the remote and a Keys button that opens Settings ->
Devices -> IR. A TV that only IR reaches points to the IR page. The `player` plug-in adds a Players tab here.

## Add a TV

One button under the cards. Each press (opening it, **Search again**) sweeps the house's /24 - the
one of `BIND_ADDRESS`, of the address the page came from and of the devices' addresses, at most 3 -
for the ports below (a few seconds; nothing runs in between: a new TV is rare). Plain TCP, so it works
from the dashboard's bridge network, where a multicast search sees nothing. A field takes an address
that is not listed. The house names the platform itself by what answers:

| platform | answers on |
|---|---|
| Samsung Tizen | `http://<ip>:8001/api/v2/` (name, model, MAC) |
| Roku TV | `http://<ip>:8060/query/device-info` |
| LG webOS | its SSAP websocket (3000, or 3001 with TLS) answers a request before pairing with an error: no prompt on the TV, and a Gitea on port 3000 is not taken for a TV |
| Android TV / Google TV | port 6466 or 6467 |
| Hisense VIDAA | port 36669 |
| Philips | port 1925 or 1926 |

A platform with a plug-in that is on (`tizen`, `webos`) opens that plug-in's form for a new TV (its
`deviceSettings` entry's `add(found, into, reload, say)`), with the name filled in (and the MAC when
the TV tells it: Samsung, Roku; an LG TV tells it at its pairing). A platform
without one is named and sent to the IR page. A TV that is off answers nothing: the wizard asks to
switch it on and try again. Each TV plug-in appends to `KNOWN_HOSTS` a callable -> `{address: name}` of its TVs, so a TV already
added is marked so, under the name on its card. A transport may still add buttons of its own below the cards (`actions`).

The `ir` plug-in adds its appliances and the "+ IR appliance" button to the Appliances tab.
